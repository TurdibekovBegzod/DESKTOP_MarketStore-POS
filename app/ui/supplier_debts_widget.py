from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QDialog, QFormLayout, QDoubleSpinBox,
    QMessageBox, QHeaderView, QComboBox, QTabWidget
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDoubleValidator
import database as db
from ui.async_loader import AsyncDataLoader, make_progress_bar
from ui.i18n import set_language, t


def _translated_debt_error(message, language):
    prefix = "To'lov joriy qarzdan oshib ketdi. Joriy qarz:"
    if str(message).startswith(prefix):
        amount = str(message)[len(prefix):].strip()
        translated_prefix = t(prefix, language)
        return f"{translated_prefix} {amount}"
    return str(message)


class PartyDialog(QDialog):
    def __init__(self, parent=None, party=None, label="Ta'minotchi"):
        super().__init__(parent)
        self.party = party
        self.label = label
        self.language = (parent.property("app_language") if parent else None) or "uz"
        title = f"{label} qo'shish" if not party else f"{label}ni tahrirlash"
        self.setWindowTitle(t(title, self.language))
        self.setFixedWidth(380)
        self._build_ui()
        set_language(self, self.language)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        form = QFormLayout()
        self.name_edit = QLineEdit(self.party["name"] if self.party else "")
        self.phone_edit = QLineEdit(self.party["phone"] if self.party and self.party["phone"] else "")
        self.note_edit = QLineEdit(self.party["note"] if self.party and self.party["note"] else "")
        self.currency_combo = QComboBox()
        for currency in db.get_currencies():
            self.currency_combo.addItem(currency["code"], currency["code"])
        current_currency = self.party["debt_currency"] if self.party and self.party["debt_currency"] else "UZS"
        idx = self.currency_combo.findData(current_currency)
        if idx >= 0:
            self.currency_combo.setCurrentIndex(idx)
        if self.party and (self.party["balance"] or 0) > 0:
            self.currency_combo.setEnabled(False)
        for widget in [self.name_edit, self.phone_edit, self.note_edit, self.currency_combo]:
            widget.setStyleSheet("border:1px solid #d1d5db;border-radius:6px;padding:7px 10px;background:white;")
        form.addRow("Nomi *:", self.name_edit)
        form.addRow("Telefon:", self.phone_edit)
        form.addRow("Qarz valyutasi:", self.currency_combo)
        form.addRow("Izoh:", self.note_edit)
        layout.addLayout(form)

        btns = QHBoxLayout()
        cancel_btn = QPushButton("Bekor")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Saqlash")
        save_btn.setStyleSheet("background:#3b82f6;color:white;border:none;border-radius:6px;padding:8px 16px;font-weight:bold;")
        save_btn.clicked.connect(self._save)
        btns.addStretch()
        btns.addWidget(cancel_btn)
        btns.addWidget(save_btn)
        layout.addLayout(btns)

    def _save(self):
        if not self.name_edit.text().strip():
            QMessageBox.warning(
                self,
                t("Xatolik", self.language),
                t(f"{self.label} nomini kiriting!", self.language),
            )
            return
        self.accept()

    def get_data(self):
        return {
            "name": self.name_edit.text().strip(),
            "phone": self.phone_edit.text().strip() or None,
            "note": self.note_edit.text().strip() or None,
            "debt_currency": self.currency_combo.currentData(),
        }


class DebtDialog(QDialog):
    def __init__(self, parent=None, title="Qarz", currency_code="UZS"):
        super().__init__(parent)
        self.currency_code = currency_code
        self.language = (parent.property("app_language") if parent else None) or "uz"
        self.setWindowTitle(t(title, self.language))
        self.setFixedWidth(340)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        form = QFormLayout()
        amount_row = QHBoxLayout()
        self.amount_edit = QLineEdit()
        self.amount_edit.setPlaceholderText("0.00")
        validator = QDoubleValidator(0, 999999999999, 2, self)
        validator.setNotation(QDoubleValidator.Notation.StandardNotation)
        self.amount_edit.setValidator(validator)
        self.amount_edit.setStyleSheet("border:1px solid #d1d5db;border-radius:6px;padding:7px 10px;background:white;")
        self.currency_lbl = QLabel(self.currency_code)
        self.currency_lbl.setStyleSheet("color:#1e293b;font-size:13px;font-weight:bold;")
        amount_row.addWidget(self.amount_edit)
        amount_row.addWidget(self.currency_lbl)
        form.addRow("Summa:", amount_row)
        layout.addLayout(form)

        btns = QHBoxLayout()
        cancel_btn = QPushButton("Bekor")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Saqlash")
        save_btn.setStyleSheet("background:#059669;color:white;border:none;border-radius:6px;padding:8px 16px;font-weight:bold;")
        save_btn.clicked.connect(self._save)
        btns.addStretch()
        btns.addWidget(cancel_btn)
        btns.addWidget(save_btn)
        layout.addLayout(btns)
        set_language(self, self.language)

    def _save(self):
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


class DebtHistoryDialog(QDialog):
    def __init__(self, parent=None, party=None, kind="supplier"):
        super().__init__(parent)
        self.party = party
        self.kind = kind
        self.language = (parent.property("app_language") if parent else None) or "uz"
        history_title = t("To'lov tarixi", self.language)
        self.setWindowTitle(f"{history_title} - {party['name']}")
        self.setMinimumSize(720, 420)
        self._build_ui()
        set_language(self, self.language)
        self.load_data()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        header = QLabel(
            f"{self.party['name']} | {t('Joriy qarz', self.language)}: "
            f"{self.party['balance']:,.2f} {self.party['debt_currency'] or 'UZS'}"
        )
        header.setStyleSheet("font-size:14px;font-weight:bold;color:#1e293b;")
        layout.addWidget(header)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Vaqt", "Amal", "Summa", "Valyuta", "Izoh"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 170)
        self.table.setColumnWidth(1, 120)
        self.table.setColumnWidth(2, 130)
        self.table.setColumnWidth(3, 80)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet(self._table_style())
        layout.addWidget(self.table)

        close_btn = QPushButton("Yopish")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)

    def load_data(self):
        self.table.setRowCount(0)
        currency = self.party["debt_currency"] or "UZS"
        movements = (
            db.get_supplier_debt_movements(self.party["id"])
            if self.kind == "supplier"
            else db.get_debtor_debt_movements(self.party["id"])
        )
        for row, movement in enumerate(movements):
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(movement["created_at"] or ""))
            if self.kind == "supplier":
                action = "Qarz olindi" if movement["type"] == "qarz" else "To'landi"
            else:
                action = "Qarz berildi" if movement["type"] == "qarz" else "Qaytarildi"
            self.table.setItem(row, 1, QTableWidgetItem(t(action, self.language)))
            amount_item = QTableWidgetItem(f"{movement['amount']:,.2f}")
            amount_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, 2, amount_item)
            self.table.setItem(row, 3, QTableWidgetItem(currency))
            self.table.setItem(row, 4, QTableWidgetItem(movement["note"] or ""))

    def _table_style(self):
        return """
            QTableWidget{background:white;border:1px solid #e2e8f0;border-radius:8px;font-size:13px;}
            QTableWidget::item{padding:7px 10px;}
            QHeaderView::section{background:#f8fafc;border:none;border-bottom:1px solid #e2e8f0;padding:8px;font-weight:bold;color:#64748b;}
            QTableWidget::item:alternate{background:#f8fafc;}
        """


class SupplierDebtsWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.tables = {}
        self._async_loader = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        self.progress_bar = make_progress_bar()
        layout.addWidget(self.progress_bar)
        self._async_loader = AsyncDataLoader(self, self.progress_bar)

        toolbar = QHBoxLayout()
        self.total_lbl = QLabel()
        self.total_lbl.setStyleSheet("font-size:14px;font-weight:bold;color:#1e293b;")
        toolbar.addWidget(self.total_lbl)
        self.total_currency_combo = QComboBox()
        self.total_currency_combo.setFixedHeight(34)
        self.total_currency_combo.setMinimumWidth(90)
        self.total_currency_combo.setStyleSheet("border:1px solid #d1d5db;border-radius:6px;padding:0 10px;background:white;")
        self._load_total_currency_combo()
        self.total_currency_combo.currentIndexChanged.connect(lambda _: self._update_toolbar())
        toolbar.addWidget(self.total_currency_combo)
        toolbar.addStretch()
        self.add_btn = QPushButton()
        self.add_btn.setFixedHeight(36)
        self.add_btn.setStyleSheet("background:#3b82f6;color:white;border:none;border-radius:6px;padding:0 14px;font-weight:bold;")
        self.add_btn.clicked.connect(self._add_current_party)
        toolbar.addWidget(self.add_btn)
        layout.addLayout(toolbar)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_table("supplier"), "Olgan qarzlar")
        self.tabs.addTab(self._build_table("debtor"), "Bergan qarzlar")
        self.tabs.currentChanged.connect(self._update_toolbar)
        layout.addWidget(self.tabs)
        self._update_toolbar()

    def _build_table(self, kind):
        table = QTableWidget()
        table.setColumnCount(6)
        total_header = "Jami olingan" if kind == "supplier" else "Jami berilgan"
        table.setHorizontalHeaderLabels(["Nomi", "Telefon", "Valyuta", "Qarz", total_header, "Amallar"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in [(1, 150), (2, 80), (3, 140), (4, 140), (5, 430)]:
            table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(column, width)
        table.verticalHeader().setDefaultSectionSize(54)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setStyleSheet(self._table_style())
        self.tables[kind] = table
        return table

    def load_data(self):
        if self.isVisible():
            self._async_loader.start(
                lambda: (db.get_all_suppliers(), db.get_all_debtors(), db.get_currencies()),
                self._apply_loaded_data,
            )
            return
        self._apply_loaded_data((db.get_all_suppliers(), db.get_all_debtors(), db.get_currencies()))

    def _apply_loaded_data(self, data):
        suppliers, debtors, currencies = data
        self._suppliers = suppliers
        self._debtors = debtors
        self._currencies = currencies
        self._load_total_currency_combo(currencies)
        self._load_table("supplier", suppliers)
        self._load_table("debtor", debtors)
        self._update_toolbar()
        set_language(self, self.property("app_language") or "uz")

    def _language_changed(self, _language):
        self._update_toolbar()
        for kind, table in self.tables.items():
            total_header = "Jami olingan" if kind == "supplier" else "Jami berilgan"
            headers = ["Nomi", "Telefon", "Valyuta", "Qarz", total_header, "Amallar"]
            for column, header in enumerate(headers):
                item = table.horizontalHeaderItem(column)
                if item:
                    item.setText(t(header, self.property("app_language") or "uz"))

    def _load_table(self, kind, rows):
        table = self.tables[kind]
        table.setRowCount(0)
        for row, party in enumerate(rows):
            table.insertRow(row)
            name_item = QTableWidgetItem(party["name"])
            name_item.setData(Qt.ItemDataRole.UserRole, dict(party))
            table.setItem(row, 0, name_item)
            table.setItem(row, 1, QTableWidgetItem(party["phone"] or ""))
            table.setItem(row, 2, QTableWidgetItem(party["debt_currency"] or "UZS"))
            table.setItem(row, 3, self._money_item(party["balance"] or 0, party["debt_currency"] or "UZS"))
            total_key = "total_received" if kind == "supplier" else "total_given"
            table.setItem(row, 4, self._money_item(party[total_key] or 0, party["debt_currency"] or "UZS"))
            table.setCellWidget(row, 5, self._actions_widget(row, kind))
            table.setRowHeight(row, 54)

    def _actions_widget(self, row, kind):
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)
        plus_btn = QPushButton("+")
        minus_btn = QPushButton("-")
        history_btn = QPushButton("Tarix")
        edit_btn = QPushButton("Tahrir")
        delete_btn = QPushButton("O'chir")
        for btn in [plus_btn, minus_btn, history_btn, edit_btn, delete_btn]:
            btn.setFixedHeight(30)
        plus_btn.setFixedWidth(38)
        minus_btn.setFixedWidth(38)
        history_btn.setFixedWidth(82)
        edit_btn.setFixedWidth(86)
        delete_btn.setFixedWidth(90)
        plus_btn.setStyleSheet(self._button_style("#ecfdf5", "#065f46", "#6ee7b7", "#10b981"))
        minus_btn.setStyleSheet(self._button_style("#fef2f2", "#b91c1c", "#fca5a5", "#ef4444"))
        history_btn.setStyleSheet(self._button_style("#eff6ff", "#1d4ed8", "#93c5fd", "#3b82f6"))
        edit_btn.setStyleSheet(self._button_style("#fff7ed", "#9a3412", "#fdba74", "#fb923c"))
        delete_btn.setStyleSheet(self._button_style("#fef2f2", "#991b1b", "#fca5a5", "#dc2626"))
        plus_btn.clicked.connect(lambda _, r=row, k=kind: self._change_debt(r, k, "plus"))
        minus_btn.clicked.connect(lambda _, r=row, k=kind: self._change_debt(r, k, "minus"))
        history_btn.clicked.connect(lambda _, r=row, k=kind: self._show_history(r, k))
        edit_btn.clicked.connect(lambda _, r=row, k=kind: self._edit_party(r, k))
        delete_btn.clicked.connect(lambda _, r=row, k=kind: self._delete_party(r, k))
        for btn in [plus_btn, minus_btn, history_btn, edit_btn, delete_btn]:
            layout.addWidget(btn)
        return widget

    def _current_kind(self):
        return "supplier" if self.tabs.currentIndex() == 0 else "debtor"

    def _label_for(self, kind):
        return "Ta'minotchi" if kind == "supplier" else "Qarz oluvchi"

    def _party_at_row(self, row, kind):
        item = self.tables[kind].item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _update_toolbar(self):
        kind = self._current_kind()
        language = self.property("app_language") or "uz"
        rows = getattr(self, "_suppliers", []) if kind == "supplier" else getattr(self, "_debtors", [])
        target_currency = self.total_currency_combo.currentData() if hasattr(self, "total_currency_combo") else "UZS"
        target_currency = target_currency or "UZS"
        total = self._converted_debt_total(rows, target_currency)
        title = "Umumiy olingan qarz" if kind == "supplier" else "Umumiy berilgan qarz"
        self.total_lbl.setText(f"{t(title, language)}: {total:,.2f} {target_currency}")
        self.add_btn.setText(t("+ Ta'minotchi" if kind == "supplier" else "+ Qarz oluvchi", language))

    def _load_total_currency_combo(self, currencies=None):
        current = self.total_currency_combo.currentData() if hasattr(self, "total_currency_combo") else "UZS"
        self.total_currency_combo.blockSignals(True)
        self.total_currency_combo.clear()
        available = {currency["code"] for currency in (currencies if currencies is not None else db.get_currencies())}
        for code in ("UZS", "USD", "EUR"):
            if code in available or code == "UZS":
                self.total_currency_combo.addItem(code, code)
        index = self.total_currency_combo.findData(current)
        if index >= 0:
            self.total_currency_combo.setCurrentIndex(index)
        self.total_currency_combo.blockSignals(False)

    def _currency_rate_map(self):
        rates = {"UZS": 1}
        for currency in getattr(self, "_currencies", None) or db.get_currencies():
            rates[currency["code"]] = currency["rate_to_uzs"] or 1
        return rates

    def _converted_debt_total(self, rows, target_currency):
        rates = self._currency_rate_map()
        target_rate = rates.get(target_currency, 1) or 1
        total_uzs = 0
        for party in rows:
            source_currency = party["debt_currency"] or "UZS"
            source_rate = rates.get(source_currency, 1) or 1
            total_uzs += (party["balance"] or 0) * source_rate
        return total_uzs / target_rate

    def _add_current_party(self):
        kind = self._current_kind()
        dlg = PartyDialog(self, label=self._label_for(kind))
        if dlg.exec():
            data = dlg.get_data()
            if kind == "supplier":
                db.add_supplier(data["name"], data["phone"], data["note"], data["debt_currency"])
            else:
                db.add_debtor(data["name"], data["phone"], data["note"], data["debt_currency"])
            self.load_data()

    def _edit_party(self, row, kind):
        party = self._party_at_row(row, kind)
        if not party:
            return
        dlg = PartyDialog(self, party, self._label_for(kind))
        if dlg.exec():
            data = dlg.get_data()
            if kind == "supplier":
                db.update_supplier(party["id"], data["name"], data["phone"], data["note"], data["debt_currency"])
            else:
                db.update_debtor(party["id"], data["name"], data["phone"], data["note"], data["debt_currency"])
            self.load_data()

    def _change_debt(self, row, kind, mode):
        party = self._party_at_row(row, kind)
        if not party:
            return
        if kind == "supplier":
            title = "Qarzni oshirish" if mode == "plus" else "Qarzni kamaytirish"
        else:
            title = "Qarz berish" if mode == "plus" else "Qarz qaytarildi"
        currency = party["debt_currency"] or "UZS"
        dlg = DebtDialog(self, title, currency)
        if dlg.exec():
            amount = dlg.amount()
            try:
                if kind == "supplier" and mode == "plus":
                    db.add_supplier_debt(party["id"], amount, f"{party['name']}dan qarz olindi")
                elif kind == "supplier":
                    db.pay_supplier_debt(party["id"], amount, f"{party['name']}ga to'landi")
                elif mode == "plus":
                    db.add_debtor_debt(party["id"], amount, f"{party['name']}ga qarz berildi")
                else:
                    db.pay_debtor_debt(party["id"], amount, f"{party['name']}dan qaytarildi")
                self.load_data()
            except db.AppError as exc:
                language = self.property("app_language") or "uz"
                QMessageBox.warning(self, t("Xatolik", language), _translated_debt_error(exc, language))

    def _show_history(self, row, kind):
        party = self._party_at_row(row, kind)
        if not party:
            return
        dlg = DebtHistoryDialog(self, party, kind)
        dlg.exec()

    def _delete_party(self, row, kind):
        party = self._party_at_row(row, kind)
        if not party:
            return
        language = self.property("app_language") or "uz"
        if kind == "supplier":
            title = "Ta'minotchini o'chirish"
            delete_question = t("o'chirilsinmi?", language)
            supplier_hint = t("Mahsulotlar o'chmaydi, faqat 'Kimdan olingan' maydoni bo'shatiladi.", language)
            text = (
                f"'{party['name']}' {delete_question}\n\n"
                f"{supplier_hint}"
            )
        else:
            title = "Qarz oluvchini o'chirish"
            debtor_question = t("va uning qarz tarixi o'chirilsinmi?", language)
            text = f"'{party['name']}' {debtor_question}"
        reply = QMessageBox.question(
            self,
            t(title, language),
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            if kind == "supplier":
                db.delete_supplier(party["id"])
            else:
                db.delete_debtor(party["id"])
            self.load_data()

    def _money_item(self, value, currency="UZS"):
        item = QTableWidgetItem(f"{value:,.2f} {currency}")
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return item

    def _table_style(self):
        return """
            QTableWidget{background:white;border:1px solid #e2e8f0;border-radius:8px;font-size:13px;}
            QTableWidget::item{padding:7px 10px;}
            QHeaderView::section{background:#f8fafc;border:none;border-bottom:1px solid #e2e8f0;padding:8px;font-weight:bold;color:#64748b;}
            QTableWidget::item:alternate{background:#f8fafc;}
        """

    def _button_style(self, bg, fg, border, hover):
        return f"""
            QPushButton {{
                background:{bg};
                color:{fg};
                border:1px solid {border};
                border-radius:6px;
                font-weight:bold;
            }}
            QPushButton:hover {{
                background:{hover};
                color:white;
                border-color:{hover};
            }}
            QPushButton:pressed {{
                background:#1e293b;
                color:white;
                border-color:#1e293b;
                padding-top:2px;
            }}
        """
