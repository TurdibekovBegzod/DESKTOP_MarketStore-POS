"""The change queue must never drop a change.

Once the manual "Yuborish" button is gone, this queue is the only thing that
gets a sale to the other devices. A change that falls out of it is not a
delayed sale - it is a sale the rest of the shop never sees.
"""

import os
import tempfile
import threading
import time
import unittest

import database as db


class SyncOutboxTest(unittest.TestCase):
    def setUp(self):
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

    def _queued(self):
        with db._get_engine().begin() as conn:
            db._ensure_sync_outbox_table(conn)
            return [
                (row[0], row[1], row[2])
                for row in conn.exec_driver_sql(
                    "SELECT seq, table_name, local_id FROM sync_outbox ORDER BY seq"
                ).fetchall()
            ]

    def _product(self, barcode):
        return db.add_product({
            "name": f"Mahsulot {barcode}", "barcode": barcode,
            "price": 1000, "cost": 600, "quantity": 0, "stock": 5,
        })

    def test_every_queued_change_carries_a_sequence_number(self):
        self._product("s1")
        queued = self._queued()
        self.assertTrue(queued)
        self.assertTrue(all(isinstance(seq, int) for seq, _, _ in queued))
        self.assertEqual([seq for seq, _, _ in queued], sorted(seq for seq, _, _ in queued))

    def test_a_change_made_during_a_push_is_not_cleared_with_it(self):
        """The lost-update window that made the old queue unsafe.

        mark_sync_pushed() used to empty the whole table, so anything a cashier
        rang up while the upload was in flight was deleted unsent.
        """
        first = self._product("s1")
        records, watermark = db.export_sync_records(incremental=True, with_watermark=True)
        self.assertTrue(records)

        # ... the upload is in flight; a sale happens now.
        second = self._product("s2")

        db.mark_sync_pushed(**watermark)

        remaining = {(table, local_id) for _, table, local_id in self._queued()}
        self.assertIn(("products", str(second)), remaining, "yangi yozuv yo'qoldi")
        self.assertNotIn(("products", str(first)), remaining, "yuborilgani tozalanmadi")

    def test_the_pending_count_matches_what_is_still_queued(self):
        self._product("s1")
        records, watermark = db.export_sync_records(incremental=True, with_watermark=True)
        self._product("s2")
        db.mark_sync_pushed(**watermark)

        status = db.get_sync_status()
        self.assertEqual(status["pending_change_count"], len(self._queued()))
        self.assertTrue(status["pending"])

    def test_a_full_push_clears_the_whole_queue(self):
        self._product("s1")
        db.mark_sync_pushed()
        self.assertEqual(self._queued(), [])
        self.assertEqual(db.get_sync_status()["pending_change_count"], 0)

    def test_a_queue_from_an_older_build_is_migrated_not_dropped(self):
        with db._get_engine().begin() as conn:
            conn.exec_driver_sql("DROP TABLE IF EXISTS sync_outbox")
            conn.exec_driver_sql("""
                CREATE TABLE sync_outbox (
                    table_name TEXT NOT NULL,
                    local_id TEXT NOT NULL,
                    action TEXT NOT NULL DEFAULT 'upsert',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (table_name, local_id)
                )
            """)
            conn.exec_driver_sql(
                "INSERT INTO sync_outbox VALUES ('products', '777', 'upsert', '2026-01-01 00:00:00')"
            )

        with db._get_engine().begin() as conn:
            db._ensure_sync_outbox_table(conn)

        queued = self._queued()
        self.assertIn(("products", "777"), [(table, local_id) for _, table, local_id in queued])
        self.assertTrue(all(isinstance(seq, int) for seq, _, _ in queued))

    def test_suspending_sync_on_one_thread_does_not_silence_another(self):
        """The race that dropped sales during an import.

        _SYNC_SUSPENDED was a process-wide flag. While the sync worker held it
        for an import, a sale committed on the GUI thread never entered the
        queue and was never pushed.
        """
        db.mark_sync_pushed()
        importing = threading.Event()
        release = threading.Event()

        def importer():
            with db.suspend_sync():
                importing.set()
                release.wait(timeout=5)

        worker = threading.Thread(target=importer)
        worker.start()
        self.assertTrue(importing.wait(timeout=5))

        product_id = self._product("during-import")

        release.set()
        worker.join(timeout=5)

        queued = {(table, local_id) for _, table, local_id in self._queued()}
        self.assertIn(("products", str(product_id)), queued)

    def test_a_suspended_thread_still_queues_nothing_of_its_own(self):
        db.mark_sync_pushed()
        with db.suspend_sync():
            self._product("silent")
        self.assertEqual(self._queued(), [])

    def test_merging_a_user_queues_every_row_it_rewrote(self):
        """Reassignment used bulk UPDATEs, which never reach the flush hooks.

        The rows changed owner locally and stayed that way: other devices kept
        attributing those sales and expenses to the old user.
        """
        source = db.add_user(email="eski@shop.uz", password="parol123",
                             role="cashier", username="Eski")
        target = db.add_user(email="yangi@shop.uz", password="parol123",
                             role="cashier", username="Yangi")
        product_id = self._product("m1")
        sale_id = db.create_sale(
            None, source,
            [{"product_id": product_id, "quantity": 1, "price": 1000, "subtotal": 1000}],
            total=1000, discount=0, paid=1000, payment_method="naqd", is_finalized=0,
        )
        categories = {row["name"]: row["id"] for row in db.get_expense_categories()}
        expense_id = db.add_expense(categories["Kassir"], 500, "UZS", "avans", None, source)

        db.mark_sync_pushed()
        with db.session_scope() as session:
            db._reassign_user_references(session, source, target)

        queued = {(table, local_id) for _, table, local_id in self._queued()}
        self.assertIn(("sales", str(sale_id)), queued)
        self.assertIn(("expenses", str(expense_id)), queued)


if __name__ == "__main__":
    unittest.main()
