"""The control panel must say why it cannot be used.

Without a password configured on the server the panel answered every login
with a generic failure, which reads like a mistyped password rather than a
server that was never set up. These tests pin the plainer answer.
"""

import os
import unittest

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-000")
os.environ.setdefault("TRUSTED_HOSTS", "localhost,127.0.0.1,testserver")

from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402


class SuperadminAvailabilityTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.previous = os.environ.get("SUPERADMIN_PASSWORD")
        # An explicit empty value overrides a developer's real api/.env. Tests
        # must never inherit control-panel credentials from the host machine.
        os.environ["SUPERADMIN_PASSWORD"] = ""
        get_settings.cache_clear()

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("SUPERADMIN_PASSWORD", None)
        else:
            os.environ["SUPERADMIN_PASSWORD"] = self.previous
        get_settings.cache_clear()

    def test_an_unconfigured_server_says_so_without_a_login_attempt(self):
        payload = self.client.get("/api/v1/superadmin/availability").json()

        self.assertFalse(payload["enabled"])
        self.assertIn("SUPERADMIN_PASSWORD", payload["message"])

    def test_the_login_failure_names_the_missing_setting(self):
        response = self.client.post(
            "/api/v1/superadmin/login", json={"username": "superadmin", "password": "x"}
        )

        self.assertEqual(response.status_code, 503)
        self.assertIn("SUPERADMIN_PASSWORD", response.json()["detail"])

    def test_a_configured_server_reports_itself_available(self):
        os.environ["SUPERADMIN_PASSWORD"] = "hunter2"
        get_settings.cache_clear()

        payload = self.client.get("/api/v1/superadmin/availability").json()
        self.assertTrue(payload["enabled"])
        self.assertEqual(payload["message"], "")

        good = self.client.post(
            "/api/v1/superadmin/login", json={"username": "superadmin", "password": "hunter2"}
        )
        self.assertEqual(good.status_code, 200)
        self.assertTrue(good.json()["access_token"])

    def test_a_wrong_password_is_reported_as_a_wrong_password(self):
        os.environ["SUPERADMIN_PASSWORD"] = "hunter2"
        get_settings.cache_clear()

        response = self.client.post(
            "/api/v1/superadmin/login", json={"username": "superadmin", "password": "nope"}
        )

        self.assertEqual(response.status_code, 401)
        self.assertNotIn("SUPERADMIN_PASSWORD", response.json()["detail"])

    def test_the_page_and_its_assets_are_served(self):
        for path in ("/superadmin", "/superadmin/assets/admin.css", "/superadmin/assets/admin.js"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)


if __name__ == "__main__":
    unittest.main()
