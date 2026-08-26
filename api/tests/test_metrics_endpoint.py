"""The metrics endpoint is exposed to the agent, and to nobody else.

The tunnel forwards every path, so the token is the only thing standing
between /metrics and the open internet.
"""

import importlib
import os
import sys
import unittest

from fastapi.testclient import TestClient
from prometheus_client import REGISTRY


def _reset_prometheus_registry() -> None:
    """Empty the process-wide registry between app rebuilds.

    prometheus_client keeps one global registry. Re-importing the app builds a
    second instrumentator whose collectors clash with the first, and the
    counters the new middleware writes to are then not the ones /metrics
    renders - so every assertion would read zero.
    """
    for collector in list(REGISTRY._collector_to_names):
        try:
            REGISTRY.unregister(collector)
        except KeyError:
            pass


def _build_client(token: str | None) -> TestClient:
    """Rebuild the app, since the endpoint is mounted at import time."""
    _reset_prometheus_registry()
    for name in [name for name in list(sys.modules) if name.startswith("app")]:
        del sys.modules[name]
    if token is None:
        os.environ.pop("METRICS_TOKEN", None)
    else:
        os.environ["METRICS_TOKEN"] = token
    main = importlib.import_module("app.main")
    # A 500 should come back as a response the way it does in production, so
    # the middleware still gets to count it.
    return TestClient(main.app, raise_server_exceptions=False)


class MetricsEndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("SECRET_KEY", "x" * 40)
        os.environ.setdefault("DATABASE_URL", "sqlite://")

    def tearDown(self):
        os.environ.pop("METRICS_TOKEN", None)

    def test_without_a_token_the_endpoint_does_not_exist(self):
        client = _build_client(None)
        self.assertEqual(client.get("/metrics").status_code, 404)

    def test_a_caller_without_the_token_is_refused(self):
        client = _build_client("s3cret")
        self.assertEqual(client.get("/metrics").status_code, 401)

    def test_a_wrong_token_is_refused(self):
        client = _build_client("s3cret")
        response = client.get("/metrics", headers={"Authorization": "Bearer nope"})
        self.assertEqual(response.status_code, 401)

    def test_the_agent_gets_request_counters_per_endpoint_and_status(self):
        client = _build_client("s3cret")
        client.get("/api/v1/auth/me")  # 401, no credentials

        response = client.get("/metrics", headers={"Authorization": "Bearer s3cret"})
        self.assertEqual(response.status_code, 200)
        body = response.text

        self.assertIn("http_requests_total", body)
        self.assertIn("http_request_duration_seconds", body)
        self.assertIn('handler="/api/v1/auth/me"', body)
        self.assertIn('status="401"', body)

    def test_health_and_metrics_are_not_counted_as_traffic(self):
        client = _build_client("s3cret")
        for _ in range(3):
            client.get("/health")
        client.get("/metrics", headers={"Authorization": "Bearer s3cret"})

        body = client.get("/metrics", headers={"Authorization": "Bearer s3cret"}).text
        # The agent polls both on a timer; counting them would drown the real
        # traffic and make every latency chart look flat.
        self.assertNotIn('handler="/health"', body)
        self.assertNotIn('handler="/metrics"', body)


if __name__ == "__main__":
    unittest.main()
