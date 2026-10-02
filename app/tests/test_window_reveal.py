"""The main window appears once, already full size and painted.

On Windows a maximized window used to show first at its normal size, blank,
and jump to full screen a moment later - a visible flicker on every start.
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class WindowRevealTest(unittest.TestCase):
    def setUp(self):
        from PyQt6.QtWidgets import QApplication, QWidget

        self.app = QApplication.instance() or QApplication([])
        self.window = QWidget()
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.close)

    def _pump(self, milliseconds):
        from PyQt6.QtCore import QEventLoop, QTimer

        loop = QEventLoop()
        QTimer.singleShot(milliseconds, loop.quit)
        loop.exec()

    def test_the_window_stays_invisible_until_it_is_drawn_full_size(self):
        from PyQt6.QtCore import QEvent, QObject
        from ui.window_reveal import show_maximized_without_flicker

        seen = []
        window = self.window

        class _Watch(QObject):
            def eventFilter(self, obj, event):
                if obj is window and event.type() in (QEvent.Type.Paint, QEvent.Type.Resize):
                    seen.append((event.type(), window.size(), window.windowOpacity()))
                return False

        watch = _Watch()
        window.installEventFilter(watch)
        show_maximized_without_flicker(window)
        self.assertEqual(window.windowOpacity(), 0.0)

        self._pump(400)

        self.assertEqual(window.windowOpacity(), 1.0)
        final = window.size()
        # Nothing was shown at any other size than the final one.
        shown_sizes = {size for _, size, opacity in seen if opacity > 0}
        self.assertLessEqual(shown_sizes, {final})

    def test_a_window_that_never_settles_is_shown_anyway(self):
        from ui import window_reveal

        window_reveal._RevealWhenSettled(self.window)
        self.window.setWindowOpacity(0.0)
        # Never shown, so never painted: only the give-up timer can reveal it.
        self._pump(window_reveal._GIVE_UP_MS + 200)
        self.assertEqual(self.window.windowOpacity(), 1.0)


if __name__ == "__main__":
    unittest.main()
