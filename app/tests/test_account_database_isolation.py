import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import database as db
import sync_service


class AccountDatabaseIsolationTest(unittest.TestCase):
    def setUp(self):
        self.storage_root = tempfile.mkdtemp(prefix="marketstore-accounts-")
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

    def _open_account(self, user_uid, email):
        db.activate_account_database(user_uid, email=email, storage_root=self.storage_root)
        db.init_db(
            account_owner={"user_uid": user_uid, "email": email, "display_name": email.split("@", 1)[0]},
            seed_defaults=False,
        )
        return db.sync_online_user(email, role="admin", user_uid=user_uid)

    def test_products_and_cashiers_are_isolated_per_account(self):
        first_owner = self._open_account("account-one", "one@example.com")
        db.add_user("cashier.one@example.com", role="cashier", username="Cashier One")
        db.add_product({
            "barcode": "ONE-1",
            "name": "First account product",
            "price": 100,
            "cost": 50,
            "stock": 2,
            "unit": "dona",
        })
        first_user_records = [
            record for record in db.export_sync_records() if record["table_name"] == "users"
        ]
        self.assertEqual(
            [record["data"]["email"] for record in first_user_records],
            ["cashier.one@example.com"],
        )
        first_path = db.DB_PATH

        second_owner = self._open_account("account-two", "two@example.com")
        self.assertNotEqual(first_path, db.DB_PATH)
        self.assertNotEqual(first_owner["email"], second_owner["email"])
        self.assertEqual(db.get_all_products(), [])
        self.assertEqual([user["email"] for user in db.get_users()], ["two@example.com"])

        db.add_user("cashier.two@example.com", "secret2", role="cashier", username="Cashier Two")
        self._open_account("account-one", "one@example.com")
        self.assertEqual([product["barcode"] for product in db.get_all_products()], ["ONE-1"])
        self.assertEqual(
            {user["email"] for user in db.get_users()},
            {"one@example.com", "cashier.one@example.com"},
        )
        self.assertNotIn("cashier.two@example.com", {user["email"] for user in db.get_users()})

        users = {user["email"]: user for user in db.get_users()}
        with self.assertRaises(db.AppError):
            db.delete_user(users["one@example.com"]["id"])
        cashier_id = users["cashier.one@example.com"]["id"]
        db.delete_user(cashier_id)
        tombstones = [
            record
            for record in db.export_sync_records()
            if record["table_name"] == "users" and record["deleted_at"]
        ]
        self.assertEqual([record["local_id"] for record in tombstones], [str(cashier_id)])

    def test_same_email_keeps_local_database_when_server_uid_changes(self):
        self._open_account("original-server-uid", "stable@example.com")
        db.add_product({
            "barcode": "STABLE-1",
            "name": "Stable product",
            "price": 10,
            "cost": 5,
            "stock": 1,
            "unit": "dona",
        })
        original_path = db.DB_PATH

        owner = self._open_account("replacement-server-uid", "stable@example.com")
        self.assertEqual(db.DB_PATH, original_path)
        self.assertEqual([product["barcode"] for product in db.get_all_products()], ["STABLE-1"])
        self.assertTrue(db.is_server_reseed_required())
        with patch.object(
            sync_service.api_client,
            "push_sync_records",
            return_value={"saved": len(db.export_sync_records()), "batch_id": 1},
        ) as push:
            result = sync_service.synchronize_account_storage(
                {**dict(owner), "api_access_token": "replacement-token"}
            )
        self.assertEqual(result["direction"], "push")
        push.assert_called_once()
        self.assertFalse(db.is_server_reseed_required())

    def test_deleted_local_database_is_recreated_then_restored_from_snapshot(self):
        self._open_account("restore-uid", "restore@example.com")
        db.add_product({
            "barcode": "RESTORE-1",
            "name": "Server snapshot product",
            "price": 20,
            "cost": 8,
            "stock": 3,
            "unit": "dona",
        })
        server_snapshot = db.export_sync_records()
        database_path = db.DB_PATH

        db._ENGINE.dispose()
        db._ENGINE = None
        db._ENGINE_PATH = None
        db._SessionLocal = None
        for suffix in ("", "-shm", "-wal"):
            path = database_path + suffix
            if os.path.exists(path):
                os.remove(path)

        activation = db.activate_account_database(
            "restore-uid",
            email="restore@example.com",
            storage_root=self.storage_root,
        )
        self.assertTrue(activation["database_created"])
        db.init_db(
            account_owner={
                "user_uid": "restore-uid",
                "email": "restore@example.com",
                "display_name": "Restore",
            },
            seed_defaults=False,
        )
        owner = db.sync_online_user(
            "restore@example.com",
            role="admin",
            access_token="restore-token",
            user_uid="restore-uid",
        )
        db.mark_server_bootstrap_required()
        self.assertEqual(db.get_all_products(), [])
        with patch.object(
            sync_service.api_client,
            "pull_sync_records",
            return_value={"records": server_snapshot, "server_time": "now"},
        ) as pull:
            result = sync_service.synchronize_account_storage(
                {**dict(owner), "api_access_token": "restore-token"}
            )
        self.assertEqual(result["direction"], "pull")
        pull.assert_called_once()
        self.assertEqual([product["barcode"] for product in db.get_all_products()], ["RESTORE-1"])
        self.assertFalse(db.is_server_bootstrap_required())


if __name__ == "__main__":
    unittest.main()
