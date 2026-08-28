"""A shop must always have a currency list.

The superadmin panel can clear an account. Every device then empties its
synchronised tables to match the server - currencies included - and nothing
ever put them back. The price fields opened empty, "So'mda" showed a rate of
1.00, and no product could be priced in dollars again until someone re-typed
the whole list by hand.
"""

import os
import tempfile
import unittest

import database as db


class ReferenceRowsSurvivePurgeTest(unittest.TestCase):
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

    def _codes(self):
        return sorted(dict(row)["code"] for row in db.get_currencies())

    def test_the_three_currencies_are_seeded(self):
        self.assertEqual(self._codes(), ["EUR", "USD", "UZS"])

    def test_a_server_purge_leaves_the_currency_list_standing(self):
        db.apply_remote_purge(48, server_generation=52)

        self.assertEqual(self._codes(), ["EUR", "USD", "UZS"])
        # Re-seeding is not a user edit, so it must not queue anything to push.
        self.assertEqual(db.count_pending_sync_rows(), 0)

    def test_a_wholesale_download_leaves_the_currency_list_standing(self):
        db.replace_local_from_records([])

        self.assertEqual(self._codes(), ["EUR", "USD", "UZS"])

    def test_an_edited_rate_is_not_overwritten(self):
        db.save_currency("USD", "AQSh dollari", 13000)
        db.ensure_reference_rows()

        self.assertEqual(dict(db.get_currency("USD"))["rate_to_uzs"], 13000)


if __name__ == "__main__":
    unittest.main()
