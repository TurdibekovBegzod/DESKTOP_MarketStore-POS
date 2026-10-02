"""Closing the app does not wait for the server's next ping.

The live-update stream sits in a socket read between pings (every 20 s).
Stopping it used to close the response from the GUI thread, which waits for
that read to finish - so the window hung until the next ping arrived.
"""

import os
import socket
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class _SilentSseServer:
    """Says hello, then stays quiet far longer than any test waits."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.connected = threading.Event()
        self._release = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.sock.getsockname()[1]}/api/v1"

    def _serve(self):
        conn, _ = self.sock.accept()
        conn.recv(4096)
        conn.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n"
            b"event: hello\ndata: {}\n\n"
        )
        self.connected.set()
        self._release.wait(15)
        conn.close()

    def close(self):
        self._release.set()
        self.sock.close()


class RealtimeStopTest(unittest.TestCase):
    def test_stopping_the_stream_returns_at_once(self):
        from PyQt6.QtCore import QCoreApplication, QThread
        import realtime

        app = QCoreApplication.instance() or QCoreApplication([])
        server = _SilentSseServer()
        self.addCleanup(server.close)

        with patch.dict(os.environ, {"MARKETSTORE_API_URL": server.url}):
            worker = realtime.SyncEventListener(lambda: "token", lambda: None)
            thread = QThread()
            worker.moveToThread(thread)
            thread.started.connect(worker.run)
            thread.start()
            self.assertTrue(server.connected.wait(5))
            # Let the worker reach the blocking read that follows the hello.
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.05)

            started = time.perf_counter()
            worker.stop()
            stop_took = time.perf_counter() - started
            thread.quit()
            finished = thread.wait(2000)
            total = time.perf_counter() - started

        self.assertLess(stop_took, 0.5)
        self.assertTrue(finished, "the listener thread kept reading")
        self.assertLess(total, 1.0)


class _SilentApiServer:
    """Reads the request, then never answers."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.received = threading.Event()
        self._release = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.sock.getsockname()[1]}/api/v1"

    def _serve(self):
        conn, _ = self.sock.accept()
        conn.recv(65536)
        self.received.set()
        self._release.wait(15)
        conn.close()

    def close(self):
        self._release.set()
        self.sock.close()


class AbortOpenRequestsTest(unittest.TestCase):
    def test_a_request_waiting_on_the_server_is_cut_at_once(self):
        import api_client

        server = _SilentApiServer()
        self.addCleanup(server.close)
        outcome = {}

        def call():
            try:
                api_client._request_json("/sync/push", {"records": []}, token="t", timeout=10)
            except Exception as exc:
                outcome["error"] = exc

        with patch.dict(os.environ, {"MARKETSTORE_API_URL": server.url}):
            caller = threading.Thread(target=call)
            caller.start()
            self.assertTrue(server.received.wait(5))
            started = time.perf_counter()
            api_client.abort_open_requests()
            caller.join(2)
            took = time.perf_counter() - started

        self.assertFalse(caller.is_alive(), "the request kept waiting")
        self.assertLess(took, 1.0)
        # The server may have applied it, so the caller must treat it as
        # unanswered - which is what keeps the upload queued.
        error = outcome.get("error")
        self.assertIsInstance(error, api_client.ApiOfflineError)
        self.assertEqual(error.kind, "uncertain")


class AbortEventStreamTest(unittest.TestCase):
    def test_a_response_without_a_socket_is_ignored(self):
        import api_client

        api_client.abort_event_stream(None)
        api_client.abort_event_stream(object())
        api_client._cut_socket(None)


if __name__ == "__main__":
    unittest.main()
