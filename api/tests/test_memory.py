"""Tests for the per-customer conversation memory and reply_in_conversation."""

import unittest
from unittest.mock import patch

from ai import agent, memory


class MemoryTest(unittest.TestCase):
    class FakeRedis:
        def __init__(self):
            self.store = {}
            self.ttls = {}

        def get(self, key):
            return self.store.get(key)

        def setex(self, key, ttl, value):
            self.store[key] = value
            self.ttls[key] = ttl

        def delete(self, key):
            self.store.pop(key, None)

    def setUp(self):
        self.redis = self.FakeRedis()
        patcher = patch.object(memory, "get_client", return_value=self.redis)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_an_unknown_customer_has_no_history(self):
        self.assertEqual(memory.load("nobody"), [])

    def test_history_survives_a_round_trip(self):
        turns = [{"role": "user", "parts": [{"text": "Salom"}]}]
        memory.save("ig-1", turns)
        self.assertEqual(memory.load("ig-1"), turns)

    def test_history_is_kept_for_one_week(self):
        memory.save("ig-1", [{"role": "user", "parts": [{"text": "x"}]}])
        self.assertEqual(self.redis.ttls["conv:ig-1"], 7 * 24 * 3600)

    def test_only_the_last_turns_are_kept(self):
        turns = [{"role": "user", "parts": [{"text": str(i)}]} for i in range(30)]
        memory.save("ig-1", turns)
        stored = memory.load("ig-1")
        self.assertEqual(len(stored), memory.MAX_TURNS)
        self.assertEqual(stored[-1]["parts"][0]["text"], "29")

    def test_unreadable_history_is_discarded_not_raised(self):
        self.redis.store["conv:ig-1"] = "{not json"
        self.assertEqual(memory.load("ig-1"), [])

    def test_clearing_forgets_the_customer(self):
        memory.save("ig-1", [{"role": "user", "parts": [{"text": "x"}]}])
        memory.clear("ig-1")
        self.assertEqual(memory.load("ig-1"), [])


class ReplyInConversationTest(unittest.TestCase):
    def setUp(self):
        self.saved = {}
        patcher = patch.object(memory, "get_client", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_history_is_placed_before_the_new_message(self):
        history = [
            {"role": "user", "parts": [{"text": "AirPods bormi?"}]},
            {"role": "model", "parts": [{"text": "Bor"}]},
        ]
        with patch.object(agent.memory, "load", return_value=history), patch.object(
            agent.memory, "save"
        ) as save, patch.object(agent, "generate", return_value="450 000 so'm") as generate:
            answer = agent.reply_in_conversation("ig-1", "Qancha turadi?")

        self.assertEqual(answer, "450 000 so'm")
        contents = generate.call_args.args[0]
        self.assertEqual(len(contents), 3)
        self.assertEqual(contents[-1]["parts"][0]["text"], "Qancha turadi?")
        save.assert_called_once()

    def test_an_empty_answer_is_not_remembered(self):
        with patch.object(agent.memory, "load", return_value=[]), patch.object(
            agent.memory, "save"
        ) as save, patch.object(agent, "generate", return_value="   "):
            self.assertEqual(agent.reply_in_conversation("ig-1", "Salom"), "")
        save.assert_not_called()

    def test_a_photo_only_dm_never_reaches_the_model(self):
        with patch.object(agent, "generate") as generate:
            self.assertEqual(agent.reply_in_conversation("ig-1", None), "")
        generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
