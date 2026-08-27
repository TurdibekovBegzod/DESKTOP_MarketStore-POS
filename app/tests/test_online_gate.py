"""Money is only written while this device can actually reach the others.

A sale or an expense written with no way to reach the server cannot be
reconciled with what the other devices did in the meantime, so it is refused
rather than written and argued about afterwards. The rule has to be strict in
the right direction: "not known yet" counts as offline, because writing money
on a guess is the thing being avoided.
"""

import os
import shutil
import tempfile
import unittest

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

    def test_a_recent_exchange_carries_a_reconnecting_stream(self):
        """A stream that is merely reconnecting must not stop a sale."""
        self.window._realtime_online = None
        self.window._engine_state = "idle"
        db.record_sync_success({"pulled": 0, "pushed": 1})
        self.assertTrue(self.window._is_online())

    def test_the_engine_reporting_offline_overrules_a_recent_exchange(self):
        db.record_sync_success({"pulled": 0, "pushed": 1})
        self.window._realtime_online = None
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
        self.assertIn("Internet", str(refused.exception))

        # And nothing was written, so nothing is waiting to be sent later.
        self.assertEqual(db.get_product_by_barcode("P1")["stock"], 5)
        self.assertEqual(db.get_product_sales_archive(""), [])

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
