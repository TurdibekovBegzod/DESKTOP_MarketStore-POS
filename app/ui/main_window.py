from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QPushButton, QLabel, QStackedWidget, QFrame, QMessageBox,
    QDialog, QFormLayout, QComboBox, QLineEdit, QApplication,
    QAbstractButton, QTableWidget, QHeaderView, QSpinBox, QDoubleSpinBox,
    QTextEdit, QDateEdit, QTabWidget, QScrollArea, QCalendarWidget,
    QFileDialog, QMenu, QWidgetAction
)
from PyQt6.QtCore import QPoint, QTimer, Qt, pyqtSignal, pyqtSlot, QEvent, QThread, QObject
from PyQt6.QtGui import QAction, QPixmap, QPainter, QIcon, QColor, QImage
from datetime import datetime, timedelta
from pathlib import Path
import sys
import api_client
import database as db
import sync_service

from ui.sales_widget import CurrencyDialog, SalesWidget
from ui.products_widget import ProductsWidget
from ui.finalize_sales_widget import FinalizeSalesWidget
from ui.stock_widget import StockWidget
from ui.reports_widget import ReportsWidget, SalesDetailsWidget
from ui.users_widget import UsersWidget
from ui.login_history_widget import LoginHistoryWidget
from ui.supplier_debts_widget import SupplierDebtsWidget
from ui.expenses_widget import ExpensesWidget
from ui.finance_widget import FinanceWidget
from ui.checking_widget import CheckingWidget
from ui.notifications_widget import NotificationsWidget
from ui.updater_dialog import UpdaterDialog
from ui.i18n import set_language


def resource_path(relative_path):
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return str(base_path / relative_path)


DESKTOP_ICON_PATH = resource_path("images/desktop.png")
APP_ICON_PATH = resource_path("images/desktop_icon.ico")
RIGHT_ARROW_LIST_PATH = resource_path("images/right-arrow_list.png")
DOWN_ARROW_PATH = resource_path("images/down_full.png")
if not Path(DOWN_ARROW_PATH).exists():
    DOWN_ARROW_PATH = resource_path("images/down_fill.png")
SYNC_ICON_PATH = resource_path("images/sync.png")


class NavGroupButton(QPushButton):
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.expanded = False
        self._right_icon = QPixmap(RIGHT_ARROW_LIST_PATH)
        self._down_icon = QPixmap(DOWN_ARROW_PATH)
        self._icon_normal_color = QColor("#94a3b8")
        self._icon_active_color = QColor("#ffffff")

    def setExpanded(self, expanded):
        self.expanded = expanded
        self.update()

    def setIconColors(self, normal_color, active_color):
        self._icon_normal_color = QColor(normal_color)
        self._icon_active_color = QColor(active_color)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        icon = self._down_icon if self.expanded else self._right_icon
        if not icon.isNull():
            size = 14
            x = self.rect().right() - size - 12
            y = self.rect().center().y() - size // 2
            tinted = icon.scaled(
                size,
                size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            color = self._icon_active_color if self.isChecked() else self._icon_normal_color
            painter.save()
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.drawPixmap(x, y, self._tinted_pixmap(tinted, color))
            painter.restore()

    def _tinted_pixmap(self, pixmap, color):
        image = pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
        for y in range(image.height()):
            for x in range(image.width()):
                pixel = image.pixelColor(x, y)
                alpha = pixel.alpha()
                if alpha <= 0:
                    continue
                if pixel.red() > 245 and pixel.green() > 245 and pixel.blue() > 245:
                    image.setPixelColor(x, y, QColor(0, 0, 0, 0))
                    continue
                icon_pixel = QColor(color)
                icon_pixel.setAlpha(alpha)
                image.setPixelColor(x, y, icon_pixel)
        return QPixmap.fromImage(image)


class LogoLabel(QLabel):
    doubleClicked = pyqtSignal()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.doubleClicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


THEMES = {
    "dark_blue": {
        "name": "Dark + Blue",
        "sidebar": "#1a1a2e",
        "sidebar_alt": "#16213e",
        "content": "#f8fafc",
        "topbar": "#ffffff",
        "border": "#2a2a4a",
        "accent": "#3b82f6",
        "nav_text": "#94a3b8",
        "nav_active": "#ffffff",
        "title": "#1e293b",
        "muted": "#64748b",
    },
    "light_blue": {
        "name": "Light + Blue",
        "sidebar": "#eaf2ff",
        "sidebar_alt": "#dbeafe",
        "content": "#f8fbff",
        "topbar": "#ffffff",
        "border": "#bfdbfe",
        "accent": "#2563eb",
        "nav_text": "#1e3a8a",
        "nav_active": "#ffffff",
        "title": "#172554",
        "muted": "#475569",
    },
    "dark_white": {
        "name": "Dark + White",
        "sidebar": "#111827",
        "sidebar_alt": "#1f2937",
        "content": "#f3f4f6",
        "topbar": "#ffffff",
        "border": "#374151",
        "accent": "#f9fafb",
        "nav_text": "#d1d5db",
        "nav_active": "#111827",
        "title": "#111827",
        "muted": "#6b7280",
    },
    "green": {
        "name": "Green + Slate",
        "sidebar": "#0f2f2a",
        "sidebar_alt": "#134e4a",
        "content": "#f0fdf4",
        "topbar": "#ffffff",
        "border": "#2dd4bf",
        "accent": "#10b981",
        "nav_text": "#a7f3d0",
        "nav_active": "#ffffff",
        "title": "#064e3b",
        "muted": "#475569",
    },
}


TEXTS = {
    "uz": {
        "settings": "Sozlamalar", "theme": "Interfeys rangi", "language": "Til",
        "app_name": "Dastur nomi", "currency": "Asosiy valyuta",
        "exchange_rates": "Kurslarni o'zgartirish", "save": "Saqlash", "cancel": "Bekor",
        "logout": "Chiqish", "logout_q": "Haqiqatan chiqmoqchimisiz?",
        "main_mode": "Asosiy", "cashier_mode": "Kassir",
        "admin_password_title": "Asosiy bo'lim",
        "admin_password_hint": "Admin bo'limlarni ochish uchun parolni kiriting.",
        "password": "Parol", "open": "Ochish",
        "email_missing": "Bu account uchun email topilmadi.",
        "password_required": "Parol kiriting.",
        "sync_clean": "Sinxron", "sync_dirty": "Yuborilmagan o'zgarish bor",
        "sync_push": "Yuborish", "sync_pull": "Olish",
        "sync_done": "Sync tugadi", "sync_error": "Sync xatosi",
        "sync_pending_count": "Yuborilmagan o'zgarishlar",
        "Admin": "Admin", "Kassir": "Kassir", "sales": "Sotuv",
        "products": "Mahsulotlar", "stock": "Ombor", "finalize_sales": "Sotishni yakunlash",
        "sales_details": "Sotuv tafsilotlari",
        "reports": "Hisobotlar", "finance": "Moliya", "supplier_debts": "Qarzlar", "expenses": "Harajatlar",
        "checking": "Tekshiruv",
        "users": "Kassirlar", "login_history": "Kirish tarixi",
        "notifications": "Bildirishnomalar", "check_updates": "Yangilanishlar",
    },
    "en": {
        "settings": "Settings", "theme": "Interface theme", "language": "Language",
        "app_name": "App name", "currency": "Default currency",
        "exchange_rates": "Edit exchange rates", "save": "Save", "cancel": "Cancel",
        "logout": "Logout", "logout_q": "Do you really want to log out?",
        "main_mode": "Main", "cashier_mode": "Cashier",
        "admin_password_title": "Main section",
        "admin_password_hint": "Enter password to open admin sections.",
        "password": "Password", "open": "Open",
        "email_missing": "Email was not found for this account.",
        "password_required": "Enter password.",
        "sync_clean": "Synced", "sync_dirty": "Unsynced changes",
        "sync_push": "Upload", "sync_pull": "Download",
        "sync_done": "Sync completed", "sync_error": "Sync error",
        "sync_pending_count": "Unsynced changes",
        "Admin": "Admin", "Kassir": "Cashier", "sales": "Sales",
        "products": "Products", "stock": "Stock", "finalize_sales": "Finalize sales",
        "sales_details": "Sales details",
        "reports": "Reports", "finance": "Finance", "supplier_debts": "Debts", "expenses": "Expenses",
        "checking": "Checking",
        "users": "Cashiers", "login_history": "Login history",
        "notifications": "Notifications", "check_updates": "Updates",
    },
    "ru": {
        "checking": "Проверка",
        "settings": "Настройки", "theme": "Тема интерфейса", "language": "Язык",
        "app_name": "Название программы", "currency": "Основная валюта",
        "exchange_rates": "Изменить курсы", "save": "Сохранить", "cancel": "Отмена",
        "logout": "Выход", "logout_q": "Вы действительно хотите выйти?",
        "Admin": "Админ", "Kassir": "Кассир", "sales": "Продажи",
        "products": "Товары", "stock": "Склад", "finalize_sales": "Завершение продаж",
        "sales_details": "Детали продаж",
        "reports": "Отчеты", "finance": "Финансы", "supplier_debts": "Долги", "expenses": "Расходы",
        "users": "Кассиры", "login_history": "История входа",
        "notifications": "Уведомления", "check_updates": "Обновления",
    },
}

TEXTS["ru"].update({
    "main_mode": "Основной",
    "cashier_mode": "Кассир",
    "admin_password_title": "Основной раздел",
    "admin_password_hint": "Введите пароль, чтобы открыть админ-разделы.",
    "password": "Пароль",
    "open": "Открыть",
    "email_missing": "Email для этого аккаунта не найден.",
    "password_required": "Введите пароль.",
    "sync_clean": "Синхронизировано",
    "sync_dirty": "Локальные изменения",
    "sync_push": "Отправить",
    "sync_pull": "Получить",
    "sync_done": "Синхронизация завершена",
    "sync_error": "Ошибка синхронизации",
})

TEXTS["ru"].update({
    "sync_pending_count": "Неотправленные изменения",
})


class SettingsDialog(QDialog):
    def __init__(self, parent=None, user_role="cashier", settings=None):
        super().__init__(parent)
        self.user_role = user_role
        self.settings = settings or db.get_app_settings()
        self.setProperty("app_language", self.settings.get("language", "uz"))
        self.setWindowTitle(TEXTS.get(self.settings["language"], TEXTS["uz"])["settings"])
        self.setFixedWidth(520)
        self._build_ui()

    def _build_ui(self):
        labels = TEXTS.get(self.settings["language"], TEXTS["uz"])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        form = QFormLayout()
        self.theme_combo = QComboBox()
        for key, theme in THEMES.items():
            self.theme_combo.addItem(theme["name"], key)
        idx = self.theme_combo.findData(self.settings.get("theme", "dark_blue"))
        if idx >= 0:
            self.theme_combo.setCurrentIndex(idx)
        self.language_combo = QComboBox()
        self.language_combo.addItem("O'zbek", "uz")
        self.language_combo.addItem("English", "en")
        self.language_combo.addItem("Русский", "ru")
        idx = self.language_combo.findData(self.settings.get("language", "uz"))
        if idx >= 0:
            self.language_combo.setCurrentIndex(idx)
        form.addRow(labels["theme"] + ":", self.theme_combo)
        form.addRow(labels["language"] + ":", self.language_combo)
        currency_row = QHBoxLayout()
        currency_row.setContentsMargins(0, 0, 0, 0)
        currency_row.setSpacing(8)
        self.currency_combo = QComboBox()
        self.currency_combo.setMinimumWidth(150)
        self.currency_manage_btn = QPushButton(labels["exchange_rates"])
        self.currency_manage_btn.setFixedHeight(32)
        self.currency_manage_btn.clicked.connect(self._manage_currencies)
        currency_row.addWidget(self.currency_combo, 1)
        currency_row.addWidget(self.currency_manage_btn)
        form.addRow(labels["currency"] + ":", currency_row)
        self._load_currency_combo(self.settings.get("currency", "UZS"))
        self.app_name_edit = None
        if self.user_role == "admin":
            self.app_name_edit = QLineEdit(self.settings.get("app_name", "Market POS"))
            form.addRow(labels["app_name"] + ":", self.app_name_edit)
        layout.addLayout(form)

        btns = QHBoxLayout()
        cancel_btn = QPushButton(labels["cancel"])
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton(labels["save"])
        save_btn.setStyleSheet("background:#3b82f6;color:white;border:none;border-radius:6px;padding:8px 16px;font-weight:bold;")
        save_btn.clicked.connect(self.accept)
        btns.addStretch()
        btns.addWidget(cancel_btn)
        btns.addWidget(save_btn)
        layout.addLayout(btns)

    def get_data(self):
        data = {
            "theme": self.theme_combo.currentData(),
            "language": self.language_combo.currentData(),
            "currency": self.currency_combo.currentData() or "UZS",
        }
        if self.app_name_edit is not None:
            data["app_name"] = self.app_name_edit.text().strip() or "Market POS"
        return data

    def _load_currency_combo(self, selected_code=None):
        selected_code = selected_code or self.currency_combo.currentData() or "UZS"
        self.currency_combo.clear()
        for currency in db.get_currencies():
            self.currency_combo.addItem(f"{currency['code']} - {currency['name']}", currency["code"])
        index = self.currency_combo.findData(selected_code)
        if index < 0:
            index = self.currency_combo.findData("UZS")
        if index >= 0:
            self.currency_combo.setCurrentIndex(index)

    def _manage_currencies(self):
        selected_code = self.currency_combo.currentData()
        dialog = CurrencyDialog(self)
        dialog.exec()
        self._load_currency_combo(selected_code)


class AdminPasswordDialog(QDialog):
    def __init__(self, parent=None, theme=None, labels=None):
        super().__init__(parent)
        self.theme = theme or THEMES["dark_blue"]
        self.labels = labels or TEXTS["uz"]
        self.setWindowTitle(self.labels.get("main_mode", "Asosiy"))
        self.setFixedSize(380, 245)
        self._build_ui()

    def _build_ui(self):
        theme = self.theme
        self.setStyleSheet(f"""
            QDialog {{
                background: {theme['topbar']};
                border-radius: 10px;
            }}
            QLabel#title {{
                color: {theme['title']};
                font-size: 18px;
                font-weight: bold;
            }}
            QLabel#hint {{
                color: {theme['muted']};
                font-size: 12px;
            }}
            QLabel#error {{
                color: #dc2626;
                font-size: 12px;
                background: transparent;
            }}
            QLineEdit {{
                background: {theme['content']};
                color: {theme['title']};
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                padding: 9px 12px;
                font-size: 14px;
            }}
            QLineEdit:focus {{
                border-color: {theme['accent']};
            }}
            QPushButton {{
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                padding: 8px 16px;
                font-size: 13px;
            }}
            QPushButton#primary {{
                background: {theme['accent']};
                color: {theme['nav_active']};
                border-color: {theme['accent']};
                font-weight: bold;
            }}
            QPushButton#secondary {{
                background: {theme['topbar']};
                color: {theme['title']};
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(10)
        title = QLabel(self.labels.get("admin_password_title", "Asosiy bo'lim"))
        title.setObjectName("title")
        hint = QLabel(self.labels.get("admin_password_hint", "Admin bo'limlarni ochish uchun parolni kiriting."))
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText(self.labels.get("password", "Parol"))
        self.password_edit.returnPressed.connect(self.accept)
        self.error_lbl = QLabel("")
        self.error_lbl.setObjectName("error")
        self.error_lbl.setWordWrap(True)
        self.error_lbl.setFixedHeight(34)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addWidget(self.password_edit)
        layout.addWidget(self.error_lbl)
        btns = QHBoxLayout()
        btns.addStretch()
        cancel_btn = QPushButton(self.labels.get("cancel", "Bekor"))
        cancel_btn.setObjectName("secondary")
        cancel_btn.clicked.connect(self.reject)
        ok_btn = QPushButton(self.labels.get("open", "Ochish"))
        ok_btn.setObjectName("primary")
        ok_btn.clicked.connect(self.accept)
        btns.addWidget(cancel_btn)
        btns.addWidget(ok_btn)
        layout.addLayout(btns)

    def password(self):
        return self.password_edit.text().strip()

    def set_error(self, text):
        self.error_lbl.setText(text)
        self.password_edit.selectAll()
        self.password_edit.setFocus()


class SyncWorker(QObject):
    finished = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, action, user):
        super().__init__()
        self.action = action
        self.user = user

    def run(self):
        try:
            if self.action == "push":
                res = sync_service.push_local_changes(self.user)
            elif self.action == "pull":
                res = sync_service.pull_server_changes(self.user)
            else:
                res = sync_service.synchronize_account_storage(self.user)
            self.finished.emit(res)
        except Exception as exc:
            self.failed.emit(str(exc))


class SyncDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.labels = getattr(parent, "labels", TEXTS["uz"])
        self.theme = THEMES.get(getattr(parent, "settings", {}).get("theme"), THEMES["dark_blue"])
        self.setWindowTitle("Sync")
        self.setFixedWidth(360)
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        theme = self.theme
        self.setStyleSheet(f"""
            QDialog {{
                background: {theme['topbar']};
            }}
            QLabel {{
                color: {theme['title']};
                font-size: 13px;
            }}
            QLabel#syncStatus {{
                font-size: 14px;
                font-weight: bold;
                padding: 10px 12px;
                border-radius: 8px;
            }}
            QPushButton {{
                background: {theme['topbar']};
                color: {theme['title']};
                border: 1px solid #cbd5e1;
                border-radius: 7px;
                padding: 8px 14px;
                font-size: 13px;
            }}
            QPushButton:hover {{
                border-color: {theme['accent']};
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        self.status_lbl = QLabel("")
        self.status_lbl.setObjectName("syncStatus")
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_lbl)
        self.info_lbl = QLabel("")
        self.info_lbl.setWordWrap(True)
        self.info_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.info_lbl)

        btn_row = QHBoxLayout()
        self.pull_btn = QPushButton(self.labels.get("sync_pull", "Olish"))
        self.pull_btn.clicked.connect(self._pull)
        self.push_btn = QPushButton(self.labels.get("sync_push", "Yuborish"))
        self.push_btn.clicked.connect(self._push)
        btn_row.addWidget(self.pull_btn)
        btn_row.addWidget(self.push_btn)
        layout.addLayout(btn_row)

    def refresh(self):
        if not self.parent_window:
            return
        status = db.get_sync_status()
        if not self.parent_window._sync_available():
            self.status_lbl.setText("Offline")
            self.status_lbl.setStyleSheet("background:#f1f5f9;color:#64748b;")
            self.pull_btn.setEnabled(False)
            self.push_btn.setEnabled(False)
            return
        pending = status["pending"]
        pending_count = int(status.get("pending_change_count") or 0)
        text = self.labels.get("sync_dirty", "Yuborilmagan o'zgarish bor") if pending else self.labels.get("sync_clean", "Sinxron")
        self.status_lbl.setText(text)
        pending_label = self.labels.get("sync_pending_count", "Yuborilmagan o'zgarishlar")
        self.info_lbl.setText(f"{pending_label}: {pending_count}")
        self.status_lbl.setStyleSheet(
            "background:#fef3c7;color:#92400e;" if pending else "background:#dcfce7;color:#166534;"
        )
        self.pull_btn.setEnabled(True)
        self.push_btn.setEnabled(True)

    def _pull(self):
        self.accept()
        if self.parent_window:
            self.parent_window._pull_from_server(show_message=True)

    def _push(self):
        self.accept()
        if self.parent_window:
            self.parent_window._push_to_server(show_message=True)


class ToastItem(QFrame):
    def __init__(self, message, title=None, level="success", duration_ms=4000, on_dismiss=None, parent=None):
        super().__init__(parent)
        self.on_dismiss = on_dismiss
        self.setObjectName("toastItem")
        self.setFixedWidth(340)

        if level == "success":
            border_color = "#10b981"
            icon_text = "✅"
            default_title = "Muvaffaqiyatli"
        elif level == "error":
            border_color = "#ef4444"
            icon_text = "⚠️"
            default_title = "Xatolik"
        elif level == "warning":
            border_color = "#f59e0b"
            icon_text = "⚠️"
            default_title = "Ogohlantirish"
        else:
            border_color = "#3b82f6"
            icon_text = "ℹ️"
            default_title = "Ma'lumot"

        self.setStyleSheet(f"""
            QFrame#toastItem {{
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-left: 5px solid {border_color};
                border-radius: 8px;
            }}
            QLabel {{
                border: none;
                background: transparent;
            }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        icon_lbl = QLabel(icon_text)
        icon_lbl.setStyleSheet("font-size: 18px; border: none; background: transparent;")
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(icon_lbl)

        content_lay = QVBoxLayout()
        content_lay.setSpacing(2)

        title_lbl = QLabel(title or default_title)
        title_lbl.setStyleSheet("font-weight: bold; font-size: 13px; color: #0f172a; border: none; background: transparent;")
        content_lay.addWidget(title_lbl)

        msg_lbl = QLabel(message)
        msg_lbl.setStyleSheet("font-size: 12px; color: #475569; border: none; background: transparent;")
        msg_lbl.setWordWrap(True)
        content_lay.addWidget(msg_lbl)

        layout.addLayout(content_lay, 1)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(20, 20)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet("""
            QPushButton {
                border: none; background: transparent; color: #94a3b8; font-size: 13px; font-weight: bold;
            }
            QPushButton:hover { color: #0f172a; }
        """)
        close_btn.clicked.connect(self.dismiss)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignTop)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        self._timer.start(duration_ms)

    def dismiss(self):
        self._timer.stop()
        if self.on_dismiss:
            self.on_dismiss(self)
        else:
            self.deleteLater()


class ToastManager(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("toastManager")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setStyleSheet("background: transparent; border: none;")
        self.setFixedWidth(340)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.hide()

    def show_toast(self, message, title=None, level="success", duration_ms=4000):
        item = ToastItem(
            message=message,
            title=title,
            level=level,
            duration_ms=duration_ms,
            on_dismiss=self._remove_item,
            parent=self,
        )
        self._layout.addWidget(item)
        item.show()
        self.adjustSize()
        self.reposition()
        self.show()
        self.raise_()

    def _remove_item(self, item):
        self._layout.removeWidget(item)
        item.deleteLater()
        self.adjustSize()
        self.reposition()
        if self._layout.count() == 0:
            self.hide()

    def reposition(self):
        if not self.parent():
            return
        parent_rect = self.parent().rect()
        x = parent_rect.width() - self.width() - 24
        y = 68
        self.move(max(10, x), y)
        self.raise_()


class MainWindow(QMainWindow):
    activity_signal = pyqtSignal(str, str, str, str, str, str)

    def __init__(self, user):
        super().__init__()
        self.user = user
        self.settings = db.get_app_settings(self.user["id"])
        self.labels = TEXTS.get(self.settings["language"], TEXTS["uz"])
        self._last_activity_saved_at = None
        self._logging_out = False
        self._activity_event_types = {
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.KeyPress,
            QEvent.Type.Wheel,
            QEvent.Type.TouchBegin,
        }
        self.setWindowTitle(self.settings["app_name"])
        self.setWindowIcon(QIcon(APP_ICON_PATH))
        self.setMinimumSize(1280, 780)
        self.activity_signal.connect(self._handle_activity_toast)
        db.register_activity_listener(self._on_database_activity)
        self._build_ui()
        self._start_clock()
        self._save_user_activity(force=True)
        app = QApplication.instance()
        if app:
            app.installEventFilter(self)

    def _on_database_activity(self, action, title, message, level, target, badge):
        self.activity_signal.emit(action, title, message, level, target, badge)

    def _handle_activity_toast(self, action, title, message, level, target, badge):
        self.show_toast(message, title=title, level=level, duration_ms=4000)
        self._refresh_notif_badge()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "toast_manager"):
            self.toast_manager.reposition()

    def show_toast(self, message, title=None, level="success", duration_ms=4000):
        if hasattr(self, "toast_manager"):
            self.toast_manager.show_toast(message, title=title, level=level, duration_ms=duration_ms)

    def eventFilter(self, obj, event):
        if event.type() in self._activity_event_types:
            self._save_user_activity()
        return super().eventFilter(obj, event)

    def closeEvent(self, event):
        if not self._logging_out:
            self._save_user_activity(force=True)
        db.unregister_activity_listener(self._on_database_activity)
        app = QApplication.instance()
        if app:
            app.removeEventFilter(self)
        super().closeEvent(event)

    def _save_user_activity(self, force=False):
        now = datetime.now()
        if not force and self._last_activity_saved_at and now - self._last_activity_saved_at < timedelta(seconds=30):
            return
        db.touch_user_activity(self.user.get("id"))
        self._last_activity_saved_at = now

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = QFrame()
        self.sidebar.setFixedWidth(220)
        self.sidebar.setObjectName("sidebar")
        sb_layout = QVBoxLayout(self.sidebar)
        sb_layout.setContentsMargins(0, 0, 0, 0)
        sb_layout.setSpacing(0)

        self.logo_frame = QFrame()
        self.logo_frame.setFixedHeight(70)
        logo_lay = QHBoxLayout(self.logo_frame)
        logo_lay.setContentsMargins(14, 0, 14, 0)
        logo_lay.setSpacing(10)
        self.logo_icon_lbl = LogoLabel()
        self.logo_icon_lbl.setFixedSize(42, 42)
        self.logo_icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logo_icon_lbl.setScaledContents(False)
        self.logo_icon_lbl.setToolTip("Logotip rasmini o'zgartirish uchun ikki marta bosing" if self.user["role"] == "admin" else "")
        self.logo_icon_lbl.doubleClicked.connect(self._change_logo_image)
        self._set_logo_icon()
        logo_lay.addWidget(self.logo_icon_lbl)
        self.logo_lbl = QLabel(self.settings["app_name"])
        self.logo_lbl.setWordWrap(True)
        logo_lay.addWidget(self.logo_lbl, 1)
        sb_layout.addWidget(self.logo_frame)

        self.nav_buttons = {}
        self.nav_group_buttons = {}
        self.nav_group_widgets = {}
        self.nav_group_items = {
            "reports_group": ("reports", "sales_details"),
        }
        if self.user["role"] == "admin":
            self.nav_group_items["products_group"] = ("products", "stock", "finalize_sales")
        nav_frame = QWidget()
        nav_frame.setStyleSheet("background: transparent;")
        nav_layout = QVBoxLayout(nav_frame)
        nav_layout.setContentsMargins(12, 16, 12, 0)
        nav_layout.setSpacing(4)

        # 1. Sales
        btn_sales = QPushButton(f"  {self.labels['sales']}")
        btn_sales.setFixedHeight(46)
        btn_sales.setCheckable(True)
        btn_sales.setObjectName("nav_sales")
        btn_sales.setStyleSheet(self._nav_btn_style())
        btn_sales.clicked.connect(lambda checked: self._switch_page("sales"))
        nav_layout.addWidget(btn_sales)
        self.nav_buttons["sales"] = btn_sales

        # 2. Products (Dropdown for admin, single button for cashier)
        if self.user["role"] == "admin":
            self._add_nav_group(
                nav_layout,
                "products_group",
                self.labels["products"],
                [
                    (self.labels["products"], "products"),
                    (self.labels["stock"], "stock"),
                    (self.labels.get("finalize_sales", "Sotishni yakunlash"), "finalize_sales"),
                ],
            )
        else:
            btn_products = QPushButton(f"  {self.labels['products']}")
            btn_products.setFixedHeight(46)
            btn_products.setCheckable(True)
            btn_products.setObjectName("nav_products")
            btn_products.setStyleSheet(self._nav_btn_style())
            btn_products.clicked.connect(lambda checked: self._switch_page("products"))
            nav_layout.addWidget(btn_products)
            self.nav_buttons["products"] = btn_products

        # 3. Reports (Dropdown: reports, sales_details) - below products for both cashier and admin
        self._add_nav_group(
            nav_layout,
            "reports_group",
            self.labels["reports"],
            [
                (self.labels["reports"], "reports"),
                (self.labels.get("sales_details", "Sotuv tafsilotlari"), "sales_details"),
            ],
        )

        # 4. Checking
        btn_checking = QPushButton(f"  {self.labels.get('checking', 'Checking')}")
        btn_checking.setFixedHeight(46)
        btn_checking.setCheckable(True)
        btn_checking.setObjectName("nav_checking")
        btn_checking.setStyleSheet(self._nav_btn_style())
        btn_checking.clicked.connect(lambda checked: self._switch_page("checking"))
        nav_layout.addWidget(btn_checking)
        self.nav_buttons["checking"] = btn_checking

        # 5. Admin-only pages
        if self.user["role"] == "admin":
            for label, key in [
                (self.labels.get("finance", "Finance"), "finance"),
                (self.labels["supplier_debts"], "supplier_debts"),
                (self.labels["expenses"], "expenses"),
                (self.labels["users"], "users"),
                (self.labels["login_history"], "login_history"),
            ]:
                btn = QPushButton(f"  {label}")
                btn.setFixedHeight(46)
                btn.setCheckable(True)
                btn.setObjectName(f"nav_{key}")
                btn.setStyleSheet(self._nav_btn_style())
                btn.clicked.connect(lambda checked, k=key: self._switch_page(k))
                nav_layout.addWidget(btn)
                self.nav_buttons[key] = btn

        nav_layout.addStretch()
        self.nav_scroll = QScrollArea()
        self.nav_scroll.setWidgetResizable(True)
        self.nav_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.nav_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.nav_scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        self.nav_scroll.setWidget(nav_frame)
        sb_layout.addWidget(self.nav_scroll, 1)

        self.user_frame = QFrame()
        self.user_frame.setFixedHeight(68)
        user_lay = QVBoxLayout(self.user_frame)
        user_lay.setContentsMargins(12, 8, 12, 10)
        user_lay.setSpacing(0)

        self.user_menu_btn = QPushButton()
        self.user_menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.user_menu_btn.setFixedHeight(48)
        self.user_menu_btn.clicked.connect(self._show_user_menu)
        user_top_row = QHBoxLayout(self.user_menu_btn)
        user_top_row.setContentsMargins(8, 6, 8, 6)
        user_top_row.setSpacing(9)
        self.user_avatar_lbl = QLabel(self._user_initials())
        self.user_avatar_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.user_avatar_lbl.setFixedSize(24, 24)
        self.uname = QLabel(self._user_display_name())
        self.uname.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.settings_btn = QPushButton("⚙")
        self.user_help_lbl = QLabel("?")
        self.user_help_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.user_help_lbl.setFixedSize(22, 22)
        user_top_row.addWidget(self.user_avatar_lbl)
        user_top_row.addWidget(self.uname)
        user_top_row.addStretch()
        user_top_row.addWidget(self.user_help_lbl)

        user_lay.addWidget(self.user_menu_btn)
        sb_layout.addWidget(self.user_frame)

        root.addWidget(self.sidebar)

        self.content_area = QWidget()
        self.content_area.setObjectName("content")
        content_layout = QVBoxLayout(self.content_area)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        self.topbar = QFrame()
        self.topbar.setFixedHeight(54)
        topbar_lay = QHBoxLayout(self.topbar)
        topbar_lay.setContentsMargins(24, 0, 24, 0)

        self.page_title_lbl = QLabel(self.labels["sales"])
        self.clock_lbl = QLabel()
        topbar_lay.addWidget(self.page_title_lbl)
        topbar_lay.addStretch()
        self.sync_wrap = QWidget()
        self.sync_wrap.setFixedSize(40, 36)
        self.sync_btn = QPushButton(self.sync_wrap)
        self.sync_btn.setObjectName("syncIconButton")
        self.sync_btn.setFixedSize(32, 32)
        self.sync_btn.move(0, 2)
        self.sync_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sync_btn.setToolTip("Sync")
        self.sync_btn.clicked.connect(self._open_sync_dialog)
        self.sync_badge_lbl = QLabel(self.sync_wrap)
        self.sync_badge_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sync_badge_lbl.setFixedSize(16, 16)
        self.sync_badge_lbl.move(24, 0)
        self.sync_badge_lbl.hide()
        sync_pixmap = QPixmap(SYNC_ICON_PATH)
        if not sync_pixmap.isNull():
            self.sync_btn.setIcon(QIcon(sync_pixmap))
            self.sync_btn.setIconSize(sync_pixmap.scaled(
                16,
                16,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ).size())
        topbar_lay.addWidget(self.sync_wrap)
        topbar_lay.addWidget(self.clock_lbl)
        content_layout.addWidget(self.topbar)

        self.stack = QStackedWidget()
        self.pages = {
            "sales": SalesWidget(self.user),
            "checking": CheckingWidget(self.user),
        }
        if self.user["role"] == "admin":
            self.pages.update({
                "products": ProductsWidget(self.user),
                "stock": StockWidget(),
                "finalize_sales": FinalizeSalesWidget(self.user),
                "reports": ReportsWidget(user=self.user),
                "sales_details": SalesDetailsWidget(user=self.user),
                "finance": FinanceWidget(),
                "supplier_debts": SupplierDebtsWidget(),
                "expenses": ExpensesWidget(self.user),
                "users": UsersWidget(),
                "login_history": LoginHistoryWidget(),
            })
        else:
            self.pages.update({
                "products": ProductsWidget(self.user, cashier_mode=True),
                "reports": ReportsWidget(user=self.user, cashier_only=True),
                "sales_details": SalesDetailsWidget(user=self.user, cashier_only=True),
            })
        for widget in self.pages.values():
            self.stack.addWidget(widget)
            set_language(widget, self.settings.get("language", "uz"))
        content_layout.addWidget(self.stack)
        root.addWidget(self.content_area)

        self._apply_theme()
        self.toast_manager = ToastManager(self)
        self._switch_page("sales")
        self._refresh_sync_status()
        self._refresh_notif_badge()
        self.sync_status_timer = QTimer(self)
        self.sync_status_timer.timeout.connect(self._refresh_sync_status)
        self.sync_status_timer.timeout.connect(self._refresh_notif_badge)
        self.sync_status_timer.start(1000)
        QTimer.singleShot(1200, self._auto_pull_from_server)
        QTimer.singleShot(1500, self._show_startup_notifications)

    def _nav_btn_style(self):
        theme = THEMES.get(self.settings.get("theme"), THEMES["dark_blue"])
        return """
            QPushButton {
                text-align: left;
                padding-left: 14px;
                padding-right: 34px;
                font-size: 14px;
                color: %s;
                background: transparent;
                border: none;
                border-radius: 8px;
            }
            QPushButton:hover {
                background: rgba(255,255,255,0.12);
                color: %s;
            }
            QPushButton:checked {
                background: %s;
                color: %s;
                font-weight: bold;
            }
        """ % (theme["nav_text"], theme["nav_text"], theme["accent"], theme["nav_active"])

    def _nav_child_btn_style(self):
        theme = THEMES.get(self.settings.get("theme"), THEMES["dark_blue"])
        return """
            QPushButton {
                text-align: left;
                padding-left: 18px;
                font-size: 13px;
                color: %s;
                background: transparent;
                border: none;
                border-radius: 7px;
            }
            QPushButton:hover {
                background: rgba(255,255,255,0.10);
                color: %s;
            }
            QPushButton:checked {
                background: %s;
                color: %s;
                font-weight: bold;
            }
        """ % (theme["nav_text"], theme["nav_text"], theme["accent"], theme["nav_active"])

    def _nav_group_style(self):
        theme = THEMES.get(self.settings.get("theme"), THEMES["dark_blue"])
        return """
            QPushButton {
                text-align: left;
                padding-left: 14px;
                font-size: 14px;
                color: %s;
                background: transparent;
                border: none;
                border-radius: 8px;
            }
            QPushButton:hover {
                background: rgba(255,255,255,0.12);
                color: %s;
            }
            QPushButton:checked {
                background: %s;
                color: %s;
                font-weight: bold;
            }
        """ % (theme["nav_text"], theme["nav_text"], theme["accent"], theme["nav_active"])

    def _add_nav_group(self, nav_layout, group_key, label, items):
        group_btn = NavGroupButton(f"  {label}")
        group_btn.setFixedHeight(40)
        group_btn.setCheckable(True)
        group_btn.setObjectName(f"nav_group_{group_key}")
        group_btn.setProperty("label_key", "products" if group_key == "products_group" else "reports")
        group_btn.setStyleSheet(self._nav_group_style())
        self._sync_nav_group_icon_colors(group_btn)
        group_btn.clicked.connect(lambda checked, k=group_key: self._toggle_nav_group(k))
        nav_layout.addWidget(group_btn)
        self.nav_group_buttons[group_key] = group_btn

        child_frame = QWidget()
        child_frame.setObjectName(f"nav_group_children_{group_key}")
        child_frame.setStyleSheet("background: transparent; border-left:1px solid rgba(148,163,184,0.35);")
        child_layout = QVBoxLayout(child_frame)
        child_layout.setContentsMargins(16, 2, 0, 4)
        child_layout.setSpacing(3)
        for child_label, child_key in items:
            btn = QPushButton(f"  {child_label}")
            btn.setFixedHeight(36)
            btn.setCheckable(True)
            btn.setObjectName(f"nav_{child_key}")
            btn.setStyleSheet(self._nav_child_btn_style())
            btn.clicked.connect(lambda checked, k=child_key: self._switch_page(k))
            child_layout.addWidget(btn)
            self.nav_buttons[child_key] = btn
        nav_layout.addWidget(child_frame)
        self.nav_group_widgets[group_key] = child_frame
        child_frame.setVisible(False)
        group_btn.setExpanded(False)
        group_btn.setChecked(False)

    def _toggle_nav_group(self, group_key):
        group = self.nav_group_widgets.get(group_key)
        button = self.nav_group_buttons.get(group_key)
        if not group or not button:
            return
        visible = not group.isVisible()
        group.setVisible(visible)
        label = self.labels.get(button.property("label_key") or "reports", "Hisobotlar")
        button.setText(f"  {label}")
        button.setExpanded(visible)
        button.setChecked(visible or any(self.nav_buttons[key].isChecked() for key in self.nav_group_items.get(group_key, ())))
        self._sync_nav_group_icon_colors(button)

    def _is_group_child(self, key):
        return any(key in items for items in self.nav_group_items.values())

    def _switch_page(self, key):
        if key not in self.pages:
            key = "sales"

        for k, btn in self.nav_buttons.items():
            btn.setChecked(k == key)
        for group_key, group_btn in self.nav_group_buttons.items():
            in_group = key in self.nav_group_items.get(group_key, ())
            group_btn.setChecked(in_group)
            if in_group:
                self.nav_group_widgets[group_key].setVisible(True)
                label = self.labels.get(group_btn.property("label_key") or "reports", "Hisobotlar")
                group_btn.setText(f"  {label}")
                group_btn.setExpanded(True)
            else:
                group_btn.setExpanded(self.nav_group_widgets[group_key].isVisible())
            self._sync_nav_group_icon_colors(group_btn)

        page = self.pages[key]
        self.stack.setCurrentWidget(page)
        self.page_title_lbl.setText(self.labels.get(key, key))

        if hasattr(page, "load_data"):
            page.load_data()
        set_language(page, self.settings.get("language", "uz"))
        self._apply_page_theme()
        self._refresh_notif_badge()

    def _refresh_notif_badge(self):
        if not hasattr(self, "notif_badge_lbl"):
            return
        try:
            unread_count = db.get_unread_notifications_count(user_id=self.user.get("id"))
            if unread_count > 0:
                self.notif_badge_lbl.setText(str(unread_count) if unread_count < 100 else "99+")
                self.notif_badge_lbl.show()
                self.notif_badge_lbl.raise_()
            else:
                self.notif_badge_lbl.hide()
        except Exception:
            pass

    def _sync_nav_group_icon_colors(self, button=None):
        theme = THEMES.get(self.settings.get("theme"), THEMES["dark_blue"])
        buttons = [button] if button is not None else self.nav_group_buttons.values()
        for group_button in buttons:
            if hasattr(group_button, "setIconColors"):
                group_button.setIconColors(theme["nav_text"], theme["nav_active"])

    def _start_clock(self):
        timer = QTimer(self)
        timer.timeout.connect(self._update_clock)
        timer.start(1000)
        self._update_clock()

    def _update_clock(self):
        now = datetime.now().strftime("%d.%m.%Y  %H:%M:%S")
        self.clock_lbl.setText(now)

    def _sync_available(self):
        return bool(self.user.get("api_access_token") or db.get_user_api_token(self.user.get("id")))

    def _refresh_sync_status(self):
        if not hasattr(self, "sync_btn"):
            return
        status = db.get_sync_status()
        if not self._sync_available():
            self.sync_btn.setToolTip("Offline")
            self.sync_btn.setEnabled(False)
            self._apply_sync_card_state("offline")
            self._update_sync_badge(0)
            return
        state = "dirty" if status["pending"] else "clean"
        text = self.labels.get("sync_dirty", "Yuborilmagan o'zgarish bor") if status["pending"] else self.labels.get("sync_clean", "Sinxron")
        self.sync_btn.setToolTip(text)
        self.sync_btn.setEnabled(True)
        self._apply_sync_card_state(state)
        self._update_sync_badge(status.get("pending_change_count") or 0)

    def _update_sync_badge(self, count):
        if not hasattr(self, "sync_badge_lbl"):
            return
        try:
            count = int(count)
        except (TypeError, ValueError):
            count = 0
        if count <= 0:
            self.sync_badge_lbl.hide()
            return
        text = "99+" if count > 99 else str(count)
        width = 22 if count > 99 else 16
        self.sync_badge_lbl.setFixedSize(width, 16)
        self.sync_badge_lbl.move(40 - width, 0)
        self.sync_badge_lbl.setText(text)
        self.sync_badge_lbl.setStyleSheet("""
            background: #ef4444;
            color: white;
            border: 1px solid white;
            border-radius: 8px;
            font-size: 9px;
            font-weight: bold;
        """)
        self.sync_badge_lbl.show()
        self.sync_badge_lbl.raise_()

    def _apply_sync_card_state(self, state):
        palette = {
            "dirty": {"bg": "#fef3c7", "border": "#f59e0b", "text": "#92400e"},
            "clean": {"bg": "#dcfce7", "border": "#22c55e", "text": "#166534"},
            "offline": {"bg": "#f1f5f9", "border": "#cbd5e1", "text": "#64748b"},
        }.get(state, {"bg": "#f8fafc", "border": "#cbd5e1", "text": "#334155"})
        self.sync_btn.setStyleSheet(f"""
            QPushButton#syncIconButton {{
                background: {palette['bg']};
                border: 1px solid {palette['border']};
                border-radius: 8px;
            }}
            QPushButton#syncIconButton:hover {{
                border: 2px solid {palette['border']};
            }}
            QPushButton#syncIconButton:disabled {{
                background: #f1f5f9;
                border-color: #cbd5e1;
            }}
        """)

    def _open_sync_dialog(self):
        dlg = SyncDialog(self)
        dlg.exec()
        self._refresh_sync_status()

    def _reload_current_page(self):
        current_page = self.stack.currentWidget()
        if hasattr(current_page, "load_data"):
            current_page.load_data()

    def _auto_pull_from_server(self):
        if not self._sync_available():
            self._refresh_sync_status()
            return
        if db.get_sync_status()["pending"]:
            self._refresh_sync_status()
            return
        try:
            result = sync_service.pull_server_changes(self.user)
            if result.get("imported"):
                self._reload_current_page()
        except Exception:
            pass
        self._refresh_sync_status()

    def _show_startup_notifications(self):
        try:
            user_name = self.user.get("username") or self.user.get("email") or "Foydalanuvchi"
            self.show_toast(
                f"Tizimga xush kelibsiz, {user_name}! Barcha xizmatlar faol va yangilangan.",
                title="Tizim faol",
                level="success",
                duration_ms=4000,
            )
            data = db.get_notifications_data(threshold=5)
            summary = data.get("summary", {})
            low_stock = summary.get("low_stock_count", 0)
            if low_stock > 0:
                self.show_toast(
                    f"{low_stock} ta mahsulot omborda kam qolgan yoki tugagan.",
                    title="Ombor ogohlantirishi",
                    level="warning",
                    duration_ms=4000,
                )
            debtors_count = summary.get("debtors_count", 0)
            if debtors_count > 0:
                self.show_toast(
                    f"{debtors_count} ta mijozda to'lanmagan qarzdorlik mavjud.",
                    title="Qarzdorliklar",
                    level="warning",
                    duration_ms=4000,
                )
        except Exception:
            pass

    def _pull_from_server(self, show_message=False):
        if not self._sync_available():
            self._refresh_sync_status()
            return
        if hasattr(self, "_sync_thread") and self._sync_thread and self._sync_thread.isRunning():
            if show_message:
                self.show_toast("Sinxronizatsiya allaqachon bajarilmoqda...", title="Sync", level="info")
            return
        if show_message:
            self.show_toast("Serverdan ma'lumotlar olinmoqda...", title="Qabul qilinmoqda", level="info", duration_ms=2500)

        self._sync_thread = QThread(self)
        self._sync_worker = SyncWorker("pull", self.user)
        self._sync_worker.moveToThread(self._sync_thread)
        self._sync_thread.started.connect(self._sync_worker.run)
        self._sync_worker.finished.connect(lambda res: self._on_pull_success(res, show_message))
        self._sync_worker.failed.connect(self._on_sync_failed)
        self._sync_thread.start()

    def _on_pull_success(self, result, show_message):
        self._cleanup_sync_thread()
        self._reload_current_page()
        self._refresh_sync_status()
        if show_message:
            imported = result.get("imported", 0)
            self.show_toast(f"Ma'lumotlar serverdan muvaffaqiyatli qabul qilindi ({imported} ta yangilandi)", title="Sync tugadi", level="success")

    def _push_to_server(self, show_message=False):
        if not self._sync_available():
            self._refresh_sync_status()
            return
        if hasattr(self, "_sync_thread") and self._sync_thread and self._sync_thread.isRunning():
            if show_message:
                self.show_toast("Sinxronizatsiya allaqachon bajarilmoqda...", title="Sync", level="info")
            return
        if show_message:
            self.show_toast("Ma'lumotlar serverga yuborilmoqda...", title="Yuborilmoqda", level="info", duration_ms=2500)

        self._sync_thread = QThread(self)
        self._sync_worker = SyncWorker("push", self.user)
        self._sync_worker.moveToThread(self._sync_thread)
        self._sync_thread.started.connect(self._sync_worker.run)
        self._sync_worker.finished.connect(lambda res: self._on_push_success(res, show_message))
        self._sync_worker.failed.connect(self._on_sync_failed)
        self._sync_thread.start()

    def _on_push_success(self, result, show_message):
        self._cleanup_sync_thread()
        self._refresh_sync_status()
        if show_message:
            saved = result.get("saved", 0)
            self.show_toast(f"Ma'lumotlar serverga muvaffaqiyatli yuborildi ({saved} ta saqlandi)", title="Sync tugadi", level="success")

    def _on_sync_failed(self, error_message):
        self._cleanup_sync_thread()
        self._refresh_sync_status()
        self.show_toast(error_message, title="Sync xatosi", level="error")

    def _cleanup_sync_thread(self):
        if hasattr(self, "_sync_thread") and self._sync_thread:
            self._sync_thread.quit()
            self._sync_thread.wait(3000)
            self._sync_thread = None
            self._sync_worker = None

    def _set_logo_icon(self):
        pixmap = QPixmap(DESKTOP_ICON_PATH)
        if not pixmap.isNull():
            self.logo_icon_lbl.setPixmap(
                pixmap.scaled(
                    34,
                    34,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def _change_logo_image(self):
        if self.user["role"] != "admin":
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Logotip rasmini tanlash",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)",
        )
        if not path:
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            QMessageBox.warning(self, "Rasm ochilmadi", "Tanlangan rasmni o'qib bo'lmadi.")
            return
        target = Path(DESKTOP_ICON_PATH)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not pixmap.save(str(target), "PNG"):
            QMessageBox.warning(self, "Saqlanmadi", "Logotip rasmini saqlab bo'lmadi.")
            return
        self._set_logo_icon()

    def _user_display_name(self):
        name = (self.user.get("username") or self.user.get("email") or "User").strip()
        if "@" in name:
            name = name.split("@", 1)[0]
        return " ".join(part.capitalize() for part in name.replace(".", " ").replace("_", " ").split()) or "User"

    def _user_initials(self):
        name = self._user_display_name()
        parts = [part for part in name.split() if part]
        if len(parts) >= 2:
            return (parts[0][0] + parts[1][0]).upper()
        return name[:2].upper()

    def _show_user_menu(self):
        theme = THEMES.get(self.settings.get("theme"), THEMES["dark_blue"])
        menu_width = max(self.user_menu_btn.width(), 180)
        action_width = max(menu_width - 16, 164)
        menu = QMenu(self)
        menu.setFixedWidth(menu_width)
        menu.setStyleSheet(f"""
            QMenu {{
                background: {theme['topbar']};
                color: {theme['title']};
                border: 1px solid #e5e7eb;
                border-radius: 10px;
                padding: 8px;
                font-size: 13px;
            }}
            QMenu::item {{
                min-width: {action_width}px;
                padding: 8px 22px 8px 10px;
                border-radius: 6px;
            }}
            QMenu::item:selected {{
                background: #f1f5f9;
            }}
            QMenu::separator {{
                height: 1px;
                background: #e5e7eb;
                margin: 6px 2px;
            }}
        """)
        profile_widget = QWidget(menu)
        profile_widget.setFixedWidth(action_width)
        profile_layout = QHBoxLayout(profile_widget)
        profile_layout.setContentsMargins(8, 6, 8, 8)
        profile_layout.setSpacing(9)
        profile_avatar = QLabel(self._user_initials())
        profile_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        profile_avatar.setFixedSize(24, 24)
        profile_avatar.setStyleSheet("""
            background: #12a383;
            color: white;
            border-radius: 12px;
            font-size: 9px;
            font-weight: bold;
        """)
        profile_name = QLabel(self._user_display_name())
        profile_name.setStyleSheet(f"background: transparent; color: {theme['title']}; font-size: 13px;")
        profile_layout.addWidget(profile_avatar)
        profile_layout.addWidget(profile_name, 1)
        profile_action = QWidgetAction(menu)
        profile_action.setDefaultWidget(profile_widget)
        menu.addAction(profile_action)
        menu.addSeparator()
        if self.user.get("role") == "admin":
            mode_text = self.labels.get("cashier_mode", self.labels.get("Kassir", "Kassir"))
            mode_callback = self._switch_to_cashier_mode
        else:
            mode_text = self.labels.get("main_mode", "Asosiy")
            mode_callback = self._unlock_main_area
        menu.addAction(self._menu_button_action(menu, mode_text, mode_callback, theme, width=action_width))
        menu.addAction(self._menu_button_action(menu, self.labels["settings"], self._open_settings, theme, width=action_width))
        menu.addAction(self._menu_button_action(menu, "🔄 " + self.labels.get("check_updates", "Yangilanishlar"), self._open_updater, theme, width=action_width))
        menu.addAction(self._menu_button_action(menu, self.labels["logout"], self._logout, theme, danger=True, width=action_width))
        size = menu.sizeHint()
        pos = self.user_menu_btn.mapToGlobal(self.user_menu_btn.rect().topLeft())
        menu.popup(pos - QPoint(0, size.height() + 8))

    def _open_updater(self):
        dlg = UpdaterDialog(self)
        dlg.exec()

    def _menu_button_action(self, menu, text, callback, theme, danger=False, width=None):
        action = QWidgetAction(menu)
        btn = QPushButton(text)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedHeight(34)
        if width:
            btn.setFixedWidth(width)
        color = "#dc2626" if danger else theme["title"]
        hover_bg = "#fee2e2" if danger else "#f1f5f9"
        hover_color = "#b91c1c" if danger else theme["title"]
        btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {color};
                border: none;
                border-radius: 6px;
                padding: 7px 10px;
                text-align: left;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background: {hover_bg};
                color: {hover_color};
            }}
        """)
        btn.clicked.connect(menu.close)
        btn.clicked.connect(callback)
        action.setDefaultWidget(btn)
        return action

    def _unlock_main_area(self):
        email = (self.user.get("email") or "").strip()
        if not email:
            QMessageBox.warning(
                self,
                self.labels.get("main_mode", "Asosiy"),
                self.labels.get("email_missing", "Bu account uchun email topilmadi."),
            )
            return
        dlg = AdminPasswordDialog(self, THEMES.get(self.settings.get("theme"), THEMES["dark_blue"]), self.labels)
        while True:
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            password = dlg.password()
            if not password:
                dlg.set_error(self.labels.get("password_required", "Parol kiriting."))
                continue
            try:
                api_client.login(email, password)
                break
            except api_client.ApiClientError as exc:
                dlg.set_error(str(exc))
        self.user["role"] = "admin"
        self.next_window = MainWindow(dict(self.user))
        self.next_window.showMaximized()
        self._logging_out = True
        self.close()

    def _switch_to_cashier_mode(self):
        self.user["role"] = "cashier"
        self.next_window = MainWindow(dict(self.user))
        self.next_window.showMaximized()
        self._logging_out = True
        self.close()

    def _open_settings(self):
        dlg = SettingsDialog(self, self.user["role"], self.settings)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            updated = dlg.get_data()
            if self.user["role"] != "admin":
                updated.pop("app_name", None)
            db.save_app_settings(updated, self.user["id"])
            self.settings = db.get_app_settings(self.user["id"])
            self.labels = TEXTS.get(self.settings["language"], TEXTS["uz"])
            self._refresh_texts()
            self._apply_theme()

    def _refresh_texts(self):
        self.setWindowTitle(self.settings["app_name"])
        self.logo_lbl.setText(self.settings["app_name"])
        self.uname.setText(self._user_display_name())
        self.user_avatar_lbl.setText(self._user_initials())
        self.logo_icon_lbl.setToolTip("Logotip rasmini o'zgartirish uchun ikki marta bosing" if self.user["role"] == "admin" else "")
        if hasattr(self, "sync_btn"):
            self._refresh_sync_status()
        for key, btn in self.nav_buttons.items():
            btn.setText(f"  {self.labels.get(key, key)}")
            btn.setStyleSheet(self._nav_child_btn_style() if self._is_group_child(key) else self._nav_btn_style())
        for group_key, btn in self.nav_group_buttons.items():
            label = self.labels.get(btn.property("label_key") or "reports", "Hisobotlar")
            visible = self.nav_group_widgets.get(group_key) and self.nav_group_widgets[group_key].isVisible()
            btn.setText(f"  {label}")
            btn.setExpanded(visible)
            btn.setStyleSheet(self._nav_group_style())
        current_page = self.stack.currentWidget()
        current_key = next((key for key, page in self.pages.items() if page is current_page), "sales")
        self.page_title_lbl.setText(self.labels.get(current_key, current_key))
        for page in self.pages.values():
            set_language(page, self.settings.get("language", "uz"))
        if hasattr(current_page, "load_data"):
            current_page.load_data()

    def _apply_theme(self):
        theme = THEMES.get(self.settings.get("theme"), THEMES["dark_blue"])
        self.sidebar.setStyleSheet(f"""
            #sidebar {{
                background: {theme['sidebar']};
                border-right: 1px solid {theme['border']};
            }}
        """)
        self.logo_frame.setStyleSheet(f"background:{theme['sidebar_alt']};border-bottom:1px solid {theme['border']};")
        self.logo_icon_lbl.setStyleSheet(
            f"background:{theme['topbar']};border:1px solid {theme['border']};border-radius:8px;"
        )
        self.logo_lbl.setStyleSheet(f"color:{theme['nav_text']};font-size:17px;font-weight:bold;")
        if hasattr(self, "nav_scroll"):
            self.nav_scroll.setStyleSheet(f"""
                QScrollArea {{
                    background: {theme['sidebar']};
                    border: none;
                }}
                QScrollBar:vertical {{
                    background: transparent;
                    width: 8px;
                    margin: 4px 0 4px 0;
                }}
                QScrollBar::handle:vertical {{
                    background: {theme['border']};
                    border-radius: 4px;
                    min-height: 24px;
                }}
                QScrollBar::add-line:vertical,
                QScrollBar::sub-line:vertical {{
                    height: 0;
                    border: none;
                    background: transparent;
                }}
                QScrollBar::add-page:vertical,
                QScrollBar::sub-page:vertical {{
                    background: transparent;
                }}
            """)
        self.user_frame.setStyleSheet(f"background:{theme['sidebar_alt']};border-top:1px solid {theme['border']};")
        self.user_menu_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: none;
                border-radius: 8px;
                text-align: left;
            }}
            QPushButton:hover {{
                background: rgba(255,255,255,0.10);
            }}
        """)
        self.user_avatar_lbl.setStyleSheet("""
            background: #12a383;
            color: white;
            border-radius: 12px;
            font-size: 9px;
            font-weight: bold;
        """)
        self.uname.setStyleSheet(f"background: transparent; border: none; color:{theme['nav_text']};font-size:13px;padding:0;")
        self.user_help_lbl.setStyleSheet(f"""
            color: {theme['nav_text']};
            border: 1px solid {theme['border']};
            border-radius: 11px;
            font-size: 12px;
            font-weight: bold;
        """)
        self.content_area.setStyleSheet(f"#content {{ background: {theme['content']}; }}")
        self.topbar.setStyleSheet(f"background:{theme['topbar']};border-bottom:1px solid #e2e8f0;")
        self.page_title_lbl.setStyleSheet(f"font-size:18px;font-weight:bold;color:{theme['title']};")
        self.clock_lbl.setStyleSheet(f"color:{theme['muted']};font-size:13px;")
        for key, btn in self.nav_buttons.items():
            btn.setStyleSheet(self._nav_child_btn_style() if self._is_group_child(key) else self._nav_btn_style())
        for group_key, btn in self.nav_group_buttons.items():
            btn.setStyleSheet(self._nav_group_style())
            self._sync_nav_group_icon_colors(btn)
            if group_key in self.nav_group_widgets:
                self.nav_group_widgets[group_key].setStyleSheet(
                    "background: transparent; border-left:1px solid rgba(148,163,184,0.35);"
                )
        app = QApplication.instance()
        if app:
            app.setStyleSheet(self._global_theme_style(theme))
        self._refresh_sync_status()
        self._apply_page_theme()

    def _apply_page_theme(self):
        theme = THEMES.get(self.settings.get("theme"), THEMES["dark_blue"])
        current_page = self.stack.currentWidget()
        if hasattr(current_page, "apply_theme"):
            current_page.apply_theme(theme)
            return
        if current_page and isinstance(current_page, SalesWidget):
            self._apply_sales_theme(current_page, theme)
            return

        target_container = current_page if current_page else self.stack
        for widget in target_container.findChildren(QWidget):
            if self._inside_custom_card(widget):
                continue
            if self._inside_calendar(widget):
                continue
            if isinstance(widget, QAbstractButton) and self._inside_table(widget):
                continue
            if isinstance(widget, QDateEdit):
                widget.setStyleSheet(self._date_field_style(theme))
            elif isinstance(widget, (QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit)):
                widget.setStyleSheet(self._field_style(theme))
            elif isinstance(widget, QTableWidget):
                widget.setStyleSheet(self._table_style(theme))
                widget.horizontalHeader().setStyleSheet(self._header_style(theme))
            elif isinstance(widget, QHeaderView):
                widget.setStyleSheet(self._header_style(theme))
            elif isinstance(widget, QTabWidget):
                widget.setStyleSheet(self._tab_style(theme))
            elif isinstance(widget, QAbstractButton):
                if widget.objectName().startswith("danger_clear"):
                    widget.setStyleSheet(self._danger_button_style())
                    continue
                widget.setStyleSheet(self._button_style(theme))
            elif isinstance(widget, QFrame):
                widget.setStyleSheet(self._panel_style(theme))
            elif isinstance(widget, QScrollArea):
                widget.setStyleSheet(f"QScrollArea {{ background: {theme['content']}; border: none; }}")
            elif isinstance(widget, QCalendarWidget):
                widget.setMinimumSize(400, 300)
                widget.setStyleSheet(self._calendar_style(theme))
            elif isinstance(widget, QLabel):
                if widget.objectName() in {"productsSectionTitle", "summaryCardTitle", "summaryCardValue"}:
                    continue
                widget.setStyleSheet(self._label_style(theme))

    def _inside_table(self, widget):
        parent = widget.parent()
        while parent is not None:
            if isinstance(parent, QTableWidget):
                return True
            parent = parent.parent()
        return False

    def _inside_calendar(self, widget):
        parent = widget.parent()
        while parent is not None:
            if isinstance(parent, QCalendarWidget):
                return True
            parent = parent.parent()
        return False

    def _inside_custom_card(self, widget):
        parent = widget
        while parent is not None:
            if parent.objectName() in {"sectionCard", "sectionMetrics", "trashCard", "summaryCard", "notif_card"}:
                return True
            parent = parent.parent()
        return False

    def _inside_products_card(self, widget):
        return self._inside_custom_card(widget)

    def _calendar_style(self, theme):
        return f"""
            QCalendarWidget {{ background:{theme['topbar']}; color:{theme['title']}; }}
            QCalendarWidget QWidget#qt_calendar_navigationbar {{
                background:{theme['accent']};
                min-height:42px;
            }}
            QCalendarWidget QToolButton {{
                background:transparent;
                color:white;
                border:none;
                border-radius:4px;
                min-height:32px;
                padding:2px 8px;
                font-size:14px;
                font-weight:bold;
            }}
            QCalendarWidget QToolButton#qt_calendar_monthbutton {{ min-width:100px; }}
            QCalendarWidget QToolButton#qt_calendar_yearbutton {{ min-width:72px; }}
            QCalendarWidget QToolButton#qt_calendar_prevmonth,
            QCalendarWidget QToolButton#qt_calendar_nextmonth {{ min-width:32px; max-width:32px; }}
            QCalendarWidget QToolButton:hover {{ background:rgba(255,255,255,0.18); }}
            QCalendarWidget QSpinBox {{
                background:white;
                color:#111827;
                border:1px solid #cbd5e1;
                border-radius:4px;
                padding:2px 6px;
                min-width:76px;
                font-size:14px;
            }}
            QCalendarWidget QAbstractItemView {{
                background:{theme['topbar']};
                color:{theme['title']};
                selection-background-color:{theme['accent']};
                selection-color:white;
                outline:none;
            }}
        """

    def _apply_sales_theme(self, page, theme):
        page.setStyleSheet(f"background:{theme['content']};")
        for table in page.findChildren(QTableWidget):
            table.setStyleSheet(self._table_style(theme))
            table.horizontalHeader().setStyleSheet(self._header_style(theme))
        for field in page.findChildren((QLineEdit, QComboBox)):
            field.setStyleSheet(self._field_style(theme))
        if hasattr(page, "discount_edit"):
            page.discount_edit.setStyleSheet(self._field_style(theme))
            page.discount_edit.setMinimumHeight(40)
            page.discount_edit.setMinimumWidth(120)
        if hasattr(page, "discount_currency_combo"):
            page.discount_currency_combo.setStyleSheet(self._field_style(theme))
            page.discount_currency_combo.setMinimumHeight(40)
            page.discount_currency_combo.setMinimumWidth(86)
        cart_title = page.findChild(QLabel, "salesCartTitle")
        if cart_title:
            cart_title.setStyleSheet(f"""
                QLabel#salesCartTitle {{
                    color:{theme['title']};
                    background:transparent;
                    border:none;
                    font-size:15px;
                    font-weight:bold;
                    padding:0;
                }}
            """)
        complete_btn = page.findChild(QPushButton, "complete_sale_btn")
        if complete_btn:
            complete_btn.setFixedHeight(48)
            complete_btn.setStyleSheet("""
                QPushButton {
                    background:#059669; color:white; font-size:15px;
                    font-weight:bold; border:none; border-radius:8px;
                }
                QPushButton:hover { background:#047857; }
                QPushButton:pressed { background:#065f46; padding-top:3px; }
            """)
        clear_btn = page.findChild(QPushButton, "clear_cart_btn")
        if clear_btn:
            clear_btn.setFixedHeight(36)
            clear_btn.setStyleSheet("""
                QPushButton {
                    background:transparent; color:#ef4444; font-size:13px;
                    border:1px solid #ef4444; border-radius:6px;
                }
                QPushButton:hover { background:#ef4444; color:white; }
                QPushButton:pressed { background:#991b1b; color:white; padding-top:3px; }
            """)
        for button in page.products_table.findChildren(QPushButton):
            if button.toolTip() == "Savatga qo'shish" or button.text().strip() == "+":
                button.setFixedSize(25, 25)
                button.setStyleSheet(f"""
                    QPushButton {{
                        background:{theme['accent']}; color:{theme['nav_active']};
                        border:none; border-radius:6px; padding:0;
                        font-size:15px; font-weight:bold;
                    }}
                    QPushButton:hover {{ background:{theme['sidebar_alt']}; color:{theme['nav_text']}; }}
                    QPushButton:pressed {{
                        background:#1d4ed8;
                        color:white;
                        padding-top:2px;
                    }}
                    QPushButton:disabled {{ background:#cbd5e1; color:#64748b; }}
                """)
        for spinner in page.cart_table.findChildren(QSpinBox):
            spinner.setMinimumHeight(34)
            spinner.setStyleSheet(f"""
                QSpinBox {{
                    background:{theme['topbar']}; color:{theme['title']};
                    border:1px solid #cbd5e1; border-radius:6px;
                    padding:3px 8px; font-size:12px; font-weight:600;
                }}
                QSpinBox:focus {{ border-color:{theme['accent']}; }}
                QSpinBox::up-button, QSpinBox::down-button {{ width:20px; }}
            """)
        for button in page.cart_table.findChildren(QPushButton):
            if button.text().strip() in {"✕", "x", "X"}:
                button.setFixedSize(30, 30)
                button.setStyleSheet("""
                    QPushButton {
                        background:#fee2e2; color:#ef4444; border:none;
                        border-radius:6px; font-weight:bold; font-size:13px;
                    }
                    QPushButton:hover { background:#ef4444; color:white; }
                    QPushButton:pressed {
                        background:#991b1b;
                        color:white;
                        padding-top:3px;
                    }
                """)

    def _field_style(self, theme):
        return f"""
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit {{
                background: {theme['topbar']};
                color: {theme['title']};
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 13px;
            }}
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTextEdit:focus {{
                border-color: {theme['accent']};
            }}
            QComboBox::drop-down {{
                border: none;
                width: 22px;
            }}
        """

    def _date_field_style(self, theme):
        return f"""
            QDateEdit {{
                background: {theme['topbar']};
                color: {theme['title']};
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 0 36px 0 12px;
                font-size: 13px;
            }}
            QDateEdit:focus {{
                border-color: {theme['accent']};
            }}
            QDateEdit::drop-down {{
                subcontrol-origin: border;
                subcontrol-position: top right;
                width: 30px;
                border-left: 1px solid #e2e8f0;
                border-top-right-radius: 6px;
                border-bottom-right-radius: 6px;
                background: {theme['topbar']};
            }}
            QDateEdit::drop-down:hover {{
                background: {theme['content']};
            }}
        """

    def _button_style(self, theme):
        return f"""
            QPushButton {{
                background: {theme['topbar']};
                color: {theme['title']};
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: {theme['accent']};
                color: {theme['nav_active']};
                border-color: {theme['accent']};
            }}
            QPushButton:pressed {{
                background: {theme['sidebar_alt']};
                color: {theme['nav_text']};
                padding-top: 7px;
            }}
            QPushButton:checked {{
                background: {theme['accent']};
                color: {theme['nav_active']};
                border-color: {theme['accent']};
            }}
            QPushButton:disabled {{
                background: #e2e8f0;
                color: #94a3b8;
                border-color: #cbd5e1;
            }}
        """

    def _danger_button_style(self):
        return """
            QPushButton {
                background:#dc2626;
                color:white;
                border:none;
                border-radius:6px;
                padding:6px 12px;
                font-size:12px;
                font-weight:bold;
            }
            QPushButton:hover { background:#b91c1c; }
            QPushButton:pressed { background:#991b1b; padding-top:7px; }
            QPushButton:disabled { background:#fecaca; color:#7f1d1d; }
        """

    def _table_style(self, theme):
        return f"""
            QTableWidget {{
                background: {theme['topbar']};
                color: {theme['title']};
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                gridline-color: #e2e8f0;
                alternate-background-color: {theme['content']};
                font-size: 13px;
            }}
            QTableWidget::item {{
                padding: 7px 10px;
            }}
            QTableWidget::item:selected {{
                background: {theme['accent']};
                color: {theme['nav_active']};
            }}
        """

    def _header_style(self, theme):
        return f"""
            QHeaderView::section {{
                background: {theme['content']};
                color: {theme['muted']};
                border: none;
                border-bottom: 1px solid #e2e8f0;
                padding: 8px;
                font-weight: bold;
            }}
        """

    def _panel_style(self, theme):
        return f"""
            QFrame {{
                background: {theme['topbar']};
                border: 1px solid #e2e8f0;
                border-radius: 8px;
            }}
            QFrame QLabel {{
                border: none;
                background: transparent;
            }}
        """

    def _label_style(self, theme):
        return f"""
            QLabel {{
                color: {theme['title']};
                background: transparent;
                border: none;
            }}
        """

    def _tab_style(self, theme):
        return f"""
            QTabWidget::pane {{
                border: 1px solid #e2e8f0;
                background: {theme['content']};
                border-radius: 8px;
            }}
            QTabBar::tab {{
                background: {theme['topbar']};
                color: {theme['muted']};
                border: 1px solid #cbd5e1;
                padding: 8px 14px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
            }}
            QTabBar::tab:selected {{
                background: {theme['accent']};
                color: {theme['nav_active']};
                border-color: {theme['accent']};
            }}
        """

    def _global_theme_style(self, theme):
        return f"""
            QWidget {{
                font-family: "Segoe UI";
                color: {theme['title']};
                selection-background-color: {theme['accent']};
            }}
            QMainWindow, QStackedWidget, QWidget#content {{
                background: {theme['content']};
            }}
            QDialog {{
                background: {theme['topbar']};
            }}
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit {{
                background: {theme['topbar']};
                color: {theme['title']};
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 6px 10px;
            }}
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
                border-color: {theme['accent']};
            }}
            QTableWidget {{
                background: {theme['topbar']};
                color: {theme['title']};
                alternate-background-color: {theme['content']};
                gridline-color: #e2e8f0;
            }}
            QHeaderView::section {{
                background: {theme['content']};
                color: {theme['muted']};
                border: none;
                border-bottom: 1px solid #e2e8f0;
                padding: 8px;
                font-weight: bold;
            }}
            QPushButton {{
                background: {theme['topbar']};
                color: {theme['title']};
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 6px 12px;
            }}
            QPushButton:hover {{
                border-color: {theme['accent']};
                color: {theme['accent']};
            }}
            QTabWidget::pane {{
                border: 1px solid #e2e8f0;
                background: {theme['content']};
                border-radius: 8px;
            }}
            QTabBar::tab {{
                background: {theme['topbar']};
                color: {theme['muted']};
                border: 1px solid #cbd5e1;
                padding: 8px 14px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
            }}
            QTabBar::tab:selected {{
                background: {theme['accent']};
                color: {theme['nav_active']};
                border-color: {theme['accent']};
            }}
            QScrollBar:vertical {{
                border: none; background: #f1f5f9; width: 8px; border-radius: 4px;
            }}
            QScrollBar::handle:vertical {{
                background: #cbd5e1; border-radius: 4px; min-height: 20px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {theme['accent']}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QToolTip {{
                background: #1e293b; color: white; border: none;
                padding: 4px 8px; border-radius: 4px;
            }}
        """

    def _logout(self):
        reply = QMessageBox.question(
            self, self.labels["logout"], self.labels["logout_q"],
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._logging_out = True
            db.clear_user_activity(self.user.get("id"))
            self.close()
            from ui.login_dialog import LoginDialog
            dlg = LoginDialog()
            if dlg.exec():
                db.touch_user_activity(dlg.logged_user["id"])
                self.next_window = MainWindow(dlg.logged_user)
                self.next_window.showMaximized()
