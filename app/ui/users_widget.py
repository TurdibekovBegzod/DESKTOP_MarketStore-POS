from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget,
    QTableWidgetItem, QDialog, QFormLayout, QLineEdit, QComboBox,
    QMessageBox, QHeaderView, QMenu
)
from PyQt6.QtCore import Qt
import database as db
from ui.async_loader import AsyncDataLoader, make_progress_bar
from ui.i18n import set_language, t


class UserDialog(QDialog):
    def __init__(self, parent=None, user=None):
        super().__init__(parent)
        self.user = user
        self.language = parent.property("app_language") if parent else "uz"
        self.language = self.language or "uz"
        title = "Kassir qo'shish" if not user else "Foydalanuvchini tahrirlash"
        self.setWindowTitle(t(title, self.language))
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
        self.full_name_edit = QLineEdit(self.user.get("username", "") if self.user else "")
        self.full_name_edit.setPlaceholderText("Ism Familiya")
        form.addRow("To'liq ism *:", self.full_name_edit)

        self.role_combo = QComboBox()
        self.role_combo.addItem(t("Kassir", self.language), "cashier")
        self.role_combo.addItem(t("Admin", self.language), "admin")
        if self.user:
            idx = self.role_combo.findData(self.user["role"])
            if idx >= 0:
                self.role_combo.setCurrentIndex(idx)
        form.addRow("Rol:", self.role_combo)
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
        set_language(self, self.language)

    def _save(self):
        if not self.full_name_edit.text().strip():
            QMessageBox.warning(self, "Xatolik", "To'liq ismni kiriting!")
            return
        self.accept()

    def get_data(self):
        return {
            "full_name": self.full_name_edit.text().strip(),
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
        self.table.setHorizontalHeaderLabels(["To'liq ism", "Rol", "Yaratilgan", "Amallar"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(3, 80)
        self.table.verticalHeader().setDefaultSectionSize(54)
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
            username_item = QTableWidgetItem(user.get("username") or user.get("email") or "")
            username_item.setData(Qt.ItemDataRole.UserRole, dict(user))
            self.table.setItem(row, 0, username_item)
            self.table.setItem(row, 1, QTableWidgetItem("Admin" if user["role"] == "admin" else "Kassir"))
            self.table.setItem(row, 2, QTableWidgetItem(user["created_at"] or ""))
            self.table.setCellWidget(row, 3, self._actions_widget(row))
            self.table.setRowHeight(row, 54)
        set_language(self, self.property("app_language") or "uz")

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
            QMenu::item { min-width:140px;padding:8px 14px;border-radius:4px; }
            QMenu::item:selected { background:#eff6ff;color:#1d4ed8; }
            QMenu::item:disabled { color:#94a3b8; }
        """

    def _build_user_actions_menu(self, row, parent=None):
        language = self.property("app_language") or "uz"
        menu = QMenu(parent or self)
        menu.setStyleSheet(self._actions_menu_style())
        edit_label = t("Tahrir", language)
        delete_label = t("O'chir", language)
        edit_action = menu.addAction(f"✏️ {edit_label}")
        edit_action.triggered.connect(lambda _=False, r=row: self._edit_user(r))
        del_action = menu.addAction(f"🗑️ {delete_label}")
        del_action.triggered.connect(lambda _=False, r=row: self._delete_user(r))
        return menu

    def _show_user_actions_menu(self, row, button):
        menu = self._build_user_actions_menu(row, button)
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def _actions_widget(self, row):
        return self._menu_button_widget(
            lambda button, r=row: self._show_user_actions_menu(r, button)
        )

    def _add_user(self):
        dlg = UserDialog(self)
        if dlg.exec():
            data = dlg.get_data()
            try:
                db.add_user(
                    role=data["role"],
                    username=data["full_name"],
                )
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
                db.update_user(
                    user["id"],
                    role=data["role"],
                    username=data["full_name"],
                )
                self.load_data()
            except db.AppError as exc:
                QMessageBox.warning(self, "Saqlanmadi", str(exc))

    def _delete_user(self, row):
        item = self.table.item(row, 0)
        if not item:
            return
        user = item.data(Qt.ItemDataRole.UserRole)
        reply = QMessageBox.question(
            self, "O'chirish", f"'{user.get('username') or user.get('email')}' foydalanuvchisi o'chirilsinmi?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                db.delete_user(user["id"])
                self.load_data()
            except db.AppError as exc:
                QMessageBox.warning(self, "O'chirilmadi", str(exc))
