from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget,
    QTableWidgetItem, QDialog, QFormLayout, QLineEdit, QComboBox,
    QMessageBox, QHeaderView
)
from PyQt6.QtCore import Qt
import database as db
from ui.async_loader import AsyncDataLoader, make_progress_bar
from ui.i18n import set_language


class UserDialog(QDialog):
    def __init__(self, parent=None, user=None):
        super().__init__(parent)
        self.user = user
        self.setWindowTitle("Kassir qo'shish" if not user else "Foydalanuvchini tahrirlash")
        self.setFixedWidth(360)
        self._build_ui()

    def _build_ui(self):
        self.setStyleSheet("""
            QDialog { background:white; }
            QLabel { color:#374151;font-size:13px; }
            QLineEdit, QComboBox {
                border:1px solid #d1d5db;border-radius:6px;
                padding:7px 10px;font-size:13px;background:white;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(10)
        self.email_edit = QLineEdit((self.user.get("email") or self.user.get("username")) if self.user else "")
        form.addRow("Email *:", self.email_edit)

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("Yangi parol" if self.user else "Parol *")
        form.addRow("Parol:", self.password_edit)

        self.role_combo = QComboBox()
        self.role_combo.addItem("Kassir", "cashier")
        self.role_combo.addItem("Admin", "admin")
        if self.user:
            idx = self.role_combo.findData(self.user["role"])
            if idx >= 0:
                self.role_combo.setCurrentIndex(idx)
        form.addRow("Role:", self.role_combo)
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Bekor")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Saqlash")
        save_btn.setStyleSheet("background:#3b82f6;color:white;border:none;border-radius:6px;padding:8px 16px;font-weight:bold;")
        save_btn.clicked.connect(self._save)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def _save(self):
        if not self.email_edit.text().strip():
            QMessageBox.warning(self, "Xatolik", "Email kiriting!")
            return
        if not self.user and not self.password_edit.text():
            QMessageBox.warning(self, "Xatolik", "Parol kiriting!")
            return
        self.accept()

    def get_data(self):
        return {
            "email": self.email_edit.text().strip(),
            "password": self.password_edit.text(),
            "role": self.role_combo.currentData(),
        }


class UsersWidget(QWidget):
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
        toolbar.addStretch()
        add_btn = QPushButton("+ Kassir qo'shish")
        add_btn.setFixedHeight(38)
        add_btn.setStyleSheet("background:#3b82f6;color:white;border:none;border-radius:6px;padding:0 16px;font-weight:bold;font-size:13px;")
        add_btn.clicked.connect(self._add_user)
        toolbar.addWidget(add_btn)
        layout.addLayout(toolbar)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Email", "Role", "Yaratilgan", "Amallar"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(3, 154)
        self.table.verticalHeader().setDefaultSectionSize(78)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("QTableWidget{background:white;border:1px solid #e2e8f0;border-radius:8px;font-size:13px;} QTableWidget::item{padding:7px 10px;} QHeaderView::section{background:#f8fafc;border:none;border-bottom:1px solid #e2e8f0;padding:8px;font-weight:bold;color:#64748b;} QTableWidget::item:alternate{background:#f8fafc;}")
        layout.addWidget(self.table)

    def load_data(self):
        if self.isVisible():
            self._async_loader.start(db.get_users, self._apply_loaded_data)
            return
        self._apply_loaded_data(db.get_users())

    def _apply_loaded_data(self, users):
        self.table.setRowCount(0)
        for row, user in enumerate(users):
            self.table.insertRow(row)
            username_item = QTableWidgetItem(user.get("email") or user["username"])
            username_item.setData(Qt.ItemDataRole.UserRole, dict(user))
            self.table.setItem(row, 0, username_item)
            self.table.setItem(row, 1, QTableWidgetItem("Admin" if user["role"] == "admin" else "Kassir"))
            self.table.setItem(row, 2, QTableWidgetItem(user["created_at"] or ""))

            actions = QWidget()
            actions_layout = QVBoxLayout(actions)
            actions_layout.setContentsMargins(8, 6, 8, 6)
            actions_layout.setSpacing(6)

            edit_btn = QPushButton("Tahrir")
            edit_btn.setFixedHeight(28)
            edit_btn.setMinimumWidth(112)
            edit_btn.setStyleSheet("background:#fff7ed;color:#9a3412;border:1px solid #fdba74;border-radius:6px;font-weight:bold;")
            edit_btn.clicked.connect(lambda _, r=row: self._edit_user(r))
            del_btn = QPushButton("O'chir")
            del_btn.setFixedHeight(28)
            del_btn.setMinimumWidth(112)
            del_btn.setStyleSheet("background:#fef2f2;color:#b91c1c;border:1px solid #fca5a5;border-radius:6px;font-weight:bold;")
            del_btn.clicked.connect(lambda _, r=row: self._delete_user(r))
            actions_layout.addWidget(edit_btn)
            actions_layout.addWidget(del_btn)
            self.table.setCellWidget(row, 3, actions)
        set_language(self, self.property("app_language") or "uz")

    def _add_user(self):
        dlg = UserDialog(self)
        if dlg.exec():
            data = dlg.get_data()
            try:
                db.add_user(data["email"], data["password"], data["role"])
                self.load_data()
            except db.AppError as exc:
                QMessageBox.warning(self, "Saqlanmadi", str(exc))

    def _edit_user(self, row):
        item = self.table.item(row, 0)
        if not item:
            return
        user = item.data(Qt.ItemDataRole.UserRole)
        dlg = UserDialog(self, user)
        if dlg.exec():
            data = dlg.get_data()
            try:
                db.update_user(user["id"], data["email"], data["password"], data["role"])
                self.load_data()
            except db.AppError as exc:
                QMessageBox.warning(self, "Saqlanmadi", str(exc))

    def _delete_user(self, row):
        item = self.table.item(row, 0)
        if not item:
            return
        user = item.data(Qt.ItemDataRole.UserRole)
        reply = QMessageBox.question(
            self, "O'chirish", f"'{user.get('email') or user['username']}' foydalanuvchisi o'chirilsinmi?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                db.delete_user(user["id"])
                self.load_data()
            except db.AppError as exc:
                QMessageBox.warning(self, "O'chirilmadi", str(exc))
