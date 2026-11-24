"""Durable database-backed Server-Sent Events (SSE) broker for live swarm telemetry.

Combines fast in-process fan-out with durable monotonic integer IDs persisted in the
``events`` table so ``Last-Event-ID`` / ``since_id`` replay and cross-instance delivery
(e.g. Vercel Fluid Compute / Neon Postgres) work reliably without lost events.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from aiwatchdogs.hub.database import dumps_json, loads_json, utc_now_iso

if TYPE_CHECKING:
    from aiwatchdogs.hub.database import Database


class SSEBroker:
    """Durable-event-aware asyncio fan-out broker for FastAPI SSE streams."""

    def __init__(self, db: Database | None = None, max_queue_size: int = 200) -> None:
        self._db = db
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

    def persist_event(
        self,
        conn: Any,
        event_type: str,
        payload: dict[str, Any],
        *,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        """Persist an event inside an active DB transaction and return its monotonic envelope."""
        ts = created_at or utc_now_iso()
        clean_payload = dict(payload)
        if "id" in clean_payload:
            clean_payload.setdefault("item_id", clean_payload.pop("id"))
        row = conn.execute(
            "INSERT INTO events (event_type, payload_json, created_at) VALUES (?, ?, ?) RETURNING id",
            (event_type, dumps_json(clean_payload), ts),
        ).fetchone()
        event_id = int(row["id"]) if row is not None else 0
        return {
            **clean_payload,
            "id": event_id,
            "type": event_type,
            "timestamp": ts,
        }

    def publish(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        event_id: int | None = None,
        timestamp: str | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        """Persist (if not already persisted) and broadcast an event to all SSE subscribers."""
        ts = timestamp or utc_now_iso()
        clean_payload = dict(payload)
        if "id" in clean_payload:
            clean_payload.setdefault("item_id", clean_payload.pop("id"))
        assigned_id = event_id
        if assigned_id is None and persist and self._db is not None:
            try:
                with self._db.transaction() as conn:
                    persisted = self.persist_event(conn, event_type, clean_payload, created_at=ts)
                    assigned_id = int(persisted["id"])
                    ts = str(persisted["timestamp"])
            except Exception:
                assigned_id = None

        envelope: dict[str, Any] = {
            **clean_payload,
            "type": event_type,
            "timestamp": ts,
        }
        if assigned_id is not None:
            envelope["id"] = assigned_id

        if self._subscribers:
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

        return envelope

    def publish_envelope(self, envelope: dict[str, Any]) -> None:
        """Broadcast an already-persisted event envelope without double-writing to DB."""
        event_type = str(envelope.get("type") or "whisper")
        raw_id = envelope.get("id")
        event_id: int | None = int(raw_id) if isinstance(raw_id, int) or (isinstance(raw_id, str) and raw_id.isdigit()) else None
        ts = str(envelope.get("timestamp") or utc_now_iso())
        payload = {k: v for k, v in envelope.items() if k not in {"id", "type", "timestamp"}}
        self.publish(
            event_type,
            payload,
            event_id=event_id,
            timestamp=ts,
            persist=False,
        )

    def fetch_events_since(
        self,
        since_id: int,
        *,
        limit: int = 200,
        event_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch persisted events with strictly greater monotonic integer ID."""
        if self._db is None:
            return []
        capped = max(1, min(int(limit), 500))
        with self._db.read() as conn:
            if event_type:
                rows = conn.execute(
                    """
                    SELECT id, event_type, payload_json, created_at
                    FROM events
                    WHERE id > ? AND event_type = ?
                    ORDER BY id ASC
                    LIMIT ?
                    """,
                    (since_id, event_type, capped),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, event_type, payload_json, created_at
                    FROM events
                    WHERE id > ?
                    ORDER BY id ASC
                    LIMIT ?
                    """,
                    (since_id, capped),
                ).fetchall()
        results: list[dict[str, Any]] = []
        for r in rows:
            payload = loads_json(r["payload_json"], {})
            if not isinstance(payload, dict):
                payload = {}
            if "id" in payload:
                payload.setdefault("item_id", payload.pop("id"))
            results.append(
                {
                    **payload,
                    "id": int(r["id"]),
                    "type": str(r["event_type"]),
                    "timestamp": str(r["created_at"]),
                }
            )
        return results

    def latest_event_id(self) -> int:
        """Return highest persisted monotonic event ID (0 if none)."""
        if self._db is None:
            return 0
        with self._db.read() as conn:
            row = conn.execute("SELECT COALESCE(MAX(id), 0) AS max_id FROM events").fetchone()
            return int(row["max_id"]) if row is not None else 0

    async def subscribe(
        self,
        keepalive_seconds: float = 15.0,
        max_events: int | None = None,
        max_duration_seconds: float | None = None,
        since_id: int | None = None,
        cross_instance_poll_seconds: float = 2.0,
    ) -> AsyncIterator[str]:
        """Yield formatted SSE frames with monotonic IDs and durable replay."""
        loop = asyncio.get_running_loop()
        self._loop = loop
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._max_queue_size)
        self._subscribers.add(queue)
        sent = 0
        last_sent_id = int(since_id) if since_id is not None else self.latest_event_id()
        deadline = (loop.time() + max_duration_seconds) if max_duration_seconds is not None else None
        last_keepalive = loop.time()

        try:
            init_payload = {
                "type": "connected",
                "timestamp": utc_now_iso(),
                "message": "Connected to AI Watchdogs India live telemetry stream",
                "subscribers": len(self._subscribers),
                "last_event_id": last_sent_id,
            }
            yield "retry: 3000\n" + self._format_sse("connected", init_payload)
            sent += 1
            if max_events is not None and sent >= max_events:
                return

            # Durable replay of missed events when client provides since_id / Last-Event-ID
            if since_id is not None and self._db is not None:
                replayed = self.fetch_events_since(since_id, limit=200)
                for ev in replayed:
                    ev_id = int(ev.get("id") or 0)
                    if ev_id <= last_sent_id:
                        continue
                    last_sent_id = ev_id
                    yield self._format_sse(str(ev.get("type", "whisper")), ev, event_id=ev_id)
                    sent += 1
                    if max_events is not None and sent >= max_events:
                        return

            while True:
                wait_timeout = min(keepalive_seconds, cross_instance_poll_seconds) if self._db is not None else keepalive_seconds
                if deadline is not None:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        yield f": keepalive {utc_now_iso()}\n\n"
                        return
                    wait_timeout = min(wait_timeout, remaining)

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=max(0.05, wait_timeout))
                    ev_id_raw = event.get("id")
                    ev_id = int(ev_id_raw) if ev_id_raw is not None else None
                    if ev_id is not None and ev_id <= last_sent_id:
                        continue
                    if ev_id is not None:
                        last_sent_id = ev_id
                    yield self._format_sse(str(event.get("type", "whisper")), event, event_id=ev_id)
                    sent += 1
                    last_keepalive = loop.time()
                    if max_events is not None and sent >= max_events:
                        return
                except asyncio.TimeoutError:
                    # Cross-instance replay check (picks up events written by other Vercel instances)
                    if self._db is not None:
                        missed = self.fetch_events_since(last_sent_id, limit=100)
                        for ev in missed:
                            ev_id = int(ev.get("id") or 0)
                            if ev_id <= last_sent_id:
                                continue
                            last_sent_id = ev_id
                            yield self._format_sse(str(ev.get("type", "whisper")), ev, event_id=ev_id)
                            sent += 1
                            last_keepalive = loop.time()
                            if max_events is not None and sent >= max_events:
                                return

                    now_t = loop.time()
                    if now_t - last_keepalive >= keepalive_seconds or (
                        deadline is not None and (deadline - now_t) <= 0
                    ):
                        yield f": keepalive {utc_now_iso()}\n\n"
                        last_keepalive = now_t
                        if deadline is not None and (deadline - now_t) <= 0:
                            return
        except (asyncio.CancelledError, GeneratorExit):
            return
        finally:
            self._subscribers.discard(queue)

    @staticmethod
    def _format_sse(event_type: str, data: dict[str, Any], *, event_id: int | None = None) -> str:
        encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        prefix = f"id: {event_id}\n" if event_id is not None else ""
        return f"{prefix}event: {event_type}\ndata: {encoded}\n\n"
