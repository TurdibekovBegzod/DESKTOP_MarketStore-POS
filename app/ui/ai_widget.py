from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel, QPushButton,
    QStackedWidget, QTextEdit, QSplitter, QSizePolicy, QGraphicsDropShadowEffect,
    QLineEdit, QScrollArea, QCheckBox, QApplication, QMessageBox
)
from PyQt6.QtCore import (
    Qt, QSettings, QSize, QTimer, QThread, pyqtSignal,
    QPropertyAnimation, QEasingCurve, pyqtProperty
)
from PyQt6.QtGui import QColor, QPalette, QPainter

import api_client
import database as db
from ui.design import (
    card, label, primary_button, secondary_button, badge, hline,
    TEXT, MUTED, FAINT, FIELD, LINE, SOFT, RAIL, GREEN, RED, own
)
from ui.icons import line_icon


# The three sections of the AI page. The keys are internal; the captions are
# proper names that read the same in uz, en and ru, so none of them goes
# through the translation table - see "AI" in TEXTS for the same reasoning.
# The last field names the line icon drawn next to the caption.
AI_SECTIONS = (
    ("chat", "Chat", "chat"),
    ("connections", "Connections", "plug"),
    ("rules", "Rules", "rules"),
)

# The app's neutral palette (see "dark_blue" in THEMES). The page keeps it
# whatever theme is chosen: the chat is meant to read like ChatGPT's.
SIDEBAR_BG = "#f7f7f5"
PAGE_BG = "#ffffff"
HOVER_BG = "#efeeeb"
ACTIVE_BG = "#e9e8e4"
LINE = "#e7e6e3"
TEXT = "#111110"
MUTED = "#6b6a66"
PLACEHOLDER = "#8a8984"


class SecretInputField(QFrame):
    """Input field with preview masking (first 3 chars visible), Eye and Copy buttons."""

    valueChanged = pyqtSignal(str)

    def __init__(self, placeholder="", is_secret=True, read_only=False, parent=None):
        super().__init__(parent)
        own(self)
        self.is_secret = is_secret
        self.read_only = read_only
        self._real_value = ""
        self._is_revealed = not is_secret
        self._build_ui(placeholder)

    def _build_ui(self, placeholder):
        self.setObjectName("secretInputFrame")
        self.setFixedHeight(40)
        self.setStyleSheet(
            f"QFrame#secretInputFrame {{ background:{SOFT if self.read_only else '#ffffff'}; "
            f"border:1px solid {FIELD}; border-radius:10px; }} "
            f"QFrame#secretInputFrame:focus-within {{ border-color:{TEXT}; }}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 6, 0)
        layout.setSpacing(4)

        self.edit = QLineEdit()
        self.edit.setFrame(False)
        self.edit.setReadOnly(self.read_only)
        self.edit.setPlaceholderText(placeholder)
        self.edit.setProperty("i18n_skip", True)
        self.edit.setStyleSheet(
            f"QLineEdit {{ background:transparent; border:none; color:{TEXT}; font-size:13px; }}"
        )
        self.edit.textEdited.connect(self._on_text_edited)
        layout.addWidget(self.edit, 1)

        if self.is_secret:
            self.eye_btn = QPushButton()
            self.eye_btn.setFixedSize(28, 28)
            self.eye_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.eye_btn.setStyleSheet(
                f"QPushButton {{ background:transparent; border:none; border-radius:6px; }} "
                f"QPushButton:hover {{ background:{RAIL}; }}"
            )
            self.eye_btn.setIcon(line_icon("eye", MUTED, size=16))
            self.eye_btn.setToolTip("Ko'rish / Yashirish")
            self.eye_btn.clicked.connect(self._toggle_reveal)
            layout.addWidget(self.eye_btn)

        self.copy_btn = QPushButton()
        self.copy_btn.setFixedSize(28, 28)
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_btn.setStyleSheet(
            f"QPushButton {{ background:transparent; border:none; border-radius:6px; }} "
            f"QPushButton:hover {{ background:{RAIL}; }}"
        )
        self.copy_btn.setIcon(line_icon("copy", MUTED, size=16))
        self.copy_btn.setToolTip("Nusxalash")
        self.copy_btn.clicked.connect(self._copy_to_clipboard)
        layout.addWidget(self.copy_btn)

    def _on_text_edited(self, text):
        self._real_value = text
        if self.is_secret and not self._is_revealed:
            self._is_revealed = True
            if hasattr(self, "eye_btn"):
                self.eye_btn.setIcon(line_icon("eye-off", TEXT, size=16))
        self.valueChanged.emit(self._real_value)

    def _toggle_reveal(self):
        if not self.is_secret:
            return
        self._is_revealed = not self._is_revealed
        self._update_display()
        icon_name = "eye-off" if self._is_revealed else "eye"
        icon_color = TEXT if self._is_revealed else MUTED
        self.eye_btn.setIcon(line_icon(icon_name, icon_color, size=16))
        self.edit.setCursorPosition(0)

    def _update_display(self):
        if not self.is_secret or self._is_revealed:
            self.edit.setText(self._real_value)
        else:
            val = self._real_value
            if not val:
                self.edit.setText("")
            elif len(val) <= 3:
                self.edit.setText(val)
            else:
                masked_length = min(28, len(val) - 3)
                self.edit.setText(val[:3] + "•" * masked_length)
        self.edit.setCursorPosition(0)

    def showEvent(self, event):
        super().showEvent(event)
        self.edit.setCursorPosition(0)

    def set_value(self, val):
        self._real_value = str(val or "").strip()
        if self.is_secret:
            self._is_revealed = False
            if hasattr(self, "eye_btn"):
                self.eye_btn.setIcon(line_icon("eye", MUTED, size=16))
        self._update_display()
        self.edit.setCursorPosition(0)

    def value(self):
        return self._real_value

    def _copy_to_clipboard(self):
        val = self._real_value
        if not val:
            return
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(val)
        self.copy_btn.setIcon(line_icon("check", GREEN, size=16))
        self.copy_btn.setToolTip("Nusxalandi!")
        QTimer.singleShot(1500, lambda: (
            self.copy_btn.setIcon(line_icon("copy", MUTED, size=16)),
            self.copy_btn.setToolTip("Nusxalash")
        ))


class ToggleSwitch(QCheckBox):
    """Modern iOS/Material-style animated pill toggle switch."""

    def __init__(self, parent=None, active_color="#2563eb", bg_color="#dcdad5", circle_color="#ffffff"):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(46, 26)
        self._active_color = QColor(active_color)
        self._bg_color = QColor(bg_color)
        self._circle_color = QColor(circle_color)
        self._thumb_position = 1.0 if self.isChecked() else 0.0

        self._anim = QPropertyAnimation(self, b"thumb_position", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self.stateChanged.connect(self._on_state_changed)

    def _get_thumb_position(self):
        return self._thumb_position

    def _set_thumb_position(self, pos):
        self._thumb_position = pos
        self.update()

    thumb_position = pyqtProperty(float, _get_thumb_position, _set_thumb_position)

    def _on_state_changed(self, state):
        self._anim.stop()
        target = 1.0 if state else 0.0
        self._anim.setStartValue(self._thumb_position)
        self._anim.setEndValue(target)
        self._anim.start()

    def setChecked(self, checked):
        super().setChecked(checked)
        self._thumb_position = 1.0 if checked else 0.0
        self.update()

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Track background color
        if 0.0 < self._thumb_position < 1.0:
            r = int(self._bg_color.red() + (self._active_color.red() - self._bg_color.red()) * self._thumb_position)
            g = int(self._bg_color.green() + (self._active_color.green() - self._bg_color.green()) * self._thumb_position)
            b = int(self._bg_color.blue() + (self._active_color.blue() - self._bg_color.blue()) * self._thumb_position)
            track_color = QColor(r, g, b)
        else:
            track_color = self._active_color if self.isChecked() else self._bg_color

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_color)
        radius = self.height() / 2.0
        painter.drawRoundedRect(0, 0, self.width(), self.height(), radius, radius)

        # Knob dimensions
        pad = 3.0
        diameter = self.height() - (pad * 2)
        travel = self.width() - diameter - (pad * 2)
        x = pad + (travel * self._thumb_position)
        y = pad

        # Knob shadow
        painter.setBrush(QColor(0, 0, 0, 35))
        painter.drawEllipse(int(x), int(y + 1), int(diameter), int(diameter))

        # White knob
        painter.setBrush(self._circle_color)
        painter.drawEllipse(int(x), int(y), int(diameter), int(diameter))
        painter.end()


class StatusDot(QFrame):
    """Circular status indicator dot.

    - Active (Green #22c55e): Connected and Auto-reply is ON
    - Disabled (Yellow #f59e0b): Auto-reply is OFF
    - Disconnected (Grey #9ca3af): Missing credentials or not connected
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self._state = "disconnected"
        self._color = "#9ca3af"
        self.set_state("disconnected")

    def set_state(self, state: str, tooltip: str = ""):
        self._state = state
        if state == "active":
            color = "#22c55e"
            default_tip = "Ulangan va faol (yashil)"
        elif state == "disabled":
            color = "#f59e0b"
            default_tip = "Habar jo'natish o'chirilgan (sariq)"
        else:
            color = "#9ca3af"
            default_tip = "Ulanmagan (kulrang)"

        self._color = color
        self.setStyleSheet(
            f"QFrame {{ "
            f"  background-color: {color}; "
            f"  border-radius: 7px; "
            f"  border: 2px solid #ffffff; "
            f"}}"
        )
        self.setToolTip(tooltip or default_tip)

    @property
    def state(self) -> str:
        return self._state

    @property
    def color(self) -> str:
        return self._color


class AccountClaimWorker(QThread):
    """Asks the server whether an Instagram account is free for this shop.

    Runs off the UI thread because it is a network round trip. ``state`` is one
    of "free" (nobody else holds it), "taken" (another shop does) or "unknown"
    (the server could not be reached, so the save has to go ahead and rely on
    the sync-side guard instead).
    """

    finished_signal = pyqtSignal(str, str)  # (state, owner_email)

    def __init__(self, user, account_id):
        super().__init__()
        self.user = user or {}
        self.account_id = str(account_id or "").strip()

    def run(self):
        if not self.account_id or not self.user:
            self.finished_signal.emit("unknown", "")
            return
        try:
            import api_client
            import sync_service
            token = sync_service._token_for_user(self.user)
            available, owner_email = api_client.check_instagram_account_claim(token, self.account_id)
        except Exception:
            self.finished_signal.emit("unknown", "")
            return
        if available:
            self.finished_signal.emit("free", "")
        else:
            self.finished_signal.emit("taken", str(owner_email or ""))


class RuleCard(QFrame):
    """One rule, with its own edit and delete buttons.

    The text lives in an editable box rather than behind an "edit" mode: a rule is
    a sentence somebody will reword often, and making that a two-click affair for
    no gain is worse than letting the box be typed in directly. Save only becomes
    active once something actually changed, so the button doubles as the answer to
    "did my edit register".
    """

    saveRequested = pyqtSignal(str, str, int)   # (rule_id, text, priority)
    deleteRequested = pyqtSignal(str, str)      # (rule_id, text)

    def __init__(self, rule, parent=None):
        super().__init__(parent)
        own(self)
        self.rule_id = str(rule.get("id") or "")
        self._original_text = str(rule.get("text") or "")
        self._original_priority = int(rule.get("priority") or 0)
        self._build_ui()

    def _build_ui(self):
        self.setObjectName("ruleCard")
        self.setStyleSheet(
            "#ruleCard{background:#ffffff;border:1px solid %s;border-radius:12px;}" % LINE
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        self.text_edit = QTextEdit()
        self.text_edit.setPlainText(self._original_text)
        self.text_edit.setObjectName("ruleText")
        self.text_edit.setProperty("i18n_skip", True)
        self.text_edit.setStyleSheet(
            "#ruleText{background:transparent;border:none;color:%s;font-size:14px;}" % TEXT
        )
        self.text_edit.setFixedHeight(72)
        self.text_edit.textChanged.connect(self._refresh_dirty)
        lay.addWidget(self.text_edit)

        row = QHBoxLayout()
        row.setSpacing(8)

        priority_lbl = label("Muhimlik", size=12, color=MUTED)
        priority_lbl.setProperty("i18n_skip", True)
        row.addWidget(priority_lbl)

        self.priority_input = QLineEdit(str(self._original_priority))
        self.priority_input.setObjectName("rulePriority")
        self.priority_input.setProperty("i18n_skip", True)
        self.priority_input.setFixedWidth(56)
        self.priority_input.setToolTip(
            "Ikki qoida bir-biriga zid bo'lsa, raqami kattasi ustun turadi."
        )
        self.priority_input.setStyleSheet(
            "#rulePriority{background:%s;border:1px solid %s;border-radius:8px;"
            "padding:4px 8px;color:%s;font-size:13px;}" % (FIELD, LINE, TEXT)
        )
        self.priority_input.textChanged.connect(self._refresh_dirty)
        row.addWidget(self.priority_input)

        row.addStretch()

        self.save_btn = primary_button("Saqlash", height=32)
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._emit_save)
        row.addWidget(self.save_btn)

        self.delete_btn = secondary_button("O'chirish", height=32)
        self.delete_btn.clicked.connect(
            lambda: self.deleteRequested.emit(self.rule_id, self._original_text)
        )
        row.addWidget(self.delete_btn)

        lay.addLayout(row)

    def _current_priority(self):
        """The typed priority, or the one it had if what is typed is not a number."""
        try:
            return int(self.priority_input.text().strip() or 0)
        except ValueError:
            return self._original_priority

    def _refresh_dirty(self):
        body = self.text_edit.toPlainText().strip()
        changed = body != self._original_text or self._current_priority() != self._original_priority
        self.save_btn.setEnabled(bool(body) and changed)

    def _emit_save(self):
        body = self.text_edit.toPlainText().strip()
        if not body:
            return
        self.saveRequested.emit(self.rule_id, body, self._current_priority())

    def mark_saved(self, text_value, priority):
        """Take the saved values as the new baseline, so Save goes quiet again."""
        self._original_text = text_value
        self._original_priority = priority
        self._refresh_dirty()


class TestConnectionWorker(QThread):
    finished_signal = pyqtSignal(bool, str, str)  # (success, message, found_id)

    def __init__(self, access_token, account_id, gemini_key=""):
        super().__init__()
        self.access_token = str(access_token or "").strip()
        self.account_id = str(account_id or "").strip()
        self.gemini_key = str(gemini_key or "").strip()

    def run(self):
        import json
        import urllib.request
        from urllib.error import HTTPError, URLError
        try:
            from ssl_support import create_ssl_context
            ctx = create_ssl_context()
        except Exception:
            ctx = None

        if not self.access_token:
            self.finished_signal.emit(False, "Instagram Access Token kiritilmagan.", "")
            return

        found_id = ""
        user_info = ""

        # 1. Instagram Check
        try:
            if self.access_token.startswith("IG"):
                url = f"https://graph.instagram.com/v21.0/me?fields=id,username&access_token={self.access_token}"
            else:
                target = self.account_id if self.account_id else "me"
                url = f"https://graph.facebook.com/v21.0/{target}?fields=id,name,username&access_token={self.access_token}"

            req = urllib.request.Request(url, headers={"User-Agent": "MarketStore-POS/1.0"})
            opener_kwargs = {"timeout": 12}
            if ctx is not None:
                opener_kwargs["context"] = ctx
            try:
                with urllib.request.urlopen(req, **opener_kwargs) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    found_id = str(data.get("id") or "")
                    name = data.get("username") or data.get("name") or "Instagram"
                    user_info = f"@{name}" if "username" in data else name
            except HTTPError as alt_e:
                # Try alternate endpoint if first attempt had HTTP error
                alt_url = (
                    f"https://graph.facebook.com/v21.0/{self.account_id or 'me'}?fields=id,name,username&access_token={self.access_token}"
                    if self.access_token.startswith("IG")
                    else f"https://graph.instagram.com/v21.0/me?fields=id,username&access_token={self.access_token}"
                )
                alt_req = urllib.request.Request(alt_url, headers={"User-Agent": "MarketStore-POS/1.0"})
                with urllib.request.urlopen(alt_req, **opener_kwargs) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    found_id = str(data.get("id") or "")
                    name = data.get("username") or data.get("name") or "Instagram"
                    user_info = f"@{name}" if "username" in data else name
        except HTTPError as e:
            try:
                err_data = json.loads(e.read().decode("utf-8"))
                msg = err_data.get("error", {}).get("message") or str(e)
            except Exception:
                msg = f"HTTP {e.code}: {e.reason}"
            self.finished_signal.emit(False, f"Instagram xatosi: {msg}", "")
            return
        except URLError as e:
            self.finished_signal.emit(False, f"Internet yoki tarmoq xatosi: {e.reason}", "")
            return
        except Exception as e:
            self.finished_signal.emit(False, f"Ulanishda xatolik: {e}", "")
            return

        msg = f"Aloqa o'rnatildi: {user_info}"
        self.finished_signal.emit(True, msg, found_id)


class AIWidget(QWidget):
    """The AI section, laid out like ChatGPT: a light sidebar, a clean page.

    The sidebar can be dragged wider or narrower and collapsed to a thin rail of
    icons. Nothing here talks to a model yet: the chat view draws its composer
    but sends nowhere, and the other two sections are empty pages.
    """

    # How far the section panel may be dragged, and where it starts.
    PANEL_MIN_WIDTH = 190
    PANEL_MAX_WIDTH = 380
    PANEL_DEFAULT_WIDTH = 260
    RAIL_WIDTH = 56
    PANEL_WIDTH_KEY = "ui/ai_panel_width"
    PANEL_COLLAPSED_KEY = "ui/ai_panel_collapsed"

    def __init__(self, user=None):
        super().__init__()
        self.user = user or {}
        self.section_buttons = {}
        self.rail_buttons = {}
        self._theme = None
        self._current_section = "chat"
        self._width_applied = False
        self._is_verified = False
        self._baseline_verified = False
        self._baseline_account_id = ""
        self._baseline_access_token = ""
        self._baseline_app_secret = ""
        self._baseline_auto_reply = True
        self.setObjectName("aiRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._build_ui()

    # ------------------------------------------------------------------ build

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("aiSplitter")
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(4)
        self.splitter.splitterMoved.connect(self._remember_panel_width)

        self.panel_stack = QStackedWidget()
        self.panel_stack.setMinimumWidth(self.RAIL_WIDTH)
        self.panel_stack.setMaximumWidth(self.PANEL_MAX_WIDTH)
        self.panel_stack.addWidget(self._build_panel())
        self.panel_stack.addWidget(self._build_rail())
        self.splitter.addWidget(self.panel_stack)

        self.content_stack = QStackedWidget()
        # A stack asks for as much width as its widest page; without this the
        # panel would have nothing left to be dragged into.
        self.content_stack.setMinimumWidth(320)
        self.content_stack.setSizePolicy(
            QSizePolicy.Policy.Ignored, self.content_stack.sizePolicy().verticalPolicy()
        )
        self.pages = {
            "chat": self._build_chat_page(),
            "connections": self._build_connections_page(),
            "rules": self._build_rules_page(),
        }
        for key, _caption, _icon in AI_SECTIONS:
            self.content_stack.addWidget(self.pages[key])
        self.splitter.addWidget(self.content_stack)

        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        root.addWidget(self.splitter)

        self._switch_section(self._current_section)
        if self._restore_collapsed():
            self._set_collapsed(True, remember=False)

    def _build_panel(self):
        """The open sidebar: name and toggle on top, then the sections."""
        self.panel = QFrame()
        self.panel.setObjectName("aiPanel")
        lay = QVBoxLayout(self.panel)
        lay.setContentsMargins(8, 10, 8, 12)
        lay.setSpacing(2)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(10, 0, 0, 0)
        self.panel_title_lbl = QLabel("AI")
        self.panel_title_lbl.setObjectName("aiPanelTitle")
        self.panel_title_lbl.setProperty("i18n_skip", True)
        top_row.addWidget(self.panel_title_lbl)
        top_row.addStretch()
        self.collapse_btn = self._make_icon_button("sidebar", "Yopish")
        self.collapse_btn.clicked.connect(lambda: self._set_collapsed(True))
        top_row.addWidget(self.collapse_btn)
        lay.addLayout(top_row)
        lay.addSpacing(10)

        for key, caption, icon in AI_SECTIONS:
            btn = QPushButton(caption)
            btn.setObjectName("aiSection")
            btn.setProperty("i18n_skip", True)
            btn.setIcon(line_icon(icon, TEXT))
            btn.setIconSize(QSize(18, 18))
            btn.setCheckable(True)
            btn.setFixedHeight(36)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _c, k=key: self._switch_section(k))
            lay.addWidget(btn)
            self.section_buttons[key] = btn

        lay.addStretch()
        return self.panel

    def _build_rail(self):
        """The collapsed sidebar: a thin column of icons, as ChatGPT shows it."""
        self.rail = QFrame()
        self.rail.setObjectName("aiRail")
        self.rail.setFixedWidth(self.RAIL_WIDTH)
        lay = QVBoxLayout(self.rail)
        lay.setContentsMargins(10, 10, 10, 12)
        lay.setSpacing(4)

        self.expand_btn = self._make_icon_button("sidebar", "Ochish")
        self.expand_btn.clicked.connect(lambda: self._set_collapsed(False))
        lay.addWidget(self.expand_btn)
        lay.addSpacing(10)

        for key, caption, icon in AI_SECTIONS:
            btn = self._make_icon_button(icon, caption)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _c, k=key: self._switch_section(k))
            lay.addWidget(btn)
            self.rail_buttons[key] = btn

        lay.addStretch()
        return self.rail

    def _make_icon_button(self, icon, tooltip):
        btn = QPushButton()
        btn.setObjectName("aiIconBtn")
        btn.setProperty("i18n_skip", True)
        btn.setIcon(line_icon(icon, MUTED))
        btn.setIconSize(QSize(20, 20))
        btn.setToolTip(tooltip)
        btn.setFixedSize(36, 36)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        return btn

    def _build_chat_page(self):
        """The chat view: a greeting over the composer, both centred."""
        page = QFrame()
        page.setObjectName("aiPage")
        outer = QHBoxLayout(page)
        outer.setContentsMargins(24, 24, 24, 24)

        # The conversation column is centred and capped, so the text stays
        # readable on a wide screen instead of running the full width.
        column = QWidget()
        column.setMaximumWidth(768)
        col_lay = QVBoxLayout(column)
        col_lay.setContentsMargins(0, 0, 0, 0)
        col_lay.setSpacing(0)

        col_lay.addStretch()
        self.chat_empty_lbl = QLabel("Bugun nima qilamiz?")
        self.chat_empty_lbl.setObjectName("aiGreeting")
        self.chat_empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col_lay.addWidget(self.chat_empty_lbl)
        col_lay.addSpacing(26)

        self.chat_view = QTextEdit()
        self.chat_view.setObjectName("aiChatView")
        self.chat_view.setReadOnly(True)
        self.chat_view.setFrameShape(QFrame.Shape.NoFrame)
        # Hidden until there is a conversation to show; Ignored keeps a hidden
        # view from holding on to space and pushing the composer down.
        self.chat_view.hide()
        self.chat_view.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Ignored
        )
        col_lay.addWidget(self.chat_view)

        self.input_box = QFrame()
        self.input_box.setObjectName("aiInputBox")
        self.input_box.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        shadow = QGraphicsDropShadowEffect(self.input_box)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 3)
        shadow.setColor(QColor(0, 0, 0, 22))
        self.input_box.setGraphicsEffect(shadow)

        box_lay = QHBoxLayout(self.input_box)
        box_lay.setContentsMargins(10, 10, 10, 10)
        box_lay.setSpacing(6)

        # Attachments come later; until then the button is shown but inert.
        self.chat_attach_btn = QPushButton()
        self.chat_attach_btn.setObjectName("aiAttachBtn")
        self.chat_attach_btn.setIcon(line_icon("plus", TEXT))
        self.chat_attach_btn.setIconSize(QSize(20, 20))
        self.chat_attach_btn.setFixedSize(36, 36)
        self.chat_attach_btn.setEnabled(False)
        box_lay.addWidget(self.chat_attach_btn)

        self.chat_input = QTextEdit()
        self.chat_input.setObjectName("aiChatInput")
        self.chat_input.setFrameShape(QFrame.Shape.NoFrame)
        self.chat_input.setPlaceholderText("Ask anything")
        self.chat_input.setProperty("i18n_skip", True)
        self.chat_input.document().setDocumentMargin(7)
        self.chat_input.setFixedHeight(36)
        self.chat_input.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        palette = self.chat_input.palette()
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(PLACEHOLDER))
        self.chat_input.setPalette(palette)
        box_lay.addWidget(self.chat_input, 1)

        self.chat_send_btn = QPushButton()
        self.chat_send_btn.setObjectName("aiSendBtn")
        self.chat_send_btn.setIcon(line_icon("send", "#ffffff"))
        self.chat_send_btn.setIconSize(QSize(18, 18))
        self.chat_send_btn.setFixedSize(36, 36)
        self.chat_send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # Deliberately not connected: there is no model behind it yet, and a
        # button that looks like it works but does nothing is worse than one
        # that plainly cannot be pressed. Disabled, it is ChatGPT's grey circle.
        self.chat_send_btn.setEnabled(False)
        box_lay.addWidget(self.chat_send_btn)

        col_lay.addWidget(self.input_box)
        col_lay.addStretch()
        # A little more room below than above lifts the pair just over the
        # middle, where ChatGPT puts it.
        col_lay.setStretch(0, 5)
        col_lay.setStretch(col_lay.count() - 1, 6)

        outer.addStretch()
        outer.addWidget(column, 1)
        outer.addStretch()
        return page

    def _build_rules_page(self):
        """The rules section: what this shop tells its agent, and the editor for it.

        Rules are looked up by meaning on the server, so the shop can keep adding
        them for years without the list ever having to be pruned to fit a prompt.
        That is why there is no cap shown here and no ordering to maintain by hand.
        """
        page = QFrame()
        page.setObjectName("aiPage")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(28, 22, 28, 24)
        outer.setSpacing(12)

        title = QLabel("Rules")
        title.setObjectName("aiPageTitle")
        title.setProperty("i18n_skip", True)
        outer.addWidget(title)

        hint = QLabel()
        hint.setTextFormat(Qt.TextFormat.RichText)
        hint.setWordWrap(True)
        hint.setText(
            f"<div style='color:{MUTED}; font-size:13px; line-height:150%;'>"
            "Bu yerga yozganlaringizni bot mijozlarga javob berishda hisobga oladi - "
            "masalan yetkazib berish shartlari, kafolat muddati, to'lov usullari. "
            "Bot har savolga mos qoidalarni o'zi topib ishlatadi, shuning uchun "
            "qancha yozsangiz ham bo'ladi. Javobi qoidalarda ham, mahsulotlar "
            "orasida ham bo'lmasa, bot bilmasligini ochiq aytadi."
            "</div>"
        )
        hint.setProperty("i18n_skip", True)
        hint.setStyleSheet("background:transparent;border:none;")
        outer.addWidget(hint)

        # The composer for a new rule.
        add_card = QFrame()
        add_card.setObjectName("ruleAddCard")
        add_card.setStyleSheet(
            "#ruleAddCard{background:#ffffff;border:1px solid %s;border-radius:12px;}" % LINE
        )
        add_lay = QVBoxLayout(add_card)
        add_lay.setContentsMargins(14, 12, 14, 12)
        add_lay.setSpacing(8)

        self.new_rule_edit = QTextEdit()
        self.new_rule_edit.setObjectName("ruleText")
        self.new_rule_edit.setProperty("i18n_skip", True)
        self.new_rule_edit.setPlaceholderText(
            "Yangi qoida, masalan: Yetkazib berish Toshkent ichida bepul, 1-2 kun ichida."
        )
        self.new_rule_edit.setStyleSheet(
            "#ruleText{background:transparent;border:none;color:%s;font-size:14px;}" % TEXT
        )
        self.new_rule_edit.setFixedHeight(72)
        self.new_rule_edit.textChanged.connect(self._refresh_add_button)
        add_lay.addWidget(self.new_rule_edit)

        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        add_row.addStretch()
        self.rule_add_btn = primary_button("Qo'shish", height=34)
        self.rule_add_btn.setEnabled(False)
        self.rule_add_btn.clicked.connect(self._add_rule)
        add_row.addWidget(self.rule_add_btn)
        add_lay.addLayout(add_row)
        outer.addWidget(add_card)

        self.rules_status_lbl = QLabel("")
        self.rules_status_lbl.setProperty("i18n_skip", True)
        self.rules_status_lbl.setStyleSheet(
            f"color:{MUTED};font-size:12px;font-weight:500;background:transparent;border:none;"
        )
        outer.addWidget(self.rules_status_lbl)

        # The stored rules, in their own scroll area so a long list does not push
        # the composer off the page.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background:transparent;border:none;")
        self.rules_container = QWidget()
        self.rules_container.setStyleSheet("background:transparent;")
        self.rules_list_lay = QVBoxLayout(self.rules_container)
        self.rules_list_lay.setContentsMargins(0, 0, 0, 0)
        self.rules_list_lay.setSpacing(10)
        self.rules_list_lay.addStretch()
        scroll.setWidget(self.rules_container)
        outer.addWidget(scroll, 1)

        self._reload_rules()
        return page

    # ------------------------------------------------------------------ rules

    def _refresh_add_button(self):
        self.rule_add_btn.setEnabled(bool(self.new_rule_edit.toPlainText().strip()))

    def _reload_rules(self):
        """Rebuild the list from the database.

        A full rebuild rather than patching one card: the list is short, the cost
        is invisible, and it keeps the order on screen exactly what the database
        says it is after a priority change.
        """
        while self.rules_list_lay.count():
            item = self.rules_list_lay.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        try:
            rules = db.list_agent_rules()
        except Exception:
            rules = []
            self._show_rules_status("Qoidalarni o'qib bo'lmadi.", success=False)

        if not rules:
            empty = label("Hali qoida qo'shilmagan.", size=13, color=MUTED)
            empty.setProperty("i18n_skip", True)
            self.rules_list_lay.addWidget(empty)

        for rule in rules:
            card = RuleCard(rule)
            card.saveRequested.connect(self._save_rule)
            card.deleteRequested.connect(self._delete_rule)
            self.rules_list_lay.addWidget(card)

        self.rules_list_lay.addStretch()

    def _add_rule(self):
        body = self.new_rule_edit.toPlainText().strip()
        if not body:
            return
        try:
            db.add_agent_rule(body)
        except Exception as exc:
            self._show_rules_status(str(exc) or "Qoida qo'shilmadi.", success=False)
            return
        self.new_rule_edit.clear()
        self._reload_rules()
        self._show_rules_status("Qoida qo'shildi.", success=True)

    def _save_rule(self, rule_id, text_value, priority):
        try:
            found = db.update_agent_rule(rule_id, text_value=text_value, priority=priority)
        except Exception as exc:
            self._show_rules_status(str(exc) or "Qoida saqlanmadi.", success=False)
            return
        if not found:
            # Deleted on another device while this one had it open.
            self._reload_rules()
            self._show_rules_status("Bu qoida topilmadi - u o'chirilgan bo'lishi mumkin.", success=False)
            return
        # Priority decides the order, so a changed one has to move the card.
        self._reload_rules()
        self._show_rules_status("Qoida saqlandi.", success=True)

    def _delete_rule(self, rule_id, rule_text):
        """Remove a rule, once the owner has confirmed which one."""
        preview = (rule_text or "").strip()
        if len(preview) > 120:
            preview = preview[:120] + "..."
        confirmed = QMessageBox.question(
            self,
            "Qoidani o'chirish",
            f"Bu qoida butunlay o'chiriladi:\n\n{preview}\n\nDavom etamizmi?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return

        try:
            found = db.delete_agent_rule(rule_id)
        except Exception as exc:
            self._show_rules_status(str(exc) or "Qoida o'chirilmadi.", success=False)
            return
        self._reload_rules()
        if found:
            self._show_rules_status("Qoida o'chirildi.", success=True)
        else:
            self._show_rules_status("Bu qoida allaqachon o'chirilgan.", success=True)

    def _show_rules_status(self, text, success=True):
        self.rules_status_lbl.setText(text)
        self.rules_status_lbl.setStyleSheet(
            f"color:{GREEN if success else RED};font-size:12px;font-weight:500;"
            "background:transparent;border:none;"
        )
        QTimer.singleShot(6000, lambda: self.rules_status_lbl.setText(""))

    def _build_placeholder_page(self, caption):
        """An empty section: its name at the top, room below for what comes later."""
        page = QFrame()
        page.setObjectName("aiPage")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(28, 22, 28, 24)
        lay.setSpacing(12)
        title = QLabel(caption)
        title.setObjectName("aiPageTitle")
        title.setProperty("i18n_skip", True)
        lay.addWidget(title)
        lay.addStretch()
        return page

    def _build_connections_page(self):
        """The connections section: Instagram Direct and AI configuration."""
        scroll = QScrollArea()
        scroll.setObjectName("aiConnectionsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea#aiConnectionsScroll { background:#ffffff; border:none; }")

        container = own(QWidget())
        container.setObjectName("aiConnectionsContainer")
        container.setStyleSheet("QWidget#aiConnectionsContainer { background:#ffffff; }")
        outer = QVBoxLayout(container)
        outer.setContentsMargins(28, 24, 28, 36)
        outer.setSpacing(20)

        # Centered column (max width 768px for clean, readable SaaS layout)
        col = own(QWidget())
        col.setMaximumWidth(768)
        lay = QVBoxLayout(col)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(18)

        # Page Header
        head_lay = QVBoxLayout()
        head_lay.setSpacing(4)
        title = label("Integratsiyalar va Ulanishlar", size=20, weight=600, color=TEXT)
        title.setProperty("i18n_skip", True)
        subtitle = label(
            "Do'koningizga tashqi platformalarni ulang. Instagram Direct xabarlariga AI tovarlar bazasi asosida avtomatik javob beradi.",
            size=13, color=MUTED
        )
        subtitle.setWordWrap(True)
        subtitle.setProperty("i18n_skip", True)
        head_lay.addWidget(title)
        head_lay.addWidget(subtitle)
        lay.addLayout(head_lay)

        # Instagram Integration Card
        card_frame, card_lay = card("instagramConnCard", padding=(22, 20, 22, 22), spacing=16)

        # Card header row with status badge
        card_top = QHBoxLayout()
        card_top.setSpacing(10)

        ig_icon_lbl = QLabel()
        ig_icon_lbl.setFixedSize(26, 26)
        ig_icon_lbl.setPixmap(line_icon("instagram", TEXT, size=22).pixmap(22, 22))
        card_top.addWidget(ig_icon_lbl)

        card_title_lay = QVBoxLayout()
        card_title_lay.setSpacing(2)
        card_title = label("Instagram Direct", size=15, weight=600, color=TEXT)
        card_title.setProperty("i18n_skip", True)
        card_desc = label("Meta Graph API va Webhook orqali avtomatik AI javob berish", size=12, color=MUTED)
        card_desc.setProperty("i18n_skip", True)
        card_title_lay.addWidget(card_title)
        card_title_lay.addWidget(card_desc)
        card_top.addLayout(card_title_lay)
        card_top.addStretch()

        self.ig_status_dot = StatusDot()
        self.ig_status_dot.setProperty("i18n_skip", True)
        card_top.addWidget(self.ig_status_dot)
        card_lay.addLayout(card_top)

        card_lay.addWidget(hline())

        # Form fields
        self.account_id_input = SecretInputField(placeholder="Masalan: 17841405822386821", is_secret=False)
        self.account_id_input.valueChanged.connect(lambda _v: self._on_input_changed())
        card_lay.addLayout(self._make_conn_field(
            "Instagram Account ID (yoki Page ID)",
            "Meta Developers portalidagi Instagram Business Account ID raqami. Xabar kelganda server sizning do'koningizni aynan shu ID orqali taniydi.",
            self.account_id_input
        ))

        self.access_token_input = SecretInputField(placeholder="EAA... yoki IGA...", is_secret=True)
        self.access_token_input.valueChanged.connect(lambda _v: self._on_input_changed())
        card_lay.addLayout(self._make_conn_field(
            "Instagram Access Token (Sahifa tokeni)",
            "Meta Developers -> Generate Access Tokens bo'limidan olingan sahifa tokeni. Mijozga sizning nomingizdan javob yuborish uchun kerak.",
            self.access_token_input
        ))

        self.app_secret_input = SecretInputField(placeholder="App Secret (Meta Developers)", is_secret=True)
        self.app_secret_input.valueChanged.connect(lambda _v: self._on_input_changed())
        card_lay.addLayout(self._make_conn_field(
            "Instagram App Secret (Maxfiy kalit)",
            "Meta App Dashboard -> App Settings -> Basic bo'limidagi maxfiy kalit (xabarlar soxta emasligini tekshirish uchun).",
            self.app_secret_input
        ))

        # Auto reply toggle switch row
        auto_reply_row = QHBoxLayout()
        auto_reply_row.setContentsMargins(0, 4, 0, 4)
        auto_reply_row.setSpacing(14)

        auto_reply_text_lay = QVBoxLayout()
        auto_reply_text_lay.setSpacing(3)
        auto_reply_title = label("Instagram Direct xabarlariga avtomatik AI javob berish", size=13, weight=600, color=TEXT)
        auto_reply_title.setProperty("i18n_skip", True)
        auto_reply_title.setCursor(Qt.CursorShape.PointingHandCursor)
        auto_reply_hint = QLabel()
        auto_reply_hint.setTextFormat(Qt.TextFormat.RichText)
        auto_reply_hint.setWordWrap(True)
        auto_reply_hint.setText(
            f"<div style='color:{FAINT}; font-size:12px; line-height:145%; margin:0; padding:0 0 2px 0;'>"
            "O'chirib qo'yilsa, kelgan xabarlar faqat logga tushadi, bot esa mijozlarga avtomatik javob qaytarmaydi."
            "</div>"
        )
        auto_reply_hint.setProperty("i18n_skip", True)
        auto_reply_hint.setStyleSheet("background:transparent; border:none;")
        auto_reply_text_lay.addWidget(auto_reply_title)
        auto_reply_text_lay.addWidget(auto_reply_hint)

        self.auto_reply_chk = ToggleSwitch(active_color="#2563eb")
        self.auto_reply_chk.setProperty("i18n_skip", True)
        self.auto_reply_chk.stateChanged.connect(lambda _v: self._on_input_changed())
        auto_reply_title.mousePressEvent = lambda _e: self.auto_reply_chk.toggle()

        auto_reply_row.addLayout(auto_reply_text_lay, 1)
        auto_reply_row.addWidget(self.auto_reply_chk, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        card_lay.addLayout(auto_reply_row)

        card_lay.addWidget(hline())

        # Action Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self.conn_save_btn = primary_button("Saqlash", height=38)
        self.conn_save_btn.clicked.connect(self._save_instagram_connections)
        self.conn_save_btn.setEnabled(False)
        btn_row.addWidget(self.conn_save_btn)

        self.conn_test_btn = secondary_button("Ulanishni tekshirish", height=38)
        self.conn_test_btn.clicked.connect(self._test_connection)
        btn_row.addWidget(self.conn_test_btn)
        btn_row.addStretch()

        card_lay.addLayout(btn_row)

        # Status text in its own row below buttons - word wrapped so it never stretches the layout
        self.conn_status_lbl = QLabel("")
        self.conn_status_lbl.setWordWrap(True)
        self.conn_status_lbl.setStyleSheet(f"color:{GREEN}; font-size:12px; font-weight:500; padding-top:4px; background:transparent; border:none;")
        self.conn_status_lbl.setProperty("i18n_skip", True)
        card_lay.addWidget(self.conn_status_lbl)

        lay.addWidget(card_frame)
        lay.addStretch()

        outer.addWidget(col, 0, Qt.AlignmentFlag.AlignHCenter)
        scroll.setWidget(container)

        self._load_instagram_connections()
        return scroll

    def _make_conn_field(self, title_text, hint_text, input_widget):
        field_lay = QVBoxLayout()
        field_lay.setSpacing(6)
        lbl = label(title_text, size=13, weight=500, color=TEXT)
        lbl.setProperty("i18n_skip", True)
        field_lay.addWidget(lbl)
        if hint_text:
            h_lbl = QLabel()
            h_lbl.setTextFormat(Qt.TextFormat.RichText)
            h_lbl.setWordWrap(True)
            h_lbl.setText(
                f"<div style='color:{FAINT}; font-size:12px; line-height:145%; margin:0; padding:0 0 2px 0;'>"
                f"{hint_text}</div>"
            )
            h_lbl.setProperty("i18n_skip", True)
            h_lbl.setStyleSheet("background:transparent; border:none;")
            field_lay.addWidget(h_lbl)
        field_lay.addWidget(input_widget)
        return field_lay

    def _has_unsaved_changes(self) -> bool:
        cur_id = self.account_id_input.value().strip()
        cur_token = self.access_token_input.value().strip()
        cur_secret = self.app_secret_input.value().strip()
        cur_reply = self.auto_reply_chk.isChecked()

        return (
            cur_id != self._baseline_account_id
            or cur_token != self._baseline_access_token
            or cur_secret != self._baseline_app_secret
            or cur_reply != self._baseline_auto_reply
        )

    def _check_dirty_state(self):
        is_dirty = self._has_unsaved_changes()
        self.conn_save_btn.setEnabled(is_dirty)

    def _on_input_changed(self):
        is_dirty = self._has_unsaved_changes()
        self.conn_save_btn.setEnabled(is_dirty)

        creds_changed = (
            self.account_id_input.value().strip() != self._baseline_account_id
            or self.access_token_input.value().strip() != self._baseline_access_token
            or self.app_secret_input.value().strip() != self._baseline_app_secret
        )

        if creds_changed:
            self._is_verified = False
        elif not is_dirty:
            self._is_verified = self._baseline_verified

        self._update_connection_status()

    def _update_connection_status(self):
        has_id = bool(self.account_id_input.value().strip())
        has_token = bool(self.access_token_input.value().strip())
        has_secret = bool(self.app_secret_input.value().strip())
        is_auto_reply = self.auto_reply_chk.isChecked()

        if not (has_id and has_token and has_secret and self._is_verified):
            self.ig_status_dot.set_state("disconnected", tooltip="Ulanmagan yoki tekshirilmagan (kulrang)")
        elif not is_auto_reply:
            self.ig_status_dot.set_state("disabled", tooltip="Habar jo'natish o'chirilgan (sariq)")
        else:
            self.ig_status_dot.set_state("active", tooltip="Ulangan va faol (yashil)")

    def _load_instagram_connections(self, check_remote: bool = True):
        try:
            settings = db.get_instagram_settings()
            self.account_id_input.set_value(settings.get("instagram_account_id", ""))
            self.access_token_input.set_value(settings.get("instagram_access_token", ""))
            self.app_secret_input.set_value(settings.get("instagram_app_secret", ""))
            auto_reply = settings.get("instagram_auto_reply", "1") == "1"
            self.auto_reply_chk.setChecked(auto_reply)

            # Store baseline
            self._baseline_account_id = self.account_id_input.value().strip()
            self._baseline_access_token = self.access_token_input.value().strip()
            self._baseline_app_secret = self.app_secret_input.value().strip()
            self._baseline_auto_reply = self.auto_reply_chk.isChecked()
            self._baseline_verified = False
            self._is_verified = False

            self._update_connection_status()
            self._check_dirty_state()

            token = self._baseline_access_token
            acc_id = self._baseline_account_id
            secret = self._baseline_app_secret
            if check_remote and token and acc_id and secret:
                self._startup_worker = TestConnectionWorker(token, acc_id)
                self._startup_worker.finished_signal.connect(self._on_startup_check_finished)
                self._startup_worker.start()
        except Exception:
            pass

    def _on_startup_check_finished(self, success, message, found_id):
        if success:
            self._baseline_verified = True
            creds_changed = (
                self.account_id_input.value().strip() != self._baseline_account_id
                or self.access_token_input.value().strip() != self._baseline_access_token
                or self.app_secret_input.value().strip() != self._baseline_app_secret
            )
            if not creds_changed:
                self._is_verified = True
        else:
            self._baseline_verified = False
            self._is_verified = False
        self._update_connection_status()

    def _save_instagram_connections(self):
        token = self.access_token_input.value().strip()
        account_id = self.account_id_input.value().strip()

        if not token:
            self._is_verified = False
            self._update_connection_status()
            self._check_dirty_state()
            self._show_save_status("Iltimos, Instagram Access Token kiriting.", success=False)
            return

        self.conn_save_btn.setEnabled(False)
        self.conn_test_btn.setEnabled(False)
        self.conn_save_btn.setText("Tekshirilmoqda...")
        self.conn_status_lbl.setText("Meta serveri bilan aloqa tekshirilmoqda...")
        self.conn_status_lbl.setStyleSheet(f"color:{MUTED};font-size:12px;font-weight:500;padding-top:4px;")

        self._save_worker = TestConnectionWorker(token, account_id)
        self._save_worker.finished_signal.connect(self._on_save_test_finished)
        self._save_worker.start()

    def _on_save_test_finished(self, success, message, found_id):
        self.conn_test_btn.setEnabled(True)
        self.conn_save_btn.setText("Saqlash")

        if success and not self.account_id_input.value().strip() and found_id:
            self.account_id_input.set_value(found_id)

        # One Instagram account belongs to one shop. Ask the server before
        # writing anything: a second shop entering credentials that are already
        # connected elsewhere must be turned away, not silently duplicated.
        account_id = self.account_id_input.value().strip()
        if account_id and self.user:
            self.conn_save_btn.setText("Tekshirilmoqda...")
            self.conn_status_lbl.setText("Instagram akkaunt bandligi tekshirilmoqda...")
            self.conn_status_lbl.setStyleSheet(f"color:{MUTED};font-size:12px;font-weight:500;padding-top:4px;")
            self._claim_worker = AccountClaimWorker(self.user, account_id)
            self._claim_worker.finished_signal.connect(
                lambda state, owner_email: self._on_claim_check_finished(
                    state, owner_email, success, message
                )
            )
            self._claim_worker.start()
            return

        self._persist_instagram_settings(success, message)

    def _on_claim_check_finished(self, state, owner_email, success, message):
        """Write the credentials, unless another shop already holds the account."""
        self.conn_save_btn.setText("Saqlash")
        if state == "taken":
            self._reject_taken_account(owner_email)
            return
        self._persist_instagram_settings(success, message)

    def _reject_taken_account(self, owner_email):
        """Refuse the save and leave no Instagram credentials behind locally.

        The fields are wiped along with the stored rows: keeping a token that
        this shop is not allowed to use only invites the same conflict again on
        the next save.
        """
        try:
            db.clear_instagram_settings()
        except Exception:
            pass

        self.account_id_input.set_value("")
        self.access_token_input.set_value("")
        self.app_secret_input.set_value("")
        self.auto_reply_chk.setChecked(False)

        self._baseline_account_id = ""
        self._baseline_access_token = ""
        self._baseline_app_secret = ""
        self._baseline_auto_reply = False
        self._baseline_verified = False
        self._is_verified = False

        self._update_connection_status()
        self._check_dirty_state()

        owner = str(owner_email or "").strip()
        who = f" Egasi: {owner}." if owner else ""
        self._show_save_status(
            "Bu Instagram akkaunt boshqa do'konga ulangan, shuning uchun saqlanmadi."
            f"{who} Kiritilgan ma'lumotlar tozalandi.",
            success=False,
        )

        # Push the blanked rows so the server drops anything this device sent
        # for this account earlier.
        if self.user:
            import threading
            import sync_service
            threading.Thread(
                target=lambda: sync_service.push_local_changes(self.user, force=True),
                daemon=True
            ).start()

    def _persist_instagram_settings(self, success, message):
        settings = {
            "instagram_account_id": self.account_id_input.value().strip(),
            "instagram_access_token": self.access_token_input.value().strip(),
            "instagram_app_secret": self.app_secret_input.value().strip(),
            "instagram_auto_reply": "1" if self.auto_reply_chk.isChecked() else "0",
        }
        try:
            db.save_instagram_settings(settings)
        except Exception as e:
            self._is_verified = False
            self._update_connection_status()
            self._check_dirty_state()
            self._show_save_status(f"Bazaga saqlashda xatolik: {e}", success=False)
            return

        # Update baseline to saved values
        self._baseline_account_id = self.account_id_input.value().strip()
        self._baseline_access_token = self.access_token_input.value().strip()
        self._baseline_app_secret = self.app_secret_input.value().strip()
        self._baseline_auto_reply = self.auto_reply_chk.isChecked()

        if success:
            self._baseline_verified = True
            self._is_verified = True
            self._update_connection_status()
            self._check_dirty_state()
            self._show_save_status(f"Sozlamalar saqlandi. {message}", success=True)
            if self.user:
                import threading
                import sync_service
                threading.Thread(
                    target=lambda: sync_service.push_local_changes(self.user, force=True),
                    daemon=True
                ).start()
        else:
            self._baseline_verified = False
            self._is_verified = False
            self._update_connection_status()
            self._check_dirty_state()
            self._show_save_status(f"Sozlamalar saqlandi, lekin aloqa o'rnatilmadi: {message}", success=False)

    def _test_connection(self):
        token = self.access_token_input.value().strip()
        account_id = self.account_id_input.value().strip()

        if not token:
            self._show_save_status("Iltimos, avval Instagram Access Token kiriting.", success=False)
            return

        self.conn_save_btn.setEnabled(False)
        self.conn_test_btn.setEnabled(False)
        self.conn_test_btn.setText("Tekshirilmoqda...")
        self.conn_status_lbl.setText("Meta serveriga ulanilmoqda...")
        self.conn_status_lbl.setStyleSheet(f"color:{MUTED};font-size:12px;font-weight:500;padding-top:4px;")

        self._test_worker = TestConnectionWorker(token, account_id)
        self._test_worker.finished_signal.connect(self._on_test_finished)
        self._test_worker.start()

    def _on_test_finished(self, success, message, found_id):
        self.conn_test_btn.setEnabled(True)
        self.conn_test_btn.setText("Ulanishni tekshirish")
        if success:
            if not self.account_id_input.value().strip() and found_id:
                self.account_id_input.set_value(found_id)
            self._is_verified = True
            if not self._has_unsaved_changes():
                self._baseline_verified = True
            self._update_connection_status()
            self._show_save_status(message, success=True)
        else:
            self._is_verified = False
            self._update_connection_status()
            self._show_save_status(message, success=False)
        self._check_dirty_state()

    def _show_save_status(self, text, success=True):
        self.conn_status_lbl.setText(text)
        self.conn_status_lbl.setStyleSheet(f"color:{GREEN if success else RED};font-size:12px;font-weight:500;padding-top:4px;")
        QTimer.singleShot(6000, lambda: self.conn_status_lbl.setText(""))

    # ----------------------------------------------------------------- behave

    def _switch_section(self, key):
        self._current_section = key
        for section_key, btn in self.section_buttons.items():
            btn.setChecked(section_key == key)
        for section_key, btn in self.rail_buttons.items():
            btn.setChecked(section_key == key)
        self.content_stack.setCurrentWidget(self.pages[key])

    def is_collapsed(self):
        return self.panel_stack.currentIndex() == 1

    def _set_collapsed(self, collapsed, remember=True):
        if collapsed:
            # Remember the open width first, so expanding restores it.
            self._remember_panel_width()
            self.panel_stack.setCurrentIndex(1)
            self.panel_stack.setMinimumWidth(self.RAIL_WIDTH)
            self.panel_stack.setMaximumWidth(self.RAIL_WIDTH)
            self._set_splitter_panel_width(self.RAIL_WIDTH)
        else:
            self.panel_stack.setCurrentIndex(0)
            self.panel_stack.setMinimumWidth(self.PANEL_MIN_WIDTH)
            self.panel_stack.setMaximumWidth(self.PANEL_MAX_WIDTH)
            self._set_splitter_panel_width(self._restore_panel_width())
        if remember:
            self._store(self.PANEL_COLLAPSED_KEY, int(bool(collapsed)))

    # ------------------------------------------------------------ panel width

    def _set_splitter_panel_width(self, width):
        remaining = max(1, self.splitter.width() - width - self.splitter.handleWidth())
        self.splitter.setSizes([width, remaining])

    def _restore_panel_width(self):
        """Width the panel was last dragged to on this machine.

        Kept in QSettings rather than the account settings: it depends on the
        screen in front of the person, so it should not follow them to another
        device through sync.
        """
        try:
            stored = QSettings().value(self.PANEL_WIDTH_KEY)
            width = int(stored) if stored is not None else self.PANEL_DEFAULT_WIDTH
        except (TypeError, ValueError):
            width = self.PANEL_DEFAULT_WIDTH
        return max(self.PANEL_MIN_WIDTH, min(self.PANEL_MAX_WIDTH, width))

    def _restore_collapsed(self):
        try:
            return str(QSettings().value(self.PANEL_COLLAPSED_KEY, 0)) in ("1", "true", "True")
        except Exception:
            return False

    def _remember_panel_width(self, *_args):
        # A drag while collapsed is not a new panel width.
        if self.is_collapsed():
            return
        width = self.splitter.sizes()[0]
        if width <= 0:
            return
        self._store(self.PANEL_WIDTH_KEY, int(width))

    def _store(self, key, value):
        try:
            QSettings().setValue(key, value)
        except Exception:
            pass

    def showEvent(self, event):
        super().showEvent(event)
        # A splitter only honours setSizes once it knows its own width, which
        # is not true while the page is still being built.
        if not self._width_applied:
            self._width_applied = True
            self._set_splitter_panel_width(
                self.RAIL_WIDTH if self.is_collapsed() else self._restore_panel_width()
            )

    # ------------------------------------------------------------------ theme

    def apply_theme(self, theme):
        # The page keeps ChatGPT's neutral look under every app theme; the
        # theme is only remembered for pages that may need it later.
        self._theme = theme
        self.setStyleSheet(
            "#aiRoot{background:%s;}"
            # The splitter grip is invisible; the sidebar's own edge is the line.
            "QSplitter#aiSplitter::handle:horizontal{background:transparent;}"
            "QSplitter#aiSplitter::handle:horizontal:hover{background:%s;}"
            "#aiPanel,#aiRail{background:%s;border:none;border-right:1px solid %s;}"
            "#aiPanelTitle{font-size:17px;font-weight:600;color:%s;background:transparent;}"
            "#aiIconBtn{background:transparent;border:none;border-radius:8px;}"
            "#aiIconBtn:hover{background:%s;}"
            "#aiIconBtn:checked{background:%s;}"
            "#aiSection{text-align:left;padding-left:10px;font-size:14px;color:%s;"
            "background:transparent;border:none;border-radius:10px;}"
            "#aiSection:hover{background:%s;}"
            "#aiSection:checked{background:%s;}"
            "#aiPage{background:%s;border:none;}"
            "#aiPageTitle{font-size:20px;font-weight:600;color:%s;background:transparent;}"
            "#aiGreeting{font-size:28px;color:%s;background:transparent;}"
            "#aiChatView{background:transparent;color:%s;border:none;font-size:15px;}"
            "#aiInputBox{background:#ffffff;border:1px solid %s;border-radius:28px;}"
            "#aiChatInput{background:transparent;color:%s;border:none;font-size:15px;}"
            "#aiAttachBtn{background:transparent;border:none;border-radius:18px;}"
            "#aiSendBtn{background:#000000;border:none;border-radius:18px;}"
            "#aiSendBtn:disabled{background:#d7d7d7;}"
            % (
                PAGE_BG, LINE, SIDEBAR_BG, LINE, TEXT, HOVER_BG, ACTIVE_BG,
                TEXT, HOVER_BG, ACTIVE_BG, PAGE_BG, TEXT, TEXT, TEXT, LINE, TEXT,
            )
        )
