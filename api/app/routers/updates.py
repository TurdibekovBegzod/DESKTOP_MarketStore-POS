import re
import os
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
import httpx

from app.config import get_settings

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
        "linux": [r"\.appimage$", r"\.deb$", r"\.tar\.gz$"],
        "macos": [r"\.dmg$", r"\.pkg$", r"\.zip$"],
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

    if assets:
        return assets[0]
    return None


_RELEASE_CACHE = {"data": None, "timestamp": 0}
CACHE_TTL_SECONDS = 300  # 5 minutes


@router.get("/version")
async def check_app_version(
    platform: str = Query("windows", description="Client OS: windows, linux, macos"),
    current_version: str = Query("1.0.0", description="Current installed client version"),
):
    import time
    global _RELEASE_CACHE
    settings = get_settings()
    repo = settings.github_repo
    token = settings.github_token

    release_data = None
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
                if resp.status_code == 200:
                    release_data = resp.json()
                    _RELEASE_CACHE = {"data": release_data, "timestamp": now}
                elif resp.status_code == 403 and _RELEASE_CACHE["data"] is not None:
                    # Rate limit exceeded on GitHub API -> use cached data
                    release_data = _RELEASE_CACHE["data"]
        except Exception:
            if _RELEASE_CACHE["data"] is not None:
                release_data = _RELEASE_CACHE["data"]

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
                }
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Versiya ma'lumotlarini olib bo'lmadi: {str(exc)}")

    if release_data:
        tag_name = release_data.get("tag_name", "1.0.0")
        latest_version = re.sub(r"^[^\d]*", "", tag_name)
        assets = release_data.get("assets", [])
        matching_asset = match_asset_for_platform(assets, platform)

                has_update = is_newer_version(latest_version, current_version)
                download_url = ""
                file_name = ""
                file_size = 0
                asset_id = None

                if matching_asset:
                    asset_id = matching_asset.get("id")
                    file_name = matching_asset.get("name", "")
                    file_size = matching_asset.get("size", 0)
                    download_url = f"{settings.api_prefix}/app/download?platform={platform}&asset_id={asset_id}"
                elif release_data.get("zipball_url"):
                    download_url = release_data.get("zipball_url")

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
                    "asset_id": asset_id,
                }
            elif resp.status_code == 404:
                return {
                    "has_update": False,
                    "latest_version": current_version,
                    "current_version": current_version,
                    "platform": platform,
                    "release_notes": "Hozircha yangi release chiqarilmagan.",
                    "download_url": "",
                    "file_name": "",
                    "file_size": 0,
                }
            else:
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"GitHub API returned error: {resp.text}",
                )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"GitHub API bilan bog'lanishda xatolik: {str(exc)}",
        )


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
    except Exception as exc:
        await client.aclose()
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/install.sh")
async def get_install_sh():
    """Serve universal Linux & macOS bash installer script."""
    installer_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "installer", "install.sh")
    if os.path.exists(installer_path):
        with open(installer_path, "r", encoding="utf-8") as f:
            content = f.read()
        return StreamingResponse(iter([content.encode("utf-8")]), media_type="text/plain")
    return StreamingResponse(iter([b"echo 'MarketStore POS installer script not found'; exit 1\n"]), media_type="text/plain")


@router.get("/install.ps1")
async def get_install_ps1():
    """Serve Windows PowerShell installer script."""
    installer_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "installer", "install.ps1")
    if os.path.exists(installer_path):
        with open(installer_path, "r", encoding="utf-8") as f:
            content = f.read()
        return StreamingResponse(iter([content.encode("utf-8")]), media_type="text/plain")
    return StreamingResponse(iter([b"Write-Host 'MarketStore POS installer script not found'\n"]), media_type="text/plain")

