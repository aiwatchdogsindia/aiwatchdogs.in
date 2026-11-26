"""Durable database-backed Server-Sent Events (SSE) broker for live swarm telemetry.

Combines fast in-process fan-out with durable monotonic integer IDs persisted in the
``events`` table so ``Last-Event-ID`` / ``since_id`` replay and cross-instance delivery
(e.g. Vercel Fluid Compute / Neon Postgres) work reliably without lost events.

Hardening controls:
- Strict global and per-client concurrent SSE subscriber caps.
- Single coalesced background DB poller running DB queries via ``asyncio.to_thread()``
  (O(1) DB query rate regardless of subscriber count, zero asyncio event-loop blocking).
- Quarantine-aware event filtering so redacted/quarantined findings never leak on replay.
- Strict single-line SSE ``event:`` token validation rejecting CR/LF frame injection.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from aiwatchdogs.hub.database import dumps_json, loads_json, utc_now_iso

if TYPE_CHECKING:
    from aiwatchdogs.hub.database import Database

_SAFE_EVENT_TYPE_RE = re.compile(r"^[a-z0-9_.-]{1,64}$")
DEFAULT_MAX_SUBSCRIBERS = int(os.environ.get("AIWATCHDOGS_SSE_MAX_SUBSCRIBERS", "100"))
DEFAULT_MAX_PER_CLIENT = int(os.environ.get("AIWATCHDOGS_SSE_MAX_PER_CLIENT", "5"))


class SSESubscriberLimitError(Exception):
    """Raised when global or per-client concurrent SSE subscriber limits are exceeded."""

    def __init__(self, message: str, *, status_code: int = 429) -> None:
        super().__init__(message)
        self.status_code = status_code


def _sanitize_event_type(event_type: str) -> str:
    cleaned = str(event_type or "whisper").strip().lower()
    if _SAFE_EVENT_TYPE_RE.fullmatch(cleaned):
        return cleaned
    return "whisper"


class SSEBroker:
    """Durable-event-aware asyncio fan-out broker for FastAPI SSE streams."""

    def __init__(
        self,
        db: Database | None = None,
        max_queue_size: int = 200,
        max_subscribers: int = DEFAULT_MAX_SUBSCRIBERS,
        max_per_client: int = DEFAULT_MAX_PER_CLIENT,
        cross_instance_poll_seconds: float = 2.0,
    ) -> None:
        self._db = db
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._client_counts: dict[str, int] = {}
        self._max_queue_size = max(10, int(max_queue_size))
        self._max_subscribers = max(1, int(max_subscribers))
        self._max_per_client = max(1, int(max_per_client))
        self._poll_interval = max(1.0, float(cross_instance_poll_seconds))
        self._loop: asyncio.AbstractEventLoop | None = None
        self._poller_task: asyncio.Task[None] | None = None
        self._poller_last_id: int = 0

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def acquire_client_slot(self, client_key: str) -> None:
        """Reserve an SSE subscriber slot enforcing global and per-client limits."""
        if len(self._subscribers) >= self._max_subscribers:
            raise SSESubscriberLimitError(
                "Live stream is temporarily at capacity; please reconnect shortly.",
                status_code=503,
            )
        current = self._client_counts.get(client_key, 0)
        if current >= self._max_per_client:
            raise SSESubscriberLimitError(
                "Too many concurrent live stream connections from this client.",
                status_code=429,
            )
        self._client_counts[client_key] = current + 1

    def release_client_slot(self, client_key: str) -> None:
        """Release a previously reserved SSE subscriber slot."""
        current = self._client_counts.get(client_key, 0)
        if current <= 1:
            self._client_counts.pop(client_key, None)
        else:
            self._client_counts[client_key] = current - 1

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
        finding_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist an event inside an active DB transaction and return its monotonic envelope."""
        ts = created_at or utc_now_iso()
        safe_type = _sanitize_event_type(event_type)
        clean_payload = dict(payload)
        if "id" in clean_payload:
            clean_payload.setdefault("item_id", clean_payload.pop("id"))
        resolved_finding_id = (
            str(finding_id or clean_payload.get("finding_id") or "").strip() or None
        )
        row = conn.execute(
            "INSERT INTO events (event_type, finding_id, payload_json, created_at) "
            "VALUES (?, ?, ?, ?) RETURNING id",
            (safe_type, resolved_finding_id, dumps_json(clean_payload), ts),
        ).fetchone()
        event_id = int(row["id"]) if row is not None else 0
        return {
            **clean_payload,
            "id": event_id,
            "type": safe_type,
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
        finding_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist (if not already persisted) and broadcast an event to all SSE subscribers."""
        ts = timestamp or utc_now_iso()
        safe_type = _sanitize_event_type(event_type)
        clean_payload = dict(payload)
        if "id" in clean_payload:
            clean_payload.setdefault("item_id", clean_payload.pop("id"))
        assigned_id = event_id
        if assigned_id is None and persist and self._db is not None:
            try:
                with self._db.transaction() as conn:
                    persisted = self.persist_event(
                        conn,
                        safe_type,
                        clean_payload,
                        created_at=ts,
                        finding_id=finding_id,
                    )
                    assigned_id = int(persisted["id"])
                    ts = str(persisted["timestamp"])
            except Exception:
                assigned_id = None

        envelope: dict[str, Any] = {
            **clean_payload,
            "type": safe_type,
            "timestamp": ts,
        }
        if assigned_id is not None:
            envelope["id"] = assigned_id
            if assigned_id > self._poller_last_id:
                self._poller_last_id = assigned_id

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
        event_type = _sanitize_event_type(str(envelope.get("type") or "whisper"))
        raw_id = envelope.get("id")
        event_id: int | None = (
            int(raw_id)
            if isinstance(raw_id, int) or (isinstance(raw_id, str) and raw_id.isdigit())
            else None
        )
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
        """Fetch persisted non-quarantined events with strictly greater monotonic integer ID."""
        if self._db is None:
            return []
        capped = max(1, min(int(limit), 500))
        quarantine_guard = (
            "(e.finding_id IS NULL OR NOT EXISTS ("
            "SELECT 1 FROM findings f WHERE f.id = e.finding_id "
            "AND (COALESCE(f.quarantined, 0) = 1 OR f.status = 'quarantined')))"
        )
        with self._db.read() as conn:
            if event_type:
                rows = conn.execute(
                    f"""
                    SELECT e.id, e.event_type, e.payload_json, e.created_at
                    FROM events e
                    WHERE e.id > ? AND e.event_type = ? AND {quarantine_guard}
                    ORDER BY e.id ASC
                    LIMIT ?
                    """,
                    (since_id, event_type, capped),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"""
                    SELECT e.id, e.event_type, e.payload_json, e.created_at
                    FROM events e
                    WHERE e.id > ? AND {quarantine_guard}
                    ORDER BY e.id ASC
                    LIMIT ?
                    """,
                    (since_id, capped),
                ).fetchall()
        results: list[dict[str, Any]] = []
        for r in rows:
            payload = loads_json(r["payload_json"], {})
            if not isinstance(payload, dict):
                payload = {}
            if payload.get("redacted") is True:
                continue
            if "id" in payload:
                payload.setdefault("item_id", payload.pop("id"))
            results.append(
                {
                    **payload,
                    "id": int(r["id"]),
                    "type": _sanitize_event_type(str(r["event_type"])),
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

    def _ensure_poller_running(self, loop: asyncio.AbstractEventLoop, start_id: int) -> None:
        if self._db is None:
            return
        if start_id > self._poller_last_id:
            self._poller_last_id = start_id
        if self._poller_task is None or self._poller_task.done():
            self._poller_task = loop.create_task(self._shared_cross_instance_poller())

    async def _shared_cross_instance_poller(self) -> None:
        """Single O(1) background task polling DB via asyncio.to_thread and fanning out to subscribers."""
        try:
            while self._subscribers and self._db is not None:
                await asyncio.sleep(self._poll_interval)
                if not self._subscribers:
                    break
                try:
                    missed = await asyncio.to_thread(
                        self.fetch_events_since, self._poller_last_id, limit=100
                    )
                except Exception:
                    continue
                for ev in missed:
                    ev_id = int(ev.get("id") or 0)
                    if ev_id > self._poller_last_id:
                        self._poller_last_id = ev_id
                    for queue in list(self._subscribers):
                        self._enqueue(queue, ev)
        except asyncio.CancelledError:
            return

    async def subscribe(
        self,
        keepalive_seconds: float = 15.0,
        max_events: int | None = None,
        max_duration_seconds: float | None = None,
        since_id: int | None = None,
        cross_instance_poll_seconds: float = 2.0,
        client_key: str | None = None,
    ) -> AsyncIterator[str]:
        """Yield formatted SSE frames with monotonic IDs, non-blocking DB reads, and bounded slots."""
        _ = cross_instance_poll_seconds  # Coalesced poller manages DB polling independently
        loop = asyncio.get_running_loop()
        self._loop = loop
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._max_queue_size)
        self._subscribers.add(queue)
        sent = 0
        eff_keepalive = max(5.0, float(keepalive_seconds))

        try:
            if since_id is not None:
                last_sent_id = int(since_id)
            elif self._db is not None:
                last_sent_id = await asyncio.to_thread(self.latest_event_id)
            else:
                last_sent_id = 0

            self._ensure_poller_running(loop, last_sent_id)
            deadline = (loop.time() + max_duration_seconds) if max_duration_seconds is not None else None
            last_keepalive = loop.time()

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
                replayed = await asyncio.to_thread(self.fetch_events_since, since_id, limit=200)
                for ev in replayed:
                    ev_id = int(ev.get("id") or 0)
                    if ev_id <= last_sent_id:
                        continue
                    last_sent_id = ev_id
                    if ev_id > self._poller_last_id:
                        self._poller_last_id = ev_id
                    yield self._format_sse(str(ev.get("type", "whisper")), ev, event_id=ev_id)
                    sent += 1
                    if max_events is not None and sent >= max_events:
                        return

            while True:
                wait_timeout = eff_keepalive
                if deadline is not None:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        yield f": keepalive {utc_now_iso()}\n\n"
                        yield self._format_sse(
                            "reconnect",
                            {
                                "type": "reconnect",
                                "reason": "stream_cycle",
                                "last_event_id": last_sent_id,
                                "timestamp": utc_now_iso(),
                            },
                        )
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
                    now_t = loop.time()
                    if deadline is not None and (deadline - now_t) <= 0:
                        yield f": keepalive {utc_now_iso()}\n\n"
                        yield self._format_sse(
                            "reconnect",
                            {
                                "type": "reconnect",
                                "reason": "stream_cycle",
                                "last_event_id": last_sent_id,
                                "timestamp": utc_now_iso(),
                            },
                        )
                        return
                    if now_t - last_keepalive >= eff_keepalive:
                        yield f": keepalive {utc_now_iso()}\n\n"
                        last_keepalive = now_t
        except (asyncio.CancelledError, GeneratorExit):
            return
        finally:
            self._subscribers.discard(queue)
            if client_key is not None:
                self.release_client_slot(client_key)
            if not self._subscribers and self._poller_task is not None and not self._poller_task.done():
                self._poller_task.cancel()

    @staticmethod
    def _format_sse(event_type: str, data: dict[str, Any], *, event_id: int | None = None) -> str:
        safe_type = _sanitize_event_type(event_type)
        encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        prefix = f"id: {event_id}\n" if event_id is not None else ""
        return f"{prefix}event: {safe_type}\ndata: {encoded}\n\n"
