"""Live change notifications from the sync server.

A background worker keeps a Server-Sent Events connection open to
``/api/v1/sync/events``.  Whenever another device of the same account writes
data, the server pushes a ``change`` event and the worker re-emits it as a Qt
signal on the GUI thread.

Server-Sent Events were chosen over websockets deliberately: the desktop client
only ships the Python standard library plus PyQt6 widgets, and SSE rides on a
plain long-lived HTTP GET, so it works through the existing ngrok tunnel and the
nginx proxy without adding a dependency or a new PyInstaller hook.
"""

import threading

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

import api_client


# Reconnect delays, in seconds, applied in order and then repeated at the last
# value. Short enough to feel instant on a flaky link, long enough not to hammer
# the tunnel while the shop's internet is genuinely down.
RECONNECT_BACKOFF = (2, 4, 8, 15, 30)
# Must comfortably exceed the server's 20s keepalive ping.
READ_TIMEOUT_SECONDS = 60


class SyncEventListener(QObject):
    """Runs on a QThread; owns exactly one SSE connection at a time."""

    remote_change = pyqtSignal(dict)
    server_hello = pyqtSignal(dict)
    release_available = pyqtSignal(dict)
    connection_changed = pyqtSignal(bool, str)
    stopped = pyqtSignal()

    def __init__(self, token_provider, generation_provider, parent=None):
        super().__init__(parent)
        self._token_provider = token_provider
        self._generation_provider = generation_provider
        self._stop_event = threading.Event()
        self._response = None
        self._response_lock = threading.Lock()

    def stop(self):
        """Safe to call from the GUI thread; unblocks a pending socket read."""
        self._stop_event.set()
        self._close_response()

    def _close_response(self):
        with self._response_lock:
            response, self._response = self._response, None
        if response is not None:
            try:
                response.close()
            except Exception:
                pass

    def _sleep(self, seconds):
        # Event.wait returns as soon as stop() fires, so shutdown is immediate
        # even in the middle of a 30 second backoff.
        return not self._stop_event.wait(seconds)

    @pyqtSlot()
    def run(self):
        attempt = 0
        while not self._stop_event.is_set():
            token = None
            try:
                token = self._token_provider()
            except Exception:
                token = None
            if not token:
                if not self._sleep(RECONNECT_BACKOFF[-1]):
                    break
                continue

            reason = ""
            try:
                since = None
                try:
                    since = self._generation_provider()
                except Exception:
                    since = None
                response = api_client.open_sync_event_stream(
                    token,
                    since_generation=since,
                    timeout=READ_TIMEOUT_SECONDS,
                )
                with self._response_lock:
                    if self._stop_event.is_set():
                        response.close()
                        break
                    self._response = response
                attempt = 0
                self.connection_changed.emit(True, "")
                for name, payload in api_client.iter_sse_events(response):
                    if self._stop_event.is_set():
                        break
                    if name == "change":
                        self.remote_change.emit(dict(payload or {}))
                    elif name == "hello":
                        data = dict(payload or {})
                        # The greeting carries the newest published build, so a
                        # device that was closed when a release went out finds
                        # out the moment it reconnects.
                        release = data.get("release")
                        if isinstance(release, dict) and release:
                            self.release_available.emit(dict(release))
                        self.server_hello.emit(data)
                    elif name == "release":
                        self.release_available.emit(dict(payload or {}))
                    # "ping" is a keepalive only; nothing to do.
            except api_client.ApiClientError as exc:
                reason = str(exc)
            except Exception as exc:  # socket resets, proxy hiccups, shutdown
                reason = str(exc)
            finally:
                self._close_response()

            if self._stop_event.is_set():
                break
            self.connection_changed.emit(False, reason)
            delay = RECONNECT_BACKOFF[min(attempt, len(RECONNECT_BACKOFF) - 1)]
            attempt += 1
            if not self._sleep(delay):
                break

        self.stopped.emit()
