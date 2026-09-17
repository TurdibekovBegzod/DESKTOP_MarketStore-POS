"""Instagram webhook callback.

Meta calls this one URL in two different ways. Once with a GET, while you are
saving the callback in the app dashboard: it wants its challenge echoed back
before it will accept the URL at all. After that, with a POST for every message
or comment the connected business account receives.
"""

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from app.config import get_settings
from app.tasks import reply_to_instagram_dm_task


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/instagram", tags=["instagram"])


def verify_signature(raw_body: bytes, header: str | None, app_secret: str) -> bool:
    """True when the body really came from Meta.

    The digest covers the bytes exactly as they arrived, so the body has to be
    read before any JSON parsing - re-serialising it changes the hash and every
    delivery would look forged.
    """
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header[len("sha256=") :], expected)


def extract_events(payload: dict) -> list[dict]:
    """Flatten Meta's entry/messaging/changes envelope into plain events."""
    if payload.get("object") != "instagram":
        return []

    events: list[dict] = []
    for entry in payload.get("entry") or []:
        account_id = str(entry.get("id") or "")

        for item in entry.get("messaging") or []:
            message = item.get("message") or {}
            if message.get("is_echo"):
                # Our own reply coming back to us; acting on it would loop.
                continue
            events.append(
                {
                    "type": "message",
                    "account_id": account_id,
                    "sender_id": str((item.get("sender") or {}).get("id") or ""),
                    "message_id": message.get("mid"),
                    "text": message.get("text"),
                }
            )

        for change in entry.get("changes") or []:
            if change.get("field") != "comments":
                continue
            value = change.get("value") or {}
            events.append(
                {
                    "type": "comment",
                    "account_id": account_id,
                    "sender_id": str((value.get("from") or {}).get("id") or ""),
                    "comment_id": value.get("id"),
                    "media_id": str((value.get("media") or {}).get("id") or ""),
                    "text": value.get("text"),
                }
            )

    return events


def handle_event(event: dict) -> None:
    """Hand a customer DM to the agent. Comments are received but not answered."""
    logger.info(
        "instagram %s on %s from %s: %s",
        event["type"],
        event["account_id"],
        event["sender_id"],
        event.get("text"),
    )
    if event["type"] != "message" or not event.get("text"):
        return
    if event["sender_id"] == event["account_id"]:
        # The account talking to itself; is_echo already covers most of these.
        return
    if not get_settings().instagram_auto_reply:
        return
    # Queued, not awaited: see reply_to_instagram_dm_task.
    reply_to_instagram_dm_task.delay(event["sender_id"], event["text"])


@router.get("/webhook")
def verify_webhook(
    mode: str | None = Query(default=None, alias="hub.mode"),
    token: str | None = Query(default=None, alias="hub.verify_token"),
    challenge: str | None = Query(default=None, alias="hub.challenge"),
):
    """Dashboard handshake. The challenge must come back as bare text: a JSON
    string with quotes around it is what Meta reads as a failed callback."""
    expected = get_settings().instagram_verify_token
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Instagram webhook is not configured",
        )
    if mode != "subscribe" or not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Verification failed")
    return Response(content=challenge or "", media_type="text/plain")


@router.post("/webhook")
async def receive_webhook(request: Request):
    secret = get_settings().instagram_app_secret
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Instagram webhook is not configured",
        )

    raw_body = await request.body()
    if not verify_signature(raw_body, request.headers.get("X-Hub-Signature-256"), secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    try:
        payload = json.loads(raw_body)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed payload")

    for event in extract_events(payload):
        try:
            handle_event(event)
        except Exception:
            # Anything other than a 200 makes Meta redeliver the whole batch and,
            # after enough failures in a row, switch the subscription off. One bad
            # event must not cost us the others or the subscription.
            logger.exception("instagram event failed: %s", event)

    return {"ok": True}
