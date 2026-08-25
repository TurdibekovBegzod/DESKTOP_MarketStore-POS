"""Tests for the realtime sync event plumbing."""

import io
import asyncio
import json
import unittest

from pydantic import ValidationError

from app.events import SyncEventBroker
from app.routers.sync import _sse
from app.schemas import PushRequest, PushResponse, RecordIn, SyncStateOut


class SyncEventBrokerTest(unittest.TestCase):
    def test_publish_reaches_every_subscriber_of_that_account_only(self):
        async def scenario():
            broker = SyncEventBroker()
            broker.bind_loop(asyncio.get_running_loop())
            first = broker.subscribe("acct-a")
            second = broker.subscribe("acct-a")
            other = broker.subscribe("acct-b")

            broker.publish("acct-a", {"generation": 4})

            self.assertEqual((await first.get())["generation"], 4)
            self.assertEqual((await second.get())["generation"], 4)
            self.assertTrue(other.empty())

            broker.unsubscribe("acct-a", first)
            broker.publish("acct-a", {"generation": 5})
            self.assertTrue(first.empty())
            self.assertEqual((await second.get())["generation"], 5)

        asyncio.run(scenario())

    def test_publish_to_nobody_is_harmless(self):
        async def scenario():
            broker = SyncEventBroker()
            broker.bind_loop(asyncio.get_running_loop())
            broker.publish("nobody", {"generation": 1})
            broker.publish("", {"generation": 1})
            self.assertEqual(broker.subscriber_count("nobody"), 0)

        asyncio.run(scenario())

    def test_a_stalled_subscriber_never_blocks_the_writer(self):
        async def scenario():
            broker = SyncEventBroker()
            broker.bind_loop(asyncio.get_running_loop())
            queue = broker.subscribe("acct-a")
            for generation in range(200):
                broker.publish("acct-a", {"generation": generation})
            # The queue is bounded; the newest event must still be in there.
            drained = []
            while not queue.empty():
                drained.append(queue.get_nowait()["generation"])
            self.assertEqual(drained[-1], 199)
            self.assertLessEqual(len(drained), 64)

        asyncio.run(scenario())

    def test_publish_from_a_worker_thread_is_scheduled_on_the_loop(self):
        async def scenario():
            broker = SyncEventBroker()
            broker.bind_loop(asyncio.get_running_loop())
            queue = broker.subscribe("acct-a")
            # Sync endpoints run in a threadpool, so this is the real code path.
            await asyncio.to_thread(broker.publish, "acct-a", {"generation": 9})
            payload = await asyncio.wait_for(queue.get(), timeout=2)
            self.assertEqual(payload["generation"], 9)

        asyncio.run(scenario())

    def test_unbound_broker_drops_thread_publishes_instead_of_raising(self):
        broker = SyncEventBroker()
        broker.publish("acct-a", {"generation": 1})  # must not raise


class SseFramingTest(unittest.TestCase):
    def test_frame_is_well_formed(self):
        frame = _sse("change", {"generation": 3, "tables": ["products"]}).decode("utf-8")
        self.assertTrue(frame.startswith("event: change\n"))
        self.assertTrue(frame.endswith("\n\n"))
        data_line = frame.splitlines()[1]
        self.assertTrue(data_line.startswith("data: "))
        self.assertEqual(json.loads(data_line[len("data: "):])["generation"], 3)

    def test_non_json_values_are_stringified_not_crashed(self):
        from datetime import datetime, timezone

        frame = _sse("change", {"server_time": datetime(2026, 8, 25, tzinfo=timezone.utc)})
        payload = json.loads(frame.decode("utf-8").splitlines()[1][len("data: "):])
        self.assertIn("2026-08-25", payload["server_time"])


class SyncSchemaTest(unittest.TestCase):
    def test_push_request_carries_an_optional_generation_guard(self):
        record = RecordIn(table_name="products", local_id="1")
        self.assertIsNone(PushRequest(records=[record]).expected_generation)
        self.assertEqual(PushRequest(records=[record], expected_generation=7).expected_generation, 7)
        with self.assertRaises(ValidationError):
            PushRequest(records=[record], expected_generation=-1)

    def test_push_response_defaults_generation_for_old_clients(self):
        self.assertEqual(PushResponse(saved=1, batch_id=2).generation, 0)

    def test_sync_state_defaults(self):
        from datetime import datetime, timezone

        state = SyncStateOut(generation=3, server_time=datetime.now(timezone.utc))
        self.assertEqual(state.last_tables, [])
        self.assertEqual(state.records_count, 0)


class AppWiringTest(unittest.TestCase):
    def test_sync_routes_are_registered(self):
        from app.main import app

        paths = app.openapi()["paths"]
        self.assertIn("/api/v1/sync/events", paths)
        self.assertIn("/api/v1/sync/state", paths)
        self.assertIn("/api/v1/sync/reset", paths)
        self.assertIn("/api/v1/sync/push", paths)


class _FakeUser:
    def __init__(self, uid):
        self.uid = uid


class _StubRequest:
    """The events endpoint only ever asks the request whether it went away."""

    def __init__(self):
        self.disconnected = False

    async def is_disconnected(self):
        return self.disconnected


class SseEndpointTest(unittest.TestCase):
    """Drives the streaming generator directly - no Postgres, no test server."""

    def _collect(self, frames):
        """Feed raw SSE bytes through the real desktop-side parser."""
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))
        import api_client  # noqa: E402

        return list(api_client.iter_sse_events(_BytesLineReader(b"".join(frames))))

    def test_hello_then_live_change_reaches_the_desktop_parser(self):
        from unittest.mock import patch

        from app.events import broker
        from app.routers import sync as sync_router

        meta = {"generation": 3, "tables": ["products"], "device_key": "dev-a", "last_change_at": None}

        async def scenario():
            broker.bind_loop(asyncio.get_running_loop())
            request = _StubRequest()
            response = await sync_router.events(request, token="tok", authorization=None, since_generation=None)
            body = response.body_iterator
            frames = [await asyncio.wait_for(body.__anext__(), timeout=5)]

            broker.publish("acct-x", {
                "type": "change",
                "generation": 4,
                "tables": ["sales"],
                "device_key": "dev-b",
                "server_time": "2026-08-25T10:00:00+00:00",
            })
            frames.append(await asyncio.wait_for(body.__anext__(), timeout=5))
            await body.aclose()
            self.assertEqual(broker.subscriber_count("acct-x"), 0)
            return frames

        with patch.object(sync_router, "_resolve_stream_user", return_value=_FakeUser("acct-x")), \
             patch.object(sync_router, "_read_meta", side_effect=lambda uid: dict(meta)), \
             patch.object(sync_router, "_read_release", return_value=None):
            frames = asyncio.run(scenario())

        events = self._collect(frames)
        self.assertEqual(events[0][0], "hello")
        self.assertEqual(events[0][1]["generation"], 3)
        self.assertEqual(events[1][0], "change")
        self.assertEqual(events[1][1]["generation"], 4)
        self.assertEqual(events[1][1]["tables"], ["sales"])
        self.assertEqual(events[1][1]["device_key"], "dev-b")

    def test_response_headers_defeat_proxy_buffering(self):
        from unittest.mock import patch

        from app.routers import sync as sync_router

        meta = {"generation": 0, "tables": [], "device_key": None, "last_change_at": None}

        async def scenario():
            response = await sync_router.events(_StubRequest(), token="tok", authorization=None, since_generation=None)
            await response.body_iterator.aclose()
            return response

        with patch.object(sync_router, "_resolve_stream_user", return_value=_FakeUser("acct-x")), \
             patch.object(sync_router, "_read_meta", side_effect=lambda uid: dict(meta)), \
             patch.object(sync_router, "_read_release", return_value=None):
            response = asyncio.run(scenario())

        self.assertEqual(response.media_type, "text/event-stream")
        self.assertEqual(response.headers["x-accel-buffering"], "no")
        self.assertIn("no-cache", response.headers["cache-control"])

    def test_reconnecting_client_is_told_about_the_change_it_missed(self):
        from unittest.mock import patch

        from app.routers import sync as sync_router

        meta = {"generation": 9, "tables": ["sales"], "device_key": "dev-b", "last_change_at": None}

        async def scenario():
            response = await sync_router.events(_StubRequest(), token="tok", authorization=None, since_generation=7)
            body = response.body_iterator
            frames = [
                await asyncio.wait_for(body.__anext__(), timeout=5),
                await asyncio.wait_for(body.__anext__(), timeout=5),
            ]
            await body.aclose()
            return frames

        with patch.object(sync_router, "_resolve_stream_user", return_value=_FakeUser("acct-x")), \
             patch.object(sync_router, "_read_meta", side_effect=lambda uid: dict(meta)), \
             patch.object(sync_router, "_read_release", return_value=None):
            frames = asyncio.run(scenario())

        events = self._collect(frames)
        self.assertEqual([name for name, _ in events], ["hello", "change"])
        self.assertTrue(events[1][1]["resumed"])
        self.assertEqual(events[1][1]["generation"], 9)

    def test_a_client_already_at_the_current_generation_gets_no_replay(self):
        from unittest.mock import patch

        from app.routers import sync as sync_router

        meta = {"generation": 9, "tables": ["sales"], "device_key": "dev-b", "last_change_at": None}

        async def scenario():
            response = await sync_router.events(_StubRequest(), token="tok", authorization=None, since_generation=9)
            body = response.body_iterator
            first = await asyncio.wait_for(body.__anext__(), timeout=5)
            with self.assertRaises(asyncio.TimeoutError):
                # Only the keepalive is due next, and that is 20s away.
                await asyncio.wait_for(body.__anext__(), timeout=4)
            await body.aclose()
            return [first]

        with patch.object(sync_router, "_resolve_stream_user", return_value=_FakeUser("acct-x")), \
             patch.object(sync_router, "_read_meta", side_effect=lambda uid: dict(meta)), \
             patch.object(sync_router, "_read_release", return_value=None):
            frames = asyncio.run(scenario())

        events = self._collect(frames)
        self.assertEqual([name for name, _ in events], ["hello"])

    def test_a_new_release_is_passed_through_to_every_device(self):
        from unittest.mock import patch

        from app.events import broker
        from app.routers import sync as sync_router

        meta = {"generation": 2, "tables": [], "device_key": None, "last_change_at": None}
        stored = {"tag": "v1.0.4", "latest_version": "1.0.4", "name": "MarketStore POS v1.0.4",
                  "published_at": None, "source": "ping"}

        async def scenario():
            broker.bind_loop(asyncio.get_running_loop())
            response = await sync_router.events(_StubRequest(), token="tok", authorization=None, since_generation=None)
            body = response.body_iterator
            frames = [await asyncio.wait_for(body.__anext__(), timeout=5)]

            # Fleet-wide announcement: not tied to any account's sync counter.
            broker.publish_all({
                "type": "release",
                "tag": "v1.0.5",
                "latest_version": "1.0.5",
                "name": "MarketStore POS v1.0.5",
                "published_at": "2026-08-25T12:00:00+00:00",
            })
            frames.append(await asyncio.wait_for(body.__anext__(), timeout=5))
            await body.aclose()
            return frames

        with patch.object(sync_router, "_resolve_stream_user", return_value=_FakeUser("acct-x")), \
             patch.object(sync_router, "_read_meta", side_effect=lambda uid: dict(meta)), \
             patch.object(sync_router, "_read_release", return_value=stored):
            frames = asyncio.run(scenario())

        events = self._collect(frames)
        self.assertEqual(events[0][0], "hello")
        # A device that was closed when the release went out learns on connect.
        self.assertEqual(events[0][1]["release"]["latest_version"], "1.0.4")
        self.assertEqual(events[1][0], "release")
        self.assertEqual(events[1][1]["latest_version"], "1.0.5")
        self.assertEqual(events[1][1]["tag"], "v1.0.5")
        # The release must not disturb the account's sync generation.
        self.assertNotIn("generation", events[1][1])

    def test_missing_credentials_are_rejected(self):
        from fastapi import HTTPException

        from app.routers import sync as sync_router

        with self.assertRaises(HTTPException) as ctx:
            sync_router._resolve_stream_user(None)
        self.assertEqual(ctx.exception.status_code, 401)


class _BytesLineReader:
    """readline() over an in-memory SSE body, matching the client's expectation."""

    def __init__(self, data):
        self._buffer = io.BytesIO(data)

    def readline(self):
        return self._buffer.readline()


if __name__ == "__main__":
    unittest.main()
