from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QDialog, QFormLayout, QDoubleSpinBox,
    QMessageBox, QHeaderView, QComboBox, QTabWidget, QMenu
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
    def __init__(self, parent=None, party=None, label="Ta'minotchi", kind="supplier"):
        super().__init__(parent)
        self.party = party
        self.label = label
        self.kind = kind
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
        self.name_lbl = QLabel("Nomi *:")
        self.phone_lbl = QLabel("Telefon:")
        self.cashier_lbl = None
        self.cashier_combo = None
        self.subject_type_combo = None
        self.initial_debt_edit = None

        if self.kind == "debtor":
            self.subject_type_combo = QComboBox()
            self.subject_type_combo.addItem(t("Qarz oluvchi", self.language), "person")
            self.subject_type_combo.addItem(t("Kassir", self.language), "cashier")
            self.cashier_lbl = QLabel("Kassir:")
            self.cashier_combo = QComboBox()
            for user in db.get_debt_cashiers():
                display_name = (user.get("username") or user.get("email") or "").strip()
                self.cashier_combo.addItem(display_name, dict(user))
            linked_user_id = self.party.get("user_id") if self.party else None
            if linked_user_id:
                self.subject_type_combo.setCurrentIndex(self.subject_type_combo.findData("cashier"))
                cashier_index = next(
                    (
                        index for index in range(self.cashier_combo.count())
                        if (self.cashier_combo.itemData(index) or {}).get("id") == linked_user_id
                    ),
                    -1,
                )
                if cashier_index >= 0:
                    self.cashier_combo.setCurrentIndex(cashier_index)
            if self.party:
                self.subject_type_combo.setEnabled(False)
                self.cashier_combo.setEnabled(False)
            form.addRow(t("Qarz oluvchi turi:", self.language), self.subject_type_combo)

        self.currency_combo = QComboBox()
        for currency in db.get_currencies():
            self.currency_combo.addItem(currency["code"], currency["code"])
        current_currency = (
            self.party["debt_currency"]
            if self.party and self.party["debt_currency"]
            else db.get_app_settings().get("currency", "UZS")
        )
        idx = self.currency_combo.findData(current_currency)
        if idx >= 0:
            self.currency_combo.setCurrentIndex(idx)
        if self.party and (self.party["balance"] or 0) > 0:
            self.currency_combo.setEnabled(False)
        styled_widgets = [self.name_edit, self.phone_edit, self.note_edit, self.currency_combo]
        if self.subject_type_combo is not None:
            styled_widgets.extend([self.subject_type_combo, self.cashier_combo])
        for widget in styled_widgets:
            widget.setStyleSheet("border:1px solid #d1d5db;border-radius:6px;padding:7px 10px;background:white;")
        form.addRow(self.name_lbl, self.name_edit)
        form.addRow(self.phone_lbl, self.phone_edit)
        if self.cashier_combo is not None:
            form.addRow(self.cashier_lbl, self.cashier_combo)
        form.addRow("Qarz valyutasi:", self.currency_combo)
        if self.kind == "debtor" and not self.party:
            self.initial_debt_edit = QLineEdit()
            self.initial_debt_edit.setPlaceholderText("0.00")
            validator = QDoubleValidator(0, 999999999999, 2, self)
            validator.setNotation(QDoubleValidator.Notation.StandardNotation)
            self.initial_debt_edit.setValidator(validator)
            self.initial_debt_edit.setStyleSheet("border:1px solid #d1d5db;border-radius:6px;padding:7px 10px;background:white;")
            form.addRow(t("Boshlang'ich qarz:", self.language), self.initial_debt_edit)
        form.addRow("Izoh:", self.note_edit)
        layout.addLayout(form)

        if self.subject_type_combo is not None:
            self.subject_type_combo.currentIndexChanged.connect(self._subject_type_changed)
            self._subject_type_changed()

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
        if self._is_cashier() and (self.cashier_combo is None or self.cashier_combo.currentData() is None):
            QMessageBox.warning(self, t("Xatolik", self.language), t("Kassirni tanlang!", self.language))
            return
        if not self.name_edit.text().strip():
            if self._is_cashier():
                self.accept()
                return
            QMessageBox.warning(
                self,
                t("Xatolik", self.language),
                t(f"{self.label} nomini kiriting!", self.language),
            )
            return
        self.accept()

    def get_data(self):
        selected_user = self.cashier_combo.currentData() if self._is_cashier() else None
        name = (
            (selected_user.get("username") or selected_user.get("email") or "").strip()
            if selected_user else self.name_edit.text().strip()
        )
        return {
            "name": name,
            "phone": None if selected_user else self.phone_edit.text().strip() or None,
            "note": self.note_edit.text().strip() or None,
            "debt_currency": self.currency_combo.currentData(),
            "user_id": selected_user.get("id") if selected_user else self.party.get("user_id") if self.party else None,
            "initial_debt": self.initial_debt(),
        }

    def initial_debt(self):
        if self.initial_debt_edit is None:
            return 0
        text = self.initial_debt_edit.text().strip().replace(" ", "").replace(",", ".")
        try:
            return float(text) if text else 0
        except ValueError:
            return 0

    def _is_cashier(self):
        return self.subject_type_combo is not None and self.subject_type_combo.currentData() == "cashier"

    def _subject_type_changed(self, *_args):
        is_cashier = self._is_cashier()
        for widget in (self.name_lbl, self.name_edit, self.phone_lbl, self.phone_edit):
            widget.setVisible(not is_cashier)
        if self.cashier_lbl is not None:
            self.cashier_lbl.setVisible(is_cashier)
            self.cashier_combo.setVisible(is_cashier)


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
        for column, width in [(1, 150), (2, 80), (3, 140), (4, 140), (5, 80)]:
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
                lambda: (db.get_all_suppliers(), db.get_all_debtors(), db.get_currencies(), db.get_users()),
                self._apply_loaded_data,
            )
            return
        self._apply_loaded_data((db.get_all_suppliers(), db.get_all_debtors(), db.get_currencies(), db.get_users()))

    def _apply_loaded_data(self, data):
        suppliers, debtors, currencies, users = data
        self._suppliers = suppliers
        self._debtors = debtors
        self._currencies = currencies
        self._users_by_id = {user["id"]: dict(user) for user in users}
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
            display_name = party["name"]
            contact = party["phone"] or ""
            if kind == "debtor" and party.get("user_id"):
                user = self._users_by_id.get(party["user_id"], {})
                display_name = user.get("username") or party.get("cashier_name") or party["name"]
                contact = user.get("email") or party.get("cashier_email") or ""
                display_name = f"{display_name} ({t('Kassir', self.property('app_language') or 'uz')})"
            name_item = QTableWidgetItem(display_name)
            name_item.setData(Qt.ItemDataRole.UserRole, dict(party))
            table.setItem(row, 0, name_item)
            table.setItem(row, 1, QTableWidgetItem(contact))
            table.setItem(row, 2, QTableWidgetItem(party["debt_currency"] or "UZS"))
            table.setItem(row, 3, self._money_item(party["balance"] or 0, party["debt_currency"] or "UZS"))
            total_key = "total_received" if kind == "supplier" else "total_given"
            table.setItem(row, 4, self._money_item(party[total_key] or 0, party["debt_currency"] or "UZS"))
            table.setCellWidget(row, 5, self._actions_widget(row, kind))
            table.setRowHeight(row, 54)

    def _menu_button_widget(self, on_click):
        widget = QWidget()
        widget.setStyleSheet("background:transparent;")
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        button = QPushButton("⋮")
        button.setFixedSize(34, 32)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        language = self.property("app_language") or "uz"
        button.setToolTip(t("Amallar", language))
        button.setStyleSheet("""
            QPushButton {
                background:#ffffff;color:#334155;border:1px solid #cbd5e1;
                border-radius:6px;font-size:20px;font-weight:bold;padding:0;
            }
            QPushButton:hover { background:#f1f5f9;border-color:#94a3b8; }
            QPushButton:pressed { background:#e2e8f0; }
        """)
        button.clicked.connect(lambda _=False: on_click(button))
        layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignCenter)
        return widget

    @staticmethod
    def _actions_menu_style():
        return """
            QMenu { background:#ffffff;color:#1e293b;border:1px solid #cbd5e1;padding:6px; }
            QMenu::item { min-width:160px;padding:8px 14px;border-radius:4px; }
            QMenu::item:selected { background:#eff6ff;color:#1d4ed8; }
            QMenu::item:disabled { color:#94a3b8; }
        """

    def _build_debt_actions_menu(self, row, kind, parent=None):
        language = self.property("app_language") or "uz"
        menu = QMenu(parent or self)
        menu.setStyleSheet(self._actions_menu_style())
        if kind == "supplier":
            plus_label = t("Qarz qo'shish", language)
            minus_label = t("Qarz to'lash", language)
        else:
            plus_label = t("Qarz berish", language)
            minus_label = t("Qarz qaytarish", language)
        history_label = t("Tarix", language)
        edit_label = t("Tahrir", language)
        delete_label = t("O'chir", language)

        plus_action = menu.addAction(f"➕ {plus_label}")
        plus_action.triggered.connect(lambda _=False, r=row, k=kind: self._change_debt(r, k, "plus"))

        minus_action = menu.addAction(f"➖ {minus_label}")
        minus_action.triggered.connect(lambda _=False, r=row, k=kind: self._change_debt(r, k, "minus"))

        history_action = menu.addAction(f"📜 {history_label}")
        history_action.triggered.connect(lambda _=False, r=row, k=kind: self._show_history(r, k))

        edit_action = menu.addAction(f"✏️ {edit_label}")
        edit_action.triggered.connect(lambda _=False, r=row, k=kind: self._edit_party(r, k))

        delete_action = menu.addAction(f"🗑️ {delete_label}")
        delete_action.triggered.connect(lambda _=False, r=row, k=kind: self._delete_party(r, k))

        return menu

    def _show_debt_actions_menu(self, row, kind, button):
        menu = self._build_debt_actions_menu(row, kind, button)
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def _actions_widget(self, row, kind):
        return self._menu_button_widget(
            lambda button, r=row, k=kind: self._show_debt_actions_menu(r, k, button)
        )

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
        current = current or db.get_app_settings().get("currency", "UZS")
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
        dlg = PartyDialog(self, label=self._label_for(kind), kind=kind)
        if dlg.exec():
            data = dlg.get_data()
            try:
                if kind == "supplier":
                    db.add_supplier(data["name"], data["phone"], data["note"], data["debt_currency"])
                else:
                    debtor_id = db.add_debtor(
                        data["name"],
                        data["phone"],
                        data["note"],
                        data["debt_currency"],
                        user_id=data["user_id"],
                    )
                    if data["initial_debt"] > 0:
                        db.add_debtor_debt(
                            debtor_id,
                            data["initial_debt"],
                            f"{data['name']}ga boshlang'ich qarz berildi",
                        )
                self.load_data()
            except db.AppError as exc:
                QMessageBox.warning(
                    self,
                    t("Xatolik", self.property("app_language") or "uz"),
                    _translated_debt_error(exc, self.property("app_language") or "uz"),
                )

    def _edit_party(self, row, kind):
        party = self._party_at_row(row, kind)
        if not party:
            return
        dlg = PartyDialog(self, party, self._label_for(kind), kind=kind)
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
