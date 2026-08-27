"""Work done on one device shows up on the others, with nobody pressing anything.

This is the whole point of the arrangement, so it is tested the way it is
lived: two real databases for one account, a stand-in server between them, and
no button anywhere. A sale, an expense, a debt and a price change all have to
make the crossing on their own.
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
    """Keeps records the way the real one does: by table and row, with a version."""

    def __init__(self):
        self.rows = {}
        self.generation = 0

    def push(self, _token, records, **_kwargs):
        next_generation = self.generation + 1
        saved = 0
        for record in records:
            key = (record["table_name"], str(record["local_id"]))
            existing = self.rows.get(key)
            if existing and record.get("expected_version") is not None:
                if existing["sync_version"] != record["expected_version"]:
                    continue
            stored = dict(record)
            stored["sync_version"] = (existing["sync_version"] + 1) if existing else 1
            stored["stored_at"] = f"t{next_generation}"
            stored["change_seq"] = next_generation
            self.rows[key] = stored
            saved += 1
        self.generation = next_generation
        return {"saved": saved, "batch_id": "b", "generation": self.generation, "rejected": []}

    def pull(self, _token, since=None, since_seq=None, table_name=None, include_deleted=True, timeout=30):
        # The real server hands back only what moved after `since`, and the
        # client leans on that, so the stand-in has to behave the same way.
        records = []
        for key, row in self.rows.items():
            if table_name is not None and key[0] != table_name:
                continue
            if since is not None and row["stored_at"] <= since:
                continue
            if since_seq is not None and row["change_seq"] <= since_seq:
                continue
            records.append({k: v for k, v in row.items() if k != "stored_at"})
        return {"records": records, "server_time": f"t{self.generation}",
                "generation": self.generation,
                "cursor": max((row["change_seq"] for row in records), default=0),
                "cursor_supported": True}

    def state(self, *_args, **_kwargs):
        return {"generation": self.generation, "purge_generation": 0}


class LiveUpdatesTest(unittest.TestCase):
    ACCOUNT = "acct-live"
    EMAIL = "owner@example.com"

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="marketstore-live-")
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

    # -- switching between two real devices --------------------------------
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
    def _settle(owner):
        """One round of what the engine does on its own."""
        return sync_service.auto_sync_turn(owner)

    # -- a sale -------------------------------------------------------------
    def test_a_sale_on_one_device_reaches_the_other(self):
        owner = self._use("a")
        cashier = db.add_user("sardor@example.com", role="cashier", username="Sardor")
        product = db.add_product({"barcode": "P1", "name": "Lenovo", "price": 1000,
                                  "cost": 600, "stock": 10, "unit": "dona"})
        sale = db.create_sale(None, cashier, [{"product_id": product, "quantity": 3,
                                               "price": 1000, "subtotal": 3000}],
                              3000, 0, 3000, "naqd")
        db.finalize_sale(sale, cashier_reward=100)
        self._settle(owner)

        other = self._use("b")
        self._settle(other)

        arrived = db.get_product_by_barcode("P1")
        self.assertIsNotNone(arrived)
        self.assertEqual(arrived["stock"], 7, "the stock the sale left behind")
        rows = db.get_product_sales_archive("")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cashier_name"], "Sardor")

    # -- an expense ---------------------------------------------------------
    def test_an_expense_reaches_the_other_device(self):
        owner = self._use("a")
        cashier = db.add_user("sardor@example.com", role="cashier", username="Sardor")
        category = db.add_expense_category("Ijara")
        db.add_expense(category, 250000, "UZS", "Avgust ijara", user_id=cashier)
        self._settle(owner)

        other = self._use("b")
        self._settle(other)

        expenses = db.get_expenses()
        self.assertEqual(len(expenses), 1)
        self.assertEqual(expenses[0]["amount"], 250000)

    # -- a debt -------------------------------------------------------------
    def test_a_debt_and_its_repayment_reach_the_other_device(self):
        owner = self._use("a")
        debtor = db.add_debtor("Qarzdor", "900000000", note=None)
        db.add_debtor_debt(debtor, 500000, note="Qarz berildi")
        db.pay_debtor_debt(debtor, 200000, note="Qisman to'lov")
        self._settle(owner)

        other = self._use("b")
        self._settle(other)

        arrived = [d for d in db.get_all_debtors() if d["name"] == "Qarzdor"]
        self.assertEqual(len(arrived), 1)
        self.assertAlmostEqual(arrived[0]["balance"], 300000)
        self.assertEqual(len(db.get_debtor_debt_movements(arrived[0]["id"])), 2)

    # -- both directions, in turn -------------------------------------------
    def test_each_device_sees_what_the_other_did(self):
        owner = self._use("a")
        db.add_product({"barcode": "FROM-A", "name": "A dan", "price": 1,
                        "cost": 1, "stock": 1, "unit": "dona"})
        self._settle(owner)

        other = self._use("b")
        self._settle(other)
        db.add_product({"barcode": "FROM-B", "name": "B dan", "price": 1,
                        "cost": 1, "stock": 1, "unit": "dona"})
        self._settle(other)

        back = self._use("a")
        self._settle(back)

        self.assertIsNotNone(db.get_product_by_barcode("FROM-A"))
        self.assertIsNotNone(db.get_product_by_barcode("FROM-B"))

    def test_an_offline_device_catches_every_missed_change_when_it_returns(self):
        owner_a = self._use("a")
        db.add_product({"barcode": "BEFORE", "name": "Avval", "price": 1,
                        "cost": 1, "stock": 1, "unit": "dona"})
        self._settle(owner_a)

        owner_b = self._use("b")
        self._settle(owner_b)
        self.assertIsNotNone(db.get_product_by_barcode("BEFORE"))

        # Device B is now offline. Several independent generations are written
        # while it has no event stream and runs no sync turns.
        self._use("a")
        for barcode in ("MISSED-1", "MISSED-2", "MISSED-3"):
            db.add_product({"barcode": barcode, "name": barcode, "price": 1,
                            "cost": 1, "stock": 1, "unit": "dona"})
            self._settle(owner_a)

        # Reconnect/catch-up is one incremental turn. The durable cursor, not
        # the number of live notifications B happened to hear, decides what it
        # downloads.
        self._use("b")
        self._settle(owner_b)
        for barcode in ("MISSED-1", "MISSED-2", "MISSED-3"):
            self.assertIsNotNone(db.get_product_by_barcode(barcode))

    # -- a deletion travels too ---------------------------------------------
    def test_a_deletion_does_not_come_back_from_the_other_device(self):
        owner = self._use("a")
        product = db.add_product({"barcode": "GONE", "name": "O'chiriladi", "price": 1,
                                  "cost": 1, "stock": 1, "unit": "dona"})
        self._settle(owner)
        other = self._use("b")
        self._settle(other)
        self.assertIsNotNone(db.get_product_by_barcode("GONE"))

        back = self._use("a")
        db.delete_product(product)
        self._settle(back)

        again = self._use("b")
        self._settle(again)
        arrived = db.get_product_by_barcode("GONE")
        self.assertTrue(arrived is None or arrived.get("is_deleted"))

    def test_an_unsent_change_is_not_overwritten_by_an_older_copy(self):
        """A download must never quietly undo what the person just did."""
        owner = self._use("a")
        product = db.add_product({"barcode": "KEEP", "name": "Asl nom", "price": 1,
                                  "cost": 1, "stock": 1, "unit": "dona"})
        self._settle(owner)

        db.update_product(product, {"name": "Yangi nom", "barcode": "KEEP",
                                    "price": 2, "cost": 1})
        # A download arrives before the change has been sent.
        sync_service.pull_server_changes(owner, incremental=False)

        self.assertEqual(db.get_product_by_barcode("KEEP")["name"], "Yangi nom")
        self.assertEqual(db.get_last_pull_stats()["kept_local"], 1)

        self._settle(owner)
        other = self._use("b")
        self._settle(other)
        self.assertEqual(db.get_product_by_barcode("KEEP")["name"], "Yangi nom")

    # -- nothing has to be pressed -------------------------------------------
    def test_writing_anything_announces_itself_at_once(self):
        """What makes the engine act without waiting to be asked."""
        owner = self._use("a")
        woken = []
        db.add_change_listener(lambda: woken.append(1))
        try:
            db.add_product({"barcode": "WAKE", "name": "Uyg'otadi", "price": 1,
                            "cost": 1, "stock": 1, "unit": "dona"})
            self.assertTrue(woken, "a write must wake the sync straight away")
            woken.clear()
            cashier = db.add_user("k@example.com", role="cashier", username="K")
            product = db.get_product_by_barcode("WAKE")
            db.create_sale(None, cashier, [{"product_id": product["id"], "quantity": 1,
                                            "price": 1, "subtotal": 1}], 1, 0, 1, "naqd")
            self.assertTrue(woken, "a sale must wake it too")
        finally:
            db._CHANGE_LISTENERS.clear()
        self.assertIsNotNone(owner)


if __name__ == "__main__":
    unittest.main()
