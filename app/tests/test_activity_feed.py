"""What one device does, the others get told about.

The activity_logs table has existed since an early migration and nothing ever
wrote to it: activity lived in a Python list that emptied on every restart,
so a device had no way to tell another what its cashier had just done.
"""

import os
import shutil
import tempfile
import unittest

import database as db


class ActivityFeedTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="marketstore-activity-")
        self.old_path = db.DB_PATH
        self.old_uid = db._ACTIVE_ACCOUNT_UID
        db.activate_account_database("acct-act", email="owner@example.com", storage_root=self.root)
        db.init_db(
            account_owner={"user_uid": "acct-act", "email": "owner@example.com", "display_name": "Owner"},
            seed_defaults=False,
        )
        db.sync_online_user("owner@example.com", role="admin", user_uid="acct-act")
        self.cashier = db.add_user("sardor@example.com", role="cashier", username="Sardor")
        db.set_activity_actor(lambda: {"id": self.cashier, "name": "Sardor"})
        # Creating the cashier logged an entry of its own; start from empty so
        # each test only sees what it wrote.
        db.clear_activity_log()
        db.clear_session_notifications()
        db.mark_sync_pushed()

    def tearDown(self):
        db.set_activity_actor(None)
        if db._ENGINE is not None:
            db._ENGINE.dispose()
        db._ENGINE = None
        db._ENGINE_PATH = None
        db._SessionLocal = None
        db.DB_PATH = self.old_path
        db._ACTIVE_ACCOUNT_UID = self.old_uid
        shutil.rmtree(self.root, ignore_errors=True)

    # -- it is written down at all ---------------------------------------
    def test_an_entry_survives_a_restart(self):
        db.log_activity("sale_created", "Lenovo Ideapad sotildi", "1 dona", target="sales")
        db.clear_session_notifications()

        entries = db.get_recent_activities()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["title"], "Lenovo Ideapad sotildi")
        self.assertEqual(entries[0]["user_name"], "Sardor")
        self.assertTrue(db.is_row_uuid(entries[0]["id"]))

    def test_it_records_who_did_it_and_where(self):
        db.log_activity("sale_created", "Sotuv", "Izoh", target="sales")

        entry = db.get_recent_activities()[0]
        self.assertEqual(entry["user_id"], self.cashier)
        self.assertEqual(entry["device_key"], db.get_sync_device_key())

    def test_the_entries_travel_to_the_other_devices(self):
        db.log_activity("sale_created", "Sotuv", "Izoh", target="sales")

        records = [r for r in db.export_sync_records() if r["table_name"] == "activity_logs"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["data"]["title"], "Sotuv")

    # -- being told, once, and not about yourself -------------------------
    def test_a_device_is_not_told_what_it_did_itself(self):
        db.take_new_remote_activities()
        db.log_activity("sale_created", "Men sotdim", "Izoh", target="sales")

        self.assertEqual(db.take_new_remote_activities(), [])

    def test_another_device_is_announced_once(self):
        db.take_new_remote_activities()
        row_id = db.stable_row_id("activity_logs", "remote-1")
        db.import_sync_records([{
            "table_name": "activity_logs",
            "local_id": row_id,
            "data": {
                "id": row_id, "user_id": None, "user_name": "Sardor",
                "device_key": "desktop-other", "action": "sale_created",
                "title": "Lenovo Ideapad sotdi", "message": "1 dona",
                "level": "success", "target": "sales", "badge": "Sotildi",
                "created_at": "2030-01-01 10:00:00",
            },
        }])

        first = db.take_new_remote_activities()
        second = db.take_new_remote_activities()

        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["title"], "Lenovo Ideapad sotdi")
        self.assertEqual(first[0]["user_name"], "Sardor")
        self.assertEqual(second, [], "an announcement must not repeat")

    def test_the_history_that_predates_us_is_not_announced_in_a_rush(self):
        """A device joining later must not toast a month of other people's work."""
        for index in range(5):
            row_id = db.stable_row_id("activity_logs", f"old-{index}")
            db.import_sync_records([{
                "table_name": "activity_logs", "local_id": row_id,
                "data": {"id": row_id, "user_name": "Sardor", "device_key": "desktop-other",
                         "action": "sale_created", "title": f"Eski {index}", "message": "",
                         "level": "info", "target": "sales", "badge": "Sotildi",
                         "created_at": "2020-01-01 10:00:00"},
            }])

        announced = db.take_new_remote_activities(limit=4)
        self.assertLessEqual(len(announced), 4)
        self.assertEqual(db.take_new_remote_activities(), [])

    # -- what has been read stays read ------------------------------------
    def test_read_notifications_are_remembered_past_a_restart(self):
        db.mark_notifications_as_read(["act_one", "act_two"], user_id=self.cashier)
        db.clear_session_notifications()

        remembered = db.get_read_notification_ids(user_id=self.cashier)
        self.assertIn("act_one", remembered)
        self.assertIn("act_two", remembered)
        # And they belong to that person alone.
        self.assertNotIn("act_one", db.get_read_notification_ids(user_id="somebody-else"))

    def test_the_log_does_not_grow_without_end(self):
        db.ACTIVITY_LOG_LIMIT, previous = 20, db.ACTIVITY_LOG_LIMIT
        try:
            for index in range(30):
                db.log_activity("noted", f"Yozuv {index}", "", target="sales")
            with db.session_scope() as session:
                from sqlalchemy import func, select
                total = session.scalar(select(func.count(db.ActivityLog.id)))
        finally:
            db.ACTIVITY_LOG_LIMIT = previous
        self.assertLessEqual(total, 21)


if __name__ == "__main__":
    unittest.main()
