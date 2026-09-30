"""One account's agent rules never reach another account's reply.

The central test here is the account filter. A vector search that loses it does
not raise anything - it answers a customer out of some other shop's policy - so
the boundary is pinned by test rather than left to review.

Also covered: the distance ceiling (irrelevant rules are dropped rather than
padded up to the limit), what an edit and a delete do to a stored vector, and the
sync mirror that keeps ``account_rules`` in step with the rows devices push.
"""

import unittest
from unittest.mock import MagicMock, patch

from app import rules_service


class FakeRuleSession:
    """Stands in for the rules table, recording what was asked of it."""

    def __init__(self, rows=(), rowcount=1):
        self.rows = list(rows)
        self.rowcount = rowcount
        self.statements = []
        self.params = []
        self.committed = False

    def execute(self, statement, params=None):
        self.statements.append(str(statement))
        self.params.append(params)
        result = MagicMock()
        result.all.return_value = self.rows
        result.rowcount = self.rowcount
        return result

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class AccountIsolationTest(unittest.TestCase):
    """The filter that keeps shops apart."""

    def test_every_search_is_scoped_to_the_asking_account(self):
        session = FakeRuleSession(rows=[])
        with patch.object(rules_service.embedding, "embed_query", return_value=[0.1] * 384), \
             patch.object(rules_service, "SessionLocal", return_value=session):
            rules_service.search_rules("uid-7", "kafolat qancha?")

        search_params = [p for p in session.params if p and "user_uid" in p]
        self.assertTrue(search_params, "the search ran without an account parameter")
        self.assertEqual(search_params[0]["user_uid"], "uid-7")
        joined = " ".join(session.statements)
        self.assertIn("user_uid = :user_uid", joined)

    def test_blank_account_never_reaches_the_database(self):
        """No account means no rules, not every account's rules."""
        session = FakeRuleSession(rows=[("r1", "hamma narsa", 0, 0.1)])
        with patch.object(rules_service, "SessionLocal", return_value=session):
            self.assertEqual(rules_service.search_rules("", "savol"), [])
            self.assertEqual(rules_service.search_rules("   ", "savol"), [])
        self.assertEqual(session.statements, [])

    def test_delete_is_scoped_to_the_account_as_well_as_the_id(self):
        """A local_id comes from a device and must not address another shop's rule."""
        session = FakeRuleSession(rowcount=1)
        with patch.object(rules_service, "SessionLocal", return_value=session):
            rules_service.delete_rule("uid-7", "rule-1")

        self.assertEqual(session.params[0], {"user_uid": "uid-7", "local_id": "rule-1"})
        self.assertIn("user_uid = :user_uid", session.statements[0])

    def test_iterative_scan_is_enabled_for_filtered_search(self):
        """Without it an HNSW scan returns fewer rows than the LIMIT - see 0014."""
        session = FakeRuleSession(rows=[])
        with patch.object(rules_service.embedding, "embed_query", return_value=[0.1] * 384), \
             patch.object(rules_service, "SessionLocal", return_value=session):
            rules_service.search_rules("uid-7", "savol")

        joined = " ".join(session.statements)
        self.assertIn("hnsw.iterative_scan", joined)
        self.assertIn("strict_order", joined)


class SearchBehaviourTest(unittest.TestCase):
    def test_rules_come_back_nearest_first(self):
        session = FakeRuleSession(rows=[
            ("r1", "Kafolat 6 oy", 0, 0.12),
            ("r2", "Yetkazib berish bepul", 0, 0.30),
        ])
        with patch.object(rules_service.embedding, "embed_query", return_value=[0.1] * 384), \
             patch.object(rules_service, "SessionLocal", return_value=session):
            rules = rules_service.search_rules("uid-7", "kafolat?")

        self.assertEqual([rule.local_id for rule in rules], ["r1", "r2"])
        self.assertEqual(rules[0].text, "Kafolat 6 oy")

    def test_nothing_is_filtered_by_distance(self):
        """Measured: on-topic 0.17-0.22 and noise 0.19-0.24 overlap for e5-small.

        Any threshold therefore drops real rules to exclude noise, while the
        ranking itself is reliable. So the best few come back whatever their
        distance and the model decides; a threshold creeping back into the SQL
        would silently hide rules the shop had written.
        """
        session = FakeRuleSession(rows=[])
        with patch.object(rules_service.embedding, "embed_query", return_value=[0.1] * 384), \
             patch.object(rules_service, "SessionLocal", return_value=session):
            rules_service.search_rules("uid-7", "salom")

        joined = " ".join(session.statements)
        self.assertNotIn("max_distance", joined)
        self.assertFalse(
            hasattr(rules_service, "MAX_DISTANCE"),
            "a distance threshold was reintroduced; see the module docstring",
        )

    def test_only_the_best_few_come_back(self):
        session = FakeRuleSession(rows=[])
        with patch.object(rules_service.embedding, "embed_query", return_value=[0.1] * 384), \
             patch.object(rules_service, "SessionLocal", return_value=session):
            rules_service.search_rules("uid-7", "savol")

        search_params = [p for p in session.params if p and "limit" in p][0]
        self.assertEqual(search_params["limit"], rules_service.MAX_RULES)
        self.assertEqual(rules_service.MAX_RULES, 5)

    def test_unembedded_rules_are_never_returned(self):
        session = FakeRuleSession(rows=[])
        with patch.object(rules_service.embedding, "embed_query", return_value=[0.1] * 384), \
             patch.object(rules_service, "SessionLocal", return_value=session):
            rules_service.search_rules("uid-7", "savol")

        self.assertIn("embedding IS NOT NULL", " ".join(session.statements))

    def test_missing_model_degrades_to_no_rules(self):
        """The web worker carries no model; a reply must still go out."""
        error = rules_service.embedding.EmbeddingUnavailableError("not installed")
        with patch.object(rules_service.embedding, "embed_query", side_effect=error):
            self.assertEqual(rules_service.search_rules("uid-7", "savol"), [])

    def test_database_failure_degrades_to_no_rules(self):
        with patch.object(rules_service.embedding, "embed_query", return_value=[0.1] * 384), \
             patch.object(rules_service, "SessionLocal", side_effect=RuntimeError("db down")):
            self.assertEqual(rules_service.search_rules("uid-7", "savol"), [])


class RuleWriteTest(unittest.TestCase):
    def test_editing_a_rule_clears_its_vector(self):
        """A changed rule must not stay searchable under its old meaning."""
        session = FakeRuleSession()
        with patch.object(rules_service, "SessionLocal", return_value=session):
            self.assertTrue(
                rules_service.upsert_rule(1, "uid-7", "rule-1", "Kafolat 12 oy")
            )

        statement = session.statements[0]
        self.assertIn("ON CONFLICT", statement)
        self.assertIn("text_hash = EXCLUDED.text_hash", statement)
        self.assertIn("ELSE NULL", statement)
        self.assertTrue(session.committed)

    def test_unchanged_text_keeps_its_vector(self):
        """A sync that resends an account's whole rule set must cost no re-embedding."""
        session = FakeRuleSession()
        with patch.object(rules_service, "SessionLocal", return_value=session):
            rules_service.upsert_rule(1, "uid-7", "rule-1", "Kafolat 6 oy")

        self.assertIn(
            "WHEN account_rules.text_hash = EXCLUDED.text_hash THEN account_rules.embedding",
            session.statements[0],
        )

    def test_empty_rule_is_not_stored(self):
        session = FakeRuleSession()
        with patch.object(rules_service, "SessionLocal", return_value=session):
            self.assertFalse(rules_service.upsert_rule(1, "uid-7", "rule-1", "   "))
            self.assertFalse(rules_service.upsert_rule(1, "uid-7", "", "matn"))
        self.assertEqual(session.statements, [])

    def test_wrong_width_vector_is_refused(self):
        """A vector from another model would rank against the wrong space."""
        session = FakeRuleSession()
        with patch.object(rules_service, "SessionLocal", return_value=session):
            stored = rules_service.store_embeddings([(1, [0.1] * 768)], model_name="other")

        self.assertEqual(stored, 0)
        self.assertEqual(session.statements, [])

    def test_stored_vector_is_only_attached_to_a_row_still_waiting(self):
        """A rule edited mid-batch is skipped, not pinned to the old meaning."""
        session = FakeRuleSession(rowcount=0)
        with patch.object(rules_service, "SessionLocal", return_value=session):
            stored = rules_service.store_embeddings(
                [(1, [0.1] * 384)], model_name="intfloat/multilingual-e5-small"
            )

        self.assertEqual(stored, 0)
        self.assertIn("embedding IS NULL", session.statements[0])


class PromptBlockTest(unittest.TestCase):
    def test_no_rules_means_no_block(self):
        """An absent block is what tells the agent it has no rule to apply."""
        self.assertEqual(rules_service.format_for_prompt([]), "")

    def test_higher_priority_is_read_first(self):
        rules = [
            rules_service.AccountRule("r1", "Chegirma yo'q", priority=0, distance=0.1),
            rules_service.AccountRule("r2", "Bu kategoriyaga 10%", priority=5, distance=0.2),
        ]
        block = rules_service.format_for_prompt(rules)
        self.assertLess(block.index("Bu kategoriyaga 10%"), block.index("Chegirma yo'q"))

    def test_distances_are_not_shown_to_the_model(self):
        rules = [rules_service.AccountRule("r1", "Kafolat 6 oy", priority=0, distance=0.1234)]
        self.assertNotIn("0.1234", rules_service.format_for_prompt(rules))

    def test_the_block_does_not_claim_every_rule_is_relevant(self):
        """Nothing was filtered, so calling it more invites a forced fit."""
        rules = [rules_service.AccountRule("r1", "Yetkazib berish bepul", priority=0, distance=0.2)]
        block = rules_service.format_for_prompt(rules)
        self.assertIn("saralanmagan", block)


class SyncMirrorTest(unittest.TestCase):
    """What a pushed agent_rules row does to the searchable table."""

    def setUp(self):
        from app.routers import sync

        self.sync = sync
        self.user = MagicMock(uid="uid-7", id=7)

    def _record(self, local_id="rule-1", data=None, deleted_at=None, table_name="agent_rules"):
        record = MagicMock()
        record.table_name = table_name
        record.local_id = local_id
        record.data = {"text": "Kafolat 6 oy"} if data is None else data
        record.deleted_at = deleted_at
        return record

    def test_a_pushed_rule_is_stored_for_search(self):
        with patch.object(self.sync.rules_service, "upsert_rule") as upsert:
            self.sync._mirror_agent_rule(self.user, self._record())

        upsert.assert_called_once()
        kwargs = upsert.call_args.kwargs
        self.assertEqual(kwargs["user_uid"], "uid-7")
        self.assertEqual(kwargs["local_id"], "rule-1")
        self.assertEqual(kwargs["raw_text"], "Kafolat 6 oy")

    def test_a_deleted_rule_is_removed_outright(self):
        """The owner asked for deleted rules to be gone, not flagged."""
        with patch.object(self.sync.rules_service, "delete_rule") as remove:
            self.sync._mirror_agent_rule(
                self.user, self._record(deleted_at="2026-09-30T10:00:00Z")
            )

        remove.assert_called_once_with("uid-7", "rule-1")

    def test_an_emptied_rule_is_removed_too(self):
        with patch.object(self.sync.rules_service, "delete_rule") as remove:
            self.sync._mirror_agent_rule(self.user, self._record(data={"text": "   "}))

        remove.assert_called_once_with("uid-7", "rule-1")

    def test_other_tables_are_left_alone(self):
        with patch.object(self.sync.rules_service, "upsert_rule") as upsert, \
             patch.object(self.sync.rules_service, "delete_rule") as remove:
            self.sync._mirror_agent_rule(self.user, self._record(table_name="products"))

        upsert.assert_not_called()
        remove.assert_not_called()

    def test_priority_survives_the_trip(self):
        with patch.object(self.sync.rules_service, "upsert_rule") as upsert:
            self.sync._mirror_agent_rule(
                self.user, self._record(data={"text": "Kafolat 6 oy", "priority": 5})
            )

        self.assertEqual(upsert.call_args.kwargs["priority"], 5)

    def test_unusable_priority_falls_back_to_zero(self):
        with patch.object(self.sync.rules_service, "upsert_rule") as upsert:
            self.sync._mirror_agent_rule(
                self.user, self._record(data={"text": "Kafolat", "priority": "juda muhim"})
            )

        self.assertEqual(upsert.call_args.kwargs["priority"], 0)

    def test_a_mirror_failure_does_not_fail_the_push(self):
        """The rule is already stored and shared; only search is affected."""
        with patch.object(
            self.sync.rules_service, "upsert_rule", side_effect=RuntimeError("db down")
        ):
            self.sync._mirror_agent_rule(self.user, self._record())  # must not raise


class RuleToolTest(unittest.TestCase):
    """search_shop_rules is kept in the code but no longer offered to the model."""

    def setUp(self):
        from ai import tools

        self.tools = tools

    def _with_account(self, user_uid="uid-7"):
        return patch.object(
            self.tools, "get_store_context", return_value=MagicMock(user_uid=user_uid)
        )

    def test_the_tool_is_not_offered_to_the_model(self):
        """The rules are in the prompt now; a search tool beside them would compete."""
        names = [decl["name"] for decl in self.tools.DECLARATIONS]
        self.assertNotIn("search_shop_rules", names)
        self.assertNotIn("search_shop_rules", self.tools.TOOLS)

    def test_the_handler_and_declaration_are_kept(self):
        """Taken out of use, not deleted: switching it back on is one line."""
        self.assertTrue(callable(self.tools.search_shop_rules))
        self.assertEqual(self.tools.SEARCH_SHOP_RULES_DECLARATION["name"], "search_shop_rules")

    def test_the_kept_handler_still_searches_the_right_account(self):
        with self._with_account("uid-7"), \
             patch.object(rules_service, "search_rules", return_value=[]) as search:
            self.tools.search_shop_rules(query="kafolat muddati")

        search.assert_called_once_with("uid-7", "kafolat muddati")


class ListRulesTest(unittest.TestCase):
    """list_rules: the whole set, for the system prompt."""

    def test_it_is_scoped_to_the_asking_account(self):
        session = FakeRuleSession(rows=[])
        with patch.object(rules_service, "SessionLocal", return_value=session):
            rules_service.list_rules("uid-7")

        self.assertEqual(session.params[0]["user_uid"], "uid-7")
        self.assertIn("user_uid = :user_uid", session.statements[0])

    def test_it_does_not_need_an_embedding(self):
        """A rule the embedder has not reached yet is still the shop's rule."""
        session = FakeRuleSession(rows=[])
        with patch.object(rules_service, "SessionLocal", return_value=session):
            rules_service.list_rules("uid-7")

        self.assertNotIn("embedding", session.statements[0])

    def test_higher_priority_comes_first(self):
        session = FakeRuleSession(rows=[])
        with patch.object(rules_service, "SessionLocal", return_value=session):
            rules_service.list_rules("uid-7")

        self.assertIn("ORDER BY priority DESC", session.statements[0])

    def test_rows_become_rules(self):
        session = FakeRuleSession(rows=[("r1", "Kafolat 6 oy", 5), ("r2", "  ", 0)])
        with patch.object(rules_service, "SessionLocal", return_value=session):
            rules = rules_service.list_rules("uid-7")

        self.assertEqual([rule.text for rule in rules], ["Kafolat 6 oy"])
        self.assertEqual(rules[0].priority, 5)

    def test_no_account_means_no_query(self):
        with patch.object(rules_service, "SessionLocal") as factory:
            self.assertEqual(rules_service.list_rules(""), [])
            self.assertEqual(rules_service.list_rules(None), [])
        factory.assert_not_called()

    def test_a_database_failure_is_no_rules_not_an_error(self):
        with patch.object(rules_service, "SessionLocal", side_effect=RuntimeError("db down")):
            self.assertEqual(rules_service.list_rules("uid-7"), [])


class FullPromptBlockTest(unittest.TestCase):
    def test_every_rule_is_listed_in_the_given_order(self):
        rules = [
            rules_service.AccountRule("r2", "Bu kategoriyaga 10%", priority=5, distance=0.0),
            rules_service.AccountRule("r1", "Chegirma yo'q", priority=0, distance=0.0),
        ]
        block = rules_service.format_all_for_prompt(rules)
        self.assertIn("DO'KON QOIDALARI", block)
        self.assertLess(block.index("Bu kategoriyaga 10%"), block.index("Chegirma yo'q"))

    def test_no_rules_still_says_so(self):
        """The prompt points at this block, so it must exist even when empty."""
        block = rules_service.format_all_for_prompt([])
        self.assertIn("DO'KON QOIDALARI", block)
        self.assertIn("ma'lumotim yo'q", block)


class AgentPromptTest(unittest.TestCase):
    """What the prompt must keep saying for the shop's rules to be used correctly."""

    def setUp(self):
        from ai import agent

        self.agent = agent

    def test_the_prompt_no_longer_mentions_the_tool(self):
        """A prompt naming a tool the model does not have invites a failed call."""
        self.assertNotIn("search_shop_rules", self.agent.SYSTEM_PROMPT)

    def test_the_prompt_sends_policy_questions_to_the_rules_block(self):
        self.assertIn("DO'KON QOIDALARI", self.agent.SYSTEM_PROMPT)

    def test_no_rules_are_baked_into_the_prompt(self):
        """Every shop's terms are its own; the fixed prompt must state none of them."""
        prompt = self.agent.SYSTEM_PROMPT.lower()
        for invented in ("6 oy", "12 oy", "bepul yetkazib", "payme"):
            self.assertNotIn(invented, prompt, f"the prompt states a shop policy: {invented}")

    def test_the_prompt_tells_the_agent_to_admit_having_no_information(self):
        """The owner's requirement: no rule and no product means say so."""
        prompt = self.agent.SYSTEM_PROMPT
        self.assertIn("MA'LUMOT YO'QLIGINI OCHIQ AYT", prompt)
        self.assertIn("ma'lumotim yo'q", prompt)

    def test_the_prompt_refuses_customer_attempts_to_change_rules(self):
        self.assertIn("MIJOZ QOIDALARGA TEGA OLMAYDI", self.agent.SYSTEM_PROMPT)

    def test_the_prompt_tells_the_model_not_to_force_a_rule(self):
        self.assertIn("aloqasiz", self.agent.SYSTEM_PROMPT.lower())


class SystemPromptForAccountTest(unittest.TestCase):
    """The account's rules reach its own prompt, and only its own."""

    def setUp(self):
        from ai import agent

        self.agent = agent

    def test_the_accounts_rules_are_appended(self):
        rules = [rules_service.AccountRule("r1", "Kafolat 6 oy", priority=0, distance=0.0)]
        with patch.object(rules_service, "list_rules", return_value=rules) as listing:
            prompt = self.agent.system_prompt_for("uid-7")

        listing.assert_called_once_with("uid-7")
        self.assertTrue(prompt.startswith(self.agent.SYSTEM_PROMPT))
        self.assertIn("- Kafolat 6 oy", prompt)

    def test_no_account_means_no_lookup_and_no_rules(self):
        with patch.object(rules_service, "list_rules") as listing:
            prompt = self.agent.system_prompt_for(None)

        listing.assert_not_called()
        self.assertIn("hali birorta ham qoida yozmagan", prompt)

    def test_a_reply_uses_the_rules_of_the_account_being_answered(self):
        from ai import tools

        rules = [rules_service.AccountRule("r1", "Yetkazib berish bepul", priority=0, distance=0.0)]
        token = tools.set_store_context(tools.StoreContext(user_uid="uid-7"))
        try:
            with patch.object(rules_service, "list_rules", return_value=rules) as listing, \
                 patch.object(self.agent, "generate", return_value="ok") as generate:
                self.agent.reply_to("Yetkazib berish bormi?")
        finally:
            tools._store_context.reset(token)

        listing.assert_called_once_with("uid-7")
        self.assertIn("Yetkazib berish bepul", generate.call_args.kwargs["system_instruction"])
        declared = [d["name"] for d in generate.call_args.kwargs["declarations"]]
        self.assertNotIn("search_shop_rules", declared)


class PerToolCallLimitTest(unittest.TestCase):
    """Each tool gets its own allowance, so one cannot spend another's."""

    def setUp(self):
        from ai import gemini

        self.gemini = gemini

    def _part(self, tool_name, args=None):
        return {"functionCall": {"name": tool_name, "args": args or {}}}

    def test_the_limit_is_five_per_tool(self):
        self.assertEqual(self.gemini.MAX_CALLS_PER_TOOL, 5)

    def test_a_tool_runs_up_to_its_limit_then_is_refused(self):
        calls = []
        tools = {"search_shop_rules": lambda **kw: calls.append(kw) or {"found": True}}
        used = {}

        for _ in range(self.gemini.MAX_CALLS_PER_TOOL):
            result = self.gemini.run_tool(self._part("search_shop_rules", {"query": "x"}), tools, used)
            self.assertNotIn("error", result)

        refused = self.gemini.run_tool(self._part("search_shop_rules", {"query": "x"}), tools, used)
        self.assertIn("error", refused)
        self.assertEqual(len(calls), self.gemini.MAX_CALLS_PER_TOOL)

    def test_one_exhausted_tool_does_not_block_another(self):
        """The whole point of a per-tool budget rather than a pooled one."""
        tools = {
            "search_products": lambda **kw: {"products": []},
            "search_shop_rules": lambda **kw: {"found": True},
        }
        used = {"search_products": self.gemini.MAX_CALLS_PER_TOOL}

        blocked = self.gemini.run_tool(self._part("search_products", {"name": "x"}), tools, used)
        allowed = self.gemini.run_tool(self._part("search_shop_rules", {"query": "x"}), tools, used)

        self.assertIn("error", blocked)
        self.assertNotIn("error", allowed)

    def test_the_refusal_tells_the_model_to_answer(self):
        """A bare error would have it retrying until the round limit ran out."""
        tools = {"search_shop_rules": lambda **kw: {"found": True}}
        used = {"search_shop_rules": self.gemini.MAX_CALLS_PER_TOOL}

        refused = self.gemini.run_tool(self._part("search_shop_rules", {"query": "x"}), tools, used)
        self.assertIn("javob ber", refused["error"])

    def test_rounds_leave_room_for_every_tool(self):
        """Rounds must not bind before the per-tool limits do."""
        from ai import tools as tools_mod

        self.assertGreater(
            self.gemini.MAX_TOOL_ROUNDS,
            len(tools_mod.DECLARATIONS),
            "the round limit binds before the per-tool limits, making them moot",
        )

    def test_counting_is_skipped_when_no_ledger_is_passed(self):
        """Callers outside the loop (and older tests) must keep working."""
        tools = {"search_shop_rules": lambda **kw: {"found": True}}
        for _ in range(20):
            result = self.gemini.run_tool(self._part("search_shop_rules", {"query": "x"}), tools)
            self.assertNotIn("error", result)


if __name__ == "__main__":
    unittest.main()
