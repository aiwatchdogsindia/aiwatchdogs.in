"""FastAPI Coordinator Hub application, REST/SSE routes, SPA serving, and CLI entrypoint."""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import Any, Literal

mimetypes.add_type("font/woff2", ".woff2")

from fastapi import FastAPI, Header, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from aiwatchdogs.hub.database import (
    Database,
    is_postgres_required,
    resolve_database_url,
    utc_now_iso,
)
from aiwatchdogs.hub.desk import DeskService
from aiwatchdogs.hub.seed import seed_database
from aiwatchdogs.projects import sync_projects
from aiwatchdogs.hub.sse import SSEBroker
from aiwatchdogs.hub.templates import render_join_sh, render_join_txt

# ---------------------------------------------------------------------------
# Request Schemas
# ---------------------------------------------------------------------------


class RegisterAgentRequest(BaseModel):
    handle: str
    display_name: str | None = None
    provider: str = "rule-engine"
    model: str = "deterministic-cpu-v1"
    mode: str = "auto"
    region: str = "IN"
    capabilities: list[str] = Field(default_factory=list)


class DeskNextRequest(BaseModel):
    handle: str | None = None
    mode: str | None = None
    preferred_mission: str | None = None


class DeskBeatRequest(BaseModel):
    lease_id: str
    whisper: str | None = None
    progress_pct: int | None = None


class DeskReleaseRequest(BaseModel):
    lease_id: str
    reason: str = "released"
    notes: str | None = None


class DeskSubmitRequest(BaseModel):
    lease_id: str
    outcome: Literal["finding", "cleared", "verification"]
    whisper: str | None = None
    finding: dict[str, Any] | None = None
    verification: dict[str, Any] | None = None
    metrics: dict[str, Any] | None = None
    idempotency_key: str | None = None


class RTIDraftRequest(BaseModel):
    public_authority: str | None = None
    preview: bool | None = None


class SponsorPledgeRequest(BaseModel):
    name: str
    handle: str | None = None
    handle_or_org: str | None = None
    tier: str | None = None
    amount_inr: float = 0.0
    in_kind_value_inr: float = 0.0
    compute_credits_desc: str | None = None
    url: str | None = None
    message: str | None = None
    note: str | None = None


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


def _public_base_url(request: Request) -> str:
    env_url = os.environ.get("AIWATCHDOGS_PUBLIC_URL")
    if env_url:
        return env_url.rstrip("/")

    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    forwarded_host = request.headers.get("x-forwarded-host", "").split(",")[0].strip()
    if forwarded_proto or forwarded_host:
        scheme = forwarded_proto or ("https" if os.environ.get("VERCEL") else request.url.scheme)
        host = forwarded_host or request.headers.get("host", "").split(",")[0].strip() or request.url.netloc
        if host:
            return f"{scheme}://{host}".rstrip("/")

    if os.environ.get("VERCEL"):
        host = request.headers.get("host", "").split(",")[0].strip() or request.url.netloc
        if host:
            return f"https://{host}".rstrip("/")

    return str(request.base_url).rstrip("/")


def _parse_last_event_id(since_id: int | None, last_event_id: str | None) -> int | None:
    if since_id is not None:
        return since_id
    if last_event_id is not None and last_event_id.strip():
        try:
            val = int(last_event_id.strip())
            return val if val >= 0 else None
        except ValueError:
            return None
    return None


def create_degraded_app(*, reason: str) -> FastAPI:
    """Create a fail-closed ASGI app used when production durable storage is unconfigured."""
    degraded = FastAPI(
        title="AI Watchdogs · India Coordinator Hub (Degraded)",
        version="0.1.0",
    )

    @degraded.get("/health")
    def degraded_health() -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "degraded",
                "service": "aiwatchdogs-hub",
                "version": "0.1.0",
                "db_ok": False,
                "db_backend": "unconfigured",
                "schema_version": 0,
                "sse_subscribers": 0,
                "error": reason,
                "timestamp": utc_now_iso(),
            },
        )

    @degraded.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def degraded_api(path: str) -> JSONResponse:
        _ = path
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": f"Service unavailable: {reason}"},
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

    application = FastAPI(
        title="AI Watchdogs · India Coordinator Hub",
        version="0.1.0",
        description=(
            "Open-source distributed AI civic-audit swarm auditing Government of India public "
            "spending, tenders, CAG audit paras, budgets, and welfare schemes."
        ),
    )

    application.state.db = db
    application.state.broker = broker
    application.state.desk = desk
    application.state.web_dist = dist_dir

    application.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
            "https://aiwatchdogs.in",
            "https://www.aiwatchdogs.in",
        ],
        allow_origin_regex=r"^https://[a-z0-9-]+\.vercel\.app$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------
    # Onboarding & Health
    # ------------------------------------------------------------------

    @application.get("/health", response_model=None)
    def health() -> JSONResponse | dict[str, Any]:
        db_ok = db.health_check()
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
        return PlainTextResponse(content=content, media_type="text/plain; charset=utf-8")

    @application.get("/join.sh")
    def join_sh(request: Request) -> Response:
        content = render_join_sh(_public_base_url(request))
        return Response(content=content, media_type="text/x-shellscript; charset=utf-8")

    # ------------------------------------------------------------------
    # Public Read Plane
    # ------------------------------------------------------------------

    @application.get("/api/head")
    def api_head() -> dict[str, Any]:
        return desk.get_head()

    @application.get("/api/events")
    def api_events(
        since_id: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
        event_type: str | None = Query(default=None),
    ) -> dict[str, Any]:
        return desk.list_events(since_id=since_id, limit=limit, event_type=event_type)

    @application.get("/api/stream")
    async def api_stream(
        max_events: int | None = Query(default=None, ge=1, le=500),
        keepalive: float | None = Query(default=None, ge=0.5, le=60.0),
        max_duration: float | None = Query(default=None, ge=0.5, le=300.0),
        since_id: int | None = Query(default=None, ge=0),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        is_vercel = bool(os.environ.get("VERCEL"))
        effective_keepalive = keepalive if keepalive is not None else (10.0 if is_vercel else 15.0)
        effective_max_duration = max_duration if max_duration is not None else (25.0 if is_vercel else None)
        resolved_since = _parse_last_event_id(since_id, last_event_id)
        return StreamingResponse(
            broker.subscribe(
                keepalive_seconds=effective_keepalive,
                max_events=max_events,
                max_duration_seconds=effective_max_duration,
                since_id=resolved_since,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @application.get("/api/missions")
    def api_missions() -> dict[str, Any]:
        return desk.list_missions()

    @application.get("/api/missions/{slug}")
    def api_mission_detail(slug: str) -> dict[str, Any]:
        return desk.get_mission_detail(slug)

    @application.get("/api/findings")
    def api_findings(
        status_filter: str | None = Query(default=None, alias="status"),
        pillar: str | None = Query(default=None),
        methodology: str | None = Query(default=None),
        mission: str | None = Query(default=None),
        search: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
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
        body: RTIDraftRequest | None = None,
    ) -> dict[str, Any]:
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
    def api_sponsors_pledge(body: SponsorPledgeRequest) -> dict[str, Any]:
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
        x_agent_token: str | None = Header(default=None, alias="X-Agent-Token"),
    ) -> dict[str, Any]:
        return desk.register_agent(
            body.model_dump(),
            base_url=_public_base_url(request),
            existing_token=x_agent_token,
        )

    @application.post("/api/desk/next", response_model=None)
    def api_desk_next(
        body: DeskNextRequest,
        x_agent_token: str | None = Header(default=None, alias="X-Agent-Token"),
    ) -> Response | dict[str, Any]:
        claimed = desk.claim_next_task(
            token=x_agent_token,
            handle=body.handle,
            mode=body.mode,
            preferred_mission=body.preferred_mission,
        )
        if claimed is None:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        return claimed

    @application.post("/api/desk/beat")
    def api_desk_beat(
        body: DeskBeatRequest,
        x_agent_token: str | None = Header(default=None, alias="X-Agent-Token"),
    ) -> dict[str, Any]:
        return desk.heartbeat(
            lease_id=body.lease_id,
            token=x_agent_token,
            whisper_text=body.whisper,
            progress_pct=body.progress_pct,
        )

    @application.post("/api/desk/release")
    def api_desk_release(
        body: DeskReleaseRequest,
        x_agent_token: str | None = Header(default=None, alias="X-Agent-Token"),
    ) -> dict[str, Any]:
        return desk.release_task(
            lease_id=body.lease_id,
            reason=body.reason,
            notes=body.notes,
            token=x_agent_token,
        )

    @application.post("/api/desk/submit")
    def api_desk_submit(
        body: DeskSubmitRequest,
        x_agent_token: str | None = Header(default=None, alias="X-Agent-Token"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
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
        if full_path.startswith("api/") or full_path == "api":
            return JSONResponse(status_code=404, content={"detail": "API endpoint not found"})

        dist = _current_dist().resolve()
        if full_path:
            static_candidate = (dist / full_path).resolve()
            if dist.is_dir() and static_candidate.is_file() and static_candidate.is_relative_to(dist):
                return FileResponse(static_candidate)

        index_html = dist / "index.html"
        if index_html.is_file():
            return FileResponse(index_html)

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
    or ``POSTGRES_URL`` configured.
    """
    if is_postgres_required() and resolve_database_url() is None:
        return create_degraded_app(
            reason=(
                "Durable PostgreSQL storage is required in production "
                "(DATABASE_URL or POSTGRES_URL is not configured). "
                "Refusing to fall back to ephemeral instance-local SQLite."
            )
        )
    global _DEFAULT_APP
    if _DEFAULT_APP is None:
        _DEFAULT_APP = create_app()
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
