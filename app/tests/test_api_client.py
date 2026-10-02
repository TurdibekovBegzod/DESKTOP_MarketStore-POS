import gzip
import json
import os
import socket
import ssl
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock, patch

import api_client
from ssl_support import create_ssl_context


class ApiClientPaginationTest(unittest.TestCase):
    def test_default_api_uses_production_ngrok_https_endpoint(self):
        self.assertEqual(
            api_client.DEFAULT_API_URL,
            "https://drinking-relight-trailside.ngrok-free.dev/api/v1",
        )

    def test_requests_use_verified_ssl_context(self):
        response = MagicMock(status=200, headers={}, will_close=False)
        response.read.return_value = b'{"status":"ok"}'
        with patch("api_client.getproxies", return_value={}), \
                patch.dict(os.environ, {"MARKETSTORE_API_URL": "https://api.test/api/v1"}), \
                patch("api_client.http.client.HTTPSConnection") as connection_class:
            connection_class.return_value.getresponse.return_value = response
            result = api_client._request_json("/health")
        api_client._connections.pool = {}

        self.assertEqual(result, {"status": "ok"})
        context = connection_class.call_args.kwargs["context"]
        self.assertIsInstance(context, ssl.SSLContext)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_ssl_context_is_built_once(self):
        self.assertIs(create_ssl_context(), create_ssl_context())

    def test_a_proxy_machine_still_goes_through_urllib(self):
        with patch("api_client.getproxies", return_value={"http": "http://proxy:3128"}), \
                patch("api_client.urlopen") as urlopen:
            response = urlopen.return_value.__enter__.return_value
            response.status = 200
            response.headers = {}
            response.read.return_value = b'{"status":"ok"}'
            with patch.dict(os.environ, {"MARKETSTORE_API_URL": "http://api.test/api/v1"}):
                self.assertEqual(api_client._request_json("/health"), {"status": "ok"})
        urlopen.assert_called_once()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    # Set by a test to make the server drop each connection after answering
    # without saying so - what the tunnel does to an idle kept-alive one.
    drop_after_reply = False
    peers = []
    encodings = []

    def do_GET(self):
        type(self).peers.append(self.client_address[1])
        type(self).encodings.append(self.headers.get("Accept-Encoding"))
        body = json.dumps({"path": self.path, "rows": ["x" * 50] * 50}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        if "gzip" in (self.headers.get("Accept-Encoding") or ""):
            body = gzip.compress(body)
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        if type(self).drop_after_reply:
            self.close_connection = True

    def log_message(self, *args):
        pass


class ApiClientConnectionReuseTest(unittest.TestCase):
    def setUp(self):
        _Handler.peers = []
        _Handler.encodings = []
        _Handler.drop_after_reply = False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{self.server.server_address[1]}/api/v1"
        self.env = patch.dict(os.environ, {"MARKETSTORE_API_URL": base})
        self.env.start()
        self.proxies = patch("api_client.getproxies", return_value={})
        self.proxies.start()
        api_client._connections.pool = {}

    def tearDown(self):
        for key in list(getattr(api_client._connections, "pool", {}) or {}):
            api_client._drop_connection(key)
        self.proxies.stop()
        self.env.stop()
        self.server.shutdown()
        self.server.server_close()

    def test_consecutive_calls_share_one_connection(self):
        for index in range(3):
            result = api_client._request_json(f"/sync/state?n={index}")
            self.assertEqual(result["path"], f"/api/v1/sync/state?n={index}")
        self.assertEqual(len(_Handler.peers), 3)
        self.assertEqual(len(set(_Handler.peers)), 1, "every call opened its own connection")

    def test_responses_are_requested_and_read_gzipped(self):
        result = api_client._request_json("/sync/pull")
        self.assertEqual(len(result["rows"]), 50)
        self.assertEqual(_Handler.encodings, ["gzip"])

    def test_a_connection_the_server_dropped_is_replaced_not_reported_offline(self):
        _Handler.drop_after_reply = True
        first = api_client._request_json("/sync/state?n=1")
        second = api_client._request_json("/sync/state?n=2")
        self.assertEqual(first["path"], "/api/v1/sync/state?n=1")
        self.assertEqual(second["path"], "/api/v1/sync/state?n=2")
        self.assertEqual(len(set(_Handler.peers)), 2)

    def test_a_dead_server_is_offline(self):
        self.server.shutdown()
        self.server.server_close()
        api_client._connections.pool = {}
        with self.assertRaises(api_client.ApiOfflineError):
            api_client._request_json("/health", timeout=2)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


class _SlowHandler(_Handler):
    def do_GET(self):
        time.sleep(1.5)
        super().do_GET()


class ApiClientFailureKindTest(unittest.TestCase):
    """Which side failed decides what the person is told."""

    def setUp(self):
        self.proxies = patch("api_client.getproxies", return_value={})
        self.proxies.start()
        api_client._connections.pool = {}

    def tearDown(self):
        for key in list(getattr(api_client._connections, "pool", {}) or {}):
            api_client._drop_connection(key)
        self.proxies.stop()

    def _kind(self, base, timeout=2):
        with patch.dict(os.environ, {"MARKETSTORE_API_URL": base}):
            with self.assertRaises(api_client.ApiOfflineError) as caught:
                api_client._request_json("/sync/state", timeout=timeout)
        return caught.exception.kind

    def test_an_unknown_host_is_the_internet(self):
        self.assertEqual(self._kind("http://no-such-host.invalid/api/v1"), "internet")

    def test_a_refused_connection_is_the_server(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.close()
        self.assertEqual(self._kind(f"http://127.0.0.1:{port}/api/v1"), "server")

    def test_a_tunnel_without_its_server_is_the_server(self):
        for code in (404, 502, 503):
            with self.subTest(code=code):
                with patch("api_client._send", return_value=(code, {}, b"{}")):
                    self.assertEqual(self._kind("https://api.test/api/v1"), "server")

    def test_no_answer_after_sending_is_uncertain(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _SlowHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            kind = self._kind(f"http://127.0.0.1:{server.server_address[1]}/api/v1", timeout=0.5)
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(kind, "uncertain")

    def test_a_slow_answer_gets_the_answer_timeout_not_the_connect_one(self):
        """Connect has 0.5 s, the answer 3 s: a server that takes 1.5 s to
        answer must still be heard."""
        server = ThreadingHTTPServer(("127.0.0.1", 0), _SlowHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with patch.dict(os.environ, {"MARKETSTORE_API_URL": f"http://127.0.0.1:{server.server_address[1]}/api/v1"}):
                result = api_client._request_json("/sync/state", timeout=(0.5, 3))
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(result["path"], "/api/v1/sync/state")

    def test_a_gateway_timeout_is_uncertain(self):
        with patch("api_client._send", return_value=(504, {}, b"{}")):
            self.assertEqual(self._kind("https://api.test/api/v1"), "uncertain")


class ApiClientPullTest(unittest.TestCase):
    def test_pull_collects_all_server_pages(self):
        responses = [
            {
                "records": [{"local_id": "1"}],
                "server_time": "2026-08-24T00:00:00Z",
                "has_more": True,
                "next_offset": 1,
            },
            {
                "records": [{"local_id": "2"}],
                "server_time": "2026-08-24T00:00:01Z",
                "has_more": False,
                "next_offset": None,
            },
        ]
        with patch("api_client._request_json", side_effect=responses) as request:
            result = api_client.pull_sync_records("token", table_name="sale items")

        self.assertEqual([row["local_id"] for row in result["records"]], ["1", "2"])
        self.assertIn("offset=0", request.call_args_list[0].args[0])
        self.assertIn("offset=1", request.call_args_list[1].args[0])
        self.assertIn("table_name=sale+items", request.call_args_list[0].args[0])

    def test_pull_filters_several_tables_in_one_bounded_request(self):
        with patch(
            "api_client._request_json",
            return_value={"records": [], "has_more": False, "generation": 7},
        ) as request:
            api_client.pull_sync_records(
                "token",
                since_seq=4,
                table_names=["products", "sales", "products"],
            )

        path = request.call_args.args[0]
        self.assertIn("tables=products%2Csales", path)
        self.assertIn("since_seq=4", path)
        self.assertIn("limit=500", path)


if __name__ == "__main__":
    unittest.main()
