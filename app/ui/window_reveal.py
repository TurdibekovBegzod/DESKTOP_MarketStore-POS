"""Show the main window only once it is drawn at its final, maximized size.

On Windows ``showMaximized()`` first shows a new window at its normal size,
leaves it blank (white) while the pages build, paints it there, and only a
few hundred milliseconds later applies the maximize - so the window flashed
white, appeared small, then jumped to full screen.

The window is kept fully transparent until it fills the screen, has stopped
changing size and has been painted at that size; then it appears in one step.
"""

from PyQt6.QtCore import QEvent, QObject, QTimer

# How often the window is checked, and how many checks in a row it must pass.
_CHECK_MS = 16
_STABLE_CHECKS = 2
# Shown regardless after this long, so a platform that never maximizes (or a
# window that is never painted) cannot keep it invisible.
_GIVE_UP_MS = 1500
# A maximized window fills the screen's work area except its own title bar.
_MAX_FRAME_SLACK = 64


class _RevealWhenSettled(QObject):
    def __init__(self, window):
        super().__init__(window)
        self._window = window
        self._painted = False
        self._last_size = None
        self._stable = 0
        self._timer = QTimer(self)
        self._timer.setInterval(_CHECK_MS)
        self._timer.timeout.connect(self._check)
        window.installEventFilter(self)
        QTimer.singleShot(_GIVE_UP_MS, self.reveal)

    def eventFilter(self, obj, event):
        if obj is self._window:
            kind = event.type()
            if kind == QEvent.Type.Paint:
                self._painted = True
            elif kind == QEvent.Type.Resize:
                # What was painted belongs to the old size.
                self._painted = False
            elif kind == QEvent.Type.Show and not self._timer.isActive():
                self._timer.start()
        return False

    def _fills_screen(self):
        screen = self._window.screen()
        if screen is None:
            return True
        area = screen.availableGeometry()
        size = self._window.size()
        return (
            0 <= area.width() - size.width() <= _MAX_FRAME_SLACK
            and 0 <= area.height() - size.height() <= _MAX_FRAME_SLACK
        )

    def _check(self):
        size = self._window.size()
        settled = self._painted and size == self._last_size and self._fills_screen()
        self._stable = self._stable + 1 if settled else 0
        self._last_size = size
        if self._stable >= _STABLE_CHECKS:
            self.reveal()

    def reveal(self):
        window, self._window = self._window, None
        if window is None:
            return
        self._timer.stop()
        window.removeEventFilter(self)
        window.setWindowOpacity(1.0)
        self.deleteLater()


def show_maximized_without_flicker(window):
    """``window.showMaximized()``, minus the white flash and the size jump."""
    window.setWindowOpacity(0.0)
    _RevealWhenSettled(window)
    window.showMaximized()
