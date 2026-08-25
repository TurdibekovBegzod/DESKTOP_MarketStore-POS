import re
import os
import secrets as secrets_module
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
import httpx

from app.config import get_settings
from app.database import get_db
from app.releases import (
    broadcast_release,
    fetch_latest_from_github,
    get_release,
    store_release,
)

router = APIRouter(prefix="/app", tags=["App Updates"])


def normalize_version(version_str: str) -> list[int]:
    """Parse version string like 'v1.2.3' or '1.2.3.4' into a list of integers."""
    clean = re.sub(r"^[^\d]*", "", version_str.strip())
    parts = []
    for part in clean.split("."):
        digits = re.match(r"\d+", part)
        if digits:
            parts.append(int(digits.group(0)))
        else:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return parts


def is_newer_version(latest_ver: str, current_ver: str) -> bool:
    """Returns True if latest_ver is strictly greater than current_ver."""
    v_latest = normalize_version(latest_ver)
    v_current = normalize_version(current_ver)
    return v_latest > v_current


def match_asset_for_platform(assets: list[dict], platform_name: str) -> Optional[dict]:
    """Find the best asset for the specified platform from release assets."""
    platform_name = platform_name.lower().strip()
    patterns = {
        "windows": [r"\.exe$", r"\.msi$"],
        "linux": [r"\.appimage$", r"\.deb$"],
        "macos": [r"\.dmg$", r"\.pkg$"],
    }
    target_patterns = patterns.get(platform_name, [r"\.exe$"])

    # Prefer installer/setup files if available
    for p in target_patterns:
        for asset in assets:
            name = asset.get("name", "").lower()
            if re.search(p, name) and ("setup" in name or "installer" in name):
                return asset

    # Otherwise any matching extension
    for p in target_patterns:
        for asset in assets:
            name = asset.get("name", "").lower()
            if re.search(p, name):
                return asset

    return None


def asset_sha256(asset: dict | None) -> str | None:
    digest = str((asset or {}).get("digest") or "").strip().lower()
    if digest.startswith("sha256:") and len(digest) == 71:
        checksum = digest.split(":", 1)[1]
        if re.fullmatch(r"[0-9a-f]{64}", checksum):
            return checksum
    return None


_RELEASE_CACHE = {"data": None, "timestamp": 0}
CACHE_TTL_SECONDS = 300  # 5 minutes


def _stored_release_response(db: Session, platform: str, current_version: str) -> dict | None:
    """Answer from the row the release workflow pinged us, without calling GitHub."""
    row = get_release(db)
    if row is None:
        return None
    settings = get_settings()
    asset = match_asset_for_platform(list(row.assets or []), platform)
    asset_id = asset.get("id") if asset else None
    return {
        "has_update": is_newer_version(row.version, current_version),
        "latest_version": row.version,
        "tag_name": row.tag,
        "current_version": current_version,
        "platform": platform,
        "release_name": row.name or f"Release {row.tag}",
        "release_notes": row.notes or "Yangi versiya va yaxshilanishlar.",
        "published_at": row.published_at.isoformat() if row.published_at else None,
        "download_url": (
            f"{settings.api_prefix}/app/download?platform={platform}&asset_id={asset_id}"
            if asset_id else ""
        ),
        # Public repo: this points straight at GitHub's CDN, so a client may use
        # it to skip our bandwidth entirely. Kept alongside the proxy URL so the
        # existing updater keeps working unchanged.
        "direct_download_url": (asset or {}).get("browser_download_url") or "",
        "file_name": (asset or {}).get("name", "") if asset else "",
        "file_size": int((asset or {}).get("size") or 0) if asset else 0,
        "sha256": asset_sha256(asset),
        "asset_id": asset_id,
        "source": row.source,
    }


class ReleasePing(BaseModel):
    tag: str = Field(min_length=1, max_length=80)


@router.post("/release-published")
async def release_published(
    payload: ReleasePing,
    x_release_secret: str | None = Header(default=None, alias="X-Release-Secret"),
    db: Session = Depends(get_db),
):
    """Called by the release workflow the moment a new build goes out.

    This is what makes the update badge realtime: no device and no scheduled job
    has to ask GitHub whether something changed.
    """
    settings = get_settings()
    expected = settings.release_ping_secret
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Release ping is not configured",
        )
    if not x_release_secret or not secrets_module.compare_digest(x_release_secret, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid release secret")

    tag = payload.tag.strip()
    # One optional GitHub call, purely to pick up the notes and asset list. If it
    # fails or describes a different tag, the ping itself is still authoritative.
    data = await fetch_latest_from_github()
    if data and str(data.get("tag_name") or "").strip() != tag:
        data = None

    event = await run_in_threadpool(store_release, db, tag, data, "ping")
    broadcast_release(event)
    return {"ok": True, "tag": event["tag"], "latest_version": event["latest_version"]}


@router.get("/version")
async def check_app_version(
    platform: str = Query("windows", description="Client OS: windows, linux, macos"),
    current_version: str = Query("1.0.0", description="Current installed client version"),
    db: Session = Depends(get_db),
):
    import time
    global _RELEASE_CACHE
    settings = get_settings()
    repo = settings.github_repo
    token = settings.github_token

    stored = await run_in_threadpool(_stored_release_response, db, platform, current_version)
    if stored is not None:
        return stored

    release_data = None
    github_status = None
    now = time.time()

    # Use in-memory cache if fresh
    if _RELEASE_CACHE["data"] is not None and (now - _RELEASE_CACHE["timestamp"] < CACHE_TTL_SECONDS):
        release_data = _RELEASE_CACHE["data"]
    else:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "MarketStore-Updater/1.0",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        github_url = f"https://api.github.com/repos/{repo}/releases/latest"

        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
                resp = await client.get(github_url, headers=headers)
                github_status = resp.status_code
                if resp.status_code == 200:
                    release_data = resp.json()
                    if not isinstance(release_data, dict):
                        raise ValueError("Invalid GitHub release response")
                    _RELEASE_CACHE = {"data": release_data, "timestamp": now}
                elif resp.status_code in (403, 429) and _RELEASE_CACHE["data"] is not None:
                    release_data = _RELEASE_CACHE["data"]
        except (httpx.RequestError, ValueError):
            if _RELEASE_CACHE["data"] is not None:
                release_data = _RELEASE_CACHE["data"]

    if github_status == 404:
        return {
            "has_update": False,
            "latest_version": current_version,
            "current_version": current_version,
            "platform": platform,
            "release_notes": "Hozircha yangi release chiqarilmagan.",
            "download_url": "",
            "file_name": "",
            "file_size": 0,
            "sha256": None,
        }

    if not release_data:
        # Fallback to web redirect method (no rate limits)
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
                resp = await client.get(f"https://github.com/{repo}/releases/latest")
                tag_name = str(resp.url).rstrip("/").split("/")[-1]
                latest_version = re.sub(r"^[^\d]*", "", tag_name)
                has_update = is_newer_version(latest_version, current_version)
                ext = ".exe" if platform == "windows" else (".AppImage" if platform == "linux" else ".dmg")
                return {
                    "has_update": has_update,
                    "latest_version": latest_version,
                    "tag_name": tag_name,
                    "current_version": current_version,
                    "platform": platform,
                    "release_name": f"MarketStore POS {tag_name}",
                    "release_notes": f"Yangi versiya {tag_name} chiqarildi.",
                    "published_at": None,
                    "download_url": f"https://github.com/{repo}/releases/download/{tag_name}/MarketStore_Setup_{latest_version}{ext}",
                    "file_name": f"MarketStore_Setup_{latest_version}{ext}",
                    "file_size": 0,
                    "sha256": None,
                }
        except (httpx.RequestError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=f"Versiya ma'lumotlarini olib bo'lmadi: {str(exc)}")

    tag_name = str(release_data.get("tag_name") or current_version)
    latest_version = re.sub(r"^[^\d]*", "", tag_name)
    matching_asset = match_asset_for_platform(release_data.get("assets", []), platform)
    has_update = is_newer_version(latest_version, current_version)
    asset_id = matching_asset.get("id") if matching_asset else None
    file_name = matching_asset.get("name", "") if matching_asset else ""
    file_size = int(matching_asset.get("size") or 0) if matching_asset else 0
    download_url = (
        f"{settings.api_prefix}/app/download?platform={platform}&asset_id={asset_id}"
        if asset_id else ""
    )
    return {
        "has_update": has_update,
        "latest_version": latest_version,
        "tag_name": tag_name,
        "current_version": current_version,
        "platform": platform,
        "release_name": release_data.get("name") or f"Release {tag_name}",
        "release_notes": release_data.get("body") or "Yangi versiya va yaxshilanishlar.",
        "published_at": release_data.get("published_at"),
        "download_url": download_url,
        "file_name": file_name,
        "file_size": file_size,
        "sha256": asset_sha256(matching_asset),
        "asset_id": asset_id,
    }


@router.get("/download")
async def download_app_release(
    platform: str = Query("windows"),
    asset_id: Optional[int] = Query(None),
):
    settings = get_settings()
    repo = settings.github_repo
    token = settings.github_token

    if not asset_id:
        # Fetch latest release and determine asset
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "MarketStore-Updater/1.0",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"https://api.github.com/repos/{repo}/releases/latest", headers=headers)
            if resp.status_code != 200:
                raise HTTPException(status_code=404, detail="Release topilmadi.")
            assets = resp.json().get("assets", [])
            asset = match_asset_for_platform(assets, platform)
            if not asset:
                raise HTTPException(status_code=404, detail="Ushbu platforma uchun fayl topilmadi.")
            asset_id = asset.get("id")

    asset_url = f"https://api.github.com/repos/{repo}/releases/assets/{asset_id}"
    req_headers = {
        "Accept": "application/octet-stream",
        "User-Agent": "MarketStore-Updater/1.0",
    }
    if token:
        req_headers["Authorization"] = f"Bearer {token}"

    client = httpx.AsyncClient(follow_redirects=True, timeout=60.0)
    try:
        req = client.build_request("GET", asset_url, headers=req_headers)
        res = await client.send(req, stream=True)
        if res.status_code != 200:
            await res.aclose()
            await client.aclose()
            raise HTTPException(status_code=res.status_code, detail="Faylni yuklab olishda xatolik yuz berdi.")

        async def stream_content():
            try:
                async for chunk in res.aiter_bytes(chunk_size=65536):
                    yield chunk
            finally:
                await res.aclose()
                await client.aclose()

        content_disposition = res.headers.get("content-disposition", 'attachment; filename="MarketStore_Update"')
        content_type = res.headers.get("content-type", "application/octet-stream")
        content_length = res.headers.get("content-length")

        response_headers = {
            "Content-Disposition": content_disposition,
        }
        if content_length:
            response_headers["Content-Length"] = content_length

        return StreamingResponse(
            stream_content(),
            media_type=content_type,
            headers=response_headers,
        )
    except HTTPException:
        await client.aclose()
        raise
    except (httpx.RequestError, OSError) as exc:
        await client.aclose()
        raise HTTPException(status_code=502, detail="Release faylini yuklab bo'lmadi.") from exc


@router.get("/install.sh")
async def get_install_sh():
    """Serve universal Linux & macOS bash installer script."""
    candidates = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "installer", "install.sh"),
        os.path.join("/app", "installer", "install.sh"),
        os.path.join(os.path.dirname(__file__), "install.sh"),
    ]
    for path in candidates:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return StreamingResponse(iter([f.read().encode("utf-8")]), media_type="text/plain")

    try:
        settings = get_settings()
        async with httpx.AsyncClient(follow_redirects=True, timeout=5.0) as client:
            resp = await client.get(f"https://raw.githubusercontent.com/{settings.github_repo}/main/installer/install.sh")
            if resp.status_code == 200:
                return StreamingResponse(iter([resp.content]), media_type="text/plain")
    except Exception:
        pass
    return StreamingResponse(iter([b"echo 'MarketStore POS installer script not found'; exit 1\n"]), media_type="text/plain")


@router.get("/install.ps1")
async def get_install_ps1():
    """Serve Windows PowerShell installer script."""
    candidates = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "installer", "install.ps1"),
        os.path.join("/app", "installer", "install.ps1"),
        os.path.join(os.path.dirname(__file__), "install.ps1"),
    ]
    for path in candidates:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return StreamingResponse(iter([f.read().encode("utf-8")]), media_type="text/plain")

    try:
        settings = get_settings()
        async with httpx.AsyncClient(follow_redirects=True, timeout=5.0) as client:
            resp = await client.get(f"https://raw.githubusercontent.com/{settings.github_repo}/main/installer/install.ps1")
            if resp.status_code == 200:
                return StreamingResponse(iter([resp.content]), media_type="text/plain")
    except Exception:
        pass
    return StreamingResponse(iter([b"Write-Host 'MarketStore POS installer script not found'\n"]), media_type="text/plain")
