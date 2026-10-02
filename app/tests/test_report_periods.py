"""Which period a sale and a cashier's money land in.

- A sale confirmed in a later month than it was sold counts on the day it was
  confirmed; one confirmed within its own month stays on the day it was sold.
- A cashier's salary is a running balance: a minus from expenses follows the
  cashier into later periods until rewards cover it, and a plus keeps growing.

Timestamps are midday UTC so the local date is the same in any time zone.
"""

import os
import tempfile
import unittest

import database as db
from reporting import cashier_balance
from reporting.sale_period import report_time


class ReportPeriodTest(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.old_path = db.DB_PATH
        db.DB_PATH = self.path
        db.init_db()
        self.categories = {row["name"]: row["id"] for row in db.get_expense_categories()}
        self.cashier_id = db.add_user(
            email="kassir@shop.uz", password="parol123", role="cashier", username="Aziz"
        )
        self.product_id = db.add_product(
            {"name": "Non", "barcode": "111", "price": 10000, "cost": 6000, "quantity": 0}
        )
        db.add_stock(self.product_id, 500, "init")

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
    def _sale(self, sold_on, confirmed_on, reward=0):
        sale_id = db.create_sale(
            None, self.cashier_id,
            [{"product_id": self.product_id, "quantity": 1, "price": 10000, "subtotal": 10000}],
            total=10000, discount=0, paid=10000, payment_method="naqd", is_finalized=0,
        )
        db.finalize_sale(sale_id, cashier_reward=reward)
        with db.session_scope() as session:
            sale = session.get(db.Sale, sale_id)
            sale.created_at = f"{sold_on} 12:00:00"
            sale.finalized_at = f"{confirmed_on} 12:00:00"
        return sale_id

    def _expense(self, on, amount):
        expense_id = db.add_expense(
            self.categories["Kassir"], amount, "UZS", "avans", None, self.cashier_id
        )
        with db.session_scope() as session:
            session.get(db.Expense, expense_id).created_at = f"{on} 12:00:00"

    def _revenue(self, start, end):
        return sum(row["revenue"] for row in db.get_overall_period_series(start, end))

    def _salary(self, start, end):
        rows = db.get_cashier_salary_period_summary(start, end)
        return next(row for row in rows if row["entity_id"] == self.cashier_id)

    # -- which day a sale counts on --------------------------------------
    def test_sale_confirmed_next_month_counts_on_the_confirmation_day(self):
        self._sale("2026-01-30", "2026-02-02", reward=1000)

        self.assertEqual(self._revenue("2026-01-01", "2026-01-31"), 0)
        self.assertEqual(self._revenue("2026-02-01", "2026-02-28"), 10000)
        labels = [row["label"] for row in db.get_overall_period_series("2026-02-01", "2026-02-28")]
        self.assertEqual(labels, ["2026-02-02"])
        self.assertEqual(self._salary("2026-02-01", "2026-02-28")["cashier_reward"], 1000)
        self.assertEqual(self._salary("2026-01-01", "2026-01-31")["cashier_reward"], 0)

    def test_sale_confirmed_in_its_own_month_stays_on_the_sale_day(self):
        self._sale("2026-01-10", "2026-01-25")

        labels = [row["label"] for row in db.get_overall_period_series("2026-01-01", "2026-01-31")]
        self.assertEqual(labels, ["2026-01-10"])

    def test_every_report_agrees_on_the_moved_day(self):
        self._sale("2026-01-30", "2026-02-02")

        self.assertEqual(db.get_daily_report("2026-02-02")["revenue"], 10000)
        self.assertIsNone(db.get_daily_report("2026-01-30")["revenue"])
        summary = db.get_cashier_period_summary("2026-02-01", "2026-02-28")
        self.assertEqual(sum(row["revenue"] for row in summary), 10000)
        details = db.get_cashier_sales_details(self.cashier_id, "2026-02-01", "2026-02-28")
        self.assertEqual(len(details), 1)
        series = db.get_entity_period_series("cashier", self.cashier_id, "2026-02-01", "2026-02-28")
        self.assertEqual([row["label"] for row in series], ["2026-02-02"])

    def test_python_and_sql_rules_match(self):
        self.assertEqual(report_time("2026-01-30 12:00:00", "2026-02-02 12:00:00"), "2026-02-02 12:00:00")
        self.assertEqual(report_time("2026-01-10 12:00:00", "2026-01-25 12:00:00"), "2026-01-10 12:00:00")
        self.assertEqual(report_time("2026-01-10 12:00:00", None), "2026-01-10 12:00:00")

    # -- the salary carries over ------------------------------------------
    def test_a_minus_follows_the_cashier_until_rewards_cover_it(self):
        self._sale("2026-01-05", "2026-01-05", reward=3000)
        self._expense("2026-01-20", 10000)

        # January: 3 000 earned, 10 000 taken.
        self.assertEqual(self._salary("2026-01-01", "2026-01-31")["total_salary"], -7000)
        # February starts 7 000 in minus; 4 000 earned does not cover it.
        self._sale("2026-02-10", "2026-02-10", reward=4000)
        february = self._salary("2026-02-01", "2026-02-28")
        self.assertEqual(february["opening_balance"], -7000)
        self.assertEqual(february["total_salary"], -3000)
        # March: the rest is covered and the cashier is back in plus.
        self._sale("2026-03-03", "2026-03-03", reward=5000)
        self.assertEqual(self._salary("2026-03-01", "2026-03-31")["total_salary"], 2000)

    def test_without_expenses_the_balance_keeps_growing(self):
        self._sale("2026-01-05", "2026-01-05", reward=3000)
        self._sale("2026-02-05", "2026-02-05", reward=4000)

        self.assertEqual(self._salary("2026-02-01", "2026-02-28")["total_salary"], 7000)
        self.assertEqual(cashier_balance.get_opening_balance("2026-03-01", self.cashier_id), 7000)

    def test_a_paid_out_salary_brings_the_balance_to_zero(self):
        self._sale("2026-01-05", "2026-01-05", reward=6000)
        self._expense("2026-01-31", 6000)

        self.assertEqual(self._salary("2026-02-01", "2026-02-28")["total_salary"], 0)

    def test_a_section_report_has_no_carried_balance(self):
        self._sale("2026-01-05", "2026-01-05", reward=3000)
        section_id = db.add_product_section("Ichimlik")
        rows = db.get_cashier_salary_period_summary("2026-02-01", "2026-02-28", section_id)
        row = next(row for row in rows if row["entity_id"] == self.cashier_id)
        self.assertEqual(row["total_salary"], 0)

    def test_the_details_page_shows_the_carried_balance(self):
        from PyQt6.QtWidgets import QApplication
        from ui.reports_widget import SalesDetailsWidget

        self._sale("2026-01-05", "2026-01-05", reward=3000)
        self._expense("2026-01-20", 10000)
        self._sale("2026-02-10", "2026-02-10", reward=4000)

        self.app = QApplication.instance() or QApplication([])
        widget = SalesDetailsWidget({"id": 1, "role": "admin", "email": "a@b.uz", "username": "a"})
        self.addCleanup(widget.deleteLater)
        widget._date_range = lambda: ("2026-02-01", "2026-02-28")
        widget.load_data()
        # -7 000 carried in + 4 000 earned.
        self.assertIn("-3,000", widget.summary_cards["salary"].text())


if __name__ == "__main__":
    unittest.main()
