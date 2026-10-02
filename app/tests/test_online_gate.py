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

import api_client
import database as db


_app = QApplication.instance() or QApplication([])


class _WindowCase(unittest.TestCase):
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
        self.installed_write_through = db._WRITE_THROUGH
        self.window._realtime_online = True
        # Ordinary record-gate tests must not contact a real API. They cover
        # the advance check, which only guards changes while write-through is
        # off; WriteThroughWindowTest below covers the normal path.
        db.set_online_check(self.window._is_online)
        db.set_write_through(None)

    def tearDown(self):
        self.window.close()
        db.set_online_check(None)
        db.set_write_through(None)
        if db._ENGINE is not None:
            db._ENGINE.dispose()
        db._ENGINE = None
        db._ENGINE_PATH = None
        db._SessionLocal = None
        db.DB_PATH = self.old_path
        db._ACTIVE_ACCOUNT_UID = self.old_uid
        shutil.rmtree(self.root, ignore_errors=True)


class OnlineGateTest(_WindowCase):
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

        # The next action is verified afresh once the first has finished.
        self.window._forget_write_probe()
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

    def test_one_sale_asks_the_server_once(self):
        """A sale flushes several times; only the first may cost a round trip."""
        product = db.add_product({"barcode": "P3", "name": "Mahsulot", "price": 1000,
                                  "cost": 600, "stock": 5, "unit": "dona"})
        cashier = db.add_user("k3@example.com", role="cashier", username="K3")
        self.window._realtime_online = True
        self.window._engine_state = "idle"
        db.set_online_check(self.window._server_accepts_writes)
        with patch("ui.main_window.api_client.get_sync_state", return_value={"generation": 1}) as probe, \
                patch("ui.main_window.QTimer.singleShot") as end_of_action:
            db.create_sale(None, cashier, [{"product_id": product, "quantity": 1,
                                            "price": 1000, "subtotal": 1000}],
                           1000, 0, 1000, "naqd")
            self.assertEqual(probe.call_count, 1)
            # The reuse ends when the action hands control back to Qt.
            end_of_action.assert_called_once_with(0, self.window._forget_write_probe)
            # The next click is a new action and is verified again.
            self.window._forget_write_probe()
            db.add_customer("Keyingi mijoz", "+99893", "next@example.com")
            self.assertEqual(probe.call_count, 2)
        self.assertEqual(db.get_product_by_barcode("P3")["stock"], 4)

    def test_a_dropped_stream_refuses_the_rest_of_an_action(self):
        """Reusing the probe never outlives the live connection itself."""
        self.window._realtime_online = True
        self.window._engine_state = "idle"
        with patch("ui.main_window.api_client.get_sync_state", return_value={"generation": 1}):
            self.assertTrue(self.window._server_accepts_writes())
        self.window._realtime_online = False
        self.assertFalse(self.window._server_accepts_writes())
        self.window._realtime_online = True
        self.window._engine_state = "offline"
        with patch("ui.main_window.api_client.get_sync_state", side_effect=OSError("api down")):
            self.assertFalse(self.window._server_accepts_writes())

    def test_the_label_says_which_it_is(self):
        self.window._realtime_online = True
        self.window._refresh_sync_status()
        online_text = self.window.sync_btn.text()

        self.window._realtime_online = False
        self.window._refresh_sync_status()
        offline_text = self.window.sync_btn.text()

        self.assertNotEqual(online_text, offline_text)
        self.assertIn(offline_text.lower(), ("offline", "офлайн"))


class WriteThroughWindowTest(_WindowCase):
    """The window's normal path: one upload per change, which is the check."""

    def setUp(self):
        super().setUp()
        db.mark_upgrade_reconcile_complete()
        db.mark_identity_reset_complete()
        db.mark_server_reseed_complete()
        self.product = db.add_product({"barcode": "W1", "name": "Mahsulot", "price": 1000,
                                       "cost": 600, "stock": 5, "unit": "dona"})
        self.cashier = db.add_user("kw@example.com", role="cashier", username="KW")
        db.mark_sync_pushed()
        db.set_online_check(self.window._server_accepts_writes)
        db.set_write_through(self.window._deliver_action,
                             after_commit=self.window._action_delivered)

    def _sell(self):
        return db.create_sale(None, self.cashier, [{"product_id": self.product, "quantity": 1,
                                                    "price": 1000, "subtotal": 1000}],
                              1000, 0, 1000, "naqd")

    def test_the_window_turns_write_through_on(self):
        self.assertEqual(self.installed_write_through, self.window._deliver_action)
        self.assertTrue(db.write_through_ready())

    def test_a_sale_is_one_request_and_no_state_probe(self):
        with patch("sync_service.api_client.push_sync_records",
                   return_value={"saved": 5, "generation": 1, "rejected": []}) as push, \
             patch("ui.main_window.api_client.get_sync_state") as probe:
            self._sell()
        push.assert_called_once()
        probe.assert_not_called()
        self.assertEqual(db.get_product_by_barcode("W1")["stock"], 4)
        self.assertEqual(db.count_pending_sync_rows(), 0)

    def test_no_internet_is_said_and_nothing_is_kept(self):
        error = api_client.ApiOfflineError("x", kind="internet")
        with patch("sync_service.api_client.push_sync_records", side_effect=error):
            with self.assertRaises(db.AppError) as refused:
                self._sell()
        self.assertIn("Internet ishlamayapti", str(refused.exception))
        self.assertEqual(db.get_product_by_barcode("W1")["stock"], 5)
        self.assertEqual(db.get_product_sales_archive(""), [])
        self.assertEqual(db.count_pending_sync_rows(), 0)

    def test_a_down_server_is_said_and_nothing_is_kept(self):
        error = api_client.ApiOfflineError("x", kind="server")
        with patch("sync_service.api_client.push_sync_records", side_effect=error):
            with self.assertRaises(db.AppError) as refused:
                self._sell()
        self.assertIn("server hozir ishlamayapti", str(refused.exception))
        self.assertEqual(db.get_product_by_barcode("W1")["stock"], 5)
        self.assertEqual(db.count_pending_sync_rows(), 0)

    def test_the_next_change_tries_again_after_a_failure(self):
        error = api_client.ApiOfflineError("x", kind="internet")
        with patch("sync_service.api_client.push_sync_records", side_effect=error):
            with self.assertRaises(db.AppError):
                self._sell()
        with patch("sync_service.api_client.push_sync_records",
                   return_value={"saved": 5, "generation": 1, "rejected": []}):
            self._sell()
        self.assertEqual(db.get_product_by_barcode("W1")["stock"], 4)


if __name__ == "__main__":
    unittest.main()
