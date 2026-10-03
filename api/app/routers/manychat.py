"""ManyChat webhook: one shop's DM agent behind a ManyChat flow.

ManyChat calls this from an "External Request" (or "Dynamic Block") action with
the subscriber's id and last message, and waits for the answer in the response
body. So unlike the Instagram callback, the reply is produced inline rather than
queued: there is no send step of our own, ManyChat delivers whatever comes back.

The shop is fixed by MANYCHAT_ACCOUNT_EMAIL, never taken from the request. A
caller with the token can ask questions, but cannot point the agent at another
account's products or rules.
"""

from dataclasses import dataclass
import hmac
import logging
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from ai.agent import reply_in_conversation
from ai.gemini import GeminiNotConfiguredError
from ai.tools import StoreContext, set_store_context
from app.config import get_settings
from app.database import SessionLocal


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/manychat", tags=["manychat"])

# ManyChat gives up on an external request after 10 seconds. Each Gemini call
# gets less than that, so a stalled one fails here with a logged error instead
# of ManyChat silently timing out on us.
GEMINI_TIMEOUT_SECONDS = 9.0


@dataclass
class ManychatShop:
    user_id: int
    user_uid: str
    email: str
    gemini_api_key: str | None


def resolve_shop(email: str) -> ManychatShop | None:
    """The account behind ``email`` and the Gemini key it synced, if any.

    No fallback to another account: when the configured one is missing, the
    caller gets an error, not answers built out of some other shop's stock.
    """
    address = (email or "").strip().lower()
    if not address:
        return None

    with SessionLocal() as session:
        user = session.execute(
            text("SELECT id, uid, email FROM users WHERE LOWER(email) = :email LIMIT 1"),
            {"email": address},
        ).first()
        if user is None:
            return None
        gemini_key = session.execute(
            text(
                """
                SELECT r.data ->> 'value'
                FROM user_records AS r
                WHERE r.user_uid = :user_uid
                  AND r.table_name = 'app_settings'
                  AND r.local_id = 'gemini_api_key'
                  AND r.deleted_at IS NULL
                LIMIT 1
                """
            ),
            {"user_uid": user[1]},
        ).scalar()

    return ManychatShop(
        user_id=user[0],
        user_uid=user[1],
        email=user[2],
        gemini_api_key=(gemini_key or "").strip() or None,
    )


class ManychatMessage(BaseModel):
    # ManyChat substitutes {{user_id}} unquoted unless the body template quotes
    # it, so a number has to be accepted as well.
    subscriber_id: str | int
    text: str | None = Field(default=None, max_length=4000)
    channel: Literal["instagram", "facebook", "telegram", "whatsapp"] = "instagram"


def dynamic_block(reply: str, channel: str) -> dict:
    """The reply in ManyChat's Dynamic Block v2 format, plus ``reply`` on its own.

    A Dynamic Block sends ``content.messages`` straight to the subscriber; an
    External Request instead maps a field into a custom field, for which the
    flat ``$.reply`` is the easy path. Messenger content carries no ``type``,
    every other channel must name itself or ManyChat rejects the block.
    """
    content: dict = {"messages": [{"type": "text", "text": reply}] if reply else []}
    if channel != "facebook":
        content["type"] = channel
    return {"version": "v2", "content": content, "reply": reply}


def _check_token(authorization: str | None) -> None:
    expected = get_settings().manychat_webhook_token
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ManyChat webhook is not configured",
        )
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token or not hmac.compare_digest(token.strip(), expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


@router.post("/webhook")
def receive_message(
    payload: ManychatMessage,
    authorization: str | None = Header(default=None),
):
    """Answer one subscriber message. A plain ``def``: the agent blocks on
    Gemini for seconds, so it runs in the threadpool, not on the event loop."""
    _check_token(authorization)

    message = (payload.text or "").strip()
    subscriber_id = str(payload.subscriber_id).strip()
    if not message or not subscriber_id:
        # A photo or sticker: nothing to answer, and nothing worth an error path.
        return dynamic_block("", payload.channel)

    email = get_settings().manychat_account_email
    try:
        shop = resolve_shop(email)
    except Exception:
        logger.exception("Failed to look up ManyChat account %s", email)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Account lookup failed")
    if shop is None:
        logger.error("ManyChat account %s does not exist; refusing to answer", email)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Account not found")

    logger.info("manychat %s message from %s: %s", payload.channel, subscriber_id, message)

    set_store_context(StoreContext(user_id=shop.user_id, user_uid=shop.user_uid, email=shop.email))
    try:
        # Prefixed so a ManyChat subscriber id can never share history with an
        # Instagram conversation id that happens to be the same digits.
        reply = reply_in_conversation(
            f"manychat:{shop.user_uid}:{subscriber_id}",
            message,
            timeout=GEMINI_TIMEOUT_SECONDS,
            api_key=shop.gemini_api_key,
        )
    except GeminiNotConfiguredError:
        logger.error("No Gemini API key for ManyChat account %s", email)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Agent is not configured")
    except Exception:
        # A non-200 sends ManyChat down the request's failure branch, where the
        # flow can hand the subscriber to a human instead of leaving them unanswered.
        logger.exception("ManyChat reply failed for subscriber %s", subscriber_id)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Agent failed to answer")
    finally:
        set_store_context(None)

    return dynamic_block(reply, payload.channel)
