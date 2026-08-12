import os
import tempfile
import unittest

import database as db
from ui.i18n import set_language


class UiDatabaseSmokeTest(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.old_path = db.DB_PATH
        db.DB_PATH = self.path
        db.init_db()

    def tearDown(self):
        try:
            db._get_engine().dispose()
        finally:
            db.DB_PATH = self.old_path
            for suffix in ("", "-shm", "-wal"):
                path = self.path + suffix
                if os.path.exists(path):
                    os.remove(path)

    def test_all_database_backed_widgets_load(self):
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
        user = db.authenticate("admin@gmail.com", "admin123")
        category_id = db.add_category("UI Category")
        supplier_id = db.add_supplier("UI Supplier")
        product_id = db.add_product({
            "barcode": "UI1",
            "name": "UI Product",
            "template_id": None,
            "supplier_id": supplier_id,
            "category_id": category_id,
            "price": 1000,
            "cost": 600,
            "stock": 5,
            "unit": "dona",
        })
        customer_id = db.add_customer("UI Customer", "90", "ui@example.com")
        db.create_sale(
            customer_id,
            user["id"],
            [{"product_id": product_id, "quantity": 1, "price": 1000, "subtotal": 1000}],
            1000,
            0,
            1000,
            "naqd",
        )
        expense_category_id = db.add_expense_category("UI Expense")
        db.add_expense(expense_category_id, 100, "UZS", "paper")

        from ui.checking_widget import CheckingWidget
        from ui.customers_widget import CustomersWidget
        from ui.expenses_widget import ExpensesWidget
        from ui.login_history_widget import LoginHistoryWidget
        from ui.products_widget import ProductsWidget
        from ui.reports_widget import ReportsWidget
        from ui.sales_widget import SalesWidget
        from ui.stock_widget import StockWidget
        from ui.supplier_debts_widget import SupplierDebtsWidget
        from ui.users_widget import UsersWidget

        widgets = [
            SalesWidget(user),
            ProductsWidget(user),
            StockWidget(),
            ReportsWidget(),
            UsersWidget(),
            LoginHistoryWidget(),
            SupplierDebtsWidget(),
            ExpensesWidget(),
            CheckingWidget(user),
            CustomersWidget(),
        ]
        for widget in widgets:
            load = getattr(widget, "load_data", None)
            if callable(load):
                load()
        products = widgets[1]
        set_language(products, "en")
        self.assertIn("Available:", products.stats_lbl.text())
        self.assertEqual(products.search_edit.placeholderText(), "Search...")
        set_language(products, "ru")
        self.assertIn("В наличии:", products.stats_lbl.text())
        self.assertEqual(products.search_edit.placeholderText(), "Поиск...")
        debts = widgets[6]
        set_language(debts, "en")
        self.assertIn("Total", debts.total_lbl.text())
        self.assertIn(debts.add_btn.text(), {"+ Supplier", "+ Debtor"})
        self.assertEqual(len(widgets), 10)
        app.processEvents()


if __name__ == "__main__":
    unittest.main()
