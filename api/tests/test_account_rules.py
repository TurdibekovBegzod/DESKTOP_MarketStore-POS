"""One account's agent rules never reach another account's reply.

The central test here is the account filter. A lookup that loses it does not
raise anything - it answers a customer out of some other shop's policy - so the
boundary is pinned by test rather than left to review.

Also covered: what an edit and a delete do to a stored rule, and the sync mirror
that keeps ``account_rules`` in step with the rows devices push.
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

    def test_delete_is_scoped_to_the_account_as_well_as_the_id(self):
        """A local_id comes from a device and must not address another shop's rule."""
        session = FakeRuleSession(rowcount=1)
        with patch.object(rules_service, "SessionLocal", return_value=session):
            rules_service.delete_rule("uid-7", "rule-1")

        self.assertEqual(session.params[0], {"user_uid": "uid-7", "local_id": "rule-1"})
        self.assertIn("user_uid = :user_uid", session.statements[0])

    def test_upsert_is_scoped_to_the_account(self):
        session = FakeRuleSession()
        with patch.object(rules_service, "SessionLocal", return_value=session):
            rules_service.upsert_rule(1, "uid-7", "rule-1", "Kafolat 6 oy")

        self.assertEqual(session.params[0]["user_uid"], "uid-7")
        self.assertIn("ON CONFLICT (user_uid, local_id)", session.statements[0])


class RuleWriteTest(unittest.TestCase):
    def test_editing_a_rule_replaces_its_text_and_priority(self):
        session = FakeRuleSession()
        with patch.object(rules_service, "SessionLocal", return_value=session):
            self.assertTrue(
                rules_service.upsert_rule(1, "uid-7", "rule-1", "Kafolat 12 oy", priority=3)
            )

        statement = session.statements[0]
        self.assertIn("raw_text = EXCLUDED.raw_text", statement)
        self.assertIn("priority = EXCLUDED.priority", statement)
        self.assertEqual(session.params[0]["raw_text"], "Kafolat 12 oy")
        self.assertEqual(session.params[0]["priority"], 3)
        self.assertTrue(session.committed)

    def test_nothing_about_embeddings_is_written(self):
        """0015 dropped those columns; naming one would fail every rule save."""
        session = FakeRuleSession()
        with patch.object(rules_service, "SessionLocal", return_value=session):
            rules_service.upsert_rule(1, "uid-7", "rule-1", "Kafolat 6 oy")

        for column in ("embedding", "text_hash", "model"):
            self.assertNotIn(column, session.statements[0])

    def test_empty_rule_is_not_stored(self):
        session = FakeRuleSession()
        with patch.object(rules_service, "SessionLocal", return_value=session):
            self.assertFalse(rules_service.upsert_rule(1, "uid-7", "rule-1", "   "))
            self.assertFalse(rules_service.upsert_rule(1, "uid-7", "", "matn"))
        self.assertEqual(session.statements, [])

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
    """The shop's rules are in the prompt; there is no tool for them."""

    def test_no_rule_tool_is_offered_or_defined(self):
        from ai import tools

        names = [decl["name"] for decl in tools.DECLARATIONS]
        self.assertNotIn("search_shop_rules", names)
        self.assertNotIn("search_shop_rules", tools.TOOLS)
        self.assertFalse(hasattr(tools, "search_shop_rules"))


class ListRulesTest(unittest.TestCase):
    """list_rules: the whole set, for the system prompt."""

    def test_it_is_scoped_to_the_asking_account(self):
        session = FakeRuleSession(rows=[])
        with patch.object(rules_service, "SessionLocal", return_value=session):
            rules_service.list_rules("uid-7")

        self.assertEqual(session.params[0]["user_uid"], "uid-7")
        self.assertIn("user_uid = :user_uid", session.statements[0])

    def test_it_does_not_need_an_embedding(self):
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
            rules_service.AccountRule("r2", "Bu kategoriyaga 10%", priority=5),
            rules_service.AccountRule("r1", "Chegirma yo'q", priority=0),
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
        rules = [rules_service.AccountRule("r1", "Kafolat 6 oy", priority=0)]
        with patch.object(rules_service, "list_rules", return_value=rules) as listing, \
             patch.object(self.agent.instagram_service, "get_shop_name", return_value="wiki"):
            prompt = self.agent.system_prompt_for("uid-7")

        listing.assert_called_once_with("uid-7")
        self.assertIn(self.agent.SYSTEM_PROMPT, prompt)
        self.assertIn("- Kafolat 6 oy", prompt)

    def test_no_account_means_no_lookup_and_no_rules(self):
        with patch.object(rules_service, "list_rules") as listing:
            prompt = self.agent.system_prompt_for(None)

        listing.assert_not_called()
        self.assertIn("hali birorta ham qoida yozmagan", prompt)

    def test_a_reply_uses_the_rules_of_the_account_being_answered(self):
        from ai import tools

        rules = [rules_service.AccountRule("r1", "Yetkazib berish bepul", priority=0)]
        token = tools.set_store_context(tools.StoreContext(user_uid="uid-7"))
        try:
            with patch.object(rules_service, "list_rules", return_value=rules) as listing, \
                 patch.object(self.agent.instagram_service, "get_shop_name", return_value="wiki"), \
                 patch.object(self.agent, "generate", return_value="ok") as generate:
                self.agent.reply_to("Yetkazib berish bormi?")
        finally:
            tools._store_context.reset(token)

        listing.assert_called_once_with("uid-7")
        self.assertIn("Yetkazib berish bepul", generate.call_args.kwargs["system_instruction"])
        declared = [d["name"] for d in generate.call_args.kwargs["declarations"]]
        self.assertNotIn("search_shop_rules", declared)


class ShopNameInPromptTest(unittest.TestCase):
    """The bot introduces itself by the name the shop gave its app."""

    def setUp(self):
        from ai import agent

        self.agent = agent

    def _prompt(self, name):
        with patch.object(rules_service, "list_rules", return_value=[]), \
             patch.object(self.agent.instagram_service, "get_shop_name", return_value=name) as lookup:
            prompt = self.agent.system_prompt_for("uid-7")
        lookup.assert_called_once_with("uid-7")
        return prompt

    def test_the_fixed_prompt_names_no_shop(self):
        self.assertNotIn("MarketStore", self.agent.SYSTEM_PROMPT)

    def test_the_shops_own_name_opens_the_prompt(self):
        prompt = self._prompt("wiki")

        self.assertTrue(prompt.startswith('Sen "wiki" kompaniyasining'))
        self.assertIn("Men wiki kompaniyasining botiman. Sizga qanday yordam bera olaman?", prompt)
        self.assertNotIn("MarketStore", prompt)

    def test_a_rename_is_used_from_the_next_prompt(self):
        """Nothing is cached: each prompt reads the name again."""
        self.assertIn("Men wiki kompaniyasining", self._prompt("wiki"))
        self.assertIn("Men Texno Mart kompaniyasining", self._prompt("Texno Mart"))


class GetShopNameTest(unittest.TestCase):
    """Reading "Dastur nomi" from the account's synced app_settings."""

    def setUp(self):
        from app import instagram_service

        self.service = instagram_service

    def _stored(self, value):
        session = MagicMock()
        session.__enter__.return_value = session
        session.execute.return_value.scalar.return_value = value
        with patch.object(self.service, "SessionLocal", return_value=session):
            name = self.service.get_shop_name("uid-7")
        return name, session

    def test_the_stored_name_is_returned(self):
        name, session = self._stored("wiki")

        self.assertEqual(name, "wiki")
        self.assertEqual(session.execute.call_args.args[1], {"user_uid": "uid-7"})

    def test_the_name_is_kept_on_one_line(self):
        self.assertEqual(self._stored("  Wiki\n  Shop  ")[0], "Wiki Shop")

    def test_no_stored_name_falls_back_to_the_app_default(self):
        self.assertEqual(self._stored(None)[0], self.service.DEFAULT_APP_NAME)
        self.assertEqual(self._stored("   ")[0], self.service.DEFAULT_APP_NAME)

    def test_no_account_means_no_query(self):
        with patch.object(self.service, "SessionLocal") as factory:
            self.assertEqual(self.service.get_shop_name(None), self.service.DEFAULT_APP_NAME)
        factory.assert_not_called()

    def test_a_database_failure_is_the_default_not_an_error(self):
        with patch.object(self.service, "SessionLocal", side_effect=RuntimeError("db down")):
            self.assertEqual(self.service.get_shop_name("uid-7"), self.service.DEFAULT_APP_NAME)


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
        tools = {"get_product_specs": lambda **kw: calls.append(kw) or {"found": True}}
        used = {}

        for _ in range(self.gemini.MAX_CALLS_PER_TOOL):
            result = self.gemini.run_tool(self._part("get_product_specs", {"name": "x"}), tools, used)
            self.assertNotIn("error", result)

        refused = self.gemini.run_tool(self._part("get_product_specs", {"name": "x"}), tools, used)
        self.assertIn("error", refused)
        self.assertEqual(len(calls), self.gemini.MAX_CALLS_PER_TOOL)

    def test_one_exhausted_tool_does_not_block_another(self):
        """The whole point of a per-tool budget rather than a pooled one."""
        tools = {
            "search_products": lambda **kw: {"products": []},
            "get_product_specs": lambda **kw: {"found": True},
        }
        used = {"search_products": self.gemini.MAX_CALLS_PER_TOOL}

        blocked = self.gemini.run_tool(self._part("search_products", {"name": "x"}), tools, used)
        allowed = self.gemini.run_tool(self._part("get_product_specs", {"name": "x"}), tools, used)

        self.assertIn("error", blocked)
        self.assertNotIn("error", allowed)

    def test_the_refusal_tells_the_model_to_answer(self):
        """A bare error would have it retrying until the round limit ran out."""
        tools = {"get_product_specs": lambda **kw: {"found": True}}
        used = {"get_product_specs": self.gemini.MAX_CALLS_PER_TOOL}

        refused = self.gemini.run_tool(self._part("get_product_specs", {"name": "x"}), tools, used)
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
        tools = {"get_product_specs": lambda **kw: {"found": True}}
        for _ in range(20):
            result = self.gemini.run_tool(self._part("get_product_specs", {"name": "x"}), tools)
            self.assertNotIn("error", result)


if __name__ == "__main__":
    unittest.main()
