"""Keeps this device in step with the others without anyone pressing a button.

One worker owns all automatic synchronisation. It runs a turn when the server
says something changed, shortly after this device writes something itself, and
every so often as a safety net in case the change stream is down.

Everything runs on a background thread and every turn goes through
``sync_service``, which serialises them, so a turn can never overlap the sync
button or another turn.
"""

import threading
import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot

import database as db
import sync_service


# How long to wait after a local write before sending. A sale writes several
# rows in a burst; waiting a moment turns that burst into one upload.
LOCAL_SETTLE_MS = 700
# How often the worker wakes up at all.
IDLE_INTERVAL_MS = 2_000
# How long a device may go without asking the server for news.
#
# This is not a nicety. The change stream is what normally says "something
# moved", but it rides a long-lived HTTP connection through a tunnel and a
# proxy, and it does go quiet. A device that is only listening -- not selling,
# not editing -- would then sit there forever, and to the person looking at it
# the whole arrangement appears dead. So even with nothing to send and nobody
# asking, it checks in on this interval.
IDLE_PULL_SECONDS = 5
# After a failed turn, wait before trying again rather than hammering a server
# that is already unhappy.
RETRY_INTERVAL_MS = 15_000
# How often the device stops exchanging differences and compares itself against
# the server in full.
#
# Sending and receiving differences is fast but has no way of noticing a
# difference that was never recorded -- a queue entry lost to a crash, a
# database restored from a backup, a row written by a version that had a bug
# here. Nothing corrects that on its own, and the two devices stay apart for
# good. So the first turn after start-up, and one turn every so often after
# that, checks the whole picture instead.
FULL_RECONCILE_SECONDS = 900


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
        # Far enough in the past that the first tick checks in immediately.
        self._last_turn_at = 0.0
        # Never reconciled in this run, so the first turn is a full one.
        self._last_full_at = None

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
            # The queue itself, not the cached counter beside it: if the two
            # ever disagree, the queue is the one holding real work.
            pending = int(db.count_pending_sync_rows())
        except Exception:
            pending = 0
        due = (time.monotonic() - self._last_turn_at) >= IDLE_PULL_SECONDS
        if not wanted and pending <= 0 and not due:
            self._set_interval(IDLE_INTERVAL_MS)
            return

        self._pull_requested.clear()
        self._last_turn_at = time.monotonic()
        self._set_state("syncing")
        full_due = (
            self._last_full_at is None
            or (time.monotonic() - self._last_full_at) >= FULL_RECONCILE_SECONDS
        )
        healed = {}
        try:
            if full_due:
                healed = dict(sync_service.reconcile_full(user) or {})
                if not healed.get("deferred"):
                    self._last_full_at = time.monotonic()
                if healed.get("queued"):
                    # Rows the server had never been told about are back in the
                    # queue; the turn below is what actually delivers them.
                    self._pull_requested.set()
            outcome = sync_service.auto_sync_turn(user)
        except sync_service.SyncConflict as exc:
            db.record_sync_failure(exc)
            self._set_state("idle")
            self.conflict.emit()
            self._set_interval(RETRY_INTERVAL_MS)
            return
        except Exception as exc:
            # Not worth interrupting a cashier over -- but it must not vanish
            # either. Sync that fails quietly is indistinguishable from sync
            # that is switched off, which is exactly how this went unnoticed.
            db.record_sync_failure(exc)
            self._set_state("offline")
            self._set_interval(RETRY_INTERVAL_MS)
            return

        db.record_sync_success(outcome)
        self._set_state("idle")
        self._set_interval(LOCAL_SETTLE_MS)
        if outcome.get("conflict"):
            self.conflict.emit()
            return
        if healed.get("imported") or healed.get("queued"):
            # The full comparison changed something on its own; the screens have
            # to be told even if the ordinary turn afterwards found nothing.
            merged = dict(outcome)
            merged["pulled"] = int(merged.get("pulled") or 0) + int(healed.get("imported") or 0)
            merged["healed"] = dict(healed)
            self.applied.emit(merged)
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
