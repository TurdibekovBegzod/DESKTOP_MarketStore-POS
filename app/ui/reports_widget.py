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
    def __init__(self):
        super().__init__()
        self.detail_mode = "overall"
        self.detail_metric = "revenue"
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
        self.report_currency_combo = QComboBox()
        self.report_currency_combo.setFixedHeight(34)
        self.report_currency_combo.setMinimumWidth(92)
        self._load_report_currency_combo()
        self.report_currency_combo.currentIndexChanged.connect(self.load_data)

        self.summary_layout = QGridLayout()
        self.summary_layout.setSpacing(12)
        self.summary_cards = {}
        for index, (key, title, color) in enumerate([
            ("revenue", "Daromad", "#059669"),
            ("profit", "Foyda", "#8b5cf6"),
            ("count", "Sotuvlar soni", "#3b82f6"),
            ("products", "Mahsulotlar soni", "#0ea5e9"),
            ("net_profit", "Sof foyda", "#f59e0b"),
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
            title_lbl.setStyleSheet("color:#64748b;font-size:11px;")
            value_lbl = QLabel("0")
            value_lbl.setObjectName("summary_value")
            value_lbl.setProperty("accent_color", color)
            value_lbl.setStyleSheet(f"color:{color};font-size:14px;font-weight:bold;")
            value_lbl.setWordWrap(False)
            value_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            card_layout.addWidget(title_lbl)
            card_layout.addWidget(value_lbl)
            card_layout.addStretch()
            self.summary_cards[key] = value_lbl
            self.summary_layout.addWidget(card, 0, index)
        layout.addLayout(self.summary_layout)

        detail_header = QHBoxLayout()
        report_title = QLabel("Hisobot turi:")
        report_title.setStyleSheet("font-size:14px;font-weight:bold;color:#1e293b;")
        detail_header.addWidget(report_title)
        self.overall_btn = self._mode_button("Umumiy", "overall")
        self.cashier_btn = self._mode_button("Kassirlar hisoboti", "cashier")
        detail_header.addWidget(self.overall_btn)
        detail_header.addWidget(self.cashier_btn)
        detail_header.addStretch()
        layout.addLayout(detail_header)

        detail_row = QHBoxLayout()
        detail_row.setSpacing(12)

        left_panel = QFrame()
        left_panel.setFrameShape(QFrame.Shape.NoFrame)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        metric_label = QLabel("Grafik:")
        metric_label.setStyleSheet("color:#64748b;font-size:12px;font-weight:bold;")
        left_layout.addWidget(metric_label)
        for key, label in [
            ("all", "Hammasi"),
            ("revenue", "Daromad"),
            ("profit", "Foyda"),
            ("count", "Cheklar"),
            ("products", "Mahsulotlar"),
            ("net_profit", "Sof foyda"),
        ]:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setFixedHeight(32)
            btn.setMinimumWidth(150)
            btn.setStyleSheet(self._metric_toggle_style(key))
            btn.clicked.connect(lambda checked, metric=key: self._set_detail_metric(metric))
            setattr(self, f"metric_{key}_btn", btn)
            left_layout.addWidget(btn)
        left_layout.addSpacing(2)

        self.entity_panel = QFrame()
        self.entity_panel.setFrameShape(QFrame.Shape.NoFrame)
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
        self.entity_table.setMaximumHeight(220)
        self.entity_table.setStyleSheet(self._entity_table_style())
        self.entity_table.itemSelectionChanged.connect(self._on_entity_selected)
        entity_layout.addWidget(self.entity_table)
        left_layout.addWidget(self.entity_panel, 1)
        left_layout.addStretch()
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
        period_controls.addWidget(self.section_lbl)
        period_controls.addWidget(self.section_combo)
        period_controls.addStretch()
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
            else:
                label.setStyleSheet(f"color:{theme['title']};background:transparent;border:none;")

        button_style = self._toggle_style(theme)
        for button in [
            self.overall_btn,
            self.cashier_btn,
            self.prev_period_btn,
            self.next_period_btn,
            self.today_btn,
        ]:
            button.setStyleSheet(button_style)
        for key in ["all", "revenue", "profit", "count", "products", "net_profit"]:
            getattr(self, f"metric_{key}_btn").setStyleSheet(self._metric_toggle_style(key))

        self.entity_table.setStyleSheet(self._entity_table_style(theme))

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
        if period == "day":
            rows = db.get_overall_day_hourly_series(start_date, section_id)
            expense_rows = [] if section_id else db.get_expense_hourly_report(start_date)
        else:
            rows = db.get_overall_period_series(start_date, end_date, section_id)
            expense_rows = [] if section_id else db.get_expense_report(start_date, end_date)
        return {
            "start_date": start_date,
            "end_date": end_date,
            "section_id": section_id,
            "rows": rows,
            "expense_rows": expense_rows,
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
        )
        currency = self._selected_report_currency()

        totals = {
            "revenue": sum(row["revenue"] for row in filled),
            "profit": sum(row["profit"] for row in filled),
            "net_profit": sum(row["net_profit"] for row in filled),
            "count": sum(row["sales_count"] for row in filled),
            "products": sum(row["product_count"] for row in filled),
        }
        self.summary_cards["revenue"].setText(self._format_money(totals["revenue"], currency))
        self.summary_cards["profit"].setText(self._format_money(totals["profit"], currency))
        self.summary_cards["count"].setText(f"{totals['count']:,.0f}")
        self.summary_cards["products"].setText(f"{totals['products']:,.0f}")
        self.summary_cards["net_profit"].setText(self._format_money(totals["net_profit"], currency))

        self.overall_rows = filled
        self._refresh_report_panel(start_date, end_date, filled)
        set_language(self, self.property("app_language") or "uz")

    def _refresh_report_panel(self, start_date, end_date, overall_rows):
        if self.detail_mode == "overall":
            self.selected_entity_id = None
            self.entity_panel.hide()
            titles = {
                "all": "Barcha ko'rsatkichlar",
                "revenue": "Umumiy daromad",
                "profit": "Umumiy foyda",
                "count": "Umumiy cheklar",
                "products": "Umumiy sotilgan mahsulotlar",
                "net_profit": "Foyda - harajatlar",
            }
            self.detail_chart.title = titles[self.detail_metric]
            self._set_chart_data(self.detail_chart, overall_rows, self.detail_metric)
            return

        self.entity_panel.show()
        self._load_entities(start_date, end_date)

    def _load_entities(self, start_date, end_date):
        current = self.selected_entity_id
        rows = db.get_cashier_period_summary(start_date, end_date, self._selected_section_id())
        title = "Kassirlar hisoboti"
        self.detail_chart.title = title
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
        self.entity_table.blockSignals(False)
        if selected_row >= 0:
            self.entity_table.selectRow(selected_row)
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
        rows = self._entity_series(self.detail_mode, self.selected_entity_id, start_date, end_date)
        filled_rows = self._filled_series(rows, start_date, end_date)
        filled = filled_rows if self._selected_section_id() else self._with_entity_net_profit(filled_rows, start_date, end_date)
        label = self.entity_table.item(self.entity_table.currentRow(), 0).text()
        titles = {
            "all": "barcha ko'rsatkichlar",
            "revenue": "daromad",
            "profit": "foyda",
            "count": "cheklar",
            "products": "mahsulotlar",
            "net_profit": "foyda - harajatlar",
        }
        self.detail_chart.title = f"{label}: {titles[self.detail_metric]}"
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
        if current:
            index = self.report_currency_combo.findText(current["code"], Qt.MatchFlag.MatchStartsWith)
            if index >= 0:
                self.report_currency_combo.setCurrentIndex(index)
        self.report_currency_combo.blockSignals(False)

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
        return [
            ("revenue", "Daromad", self._metric_color("revenue")),
            ("profit", "Foyda", self._metric_color("profit")),
            ("count", "Cheklar", self._metric_color("count")),
            ("products", "Mahsulotlar", self._metric_color("products")),
            ("net_profit", "Sof foyda", self._metric_color("net_profit")),
        ]

    def _metric_color(self, metric):
        return {
            "all": "#334155",
            "revenue": "#2563eb",
            "profit": "#16a34a",
            "count": "#f97316",
            "products": "#8b5cf6",
            "net_profit": "#ef4444",
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
        if self.period_combo.currentData() == "day":
            return db.get_expense_hourly_report(start_date, user_id=user_id, include_unassigned=include_unassigned)
        return db.get_expense_report(start_date, end_date, user_id=user_id, include_unassigned=include_unassigned)

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
            row["net_profit"] = (row["profit"] or 0) - row["expense"]
        return rows

    def _with_net_profit_from_expenses(self, rows, expense_rows, currencies):
        rates = {currency["code"]: currency["rate_to_uzs"] or 1 for currency in currencies}
        totals = {}
        for expense in expense_rows:
            try:
                label = self._expense_period_key(expense["label"], self.period_combo.currentData())
            except (TypeError, ValueError):
                continue
            currency = expense["currency_code"] or "UZS"
            totals[label] = totals.get(label, 0) + (expense["amount"] or 0) * (rates.get(currency, 1) or 1)
        for row in rows:
            row["expense"] = totals.get(row["label"], 0)
            row["net_profit"] = (row["profit"] or 0) - row["expense"]
        return rows

    def _with_entity_net_profit(self, rows, start_date, end_date):
        entity = self._selected_entity()
        if entity and entity.get("role") == "admin":
            expenses = self._expense_totals_by_period(start_date, end_date, user_id=entity["id"], include_unassigned=True)
            for row in rows:
                row["expense"] = expenses.get(row["label"], 0)
                row["net_profit"] = (row["profit"] or 0) - row["expense"]
            return rows
        for row in rows:
            row["expense"] = 0
            row["net_profit"] = row["profit"] or 0
        return rows

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
        self.detail_mode = mode
        self.selected_entity_id = None
        self._sync_buttons()
        start_date, end_date = self._date_range()
        self._refresh_report_panel(start_date, end_date, self.overall_rows)

    def _set_detail_metric(self, metric):
        self.detail_metric = metric
        self._sync_buttons()
        if self.detail_mode == "overall":
            start_date, end_date = self._date_range()
            self._refresh_report_panel(start_date, end_date, self.overall_rows)
        else:
            self._refresh_detail_chart()

    def _sync_buttons(self):
        self.overall_btn.setChecked(self.detail_mode == "overall")
        self.cashier_btn.setChecked(self.detail_mode == "cashier")
        for key in ["all", "revenue", "profit", "count", "products", "net_profit"]:
            getattr(self, f"metric_{key}_btn").setChecked(self.detail_metric == key)

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
