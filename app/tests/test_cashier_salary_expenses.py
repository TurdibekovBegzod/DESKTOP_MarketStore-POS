"""Expenses filed under the "Kassir" category come out of that cashier's salary."""

import datetime
import os
import tempfile
import unittest

import database as db


class CashierSalaryExpenseTest(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.old_path = db.DB_PATH
        db.DB_PATH = self.path
        db.init_db()
        self.today = datetime.date.today().isoformat()
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
    def _sell(self, quantity, reward):
        sale_id = db.create_sale(
            None, self.cashier_id,
            [{"product_id": self.product_id, "quantity": quantity,
              "price": 10000, "subtotal": quantity * 10000}],
            total=quantity * 10000, discount=0, paid=quantity * 10000,
            payment_method="naqd", is_finalized=0,
        )
        db.finalize_sale(sale_id, cashier_reward=reward)
        return sale_id

    def _salary_row(self):
        rows = db.get_cashier_salary_period_summary(self.today, self.today)
        return next(row for row in rows if row["entity_id"] == self.cashier_id)

    # -- tests -----------------------------------------------------------
    def test_cashier_category_always_exists(self):
        self.assertTrue(
            any(db.is_cashier_expense_category_name(name) for name in self.categories)
        )

    def test_cashier_category_is_added_to_an_older_database(self):
        db.delete_expense_category(self.categories["Kassir"])
        self.assertFalse(
            any(db.is_cashier_expense_category_name(row["name"])
                for row in db.get_expense_categories())
        )
        db.init_db(seed_defaults=False)
        self.assertTrue(
            any(db.is_cashier_expense_category_name(row["name"])
                for row in db.get_expense_categories())
        )

    def test_expense_is_deducted_once_and_later_sales_keep_earning(self):
        self._sell(10, 5000)
        self._sell(20, 10000)
        self.assertEqual(self._salary_row()["total_salary"], 15000)

        db.add_expense(self.categories["Kassir"], 6000, "UZS", "avans", None, self.cashier_id)
        row = self._salary_row()
        self.assertEqual(row["salary_deduction"], 6000)
        self.assertEqual(row["total_salary"], 9000)

        # Reading the report again must not deduct a second time.
        self.assertEqual(self._salary_row()["total_salary"], 9000)

        self._sell(10, 5000)
        self.assertEqual(self._salary_row()["total_salary"], 14000)

    def test_expense_without_a_cashier_never_touches_a_salary(self):
        self._sell(10, 5000)
        db.add_expense(self.categories["Transport"], 7000, "UZS", "benzin", None, None)
        row = self._salary_row()
        self.assertEqual(row["salary_deduction"], 0)
        self.assertEqual(row["total_salary"], 5000)

    def test_foreign_currency_expense_is_converted_to_uzs(self):
        self._sell(100, 200000)
        db.add_expense(self.categories["Kassir"], 10, "USD", "avans", None, self.cashier_id)
        rate = next(c["rate_to_uzs"] for c in db.get_currencies() if c["code"] == "USD")
        self.assertAlmostEqual(self._salary_row()["salary_deduction"], 10 * rate, places=2)

    def test_deduction_may_exceed_earnings_and_stays_negative(self):
        self._sell(10, 1000)
        db.add_expense(self.categories["Kassir"], 5000, "UZS", "avans", None, self.cashier_id)
        self.assertEqual(self._salary_row()["total_salary"], -4000)

    def test_cashier_expense_requires_a_real_cashier(self):
        with self.assertRaises(db.AppError):
            db.add_expense(self.categories["Kassir"], 1000, "UZS", None, None, None)
        admin_id = db.add_user(
            email="admin@shop.uz", password="parol123", role="admin", username="Admin"
        )
        with self.assertRaises(db.AppError):
            db.add_expense(self.categories["Kassir"], 1000, "UZS", None, None, admin_id)

    def test_editing_an_expense_moves_the_deduction(self):
        self._sell(20, 10000)
        expense_id = db.add_expense(
            self.categories["Kassir"], 4000, "UZS", "avans", None, self.cashier_id
        )
        self.assertEqual(self._salary_row()["total_salary"], 6000)

        # Moving it to a normal category releases the salary again.
        db.update_expense(expense_id, self.categories["Transport"], 4000, "UZS", "avans", None, None)
        self.assertEqual(self._salary_row()["total_salary"], 10000)

        db.update_expense(
            expense_id, self.categories["Kassir"], 4000, "UZS", "avans", None, self.cashier_id
        )
        db.delete_expense(expense_id)
        self.assertEqual(self._salary_row()["total_salary"], 10000)

    def test_reports_expose_the_deduction_per_cashier(self):
        self._sell(20, 10000)
        db.add_expense(self.categories["Kassir"], 4000, "UZS", "avans", None, self.cashier_id)
        db.add_expense(self.categories["Kassir"], 1000, "UZS", "taksi", None, self.cashier_id)

        rows = db.get_cashier_expense_deductions(self.today, self.today)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cashier_id"], self.cashier_id)
        self.assertEqual(rows[0]["amount"], 5000)
        self.assertEqual(rows[0]["expense_count"], 2)
        self.assertEqual(db.get_cashier_expense_total(self.today, self.today), 5000)
        self.assertEqual(len(db.get_cashier_expense_entries(self.today, self.today)), 2)

    def test_series_carry_the_deduction_for_charts(self):
        self._sell(20, 10000)
        db.add_expense(self.categories["Kassir"], 4000, "UZS", "avans", None, self.cashier_id)

        overall = db.get_overall_period_series(self.today, self.today)
        self.assertEqual(sum(row["salary_deduction"] or 0 for row in overall), 4000)

        hourly = db.get_overall_day_hourly_series(self.today)
        self.assertEqual(sum(row["salary_deduction"] or 0 for row in hourly), 4000)

        entity = db.get_entity_period_series(
            "cashier_salary", self.cashier_id, self.today, self.today
        )
        self.assertEqual(sum(row["total_salary"] or 0 for row in entity), 6000)

        entity_hourly = db.get_entity_day_hourly_series(
            "cashier_salary", self.cashier_id, self.today
        )
        self.assertEqual(sum(row["total_salary"] or 0 for row in entity_hourly), 6000)

    def test_expense_list_names_the_charged_cashier(self):
        db.add_expense(self.categories["Kassir"], 4000, "UZS", "avans", None, self.cashier_id)
        db.add_expense(self.categories["Transport"], 1000, "UZS", "benzin", None, None)
        by_category = {row["category_name"]: row for row in db.get_expenses()}
        self.assertEqual(by_category["Kassir"]["cashier_name"], "Aziz")
        self.assertIsNone(by_category["Transport"]["cashier_name"])

    def test_cashier_category_cannot_be_renamed_or_removed_while_in_use(self):
        db.add_expense(self.categories["Kassir"], 4000, "UZS", "avans", None, self.cashier_id)
        with self.assertRaises(db.AppError):
            db.update_expense_category(self.categories["Kassir"], "Boshqa nom")
        with self.assertRaises(db.AppError):
            db.delete_expense_category(self.categories["Kassir"])
        # An unused cashier category is still free to rename.
        db.delete_expense(db.get_expenses()[0]["id"])
        db.update_expense_category(self.categories["Kassir"], "Cashier")
        self.assertTrue(db.is_cashier_expense_category_name("Cashier"))


if __name__ == "__main__":
    unittest.main()


class SalesDetailsExpenseRowTest(unittest.TestCase):
    """The details table lists cashier expenses beside the sales they offset."""

    def setUp(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.old_path = db.DB_PATH
        db.DB_PATH = self.path
        db.init_db()
        self.categories = {row["name"]: row["id"] for row in db.get_expense_categories()}
        self.cashier_id = db.add_user(
            email="sardor@shop.uz", password="parol123", role="cashier", username="Sardor"
        )
        self.product_id = db.add_product(
            {"name": "Noutbuk", "barcode": "e2", "price": 1000000, "cost": 800000, "quantity": 0}
        )
        db.add_stock(self.product_id, 50, "init")
        sale_id = db.create_sale(
            None, self.cashier_id,
            [{"product_id": self.product_id, "quantity": 1, "price": 1000000, "subtotal": 1000000}],
            total=1000000, discount=0, paid=1000000, payment_method="naqd", is_finalized=0,
        )
        db.finalize_sale(sale_id, cashier_reward=125000)
        db.add_expense(self.categories["Kassir"], 40000, "UZS", "avans", None, self.cashier_id)

    def tearDown(self):
        try:
            db._get_engine().dispose()
        finally:
            db.DB_PATH = self.old_path
            for suffix in ("", "-shm", "-wal"):
                path = self.path + suffix
                if os.path.exists(path):
                    os.remove(path)

    def _widget(self):
        from PyQt6.QtWidgets import QApplication
        from ui.reports_widget import SalesDetailsWidget

        # Keep the reference alive: a garbage-collected QApplication aborts Qt.
        self.app = QApplication.instance() or QApplication([])
        widget = SalesDetailsWidget({"id": 1, "role": "admin", "email": "a@b.uz", "username": "a"})
        widget.load_data()
        self.addCleanup(widget.deleteLater)
        return widget

    def test_an_expense_gets_its_own_row_in_the_details_table(self):
        widget = self._widget()
        texts = [
            widget.table.item(row, 1).text()
            for row in range(widget.table.rowCount())
            if widget.table.item(row, 1)
        ]
        self.assertTrue(any("avans" in text for text in texts), texts)

    def test_the_expense_row_shows_a_negative_allocation_in_its_own_colour(self):
        from PyQt6.QtGui import QColor
        from ui.reports_widget import SalesDetailsWidget

        widget = self._widget()
        expense_row = next(
            row for row in range(widget.table.rowCount())
            if widget.table.item(row, 1) and "avans" in widget.table.item(row, 1).text()
        )
        allocation = widget.table.item(expense_row, 6)
        self.assertTrue(allocation.text().startswith("-"))
        self.assertEqual(
            widget.table.item(expense_row, 0).background().color(),
            QColor(SalesDetailsWidget.EXPENSE_ROW_HEX),
        )
        self.assertEqual(
            widget.table.item(expense_row, 8).background().color(),
            QColor(SalesDetailsWidget.EXPENSE_STATUS_HEX),
        )

    def test_the_old_banner_above_the_table_is_gone(self):
        widget = self._widget()
        self.assertFalse(hasattr(widget, "deduction_lbl"))
        self.assertEqual(widget.summary_stats_lbl.text(), "")

    def test_expense_rows_do_not_distort_the_sales_totals(self):
        widget = self._widget()
        self.assertEqual(widget.summary_cards["count"].text(), "1")
        self.assertEqual(widget.summary_cards["products"].text(), "1")
        # 125 000 earned - 40 000 already taken.
        self.assertIn("85,000", widget.summary_cards["salary"].text())
        self.assertIn("85,000", widget.table.item(0, 6).text())


class ProfitIsolationTest(unittest.TestCase):
    """A cashier expense moves only that cashier's salary, never the profit."""

    def setUp(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.old_path = db.DB_PATH
        db.DB_PATH = self.path
        db.init_db()
        self.today = datetime.date.today().isoformat()
        self.categories = {row["name"]: row["id"] for row in db.get_expense_categories()}
        self.cashier_id = db.add_user(
            email="sardor@shop.uz", password="parol123", role="cashier", username="Sardor"
        )
        product_id = db.add_product(
            {"name": "Noutbuk", "barcode": "e2", "price": 1000000, "cost": 600000, "quantity": 0}
        )
        db.add_stock(product_id, 50, "init")
        for _ in range(2):
            sale_id = db.create_sale(
                None, self.cashier_id,
                [{"product_id": product_id, "quantity": 1, "price": 1000000, "subtotal": 1000000}],
                total=1000000, discount=0, paid=1000000, payment_method="naqd", is_finalized=0,
            )
            db.finalize_sale(sale_id, cashier_reward=100000)

    def tearDown(self):
        try:
            db._get_engine().dispose()
        finally:
            db.DB_PATH = self.old_path
            for suffix in ("", "-shm", "-wal"):
                path = self.path + suffix
                if os.path.exists(path):
                    os.remove(path)

    def _cards(self):
        from PyQt6.QtWidgets import QApplication
        from ui.reports_widget import ReportsWidget

        self.app = QApplication.instance() or QApplication([])
        widget = ReportsWidget({"id": 1, "role": "admin", "email": "a@b.uz", "username": "a"})
        widget.load_data()
        self.addCleanup(widget.deleteLater)
        return {key: label.text() for key, label in widget.summary_cards.items()}

    def test_a_cashier_expense_leaves_profit_and_net_profit_untouched(self):
        db.add_expense(self.categories["Transport"], 50000, "UZS", "benzin", None, None)
        before = self._cards()
        db.add_expense(self.categories["Kassir"], 120000, "UZS", "avans", None, self.cashier_id)
        after = self._cards()

        self.assertEqual(before["profit"], after["profit"])
        self.assertEqual(before["net_profit"], after["net_profit"])
        self.assertEqual(before["revenue"], after["revenue"])
        # 800 000 profit - 50 000 ordinary expense = 750 000 net profit
        self.assertIn("750,000", after["net_profit"])
        # Only the salary moves: 200 000 earned - 120 000 already taken.
        self.assertIn("200,000", before["salary"])
        self.assertIn("80,000", after["salary"])

    def test_profit_reports_skip_cashier_expenses_at_the_query_level(self):
        db.add_expense(self.categories["Transport"], 50000, "UZS", "benzin", None, None)
        db.add_expense(self.categories["Kassir"], 120000, "UZS", "avans", None, self.cashier_id)

        everything = sum(
            row["amount"] or 0
            for row in db.get_expense_report(self.today, self.today)
        )
        profit_side = sum(
            row["amount"] or 0
            for row in db.get_expense_report(self.today, self.today, include_cashier=False)
        )
        self.assertEqual(everything, 170000)
        self.assertEqual(profit_side, 50000)

        hourly = sum(
            row["amount"] or 0
            for row in db.get_expense_hourly_report(self.today, include_cashier=False)
        )
        self.assertEqual(hourly, 50000)


class CashierCategoryAvailabilityTest(unittest.TestCase):
    """The category must be offerable even on a database that never had it."""

    def setUp(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.old_path = db.DB_PATH
        db.DB_PATH = self.path
        db.init_db()
        for category in db.get_expense_categories():
            if db.is_cashier_expense_category_name(category["name"]):
                db.delete_expense_category(category["id"])
        db.add_user(email="sardor@shop.uz", password="parol123", role="cashier", username="Sardor")

    def tearDown(self):
        try:
            db._get_engine().dispose()
        finally:
            db.DB_PATH = self.old_path
            for suffix in ("", "-shm", "-wal"):
                path = self.path + suffix
                if os.path.exists(path):
                    os.remove(path)

    def test_renamed_variants_are_still_recognised(self):
        for name in ("Kassir", "kassir oyligi", "Cashier advance", "КАССИР"):
            self.assertTrue(db.is_cashier_expense_category_name(name), name)
        for name in ("Transport", "Boshqa", "Ijara", ""):
            self.assertFalse(db.is_cashier_expense_category_name(name), name)

    def test_ensure_creates_it_once_and_only_once(self):
        self.assertFalse(
            any(db.is_cashier_expense_category_name(c["name"])
                for c in db.get_expense_categories())
        )
        db.ensure_cashier_expense_category()
        db.ensure_cashier_expense_category()
        matches = [
            c for c in db.get_expense_categories()
            if db.is_cashier_expense_category_name(c["name"])
        ]
        self.assertEqual(len(matches), 1)

    def test_the_dialog_offers_the_category_and_reveals_the_cashier_picker(self):
        from PyQt6.QtWidgets import QApplication
        from ui.expenses_widget import ExpenseDialog, ExpensesWidget

        self.app = QApplication.instance() or QApplication([])
        parent = ExpensesWidget({"id": 1, "role": "admin"})
        parent.load_data()
        self.addCleanup(parent.deleteLater)

        dialog = ExpenseDialog(parent)
        self.addCleanup(dialog.deleteLater)
        dialog.show()
        self.app.processEvents()

        names = [dialog.category_combo.itemText(i) for i in range(dialog.category_combo.count())]
        cashier_index = next(
            i for i in range(dialog.category_combo.count())
            if db.is_cashier_expense_category_name(dialog.category_combo.itemText(i))
        )
        self.assertTrue(any(db.is_cashier_expense_category_name(n) for n in names), names)

        self.assertFalse(dialog.cashier_combo.isVisible())
        dialog.category_combo.setCurrentIndex(cashier_index)
        self.app.processEvents()
        self.assertTrue(dialog.cashier_combo.isVisible())
        self.assertIn("Sardor", [
            dialog.cashier_combo.itemText(i) for i in range(dialog.cashier_combo.count())
        ])

        dialog.cashier_combo.setCurrentIndex(1)
        dialog.amount_edit.setText("120000")
        data = dialog.get_data()
        self.assertIsNotNone(data["cashier_id"])
        self.assertEqual(data["amount"], 120000)
