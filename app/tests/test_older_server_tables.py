"""One table the server has not heard of must not stop every other change.

The API validates a push as a whole, so a single row from a table an older
server build does not know turns the entire batch - the sale, the product, the
section - into a 422. The shop then looks online, keeps taking money, and
nothing at all reaches the other devices.

So the desktop remembers which tables this server refuses, sends everything
else immediately, and keeps the refused rows queued for the day the API is
brought up to date.
"""

import os
import tempfile
import unittest

import api_client
import database as db
import sync_service


class OlderServerTablesTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.old_path = db.DB_PATH
        db.DB_PATH = self.path
        db.init_db()
        db.mark_server_bootstrap_complete()
        # init_db seeds reference rows, which queue themselves like any other
        # write. Start from an empty queue so each test only sees its own rows.
        with db._get_engine().begin() as conn:
            db._ensure_sync_outbox_table(conn)
            conn.exec_driver_sql("DELETE FROM sync_outbox")
        self._real_push = api_client.push_sync_records
        self._real_state = api_client.get_sync_state
        self._real_server_state = sync_service.get_server_state
        self._real_reconcile = sync_service.reconcile_after_upgrade
        db._UNSENDABLE_TABLES.clear()

    def tearDown(self):
        api_client.push_sync_records = self._real_push
        api_client.get_sync_state = self._real_state
        sync_service.get_server_state = self._real_server_state
        sync_service.reconcile_after_upgrade = self._real_reconcile
        db._UNSENDABLE_TABLES.clear()
        try:
            db._get_engine().dispose()
        finally:
            db.DB_PATH = self.old_path
            for suffix in ("", "-shm", "-wal"):
                path = self.path + suffix
                if os.path.exists(path):
                    os.remove(path)

    def _serve(self, refused):
        """Stand in for an API build that only knows some of our tables."""
        self.batches = []

        def push(token, records, **kwargs):
            self.batches.append(sorted({record["table_name"] for record in records}))
            unknown = {record["table_name"] for record in records} & set(refused)
            if unknown:
                raise api_client.UnsupportedSyncTableError("older server", tables=unknown)
            return {"saved": len(records), "batch_id": 1, "generation": 5, "rejected": []}

        api_client.push_sync_records = push
        api_client.get_sync_state = lambda token, timeout=15: {"generation": 5}
        sync_service.get_server_state = lambda user: {"local_purge_applied": False}
        sync_service.reconcile_after_upgrade = lambda user: None

    def _queue(self, entries):
        with db._get_engine().begin() as conn:
            db._ensure_sync_outbox_table(conn)
            db._write_outbox_entries(conn, entries)

    def _outbox_tables(self):
        with db._get_engine().begin() as conn:
            return sorted(
                row[0]
                for row in conn.exec_driver_sql("SELECT table_name FROM sync_outbox").fetchall()
            )

    def test_refused_table_does_not_block_the_rest(self):
        self._serve(refused={"activity_logs"})
        with db._get_engine().begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO activity_logs (id, action, title, message) "
                "VALUES ('a-1', 'test', 'Sarlavha', 'xabar')"
            )
            conn.exec_driver_sql("INSERT INTO product_sections (id, name) VALUES ('sec-1', 'Bo''lim')")
        self._queue([("activity_logs", "a-1", "upsert"), ("product_sections", "sec-1", "upsert")])

        outcome = sync_service.push_local_changes({"id": 1, "api_access_token": "t"}, guard_generation=False)

        # The section travelled even though the batch also held a refused row.
        self.assertGreater(outcome["sent"], 0)
        self.assertNotIn("product_sections", self._outbox_tables())
        # The refused row is remembered, not thrown away.
        self.assertIn("activity_logs", db.get_unsendable_tables())
        self.assertIn("activity_logs", self._outbox_tables())
        # Two attempts: the batch as queued, then the batch the server accepts.
        self.assertEqual(len(self.batches), 2)

    def test_refused_rows_stop_counting_as_pending_work(self):
        """Otherwise the worker spins, and no "saved on the server" notice closes."""
        self._serve(refused={"activity_logs"})
        db.set_unsendable_tables(["activity_logs"])
        self._queue([("activity_logs", "a-1", "upsert")])

        self.assertEqual(db.count_pending_sync_rows(), 0)
        self.assertEqual(int(db.get_sync_status()["pending_change_count"]), 0)
        # Still queued, so an upgraded server receives it later.
        self.assertEqual(self._outbox_tables(), ["activity_logs"])

    def test_second_push_reaches_the_server_in_one_attempt(self):
        self._serve(refused={"activity_logs"})
        db.set_unsendable_tables(["activity_logs"])
        with db._get_engine().begin() as conn:
            conn.exec_driver_sql("INSERT INTO product_sections (id, name) VALUES ('sec-2', 'Ikki')")
        self._queue([("product_sections", "sec-2", "upsert")])

        sync_service.push_local_changes({"id": 1, "api_access_token": "t"}, guard_generation=False)

        self.assertEqual(self.batches, [["product_sections"]])


if __name__ == "__main__":
    unittest.main()
