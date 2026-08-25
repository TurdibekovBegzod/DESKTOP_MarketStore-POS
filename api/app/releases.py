"""Latest-release bookkeeping for the desktop auto-updater.

The release workflow pings us the moment a build is published, we store it, and
every connected device learns about it over the sync event stream it is already
holding open. That keeps GitHub API usage at zero per device - the rate limit
(60 requests/hour unauthenticated, 5000 with a token) never comes into play no
matter how many shops are running the app.

A slow background poll stays as a safety net for releases published by hand, or
published while this service happened to be down.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.events import broker
from app.models import AppRelease


RELEASE_ROW_ID = 1
GITHUB_TIMEOUT_SECONDS = 10.0


def normalize_version(version_str: str) -> str:
    return re.sub(r"^[^\d]*", "", (version_str or "").strip())


def _parse_published_at(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _slim_assets(assets) -> list[dict]:
    """Keep only what the updater needs; release payloads are otherwise huge."""
    slim = []
    for asset in assets or []:
        if not isinstance(asset, dict):
            continue
        slim.append({
            "id": asset.get("id"),
            "name": asset.get("name"),
            "size": asset.get("size"),
            "digest": asset.get("digest"),
            "browser_download_url": asset.get("browser_download_url"),
        })
    return slim


def get_release(db: Session) -> AppRelease | None:
    return db.get(AppRelease, RELEASE_ROW_ID)


def release_payload(db: Session) -> dict | None:
    row = get_release(db)
    if row is None:
        return None
    return {
        "tag": row.tag,
        "latest_version": row.version,
        "name": row.name,
        "published_at": row.published_at.isoformat() if row.published_at else None,
        "source": row.source,
    }


def store_release(db: Session, tag: str, data: dict | None = None, source: str = "ping") -> dict:
    """Upsert the newest release. Returns the broadcast payload."""
    data = data or {}
    tag = str(tag or data.get("tag_name") or "").strip()
    if not tag:
        raise ValueError("Release tag is required")
    version = normalize_version(tag)
    row = get_release(db)
    if row is None:
        row = AppRelease(id=RELEASE_ROW_ID, tag=tag, version=version)
        db.add(row)
    row.tag = tag
    row.version = version
    row.name = (data.get("name") or f"MarketStore POS {tag}")[:200]
    row.notes = data.get("body") or None
    row.published_at = _parse_published_at(data.get("published_at")) or datetime.now(timezone.utc)
    row.assets = _slim_assets(data.get("assets"))
    row.source = source
    db.commit()
    db.refresh(row)
    return {
        "type": "release",
        "tag": row.tag,
        "latest_version": row.version,
        "name": row.name,
        "published_at": row.published_at.isoformat() if row.published_at else None,
        "source": row.source,
    }


def broadcast_release(payload: dict) -> None:
    broker.publish_all(payload)


async def fetch_latest_from_github() -> dict | None:
    """One GitHub API call. Used by the ping (to enrich) and by the poller."""
    settings = get_settings()
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "MarketStore-Updater/1.0",
    }
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    url = f"https://api.github.com/repos/{settings.github_repo}/releases/latest"
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=GITHUB_TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                data = response.json()
                return data if isinstance(data, dict) else None
    except (httpx.RequestError, ValueError):
        return None
    return None


async def resolve_tag_without_api(tag_hint: str | None = None) -> str | None:
    """Fall back to the public redirect, which is not part of the REST quota."""
    if tag_hint:
        return tag_hint
    settings = get_settings()
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=GITHUB_TIMEOUT_SECONDS) as client:
            response = await client.get(f"https://github.com/{settings.github_repo}/releases/latest")
            resolved = str(response.url).rstrip("/").split("/")[-1]
            return resolved or None
    except (httpx.RequestError, ValueError):
        return None
