"""FastAPI Coordinator Hub application, REST/SSE routes, SPA serving, and CLI entrypoint."""

from __future__ import annotations

import logging
import math
import mimetypes
import os
import re
import threading
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import anyio

mimetypes.add_type("font/woff2", ".woff2")

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field

from aiwatchdogs.core.safety import compute_token_digest
from aiwatchdogs.core.usage import MAX_TOKENS, UsageReport
from aiwatchdogs.hub.agent_access import (
    build_link_header,
    estimate_markdown_tokens,
    handle_mcp_post,
    markdown_response,
    prefers_markdown,
    render_api_catalog,
    render_back_md,
    render_brief_md,
    render_changelog_md,
    render_contributor_md,
    render_finding_detail_md,
    render_findings_md,
    render_graveyard_md,
    render_how_it_works_md,
    render_index_md,
    render_leaderboard_md,
    render_live_md,
    render_llms_full_txt,
    render_llms_txt,
    render_markdown_for_path,
    render_mcp_discovery,
    render_mcp_markdown,
    render_project_detail_md,
    render_projects_md,
    render_robots_txt,
    render_sitemap_xml,
    resolve_spa_markdown_path,
)
from aiwatchdogs.hub.database import (
    Database,
    is_postgres_required,
    is_read_only_deployment,
    redact_database_url,
    resolve_database_url,
    utc_now_iso,
)
from aiwatchdogs.hub.desk import MAX_TASK_PAGE, DeskService
from aiwatchdogs.hub.feed import feed_entries, graveyard_feed_entries, render_atom
from aiwatchdogs.hub.seed import seed_database
from aiwatchdogs.hub.sse import SSEBroker, SSESubscriberLimitError
from aiwatchdogs.hub.templates import DEFAULT_PUBLIC_HUB_URL, normalize_base_url, render_join_sh, render_join_txt
from aiwatchdogs.projects import (
    UNION_BUDGET_PROJECT_SLUG,
    project_codes_by_slug,
    slug_aliases,
    sync_projects,
)

logger = logging.getLogger("aiwatchdogs.hub")

# ---------------------------------------------------------------------------
# Security Policy Constants
# ---------------------------------------------------------------------------

MAX_REQUEST_BODY_BYTES = int(os.environ.get("AIWATCHDOGS_MAX_BODY_BYTES", str(256 * 1024)))

SECURITY_CSP = (
    "default-src 'self'; "
    "script-src 'self' https://www.googletagmanager.com; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https://www.google-analytics.com https://www.googletagmanager.com "
    "https://github.com https://avatars.githubusercontent.com; "
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
# The official CLI names itself in its User-Agent. Builds older than this cannot safely submit
# grouped multi-line UB1 shortfalls or idempotent claims, so they are asked to refresh.
_OFFICIAL_CLI_UA_RE = re.compile(r"^AIWatchdogs-India-AgentCLI/(\d{1,4})\.(\d{1,4})")
MIN_OFFICIAL_CLI_VERSION = (0, 6)
_WEBSITE_BATCH_FILENAME_RE = re.compile(r"^batch-(?:000[1-9]|00[1-9][0-9]|0[1-9][0-9]{2}|1000)\.csv\Z")
_LOCAL_DEV_HOSTS = frozenset({"localhost", "127.0.0.1", "testserver"})
_PUBLIC_CROSS_ORIGIN_ASSET_RE = re.compile(
    r"^/(?:og-[^/]+\.(?:png|jpg|jpeg|webp)|logo[^/]*\.(?:png|svg)|favicon\.(?:svg|ico)|apple-touch-icon[^/]*\.png|agents/[^/]+\.(?:svg|png))\Z"
)

# ---------------------------------------------------------------------------
# Pure ASGI Security & Body-Size Middlewares (SSE-safe, zero response buffering)
# ---------------------------------------------------------------------------


class _RequestBodyTooLargeError(Exception):
    """Internal sentinel raised when a streamed request body exceeds MAX_REQUEST_BODY_BYTES."""


class ReadOnlyMiddleware:
    """Refuse every request that could write, for preview deployments on the live database."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") == "http" and scope.get("method") not in {"GET", "HEAD", "OPTIONS"}:
            # Streamable HTTP MCP is strictly read-only JSON-RPC 2.0 and never opens a write transaction.
            if not (scope.get("method") == "POST" and scope.get("path") == "/mcp"):
                response = JSONResponse(
                    status_code=403,
                    content={"detail": "This is a read-only preview. Use https://aiwatchdogs.in."},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


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

        req_path = str(scope.get("path") or "")
        is_head = str(scope.get("method") or "").upper() == "HEAD"
        corp_value = b"cross-origin" if _PUBLIC_CROSS_ORIGIN_ASSET_RE.fullmatch(req_path) else b"same-origin"
        head_body_sent = False

        async def _send_with_headers(message: dict[str, Any]) -> None:
            nonlocal head_body_sent
            msg_type = message.get("type")
            if msg_type == "http.response.start":
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
                _add(b"cross-origin-resource-policy", corp_value)
                if req_path.startswith("/api/"):
                    _add(b"link", build_link_header(None).encode("ascii"))
                if is_https:
                    _add(b"strict-transport-security", b"max-age=63072000; includeSubDomains; preload")

                message = {**message, "headers": headers}
                await send(message)
                return

            if is_head and msg_type == "http.response.body":
                if not head_body_sent:
                    head_body_sent = True
                    await send({"type": "http.response.body", "body": b"", "more_body": False})
                return

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
    # Optional client-generated high-entropy nonce allowing safe retry if the first registration reply drops.
    registration_key: str | None = Field(default=None, max_length=128)


class DeskNextRequest(BaseModel):
    website_observations: bool = False
    handle: str | None = Field(default=None, max_length=64)
    mode: str | None = Field(default=None, max_length=32)
    preferred_mission: str | None = Field(default=None, max_length=120)
    max_estimated_tokens: int | None = Field(default=None, ge=0, le=MAX_TOKENS)
    task_id: str | None = Field(default=None, max_length=128)
    request_id: str | None = Field(default=None, max_length=128)


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
    outcome: Literal["finding", "cleared", "verification", "observations"]
    observations: list[dict[str, Any]] | None = Field(default=None, max_length=126)
    whisper: str | None = Field(default=None, max_length=600)
    finding: dict[str, Any] | None = None
    findings: list[dict[str, Any]] | None = Field(default=None, max_length=25)
    verification: dict[str, Any] | None = None
    metrics: dict[str, Any] | None = None
    usage: UsageReport | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)


class DeskUsageRequest(BaseModel):
    task_ids: list[str] = Field(..., min_length=1, max_length=20)
    usage: UsageReport


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


def _require_current_cli(request: Request) -> None:
    """Refuse work to an out-of-date official CLI. Hand-written HTTP clients are not affected."""
    match = _OFFICIAL_CLI_UA_RE.match(request.headers.get("user-agent", ""))
    if match and (int(match.group(1)), int(match.group(2))) < MIN_OFFICIAL_CLI_VERSION:
        raise HTTPException(
            status_code=status.HTTP_426_UPGRADE_REQUIRED,
            detail=(
                "This aiwatchdogs CLI build is out of date for the current projects. Run the join command "
                "from /join.txt again with `uvx --refresh --from ...` (or `git pull` in a checkout) and retry."
            ),
            headers={"Upgrade": "aiwatchdogs-cli/%d.%d" % MIN_OFFICIAL_CLI_VERSION},
        )


def _extract_client_ip(request: Request) -> str:
    """Extract a trusted client IP for rate limiting & SSE slot capping (never spoofable leftmost XFF)."""
    # Only trust CF-Connecting-IP when explicitly deployed behind Cloudflare proxy.
    if os.environ.get("AIWATCHDOGS_TRUST_CLOUDFLARE", "").strip().lower() in {"1", "true", "yes"}:
        cf_ip = request.headers.get("cf-connecting-ip", "").strip()
        if cf_ip:
            return cf_ip.split(",")[0].strip()[:64]
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


class _SelfHealingHubMiddleware:
    """ASGI middleware that allows a transiently degraded container to recover automatically.

    When cold-start database initialization fails due to a transient error (e.g., Neon wakeup or
    concurrent startup lock contention), ``api/index.py`` binds the returned degraded FastAPI
    instance once at module import time. This middleware retries initialization at most once per
    cooldown window and transparently delegates all subsequent requests to the healthy app once
    recovery succeeds — preventing permanent 503 zombie serverless instances.
    """

    def __init__(
        self,
        app: Any,
        *,
        retry_factory: Callable[[], FastAPI],
        retry_cooldown_s: float = 3.0,
    ) -> None:
        self.app = app
        self._retry_factory = retry_factory
        self._cooldown_s = max(0.0, float(retry_cooldown_s))
        self._recovered_app: FastAPI | None = None
        self._next_retry_at: float = time.monotonic() + self._cooldown_s
        self._retry_lock = threading.Lock()
        self._retrying = False

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") == "lifespan":
            try:
                await self.app(scope, receive, send)
            finally:
                recovered = self._recovered_app
                if recovered is not None:
                    db = getattr(getattr(recovered, "state", None), "db", None)
                    if db is not None:
                        try:
                            db.close()
                        except Exception:
                            pass
            return

        recovered = self._recovered_app
        if recovered is not None:
            await recovered(scope, receive, send)
            return

        if scope.get("type") == "http":
            should_retry = False
            with self._retry_lock:
                if self._recovered_app is None and not self._retrying and time.monotonic() >= self._next_retry_at:
                    self._retrying = True
                    should_retry = True
            if should_retry:
                candidate: FastAPI | None = None
                try:
                    candidate = await anyio.to_thread.run_sync(self._retry_factory)
                except Exception as exc:
                    with self._retry_lock:
                        self._next_retry_at = time.monotonic() + self._cooldown_s
                    logger.warning(
                        "Degraded Hub recovery attempt failed (retrying in %.1fs): %s",
                        self._cooldown_s,
                        redact_database_url(str(exc)),
                    )
                else:
                    with self._retry_lock:
                        self._recovered_app = candidate
                    logger.info("Coordinator Hub self-healed from degraded startup state.")
                finally:
                    with self._retry_lock:
                        self._retrying = False
                if candidate is not None:
                    await candidate(scope, receive, send)
                    return
            if self._recovered_app is not None:
                await self._recovered_app(scope, receive, send)
                return

        await self.app(scope, receive, send)


def create_degraded_app(
    *,
    reason: str,
    retry_factory: Callable[[], FastAPI] | None = None,
    retry_cooldown_s: float = 3.0,
) -> FastAPI:
    """Create a fail-closed ASGI app used when production durable storage is unavailable."""
    logger.error("Starting degraded Hub app: %s", redact_database_url(reason))
    degraded = FastAPI(
        title="AI Watchdogs · India Coordinator Hub (Degraded)",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    degraded.add_middleware(SecurityHeadersMiddleware)
    if retry_factory is not None:
        degraded.add_middleware(
            _SelfHealingHubMiddleware,
            retry_factory=retry_factory,
            retry_cooldown_s=retry_cooldown_s,
        )

    @degraded.get("/health")
    @degraded.head("/health", include_in_schema=False)
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
    read_only = is_read_only_deployment()
    db = Database(
        db_path=db_path,
        database_url=database_url,
        require_postgres=require_postgres,
        read_only=read_only,
    )
    if seed and not read_only:
        try:
            seed_database(db)
            sync_projects(db)
        except Exception:
            try:
                db.close()
            except Exception:
                pass
            raise
    broker = SSEBroker(db=db)
    desk = DeskService(db=db, broker=broker)
    # Renamed projects keep answering at their earlier slug (page, API and feed links).
    try:
        # Project files the sync has already accepted are read here without being validated again.
        desk.slug_aliases = slug_aliases(db=db)
        desk.project_codes = project_codes_by_slug(db=db)
    except Exception as exc:  # a bad project file must never stop the hub from starting
        logger.warning("Could not read project slug aliases: %s", exc)
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

    if read_only:
        application.add_middleware(ReadOnlyMiddleware)

    # Middleware order (inner -> outer): MaxBodySize -> CORS -> SecurityHeaders
    application.add_middleware(MaxBodySizeMiddleware, max_bytes=MAX_REQUEST_BODY_BYTES)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=_build_cors_origins(),
        allow_origin_regex=r"^https://aiwatchdogs(?:-[a-z0-9-]+)?\.vercel\.app$",
        allow_credentials=False,
        allow_methods=["GET", "HEAD", "POST", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "Accept",
            "X-Agent-Token",
            "Idempotency-Key",
            "Last-Event-ID",
            "MCP-Protocol-Version",
            "Mcp-Session-Id",
        ],
        expose_headers=["X-Markdown-Tokens", "MCP-Protocol-Version", "Link"],
    )
    application.add_middleware(SecurityHeadersMiddleware)

    # ------------------------------------------------------------------
    # Onboarding & Health
    # ------------------------------------------------------------------

    @application.get("/health", response_model=None)
    @application.head("/health", response_model=None, include_in_schema=False)
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

    def _atom(request: Request, path: str, title: str, page: str, entries: list[dict[str, str]]) -> Response:
        return Response(
            content=render_atom(entries, base_url=_public_base_url(request), path=path, title=title, page=page),
            media_type="application/atom+xml",
            headers={"Cache-Control": "public, max-age=120, s-maxage=300, stale-while-revalidate=600"},
        )

    @application.get("/feed.atom")
    @application.head("/feed.atom", include_in_schema=False)
    def feed_atom(request: Request) -> Response:
        with db.read() as conn:
            entries = feed_entries(conn)
        return _atom(request, "/feed.atom", "AI Watchdogs India: every change of state", "/", entries)

    @application.get("/graveyard.atom")
    @application.head("/graveyard.atom", include_in_schema=False)
    def graveyard_atom(request: Request) -> Response:
        with db.read() as conn:
            entries = graveyard_feed_entries(conn)
        return _atom(
            request,
            "/graveyard.atom",
            "AI Watchdogs India: the graveyard",
            "/graveyard",
            entries,
        )

    @application.get("/feeds/{slug}.atom")
    @application.head("/feeds/{slug}.atom", include_in_schema=False)
    def project_feed_atom(slug: str, request: Request) -> Response:
        slug = desk.resolve_slug(slug)
        with db.read() as conn:
            row = conn.execute(
                "SELECT slug, title FROM missions WHERE slug = ? AND is_community_project = 1", (slug,)
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Project not found")
            entries = feed_entries(conn, slug=str(row["slug"]))
        return _atom(
            request,
            f"/feeds/{row['slug']}.atom",
            f"AI Watchdogs India: {row['title']}",
            f"/projects/{row['slug']}",
            entries,
        )

    @application.get("/join.txt", response_class=PlainTextResponse)
    @application.head("/join.txt", response_class=PlainTextResponse, include_in_schema=False)
    def join_txt(request: Request) -> PlainTextResponse:
        content = render_join_txt(_public_base_url(request))
        return PlainTextResponse(
            content=content,
            media_type="text/plain; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    @application.get("/join.sh")
    @application.head("/join.sh", include_in_schema=False)
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

    _PUBLIC_READ_CACHE = "public, max-age=10, s-maxage=30, stale-while-revalidate=120"
    _DETAIL_READ_CACHE = "public, max-age=15, s-maxage=60, stale-while-revalidate=300"
    _LIVE_SWARM_CACHE = "public, max-age=0, s-maxage=10, stale-while-revalidate=30"
    _EVENTS_READ_CACHE = "public, max-age=0, s-maxage=5, stale-while-revalidate=15"
    _OBSERVATIONS_CACHE = "public, max-age=300, s-maxage=3600, stale-while-revalidate=86400"
    _MATRIX_CACHE = "public, max-age=3600, s-maxage=86400, stale-while-revalidate=86400"

    @application.get("/api/head")
    def api_head(response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = _PUBLIC_READ_CACHE
        return desk.get_head()

    @application.get("/api/events")
    def api_events(
        response: Response,
        since_id: int = Query(default=0, ge=0, le=9_223_372_036_854_775_807),
        limit: int = Query(default=100, ge=1, le=500),
        event_type: str | None = Query(default=None, max_length=64),
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = _EVENTS_READ_CACHE
        return desk.list_events(since_id=since_id, limit=limit, event_type=event_type)

    @application.get("/api/stream", response_model=None)
    async def api_stream(
        request: Request,
        max_events: int | None = Query(default=None, ge=1, le=500),
        keepalive: float | None = Query(default=None, ge=0.5, le=60.0),
        max_duration: float | None = Query(default=None, ge=0.5, le=300.0),
        since_id: int | None = Query(default=None, ge=0, le=9_223_372_036_854_775_807),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> Response:
        is_vercel = bool(os.environ.get("VERCEL"))
        if is_vercel and os.environ.get("AIWATCHDOGS_ENABLE_SSE", "").strip().lower() not in {"1", "true", "yes"}:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={
                    "detail": "Live SSE stream is disabled on serverless deployments; poll /api/swarm/live or /api/events instead."
                },
                headers={"Cache-Control": "public, max-age=60, s-maxage=300"},
            )
        client_ip = _extract_client_ip(request)
        try:
            broker.acquire_client_slot(client_ip)
        except SSESubscriberLimitError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=str(exc),
                headers={"Retry-After": "10"},
            ) from exc

        effective_keepalive = max(5.0, keepalive) if keepalive is not None else (15.0 if is_vercel else 15.0)
        max_allowed_dur = 45.0 if is_vercel else 300.0
        default_dur = 45.0 if is_vercel else 120.0
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
    def api_missions(response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = _PUBLIC_READ_CACHE
        return desk.list_missions()

    @application.get("/api/missions/{slug}")
    def api_mission_detail(slug: str, response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = _DETAIL_READ_CACHE
        return desk.get_mission_detail(slug)

    @application.get("/api/missions/{slug}/observations/{task_id}")
    def website_observations(slug: str, task_id: str, response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = _OBSERVATIONS_CACHE
        return desk.website_observations(slug, task_id)

    @application.get("/api/missions/{slug}/matrix")
    def api_mission_matrix(slug: str, response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = _MATRIX_CACHE
        return desk.get_website_matrix(slug)

    @application.get("/api/missions/gov-in-public-information/batches/{filename}")
    def api_website_batch_csv(filename: str) -> Response:
        if not _WEBSITE_BATCH_FILENAME_RE.fullmatch(filename):
            raise HTTPException(status_code=404, detail="Batch manifest not found")
        from aiwatchdogs.projects import default_projects_dir

        expected_dir = (default_projects_dir() / "gov-in-public-information").resolve()
        batch_path = (expected_dir / filename).resolve()
        if batch_path.parent != expected_dir or not batch_path.is_file():
            raise HTTPException(status_code=404, detail="Batch manifest not found")
        return Response(
            content=batch_path.read_bytes(),
            media_type="text/csv; charset=utf-8",
            headers={"Cache-Control": "public, max-age=86400, immutable"},
        )

    @application.get("/api/missions/{slug}/tasks")
    def api_mission_tasks(
        slug: str,
        response: Response,
        task_status: str = Query(default="all", alias="status", max_length=12),
        q: str = Query(default="", max_length=120),
        kind: str = Query(default="audit", max_length=8),
        offset: int = Query(default=0, ge=0, le=1_000_000),
        limit: int = Query(default=25, ge=1, le=MAX_TASK_PAGE),
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = _DETAIL_READ_CACHE
        return desk.list_mission_tasks(slug, status_filter=task_status, q=q, kind=kind, offset=offset, limit=limit)

    @application.get(f"/api/missions/{UNION_BUDGET_PROJECT_SLUG}/{{filename}}")
    def api_union_budget_file(filename: str) -> Response:
        """The statements the Union Budget project reads (URL and SHA-256 of each) and its coverage summary."""
        media_types = {"documents.csv": "text/csv; charset=utf-8", "coverage.json": "application/json"}
        if filename not in media_types:
            raise HTTPException(status_code=404, detail="Not found")
        from aiwatchdogs.projects import default_projects_dir

        path = default_projects_dir() / UNION_BUDGET_PROJECT_SLUG / filename
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Not found")
        return Response(
            content=path.read_bytes(),
            media_type=media_types[filename],
            headers={"Cache-Control": "public, max-age=3600"},
        )

    @application.get("/api/findings")
    def api_findings(
        response: Response,
        status_filter: str | None = Query(default=None, alias="status", max_length=40),
        pillar: str | None = Query(default=None, max_length=64),
        methodology: str | None = Query(default=None, max_length=32),
        mission: str | None = Query(default=None, max_length=120),
        search: str | None = Query(default=None, max_length=120),
        limit: int = Query(default=24, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=50_000),
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = _DETAIL_READ_CACHE
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
    def api_finding_detail(finding_id: str, response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = _DETAIL_READ_CACHE
        return desk.get_finding_detail(finding_id)

    @application.get("/api/graveyard")
    def api_graveyard(
        response: Response,
        cause: str | None = Query(default=None, max_length=40),
        project: str | None = Query(default=None, max_length=120),
        limit: int = Query(default=24, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=50_000),
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = _PUBLIC_READ_CACHE
        return desk.list_graveyard(cause=cause, project=project, limit=limit, offset=offset)

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
    def api_graph(response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = "public, max-age=60, s-maxage=300, stale-while-revalidate=900"
        return desk.get_graph()

    @application.get("/api/live")
    def api_live(response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = _DETAIL_READ_CACHE
        return desk.get_live()

    @application.get("/api/swarm/live")
    def api_swarm_live(response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = _LIVE_SWARM_CACHE
        return desk.get_swarm_live()

    @application.get("/api/contributors")
    def api_contributors(
        response: Response,
        window: Literal["all", "hour"] = Query(default="all"),
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = _PUBLIC_READ_CACHE
        return desk.list_contributors(window=window)

    @application.get("/api/contributors/{handle}")
    def api_contributor_profile(handle: str, response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = _DETAIL_READ_CACHE
        return desk.get_contributor_profile(handle)

    @application.get("/api/sponsors")
    def api_sponsors(response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = _DETAIL_READ_CACHE
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
        response: Response,
        fmt: Literal["json", "csv"] = Query(default="json", alias="format"),
    ) -> Response | dict[str, Any]:
        if fmt == "csv":
            csv_text = desk.export_ledger_csv()
            return Response(
                content=csv_text,
                media_type="text/csv; charset=utf-8",
                headers={
                    "Content-Disposition": 'attachment; filename="aiwatchdogs-treasury-ledger.csv"',
                    "Cache-Control": _DETAIL_READ_CACHE,
                },
            )
        response.headers["Cache-Control"] = _DETAIL_READ_CACHE
        return desk.get_ledger()

    @application.get("/api/sources")
    def api_sources(response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = _DETAIL_READ_CACHE
        return desk.list_sources()

    # ------------------------------------------------------------------
    # Desk Coordination Plane (unlimited volunteer contributions — zero rate-limit throttling)
    # ------------------------------------------------------------------

    @application.post("/api/agents/register")
    def api_agents_register(
        body: RegisterAgentRequest,
        request: Request,
        response: Response,
        x_agent_token: str | None = Header(default=None, alias="X-Agent-Token"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        _require_current_cli(request)
        response.headers["Cache-Control"] = "no-store"
        return desk.register_agent(
            body.model_dump(),
            base_url=_public_base_url(request),
            existing_token=x_agent_token,
            registration_key=idempotency_key or body.registration_key,
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
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> Response | dict[str, Any]:
        _require_current_cli(request)
        claimed = desk.claim_next_task(
            token=x_agent_token,
            handle=body.handle,
            mode=body.mode,
            preferred_mission=body.preferred_mission,
            max_estimated_tokens=body.max_estimated_tokens,
            website_observations=body.website_observations,
            task_id=body.task_id,
            request_id=idempotency_key or body.request_id,
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
        response.headers["Cache-Control"] = "no-store"
        return desk.release_task(
            lease_id=body.lease_id,
            reason=body.reason,
            notes=body.notes,
            token=x_agent_token,
        )

    @application.post("/api/desk/usage")
    def api_desk_usage(
        body: DeskUsageRequest,
        request: Request,
        response: Response,
        x_agent_token: str | None = Header(default=None, alias="X-Agent-Token"),
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        return desk.report_usage(task_ids=body.task_ids, usage=body.usage, token=x_agent_token)

    @application.post("/api/desk/submit")
    def api_desk_submit(
        body: DeskSubmitRequest,
        request: Request,
        response: Response,
        x_agent_token: str | None = Header(default=None, alias="X-Agent-Token"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        _require_current_cli(request)
        response.headers["Cache-Control"] = "no-store"
        return desk.submit_task(
            body.model_dump(),
            token=x_agent_token,
            idempotency_key=idempotency_key or body.idempotency_key,
        )

    # ------------------------------------------------------------------
    # Agent Discovery, Crawlability, Markdown Pages & Streamable HTTP MCP
    # ------------------------------------------------------------------

    @application.get("/llms.txt", response_class=PlainTextResponse)
    @application.head("/llms.txt", response_class=PlainTextResponse, include_in_schema=False)
    def llms_txt(request: Request) -> PlainTextResponse:
        content = render_llms_txt(desk, _public_base_url(request))
        return PlainTextResponse(
            content=content,
            media_type="text/plain; charset=utf-8",
            headers={
                "X-Markdown-Tokens": str(estimate_markdown_tokens(content)),
                "Link": build_link_header("/index.md"),
                "Cache-Control": "public, max-age=300, s-maxage=1800, stale-while-revalidate=3600",
            },
        )

    @application.get("/llms-full.txt", response_model=None)
    @application.head("/llms-full.txt", response_model=None, include_in_schema=False)
    def llms_full_txt() -> RedirectResponse:
        # Redirect heavy combined dump to lightweight /llms.txt index to prevent crawler DB egress amplification.
        return RedirectResponse(
            url="/llms.txt",
            status_code=status.HTTP_308_PERMANENT_REDIRECT,
            headers={"Cache-Control": "public, max-age=3600, s-maxage=86400"},
        )

    @application.get("/robots.txt", response_class=PlainTextResponse)
    @application.head("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
    def robots_txt(request: Request) -> PlainTextResponse:
        robots_body = render_robots_txt(_public_base_url(request))
        if "User-agent: Twitterbot" not in robots_body:
            robots_body = robots_body.replace(
                "User-agent: OAI-SearchBot",
                "User-agent: Twitterbot\nAllow: /\n\nUser-agent: OAI-SearchBot",
                1,
            )
        return PlainTextResponse(
            content=robots_body,
            media_type="text/plain; charset=utf-8",
            headers={"Cache-Control": "public, max-age=300, s-maxage=3600"},
        )

    @application.get("/sitemap.xml")
    @application.head("/sitemap.xml", include_in_schema=False)
    def sitemap_xml(request: Request) -> Response:
        return Response(
            content=render_sitemap_xml(desk, _public_base_url(request)),
            media_type="application/xml; charset=utf-8",
            headers={"Cache-Control": "public, max-age=300, s-maxage=1800, stale-while-revalidate=3600"},
        )

    @application.get("/.well-known/api-catalog")
    @application.head("/.well-known/api-catalog", include_in_schema=False)
    def well_known_api_catalog(request: Request) -> Response:
        import json as _json

        catalog = render_api_catalog(_public_base_url(request))
        return Response(
            content=_json.dumps(catalog, ensure_ascii=False, indent=2),
            media_type="application/linkset+json; charset=utf-8",
            headers={
                "Link": build_link_header(None),
                "Cache-Control": "public, max-age=300, s-maxage=300",
            },
        )

    @application.get("/.well-known/mcp.json")
    @application.head("/.well-known/mcp.json", include_in_schema=False)
    @application.get("/.well-known/mcp/server-card.json")
    @application.head("/.well-known/mcp/server-card.json", include_in_schema=False)
    def well_known_mcp(request: Request) -> JSONResponse:
        return JSONResponse(
            content=render_mcp_discovery(_public_base_url(request)),
            headers={
                "Link": build_link_header(None),
                "Cache-Control": "public, max-age=300, s-maxage=300",
            },
        )

    @application.get("/mcp", response_model=None)
    @application.head("/mcp", response_model=None, include_in_schema=False)
    def mcp_get(request: Request) -> Response:
        base_url = _public_base_url(request)
        if prefers_markdown(request.headers.get("accept")):
            return markdown_response(render_mcp_markdown(base_url), md_path="/mcp")
        return JSONResponse(
            content=render_mcp_discovery(base_url),
            headers={
                "Vary": "Accept",
                "Link": build_link_header(None),
                "Cache-Control": "public, max-age=60, s-maxage=60",
            },
        )

    @application.post("/mcp", response_model=None)
    async def mcp_post(request: Request) -> Response:
        if os.environ.get("VERCEL") and os.environ.get("AIWATCHDOGS_ENABLE_MCP", "").strip().lower() not in {"1", "true", "yes"}:
            return JSONResponse(
                status_code=status.HTTP_410_GONE,
                content={
                    "detail": "Streamable HTTP MCP is disabled on serverless deployments; use /llms.txt or cached /api/* endpoints."
                },
                headers={"Cache-Control": "public, max-age=60, s-maxage=300"},
            )
        desk.check_rate_limit(
            bucket_key=f"mcp:{_extract_client_ip(request)}",
            limit=120,
            window_seconds=60,
        )
        raw_body = await request.body()
        return handle_mcp_post(desk, _public_base_url(request), raw_body)

    # Explicit .md routes for every site page
    @application.get("/index.md")
    @application.head("/index.md", include_in_schema=False)
    def md_index(request: Request) -> Response:
        return markdown_response(render_index_md(desk, _public_base_url(request)), md_path="/index.md")

    @application.get("/projects.md")
    @application.head("/projects.md", include_in_schema=False)
    @application.get("/missions.md")
    @application.head("/missions.md", include_in_schema=False)
    def md_projects(request: Request) -> Response:
        return markdown_response(render_projects_md(desk, _public_base_url(request)), md_path="/projects.md")

    @application.get("/projects/{slug}.md")
    @application.head("/projects/{slug}.md", include_in_schema=False)
    @application.get("/missions/{slug}.md")
    @application.head("/missions/{slug}.md", include_in_schema=False)
    def md_project_detail(slug: str, request: Request) -> Response:
        resolved = desk.resolve_slug(slug)
        return markdown_response(
            render_project_detail_md(desk, _public_base_url(request), resolved),
            md_path=f"/projects/{resolved}.md",
        )

    @application.get("/findings.md")
    @application.head("/findings.md", include_in_schema=False)
    def md_findings(request: Request) -> Response:
        return markdown_response(render_findings_md(desk, _public_base_url(request)), md_path="/findings.md")

    @application.get("/findings/{finding_id}.md")
    @application.head("/findings/{finding_id}.md", include_in_schema=False)
    def md_finding_detail(finding_id: str, request: Request) -> Response:
        return markdown_response(
            render_finding_detail_md(desk, _public_base_url(request), finding_id),
            md_path=f"/findings/{finding_id}.md",
        )

    @application.get("/live.md")
    @application.head("/live.md", include_in_schema=False)
    def md_live(request: Request) -> Response:
        return markdown_response(render_live_md(desk, _public_base_url(request)), md_path="/live.md")

    @application.get("/leaderboard.md")
    @application.head("/leaderboard.md", include_in_schema=False)
    @application.get("/contributors.md")
    @application.head("/contributors.md", include_in_schema=False)
    def md_leaderboard(request: Request) -> Response:
        return markdown_response(render_leaderboard_md(desk, _public_base_url(request)), md_path="/leaderboard.md")

    @application.get("/a/{handle}.md")
    @application.head("/a/{handle}.md", include_in_schema=False)
    def md_contributor(handle: str, request: Request) -> Response:
        return markdown_response(
            render_contributor_md(desk, _public_base_url(request), handle),
            md_path=f"/a/{handle}.md",
        )

    @application.get("/how-it-works.md")
    @application.head("/how-it-works.md", include_in_schema=False)
    def md_how_it_works(request: Request) -> Response:
        return markdown_response(render_how_it_works_md(desk, _public_base_url(request)), md_path="/how-it-works.md")

    @application.get("/brief.md")
    @application.head("/brief.md", include_in_schema=False)
    def md_brief(request: Request) -> Response:
        return markdown_response(render_brief_md(_public_base_url(request)), md_path="/brief.md")

    @application.get("/changelog.md")
    @application.head("/changelog.md", include_in_schema=False)
    def md_changelog(request: Request) -> Response:
        return markdown_response(render_changelog_md(_public_base_url(request)), md_path="/changelog.md")

    @application.get("/graveyard.md")
    @application.head("/graveyard.md", include_in_schema=False)
    def md_graveyard(request: Request) -> Response:
        return markdown_response(render_graveyard_md(desk, _public_base_url(request)), md_path="/graveyard.md")

    @application.get("/back.md")
    @application.head("/back.md", include_in_schema=False)
    @application.get("/sponsors.md")
    @application.head("/sponsors.md", include_in_schema=False)
    def md_back(request: Request) -> Response:
        return markdown_response(render_back_md(desk, _public_base_url(request)), md_path="/back.md")

    # ------------------------------------------------------------------
    # Built Frontend SPA Serving (web/dist) + Accept: text/markdown Negotiation
    # ------------------------------------------------------------------

    def _current_dist() -> Path:
        configured = application.state.web_dist
        if configured and Path(configured).is_dir():
            return Path(configured)
        return _resolve_web_dist(None)

    @application.api_route("/assets/{file_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def serve_assets(file_path: str) -> Response:
        assets_root = (_current_dist() / "assets").resolve()
        candidate = (assets_root / file_path).resolve()
        if assets_root.is_dir() and candidate.is_file() and candidate.is_relative_to(assets_root):
            return FileResponse(candidate)
        return JSONResponse(status_code=404, content={"detail": "Asset not found"})

    @application.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def spa_fallback(full_path: str, request: Request) -> Response:
        norm_path = full_path.strip("/")
        if norm_path.startswith("api/") or norm_path == "api":
            return JSONResponse(status_code=404, content={"detail": "API endpoint not found"})
        if not enable_docs and norm_path in {"docs", "docs/oauth2-redirect", "redoc", "openapi.json"}:
            return JSONResponse(status_code=404, content={"detail": "Not found"})

        md_alt = resolve_spa_markdown_path(norm_path)
        if md_alt is not None and prefers_markdown(request.headers.get("accept")):
            rendered = render_markdown_for_path(desk, _public_base_url(request), "/" + norm_path)
            if rendered is not None:
                md_text, canon_md = rendered
                return markdown_response(md_text, md_path=canon_md)

        dist = _current_dist().resolve()
        if full_path:
            static_candidate = (dist / full_path).resolve()
            if dist.is_dir() and static_candidate.is_file() and static_candidate.is_relative_to(dist):
                return FileResponse(static_candidate)

        discovery_headers = {
            "Vary": "Accept",
            "Link": build_link_header(md_alt or "/index.md"),
        }

        index_html = dist / "index.html"
        if index_html.is_file():
            return FileResponse(index_html, headers=discovery_headers)

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
                "<li><a href='/index.md'>/index.md</a></li>"
                "<li><a href='/llms.txt'>/llms.txt</a></li>"
                "<li><a href='/mcp'>/mcp</a></li>"
                "<li><a href='/api/head'>/api/head</a></li>"
                "<li><a href='/api/missions'>/api/missions</a></li>"
                "<li><a href='/api/findings'>/api/findings</a></li>"
                "<li><a href='/api/events'>/api/events</a></li>"
                "<li><a href='/join.txt'>/join.txt</a></li>"
                "<li><a href='/docs'>/docs (OpenAPI)</a></li>"
                "</ul></body></html>"
            ),
            status_code=200,
            headers=discovery_headers,
        )

    return application


_DEFAULT_APP: FastAPI | None = None
_DEFAULT_APP_LOCK = threading.Lock()


def _init_default_app() -> FastAPI:
    global _DEFAULT_APP
    with _DEFAULT_APP_LOCK:
        if _DEFAULT_APP is None:
            _DEFAULT_APP = create_app()
        return _DEFAULT_APP


def get_default_app() -> FastAPI:
    """Return the lazily-initialized process-wide default Hub ASGI application.

    Fails closed with a degraded 503 ASGI app when running in production
    (``VERCEL=1`` or ``AIWATCHDOGS_REQUIRE_POSTGRES=1``) without ``DATABASE_URL``
    or ``POSTGRES_URL`` configured, or if database startup fails (with bounded
    self-healing retries for transient startup failures).
    """
    if is_postgres_required() and resolve_database_url() is None:
        return create_degraded_app(
            reason="Durable PostgreSQL storage is required in production (DATABASE_URL or POSTGRES_URL not configured)."
        )
    if _DEFAULT_APP is not None:
        return _DEFAULT_APP
    try:
        return _init_default_app()
    except Exception as exc:
        logger.exception("Failed to initialize Coordinator Hub app: %s", redact_database_url(str(exc)))
        return create_degraded_app(
            reason="Coordinator Hub database initialization failed.",
            retry_factory=_init_default_app,
            retry_cooldown_s=3.0,
        )


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
