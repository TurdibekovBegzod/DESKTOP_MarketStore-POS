"""Keeps this device in step with the others without anyone pressing a button.

One worker owns all automatic synchronisation. It runs a turn only when the
server says something changed or shortly after this device writes something
itself. Reconnecting the event stream performs the catch-up; an idle desktop
does not poll the API.

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
LOCAL_SETTLE_MS = 100
# Idle timer frequency. It never turns pending local rows into a send request;
# only the write that just happened may request one server delivery attempt.
IDLE_INTERVAL_MS = 2_000
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
    # Emitted after every completed turn, including one that moved nothing.
    # `applied` only fires when data actually travelled, so anything waiting for
    # confirmation that the server has this device's work - the "sending to the
    # server" notice above all - would otherwise wait for ever.
    turn_finished = pyqtSignal(dict)
    turn_failed = pyqtSignal(str)
    state_changed = pyqtSignal(str)
    conflict = pyqtSignal()
    wake_requested = pyqtSignal()

    def __init__(self, user_provider, parent=None):
        super().__init__(parent)
        self._user_provider = user_provider
        self._timer = None
        self._stopping = threading.Event()
        self._pull_requested = threading.Event()
        self._push_requested = threading.Event()
        self._state = "idle"
        # Never reconciled in this run, so the first turn is a full one.
        self._last_full_at = None
        # Emitting this signal is safe from the GUI thread, a database callback
        # or any future background writer. Qt runs the slot in this object's
        # worker thread, where changing its timer is legal.
        self.wake_requested.connect(self._wake_now)

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
        """The server changed, so download once without resending local rows."""
        self._pull_requested.set()
        self.wake_requested.emit()

    @pyqtSlot()
    def request_push(self):
        """A new local write gets exactly one delivery attempt."""
        self._push_requested.set()
        self.wake_requested.emit()

    def notify_local_change(self):
        """Called from whichever thread just wrote to the database.

        Only a flag is set, which is safe from any thread; the work itself
        happens on this worker's own next tick.
        """
        self._push_requested.set()
        self.wake_requested.emit()

    @pyqtSlot()
    def _wake_now(self):
        """Re-arm an idle worker immediately, from inside its own Qt thread."""
        if self._stopping.is_set():
            return
        if self._timer is not None and self._timer.interval() != 1:
            self._timer.setInterval(1)

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

        wants_pull = self._pull_requested.is_set()
        wants_push = self._push_requested.is_set()
        if not wants_pull and not wants_push:
            self._set_interval(IDLE_INTERVAL_MS)
            return

        self._pull_requested.clear()
        self._push_requested.clear()
        self._set_state("syncing")
        full_due = wants_push and not db.is_remote_session_cache() and (
            self._last_full_at is None
            or (time.monotonic() - self._last_full_at) >= FULL_RECONCILE_SECONDS
        )
        healed = {}
        try:
            if full_due:
                healed = dict(sync_service.reconcile_full(user) or {})
                if not healed.get("deferred"):
                    self._last_full_at = time.monotonic()
            if wants_push:
                outcome = sync_service.auto_sync_turn(user)
            else:
                pull = sync_service.pull_server_changes(user, incremental=True)
                outcome = {
                    "pulled": int(pull.get("imported") or 0),
                    "pushed": 0,
                    "tables": db.get_last_pull_stats().get("tables", []),
                    "rejected": [],
                    "conflict": False,
                    "settled": False,
                }
        except sync_service.SyncConflict as exc:
            db.record_sync_failure(exc)
            self._set_state("idle")
            self.turn_failed.emit(str(exc))
            self.conflict.emit()
            self._set_interval(IDLE_INTERVAL_MS)
            return
        except Exception as exc:
            # Not worth interrupting a cashier over -- but it must not vanish
            # either. Sync that fails quietly is indistinguishable from sync
            # that is switched off, which is exactly how this went unnoticed.
            db.record_sync_failure(exc)
            # Keep the pending rows untouched, but do not queue another send.
            # Reconnects and remote events are download-only; only a distinct
            # new local write can ever create a new one-shot send request.
            self._set_state("offline")
            self.turn_failed.emit(f"{type(exc).__name__}: {exc}")
            self._set_interval(IDLE_INTERVAL_MS)
            return

        db.record_sync_success(outcome)
        self._set_state("idle")
        # Say the turn is over before deciding whether anything is worth
        # reloading: a turn that sent the last queued row and found nothing to
        # download still means "the server has it now".
        self.turn_finished.emit(dict(outcome))
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
