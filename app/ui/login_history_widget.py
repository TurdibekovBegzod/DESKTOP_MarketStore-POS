from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox
)
from PyQt6.QtCore import Qt
import database as db
from ui.async_loader import AsyncDataLoader, make_progress_bar
from ui.i18n import set_language, t


class LoginHistoryWidget(QWidget):
    def __init__(self):
        super().__init__()
        self._async_loader = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        self.progress_bar = make_progress_bar()
        layout.addWidget(self.progress_bar)
        self._async_loader = AsyncDataLoader(self, self.progress_bar)

        toolbar = QHBoxLayout()
        toolbar.addStretch()
        clear_btn = QPushButton("Tarixni tozalash")
        clear_btn.setObjectName("danger_clear_login_history_btn")
        clear_btn.setFixedHeight(36)
        clear_btn.setStyleSheet(
            "background:#dc2626;color:white;border:none;border-radius:6px;padding:0 16px;font-weight:bold;"
        )
        clear_btn.clicked.connect(self._clear_history)
        toolbar.addWidget(clear_btn)
        refresh_btn = QPushButton("Yangilash")
        refresh_btn.setFixedHeight(36)
        refresh_btn.setStyleSheet("background:#3b82f6;color:white;border:none;border-radius:6px;padding:0 16px;font-weight:bold;")
        refresh_btn.clicked.connect(self.load_data)
        toolbar.addWidget(refresh_btn)
        layout.addLayout(toolbar)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Vaqt", "Email", "Role", "User ID"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 180)
        self.table.setColumnWidth(2, 120)
        self.table.setColumnWidth(3, 80)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("""
            QTableWidget{background:white;border:1px solid #e2e8f0;border-radius:8px;font-size:13px;}
            QTableWidget::item{padding:7px 10px;}
            QHeaderView::section{background:#f8fafc;border:none;border-bottom:1px solid #e2e8f0;padding:8px;font-weight:bold;color:#64748b;}
            QTableWidget::item:alternate{background:#f8fafc;}
        """)
        layout.addWidget(self.table)

    def load_data(self):
        if self.isVisible():
            self._async_loader.start(db.get_login_logs, self._apply_loaded_data)
            return
        self._apply_loaded_data(db.get_login_logs())

    def _apply_loaded_data(self, logs):
        self.table.setRowCount(0)
        for row, log in enumerate(logs):
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(log["logged_at"] or ""))
            self.table.setItem(row, 1, QTableWidgetItem(log["username"] or ""))
            self.table.setItem(row, 2, QTableWidgetItem("Admin" if log["role"] == "admin" else "Kassir"))
            user_id_item = QTableWidgetItem(str(log["user_id"] or "")[:8])
            user_id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 3, user_id_item)
        set_language(self, self.property("app_language") or "uz")

    def _clear_history(self):
        language = self.property("app_language") or "uz"
        reply = QMessageBox.question(
            self,
            t("Kirish tarixini tozalash", language),
            t("Barcha kirish tarixi o'chirilsinmi?", language),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            db.clear_login_logs()
            self.load_data()
