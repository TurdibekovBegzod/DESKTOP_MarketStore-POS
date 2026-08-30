import os
import tempfile
import unittest

import database as db
from ui.i18n import set_language


class UiDatabaseSmokeTest(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        db.set_online_check(None)
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.old_path = db.DB_PATH
        db.DB_PATH = self.path
        db.init_db()

    def tearDown(self):
        db.set_online_check(None)
        try:
            db._get_engine().dispose()
        finally:
            db.DB_PATH = self.old_path
            for suffix in ("", "-shm", "-wal"):
                path = self.path + suffix
                if os.path.exists(path):
                    os.remove(path)
            marker = os.path.join(
                os.path.dirname(self.path),
                f".{os.path.basename(self.path)}.account_logo_migrated",
            )
            if os.path.exists(marker):
                os.remove(marker)

    def test_account_logo_pixmap_is_stored_as_a_synced_asset(self):
        from PyQt6.QtGui import QColor, QPixmap
        from PyQt6.QtWidgets import QApplication
        from ui.main_window import (
            ACCOUNT_LOGO_MAX_SIDE,
            load_custom_logo_pixmap,
            reset_custom_logo,
            save_custom_logo,
        )

        app = QApplication.instance() or QApplication([])
        pixmap = QPixmap(640, 320)
        pixmap.fill(QColor("#16a34a"))

        self.assertTrue(save_custom_logo(pixmap))
        asset = db.get_account_asset("desktop_logo")
        self.assertIsNotNone(asset)
        self.assertEqual(asset["media_type"], "image/png")
        self.assertTrue(asset["content"].startswith(b"\x89PNG\r\n\x1a\n"))
        restored = load_custom_logo_pixmap()
        self.assertFalse(restored.isNull())
        self.assertLessEqual(restored.width(), ACCOUNT_LOGO_MAX_SIDE)
        self.assertLessEqual(restored.height(), ACCOUNT_LOGO_MAX_SIDE)
        app.processEvents()

        self.assertTrue(reset_custom_logo())
        self.assertIsNone(db.get_account_asset("desktop_logo"))

    def test_pending_sale_count_matches_all_finalize_product_rows(self):
        cashier_id = db.add_user(
            "badge-cashier@example.com", role="cashier", username="Badge cashier"
        )
        first_product = db.add_product({
            "barcode": "BADGE-1", "name": "Badge product 1", "price": 1000,
            "cost": 600, "stock": 5, "unit": "dona",
        })
        second_product = db.add_product({
            "barcode": "BADGE-2", "name": "Badge product 2", "price": 2000,
            "cost": 1200, "stock": 5, "unit": "dona",
        })
        sale_id = db.create_sale(
            None,
            cashier_id,
            [
                {"product_id": first_product, "quantity": 1, "price": 1000, "subtotal": 1000},
                {"product_id": second_product, "quantity": 1, "price": 2000, "subtotal": 2000},
            ],
            3000,
            0,
            3000,
            "naqd",
            is_finalized=0,
        )

        visible_rows = db.get_product_sales_archive(
            "", None, None, only_cashiers=True, only_pending=True
        )
        self.assertEqual(db.count_pending_sale_items(only_cashiers=True), len(visible_rows))
        self.assertEqual(db.count_pending_sale_items(only_cashiers=True), 2)

        db.finalize_sale(sale_id)
        self.assertEqual(db.count_pending_sale_items(only_cashiers=True), 0)

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
        from ui.reports_widget import ReportsWidget, SalesDetailsWidget
        from ui.sales_widget import SalesWidget
        from ui.supplier_debts_widget import SupplierDebtsWidget
        from ui.users_widget import UsersWidget

        widgets = [
            SalesWidget(user),
            ProductsWidget(user),
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
        products = next(w for w in widgets if isinstance(w, ProductsWidget))
        self.assertEqual(products.table.columnCount(), 7)
        self.assertNotIn(
            "Copy",
            [products.table.horizontalHeaderItem(column).text() for column in range(products.table.columnCount())],
        )
        product_menu = products._build_product_actions_menu(0, products.table)
        self.assertEqual(len(product_menu.actions()), 5)
        self.assertTrue(all(action.text()[0] in "⏳✏🏷🗑📄" for action in product_menu.actions()))
        set_language(products, "en")
        self.assertIn("Available:", products.stats_lbl.text())
        self.assertEqual(products.search_edit.placeholderText(), "Search...")
        set_language(products, "ru")
        self.assertIn("В наличии:", products.stats_lbl.text())
        self.assertEqual(products.search_edit.placeholderText(), "Поиск...")
        reports = next(w for w in widgets if isinstance(w, ReportsWidget))
        self.assertEqual(len(reports.summary_cards), 6)
        self.assertTrue(reports.summary_card_frames["salary"].isHidden())
        cashier_report_index = reports.report_type_combo.findData("cashier")
        reports.report_type_combo.setCurrentIndex(cashier_report_index)
        app.processEvents()
        self.assertFalse(reports.summary_card_frames["salary"].isHidden())
        metric_labels = [
            reports.metric_combo.itemText(index)
            for index in range(reports.metric_combo.count())
        ]
        self.assertIn("Sotuvlar soni", metric_labels)
        self.assertNotIn("Cheklar", metric_labels)
        debts = next(w for w in widgets if isinstance(w, SupplierDebtsWidget))
        set_language(debts, "en")
        self.assertIn("Total", debts.total_lbl.text())
        self.assertIn(debts.add_btn.text(), {"+ Supplier", "+ Debtor"})
        self.assertEqual(len(widgets), 9)
        app.processEvents()

    def test_cashier_products_and_reports_are_restricted(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QApplication, QSizePolicy, QHeaderView
        from ui.expenses_widget import ExpenseDialog
        from ui.products_widget import ProductDialog, ProductsWidget
        from ui.reports_widget import ReportsWidget, SalesDetailsWidget

        app = QApplication.instance() or QApplication([])
        user = db.authenticate("admin@gmail.com", "admin123")

        products = ProductsWidget(user, cashier_mode=True)
        self.assertTrue(products.section_add_btn.isHidden())
        self.assertTrue(products.trash_btn.isHidden())
        self.assertEqual(products.table.horizontalHeader().sectionResizeMode(0), QHeaderView.ResizeMode.Stretch)
        self.assertFalse(products.table.isColumnHidden(6))
        self.assertFalse(products.process_table.isColumnHidden(10))
        self.assertEqual(products.sold_table.columnCount(), 9)
        self.assertNotIn(
            "Holati",
            [products.sold_table.horizontalHeaderItem(column).text() for column in range(products.sold_table.columnCount())],
        )
        self.assertTrue(products.sold_table.isColumnHidden(8))

        products.current_section = {"id": 1, "name": "Test"}
        products._set_products_mode(True)
        self.assertFalse(products.add_btn.isHidden())
        self.assertTrue(products.templates_btn.isHidden())

        empty_section_id = db.add_product_section("Cashier add section")
        add_dialog = ProductDialog(
            products,
            section_id=empty_section_id,
            require_template=True,
        )
        self.assertEqual(add_dialog.template_combo.count(), 1)
        self.assertIsNotNone(add_dialog.template_combo.currentData())

        reports = ReportsWidget(user=user, cashier_only=True)
        self.assertEqual(
            reports.entity_table.sizePolicy().verticalPolicy(),
            QSizePolicy.Policy.Expanding,
        )
        self.assertGreater(reports.entity_table.maximumHeight(), 220)
        self.assertEqual(reports.detail_mode, "cashier")
        self.assertFalse(all(card.isHidden() for card in reports.summary_card_widgets))
        self.assertTrue(reports.summary_card_frames["revenue"].isHidden())
        self.assertTrue(reports.summary_card_frames["profit"].isHidden())
        self.assertTrue(reports.summary_card_frames["net_profit"].isHidden())
        self.assertFalse(reports.summary_card_frames["count"].isHidden())
        self.assertFalse(reports.summary_card_frames["products"].isHidden())
        self.assertFalse(reports.summary_card_frames["salary"].isHidden())
        cashier_id = db.add_user(
            "cashier.report@example.com",
            role="cashier",
            username="Cashier Report",
        )
        expense_dialog = ExpenseDialog()
        self.assertTrue(expense_dialog.cashier_combo.isHidden())
        cashier_category_id = next(
            category["id"]
            for category in db.get_expense_categories()
            if db.is_cashier_expense_category_name(category["name"])
        )
        expense_dialog.category_combo.setCurrentIndex(
            expense_dialog.category_combo.findData(cashier_category_id)
        )
        self.assertFalse(expense_dialog.cashier_combo.isHidden())
        cashier_ids = {
            expense_dialog.cashier_combo.itemData(index)
            for index in range(expense_dialog.cashier_combo.count())
            if expense_dialog.cashier_combo.itemData(index) is not None
        }
        self.assertEqual(cashier_ids, {cashier_id})
        other_category_id = next(
            category["id"]
            for category in db.get_expense_categories()
            if not db.is_cashier_expense_category_name(category["name"])
        )
        expense_dialog.category_combo.setCurrentIndex(
            expense_dialog.category_combo.findData(other_category_id)
        )
        self.assertTrue(expense_dialog.cashier_combo.isHidden())
        reports._load_entities("2000-01-01", "2999-12-31")
        report_user_ids = {
            reports.entity_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            for row in range(reports.entity_table.rowCount())
        }
        self.assertEqual(report_user_ids, {cashier_id})
        self.assertNotIn(user["id"], report_user_ids)
        details = SalesDetailsWidget(user=user, cashier_only=True)
        details._fill_table([{
            "created_at": "2026-08-22 14:30:00",
            "product_name": "Test product",
            "barcode": "TEST-1",
            "sold_quantity": 2,
            "returned_quantity": 1,
            "net_quantity": 1,
            "price": 5000,
            "item_total_after_discount": 5000,
            "cashier_reward": 750,
            "payment_method": "naqd",
            "is_finalized": 1,
        }])
        self.assertEqual(details.table.columnCount(), 9)
        self.assertEqual(details.table.rowCount(), 2)
        self.assertEqual(details.table.item(0, 0).text(), "")
        self.assertEqual(details.table.item(0, 1).text(), "")
        self.assertEqual(details.table.item(0, 8).text(), "")
        self.assertEqual(details.table.item(0, 3).text(), "1")
        self.assertIn("5,000", details.table.item(0, 5).text())
        self.assertIn("750", details.table.item(0, 6).text())
        self.assertEqual(details.table.verticalHeaderItem(0).text(), "")
        self.assertEqual(details.table.verticalHeaderItem(1).text(), "1")
        self.assertEqual(details.table.item(1, 0).text(), "22.08 14:30")
        self.assertEqual(details.table.item(1, 1).text(), "Test product")
        self.assertEqual(details.table.item(0, 3).text(), "1")
        self.assertEqual(details.table.item(1, 3).text(), "1")
        self.assertEqual(details.table.item(1, 4).text(), "5,000 so'm")
        self.assertEqual(details.table.item(1, 8).text(), "✅")
        self.assertEqual(details.table.item(1, 8).toolTip(), "Yakunlangan")
        self.assertEqual(details.table.item(1, 8).background().color().name(), "#bbf7d0")
        self.assertNotEqual(details.table.item(1, 8).background().color().name(), "#ffffff")
        self.assertIn("#f3f4f6", details.table.verticalHeader().styleSheet())
        self.assertEqual(details._status_icon("Yakunlangan"), "✅")

        pending_row = dict(details._last_rows[0], is_finalized=0, cashier_reward=0)
        details._fill_table([pending_row])
        self.assertEqual(details.table.item(1, 8).text(), "⏳")
        self.assertEqual(details.table.item(1, 6).text(), "-")
        grouped = details._group_sales_rows([pending_row, pending_row])
        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0]["net_quantity"], 2)
        self.assertEqual(grouped[0]["created_at"], "2026-08-22 14:30:00")

        completed_row = dict(details._last_rows[0], returned_quantity=0, net_quantity=2, is_finalized=1)
        details._fill_table([completed_row])
        self.assertEqual(details.table.item(1, 8).text(), "✅")

        fully_returned_row = dict(
            details._last_rows[0],
            returned_quantity=2,
            net_quantity=0,
            item_total_after_discount=0,
            cashier_reward=0,
            is_finalized=1,
        )
        details._fill_table([fully_returned_row])
        self.assertEqual(details.table.rowCount(), 0)

        active_rows = [
            dict(completed_row, sale_item_id=20, sold_quantity=1, net_quantity=1, created_at="2026-08-22 14:20:00"),
            dict(completed_row, sale_item_id=21, sold_quantity=1, net_quantity=1, created_at="2026-08-22 14:25:00"),
        ]
        old_return = dict(
            fully_returned_row,
            sale_item_id=10,
            sold_quantity=1,
            returned_quantity=1,
            created_at="2026-08-22 13:00:00",
            returned_at="2026-08-22 13:05:00",
        )
        latest_return = dict(
            fully_returned_row,
            sale_item_id=22,
            sold_quantity=1,
            returned_quantity=1,
            created_at="2026-08-22 14:30:00",
            returned_at="2026-08-22 14:35:00",
        )
        details._fill_table([old_return, *active_rows, latest_return])
        self.assertEqual(details.table.rowCount(), 2)
        self.assertEqual(details.table.item(0, 3).text(), "2")
        self.assertEqual(details.table.item(1, 3).text(), "2")

        returned_once = dict(
            fully_returned_row,
            sale_item_id=30,
            sold_quantity=1,
            returned_quantity=1,
            created_at="2026-08-22 15:00:00",
            returned_at="2026-08-22 15:05:00",
        )
        resale = dict(
            completed_row,
            sale_item_id=31,
            sold_quantity=1,
            returned_quantity=0,
            net_quantity=1,
            created_at="2026-08-22 15:10:00",
            returned_at=None,
        )
        details._fill_table([returned_once, resale])
        self.assertEqual(details.table.item(1, 3).text(), "1")
        self.assertEqual(details.table.item(1, 4).text(), "5,000 so'm")
        self.assertEqual(details.table.item(1, 8).text(), "✅")
        self.assertEqual(details.table.item(1, 8).background().color().name(), "#bbf7d0")

        pending_resale = dict(resale, sale_item_id=32, is_finalized=0, created_at="2026-08-22 15:15:00")
        grouped = details._group_sales_rows([returned_once, pending_resale])
        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0]["returned_quantity"], 0)
        self.assertEqual(grouped[0]["is_finalized"], 0)

        net_rows = reports._with_net_profit_from_expenses(
            [{"label": "2026-08-22", "profit": 5000, "cashier_reward": 750}],
            [],
            [dict(currency) for currency in db.get_currencies()],
        )
        self.assertEqual(net_rows[0]["net_profit"], 4250)

        products._load_cashier_filter()
        cashier_index = products.cashier_filter.findData(cashier_id)
        self.assertGreaterEqual(cashier_index, 0)
        products.cashier_filter.setCurrentIndex(cashier_index)
        filtered = products._apply_product_filters([
            {"section_id": 1, "created_by_user_id": cashier_id},
            {"section_id": 1, "created_by_user_id": user["id"]},
        ])
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["created_by_user_id"], cashier_id)
        sold_filtered = products._apply_product_filters([
            {"sale_item_id": 1, "section_id": 1, "cashier_id": cashier_id},
            {"sale_item_id": 2, "section_id": 1, "cashier_id": user["id"]},
        ])
        self.assertEqual(len(sold_filtered), 1)
        self.assertEqual(sold_filtered[0]["cashier_id"], cashier_id)
        report_data = reports._fetch_report_data("2026-01-01", "2026-01-31", "month")
        self.assertEqual(report_data["rows"], [])
        self.assertEqual(report_data["expense_rows"], [])
        app.processEvents()

    def test_cashier_main_window_exposes_restricted_pages(self):
        from PyQt6.QtWidgets import QApplication
        from ui.main_window import MainWindow

        app = QApplication.instance() or QApplication([])
        user = dict(db.authenticate("admin@gmail.com", "admin123"))
        user["role"] = "cashier"
        window = MainWindow(user)

        self.assertIn("products", window.nav_buttons)
        self.assertIn("reports", window.nav_buttons)
        self.assertIn("sales_details", window.nav_buttons)
        self.assertEqual(window.nav_group_items, {"reports_group": ("reports", "sales_details")})
        self.assertTrue(window.pages["products"].cashier_mode)
        self.assertTrue(window.pages["reports"].cashier_only)
        self.assertTrue(window.pages["sales_details"].cashier_only)
        self.assertNotIn("stock", window.pages)
        self.assertNotIn("finance", window.pages)
        window.close()
        app.processEvents()


if __name__ == "__main__":
    unittest.main()
