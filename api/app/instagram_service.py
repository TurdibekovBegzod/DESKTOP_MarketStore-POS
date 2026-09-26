"""Service for multi-tenant Instagram account resolution and connection verification.

Each store's credentials (account ID, access token, app secret, auto-reply switch,
and Gemini API key) are stored in PostgreSQL under ``user_records`` with
``table_name='app_settings'``, synced from the desktop application.
"""

from dataclasses import dataclass
import hashlib
import logging
import time
from typing import Any

import httpx
from sqlalchemy import text

from app.config import get_settings
from app.database import SessionLocal


logger = logging.getLogger(__name__)

# Cache connection check results for 10 minutes to avoid hitting Meta's rate limits
_CONNECTION_CACHE: dict[str, tuple[bool, float]] = {}
CONNECTION_CACHE_TTL_SECONDS = 600.0


@dataclass
class InstagramAccountConfig:
    user_id: int
    user_uid: str
    email: str | None
    account_id: str
    access_token: str
    app_secret: str | None
    auto_reply: bool
    gemini_api_key: str | None


def get_account_config_by_id(account_id: str) -> InstagramAccountConfig | None:
    """Find a shop's credentials by its Instagram Business Account ID.

    Queries ``user_records`` for the account_id setting row, then fetches all
    other settings for that user. Falls back to .env only if account_id matches
    or if no DB record exists but .env is configured (for local dev/testing).
    """
    account_id = str(account_id or "").strip()
    if not account_id:
        return None

    try:
        with SessionLocal() as session:
            # 1. Locate the user who owns this instagram_account_id or matches email
            match_row = session.execute(
                text(
                    """
                    SELECT r.user_id, r.user_uid, u.email
                    FROM user_records AS r
                    JOIN users AS u ON u.id = r.user_id
                    WHERE r.table_name = 'app_settings'
                      AND (
                        (r.local_id = 'instagram_account_id' AND TRIM(r.data ->> 'value') = :account_id)
                        OR LOWER(u.email) = LOWER(:account_id)
                      )
                      AND r.deleted_at IS NULL
                    ORDER BY r.updated_at DESC
                    LIMIT 1
                    """
                ),
                {"account_id": account_id},
            ).first()

            # If not matched directly, check if there is a single store that configured an active
            # Instagram access token. Handles Meta Page ID vs Instagram ID mapping differences.
            if not match_row:
                single_store_rows = session.execute(
                    text(
                        """
                        SELECT DISTINCT r.user_id, r.user_uid, u.email
                        FROM user_records AS r
                        JOIN users AS u ON u.id = r.user_id
                        WHERE r.table_name = 'app_settings'
                          AND r.local_id = 'instagram_access_token'
                          AND LENGTH(TRIM(r.data ->> 'value')) > 10
                          AND r.deleted_at IS NULL
                        """
                    )
                ).all()
                if len(single_store_rows) == 1:
                    match_row = single_store_rows[0]
                    logger.info("Account %s resolved to single connected store user %s (%s)", account_id, match_row[0], match_row[2])

            if not match_row and get_settings().shop_account_email:
                shop_email = get_settings().shop_account_email.strip().lower()
                match_row = session.execute(
                    text(
                        """
                        SELECT r.user_id, r.user_uid, u.email
                        FROM user_records AS r
                        JOIN users AS u ON u.id = r.user_id
                        WHERE r.table_name = 'app_settings'
                          AND LOWER(u.email) = :email
                          AND r.deleted_at IS NULL
                        ORDER BY r.updated_at DESC
                        LIMIT 1
                        """
                    ),
                    {"email": shop_email},
                ).first()

            if match_row:
                user_id, user_uid, email = match_row

                # 2. Fetch all app_settings rows for this user
                settings_rows = session.execute(
                    text(
                        """
                        SELECT r.local_id, r.data ->> 'value'
                        FROM user_records AS r
                        WHERE r.user_uid = :user_uid
                          AND r.table_name = 'app_settings'
                          AND r.deleted_at IS NULL
                        """
                    ),
                    {"user_uid": user_uid},
                ).all()

                settings_map: dict[str, str] = {
                    row[0]: (row[1] or "").strip() for row in settings_rows if row[0]
                }

                auto_reply_val = settings_map.get("instagram_auto_reply", "1")
                auto_reply = auto_reply_val not in ("0", "false", "False", False)
                access_token = settings_map.get("instagram_access_token", "")
                resolved_account_id = settings_map.get("instagram_account_id") or account_id
                app_secret = settings_map.get("instagram_app_secret") or get_settings().instagram_app_secret
                gemini_key = settings_map.get("gemini_api_key") or getattr(get_settings(), "gemini_api_key", None)

                return InstagramAccountConfig(
                    user_id=user_id,
                    user_uid=user_uid,
                    email=email,
                    account_id=resolved_account_id,
                    access_token=access_token,
                    app_secret=app_secret,
                    auto_reply=auto_reply,
                    gemini_api_key=gemini_key,
                )
    except Exception:
        logger.exception("Failed to query Instagram account settings for account %s", account_id)

    # 3. Fallback to .env settings if DB record wasn't found but .env has credentials
    try:
        env_settings = get_settings()
        if env_settings.instagram_access_token:
            with SessionLocal() as session:
                target_email = (env_settings.shop_account_email or "").strip().lower()
                user_row = None
                if target_email:
                    user_row = session.execute(
                        text("SELECT id, uid, email FROM users WHERE LOWER(email) = :email LIMIT 1"),
                        {"email": target_email},
                    ).first()
                if not user_row:
                    user_row = session.execute(
                        text("SELECT id, uid, email FROM users ORDER BY id ASC LIMIT 1")
                    ).first()

                if user_row:
                    u_id, u_uid, u_email = user_row
                    return InstagramAccountConfig(
                        user_id=u_id,
                        user_uid=u_uid,
                        email=u_email,
                        account_id=account_id or "default",
                        access_token=env_settings.instagram_access_token,
                        app_secret=env_settings.instagram_app_secret,
                        auto_reply=env_settings.instagram_auto_reply,
                        gemini_api_key=env_settings.gemini_api_key,
                    )
    except Exception:
        pass

    return None


def verify_instagram_connection(
    access_token: str,
    account_id: str | None = None,
    timeout: float = 8.0,
) -> bool:
    """Verify with Meta Graph API that this Instagram access token is active and valid.

    Uses a 10-minute cache to avoid calling Meta on every customer DM turn.
    Returns True if valid, False otherwise.
    """
    token = str(access_token or "").strip()
    if not token:
        return False

    token_hash = hashlib.sha256(token.encode()).hexdigest()[:16]
    cache_key = f"{account_id or 'me'}:{token_hash}"

    now = time.time()
    cached = _CONNECTION_CACHE.get(cache_key)
    if cached and (now - cached[1]) < CONNECTION_CACHE_TTL_SECONDS:
        return cached[0]

    try:
        # Instagram tokens start with IG (e.g. IGAA, IGAB, IGQV). Facebook Page tokens start with EAA.
        if token.startswith("IG"):
            url = f"https://graph.instagram.com/v21.0/me?fields=id,username&access_token={token}"
        else:
            target = account_id if account_id else "me"
            url = f"https://graph.facebook.com/v21.0/{target}?fields=id,name,username&access_token={token}"

        response = httpx.get(
            url,
            headers={"User-Agent": "MarketStore-POS/1.0"},
            timeout=timeout,
        )
        if response.status_code == 200:
            data = response.json()
            if "id" in data:
                _CONNECTION_CACHE[cache_key] = (True, now)
                return True

        # Alternate endpoint fallback
        alt_url = (
            f"https://graph.facebook.com/v21.0/{account_id or 'me'}?fields=id,name,username&access_token={token}"
            if token.startswith("IG")
            else f"https://graph.instagram.com/v21.0/me?fields=id,username&access_token={token}"
        )
        alt_resp = httpx.get(alt_url, headers={"User-Agent": "MarketStore-POS/1.0"}, timeout=timeout)
        if alt_resp.status_code == 200 and "id" in alt_resp.json():
            _CONNECTION_CACHE[cache_key] = (True, now)
            return True

        logger.warning(
            "Instagram connection check returned %s: %s",
            response.status_code,
            response.text[:200],
        )
        _CONNECTION_CACHE[cache_key] = (False, now)
        return False
    except Exception as exc:
        logger.warning("Instagram connection check failed for account %s: %s", account_id, exc)
        return False


def clear_connection_cache():
    """Clear cached connection check results (used in tests)."""
    _CONNECTION_CACHE.clear()
