"""In-process pub/sub used to push sync-change notifications to connected devices.

The desktop client keeps a long-lived Server-Sent Events connection open
(``GET /api/v1/sync/events``).  Whenever any device pushes data for an account we
bump ``sync_meta.generation`` and publish an event here so every other device of
that account is told about it within milliseconds.

The broker is intentionally process-local.  To stay correct when the API runs with
more than one uvicorn worker (or more than one container), the SSE handler *also*
polls ``sync_meta.generation`` on a slow interval; the broker is the fast path and
the poll is the safety net.  That keeps the deployment free of any extra
infrastructure while still guaranteeing delivery.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any


class SyncEventBroker:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        # Every open stream, regardless of account - used for app-wide news such
        # as "a new desktop release was published".
        self._everyone: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Remember the serving event loop so sync endpoints can publish safely."""
        self._loop = loop

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
                # A stalled client must never block the writer; it will catch up
                # through the generation poll fallback on its next tick.
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(payload)

    def _deliver(self, user_uid: str, payload: dict[str, Any]) -> None:
        self._deliver_to(self._subscribers.get(user_uid, ()), payload)

    def _deliver_all(self, payload: dict[str, Any]) -> None:
        self._deliver_to(self._everyone, payload)

    def _dispatch(self, fn, *args) -> None:
        """Run the delivery on the serving loop, whichever thread we are on."""
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None:
            fn(*args)
            return
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(fn, *args)

    def publish(self, user_uid: str, payload: dict[str, Any]) -> None:
        """Publish to one account - async handler or sync endpoint threadpool."""
        if not user_uid:
            return
        self._dispatch(self._deliver, user_uid, payload)

    def publish_all(self, payload: dict[str, Any]) -> None:
        """Publish to every connected device of every account."""
        self._dispatch(self._deliver_all, payload)


broker = SyncEventBroker()
