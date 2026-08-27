"""
Telegram-style Auto-Updater Dialog for MarketStore POS
"""

import os
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QTextEdit, QFrame, QSizePolicy, QMessageBox,
    QApplication,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QColor

from version import APP_VERSION, APP_NAME
from updater import UpdateCheckerThread, UpdateDownloaderThread, apply_and_restart
from ui.i18n import t


class UpdaterDialog(QDialog):
    def __init__(self, parent=None, auto_start_check=True, update_data=None, language=None):
        super().__init__(parent)
        parent_settings = getattr(parent, "settings", {}) if parent else {}
        self.language = language or parent_settings.get("language") or (parent.property("app_language") if parent else None) or "uz"
        self.setProperty("app_language", self.language)
        self.setWindowTitle(self._tr("Dastur yangilanishi"))
        self.setFixedSize(480, 520)
        self.setModal(True)

        self.update_data = update_data
        self.downloaded_file = None
        self.checker_thread = None
        self.downloader_thread = None

        self._build_ui()
        self._apply_styles()

        if self.update_data:
            self._show_update_available(self.update_data)
        elif auto_start_check:
            self._start_check()

    def _tr(self, text):
        return t(text, self.language)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Header Card
        header = QFrame()
        header.setObjectName("headerCard")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 14, 14, 14)
        header_layout.setSpacing(14)

        icon_lbl = QLabel("🚀")
        icon_lbl.setStyleSheet("font-size: 32px;")
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(icon_lbl)

        title_col = QVBoxLayout()
        title_col.setSpacing(4)
        self.app_title_lbl = QLabel(APP_NAME)
        self.app_title_lbl.setStyleSheet("font-size: 17px; font-weight: bold; color: #0f172a;")
        self.version_info_lbl = QLabel(f"{self._tr('Joriy versiya')}: v{APP_VERSION}")
        self.version_info_lbl.setStyleSheet("font-size: 13px; color: #64748b;")
        title_col.addWidget(self.app_title_lbl)
        title_col.addWidget(self.version_info_lbl)
        header_layout.addLayout(title_col, 1)

        layout.addWidget(header)

        # Status & Message Badge
        self.status_badge = QLabel(self._tr("Yangilanishlar tekshirilmoqda..."))
        self.status_badge.setObjectName("statusBadge")
        self.status_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_badge)

        # Release Notes Box
        self.notes_label = QLabel(self._tr("Yangi versiyadagi o'zgarishlar:"))
        self.notes_label.setStyleSheet("font-size: 13px; font-weight: bold; color: #334155;")
        layout.addWidget(self.notes_label)

        self.notes_edit = QTextEdit()
        self.notes_edit.setObjectName("notesEdit")
        self.notes_edit.setReadOnly(True)
        self.notes_edit.setPlaceholderText("O'zgarishlar izohi...")
        layout.addWidget(self.notes_edit, 1)

        # Progress Area
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("progressBar")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.progress_detail_lbl = QLabel("")
        self.progress_detail_lbl.setStyleSheet("font-size: 12px; color: #64748b;")
        self.progress_detail_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # The dialog has a fixed width, so anything longer than the download
        # counter this used to hold is cut off at both ends without this.
        self.progress_detail_lbl.setWordWrap(True)
        self.progress_detail_lbl.setVisible(False)
        layout.addWidget(self.progress_detail_lbl)

        # Action Buttons
        button_row = QHBoxLayout()
        button_row.setSpacing(10)

        self.secondary_btn = QPushButton(self._tr("Bekor qilish"))
        self.secondary_btn.setObjectName("secondaryBtn")
        self.secondary_btn.setFixedHeight(38)
        self.secondary_btn.clicked.connect(self._on_secondary_clicked)
        button_row.addWidget(self.secondary_btn)

        self.primary_btn = QPushButton(self._tr("Qayta tekshirish"))
        self.primary_btn.setObjectName("primaryBtn")
        self.primary_btn.setFixedHeight(38)
        self.primary_btn.clicked.connect(self._on_primary_clicked)
        button_row.addWidget(self.primary_btn, 1)

        layout.addLayout(button_row)

    def _apply_styles(self):
        self.setStyleSheet("""
            QDialog {
                background: #ffffff;
                font-family: 'Segoe UI', Arial, sans-serif;
            }
            QFrame#headerCard {
                background: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
            }
            QLabel#statusBadge {
                background: #eff6ff;
                color: #1d4ed8;
                border: 1px solid #bfdbfe;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 13px;
                font-weight: bold;
            }
            QTextEdit#notesEdit {
                background: #f8fafc;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 10px;
                font-size: 13px;
                color: #1e293b;
            }
            QProgressBar#progressBar {
                background: #e2e8f0;
                border: none;
                border-radius: 4px;
            }
            QProgressBar#progressBar::chunk {
                background: #3b82f6;
                border-radius: 4px;
            }
            QPushButton#primaryBtn {
                background: #2563eb;
                color: white;
                border: none;
                border-radius: 6px;
                font-size: 13px;
                font-weight: bold;
                padding: 0 16px;
            }
            QPushButton#primaryBtn:hover {
                background: #1d4ed8;
            }
            QPushButton#primaryBtn:pressed {
                background: #1e40af;
            }
            QPushButton#secondaryBtn {
                background: #ffffff;
                color: #475569;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                font-size: 13px;
                font-weight: 500;
                padding: 0 16px;
            }
            QPushButton#secondaryBtn:hover {
                background: #f1f5f9;
                color: #1e293b;
            }
        """)

    def _start_check(self):
        self.status_badge.setText(f"⏳ {self._tr('Yangilanishlar tekshirilmoqda...')}")
        self.status_badge.setStyleSheet("background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;")
        self.notes_edit.setText(self._tr("Serverdan ma'lumot yuklanmoqda..."))
        self.primary_btn.setEnabled(False)
        self.secondary_btn.setText(self._tr("Yopish"))

        self.checker_thread = UpdateCheckerThread(parent=QApplication.instance())
        self.checker_thread.update_available.connect(self._show_update_available)
        self.checker_thread.no_update_available.connect(self._show_up_to_date)
        self.checker_thread.check_error.connect(self._show_check_error)
        self.checker_thread.start()

    def _show_update_available(self, data):
        self.update_data = data
        latest = data.get("latest_version") or data.get("tag_name") or "Yangi"
        self.status_badge.setText(f"🎉 {self._tr('Yangi versiya mavjud')}: v{latest}")
        self.status_badge.setStyleSheet("background:#ecfdf5;color:#047857;border:1px solid #a7f3d0;")

        notes = data.get("release_notes") or "Yangi imkoniyatlar va yaxshilanishlar kiritildi."
        self.notes_edit.setText(notes)

        file_size = data.get("file_size", 0)
        size_str = f" ({file_size / (1024 * 1024):.1f} MB)" if file_size > 0 else ""

        self.primary_btn.setText(f"{self._tr('Yuklab olish va yangilash')}{size_str}")
        self.primary_btn.setStyleSheet("""
            QPushButton#primaryBtn {
                background: #10b981; color: white; border: none; border-radius: 6px;
                font-size: 13px; font-weight: bold;
            }
            QPushButton#primaryBtn:hover { background: #059669; }
        """)
        self.primary_btn.setEnabled(True)
        self.secondary_btn.setText(self._tr("Keyinroq"))

    def _show_up_to_date(self, data):
        self.status_badge.setText("✅ " + self._tr("Sizda eng so'nggi versiya o'rnatilgan"))
        self.status_badge.setStyleSheet("background:#f0fdf4;color:#15803d;border:1px solid #bbf7d0;")
        latest_text = {
            "uz": f"MarketStore POS v{APP_VERSION} eng yangi versiya hisoblanadi.",
            "en": f"MarketStore POS v{APP_VERSION} is the latest version.",
            "ru": f"MarketStore POS v{APP_VERSION} является последней версией.",
        }.get(self.language, f"MarketStore POS v{APP_VERSION} eng yangi versiya hisoblanadi.")
        self.notes_edit.setText(f"{latest_text}\n{self._tr('Hozirda yangilanishlar mavjud emas.')}")
        self.primary_btn.setText(self._tr("Qayta tekshirish"))
        self.primary_btn.setEnabled(True)
        self.secondary_btn.setText(self._tr("Yopish"))

    def _show_check_error(self, err_msg):
        self.status_badge.setText("⚠️ " + self._tr("Yangilanishni tekshirib bo'lmadi"))
        self.status_badge.setStyleSheet("background:#fef2f2;color:#b91c1c;border:1px solid #fecaca;")
        self.notes_edit.setText(
            f"{self._tr('Xatolik tafsiloti')}:\n{err_msg}\n\n"
            f"{self._tr('Iltimos internet aloqasini yoki server holatini tekshiring.')}"
        )
        self.primary_btn.setText(self._tr("Qayta urinish"))
        self.primary_btn.setEnabled(True)
        self.secondary_btn.setText(self._tr("Yopish"))

    def _start_download(self):
        download_url = self.update_data.get("download_url")
        if not download_url:
            QMessageBox.warning(self, self._tr("Xatolik"), self._tr("Yuklab olish havolasi topilmadi."))
            return

        self.status_badge.setText(f"⬇️ {self._tr('Yangi versiya yuklab olinmoqda...')}")
        self.status_badge.setStyleSheet("background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;")
        self.progress_bar.setVisible(True)
        self.progress_detail_lbl.setVisible(True)
        self.primary_btn.setEnabled(False)
        self.secondary_btn.setText(self._tr("Bekor qilish"))

        file_name = self.update_data.get("file_name", "")
        self.downloader_thread = UpdateDownloaderThread(
            download_url,
            file_name,
            expected_size=self.update_data.get("file_size", 0),
            expected_sha256=self.update_data.get("sha256", ""),
            parent=QApplication.instance(),
        )
        self.downloader_thread.progress.connect(self._on_download_progress)
        self.downloader_thread.download_finished.connect(self._on_download_finished)
        self.downloader_thread.download_error.connect(self._on_download_error)
        self.downloader_thread.start()

    def _on_download_progress(self, downloaded, total, speed, percent):
        self.progress_bar.setValue(percent)
        dl_mb = downloaded / (1024 * 1024)
        if total > 0:
            tot_mb = total / (1024 * 1024)
            self.progress_detail_lbl.setText(f"{dl_mb:.1f} MB / {tot_mb:.1f} MB ({percent}%) — {speed:.1f} MB/s")
        else:
            self.progress_detail_lbl.setText(f"{dl_mb:.1f} MB {self._tr('yuklandi')} — {speed:.1f} MB/s")

    def _on_download_finished(self, local_path):
        self.downloaded_file = local_path
        self.status_badge.setText(f"✅ {self._tr('Yangilanish yuklab olindi!')}")
        self.status_badge.setStyleSheet("background:#ecfdf5;color:#047857;border:1px solid #a7f3d0;")
        self.progress_bar.setValue(100)
        self.progress_detail_lbl.setText(self._tr("O'rnatish uchun tayyor."))

        self.primary_btn.setText(self._tr("Dasturni qayta ishga tushirish"))
        self.primary_btn.setStyleSheet("""
            QPushButton#primaryBtn {
                background: #059669; color: white; border: none; border-radius: 6px;
                font-size: 13px; font-weight: bold;
            }
            QPushButton#primaryBtn:hover { background: #047857; }
        """)
        self.primary_btn.setEnabled(True)
        self.secondary_btn.setText(self._tr("Keyinroq o'rnatish"))

    def _on_download_error(self, err_msg):
        self.status_badge.setText(f"❌ {self._tr('Yuklab olishda xatolik yuz berdi')}")
        self.status_badge.setStyleSheet("background:#fef2f2;color:#b91c1c;border:1px solid #fecaca;")
        self.progress_detail_lbl.setText(err_msg)
        self.primary_btn.setText(self._tr("Qayta yuklash"))
        self.primary_btn.setEnabled(True)
        self.secondary_btn.setText(self._tr("Yopish"))

    def _on_primary_clicked(self):
        if self.downloaded_file and os.path.exists(self.downloaded_file):
            try:
                apply_and_restart(self.downloaded_file)
            except (OSError, ValueError) as exc:
                self._on_download_error(str(exc))
                QMessageBox.warning(self, self._tr("Xatolik"), str(exc))
            else:
                # The application does not close itself any more: the installer
                # does that when it is ready. Say so, and stop the button from
                # starting a second installer on top of the first.
                self._show_installer_started()
        elif self.update_data and self.update_data.get("has_update"):
            self._start_download()
        else:
            self._start_check()

    def _show_installer_started(self):
        opened = self._tr("O\u2019rnatuvchi ochildi")
        self.status_badge.setText("\u2705 " + opened)
        self.status_badge.setStyleSheet(
            "background:#ecfdf5;color:#047857;border:1px solid #a7f3d0;"
        )
        self.progress_detail_lbl.setText(self._tr(
            "Windows ruxsat so\u2019rasa \u201cHa\u201d deng.\n"
            "Dastur o\u2019zi yopiladi \u2014 qo\u2019lda yopmang."
        ))
        # The instruction is the whole point of this state, so it is shown
        # whether or not a download made the row visible earlier.
        self.progress_detail_lbl.setVisible(True)
        self.progress_bar.setVisible(False)
        # A disabled button keeps its stylesheet, so without a grey one of its
        # own it still looks pressable and invites a second installer.
        self.primary_btn.setText(self._tr("O\u2019rnatuvchi ochildi"))
        self.primary_btn.setEnabled(False)
        self.primary_btn.setStyleSheet("""
            QPushButton { background: #e2e8f0; color: #94a3b8; border: none;
                          border-radius: 10px; padding: 12px 20px;
                          font-size: 14px; font-weight: 600; }
        """)
        self.secondary_btn.setText(self._tr("Yopish"))

    def _on_secondary_clicked(self):
        if self.downloader_thread and self.downloader_thread.isRunning():
            self.downloader_thread.cancel()
        self.reject()

    def closeEvent(self, event):
        if self.checker_thread and self.checker_thread.isRunning():
            self.checker_thread.requestInterruption()
        if self.downloader_thread and self.downloader_thread.isRunning():
            self.downloader_thread.cancel()
        super().closeEvent(event)
