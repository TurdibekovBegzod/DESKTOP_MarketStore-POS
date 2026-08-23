from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QDialog,
    QFormLayout, QMessageBox, QHeaderView, QMenu
)
from PyQt6.QtCore import Qt
import database as db
from ui.async_loader import AsyncDataLoader, make_progress_bar


class CustomersWidget(QWidget):
    def __init__(self):
        super().__init__()
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
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("🔍 Mijoz qidirish...")
        self.search_edit.setFixedHeight(38)
        self.search_edit.setStyleSheet("border:1px solid #d1d5db;border-radius:6px;padding:0 12px;font-size:13px;background:white;")
        self.search_edit.textChanged.connect(self.load_data)
        toolbar.addWidget(self.search_edit)
        toolbar.addStretch()
        add_btn = QPushButton("+ Mijoz qo'shish")
        add_btn.setFixedHeight(38)
        add_btn.setStyleSheet("background:#3b82f6;color:white;border:none;border-radius:6px;padding:0 16px;font-weight:bold;font-size:13px;")
        add_btn.clicked.connect(self._add_customer)
        toolbar.addWidget(add_btn)
        layout.addLayout(toolbar)
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["Ism","Telefon","Email","Balans","Jami xaridlar","Amallar"])
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(1, 150)
        self.table.setColumnWidth(2, 190)
        self.table.setColumnWidth(3, 130)
        self.table.setColumnWidth(4, 145)
        self.table.setColumnWidth(5, 80)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("QTableWidget{background:white;border:1px solid #e2e8f0;border-radius:8px;font-size:13px;} QTableWidget::item{padding:7px 10px;} QHeaderView::section{background:#f8fafc;border:none;border-bottom:1px solid #e2e8f0;padding:8px;font-weight:bold;color:#64748b;} QTableWidget::item:alternate{background:#f8fafc;}")
        self.table.verticalHeader().setDefaultSectionSize(54)
        layout.addWidget(self.table)

    def load_data(self):
        query = self.search_edit.text() if hasattr(self, 'search_edit') else ""
        if self.isVisible():
            self._async_loader.start(lambda: (db.get_all_customers(), query), self._apply_loaded_data)
            return
        self._apply_loaded_data((db.get_all_customers(), query))

    def _apply_loaded_data(self, data):
        customers, query = data
        if query:
            customers = [c for c in customers if query.lower() in c["name"].lower()
                         or (c["phone"] and query in c["phone"])]
        self.table.setRowCount(0)
        for row, c in enumerate(customers):
            self.table.insertRow(row)
            name_item = QTableWidgetItem(c["name"])
            name_item.setData(Qt.ItemDataRole.UserRole, dict(c))
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, QTableWidgetItem(c["phone"] or ""))
            self.table.setItem(row, 2, QTableWidgetItem(c["email"] or ""))
            bal_item = QTableWidgetItem(f"{c['balance']:,.0f} so'm")
            bal_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, 3, bal_item)
            purchases_item = QTableWidgetItem(f"{c['total_purchases']:,.0f} so'm")
            purchases_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, 4, purchases_item)
            self.table.setCellWidget(row, 5, self._actions_widget(row))
            self.table.setRowHeight(row, 54)

    def _menu_button_widget(self, on_click):
        widget = QWidget()
        widget.setStyleSheet("background:transparent;")
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        button = QPushButton("⋮")
        button.setFixedSize(34, 32)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip("Amallar")
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
            QMenu::item { min-width:140px;padding:8px 14px;border-radius:4px; }
            QMenu::item:selected { background:#eff6ff;color:#1d4ed8; }
            QMenu::item:disabled { color:#94a3b8; }
        """

    def _build_customer_actions_menu(self, row, parent=None):
        menu = QMenu(parent or self)
        menu.setStyleSheet(self._actions_menu_style())
        edit_action = menu.addAction("✏️ Tahrir")
        edit_action.triggered.connect(lambda _=False, r=row: self._edit_customer(r))
        return menu

    def _show_customer_actions_menu(self, row, button):
        menu = self._build_customer_actions_menu(row, button)
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def _actions_widget(self, row):
        return self._menu_button_widget(
            lambda button, r=row: self._show_customer_actions_menu(r, button)
        )

    def _add_customer(self):
        dlg = CustomerDialog(self)
        if dlg.exec():
            d = dlg.get_data()
            db.add_customer(d["name"], d["phone"], d["email"])
            self.load_data()

    def _edit_customer(self, row):
        item = self.table.item(row, 0)
        if not item:
            return
        c = item.data(Qt.ItemDataRole.UserRole)
        dlg = CustomerDialog(self, c)
        if dlg.exec():
            d = dlg.get_data()
            db.update_customer(c["id"], d["name"], d["phone"], d["email"])
            self.load_data()


class CustomerDialog(QDialog):
    def __init__(self, parent=None, customer=None):
        super().__init__(parent)
        self.setWindowTitle("Mijoz" + (" tahrirlash" if customer else " qo'shish"))
        self.setFixedWidth(360)
        self.setStyleSheet("QDialog{background:white;} QLabel{color:#374151;font-size:13px;}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        form = QFormLayout()
        form.setSpacing(10)
        inp = "border:1px solid #d1d5db;border-radius:6px;padding:7px 10px;font-size:13px;background:white;"
        self.name_edit = QLineEdit(customer["name"] if customer else "")
        self.name_edit.setStyleSheet(inp)
        form.addRow("Ism *:", self.name_edit)
        self.phone_edit = QLineEdit(customer["phone"] if customer and customer["phone"] else "")
        self.phone_edit.setPlaceholderText("+998 XX XXX XX XX")
        self.phone_edit.setStyleSheet(inp)
        form.addRow("Telefon:", self.phone_edit)
        self.email_edit = QLineEdit(customer["email"] if customer and customer["email"] else "")
        self.email_edit.setStyleSheet(inp)
        form.addRow("Email:", self.email_edit)
        layout.addLayout(form)
        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Bekor")
        cancel_btn.setStyleSheet("border:1px solid #d1d5db;border-radius:6px;padding:8px 16px;color:#6b7280;background:white;")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Saqlash")
        save_btn.setStyleSheet("background:#3b82f6;color:white;border:none;border-radius:6px;padding:8px 16px;font-weight:bold;")
        save_btn.clicked.connect(self._save)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def _save(self):
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Xatolik", "Ism kiriting!")
            return
        self.accept()

    def get_data(self):
        return {
            "name": self.name_edit.text().strip(),
            "phone": self.phone_edit.text().strip() or None,
            "email": self.email_edit.text().strip() or None,
        }
