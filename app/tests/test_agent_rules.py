"""Adding, editing and deleting the rules this shop gives its Instagram agent.

The delete tests are the point of the file. The owner asked for two things that
pull in opposite directions - a deleted rule must be gone from this database, and
it must also disappear on every other device - and the only way to have both is a
tombstone plus a real DELETE. A regression would satisfy one and quietly drop the
other: the rule would come back on the next sync, or it would linger here as a
row that looks live.
"""

import shutil
import tempfile
import unittest

from sqlalchemy import select

import database as db


class AgentRuleTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="marketstore-rules-")
        self.old_path = db.DB_PATH
        self.old_uid = db._ACTIVE_ACCOUNT_UID
        db.activate_account_database("acct-rules", email="owner@example.com", storage_root=self.root)
        db.init_db(
            account_owner={"user_uid": "acct-rules", "email": "owner@example.com", "display_name": "Owner"},
            seed_defaults=False,
        )
        # The desktop gates business writes on a live server. Most tests here are
        # not about that gate, so it is stood down; OfflineGateTest puts it back.
        db.set_online_check(lambda: True)

    def tearDown(self):
        db.set_online_check(None)
        if db._ENGINE is not None:
            db._ENGINE.dispose()
        db._ENGINE = None
        db._ENGINE_PATH = None
        db._SessionLocal = None
        db.DB_PATH = self.old_path
        db._ACTIVE_ACCOUNT_UID = self.old_uid
        shutil.rmtree(self.root, ignore_errors=True)


class AddRuleTest(AgentRuleTestCase):
    def test_a_rule_is_stored_and_listed(self):
        rule_id = db.add_agent_rule("Kafolat 6 oy")

        rules = db.list_agent_rules()
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["id"], rule_id)
        self.assertEqual(rules[0]["text"], "Kafolat 6 oy")
        self.assertEqual(rules[0]["priority"], 0)

    def test_text_is_trimmed(self):
        db.add_agent_rule("   Kafolat 6 oy   ")
        self.assertEqual(db.list_agent_rules()[0]["text"], "Kafolat 6 oy")

    def test_an_empty_rule_is_refused(self):
        with self.assertRaises(ValueError):
            db.add_agent_rule("")
        with self.assertRaises(ValueError):
            db.add_agent_rule("    ")
        self.assertEqual(db.list_agent_rules(), [])

    def test_an_overlong_rule_is_refused(self):
        with self.assertRaises(ValueError):
            db.add_agent_rule("x" * (db.AGENT_RULE_MAX_CHARS + 1))
        self.assertEqual(db.list_agent_rules(), [])

    def test_every_rule_gets_its_own_id(self):
        first = db.add_agent_rule("Kafolat 6 oy")
        second = db.add_agent_rule("Yetkazib berish bepul")
        self.assertNotEqual(first, second)
        self.assertEqual(len(db.list_agent_rules()), 2)

    def test_higher_priority_is_listed_first(self):
        db.add_agent_rule("Oddiy qoida", priority=0)
        db.add_agent_rule("Muhim qoida", priority=10)

        rules = db.list_agent_rules()
        self.assertEqual(rules[0]["text"], "Muhim qoida")


class UpdateRuleTest(AgentRuleTestCase):
    def test_text_can_be_changed(self):
        rule_id = db.add_agent_rule("Kafolat 6 oy")

        self.assertTrue(db.update_agent_rule(rule_id, text_value="Kafolat 12 oy"))
        self.assertEqual(db.list_agent_rules()[0]["text"], "Kafolat 12 oy")

    def test_priority_can_be_changed_on_its_own(self):
        rule_id = db.add_agent_rule("Kafolat 6 oy")

        self.assertTrue(db.update_agent_rule(rule_id, priority=7))
        stored = db.list_agent_rules()[0]
        self.assertEqual(stored["priority"], 7)
        self.assertEqual(stored["text"], "Kafolat 6 oy")

    def test_an_unknown_id_reports_not_found(self):
        self.assertFalse(db.update_agent_rule("no-such-rule", text_value="matn"))

    def test_blanking_a_rule_is_refused(self):
        """Clearing the text is not how a rule is removed - delete is."""
        rule_id = db.add_agent_rule("Kafolat 6 oy")

        with self.assertRaises(ValueError):
            db.update_agent_rule(rule_id, text_value="   ")
        self.assertEqual(db.list_agent_rules()[0]["text"], "Kafolat 6 oy")

    def test_an_edit_marks_the_rule_for_re_embedding(self):
        """updated_at is what tells sync, and then the server, to look again."""
        rule_id = db.add_agent_rule("Kafolat 6 oy")
        before = db.list_agent_rules()[0]["updated_at"]

        db.update_agent_rule(rule_id, text_value="Kafolat 12 oy")
        after = db.list_agent_rules()[0]["updated_at"]

        self.assertTrue(after >= before)
        self.assertTrue(after)


class DeleteRuleTest(AgentRuleTestCase):
    """A deleted rule leaves this database, and still reaches the other devices."""

    def _tombstones(self):
        with db.session_scope() as session:
            return {
                (row.table_name, row.local_id)
                for row in session.scalars(select(db.SyncTombstone)).all()
            }

    def test_the_row_is_really_gone(self):
        rule_id = db.add_agent_rule("Kafolat 6 oy")

        self.assertTrue(db.delete_agent_rule(rule_id))
        self.assertEqual(db.list_agent_rules(), [])

    def test_a_tombstone_carries_the_deletion_to_other_devices(self):
        rule_id = db.add_agent_rule("Kafolat 6 oy")
        db.delete_agent_rule(rule_id)

        self.assertIn(("agent_rules", rule_id), self._tombstones())

    def test_only_the_named_rule_is_removed(self):
        keep = db.add_agent_rule("Kafolat 6 oy")
        drop = db.add_agent_rule("Yetkazib berish bepul")

        db.delete_agent_rule(drop)

        remaining = db.list_agent_rules()
        self.assertEqual([rule["id"] for rule in remaining], [keep])

    def test_deleting_twice_is_not_an_error(self):
        rule_id = db.add_agent_rule("Kafolat 6 oy")

        self.assertTrue(db.delete_agent_rule(rule_id))
        self.assertFalse(db.delete_agent_rule(rule_id))

    def test_an_unknown_id_reports_not_found(self):
        self.assertFalse(db.delete_agent_rule("no-such-rule"))
        self.assertFalse(db.delete_agent_rule(""))


class SyncMembershipTest(AgentRuleTestCase):
    def test_rules_are_a_synced_table(self):
        """Without this the rules would never leave the device that wrote them."""
        self.assertIn("agent_rules", db.SYNC_TABLES)

    def test_rules_are_keyed_by_uuid(self):
        """Two devices writing a rule must not be able to claim the same id."""
        self.assertIn("agent_rules", db.UUID_KEYED_TABLES)

    def test_rules_are_not_local_only(self):
        self.assertNotIn("agent_rules", db.LOCAL_ONLY_TABLES)


class OfflineGateTest(AgentRuleTestCase):
    """Rules are business data: writable only while the server is reachable.

    A rule saved offline would sit here with no vector on the server, and the shop
    would believe the bot was already answering by it.
    """

    def setUp(self):
        super().setUp()
        self.rule_id = db.add_agent_rule("Kafolat 6 oy")
        db.set_online_check(lambda: False)

    def test_adding_offline_is_refused(self):
        with self.assertRaises(db.AppError):
            db.add_agent_rule("Yetkazib berish bepul")

    def test_editing_offline_is_refused(self):
        with self.assertRaises(db.AppError):
            db.update_agent_rule(self.rule_id, text_value="Kafolat 12 oy")

    def test_deleting_offline_is_refused(self):
        with self.assertRaises(db.AppError):
            db.delete_agent_rule(self.rule_id)

    def test_reading_offline_still_works(self):
        """Seeing the rules is not a write, and a shop offline may still look."""
        self.assertEqual(len(db.list_agent_rules()), 1)


if __name__ == "__main__":
    unittest.main()
