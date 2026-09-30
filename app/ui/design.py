"""Shared building blocks for the neutral page design.

Pages built from these pieces mark their containers with the `designOwned`
property, so the main window's generic page theming leaves their styles alone.
"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QFontMetrics
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

TEXT = "#111110"
MUTED = "#6b6a66"
FAINT = "#8a8984"
LINE = "#e7e6e3"
FIELD = "#dcdad5"
SOFT = "#fafaf9"
RAIL = "#efeeeb"
SELECTED = "#e9e8e4"
GREEN = "#15803d"
AMBER = "#b45309"
RED = "#b91c1c"
BLUE = "#2f6fed"


def own(widget):
    """Tell the main window's page theming not to restyle this subtree."""
    widget.setProperty("designOwned", True)
    return widget


def card(object_name, padding=(18, 16, 18, 16), spacing=8, background="#ffffff", radius=14):
    """A white rounded panel with a hairline border."""
    frame = own(QFrame())
    frame.setObjectName(object_name)
    frame.setStyleSheet(
        f"QFrame#{object_name} {{ background:{background}; border:1px solid {LINE}; border-radius:{radius}px; }}"
        f" QFrame#{object_name} QLabel {{ background:transparent; border:none; }}"
    )
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(*padding)
    layout.setSpacing(spacing)
    return frame, layout


def label(text="", size=13, color=TEXT, weight=400, mono=False, skip_i18n=False):
    lbl = QLabel(text)
    family = "font-family:'Consolas','Cascadia Mono',monospace;" if mono else ""
    lbl.setStyleSheet(
        f"color:{color};font-size:{size}px;font-weight:{weight};{family}background:transparent;border:none;"
    )
    if skip_i18n:
        lbl.setProperty("i18n_skip", True)
    return lbl


def restyle_label(lbl, size=13, color=TEXT, weight=400):
    lbl.setStyleSheet(f"color:{color};font-size:{size}px;font-weight:{weight};background:transparent;border:none;")


def kpi_card(object_name, title, value="0"):
    """Title, a large figure and a small note underneath, as on the dashboards."""
    frame, layout = card(object_name, padding=(18, 14, 18, 14), spacing=4)
    title_lbl = label(title, 13, MUTED)
    value_lbl = label(value, 24, TEXT, 600, skip_i18n=True)
    note_lbl = label("", 12, FAINT, skip_i18n=True)
    layout.addWidget(title_lbl)
    layout.addWidget(value_lbl)
    layout.addWidget(note_lbl)
    return frame, title_lbl, value_lbl, note_lbl


def primary_button(text, height=38):
    btn = QPushButton(text)
    btn.setProperty("primary", True)
    btn.setFixedHeight(height)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(
        f"QPushButton {{ background:{TEXT}; color:white; border:none; border-radius:10px;"
        " padding:0 16px; font-size:13px; font-weight:600; }"
        " QPushButton:hover { background:#000000; }"
        " QPushButton:pressed { background:#3f3e3a; }"
        f" QPushButton:disabled {{ background:#e5e5e0; color:#a3a19b; }}"
    )
    return btn


def secondary_button(text, height=38):
    btn = QPushButton(text)
    btn.setFixedHeight(height)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(
        f"QPushButton {{ background:#ffffff; color:{TEXT}; border:1px solid {FIELD}; border-radius:10px;"
        " padding:0 14px; font-size:13px; font-weight:500; }"
        f" QPushButton:hover {{ background:{RAIL}; }}"
        f" QPushButton:pressed {{ background:{SELECTED}; }}"
        f" QPushButton:disabled {{ color:#a3a19b; background:#f2f1ee; border-color:{LINE}; }}"
    )
    return btn


def danger_text_button(text, height=34):
    btn = QPushButton(text)
    btn.setFixedHeight(height)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(
        f"QPushButton {{ background:transparent; color:{RED}; border:none; padding:0 8px;"
        " font-size:13px; font-weight:500; }"
        " QPushButton:hover { background:#fdecec; border-radius:8px; }"
    )
    return btn


def badge(text, background, color):
    lbl = QLabel(text)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setStyleSheet(
        f"background:{background};color:{color};border:none;border-radius:7px;"
        "padding:3px 9px;font-size:12px;font-weight:500;"
    )
    return lbl


class Segmented(QFrame):
    """Buttons on one grey rail; the chosen one is lifted out in white."""

    changed = pyqtSignal(object)

    def __init__(self, options, parent=None, height=34):
        super().__init__(parent)
        own(self)
        self.setObjectName("segmented")
        self.setStyleSheet(f"QFrame#segmented {{ background:{RAIL}; border:none; border-radius:11px; }}")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        self.buttons = {}
        self._value = None
        for text, value in options:
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setFixedHeight(height - 8)
            self._fit(btn, text)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton { background:transparent; color:#4a4945; border:none; border-radius:8px;"
                " padding:0 14px; font-size:13px; }"
                f" QPushButton:hover {{ color:{TEXT}; }}"
                f" QPushButton:checked {{ background:#ffffff; color:{TEXT}; font-weight:500; }}"
            )
            btn.clicked.connect(lambda _checked=False, v=value: self.set_value(v, emit=True))
            layout.addWidget(btn)
            self.buttons[value] = btn

    def value(self):
        return self._value

    def set_value(self, value, emit=False):
        self._value = value
        for key, btn in self.buttons.items():
            btn.setChecked(key == value)
        if emit:
            self.changed.emit(value)

    def set_texts(self, texts):
        for value, text in texts.items():
            if value in self.buttons:
                self.buttons[value].setText(text)
                self._fit(self.buttons[value], text)

    @staticmethod
    def _fit(btn, text):
        """Never let a crowded toolbar squeeze a choice below its own text."""
        font = QFont(btn.font())
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.DemiBold)
        btn.setMinimumWidth(QFontMetrics(font).horizontalAdvance(text) + 40)


def hline():
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet(f"background:{LINE};border:none;")
    return line


def empty_state(text):
    wrap = own(QWidget())
    layout = QVBoxLayout(wrap)
    layout.setContentsMargins(0, 24, 0, 24)
    lbl = label(text, 13, FAINT)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setWordWrap(True)
    layout.addWidget(lbl)
    return wrap
