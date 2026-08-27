"""Every row must carry an identifier no other device can invent.

Two devices used to hand the same integer to two different sales, and the
server -- which keys records by (account, table, local_id) -- kept only one of
them. These tests pin the replacement: UUIDs for identity, a deterministic
identifier for the account owner so cashier attribution means the same thing
everywhere, and a display number for the humans.
"""

import os
import shutil
import sqlite3
import tempfile
import unittest

import database as db


class RowIdentityTest(unittest.TestCase):
    def setUp(self):
        self.storage_root = tempfile.mkdtemp(prefix="marketstore-identity-")
        self.old_path = db.DB_PATH
        self.old_active_uid = db._ACTIVE_ACCOUNT_UID

    def tearDown(self):
        if db._ENGINE is not None:
            db._ENGINE.dispose()
        db._ENGINE = None
        db._ENGINE_PATH = None
        db._SessionLocal = None
        db.DB_PATH = self.old_path
        db._ACTIVE_ACCOUNT_UID = self.old_active_uid
        shutil.rmtree(self.storage_root, ignore_errors=True)

    def _open_account(self, user_uid, email, storage_root=None):
        db.activate_account_database(
            user_uid, email=email, storage_root=storage_root or self.storage_root
        )
        db.init_db(
            account_owner={"user_uid": user_uid, "email": email, "display_name": email.split("@", 1)[0]},
            seed_defaults=True,
        )
        return db.sync_online_user(email, role="admin", user_uid=user_uid)

    def _product(self, barcode="P1", stock=20):
        return db.add_product({
            "barcode": barcode, "name": f"Mahsulot {barcode}", "price": 1000,
            "cost": 600, "stock": stock, "unit": "dona",
        })

    # -- identity --------------------------------------------------------
    def test_new_rows_are_identified_by_a_uuid(self):
        self._open_account("acc-1", "owner@example.com")
        product_id = self._product()
        cashier_id = db.add_user("kassir@example.com", role="cashier", username="Kassir")
        sale_id = db.create_sale(
            None, cashier_id,
            [{"product_id": product_id, "quantity": 1, "price": 1000, "subtotal": 1000}],
            1000, 0, 1000, "naqd",
        )
        for value in (product_id, cashier_id, sale_id):
            self.assertTrue(db.is_row_uuid(value), value)

    def test_two_devices_never_mint_the_same_sale_id(self):
        """The whole point: independent devices cannot collide."""
        first_root = os.path.join(self.storage_root, "device-a")
        second_root = os.path.join(self.storage_root, "device-b")

        def sell(root):
            self._open_account("acc-shared", "owner@example.com", storage_root=root)
            product_id = self._product("SAME")
            cashier = db.add_user("k@example.com", role="cashier", username="K")
            return db.create_sale(
                None, cashier,
                [{"product_id": product_id, "quantity": 1, "price": 1000, "subtotal": 1000}],
                1000, 0, 1000, "naqd",
            )

        first_sale = sell(first_root)
        second_sale = sell(second_root)
        self.assertNotEqual(first_sale, second_sale)

    def test_the_account_owner_has_the_same_identifier_on_every_device(self):
        """What made cashier salary disagree between devices."""
        first_root = os.path.join(self.storage_root, "device-a")
        second_root = os.path.join(self.storage_root, "device-b")

        self._open_account("acc-shared", "owner@example.com", storage_root=first_root)
        # Push the second device's own numbering out of step, the way a device
        # that was set up later really is.
        db.add_user("one@example.com", role="cashier", username="One")
        db.add_user("two@example.com", role="cashier", username="Two")
        first_owner = next(u["id"] for u in db.get_users() if u["email"] == "owner@example.com")

        self._open_account("acc-shared", "owner@example.com", storage_root=second_root)
        second_owner = next(u["id"] for u in db.get_users() if u["email"] == "owner@example.com")

        self.assertEqual(first_owner, second_owner)
        self.assertEqual(first_owner, db.owner_row_id("acc-shared"))

    def test_seeded_rows_match_across_devices(self):
        """Both devices seed their own currencies; they must be one row, not two."""
        first_root = os.path.join(self.storage_root, "device-a")
        second_root = os.path.join(self.storage_root, "device-b")

        self._open_account("acc-shared", "owner@example.com", storage_root=first_root)
        first = {row["code"]: row["id"] for row in db.get_currencies()}

        self._open_account("acc-shared", "owner@example.com", storage_root=second_root)
        second = {row["code"]: row["id"] for row in db.get_currencies()}

        self.assertEqual(first, second)

    # -- the number a cashier reads --------------------------------------
    def test_the_sale_display_number_counts_up_and_is_not_the_identity(self):
        self._open_account("acc-1", "owner@example.com")
        product_id = self._product(stock=50)
        cashier = db.add_user("k@example.com", role="cashier", username="K")
        numbers = []
        for _ in range(3):
            sale_id = db.create_sale(
                None, cashier,
                [{"product_id": product_id, "quantity": 1, "price": 1000, "subtotal": 1000}],
                1000, 0, 1000, "naqd",
            )
            numbers.append(db.get_sale_display_no(sale_id))
        self.assertEqual(numbers, ["1", "2", "3"])

    # -- migration -------------------------------------------------------
    def test_an_integer_keyed_database_is_translated_without_data_loss(self):
        path = os.path.join(self.storage_root, "legacy.db")
        conn = sqlite3.connect(path)
        try:
            conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, role TEXT, email TEXT)")
            conn.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, barcode TEXT UNIQUE, name TEXT NOT NULL, price REAL DEFAULT 0, cost REAL DEFAULT 0, stock INTEGER DEFAULT 0, unit TEXT DEFAULT 'dona')")
            conn.execute("INSERT INTO users (id, username, password, role, email) VALUES (1, 'owner', 'x', 'admin', 'owner@example.com')")
            conn.execute("INSERT INTO products (id, barcode, name) VALUES (7, 'OLD', 'Eski mahsulot')")
            conn.commit()
        finally:
            conn.close()

        db._ENGINE = None
        db._ENGINE_PATH = None
        db.DB_PATH = path
        db.run_migrations()

        conn = sqlite3.connect(path)
        try:
            product = conn.execute("SELECT id, barcode, name FROM products").fetchone()
            id_type = next(row[2] for row in conn.execute("PRAGMA table_info(products)") if row[1] == "id")
            self.assertFalse(str(id_type).upper().startswith("INT"), id_type)
            flag = conn.execute(
                "SELECT value FROM sync_state WHERE key='identity_reset_required'"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(product[1:], ("OLD", "Eski mahsulot"))
        self.assertTrue(db.is_row_uuid(product[0]), product[0])
        self.assertEqual(flag[0], "1")

    def test_the_migration_keeps_the_owner_signed_in(self):
        path = os.path.join(self.storage_root, "legacy-owner.db")
        conn = sqlite3.connect(path)
        try:
            conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, role TEXT, email TEXT)")
            conn.execute("CREATE TABLE user_settings (user_id INTEGER, key TEXT, value TEXT, PRIMARY KEY (user_id, key))")
            conn.execute("CREATE TABLE sync_state (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
            conn.execute("INSERT INTO users (id, username, password, role, email) VALUES (4, 'owner', 'x', 'admin', 'owner@example.com')")
            conn.execute("INSERT INTO user_settings VALUES (4, 'api_user_uid', 'acc-9')")
            conn.execute("INSERT INTO user_settings VALUES (4, 'api_access_token', 'secret-token')")
            conn.execute("INSERT INTO sync_state VALUES ('account_user_uid', 'acc-9', '')")
            conn.commit()
        finally:
            conn.close()

        db._ENGINE = None
        db._ENGINE_PATH = None
        db.DB_PATH = path
        db.run_migrations()

        conn = sqlite3.connect(path)
        try:
            users = conn.execute("SELECT id, email FROM users").fetchall()
            token = conn.execute(
                "SELECT value FROM user_settings WHERE user_id=? AND key='api_access_token'",
                (db.owner_row_id("acc-9"),),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(users, [(db.owner_row_id("acc-9"), "owner@example.com")])
        self.assertEqual(token[0], "secret-token")

    # -- import guards ---------------------------------------------------
    def test_a_record_from_a_device_that_has_not_upgraded_is_refused(self):
        self._open_account("acc-1", "owner@example.com")
        imported = db.import_sync_records([{
            "table_name": "products",
            "local_id": "501",
            "data": {"id": 501, "barcode": "LEGACY", "name": "Eski", "price": 1, "cost": 1, "stock": 1},
        }])
        self.assertEqual(imported, 0)
        self.assertIsNone(db.get_product_by_barcode("LEGACY"))

    def test_a_name_clash_costs_one_row_not_the_whole_download(self):
        self._open_account("acc-1", "owner@example.com")
        db.add_category("Shirinliklar")
        incoming_id = db.stable_row_id("categories", "server-shirinliklar")
        other_id = db.stable_row_id("products", "kept")
        imported = db.import_sync_records([
            {"table_name": "categories", "local_id": incoming_id,
             "data": {"id": incoming_id, "name": "Shirinliklar"}},
            {"table_name": "products", "local_id": other_id,
             "data": {"id": other_id, "barcode": "KEPT", "name": "O'tdi",
                      "price": 10, "cost": 5, "stock": 1, "unit": "dona"}},
        ])
        self.assertEqual(imported, 2)
        names = [row["name"] for row in db.get_categories()]
        self.assertEqual(names.count("Shirinliklar"), 1)
        self.assertIsNotNone(db.get_product_by_barcode("KEPT"))


if __name__ == "__main__":
    unittest.main()
