"""Outgoing side of the Instagram integration - sending a DM back.

The webhook router only receives. This is the other direction, and it needs the
account's own access token: the one generated under "Generate access tokens" in
the app dashboard.
"""

import httpx

from app.config import get_settings


class InstagramNotConfiguredError(RuntimeError):
    """No access token. Retrying cannot fix it, so the task must not retry."""


def send_message(recipient_id: str, text: str, timeout: float = 15.0) -> None:
    """Send a direct message as the connected business account.

    Meta only allows this within 24 hours of the customer's own message, which
    is exactly the case the webhook puts us in.
    """
    settings = get_settings()
    if not settings.instagram_access_token:
        raise InstagramNotConfiguredError("INSTAGRAM_ACCESS_TOKEN is not set")

    response = httpx.post(
        f"{settings.instagram_graph_url}/me/messages",
        headers={"Authorization": f"Bearer {settings.instagram_access_token}"},
        json={"recipient": {"id": recipient_id}, "message": {"text": text}},
        timeout=timeout,
    )
    response.raise_for_status()
