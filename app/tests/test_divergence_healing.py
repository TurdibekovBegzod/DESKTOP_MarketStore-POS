"""Two devices that have drifted apart find their way back together.

This is written from a photograph of two shop machines standing next to each
other. Both said "Onlayn". Both were sure they were current. One listed four
laptops the other had never heard of, and the other listed four the first had
never heard of. Nothing in the app noticed, because nothing in the app was
looking: the upload queue carries what changed here, the download marker carries
what changed there, and neither has any way of spotting a difference that was
never written down in the first place.

So there is now a round that stops trading differences and compares the whole
picture instead. These tests are that round, and the two failures it has to
survive: a row whose queue entry was lost, and a download marker that has moved
past rows this device never actually received.
"""

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import api_client
import database as db
import sync_service


class _FakeServer:
    """Stores rows the way the real one does, with a change history."""

    def __init__(self):
        self.rows = {}
        self.generation = 0
        self.asked = []

    def push(self, _token, records, **_kwargs):
        self.generation += 1
        saved = 0
        for record in records:
            key = (record["table_name"], str(record["local_id"]))
            existing = self.rows.get(key)
            stored = dict(record)
            stored["sync_version"] = (existing["sync_version"] + 1) if existing else 1
            stored["change_seq"] = self.generation
            self.rows[key] = stored
            saved += 1
        return {"saved": saved, "batch_id": "b", "generation": self.generation, "rejected": []}

    def pull(self, _token, since=None, since_seq=None, table_name=None,
             include_deleted=True, timeout=30):
        self.asked.append({"since": since, "since_seq": since_seq})
        records = []
        for key, row in self.rows.items():
            if table_name is not None and key[0] != table_name:
                continue
            if since_seq is not None and row["change_seq"] <= since_seq:
                continue
            records.append({k: v for k, v in row.items() if k != "change_seq"} | {
                "change_seq": row["change_seq"]
            })
        return {
            "records": records,
            "server_time": f"t{self.generation}",
            "generation": self.generation,
            "cursor": max((row["change_seq"] for row in records), default=0),
            "cursor_supported": True,
        }

    def state(self, *_args, **_kwargs):
        return {"generation": self.generation, "purge_generation": 0}


class DivergenceHealingTest(unittest.TestCase):
    ACCOUNT = "acct-heal"
    EMAIL = "owner@example.com"

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="marketstore-heal-")
        self.old_path = db.DB_PATH
        self.old_uid = db._ACTIVE_ACCOUNT_UID
        self.old_backup = db.BACKUP_DIR
        db.BACKUP_DIR = os.path.join(self.root, "backups")
        self.server = _FakeServer()
        self.patches = [
            patch.object(api_client, "push_sync_records", side_effect=self.server.push),
            patch.object(api_client, "pull_sync_records", side_effect=self.server.pull),
            patch.object(api_client, "get_sync_state", side_effect=self.server.state),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        if db._ENGINE is not None:
            db._ENGINE.dispose()
        db._ENGINE = None
        db._ENGINE_PATH = None
        db._SessionLocal = None
        db.DB_PATH = self.old_path
        db._ACTIVE_ACCOUNT_UID = self.old_uid
        db.BACKUP_DIR = self.old_backup
        shutil.rmtree(self.root, ignore_errors=True)

    def _use(self, device):
        if db._ENGINE is not None:
            db._ENGINE.dispose()
        db._ENGINE = None
        db._ENGINE_PATH = None
        db._SessionLocal = None
        db.activate_account_database(
            self.ACCOUNT, email=self.EMAIL, storage_root=os.path.join(self.root, device)
        )
        db.init_db(
            account_owner={"user_uid": self.ACCOUNT, "email": self.EMAIL, "display_name": "Owner"},
            seed_defaults=False,
        )
        owner = db.sync_online_user(self.EMAIL, role="admin", user_uid=self.ACCOUNT, access_token="tok")
        db.mark_upgrade_reconcile_complete()
        db.mark_identity_reset_complete()
        return owner

    @staticmethod
    def _product(barcode, name):
        return db.add_product({"barcode": barcode, "name": name, "price": 1000,
                               "cost": 800, "stock": 5, "unit": "dona"})

    @staticmethod
    def _forget_the_queue():
        """Whatever the cause -- a crash, a restore, a bug -- the queue entry is
        gone and the row is now invisible to every other device."""
        with db._get_engine().begin() as conn:
            conn.exec_driver_sql("DELETE FROM sync_outbox")

    # -- the download position ---------------------------------------------
    def test_the_download_asks_by_position_once_it_knows_one(self):
        owner = self._use("a")
        self._product("P1", "Lenovo")
        for _ in range(3):
            sync_service.auto_sync_turn(owner)
        by_number = [ask for ask in self.server.asked if ask["since_seq"] is not None]
        self.assertTrue(by_number, "later downloads must ask by position, not by clock")
        self.assertTrue(all(ask["since"] is None for ask in by_number))

    # -- the failure the photograph showed ----------------------------------
    def test_a_row_that_never_reached_the_queue_is_still_delivered(self):
        owner = self._use("a")
        self._product("P1", "Lenovo")
        self._forget_the_queue()
        sync_service.auto_sync_turn(owner)
        self.assertEqual(self.server.rows, {}, "nothing was queued, so nothing went")

        healed = sync_service.reconcile_full(owner)
        self.assertGreater(healed["queued"], 0)
        sync_service.auto_sync_turn(owner)

        owner_b = self._use("b")
        sync_service.auto_sync_turn(owner_b)
        self.assertIsNotNone(db.get_product_by_barcode("P1"))

    def test_two_devices_that_drifted_apart_end_up_with_both_lists(self):
        owner_a = self._use("a")
        self._product("A1", "Surface Laptop 3")
        self._product("A2", "hp Omnibook")
        self._forget_the_queue()

        owner_b = self._use("b")
        self._product("B1", "Dell Latitude 5440")
        self._product("B2", "Hp Elitebook")
        self._forget_the_queue()

        # Ordinary rounds settle nothing: neither device knows it is missing
        # anything, and that is exactly how it looked in the shop.
        self._use("a")
        sync_service.auto_sync_turn(owner_a)
        self._use("b")
        sync_service.auto_sync_turn(owner_b)
        self._use("a")
        self.assertIsNone(db.get_product_by_barcode("B1"))

        # The full comparison, on both devices, then one ordinary round each.
        for device, owner in (("a", owner_a), ("b", owner_b), ("a", owner_a), ("b", owner_b)):
            self._use(device)
            sync_service.reconcile_full(owner)
            sync_service.auto_sync_turn(owner)

        for device in ("a", "b"):
            self._use(device)
            for barcode in ("A1", "A2", "B1", "B2"):
                self.assertIsNotNone(
                    db.get_product_by_barcode(barcode),
                    f"{device} is still missing {barcode}",
                )

    def test_the_comparison_leaves_a_settled_pair_alone(self):
        """Once the two sides genuinely agree, the round has nothing left to do
        -- otherwise it would re-upload the whole account every time it ran."""
        owner = self._use("a")
        self._product("P1", "Lenovo")
        sync_service.reconcile_full(owner)
        sync_service.auto_sync_turn(owner)
        healed = sync_service.reconcile_full(owner)
        self.assertEqual(healed["queued"], 0)

    def test_rows_the_server_knows_are_not_queued_again(self):
        owner = self._use("a")
        product = self._product("P1", "Lenovo")
        sync_service.reconcile_full(owner)
        sync_service.auto_sync_turn(owner)
        known = {(table, str(local_id)) for table, local_id in self.server.rows}
        self.assertEqual(db.queue_rows_absent_from_server(known), 0)
        self.assertIn(("products", str(product)), known)

    def test_the_comparison_waits_for_the_one_time_settlement(self):
        owner = self._use("a")
        with patch.object(db, "is_upgrade_reconcile_required", return_value=True):
            healed = sync_service.reconcile_full(owner)
        self.assertTrue(healed.get("deferred"))
        self.assertEqual(healed["queued"], 0)


if __name__ == "__main__":
    unittest.main()
