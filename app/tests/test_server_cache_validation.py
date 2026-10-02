"""A cache kept between launches is checked once against the server.

It only ever moves forward by change number, which cannot skip a row. What it
cannot notice alone is the server going somewhere else while this device was
off - an erased account, a restore from backup, a copy that has drifted.
"""

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import api_client
import database as db
import sync_service


class ServerCacheValidationTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="marketstore-cache-check-")
        self.originals = {
            name: getattr(db, name)
            for name in ("DB_PATH", "_ACTIVE_ACCOUNT_UID", "ACCOUNT_DB_ROOT", "_SESSION_DB_ROOT",
                         "LOCAL_PREFERENCES_PATH", "BACKUP_DIR", "REMOTE_DATA_MODE")
        }
        db.ACCOUNT_DB_ROOT = os.path.join(self.root, "persistent")
        db._SESSION_DB_ROOT = os.path.join(self.root, "server_cache")
        db.LOCAL_PREFERENCES_PATH = os.path.join(self.root, "prefs.json")
        db.BACKUP_DIR = os.path.join(self.root, "backups")
        db.REMOTE_DATA_MODE = True
        self.owner = self._launch()
        # The first launch's load: startup tables and the expenses page.
        loaded = {
            "records": [self._category("Ijara")],
            "generation": 10, "cursor_supported": True, "cursor": 10,
        }
        with patch.object(api_client, "pull_sync_records", return_value=loaded):
            sync_service.synchronize_account_storage(self.owner)
            sync_service.refresh_page_data(self.owner, "expenses")
        self.assertFalse(db.is_server_bootstrap_required())

    def tearDown(self):
        if db._ENGINE is not None:
            db._ENGINE.dispose()
        db._ENGINE = None
        db._ENGINE_PATH = None
        db._SessionLocal = None
        for name, value in self.originals.items():
            setattr(db, name, value)
        shutil.rmtree(self.root, ignore_errors=True)

    @staticmethod
    def _category(name):
        row_id = db.stable_row_id("expense_categories", name)
        return {"table_name": "expense_categories", "local_id": row_id,
                "data": {"id": row_id, "name": name}}

    @staticmethod
    def _names():
        return sorted(row["name"] for row in db.get_expense_categories())

    def _launch(self):
        if db._ENGINE is not None:
            db._ENGINE.dispose()
            db._ENGINE = None
            db._ENGINE_PATH = None
            db._SessionLocal = None
        activation = db.activate_account_database("acct-cache", email="owner@example.com")
        db.init_db(account_owner={"user_uid": "acct-cache", "email": "owner@example.com",
                                  "display_name": "Owner"}, seed_defaults=False)
        if activation.get("database_created"):
            db.mark_server_bootstrap_required()
        owner = db.sync_online_user("owner@example.com", role="admin",
                                    user_uid="acct-cache", access_token="tok")
        return dict(owner, api_access_token="tok")

    def _second_launch(self, state, summary_tables, changes=None):
        self.owner = self._launch()
        changes = changes or {"records": [], "generation": state["generation"],
                              "cursor_supported": True, "cursor": state["generation"]}
        with patch.object(api_client, "get_sync_state", return_value=state), \
             patch.object(api_client, "get_sync_summary",
                          return_value={"tables": summary_tables}) as summary, \
             patch.object(api_client, "pull_sync_records", return_value=changes) as pull:
            result = sync_service.synchronize_account_storage(self.owner)
        return result, pull, summary

    @staticmethod
    def _matching_summary():
        return [{"table_name": "expense_categories", "records_count": 1, "deleted_count": 0}]

    def test_an_unchanged_server_costs_no_download(self):
        result, pull, _ = self._second_launch(
            {"generation": 10, "purge_generation": 0}, self._matching_summary()
        )
        self.assertEqual(result["direction"], "cache")
        self.assertEqual(result["cache"], "kept")
        # Only the changes since the last launch were asked for.
        self.assertEqual(pull.call_args.kwargs["since_seq"], 10)
        self.assertIn("Ijara", self._names())
        self.assertFalse(db.is_server_bootstrap_required())

    def test_changes_made_elsewhere_while_closed_are_brought_in(self):
        changes = {
            "records": [self._category("Svet")],
            "generation": 11, "cursor_supported": True, "cursor": 11,
        }
        result, _, _ = self._second_launch(
            {"generation": 11, "purge_generation": 0},
            [{"table_name": "expense_categories", "records_count": 2, "deleted_count": 0}],
            changes,
        )
        self.assertEqual(result["cache"], "kept")
        self.assertTrue({"Ijara", "Svet"} <= set(self._names()))

    def test_a_server_restored_from_backup_starts_the_cache_over(self):
        result, _, _ = self._second_launch(
            {"generation": 4, "purge_generation": 0}, self._matching_summary()
        )
        self.assertEqual(result["direction"], "pull")
        self.assertNotIn("Ijara", self._names())

    def test_a_drifted_table_starts_the_cache_over(self):
        result, _, _ = self._second_launch(
            {"generation": 10, "purge_generation": 0},
            # The server holds more live categories than this cache has rows.
            [{"table_name": "expense_categories", "records_count": 6, "deleted_count": 1}],
        )
        self.assertEqual(result["direction"], "pull")
        self.assertNotIn("Ijara", self._names())

    def test_rows_waiting_to_be_sent_are_never_thrown_away(self):
        with db._get_engine().begin() as conn:
            db._write_outbox_entries(conn, {("expense_categories",
                                             db.stable_row_id("expense_categories", "Ijara"), "upsert")})
        result, pull, summary = self._second_launch(
            {"generation": 4, "purge_generation": 0}, []
        )
        self.assertEqual(result["skipped"], "pending_changes")
        self.assertIn("Ijara", self._names())
        summary.assert_not_called()

    def test_the_seeded_defaults_are_not_mistaken_for_drift(self):
        # Default currencies, the "Kassir" category and the owner's own row
        # exist only here.
        result, _, _ = self._second_launch(
            {"generation": 10, "purge_generation": 0},
            self._matching_summary() + [{"table_name": "users", "records_count": 0,
                                         "deleted_count": 0}],
        )
        self.assertEqual(result["cache"], "kept")


if __name__ == "__main__":
    unittest.main()
