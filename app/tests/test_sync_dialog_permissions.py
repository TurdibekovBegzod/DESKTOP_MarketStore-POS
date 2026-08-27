"""Replacing the server copy is an admin action.

The sync button sits in the top bar for every role, so without this guard a
cashier is one dialog and one click away from wiping the account on every
device in the shop.
"""

import os
import tempfile
import unittest

import database as db


class SyncDialogPermissionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.old_path = db.DB_PATH
        db.DB_PATH = self.path
        db.init_db()

    def tearDown(self):
        try:
            db._get_engine().dispose()
        finally:
            db.DB_PATH = self.old_path
            for suffix in ("", "-shm", "-wal"):
                path = self.path + suffix
                if os.path.exists(path):
                    os.remove(path)

    def _dialog_for(self, role):
        """Build the dialog against a stand-in parent.

        A real MainWindow starts timers and a realtime thread and aborts Qt when
        torn down per test; SyncDialog only reads `user`, `labels`, `settings`
        and calls back into two methods, so a stub exercises the same code.
        """
        from PyQt6.QtWidgets import QApplication, QWidget
        from ui.main_window import SyncDialog, TEXTS

        self.app = QApplication.instance() or QApplication([])

        class _ParentStub(QWidget):
            def __init__(self):
                super().__init__()
                self.user = {"id": 1, "role": role, "email": "a@b.uz"}
                self.labels = TEXTS["uz"]
                self.settings = {"theme": "dark_blue"}
                self.resolved = []

            def _sync_available(self):
                return True

            def _resolve_conflict(self, action):
                self.resolved.append(action)

        parent = _ParentStub()
        self.addCleanup(parent.deleteLater)
        dialog = SyncDialog(parent)
        self.addCleanup(dialog.deleteLater)
        return parent, dialog

    def test_an_admin_can_replace_the_server_copy(self):
        _, dialog = self._dialog_for("admin")
        self.assertIsNotNone(dialog.replace_btn)

    def test_a_cashier_is_not_offered_the_button(self):
        _, dialog = self._dialog_for("cashier")
        self.assertIsNone(dialog.replace_btn)

    def test_a_cashier_calling_it_directly_is_refused(self):
        """Hiding the button is not the guard - the handler is."""
        parent, dialog = self._dialog_for("cashier")

        dialog._replace_server()

        self.assertEqual(parent.resolved, [], "kassir uchun bajarilmasligi kerak")

    def test_the_ordinary_sync_actions_stay_available_to_a_cashier(self):
        _, dialog = self._dialog_for("cashier")
        self.assertIsNotNone(dialog.pull_btn)
        self.assertIsNotNone(dialog.push_btn)


if __name__ == "__main__":
    unittest.main()
