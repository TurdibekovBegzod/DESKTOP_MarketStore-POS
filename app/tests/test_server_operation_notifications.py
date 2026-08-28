"""Server-backed UI operations only turn green after the push succeeds."""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

import sync_service
from ui.main_window import MainWindow, ToastItem


_app = QApplication.instance() or QApplication([])


class _ToastRecorder:
    def __init__(self):
        self.updates = []

    def update_content(self, message, title=None, level="success", duration_ms=4000):
        self.updates.append({
            "message": message,
            "title": title,
            "level": level,
            "duration_ms": duration_ms,
        })


class _OperationWindow:
    def __init__(self):
        self.toast = _ToastRecorder()
        self.operation = {
            "toast": self.toast,
            "success_message": "Serverda saqlandi",
            "failure_message": "Serverda saqlanmadi",
            "success_title": "Tayyor",
            "failure_title": "Xatolik",
            "tables": {"products"},
            "committed": True,
        }
        self._pending_server_operations = [self.operation]

    def fail_server_operation(self, operation, error=None):
        MainWindow.fail_server_operation(self, operation, error)


class ServerOperationNotificationTest(unittest.TestCase):
    def test_toast_changes_from_yellow_to_green_in_place(self):
        toast = ToastItem("Yuborilmoqda", level="warning", duration_ms=60_000)
        self.assertIn("#fffbeb", toast.styleSheet())

        toast.update_content("Serverda saqlandi", level="success")

        self.assertEqual(toast.msg_lbl.text(), "Serverda saqlandi")
        self.assertEqual(toast.icon_lbl.text(), "✅")
        self.assertIn("#ecfdf5", toast.styleSheet())
        toast.dismiss()

    def test_pending_outbox_keeps_notification_yellow(self):
        window = _OperationWindow()
        with patch("ui.main_window.db.count_pending_sync_rows", return_value=1):
            MainWindow._settle_server_operations(window, {"pushed": 1})

        self.assertEqual(window.toast.updates, [])
        self.assertEqual(window._pending_server_operations, [window.operation])

    def test_empty_outbox_turns_same_notification_green(self):
        window = _OperationWindow()
        with patch("ui.main_window.db.count_pending_sync_rows", return_value=0):
            MainWindow._settle_server_operations(window, {"pushed": 1})

        self.assertEqual(window.toast.updates[-1]["level"], "success")
        self.assertEqual(window._pending_server_operations, [])

    def test_rejected_product_turns_notification_red(self):
        window = _OperationWindow()
        MainWindow._settle_server_operations(
            window,
            {"rejected": [{"table_name": "products"}]},
        )

        self.assertEqual(window.toast.updates[-1]["level"], "error")
        self.assertEqual(window._pending_server_operations, [])

    def test_close_flushes_a_committed_visible_operation(self):
        class Window:
            user = {"id": "owner", "api_access_token": "token"}
            _pending_server_operations = [{"committed": True}]

            @staticmethod
            def _sync_available():
                return True

            def _settle_server_operations(self, outcome):
                self.settled = outcome

        window = Window()
        outcome = {"pulled": 0, "pushed": 1}
        with patch("ui.main_window.db.count_pending_sync_rows", return_value=1), \
             patch.object(sync_service, "auto_sync_turn", return_value=outcome) as sync, \
             patch("ui.main_window.db.record_sync_success") as record:
            MainWindow._flush_pending_before_close(window)

        sync.assert_called_once_with(window.user)
        record.assert_called_once_with(outcome)
        self.assertEqual(window.settled, outcome)


if __name__ == "__main__":
    unittest.main()
