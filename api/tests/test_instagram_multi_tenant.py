"""Tests for database-backed multi-tenant Instagram AI bot functionality."""

import unittest
from unittest.mock import MagicMock, patch

from app.instagram_api import InstagramNotConfiguredError, send_message
from app.instagram_service import (
    InstagramAccountConfig,
    clear_connection_cache,
    get_account_config_by_id,
    verify_instagram_connection,
)
from app.routers import instagram
from app import tasks


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class FakeDbSession:
    def __init__(self, match_row=None, settings_rows=()):
        self.match_row = match_row
        self.settings_rows = list(settings_rows)
        self.queries = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.queries.append((sql, params or {}))
        if "SELECT r.user_id, r.user_uid, u.email" in sql:
            return FakeResult([self.match_row] if self.match_row else [])
        if "SELECT r.local_id, r.data ->> 'value'" in sql:
            return FakeResult(self.settings_rows)
        return FakeResult([])

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class MultiTenantAccountLookupTest(unittest.TestCase):
    def test_known_account_loads_full_settings(self):
        session = FakeDbSession(
            match_row=(10, "uid-10", "owner@example.com"),
            settings_rows=[
                ("instagram_account_id", "1784140001"),
                ("instagram_access_token", "EAAtesttoken123"),
                ("instagram_app_secret", "secret456"),
                ("instagram_auto_reply", "1"),
                ("gemini_api_key", "AIzaTestKey"),
            ],
        )
        with patch("app.instagram_service.SessionLocal", return_value=session):
            config = get_account_config_by_id("1784140001")

        self.assertIsNotNone(config)
        self.assertEqual(config.user_id, 10)
        self.assertEqual(config.user_uid, "uid-10")
        self.assertEqual(config.email, "owner@example.com")
        self.assertEqual(config.account_id, "1784140001")
        self.assertEqual(config.access_token, "EAAtesttoken123")
        self.assertEqual(config.app_secret, "secret456")
        self.assertTrue(config.auto_reply)
        self.assertEqual(config.gemini_api_key, "AIzaTestKey")

    def test_account_with_auto_reply_disabled_is_parsed_correctly(self):
        session = FakeDbSession(
            match_row=(10, "uid-10", "owner@example.com"),
            settings_rows=[
                ("instagram_account_id", "1784140002"),
                ("instagram_access_token", "EAAtesttoken"),
                ("instagram_auto_reply", "0"),
            ],
        )
        with patch("app.instagram_service.SessionLocal", return_value=session):
            config = get_account_config_by_id("1784140002")

        self.assertIsNotNone(config)
        self.assertFalse(config.auto_reply)

    def test_unknown_account_returns_none(self):
        session = FakeDbSession(match_row=None)
        with patch("app.instagram_service.SessionLocal", return_value=session):
            config = get_account_config_by_id("9999999999")

        self.assertIsNone(config)


class InstagramConnectionVerificationTest(unittest.TestCase):
    def setUp(self):
        clear_connection_cache()

    def test_successful_meta_verification_is_cached(self):
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {"id": "1784140001", "username": "myshop"}

        with patch("httpx.get", return_value=mock_response) as get_mock:
            # 1st call: makes HTTP request
            self.assertTrue(verify_instagram_connection("EAAtesttoken", "1784140001"))
            self.assertEqual(get_mock.call_count, 1)

            # 2nd call: returns from cache, does not repeat HTTP call
            self.assertTrue(verify_instagram_connection("EAAtesttoken", "1784140001"))
            self.assertEqual(get_mock.call_count, 1)

    def test_invalid_token_returns_false(self):
        mock_response = MagicMock(status_code=400, text="OAuthException")
        mock_response.json.return_value = {"error": {"message": "Invalid OAuth access token"}}

        with patch("httpx.get", return_value=mock_response):
            self.assertFalse(verify_instagram_connection("bad-token", "1784140001"))

    def test_empty_token_returns_false_immediately(self):
        with patch("httpx.get") as get_mock:
            self.assertFalse(verify_instagram_connection(""))
            get_mock.assert_not_called()


class WebhookHandlingMultiTenantTest(unittest.TestCase):
    def _event(self, account_id="1784140001", text="iPhone narxi?"):
        return {
            "type": "message",
            "account_id": account_id,
            "sender_id": "cust-555",
            "text": text,
        }

    def test_event_is_queued_when_account_has_valid_settings(self):
        cfg = InstagramAccountConfig(
            user_id=1,
            user_uid="u-1",
            email="shop@pos.uz",
            account_id="1784140001",
            access_token="tok-1",
            app_secret="sec-1",
            auto_reply=True,
            gemini_api_key="gem-1",
        )
        with patch("app.routers.instagram.get_account_config_by_id", return_value=cfg), \
             patch.object(instagram, "reply_to_instagram_dm_task") as task:
            instagram.handle_event(self._event())

        task.delay.assert_called_once_with("1784140001", "cust-555", "iPhone narxi?")

    def test_event_is_dropped_if_account_not_in_database(self):
        with patch("app.routers.instagram.get_account_config_by_id", return_value=None), \
             patch.object(instagram, "get_settings") as get_set, \
             patch.object(instagram, "reply_to_instagram_dm_task") as task:
            get_set.return_value.instagram_auto_reply = False
            instagram.handle_event(self._event(account_id="unregistered-account"))

        task.delay.assert_not_called()

    def test_event_is_dropped_if_auto_reply_disabled_for_account(self):
        cfg = InstagramAccountConfig(
            user_id=1,
            user_uid="u-1",
            email="shop@pos.uz",
            account_id="1784140001",
            access_token="tok-1",
            app_secret=None,
            auto_reply=False,  # OFF
            gemini_api_key="gem-1",
        )
        with patch("app.routers.instagram.get_account_config_by_id", return_value=cfg), \
             patch.object(instagram, "reply_to_instagram_dm_task") as task:
            instagram.handle_event(self._event())

        task.delay.assert_not_called()

    def test_event_is_dropped_if_no_access_token_for_account(self):
        cfg = InstagramAccountConfig(
            user_id=1,
            user_uid="u-1",
            email="shop@pos.uz",
            account_id="1784140001",
            access_token="",  # MISSING
            app_secret=None,
            auto_reply=True,
            gemini_api_key="gem-1",
        )
        with patch("app.routers.instagram.get_account_config_by_id", return_value=cfg), \
             patch.object(instagram, "reply_to_instagram_dm_task") as task:
            instagram.handle_event(self._event())

        task.delay.assert_not_called()


class ReplyTaskExecutionTest(unittest.TestCase):
    def test_task_skips_if_connection_verification_fails(self):
        cfg = InstagramAccountConfig(
            user_id=1,
            user_uid="u-1",
            email="shop@pos.uz",
            account_id="1784140001",
            access_token="invalid-token",
            app_secret=None,
            auto_reply=True,
            gemini_api_key="gem-1",
        )
        with patch("app.tasks.get_account_config_by_id", return_value=cfg), \
             patch("app.tasks.verify_instagram_connection", return_value=False), \
             patch("app.tasks.reply_in_conversation") as reply_mock:
            tasks.reply_to_instagram_dm_task("1784140001", "cust-555", "Salom")

        reply_mock.assert_not_called()

    def test_task_runs_gemini_and_sends_with_store_token_when_verified(self):
        cfg = InstagramAccountConfig(
            user_id=1,
            user_uid="u-1",
            email="shop@pos.uz",
            account_id="1784140001",
            access_token="valid-store-token",
            app_secret=None,
            auto_reply=True,
            gemini_api_key="store-gemini-key",
        )
        with patch("app.tasks.get_account_config_by_id", return_value=cfg), \
             patch("app.tasks.verify_instagram_connection", return_value=True), \
             patch("app.tasks.reply_in_conversation", return_value="Bizda bor!") as reply_mock, \
             patch("app.tasks.send_message") as send_mock:
            tasks.reply_to_instagram_dm_task("1784140001", "cust-555", "iPhone bormi?")

        reply_mock.assert_called_once_with(
            "1784140001:cust-555",
            "iPhone bormi?",
            api_key="store-gemini-key",
        )
        send_mock.assert_called_once_with(
            "cust-555",
            "Bizda bor!",
            access_token="valid-store-token",
        )


class SendMessageTest(unittest.TestCase):
    def test_send_message_uses_custom_token(self):
        mock_response = MagicMock(status_code=200)
        with patch("httpx.post", return_value=mock_response) as post_mock:
            send_message("cust-1", "Salom", access_token="custom-token-999")

        post_mock.assert_called_once()
        headers = post_mock.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer custom-token-999")

    def test_send_message_raises_when_no_token_available(self):
        with patch("app.instagram_api.get_settings") as get_set:
            get_set.return_value.instagram_access_token = None
            with self.assertRaises(InstagramNotConfiguredError):
                send_message("cust-1", "Salom", access_token="")


if __name__ == "__main__":
    unittest.main()
