"""Keeps this device in step with the others without anyone pressing a button.

One worker owns all automatic synchronisation. It runs a turn when the server
says something changed, shortly after this device writes something itself, and
every so often as a safety net in case the change stream is down.

Everything runs on a background thread and every turn goes through
``sync_service``, which serialises them, so a turn can never overlap the sync
button or another turn.
"""

import threading

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot

import database as db
import sync_service


# How long to wait after a local write before sending. A sale writes several
# rows in a burst; waiting a moment turns that burst into one upload.
LOCAL_SETTLE_MS = 700
# How often to look for work when nothing has told us to. Kept short: a sale
# rung up during a quiet spell must reach the other devices in a moment, not
# whenever the next long timer happens to come round. The check itself is one
# small read of one row.
IDLE_INTERVAL_MS = 2_000
# After a failed turn, wait before trying again rather than hammering a server
# that is already unhappy.
RETRY_INTERVAL_MS = 15_000


class SyncEngine(QObject):
    """Runs on its own QThread; performs one sync turn at a time."""

    applied = pyqtSignal(dict)
    state_changed = pyqtSignal(str)
    conflict = pyqtSignal()

    def __init__(self, user_provider, parent=None):
        super().__init__(parent)
        self._user_provider = user_provider
        self._timer = None
        self._stopping = threading.Event()
        self._pull_requested = threading.Event()
        self._state = "idle"

    # -- lifecycle -------------------------------------------------------
    @pyqtSlot()
    def start(self):
        self._timer = QTimer(self)
        self._timer.setInterval(LOCAL_SETTLE_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    @pyqtSlot()
    def stop(self):
        """Safe to call from the GUI thread.

        Only the flag is set here: the timer belongs to this worker's own
        thread, and Qt refuses to stop a timer from a different one. The next
        tick sees the flag and stops it from the right side.
        """
        self._stopping.set()

    @pyqtSlot()
    def request_turn(self):
        """Something changed -- here or on another device -- so act at once."""
        self._pull_requested.set()

    def notify_local_change(self):
        """Called from whichever thread just wrote to the database.

        Only a flag is set, which is safe from any thread; the work itself
        happens on this worker's own next tick.
        """
        self._pull_requested.set()

    # -- the loop --------------------------------------------------------
    def _tick(self):
        if self._stopping.is_set():
            if self._timer is not None:
                self._timer.stop()
            return
        user = None
        try:
            user = self._user_provider()
        except Exception:
            user = None
        if not user:
            return

        wanted = self._pull_requested.is_set()
        pending = 0
        try:
            pending = int(db.get_sync_status().get("pending_change_count") or 0)
        except Exception:
            pending = 0
        if not wanted and pending <= 0:
            # Nothing asked for and nothing of ours to send: check in rarely,
            # only so a device whose stream is down still catches up.
            self._set_interval(IDLE_INTERVAL_MS)
            return

        self._pull_requested.clear()
        self._set_state("syncing")
        try:
            outcome = sync_service.auto_sync_turn(user)
        except sync_service.SyncConflict:
            self._set_state("idle")
            self.conflict.emit()
            self._set_interval(RETRY_INTERVAL_MS)
            return
        except Exception:
            # Offline, a proxy hiccup, an expired token: none of these are
            # worth a message on the cashier's screen. The status indicator
            # already says the connection is down.
            self._set_state("offline")
            self._set_interval(RETRY_INTERVAL_MS)
            return

        self._set_state("idle")
        self._set_interval(LOCAL_SETTLE_MS)
        if outcome.get("conflict"):
            self.conflict.emit()
            return
        if outcome.get("pulled") or outcome.get("pushed") or outcome.get("settled"):
            self.applied.emit(dict(outcome))

    def _set_interval(self, milliseconds):
        if self._timer is not None and self._timer.interval() != milliseconds:
            self._timer.setInterval(milliseconds)

    def _set_state(self, state):
        if state != self._state:
            self._state = state
            self.state_changed.emit(state)
