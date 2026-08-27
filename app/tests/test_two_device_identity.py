"""Two devices of one account must never write over each other.

This is the guarantee the whole identity change exists for. Each test here
runs two real databases side by side, moves records between them the way the
sync does, and checks that nothing is lost, nothing is resurrected, and the
money adds up to the same figure on both.
"""

import os
import shutil
import tempfile
import unittest

import database as db


class TwoDeviceIdentityTest(unittest.TestCase):
    ACCOUNT_UID = "account-shared"
    OWNER_EMAIL = "owner@example.com"

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="marketstore-two-device-")
        self.old_path = db.DB_PATH
        self.old_uid = db._ACTIVE_ACCOUNT_UID

    def tearDown(self):
        if db._ENGINE is not None:
            db._ENGINE.dispose()
        db._ENGINE = None
        db._ENGINE_PATH = None
        db._SessionLocal = None
        db.DB_PATH = self.old_path
        db._ACTIVE_ACCOUNT_UID = self.old_uid
        shutil.rmtree(self.root, ignore_errors=True)

    # -- device switching ------------------------------------------------
    def _use(self, device):
        """Point the whole module at one device's database."""
        if db._ENGINE is not None:
            db._ENGINE.dispose()
        db._ENGINE = None
        db._ENGINE_PATH = None
        db._SessionLocal = None
        db.activate_account_database(
            self.ACCOUNT_UID,
            email=self.OWNER_EMAIL,
            storage_root=os.path.join(self.root, device),
        )
        db.init_db(
            account_owner={
                "user_uid": self.ACCOUNT_UID,
                "email": self.OWNER_EMAIL,
                "display_name": "Owner",
            },
            seed_defaults=False,
        )

    def _product(self, barcode, stock=2000):
        return db.add_product({
            "barcode": barcode, "name": f"Mahsulot {barcode}", "price": 1000,
            "cost": 600, "stock": stock, "unit": "dona",
        })

    def _sell(self, cashier_id, product_id, quantity=1, finalize=True):
        sale_id = db.create_sale(
            None, cashier_id,
            [{"product_id": product_id, "quantity": quantity, "price": 1000,
              "subtotal": 1000 * quantity}],
            1000 * quantity, 0, 1000 * quantity, "naqd",
        )
        if finalize:
            db.finalize_sale(sale_id, cashier_reward=100)
        return sale_id

    @staticmethod
    def _records(tables=None):
        records = db.export_sync_records()
        if tables is None:
            return records
        return [record for record in records if record["table_name"] in tables]

    # -- the core guarantee ----------------------------------------------
    def test_three_hundred_sales_on_each_device_stay_three_hundred_each(self):
        """The old scheme lost one of every pair that shared an integer."""
        per_device = 300

        self._use("a")
        cashier_a = db.add_user("a@example.com", role="cashier", username="Kassir A")
        product_a = self._product("A-1")
        for _ in range(per_device):
            self._sell(cashier_a, product_a, finalize=False)
        a_records = self._records()
        a_sale_ids = {r["local_id"] for r in a_records if r["table_name"] == "sales"}

        self._use("b")
        cashier_b = db.add_user("b@example.com", role="cashier", username="Kassir B")
        product_b = self._product("B-1")
        for _ in range(per_device):
            self._sell(cashier_b, product_b, finalize=False)
        b_sale_ids = {r["local_id"] for r in self._records() if r["table_name"] == "sales"}

        self.assertEqual(len(a_sale_ids), per_device)
        self.assertEqual(len(b_sale_ids), per_device)
        # Not one identifier in common: this is what used to fail.
        self.assertEqual(a_sale_ids & b_sale_ids, set())

        # Device B downloads what A wrote; both sets must survive together.
        db.import_sync_records(a_records)
        merged = {r["local_id"] for r in self._records() if r["table_name"] == "sales"}
        self.assertEqual(len(merged), per_device * 2)
        self.assertEqual(merged, a_sale_ids | b_sale_ids)

    def test_a_deleted_sale_is_not_resurrected_by_later_writes(self):
        self._use("a")
        cashier = db.add_user("a@example.com", role="cashier", username="Kassir")
        product = self._product("A-1")
        doomed = self._sell(cashier, product, finalize=False)

        details = db.get_product_sales_archive("")
        item_id = next(row["sale_item_id"] for row in details if row["sale_id"] == doomed)
        db.delete_sale_item(item_id)

        reborn = {self._sell(cashier, product, finalize=False) for _ in range(50)}
        self.assertNotIn(doomed, reborn)

        # And the deletion travels as a tombstone rather than as a gap.
        tombstones = [
            record for record in db.export_sync_records()
            if record["table_name"] == "sale_items" and record.get("deleted_at")
        ]
        self.assertTrue(any(record["local_id"] == item_id for record in tombstones))

    def test_the_same_cashier_earns_the_same_salary_on_both_devices(self):
        """The report used to disagree because cashier_id meant two people."""
        period = ("2000-01-01", "2100-01-01")

        self._use("a")
        cashier = db.add_user("kassir@example.com", role="cashier", username="Sardor")
        product = self._product("A-1")
        for _ in range(5):
            self._sell(cashier, product)
        a_records = self._records()
        a_summary = {
            row["entity_name"]: (row["sales_count"], row["revenue"], row["cashier_reward"])
            for row in db.get_cashier_salary_period_summary(*period)
        }

        self._use("b")
        db.import_sync_records(a_records)
        b_summary = {
            row["entity_name"]: (row["sales_count"], row["revenue"], row["cashier_reward"])
            for row in db.get_cashier_salary_period_summary(*period)
        }

        self.assertEqual(a_summary, b_summary)
        self.assertEqual(a_summary["Sardor"][0], 5)

    def test_the_owner_is_one_person_on_both_devices(self):
        self._use("a")
        owner_a = next(u["id"] for u in db.get_users() if u["email"] == self.OWNER_EMAIL)
        self._use("b")
        owner_b = next(u["id"] for u in db.get_users() if u["email"] == self.OWNER_EMAIL)
        self.assertEqual(owner_a, owner_b)

if __name__ == "__main__":
    unittest.main()
