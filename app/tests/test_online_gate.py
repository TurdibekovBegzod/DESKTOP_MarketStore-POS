"""All business records require a verified live server connection.

The rule lives at the shared database boundary so products, stock, customers,
users, and future synced records cannot silently keep working offline while
sales and expenses are refused.
"""

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

import database as db


_app = QApplication.instance() or QApplication([])


class OnlineGateTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="marketstore-online-")
        self.old_path = db.DB_PATH
        self.old_uid = db._ACTIVE_ACCOUNT_UID
        db.activate_account_database("acct-gate", email="owner@example.com", storage_root=self.root)
        db.init_db(
            account_owner={"user_uid": "acct-gate", "email": "owner@example.com",
                           "display_name": "Owner"},
            seed_defaults=False,
        )
        self.owner = db.sync_online_user(
            "owner@example.com", role="admin", user_uid="acct-gate", access_token="tok"
        )
        from ui.main_window import MainWindow
        self.window = MainWindow(self.owner)
        self.window._realtime_online = True
        # Ordinary record-gate tests must not contact a real API.
        db.set_online_check(self.window._is_online)

    def tearDown(self):
        self.window.close()
        db.set_online_check(None)
        if db._ENGINE is not None:
            db._ENGINE.dispose()
        db._ENGINE = None
        db._ENGINE_PATH = None
        db._SessionLocal = None
        db.DB_PATH = self.old_path
        db._ACTIVE_ACCOUNT_UID = self.old_uid
        shutil.rmtree(self.root, ignore_errors=True)

    def test_a_live_stream_means_online(self):
        self.window._realtime_online = True
        self.assertTrue(self.window._is_online())

    def test_not_knowing_yet_counts_as_offline(self):
        """The old rule treated "unknown" as online, which is a guess."""
        self.window._realtime_online = None
        self.window._engine_state = "idle"
        self.assertFalse(self.window._is_online())

    def test_a_dropped_stream_means_offline(self):
        self.window._realtime_online = False
        self.assertFalse(self.window._is_online())

    def test_a_recent_exchange_does_not_replace_a_live_connection(self):
        self.window._realtime_online = None
        self.window._engine_state = "idle"
        db.record_sync_success({"pulled": 0, "pushed": 1})
        self.assertFalse(self.window._is_online())

    def test_the_engine_reporting_offline_overrules_a_live_stream(self):
        self.window._realtime_online = True
        self.window._engine_state = "offline"
        self.assertFalse(self.window._is_online())

    def test_money_is_refused_while_the_device_is_offline(self):
        product = db.add_product({"barcode": "P1", "name": "Mahsulot", "price": 1000,
                                  "cost": 600, "stock": 5, "unit": "dona"})
        cashier = db.add_user("k@example.com", role="cashier", username="K")
        self.window._realtime_online = False

        with self.assertRaises(db.AppError) as refused:
            db.create_sale(None, cashier, [{"product_id": product, "quantity": 1,
                                            "price": 1000, "subtotal": 1000}],
                           1000, 0, 1000, "naqd")
        self.assertIn("internetga ulanmagansiz yoki server ishlamayapti", str(refused.exception).lower())

        # And nothing was written, so nothing is waiting to be sent later.
        self.assertEqual(db.get_product_by_barcode("P1")["stock"], 5)
        self.assertEqual(db.get_product_sales_archive(""), [])

    def test_all_synced_record_types_are_refused_while_offline(self):
        product_id = db.add_product({
            "barcode": "P2", "name": "Oldingi mahsulot", "price": 1000,
            "cost": 600, "stock": 5, "unit": "dona",
        })
        customer_id = db.add_customer("Oldingi mijoz", "+99890", "old@example.com")
        db.mark_sync_pushed()
        self.window._realtime_online = False

        attempts = (
            ("mahsulot", lambda: db.add_product({
                "barcode": "OFFLINE", "name": "Offline mahsulot", "price": 1,
                "cost": 1, "stock": 1, "unit": "dona",
            })),
            ("qoldiq", lambda: db.add_stock(product_id, 3, "offline kirim")),
            ("mijoz", lambda: db.add_customer("Offline mijoz", "+99891", "new@example.com")),
            ("mijoz tahriri", lambda: db.update_customer(
                customer_id, "O'zgargan mijoz", "+99892", "changed@example.com"
            )),
            ("kategoriya", lambda: db.add_category("Offline kategoriya")),
            ("kassir", lambda: db.add_user(
                "offline@example.com", role="cashier", username="Offline kassir"
            )),
        )
        for label, attempt in attempts:
            with self.subTest(label=label):
                with self.assertRaises(db.AppError) as refused:
                    attempt()
                self.assertIn(
                    "internetga ulanmagansiz yoki server ishlamayapti",
                    str(refused.exception).lower(),
                )

        self.assertIsNone(db.get_product_by_barcode("OFFLINE"))
        self.assertEqual(db.get_product_by_id(product_id)["stock"], 5)
        self.assertEqual(db.get_all_customers()[0]["name"], "Oldingi mijoz")
        self.assertEqual(db.count_pending_sync_rows(), 0)

    def test_connectivity_check_failure_is_offline(self):
        db.set_online_check(lambda: (_ for _ in ()).throw(RuntimeError("check failed")))
        self.assertFalse(db.is_online())

    def test_write_probe_requires_the_api_without_changing_data(self):
        self.window._realtime_online = True
        self.window._engine_state = "idle"
        with patch("ui.main_window.api_client.get_sync_state", return_value={"generation": 1}) as probe:
            self.assertTrue(self.window._server_accepts_writes())
        probe.assert_called_once_with("tok", timeout=3)

        with patch("ui.main_window.api_client.get_sync_state", side_effect=OSError("api down")):
            self.assertFalse(self.window._server_accepts_writes())
        self.assertEqual(self.window._engine_state, "offline")

        db.set_online_check(self.window._server_accepts_writes)
        db.mark_sync_pushed()
        with patch("ui.main_window.api_client.get_sync_state", side_effect=OSError("api down")):
            with self.assertRaises(db.AppError):
                db.add_customer("API ishlamayapti", "+99890", "blocked@example.com")
        self.assertEqual(db.get_all_customers(), [])
        self.assertEqual(db.count_pending_sync_rows(), 0)

    def test_the_label_says_which_it_is(self):
        self.window._realtime_online = True
        self.window._refresh_sync_status()
        online_text = self.window.sync_btn.text()

        self.window._realtime_online = False
        self.window._refresh_sync_status()
        offline_text = self.window.sync_btn.text()

        self.assertNotEqual(online_text, offline_text)
        self.assertIn(offline_text.lower(), ("offline", "офлайн"))


if __name__ == "__main__":
    unittest.main()
