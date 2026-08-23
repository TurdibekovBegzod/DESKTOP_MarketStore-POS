import unittest
from PyQt6.QtWidgets import QApplication

import sys
import os

# Ensure app directory is on path
app_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if app_dir not in sys.path:
    sys.path.insert(0, app_dir)

from version import APP_VERSION, APP_NAME
from updater import parse_version_tuple, is_newer_version, get_client_platform
from ui.updater_dialog import UpdaterDialog

class TestUpdaterModule(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_version_constants(self):
        self.assertTrue(len(APP_VERSION) > 0)
        self.assertEqual(APP_NAME, "MarketStore POS")

    def test_version_parsing_and_comparison(self):
        self.assertEqual(parse_version_tuple("1.0.0"), (1, 0, 0))
        self.assertEqual(parse_version_tuple("v2.15.3"), (2, 15, 3))
        self.assertEqual(parse_version_tuple("1.2"), (1, 2, 0))

        self.assertTrue(is_newer_version("1.0.1", "1.0.0"))
        self.assertTrue(is_newer_version("v2.0.0", "1.9.9"))
        self.assertTrue(is_newer_version("1.10.0", "1.9.0"))

        self.assertFalse(is_newer_version("1.0.0", "1.0.0"))
        self.assertFalse(is_newer_version("0.9.9", "1.0.0"))
        self.assertFalse(is_newer_version("1.0.0", "1.0.1"))

    def test_platform_detection(self):
        platform_name = get_client_platform()
        self.assertIn(platform_name, ["windows", "linux", "macos"])

    def test_updater_dialog_ui(self):
        test_update_data = {
            "has_update": True,
            "latest_version": "1.0.1",
            "release_notes": "Test notes",
            "download_url": "/api/v1/app/download?platform=windows",
            "file_name": "MarketStore_Setup.exe",
            "file_size": 1024 * 1024 * 5,
        }
        dialog = UpdaterDialog(auto_start_check=False, update_data=test_update_data)
        self.assertIn("1.0.1", dialog.status_badge.text())
        self.assertEqual(dialog.notes_edit.toPlainText(), "Test notes")
        self.assertTrue(dialog.primary_btn.isEnabled())


if __name__ == "__main__":
    unittest.main()
