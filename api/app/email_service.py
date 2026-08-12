import smtplib
from email.message import EmailMessage

from app.config import get_settings


class EmailNotConfiguredError(RuntimeError):
    pass


def send_password_reset_code(to_email: str, code: str) -> None:
    settings = get_settings()
    username = settings.smtp_username
    password = settings.smtp_password
    from_email = settings.smtp_from_email or username
    if not settings.smtp_host or not username or not password or not from_email:
        raise EmailNotConfiguredError("SMTP settings are not configured")

    message = EmailMessage()
    message["Subject"] = "MarketStore POS password reset code"
    message["From"] = f"{settings.smtp_from_name} <{from_email}>"
    message["To"] = to_email
    message.set_content(
        "MarketStore POS parolni tiklash kodi:\n\n"
        f"{code}\n\n"
        f"Kod {settings.password_reset_code_minutes} minut amal qiladi. "
        "Agar bu so'rovni siz yubormagan bo'lsangiz, xabarni e'tiborsiz qoldiring."
    )

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
        if settings.smtp_use_tls:
            smtp.starttls()
        smtp.login(username, password)
        smtp.send_message(message)
