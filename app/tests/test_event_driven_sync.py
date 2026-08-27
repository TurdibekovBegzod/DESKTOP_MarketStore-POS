"""Event-driven desktop sync must neither poll nor miss a real remote write."""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

import database as db
import sync_service
from ui.async_loader import AsyncDataLoader
from ui.main_window import MainWindow


_app = QApplication.instance() or QApplication([])


class _EngineRecorder:
    def __init__(self):
        self.requests = 0

    def request_turn(self):
        self.requests += 1


class _WindowStub:
    def __init__(self):
        self._engine_worker = _EngineRecorder()
        self._assets_checked_generation = None
        self.refreshes = 0

    def _refresh_sync_status(self):
        self.refreshes += 1

    def _apply_remote_assets(self, *_args, **_kwargs):
        raise AssertionError("A product event must not refresh assets")


class EventDrivenSyncTest(unittest.TestCase):
    def test_a_copied_device_key_cannot_hide_newer_server_data(self):
        window = _WindowStub()
        payload = {
            "generation": 8,
            "device_key": "desktop-copied",
            "tables": ["products", "sales", "sale_items"],
        }
        with patch.object(sync_service, "apply_server_control", return_value={"purged": False}), \
             patch.object(db, "get_sync_generation", return_value=7), \
             patch.object(db, "get_sync_device_key", return_value="desktop-copied"), \
             patch.object(db, "get_remote_change", return_value={"pending": False}), \
             patch.object(db, "mark_remote_change") as mark:
            MainWindow._on_remote_change(window, payload)

        mark.assert_called_once()
        self.assertEqual(window._engine_worker.requests, 1)

    def test_an_already_applied_own_event_does_not_download_again(self):
        window = _WindowStub()
        payload = {
            "generation": 8,
            "device_key": "desktop-this-one",
            "tables": ["products"],
        }
        with patch.object(sync_service, "apply_server_control", return_value={"purged": False}), \
             patch.object(db, "get_sync_generation", return_value=8), \
             patch.object(db, "get_sync_device_key", return_value="desktop-this-one"), \
             patch.object(db, "clear_remote_change") as clear:
            MainWindow._on_remote_change(window, payload)

        clear.assert_called_once()
        self.assertEqual(window._engine_worker.requests, 0)

    def test_a_pending_fresh_read_discards_the_older_table_snapshot(self):
        owner = QWidget()
        loader = AsyncDataLoader(owner)
        painted = []
        loader.apply_fn = painted.append
        loader.pending = (lambda: "fresh", painted.append)

        with patch("ui.async_loader.QTimer.singleShot") as schedule:
            loader._apply_result("stale")

        self.assertEqual(painted, [])
        schedule.assert_called_once()


if __name__ == "__main__":
    unittest.main()
