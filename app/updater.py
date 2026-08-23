"""
Cross-Platform Auto-Updater Engine for MarketStore POS
"""

import os
import sys
import re
import time
import tempfile
import subprocess
import urllib.request
import urllib.error
import json
from PyQt6.QtCore import QThread, pyqtSignal

from version import APP_VERSION


def get_client_platform() -> str:
    """Returns 'windows', 'linux', or 'macos'."""
    if sys.platform.startswith("win"):
        return "windows"
    elif sys.platform.startswith("darwin"):
        return "macos"
    elif sys.platform.startswith("linux"):
        return "linux"
    return "windows"


def parse_version_tuple(version_str: str) -> tuple[int, ...]:
    """Parse version string into tuple of integers."""
    clean = re.sub(r"^[^\d]*", "", str(version_str).strip())
    parts = []
    for part in clean.split("."):
        digits = re.match(r"\d+", part)
        if digits:
            parts.append(int(digits.group(0)))
        else:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer_version(latest_ver: str, current_ver: str) -> bool:
    """Return True if latest_ver is newer than current_ver."""
    return parse_version_tuple(latest_ver) > parse_version_tuple(current_ver)


def get_default_api_base() -> str:
    """Read API base URL from settings or default localhost/server."""
    try:
        from database import get_app_settings
        settings = get_app_settings()
        url = settings.get("sync_server_url") or "http://localhost:8000"
        return url.rstrip("/")
    except Exception:
        return "http://localhost:8000"


def match_asset_for_platform(assets: list, platform_name: str):
    platform_name = platform_name.lower().strip()
    patterns = {
        "windows": [r"\.exe$", r"\.msi$"],
        "linux": [r"\.appimage$", r"\.deb$", r"\.tar\.gz$"],
        "macos": [r"\.dmg$", r"\.pkg$", r"\.zip$"],
    }
    target_patterns = patterns.get(platform_name, [r"\.exe$"])

    for p in target_patterns:
        for asset in assets:
            name = asset.get("name", "").lower()
            if re.search(p, name) and ("setup" in name or "installer" in name):
                return asset

    for p in target_patterns:
        for asset in assets:
            name = asset.get("name", "").lower()
            if re.search(p, name):
                return asset

    if assets:
        return assets[0]
    return None


class UpdateCheckerThread(QThread):
    """Asynchronously checks for new updates via backend API or direct GitHub."""
    update_available = pyqtSignal(dict)
    no_update_available = pyqtSignal(dict)
    check_error = pyqtSignal(str)

    def __init__(self, api_base_url: str = None, parent=None):
        super().__init__(parent)
        self.api_base_url = (api_base_url or get_default_api_base()).rstrip("/")
        self.platform = get_client_platform()
        self.current_version = APP_VERSION

    def run(self):
        # 1. Try Backend API first if reachable
        url = f"{self.api_base_url}/api/v1/app/version?platform={self.platform}&current_version={self.current_version}"
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": f"MarketStore-POS/{self.current_version}"},
            )
            with urllib.request.urlopen(req, timeout=3) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode("utf-8"))
                    if data.get("has_update"):
                        self.update_available.emit(data)
                    else:
                        self.no_update_available.emit(data)
                    return
        except Exception:
            # Backend server not running or unreachable -> Fallback to GitHub Releases directly
            pass

        # 2. Direct GitHub Releases API Fallback
        self._check_github_direct()

    def _check_github_direct(self):
        github_url = "https://api.github.com/repos/TurdibekovBegzod/MarketStore-POS/releases/latest"
        try:
            req = urllib.request.Request(
                github_url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": f"MarketStore-POS/{self.current_version}",
                },
            )
            with urllib.request.urlopen(req, timeout=8) as response:
                if response.status == 200:
                    release_data = json.loads(response.read().decode("utf-8"))
                    tag_name = release_data.get("tag_name", "1.0.0")
                    latest_version = re.sub(r"^[^\d]*", "", tag_name)
                    assets = release_data.get("assets", [])
                    matching_asset = match_asset_for_platform(assets, self.platform)

                    has_update = is_newer_version(latest_version, self.current_version)
                    download_url = ""
                    file_name = ""
                    file_size = 0

                    if matching_asset:
                        download_url = matching_asset.get("browser_download_url", "")
                        file_name = matching_asset.get("name", "")
                        file_size = matching_asset.get("size", 0)
                    elif release_data.get("zipball_url"):
                        download_url = release_data.get("zipball_url")

                    data = {
                        "has_update": has_update,
                        "latest_version": latest_version,
                        "tag_name": tag_name,
                        "current_version": self.current_version,
                        "platform": self.platform,
                        "release_name": release_data.get("name") or f"Release {tag_name}",
                        "release_notes": release_data.get("body") or "Yangi versiya va yaxshilanishlar.",
                        "published_at": release_data.get("published_at"),
                        "download_url": download_url,
                        "file_name": file_name,
                        "file_size": file_size,
                    }
                    if has_update:
                        self.update_available.emit(data)
                    else:
                        self.no_update_available.emit(data)
                elif response.status == 404:
                    self.no_update_available.emit({
                        "has_update": False,
                        "latest_version": self.current_version,
                        "current_version": self.current_version,
                        "release_notes": "Hozircha yangi release chiqarilmagan.",
                    })
                else:
                    self.check_error.emit(f"GitHub API javobi: {response.status}")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                self.no_update_available.emit({
                    "has_update": False,
                    "latest_version": self.current_version,
                    "current_version": self.current_version,
                    "release_notes": "Hozircha yangi release chiqarilmagan.",
                })
            else:
                self.check_error.emit(f"GitHub HTTP xatolik: {exc.code} {exc.reason}")
        except Exception as exc:
            self.check_error.emit(f"Yangilanishni tekshirishda xatolik: {str(exc)}")


class UpdateDownloaderThread(QThread):
    """Downloads update package with progress and speed reporting."""
    progress = pyqtSignal(int, int, float, int)  # (downloaded_bytes, total_bytes, speed_mb_s, percent)
    download_finished = pyqtSignal(str)          # local_file_path
    download_error = pyqtSignal(str)

    def __init__(self, download_url: str, file_name: str = "", api_base_url: str = None, parent=None):
        super().__init__(parent)
        self.download_url = download_url
        self.file_name = file_name or "MarketStore_Update"
        self.api_base_url = (api_base_url or get_default_api_base()).rstrip("/")
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        url = self.download_url
        if url.startswith("/"):
            url = f"{self.api_base_url}{url}"

        # Determine target file extension
        ext = ".exe" if sys.platform.startswith("win") else (".AppImage" if sys.platform.startswith("linux") else ".dmg")
        if "." in self.file_name:
            ext = os.path.splitext(self.file_name)[1]

        temp_dir = tempfile.gettempdir()
        target_path = os.path.join(temp_dir, f"marketstore_update_{int(time.time())}{ext}")

        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": f"MarketStore-POS/{APP_VERSION}"},
            )
            with urllib.request.urlopen(req, timeout=30) as response:
                total_size = int(response.headers.get("Content-Length", 0))
                downloaded = 0
                start_time = time.time()
                last_time = start_time
                last_downloaded = 0
                speed = 0.0

                with open(target_path, "wb") as out_file:
                    while not self._is_cancelled:
                        chunk = response.read(65536)
                        if not chunk:
                            break
                        out_file.write(chunk)
                        downloaded += len(chunk)
                        now = time.time()
                        if now - last_time >= 0.2:
                            elapsed = now - last_time
                            speed = ((downloaded - last_downloaded) / elapsed) / (1024 * 1024)
                            last_time = now
                            last_downloaded = downloaded
                            percent = int((downloaded / total_size * 100)) if total_size > 0 else 0
                            self.progress.emit(downloaded, total_size, speed, percent)

            if self._is_cancelled:
                try:
                    if os.path.exists(target_path):
                        os.remove(target_path)
                except Exception:
                    pass
                return

            if os.path.exists(target_path) and os.path.getsize(target_path) > 0:
                self.progress.emit(downloaded, total_size, speed, 100)
                self.download_finished.emit(target_path)
            else:
                self.download_error.emit("Yuklangan fayl bo'sh.")
        except Exception as exc:
            self.download_error.emit(f"Yuklab olishda xatolik: {str(exc)}")


def apply_and_restart(file_path: str):
    """Execute installer or update package and close current app."""
    platform_name = get_client_platform()
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Update fayli topilmadi: {file_path}")

    if platform_name == "windows":
        # Launch installer in separate process
        try:
            # Use os.startfile for Windows native launch or subprocess
            if hasattr(os, "startfile"):
                os.startfile(file_path)
            else:
                subprocess.Popen([file_path], shell=True)
        except Exception as exc:
            subprocess.Popen([file_path], shell=True)
    elif platform_name == "linux":
        try:
            os.chmod(file_path, 0o755)
            subprocess.Popen([file_path])
        except Exception as exc:
            subprocess.Popen(["xdg-open", file_path])
    elif platform_name == "macos":
        subprocess.Popen(["open", file_path])

    # Exit application cleanly
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app:
        app.quit()
    sys.exit(0)
