import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import database as db
import sync_service


class ServerFirstCacheTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="marketstore-server-first-")
        self.originals = {
            "DB_PATH": db.DB_PATH,
            "_ACTIVE_ACCOUNT_UID": db._ACTIVE_ACCOUNT_UID,
            "ACCOUNT_DB_ROOT": db.ACCOUNT_DB_ROOT,
            "_SESSION_DB_ROOT": db._SESSION_DB_ROOT,
            "LOCAL_PREFERENCES_PATH": db.LOCAL_PREFERENCES_PATH,
            "BACKUP_DIR": db.BACKUP_DIR,
            "REMOTE_DATA_MODE": db.REMOTE_DATA_MODE,
        }
        db.ACCOUNT_DB_ROOT = os.path.join(self.root, "persistent")
        db._SESSION_DB_ROOT = os.path.join(self.root, "session-a")
        db.LOCAL_PREFERENCES_PATH = os.path.join(self.root, "local_preferences.json")
        db.BACKUP_DIR = os.path.join(self.root, "backups")
        db.REMOTE_DATA_MODE = True

    def tearDown(self):
        if db._ENGINE is not None:
            db._ENGINE.dispose()
        db._ENGINE = None
        db._ENGINE_PATH = None
        db._SessionLocal = None
        for key, value in self.originals.items():
            setattr(db, key, value)
        shutil.rmtree(self.root, ignore_errors=True)

    def _open(self):
        activation = db.activate_account_database("acct-remote", email="owner@example.com")
        db.init_db(
            account_owner={
                "user_uid": "acct-remote",
                "email": "owner@example.com",
                "display_name": "Owner",
            },
            seed_defaults=False,
        )
        owner = db.sync_online_user(
            "owner@example.com",
            role="admin",
            user_uid="acct-remote",
            access_token="token",
        )
        return activation, dict(owner, api_access_token="token")

    def test_business_rows_are_disposable_but_interface_preferences_survive(self):
        activation, owner = self._open()
        self.assertTrue(activation["session_cache"])
        self.assertTrue(db.is_remote_session_cache())
        self.assertTrue(os.path.abspath(db.DB_PATH).startswith(os.path.abspath(db._SESSION_DB_ROOT)))
        self.assertFalse(os.path.exists(db.account_database_path("acct-remote", email="owner@example.com")))

        db.add_product({
            "barcode": "LOCAL-ONLY",
            "name": "Local only",
            "price": 10,
            "cost": 5,
            "stock": 1,
            "unit": "dona",
        })
        db.save_app_settings({"theme": "green", "language": "en"}, owner["id"])
        self.assertEqual(len(db.get_all_products()), 1)

        db._ENGINE.dispose()
        db._ENGINE = None
        db._ENGINE_PATH = None
        db._SessionLocal = None
        db._SESSION_DB_ROOT = os.path.join(self.root, "session-b")
        self._open()

        self.assertEqual(db.get_all_products(), [])
        settings = db.get_app_settings()
        self.assertEqual(settings["theme"], "green")
        self.assertEqual(settings["language"], "en")

    def test_login_bootstrap_and_pages_use_filtered_server_batches(self):
        _activation, owner = self._open()
        db.mark_server_bootstrap_required()
        responses = [
            {"records": [], "generation": 12, "cursor_supported": True, "cursor": 12},
            {"records": [], "generation": 12, "cursor_supported": True, "cursor": 12},
        ]
        with patch.object(sync_service.api_client, "pull_sync_records", side_effect=responses) as pull:
            result = sync_service.synchronize_account_storage(owner)
            page = sync_service.refresh_page_data(owner, "sales")

        self.assertEqual(result["direction"], "pull")
        startup_tables = pull.call_args_list[0].kwargs["table_names"]
        self.assertEqual(
            startup_tables,
            [
                "users", "currencies", "app_settings", "account_assets", "debtors",
                "activity_logs", "notification_reads",
            ],
        )
        self.assertNotIn("products", startup_tables)
        sales_tables = pull.call_args_list[1].kwargs["table_names"]
        self.assertIn("products", sales_tables)
        self.assertIn("customers", sales_tables)
        self.assertIsNone(pull.call_args_list[1].kwargs["since_seq"])
        self.assertEqual(db.get_pull_cursor(), 12)
        self.assertEqual(page["generation"], 12)

    def test_generation_zero_is_still_remembered_as_loaded(self):
        _activation, owner = self._open()
        db.mark_server_bootstrap_required()
        empty = {"records": [], "generation": 0, "cursor_supported": True, "cursor": 0}
        with patch.object(sync_service.api_client, "pull_sync_records", return_value=empty) as pull:
            sync_service.synchronize_account_storage(owner)
            sync_service.refresh_page_data(owner, "sales")
            sync_service.refresh_page_data(owner, "sales")
            sync_service.pull_server_changes(owner, incremental=True)

        self.assertTrue(db.is_pull_cursor_initialized())
        self.assertEqual(pull.call_args_list[2].kwargs["since_seq"], 0)
        self.assertEqual(pull.call_args_list[3].kwargs["since_seq"], 0)


if __name__ == "__main__":
    unittest.main()
