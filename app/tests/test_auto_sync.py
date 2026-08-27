"""Synchronisation without anybody pressing a button.

Two things had to change for this. The server has always been able to hand
back only what moved since a given moment, but the client never asked, so every
download was a full copy of the account. And nothing ever ran on its own: the
data only moved when someone pressed Send or Download.
"""

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import api_client
import database as db
import sync_service


class AutoSyncTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="marketstore-auto-")
        self.old_path = db.DB_PATH
        self.old_uid = db._ACTIVE_ACCOUNT_UID
        self.old_backup = db.BACKUP_DIR
        db.BACKUP_DIR = os.path.join(self.root, "backups")
        db.activate_account_database("acct-auto", email="owner@example.com", storage_root=self.root)
        db.init_db(
            account_owner={"user_uid": "acct-auto", "email": "owner@example.com", "display_name": "Owner"},
            seed_defaults=False,
        )
        self.owner = db.sync_online_user(
            "owner@example.com", role="admin", user_uid="acct-auto", access_token="tok"
        )
        db.mark_upgrade_reconcile_complete()
        db.mark_identity_reset_complete()

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
    def _record(barcode="FROM-SERVER"):
        row_id = db.stable_row_id("products", barcode)
        return {
            "table_name": "products",
            "local_id": row_id,
            "data": {"id": row_id, "barcode": barcode, "name": barcode,
                     "price": 10, "cost": 5, "stock": 1, "unit": "dona"},
        }

    @staticmethod
    def _legacy_record():
        return {
            "table_name": "products",
            "local_id": "77",
            "data": {"id": 77, "barcode": "OLD", "name": "Eski", "price": 1,
                     "cost": 1, "stock": 1, "unit": "dona"},
        }

    def _pull_patch(self, records, server_time, seen):
        def fake_pull(_token, since=None, table_name=None, include_deleted=True, timeout=30):
            seen.append(since)
            return {"records": records, "server_time": server_time, "generation": 3}
        return patch.object(api_client, "pull_sync_records", side_effect=fake_pull)

    # -- the reading position --------------------------------------------
    def test_the_first_download_asks_for_everything_and_the_next_only_for_changes(self):
        seen = []
        with self._pull_patch([self._record()], "2026-08-27 10:00:00", seen), \
             patch.object(api_client, "get_sync_state",
                          return_value={"generation": 3, "purge_generation": 0}):
            sync_service.pull_server_changes(self.owner, incremental=True)
            sync_service.pull_server_changes(self.owner, incremental=True)

        self.assertEqual(seen[0], None)
        self.assertEqual(seen[1], "2026-08-27 10:00:00")

    def test_the_position_does_not_move_past_a_record_that_was_dropped(self):
        seen = []
        with self._pull_patch([self._legacy_record()], "2026-08-27 10:00:00", seen), \
             patch.object(api_client, "get_sync_state",
                          return_value={"generation": 3, "purge_generation": 0}):
            sync_service.pull_server_changes(self.owner, incremental=True)

        self.assertIsNone(db.get_pull_watermark())

    # -- one automatic round ---------------------------------------------
    def test_a_round_takes_before_it_gives(self):
        order = []

        def fake_pull(_token, since=None, table_name=None, include_deleted=True, timeout=30):
            order.append("pull")
            return {"records": [self._record()], "server_time": "t1", "generation": 3}

        def fake_push(_token, records, **_kwargs):
            order.append("push")
            return {"saved": len(records), "batch_id": "b", "generation": 4}

        db.add_product({"barcode": "MINE", "name": "Meniki", "price": 1,
                        "cost": 1, "stock": 1, "unit": "dona"})

        with patch.object(api_client, "pull_sync_records", side_effect=fake_pull), \
             patch.object(api_client, "push_sync_records", side_effect=fake_push), \
             patch.object(api_client, "get_sync_state",
                          return_value={"generation": 3, "purge_generation": 0}):
            outcome = sync_service.auto_sync_turn(self.owner)

        self.assertEqual(order[0], "pull")
        self.assertIn("push", order)
        self.assertEqual(outcome["pulled"], 1)
        self.assertGreaterEqual(outcome["pushed"], 1)
        self.assertIn("products", outcome["tables"])
        self.assertIsNotNone(db.get_product_by_barcode("FROM-SERVER"))

    def test_a_round_with_nothing_of_ours_to_send_does_not_send(self):
        pushes = []

        with self._pull_patch([self._record()], "t1", []), \
             patch.object(api_client, "push_sync_records",
                          side_effect=lambda *a, **k: pushes.append(1)), \
             patch.object(api_client, "get_sync_state",
                          return_value={"generation": 3, "purge_generation": 0}):
            outcome = sync_service.auto_sync_turn(self.owner)

        self.assertEqual(pushes, [])
        self.assertEqual(outcome["pushed"], 0)

    def test_a_conflict_is_re_read_and_retried_once(self):
        db.add_product({"barcode": "MINE", "name": "Meniki", "price": 1,
                        "cost": 1, "stock": 1, "unit": "dona"})
        attempts = {"push": 0}

        def fake_push(_token, records, **_kwargs):
            attempts["push"] += 1
            if attempts["push"] == 1:
                raise api_client.SyncConflictError("busy", server_generation=9, expected_generation=3)
            return {"saved": len(records), "batch_id": "b", "generation": 10}

        with self._pull_patch([], "t1", []), \
             patch.object(api_client, "push_sync_records", side_effect=fake_push), \
             patch.object(api_client, "get_sync_state",
                          return_value={"generation": 3, "purge_generation": 0}):
            outcome = sync_service.auto_sync_turn(self.owner)

        self.assertEqual(attempts["push"], 2)
        self.assertFalse(outcome["conflict"])

    # -- applying a download in pieces ------------------------------------
    def test_a_large_download_is_applied_in_bounded_pieces(self):
        records = []
        for index in range(450):
            barcode = f"BULK-{index}"
            row_id = db.stable_row_id("products", barcode)
            records.append({
                "table_name": "products", "local_id": row_id,
                "data": {"id": row_id, "barcode": barcode, "name": barcode,
                         "price": 1, "cost": 1, "stock": 1, "unit": "dona"},
            })

        imported = db.import_sync_records(records, chunk_size=100)

        self.assertEqual(imported, 450)
        self.assertEqual(len(db.get_all_products()), 450)

    # -- writing money needs the server -----------------------------------
    def test_money_is_refused_while_the_server_is_unreachable(self):
        product = db.add_product({"barcode": "P1", "name": "Mahsulot", "price": 1000,
                                  "cost": 600, "stock": 10, "unit": "dona"})
        cashier = db.add_user("k@example.com", role="cashier", username="K")
        cart = [{"product_id": product, "quantity": 1, "price": 1000, "subtotal": 1000}]

        db.set_online_check(lambda: False)
        try:
            with self.assertRaises(db.AppError) as refused:
                db.create_sale(None, cashier, cart, 1000, 0, 1000, "naqd")
            self.assertIn("Internet", str(refused.exception))

            with self.assertRaises(db.AppError):
                db.add_expense(None, 5000, "UZS", "Ijara", user_id=cashier)
        finally:
            db.set_online_check(None)

        # And goes through again the moment the connection is back.
        sale_id = db.create_sale(None, cashier, cart, 1000, 0, 1000, "naqd")
        self.assertTrue(db.is_row_uuid(sale_id))

    def test_stock_and_reports_still_work_while_offline(self):
        """Only writing money is blocked; looking things up is not."""
        db.add_product({"barcode": "P2", "name": "Mahsulot", "price": 1000,
                        "cost": 600, "stock": 10, "unit": "dona"})
        db.set_online_check(lambda: False)
        try:
            self.assertEqual(len(db.get_all_products()), 1)
            self.assertIsNotNone(db.get_finance_rows("2020-01-01", "2030-01-01"))
        finally:
            db.set_online_check(None)


if __name__ == "__main__":
    unittest.main()
