"""Focused regressions for report filters and non-shifting load feedback."""

import os
import shutil
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

import database as db
from ui.async_loader import make_progress_bar, set_progress_bar_loading
from ui.reports_widget import ReportsWidget, SalesDetailsWidget


_app = QApplication.instance() or QApplication([])


class ReportFiltersAndProgressTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="marketstore-report-ui-")
        self.old_path = db.DB_PATH
        self.old_uid = db._ACTIVE_ACCOUNT_UID
        db.activate_account_database(
            "report-ui-owner",
            email="owner@example.com",
            storage_root=self.root,
        )
        db.init_db(
            account_owner={
                "user_uid": "report-ui-owner",
                "email": "owner@example.com",
                "display_name": "Owner",
            },
            seed_defaults=False,
        )
        self.owner = db.sync_online_user(
            "owner@example.com", role="admin", user_uid="report-ui-owner"
        )
        db.set_online_check(lambda: True)
        self.cashier_id = db.add_user(role="cashier", username="Sardor Kassir")
        self.staff_admin_id = db.add_user(role="admin", username="Madina Admin")
        product_id = db.add_product({
            "name": "Test mahsulot",
            "barcode": "REPORT-CASHIER-1",
            "price": 10_000,
            "cost": 5_000,
            "stock": 5,
            "unit": "dona",
        })
        item = [{
            "product_id": product_id,
            "quantity": 1,
            "price": 10_000,
            "subtotal": 10_000,
        }]
        for cashier_id in (self.cashier_id, self.staff_admin_id):
            db.create_sale(
                None, cashier_id, item,
                total=10_000,
                discount=0,
                paid=10_000,
                payment_method="naqd",
            )

    def tearDown(self):
        db.set_online_check(None)
        if db._ENGINE is not None:
            db._ENGINE.dispose()
        db._ENGINE = None
        db._ENGINE_PATH = None
        db._SessionLocal = None
        db.DB_PATH = self.old_path
        db._ACTIVE_ACCOUNT_UID = self.old_uid
        shutil.rmtree(self.root, ignore_errors=True)

    def test_sales_details_lists_all_staff_and_shows_seller_column(self):
        widget = SalesDetailsWidget(user=self.owner)
        self.addCleanup(widget.deleteLater)

        combo_names = {
            widget.cashier_combo.itemText(index)
            for index in range(1, widget.cashier_combo.count())
        }
        self.assertEqual(combo_names, {"Sardor Kassir", "Madina Admin"})
        self.assertNotIn("Owner", combo_names)
        self.assertEqual(widget.table.horizontalHeaderItem(7).text(), "Kassir")

        seller_names = {
            widget.table.item(row, 7).text()
            for row in range(1, widget.table.rowCount())
            if widget.table.item(row, 7)
        }
        self.assertEqual(seller_names, {"Sardor Kassir", "Madina Admin"})

    def test_both_report_pages_start_on_month(self):
        reports = ReportsWidget(user=self.owner)
        details = SalesDetailsWidget(user=self.owner)
        self.addCleanup(reports.deleteLater)
        self.addCleanup(details.deleteLater)

        self.assertEqual(reports.period_combo.currentData(), "month")
        self.assertEqual(details.period_combo.currentData(), "month")

    def test_progress_indicator_never_leaves_its_layout(self):
        bar = make_progress_bar()
        self.assertFalse(bar.isHidden())
        self.assertEqual(bar.height(), 4)

        set_progress_bar_loading(bar, True)
        self.assertFalse(bar.isHidden())
        self.assertTrue(bar.property("loading"))
        set_progress_bar_loading(bar, False)
        self.assertFalse(bar.isHidden())
        self.assertFalse(bar.property("loading"))
        self.assertEqual(bar.height(), 4)


if __name__ == "__main__":
    unittest.main()
