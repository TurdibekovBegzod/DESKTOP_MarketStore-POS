from datetime import date, timedelta

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QDateEdit, QComboBox,
    QGridLayout, QFrame, QSizePolicy, QCalendarWidget
)
from PyQt6.QtCore import Qt, QDate, QPointF
from PyQt6.QtGui import QPainter, QPen, QColor, QFont
import database as db
from ui.async_loader import AsyncDataLoader, make_progress_bar
from ui.i18n import set_language, t


class LineChart(QWidget):
    def __init__(self, title="Grafik", color="#3b82f6"):
        super().__init__()
        self.title = title
        self.color = color
        self.points = []
        self.series = []
        self.view_start = 0
        self.visible_count = 14
        self.drag_start_x = None
        self.drag_start_view = 0
        self.setMinimumHeight(190)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setStyleSheet("background:white;border:1px solid #e2e8f0;border-radius:8px;")

    def set_data(self, points):
        self.points = points
        self.series = []
        self.view_start = min(self.view_start, self._max_view_start())
        self.update()

    def set_series(self, series):
        self.series = series
        self.points = series[0]["points"] if series else []
        self.view_start = min(self.view_start, self._max_view_start())
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(18, 12, -18, -12)
        painter.setPen(QColor("#4b5563"))
        painter.setFont(QFont("Segoe UI", 11, QFont.Weight.Normal))
        painter.drawText(rect.left(), rect.top(), rect.width(), 24, Qt.AlignmentFlag.AlignCenter, self.title)

        grid_lines = 5
        chart_series = self._visible_series()
        all_points = [point for series in chart_series for point in series["points"]]

        if not all_points:
            chart = rect.adjusted(54, 38, -14, -92)
            painter.setPen(QColor("#94a3b8"))
            painter.drawText(chart, Qt.AlignmentFlag.AlignCenter, "Ma'lumot yo'q")
            return

        values = [value for _, value in all_points]
        min_value = min(0, min(values))
        max_value = max(0, max(values))
        if max_value == 0 and min_value < 0:
            max_value = 0
        elif min_value == 0 and max_value > 0:
            min_value = 0
        value_range = (max_value - min_value) or 1

        painter.setFont(QFont("Segoe UI", 8))
        y_axis_labels = [f"{max_value - (step / grid_lines) * value_range:,.0f}" for step in range(grid_lines + 1)]
        y_label_width = max(painter.fontMetrics().horizontalAdvance(label) for label in y_axis_labels)
        chart = rect.adjusted(y_label_width + 18, 38, -14, -92)

        painter.setPen(QPen(QColor("#d9d9d9"), 1))
        for step in range(grid_lines + 1):
            y = chart.top() + (step / grid_lines) * chart.height()
            painter.drawLine(chart.left(), int(y), chart.right(), int(y))

        painter.setPen(QColor("#4b5563"))
        for step in range(grid_lines + 1):
            y = chart.top() + (step / grid_lines) * chart.height()
            painter.drawText(
                chart.left() - y_label_width - 8,
                int(y) - 8,
                y_label_width,
                16,
                Qt.AlignmentFlag.AlignRight,
                y_axis_labels[step],
            )

        plotted_by_series = []
        for item in chart_series:
            points = item["points"]
            count = max(len(points) - 1, 1)
            plotted = []
            for index, (_, value) in enumerate(points):
                x = chart.left() + (index / count) * chart.width()
                y = chart.bottom() - ((value - min_value) / value_range) * chart.height()
                plotted.append(QPointF(x, y))

            color = QColor(item["color"])
            painter.setPen(QPen(color, 2.5))
            for index in range(1, len(plotted)):
                painter.drawLine(plotted[index - 1], plotted[index])

            painter.setBrush(color)
            painter.setPen(QPen(QColor("#ffffff"), 1.5))
            for point in plotted:
                painter.drawEllipse(point, 4.5, 4.5)
            plotted_by_series.append((item, plotted))

        label_points = chart_series[0]["points"]
        label_plotted = plotted_by_series[0][1] if plotted_by_series else []
        painter.setPen(QColor("#374151"))
        painter.setFont(QFont("Segoe UI", 7))
        for index, (label, _) in enumerate(label_points):
            x = label_plotted[index].x()
            if len(label_points) > 12:
                painter.save()
                painter.translate(x - 4, chart.bottom() + 30)
                painter.rotate(-45)
                painter.drawText(0, 0, label)
                painter.restore()
            else:
                painter.drawText(int(x - 22), chart.bottom() + 22, 44, 14, Qt.AlignmentFlag.AlignCenter, label)

        painter.setFont(QFont("Segoe UI", 8))
        legend_items = []
        for item in chart_series:
            label = item["label"]
            if len(label) > 20:
                label = label[:17] + "..."
            legend_items.append((label, item["color"], painter.fontMetrics().horizontalAdvance(label) + 42))
        total_width = sum(width for _, _, width in legend_items) + max(0, len(legend_items) - 1) * 10
        legend_x = rect.center().x() - total_width / 2
        legend_y = rect.bottom() - 12
        for label, color, width in legend_items:
            painter.setPen(QPen(QColor(color), 2.5))
            painter.drawLine(int(legend_x), legend_y, int(legend_x + 24), legend_y)
            painter.setBrush(QColor(color))
            painter.setPen(QPen(QColor("#ffffff"), 1.5))
            painter.drawEllipse(QPointF(legend_x + 12, legend_y), 4, 4)
            painter.setPen(QColor("#4b5563"))
            painter.drawText(int(legend_x + 32), legend_y - 8, int(width - 32), 16, Qt.AlignmentFlag.AlignLeft, label)
            legend_x += width + 10

        total_points = len(self.points)
        visible_points = len(label_points)
        if total_points > self.visible_count:
            painter.setPen(QColor("#94a3b8"))
            painter.setFont(QFont("Segoe UI", 8))
            text = f"{self.view_start + 1}-{self.view_start + visible_points} / {total_points}"
            painter.drawText(rect.right() - 90, rect.top() + 4, 86, 18, Qt.AlignmentFlag.AlignRight, text)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and len(self.points) > self.visible_count:
            self.drag_start_x = event.position().x()
            self.drag_start_view = self.view_start
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drag_start_x is None or len(self.points) <= self.visible_count:
            super().mouseMoveEvent(event)
            return
        width_per_point = max(self.width() / max(self.visible_count, 1), 24)
        delta = event.position().x() - self.drag_start_x
        steps = int(delta / width_per_point)
        self.view_start = max(0, min(self.drag_start_view - steps, self._max_view_start()))
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event):
        if self.drag_start_x is not None:
            self.drag_start_x = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _visible_series(self):
        source = self.series or [{"label": self.title.split(":")[-1].strip() or self.title, "color": self.color, "points": self.points}]
        if len(self.points) <= self.visible_count:
            return source
        end = self.view_start + self.visible_count
        return [
            {
                "label": item["label"],
                "color": item["color"],
                "points": item["points"][self.view_start:end],
            }
            for item in source
        ]

    def _max_view_start(self):
        return max(0, len(self.points) - self.visible_count)


class ReportsWidget(QWidget):
    def __init__(self, user=None, cashier_only=False):
        super().__init__()
        self.user = user
        self.cashier_only = bool(cashier_only) or ((user or {}).get("role") == "cashier")
        self.detail_mode = "cashier" if self.cashier_only else "overall"
        self.detail_metric = "count" if self.cashier_only else "revenue"
        self.selected_entity_id = None
        self._async_loader = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)
        self.progress_bar = make_progress_bar()
        layout.addWidget(self.progress_bar)
        self._async_loader = AsyncDataLoader(self, self.progress_bar)

        self.date_lbl = QLabel("Sana:")
        self.date_edit = QDateEdit()
        self.date_edit.setDate(QDate.currentDate())
        self.date_edit.setDateRange(QDate(2000, 1, 1), QDate.currentDate().addYears(10))
        self.date_edit.setCalendarPopup(True)
        calendar = QCalendarWidget(self)
        calendar.setNavigationBarVisible(True)
        calendar.setGridVisible(True)
        calendar.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        self.date_edit.setCalendarWidget(calendar)
        self.date_edit.setDisplayFormat("dd.MM.yyyy")
        self.date_edit.setFixedWidth(210)
        self.date_edit.setFixedHeight(36)
        self.date_edit.setStyleSheet("""
            QDateEdit {
                border:1px solid #d1d5db;
                border-radius:6px;
                padding:0 10px;
                background:white;
                font-size:13px;
            }
            QDateEdit::drop-down { width:28px; border:none; }
        """)
        self.date_edit.dateChanged.connect(self.load_data)

        self.period_combo = QComboBox()
        self.period_combo.addItem("Kunlik", "day")
        self.period_combo.addItem("Haftalik", "week")
        self.period_combo.addItem("Oylik", "month")
        self.period_combo.addItem("Yillik", "year")
        self.period_combo.setCurrentIndex(self.period_combo.findData("month"))
        self.period_combo.setStyleSheet("border:1px solid #d1d5db;border-radius:6px;padding:6px 10px;background:white;")
        self.period_combo.currentIndexChanged.connect(self._period_changed)

        self.period_range_lbl = QLabel("")
        self.period_range_lbl.setObjectName("period_range_lbl")
        self.period_range_lbl.setStyleSheet("color:#64748b;font-size:12px;font-weight:bold;")
        self.prev_period_btn = QPushButton("<")
        self.prev_period_btn.setFixedSize(36, 34)
        self.prev_period_btn.setStyleSheet(self._toggle_style())
        self.prev_period_btn.clicked.connect(lambda: self._shift_period(-1))
        self.next_period_btn = QPushButton(">")
        self.next_period_btn.setFixedSize(36, 34)
        self.next_period_btn.setStyleSheet(self._toggle_style())
        self.next_period_btn.clicked.connect(lambda: self._shift_period(1))
        self.today_btn = QPushButton("Bugun")
        self.today_btn.setFixedHeight(34)
        self.today_btn.setMinimumWidth(82)
        self.today_btn.setStyleSheet(self._toggle_style())
        self.today_btn.clicked.connect(lambda: self.date_edit.setDate(QDate.currentDate()))
        self.section_lbl = QLabel("Bo'lim:")
        self.section_lbl.setStyleSheet("color:#64748b;font-size:12px;font-weight:bold;")
        self.section_combo = QComboBox()
        self.section_combo.setFixedHeight(34)
        self.section_combo.setMinimumWidth(180)
        self._load_section_combo()
        self.section_combo.currentIndexChanged.connect(self.load_data)
        self.report_type_lbl = QLabel("Hisobot turi:")
        self.report_type_lbl.setStyleSheet("color:#64748b;font-size:12px;font-weight:bold;")
        self.report_type_combo = QComboBox()
        self.report_type_combo.setFixedHeight(34)
        self.report_type_combo.setMinimumWidth(190)
        if not self.cashier_only:
            self.report_type_combo.addItem("Umumiy hisobot", "overall")
        self.report_type_combo.addItem("Kassirlar hisoboti", "cashier")
        self.report_type_combo.currentIndexChanged.connect(self._report_type_changed)
        self.metric_lbl = QLabel("Grafik:")
        self.metric_lbl.setStyleSheet("color:#64748b;font-size:12px;font-weight:bold;")
        self.metric_combo = QComboBox()
        self.metric_combo.setFixedHeight(34)
        self.metric_combo.setMinimumWidth(170)
        self.metric_combo.currentIndexChanged.connect(self._metric_changed)
        self._load_metric_combo()
        self.report_currency_combo = QComboBox()
        self.report_currency_combo.setFixedHeight(34)
        self.report_currency_combo.setMinimumWidth(92)
        self._load_report_currency_combo()
        self.report_currency_combo.currentIndexChanged.connect(self.load_data)

        self.summary_layout = QGridLayout()
        self.summary_layout.setSpacing(12)
        self.summary_cards = {}
        self.summary_card_frames = {}
        self.summary_card_widgets = []
        for index, (key, title, color) in enumerate([
            ("revenue", "Daromad", "#059669"),
            ("profit", "Foyda", "#8b5cf6"),
            ("count", "Sotuvlar soni", "#3b82f6"),
            ("products", "Mahsulotlar soni", "#0ea5e9"),
            ("net_profit", "Sof foyda", "#f59e0b"),
            ("salary", "Oylik", "#ec4899"),
        ]):
            card = QFrame()
            card.setObjectName(f"summary_{key}")
            card.setProperty("accent_color", color)
            card.setFixedHeight(72)
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            card.setStyleSheet(f"""
                QFrame#summary_{key} {{
                    background:white;
                    border-left:4px solid {color};
                    border-top:1px solid #e2e8f0;
                    border-right:1px solid #e2e8f0;
                    border-bottom:1px solid #e2e8f0;
                    border-radius:8px;
                }}
            """)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 8, 14, 8)
            card_layout.setSpacing(4)
            title_lbl = QLabel(title)
            title_lbl.setObjectName("summary_title")
            title_lbl.setStyleSheet("color:#64748b;font-size:11px;background:transparent;border:none;")
            value_lbl = QLabel("0")
            value_lbl.setObjectName("summary_value")
            value_lbl.setProperty("accent_color", color)
            value_lbl.setStyleSheet(f"color:{color};font-size:14px;font-weight:bold;background:transparent;border:none;")
            value_lbl.setWordWrap(False)
            value_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            card_layout.addWidget(title_lbl)
            card_layout.addWidget(value_lbl)
            if key == "salary":
                hint_lbl = QLabel("")
                hint_lbl.setObjectName("summary_hint")
                hint_lbl.setProperty("i18n_skip", True)
                hint_lbl.setStyleSheet("color:#b91c1c;font-size:10px;font-weight:bold;background:transparent;border:none;")
                hint_lbl.setVisible(False)
                card_layout.addWidget(hint_lbl)
            card_layout.addStretch()
            self.summary_cards[key] = value_lbl
            self.summary_card_frames[key] = card
            self.summary_card_widgets.append(card)
            self.summary_layout.addWidget(card, 0, index)
        layout.addLayout(self.summary_layout)
        self._update_summary_card_visibility()

        report_filters = QHBoxLayout()
        report_filters.setSpacing(8)
        report_filters.addWidget(self.report_type_lbl)
        report_filters.addWidget(self.report_type_combo)
        report_filters.addSpacing(10)
        report_filters.addWidget(self.section_lbl)
        report_filters.addWidget(self.section_combo)
        report_filters.addSpacing(10)
        report_filters.addWidget(self.metric_lbl)
        report_filters.addWidget(self.metric_combo)
        report_filters.addStretch()
        layout.addLayout(report_filters)

        detail_row = QHBoxLayout()
        detail_row.setSpacing(12)

        left_panel = QFrame()
        left_panel.setFrameShape(QFrame.Shape.NoFrame)
        left_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        self.entity_panel = QFrame()
        self.entity_panel.setFrameShape(QFrame.Shape.NoFrame)
        self.entity_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        entity_layout = QVBoxLayout(self.entity_panel)
        entity_layout.setContentsMargins(0, 0, 0, 0)
        self.entity_table = QTableWidget()
        self.entity_table.setColumnCount(1)
        self.entity_table.setHorizontalHeaderLabels(["Nomi"])
        self.entity_table.setMinimumWidth(170)
        self.entity_table.setMaximumWidth(220)
        self.entity_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.entity_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.entity_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.entity_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.entity_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.entity_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.entity_table.setAlternatingRowColors(True)
        self.entity_table.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.entity_table.setStyleSheet(self._entity_table_style())
        self.entity_table.itemSelectionChanged.connect(self._on_entity_selected)
        entity_layout.addWidget(self.entity_table, 1)
        left_layout.addWidget(self.entity_panel, 1)
        self.entity_container = left_panel
        detail_row.addWidget(left_panel, 0)

        chart_panel = QWidget()
        chart_layout = QVBoxLayout(chart_panel)
        chart_layout.setContentsMargins(0, 0, 0, 0)
        chart_layout.setSpacing(8)

        self.detail_chart = LineChart("Tanlangan hisobot grafigi", "#3b82f6")
        self.detail_chart.setMinimumHeight(330)
        chart_layout.addWidget(self.detail_chart, 1)

        period_controls = QHBoxLayout()
        period_controls.setSpacing(8)
        period_controls.addWidget(self.prev_period_btn)
        period_controls.addWidget(self.date_lbl)
        period_controls.addWidget(self.date_edit)
        period_controls.addWidget(self.period_combo)
        period_controls.addWidget(self.next_period_btn)
        period_controls.addWidget(self.today_btn)
        period_controls.addWidget(self.report_currency_combo, 0, Qt.AlignmentFlag.AlignTop)
        period_controls.addWidget(self.period_range_lbl)
        period_controls.addStretch()
        chart_layout.addLayout(period_controls)
        detail_row.addWidget(chart_panel, 1)
        layout.addLayout(detail_row, 1)

        self.overall_rows = []
        self._sync_buttons()
        self._period_changed()

    def apply_theme(self, theme):
        self.setStyleSheet(f"background:{theme['content']};")
        field_style = f"""
            QDateEdit, QComboBox {{
                background:{theme['topbar']};
                color:{theme['title']};
                border:1px solid #cbd5e1;
                border-radius:6px;
                padding:0 10px;
                font-size:13px;
            }}
            QDateEdit:focus, QComboBox:focus {{ border-color:{theme['accent']}; }}
            QDateEdit::drop-down, QComboBox::drop-down {{
                border:none;
                width:28px;
            }}
        """
        self.date_edit.setFixedWidth(210)
        self.date_edit.setFixedHeight(36)
        self.date_edit.setStyleSheet(field_style)
        self.period_combo.setMinimumHeight(38)
        self.period_combo.setStyleSheet(field_style)
        self.section_combo.setFixedHeight(34)
        self.section_combo.setStyleSheet(field_style)
        self.section_lbl.setStyleSheet(f"color:{theme['muted']};font-size:12px;font-weight:bold;")
        self.report_type_combo.setFixedHeight(34)
        self.report_type_combo.setStyleSheet(field_style)
        self.report_type_lbl.setStyleSheet(f"color:{theme['muted']};font-size:12px;font-weight:bold;")
        self.metric_combo.setFixedHeight(34)
        self.metric_combo.setStyleSheet(field_style)
        self.metric_lbl.setStyleSheet(f"color:{theme['muted']};font-size:12px;font-weight:bold;")
        self.report_currency_combo.setFixedHeight(34)
        self.report_currency_combo.setStyleSheet(field_style)
        self.period_range_lbl.setStyleSheet(f"color:{theme['muted']};font-size:12px;font-weight:bold;background:transparent;border:none;")

        for card in self.findChildren(QFrame):
            if card.objectName().startswith("summary_"):
                color = card.property("accent_color") or theme["accent"]
                card.setStyleSheet(f"""
                    QFrame#{card.objectName()} {{
                        background:{theme['topbar']};
                        border-left:4px solid {color};
                        border-top:1px solid #e2e8f0;
                        border-right:1px solid #e2e8f0;
                        border-bottom:1px solid #e2e8f0;
                        border-radius:8px;
                    }}
                """)
        for label in self.findChildren(QLabel):
            if label.objectName() == "summary_title":
                label.setStyleSheet(f"color:{theme['muted']};font-size:11px;background:transparent;border:none;")
            elif label.objectName() == "summary_value":
                color = label.property("accent_color") or theme["accent"]
                label.setStyleSheet(f"color:{color};font-size:14px;font-weight:bold;background:transparent;border:none;")
            elif label.objectName() == "summary_hint":
                label.setStyleSheet(
                    "color:#b91c1c;font-size:10px;font-weight:bold;background:transparent;border:none;"
                )
            else:
                label.setStyleSheet(f"color:{theme['title']};background:transparent;border:none;")

        button_style = self._toggle_style(theme)
        for button in [
            self.prev_period_btn,
            self.next_period_btn,
            self.today_btn,
        ]:
            button.setStyleSheet(button_style)

        self.entity_table.setStyleSheet(self._entity_table_style(theme))

    def showEvent(self, event):
        super().showEvent(event)
        self.load_data()

    def load_data(self):
        start_date, end_date = self._date_range()
        period = self.period_combo.currentData()
        section_id = self._selected_section_id()
        if self.isVisible():
            self._async_loader.start(
                lambda: self._fetch_report_data(start_date, end_date, period, section_id),
                self._apply_loaded_data,
            )
            return
        self._apply_loaded_data(self._fetch_report_data(start_date, end_date, period, section_id))

    def _fetch_report_data(self, start_date, end_date, period, section_id=None):
        if self.cashier_only:
            user_id = (self.user or {}).get("id")
            if period == "day":
                rows = db.get_overall_day_hourly_series(start_date, section_id)
            else:
                rows = db.get_overall_period_series(start_date, end_date, section_id)
            expense_rows = []
            salary_rows = db.get_cashier_salary_period_summary(start_date, end_date, section_id)
            salary_rows = [r for r in salary_rows if r.get("entity_id") == user_id]
        elif period == "day":
            rows = db.get_overall_day_hourly_series(start_date, section_id)
            expense_rows = db.get_expense_hourly_report(start_date, include_cashier=False)
            salary_rows = db.get_cashier_salary_period_summary(start_date, end_date, section_id)
        else:
            rows = db.get_overall_period_series(start_date, end_date, section_id)
            expense_rows = db.get_expense_report(start_date, end_date, include_cashier=False)
            salary_rows = db.get_cashier_salary_period_summary(start_date, end_date, section_id)
        return {
            "start_date": start_date,
            "end_date": end_date,
            "section_id": section_id,
            "rows": rows,
            "expense_rows": expense_rows,
            "salary_rows": salary_rows,
            "sections": [dict(section) for section in db.get_product_sections()],
            "currencies": [dict(currency) for currency in db.get_currencies()],
        }

    def _apply_loaded_data(self, data):
        start_date = data["start_date"]
        end_date = data["end_date"]
        self._update_period_range_label(start_date, end_date)
        self._load_section_combo(data.get("sections"))
        self._load_report_currency_combo(data["currencies"])
        filled = self._with_net_profit_from_expenses(
            self._filled_series(data["rows"], start_date, end_date),
            data["expense_rows"],
            data["currencies"],
            section_id=data.get("section_id"),
            start_date=start_date,
            end_date=end_date,
        )
        currency = self._selected_report_currency()

        totals = {
            "revenue": sum(row["revenue"] for row in filled),
            "profit": sum(row["profit"] for row in filled),
            "net_profit": sum(row["net_profit"] for row in filled),
            "count": sum(row["sales_count"] for row in filled),
            "products": sum(row["product_count"] for row in filled),
            "salary": sum(row.get("total_salary", 0) or 0 for row in data.get("salary_rows", [])),
            "salary_deduction": sum(row.get("salary_deduction", 0) or 0 for row in data.get("salary_rows", [])),
        }
        self.summary_cards["revenue"].setText(self._format_money(totals["revenue"], currency))
        self.summary_cards["profit"].setText(self._format_money(totals["profit"], currency))
        self.summary_cards["count"].setText(f"{totals['count']:,.0f}")
        self.summary_cards["products"].setText(f"{totals['products']:,.0f}")
        self.summary_cards["net_profit"].setText(self._format_money(totals["net_profit"], currency))
        self.summary_cards["salary"].setText(self._format_money(totals["salary"], currency))
        self._apply_salary_card_hint(totals.get("salary_deduction", 0), currency)
        self._update_summary_card_visibility()

        self.overall_rows = filled
        self._refresh_report_panel(start_date, end_date, filled)
        set_language(self, self.property("app_language") or "uz")

    def _apply_salary_card_hint(self, deduction, currency):
        """Show, on the salary card, how much was taken back through expenses."""
        label = self.summary_cards.get("salary") if hasattr(self, "summary_cards") else None
        if label is None:
            return
        language = self.property("app_language") or "uz"
        deduction = deduction or 0
        if deduction > 0:
            text = f"{t('Harajat', language)}: -{self._format_money(deduction, currency)}"
            label.setToolTip(f"{t('Kassir harajatlari ayrildi', language)} ({text})")
        else:
            label.setToolTip("")
        card = self.summary_card_frames.get("salary") if hasattr(self, "summary_card_frames") else None
        if card is not None:
            hint = card.findChild(QLabel, "summary_hint")
            if hint is not None:
                hint.setProperty("i18n_skip", True)
                hint.setText(
                    f"− {self._format_money(deduction, currency)} {t('harajat', language)}"
                    if deduction > 0 else ""
                )
                hint.setVisible(deduction > 0)

    def _refresh_report_panel(self, start_date, end_date, overall_rows):
        if self.detail_mode == "overall":
            self.selected_entity_id = None
            self.entity_panel.hide()
            self.entity_container.hide()
            titles = {
                "all": "Barcha ko'rsatkichlar",
                "revenue": "Umumiy daromad",
                "profit": "Umumiy foyda",
                "count": "Umumiy sotuvlar soni",
                "products": "Umumiy sotilgan mahsulotlar",
                "net_profit": "Foyda - harajatlar",
            }
            self.detail_chart.title = titles.get(self.detail_metric, "Umumiy hisobot")
            self._set_chart_data(self.detail_chart, overall_rows, self.detail_metric)
            return

        self.entity_container.show()
        self.entity_panel.show()
        self._load_entities(start_date, end_date)

    def _load_entities(self, start_date, end_date):
        current = self.selected_entity_id
        rows = db.get_cashier_period_summary(
            start_date,
            end_date,
            self._selected_section_id(),
            only_cashiers=True,
        )
        user_is_cashier = (self.user or {}).get("role") == "cashier"
        self.detail_chart.title = "Kassirlar hisoboti"
        self.entity_table.blockSignals(True)
        self.entity_table.setRowCount(0)
        selected_row = 0 if rows else -1
        for row, item in enumerate(rows):
            self.entity_table.insertRow(row)
            name = item["entity_name"] or "Noma'lum"
            name_item = QTableWidgetItem(name)
            name_item.setData(Qt.ItemDataRole.UserRole, item["entity_id"])
            self.entity_table.setItem(row, 0, name_item)
            if current == item["entity_id"]:
                selected_row = row
            elif user_is_cashier and item["entity_id"] == (self.user or {}).get("id"):
                selected_row = row
        self.entity_table.blockSignals(False)
        if selected_row >= 0:
            self.entity_table.selectRow(selected_row)
            item = self.entity_table.item(selected_row, 0)
            self.selected_entity_id = item.data(Qt.ItemDataRole.UserRole) if item else None
            self._refresh_detail_chart()
        else:
            self.selected_entity_id = None
            self.detail_chart.set_data([])

    def _on_entity_selected(self):
        row = self.entity_table.currentRow()
        item = self.entity_table.item(row, 0) if row >= 0 else None
        self.selected_entity_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        self._refresh_detail_chart()

    def _refresh_detail_chart(self):
        if not self.selected_entity_id:
            self.detail_chart.set_data([])
            return
        start_date, end_date = self._date_range()
        # "cashier_salary" is the series that also reports expenses charged to
        # this cashier, so the salary figures below are net of them.
        rows = self._entity_series("cashier_salary", self.selected_entity_id, start_date, end_date)
        filled_rows = self._filled_series(rows, start_date, end_date)
        if self._selected_section_id():
            for row in filled_rows:
                row["expense"] = 0
                row["net_profit"] = row.get("profit", 0) or 0
            filled = filled_rows
        else:
            filled = self._with_entity_net_profit(filled_rows, start_date, end_date)

        # Update top summary cards for the selected cashier
        if hasattr(self, "summary_cards") and self.summary_cards:
            currency = self._selected_report_currency()
            gross_salary = sum(
                (row.get("cashier_reward", 0) or 0) for row in filled
            )
            salary_deduction = sum(
                (row.get("salary_deduction", 0) or 0) for row in filled
            )
            cashier_totals = {
                "revenue": sum(row.get("revenue", 0) or 0 for row in filled),
                "profit": sum(row.get("profit", 0) or 0 for row in filled),
                "count": sum(row.get("sales_count", 0) or 0 for row in filled),
                "products": sum(row.get("product_count", 0) or 0 for row in filled),
                # What the cashier is still owed: earned minus already taken.
                "salary": gross_salary - salary_deduction,
                "salary_deduction": salary_deduction,
            }
            cashier_totals["net_profit"] = cashier_totals["profit"]

            if "revenue" in self.summary_cards:
                self.summary_cards["revenue"].setText(self._format_money(cashier_totals["revenue"], currency))
            if "profit" in self.summary_cards:
                self.summary_cards["profit"].setText(self._format_money(cashier_totals["profit"], currency))
            if "count" in self.summary_cards:
                self.summary_cards["count"].setText(f"{cashier_totals['count']:,.0f}")
            if "products" in self.summary_cards:
                self.summary_cards["products"].setText(f"{cashier_totals['products']:,.0f}")
            if "net_profit" in self.summary_cards:
                self.summary_cards["net_profit"].setText(self._format_money(cashier_totals["net_profit"], currency))
            if "salary" in self.summary_cards:
                self.summary_cards["salary"].setText(self._format_money(cashier_totals["salary"], currency))
            self._apply_salary_card_hint(cashier_totals.get("salary_deduction", 0), currency)

        self._update_summary_card_visibility()

        entity = self._selected_entity()
        label = (entity["username"] or entity.get("email") or "") if entity else (
            self.entity_table.item(self.entity_table.currentRow(), 0).text()
            if self.entity_table.currentRow() >= 0
            else ""
        )
        if self.detail_metric == "cashier_salary":
            self.detail_chart.title = f"{label}: Ajratilgan oylik"
            self.detail_chart.color = "#ec4899"
            points = [
                (
                    row.get("display_label") or row["label"][5:],
                    self._converted_money(row.get("cashier_reward", 0) or row.get("total_salary", 0) or row.get("salary", 0)),
                )
                for row in filled
            ]
            self.detail_chart.set_data(points)
            return

        titles = {
            "all": "barcha ko'rsatkichlar",
            "revenue": "daromad",
            "profit": "foyda",
            "count": "sotuvlar soni",
            "products": "mahsulotlar",
            "net_profit": "foyda - harajatlar",
        }
        self.detail_chart.title = f"{label}: {titles.get(self.detail_metric, '')}"
        self._set_chart_data(self.detail_chart, filled, self.detail_metric)

    def _set_chart_data(self, chart, rows, metric):
        chart_rows = self._smooth_chart_rows(rows)
        if metric == "all":
            chart.set_series([
                {
                    "label": label,
                    "color": color,
                    "points": [(row.get("display_label") or row["label"][5:], self._metric_value(row, key)) for row in chart_rows],
                }
                for key, label, color in self._chart_metrics()
            ])
            return

        chart.color = self._metric_color(metric)
        points = []
        for row in chart_rows:
            value = self._metric_value(row, metric)
            points.append((row.get("display_label") or row["label"][5:], value))
        chart.set_data(points)

    def _smooth_chart_rows(self, rows):
        chart_rows = [dict(row) for row in rows]
        metric_keys = ("sales_count", "product_count", "revenue", "profit", "expense", "net_profit")
        for key in metric_keys:
            active_indexes = [
                index for index, row in enumerate(chart_rows)
                if (row.get(key, 0) or 0) != 0
            ]
            for left, right in zip(active_indexes, active_indexes[1:]):
                if right - left <= 1:
                    continue
                start_value = chart_rows[left].get(key, 0) or 0
                end_value = chart_rows[right].get(key, 0) or 0
                span = right - left
                for index in range(left + 1, right):
                    if (chart_rows[index].get(key, 0) or 0) != 0:
                        continue
                    ratio = (index - left) / span
                    chart_rows[index][key] = start_value + (end_value - start_value) * ratio
        return chart_rows

    def _metric_value(self, row, metric):
        if metric == "cashier_salary":
            return self._converted_money(row.get("total_salary", 0) or row.get("salary", 0))
        if metric == "net_profit":
            return self._converted_money(row["net_profit"])
        if metric == "count":
            return row["sales_count"]
        if metric == "products":
            return row["product_count"]
        if metric in ("revenue", "profit"):
            return self._converted_money(row[metric])
        return row[metric]

    def _selected_report_currency(self):
        return self.report_currency_combo.currentData() or {"code": "UZS", "rate_to_uzs": 1}

    def _selected_section_id(self):
        return self.section_combo.currentData() if hasattr(self, "section_combo") else None

    def _converted_money(self, value):
        currency = self._selected_report_currency()
        rate = currency.get("rate_to_uzs") or 1
        return (value or 0) / rate

    def _format_money(self, value, currency=None):
        currency = currency or self._selected_report_currency()
        code = currency.get("code") or "UZS"
        converted = (value or 0) / (currency.get("rate_to_uzs") or 1)
        if code == "UZS":
            unit = t("so'm", self.property("app_language") or "uz")
            return f"{converted:,.0f} {unit}"
        return f"{converted:,.2f} {code}"

    def _load_report_currency_combo(self, currencies=None):
        current = self.report_currency_combo.currentData() if hasattr(self, "report_currency_combo") else None
        self.report_currency_combo.blockSignals(True)
        self.report_currency_combo.clear()
        currencies = [dict(currency) for currency in (currencies if currencies is not None else db.get_currencies())]
        priority = {"UZS": 0, "USD": 1, "EUR": 2}
        currencies.sort(key=lambda currency: (priority.get(currency["code"], 10), currency["code"]))
        for currency in currencies:
            self.report_currency_combo.addItem(currency["code"], currency)
        if self.report_currency_combo.count() == 0:
            self.report_currency_combo.addItem("UZS", {"code": "UZS", "rate_to_uzs": 1})
        selected_code = current["code"] if current else db.get_app_settings().get("currency", "UZS")
        index = self.report_currency_combo.findText(selected_code, Qt.MatchFlag.MatchStartsWith)
        if index >= 0:
            self.report_currency_combo.setCurrentIndex(index)
        self.report_currency_combo.blockSignals(False)

    def _load_metric_combo(self):
        if not hasattr(self, "metric_combo"):
            return
        current = self.detail_metric
        language = self.property("app_language") or "uz"
        is_cashier_user = self.cashier_only or ((self.user or {}).get("role") == "cashier")
        if is_cashier_user:
            options = [
                ("count", "Sotuvlar soni"),
                ("products", "Mahsulotlar"),
                ("cashier_salary", "Oylik"),
            ]
        else:
            options = [
                ("all", "Hammasi"),
                ("revenue", "Daromad"),
                ("profit", "Foyda"),
                ("count", "Sotuvlar soni"),
                ("products", "Mahsulotlar"),
                ("net_profit", "Sof foyda"),
            ]
            if self.detail_mode == "cashier":
                options.append(("cashier_salary", "Kassirlar oyligi"))
        self.metric_combo.blockSignals(True)
        self.metric_combo.clear()
        for key, label in options:
            self.metric_combo.addItem(t(label, language), key)
        index = self.metric_combo.findData(current)
        if index < 0:
            index = 0
            self.detail_metric = self.metric_combo.itemData(index)
        self.metric_combo.setCurrentIndex(index)
        self.metric_combo.blockSignals(False)

    def _load_section_combo(self, sections=None):
        if not hasattr(self, "section_combo"):
            return
        current = self.section_combo.currentData()
        language = self.property("app_language") or "uz"
        self.section_combo.blockSignals(True)
        self.section_combo.clear()
        self.section_combo.addItem(t("Barcha bo'limlar", language), None)
        for section in sections if sections is not None else db.get_product_sections():
            self.section_combo.addItem(section["name"], section["id"])
        if current is not None:
            index = self.section_combo.findData(current)
            if index >= 0:
                self.section_combo.setCurrentIndex(index)
        self.section_combo.blockSignals(False)

    def _chart_metrics(self):
        is_cashier_user = self.cashier_only or ((self.user or {}).get("role") == "cashier")
        if is_cashier_user:
            return [
                ("count", "Sotuvlar soni", self._metric_color("count")),
                ("products", "Mahsulotlar", self._metric_color("products")),
                ("cashier_salary", "Oylik", self._metric_color("cashier_salary")),
            ]
        metrics = [
            ("revenue", "Daromad", self._metric_color("revenue")),
            ("profit", "Foyda", self._metric_color("profit")),
            ("count", "Sotuvlar soni", self._metric_color("count")),
            ("products", "Mahsulotlar", self._metric_color("products")),
            ("net_profit", "Sof foyda", self._metric_color("net_profit")),
        ]
        return metrics

    def _metric_color(self, metric):
        return {
            "all": "#334155",
            "revenue": "#2563eb",
            "profit": "#16a34a",
            "count": "#f97316",
            "products": "#8b5cf6",
            "net_profit": "#ef4444",
            "cashier_salary": "#ec4899",
        }.get(metric, "#3b82f6")

    def _date_range(self):
        selected = self.date_edit.date().toPyDate()
        period = self.period_combo.currentData()
        if period == "day":
            start = end = selected
        elif period == "week":
            start = selected - timedelta(days=selected.weekday())
            end = start + timedelta(days=6)
        elif period == "month":
            start = selected.replace(day=1)
            if selected.month == 12:
                end = selected.replace(year=selected.year + 1, month=1, day=1) - timedelta(days=1)
            else:
                end = selected.replace(month=selected.month + 1, day=1) - timedelta(days=1)
        elif period == "year":
            start = selected.replace(month=1, day=1)
            end = selected.replace(month=12, day=31)
        else:
            start = end = selected
        return start.isoformat(), end.isoformat()

    def _update_period_range_label(self, start_date, end_date):
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        period = self.period_combo.currentData()
        if period == "day":
            text = start.strftime("%d.%m.%Y")
        elif period == "week":
            text = f"{start.strftime('%d.%m.%Y')} - {end.strftime('%d.%m.%Y')}"
        elif period == "year":
            text = start.strftime("%Y")
        else:
            text = start.strftime("%m.%Y")
        self.period_range_lbl.setText(text)

    def _filled_series(self, rows, start_date, end_date):
        period = self.period_combo.currentData()
        grouped = {}
        for raw in rows:
            row = dict(raw)
            if period == "day":
                key = row.get("label")
                if not key:
                    continue
            else:
                try:
                    row_date = date.fromisoformat(row["label"])
                except (TypeError, ValueError):
                    continue
                key = self._period_key(row_date, period)
            item = grouped.setdefault(key, self._empty_report_row(key))
            item["sales_count"] += row.get("sales_count", 0) or 0
            item["product_count"] += row.get("product_count", 0) or 0
            item["revenue"] += row.get("revenue", 0) or 0
            item["profit"] += row.get("profit", 0) or 0
            item["cashier_reward"] += row.get("cashier_reward", 0) or 0
            item["salary"] += row.get("salary", 0) or 0
            item["total_salary"] += row.get("total_salary", 0) or row.get("salary", 0) or 0
            item["salary_deduction"] += row.get("salary_deduction", 0) or 0

        filled = []
        for key in self._period_keys(start_date, end_date, period):
            row = grouped.get(key, self._empty_report_row(key))
            row["display_label"] = self._period_display_label(key, period)
            filled.append(row)
        return filled

    def _empty_report_row(self, label):
        return {
            "label": label,
            "sales_count": 0,
            "product_count": 0,
            "revenue": 0,
            "profit": 0,
            "cashier_reward": 0,
            "salary": 0,
            "total_salary": 0,
            "salary_deduction": 0,
        }

    def _period_keys(self, start_date, end_date, period):
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        keys = []
        if period == "day":
            return [f"{hour:02d}:00" for hour in range(24)]
        if period == "week":
            current = start
            while current <= end:
                keys.append(current.isoformat())
                current += timedelta(days=1)
            return keys
        if period == "month":
            current = start
            while current <= end:
                keys.append(current.isoformat())
                current += timedelta(days=1)
            return keys
        if period == "year":
            current = start.replace(month=1, day=1)
            while current <= end:
                keys.append(current.strftime("%Y-%m"))
                current = current.replace(year=current.year + 1, month=1, day=1) if current.month == 12 else current.replace(month=current.month + 1, day=1)
            return keys
        current = start
        while current <= end:
            keys.append(current.isoformat())
            current += timedelta(days=1)
        return keys

    def _period_key(self, value, period):
        if period == "year":
            return value.strftime("%Y-%m")
        if period in ("week", "month"):
            return value.isoformat()
        if period == "day":
            return str(value)
        return value.isoformat()

    def _expense_period_key(self, label, period):
        if period == "day":
            return label
        row_date = date.fromisoformat(label)
        if period == "year":
            return row_date.strftime("%Y-%m")
        if period in ("week", "month"):
            return row_date.isoformat()
        return row_date.isoformat()

    def _entity_rows(self, entity_type, entity_id, start_date, end_date):
        section_id = self._selected_section_id()
        if self.period_combo.currentData() == "day":
            return db.get_entity_day_hourly_series(entity_type, entity_id, start_date, section_id)
        return db.get_entity_period_series(entity_type, entity_id, start_date, end_date, section_id)

    def _expense_rows(self, start_date, end_date, user_id=None, include_unassigned=False):
        # include_cashier=False: money charged to a cashier comes out of that
        # cashier's salary, so it must never move the shop's profit figures.
        if self.period_combo.currentData() == "day":
            return db.get_expense_hourly_report(
                start_date, user_id=user_id, include_unassigned=include_unassigned,
                include_cashier=False,
            )
        return db.get_expense_report(
            start_date, end_date, user_id=user_id, include_unassigned=include_unassigned,
            include_cashier=False,
        )

    def _period_display_label(self, key, period):
        if period == "day":
            return key
        if period in ("week", "month"):
            item = date.fromisoformat(key)
            if period == "week":
                return f"{self._weekday_label(item.weekday())} {item.strftime('%d.%m')}"
            return item.strftime("%d.%m")
        if period == "year":
            item = date.fromisoformat(f"{key}-01")
            return self._month_label(item.month)
        item = date.fromisoformat(key)
        return item.strftime("%d.%m")

    def _weekday_label(self, weekday):
        language = self.property("app_language") or "uz"
        labels = {
            "uz": ["Dush", "Sesh", "Chor", "Pay", "Jum", "Shan", "Yak"],
            "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            "ru": ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"],
        }
        return labels.get(language, labels["uz"])[weekday]

    def _month_label(self, month):
        language = self.property("app_language") or "uz"
        labels = {
            "uz": ["Yan", "Fev", "Mar", "Apr", "May", "Iyun", "Iyul", "Avg", "Sen", "Okt", "Noy", "Dek"],
            "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
            "ru": ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"],
        }
        return labels.get(language, labels["uz"])[month - 1]

    def _with_net_profit(self, rows, start_date, end_date):
        expenses = self._expense_totals_by_period(start_date, end_date)
        for row in rows:
            row["expense"] = expenses.get(row["label"], 0)
            row["net_profit"] = (row["profit"] or 0) - (row["expense"] or 0)
        return rows

    def _with_net_profit_from_expenses(self, rows, expense_rows, currencies, section_id=None, start_date=None, end_date=None):
        rates = {currency["code"]: currency["rate_to_uzs"] or 1 for currency in currencies}
        
        ratio = 1.0
        if section_id and start_date and end_date:
            section_rev = sum(r.get("revenue", 0) or 0 for r in rows)
            period = self.period_combo.currentData()
            all_rows = db.get_overall_period_series(start_date, end_date) if period != "day" else db.get_overall_day_hourly_series(start_date)
            total_rev = sum(r.get("revenue", 0) or 0 for r in all_rows)
            ratio = (section_rev / total_rev) if total_rev > 0 else 1.0
            ratio = max(0.0, min(1.0, ratio))

        totals = {}
        for expense in expense_rows:
            try:
                label = self._expense_period_key(expense["label"], self.period_combo.currentData())
            except (TypeError, ValueError):
                continue
            currency = expense["currency_code"] or "UZS"
            totals[label] = totals.get(label, 0) + (expense["amount"] or 0) * (rates.get(currency, 1) or 1) * ratio
        for row in rows:
            row["expense"] = totals.get(row["label"], 0)
            row["net_profit"] = (row["profit"] or 0) - (row["expense"] or 0)
        return rows

    def _with_entity_net_profit(self, rows, start_date, end_date):
        entity = self._selected_entity()
        if entity and entity.get("role") == "admin":
            expenses = self._expense_totals_by_period(start_date, end_date, user_id=entity["id"], include_unassigned=True)
            for row in rows:
                row["expense"] = expenses.get(row["label"], 0)
                row["net_profit"] = (row["profit"] or 0) - (row["expense"] or 0)
            return rows
        for row in rows:
            row["expense"] = 0
            row["net_profit"] = row.get("profit", 0) or 0
        return rows

    @staticmethod
    def _cashier_cost(row):
        # The full reward the sales earned the cashier. Whether part of it was
        # already handed over as an expense changes who holds the money, not
        # what the shop paid, so the deduction is not applied here.
        if "cashier_reward" in row:
            return row.get("cashier_reward") or 0
        return row.get("total_salary", 0) or row.get("salary", 0) or 0

    def _expense_totals_by_period(self, start_date, end_date, user_id=None, include_unassigned=False):
        rates = {currency["code"]: currency["rate_to_uzs"] or 1 for currency in db.get_currencies()}
        totals = {}
        expense_rows = self._expense_rows(start_date, end_date, user_id=user_id, include_unassigned=include_unassigned)
        for row in expense_rows:
            try:
                label = self._expense_period_key(row["label"], self.period_combo.currentData())
            except (TypeError, ValueError):
                continue
            currency = row["currency_code"] or "UZS"
            totals[label] = totals.get(label, 0) + (row["amount"] or 0) * (rates.get(currency, 1) or 1)
        return totals

    def _selected_entity(self):
        entity_id = self.selected_entity_id
        if not entity_id:
            return None
        for user in db.get_users():
            if user["id"] == entity_id:
                return user
        return None

    def _overall_series(self, start_date, end_date):
        return db.get_overall_period_series(start_date, end_date)

    def _entity_series(self, entity_type, entity_id, start_date, end_date):
        return self._entity_rows(entity_type, entity_id, start_date, end_date)

    def _aggregate_monthly(self, rows):
        grouped = {}
        for row in rows:
            label = row["label"][:7]
            item = grouped.setdefault(label, {
                "label": label,
                "sales_count": 0,
                "product_count": 0,
                "revenue": 0,
                "profit": 0,
            })
            item["sales_count"] += row["sales_count"] or 0
            item["product_count"] += row["product_count"] or 0
            item["revenue"] += row["revenue"] or 0
            item["profit"] += row["profit"] or 0
        return [grouped[key] for key in sorted(grouped)]

    def _shift_period(self, direction):
        current = self.date_edit.date()
        period = self.period_combo.currentData()
        if period == "day":
            next_date = current.addDays(direction)
        elif period == "week":
            next_date = current.addDays(direction * 7)
        elif period == "month":
            next_date = current.addMonths(direction)
        else:
            next_date = current.addYears(direction)
        self.date_edit.setDate(next_date)

    def _period_changed(self):
        period = self.period_combo.currentData()
        if period == "day":
            self.date_edit.setDisplayFormat("dd.MM.yyyy")
            self.date_edit.setFixedWidth(150)
        elif period == "year":
            self.date_edit.setDisplayFormat("yyyy")
            self.date_edit.setFixedWidth(110)
        else:
            self.date_edit.setDisplayFormat("dd.MM.yyyy")
            self.date_edit.setFixedWidth(150)
        self.load_data()

    def _mode_button(self, label, mode):
        btn = QPushButton(label)
        btn.setCheckable(True)
        btn.setFixedHeight(34)
        btn.setStyleSheet(self._toggle_style())
        btn.clicked.connect(lambda checked: self._set_detail_mode(mode))
        return btn

    def _set_detail_mode(self, mode):
        if self.cashier_only:
            mode = "cashier"
        if mode not in {"overall", "cashier"}:
            mode = "overall"
        self.detail_mode = mode
        self.selected_entity_id = None
        self._load_metric_combo()
        self._sync_buttons()
        self._update_summary_card_visibility()
        self.load_data()

    def _update_summary_card_visibility(self):
        if not hasattr(self, "summary_card_frames"):
            return
        is_cashier_user = self.cashier_only or ((self.user or {}).get("role") == "cashier")
        if is_cashier_user:
            for key, card in self.summary_card_frames.items():
                card.setVisible(key in ("count", "products", "salary"))
        else:
            is_cashier_mode = self.detail_mode == "cashier"
            for key, card in self.summary_card_frames.items():
                card.setVisible(True if is_cashier_mode else (key != "salary"))

    def _set_detail_metric(self, metric):
        self.detail_metric = metric
        self._sync_buttons()
        if self.detail_mode == "overall":
            start_date, end_date = self._date_range()
            self._refresh_report_panel(start_date, end_date, self.overall_rows)
        else:
            self._refresh_detail_chart()

    def _report_type_changed(self, _index):
        mode = self.report_type_combo.currentData()
        if mode:
            self._set_detail_mode(mode)

    def _metric_changed(self, _index):
        metric = self.metric_combo.currentData()
        if metric:
            self._set_detail_metric(metric)

    def _sync_buttons(self):
        if hasattr(self, "report_type_combo"):
            self.report_type_combo.blockSignals(True)
            index = self.report_type_combo.findData(self.detail_mode)
            if index >= 0:
                self.report_type_combo.setCurrentIndex(index)
            self.report_type_combo.blockSignals(False)
        if hasattr(self, "metric_combo"):
            self.metric_combo.blockSignals(True)
            index = self.metric_combo.findData(self.detail_metric)
            if index >= 0:
                self.metric_combo.setCurrentIndex(index)
            self.metric_combo.blockSignals(False)

    def _language_changed(self, language):
        self.report_type_lbl.setText(t("Hisobot turi:", language))
        self.section_lbl.setText(t("Bo'lim:", language))
        self.metric_lbl.setText(t("Grafik:", language))
        report_labels = {
            "overall": "Umumiy hisobot",
            "cashier": "Kassirlar hisoboti",
        }
        for index in range(self.report_type_combo.count()):
            self.report_type_combo.setItemText(index, t(report_labels[self.report_type_combo.itemData(index)], language))
        self._load_metric_combo()
        self._load_section_combo()

    def _money_item(self, value):
        item = QTableWidgetItem(f"{value:,.0f} so'm")
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return item


    def _toggle_style(self, theme=None):
        if theme:
            return f"""
            QPushButton {{ background:{theme['topbar']};color:{theme['title']};border:1px solid #cbd5e1;
                          border-radius:6px;padding:0 12px;font-size:12px;font-weight:bold; }}
            QPushButton:hover {{ background:{theme['content']}; }}
            QPushButton:pressed {{ background:{theme['sidebar_alt']};color:{theme['nav_text']};padding-top:2px; }}
            QPushButton:checked {{ background:{theme['accent']};color:{theme['nav_active']};border-color:{theme['accent']}; }}
        """
        return """
            QPushButton { background:white;color:#334155;border:1px solid #cbd5e1;
                          border-radius:6px;padding:0 12px;font-size:12px;font-weight:bold; }
            QPushButton:hover { background:#f8fafc; }
            QPushButton:pressed { background:#e2e8f0;padding-top:2px; }
            QPushButton:checked { background:#3b82f6;color:white;border-color:#3b82f6; }
        """

    def _metric_toggle_style(self, metric):
        color = self._metric_color(metric)
        if metric == "all":
            return """
                QPushButton {{
                    background:white;
                    color:#334155;
                    border:1px solid #cbd5e1;
                    border-radius:6px;
                    padding:0 12px;
                    font-size:12px;
                    font-weight:bold;
                }}
                QPushButton:hover {{
                    border-color:#94a3b8;
                    background:#f8fafc;
                }}
                QPushButton:pressed {{
                    background:#e2e8f0;
                    padding-top:2px;
                }}
                QPushButton:checked {{
                    background:#3b82f6;
                    color:white;
                    border-color:#3b82f6;
                }}
            """
        return f"""
            QPushButton {{
                background:white;
                color:{color};
                border:1px solid {color};
                border-radius:6px;
                padding:0 12px;
                font-size:12px;
                font-weight:bold;
            }}
            QPushButton:hover {{
                background:#f8fafc;
                border-color:{color};
            }}
            QPushButton:pressed {{
                background:#e2e8f0;
                padding-top:2px;
            }}
            QPushButton:checked {{
                background:{color};
                color:white;
                border-color:{color};
            }}
        """

    def _table_style(self, theme=None):
        if theme:
            return f"""
            QTableWidget{{background:{theme['topbar']};color:{theme['title']};border:1px solid #e2e8f0;border-radius:8px;font-size:13px;}}
            QTableWidget::item{{padding:7px 10px;}}
            QTableWidget::item:selected{{background:{theme['accent']};color:{theme['nav_active']};}}
            QTableWidget::item:focus{{outline:none;border:none;}}
            QHeaderView::section{{background:{theme['content']};border:none;border-bottom:1px solid #e2e8f0;padding:8px;font-weight:bold;color:{theme['muted']};}}
            QTableWidget::item:alternate{{background:{theme['content']};}}
        """

    def _entity_table_style(self, theme=None):
        if theme:
            return f"""
            QTableWidget{{background:{theme['topbar']};color:{theme['title']};border:1px solid #e2e8f0;border-radius:8px;font-size:13px;selection-background-color:{theme['accent']};selection-color:{theme['nav_active']};}}
            QTableWidget::item{{padding:7px 10px;color:{theme['title']};}}
            QTableWidget::item:selected{{background:{theme['accent']};color:{theme['nav_active']};}}
            QTableWidget::item:focus{{outline:none;border:1px solid {theme['accent']};color:{theme['nav_active']};background:{theme['accent']};}}
            QHeaderView::section{{background:{theme['content']};border:none;border-bottom:1px solid #e2e8f0;padding:8px;font-weight:bold;color:{theme['muted']};}}
            QTableWidget::item:alternate{{background:{theme['content']};color:{theme['title']};}}
            """
        return """
            QTableWidget{background:white;color:#111827;border:1px solid #e2e8f0;border-radius:8px;font-size:13px;selection-background-color:#3b82f6;selection-color:white;}
            QTableWidget::item{padding:7px 10px;color:#111827;}
            QTableWidget::item:selected{background:#3b82f6;color:white;}
            QTableWidget::item:focus{outline:none;border:1px solid #3b82f6;background:#3b82f6;color:white;}
            QHeaderView::section{background:#f8fafc;border:none;border-bottom:1px solid #e2e8f0;padding:8px;font-weight:bold;color:#64748b;}
            QTableWidget::item:alternate{background:#f8fafc;color:#111827;}
        """


class SalesDetailsWidget(QWidget):
    def __init__(self, user=None, cashier_only=False):
        super().__init__()
        self.user = user or {}
        self.cashier_only = bool(cashier_only)
        self._async_loader = None
        self._last_rows = []
        self._last_deductions = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self.progress_bar = make_progress_bar()
        layout.addWidget(self.progress_bar)
        self._async_loader = AsyncDataLoader(self, self.progress_bar)

        filters = QHBoxLayout()
        filters.setSpacing(8)
        self.cashier_lbl = QLabel("Kassir:")
        self.cashier_combo = QComboBox()
        self.cashier_combo.setMinimumWidth(220)
        self.cashier_combo.setFixedHeight(36)
        self.section_lbl = QLabel("Bo'lim:")
        self.section_combo = QComboBox()
        self.section_combo.setMinimumWidth(180)
        self.section_combo.setFixedHeight(36)
        self.date_lbl = QLabel("Sana:")
        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("dd.MM.yyyy")
        self.date_edit.setFixedSize(150, 36)
        calendar = QCalendarWidget(self)
        calendar.setNavigationBarVisible(True)
        calendar.setGridVisible(True)
        calendar.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        self.date_edit.setCalendarWidget(calendar)

        self.period_combo = QComboBox()
        self.period_combo.addItem("Kunlik", "day")
        self.period_combo.addItem("Haftalik", "week")
        self.period_combo.addItem("Oylik", "month")
        self.period_combo.addItem("Yillik", "year")
        self.period_combo.setCurrentIndex(self.period_combo.findData("month"))
        self.period_combo.setMinimumWidth(110)
        self.period_combo.setFixedHeight(36)
        self.prev_period_btn = QPushButton("<")
        self.prev_period_btn.setFixedSize(36, 36)
        self.next_period_btn = QPushButton(">")
        self.next_period_btn.setFixedSize(36, 36)
        self.today_btn = QPushButton("Bugun")
        self.today_btn.setFixedHeight(36)
        self.today_btn.setMinimumWidth(76)
        self.currency_combo = QComboBox()
        self.currency_combo.setMinimumWidth(88)
        self.currency_combo.setFixedHeight(36)

        filters.addWidget(self.cashier_lbl)
        filters.addWidget(self.cashier_combo)
        filters.addSpacing(8)
        filters.addWidget(self.section_lbl)
        filters.addWidget(self.section_combo)
        filters.addStretch()
        filters.addWidget(self.prev_period_btn)
        filters.addWidget(self.date_lbl)
        filters.addWidget(self.date_edit)
        filters.addWidget(self.period_combo)
        filters.addWidget(self.next_period_btn)
        filters.addWidget(self.today_btn)
        filters.addWidget(self.currency_combo)
        layout.addLayout(filters)

        self.summary_cards = {}
        self.summary_card_frames = {}
        if not self.cashier_only:
            self.summary_layout = QGridLayout()
            self.summary_layout.setSpacing(12)
            for index, (key, title, color) in enumerate([
                ("revenue", "Daromad", "#059669"),
                ("profit", "Foyda", "#8b5cf6"),
                ("count", "Sotuvlar soni", "#3b82f6"),
                ("products", "Mahsulotlar soni", "#0ea5e9"),
                ("net_profit", "Sof foyda", "#f59e0b"),
                ("salary", "Oylik", "#ec4899"),
            ]):
                card = QFrame()
                card.setObjectName(f"details_summary_{key}")
                card.setProperty("accent_color", color)
                card.setFixedHeight(72)
                card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                card.setStyleSheet(f"""
                    QFrame#details_summary_{key} {{
                        background:white;
                        border-left:4px solid {color};
                        border-top:1px solid #e2e8f0;
                        border-right:1px solid #e2e8f0;
                        border-bottom:1px solid #e2e8f0;
                        border-radius:8px;
                    }}
                """)
                card_layout = QVBoxLayout(card)
                card_layout.setContentsMargins(14, 8, 14, 8)
                card_layout.setSpacing(4)
                title_lbl = QLabel(title)
                title_lbl.setObjectName("summary_title")
                title_lbl.setStyleSheet("color:#64748b;font-size:11px;background:transparent;border:none;")
                value_lbl = QLabel("0")
                value_lbl.setObjectName("summary_value")
                value_lbl.setProperty("accent_color", color)
                value_lbl.setStyleSheet(f"color:{color};font-size:14px;font-weight:bold;background:transparent;border:none;")
                value_lbl.setWordWrap(False)
                value_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                card_layout.addWidget(title_lbl)
                card_layout.addWidget(value_lbl)
                if key == "salary":
                    hint_lbl = QLabel("")
                    hint_lbl.setObjectName("summary_hint")
                    hint_lbl.setProperty("i18n_skip", True)
                    hint_lbl.setStyleSheet("color:#b91c1c;font-size:10px;font-weight:bold;background:transparent;border:none;")
                    hint_lbl.setVisible(False)
                    card_layout.addWidget(hint_lbl)
                card_layout.addStretch()
                self.summary_cards[key] = value_lbl
                self.summary_card_frames[key] = card
                self.summary_layout.addWidget(card, 0, index)
            layout.addLayout(self.summary_layout)


        summary = QHBoxLayout()
        self.summary_title_lbl = QLabel("Sotuv tafsilotlari")
        self.summary_title_lbl.setStyleSheet("font-size:16px;font-weight:bold;color:#0f172a;")
        self.summary_stats_lbl = QLabel("")
        self.summary_stats_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.summary_stats_lbl.setStyleSheet("font-size:13px;font-weight:bold;color:#475569;")
        self.summary_stats_lbl.hide()
        summary.addWidget(self.summary_title_lbl)
        summary.addStretch()
        summary.addWidget(self.summary_stats_lbl)
        layout.addLayout(summary)

        self.table = QTableWidget()
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels([
            "Sana", "Mahsulot", "Shtrix-kod", "Miqdor",
            "Narx", "Jami", "Kassirga ajratildi", "Kassir", "Holati",
        ])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column, width in [
            (0, 105), (2, 130), (3, 72), (4, 125),
            (5, 135), (6, 150), (7, 150), (8, 82),
        ]:
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(column, width)
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.verticalHeader().setFixedWidth(46)
        self.table.verticalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.verticalHeader().setStyleSheet(self._vertical_header_style())
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(False)
        self.table.setStyleSheet(self._table_style())
        layout.addWidget(self.table, 1)

        self._load_cashiers(initial=True)
        self._load_sections()
        self._load_currencies()
        self.cashier_combo.currentIndexChanged.connect(self.load_data)
        self.section_combo.currentIndexChanged.connect(self.load_data)
        self.date_edit.dateChanged.connect(self.load_data)
        self.period_combo.currentIndexChanged.connect(self.load_data)
        self.currency_combo.currentIndexChanged.connect(lambda _: self._fill_table(self._last_rows))
        self.prev_period_btn.clicked.connect(lambda: self._shift_period(-1))
        self.next_period_btn.clicked.connect(lambda: self._shift_period(1))
        self.today_btn.clicked.connect(lambda: self.date_edit.setDate(QDate.currentDate()))
        self.load_data()

    def showEvent(self, event):
        super().showEvent(event)
        self.load_data()

    def load_data(self, *_args):
        # Cashiers can arrive from another device while this page is already
        # open. Refresh the list on every server-driven page reload while
        # preserving the current selection.
        self._load_cashiers()
        cashier_id = self.cashier_combo.currentData()
        start_date, end_date = self._date_range()
        section_id = self.section_combo.currentData()

        def fetch():
            rows = [dict(row) for row in db.get_cashier_sales_details(
                cashier_id,
                start_date,
                end_date,
                section_id,
                only_cashiers=True,
            )]
            # Cashier-charged expenses are not tied to a product section, so a
            # section filter leaves them out rather than mis-attributing them.
            deductions = [] if section_id else [
                dict(row) for row in db.get_cashier_expense_entries(
                    start_date, end_date, cashier_id
                )
            ]
            return {"rows": rows, "deductions": deductions}

        if self.isVisible():
            self._async_loader.start(fetch, self._apply_details_data)
        else:
            self._apply_details_data(fetch())

    def _apply_salary_card_hint(self, deduction, gross_salary):
        """Annotate the "Oylik" card with the amount already taken as expenses."""
        label = self.summary_cards.get("salary")
        card = self.summary_card_frames.get("salary")
        if label is None:
            return
        language = self.property("app_language") or "uz"
        deduction = deduction or 0
        if deduction > 0:
            label.setToolTip(
                f"{t('Jami ajratildi', language)}: {self._format_money(gross_salary)}\n"
                f"{t('Kassir harajatlari', language)}: -{self._format_money(deduction)}"
            )
        else:
            label.setToolTip("")
        if card is None:
            return
        hint = card.findChild(QLabel, "summary_hint")
        if hint is None:
            return
        hint.setProperty("i18n_skip", True)
        hint.setText(
            f"− {self._format_money(deduction)} {t('harajat', language)}" if deduction > 0 else ""
        )
        hint.setVisible(deduction > 0)

    def _apply_details_data(self, data):
        self._last_deductions = list(data.get("deductions") or [])
        self._fill_table(data.get("rows") or [])

    def _total_deduction(self):
        return sum(
            (row.get("amount_uzs", 0) or 0) for row in getattr(self, "_last_deductions", [])
        )

    def _expense_table_rows(self):
        """Turn cashier-charged expenses into rows for the sales table.

        They belong next to the sales, not in a banner above them: the money
        left the cashier's salary on a given day just like a sale added to it.
        """
        language = self.property("app_language") or "uz"
        show_owner = self.cashier_combo.currentData() is None
        rows = []
        for entry in getattr(self, "_last_deductions", []):
            amount = entry.get("amount_uzs", 0) or 0
            if amount <= 0:
                continue
            label = entry.get("description") or t("Kassir harajati", language)
            owner = entry.get("cashier_name") or ""
            rows.append({
                "is_expense": True,
                "created_at": entry.get("created_at") or "",
                "product_name": f"{owner} · {label}" if (show_owner and owner) else label,
                "barcode": "-",
                "expense_amount": amount,
                "expense_currency_amount": entry.get("amount", 0) or 0,
                "expense_currency_code": entry.get("currency_code") or "UZS",
                "cashier_name": owner,
                "category_name": entry.get("category_name") or "",
            })
        return rows

    def _fill_table(self, rows):
        raw_rows = [dict(row) for row in rows]
        self._last_rows = raw_rows
        
        # Only count successfully finalized sales with positive net quantity for metrics
        finalized_raw = [
            r for r in raw_rows
            if bool(r.get("is_finalized")) and (r.get("net_quantity", 0) or 0) > 0
        ]
        
        rows = self._group_sales_rows(raw_rows)
        # Only show sold products and products pending confirmation (exclude returned products)
        rows = [
            r for r in rows
            if (r.get("net_quantity", 0) or 0) > 0
        ]
        # Cashier expenses sit in the same list as the sales, ordered by time.
        rows = sorted(
            rows + self._expense_table_rows(),
            key=lambda row: row.get("created_at") or "",
            reverse=True,
        )
        self.table.setRowCount(0)
        self.table.setUpdatesEnabled(False)
        language = self.property("app_language") or "uz"

        # Update the 6 summary cards with finalized successful sales only
        distinct_sales = {r.get("sale_id") for r in finalized_raw if r.get("sale_id")}
        sales_count = len(distinct_sales)
        products_count = sum(r.get("net_quantity", 0) or 0 for r in finalized_raw)
        revenue_uzs = sum(r.get("item_total_after_discount", 0) or 0 for r in finalized_raw)
        cost_uzs = sum((r.get("cost", 0) or 0) * (r.get("net_quantity", 0) or 0) for r in finalized_raw)
        profit_uzs = max(0, revenue_uzs - cost_uzs)
        gross_salary_uzs = sum(r.get("cashier_reward", 0) or 0 for r in finalized_raw)
        # Money already handed to the cashier as a "Kassir" expense is taken
        # off once, here; later sales keep adding to the salary as normal.
        deduction_uzs = self._total_deduction()
        # Deliberately not clamped: when the expenses exceed what the sales have
        # earned so far, the cashier owes the difference back and must see it.
        salary_uzs = gross_salary_uzs - deduction_uzs
        net_profit_uzs = profit_uzs

        if hasattr(self, "summary_cards"):
            if "revenue" in self.summary_cards:
                self.summary_cards["revenue"].setText(self._format_money(revenue_uzs))
            if "profit" in self.summary_cards:
                self.summary_cards["profit"].setText(self._format_money(profit_uzs))
            if "count" in self.summary_cards:
                self.summary_cards["count"].setText(f"{sales_count:,.0f}")
            if "products" in self.summary_cards:
                self.summary_cards["products"].setText(f"{products_count:,.0f}")
            if "net_profit" in self.summary_cards:
                self.summary_cards["net_profit"].setText(self._format_money(net_profit_uzs))
            if "salary" in self.summary_cards:
                self.summary_cards["salary"].setText(self._format_money(salary_uzs))
                self._apply_salary_card_hint(deduction_uzs, gross_salary_uzs)

        finalized_table_rows = [
            r for r in rows
            if not r.get("is_expense")
            and bool(r.get("is_finalized"))
            and (r.get("net_quantity", 0) or 0) > 0
        ]
        total_quantity = sum(row.get("net_quantity", 0) or 0 for row in finalized_table_rows)
        total_returns = sum(
            row.get("returned_quantity", 0) or 0 for row in rows if not row.get("is_expense")
        )
        total_value = sum(row.get("item_total_after_discount", 0) or 0 for row in finalized_table_rows)
        total_cashier_reward = sum(row.get("cashier_reward", 0) or 0 for row in finalized_table_rows)
        net_cashier_reward = total_cashier_reward - deduction_uzs
        if rows:
            summary_values = [
                "",
                "",
                "",
                f"{total_quantity:g}",
                "",
                self._format_money(total_value),
                self._format_money(net_cashier_reward)
                if (total_cashier_reward > 0 or deduction_uzs > 0) else "-",
                "",
                "",
            ]
            self.table.insertRow(0)
            self.table.setVerticalHeaderItem(0, QTableWidgetItem(""))
            for column, value in enumerate(summary_values):
                item = QTableWidgetItem(value)
                item.setBackground(QColor("#dbeafe"))
                item.setForeground(QColor("#1e3a8a"))
                font = item.font()
                font.setBold(True)
                item.setFont(font)
                if column in (3, 4, 5, 6):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                elif column in (0, 2, 7, 8):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if column == 6 and deduction_uzs > 0:
                    item.setForeground(QColor("#991b1b"))
                    item.setToolTip(
                        f"{t('Jami ajratildi', language)}: {self._format_money(total_cashier_reward)}\n"
                        f"{t('Kassir harajatlari', language)}: -{self._format_money(deduction_uzs)}\n"
                        f"{t('Qolgan oylik', language)}: {self._format_money(net_cashier_reward)}"
                    )
                self.table.setItem(0, column, item)
            self.table.setRowHeight(0, 48)

        for row_index, data in enumerate(rows):
            table_row = row_index + 1
            self.table.insertRow(table_row)
            self.table.setVerticalHeaderItem(table_row, QTableWidgetItem(str(row_index + 1)))
            returned = data.get("returned_quantity", 0) or 0
            sold = data.get("sold_quantity", 0) or 0
            net_quantity = data.get("net_quantity", max(0, sold - returned)) or 0
            payment = data.get("payment_method") or ""
            is_finalized = bool(data.get("is_finalized"))
            cashier_reward = data.get("cashier_reward", 0) or 0

            if data.get("is_expense"):
                self._fill_expense_row(table_row, data, language)
                continue

            if not is_finalized:
                status_key, row_hex, status_hex, status_text = (
                    "Hali yakunlanmagan", "#fffbeb", "#fef3c7", "#92400e"
                )
            else:
                status_key = "Yakunlangan"
                row_hex = {
                    "naqd": "#ecfdf5",
                    "plastik karta": "#eff6ff",
                    "qarz": "#fff7ed",
                }.get(payment, "#f8fafc")
                status_hex, status_text = "#bbf7d0", "#166534"

            status_value = self._status_icon(status_key)
            values = [
                self._compact_details_time(data.get("created_at")),
                str(data.get("product_name") or "-"),
                str(data.get("barcode") or "-"),
                f"{net_quantity:g}",
                self._format_money(data.get("price", 0)),
                self._format_money(data.get("item_total_after_discount", 0)),
                self._format_money(cashier_reward) if cashier_reward > 0 else "-",
                str(data.get("cashier_name") or "-"),
                status_value,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setBackground(QColor(row_hex))
                item.setForeground(QColor("#1e293b"))
                if column in (3, 4, 5, 6):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                elif column in (0, 2, 7, 8):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if column == 8:
                    item.setBackground(QColor(status_hex))
                    item.setForeground(QColor(status_text))
                    item.setToolTip(t(status_key, language))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.table.setItem(table_row, column, item)

        self.table.setUpdatesEnabled(True)
        cashier_name_raw = self.cashier_combo.currentText()
        language = self.property("app_language") or "uz"
        # Don't show "Barcha kassirlar" as a name prefix in the title
        cashier_name = cashier_name_raw if self.cashier_combo.currentData() is not None else ""
        title = t("Sotuv tafsilotlari", language)
        self.summary_title_lbl.setProperty("i18n_skip", True)
        self.summary_title_lbl.setText(f"{cashier_name} · {title}" if cashier_name else title)
        self.summary_stats_lbl.setProperty("i18n_skip", True)
        self.summary_stats_lbl.setText("")

    # Red, because money left the cashier's salary. The badge is a solid red
    # chip rather than the pale tint "Qaytarilgan" uses, so the two red states
    # still read as different things at a glance.
    EXPENSE_ROW_HEX = "#fef2f2"
    EXPENSE_STATUS_HEX = "#dc2626"
    EXPENSE_STATUS_TEXT = "#ffffff"
    EXPENSE_TEXT = "#b91c1c"
    EXPENSE_AMOUNT_TEXT = "#b91c1c"

    def _fill_expense_row(self, table_row, data, language):
        amount = data.get("expense_amount", 0) or 0
        original = data.get("expense_currency_amount", 0) or 0
        code = data.get("expense_currency_code") or "UZS"
        status_key = "Harajat"
        values = [
            self._compact_details_time(data.get("created_at")),
            str(data.get("product_name") or "-"),
            "-",
            "-",
            "-",
            "-",
            f"-{self._format_money(amount)}",
            str(data.get("cashier_name") or "-"),
            "\U0001f4b8",
        ]
        tooltip = f"{t('Kassir harajati', language)}: -{self._format_money(amount)}"
        if code != "UZS" and original:
            tooltip += f"\n{original:,.2f} {code}"
        if data.get("cashier_name"):
            tooltip += f"\n{t('Kassir:', language)} {data['cashier_name']}"
        tooltip += f"\n{t('Kassirga ajratildi', language)} {t('summasidan ayrildi', language)}"

        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setBackground(QColor(self.EXPENSE_ROW_HEX))
            item.setForeground(QColor("#1e293b"))
            item.setToolTip(tooltip)
            if column in (3, 4, 5, 6):
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            elif column in (0, 2, 7, 8):
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if column == 6:
                item.setForeground(QColor(self.EXPENSE_AMOUNT_TEXT))
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            if column == 1:
                item.setForeground(QColor(self.EXPENSE_TEXT))
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            if column == 8:
                item.setBackground(QColor(self.EXPENSE_STATUS_HEX))
                item.setForeground(QColor(self.EXPENSE_STATUS_TEXT))
                item.setToolTip(t(status_key, language))
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            self.table.setItem(table_row, column, item)

    def _group_sales_rows(self, rows):
        states = self._sales_return_states(rows)
        rows = self._current_sales_rows(rows)
        grouped = {}
        for data in rows:
            is_fin = bool(data.get("is_finalized"))
            key = self._sales_product_key(data)
            item = grouped.setdefault(key, {
                "product_id": data.get("product_id"),
                "product_name": data.get("product_name") or "-",
                "barcode": data.get("barcode") or "-",
                "sold_quantity": 0,
                "returned_quantity": 0,
                "net_quantity": 0,
                "price": data.get("price", 0) or 0,
                "item_total_after_discount": 0,
                "cashier_reward": 0,
                "cashier_id": data.get("cashier_id"),
                "cashier_name": data.get("cashier_name") or "-",
                "payment_method": "",
                "payment_methods": set(),
                "is_finalized": int(is_fin),
                "created_at": data.get("created_at") or "",
            })
            item["is_finalized"] = int(bool(item["is_finalized"]) and is_fin)
            item["sold_quantity"] += data.get("sold_quantity", 0) or 0
            item["net_quantity"] += data.get("net_quantity", 0) or 0
            item["item_total_after_discount"] += data.get("item_total_after_discount", 0) or 0
            item["cashier_reward"] += data.get("cashier_reward", 0) or 0
            if data.get("payment_method"):
                item["payment_methods"].add(data.get("payment_method"))
            created_at = data.get("created_at") or ""
            if created_at > (item.get("created_at") or ""):
                item["created_at"] = created_at
                item["price"] = data.get("price", 0) or 0
                item["cashier_name"] = data.get("cashier_name") or item["cashier_name"]
        for key, item in grouped.items():
            state = states.get(key, {})
            item["sold_quantity"] = state.get("sold_quantity", item["sold_quantity"])
            item["net_quantity"] = state.get("net_quantity", item["net_quantity"])
            item["returned_quantity"] = state.get("outstanding_returns", 0)
            methods = item.pop("payment_methods")
            item["payment_method"] = next(iter(methods)) if len(methods) == 1 else ""
        return sorted(
            grouped.values(),
            key=lambda row: row.get("created_at") or "",
            reverse=True,
        )

    @staticmethod
    def _sales_product_key(data):
        product_key = data.get("product_id") or (
            data.get("product_name") or "-",
            data.get("barcode") or "-",
        )
        return data.get("cashier_id"), product_key

    @classmethod
    def _sales_return_states(cls, rows):
        states = {}
        for data in rows:
            key = cls._sales_product_key(data)
            state = states.setdefault(key, {
                "sold_quantity": 0,
                "net_quantity": 0,
                "returned_quantity": 0,
                "events": [],
            })
            sold = data.get("sold_quantity", 0) or 0
            returned = data.get("returned_quantity", 0) or 0
            net_quantity = data.get("net_quantity", max(0, sold - returned)) or 0
            # Only ever a tiebreaker for rows sharing a timestamp -- ids are
            # UUIDs now, so compare them as text rather than as numbers.
            item_id = str(data.get("sale_item_id") or "")
            state["sold_quantity"] += sold
            state["net_quantity"] += net_quantity
            state["returned_quantity"] += returned
            state["events"].append((str(data.get("created_at") or ""), 0, item_id, sold))
            if returned > 0:
                returned_at = data.get("returned_at") or data.get("created_at") or ""
                state["events"].append((str(returned_at), 1, item_id, returned))

        for state in states.values():
            outstanding = 0
            for _event_time, event_type, _item_id, quantity in sorted(state.pop("events")):
                if event_type == 0:
                    outstanding = max(0, outstanding - quantity)
                else:
                    outstanding += quantity
            state["outstanding_returns"] = min(outstanding, state["returned_quantity"])
        return states

    @classmethod
    def _current_sales_rows(cls, rows):
        active_rows = []
        for data in rows:
            sold = data.get("sold_quantity", 0) or 0
            returned = data.get("returned_quantity", 0) or 0
            net_quantity = data.get("net_quantity", max(0, sold - returned)) or 0
            if net_quantity > 0:
                active_rows.append(data)
        return active_rows

    @staticmethod
    def _compact_details_time(value):
        value = str(value or "").replace("T", " ")
        try:
            date_part, time_part = value.split(" ", 1)
            year, month, day = date_part.split("-")
            return f"{day}.{month} {time_part[:5]}"
        except ValueError:
            return value[:16]

    def _load_cashiers(self, initial=False):
        current_idx = self.cashier_combo.currentIndex()
        current_data = self.cashier_combo.currentData()
        self.cashier_combo.blockSignals(True)
        self.cashier_combo.clear()
        language = self.property("app_language") or "uz"

        # Always add "Barcha kassirlar" first
        self.cashier_combo.addItem(t("Barcha kassirlar", language), None)

        for user in db.get_staff_users():
            name = user.get("username") or user.get("email") or t("Noma'lum", language)
            uid = user.get("id")
            self.cashier_combo.addItem(name, uid)

        user_role = (self.user or {}).get("role", "")
        user_id = (self.user or {}).get("id")
        user_is_cashier = (user_role == "cashier")

        # Determine which index to select
        if initial and user_is_cashier and user_id:
            idx = self.cashier_combo.findData(user_id)
            if idx >= 0:
                self.cashier_combo.setCurrentIndex(idx)
        elif current_idx >= 0 and current_idx < self.cashier_combo.count() and self.cashier_combo.itemData(current_idx) == current_data:
            self.cashier_combo.setCurrentIndex(current_idx)
        else:
            idx = self.cashier_combo.findData(current_data) if current_data is not None else 0
            self.cashier_combo.setCurrentIndex(max(0, idx))
        self.cashier_combo.blockSignals(False)

    def _load_sections(self):
        current = self.section_combo.currentData()
        language = self.property("app_language") or "uz"
        self.section_combo.blockSignals(True)
        self.section_combo.clear()
        self.section_combo.addItem(t("Barcha bo'limlar", language), None)
        for section in db.get_product_sections():
            self.section_combo.addItem(section["name"], section["id"])
        index = self.section_combo.findData(current)
        if index >= 0:
            self.section_combo.setCurrentIndex(index)
        self.section_combo.blockSignals(False)

    def _load_currencies(self):
        current = self.currency_combo.currentData()
        current_code = current.get("code") if isinstance(current, dict) else None
        self.currency_combo.blockSignals(True)
        self.currency_combo.clear()
        currencies = [dict(currency) for currency in db.get_currencies()]
        currencies.sort(key=lambda currency: ({"UZS": 0, "USD": 1, "EUR": 2}.get(currency["code"], 10), currency["code"]))
        for currency in currencies:
            self.currency_combo.addItem(currency["code"], currency)
        selected_code = current_code or db.get_app_settings().get("currency", "UZS")
        index = self.currency_combo.findText(selected_code)
        if index >= 0:
            self.currency_combo.setCurrentIndex(index)
        self.currency_combo.blockSignals(False)

    def _date_range(self):
        selected = self.date_edit.date().toPyDate()
        period = self.period_combo.currentData()
        if period == "day":
            start = end = selected
        elif period == "week":
            start = selected - timedelta(days=selected.weekday())
            end = start + timedelta(days=6)
        elif period == "month":
            start = selected.replace(day=1)
            next_month = selected.replace(year=selected.year + 1, month=1, day=1) if selected.month == 12 else selected.replace(month=selected.month + 1, day=1)
            end = next_month - timedelta(days=1)
        else:
            start = selected.replace(month=1, day=1)
            end = selected.replace(month=12, day=31)
        return start.isoformat(), end.isoformat()

    def _shift_period(self, direction):
        current = self.date_edit.date()
        period = self.period_combo.currentData()
        if period == "day":
            self.date_edit.setDate(current.addDays(direction))
        elif period == "week":
            self.date_edit.setDate(current.addDays(direction * 7))
        elif period == "month":
            self.date_edit.setDate(current.addMonths(direction))
        else:
            self.date_edit.setDate(current.addYears(direction))

    def _format_money(self, value):
        currency = self.currency_combo.currentData() or {"code": "UZS", "rate_to_uzs": 1}
        converted = (value or 0) / (currency.get("rate_to_uzs") or 1)
        if currency.get("code") == "UZS":
            unit = t("so'm", self.property("app_language") or "uz")
            return f"{converted:,.0f} {unit}"
        return f"{converted:,.2f} {currency.get('code', 'UZS')}"

    def _payment_label(self, value):
        language = self.property("app_language") or "uz"
        return t({
            "naqd": "Naqd",
            "plastik karta": "Plastik karta",
            "qarz": "Qarz",
        }.get(value, value or "-"), language)

    @staticmethod
    def _status_icon(status_key):
        return {
            "Yakunlangan": "✅",
            "Hali yakunlanmagan": "⏳",
            "Qisman qaytarilgan": "↩️",
            "Qaytarilgan": "↩️",
        }.get(status_key, "")

    def _language_changed(self, language):
        self.cashier_lbl.setText(t("Kassir:", language))
        self.section_lbl.setText(t("Bo'lim:", language))
        self.date_lbl.setText(t("Sana:", language))
        period_labels = {"day": "Kunlik", "week": "Haftalik", "month": "Oylik", "year": "Yillik"}
        for index in range(self.period_combo.count()):
            self.period_combo.setItemText(index, t(period_labels[self.period_combo.itemData(index)], language))
        self.today_btn.setText(t("Bugun", language))
        self._load_sections()
        self._fill_table(self._last_rows)

    def apply_theme(self, theme):
        self.setStyleSheet(f"background:{theme['content']};")
        field_style = f"""
            QComboBox, QDateEdit {{
                background:{theme['topbar']};color:{theme['title']};border:1px solid #cbd5e1;
                border-radius:6px;padding:0 10px;font-size:13px;
            }}
            QComboBox:focus, QDateEdit:focus {{ border-color:{theme['accent']}; }}
        """
        for field in (self.cashier_combo, self.section_combo, self.date_edit, self.period_combo, self.currency_combo):
            field.setStyleSheet(field_style)
        button_style = f"""
            QPushButton {{background:{theme['topbar']};color:{theme['title']};border:1px solid #cbd5e1;border-radius:6px;font-weight:bold;}}
            QPushButton:hover {{background:{theme['content']};border-color:{theme['accent']};}}
        """
        for button in (self.prev_period_btn, self.next_period_btn, self.today_btn):
            button.setStyleSheet(button_style)
        self.summary_title_lbl.setStyleSheet(f"font-size:16px;font-weight:bold;color:{theme['title']};background:transparent;")
        self.summary_stats_lbl.setStyleSheet(f"font-size:13px;font-weight:bold;color:{theme['muted']};background:transparent;")
        self._apply_summary_card_theme(theme)
        self.cashier_lbl.setStyleSheet(f"color:{theme['muted']};font-weight:bold;background:transparent;")
        self.section_lbl.setStyleSheet(f"color:{theme['muted']};font-weight:bold;background:transparent;")
        self.date_lbl.setStyleSheet(f"color:{theme['muted']};font-weight:bold;background:transparent;")
        self.table.setStyleSheet(self._table_style(theme))
        self.table.verticalHeader().setStyleSheet(self._vertical_header_style())

    def _apply_summary_card_theme(self, theme):
        """Repaint the metric cards, keeping their text backgrounds transparent."""
        for key, card in self.summary_card_frames.items():
            color = card.property("accent_color") or theme["accent"]
            card.setStyleSheet(f"""
                QFrame#{card.objectName()} {{
                    background:{theme['topbar']};
                    border-left:4px solid {color};
                    border-top:1px solid #e2e8f0;
                    border-right:1px solid #e2e8f0;
                    border-bottom:1px solid #e2e8f0;
                    border-radius:8px;
                }}
            """)
            title = card.findChild(QLabel, "summary_title")
            if title is not None:
                title.setStyleSheet(
                    f"color:{theme['muted']};font-size:11px;background:transparent;border:none;"
                )
            value = card.findChild(QLabel, "summary_value")
            if value is not None:
                value_color = value.property("accent_color") or theme["accent"]
                value.setStyleSheet(
                    f"color:{value_color};font-size:14px;font-weight:bold;"
                    "background:transparent;border:none;"
                )
            hint = card.findChild(QLabel, "summary_hint")
            if hint is not None:
                hint.setStyleSheet(
                    "color:#b91c1c;font-size:10px;font-weight:bold;background:transparent;border:none;"
                )

    @staticmethod
    def _vertical_header_style():
        return """
            QHeaderView::section {
                background:#f3f4f6;
                color:#374151;
                border:none;
                border-right:1px solid #d1d5db;
                border-bottom:1px solid #d1d5db;
                padding:6px;
                font-weight:600;
            }
        """

    def _table_style(self, theme=None):
        if theme:
            return f"""
                QTableWidget {{background:{theme['topbar']};color:{theme['title']};border:1px solid #dbe3ef;border-radius:8px;font-size:13px;gridline-color:#dbe3ef;}}
                QTableWidget::item {{padding:8px 10px;}}
                QTableWidget::item:selected {{background:{theme['accent']};color:{theme['nav_active']};}}
                QHeaderView::section {{background:{theme['sidebar_alt']};color:{theme['nav_text']};border:none;border-right:1px solid {theme['border']};padding:10px;font-size:13px;font-weight:bold;}}
            """
        return """
            QTableWidget{background:white;color:#1e293b;border:1px solid #dbe3ef;border-radius:8px;font-size:13px;gridline-color:#dbe3ef;}
            QTableWidget::item{padding:8px 10px;}
            QTableWidget::item:selected{background:#2563eb;color:white;}
            QHeaderView::section{background:#1e3a5f;color:white;border:none;border-right:1px solid #315579;padding:10px;font-size:13px;font-weight:bold;}
        """
