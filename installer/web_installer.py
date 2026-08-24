import sys
import os
import re
import json
import time
import shutil
import zipfile
import tempfile
import urllib.request
import subprocess

from PyQt6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QProgressBar, QFrame, QMessageBox, QGraphicsDropShadowEffect
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QPixmap

from ssl_support import create_ssl_context

API_BASE = "http://169.58.152.33:8000"
GITHUB_API = "https://api.github.com/repos/TurdibekovBegzod/DESKTOP_MarketStore-POS/releases/latest"
APP_NAME = "MarketStore POS"


def get_current_platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    elif sys.platform.startswith("darwin"):
        return "macos"
    return "linux"


class InstallWorker(QThread):
    status_changed = pyqtSignal(str)
    progress_changed = pyqtSignal(int, str)  # percent, detail_text
    install_finished = pyqtSignal(bool, str) # success, message

    def __init__(self, parent=None):
        super().__init__(parent)
        self.platform = get_current_platform()

    def run(self):
        try:
            self.status_changed.emit("Eng so'nggi versiya ma'lumotlari olinmoqda...")
            self.progress_changed.emit(10, "Serverga ulanmoqda...")

            version_info = self._fetch_version_info()
            download_url = version_info.get("download_url")
            version_str = version_info.get("latest_version", "1.0.0")

            if not download_url:
                raise Exception("Ushbu operatsion tizim uchun yuklab olish havolasi topilmadi.")

            self.status_changed.emit(f"MarketStore POS v{version_str} yuklab olinmoqda...")
            local_file = self._download_file(download_url, version_info.get("file_size", 0))

            self.status_changed.emit("Dastur tizimga o'rnatilmoqda...")
            self.progress_changed.emit(85, "Fayllar joylashtirilmoqda...")

            installed_exe = self._install_app(local_file)

            self.progress_changed.emit(100, "O'rnatish yakunlandi!")
            self.install_finished.emit(True, installed_exe)

        except Exception as exc:
            self.install_finished.emit(False, str(exc))

    def _fetch_version_info(self) -> dict:
        # 1. Try Backend API
        api_url = f"{API_BASE}/api/v1/app/version?platform={self.platform}&current_version=0.0.0"
        try:
            req = urllib.request.Request(api_url, headers={"User-Agent": "MarketStore-Installer"})
            with urllib.request.urlopen(req, timeout=5, context=create_ssl_context()) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    if data.get("download_url"):
                        if data["download_url"].startswith("/"):
                            data["download_url"] = f"{API_BASE}{data['download_url']}"
                        return data
        except Exception:
            pass

        # 2. Fallback to GitHub directly
        req = urllib.request.Request(
            GITHUB_API,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "MarketStore-Installer"}
        )
        with urllib.request.urlopen(req, timeout=8, context=create_ssl_context()) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                tag = data.get("tag_name", "1.0.0")
                ver = re.sub(r"^[^\d]*", "", tag)
                assets = data.get("assets", [])

                # Match asset
                patterns = {
                    "windows": [r"\.exe$", r"\.msi$"],
                    "linux": [r"\.appimage$", r"\.deb$"],
                    "macos": [r"\.dmg$", r"\.zip$"],
                }
                target_p = patterns.get(self.platform, [r"\.exe$"])

                chosen_asset = None
                for p in target_p:
                    for a in assets:
                        if re.search(p, a.get("name", "").lower()):
                            chosen_asset = a
                            break
                    if chosen_asset:
                        break

                dl_url = chosen_asset.get("browser_download_url") if chosen_asset else data.get("zipball_url")
                size = chosen_asset.get("size", 0) if chosen_asset else 0

                return {
                    "latest_version": ver,
                    "download_url": dl_url,
                    "file_size": size,
                    "file_name": chosen_asset.get("name", "") if chosen_asset else "marketstore_update"
                }

        raise Exception("Serverdan yoki GitHub'dan versiya ma'lumotlarini olib bo'lmadi.")

    def _download_file(self, url: str, expected_size: int) -> str:
        temp_dir = tempfile.gettempdir()
        ext = ".exe" if self.platform == "windows" else (".AppImage" if self.platform == "linux" else ".dmg")
        if ".zip" in url:
            ext = ".zip"

        target_file = os.path.join(temp_dir, f"marketstore_install_{int(time.time())}{ext}")

        req = urllib.request.Request(url, headers={"User-Agent": "MarketStore-Installer"})
        start_time = time.time()

        with urllib.request.urlopen(req, timeout=30, context=create_ssl_context()) as resp, open(target_file, "wb") as out:
            total_bytes = int(resp.headers.get("Content-Length", expected_size or 0))
            downloaded = 0
            chunk_size = 1024 * 64

            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                out.write(chunk)
                downloaded += len(chunk)

                if total_bytes > 0:
                    percent = int((downloaded / total_bytes) * 70) + 10  # 10% to 80%
                    elapsed = max(time.time() - start_time, 0.1)
                    speed_mb = (downloaded / (1024 * 1024)) / elapsed
                    detail = f"{downloaded // (1024*1024)} MB / {total_bytes // (1024*1024)} MB ({speed_mb:.1f} MB/s)"
                    self.progress_changed.emit(percent, detail)
                else:
                    self.progress_changed.emit(50, f"{downloaded // (1024*1024)} MB yuklandi...")

        return target_file

    def _install_app(self, download_file: str) -> str:
        if self.platform == "windows":
            if download_file.endswith(".exe"):
                # If it is a full NSIS Setup exe, run it
                return download_file
            elif download_file.endswith(".zip"):
                # Extract zip to %LOCALAPPDATA%\MarketStore-POS
                dest = os.path.join(os.environ.get("LOCALAPPDATA", "C:\\"), "MarketStore-POS")
                os.makedirs(dest, exist_ok=True)
                with zipfile.ZipFile(download_file, "r") as z:
                    z.extractall(dest)
                exe_path = os.path.join(dest, "MarketStore-POS.exe")
                return exe_path
            return download_file

        elif self.platform == "linux":
            dest_dir = os.path.expanduser("~/.local/bin")
            os.makedirs(dest_dir, exist_ok=True)
            app_dest = os.path.join(dest_dir, "MarketStore-POS.AppImage")
            shutil.copy2(download_file, app_dest)
            os.chmod(app_dest, 0o755)

            # Create .desktop file
            apps_dir = os.path.expanduser("~/.local/share/applications")
            os.makedirs(apps_dir, exist_ok=True)
            desktop_file = os.path.join(apps_dir, "marketstore.desktop")
            with open(desktop_file, "w", encoding="utf-8") as f:
                f.write(f"""[Desktop Entry]
Name=MarketStore POS
Exec={app_dest}
Icon=utilities-terminal
Type=Application
Categories=Office;Finance;
""")
            return app_dest

        elif self.platform == "macos":
            # Mount DMG and copy .app to /Applications
            mount_point = tempfile.mkdtemp()
            subprocess.run(["hdiutil", "attach", download_file, "-mountpoint", mount_point], check=True)
            try:
                for item in os.listdir(mount_point):
                    if item.endswith(".app"):
                        src = os.path.join(mount_point, item)
                        dst = os.path.join("/Applications", item)
                        if os.path.exists(dst):
                            shutil.rmtree(dst)
                        shutil.copytree(src, dst)
                        return dst
            finally:
                subprocess.run(["hdiutil", "detach", mount_point], check=False)
            return "/Applications/MarketStore POS.app"

        return download_file


class WebInstallerWindow(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MarketStore POS — O'rnatish Ustasi")
        self.setFixedSize(460, 320)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowCloseButtonHint)
        self.setStyleSheet("""
            QDialog {
                background-color: #ffffff;
                font-family: 'Segoe UI', Arial, sans-serif;
            }
            QProgressBar {
                border: 1px solid #e2e8f0;
                border-radius: 6px;
                background-color: #f8fafc;
                text-align: center;
                height: 18px;
                color: #0f172a;
                font-weight: bold;
                font-size: 11px;
            }
            QProgressBar::chunk {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2563eb, stop:1 #3b82f6);
                border-radius: 5px;
            }
            QPushButton#installBtn {
                background-color: #2563eb;
                color: white;
                font-weight: bold;
                font-size: 13px;
                padding: 10px 20px;
                border-radius: 8px;
                border: none;
            }
            QPushButton#installBtn:hover {
                background-color: #1d4ed8;
            }
            QPushButton#installBtn:pressed {
                background-color: #1e40af;
            }
            QPushButton#installBtn:disabled {
                background-color: #94a3b8;
            }
            QPushButton#closeBtn {
                background-color: #f1f5f9;
                color: #475569;
                font-size: 13px;
                padding: 10px 18px;
                border-radius: 8px;
                border: 1px solid #cbd5e1;
            }
            QPushButton#closeBtn:hover {
                background-color: #e2e8f0;
            }
        """)

        self._installed_target = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        # Header with icon and title
        header = QHBoxLayout()
        header.setSpacing(14)

        icon_box = QLabel("🛒")
        icon_box.setStyleSheet("font-size: 38px;")
        header.addWidget(icon_box)

        title_layout = QVBoxLayout()
        title = QLabel("MarketStore POS")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #0f172a;")
        sub = QLabel("Savdo va Ombor Boshqaruvi Dasturi")
        sub.setStyleSheet("font-size: 12px; color: #64748b;")
        title_layout.addWidget(title)
        title_layout.addWidget(sub)
        header.addLayout(title_layout)
        header.addStretch()

        layout.addLayout(header)

        # Divider
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #f1f5f9; background-color: #f1f5f9;")
        layout.addWidget(line)

        # Status and Details
        self.status_lbl = QLabel("MarketStore POS dasturini o'rnatishga tayyor.")
        self.status_lbl.setStyleSheet("font-size: 13px; font-weight: 600; color: #334155;")
        self.status_lbl.setWordWrap(True)
        layout.addWidget(self.status_lbl)

        self.detail_lbl = QLabel(f"Tizim: {get_current_platform().upper()} | Avtomatik yuklab o'rnatish")
        self.detail_lbl.setStyleSheet("font-size: 11px; color: #64748b;")
        layout.addWidget(self.detail_lbl)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        layout.addStretch()

        # Action Buttons
        btn_layout = QHBoxLayout()
        self.close_btn = QPushButton("Bekor qilish")
        self.close_btn.setObjectName("closeBtn")
        self.close_btn.clicked.connect(self.close)

        self.install_btn = QPushButton("🚀 O'rnatishni boshlash")
        self.install_btn.setObjectName("installBtn")
        self.install_btn.clicked.connect(self._start_install)

        btn_layout.addWidget(self.close_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(self.install_btn)
        layout.addLayout(btn_layout)

    def _start_install(self):
        self.install_btn.setEnabled(False)
        self.close_btn.setEnabled(False)
        self.progress_bar.show()
        self.progress_bar.setValue(5)

        self.worker = InstallWorker(self)
        self.worker.status_changed.connect(self.status_lbl.setText)
        self.worker.progress_changed.connect(self._on_progress)
        self.worker.install_finished.connect(self._on_finished)
        self.worker.start()

    def _on_progress(self, percent: int, detail: str):
        self.progress_bar.setValue(percent)
        self.detail_lbl.setText(detail)

    def _on_finished(self, success: bool, msg: str):
        self.close_btn.setEnabled(True)
        if success:
            self._installed_target = msg
            self.status_lbl.setText("🎉 O'rnatish muvaffaqiyatli yakunlandi!")
            self.detail_lbl.setText("Dasturni hoziroq ishga tushirishingiz mumkin.")
            self.progress_bar.setValue(100)
            self.install_btn.setText("▶️ Dasturni ochish")
            self.install_btn.setEnabled(True)
            self.install_btn.clicked.disconnect()
            self.install_btn.clicked.connect(self._launch_app)
        else:
            self.status_lbl.setText("❌ O'rnatishda xatolik yuz berdi:")
            self.detail_lbl.setText(msg)
            self.install_btn.setText("Qayta urinish")
            self.install_btn.setEnabled(True)
            self.install_btn.clicked.disconnect()
            self.install_btn.clicked.connect(self._start_install)

    def _launch_app(self):
        if self._installed_target:
            if sys.platform.startswith("win"):
                os.startfile(self._installed_target)
            elif sys.platform.startswith("darwin"):
                subprocess.Popen(["open", self._installed_target])
            else:
                subprocess.Popen([self._installed_target])
        self.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = WebInstallerWindow()
    window.show()
    sys.exit(app.exec())
