"""The worker that syncs without being asked.

It has to be conservative about when it runs: a turn on every tick would hammer
the server, and a turn that never runs leaves the device stale. These pin the
three reasons it acts -- the server said something changed, this device wrote
something, or nothing has been checked in a long while -- and the reasons it
stays quiet.
"""

import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

import database as db
import sync_engine


_app = QApplication.instance() or QApplication([])


class _Recorder:
    def __init__(self):
        self.applied = []
        self.states = []
        self.conflicts = 0


class SyncEngineTest(unittest.TestCase):
    def setUp(self):
        self.engine = sync_engine.SyncEngine(lambda: {"id": "u1"})
        self.engine.start()
        self.recorder = _Recorder()
        self.engine.applied.connect(self.recorder.applied.append)
        self.engine.state_changed.connect(self.recorder.states.append)
        self.engine.conflict.connect(lambda: setattr(
            self.recorder, "conflicts", self.recorder.conflicts + 1
        ))

    def tearDown(self):
        self.engine.stop()

    @staticmethod
    def _status(pending):
        return {"pending_change_count": pending}

    def test_it_stays_quiet_between_check_ins(self):
        self.engine._last_turn_at = sync_engine.time.monotonic()
        with patch.object(sync_engine.sync_service, "reconcile_full", return_value={}), \
             patch.object(db, "count_pending_sync_rows", return_value=0), \
             patch.object(sync_engine.sync_service, "auto_sync_turn") as turn:
            self.engine._tick()

        turn.assert_not_called()
        self.assertEqual(self.recorder.applied, [])

    def test_a_listening_device_still_asks_the_server_for_news(self):
        """The change stream goes quiet; a device that only listens must not.

        With nothing of its own to send and no event to act on, a device used
        to do nothing at all -- so work done elsewhere never arrived and the
        whole arrangement looked dead from that desk.
        """
        self.engine._last_turn_at = (
            sync_engine.time.monotonic() - sync_engine.IDLE_PULL_SECONDS - 1
        )
        with patch.object(sync_engine.sync_service, "reconcile_full", return_value={}), \
             patch.object(db, "count_pending_sync_rows", return_value=0), \
             patch.object(db, "record_sync_success"), \
             patch.object(sync_engine.sync_service, "auto_sync_turn",
                          return_value={"pulled": 1, "pushed": 0, "tables": ["sales"]}) as turn:
            self.engine._tick()

        turn.assert_called_once()
        self.assertEqual(self.recorder.applied[0]["pulled"], 1)

    def test_a_failure_is_written_down_rather_than_swallowed(self):
        self.engine.request_turn()
        with patch.object(sync_engine.sync_service, "reconcile_full", return_value={}), \
             patch.object(db, "count_pending_sync_rows", return_value=0), \
             patch.object(db, "record_sync_failure") as noted, \
             patch.object(sync_engine.sync_service, "auto_sync_turn",
                          side_effect=OSError("tunnel down")):
            self.engine._tick()

        noted.assert_called_once()

    def test_a_local_write_is_enough_to_make_it_run(self):
        with patch.object(sync_engine.sync_service, "reconcile_full", return_value={}), \
             patch.object(db, "count_pending_sync_rows", return_value=(3)), \
             patch.object(sync_engine.sync_service, "auto_sync_turn",
                          return_value={"pulled": 0, "pushed": 3, "tables": ["sales"]}) as turn, \
             patch.object(db, "record_sync_success"):
            self.engine._tick()

        turn.assert_called_once()
        self.assertEqual(self.recorder.applied[0]["pushed"], 3)

    def test_the_server_asking_is_enough_even_with_nothing_of_ours(self):
        self.engine.request_turn()
        with patch.object(sync_engine.sync_service, "reconcile_full", return_value={}), \
             patch.object(db, "count_pending_sync_rows", return_value=(0)), \
             patch.object(sync_engine.sync_service, "auto_sync_turn",
                          return_value={"pulled": 2, "pushed": 0, "tables": ["products"]}) as turn, \
             patch.object(db, "record_sync_success"):
            self.engine._tick()

        turn.assert_called_once()
        self.assertEqual(self.recorder.applied[0]["pulled"], 2)
        # The request is spent, not repeated on the next tick.
        with patch.object(sync_engine.sync_service, "reconcile_full", return_value={}), \
             patch.object(db, "count_pending_sync_rows", return_value=(0)), \
             patch.object(sync_engine.sync_service, "auto_sync_turn") as again:
            self.engine._tick()
        again.assert_not_called()

    def test_a_change_rearms_an_idle_worker_immediately(self):
        self.engine._timer.setInterval(sync_engine.IDLE_INTERVAL_MS)

        self.engine.request_turn()

        self.assertEqual(self.engine._timer.interval(), 1)
        self.assertTrue(self.engine._pull_requested.is_set())

    def test_a_turn_that_changed_nothing_says_nothing(self):
        self.engine.request_turn()
        with patch.object(sync_engine.sync_service, "reconcile_full", return_value={}), \
             patch.object(db, "count_pending_sync_rows", return_value=(0)), \
             patch.object(sync_engine.sync_service, "auto_sync_turn",
                          return_value={"pulled": 0, "pushed": 0, "tables": []}), \
             patch.object(db, "record_sync_success"):
            self.engine._tick()

        self.assertEqual(self.recorder.applied, [])

    def test_a_broken_connection_backs_off_instead_of_shouting(self):
        self.engine.request_turn()
        with patch.object(sync_engine.sync_service, "reconcile_full", return_value={}), \
             patch.object(db, "count_pending_sync_rows", return_value=(0)), \
             patch.object(sync_engine.sync_service, "auto_sync_turn",
                          side_effect=OSError("network down")), \
             patch.object(db, "record_sync_failure"):
            self.engine._tick()

        self.assertIn("offline", self.recorder.states)
        self.assertEqual(self.recorder.applied, [])
        self.assertEqual(self.engine._timer.interval(), sync_engine.RETRY_INTERVAL_MS)

    def test_a_conflict_is_handed_over_rather_than_retried_forever(self):
        self.engine.request_turn()
        with patch.object(sync_engine.sync_service, "reconcile_full", return_value={}), \
             patch.object(db, "count_pending_sync_rows", return_value=(1)), \
             patch.object(sync_engine.sync_service, "auto_sync_turn",
                          return_value={"pulled": 0, "pushed": 0, "conflict": True, "tables": []}), \
             patch.object(db, "record_sync_success"):
            self.engine._tick()

        self.assertEqual(self.recorder.conflicts, 1)
        self.assertEqual(self.recorder.applied, [])

    def test_it_does_nothing_without_a_signed_in_account(self):
        engine = sync_engine.SyncEngine(lambda: None)
        engine.start()
        try:
            with patch.object(sync_engine.sync_service, "auto_sync_turn") as turn:
                engine._tick()
            turn.assert_not_called()
        finally:
            engine.stop()


if __name__ == "__main__":
    unittest.main()
