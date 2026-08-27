"""Realtime account events shared by every API process.

Desktop devices keep a Server-Sent Events connection open to
``GET /api/v1/sync/events``. Database writes publish a small notification here;
the receiving desktop then downloads the durable rows from PostgreSQL.

Redis is the cross-process transport. This matters when uvicorn has multiple
workers or several API containers are running: an SSE client and the request
that changed its account may be handled by different processes. Local queues
remain the final hop to connected clients, while the generation poll in the SSE
route is the recovery path if Redis is temporarily unavailable.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from typing import Any


REDIS_CHANNEL = "marketstore:realtime:v1"
REDIS_RECONNECT_SECONDS = 2


class SyncEventBroker:
    def __init__(self, redis_url: str | None = None) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        # Every open stream, regardless of account - used for app-wide news such
        # as "a new desktop release was published".
        self._everyone: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._redis_url = (redis_url or "").strip() or None
        self._redis_client = None
        self._redis_pubsub = None
        self._redis_task: asyncio.Task | None = None
        self._redis_ready = False
        self._stopping = False
        self._instance_id = uuid.uuid4().hex

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Remember the serving event loop so sync endpoints can publish safely."""
        self._loop = loop

    async def start(self, redis_url: str | None = None) -> None:
        """Start Redis without making API startup depend on its availability.

        PostgreSQL remains the source of truth. If Redis is restarting, the
        listener reconnects in the background and SSE's generation poll still
        catches every committed change.
        """
        self.bind_loop(asyncio.get_running_loop())
        if redis_url is not None:
            self._redis_url = str(redis_url).strip() or None
        if not self._redis_url:
            return
        if self._redis_task is not None and not self._redis_task.done():
            return
        self._stopping = False
        self._redis_task = asyncio.create_task(
            self._redis_listener(), name="marketstore-sync-events"
        )
        await asyncio.sleep(0)

    async def stop(self) -> None:
        self._stopping = True
        task, self._redis_task = self._redis_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self._close_redis()

    @property
    def redis_connected(self) -> bool:
        return self._redis_ready

    def subscribe(self, user_uid: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._subscribers.setdefault(user_uid, set()).add(queue)
        self._everyone.add(queue)
        return queue

    def unsubscribe(self, user_uid: str, queue: asyncio.Queue) -> None:
        self._everyone.discard(queue)
        holders = self._subscribers.get(user_uid)
        if not holders:
            return
        holders.discard(queue)
        if not holders:
            self._subscribers.pop(user_uid, None)

    def subscriber_count(self, user_uid: str) -> int:
        return len(self._subscribers.get(user_uid, ()))

    def subscriber_total(self) -> int:
        return len(self._everyone)

    def _deliver_to(self, queues, payload: dict[str, Any]) -> None:
        for queue in list(queues):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # Keeping the newest generation is enough: the desktop then
                # downloads every row after its durable cursor.
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(payload)

    def _deliver(self, user_uid: str, payload: dict[str, Any]) -> None:
        self._deliver_to(self._subscribers.get(user_uid, ()), payload)

    def _deliver_all(self, payload: dict[str, Any]) -> None:
        self._deliver_to(self._everyone, payload)

    def _dispatch(self, fn, *args) -> None:
        """Run local delivery on the serving loop, whichever thread called us."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            fn(*args)
            return
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(fn, *args)

    def _schedule_publish(self, target: str, payload: dict[str, Any]) -> None:
        loop = self._loop
        if not self._redis_url or not self._redis_ready or loop is None or loop.is_closed():
            self._deliver_target(target, payload)
            return
        coroutine = self._publish_redis(target, payload)
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            loop.create_task(coroutine)
            return
        try:
            asyncio.run_coroutine_threadsafe(coroutine, loop)
        except RuntimeError:
            coroutine.close()
            self._deliver_target(target, payload)

    def _deliver_target(self, target: str, payload: dict[str, Any]) -> None:
        if target == "*":
            self._dispatch(self._deliver_all, payload)
        else:
            self._dispatch(self._deliver, target, payload)

    async def _publish_redis(self, target: str, payload: dict[str, Any]) -> None:
        client = self._redis_client
        if client is None:
            self._deliver_target(target, payload)
            return
        envelope = json.dumps(
            {"source": self._instance_id, "target": target, "payload": payload},
            default=str,
            separators=(",", ":"),
        )
        try:
            await client.publish(REDIS_CHANNEL, envelope)
        except Exception:
            # The transaction is already durable. This process receives the
            # event now; peer processes recover through generation polling.
            self._redis_ready = False
            self._deliver_target(target, payload)

    def _accept_redis_message(self, raw: Any) -> None:
        """Decode one bus message and route it only to the intended account."""
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            envelope = json.loads(raw)
            target = str(envelope.get("target") or "")
            payload = envelope.get("payload")
            if not target or not isinstance(payload, dict):
                return
        except (TypeError, ValueError, UnicodeDecodeError):
            return
        if target == "*":
            self._deliver_all(dict(payload))
        else:
            self._deliver(target, dict(payload))

    async def _redis_listener(self) -> None:
        from redis.asyncio import Redis

        while not self._stopping:
            try:
                client = Redis.from_url(
                    self._redis_url,
                    decode_responses=False,
                    socket_connect_timeout=3,
                    socket_timeout=5,
                    health_check_interval=15,
                )
                pubsub = client.pubsub(ignore_subscribe_messages=True)
                await pubsub.subscribe(REDIS_CHANNEL)
                self._redis_client = client
                self._redis_pubsub = pubsub
                self._redis_ready = True

                while not self._stopping:
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=1.0
                    )
                    if message and message.get("type") == "message":
                        self._accept_redis_message(message.get("data"))
            except asyncio.CancelledError:
                raise
            except Exception:
                self._redis_ready = False
            finally:
                await self._close_redis()

            if not self._stopping:
                await asyncio.sleep(REDIS_RECONNECT_SECONDS)

    async def _close_redis(self) -> None:
        self._redis_ready = False
        pubsub, self._redis_pubsub = self._redis_pubsub, None
        client, self._redis_client = self._redis_client, None
        if pubsub is not None:
            with contextlib.suppress(Exception):
                await pubsub.aclose()
        if client is not None:
            with contextlib.suppress(Exception):
                await client.aclose()

    def publish(self, user_uid: str, payload: dict[str, Any]) -> None:
        """Publish a committed account-change notification from any thread."""
        if not user_uid:
            return
        self._schedule_publish(str(user_uid), dict(payload))

    def publish_all(self, payload: dict[str, Any]) -> None:
        """Publish fleet-wide news to every connected desktop."""
        self._schedule_publish("*", dict(payload))


broker = SyncEventBroker()
