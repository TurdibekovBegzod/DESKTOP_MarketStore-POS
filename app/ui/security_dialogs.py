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

TEXTS = {
    "uz": {
        "title_account": "Gmail parolini o'zgartirish",
        "title_admin": "Asosiy oyna parolini o'zgartirish",
        "title_admin_reset": "Asosiy oyna parolini tiklash",
        "hint_account": "{email} manziliga tasdiqlash kodi yuboriladi. Kodni va yangi parolni kiriting.",
        "hint_admin": "{email} manziliga tasdiqlash kodi yuboriladi. Kodni va yangi parolni kiriting. "
                      "Shundan keyin asosiy oynaga faqat shu yangi parol bilan kiriladi.",
        "send": "Kodni jo'natish",
        "resend": "Qayta jo'natish",
        "resend_in": "Qayta jo'natish ({time})",
        "code": "Emailga kelgan kod (6 xonali)",
        "new_password": "Yangi parol (kamida 6 ta belgi)",
        "repeat_password": "Yangi parolni takrorlang",
        "cancel": "Bekor",
        "save": "Saqlash",
        "sending": "Kod yuborilmoqda...",
        "sent": "Emailingizga tasdiqlash kodi yuborildi.",
        "saving": "Saqlanmoqda...",
        "need_code": "Emailga kelgan kodni kiriting.",
        "too_short": "Parol kamida 6 ta belgidan iborat bo'lishi kerak.",
        "mismatch": "Parollar bir xil emas.",
        "offline": "Serverga ulanib bo'lmadi. Bu amal uchun internet kerak.",
        "unknown": "Noma'lum xatolik.",
        "done_title": "Tayyor",
        "done_account": "Gmail paroli yangilandi. Keyingi safar shu parol bilan kiring.",
        "done_admin": "Asosiy oyna paroli yangilandi. Bu o'zgarish barcha qurilmalarga tarqaladi.",
    },
    "en": {
        "title_account": "Change email password",
        "title_admin": "Change main section password",
        "title_admin_reset": "Reset main section password",
        "hint_account": "A verification code will be sent to {email}. Enter the code and your new password.",
        "hint_admin": "A verification code will be sent to {email}. Enter the code and your new password. "
                      "From then on the main section opens with this password only.",
        "send": "Send code",
        "resend": "Resend",
        "resend_in": "Resend ({time})",
        "code": "Code from your email (6 digits)",
        "new_password": "New password (at least 6 characters)",
        "repeat_password": "Repeat the new password",
        "cancel": "Cancel",
        "save": "Save",
        "sending": "Sending the code...",
        "sent": "A verification code was sent to your email.",
        "saving": "Saving...",
        "need_code": "Enter the code from your email.",
        "too_short": "The password must be at least 6 characters.",
        "mismatch": "The passwords do not match.",
        "offline": "Could not reach the server. This needs an internet connection.",
        "unknown": "Unknown error.",
        "done_title": "Done",
        "done_account": "The email password was updated. Sign in with it next time.",
        "done_admin": "The main section password was updated. The change reaches every device.",
    },
    "ru": {
        "title_account": "Изменить пароль Gmail",
        "title_admin": "Изменить пароль основного раздела",
        "title_admin_reset": "Восстановить пароль основного раздела",
        "hint_account": "На {email} будет отправлен код подтверждения. Введите код и новый пароль.",
        "hint_admin": "На {email} будет отправлен код подтверждения. Введите код и новый пароль. "
                      "После этого основной раздел открывается только этим паролем.",
        "send": "Отправить код",
        "resend": "Отправить снова",
        "resend_in": "Отправить снова ({time})",
        "code": "Код из письма (6 цифр)",
        "new_password": "Новый пароль (минимум 6 символов)",
        "repeat_password": "Повторите новый пароль",
        "cancel": "Отмена",
        "save": "Сохранить",
        "sending": "Код отправляется...",
        "sent": "Код подтверждения отправлен на вашу почту.",
        "saving": "Сохранение...",
        "need_code": "Введите код из письма.",
        "too_short": "Пароль должен содержать минимум 6 символов.",
        "mismatch": "Пароли не совпадают.",
        "offline": "Не удалось связаться с сервером. Для этого нужен интернет.",
        "unknown": "Неизвестная ошибка.",
        "done_title": "Готово",
        "done_account": "Пароль Gmail обновлён. В следующий раз входите с ним.",
        "done_admin": "Пароль основного раздела обновлён. Изменение доходит до всех устройств.",
    },
}


def _labels(parent, language=None):
    if not language and parent is not None:
        language = parent.property("app_language")
    return TEXTS.get(language or "uz", TEXTS["uz"])


class PasswordChangeDialog(QDialog):
    """Send a code, then take the code and the new password together."""

    def __init__(
        self,
        parent=None,
        *,
        title="",
        hint="",
        labels=None,
        request_code=None,
        confirm_change=None,
        theme=None,
        send_on_open=False,
    ):
        super().__init__(parent)
        self.labels = labels or TEXTS["uz"]
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
        title_lbl.setWordWrap(True)
        layout.addWidget(title_lbl)

        if hint:
            hint_lbl = QLabel(hint)
            hint_lbl.setObjectName("hint")
            hint_lbl.setWordWrap(True)
            layout.addWidget(hint_lbl)

        self.send_btn = QPushButton(self.labels["send"])
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.clicked.connect(self._send_code)
        layout.addWidget(self.send_btn)

        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText(self.labels["code"])
        self.code_edit.setMaxLength(12)
        layout.addWidget(self.code_edit)

        self.password_edit = QLineEdit()
        self.password_edit.setPlaceholderText(self.labels["new_password"])
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.password_edit)

        self.confirm_edit = QLineEdit()
        self.confirm_edit.setPlaceholderText(self.labels["repeat_password"])
        self.confirm_edit.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.confirm_edit)

        self.message_lbl = QLabel("")
        self.message_lbl.setWordWrap(True)
        self.message_lbl.setMinimumHeight(34)
        layout.addWidget(self.message_lbl)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel_btn = QPushButton(self.labels["cancel"])
        cancel_btn.clicked.connect(self.reject)
        self.save_btn = QPushButton(self.labels["save"])
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
            self.send_btn.setText(self.labels["resend_in"].format(time=f"{minutes:02d}:{seconds:02d}"))
        else:
            self.send_btn.setEnabled(True)
            self.send_btn.setText(self.labels["resend"] if self.has_sent_once else self.labels["send"])

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------
    def _show_error(self, message):
        self.message_lbl.setStyleSheet("color:#dc2626;font-size:12px;")
        self.message_lbl.setText(str(message or self.labels["unknown"]))

    def _show_info(self, message):
        self.message_lbl.setStyleSheet("color:#16a34a;font-size:12px;")
        self.message_lbl.setText(str(message or ""))

    def _error_text(self, exc):
        if isinstance(exc, api_client.ApiOfflineError):
            return self.labels["offline"]
        return str(exc).strip() or f"{self.labels['unknown']} ({type(exc).__name__})"

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _send_code(self):
        if self.countdown > 0 or not self._request_code:
            return
        self._show_info(self.labels["sending"])
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
        self._show_info(self.labels["sent"])
        self.code_edit.setFocus()
        self._start_countdown()

    def _confirm(self):
        if not self._confirm_change:
            return
        code = self.code_edit.text().strip()
        password = self.password_edit.text().strip()
        confirm = self.confirm_edit.text().strip()
        if not code:
            self._show_error(self.labels["need_code"])
            return
        if len(password) < 6:
            self._show_error(self.labels["too_short"])
            return
        if password != confirm:
            self._show_error(self.labels["mismatch"])
            return

        self._show_info(self.labels["saving"])
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


def change_account_password(parent, email, theme=None, language=None):
    """Change the password used to sign in with the account's e-mail."""
    labels = _labels(parent, language)
    dialog = PasswordChangeDialog(
        parent,
        title=labels["title_account"],
        hint=labels["hint_account"].format(email=email),
        labels=labels,
        request_code=lambda: api_client.request_password_reset(email),
        confirm_change=lambda code, password: api_client.confirm_password_reset(
            email, code, password
        ),
        theme=theme,
    )
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    QMessageBox.information(parent, labels["done_title"], labels["done_account"])
    return dialog.new_password


def change_admin_password(parent, token, email, theme=None, send_on_open=False,
                          recovery=False, language=None):
    """Change the password that opens the main section.

    ``recovery`` is the same change reached from the unlock prompt's "forgot
    password" link - the wording differs, the flow does not.
    """
    labels = _labels(parent, language)
    dialog = PasswordChangeDialog(
        parent,
        title=labels["title_admin_reset"] if recovery else labels["title_admin"],
        hint=labels["hint_admin"].format(email=email),
        labels=labels,
        request_code=lambda: api_client.request_admin_password_code(token),
        confirm_change=lambda code, password: api_client.confirm_admin_password(
            token, code, password
        ),
        theme=theme,
        send_on_open=send_on_open,
    )
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    QMessageBox.information(parent, labels["done_title"], labels["done_admin"])
    return dialog.new_password
