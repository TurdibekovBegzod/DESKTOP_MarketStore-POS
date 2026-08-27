"""A device that just converted to UUID keys must still be able to sync.

The identity migration marks the device as owing the server a replacement,
because the server still holds rows keyed by the old integers. But that marker
alone must not decide anything: another device of the same account may have
replaced the server already. Deciding from the marker rather than from what the
server actually returns produced two failures -- a device that could never
download, and a second device that wiped what the first had just uploaded.
"""

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import api_client
import database as db
import sync_service


class IdentityResetRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.storage_root = tempfile.mkdtemp(prefix="marketstore-reset-")
        self.old_path = db.DB_PATH
        self.old_active_uid = db._ACTIVE_ACCOUNT_UID
        self.old_backup_dir = db.BACKUP_DIR
        db.BACKUP_DIR = os.path.join(self.storage_root, "backups")
        db.activate_account_database("acct-1", email="one@example.com", storage_root=self.storage_root)
        db.init_db(
            account_owner={"user_uid": "acct-1", "email": "one@example.com", "display_name": "one"},
            seed_defaults=False,
        )
        self.owner = db.sync_online_user(
            "one@example.com", role="admin", user_uid="acct-1", access_token="tok-1"
        )
        self._mark_reset_required()

    def tearDown(self):
        if db._ENGINE is not None:
            db._ENGINE.dispose()
        db._ENGINE = None
        db._ENGINE_PATH = None
        db._SessionLocal = None
        db.DB_PATH = self.old_path
        db._ACTIVE_ACCOUNT_UID = self.old_active_uid
        db.BACKUP_DIR = self.old_backup_dir
        shutil.rmtree(self.storage_root, ignore_errors=True)

    @staticmethod
    def _mark_reset_required():
        with db._get_engine().begin() as conn:
            db._sync_state_set(conn, "identity_reset_required", "1")

    @staticmethod
    def _mark_upgrade_pending():
        with db._get_engine().begin() as conn:
            db._sync_state_set(conn, "upgrade_reconcile_required", "1")

    @staticmethod
    def _legacy_records():
        return [{
            "table_name": "products",
            "local_id": "501",
            "data": {"id": 501, "barcode": "OLD", "name": "Eski", "price": 1,
                     "cost": 1, "stock": 1, "unit": "dona"},
        }]

    @staticmethod
    def _upgraded_records():
        row_id = db.stable_row_id("products", "from-sibling")
        return [{
            "table_name": "products",
            "local_id": row_id,
            "data": {"id": row_id, "barcode": "NEW", "name": "Boshqa qurilmadan",
                     "price": 10, "cost": 5, "stock": 3, "unit": "dona"},
        }]

    def _pull(self, records):
        with patch.object(
            api_client, "pull_sync_records",
            return_value={"records": records, "server_time": None, "generation": 4},
        ), patch.object(
            api_client, "get_sync_state",
            return_value={"generation": 4, "purge_generation": 0},
        ):
            return sync_service.pull_server_changes(self.owner)

    # -- download --------------------------------------------------------
    def test_a_sibling_that_already_upgraded_unblocks_the_download(self):
        result = self._pull(self._upgraded_records())

        self.assertEqual(result["imported"], 1)
        self.assertIsNotNone(db.get_product_by_barcode("NEW"))
        # Nothing left to replace: the server is already on the new format.
        self.assertFalse(db.is_identity_reset_required())

    def test_an_empty_server_also_clears_the_marker(self):
        result = self._pull([])

        self.assertEqual(result["imported"], 0)
        self.assertFalse(db.is_identity_reset_required())

    def test_a_server_still_on_the_old_format_says_what_to_do(self):
        with self.assertRaises(sync_service.SyncError) as caught:
            self._pull(self._legacy_records())

        self.assertIn("Yuborish", str(caught.exception))
        # The marker stays: the replacement really is still owed.
        self.assertTrue(db.is_identity_reset_required())
        self.assertIsNone(db.get_product_by_barcode("OLD"))

    # -- upload ----------------------------------------------------------
    def test_the_second_device_merges_instead_of_wiping_the_first(self):
        pushed = {"records": None, "reset": 0}

        def fake_push(_token, records, **_kwargs):
            pushed["records"] = records
            return {"saved": len(records), "batch_id": "b1", "generation": 5}

        def fake_reset(*_args, **_kwargs):
            pushed["reset"] += 1
            return {"removed": 0, "generation": 5}

        with patch.object(
            api_client, "pull_sync_records",
            return_value={"records": self._upgraded_records(), "server_time": None, "generation": 4},
        ), patch.object(
            api_client, "get_sync_state",
            return_value={"generation": 4, "purge_generation": 0},
        ), patch.object(api_client, "push_sync_records", side_effect=fake_push), \
             patch.object(api_client, "reset_sync_records", side_effect=fake_reset):
            sync_service.push_local_changes(self.owner)

        self.assertEqual(pushed["reset"], 0, "the sibling's upload must not be wiped")
        self.assertIsNotNone(db.get_product_by_barcode("NEW"))
        self.assertFalse(db.is_identity_reset_required())

    def test_the_first_upgraded_device_does_replace_a_legacy_server(self):
        calls = {"reset": 0}

        def fake_reset(*_args, **_kwargs):
            calls["reset"] += 1
            return {"removed": 1, "generation": 5}

        with patch.object(
            api_client, "pull_sync_records",
            return_value={"records": self._legacy_records(), "server_time": None, "generation": 4},
        ), patch.object(
            api_client, "get_sync_state",
            return_value={"generation": 4, "purge_generation": 0},
        ), patch.object(
            api_client, "push_sync_records",
            return_value={"saved": 0, "batch_id": "b1", "generation": 5},
        ), patch.object(api_client, "reset_sync_records", side_effect=fake_reset):
            sync_service.push_local_changes(self.owner)

        self.assertEqual(calls["reset"], 1)

    # -- the one-time settlement after an upgrade ------------------------
    def test_the_first_sync_takes_the_server_copy_and_drops_the_stale_one(self):
        """Rows deleted on the other devices must not come back from this one."""
        self._mark_upgrade_pending()
        stale = db.add_product({
            "barcode": "STALE", "name": "Boshqa qurilmada o'chirilgan",
            "price": 1, "cost": 1, "stock": 1, "unit": "dona",
        })
        self.assertIsNotNone(stale)

        pushed = {"count": 0}

        with patch.object(
            api_client, "pull_sync_records",
            return_value={"records": self._upgraded_records(), "server_time": None, "generation": 9},
        ), patch.object(
            api_client, "get_sync_state",
            return_value={"generation": 9, "purge_generation": 0},
        ), patch.object(
            api_client, "push_sync_records",
            side_effect=lambda *a, **k: pushed.__setitem__("count", pushed["count"] + 1)
            or {"saved": 0, "batch_id": "b", "generation": 9},
        ):
            result = sync_service.pull_server_changes(self.owner)

        self.assertTrue(result["adopted_server"])
        self.assertTrue(os.path.exists(result["backup_path"]))
        # The server's row is here; the stale local one is not.
        self.assertIsNotNone(db.get_product_by_barcode("NEW"))
        self.assertIsNone(db.get_product_by_barcode("STALE"))
        self.assertFalse(db.is_upgrade_reconcile_required())
        self.assertEqual(pushed["count"], 0, "adopting the server must not upload first")

    def test_a_device_with_nothing_on_the_server_uploads_instead(self):
        self._mark_upgrade_pending()
        db.add_product({
            "barcode": "ONLY-HERE", "name": "Faqat shu qurilmada",
            "price": 1, "cost": 1, "stock": 1, "unit": "dona",
        })
        calls = {"reset": 0, "push": 0}

        with patch.object(
            api_client, "pull_sync_records",
            return_value={"records": [], "server_time": None, "generation": 0},
        ), patch.object(
            api_client, "get_sync_state",
            return_value={"generation": 0, "purge_generation": 0},
        ), patch.object(
            api_client, "reset_sync_records",
            side_effect=lambda *a, **k: calls.__setitem__("reset", calls["reset"] + 1)
            or {"removed": 0, "generation": 1},
        ), patch.object(
            api_client, "push_sync_records",
            side_effect=lambda *a, **k: calls.__setitem__("push", calls["push"] + 1)
            or {"saved": 1, "batch_id": "b", "generation": 2},
        ):
            result = sync_service.push_local_changes(self.owner)

        self.assertFalse(result["adopted_server"])
        self.assertGreaterEqual(calls["push"], 1)
        self.assertIsNotNone(db.get_product_by_barcode("ONLY-HERE"))
        self.assertFalse(db.is_upgrade_reconcile_required())

    def test_the_settlement_happens_only_once(self):
        self._mark_upgrade_pending()
        pulls = {"count": 0}

        def counted_pull(*_args, **_kwargs):
            pulls["count"] += 1
            return {"records": self._upgraded_records(), "server_time": None, "generation": 9}

        with patch.object(api_client, "pull_sync_records", side_effect=counted_pull), \
             patch.object(api_client, "get_sync_state",
                          return_value={"generation": 9, "purge_generation": 0}):
            sync_service.pull_server_changes(self.owner)
            first = pulls["count"]
            sync_service.pull_server_changes(self.owner)

        self.assertGreater(first, 0)
        self.assertFalse(db.is_upgrade_reconcile_required())


if __name__ == "__main__":
    unittest.main()
