import math
import time

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QMessageBox, QFrame
)

from PyQt6.QtCore import QTimer, Qt
import api_client
import database as db
import sync_service


class PasswordResetDialog(QDialog):
    def __init__(self, email="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Parolni tiklash")
        self.setMinimumSize(420, 380)
        self.reset_countdown = 0
        self.reset_resend_at = 0.0
        self.has_sent_once = False
        self.reset_timer = QTimer(self)
        self.reset_timer.setInterval(1000)
        self.reset_timer.timeout.connect(self._tick_reset_countdown)
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
                background: #334155; color: #e2e8f0; border: 1px solid #475569;
                border-radius: 8px; padding: 11px; font-weight: bold;
            }
            QPushButton:hover { background: #475569; border-color: #64748b; }
            QPushButton:pressed {
                background: #1e293b; border-color: #3b82f6;
                padding-top: 13px; padding-bottom: 9px;
            }
            QPushButton:disabled {
                background: #1e293b; color: #64748b; border: 1px solid #334155;
            }
            QPushButton#primary { background: #3b82f6; color: white; border: none; }
            QPushButton#primary:hover { background: #2563eb; }
            QPushButton#primary:pressed {
                background: #1d4ed8;
                padding-top: 13px; padding-bottom: 9px;
            }
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

        self.send_btn = QPushButton("Kodni jo'natish")
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.clicked.connect(self._send_code)
        layout.addWidget(self.send_btn)

        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("Verification code (6 xonali)")
        self.code_edit.setMinimumHeight(44)
        self.code_edit.setMaxLength(12)
        layout.addWidget(self.code_edit)

        self.new_password_edit = QLineEdit()
        self.new_password_edit.setPlaceholderText("Yangi parol (kamida 6 ta belgi)")
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
        confirm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        confirm_btn.clicked.connect(self._confirm_reset)
        layout.addWidget(confirm_btn)

    def _start_countdown(self, seconds=180):
        self.reset_countdown = seconds
        self.reset_resend_at = time.monotonic() + seconds
        self._update_send_button()
        self.reset_timer.start()

    def _tick_reset_countdown(self):
        self.reset_countdown = max(0, math.ceil(self.reset_resend_at - time.monotonic()))
        if self.reset_countdown == 0:
            self.reset_timer.stop()
        self._update_send_button()

    def _update_send_button(self):
        if self.reset_countdown > 0:
            self.send_btn.setEnabled(False)
            minutes, seconds = divmod(self.reset_countdown, 60)
            self.send_btn.setText(f"Qayta jo'natish ({minutes:02d}:{seconds:02d})")
        else:
            self.send_btn.setEnabled(True)
            if self.has_sent_once:
                self.send_btn.setText("Qayta jo'natish")
            else:
                self.send_btn.setText("Kodni jo'natish")

    def _send_code(self):
        if self.reset_countdown > 0:
            return
        email = self.email_edit.text().strip()
        if not email:
            self.error_lbl.setStyleSheet("color: #f87171; font-size: 12px;")
            self.error_lbl.setText("Email kiriting.")
            return
        try:
            api_client.request_password_reset(email)
            self.has_sent_once = True
            self.error_lbl.setStyleSheet("color: #22c55e; font-size: 12px;")
            self.error_lbl.setText("Agar email mavjud bo'lsa, verification code yuborildi.")
            self.code_edit.setFocus()
            self._start_countdown(180)
        except (api_client.ApiClientError, Exception) as exc:
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
        if len(new_password) < 6:
            self.error_lbl.setStyleSheet("color: #f87171; font-size: 12px;")
            self.error_lbl.setText("Parol kamida 6 ta belgidan iborat bo'lishi kerak.")
            return
        try:
            api_client.confirm_password_reset(email, code, new_password)
            QMessageBox.information(self, "Tayyor", "Parol yangilandi. Endi yangi parol bilan kiring.")
            self.accept()
        except (api_client.ApiClientError, Exception) as exc:
            self.error_lbl.setStyleSheet("color: #f87171; font-size: 12px;")
            self.error_lbl.setText(str(exc))


class LoginDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.logged_user = None
        self.auth_mode = "login"
        self.signup_stage = "enter_email"
        self.signup_countdown = 0
        self.signup_resend_at = 0.0
        self.has_signup_sent_once = False
        self.signup_timer = QTimer(self)
        self.signup_timer.setInterval(1000)
        self.signup_timer.timeout.connect(self._tick_signup_countdown)
        self.setWindowTitle("Market Store POS - Kirish")
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
            QPushButton#login:pressed {
                background: #1d4ed8;
                padding-top: 14px; padding-bottom: 10px;
            }
            QPushButton#login:disabled { background: #1e293b; color: #64748b; }
            QPushButton#resend {
                background: #334155; color: #e2e8f0; border: 1px solid #475569;
                border-radius: 8px; padding: 11px; font-weight: bold; font-size: 14px;
            }
            QPushButton#resend:hover { background: #475569; border-color: #64748b; }
            QPushButton#resend:pressed {
                background: #1e293b; border-color: #3b82f6;
                padding-top: 13px; padding-bottom: 9px;
            }
            QPushButton#resend:disabled {
                color: #64748b; border-color: #334155; background: #1e293b;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 34, 40, 28)
        layout.setSpacing(10)

        icon_lbl = QLabel("POS")
        icon_lbl.setStyleSheet("font-size: 24px; font-weight: bold; color: #60a5fa;")
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

        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        self.login_mode_btn = QPushButton("Login")
        self.signup_mode_btn = QPushButton("Signup")
        for mode_btn in (self.login_mode_btn, self.signup_mode_btn):
            mode_btn.setMinimumHeight(36)
            mode_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.login_mode_btn.clicked.connect(lambda: self._set_mode("login"))
        self.signup_mode_btn.clicked.connect(lambda: self._set_mode("signup"))
        mode_row.addWidget(self.login_mode_btn)
        mode_row.addWidget(self.signup_mode_btn)
        layout.addLayout(mode_row)

        self.email_edit = QLineEdit()
        self.email_edit.setPlaceholderText("Gmail / Email")
        self.email_edit.setMinimumHeight(44)
        self.email_edit.returnPressed.connect(self._handle_email_return)
        layout.addWidget(self.email_edit)

        self.signup_send_code_btn = QPushButton("Kodni jo'natish")
        self.signup_send_code_btn.setObjectName("resend")
        self.signup_send_code_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.signup_send_code_btn.setMinimumHeight(42)
        self.signup_send_code_btn.clicked.connect(self._request_signup_code)
        layout.addWidget(self.signup_send_code_btn)

        self.verification_hint = QLabel("Emailingizga yuborilgan 6 xonali kodni va yangi parolni kiriting.")
        self.verification_hint.setObjectName("sub")
        self.verification_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.verification_hint.setWordWrap(True)
        layout.addWidget(self.verification_hint)

        self.verification_code_edit = QLineEdit()
        self.verification_code_edit.setPlaceholderText("Verification code (6 xonali)")
        self.verification_code_edit.setMinimumHeight(44)
        self.verification_code_edit.setMaxLength(6)
        self.verification_code_edit.returnPressed.connect(self._try_login)
        layout.addWidget(self.verification_code_edit)

        self.password_edit = QLineEdit()
        self.password_edit.setPlaceholderText("Parol")
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setMinimumHeight(44)
        self.password_edit.returnPressed.connect(self._try_login)
        layout.addWidget(self.password_edit)

        self.confirm_password_edit = QLineEdit()
        self.confirm_password_edit.setPlaceholderText("Parolni qayta kiriting")
        self.confirm_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm_password_edit.setMinimumHeight(44)
        self.confirm_password_edit.returnPressed.connect(self._try_login)
        layout.addWidget(self.confirm_password_edit)

        layout.addSpacing(4)

        self.error_lbl = QLabel("")
        self.error_lbl.setStyleSheet("color: #f87171; font-size: 12px;")
        self.error_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.error_lbl.setMinimumHeight(22)
        self.error_lbl.setWordWrap(True)
        layout.addWidget(self.error_lbl)

        self.submit_btn = QPushButton("Kirish")
        self.submit_btn.setObjectName("login")
        self.submit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.submit_btn.setMinimumHeight(46)
        self.submit_btn.clicked.connect(self._try_login)
        layout.addWidget(self.submit_btn)

        self.forgot_btn = QPushButton("Parolni unutdingizmi?")
        self.forgot_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.forgot_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #93c5fd; border: none;
                padding: 6px; font-size: 12px; font-weight: normal;
            }
            QPushButton:hover { color: #bfdbfe; text-decoration: underline; }
            QPushButton:pressed { color: #38bdf8; }
        """)
        self.forgot_btn.clicked.connect(self._open_password_reset)
        layout.addWidget(self.forgot_btn)
        self._set_mode("login")

    def _handle_email_return(self):
        if self.auth_mode == "signup" and self.signup_stage == "enter_email":
            self._request_signup_code()
        else:
            self._try_login()

    def _set_mode(self, mode):
        self.auth_mode = mode
        is_signup = mode == "signup"
        self.signup_stage = "enter_email"
        self.signup_timer.stop()
        self.signup_countdown = 0
        self.signup_resend_at = 0.0
        self.has_signup_sent_once = False

        self.email_edit.setReadOnly(False)
        self.error_lbl.setStyleSheet("color: #f87171; font-size: 12px;")
        self.error_lbl.setText("")

        if is_signup:
            self.signup_send_code_btn.setVisible(True)
            self.signup_send_code_btn.setEnabled(True)
            self.signup_send_code_btn.setText("Kodni jo'natish")
            self.verification_hint.setVisible(False)
            self.verification_code_edit.setVisible(False)
            self.password_edit.setVisible(False)
            self.password_edit.setPlaceholderText("Yangi parol (kamida 6 ta belgi)")
            self.confirm_password_edit.setVisible(False)
            self.confirm_password_edit.setPlaceholderText("Parolni qayta kiriting")
            self.forgot_btn.setVisible(False)
            self.submit_btn.setVisible(False)
            self.submit_btn.setText("Ro'yxatdan o'tish")
        else:
            self.signup_send_code_btn.setVisible(False)
            self.verification_hint.setVisible(False)
            self.verification_code_edit.setVisible(False)
            self.password_edit.setVisible(True)
            self.password_edit.setPlaceholderText("Parol")
            self.confirm_password_edit.setVisible(False)
            self.forgot_btn.setVisible(True)
            self.submit_btn.setVisible(True)
            self.submit_btn.setText("Kirish")

        active_style = """
            QPushButton {
                background: #3b82f6; color: white; border: none;
                border-radius: 8px; padding: 8px; font-weight: bold;
            }
            QPushButton:hover { background: #2563eb; }
            QPushButton:pressed { background: #1d4ed8; padding-top: 10px; padding-bottom: 6px; }
        """
        inactive_style = """
            QPushButton {
                background: #1e293b; color: #94a3b8; border: 1px solid #334155;
                border-radius: 8px; padding: 8px; font-weight: bold;
            }
            QPushButton:hover { border-color: #3b82f6; color: #e2e8f0; background: #243248; }
            QPushButton:pressed { background: #0f172a; border-color: #2563eb; padding-top: 10px; padding-bottom: 6px; }
        """
        self.login_mode_btn.setStyleSheet(active_style if not is_signup else inactive_style)
        self.signup_mode_btn.setStyleSheet(active_style if is_signup else inactive_style)

    def _start_signup_countdown(self, seconds=180):
        self.signup_countdown = seconds
        self.signup_resend_at = time.monotonic() + seconds
        self._update_signup_send_button()
        self.signup_timer.start()

    def _tick_signup_countdown(self):
        self.signup_countdown = max(0, math.ceil(self.signup_resend_at - time.monotonic()))
        if self.signup_countdown == 0:
            self.signup_timer.stop()
        self._update_signup_send_button()

    def _update_signup_send_button(self):
        if self.signup_countdown > 0:
            self.signup_send_code_btn.setEnabled(False)
            minutes, seconds = divmod(self.signup_countdown, 60)
            self.signup_send_code_btn.setText(f"Qayta jo'natish ({minutes:02d}:{seconds:02d})")
        else:
            self.signup_send_code_btn.setEnabled(True)
            if self.has_signup_sent_once:
                self.signup_send_code_btn.setText("Qayta jo'natish")
            else:
                self.signup_send_code_btn.setText("Kodni jo'natish")

    def _request_signup_code(self):
        if self.signup_countdown > 0:
            return
        email = self.email_edit.text().strip()
        if not email:
            self.error_lbl.setStyleSheet("color: #f87171; font-size: 12px;")
            self.error_lbl.setText("Email kiriting.")
            return
        try:
            api_client.request_registration_code(email)
            self.has_signup_sent_once = True
            self.signup_stage = "enter_code_and_password"
            self.verification_hint.setVisible(True)
            self.verification_code_edit.setVisible(True)
            self.password_edit.setVisible(True)
            self.confirm_password_edit.setVisible(True)
            self.submit_btn.setVisible(True)
            self.submit_btn.setText("Ro'yxatdan o'tish")
            self.error_lbl.setStyleSheet("color: #22c55e; font-size: 12px;")
            self.error_lbl.setText("Verification code emailingizga yuborildi.")
            self.verification_code_edit.setFocus()
            self._start_signup_countdown(180)
            self.resize(420, 580)
        except (api_client.ApiClientError, Exception) as exc:
            self.error_lbl.setStyleSheet("color: #f87171; font-size: 12px;")
            self.error_lbl.setText(str(exc))

    def _open_password_reset(self):
        dlg = PasswordResetDialog(self.email_edit.text().strip(), self)
        dlg.exec()

    def _try_login(self):
        email = self.email_edit.text().strip()
        password = self.password_edit.text().strip()
        confirm_password = self.confirm_password_edit.text().strip()

        if self.auth_mode == "signup":
            if self.signup_stage == "enter_email":
                self._request_signup_code()
                return

            code = self.verification_code_edit.text().strip()
            if not code or not password or not confirm_password:
                self.error_lbl.setStyleSheet("color: #f87171; font-size: 12px;")
                self.error_lbl.setText("Barcha maydonlarni to'ldiring.")
                return
            if len(password) < 6:
                self.error_lbl.setStyleSheet("color: #f87171; font-size: 12px;")
                self.error_lbl.setText("Parol kamida 6 ta belgidan iborat bo'lishi kerak.")
                return
            if password != confirm_password:
                self.error_lbl.setStyleSheet("color: #f87171; font-size: 12px;")
                self.error_lbl.setText("Parollar bir xil emas.")
                return
            try:
                online_session = api_client.confirm_registration(email, code, password)
                self._complete_online_login(
                    online_session["token"],
                    online_session["user"],
                    allow_legacy_import=False,
                )
            except (api_client.ApiClientError, db.AppError, Exception) as exc:
                self.error_lbl.setStyleSheet("color: #f87171; font-size: 12px;")
                self.error_lbl.setText(str(exc))
                self.verification_code_edit.setFocus()
            return

        if not email or not password:
            self.error_lbl.setStyleSheet("color: #f87171; font-size: 12px;")
            self.error_lbl.setText("Iltimos, barcha maydonlarni to'ldiring.")
            return

        try:
            online_session = api_client.login(email, password)
            self._complete_online_login(
                online_session["token"],
                online_session["user"],
                allow_legacy_import=False,
            )
        except (api_client.ApiClientError, db.AppError, Exception) as exc:
            self.error_lbl.setStyleSheet("color: #f87171; font-size: 12px;")
            self.error_lbl.setText(str(exc))
            self.password_edit.clear()
            self.password_edit.setFocus()

    def _complete_online_login(self, token, api_user=None, allow_legacy_import=False):
        api_user = api_user or api_client.get_current_user(token)
        user_uid = api_user.get("user_uid") or api_user.get("uid")
        if not user_uid:
            raise db.AppError("Server account UID qaytarmadi.")
        activation = db.activate_account_database(
            user_uid,
            email=api_user.get("email"),
            allow_legacy_import=allow_legacy_import,
        )
        db.init_db(account_owner=api_user, seed_defaults=False)
        if activation.get("database_created"):
            db.mark_server_bootstrap_required()
        user = db.sync_online_user(
            api_user.get("email"),
            display_name=api_user.get("display_name"),
            role="admin",
            access_token=token,
            user_uid=user_uid,
        )
        db.remove_foreign_online_accounts(user["id"], user_uid)
        db.save_account_session(api_user, token)
        self.logged_user = dict(user)
        # role is already correctly set by sync_online_user (admin for account owners)
        self.logged_user["api_access_token"] = token
        self.logged_user["api_user_id"] = api_user.get("id")
        self.logged_user["api_user_uid"] = user_uid
        sync_result = sync_service.synchronize_account_storage(self.logged_user)
        if sync_result.get("direction") == "none":
            sync_service.refresh_account_assets(self.logged_user)
        db.log_login(self.logged_user)
        self.accept()

