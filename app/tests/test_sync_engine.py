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

    def test_it_stays_quiet_when_nothing_asked_and_nothing_is_pending(self):
        with patch.object(db, "get_sync_status", return_value=self._status(0)), \
             patch.object(sync_engine.sync_service, "auto_sync_turn") as turn:
            self.engine._tick()

        turn.assert_not_called()
        self.assertEqual(self.recorder.applied, [])

    def test_a_local_write_is_enough_to_make_it_run(self):
        with patch.object(db, "get_sync_status", return_value=self._status(3)), \
             patch.object(sync_engine.sync_service, "auto_sync_turn",
                          return_value={"pulled": 0, "pushed": 3, "tables": ["sales"]}) as turn:
            self.engine._tick()

        turn.assert_called_once()
        self.assertEqual(self.recorder.applied[0]["pushed"], 3)

    def test_the_server_asking_is_enough_even_with_nothing_of_ours(self):
        self.engine.request_turn()
        with patch.object(db, "get_sync_status", return_value=self._status(0)), \
             patch.object(sync_engine.sync_service, "auto_sync_turn",
                          return_value={"pulled": 2, "pushed": 0, "tables": ["products"]}) as turn:
            self.engine._tick()

        turn.assert_called_once()
        self.assertEqual(self.recorder.applied[0]["pulled"], 2)
        # The request is spent, not repeated on the next tick.
        with patch.object(db, "get_sync_status", return_value=self._status(0)), \
             patch.object(sync_engine.sync_service, "auto_sync_turn") as again:
            self.engine._tick()
        again.assert_not_called()

    def test_a_turn_that_changed_nothing_says_nothing(self):
        self.engine.request_turn()
        with patch.object(db, "get_sync_status", return_value=self._status(0)), \
             patch.object(sync_engine.sync_service, "auto_sync_turn",
                          return_value={"pulled": 0, "pushed": 0, "tables": []}):
            self.engine._tick()

        self.assertEqual(self.recorder.applied, [])

    def test_a_broken_connection_backs_off_instead_of_shouting(self):
        self.engine.request_turn()
        with patch.object(db, "get_sync_status", return_value=self._status(0)), \
             patch.object(sync_engine.sync_service, "auto_sync_turn",
                          side_effect=OSError("network down")):
            self.engine._tick()

        self.assertIn("offline", self.recorder.states)
        self.assertEqual(self.recorder.applied, [])
        self.assertEqual(self.engine._timer.interval(), sync_engine.RETRY_INTERVAL_MS)

    def test_a_conflict_is_handed_over_rather_than_retried_forever(self):
        self.engine.request_turn()
        with patch.object(db, "get_sync_status", return_value=self._status(1)), \
             patch.object(sync_engine.sync_service, "auto_sync_turn",
                          return_value={"pulled": 0, "pushed": 0, "conflict": True, "tables": []}):
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
