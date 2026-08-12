from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QMessageBox, QFrame
)
import webbrowser

from PyQt6.QtCore import QTimer, Qt
import api_client
import database as db


class PasswordResetDialog(QDialog):
    def __init__(self, email="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Parolni tiklash")
        self.setMinimumSize(420, 360)
        self.setStyleSheet("""
            QDialog { background: #0f172a; }
            QLabel#title { color: #e2e8f0; font-size: 20px; font-weight: bold; }
            QLabel#hint { color: #94a3b8; font-size: 12px; }
            QLabel { color: #cbd5e1; font-size: 13px; }
            QLineEdit {
                background: #1e293b; border: 1px solid #334155;
                border-radius: 8px; padding: 10px 14px;
                color: #e2e8f0; font-size: 14px;
            }
            QLineEdit:focus { border: 1px solid #3b82f6; }
            QPushButton {
                background: #334155; color: #e2e8f0; border: none;
                border-radius: 8px; padding: 11px; font-weight: bold;
            }
            QPushButton:hover { background: #475569; }
            QPushButton#primary { background: #3b82f6; color: white; }
            QPushButton#primary:hover { background: #2563eb; }
        """)
        self._build_ui(email)

    def _build_ui(self, email):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 26)
        layout.setSpacing(10)

        title = QLabel("Parolni tiklash")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        hint = QLabel("Emailingizga yuborilgan 6 xonali kodni kiriting.")
        hint.setObjectName("hint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.email_edit = QLineEdit(email)
        self.email_edit.setPlaceholderText("Gmail / Email")
        self.email_edit.setMinimumHeight(44)
        layout.addWidget(self.email_edit)

        send_btn = QPushButton("Kod yuborish")
        send_btn.clicked.connect(self._send_code)
        layout.addWidget(send_btn)

        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("Verification code")
        self.code_edit.setMinimumHeight(44)
        self.code_edit.setMaxLength(12)
        layout.addWidget(self.code_edit)

        self.new_password_edit = QLineEdit()
        self.new_password_edit.setPlaceholderText("Yangi parol")
        self.new_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.new_password_edit.setMinimumHeight(44)
        layout.addWidget(self.new_password_edit)

        self.error_lbl = QLabel("")
        self.error_lbl.setStyleSheet("color: #f87171; font-size: 12px;")
        self.error_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.error_lbl.setMinimumHeight(22)
        self.error_lbl.setWordWrap(True)
        layout.addWidget(self.error_lbl)

        confirm_btn = QPushButton("Parolni yangilash")
        confirm_btn.setObjectName("primary")
        confirm_btn.clicked.connect(self._confirm_reset)
        layout.addWidget(confirm_btn)

    def _send_code(self):
        email = self.email_edit.text().strip()
        if not email:
            self.error_lbl.setText("Email kiriting.")
            return
        try:
            api_client.request_password_reset(email)
            self.error_lbl.setStyleSheet("color: #22c55e; font-size: 12px;")
            self.error_lbl.setText("Agar email mavjud bo'lsa, verification code yuborildi.")
            self.code_edit.setFocus()
        except api_client.ApiClientError as exc:
            self.error_lbl.setStyleSheet("color: #f87171; font-size: 12px;")
            self.error_lbl.setText(str(exc))

    def _confirm_reset(self):
        email = self.email_edit.text().strip()
        code = self.code_edit.text().strip()
        new_password = self.new_password_edit.text().strip()
        if not email or not code or not new_password:
            self.error_lbl.setStyleSheet("color: #f87171; font-size: 12px;")
            self.error_lbl.setText("Email, code va yangi parolni kiriting.")
            return
        try:
            api_client.confirm_password_reset(email, code, new_password)
            QMessageBox.information(self, "Tayyor", "Parol yangilandi. Endi yangi parol bilan kiring.")
            self.accept()
        except api_client.ApiClientError as exc:
            self.error_lbl.setStyleSheet("color: #f87171; font-size: 12px;")
            self.error_lbl.setText(str(exc))


class LoginDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.logged_user = None
        self._google_state = None
        self._google_timer = QTimer(self)
        self._google_timer.setInterval(2000)
        self._google_timer.timeout.connect(self._poll_google_login)
        self.setWindowTitle("Market Store POS — Kirish")
        self.setMinimumSize(420, 500)
        self.resize(420, 520)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self._build_ui()

    def _build_ui(self):
        self.setStyleSheet("""
            QDialog { background: #0f172a; }
            QLabel#title { color: #e2e8f0; font-size: 22px; font-weight: bold; }
            QLabel#sub   { color: #64748b; font-size: 13px; }
            QLabel       { color: #cbd5e1; font-size: 13px; }
            QLineEdit {
                background: #1e293b; border: 1px solid #334155;
                border-radius: 8px; padding: 10px 14px;
                color: #e2e8f0; font-size: 14px;
            }
            QLineEdit:focus { border: 1px solid #3b82f6; }
            QPushButton#login {
                background: #3b82f6; color: white; font-size: 15px;
                font-weight: bold; border: none; border-radius: 8px;
                padding: 12px;
            }
            QPushButton#login:hover { background: #2563eb; }
            QPushButton#login:pressed { background: #1d4ed8; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 34, 40, 28)
        layout.setSpacing(10)

        icon_lbl = QLabel("🛒")
        icon_lbl.setStyleSheet("font-size: 40px;")
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon_lbl)

        title = QLabel("Market Store POS")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        sub = QLabel("Tizimga kirish")
        sub.setObjectName("sub")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(8)
        layout.addWidget(sub)

        self.email_edit = QLineEdit()
        self.email_edit.setPlaceholderText("Gmail / Email")
        self.email_edit.setMinimumHeight(44)
        layout.addWidget(self.email_edit)

        self.password_edit = QLineEdit()
        self.password_edit.setPlaceholderText("Parol")
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setMinimumHeight(44)
        self.password_edit.returnPressed.connect(self._try_login)
        layout.addWidget(self.password_edit)

        layout.addSpacing(4)

        self.error_lbl = QLabel("")
        self.error_lbl.setStyleSheet("color: #f87171; font-size: 12px;")
        self.error_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.error_lbl.setMinimumHeight(22)
        self.error_lbl.setWordWrap(True)
        layout.addWidget(self.error_lbl)

        btn = QPushButton("Kirish")
        btn.setObjectName("login")
        btn.setMinimumHeight(46)
        btn.clicked.connect(self._try_login)
        layout.addWidget(btn)

        self.google_btn = QPushButton("Google orqali kirish")
        self.google_btn.setMinimumHeight(44)
        self.google_btn.setStyleSheet("""
            QPushButton {
                background: #ffffff; color: #0f172a; border: 1px solid #cbd5e1;
                border-radius: 8px; padding: 10px; font-weight: bold;
            }
            QPushButton:hover { background: #f8fafc; }
        """)
        self.google_btn.clicked.connect(self._start_google_login)
        layout.addWidget(self.google_btn)

        forgot_btn = QPushButton("Parolni unutdingizmi?")
        forgot_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        forgot_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #93c5fd; border: none;
                padding: 6px; font-size: 12px; font-weight: normal;
            }
            QPushButton:hover { color: #bfdbfe; text-decoration: underline; }
        """)
        forgot_btn.clicked.connect(self._open_password_reset)
        layout.addWidget(forgot_btn)

        hint = QLabel("Default: admin@gmail.com / admin123")
        hint.setStyleSheet("color: #475569; font-size: 11px;")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)

    def _open_password_reset(self):
        dlg = PasswordResetDialog(self.email_edit.text().strip(), self)
        dlg.exec()

    def _try_login(self):
        email = self.email_edit.text().strip()
        password = self.password_edit.text().strip()

        if not email or not password:
            self.error_lbl.setText("⚠ Iltimos, barcha maydonlarni to'ldiring")
            return

        try:
            online_session = api_client.login(email, password)
            api_user = online_session["user"]
            user = db.sync_online_user(
                api_user.get("email"),
                display_name=api_user.get("display_name"),
                role="cashier",
                access_token=online_session["token"],
            )
            self.logged_user = dict(user)
            self.logged_user["api_access_token"] = online_session["token"]
            self.logged_user["api_user_id"] = api_user.get("id")
            db.log_login(self.logged_user)
            self.accept()
        except api_client.ApiClientError as exc:
            self.error_lbl.setText(str(exc))
            self.password_edit.clear()
            self.password_edit.setFocus()
        except db.AppError as exc:
            self.error_lbl.setText(str(exc))
            self.password_edit.setFocus()

    def _start_google_login(self):
        self.error_lbl.setStyleSheet("color: #93c5fd; font-size: 12px;")
        self.error_lbl.setText("Google oynasi ochilmoqda...")
        self.google_btn.setEnabled(False)
        try:
            session = api_client.start_google_login()
            self._google_state = session["state"]
            webbrowser.open(session["auth_url"])
            self.error_lbl.setText("Browserda Google orqali kiring. App avtomatik davom etadi.")
            self._google_timer.start()
        except api_client.ApiClientError as exc:
            self.google_btn.setEnabled(True)
            self.error_lbl.setStyleSheet("color: #f87171; font-size: 12px;")
            self.error_lbl.setText(str(exc))
        except Exception:
            self.google_btn.setEnabled(True)
            self.error_lbl.setStyleSheet("color: #f87171; font-size: 12px;")
            self.error_lbl.setText("Google loginni boshlashda xatolik yuz berdi.")

    def _poll_google_login(self):
        if not self._google_state:
            self._google_timer.stop()
            self.google_btn.setEnabled(True)
            return
        try:
            status = api_client.get_google_login_status(self._google_state)
        except api_client.ApiClientError as exc:
            self._google_timer.stop()
            self.google_btn.setEnabled(True)
            self.error_lbl.setStyleSheet("color: #f87171; font-size: 12px;")
            self.error_lbl.setText(str(exc))
            return
        if status.get("status") == "pending":
            return
        self._google_timer.stop()
        self.google_btn.setEnabled(True)
        if status.get("status") != "completed" or not status.get("access_token"):
            self.error_lbl.setStyleSheet("color: #f87171; font-size: 12px;")
            self.error_lbl.setText(status.get("detail") or "Google login yakunlanmadi.")
            return
        try:
            self._complete_online_login(status["access_token"])
        except api_client.ApiClientError as exc:
            self.error_lbl.setStyleSheet("color: #f87171; font-size: 12px;")
            self.error_lbl.setText(str(exc))
        except db.AppError as exc:
            self.error_lbl.setStyleSheet("color: #f87171; font-size: 12px;")
            self.error_lbl.setText(str(exc))

    def _complete_online_login(self, token):
        api_user = api_client.get_current_user(token)
        user = db.sync_online_user(
            api_user.get("email"),
            display_name=api_user.get("display_name"),
            role="cashier",
            access_token=token,
        )
        self.logged_user = dict(user)
        self.logged_user["api_access_token"] = token
        self.logged_user["api_user_id"] = api_user.get("id")
        db.log_login(self.logged_user)
        self.accept()
