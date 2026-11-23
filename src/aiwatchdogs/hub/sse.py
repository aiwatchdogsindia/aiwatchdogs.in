"""In-process asyncio pub-sub Server-Sent Events (SSE) broker for live swarm telemetry."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from aiwatchdogs.hub.database import utc_now_iso


class SSEBroker:
    """Lightweight asyncio fan-out broker for FastAPI SSE streams."""

    def __init__(self, max_queue_size: int = 200) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._max_queue_size = max_queue_size
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def _enqueue(self, queue: asyncio.Queue[dict[str, Any]], event: dict[str, Any]) -> None:
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass

    def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        """Broadcast an event to all connected SSE subscribers (safe from sync or async)."""
        envelope = {
            "type": event_type,
            "timestamp": utc_now_iso(),
            **payload,
        }
        if not self._subscribers:
            return
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop is not None:
            for queue in list(self._subscribers):
                self._enqueue(queue, envelope)
        elif self._loop is not None and self._loop.is_running():
            for queue in list(self._subscribers):
                self._loop.call_soon_threadsafe(self._enqueue, queue, envelope)

    async def subscribe(
        self,
        keepalive_seconds: float = 15.0,
        max_events: int | None = None,
        max_duration_seconds: float | None = None,
    ) -> AsyncIterator[str]:
        """Yield formatted SSE frames for a connected client until disconnect or max_duration."""
        loop = asyncio.get_running_loop()
        self._loop = loop
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._max_queue_size)
        self._subscribers.add(queue)
        sent = 0
        deadline = (loop.time() + max_duration_seconds) if max_duration_seconds is not None else None
        try:
            init_payload = {
                "type": "connected",
                "timestamp": utc_now_iso(),
                "message": "Connected to AI Watchdogs India live telemetry stream",
                "subscribers": len(self._subscribers),
            }
            yield "retry: 3000\n" + self._format_sse("connected", init_payload)
            sent += 1
            if max_events is not None and sent >= max_events:
                return

            while True:
                wait_timeout = keepalive_seconds
                if deadline is not None:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        yield f": keepalive {utc_now_iso()}\n\n"
                        return
                    wait_timeout = min(keepalive_seconds, remaining)
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=wait_timeout)
                    event_type = str(event.get("type", "whisper"))
                    yield self._format_sse(event_type, event)
                    sent += 1
                    if max_events is not None and sent >= max_events:
                        return
                except asyncio.TimeoutError:
                    yield f": keepalive {utc_now_iso()}\n\n"
                    if deadline is not None and (deadline - loop.time()) <= 0:
                        return
        except (asyncio.CancelledError, GeneratorExit):
            return
        finally:
            self._subscribers.discard(queue)

    @staticmethod
    def _format_sse(event_type: str, data: dict[str, Any]) -> str:
        encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        return f"event: {event_type}\ndata: {encoded}\n\n"
