"""Login must survive a flaky tunnel without ever accepting a wrong password."""

import inspect
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import api_client
import database as db


def _http_error(code, detail):
    body = json.dumps({"detail": detail}).encode("utf-8")
    return HTTPError("https://example.test", code, "error", {}, io.BytesIO(body))


class ApiClientAuthErrorsTest(unittest.TestCase):
    def _login_raising(self, error):
        calls = {"count": 0}

        def fake_urlopen(request, timeout=None, context=None):
            calls["count"] += 1
            raise error() if callable(error) else error

        with patch("api_client.urlopen", side_effect=fake_urlopen), \
                patch("api_client._RETRY_BACKOFF_SECONDS", (0, 0)):
            with self.assertRaises(api_client.ApiClientError) as caught:
                api_client.login("user@shop.uz", "123456")
        return caught.exception, calls["count"]

    def test_bad_credentials_are_reported_as_an_auth_error_without_retrying(self):
        error, attempts = self._login_raising(
            lambda: _http_error(401, "Invalid email or password")
        )
        self.assertIsInstance(error, api_client.ApiAuthError)
        self.assertNotIsInstance(error, api_client.ApiOfflineError)
        self.assertEqual(attempts, 1)

    def test_unverified_account_gets_its_own_error(self):
        error, _ = self._login_raising(
            lambda: _http_error(403, "Email verification is required")
        )
        self.assertIsInstance(error, api_client.ApiVerificationRequiredError)
        self.assertIn("tasdiqlanmagan", str(error).lower())

    def test_a_dead_tunnel_is_offline_not_a_wrong_password(self):
        for code in (404, 500, 502, 503, 504):
            with self.subTest(code=code):
                error, attempts = self._login_raising(lambda code=code: _http_error(code, "gateway"))
                self.assertIsInstance(error, api_client.ApiOfflineError)
                self.assertGreater(attempts, 1, "transport failures must be retried")

    def test_transport_failures_are_retried_then_reported_as_offline(self):
        error, attempts = self._login_raising(URLError("name resolution failed"))
        self.assertIsInstance(error, api_client.ApiOfflineError)
        self.assertEqual(attempts, api_client.AUTH_RETRIES + 1)

    def test_email_is_normalised_before_it_reaches_the_server(self):
        seen = {}

        def fake_urlopen(request, timeout=None, context=None):
            seen["payload"] = json.loads(request.data.decode("utf-8"))
            raise _http_error(401, "Invalid email or password")

        with patch("api_client.urlopen", side_effect=fake_urlopen):
            with self.assertRaises(api_client.ApiClientError):
                api_client.login("  User@Shop.UZ ", "123456")
        self.assertEqual(seen["payload"]["email"], "user@shop.uz")

    def test_obviously_invalid_input_never_reaches_the_network(self):
        with patch("api_client.urlopen") as urlopen:
            for email, password in [("", "123456"), ("nope", "123456"), ("a@b.uz", "")]:
                with self.subTest(email=email, password=password):
                    with self.assertRaises(api_client.ApiClientError):
                        api_client.login(email, password)
            urlopen.assert_not_called()


class NoLocalPasswordTest(unittest.TestCase):
    """Signing in is server-side only: nothing about the password is kept here."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.old_path = db.DB_PATH
        db.DB_PATH = self.path
        db.init_db()
        self.user_id = db.add_user(
            email="eliboyakbar@gmail.com", password="unused", role="admin", username="Eli"
        )

    def tearDown(self):
        try:
            db._get_engine().dispose()
        finally:
            db.DB_PATH = self.old_path
            for suffix in ("", "-shm", "-wal"):
                path = self.path + suffix
                if os.path.exists(path):
                    os.remove(path)

    def test_there_is_no_offline_credential_api_left(self):
        for name in (
            "store_offline_credential",
            "verify_offline_credential",
            "OFFLINE_CREDENTIAL_KEY",
            "find_account_session",
        ):
            self.assertFalse(hasattr(db, name), f"{name} must be gone")

    def test_a_hash_left_by_an_older_build_is_wiped_on_startup(self):
        with db.session_scope() as session:
            session.add(db.UserSetting(
                user_id=self.user_id, key="offline_password_hash", value="pbkdf2_sha256$x$y"
            ))
        db.init_db()
        with db.session_scope() as session:
            leftover = session.get(
                db.UserSetting, {"user_id": self.user_id, "key": "offline_password_hash"}
            )
        self.assertIsNone(leftover)

    def test_the_login_dialog_has_no_offline_path(self):
        import ui.login_dialog as login_dialog

        source = inspect.getsource(login_dialog)
        self.assertNotIn("offline_credential", source)
        self.assertFalse(hasattr(login_dialog.LoginDialog, "_try_offline_login"))


if __name__ == "__main__":
    unittest.main()
