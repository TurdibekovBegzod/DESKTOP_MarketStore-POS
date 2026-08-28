from datetime import date, timedelta
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QFrame,
    QComboBox, QDateEdit, QCalendarWidget, QDialog, QFormLayout
)
from PyQt6.QtCore import Qt, QTimer, QDate, QRegularExpression
from PyQt6.QtGui import QRegularExpressionValidator
import database as db
from ui.async_loader import AsyncDataLoader, make_progress_bar, set_progress_bar_loading
from ui.i18n import t


def _compact_time_str(dt_str):
    if not dt_str:
        return "-"
    try:
        parts = str(dt_str).strip().split()
        if len(parts) >= 2:
            date_part, time_part = parts[0], parts[1]
            d_parts = date_part.split("-")
            t_parts = time_part.split(":")
            if len(d_parts) == 3:
                short_date = f"{d_parts[2]}.{d_parts[1]}"
            else:
                short_date = date_part
            short_time = f"{t_parts[0]}:{t_parts[1]}" if len(t_parts) >= 2 else time_part
            return f"{short_date} {short_time}"
    except Exception:
        pass
    return str(dt_str)[:16]


class CashierRewardDialog(QDialog):
    def __init__(self, parent, archive_row):
        super().__init__(parent)
        self.setWindowTitle("Sotishni yakunlash")
        self.setFixedWidth(420)
        self.setStyleSheet("background: white; border-radius: 8px; font-size: 13px;")
        self._reward_uzs = 0.0

        # Load currency rates
        self.currencies = [dict(c) for c in db.get_currencies()]
        self.rates = {c["code"]: (c.get("rate_to_uzs") or 1.0) for c in self.currencies}
        if "USD" not in self.rates or self.rates["USD"] <= 1:
            self.rates["USD"] = 12800.0
        if "EUR" not in self.rates or self.rates["EUR"] <= 1:
            self.rates["EUR"] = 13900.0
        self.rates["UZS"] = 1.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        title = QLabel("Sotishni yakunlash va oylik ajratish")
        title.setStyleSheet("font-size: 15px; font-weight: bold; color: #1e293b;")
        layout.addWidget(title)

        info_box = QFrame()
        info_box.setStyleSheet("background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 8px;")
        info_layout = QVBoxLayout(info_box)
        info_layout.setContentsMargins(10, 8, 10, 8)
        info_layout.setSpacing(4)

        cashier_name = archive_row.get("cashier_name") or "-"
        product_name = archive_row.get("product_name") or "-"
        item_total = archive_row.get("item_total_after_discount", archive_row.get("subtotal", 0)) or 0

        info_layout.addWidget(QLabel(f"<b>Kassir:</b> {cashier_name}"))
        info_layout.addWidget(QLabel(f"<b>Mahsulot:</b> {product_name}"))
        info_layout.addWidget(QLabel(f"<b>Jami sotuv:</b> {item_total:,.0f} so'm"))
        layout.addWidget(info_box)

        prompt_lbl = QLabel("Kassirga ushbu sotuv uchun ajratiladigan summa:")
        prompt_lbl.setStyleSheet("color: #475569; font-weight: 500;")
        layout.addWidget(prompt_lbl)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)

        self.reward_edit = QLineEdit()
        self.reward_edit.setPlaceholderText("0.00")
        self.reward_edit.setValidator(
            QRegularExpressionValidator(QRegularExpression(r"^\d*([.,]\d{0,2})?$"), self)
        )
        self.reward_edit.setMaxLength(16)
        self.reward_edit.setFixedHeight(38)
        self.reward_edit.setStyleSheet("""
            QLineEdit {
                background: white; border: 1px solid #cbd5e1; border-radius: 6px;
                padding: 0 10px; font-size: 14px; font-weight: bold; color: #0f172a;
            }
            QLineEdit:focus { border-color: #10b981; }
        """)
        input_row.addWidget(self.reward_edit, 1)

        self.currency_combo = QComboBox()
        self.currency_combo.setFixedHeight(38)
        self.currency_combo.setMinimumWidth(110)
        self.currency_combo.setStyleSheet("""
            QComboBox {
                background: white; border: 1px solid #cbd5e1; border-radius: 6px;
                padding: 0 10px; font-size: 13px; font-weight: bold; color: #1e293b;
            }
            QComboBox:focus { border-color: #10b981; }
        """)
        self.currency_combo.addItem("USD ($)", "USD")
        self.currency_combo.addItem("EUR (€)", "EUR")
        self.currency_combo.addItem("UZS (so'm)", "UZS")
        self.currency_combo.currentIndexChanged.connect(self._on_currency_changed)
        input_row.addWidget(self.currency_combo)

        layout.addLayout(input_row)

        self.equiv_lbl = QLabel()
        self.equiv_lbl.setStyleSheet("color: #64748b; font-size: 12px; font-style: italic;")
        layout.addWidget(self.equiv_lbl)

        self.reward_edit.textChanged.connect(self._update_equiv)

        # Default to USD
        self.currency_combo.setCurrentIndex(0)
        self._on_currency_changed()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()

        cancel_btn = QPushButton("Bekor qilish")
        cancel_btn.setFixedHeight(36)
        cancel_btn.setStyleSheet("""
            QPushButton { background: white; border: 1px solid #cbd5e1; border-radius: 6px; padding: 0 16px; color: #64748b; font-weight: bold; }
            QPushButton:hover { background: #f1f5f9; color: #1e293b; }
        """)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        confirm_btn = QPushButton("Tasdiqlash va yakunlash")
        confirm_btn.setFixedHeight(36)
        confirm_btn.setStyleSheet("""
            QPushButton { background: #10b981; border: none; border-radius: 6px; padding: 0 18px; color: white; font-weight: bold; }
            QPushButton:hover { background: #059669; }
            QPushButton:pressed { background: #047857; }
        """)
        confirm_btn.clicked.connect(self._on_confirm)
        btn_row.addWidget(confirm_btn)

        layout.addLayout(btn_row)

    def _on_currency_changed(self):
        code = self.currency_combo.currentData()
        self.reward_edit.setPlaceholderText("0" if code == "UZS" else "0.00")
        self._update_equiv()

    def _reward_value(self):
        text = self.reward_edit.text().strip().replace(",", ".")
        try:
            return max(0.0, float(text)) if text else 0.0
        except ValueError:
            return 0.0

    def _update_equiv(self, *_args):
        val = self._reward_value()
        code = self.currency_combo.currentData()
        rate = self.rates.get(code, 1.0)
        uzs_val = val * rate
        if code != "UZS" and val > 0:
            self.equiv_lbl.setText(f"≈ {uzs_val:,.0f} so'm (Kurs: 1 {code} = {rate:,.0f} so'm)")
        elif code == "UZS" and val > 0:
            usd_rate = self.rates.get("USD", 12800.0)
            self.equiv_lbl.setText(f"≈ {val / usd_rate:,.2f} USD")
        else:
            self.equiv_lbl.setText("")

    def _on_confirm(self):
        val = self._reward_value()
        if val > 100_000_000:
            QMessageBox.warning(self, "Xatolik", "Summa 100 000 000 dan oshmasligi kerak.")
            self.reward_edit.setFocus()
            return
        code = self.currency_combo.currentData()
        rate = self.rates.get(code, 1.0)
        self._reward_uzs = val * rate
        self.accept()

    def get_reward(self):
        return self._reward_uzs


class FinalizeSalesWidget(QWidget):
    def __init__(self, user=None):
        super().__init__()
        self.user = user or {}
        self._async_loader = None
        self._render_generation = 0
        self._last_loaded_rows = []
        self._build_ui()
        self.load_data()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.progress_bar = make_progress_bar()
        layout.addWidget(self.progress_bar)
        self._async_loader = AsyncDataLoader(self, self.progress_bar)

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)

        # Search bar
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Qidirish (Mahsulot, Shtrix-kod, Kassir)...")
        self.search_edit.setMinimumWidth(260)
        self.search_edit.setFixedHeight(38)
        self.search_edit.setStyleSheet("""
            QLineEdit {
                background: white; border: 1px solid #cbd5e1; border-radius: 6px;
                padding: 0 12px; font-size: 13px;
            }
            QLineEdit:focus { border: 1px solid #10b981; }
        """)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self.load_data)
        self.search_edit.textChanged.connect(lambda: self._search_timer.start())
        toolbar.addWidget(self.search_edit)

        # Date controls
        self.date_lbl = QLabel("Sana:")
        self.date_lbl.setStyleSheet("font-size: 13px; font-weight: bold; color: #475569;")
        toolbar.addWidget(self.date_lbl)

        self.prev_date_btn = QPushButton("<")
        self.prev_date_btn.setFixedSize(32, 38)
        self.prev_date_btn.setStyleSheet("""
            QPushButton {
                background: white; border: 1px solid #cbd5e1; border-radius: 6px;
                font-weight: bold; font-size: 14px; color: #475569;
            }
            QPushButton:hover { background: #f1f5f9; color: #0f172a; }
            QPushButton:pressed { background: #e2e8f0; }
        """)
        self.prev_date_btn.clicked.connect(lambda: self._shift_period(-1))
        toolbar.addWidget(self.prev_date_btn)

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
        self.date_edit.setFixedSize(170, 38)
        self.date_edit.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.date_edit.setStyleSheet("""
            QDateEdit {
                background: white;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 0 36px 0 12px;
                font-size: 13px;
                font-weight: 500;
                color: #1e293b;
            }
            QDateEdit:focus {
                border-color: #10b981;
            }
            QDateEdit::drop-down {
                subcontrol-origin: border;
                subcontrol-position: top right;
                width: 30px;
                border-left: 1px solid #e2e8f0;
                border-top-right-radius: 6px;
                border-bottom-right-radius: 6px;
                background: #f8fafc;
            }
            QDateEdit::drop-down:hover {
                background: #f1f5f9;
            }
        """)
        self.date_edit.dateChanged.connect(lambda: self.load_data())
        toolbar.addWidget(self.date_edit)

        self.next_date_btn = QPushButton(">")
        self.next_date_btn.setFixedSize(32, 38)
        self.next_date_btn.setStyleSheet("""
            QPushButton {
                background: white; border: 1px solid #cbd5e1; border-radius: 6px;
                font-weight: bold; font-size: 14px; color: #475569;
            }
            QPushButton:hover { background: #f1f5f9; color: #0f172a; }
            QPushButton:pressed { background: #e2e8f0; }
        """)
        self.next_date_btn.clicked.connect(lambda: self._shift_period(1))
        toolbar.addWidget(self.next_date_btn)

        self.period_combo = QComboBox()
        self.period_combo.setFixedSize(115, 38)
        self.period_combo.setStyleSheet("""
            QComboBox {
                background: white; border: 1px solid #cbd5e1; border-radius: 6px;
                padding: 0 10px; font-size: 13px; color: #1e293b;
            }
            QComboBox:focus { border-color: #10b981; }
        """)
        self.period_combo.addItem("Kunlik", "day")
        self.period_combo.addItem("Haftalik", "week")
        self.period_combo.addItem("Oylik", "month")
        self.period_combo.addItem("Barchasi", "all")
        self.period_combo.currentIndexChanged.connect(lambda: self.load_data())
        toolbar.addWidget(self.period_combo)

        toolbar.addStretch()

        # Finalize all button
        self.finalize_all_btn = QPushButton("Barchasini yakunlash")
        self.finalize_all_btn.setFixedHeight(38)
        self.finalize_all_btn.setStyleSheet("""
            QPushButton {
                background: #10b981; color: white; border: none; border-radius: 6px;
                padding: 0 16px; font-size: 13px; font-weight: bold;
            }
            QPushButton:hover { background: #059669; }
            QPushButton:pressed { background: #047857; padding-top: 2px; }
        """)
        self.finalize_all_btn.clicked.connect(self._finalize_all_sales)
        toolbar.addWidget(self.finalize_all_btn)

        layout.addLayout(toolbar)

        # Stats bar
        self.stats_lbl = QLabel()
        self.stats_lbl.setStyleSheet("color: #64748b; font-size: 13px; font-weight: 500;")
        layout.addWidget(self.stats_lbl)

        # Table (9 columns: Kassir, Mahsulot, Shtrix-kod, Xarid narxi, Sotish narxi, Sotilgan narxi, To'lov, Vaqt, Amallar)
        self.table = QTableWidget()
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels([
            "Kassir", "Mahsulot", "Shtrix-kod", "Xarid narxi",
            "Sotish narxi", "Sotilgan narxi", "To'lov", "Vaqt", "Amallar"
        ])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column, width in [
            (0, 120), (2, 110), (3, 115), (4, 115),
            (5, 120), (6, 100), (7, 100), (8, 160)
        ]:
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(column, width)
        self.table.verticalHeader().setDefaultSectionSize(52)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("""
            QTableWidget {
                background: white; border: 1px solid #e2e8f0;
                border-radius: 8px; font-size: 13px;
            }
            QTableWidget::item { padding: 6px 10px; }
            QTableWidget::item:selected { background: #dbeafe; color: #1e40af; }
            QHeaderView::section {
                background: #f8fafc; border: none;
                border-bottom: 1px solid #e2e8f0;
                padding: 8px; font-weight: bold; color: #64748b;
            }
            QTableWidget::item:alternate { background: #f8fafc; }
        """)
        layout.addWidget(self.table, 1)

    def _date_range(self):
        selected = self.date_edit.date().toPyDate()
        period = self.period_combo.currentData()
        if period == "day":
            return selected.isoformat(), selected.isoformat()
        if period == "week":
            start = selected - timedelta(days=selected.weekday())
            end = start + timedelta(days=6)
            return start.isoformat(), end.isoformat()
        if period == "month":
            start = selected.replace(day=1)
            next_month = selected.replace(year=selected.year + 1, month=1, day=1) if selected.month == 12 else selected.replace(month=selected.month + 1, day=1)
            end = next_month - timedelta(days=1)
            return start.isoformat(), end.isoformat()
        return None, None

    def _shift_period(self, direction):
        current = self.date_edit.date()
        period = self.period_combo.currentData()
        if period == "day":
            self.date_edit.setDate(current.addDays(direction))
        elif period == "week":
            self.date_edit.setDate(current.addDays(direction * 7))
        elif period == "month":
            self.date_edit.setDate(current.addMonths(direction))

    def load_data(self):
        query = self.search_edit.text().strip() if hasattr(self, "search_edit") else ""
        start_date, end_date = self._date_range()

        if self.isVisible():
            self._async_loader.start(
                lambda: {
                    "rows": [dict(r) for r in db.get_product_sales_archive(query, start_date, end_date, only_cashiers=True, only_pending=True)],
                },
                self._apply_loaded_data,
            )
        else:
            self._apply_loaded_data({
                "rows": [dict(r) for r in db.get_product_sales_archive(query, start_date, end_date, only_cashiers=True, only_pending=True)],
            })

    def _apply_loaded_data(self, data):
        rows = data.get("rows", [])
        self._last_loaded_rows = rows

        pending_count = len(rows)
        pending_total = sum(r.get("item_total_after_discount", r.get("subtotal", 0)) for r in rows)

        language = self.property("app_language") or "uz"
        unit = t("ta", language)
        so_m = t("so'm", language)
        self.stats_lbl.setText(
            f"{t('Kutilmoqda', language)}: <b>{pending_count}</b> {unit} ({pending_total:,.0f} {so_m})"
        )

        self._fill_table(rows)

    def _fill_table(self, rows):
        self.table.setRowCount(0)
        self.table.setUpdatesEnabled(False)
        self._render_generation += 1
        generation = self._render_generation
        if self.progress_bar:
            set_progress_bar_loading(self.progress_bar, True)

        state = {"row": 0}

        def render_chunk():
            if generation != self._render_generation:
                return
            end = min(state["row"] + 30, len(rows))
            for row_idx in range(state["row"], end):
                self._fill_table_row(row_idx, rows[row_idx])
            state["row"] = end
            if state["row"] < len(rows):
                QTimer.singleShot(0, render_chunk)
                return
            self.table.setUpdatesEnabled(True)
            if self.progress_bar:
                set_progress_bar_loading(self.progress_bar, False)

        render_chunk()

    def _fill_table_row(self, row_index, archive_row):
        self.table.insertRow(row_index)
        data = dict(archive_row)
        language = self.property("app_language") or "uz"
        unit = t("so'm", language)

        cost_val = data.get("cost", 0) or 0
        price_val = data.get("price", 0) or 0
        sold_price_val = data.get("item_total_after_discount", data.get("active_subtotal", data.get("subtotal", 0))) or 0

        payment_labels = {
            "naqd": t("Naqd", language),
            "plastik karta": t("Plastik karta", language),
            "qarz": t("Qarz", language),
        }
        payment_str = payment_labels.get(data.get("payment_method"), data.get("payment_method") or "")
        time_str = _compact_time_str(data.get("created_at"))

        # Columns: Kassir, Mahsulot, Shtrix-kod, Xarid narxi, Sotish narxi, Sotilgan narxi, To'lov, Vaqt, Amallar
        values = [
            str(data.get("cashier_name") or "-"),
            str(data.get("product_name") or "-"),
            str(data.get("barcode") or "-"),
            f"{cost_val:,.0f} {unit}",
            f"{price_val:,.0f} {unit}",
            f"{sold_price_val:,.0f} {unit}",
            payment_str,
            time_str,
        ]

        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            if column == 0:
                item.setData(Qt.ItemDataRole.UserRole, data)
            if column in (3, 4, 5):
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            elif column in (2, 6, 7):
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row_index, column, item)

        # Actions (Column 8)
        action_widget = QWidget()
        action_layout = QHBoxLayout(action_widget)
        action_layout.setContentsMargins(4, 4, 4, 4)
        action_layout.setSpacing(0)

        finalize_btn = QPushButton(t("Sotishni yakunlash", language))
        finalize_btn.setFixedHeight(32)
        finalize_btn.setStyleSheet("""
            QPushButton {
                background: #10b981; color: white; border: none; border-radius: 6px;
                font-weight: bold; font-size: 12px; padding: 0 12px;
            }
            QPushButton:hover { background: #059669; }
            QPushButton:pressed { background: #047857; padding-top: 2px; }
        """)
        finalize_btn.clicked.connect(lambda _, row_data=data: self._finalize_single_sale(row_data))
        action_layout.addWidget(finalize_btn)

        self.table.setCellWidget(row_index, 8, action_widget)
        self.table.setRowHeight(row_index, 52)

    def _finalize_single_sale(self, row_data):
        language = self.property("app_language") or "uz"
        sale_id = row_data.get("sale_id")
        if not sale_id:
            return

        dlg = CashierRewardDialog(self, row_data)
        if dlg.exec():
            reward = dlg.get_reward()
            try:
                db.finalize_sale(sale_id, cashier_reward=reward)
                self.load_data()
            except db.AppError as exc:
                QMessageBox.warning(self, t("Xatolik", language), str(exc))

    def _finalize_all_sales(self):
        language = self.property("app_language") or "uz"
        reply = QMessageBox.question(
            self,
            t("Sotishni yakunlash", language),
            t("Barcha kutilayotgan sotuvlar yakunlanib, hisobotlar bo'limiga qo'shilsinmi?", language),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            count = db.finalize_all_pending_sales()
            if count == 0:
                QMessageBox.information(self, t("Ma'lumot", language), t("Barcha sotuvlar allaqachon yakunlangan.", language))
            self.load_data()
        except db.AppError as exc:
            QMessageBox.warning(self, t("Xatolik", language), str(exc))

    def _language_changed(self, language):
        if hasattr(self, "finalize_all_btn"):
            self.finalize_all_btn.setText(t("Barchasini yakunlash", language))
        if hasattr(self, "date_lbl"):
            self.date_lbl.setText(t("Sana:", language))
