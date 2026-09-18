"""Tests for the /ai/reply endpoint chat platforms call."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from app.routers import assistant
from app.routers.assistant import ReplyRequest


def _settings(**overrides):
    values = {
        "assistant_api_key": "secret-key",
        "assistant_fallback_reply": "Operatorimiz javob beradi.",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class ReplyEndpointTest(unittest.TestCase):
    def _call(self, text="Salom", key="secret-key", settings=None, **patches):
        with patch.object(assistant, "get_settings", return_value=settings or _settings()), \
                patch.object(assistant, "reply_to", **patches):
            return assistant.reply(ReplyRequest(text=text), x_api_key=key)

    def test_the_model_answer_is_returned(self):
        out = self._call(return_value="Ha, bizda bor.")
        self.assertEqual(out.reply, "Ha, bizda bor.")

    def test_a_wrong_or_missing_key_is_rejected(self):
        for key in ("wrong-key", None):
            with self.subTest(key=key), self.assertRaises(HTTPException) as caught:
                self._call(key=key, return_value="x")
            self.assertEqual(caught.exception.status_code, 401)

    def test_an_unconfigured_endpoint_refuses_rather_than_answers(self):
        with self.assertRaises(HTTPException) as caught:
            self._call(settings=_settings(assistant_api_key=None), return_value="x")
        self.assertEqual(caught.exception.status_code, 503)

    def test_an_empty_answer_becomes_the_handover_line(self):
        out = self._call(return_value="")
        self.assertEqual(out.reply, "Operatorimiz javob beradi.")

    def test_a_failed_model_call_becomes_the_handover_line_not_a_500(self):
        out = self._call(side_effect=RuntimeError("gemini is down"))
        self.assertEqual(out.reply, "Operatorimiz javob beradi.")

    def test_the_wait_is_bounded_so_the_platform_does_not_give_up(self):
        with patch.object(assistant, "get_settings", return_value=_settings()), \
                patch.object(assistant, "reply_to", return_value="ok") as reply_to:
            assistant.reply(ReplyRequest(text="Salom"), x_api_key="secret-key")
        self.assertEqual(reply_to.call_args.kwargs["timeout"], assistant.REPLY_TIMEOUT_SECONDS)


if __name__ == "__main__":
    unittest.main()
