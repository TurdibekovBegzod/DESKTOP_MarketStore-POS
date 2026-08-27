"""A change made from a stale screen must not undo what happened meanwhile.

Two cashiers work at once. One sells a product; the other, whose screen still
shows yesterday's stock, edits it and sends that. Without this the second write
lands on top and the sale disappears from the stock. The device now says which
version of the row it was looking at, and the server refuses the change if the
row has moved on -- that one row only, never the whole batch.
"""

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import api_client
import database as db
import sync_service


class StaleWriteProtectionTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="marketstore-stale-")
        self.old_path = db.DB_PATH
        self.old_uid = db._ACTIVE_ACCOUNT_UID
        self.old_backup = db.BACKUP_DIR
        db.BACKUP_DIR = os.path.join(self.root, "backups")
        db.activate_account_database("acct-stale", email="owner@example.com", storage_root=self.root)
        db.init_db(
            account_owner={"user_uid": "acct-stale", "email": "owner@example.com", "display_name": "Owner"},
            seed_defaults=False,
        )
        self.owner = db.sync_online_user(
            "owner@example.com", role="admin", user_uid="acct-stale", access_token="tok"
        )
        db.mark_upgrade_reconcile_complete()
        db.mark_identity_reset_complete()
        db.mark_sync_pushed()

    def tearDown(self):
        if db._ENGINE is not None:
            db._ENGINE.dispose()
        db._ENGINE = None
        db._ENGINE_PATH = None
        db._SessionLocal = None
        db.DB_PATH = self.old_path
        db._ACTIVE_ACCOUNT_UID = self.old_uid
        db.BACKUP_DIR = self.old_backup
        shutil.rmtree(self.root, ignore_errors=True)

    @staticmethod
    def _server_product(barcode="SHARED", stock=10, version=4):
        row_id = db.stable_row_id("products", barcode)
        return {
            "table_name": "products",
            "local_id": row_id,
            "sync_version": version,
            "data": {"id": row_id, "barcode": barcode, "name": "Umumiy",
                     "price": 1000, "cost": 600, "stock": stock, "unit": "dona"},
        }

    # -- remembering what we saw -----------------------------------------
    def test_the_version_of_every_downloaded_row_is_remembered(self):
        record = self._server_product(version=7)
        db.import_sync_records([record])

        self.assertEqual(db.get_known_row_version("products", record["local_id"]), 7)

    def test_a_change_says_which_version_it_was_made_from(self):
        record = self._server_product(version=7)
        db.import_sync_records([record])
        db.update_product(record["local_id"], {"name": "Yangi nom", "barcode": "SHARED",
                                               "price": 1200, "cost": 600})

        sent = [r for r in db.export_sync_records(incremental=True)
                if r["local_id"] == record["local_id"]]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["expected_version"], 7)

    def test_a_brand_new_row_claims_no_version(self):
        """This is what keeps a sale from ever being refused."""
        product = db.add_product({"barcode": "NEW", "name": "Yangi", "price": 10,
                                  "cost": 5, "stock": 1, "unit": "dona"})

        sent = [r for r in db.export_sync_records(incremental=True)
                if r["local_id"] == str(product)]
        self.assertEqual(len(sent), 1)
        self.assertIsNone(sent[0]["expected_version"])

    def test_after_sending_we_no_longer_claim_to_know_the_version(self):
        record = self._server_product(version=7)
        db.import_sync_records([record])
        db.update_product(record["local_id"], {"name": "Yangi", "barcode": "SHARED",
                                               "price": 1200, "cost": 600})

        with patch.object(api_client, "pull_sync_records",
                          return_value={"records": [], "server_time": "t", "generation": 5}), \
             patch.object(api_client, "get_sync_state",
                          return_value={"generation": 5, "purge_generation": 0}), \
             patch.object(api_client, "push_sync_records",
                          return_value={"saved": 1, "batch_id": "b", "generation": 6, "rejected": []}):
            sync_service.push_local_changes(self.owner)

        self.assertIsNone(db.get_known_row_version("products", record["local_id"]))

    # -- being told no ----------------------------------------------------
    def test_a_refused_row_is_replaced_by_the_server_copy_and_reported(self):
        record = self._server_product(stock=10, version=4)
        db.import_sync_records([record])
        db.update_product(record["local_id"], {"name": "Eskirgan tahrir", "barcode": "SHARED",
                                               "price": 1200, "cost": 600})

        newer = self._server_product(stock=7, version=5)
        newer["data"]["name"] = "Boshqa qurilmadan"
        pulls = {"count": 0}

        def fake_pull(*_args, **_kwargs):
            pulls["count"] += 1
            # The refusal is followed by a download, which is where the
            # newer copy comes from.
            records = [newer] if pulls["count"] > 1 else []
            return {"records": records, "server_time": "t", "generation": 6}

        def fake_push(_token, records, **_kwargs):
            refused = [{
                "table_name": r["table_name"], "local_id": r["local_id"],
                "expected_version": r.get("expected_version"), "server_version": 5,
            } for r in records if r["table_name"] == "products"]
            return {"saved": len(records) - len(refused), "batch_id": "b",
                    "generation": 6, "rejected": refused}

        with patch.object(api_client, "pull_sync_records", side_effect=fake_pull), \
             patch.object(api_client, "get_sync_state",
                          return_value={"generation": 6, "purge_generation": 0}), \
             patch.object(api_client, "push_sync_records", side_effect=fake_push):
            outcome = sync_service.auto_sync_turn(self.owner)

        self.assertEqual(len(outcome["rejected"]), 1)
        self.assertIn("products", outcome["tables"])
        product = db.get_product_by_barcode("SHARED")
        self.assertEqual(product["name"], "Boshqa qurilmadan")
        self.assertEqual(product["stock"], 7)

    def test_the_rest_of_the_batch_still_goes_through(self):
        """One stale row must not turn into a failed sale."""
        record = self._server_product(version=4)
        db.import_sync_records([record])
        db.update_product(record["local_id"], {"name": "Eskirgan", "barcode": "SHARED",
                                               "price": 1200, "cost": 600})
        db.add_product({"barcode": "FINE", "name": "Yaxshi", "price": 10,
                        "cost": 5, "stock": 1, "unit": "dona"})

        saved = {"count": 0}

        def fake_push(_token, records, **_kwargs):
            refused = [{"table_name": r["table_name"], "local_id": r["local_id"],
                        "expected_version": r.get("expected_version"), "server_version": 5}
                       for r in records if r.get("expected_version") is not None]
            saved["count"] += len(records) - len(refused)
            return {"saved": len(records) - len(refused), "batch_id": "b",
                    "generation": 6, "rejected": refused}

        with patch.object(api_client, "pull_sync_records",
                          return_value={"records": [], "server_time": "t", "generation": 6}), \
             patch.object(api_client, "get_sync_state",
                          return_value={"generation": 6, "purge_generation": 0}), \
             patch.object(api_client, "push_sync_records", side_effect=fake_push):
            outcome = sync_service.auto_sync_turn(self.owner)

        self.assertEqual(len(outcome["rejected"]), 1)
        self.assertGreaterEqual(saved["count"], 1)


if __name__ == "__main__":
    unittest.main()
