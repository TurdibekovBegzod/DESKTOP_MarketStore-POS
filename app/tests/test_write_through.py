"""A change is kept on this device only once the server has it.

Before, a sale was committed here first and uploaded by the sync engine a
moment later. Each step of the sale also asked the server whether it was
reachable - four or five round trips with the window frozen - and a link that
died in between still left the sale here, waiting. Now the action is one local
transaction and one upload, and that upload is the whole check: it either
reaches the server or the action is rolled back and the person is told why.
"""

import os
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

import api_client
import database as db
import sync_service


class WriteThroughTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="marketstore-write-through-")
        self.old_path = db.DB_PATH
        self.old_uid = db._ACTIVE_ACCOUNT_UID
        db.activate_account_database("acct-wt", email="owner@example.com", storage_root=self.root)
        db.init_db(
            account_owner={"user_uid": "acct-wt", "email": "owner@example.com",
                           "display_name": "Owner"},
            seed_defaults=False,
        )
        db.mark_upgrade_reconcile_complete()
        db.mark_identity_reset_complete()
        db.mark_server_reseed_complete()
        self.product = db.add_product({"barcode": "P1", "name": "Mahsulot", "price": 1000,
                                       "cost": 600, "stock": 5, "unit": "dona"})
        self.cashier = db.add_user("k@example.com", role="cashier", username="K")
        db.mark_sync_pushed()

        # Another test's window may have left its signed-in user as the author
        # of activity entries; that user does not exist in this database.
        db.set_activity_actor(None)
        self.uploads = []
        self.committed = []
        self.announced = []
        self.failure = None
        db.set_write_through(self._deliver, after_commit=self.committed.append)
        db.add_change_listener(self._announce)

    def tearDown(self):
        db.set_write_through(None)
        db.remove_change_listener(self._announce)
        if db._ENGINE is not None:
            db._ENGINE.dispose()
        db._ENGINE = None
        db._ENGINE_PATH = None
        db._SessionLocal = None
        db.DB_PATH = self.old_path
        db._ACTIVE_ACCOUNT_UID = self.old_uid
        shutil.rmtree(self.root, ignore_errors=True)

    def _announce(self):
        self.announced.append(1)

    def _deliver(self, records):
        self.uploads.append([(r["table_name"], r["local_id"]) for r in records])
        if self.failure is not None:
            raise self.failure
        return {"saved": len(records), "generation": 7, "rejected": []}

    def _sell(self):
        return db.create_sale(None, self.cashier, [{"product_id": self.product, "quantity": 1,
                                                    "price": 1000, "subtotal": 1000}],
                              1000, 0, 1000, "naqd")

    def test_a_sale_is_one_upload_with_everything_in_it(self):
        self._sell()

        self.assertEqual(len(self.uploads), 1)
        tables = {table for table, _ in self.uploads[0]}
        self.assertTrue({"sales", "sale_items", "products", "stock_movements",
                         "activity_logs"} <= tables, tables)
        self.assertEqual(db.get_product_by_barcode("P1")["stock"], 4)
        # Delivered, so nothing is left for the engine to send again.
        self.assertEqual(db.count_pending_sync_rows(), 0)
        self.assertEqual(self.announced, [])
        self.assertEqual(self.committed, [{"saved": len(self.uploads[0]), "generation": 7,
                                           "rejected": [], "sent": len(self.uploads[0])}])

    def test_no_internet_keeps_nothing_and_says_so(self):
        self.failure = db.AppError(sync_service.NO_INTERNET_MESSAGE)

        with self.assertRaises(db.AppError) as refused:
            self._sell()

        self.assertIn("Internet ishlamayapti", str(refused.exception))
        self.assertEqual(db.get_product_by_barcode("P1")["stock"], 5)
        self.assertEqual(db.get_product_sales_archive(""), [])
        self.assertEqual(db.count_pending_sync_rows(), 0)
        self.assertEqual(self.committed, [])
        self.assertEqual(self.announced, [])

    def test_an_unanswered_upload_is_kept_and_queued_not_lost_or_doubled(self):
        self.failure = db.DeliveryUncertain("no answer")

        self._sell()

        self.assertEqual(db.get_product_by_barcode("P1")["stock"], 4)
        self.assertGreater(db.count_pending_sync_rows(), 0)
        self.assertEqual(self.announced, [1])
        self.assertEqual(self.committed, [])

    def test_the_first_step_is_not_committed_on_its_own(self):
        """pysqlite would let the first SAVEPOINT open the transaction, and
        releasing it would then commit - the outer BEGIN prevents that."""
        self.failure = db.AppError("down")
        with self.assertRaises(db.AppError):
            with db.server_action():
                db.add_customer("Birinchi", "+99890", "a@example.com")
                db.add_customer("Ikkinchi", "+99891", "b@example.com")
        self.assertEqual(db.get_all_customers(), [])

    def test_a_failed_step_is_undone_alone_and_not_uploaded(self):
        with db.server_action():
            db.add_customer("Qoladi", "+99890", "a@example.com")
            try:
                with db.session_scope() as session:
                    session.add(db.Customer(name="Qolmaydi", phone="+99891"))
                    session.flush()
                    raise RuntimeError("step failed")
            except RuntimeError:
                pass

        self.assertEqual([row["name"] for row in db.get_all_customers()], ["Qoladi"])
        self.assertEqual(len(self.uploads), 1)
        self.assertEqual({table for table, _ in self.uploads[0]}, {"customers"})
        self.assertEqual(len(self.uploads[0]), 1)

    def test_a_step_whose_flush_fails_does_not_spoil_the_action(self):
        """An activity entry that cannot be written (here: its author does not
        exist) is best effort - the sale it describes must still go through."""
        with patch.object(db, "_current_actor", return_value={"id": "no-such-user", "name": "X"}):
            self._sell()
        self.assertEqual(db.get_product_by_barcode("P1")["stock"], 4)
        self.assertEqual(len(self.uploads), 1)
        self.assertNotIn("activity_logs", {table for table, _ in self.uploads[0]})

    def test_another_thread_writing_mid_action_does_not_break_it(self):
        """WAL refuses at once to turn a transaction that has read into one
        that writes, if another connection committed in between - the sync
        engine does that all the time. The action therefore takes the write
        lock when it starts, and the other writer waits for it."""
        done = threading.Event()

        def other_writer():
            db.set_write_through(None)  # this thread's write is not under test
            db.record_sync_failure(RuntimeError("engine turn"))
            done.set()

        with db.server_action():
            db.get_product_by_barcode("P1")
            thread = threading.Thread(target=other_writer)
            thread.start()
            thread.join(0.5)
            db.add_customer("Mijoz", "+99890", "a@example.com")
        db.set_write_through(self._deliver, after_commit=self.committed.append)
        thread.join(10)

        self.assertTrue(done.is_set())
        self.assertEqual([row["name"] for row in db.get_all_customers()], ["Mijoz"])

    def test_an_error_inside_the_action_uploads_nothing(self):
        with self.assertRaises(RuntimeError):
            with db.server_action():
                db.add_customer("Yo'q", "+99890", "a@example.com")
                raise RuntimeError("boom")
        self.assertEqual(self.uploads, [])
        self.assertEqual(db.get_all_customers(), [])

    def test_a_single_write_outside_an_action_is_delivered_too(self):
        db.add_category("Kategoriya")
        self.assertEqual(len(self.uploads), 1)
        self.assertEqual(db.count_pending_sync_rows(), 0)

    def test_reads_never_upload(self):
        db.get_all_customers()
        db.get_product_by_barcode("P1")
        self.assertEqual(self.uploads, [])

    def test_a_pending_migration_goes_the_old_way(self):
        with db._get_engine().begin() as conn:
            db._sync_state_set(conn, "server_reseed_required", "1")
        self.assertFalse(db.write_through_ready())

        db.add_category("Kategoriya")

        self.assertEqual(self.uploads, [])
        self.assertEqual(self.announced, [1])
        self.assertGreater(db.count_pending_sync_rows(), 0)

    def test_without_write_through_changes_are_queued_as_before(self):
        db.set_write_through(None)
        db.add_category("Kategoriya")
        self.assertEqual(self.uploads, [])
        self.assertEqual(self.announced, [1])
        self.assertGreater(db.count_pending_sync_rows(), 0)


class DeliveryMessagesTest(unittest.TestCase):
    """The one upload tells the person what is wrong, in their words."""

    user = {"id": 1, "api_access_token": "tok"}

    def _deliver_failing(self, error):
        with patch.object(api_client, "push_sync_records", side_effect=error), \
             patch.object(db, "get_sync_device_key", return_value="dev"), \
             patch.object(db, "get_applied_purge_generation", return_value=0):
            return sync_service.deliver_records(self.user, [{"table_name": "sales"}])

    def test_no_internet(self):
        with self.assertRaises(db.AppError) as caught:
            self._deliver_failing(api_client.ApiOfflineError("x", kind="internet"))
        self.assertEqual(str(caught.exception), sync_service.NO_INTERNET_MESSAGE)

    def test_server_down(self):
        with self.assertRaises(db.AppError) as caught:
            self._deliver_failing(api_client.ApiOfflineError("x", kind="server"))
        self.assertEqual(str(caught.exception), sync_service.SERVER_DOWN_MESSAGE)

    def test_no_answer_is_uncertain_not_a_failure(self):
        with self.assertRaises(db.DeliveryUncertain):
            self._deliver_failing(api_client.ApiOfflineError("x", kind="uncertain"))

    def test_one_request_with_a_quick_connect_and_a_longer_answer(self):
        with patch.object(api_client, "push_sync_records", return_value={"generation": 3}) as push, \
             patch.object(db, "get_applied_purge_generation", return_value=0):
            sync_service.deliver_records(self.user, [{"table_name": "sales"}])
        push.assert_called_once()
        self.assertEqual(push.call_args.kwargs["timeout"],
                         (sync_service.DELIVERY_CONNECT_SECONDS,
                          sync_service.DELIVERY_RESPONSE_SECONDS))
        self.assertIsNone(push.call_args.kwargs["expected_generation"])


class DeliveryRetryTest(unittest.TestCase):
    """A lost answer is asked for again; it never turns into a double sale."""

    user = {"id": 1, "api_access_token": "tok"}

    def setUp(self):
        self.now = 0.0
        self.slept = []

    def _clock(self):
        return self.now

    def _sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds

    def _deliver(self, outcomes, cost=0.5):
        """``cost`` None: every attempt runs out its whole timeout."""
        calls = []

        def push(*_args, **kwargs):
            calls.append(kwargs["timeout"])
            self.now += sum(kwargs["timeout"]) if cost is None else cost
            outcome = outcomes[len(calls) - 1]
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        with patch.object(api_client, "push_sync_records", side_effect=push), \
             patch.object(db, "get_applied_purge_generation", return_value=0):
            try:
                return sync_service.deliver_records(
                    self.user, [{"table_name": "sales"}], clock=self._clock, sleep=self._sleep
                ), calls
            except Exception as exc:
                exc.calls = calls
                raise

    @staticmethod
    def _offline(kind):
        return api_client.ApiOfflineError("x", kind=kind)

    def test_a_lost_answer_is_asked_again_and_the_second_answer_counts(self):
        answer, calls = self._deliver([self._offline("uncertain"), {"generation": 4}])
        self.assertEqual(answer, {"generation": 4})
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.slept, [1])

    def test_no_internet_on_the_first_try_is_said_at_once(self):
        with self.assertRaises(db.AppError) as caught:
            self._deliver([self._offline("internet")])
        self.assertEqual(len(caught.exception.calls), 1)
        self.assertEqual(self.slept, [])

    def test_saved_then_the_server_went_down_is_kept_not_refused(self):
        """The first attempt may have been stored. A later "server is down"
        must not roll the sale back, or it is rung up twice."""
        with self.assertRaises(db.DeliveryUncertain):
            self._deliver([self._offline("uncertain"), self._offline("server"),
                           self._offline("internet")])

    def test_it_never_freezes_the_window_past_the_deadline(self):
        with self.assertRaises(db.DeliveryUncertain) as caught:
            self._deliver([self._offline("uncertain")] * 3, cost=None)
        self.assertLessEqual(self.now, sync_service.DELIVERY_DEADLINE_SECONDS)
        # A first attempt that ran its full time still leaves room for one more.
        self.assertEqual(len(caught.exception.calls), 2)


class FinishDeliveryTest(unittest.TestCase):
    def test_our_own_change_is_adopted_without_a_download(self):
        with patch.object(db, "get_sync_generation", return_value=6), \
             patch.object(sync_service, "_apply_generation") as adopt:
            self.assertFalse(sync_service.finish_delivery({"generation": 7}))
        adopt.assert_called_once_with(7)

    def test_someone_elses_change_in_between_asks_for_a_download(self):
        with patch.object(db, "get_sync_generation", return_value=6), \
             patch.object(sync_service, "_apply_generation") as adopt:
            self.assertTrue(sync_service.finish_delivery({"generation": 9}))
        adopt.assert_not_called()


if __name__ == "__main__":
    unittest.main()
