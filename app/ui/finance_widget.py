from datetime import date, timedelta

from PyQt6.QtCore import Qt, QDate, QPointF, QTimer, QObject, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QDoubleValidator
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QDateEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QDialog, QFormLayout,
    QComboBox, QLineEdit, QMessageBox, QFileDialog
)

import database as db
from ui.async_loader import make_progress_bar, set_progress_bar_loading
from ui.finance_excel import export_finance_xlsx
from ui.i18n import set_language, t


MANUAL_FINANCE_PATH = "finance_manual.json"
MANUAL_ROLE = Qt.ItemDataRole.UserRole + 10


class FinanceLoadWorker(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, start, end):
        super().__init__()
        self.start = start
        self.end = end

    def run(self):
        try:
            self.finished.emit(db.get_finance_rows(self.start, self.end))
        except Exception as exc:
            self.failed.emit(str(exc))


class FinanceChart(QWidget):
    def __init__(self):
        super().__init__()
        self.points = []
        self.view_start = 0
        self.visible_count = 14
        self.drag_start_x = None
        self.drag_start_view = 0
        self.title = "Summa grafigi"
        self.setMinimumHeight(260)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setStyleSheet("background:white;border:1px solid #e2e8f0;border-radius:8px;")

    def set_points(self, points, title):
        self.points = points
        self.title = title
        self.view_start = min(self.view_start, self._max_view_start())
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(18, 14, -18, -14)
        painter.setPen(QColor("#1f2937"))
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        painter.drawText(rect.left(), rect.top(), rect.width(), 22, Qt.AlignmentFlag.AlignCenter, self.title)

        visible_points = self._visible_points()
        values = [value or 0 for _, value in visible_points]
        max_value = max(values) if values else 0
        min_value = min(values) if values else 0
        if min_value > 0:
            min_value = 0
        if max_value < 0:
            max_value = 0
        if max_value == min_value:
            max_value = min_value + 1
        value_span = max_value - min_value
        y_labels = [f"{max_value - (step / 5) * value_span:,.0f}" for step in range(6)]
        painter.setFont(QFont("Segoe UI", 8))
        label_width = max(painter.fontMetrics().horizontalAdvance(label) for label in y_labels) + 12
        left_margin = max(58, min(label_width + 12, max(90, rect.width() // 3)))
        chart = rect.adjusted(left_margin, 38, -14, -58)
        painter.setPen(QPen(QColor("#e2e8f0"), 1))
        for step in range(6):
            y = chart.top() + (step / 5) * chart.height()
            painter.drawLine(chart.left(), int(y), chart.right(), int(y))

        if not visible_points:
            painter.setPen(QColor("#94a3b8"))
            painter.drawText(chart, Qt.AlignmentFlag.AlignCenter, t("Ma'lumot yo'q", self.property("app_language") or "uz"))
            return

        painter.setPen(QColor("#64748b"))
        for step, label in enumerate(y_labels):
            y = chart.top() + (step / 5) * chart.height()
            painter.drawText(chart.left() - label_width - 6, int(y) - 8, label_width, 16, Qt.AlignmentFlag.AlignRight, label)

        count = max(len(visible_points) - 1, 1)
        plotted = []
        for index, (_, value) in enumerate(visible_points):
            x = chart.left() + (index / count) * chart.width()
            safe_value = value or 0
            y = chart.bottom() - ((safe_value - min_value) / value_span) * chart.height()
            y = max(chart.top(), min(chart.bottom(), y))
            plotted.append(QPointF(x, y))

        color = QColor("#2563eb")
        painter.save()
        painter.setClipRect(chart.adjusted(-6, -6, 6, 6))
        painter.setPen(QPen(color, 3))
        for index in range(1, len(plotted)):
            painter.drawLine(plotted[index - 1], plotted[index])
        painter.setBrush(color)
        painter.setPen(QPen(QColor("white"), 2))
        for point in plotted:
            painter.drawEllipse(point, 4.5, 4.5)
        painter.restore()

        painter.setPen(QColor("#475569"))
        painter.setFont(QFont("Segoe UI", 7))
        for index, (label, _) in enumerate(visible_points):
            if len(visible_points) > 16 and index % 2:
                continue
            x = plotted[index].x()
            painter.save()
            painter.translate(x - 5, chart.bottom() + 30)
            painter.rotate(-45)
            painter.drawText(0, 0, label)
            painter.restore()

        if len(self.points) > self.visible_count:
            painter.setPen(QColor("#94a3b8"))
            painter.setFont(QFont("Segoe UI", 8))
            text = f"{self.view_start + 1}-{self.view_start + len(visible_points)} / {len(self.points)}"
            painter.drawText(rect.right() - 90, rect.top() + 2, 86, 18, Qt.AlignmentFlag.AlignRight, text)

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

    def _visible_points(self):
        if len(self.points) <= self.visible_count:
            return self.points
        end = self.view_start + self.visible_count
        return self.points[self.view_start:end]

    def _max_view_start(self):
        return max(0, len(self.points) - self.visible_count)


class FinanceMoneyDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.language = (parent.property("app_language") if parent else None) or "uz"
        self.setWindowTitle(t("Mablag' o'zgartirish", self.language))
        self.setFixedWidth(380)
        self._build_ui()
        set_language(self, self.language)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        form = QFormLayout()
        self.current_date = date.today()
        self.date_value_lbl = QLabel(self.current_date.strftime("%d.%m.%Y"))
        self.kind_combo = QComboBox()
        self.kind_combo.addItem(t("Naqd", self.language), "cash")
        self.kind_combo.addItem(t("Karta", self.language), "card")
        self.kind_combo.addItem(t("Boshqa", self.language), "other")
        self.operation_combo = QComboBox()
        self.operation_combo.addItem(t("Qo'shish", self.language), "+")
        self.operation_combo.addItem(t("Kamaytirish", self.language), "-")
        self.currency_combo = QComboBox()
        self._load_currencies()
        self.amount_edit = QLineEdit()
        self.amount_edit.setPlaceholderText("0.00")
        validator = QDoubleValidator(0, 999999999999, 2, self)
        validator.setNotation(QDoubleValidator.Notation.StandardNotation)
        self.amount_edit.setValidator(validator)

        self.date_value_lbl.setFixedHeight(34)
        self.date_value_lbl.setStyleSheet("border:1px solid #cbd5e1;border-radius:6px;padding:6px 10px;background:white;")
        for widget in (self.kind_combo, self.operation_combo, self.currency_combo, self.amount_edit):
            widget.setFixedHeight(34)
            widget.setStyleSheet("border:1px solid #cbd5e1;border-radius:6px;padding:6px 10px;background:white;")
        form.addRow(t("Sana:", self.language), self.date_value_lbl)
        form.addRow(t("Ustun", self.language), self.kind_combo)
        form.addRow(t("Amal", self.language), self.operation_combo)
        form.addRow(t("Valyuta", self.language), self.currency_combo)
        form.addRow(t("Summa", self.language), self.amount_edit)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        cancel_btn = QPushButton(t("Bekor", self.language))
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton(t("Saqlash", self.language))
        save_btn.setStyleSheet("background:#2563eb;color:white;border:none;border-radius:6px;padding:8px 16px;font-weight:bold;")
        save_btn.clicked.connect(self._accept)
        buttons.addStretch()
        buttons.addWidget(cancel_btn)
        buttons.addWidget(save_btn)
        layout.addLayout(buttons)

    def _load_currencies(self):
        selected_currency = db.get_app_settings().get("currency", "UZS")
        currencies = [dict(currency) for currency in db.get_currencies()]
        priority = {"UZS": 0, "USD": 1, "EUR": 2}
        currencies.sort(key=lambda item: (priority.get(item["code"], 10), item["code"]))
        for currency in currencies:
            self.currency_combo.addItem(currency["code"], currency)
        if self.currency_combo.count() == 0:
            self.currency_combo.addItem("UZS", {"code": "UZS", "rate_to_uzs": 1})
        index = self.currency_combo.findText(selected_currency, Qt.MatchFlag.MatchStartsWith)
        if index >= 0:
            self.currency_combo.setCurrentIndex(index)

    def _accept(self):
        if self.amount() <= 0:
            QMessageBox.warning(self, t("Xatolik", self.language), t("Summani kiriting!", self.language))
            return
        self.accept()

    def amount(self):
        text = self.amount_edit.text().strip().replace(" ", "").replace(",", ".")
        try:
            return float(text) if text else 0
        except ValueError:
            return 0

    def data(self):
        currency = self.currency_combo.currentData() or {"code": "UZS", "rate_to_uzs": 1}
        return {
            "date": self.current_date.isoformat(),
            "kind": self.kind_combo.currentData() or "cash",
            "operation": self.operation_combo.currentData() or "+",
            "amount": self.amount(),
            "currency": currency.get("code", "UZS"),
            "rate_to_uzs": currency.get("rate_to_uzs") or 1,
        }


class FinanceWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.language = "uz"
        self.theme = None
        self._loading = False
        self._load_thread = None
        self._load_worker = None
        self._pending_load = False
        self.current_day = date.today()
        db.migrate_finance_manual_json(MANUAL_FINANCE_PATH)
        self.manual_values = db.get_finance_manual_values()
        self._build_ui()
        self.midnight_timer = QTimer(self)
        self.midnight_timer.timeout.connect(self._refresh_if_day_changed)
        self.midnight_timer.start(60000)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self.progress_bar = make_progress_bar()
        layout.addWidget(self.progress_bar)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.date_lbl = QLabel("Sana:")
        self.month_edit = QDateEdit()
        self.month_edit.setCalendarPopup(True)
        self.month_edit.setDisplayFormat("MM.yyyy")
        self.month_edit.setDate(QDate.currentDate())
        self.month_edit.setFixedHeight(36)
        self.month_edit.setFixedWidth(140)
        self.month_edit.dateChanged.connect(self.load_data)
        self.period_combo = QComboBox()
        self.period_combo.addItem("Kunlik", "day")
        self.period_combo.addItem("Haftalik", "week")
        self.period_combo.addItem("Oylik", "month")
        self.period_combo.addItem("Yillik", "year")
        self.period_combo.setCurrentIndex(0)
        self.period_combo.setFixedHeight(36)
        self.period_combo.setMinimumWidth(110)
        self.period_combo.currentIndexChanged.connect(self._period_changed)
        self.prev_btn = QPushButton("<")
        self.prev_btn.setFixedSize(38, 36)
        self.prev_btn.clicked.connect(lambda: self._shift_period(-1))
        self.next_btn = QPushButton(">")
        self.next_btn.setFixedSize(38, 36)
        self.next_btn.clicked.connect(lambda: self._shift_period(1))
        self.currency_combo = QComboBox()
        self.currency_combo.setFixedHeight(36)
        self.currency_combo.setMinimumWidth(92)
        self._load_display_currencies()
        self.currency_combo.currentIndexChanged.connect(self.load_data)
        self.money_btn = QPushButton("Mablag' o'zgartirish")
        self.money_btn.setFixedHeight(36)
        self.money_btn.clicked.connect(self._open_money_dialog)
        self.export_btn = QPushButton("Export")
        self.export_btn.setFixedHeight(36)
        self.export_btn.clicked.connect(self._export_excel)
        self.refresh_btn = QPushButton("Yangilash")
        self.refresh_btn.setFixedHeight(36)
        self.refresh_btn.clicked.connect(self.load_data)
        toolbar.addWidget(self.prev_btn)
        toolbar.addWidget(self.date_lbl)
        toolbar.addWidget(self.month_edit)
        toolbar.addWidget(self.period_combo)
        toolbar.addWidget(self.next_btn)
        toolbar.addWidget(self.currency_combo)
        toolbar.addWidget(self.money_btn)
        toolbar.addWidget(self.export_btn)
        toolbar.addWidget(self.refresh_btn)
        toolbar.addStretch()

        content_row = QHBoxLayout()
        content_row.setSpacing(12)

        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        content_row.addWidget(self.table, 3)

        self.chart = FinanceChart()
        self.chart.setMinimumWidth(360)
        content_row.addWidget(self.chart, 2)
        layout.addLayout(content_row, 1)

        bottom_controls = QHBoxLayout()
        bottom_controls.setSpacing(8)
        bottom_controls.addStretch()
        while toolbar.count():
            item = toolbar.takeAt(0)
            if item.widget():
                bottom_controls.addWidget(item.widget())
        bottom_controls.addStretch()
        layout.addLayout(bottom_controls)
        self.load_data()

    def load_data(self):
        start, end = self._date_range()
        self.manual_values = db.get_finance_manual_values()
        if not self.isVisible():
            self._render_finance_data(start, end, db.get_finance_rows(start.isoformat(), end.isoformat()))
            return
        if self._load_thread and self._load_thread.isRunning():
            self._pending_load = True
            return

        self._loading = True
        self._set_loading_visible(True)
        self._load_thread = QThread(self)
        self._load_worker = FinanceLoadWorker(start.isoformat(), end.isoformat())
        self._load_worker.moveToThread(self._load_thread)
        self._load_thread.started.connect(self._load_worker.run)
        self._load_worker.finished.connect(lambda finance, s=start, e=end: self._on_finance_loaded(s, e, finance))
        self._load_worker.failed.connect(self._on_finance_failed)
        self._load_worker.finished.connect(self._load_thread.quit)
        self._load_worker.failed.connect(self._load_thread.quit)
        self._load_thread.finished.connect(self._load_worker.deleteLater)
        self._load_thread.finished.connect(self._load_thread.deleteLater)
        self._load_thread.finished.connect(self._clear_load_thread)
        self._load_thread.start()

    def _on_finance_loaded(self, start, end, finance):
        self._render_finance_data(start, end, finance)

    def _on_finance_failed(self, message):
        self._loading = False
        self._set_loading_visible(False)
        QMessageBox.warning(self, t("Xatolik", self.language), message)

    def _clear_load_thread(self):
        self._load_thread = None
        self._load_worker = None
        if self._pending_load:
            self._pending_load = False
            QTimer.singleShot(0, self.load_data)
        else:
            self._set_loading_visible(False)

    def _render_finance_data(self, start, end, finance):
        self._loading = True
        template_columns = finance["templates"]
        headers = (
            ["Pullar", "Naqd", "Karta"]
            + [template["name"] for template in template_columns]
            + ["Boshqa", "Qarzim", "Summa"]
        )
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels([t(header, self.language) for header in headers])

        chart_rows = self._display_rows(finance["rows"], template_columns, include_empty=True)
        rows = [row for row in chart_rows if self._show_table_row(row)]

        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            cash = self._manual_period_total(row["label"], "cash")
            card = self._manual_period_total(row["label"], "card")
            other = self._manual_period_total(row["label"], "other")
            debt = row.get("debt", 0) or 0
            template_total = sum(row["templates"].get(template["id"], 0) for template in template_columns)
            total = self._row_total(row, template_columns)
            values = [
                self._display_date(row["label"]),
                self._money(cash),
                self._money(card),
            ]
            for template in template_columns:
                values.append(self._money(row["templates"].get(template["id"], 0)))
            values.extend([self._money(other), self._money(debt), self._money(total)])
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(MANUAL_ROLE, row["label"])
                if column > 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row_index, column, item)

        if self.table.columnCount():
            self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(0, 104)
            self.table.horizontalHeader().setSectionResizeMode(self.table.columnCount() - 1, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(self.table.columnCount() - 1, 130)

        self._loading = False
        self.chart.setProperty("app_language", self.language)
        self.chart.set_points(self._finance_chart_points(chart_rows, template_columns), t("Summa grafigi", self.language))
        set_language(self, self.language)
        if self.theme:
            self.apply_theme(self.theme)

    def apply_theme(self, theme):
        self.theme = theme
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
            QDateEdit::drop-down, QComboBox::drop-down {{ border:none;width:24px; }}
        """
        button_style = f"""
            QPushButton {{
                background:{theme['topbar']};
                color:{theme['title']};
                border:1px solid #cbd5e1;
                border-radius:6px;
                padding:6px 12px;
                font-size:12px;
                font-weight:600;
            }}
            QPushButton:hover {{ background:{theme['accent']};color:{theme['nav_active']};border-color:{theme['accent']}; }}
            QPushButton:pressed {{ background:{theme['sidebar_alt']};color:{theme['nav_text']};padding-top:7px; }}
        """
        table_style = f"""
            QTableWidget {{
                background:{theme['topbar']};
                color:{theme['title']};
                border:1px solid #e2e8f0;
                border-radius:8px;
                gridline-color:#e2e8f0;
                alternate-background-color:{theme['content']};
                font-size:13px;
            }}
            QTableWidget::item {{ padding:7px 10px; }}
            QTableWidget::item:selected {{ background:{theme['accent']};color:{theme['nav_active']}; }}
            QHeaderView::section {{
                background:{theme['content']};
                color:{theme['muted']};
                border:none;
                border-bottom:1px solid #e2e8f0;
                padding:8px;
                font-weight:bold;
            }}
        """
        self.date_lbl.setStyleSheet(f"color:{theme['title']};background:transparent;border:none;")
        self.progress_bar.setStyleSheet(f"""
            QProgressBar[loading="true"] {{
                background:{theme['content']};
                border:none;
                border-radius:2px;
            }}
            QProgressBar[loading="true"]::chunk {{
                background:{theme['accent']};
                border-radius:2px;
            }}
            QProgressBar[loading="false"] {{
                background:transparent;
                border:none;
            }}
            QProgressBar[loading="false"]::chunk {{ background:transparent; }}
        """)
        self.month_edit.setStyleSheet(field_style)
        self.period_combo.setStyleSheet(field_style)
        self.currency_combo.setStyleSheet(field_style)
        for button in (self.prev_btn, self.next_btn, self.refresh_btn, self.money_btn, self.export_btn):
            button.setStyleSheet(button_style)
        self.table.setStyleSheet(table_style)
        self.chart.setStyleSheet(f"background:{theme['topbar']};border:1px solid #e2e8f0;border-radius:8px;")

    def _date_range(self):
        selected = self.month_edit.date().toPyDate()
        period = self.period_combo.currentData() or "day"
        if period == "day":
            start = selected.replace(day=1)
            if selected.month == 12:
                end = selected.replace(year=selected.year + 1, month=1, day=1) - timedelta(days=1)
            else:
                end = selected.replace(month=selected.month + 1, day=1) - timedelta(days=1)
        elif period in ("week", "month"):
            start = selected.replace(month=1, day=1)
            end = selected.replace(month=12, day=31)
        elif period == "year":
            start_year = (selected.year // 10) * 10
            start = date(start_year, 1, 1)
            end = date(start_year + 9, 12, 31)
        else:
            start = selected.replace(day=1)
            end = start
        return start, end

    def _shift_period(self, direction):
        period = self.period_combo.currentData() or "day"
        if period == "day":
            self.month_edit.setDate(self.month_edit.date().addMonths(direction))
        elif period in ("week", "month"):
            self.month_edit.setDate(self.month_edit.date().addYears(direction))
        elif period == "year":
            self.month_edit.setDate(self.month_edit.date().addYears(direction * 10))
        else:
            self.month_edit.setDate(self.month_edit.date().addMonths(direction))

    def _period_changed(self):
        period = self.period_combo.currentData() or "day"
        if period == "day":
            self.month_edit.setDisplayFormat("MM.yyyy")
            self.month_edit.setFixedWidth(140)
        elif period in ("week", "month", "year"):
            self.month_edit.setDisplayFormat("yyyy")
            self.month_edit.setFixedWidth(110)
        else:
            self.month_edit.setDisplayFormat("MM.yyyy")
            self.month_edit.setFixedWidth(140)
        self.load_data()

    def _display_date(self, value, short=False):
        value = str(value)
        if value.startswith("week:"):
            _, start_text, end_text = value.split(":")
            start = date.fromisoformat(start_text)
            end = date.fromisoformat(end_text)
            return f"{start.strftime('%d.%m')}-{end.strftime('%d.%m')}"
        if value.startswith("year:"):
            return value.split(":", 1)[1]
        if len(value) == 7:
            item = date.fromisoformat(f"{value}-01")
            return item.strftime("%m.%Y" if not short else "%m.%y")
        item = date.fromisoformat(value)
        return item.strftime("%d.%m" if short else "%d.%m.%Y")

    def _money(self, value):
        currency = self._selected_currency()
        code = currency.get("code", "UZS")
        converted = self._display_value(value)
        if code == "UZS":
            unit = t("so'm", self.language)
            return f"{converted:,.0f} {unit}"
        return f"{converted:,.2f} {code}"

    def _display_value(self, value):
        currency = self._selected_currency()
        return (value or 0) / (currency.get("rate_to_uzs") or 1)

    def _number(self, value):
        return f"{value or 0:,.0f}"

    def _load_display_currencies(self):
        current = self.currency_combo.currentData() if self.currency_combo.count() else None
        selected_currency = current.get("code") if current else db.get_app_settings().get("currency", "UZS")
        self.currency_combo.clear()
        currencies = [dict(currency) for currency in db.get_currencies()]
        priority = {"UZS": 0, "USD": 1, "EUR": 2}
        currencies.sort(key=lambda item: (priority.get(item["code"], 10), item["code"]))
        for currency in currencies:
            self.currency_combo.addItem(currency["code"], currency)
        if self.currency_combo.count() == 0:
            self.currency_combo.addItem("UZS", {"code": "UZS", "rate_to_uzs": 1})
        index = self.currency_combo.findText(selected_currency, Qt.MatchFlag.MatchStartsWith)
        if index >= 0:
            self.currency_combo.setCurrentIndex(index)

    def _selected_currency(self):
        return self.currency_combo.currentData() or {"code": "UZS", "rate_to_uzs": 1}

    def _manual_total(self, label, kind):
        value = self.manual_values.get(label, {}).get(kind, 0)
        if isinstance(value, list):
            total = 0
            for movement in value:
                sign = -1 if movement.get("operation") == "-" else 1
                total += sign * (movement.get("amount", 0) or 0) * (movement.get("rate_to_uzs", 1) or 1)
            return total
        if isinstance(value, dict):
            sign = -1 if value.get("operation") == "-" else 1
            return sign * (value.get("amount", 0) or 0) * (value.get("rate_to_uzs", 1) or 1)
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0

    def _manual_period_total(self, label, kind):
        label = str(label)
        if label.startswith("week:"):
            _, start_text, end_text = label.split(":")
            start = date.fromisoformat(start_text)
            end = date.fromisoformat(end_text)
            return sum(
                self._manual_total(day, kind)
                for day in self.manual_values
                if start <= date.fromisoformat(str(day)) <= end
            )
        if label.startswith("year:"):
            year = label.split(":", 1)[1]
            return sum(self._manual_total(day, kind) for day in self.manual_values if str(day).startswith(year))
        if len(label) == 7:
            return sum(self._manual_total(day, kind) for day in self.manual_values if str(day).startswith(label))
        return self._manual_total(label, kind)

    def _display_rows(self, rows, template_columns, include_empty=False):
        period = self.period_combo.currentData() or "day"
        if period == "day":
            return rows
        grouped = {}
        for row in rows:
            row_date = date.fromisoformat(row["label"])
            if period == "week":
                week_start = row_date - timedelta(days=row_date.weekday())
                week_end = week_start + timedelta(days=6)
                key = f"week:{week_start.isoformat()}:{week_end.isoformat()}"
            elif period == "month":
                key = row["label"][:7]
            else:
                key = f"year:{row_date.year}"
            item = grouped.setdefault(key, {
                "label": key,
                "cash": 0,
                "card": 0,
                "other": 0,
                "inventory_other_total": 0,
                "manual_cash": 0,
                "manual_card": 0,
                "manual_other": 0,
                "debt": 0,
                "total_avg": 0,
                "total_sum": 0,
                "total": 0,
                "active": 0,
                "value_count": 0,
                "templates": {template["id"]: 0 for template in template_columns},
                "template_totals": {template["id"]: 0 for template in template_columns},
            })
            has_value = self._row_has_activity(row)
            item["active"] = 1 if item["active"] or has_value else 0
            item["manual_cash"] += self._manual_period_total(row["label"], "cash")
            item["manual_card"] += self._manual_period_total(row["label"], "card")
            item["manual_other"] += self._manual_period_total(row["label"], "other")
            if has_value:
                item["value_count"] += 1
                item["inventory_other_total"] += row["other"] or 0
                item["total_sum"] += self._inventory_total(row, template_columns)
            item["debt"] = row.get("debt", 0) or 0
            for template in template_columns:
                if has_value:
                    item["template_totals"][template["id"]] += row["templates"].get(template["id"], 0)
        result = [grouped[key] for key in sorted(grouped)]
        for item in result:
            count = item.pop("value_count", 0) or 1
            item["inventory_other_avg"] = item.pop("inventory_other_total", 0) / count
            item["other"] = item.pop("manual_other", 0)
            item["cash"] = item.pop("manual_cash", 0)
            item["card"] = item.pop("manual_card", 0)
            item["total_avg"] = (
                item.pop("total_sum", 0) / count
                + item["cash"]
                + item["card"]
                + item["other"]
                - (item.get("debt", 0) or 0)
            )
            totals = item.pop("template_totals", {})
            for template in template_columns:
                item["templates"][template["id"]] = (totals.get(template["id"], 0) or 0) / count
        if include_empty:
            return result
        return [row for row in result if self._row_has_activity(row)]

    def _row_has_activity(self, row):
        label = str(row["label"])
        if row.get("active"):
            return True
        return any(self._manual_period_total(label, kind) for kind in ("cash", "card", "other"))

    def _show_table_row(self, row):
        if self._row_has_activity(row):
            return True
        if (self.period_combo.currentData() or "day") != "day":
            return False
        try:
            return date.fromisoformat(str(row["label"])) <= date.today()
        except ValueError:
            return False

    def _row_total(self, row, template_columns):
        if "total_avg" in row:
            return row.get("total_avg", 0) or 0
        return self._daily_row_total(row, template_columns)

    def _finance_chart_value(self, row, template_columns):
        label = str(row["label"])
        today = date.today()
        try:
            if label.startswith("week:"):
                _, start_text, end_text = label.split(":")
                row_start = date.fromisoformat(start_text)
            elif label.startswith("year:"):
                row_start = date(int(label.split(":", 1)[1]), 1, 1)
            elif len(label) == 7:
                row_start = date.fromisoformat(f"{label}-01")
            else:
                row_start = date.fromisoformat(label)
        except ValueError:
            row_start = today
        if row_start > today:
            return 0
        if not self._row_has_activity(row):
            return 0
        return self._display_value(self._row_total(row, template_columns))

    def _finance_chart_points(self, rows, template_columns):
        values = [self._finance_chart_value(row, template_columns) for row in rows]
        if (self.period_combo.currentData() or "day") == "day":
            active_indexes = [index for index, value in enumerate(values) if value != 0]
            for left, right in zip(active_indexes, active_indexes[1:]):
                if right - left <= 1:
                    continue
                span = right - left
                for index in range(left + 1, right):
                    label = str(rows[index]["label"])
                    try:
                        if date.fromisoformat(label) > date.today():
                            continue
                    except ValueError:
                        pass
                    ratio = (index - left) / span
                    values[index] = values[left] + (values[right] - values[left]) * ratio
        return [
            (self._display_date(row["label"], short=True), values[index])
            for index, row in enumerate(rows)
        ]

    def _daily_row_total(self, row, template_columns):
        return (
            self._manual_period_total(row["label"], "cash")
            + self._manual_period_total(row["label"], "card")
            + self._manual_period_total(row["label"], "other")
            + self._inventory_total(row, template_columns)
            - (row.get("debt", 0) or 0)
        )

    def _inventory_total(self, row, template_columns):
        template_total = sum(row["templates"].get(template["id"], 0) for template in template_columns)
        return (row["other"] or 0) + template_total

    def _open_money_dialog(self):
        dialog = FinanceMoneyDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        data = dialog.data()
        kind = data["kind"]
        current_total = self._manual_total(data["date"], kind)
        delta = data["amount"] * data["rate_to_uzs"]
        if data["operation"] == "-" and delta > current_total:
            QMessageBox.warning(
                self,
                t("Xatolik", self.language),
                t("Kamaytirish summasi joriy mablag'dan oshib ketdi.", self.language),
            )
            return
        db.add_finance_manual_movement(
            data["date"],
            kind,
            data["operation"],
            data["amount"],
            data["currency"],
            data["rate_to_uzs"],
        )
        self.manual_values = db.get_finance_manual_values()
        start, end = self._date_range()
        movement_date = date.fromisoformat(data["date"])
        if start <= movement_date <= end:
            self.load_data()

    def _export_excel(self):
        current = self.month_edit.date().toPyDate()
        default_name = f"finance_{current.strftime('%Y_%m')}.xlsx"
        path, _ = QFileDialog.getSaveFileName(
            self,
            t("Export", self.language),
            default_name,
            "Excel (*.xlsx)",
        )
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        try:
            start, end = self._date_range()
            summary_rows, monthly_rows = self._export_rows(start, end)
            year_start = current.replace(month=1, day=1)
            year_end = min(date.today(), current.replace(month=12, day=31))
            yearly_rows = self._yearly_chart_rows(year_start, year_end)
            export_finance_xlsx(
                path,
                summary_rows,
                monthly_rows,
                yearly_rows,
                self._selected_currency().get("code", "UZS"),
            )
            QMessageBox.information(self, t("Export", self.language), t("Excel fayl saqlandi.", self.language))
        except Exception as exc:
            QMessageBox.warning(self, t("Xatolik", self.language), str(exc))

    def _export_rows(self, start, end):
        finance = db.get_finance_rows(start.isoformat(), end.isoformat())
        template_columns = finance["templates"]
        headers = (
            ["Pullar", "Naqd", "Karta"]
            + [template["name"] for template in template_columns]
            + ["Boshqa", "Qarzim", "Summa"]
        )
        rows = [["Oylik", "Yillik"], [], [t(header, self.language) for header in headers]]
        chart_rows = [[t("Sana:", self.language).replace(":", ""), t("Summa", self.language)]]
        for row in self._display_rows(finance["rows"], template_columns):
            if not self._row_has_activity(row):
                continue
            values = self._finance_row_values(row, template_columns)
            rows.append(values)
            chart_rows.append([values[0], values[-1]])
        return rows, chart_rows

    def _finance_row_values(self, row, template_columns):
        cash = self._manual_period_total(row["label"], "cash")
        card = self._manual_period_total(row["label"], "card")
        other = (row["other"] or 0) + self._manual_period_total(row["label"], "other")
        debt = row.get("debt", 0) or 0
        values = [
            self._display_date(row["label"]),
            self._display_value(cash),
            self._display_value(card),
        ]
        for template in template_columns:
            values.append(self._display_value(row["templates"].get(template["id"], 0)))
        values.append(self._display_value(other))
        values.append(self._display_value(debt))
        values.append(self._display_value(self._row_total(row, template_columns)))
        return values

    def _yearly_chart_rows(self, start, end):
        rows = [[t("Oy", self.language), t("Summa", self.language)]]
        if end < start:
            return rows
        finance = db.get_finance_rows(start.isoformat(), end.isoformat())
        template_columns = finance["templates"]
        latest_by_month = {}
        for row in finance["rows"]:
            latest_by_month[row["label"][:7]] = self._display_value(self._row_total(row, template_columns))
        for month, value in latest_by_month.items():
            rows.append([month, value])
        return rows

    def _refresh_if_day_changed(self):
        today = date.today()
        if today == self.current_day:
            return
        self.current_day = today
        self.load_data()

    def _set_loading_visible(self, visible):
        set_progress_bar_loading(self.progress_bar, visible)
        for widget in (self.prev_btn, self.next_btn, self.period_combo, self.currency_combo, self.refresh_btn):
            widget.setEnabled(not visible)

    def _language_changed(self, language):
        if language == self.language:
            return
        self.language = language
        if self._load_thread and self._load_thread.isRunning():
            self._pending_load = True
            return
        self.load_data()
