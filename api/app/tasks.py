import smtplib

import httpx

from ai.agent import reply_to
from ai.gemini import GeminiNotConfiguredError
from app.celery_app import celery_app
from app.email_service import (
    EmailNotConfiguredError,
    send_admin_password_reset_code,
    send_password_reset_code,
    send_signup_verification_code,
)
from app.instagram_api import InstagramNotConfiguredError, send_message


TRANSIENT_EMAIL_ERRORS = (OSError, TimeoutError, ConnectionError, smtplib.SMTPException)
TRANSIENT_HTTP_ERRORS = (httpx.TransportError, httpx.HTTPStatusError, OSError, TimeoutError)


@celery_app.task(
    name="send_password_reset_code",
    autoretry_for=TRANSIENT_EMAIL_ERRORS,
    dont_autoretry_for=(EmailNotConfiguredError,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 5},
)
def send_password_reset_code_task(to_email: str, code: str) -> None:
    send_password_reset_code(to_email, code)


@celery_app.task(
    name="send_admin_password_reset_code",
    autoretry_for=TRANSIENT_EMAIL_ERRORS,
    dont_autoretry_for=(EmailNotConfiguredError,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 5},
)
def send_admin_password_reset_code_task(to_email: str, code: str) -> None:
    send_admin_password_reset_code(to_email, code)


@celery_app.task(
    name="send_signup_verification_code",
    autoretry_for=TRANSIENT_EMAIL_ERRORS,
    dont_autoretry_for=(EmailNotConfiguredError,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 5},
)
def send_signup_verification_code_task(to_email: str, code: str) -> None:
    send_signup_verification_code(to_email, code)


@celery_app.task(
    name="reply_to_instagram_dm",
    autoretry_for=TRANSIENT_HTTP_ERRORS,
    dont_autoretry_for=(GeminiNotConfiguredError, InstagramNotConfiguredError),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def reply_to_instagram_dm_task(recipient_id: str, text: str | None) -> None:
    """Answer one customer DM.

    Off the request path on purpose: Gemini takes seconds, and a webhook that
    answers slowly makes Meta redeliver the whole batch and eventually switch
    the subscription off.
    """
    reply = reply_to(text)
    if not reply:
        return
    send_message(recipient_id, reply)
