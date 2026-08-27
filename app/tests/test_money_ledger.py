"""Every money figure must be derivable from rows that are never rewritten.

Before this, a return overwrote the sale's total, discount and paid amount in
place. The sale forgot what it had been, so the same return applied twice was
indistinguishable from two real ones, and profit read the product's current
cost -- which meant editing a cost rewrote the profit of every past month.
"""

import os
import shutil
import tempfile
import unittest

from sqlalchemy import func, select

import database as db


class MoneyLedgerTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="marketstore-ledger-")
        self.old_path = db.DB_PATH
        self.old_uid = db._ACTIVE_ACCOUNT_UID
        db.activate_account_database("acct-ledger", email="owner@example.com", storage_root=self.root)
        db.init_db(
            account_owner={"user_uid": "acct-ledger", "email": "owner@example.com", "display_name": "Owner"},
            seed_defaults=False,
        )
        db.sync_online_user("owner@example.com", role="admin", user_uid="acct-ledger")
        self.cashier = db.add_user("kassir@example.com", role="cashier", username="Kassir")

    def tearDown(self):
        if db._ENGINE is not None:
            db._ENGINE.dispose()
        db._ENGINE = None
        db._ENGINE_PATH = None
        db._SessionLocal = None
        db.DB_PATH = self.old_path
        db._ACTIVE_ACCOUNT_UID = self.old_uid
        shutil.rmtree(self.root, ignore_errors=True)

    # -- helpers ---------------------------------------------------------
    def _product(self, barcode="P1", cost=600, price=1000, stock=100):
        return db.add_product({
            "barcode": barcode, "name": f"Mahsulot {barcode}", "price": price,
            "cost": cost, "stock": stock, "unit": "dona",
        })

    def _sale(self, product_id, quantity=4, price=1000, discount=0, customer_id=None,
              payment_method="naqd", finalize_reward=None):
        total = price * quantity
        paid = total - discount
        sale_id = db.create_sale(
            customer_id, self.cashier,
            [{"product_id": product_id, "quantity": quantity, "price": price,
              "subtotal": total}],
            total, discount, paid, payment_method,
        )
        if finalize_reward is not None:
            db.finalize_sale(sale_id, cashier_reward=finalize_reward)
        return sale_id

    @staticmethod
    def _items(sale_id):
        with db.session_scope() as session:
            return [
                dict(id=item.id, quantity=item.quantity, returned=item.returned_quantity or 0)
                for item in session.scalars(
                    select(db.SaleItem).where(db.SaleItem.sale_id == sale_id)
                )
            ]

    @staticmethod
    def _sale_row(sale_id):
        with db.session_scope() as session:
            sale = session.get(db.Sale, sale_id)
            return dict(
                total=sale.total, discount=sale.discount, paid=sale.paid,
                reward=sale.cashier_reward,
                original_total=sale.original_total,
                original_discount=sale.original_discount,
                original_reward=sale.original_cashier_reward,
            )

    # -- the sealed original ---------------------------------------------
    def test_a_return_never_touches_the_sealed_amounts(self):
        product = self._product()
        sale_id = self._sale(product, quantity=4, discount=400)
        item_id = self._items(sale_id)[0]["id"]

        db.return_sale_item(item_id, 1)

        row = self._sale_row(sale_id)
        self.assertEqual(row["original_total"], 4000)
        self.assertEqual(row["original_discount"], 400)
        # The live figures moved; the sealed ones did not.
        self.assertEqual(row["total"], 3000)
        self.assertAlmostEqual(row["discount"], 300)

    def test_returning_everything_gives_back_the_whole_discount_and_reward(self):
        product = self._product()
        sale_id = self._sale(product, quantity=4, discount=400, finalize_reward=200)
        item_id = self._items(sale_id)[0]["id"]

        for _ in range(4):
            db.return_sale_item(item_id, 1)

        row = self._sale_row(sale_id)
        self.assertEqual(row["total"], 0)
        self.assertEqual(row["discount"], 0)
        self.assertAlmostEqual(row["reward"], 0.0)
        self.assertEqual(row["original_reward"], 200)

    def test_a_replayed_return_row_changes_nothing(self):
        """A download can hand us the same return twice; it must not count twice."""
        product = self._product()
        sale_id = self._sale(product, quantity=4)
        item_id = self._items(sale_id)[0]["id"]
        db.return_sale_item(item_id, 2)

        before = self._sale_row(sale_id)
        record = [r for r in db.export_sync_records() if r["table_name"] == "sale_returns"]
        self.assertEqual(len(record), 1)

        db.import_sync_records(record)
        db.import_sync_records(record)
        after = db.recalculate_sale_totals(sale_id)

        self.assertEqual(after["total"], before["total"])
        self.assertEqual(self._items(sale_id)[0]["returned"], 2)

    def test_returned_quantity_is_the_sum_of_the_return_rows(self):
        product = self._product()
        sale_id = self._sale(product, quantity=5)
        item_id = self._items(sale_id)[0]["id"]

        db.return_sale_item(item_id, 1)
        db.return_sale_item(item_id, 2)

        rows = db.get_sale_returns(sale_id)
        self.assertEqual(sum(row["quantity"] for row in rows), 3)
        self.assertEqual(self._items(sale_id)[0]["returned"], 3)

    def test_returning_more_than_was_sold_is_refused(self):
        product = self._product()
        sale_id = self._sale(product, quantity=2)
        item_id = self._items(sale_id)[0]["id"]
        db.return_sale_item(item_id, 2)

        with self.assertRaises(db.AppError):
            db.return_sale_item(item_id, 1)

    # -- cost sealed at sale time ----------------------------------------
    def test_changing_a_cost_does_not_rewrite_last_month_profit(self):
        product = self._product(cost=600)
        sale_id = self._sale(product, quantity=3)
        before = db.get_sale_cost(sale_id)

        db.update_product(product, {"name": "Mahsulot P1", "barcode": "P1",
                                    "price": 1000, "cost": 900})

        self.assertEqual(before, 1800)
        self.assertEqual(db.get_sale_cost(sale_id), 1800)

    # -- customer debt ----------------------------------------------------
    def test_the_customer_balance_equals_its_ledger(self):
        customer_id = db.add_customer("Mijoz", "900000000", None)
        product = self._product(stock=50)
        sale_id = self._sale(product, quantity=4, customer_id=customer_id, payment_method="qarz")
        item_id = self._items(sale_id)[0]["id"]
        db.return_sale_item(item_id, 1)

        movements = db.get_customer_debt_movements(customer_id)
        ledger_total = sum(row["amount"] for row in movements)
        with db.session_scope() as session:
            balance = session.get(db.Customer, customer_id).balance

        self.assertEqual(len(movements), 2)
        self.assertAlmostEqual(balance, ledger_total)
        self.assertAlmostEqual(balance, 3000)

    # -- deletion ---------------------------------------------------------
    def test_deleting_a_line_keeps_the_returns_already_made(self):
        product = self._product(stock=50)
        first = self._product("P2", stock=50)
        sale_id = db.create_sale(
            None, self.cashier,
            [{"product_id": product, "quantity": 2, "price": 1000, "subtotal": 2000},
             {"product_id": first, "quantity": 1, "price": 1000, "subtotal": 1000}],
            3000, 0, 3000, "naqd",
        )
        items = self._items(sale_id)
        db.return_sale_item(items[0]["id"], 1)

        db.delete_sale_item(items[0]["id"])

        rows = db.get_sale_returns(sale_id)
        self.assertTrue(rows, "the returns must survive the line they belonged to")
        with db.session_scope() as session:
            total = session.scalar(
                select(func.coalesce(func.sum(db.SaleReturn.quantity), 0))
                .where(db.SaleReturn.sale_id == sale_id)
            )
        self.assertEqual(int(total), 2)

    # -- debtors and suppliers -------------------------------------------
    def test_a_debtor_balance_equals_its_movements(self):
        debtor_id = db.add_debtor("Qarzdor", "900000001", note=None)
        db.add_debtor_debt(debtor_id, 5000, note="Qarz berildi")
        db.pay_debtor_debt(debtor_id, 2000, note="Qisman to'lov")

        movements = db.get_debtor_debt_movements(debtor_id)
        ledger = sum(
            row["amount"] if row["type"] == "qarz" else -row["amount"] for row in movements
        )
        with db.session_scope() as session:
            row = session.get(db.Debtor, debtor_id)
            balance, given = row.balance, row.total_given

        self.assertAlmostEqual(balance, ledger)
        self.assertAlmostEqual(balance, 3000)
        self.assertAlmostEqual(given, 5000)

    def test_a_supplier_balance_equals_its_movements(self):
        supplier_id = db.add_supplier("Ta'minotchi", "900000002", note=None)
        db.add_supplier_debt(supplier_id, 8000, note="Tovar olindi")
        db.pay_supplier_debt(supplier_id, 3000, note="To'lov")

        movements = db.get_supplier_debt_movements(supplier_id)
        ledger = sum(
            row["amount"] if row["type"] == "qarz" else -row["amount"] for row in movements
        )
        with db.session_scope() as session:
            row = session.get(db.Supplier, supplier_id)
            balance, received = row.balance, row.total_received

        self.assertAlmostEqual(balance, ledger)
        self.assertAlmostEqual(balance, 5000)
        self.assertAlmostEqual(received, 8000)


if __name__ == "__main__":
    unittest.main()
