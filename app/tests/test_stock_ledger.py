"""Stock must always equal the sum of its movements.

Every figure the finance report derives - inventory value, profit, what a
stocktake says is missing - is only meaningful if the ledger balances. These
tests pin that invariant against every path that moves stock.
"""

import os
import tempfile
import unittest

from sqlalchemy import func, select, text

import database as db


class StockLedgerTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.old_path = db.DB_PATH
        db.DB_PATH = self.path
        db.init_db()
        self.cashier_id = db.add_user(
            email="kassir@shop.uz", password="parol123", role="cashier", username="Kassir"
        )

    def tearDown(self):
        try:
            db._get_engine().dispose()
        finally:
            db.DB_PATH = self.old_path
            for suffix in ("", "-shm", "-wal"):
                path = self.path + suffix
                if os.path.exists(path):
                    os.remove(path)

    # -- helpers ---------------------------------------------------------
    def _product(self, barcode="p1", stock=50, price=1000, cost=600):
        return db.add_product({
            "name": f"Mahsulot {barcode}", "barcode": barcode,
            "price": price, "cost": cost, "quantity": 0, "stock": stock,
        })

    def _stock_and_ledger(self, product_id):
        with db.session_scope() as session:
            stock = int((session.get(db.Product, product_id).stock or 0))
            ledger = int(session.scalar(
                select(func.coalesce(func.sum(db.StockMovement.quantity), 0))
                .where(db.StockMovement.product_id == product_id)
            ) or 0)
        return stock, ledger

    def _assert_balanced(self, product_id, msg=""):
        stock, ledger = self._stock_and_ledger(product_id)
        self.assertEqual(
            stock, ledger,
            f"{msg}: qoldiq {stock}, jurnal {ledger} - farq {stock - ledger}",
        )
        return stock

    def _sell(self, product_id, quantity, price=1000, finalize=None):
        sale_id = db.create_sale(
            None, self.cashier_id,
            [{"product_id": product_id, "quantity": quantity,
              "price": price, "subtotal": quantity * price}],
            total=quantity * price, discount=0, paid=quantity * price,
            payment_method="naqd", is_finalized=0,
        )
        if finalize is not None:
            db.finalize_sale(sale_id, cashier_reward=finalize)
        return sale_id

    # -- tests -----------------------------------------------------------
    def test_a_new_product_records_its_opening_balance(self):
        product_id = self._product(stock=50)
        self.assertEqual(self._assert_balanced(product_id, "yangi mahsulot"), 50)

    def test_a_product_created_empty_has_no_phantom_movement(self):
        product_id = self._product(barcode="p0", stock=0)
        self.assertEqual(self._assert_balanced(product_id, "bo'sh mahsulot"), 0)

    def test_receiving_stock_stays_balanced(self):
        product_id = self._product(stock=50)
        db.add_stock(product_id, 20, "kirim")
        self.assertEqual(self._assert_balanced(product_id, "kirimdan keyin"), 70)

    def test_editing_the_stock_by_hand_is_recorded_as_a_correction(self):
        product_id = self._product(stock=50)
        db.update_product(product_id, {"name": "Mahsulot p1", "barcode": "p1",
                                       "price": 1000, "cost": 600, "stock": 80})
        self.assertEqual(self._assert_balanced(product_id, "qo'lda oshirildi"), 80)

        db.update_product(product_id, {"name": "Mahsulot p1", "barcode": "p1",
                                       "price": 1000, "cost": 600, "stock": 65})
        self.assertEqual(self._assert_balanced(product_id, "qo'lda kamaytirildi"), 65)

        with db.session_scope() as session:
            # Read the values inside the session: the ORM objects detach once
            # it closes.
            corrections = list(session.scalars(
                select(db.StockMovement.quantity).where(
                    db.StockMovement.product_id == product_id,
                    db.StockMovement.type == "korrektirovka",
                # Identifiers are UUIDs, so they say nothing about order.
                # The journal is read in the order it was written.
                ).order_by(text("rowid"))
            ).all())
        self.assertEqual(corrections, [30, -15])

    def test_editing_other_fields_writes_no_movement(self):
        product_id = self._product(stock=50)
        db.update_product(product_id, {"name": "Yangi nom", "barcode": "p1",
                                       "price": 2000, "cost": 600, "stock": 50})
        self._assert_balanced(product_id, "faqat nom o'zgardi")
        with db.session_scope() as session:
            count = session.scalar(
                select(func.count(db.StockMovement.id))
                .where(db.StockMovement.product_id == product_id)
            )
        self.assertEqual(count, 1, "faqat boshlang'ich qoldiq bo'lishi kerak")

    def test_selling_stays_balanced(self):
        product_id = self._product(stock=50)
        self._sell(product_id, 10)
        self.assertEqual(self._assert_balanced(product_id, "sotuvdan keyin"), 40)

    def test_deleting_a_product_with_unfinalized_sales_gives_the_stock_back(self):
        """The regression this suite exists for.

        create_sale takes the quantity out of stock; deleting the product wiped
        the sale_items without putting it back, so the shop permanently lost
        inventory it still physically had - and the movement log kept a
        negative row whose sale no longer existed.
        """
        product_id = self._product(stock=50)
        self._sell(product_id, 10)
        before = self._assert_balanced(product_id, "o'chirishdan oldin")
        self.assertEqual(before, 40)

        db.delete_product(product_id)

        after = self._assert_balanced(product_id, "o'chirishdan keyin")
        self.assertEqual(after, 50, "sotilgan 10 ta qoldiqqa qaytishi kerak")

    def test_returning_an_item_stays_balanced(self):
        product_id = self._product(stock=30)
        self._sell(product_id, 5, finalize=500)
        import datetime
        today = datetime.date.today().isoformat()
        rows = db.get_cashier_sales_details(self.cashier_id, today, today, None, only_cashiers=True)
        item = next(row for row in rows if row["product_id"] == product_id)

        db.return_sale_item(item["sale_item_id"], 2, "qaytardi")
        self.assertEqual(self._assert_balanced(product_id, "qaytarishdan keyin"), 27)

    def test_a_stocktake_does_not_move_stock_unless_asked(self):
        product_id = self._product(stock=30)
        session_id = db.start_inventory_check(self.cashier_id)
        before = self._assert_balanced(product_id, "tekshiruvdan oldin")

        db.finish_inventory_check(session_id)

        self.assertEqual(
            self._assert_balanced(product_id, "tekshiruvdan keyin"), before,
            "tekshiruv o'zi qoldiqni o'zgartirmasligi kerak",
        )

    def test_a_stocktake_correction_goes_through_the_ledger(self):
        product_id = self._product(stock=30)
        session_id = db.start_inventory_check(self.cashier_id)
        with db.session_scope() as session:
            item = session.scalar(
                select(db.InventoryCheckItem).where(
                    db.InventoryCheckItem.session_id == session_id,
                    db.InventoryCheckItem.product_id == product_id,
                )
            )
            item.checked_quantity = 28   # ikkitasi yetishmayapti
            item.checked_at = db._now()

        discrepancies = db.get_inventory_check_discrepancies(session_id)
        self.assertEqual([(row["expected_stock"], row["counted_quantity"], row["delta"])
                          for row in discrepancies], [(30, 28, -2)])

        result = db.finish_inventory_check(session_id, apply_corrections=True)

        self.assertEqual(result["corrected_count"], 1)
        self.assertEqual(self._assert_balanced(product_id, "tuzatishdan keyin"), 28)

    def test_an_uncounted_product_is_never_written_off(self):
        """An untouched row means "not counted", not "counted as zero"."""
        product_id = self._product(stock=30)
        session_id = db.start_inventory_check(self.cashier_id)

        self.assertEqual(db.get_inventory_check_discrepancies(session_id), [])
        db.finish_inventory_check(session_id, apply_corrections=True)

        self.assertEqual(self._assert_balanced(product_id, "sanalmagan mahsulot"), 30)

    def test_a_full_day_of_activity_leaves_the_ledger_balanced(self):
        import datetime
        today = datetime.date.today().isoformat()
        products = [self._product(barcode=f"m{i}", stock=100) for i in range(4)]

        db.add_stock(products[0], 25, "kirim")
        db.update_product(products[1], {"name": "Mahsulot m1", "barcode": "m1",
                                        "price": 1000, "cost": 600, "stock": 120})
        self._sell(products[0], 7)
        self._sell(products[1], 3, finalize=300)
        self._sell(products[2], 12)
        db.delete_product(products[2])

        rows = db.get_cashier_sales_details(self.cashier_id, today, today, None, only_cashiers=True)
        returnable = next(row for row in rows if row["product_id"] == products[1])
        db.return_sale_item(returnable["sale_item_id"], 1, "qaytardi")

        for product_id in products:
            self._assert_balanced(product_id, f"mahsulot {product_id}")


if __name__ == "__main__":
    unittest.main()
