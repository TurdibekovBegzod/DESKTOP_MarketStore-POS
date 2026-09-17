"""Tests for the Gemini-backed Instagram DM agent."""

import unittest
from unittest.mock import patch

from ai import agent
from ai.gemini import extract_text


class ExtractTextTest(unittest.TestCase):
    def test_the_parts_of_the_first_candidate_are_joined(self):
        payload = {"candidates": [{"content": {"parts": [{"text": "Salom"}, {"text": " dunyo"}]}}]}
        self.assertEqual(extract_text(payload), "Salom dunyo")

    def test_a_candidate_with_no_text_falls_through_to_the_next(self):
        payload = {"candidates": [{"content": {"parts": []}}, {"content": {"parts": [{"text": "Bor"}]}}]}
        self.assertEqual(extract_text(payload), "Bor")

    def test_a_blocked_answer_is_an_empty_string_not_an_error(self):
        self.assertEqual(extract_text({"promptFeedback": {"blockReason": "SAFETY"}}), "")
        self.assertEqual(extract_text({}), "")


class ReplyToTest(unittest.TestCase):
    def test_a_question_is_answered(self):
        with patch.object(agent, "generate", return_value="Assalomu alaykum!") as generate:
            self.assertEqual(agent.reply_to("Salom"), "Assalomu alaykum!")
        self.assertEqual(generate.call_args.args[0], "Salom")
        self.assertIs(generate.call_args.kwargs["system_instruction"], agent.SYSTEM_PROMPT)

    def test_a_photo_only_dm_is_not_sent_to_the_model_at_all(self):
        with patch.object(agent, "generate") as generate:
            self.assertEqual(agent.reply_to(None), "")
            self.assertEqual(agent.reply_to("   "), "")
        generate.assert_not_called()

    def test_a_very_long_dm_is_cut_before_it_reaches_the_model(self):
        with patch.object(agent, "generate", return_value="ok") as generate:
            agent.reply_to("x" * 5000)
        self.assertEqual(len(generate.call_args.args[0]), agent.MAX_INCOMING_CHARS)

    def test_an_overlong_answer_is_cut_to_what_instagram_accepts(self):
        with patch.object(agent, "generate", return_value="y" * 3000):
            self.assertEqual(len(agent.reply_to("Salom")), agent.MAX_REPLY_CHARS)

    def test_an_empty_answer_stays_empty_so_nothing_is_sent(self):
        with patch.object(agent, "generate", return_value="   "):
            self.assertEqual(agent.reply_to("Salom"), "")


if __name__ == "__main__":
    unittest.main()
