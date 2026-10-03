"""Tests for the ManyChat webhook: one fixed shop, answered inline."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai.gemini import GeminiNotConfiguredError
from ai.tools import get_store_context
from app.routers import manychat


TOKEN = "manychat-token"
SHOP = manychat.ManychatShop(
    user_id=7,
    user_uid="uid-akbar",
    email="akbareliboy@gmail.com",
    gemini_api_key="shop-key",
)


def _settings(token: str | None = TOKEN):
    return SimpleNamespace(manychat_webhook_token=token, manychat_account_email="akbareliboy@gmail.com")


class ManychatWebhookTest(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(manychat.router)
        self.client = TestClient(app)

    def _post(self, body=None, token: str | None = TOKEN):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return self.client.post(
            "/manychat/webhook",
            json=body if body is not None else {"subscriber_id": "123", "text": "Narxi qancha?"},
            headers=headers,
        )

    def test_without_a_configured_token_the_endpoint_refuses_everyone(self):
        with patch.object(manychat, "get_settings", return_value=_settings(None)):
            self.assertEqual(self._post().status_code, 503)

    def test_a_wrong_or_missing_token_is_refused(self):
        with patch.object(manychat, "get_settings", return_value=_settings()), \
                patch.object(manychat, "reply_in_conversation") as reply:
            self.assertEqual(self._post(token="other").status_code, 401)
            self.assertEqual(self._post(token=None).status_code, 401)
        reply.assert_not_called()

    def test_the_answer_comes_back_for_the_configured_shop_only(self):
        seen = {}

        def fake_reply(conv_id, text, timeout=None, api_key=None):
            seen.update(conv_id=conv_id, text=text, api_key=api_key, ctx=get_store_context())
            return "Bizda bor: 450 000 so'm"

        with patch.object(manychat, "get_settings", return_value=_settings()), \
                patch.object(manychat, "resolve_shop", return_value=SHOP) as resolve, \
                patch.object(manychat, "reply_in_conversation", side_effect=fake_reply):
            response = self._post({"subscriber_id": 123, "text": "Narxi qancha?"})

        self.assertEqual(response.status_code, 200)
        resolve.assert_called_once_with("akbareliboy@gmail.com")
        self.assertEqual(seen["conv_id"], "manychat:uid-akbar:123")
        self.assertEqual(seen["api_key"], "shop-key")
        self.assertEqual(seen["ctx"].user_uid, "uid-akbar")
        self.assertIsNone(get_store_context())
        self.assertEqual(response.json(), {
            "version": "v2",
            "content": {
                "type": "instagram",
                "messages": [{"type": "text", "text": "Bizda bor: 450 000 so'm"}],
            },
            "reply": "Bizda bor: 450 000 so'm",
        })

    def test_a_message_with_no_text_gets_an_empty_answer(self):
        with patch.object(manychat, "get_settings", return_value=_settings()), \
                patch.object(manychat, "reply_in_conversation") as reply:
            response = self._post({"subscriber_id": "123", "text": "  "})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["content"]["messages"], [])
        reply.assert_not_called()

    def test_a_missing_account_is_an_error_not_another_shop(self):
        with patch.object(manychat, "get_settings", return_value=_settings()), \
                patch.object(manychat, "resolve_shop", return_value=None), \
                patch.object(manychat, "reply_in_conversation") as reply:
            self.assertEqual(self._post().status_code, 503)
        reply.assert_not_called()

    def test_agent_failures_take_manychats_failure_branch(self):
        with patch.object(manychat, "get_settings", return_value=_settings()), \
                patch.object(manychat, "resolve_shop", return_value=SHOP), \
                patch.object(manychat, "reply_in_conversation", side_effect=RuntimeError("boom")):
            self.assertEqual(self._post().status_code, 502)
        self.assertIsNone(get_store_context())

        with patch.object(manychat, "get_settings", return_value=_settings()), \
                patch.object(manychat, "resolve_shop", return_value=SHOP), \
                patch.object(manychat, "reply_in_conversation", side_effect=GeminiNotConfiguredError("x")):
            self.assertEqual(self._post().status_code, 503)


class DynamicBlockTest(unittest.TestCase):
    def test_messenger_content_carries_no_type(self):
        self.assertNotIn("type", manychat.dynamic_block("Salom", "facebook")["content"])

    def test_other_channels_name_themselves(self):
        self.assertEqual(manychat.dynamic_block("Salom", "telegram")["content"]["type"], "telegram")


if __name__ == "__main__":
    unittest.main()
