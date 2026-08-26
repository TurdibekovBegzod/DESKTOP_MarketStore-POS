"""A published release must light up every running client, and only once."""

import io
import os
import tempfile
import unittest

import api_client
import database as db


SSE_STREAM = (
    b'event: hello\ndata: {"generation": 3, "release": '
    b'{"tag": "v1.1.1", "latest_version": "1.1.1"}}\n\n'
    b': keepalive comment\n\n'
    b'event: ping\ndata: {"generation": 3}\n\n'
    b'event: release\ndata: {"tag": "v1.1.2", "latest_version": "1.1.2", '
    b'"name": "MarketStore POS v1.1.2"}\n\n'
)


class ReleaseStreamParsingTest(unittest.TestCase):
    def test_the_stream_is_split_into_hello_ping_and_release(self):
        events = list(api_client.iter_sse_events(io.BytesIO(SSE_STREAM)))
        self.assertEqual([name for name, _ in events], ["hello", "ping", "release"])

        hello = events[0][1]
        self.assertEqual(hello["release"]["latest_version"], "1.1.1")
        self.assertEqual(events[2][1]["latest_version"], "1.1.2")

    def test_a_truncated_frame_does_not_raise(self):
        list(api_client.iter_sse_events(io.BytesIO(b'event: release\ndata: {"tag"')))


class ReleaseBadgeTest(unittest.TestCase):
    """The badge is driven by what is installed, so it clears itself.

    MainWindow is far too heavy to build per test, so the real methods are
    bound to a stand-in holding just the two widgets they touch.
    """

    def setUp(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.old_path = db.DB_PATH
        db.DB_PATH = self.path
        db.init_db()

        from PyQt6.QtWidgets import QApplication, QLabel, QPushButton
        from ui.main_window import MainWindow

        self.app = QApplication.instance() or QApplication([])
        self.window_cls = MainWindow

        class _Sidebar:
            """Only what _refresh_release_badge and _position_release_dot use."""

            labels = {"release_badge_tooltip": "Yangi versiya mavjud: {v}"}
            pending_release_count = MainWindow.pending_release_count
            _counter_badge_style = MainWindow._counter_badge_style
            _refresh_release_badge = MainWindow._refresh_release_badge
            _position_release_dot = MainWindow._position_release_dot

            def __init__(self):
                self.user_menu_btn = QPushButton()
                self.user_menu_btn.setFixedWidth(180)
                self.release_dot_lbl = QLabel(self.user_menu_btn)
                self.release_dot_lbl.setFixedSize(16, 16)
                self.release_dot_lbl.hide()

        self.sidebar = _Sidebar()
        self.addCleanup(self.sidebar.user_menu_btn.deleteLater)

    def tearDown(self):
        try:
            db._get_engine().dispose()
        finally:
            db.DB_PATH = self.old_path
            for suffix in ("", "-shm", "-wal"):
                path = self.path + suffix
                if os.path.exists(path):
                    os.remove(path)

    def _badge_shown(self):
        # The button is never shown, and a child of a hidden parent always
        # reports isVisible() == False - ask whether it was hidden instead.
        return not self.sidebar.release_dot_lbl.isHidden()

    def _menu_badge(self):
        from PyQt6.QtWidgets import QLabel, QMenu
        from ui.main_window import THEMES

        menu = QMenu()
        self.addCleanup(menu.deleteLater)
        action = self.window_cls._menu_button_action(
            self.sidebar, menu, "Yangilanishlar", lambda: None, THEMES["dark_blue"],
            width=180, badge=self.sidebar.pending_release_count(),
        )
        label = action.defaultWidget().findChild(QLabel)
        return label.text() if label else None

    def test_a_new_release_shows_one_on_the_account_and_in_the_menu(self):
        self.assertEqual(self.sidebar.pending_release_count(), 0)
        self.assertFalse(self._badge_shown())
        self.assertIsNone(self._menu_badge())

        db.set_known_release("9.9.9", tag="v9.9.9")
        self.sidebar._refresh_release_badge()

        self.assertEqual(self.sidebar.pending_release_count(), 1)
        self.assertTrue(self._badge_shown())
        self.assertEqual(self.sidebar.release_dot_lbl.text(), "1")
        self.assertEqual(self._menu_badge(), "1")
        self.assertIn("9.9.9", self.sidebar.user_menu_btn.toolTip())

    def test_the_same_release_announced_again_still_counts_one(self):
        for _ in range(3):
            db.set_known_release("9.9.9", tag="v9.9.9")
            self.sidebar._refresh_release_badge()
        self.assertEqual(self.sidebar.release_dot_lbl.text(), "1")
        self.assertEqual(self._menu_badge(), "1")

    def test_both_badges_clear_once_that_version_is_installed(self):
        from version import APP_VERSION

        db.set_known_release("9.9.9", tag="v9.9.9")
        self.sidebar._refresh_release_badge()
        self.assertTrue(self._badge_shown())

        # The user installed it: the running build now matches the release.
        db.set_known_release(APP_VERSION, tag=f"v{APP_VERSION}")
        self.sidebar._refresh_release_badge()

        self.assertFalse(self._badge_shown())
        self.assertIsNone(self._menu_badge())
        self.assertEqual(self.sidebar.user_menu_btn.toolTip(), "")

    def test_an_older_release_never_lights_the_badge(self):
        db.set_known_release("0.0.1", tag="v0.0.1")
        self.sidebar._refresh_release_badge()
        self.assertFalse(self._badge_shown())
        self.assertIsNone(self._menu_badge())


if __name__ == "__main__":
    unittest.main()
