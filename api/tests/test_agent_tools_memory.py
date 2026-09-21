"""Tests for the tool loop and the per-customer conversation memory."""

import unittest
from unittest.mock import patch

from ai import agent, gemini, memory


def _text(answer: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": answer}]}}]}


def _call(name: str, **args) -> dict:
    return {"candidates": [{"content": {"parts": [{"functionCall": {"name": name, "args": args}}]}}]}


class ExtractFunctionCallsTest(unittest.TestCase):
    def test_a_plain_answer_has_no_calls(self):
        self.assertEqual(gemini.extract_function_calls(_text("Salom")), [])

    def test_a_call_is_returned_with_its_arguments(self):
        calls = gemini.extract_function_calls(_call("search_products", query="olma"))
        self.assertEqual(calls, [{"functionCall": {"name": "search_products", "args": {"query": "olma"}}}])

    def test_a_thought_signature_survives_extraction(self):
        """3.x rejects the next request unless the part comes back whole."""
        payload = {"candidates": [{"content": {"parts": [
            {"functionCall": {"name": "a", "args": {}}, "thoughtSignature": "sig-1"}]}}]}
        self.assertEqual(gemini.extract_function_calls(payload)[0]["thoughtSignature"], "sig-1")

    def test_parallel_calls_are_all_returned(self):
        payload = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"functionCall": {"name": "a", "args": {}}},
                            {"functionCall": {"name": "b", "args": {}}},
                        ]
                    }
                }
            ]
        }
        self.assertEqual([c["functionCall"]["name"] for c in gemini.extract_function_calls(payload)], ["a", "b"])


class RunToolTest(unittest.TestCase):
    def test_an_unknown_tool_is_reported_not_raised(self):
        self.assertIn("error", gemini.run_tool({"functionCall": {"name": "nope", "args": {}}}, {}))

    def test_a_failing_tool_is_reported_not_raised(self):
        def boom():
            raise RuntimeError("db down")

        self.assertEqual(gemini.run_tool({"functionCall": {"name": "x"}}, {"x": boom}), {"error": "db down"})

    def test_a_working_tool_returns_its_result(self):
        self.assertEqual(
            gemini.run_tool({"functionCall": {"name": "x", "args": {"n": 2}}}, {"x": lambda n: {"got": n}}),
            {"got": 2},
        )


class ToolLoopTest(unittest.TestCase):
    """The loop drives gemini.generate, so the HTTP call is what gets faked."""

    def _run(self, payloads, tools):
        with patch.object(gemini, "get_settings") as settings, patch.object(gemini.httpx, "post") as post:
            settings.return_value.gemini_api_key = "k"
            settings.return_value.gemini_model = "m"
            post.side_effect = [
                unittest.mock.Mock(json=unittest.mock.Mock(return_value=p), raise_for_status=lambda: None)
                for p in payloads
            ]
            answer = gemini.generate("savol", tools=tools, declarations=[{"name": "search_products"}])
            return answer, post

    def test_an_answer_without_tools_returns_immediately(self):
        answer, post = self._run([_text("Salom")], {})
        self.assertEqual(answer, "Salom")
        self.assertEqual(post.call_count, 1)

    def test_a_tool_call_is_run_and_fed_back(self):
        seen = {}

        def search_products(query):
            seen["query"] = query
            return {"products": [{"name": "Olma", "price": 12000}]}

        answer, post = self._run(
            [_call("search_products", query="olma"), _text("Olma 12000 so'm")],
            {"search_products": search_products},
        )
        self.assertEqual(answer, "Olma 12000 so'm")
        self.assertEqual(seen["query"], "olma")
        self.assertEqual(post.call_count, 2)

        # The second request must carry the call and its result as history.
        roles = [c["role"] for c in post.call_args.kwargs["json"]["contents"]]
        self.assertEqual(roles, ["user", "model", "user"])

    def test_a_model_that_never_stops_is_cut_off(self):
        """After MAX_TOOL_ROUNDS the loop stops asking for tools, but it still
        owes the model one forced, tool-free request - the customer must get
        whatever the model can say about the last tool result, not silence."""
        payloads = [_call("search_products", query="x")] * gemini.MAX_TOOL_ROUNDS + [
            _text("Hech narsa topilmadi")
        ]
        answer, post = self._run(payloads, {"search_products": lambda query: {"products": []}})
        self.assertEqual(answer, "Hech narsa topilmadi")
        self.assertEqual(post.call_count, gemini.MAX_TOOL_ROUNDS + 1)
        # That last request must not offer tools again - it is asking for words.
        self.assertNotIn("tools", post.call_args.kwargs["json"])

    def test_a_model_that_never_stops_and_still_wont_talk_is_silent(self):
        """Even the forced round can come back with nothing usable (e.g. a
        safety filter) - that must still be silence, not an exception."""
        payloads = [_call("search_products", query="x")] * (gemini.MAX_TOOL_ROUNDS + 1)
        answer, post = self._run(payloads, {"search_products": lambda query: {"products": []}})
        self.assertEqual(answer, "")
        self.assertEqual(post.call_count, gemini.MAX_TOOL_ROUNDS + 1)


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

    def test_history_is_kept_for_fourteen_days(self):
        memory.save("ig-1", [{"role": "user", "parts": [{"text": "x"}]}])
        self.assertEqual(self.redis.ttls["conv:ig-1"], 14 * 24 * 3600)

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
