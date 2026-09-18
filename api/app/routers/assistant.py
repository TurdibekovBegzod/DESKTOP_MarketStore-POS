"""Answer endpoint for chat platforms that call us rather than push to us.

Manychat and the like own the Instagram connection; we own the answer. Their
flow calls this with the customer's message and waits, so the reply has to come
back quickly and must never be empty - an empty string would have the platform
send a blank message, or fail the step outright.
"""

import logging
import secrets

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field

from ai.agent import reply_to
from app.config import get_settings


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["assistant"])

# The caller is holding a customer's chat open while we think, and Manychat
# gives the step its own deadline. Better a fallback line in time than a good
# answer after the platform has given up.
REPLY_TIMEOUT_SECONDS = 12.0


class ReplyRequest(BaseModel):
    text: str = Field(max_length=4000)
    contact_id: str | None = Field(default=None, max_length=120)


class ReplyOut(BaseModel):
    reply: str


@router.post("/reply", response_model=ReplyOut)
def reply(
    payload: ReplyRequest,
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
):
    settings = get_settings()
    expected = settings.assistant_api_key
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Assistant endpoint is not configured",
        )
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    try:
        answer = reply_to(payload.text, timeout=REPLY_TIMEOUT_SECONDS)
    except Exception:
        # The customer is waiting. A 500 here would leave them with nothing at
        # all, so a failed model call degrades to the handover line instead.
        logger.exception("assistant reply failed for contact %s", payload.contact_id)
        answer = ""

    return ReplyOut(reply=answer or settings.assistant_fallback_reply)
