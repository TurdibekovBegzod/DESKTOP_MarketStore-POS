"""Password changes that are proven by a code mailed to the account address.

Two passwords live behind this one dialog: the account (Gmail) password used to
sign in, and the password that opens the main section. They are set to the same
value when the account is created and drift apart the moment either is changed.

Both changes work the same way - the server mails a six digit code to the
account's own address, and the new password is only accepted together with that
code - so the difference between them is two callables, not two dialogs.
"""
import math
import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QApplication, QMessageBox,
)

import api_client


RESEND_SECONDS = 180


class PasswordChangeDialog(QDialog):
    """Send a code, then take the code and the new password together."""

    def __init__(
        self,
        parent=None,
        *,
        title="Parolni o'zgartirish",
        hint="",
        request_code=None,
        confirm_change=None,
        theme=None,
        send_on_open=False,
    ):
        super().__init__(parent)
        self._request_code = request_code
        self._confirm_change = confirm_change
        self.theme = theme or {}
        self.countdown = 0
        self.resend_at = 0.0
        self.has_sent_once = False
        self.new_password = None
        self.setWindowTitle(title)
        self.setFixedWidth(400)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
        self._build_ui(title, hint)
        if send_on_open:
            QTimer.singleShot(0, self._send_code)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self, title, hint):
        background = self.theme.get("topbar", "#ffffff")
        text_color = self.theme.get("title", "#0f172a")
        muted = self.theme.get("muted", "#64748b")
        field_bg = self.theme.get("content", "#ffffff")
        accent = self.theme.get("accent", "#3b82f6")
        self.setStyleSheet(f"""
            QDialog {{ background: {background}; }}
            QLabel {{ color: {text_color}; font-size: 13px; }}
            QLabel#title {{ font-size: 17px; font-weight: bold; }}
            QLabel#hint {{ color: {muted}; font-size: 12px; }}
            QLineEdit {{
                background: {field_bg}; color: {text_color};
                border: 1px solid #cbd5e1; border-radius: 8px;
                padding: 9px 12px; font-size: 14px;
            }}
            QLineEdit:focus {{ border-color: {accent}; }}
            QPushButton {{
                background: {background}; color: {text_color};
                border: 1px solid #cbd5e1; border-radius: 8px;
                padding: 9px 16px; font-size: 13px;
            }}
            QPushButton:hover {{ border-color: {accent}; }}
            QPushButton:disabled {{ color: #94a3b8; border-color: #e2e8f0; }}
            QPushButton#primary {{
                background: {accent}; color: white;
                border-color: {accent}; font-weight: bold;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(10)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("title")
        layout.addWidget(title_lbl)

        if hint:
            hint_lbl = QLabel(hint)
            hint_lbl.setObjectName("hint")
            hint_lbl.setWordWrap(True)
            layout.addWidget(hint_lbl)

        self.send_btn = QPushButton("Kodni jo'natish")
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.clicked.connect(self._send_code)
        layout.addWidget(self.send_btn)

        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("Emailga kelgan kod (6 xonali)")
        self.code_edit.setMaxLength(12)
        layout.addWidget(self.code_edit)

        self.password_edit = QLineEdit()
        self.password_edit.setPlaceholderText("Yangi parol (kamida 6 ta belgi)")
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.password_edit)

        self.confirm_edit = QLineEdit()
        self.confirm_edit.setPlaceholderText("Yangi parolni takrorlang")
        self.confirm_edit.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.confirm_edit)

        self.message_lbl = QLabel("")
        self.message_lbl.setWordWrap(True)
        self.message_lbl.setMinimumHeight(34)
        layout.addWidget(self.message_lbl)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel_btn = QPushButton("Bekor")
        cancel_btn.clicked.connect(self.reject)
        self.save_btn = QPushButton("Saqlash")
        self.save_btn.setObjectName("primary")
        self.save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_btn.clicked.connect(self._confirm)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(self.save_btn)
        layout.addLayout(buttons)

        self.code_edit.returnPressed.connect(self._confirm)
        self.password_edit.returnPressed.connect(self._confirm)
        self.confirm_edit.returnPressed.connect(self._confirm)

    # ------------------------------------------------------------------
    # Resend countdown
    # ------------------------------------------------------------------
    def _start_countdown(self, seconds=RESEND_SECONDS):
        self.countdown = seconds
        self.resend_at = time.monotonic() + seconds
        self._update_send_button()
        self.timer.start()

    def _tick(self):
        self.countdown = max(0, math.ceil(self.resend_at - time.monotonic()))
        if self.countdown == 0:
            self.timer.stop()
        self._update_send_button()

    def _update_send_button(self):
        if self.countdown > 0:
            minutes, seconds = divmod(self.countdown, 60)
            self.send_btn.setEnabled(False)
            self.send_btn.setText(f"Qayta jo'natish ({minutes:02d}:{seconds:02d})")
        else:
            self.send_btn.setEnabled(True)
            self.send_btn.setText("Qayta jo'natish" if self.has_sent_once else "Kodni jo'natish")

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------
    def _show_error(self, message):
        self.message_lbl.setStyleSheet("color:#dc2626;font-size:12px;")
        self.message_lbl.setText(str(message or "Noma'lum xatolik."))

    def _show_info(self, message):
        self.message_lbl.setStyleSheet("color:#16a34a;font-size:12px;")
        self.message_lbl.setText(str(message or ""))

    @staticmethod
    def _error_text(exc):
        if isinstance(exc, api_client.ApiOfflineError):
            return "Serverga ulanib bo'lmadi. Bu amal uchun internet kerak."
        text = str(exc).strip()
        return text or f"Kutilmagan xatolik: {type(exc).__name__}"

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _send_code(self):
        if self.countdown > 0 or not self._request_code:
            return
        self._show_info("Kod yuborilmoqda...")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self.send_btn.setEnabled(False)
        try:
            self._request_code()
        except Exception as exc:  # noqa: BLE001 - shown to the user
            self._show_error(self._error_text(exc))
            self.send_btn.setEnabled(True)
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.has_sent_once = True
        self._show_info("Emailingizga tasdiqlash kodi yuborildi.")
        self.code_edit.setFocus()
        self._start_countdown()

    def _confirm(self):
        if not self._confirm_change:
            return
        code = self.code_edit.text().strip()
        password = self.password_edit.text().strip()
        confirm = self.confirm_edit.text().strip()
        if not code:
            self._show_error("Emailga kelgan kodni kiriting.")
            return
        if len(password) < 6:
            self._show_error("Parol kamida 6 ta belgidan iborat bo'lishi kerak.")
            return
        if password != confirm:
            self._show_error("Parollar bir xil emas.")
            return

        self._show_info("Saqlanmoqda...")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self.save_btn.setEnabled(False)
        try:
            self._confirm_change(code, password)
        except Exception as exc:  # noqa: BLE001 - shown to the user
            self._show_error(self._error_text(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()
            self.save_btn.setEnabled(True)
        self.new_password = password
        self.accept()


def change_account_password(parent, email, theme=None):
    """Change the password used to sign in with the account's e-mail."""
    dialog = PasswordChangeDialog(
        parent,
        title="Gmail parolini o'zgartirish",
        hint=(
            f"{email} manziliga tasdiqlash kodi yuboriladi. "
            "Kodni va yangi parolni kiriting."
        ),
        request_code=lambda: api_client.request_password_reset(email),
        confirm_change=lambda code, password: api_client.confirm_password_reset(
            email, code, password
        ),
        theme=theme,
    )
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    QMessageBox.information(
        parent,
        "Tayyor",
        "Gmail paroli yangilandi. Keyingi safar shu parol bilan kiring.",
    )
    return dialog.new_password


def change_admin_password(parent, token, email, theme=None, send_on_open=False, recovery=False):
    """Change the password that opens the main section.

    ``recovery`` is the same change reached from the unlock prompt's "forgot
    password" link - the wording differs, the flow does not.
    """
    dialog = PasswordChangeDialog(
        parent,
        title="Asosiy oyna parolini tiklash" if recovery else "Asosiy oyna parolini o'zgartirish",
        hint=(
            f"{email} manziliga tasdiqlash kodi yuboriladi. Kodni va yangi parolni "
            "kiriting. Shundan keyin asosiy oynaga faqat shu yangi parol bilan kiriladi."
        ),
        request_code=lambda: api_client.request_admin_password_code(token),
        confirm_change=lambda code, password: api_client.confirm_admin_password(
            token, code, password
        ),
        theme=theme,
        send_on_open=send_on_open,
    )
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    QMessageBox.information(
        parent,
        "Tayyor",
        "Asosiy oyna paroli yangilandi. Bu o'zgarish barcha qurilmalarga tarqaladi.",
    )
    return dialog.new_password
