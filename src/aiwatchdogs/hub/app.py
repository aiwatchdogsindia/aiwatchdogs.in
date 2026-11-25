"""FastAPI Coordinator Hub application, REST/SSE routes, SPA serving, and CLI entrypoint."""

from __future__ import annotations

import logging
import math
import mimetypes
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

mimetypes.add_type("font/woff2", ".woff2")

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from aiwatchdogs.hub.database import (
    Database,
    is_postgres_required,
    redact_database_url,
    resolve_database_url,
    utc_now_iso,
)
from aiwatchdogs.hub.desk import DeskService
from aiwatchdogs.hub.seed import seed_database
from aiwatchdogs.hub.sse import SSEBroker, SSESubscriberLimitError
from aiwatchdogs.hub.templates import DEFAULT_PUBLIC_HUB_URL, normalize_base_url, render_join_sh, render_join_txt
from aiwatchdogs.projects import sync_projects

logger = logging.getLogger("aiwatchdogs.hub")

# ---------------------------------------------------------------------------
# Security Policy Constants
# ---------------------------------------------------------------------------

MAX_REQUEST_BODY_BYTES = int(os.environ.get("AIWATCHDOGS_MAX_BODY_BYTES", str(256 * 1024)))

SECURITY_CSP = (
    "default-src 'self'; "
    "script-src 'self' https://www.googletagmanager.com; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https://www.google-analytics.com https://www.googletagmanager.com; "
    "font-src 'self'; "
    "connect-src 'self' https://www.google-analytics.com https://*.google-analytics.com "
    "https://*.analytics.google.com https://www.googletagmanager.com; "
    "object-src 'none'; "
    "frame-src 'none'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "upgrade-insecure-requests"
)

_ALLOWED_VERCEL_HOST_RE = re.compile(r"^aiwatchdogs(?:-[a-z0-9-]+)?\.vercel\.app$")
_SAFE_HOST_PORT_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?(?::[0-9]{1,5})?$")
_CANONICAL_HOSTS = frozenset({"aiwatchdogs.in", "www.aiwatchdogs.in"})
_LOCAL_DEV_HOSTS = frozenset({"localhost", "127.0.0.1", "testserver"})

# ---------------------------------------------------------------------------
# Pure ASGI Security & Body-Size Middlewares (SSE-safe, zero response buffering)
# ---------------------------------------------------------------------------


class _RequestBodyTooLargeError(Exception):
    """Internal sentinel raised when a streamed request body exceeds MAX_REQUEST_BODY_BYTES."""


class MaxBodySizeMiddleware:
    """Pure ASGI middleware enforcing a strict request body byte limit (both Content-Length & chunked)."""

    def __init__(self, app: Any, *, max_bytes: int = MAX_REQUEST_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max(1024, int(max_bytes))

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or scope.get("method", "GET").upper() not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return

        for raw_key, raw_val in scope.get("headers") or ():
            if raw_key.lower() == b"content-length":
                try:
                    declared = int(raw_val.decode("ascii").strip())
                except (UnicodeDecodeError, ValueError):
                    response = JSONResponse(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        content={"detail": "Invalid Content-Length header."},
                    )
                    await response(scope, receive, send)
                    return
                if declared < 0 or declared > self.max_bytes:
                    response = JSONResponse(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        content={"detail": f"Request body exceeds maximum allowed size ({self.max_bytes} bytes)."},
                    )
                    await response(scope, receive, send)
                    return

        received_total = 0
        response_started = False

        async def _limited_receive() -> dict[str, Any]:
            nonlocal received_total
            message = await receive()
            if message.get("type") == "http.request":
                chunk = message.get("body") or b""
                received_total += len(chunk)
                if received_total > self.max_bytes:
                    raise _RequestBodyTooLargeError()
            return message

        async def _tracking_send(message: dict[str, Any]) -> None:
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, _limited_receive, _tracking_send)
        except _RequestBodyTooLargeError:
            if not response_started:
                response = JSONResponse(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    content={"detail": f"Request body exceeds maximum allowed size ({self.max_bytes} bytes)."},
                )
                await response(scope, receive, send)


class SecurityHeadersMiddleware:
    """Pure ASGI middleware attaching defense-in-depth HTTP security headers to every response."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        req_headers = {k.lower(): v for k, v in (scope.get("headers") or ())}
        fwd_proto = req_headers.get(b"x-forwarded-proto", b"").decode("latin-1").split(",")[0].strip().lower()
        is_https = (
            scope.get("scheme") == "https"
            or fwd_proto == "https"
            or bool(os.environ.get("VERCEL"))
        )

        async def _send_with_headers(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers") or ())
                existing = {k.lower() for k, _ in headers}

                def _add(name: bytes, value: bytes) -> None:
                    if name.lower() not in existing:
                        headers.append((name, value))
                        existing.add(name.lower())

                _add(b"content-security-policy", SECURITY_CSP.encode("ascii"))
                _add(b"x-content-type-options", b"nosniff")
                _add(b"x-frame-options", b"DENY")
                _add(b"referrer-policy", b"strict-origin-when-cross-origin")
                _add(b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=(), usb=()")
                _add(b"cross-origin-opener-policy", b"same-origin")
                _add(b"cross-origin-resource-policy", b"same-origin")
                if is_https:
                    _add(b"strict-transport-security", b"max-age=63072000; includeSubDomains; preload")

                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, _send_with_headers)


# ---------------------------------------------------------------------------
# Request Schemas (Explicit Length & Finite Numeric Bounds)
# ---------------------------------------------------------------------------


class RegisterAgentRequest(BaseModel):
    handle: str = Field(..., min_length=1, max_length=64)
    display_name: str | None = Field(default=None, max_length=120)
    provider: str = Field(default="rule-engine", max_length=64)
    model: str = Field(default="deterministic-cpu-v1", max_length=120)
    mode: str = Field(default="auto", max_length=32)
    region: str = Field(default="IN", max_length=32)
    capabilities: list[str] = Field(default_factory=list, max_length=20)
    # Which AI coding agent runs this node (e.g. "Claude Code", "Antigravity") and its model.
    coding_agent: str | None = Field(default=None, max_length=80)
    coding_agent_model: str | None = Field(default=None, max_length=120)


class DeskNextRequest(BaseModel):
    handle: str | None = Field(default=None, max_length=64)
    mode: str | None = Field(default=None, max_length=32)
    preferred_mission: str | None = Field(default=None, max_length=120)


class DeskBeatRequest(BaseModel):
    lease_id: str = Field(..., min_length=1, max_length=128)
    whisper: str | None = Field(default=None, max_length=600)
    progress_pct: int | None = Field(default=None, ge=0, le=100)


class DeskReleaseRequest(BaseModel):
    lease_id: str = Field(..., min_length=1, max_length=128)
    reason: str = Field(default="released", max_length=80)
    notes: str | None = Field(default=None, max_length=1000)


class DeskSubmitRequest(BaseModel):
    lease_id: str = Field(..., min_length=1, max_length=128)
    outcome: Literal["finding", "cleared", "verification"]
    whisper: str | None = Field(default=None, max_length=600)
    finding: dict[str, Any] | None = None
    verification: dict[str, Any] | None = None
    metrics: dict[str, Any] | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)


class RTIDraftRequest(BaseModel):
    public_authority: str | None = Field(default=None, max_length=240)
    preview: bool | None = None


class SponsorPledgeRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    handle: str | None = Field(default=None, max_length=120)
    handle_or_org: str | None = Field(default=None, max_length=120)
    tier: str | None = Field(default=None, max_length=64)
    amount_inr: float = Field(default=0.0, ge=0.0, le=1e12, allow_inf_nan=False)
    in_kind_value_inr: float = Field(default=0.0, ge=0.0, le=1e12, allow_inf_nan=False)
    compute_credits_desc: str | None = Field(default=None, max_length=240)
    url: str | None = Field(default=None, max_length=500)
    message: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=500)


# ---------------------------------------------------------------------------
# Environment & Host / Client-IP Helpers
# ---------------------------------------------------------------------------


def _is_production_env() -> bool:
    return bool(
        os.environ.get("VERCEL")
        or os.environ.get("AIWATCHDOGS_ENV", "").strip().lower() in {"prod", "production"}
    )


def _resolve_web_dist(web_dist: str | Path | None = None) -> Path:
    if web_dist is not None:
        return Path(web_dist)
    env_val = os.environ.get("AIWATCHDOGS_WEB_DIST")
    if env_val:
        return Path(env_val)
    here = Path(__file__).resolve()
    candidates: list[Path] = []
    if len(here.parents) > 3:
        candidates.append(here.parents[3] / "web" / "dist")
    candidates.append(Path.cwd() / "web" / "dist")
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[0]


def _extra_allowed_hosts() -> set[str]:
    raw = os.environ.get("AIWATCHDOGS_ALLOWED_HOSTS", "")
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def _is_trusted_host(host_header: str) -> bool:
    cleaned = host_header.strip().lower()
    if not cleaned or len(cleaned) > 255 or not _SAFE_HOST_PORT_RE.fullmatch(cleaned):
        return False
    hostname = cleaned.split(":", 1)[0]
    if hostname in _CANONICAL_HOSTS or _ALLOWED_VERCEL_HOST_RE.fullmatch(hostname):
        return True
    if hostname in _extra_allowed_hosts():
        return True
    if not os.environ.get("VERCEL") and hostname in _LOCAL_DEV_HOSTS:
        return True
    return False


def _public_base_url(request: Request) -> str:
    """Resolve a strictly validated public origin for /join.sh, /join.txt, and profile URLs.

    Never interpolates arbitrary attacker-controlled Host or X-Forwarded-Host headers into
    shell scripts or links; unrecognized or malformed hosts fall back to ``https://aiwatchdogs.in``.
    """
    env_url = os.environ.get("AIWATCHDOGS_PUBLIC_URL", "").strip()
    if env_url:
        return normalize_base_url(env_url)

    is_vercel = bool(os.environ.get("VERCEL"))

    # Prefer the direct Host header (or rightmost trusted forwarded host); never trust spoofed leftmost values.
    candidates: list[str] = []
    raw_host = request.headers.get("host", "").strip()
    if raw_host:
        candidates.append(raw_host)
    fwd_host_hdr = request.headers.get("x-forwarded-host", "").strip()
    if fwd_host_hdr:
        parts = [p.strip() for p in fwd_host_hdr.split(",") if p.strip()]
        if parts:
            candidates.append(parts[-1])
    if request.url.netloc:
        candidates.append(request.url.netloc)

    chosen_host: str | None = None
    for cand in candidates:
        if _is_trusted_host(cand):
            chosen_host = cand.lower()
            break

    if not chosen_host:
        return DEFAULT_PUBLIC_HUB_URL

    hostname = chosen_host.split(":", 1)[0]
    if is_vercel or hostname in _CANONICAL_HOSTS or _ALLOWED_VERCEL_HOST_RE.fullmatch(hostname):
        scheme = "https"
    else:
        fwd_proto = request.headers.get("x-forwarded-proto", "").split(",")[-1].strip().lower()
        req_scheme = (request.url.scheme or "http").lower()
        scheme = fwd_proto if fwd_proto in {"http", "https"} else (req_scheme if req_scheme in {"http", "https"} else "https")

    return normalize_base_url(f"{scheme}://{chosen_host}")


def _extract_client_ip(request: Request) -> str:
    """Extract a trusted client IP for rate limiting & SSE slot capping (never spoofable leftmost XFF)."""
    # Vercel sets x-vercel-forwarded-for at the edge (cannot be forged by external clients).
    vercel_ip = request.headers.get("x-vercel-forwarded-for", "").strip()
    if vercel_ip:
        return vercel_ip.split(",")[-1].strip()[:64]
    # Caddy / nginx reverse proxies set X-Real-IP to remote_host.
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip.split(",")[0].strip()[:64]
    if request.client and request.client.host:
        return str(request.client.host)[:64]
    return "unknown"


def _parse_last_event_id(since_id: int | None, last_event_id: str | None) -> int | None:
    if since_id is not None:
        return since_id
    if last_event_id is not None and last_event_id.strip():
        try:
            val = int(last_event_id.strip())
            return val if 0 <= val <= 9_223_372_036_854_775_807 else None
        except ValueError:
            return None
    return None


def _build_cors_origins() -> list[str]:
    origins = [
        "https://aiwatchdogs.in",
        "https://www.aiwatchdogs.in",
    ]
    if not _is_production_env():
        origins.extend(
            [
                "http://localhost:5173",
                "http://127.0.0.1:5173",
                "http://localhost:8000",
                "http://127.0.0.1:8000",
            ]
        )
    extra = os.environ.get("AIWATCHDOGS_CORS_ORIGINS", "")
    for item in extra.split(","):
        cleaned = item.strip().rstrip("/")
        if cleaned and cleaned.startswith(("http://", "https://")) and cleaned not in origins:
            origins.append(cleaned)
    return origins


# ---------------------------------------------------------------------------
# Degraded Fail-Closed App & Production App Factory
# ---------------------------------------------------------------------------


def create_degraded_app(*, reason: str) -> FastAPI:
    """Create a fail-closed ASGI app used when production durable storage is unconfigured."""
    logger.error("Starting degraded Hub app: %s", redact_database_url(reason))
    degraded = FastAPI(
        title="AI Watchdogs · India Coordinator Hub (Degraded)",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    degraded.add_middleware(SecurityHeadersMiddleware)

    @degraded.get("/health")
    def degraded_health() -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "degraded",
                "service": "aiwatchdogs-hub",
                "db_ok": False,
                "timestamp": utc_now_iso(),
            },
        )

    @degraded.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def degraded_api(path: str) -> JSONResponse:
        _ = path
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "Service temporarily unavailable."},
        )

    return degraded


def create_app(
    *,
    db_path: str | Path | None = None,
    database_url: str | None = None,
    seed: bool = True,
    web_dist: str | Path | None = None,
    require_postgres: bool = False,
) -> FastAPI:
    """Factory creating a configured AI Watchdogs India Coordinator Hub FastAPI app."""
    db = Database(
        db_path=db_path,
        database_url=database_url,
        require_postgres=require_postgres,
    )
    if seed:
        seed_database(db)
        sync_projects(db)
    broker = SSEBroker(db=db)
    desk = DeskService(db=db, broker=broker)
    dist_dir = _resolve_web_dist(web_dist)
    is_prod = _is_production_env()
    enable_docs = (not is_prod) or (os.environ.get("AIWATCHDOGS_ENABLE_DOCS") == "1")

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        try:
            yield
        finally:
            db.close()

    application = FastAPI(
        title="AI Watchdogs · India Coordinator Hub",
        version="0.1.0",
        description=(
            "Open-source distributed AI civic-audit swarm auditing Government of India public "
            "spending, tenders, CAG audit paras, budgets, and welfare schemes."
        ),
        docs_url="/docs" if enable_docs else None,
        redoc_url="/redoc" if enable_docs else None,
        openapi_url="/openapi.json" if enable_docs else None,
        lifespan=_lifespan,
    )

    application.state.db = db
    application.state.broker = broker
    application.state.desk = desk
    application.state.web_dist = dist_dir

    def _sanitize_validation_obj(obj: Any) -> Any:
        if isinstance(obj, float):
            return repr(obj) if not math.isfinite(obj) else obj
        if isinstance(obj, dict):
            return {str(k): _sanitize_validation_obj(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [_sanitize_validation_obj(v) for v in obj]
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="replace")
        return obj

    @application.exception_handler(RequestValidationError)
    async def _validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # Ensure non-finite floats (NaN/Infinity) inside Pydantic error details serialize cleanly as HTTP 422.
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": _sanitize_validation_obj(exc.errors())},
        )

    # Middleware order (inner -> outer): MaxBodySize -> CORS -> SecurityHeaders
    application.add_middleware(MaxBodySizeMiddleware, max_bytes=MAX_REQUEST_BODY_BYTES)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=_build_cors_origins(),
        allow_origin_regex=r"^https://aiwatchdogs(?:-[a-z0-9-]+)?\.vercel\.app$",
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Agent-Token", "Idempotency-Key", "Last-Event-ID"],
    )
    application.add_middleware(SecurityHeadersMiddleware)

    # ------------------------------------------------------------------
    # Onboarding & Health
    # ------------------------------------------------------------------

    @application.get("/health", response_model=None)
    def health() -> JSONResponse | dict[str, Any]:
        db_ok = db.health_check()
        if _is_production_env():
            payload: dict[str, Any] = {
                "status": "ok" if db_ok else "degraded",
                "db_ok": db_ok,
                "timestamp": utc_now_iso(),
            }
        else:
            schema_ver = db.schema_version() if db_ok else 0
            payload = {
                "status": "ok" if db_ok else "degraded",
                "service": "aiwatchdogs-hub",
                "version": "0.1.0",
                "db_ok": db_ok,
                "db_backend": db.backend,
                "schema_version": schema_ver,
                "sse_subscribers": broker.subscriber_count,
                "timestamp": utc_now_iso(),
            }
        if not db_ok:
            return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=payload)
        return payload

    @application.get("/join.txt", response_class=PlainTextResponse)
    def join_txt(request: Request) -> PlainTextResponse:
        content = render_join_txt(_public_base_url(request))
        return PlainTextResponse(
            content=content,
            media_type="text/plain; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    @application.get("/join.sh")
    def join_sh(request: Request) -> Response:
        content = render_join_sh(_public_base_url(request))
        return Response(
            content=content,
            media_type="text/x-shellscript; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    # ------------------------------------------------------------------
    # Public Read Plane
    # ------------------------------------------------------------------

    @application.get("/api/head")
    def api_head() -> dict[str, Any]:
        return desk.get_head()

    @application.get("/api/events")
    def api_events(
        since_id: int = Query(default=0, ge=0, le=9_223_372_036_854_775_807),
        limit: int = Query(default=100, ge=1, le=500),
        event_type: str | None = Query(default=None, max_length=64),
    ) -> dict[str, Any]:
        return desk.list_events(since_id=since_id, limit=limit, event_type=event_type)

    @application.get("/api/stream")
    async def api_stream(
        request: Request,
        max_events: int | None = Query(default=None, ge=1, le=500),
        keepalive: float | None = Query(default=None, ge=0.5, le=60.0),
        max_duration: float | None = Query(default=None, ge=0.5, le=300.0),
        since_id: int | None = Query(default=None, ge=0, le=9_223_372_036_854_775_807),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        is_vercel = bool(os.environ.get("VERCEL"))
        client_ip = _extract_client_ip(request)
        try:
            broker.acquire_client_slot(client_ip)
        except SSESubscriberLimitError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=str(exc),
                headers={"Retry-After": "10"},
            ) from exc

        effective_keepalive = max(5.0, keepalive) if keepalive is not None else (10.0 if is_vercel else 15.0)
        max_allowed_dur = 25.0 if is_vercel else 300.0
        default_dur = 25.0 if is_vercel else 120.0
        effective_max_duration = (
            min(float(max_duration), max_allowed_dur) if max_duration is not None else default_dur
        )
        resolved_since = _parse_last_event_id(since_id, last_event_id)
        try:
            return StreamingResponse(
                broker.subscribe(
                    keepalive_seconds=effective_keepalive,
                    max_events=max_events,
                    max_duration_seconds=effective_max_duration,
                    since_id=resolved_since,
                    client_key=client_ip,
                ),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache, no-store",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
        except Exception:
            broker.release_client_slot(client_ip)
            raise

    @application.get("/api/missions")
    def api_missions() -> dict[str, Any]:
        return desk.list_missions()

    @application.get("/api/missions/{slug}")
    def api_mission_detail(slug: str) -> dict[str, Any]:
        return desk.get_mission_detail(slug)

    @application.get("/api/findings")
    def api_findings(
        status_filter: str | None = Query(default=None, alias="status", max_length=40),
        pillar: str | None = Query(default=None, max_length=64),
        methodology: str | None = Query(default=None, max_length=32),
        mission: str | None = Query(default=None, max_length=120),
        search: str | None = Query(default=None, max_length=120),
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0, le=50_000),
    ) -> dict[str, Any]:
        return desk.list_findings(
            status_filter=status_filter,
            pillar=pillar,
            methodology=methodology,
            mission=mission,
            search=search,
            limit=limit,
            offset=offset,
        )

    @application.get("/api/findings/{finding_id}")
    def api_finding_detail(finding_id: str) -> dict[str, Any]:
        return desk.get_finding_detail(finding_id)

    @application.post("/api/findings/{finding_id}/rti-draft")
    def api_finding_rti_draft(
        finding_id: str,
        request: Request,
        body: RTIDraftRequest | None = None,
    ) -> dict[str, Any]:
        desk.check_rate_limit(
            bucket_key=f"rti_draft:{_extract_client_ip(request)}",
            limit=30,
            window_seconds=60,
        )
        authority = body.public_authority if body is not None else None
        preview = body.preview if body is not None else None
        return desk.create_or_get_rti_draft(finding_id, public_authority=authority, preview=preview)

    @application.get("/api/graph")
    def api_graph() -> dict[str, Any]:
        return desk.get_graph()

    @application.get("/api/live")
    def api_live() -> dict[str, Any]:
        return desk.get_live()

    @application.get("/api/contributors")
    def api_contributors(
        window: Literal["all", "hour"] = Query(default="all"),
    ) -> dict[str, Any]:
        return desk.list_contributors(window=window)

    @application.get("/api/contributors/{handle}")
    def api_contributor_profile(handle: str) -> dict[str, Any]:
        return desk.get_contributor_profile(handle)

    @application.get("/api/sponsors")
    def api_sponsors() -> dict[str, Any]:
        return desk.list_sponsors()

    @application.post("/api/sponsors/pledge", status_code=status.HTTP_201_CREATED)
    def api_sponsors_pledge(body: SponsorPledgeRequest, request: Request) -> dict[str, Any]:
        desk.check_rate_limit(
            bucket_key=f"sponsor_pledge:{_extract_client_ip(request)}",
            limit=10,
            window_seconds=60,
        )
        return desk.create_sponsor_pledge(body.model_dump())

    @application.get("/api/ledger", response_model=None)
    @application.get("/api/treasury", response_model=None)
    def api_ledger(
        fmt: Literal["json", "csv"] = Query(default="json", alias="format"),
    ) -> Response | dict[str, Any]:
        if fmt == "csv":
            csv_text = desk.export_ledger_csv()
            return Response(
                content=csv_text,
                media_type="text/csv; charset=utf-8",
                headers={
                    "Content-Disposition": 'attachment; filename="aiwatchdogs-treasury-ledger.csv"'
                },
            )
        return desk.get_ledger()

    @application.get("/api/sources")
    def api_sources() -> dict[str, Any]:
        return desk.list_sources()

    # ------------------------------------------------------------------
    # Desk Coordination Plane
    # ------------------------------------------------------------------

    @application.post("/api/agents/register")
    def api_agents_register(
        body: RegisterAgentRequest,
        request: Request,
        response: Response,
        x_agent_token: str | None = Header(default=None, alias="X-Agent-Token"),
    ) -> dict[str, Any]:
        desk.check_rate_limit(
            bucket_key=f"agent_register:{_extract_client_ip(request)}",
            limit=20,
            window_seconds=60,
        )
        response.headers["Cache-Control"] = "no-store"
        return desk.register_agent(
            body.model_dump(),
            base_url=_public_base_url(request),
            existing_token=x_agent_token,
        )

    @application.post("/api/agents/rotate-token")
    def api_agents_rotate_token(
        request: Request,
        response: Response,
        x_agent_token: str | None = Header(default=None, alias="X-Agent-Token"),
    ) -> dict[str, Any]:
        desk.check_rate_limit(
            bucket_key=f"agent_rotate:{_extract_client_ip(request)}",
            limit=10,
            window_seconds=60,
        )
        response.headers["Cache-Control"] = "no-store"
        return desk.rotate_agent_token(token=x_agent_token)

    @application.post("/api/agents/revoke")
    def api_agents_revoke(
        request: Request,
        response: Response,
        x_agent_token: str | None = Header(default=None, alias="X-Agent-Token"),
    ) -> dict[str, Any]:
        desk.check_rate_limit(
            bucket_key=f"agent_revoke:{_extract_client_ip(request)}",
            limit=10,
            window_seconds=60,
        )
        response.headers["Cache-Control"] = "no-store"
        return desk.revoke_agent_token(token=x_agent_token)

    @application.post("/api/desk/next", response_model=None)
    def api_desk_next(
        body: DeskNextRequest,
        request: Request,
        x_agent_token: str | None = Header(default=None, alias="X-Agent-Token"),
    ) -> Response | dict[str, Any]:
        desk.check_rate_limit(
            bucket_key=f"desk_next:{_extract_client_ip(request)}",
            limit=60,
            window_seconds=60,
        )
        claimed = desk.claim_next_task(
            token=x_agent_token,
            handle=body.handle,
            mode=body.mode,
            preferred_mission=body.preferred_mission,
        )
        if claimed is None:
            return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"Cache-Control": "no-store"})
        return JSONResponse(content=claimed, headers={"Cache-Control": "no-store"})

    @application.post("/api/desk/beat")
    def api_desk_beat(
        body: DeskBeatRequest,
        request: Request,
        response: Response,
        x_agent_token: str | None = Header(default=None, alias="X-Agent-Token"),
    ) -> dict[str, Any]:
        desk.check_rate_limit(
            bucket_key=f"desk_beat:{_extract_client_ip(request)}",
            limit=120,
            window_seconds=60,
        )
        response.headers["Cache-Control"] = "no-store"
        return desk.heartbeat(
            lease_id=body.lease_id,
            token=x_agent_token,
            whisper_text=body.whisper,
            progress_pct=body.progress_pct,
        )

    @application.post("/api/desk/release")
    def api_desk_release(
        body: DeskReleaseRequest,
        request: Request,
        response: Response,
        x_agent_token: str | None = Header(default=None, alias="X-Agent-Token"),
    ) -> dict[str, Any]:
        desk.check_rate_limit(
            bucket_key=f"desk_release:{_extract_client_ip(request)}",
            limit=60,
            window_seconds=60,
        )
        response.headers["Cache-Control"] = "no-store"
        return desk.release_task(
            lease_id=body.lease_id,
            reason=body.reason,
            notes=body.notes,
            token=x_agent_token,
        )

    @application.post("/api/desk/submit")
    def api_desk_submit(
        body: DeskSubmitRequest,
        request: Request,
        response: Response,
        x_agent_token: str | None = Header(default=None, alias="X-Agent-Token"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        desk.check_rate_limit(
            bucket_key=f"desk_submit:{_extract_client_ip(request)}",
            limit=60,
            window_seconds=60,
        )
        response.headers["Cache-Control"] = "no-store"
        return desk.submit_task(
            body.model_dump(),
            token=x_agent_token,
            idempotency_key=idempotency_key or body.idempotency_key,
        )

    # ------------------------------------------------------------------
    # Built Frontend SPA Serving (web/dist)
    # ------------------------------------------------------------------

    def _current_dist() -> Path:
        configured = application.state.web_dist
        if configured and Path(configured).is_dir():
            return Path(configured)
        return _resolve_web_dist(None)

    @application.get("/assets/{file_path:path}", include_in_schema=False)
    def serve_assets(file_path: str) -> Response:
        assets_root = (_current_dist() / "assets").resolve()
        candidate = (assets_root / file_path).resolve()
        if assets_root.is_dir() and candidate.is_file() and candidate.is_relative_to(assets_root):
            return FileResponse(candidate)
        return JSONResponse(status_code=404, content={"detail": "Asset not found"})

    @application.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str) -> Response:
        norm_path = full_path.strip("/")
        if norm_path.startswith("api/") or norm_path == "api":
            return JSONResponse(status_code=404, content={"detail": "API endpoint not found"})
        if not enable_docs and norm_path in {"docs", "docs/oauth2-redirect", "redoc", "openapi.json"}:
            return JSONResponse(status_code=404, content={"detail": "Not found"})

        dist = _current_dist().resolve()
        if full_path:
            static_candidate = (dist / full_path).resolve()
            if dist.is_dir() and static_candidate.is_file() and static_candidate.is_relative_to(dist):
                return FileResponse(static_candidate)

        index_html = dist / "index.html"
        if index_html.is_file():
            return FileResponse(index_html)

        if _is_production_env():
            return JSONResponse(status_code=404, content={"detail": "Not found"})

        return HTMLResponse(
            content=(
                "<!doctype html><html lang='en'><head><meta charset='utf-8'/>"
                "<title>AI Watchdogs · India Hub</title></head>"
                "<body style='font-family:system-ui,sans-serif;max-width:680px;margin:3rem auto;padding:1rem;background:#F6F4EF;color:#1B1A17;'>"
                "<h1>AI Watchdogs · India — Coordinator Hub API</h1>"
                "<p>The FastAPI Coordinator Hub is live. Build the frontend via <code>cd web && npm ci && npm run build</code> "
                "or run Vite dev server on <code>http://127.0.0.1:5173</code>.</p>"
                "<ul>"
                "<li><a href='/health'>/health</a></li>"
                "<li><a href='/api/head'>/api/head</a></li>"
                "<li><a href='/api/missions'>/api/missions</a></li>"
                "<li><a href='/api/findings'>/api/findings</a></li>"
                "<li><a href='/api/treasury'>/api/treasury</a></li>"
                "<li><a href='/api/events'>/api/events</a></li>"
                "<li><a href='/join.txt'>/join.txt</a></li>"
                "<li><a href='/docs'>/docs (OpenAPI)</a></li>"
                "</ul></body></html>"
            ),
            status_code=200,
        )

    return application


_DEFAULT_APP: FastAPI | None = None


def get_default_app() -> FastAPI:
    """Return the lazily-initialized process-wide default Hub ASGI application.

    Fails closed with a degraded 503 ASGI app when running in production
    (``VERCEL=1`` or ``AIWATCHDOGS_REQUIRE_POSTGRES=1``) without ``DATABASE_URL``
    or ``POSTGRES_URL`` configured, or if database startup fails.
    """
    if is_postgres_required() and resolve_database_url() is None:
        return create_degraded_app(
            reason="Durable PostgreSQL storage is required in production (DATABASE_URL or POSTGRES_URL not configured)."
        )
    global _DEFAULT_APP
    if _DEFAULT_APP is None:
        try:
            _DEFAULT_APP = create_app()
        except Exception as exc:
            logger.exception("Failed to initialize Coordinator Hub app: %s", redact_database_url(str(exc)))
            return create_degraded_app(reason="Coordinator Hub database initialization failed.")
    return _DEFAULT_APP


def __getattr__(name: str) -> Any:
    if name == "app":
        return get_default_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def run_cli() -> None:
    """CLI entrypoint for `aiwatchdogs-hub` running uvicorn."""
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="Run the AI Watchdogs India Coordinator Hub.")
    parser.add_argument("--host", default=os.environ.get("AIWATCHDOGS_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("AIWATCHDOGS_PORT", "8000")))
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    args = parser.parse_args()

    uvicorn.run(
        "aiwatchdogs.hub.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
