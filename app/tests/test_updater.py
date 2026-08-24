import unittest
import ssl
from PyQt6.QtWidgets import QApplication

import sys
import os

# Ensure app directory is on path
app_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if app_dir not in sys.path:
    sys.path.insert(0, app_dir)

from version import APP_VERSION, APP_NAME
from ssl_support import ca_bundle_path, create_ssl_context, verify_ca_bundle
from updater import (
    _asset_sha256,
    get_client_platform,
    is_newer_version,
    match_asset_for_platform,
    parse_version_tuple,
)
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

    def test_verified_https_context_has_trusted_certificates(self):
        context = create_ssl_context()
        self.assertIsInstance(context, ssl.SSLContext)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertGreater(len(context.get_ca_certs()), 0)
        self.assertTrue(verify_ca_bundle())
        if ca_bundle_path():
            self.assertTrue(os.path.isfile(ca_bundle_path()))

    def test_update_asset_must_match_platform_and_digest_must_be_sha256(self):
        assets = [{"name": "notes.txt"}, {"name": "MarketStore_Setup_1.2.3.exe"}]
        self.assertEqual(match_asset_for_platform(assets, "windows")["name"], assets[1]["name"])
        self.assertIsNone(match_asset_for_platform([assets[0]], "windows"))
        checksum = "a" * 64
        self.assertEqual(_asset_sha256({"digest": f"sha256:{checksum}"}), checksum)
        self.assertEqual(_asset_sha256({"digest": "md5:bad"}), "")

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

        english_dialog = UpdaterDialog(
            auto_start_check=False,
            update_data=test_update_data,
            language="en",
        )
        self.assertEqual(english_dialog.windowTitle(), "App update")
        self.assertIn("Download and update", english_dialog.primary_btn.text())


if __name__ == "__main__":
    unittest.main()
