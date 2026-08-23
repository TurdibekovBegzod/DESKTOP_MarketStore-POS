from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QLineEdit, QScrollArea, QFrame, QComboBox, QGridLayout
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QColor
import database as db
from ui.async_loader import AsyncDataLoader, make_progress_bar
from ui.i18n import set_language, t


class NotificationCard(QFrame):
    def __init__(self, notif, on_navigate=None, parent=None):
        super().__init__(parent)
        self.notif = notif
        self.on_navigate = on_navigate
        self._build_ui()

    def _build_ui(self):
        level = self.notif.get("level", "info")

        if level == "danger":
            border_color = "#ef4444"
            bg_color = "#fef2f2"
            icon_text = "🔴"
            badge_bg = "#fee2e2"
            badge_fg = "#b91c1c"
        elif level == "warning":
            border_color = "#f59e0b"
            bg_color = "#fffbeb"
            icon_text = "⚠️"
            badge_bg = "#fef3c7"
            badge_fg = "#b45309"
        elif level == "success":
            border_color = "#10b981"
            bg_color = "#f0fdf4"
            icon_text = "✅"
            badge_bg = "#dcfce7"
            badge_fg = "#15803d"
        else:
            border_color = "#3b82f6"
            bg_color = "#eff6ff"
            icon_text = "ℹ️"
            badge_bg = "#dbeafe"
            badge_fg = "#1d4ed8"

        self.setObjectName("notif_card")
        self.setStyleSheet(f"""
            QFrame#notif_card {{
                background: {bg_color};
                border: 1px solid {border_color};
                border-left: 5px solid {border_color};
                border-radius: 8px;
            }}
            QFrame#notif_card QLabel {{
                border: none;
                background: transparent;
            }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(14)

        icon_lbl = QLabel(icon_text)
        icon_lbl.setStyleSheet("font-size: 20px; background: transparent; border: none;")
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(icon_lbl)

        content_layout = QVBoxLayout()
        content_layout.setSpacing(4)

        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)

        title_lbl = QLabel(self.notif.get("title", ""))
        title_lbl.setStyleSheet("font-size: 14px; font-weight: bold; color: #1e293b; background: transparent; border: none;")
        header_layout.addWidget(title_lbl)

        if not self.notif.get("is_read"):
            new_lbl = QLabel(" Yangi ")
            new_lbl.setStyleSheet("""
                QLabel {
                    background: #ef4444;
                    color: white;
                    font-size: 10px;
                    font-weight: bold;
                    border-radius: 4px;
                    border: none;
                    padding: 1px 5px;
                }
            """)
            header_layout.addWidget(new_lbl)

        badge_text = self.notif.get("badge")
        if badge_text:
            badge_lbl = QLabel(f" {badge_text} ")
            badge_lbl.setStyleSheet(f"""
                QLabel {{
                    background: {badge_bg};
                    color: {badge_fg};
                    font-size: 11px;
                    font-weight: bold;
                    border-radius: 4px;
                    border: none;
                    padding: 2px 6px;
                }}
            """)
            header_layout.addWidget(badge_lbl)

        header_layout.addStretch()
        content_layout.addLayout(header_layout)

        msg_lbl = QLabel(self.notif.get("message", ""))
        msg_lbl.setStyleSheet("font-size: 13px; color: #475569; background: transparent; border: none;")
        msg_lbl.setWordWrap(True)
        content_layout.addWidget(msg_lbl)

        layout.addLayout(content_layout, 1)

        target = self.notif.get("target")
        if target and self.on_navigate:
            btn_text = "O'tish"
            if target == "stock":
                btn_text = "Ombor"
            elif target == "supplier_debts":
                btn_text = "Qarzlar"
            elif target == "products":
                btn_text = "Mahsulotlar"
            elif target == "sales":
                btn_text = "Sotuv"
            elif target == "checking":
                btn_text = "Checking"
            elif target == "expenses":
                btn_text = "Xarajatlar"
            elif target == "users":
                btn_text = "Kassirlar"
            elif target == "login_history":
                btn_text = "Tarix"

            action_btn = QPushButton(btn_text)
            action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            action_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {border_color};
                    color: white;
                    border: none;
                    border-radius: 6px;
                    padding: 6px 14px;
                    font-size: 12px;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    opacity: 0.9;
                }}
            """)
            action_btn.clicked.connect(lambda _, t=target: self.on_navigate(t))
            layout.addWidget(action_btn, alignment=Qt.AlignmentFlag.AlignVCenter)


class NotificationsWidget(QWidget):
    def __init__(self, user=None, on_navigate=None, on_read_updated=None, parent=None):
        super().__init__(parent)
        self.user = user or {}
        self.on_navigate = on_navigate
        self.on_read_updated = on_read_updated
        self.active_filter = "all"
        self._all_notifications = []
        self._async_loader = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        self.progress_bar = make_progress_bar()
        root.addWidget(self.progress_bar)
        self._async_loader = AsyncDataLoader(self, self.progress_bar)

        # 1. Top Summary Cards
        summary_grid = QGridLayout()
        summary_grid.setSpacing(14)

        self.card_act = self._create_summary_card("📦 Mahsulot harakatlari", "0 ta amal", "#0ea5e9", "#f0f9ff", "#e0f2fe")
        self.card_stock = self._create_summary_card("⚠️ Kam qolgan tovarlar", "0 ta mahsulot", "#f59e0b", "#fffbeb", "#fef3c7")
        self.card_debts = self._create_summary_card("💰 Qarzdorliklar", "0 ta mijoz", "#ef4444", "#fef2f2", "#fee2e2")
        self.card_total = self._create_summary_card("🔔 Jami bildirishnomalar", "0 ta", "#6366f1", "#eef2ff", "#e0e7ff")

        summary_grid.addWidget(self.card_act, 0, 0)
        summary_grid.addWidget(self.card_stock, 0, 1)
        summary_grid.addWidget(self.card_debts, 0, 2)
        summary_grid.addWidget(self.card_total, 0, 3)
        root.addLayout(summary_grid)

        # 2. Controls Toolbar (Filter ComboBox + Search + Threshold + Refresh)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)

        filter_lbl = QLabel("Bo'lim:")
        filter_lbl.setStyleSheet("color: #64748b; font-size: 13px; font-weight: bold; border: none; background: transparent;")
        toolbar.addWidget(filter_lbl)

        self.filter_combo = QComboBox()
        self.filter_combo.setFixedHeight(36)
        self.filter_combo.setMinimumWidth(210)
        self.filter_combo.setStyleSheet("""
            QComboBox {
                background: white; border: 1px solid #cbd5e1;
                border-radius: 6px; padding: 4px 10px; font-size: 13px; font-weight: 500;
            }
            QComboBox:focus { border-color: #3b82f6; }
        """)
        self._update_filter_combo_labels()
        self.filter_combo.currentIndexChanged.connect(self._on_filter_combo_changed)
        toolbar.addWidget(self.filter_combo)

        toolbar.addStretch()

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Qidirish (nomi, shtrix-kod, mijoz)...")
        self.search_edit.setMinimumWidth(220)
        self.search_edit.setFixedHeight(36)
        self.search_edit.setStyleSheet("""
            QLineEdit {
                background: white; border: 1px solid #cbd5e1;
                border-radius: 6px; padding: 6px 12px; font-size: 13px;
            }
            QLineEdit:focus { border-color: #3b82f6; }
        """)
        self.search_edit.textChanged.connect(self._apply_filters)
        toolbar.addWidget(self.search_edit)

        threshold_lbl = QLabel("Chegara:")
        threshold_lbl.setStyleSheet("color: #64748b; font-size: 13px;")
        toolbar.addWidget(threshold_lbl)

        self.threshold_combo = QComboBox()
        self.threshold_combo.setFixedHeight(36)
        self.threshold_combo.addItems(["≤ 3 dona", "≤ 5 dona", "≤ 10 dona", "≤ 20 dona"])
        self.threshold_combo.setCurrentIndex(1)
        self.threshold_combo.setStyleSheet("""
            QComboBox {
                background: white; border: 1px solid #cbd5e1;
                border-radius: 6px; padding: 4px 10px; font-size: 13px;
            }
            QComboBox:focus { border-color: #3b82f6; }
        """)
        self.threshold_combo.currentIndexChanged.connect(self.load_data)
        toolbar.addWidget(self.threshold_combo)

        refresh_btn = QPushButton("🔄 Yangilash")
        refresh_btn.setFixedHeight(36)
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.setStyleSheet("""
            QPushButton {
                background: #f1f5f9; border: 1px solid #cbd5e1;
                border-radius: 6px; padding: 6px 14px; font-size: 13px; font-weight: bold; color: #334155;
            }
            QPushButton:hover { background: #e2e8f0; }
        """)
        refresh_btn.clicked.connect(self.load_data)
        toolbar.addWidget(refresh_btn)

        root.addLayout(toolbar)

        # 3. Notification List Area (ScrollArea)
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        scroll_widget = QWidget()
        scroll_widget.setStyleSheet("background: transparent;")
        self.list_layout = QVBoxLayout(scroll_widget)
        self.list_layout.setContentsMargins(0, 4, 0, 10)
        self.list_layout.setSpacing(10)
        self.list_layout.addStretch()

        scroll_area.setWidget(scroll_widget)
        root.addWidget(scroll_area, 1)

    def _update_filter_combo_labels(self, unread_by_type=None, unread_total=0):
        unread_by_type = unread_by_type or {}
        filter_definitions = [
            ("📌 Barchasi", "all", unread_total),
            ("🛒 Sotuvlar", "sales", unread_by_type.get("sales", 0)),
            ("📦 Mahsulotlar", "products", unread_by_type.get("products", 0)),
            ("📊 Ombor", "stock", unread_by_type.get("stock", 0)),
            ("🔍 Checking", "checking", unread_by_type.get("checking", 0)),
            ("💰 Qarzlar", "supplier_debts", unread_by_type.get("supplier_debts", 0)),
            ("💸 Xarajatlar", "expenses", unread_by_type.get("expenses", 0)),
            ("👥 Kassirlar", "users", unread_by_type.get("users", 0)),
            ("🕒 Kirish tarixi", "login_history", unread_by_type.get("login_history", 0)),
            ("🔄 Tizim va Sync", "system", unread_by_type.get("system", 0)),
        ]
        self.filter_combo.blockSignals(True)
        current_data = self.filter_combo.currentData() or "all"
        self.filter_combo.clear()
        selected_idx = 0
        for idx, (label, key, count) in enumerate(filter_definitions):
            txt = f"{label} ({count})" if count > 0 else label
            self.filter_combo.addItem(txt, key)
            if key == current_data:
                selected_idx = idx
        self.filter_combo.setCurrentIndex(selected_idx)
        self.filter_combo.blockSignals(False)

    def _create_summary_card(self, title, default_val, accent_color, bg_color, border_color):
        card = QFrame()
        card.setObjectName("summaryCard")
        card.setStyleSheet(f"""
            QFrame#summaryCard {{
                background: {bg_color};
                border: 1px solid {border_color};
                border-radius: 10px;
                padding: 0px;
            }}
            QFrame#summaryCard QLabel {{
                border: none;
                margin: 0px;
                padding: 0px;
                background: transparent;
            }}
        """)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("summaryCardTitle")
        title_lbl.setStyleSheet(f"""
            QLabel#summaryCardTitle {{
                font-size: 13px;
                font-weight: 600;
                color: {accent_color};
                background: transparent;
                border: none;
                outline: none;
                margin: 0px;
                padding: 0px;
            }}
        """)
        lay.addWidget(title_lbl)

        val_lbl = QLabel(default_val)
        val_lbl.setObjectName("val_label")
        val_lbl.setStyleSheet("""
            QLabel#val_label {
                font-size: 18px;
                font-weight: bold;
                color: #1e293b;
                background: transparent;
                border: none;
                outline: none;
                margin: 0px;
                padding: 0px;
            }
        """)
        lay.addWidget(val_lbl)

        return card

    def apply_theme(self, theme):
        pass

    def _on_filter_combo_changed(self):
        self.active_filter = self.filter_combo.currentData() or "all"
        self._apply_filters()

    def _get_threshold(self):
        txt = self.threshold_combo.currentText()
        if "3" in txt:
            return 3
        if "10" in txt:
            return 10
        if "20" in txt:
            return 20
        return 5

    def load_data(self):
        threshold = self._get_threshold()
        uid = self.user.get("id")
        if self.isVisible():
            self._async_loader.start(lambda: db.get_notifications_data(threshold=threshold, user_id=uid), self._apply_loaded_data)
            return
        self._apply_loaded_data(db.get_notifications_data(threshold=threshold, user_id=uid))

    def _apply_loaded_data(self, data):
        summary = data.get("summary", {})
        self._all_notifications = data.get("notifications", [])

        # Update Summary Cards
        act_total = summary.get('product_activity_count', 0) + summary.get('sales_activity_count', 0)
        self.card_act.findChild(QLabel, "val_label").setText(f"{act_total} ta amal")
        self.card_stock.findChild(QLabel, "val_label").setText(f"{summary.get('low_stock_count', 0)} ta mahsulot")
        self.card_debts.findChild(QLabel, "val_label").setText(f"{summary.get('debtors_count', 0)} ta mijoz")
        self.card_total.findChild(QLabel, "val_label").setText(f"{summary.get('total', 0)} ta")

        self._update_filter_combo_labels(summary.get("unread_by_type"), summary.get("unread_total", 0))
        self._apply_filters()
        set_language(self, self.property("app_language") or "uz")

    def _apply_filters(self):
        # Clear existing cards
        while self.list_layout.count() > 0:
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        query = self.search_edit.text().strip().lower()

        filtered = []
        for n in self._all_notifications:
            if self.active_filter != "all" and n.get("type") != self.active_filter:
                continue
            if query:
                title = (n.get("title") or "").lower()
                msg = (n.get("message") or "").lower()
                badge = (n.get("badge") or "").lower()
                if query not in title and query not in msg and query not in badge:
                    continue
            filtered.append(n)

        # Mark visible items as read
        unread_visible = [n["id"] for n in filtered if not n.get("is_read")]
        if unread_visible:
            db.mark_notifications_as_read(unread_visible, user_id=self.user.get("id"))
            for n in filtered:
                n["is_read"] = True
            if self.on_read_updated:
                self.on_read_updated()

        if not filtered:
            empty_frame = QFrame()
            empty_frame.setStyleSheet("""
                QFrame {
                    background: white; border: 1px dashed #cbd5e1;
                    border-radius: 8px; padding: 40px;
                }
                QLabel {
                    border: none; background: transparent;
                }
            """)
            empty_lay = QVBoxLayout(empty_frame)
            empty_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            
            icon = QLabel("🎉")
            icon.setStyleSheet("font-size: 32px; background: transparent; border: none;")
            icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_lay.addWidget(icon)

            lbl = QLabel("Hozircha hech qanday bildirishnoma yo'q")
            lbl.setStyleSheet("color: #64748b; font-size: 15px; font-weight: bold; background: transparent; border: none;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_lay.addWidget(lbl)
            
            self.list_layout.addWidget(empty_frame)
        else:
            for notif in filtered:
                card = NotificationCard(notif, on_navigate=self.on_navigate)
                self.list_layout.addWidget(card)

        self.list_layout.addStretch()
