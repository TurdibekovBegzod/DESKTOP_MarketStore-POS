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

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field

from app.config import get_settings
from app.deps import get_current_user
from app.instagram_service import find_conflicting_owner, get_account_config_by_id
from app.models import User
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

    account_id = str(event.get("account_id") or "").strip()
    config = get_account_config_by_id(account_id) if account_id else None

    if config is not None:
        if not config.auto_reply:
            logger.info("Auto-reply disabled for account %s; dropping event", account_id)
            return
        if not config.access_token:
            logger.warning("No access token for account %s; dropping event", account_id)
            return
    elif not get_settings().instagram_auto_reply:
        return

    # Queued, not awaited: see reply_to_instagram_dm_task.
    reply_to_instagram_dm_task.delay(account_id, event["sender_id"], event["text"])


class AccountClaimRequest(BaseModel):
    account_id: str = Field(min_length=1, max_length=64)


class AccountClaimResponse(BaseModel):
    available: bool
    owner_email: str | None = None


def _mask_email(email: str | None) -> str | None:
    """Hide most of another shop's address while still making it recognisable.

    The desktop app shows this to whoever hit the conflict, and that person is
    not necessarily entitled to the other owner's full address.
    """
    address = (email or "").strip()
    if "@" not in address:
        return None
    name, domain = address.rsplit("@", 1)
    if len(name) <= 2:
        hidden = name[:1] + "*"
    else:
        hidden = f"{name[:2]}{'*' * (len(name) - 2)}"
    return f"{hidden}@{domain}"


@router.post("/account/claim-check", response_model=AccountClaimResponse)
def claim_check(
    payload: AccountClaimRequest,
    current_user: User = Depends(get_current_user),
):
    """Ask whether this Instagram account may be connected to the caller's shop.

    One Instagram Business Account belongs to exactly one shop. Re-saving the
    same ID from the shop that already holds it is fine -- that is a token
    rotation -- but a second shop claiming it is refused here, before the
    credentials are ever written locally or synced.
    """
    account_id = payload.account_id.strip()
    if not account_id:
        raise HTTPException(status_code=422, detail="account_id is required")

    owner = find_conflicting_owner(account_id, current_user.uid)
    if owner is None:
        return AccountClaimResponse(available=True)
    return AccountClaimResponse(available=False, owner_email=_mask_email(owner.email))


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
    raw_body = await request.body()
    try:
        payload = json.loads(raw_body)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed payload")

    # Determine secret to verify signature:
    # 1. Look up account_id in payload entry to see if that account has its own app_secret
    account_secret: str | None = None
    entries = payload.get("entry") or []
    if entries and isinstance(entries, list):
        first_id = str(entries[0].get("id") or "")
        if first_id:
            cfg = get_account_config_by_id(first_id)
            if cfg and cfg.app_secret:
                account_secret = cfg.app_secret

    signature_header = request.headers.get("X-Hub-Signature-256")
    global_secret = get_settings().instagram_app_secret

    verified = False
    if account_secret and verify_signature(raw_body, signature_header, account_secret):
        verified = True
    elif global_secret and verify_signature(raw_body, signature_header, global_secret):
        verified = True

    if not verified:
        if not account_secret and not global_secret:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Instagram webhook is not configured",
            )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    for event in extract_events(payload):
        try:
            handle_event(event)
        except Exception:
            # Anything other than a 200 makes Meta redeliver the whole batch and,
            # after enough failures in a row, switch the subscription off. One bad
            # event must not cost us the others or the subscription.
            logger.exception("instagram event failed: %s", event)

    return {"ok": True}
