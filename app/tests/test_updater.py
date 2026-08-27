import unittest
import ssl
import tempfile
from unittest.mock import patch
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
    normalize_api_root,
    parse_version_tuple,
    apply_and_restart,
    validate_update_package,
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

    def test_legacy_server_url_moves_to_production_ngrok_endpoint(self):
        expected = "https://drinking-relight-trailside.ngrok-free.dev"
        self.assertEqual(normalize_api_root("http://169.58.152.33:8000"), expected)
        self.assertEqual(normalize_api_root(f"{expected}/api/v1"), expected)

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

    def test_the_app_stays_open_until_the_installer_closes_it(self):
        """Closing first is what made the update fail.

        The installer asks Windows for administrator rights, and the consent
        prompt belongs to whoever asked. Quitting a moment later killed the
        request before the person could answer, so the app disappeared and
        nothing was installed. The installer closes the app itself when it is
        ready to replace the files.
        """
        handle, path = tempfile.mkstemp(suffix=".exe")
        os.close(handle)
        try:
            with open(path, "wb") as package:
                package.write(b"MZ" + b"\0" * 2048)
            with patch("updater.get_client_platform", return_value="windows"), \
                 patch.object(os, "startfile", create=True) as startfile, \
                 patch("PyQt6.QtCore.QTimer.singleShot") as single_shot:
                self.assertTrue(apply_and_restart(path))
            startfile.assert_called_once_with(path)
            single_shot.assert_not_called()

            from PyQt6.QtWidgets import QApplication
            self.assertIsNotNone(QApplication.instance(), "the app must still be running")
        finally:
            os.remove(path)

    def test_the_installer_is_never_started_as_a_child_of_the_app(self):
        """A child would be taken down by the installer's own taskkill."""
        handle, path = tempfile.mkstemp(suffix=".exe")
        os.close(handle)
        try:
            with open(path, "wb") as package:
                package.write(b"MZ" + b"\0" * 2048)
            with patch("updater.get_client_platform", return_value="windows"), \
                 patch.object(os, "startfile", create=True, side_effect=OSError("no shell")), \
                 patch("updater.subprocess.Popen") as popen:
                self.assertTrue(apply_and_restart(path))
            popen.assert_called_once()
            self.assertNotIn("start_new_session", popen.call_args.kwargs)
        finally:
            os.remove(path)

    def test_a_package_that_is_not_an_installer_never_reaches_the_shell(self):
        handle, path = tempfile.mkstemp(suffix=".exe")
        os.close(handle)
        try:
            with open(path, "wb") as package:
                package.write(b"<html>gateway error</html>" * 100)
            with patch("updater.get_client_platform", return_value="windows"), \
                 patch.object(os, "startfile", create=True) as startfile:
                with self.assertRaises(ValueError):
                    apply_and_restart(path)
            startfile.assert_not_called()
        finally:
            os.remove(path)

    def test_invalid_download_is_rejected_before_installer_launch(self):
        handle, path = tempfile.mkstemp(suffix=".exe")
        os.close(handle)
        try:
            with open(path, "wb") as package:
                package.write(b"<html>gateway error</html>" * 100)
            with self.assertRaises(ValueError):
                validate_update_package(path, "windows")
        finally:
            os.remove(path)

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
