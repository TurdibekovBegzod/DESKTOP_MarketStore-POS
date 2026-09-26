"""Tests for the Instagram webhook callback."""

import hashlib
import hmac
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.routers import instagram
from app.routers.instagram import extract_events, verify_signature


SECRET = "app-secret"


def _signature(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class SignatureTest(unittest.TestCase):
    def test_a_body_signed_with_our_app_secret_passes(self):
        body = b'{"object":"instagram"}'
        self.assertTrue(verify_signature(body, _signature(body), SECRET))

    def test_a_body_signed_with_anything_else_fails(self):
        body = b'{"object":"instagram"}'
        self.assertFalse(verify_signature(body, _signature(body, "other-secret"), SECRET))

    def test_one_changed_byte_fails(self):
        header = _signature(b'{"object":"instagram"}')
        self.assertFalse(verify_signature(b'{"object":"instagra_"}', header, SECRET))

    def test_a_missing_or_unprefixed_header_fails(self):
        body = b"{}"
        self.assertFalse(verify_signature(body, None, SECRET))
        self.assertFalse(verify_signature(body, "sha1=deadbeef", SECRET))


class ExtractEventsTest(unittest.TestCase):
    def test_a_direct_message_becomes_one_event(self):
        events = extract_events({
            "object": "instagram",
            "entry": [{
                "id": "17841477756581063",
                "messaging": [{
                    "sender": {"id": "5551"},
                    "message": {"mid": "mid.1", "text": "Narxi qancha?"},
                }],
            }],
        })
        self.assertEqual(events, [{
            "type": "message",
            "account_id": "17841477756581063",
            "sender_id": "5551",
            "message_id": "mid.1",
            "text": "Narxi qancha?",
        }])

    def test_our_own_reply_coming_back_is_dropped(self):
        events = extract_events({
            "object": "instagram",
            "entry": [{
                "id": "1",
                "messaging": [{
                    "sender": {"id": "1"},
                    "message": {"mid": "mid.2", "text": "Salom", "is_echo": True},
                }],
            }],
        })
        self.assertEqual(events, [])

    def test_a_comment_becomes_one_event(self):
        events = extract_events({
            "object": "instagram",
            "entry": [{
                "id": "1",
                "changes": [{
                    "field": "comments",
                    "value": {
                        "id": "c1",
                        "from": {"id": "5551"},
                        "media": {"id": "m1"},
                        "text": "Bor yo'qmi?",
                    },
                }],
            }],
        })
        self.assertEqual(events[0]["type"], "comment")
        self.assertEqual(events[0]["comment_id"], "c1")
        self.assertEqual(events[0]["media_id"], "m1")

    def test_fields_we_did_not_subscribe_to_are_ignored(self):
        events = extract_events({
            "object": "instagram",
            "entry": [{"id": "1", "changes": [{"field": "mentions", "value": {"id": "x"}}]}],
        })
        self.assertEqual(events, [])

    def test_a_payload_from_another_product_is_ignored(self):
        events = extract_events({
            "object": "page",
            "entry": [{"id": "1", "messaging": [{"sender": {"id": "2"}, "message": {"mid": "m"}}]}],
        })
        self.assertEqual(events, [])

    def test_an_empty_envelope_is_not_an_error(self):
        self.assertEqual(extract_events({"object": "instagram"}), [])
        self.assertEqual(extract_events({"object": "instagram", "entry": [{"id": "1"}]}), [])


class HandleEventTest(unittest.TestCase):
    """The webhook never answers inline - it queues the reply and returns."""

    def _event(self, **overrides):
        event = {
            "type": "message",
            "account_id": "17841477756581063",
            "sender_id": "5551",
            "message_id": "m1",
            "text": "Narxi qancha?",
        }
        event.update(overrides)
        return event

    def test_a_customer_dm_is_queued_for_the_agent(self):
        with patch.object(instagram, "reply_to_instagram_dm_task") as task:
            instagram.handle_event(self._event())
        task.delay.assert_called_once_with("17841477756581063", "5551", "Narxi qancha?")

    def test_a_comment_is_logged_but_not_answered(self):
        with patch.object(instagram, "reply_to_instagram_dm_task") as task:
            instagram.handle_event(self._event(type="comment"))
        task.delay.assert_not_called()

    def test_a_dm_with_no_text_is_not_queued(self):
        with patch.object(instagram, "reply_to_instagram_dm_task") as task:
            instagram.handle_event(self._event(text=None))
        task.delay.assert_not_called()

    def test_the_account_talking_to_itself_is_not_queued(self):
        with patch.object(instagram, "reply_to_instagram_dm_task") as task:
            instagram.handle_event(self._event(sender_id="17841477756581063"))
        task.delay.assert_not_called()

    def test_the_kill_switch_stops_replies_without_stopping_delivery(self):
        off = SimpleNamespace(instagram_auto_reply=False)
        with patch.object(instagram, "reply_to_instagram_dm_task") as task, \
                patch.object(instagram, "get_settings", return_value=off):
            instagram.handle_event(self._event())
        task.delay.assert_not_called()


if __name__ == "__main__":
    unittest.main()
