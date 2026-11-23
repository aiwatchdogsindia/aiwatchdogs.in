"""FastAPI Coordinator Hub application, REST/SSE routes, SPA serving, and CLI entrypoint."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from aiwatchdogs.hub.database import Database, utc_now_iso
from aiwatchdogs.hub.desk import DeskService
from aiwatchdogs.hub.seed import seed_database
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


class RTIDraftRequest(BaseModel):
    public_authority: str | None = None


class SponsorPledgeRequest(BaseModel):
    name: str
    handle: str | None = None
    handle_or_org: str | None = None
    tier: str | None = None
    amount_inr: float = 0.0
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
    candidates = [here.parents[3] / "web" / "dist", Path.cwd() / "web" / "dist"]
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[0]


def _public_base_url(request: Request) -> str:
    env_url = os.environ.get("AIWATCHDOGS_PUBLIC_URL")
    if env_url:
        return env_url.rstrip("/")
    return str(request.base_url).rstrip("/")


def create_app(
    *,
    db_path: str | Path | None = None,
    seed: bool = True,
    web_dist: str | Path | None = None,
) -> FastAPI:
    """Factory creating a configured AI Watchdogs India Coordinator Hub FastAPI app."""
    db = Database(db_path=db_path)
    if seed:
        seed_database(db)
    broker = SSEBroker()
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
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------
    # Onboarding & Health
    # ------------------------------------------------------------------

    @application.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "aiwatchdogs-hub",
            "version": "0.1.0",
            "db_ok": db.health_check(),
            "sse_subscribers": broker.subscriber_count,
            "timestamp": utc_now_iso(),
        }

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

    @application.get("/api/stream")
    async def api_stream(
        max_events: int | None = Query(default=None, ge=1, le=500),
        keepalive: float = Query(default=15.0, ge=0.5, le=60.0),
    ) -> StreamingResponse:
        return StreamingResponse(
            broker.subscribe(keepalive_seconds=keepalive, max_events=max_events),
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
        return desk.create_or_get_rti_draft(finding_id, public_authority=authority)

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
    ) -> dict[str, Any]:
        return desk.submit_task(body.model_dump(), token=x_agent_token)

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
        # Serve static root files like favicon.svg, robots.txt, manifest.webmanifest if built
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
                "<p>The FastAPI Coordinator Hub is live. Build the frontend via <code>cd web && npm install && npm run build</code> "
                "or run Vite dev server on <code>http://127.0.0.1:5173</code>.</p>"
                "<ul>"
                "<li><a href='/health'>/health</a></li>"
                "<li><a href='/api/head'>/api/head</a></li>"
                "<li><a href='/api/missions'>/api/missions</a></li>"
                "<li><a href='/api/findings'>/api/findings</a></li>"
                "<li><a href='/join.txt'>/join.txt</a></li>"
                "<li><a href='/docs'>/docs (OpenAPI)</a></li>"
                "</ul></body></html>"
            ),
            status_code=200,
        )

    return application


app = create_app()


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
