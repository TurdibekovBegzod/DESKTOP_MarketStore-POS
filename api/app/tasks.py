import logging
import smtplib

import httpx

from ai.agent import reply_in_conversation
from ai.gemini import GeminiNotConfiguredError
from ai.tools import StoreContext, set_store_context
from app.celery_app import celery_app
from app.email_service import (
    EmailNotConfiguredError,
    send_admin_password_reset_code,
    send_password_reset_code,
    send_signup_verification_code,
)
from app.instagram_api import InstagramNotConfiguredError, send_message
from app.instagram_service import get_account_config_by_id, verify_instagram_connection


logger = logging.getLogger(__name__)


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
def reply_to_instagram_dm_task(
    account_id_or_recipient: str,
    recipient_id_or_text: str | None = None,
    text: str | None = None,
) -> None:
    """Answer one customer DM using the store's database settings.

    Off the request path on purpose: Gemini takes seconds, and a webhook that
    answers slowly makes Meta redeliver the whole batch and eventually switch
    the subscription off.
    """
    if text is None and recipient_id_or_text is not None:
        # Legacy 2-argument signature: (recipient_id, text)
        account_id = ""
        recipient_id = account_id_or_recipient
        msg_text = recipient_id_or_text
    else:
        # 3-argument signature: (account_id, recipient_id, text)
        account_id = account_id_or_recipient
        recipient_id = str(recipient_id_or_text or "")
        msg_text = text

    config = get_account_config_by_id(account_id) if account_id else None

    if account_id:
        if config is None:
            logger.warning("Account %s not found in database; skipping DM reply", account_id)
            return
        if not config.auto_reply:
            logger.info("Auto-reply is off for account %s; skipping DM reply", account_id)
            return
        if not config.access_token:
            logger.warning("No Instagram access token configured for account %s; skipping DM reply", account_id)
            return
        # Verify connection with Instagram before proceeding
        if not verify_instagram_connection(config.access_token, config.account_id):
            logger.warning("Instagram connection check failed for account %s; skipping DM reply", account_id)
            return

    if config is not None:
        store_ctx = StoreContext(user_id=config.user_id, user_uid=config.user_uid, email=config.email)
        token_to_use = config.access_token
        gemini_key = config.gemini_api_key
        conv_id = f"{config.account_id}:{recipient_id}"
    else:
        store_ctx = None
        token_to_use = None
        gemini_key = None
        conv_id = recipient_id

    set_store_context(store_ctx)
    try:
        reply = reply_in_conversation(conv_id, msg_text, api_key=gemini_key)
        if not reply:
            return
        send_message(recipient_id, reply, access_token=token_to_use)
    finally:
        set_store_context(None)
