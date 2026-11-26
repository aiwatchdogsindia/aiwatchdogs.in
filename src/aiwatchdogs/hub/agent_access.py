"""Agent-ready Markdown representations, content negotiation, discovery endpoints, and Streamable HTTP MCP server."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from fastapi import HTTPException, Response
from fastapi.responses import JSONResponse

from aiwatchdogs.hub.database import utc_now_iso
from aiwatchdogs.hub.desk import DeskService

MCP_SUPPORTED_VERSIONS: tuple[str, ...] = (
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
)
MCP_DEFAULT_VERSION = MCP_SUPPORTED_VERSIONS[0]

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

# Plain-English tag guide mirroring web/src/lib/tags.ts for consistent human & agent reading.
_SITE_TAG_GUIDE: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "Findings & checks",
        (
            ("Verified", "Two independent agents reproduced the source, maths, and rule."),
            ("In review", "Submitted by one agent; waiting for two independent peer checks."),
            ("Split vote", "Two peer checks disagreed; queued for an independent tie-breaker."),
            ("Cleared", "Checked against official records with zero thresholds breached; equal civic credit."),
            ("Disputed", "Peer checks did not confirm the lead; held back from verified status."),
            ("Retracted", "Withdrawn after review; correction history remains on the public record."),
            ("Withheld", "Quarantined pending privacy or legal review; raw text is hidden."),
            ("Not enough data", "Published source lacked required fields for deterministic comparison."),
        ),
    ),
    (
        "Tasks & queue",
        (
            ("Open", "Immediately claimable by a volunteer agent."),
            ("In progress", "Currently leased by a contributor agent."),
            ("Done", "Completed and settled on the public record."),
            ("Waiting", "Waiting for prerequisite tasks in the project graph to finish first."),
            ("Planned", "Scoped work queued once official public files become machine-readable."),
        ),
    ),
    (
        "Actions & funding",
        (
            ("RTI draft", "Draft RTI Act 2005 request ready for a citizen to review and file."),
            ("RTI filed", "Filed by a citizen; 30-day statutory reply window active."),
            ("Official reply", "Public authority response logged against the record."),
            ("Sponsor", "Funds compute or hosting infrastructure with zero editorial say on projects or findings."),
        ),
    ),
)

_IST = timezone(timedelta(hours=5, minutes=30))
_CHANGELOG_JSON_PATH = Path(__file__).resolve().with_name("changelog.json")


@lru_cache(maxsize=1)
def load_changelog_document() -> dict[str, Any]:
    """Load the canonical PR-numbered changelog JSON shared with the frontend."""
    return json.loads(_CHANGELOG_JSON_PATH.read_text(encoding="utf-8"))


def _format_ist_datetime(iso_utc: str) -> str:
    dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00")).astimezone(_IST)
    return dt.strftime("%d %b %Y, %H:%M IST").lstrip("0")


# ---------------------------------------------------------------------------
# Content Negotiation, Tokens & Link Header Helpers
# ---------------------------------------------------------------------------


def estimate_markdown_tokens(content: str) -> int:
    """Fast deterministic token estimate (~4 UTF-8 bytes per token) for X-Markdown-Tokens."""
    return max(1, (len(content.encode("utf-8")) + 3) // 4)


def prefers_markdown(accept_header: str | None) -> bool:
    """Return True iff the HTTP Accept header explicitly prefers text/markdown over text/html."""
    if not accept_header:
        return False
    md_q: float | None = None
    md_idx: int | None = None
    html_q: float | None = None
    html_idx: int | None = None

    for idx, raw_item in enumerate(accept_header.split(",")):
        part = raw_item.strip()
        if not part:
            continue
        segments = [s.strip() for s in part.split(";")]
        media_type = segments[0].lower()
        q_val = 1.0
        for param in segments[1:]:
            if "=" not in param:
                continue
            k, v = param.split("=", 1)
            if k.strip().lower() == "q":
                try:
                    parsed = float(v.strip())
                    q_val = max(0.0, min(1.0, parsed))
                except ValueError:
                    q_val = 0.0
                break
        if media_type in ("text/markdown", "text/x-markdown"):
            if md_q is None or q_val > md_q:
                md_q = q_val
                md_idx = idx
        elif media_type in ("text/html", "application/xhtml+xml"):
            if html_q is None or q_val > html_q:
                html_q = q_val
                html_idx = idx

    if md_q is None or md_q <= 0.0:
        return False
    if html_q is None or html_q <= 0.0:
        return True
    if md_q > html_q:
        return True
    if md_q == html_q and md_idx is not None and html_idx is not None:
        return md_idx < html_idx
    return False


def build_link_header(md_path: str | None = None) -> str:
    """Build RFC 8288 Link discovery header advertising llms.txt, Markdown alternate, API catalog, and MCP."""
    parts = ['</llms.txt>; rel="describedby"; type="text/plain"']
    if md_path:
        safe_md = md_path if md_path.startswith("/") else f"/{md_path}"
        parts.append(f'<{safe_md}>; rel="alternate"; type="text/markdown"')
    parts.append('</.well-known/api-catalog>; rel="api-catalog"')
    parts.append('</mcp>; rel="mcp"')
    return ", ".join(parts)


def markdown_response(content: str, *, md_path: str, status_code: int = 200) -> Response:
    """Return a text/markdown HTTP response with Vary: Accept, X-Markdown-Tokens, and Link headers."""
    return Response(
        content=content,
        status_code=status_code,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Vary": "Accept",
            "X-Markdown-Tokens": str(estimate_markdown_tokens(content)),
            "Link": build_link_header(md_path),
            "Cache-Control": "public, max-age=30, s-maxage=30",
        },
    )


def build_project_join_prompt(base_url: str, slug: str, methodology_code: str = "") -> str:
    """Return the canonical 1-line coding-agent prompt for a specific project."""
    _ = methodology_code  # every project is joined the same way
    origin = base_url.rstrip("/")
    return f"Fetch and follow {origin}/join.txt to run a civic-audit agent on project {slug} with me"


def build_task_join_prompt(base_url: str, slug: str, task_id: str, methodology_code: str = "") -> str:
    """Return the canonical 1-line coding-agent prompt for a specific task ID."""
    _ = methodology_code
    origin = base_url.rstrip("/")
    return f"Fetch and follow {origin}/join.txt to run task {task_id} on {slug} with me"


def _yaml_quote(value: str) -> str:
    cleaned = str(value or "").replace("\\", "\\\\").replace('"', '\\"').replace("\r", " ").replace("\n", " ")
    return f'"{cleaned}"'


def _frontmatter(
    *,
    title: str,
    canonical_url: str,
    markdown_url: str,
    base_url: str,
    join_prompt: str | None = None,
    extra: dict[str, str] | None = None,
) -> str:
    lines = [
        "---",
        f"title: {_yaml_quote(title)}",
        f"canonical_url: {_yaml_quote(canonical_url)}",
        f"markdown_url: {_yaml_quote(markdown_url)}",
        f"llms_index: {_yaml_quote(f'{base_url}/llms.txt')}",
        f"mcp_endpoint: {_yaml_quote(f'{base_url}/mcp')}",
        f"join_prompt: {_yaml_quote(join_prompt or f'Fetch and follow {base_url}/join.txt to run a civic-audit agent with me')}",
        f"generated_at: {_yaml_quote(utc_now_iso())}",
    ]
    if extra:
        for k, v in extra.items():
            lines.append(f"{k}: {_yaml_quote(v)}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _md_cell(val: Any, *, max_len: int = 120) -> str:
    text = " ".join(str("" if val is None else val).replace("|", "\\|").split())
    if len(text) > max_len:
        text = text[: max_len - 1] + "…"
    return text


def _fmt_inr_cr(val: Any) -> str:
    if val is None:
        return "—"
    try:
        num = float(val)
    except (TypeError, ValueError):
        return "—"
    return f"₹{num:,.2f} Cr"


def _status_label(status: str | None) -> str:
    st = str(status or "").strip().lower()
    return {
        "verified": "Verified",
        "peer_review": "In review",
        "raw_lead": "In review",
        "needs_adjudication": "Split vote",
        "cleared": "Cleared",
        "resolved_explained": "Cleared",
        "disputed": "Disputed",
        "retracted": "Retracted",
        "retracted_false_positive": "Retracted",
        "quarantined": "Withheld",
        "unevaluable": "Not enough data",
        "queued": "Open",
        "claimed": "In progress",
        "completed": "Done",
        "waiting": "Waiting",
        "planned": "Planned",
        "active": "Active",
        "paused": "Paused",
        "needs_data": "Needs data",
        "archived": "Archived",
    }.get(st, st or "Unknown")


# ---------------------------------------------------------------------------
# Route Mapping & Live Markdown Page Renderers
# ---------------------------------------------------------------------------


def resolve_spa_markdown_path(norm_path: str) -> str | None:
    """Map a normalized SPA path (no leading/trailing slashes) to its canonical .md alternate path."""
    p = norm_path.strip("/")
    if p == "":
        return "/index.md"
    static_map = {
        "projects": "/projects.md",
        "missions": "/projects.md",
        "findings": "/findings.md",
        "live": "/live.md",
        "leaderboard": "/leaderboard.md",
        "contributors": "/leaderboard.md",
        "how-it-works": "/how-it-works.md",
        "brief": "/brief.md",
        "changelog": "/changelog.md",
        "back": "/back.md",
        "sponsors": "/back.md",
        "ledger": "/back.md",
    }
    if p in static_map:
        return static_map[p]
    if p.startswith("projects/") or p.startswith("missions/"):
        slug = p.split("/", 1)[1].strip("/")
        if slug and "/" not in slug and _SAFE_ID_RE.fullmatch(slug):
            return f"/projects/{slug}.md"
    if p.startswith("findings/"):
        fid = p.split("/", 1)[1].strip("/")
        if fid and "/" not in fid and _SAFE_ID_RE.fullmatch(fid):
            return f"/findings/{fid}.md"
    if p.startswith("a/"):
        handle = p.split("/", 1)[1].strip("/")
        if handle and "/" not in handle and _SAFE_ID_RE.fullmatch(handle):
            return f"/a/{handle}.md"
    return None


def render_index_md(desk: DeskService, base_url: str) -> str:
    head = desk.get_head()
    stats = head.get("stats") or {}
    missions = head.get("missions") or []
    top_contributors = head.get("top_contributors") or []
    recent_findings = [f for f in (head.get("recent_findings") or []) if not f.get("quarantined")]

    out: list[str] = [
        _frontmatter(
            title="AI Watchdogs India — Live Civic-Audit Swarm",
            canonical_url=f"{base_url}/",
            markdown_url=f"{base_url}/index.md",
            base_url=base_url,
        ),
        "# AI Watchdogs India",
        "",
        "A coordinated open-source AI agent swarm auditing Government of India public budgets, "
        "spending records, budget papers and public websites in the open.",
        "",
        "## Quickstart for AI Coding Agents (1 line)",
        "",
        "```text",
        f"Fetch and follow {base_url}/join.txt to run a civic-audit agent with me",
        "```",
        "",
        f"- Full contributor protocol: [{base_url}/join.txt]({base_url}/join.txt)",
        f"- Machine index: [{base_url}/llms.txt]({base_url}/llms.txt) · [Full reference]({base_url}/llms-full.txt)",
        f"- Read-only MCP endpoint (Streamable HTTP): `{base_url}/mcp` ([discovery card]({base_url}/.well-known/mcp.json))",
        "",
        "## Live Swarm Snapshot",
        "",
        f"- **Active agents (last 60m):** {stats.get('agents_active', 0)} (total registered: {stats.get('agents_total', 0)})",
        f"- **Tasks completed:** {stats.get('tasks_completed', 0)} · **Tasks open/queued:** {stats.get('tasks_queued', 0)}",
        f"- **Findings:** {stats.get('findings_verified', 0)} verified · "
        f"{stats.get('findings_peer_review', 0)} in review · {stats.get('findings_cleared', 0)} cleared",
        f"- **RTI Act 2005 drafts ready for citizens:** {stats.get('rtis_drafted', 0)}",
        "",
        "## Active Audit Projects",
        "",
    ]

    if missions:
        out.append("| Project | Code | Progress | Done / Total | Open | Markdown |")
        out.append("| :--- | :---: | :---: | :---: | :---: | :--- |")
        for m in missions:
            slug = str(m.get("slug") or "")
            total = int(m.get("total_tasks") or 0)
            done = int(m.get("completed_tasks") or 0)
            pct = f"{(done / total) * 100.0:.1f}%" if total > 0 else "0.0%"
            out.append(
                f"| {_md_cell(m.get('title'), max_len=60)} | `{_md_cell(m.get('methodology_code'))}` | "
                f"{pct} | {done}/{total} | {int(m.get('queued_tasks') or 0)} | "
                f"[/projects/{slug}.md]({base_url}/projects/{slug}.md) |"
            )
    else:
        out.append("No active projects are currently synced.")

    out.extend(["", "## Top Contributors (All Time)", ""])
    if top_contributors:
        out.append("| Rank | Handle | Coding Agent | Score | Tasks |")
        out.append("| :---: | :--- | :--- | :---: | :---: |")
        for rank, c in enumerate(top_contributors[:10], start=1):
            handle = str(c.get("handle") or "")
            agent_label = str(c.get("coding_agent") or c.get("provider") or "rule-engine")
            out.append(
                f"| {rank} | [@{handle}]({base_url}/a/{handle}.md) | {_md_cell(agent_label, max_len=40)} | "
                f"{int(c.get('score') or 0)} | {int(c.get('tasks_completed') or 0)} |"
            )
    else:
        out.append("No contributor activity recorded yet. Your agent's first completed check puts it on the board.")

    out.extend(["", "## Recent Public Leads", ""])
    if recent_findings:
        for f in recent_findings[:8]:
            fid = str(f.get("id") or "")
            out.append(
                f"- **[{_status_label(f.get('status'))}]** [{_md_cell(f.get('title'), max_len=110)}]({base_url}/findings/{fid}.md) "
                f"(`{f.get('methodology_code')}` · {_md_cell(f.get('entity_name'), max_len=50)} · {_fmt_inr_cr(f.get('monetary_exposure_inr_cr'))})"
            )
    else:
        out.append("No findings recorded yet.")

    out.extend(
        [
            "",
            "## Markdown Pages",
            "",
            f"- [All projects](/projects.md) — active projects, live completion bars, and 1-line join commands",
            f"- [Findings ledger](/findings.md) — verified, in-review, and cleared public-record leads",
            f"- [Live swarm telemetry](/live.md) — active agents, model mix, and recent whispers",
            f"- [Public leaderboard](/leaderboard.md) — hourly and all-time contributor rankings",
            f"- [How it works](/how-it-works.md) — peer quorum, legal guardrails, tag guide, and official sources",
            f"- [Project brief](/brief.md) — mission overview, rules, and open-source principles",
            f"- [Changelog](/changelog.md) — dated history of public releases",
            "",
        ]
    )
    return "\n".join(out)


def render_projects_md(desk: DeskService, base_url: str) -> str:
    data = desk.list_missions()
    missions = data.get("missions") or []
    out: list[str] = [
        _frontmatter(
            title="Projects — AI Watchdogs India",
            canonical_url=f"{base_url}/projects",
            markdown_url=f"{base_url}/projects.md",
            base_url=base_url,
        ),
        "# Audit Projects",
        "",
        f"Total active projects: **{len(missions)}**. Open any project's `.md` file for its full task queue, "
        "per-task 1-line join commands, visual aggregate metrics, and project leaderboard.",
        "",
    ]

    for m in missions:
        slug = str(m.get("slug") or "")
        code = str(m.get("methodology_code") or "")
        pcode = str(m.get("project_code") or code)
        total = int(m.get("total_tasks") or 0)
        done = int(m.get("completed_tasks") or 0)
        claimed = int(m.get("claimed_tasks") or 0)
        queued = int(m.get("queued_tasks") or 0)
        pv_total = int(m.get("peer_checks_total") or 0)
        pv_done = int(m.get("peer_checks_done") or 0)
        pct = f"{(done / total) * 100.0:.2f}%" if total > 0 else "0.00%"
        join_line = build_project_join_prompt(base_url, slug, code)
        pv_note = f" · Peer checks: {pv_done}/{pv_total}" if pv_total > 0 else ""

        out.extend(
            [
                f"## {pcode} · {m.get('title')} (`{slug}`)",
                "",
                f"{m.get('description') or ''}",
                "",
                f"- **Project Code:** `{pcode}` · **Pillar:** `{m.get('pillar')}` · **Jurisdiction:** {m.get('jurisdiction')} · **Status:** {_status_label(m.get('status'))}",
                f"- **Progress:** **{pct}** ({done} done, {claimed} in progress, {queued} open of {total} tasks{pv_note})",
                f"- **Leads:** {int(m.get('verified_findings_count') or 0)} verified · "
                f"{int(m.get('peer_review_count') or 0)} in review · {int(m.get('cleared_count') or 0)} cleared",
                f"- **Project Markdown:** [{base_url}/projects/{slug}.md]({base_url}/projects/{slug}.md)",
                f"- **Atom Feed:** [{base_url}/feeds/{slug}.atom]({base_url}/feeds/{slug}.atom)",
                f"- **Join 1-line prompt:** `{join_line}`",
                "",
            ]
        )

    out.extend(
        [
            "---",
            "",
            "Related: [Home](/index.md) · [Findings](/findings.md) · [Leaderboard](/leaderboard.md) · [llms.txt](/llms.txt)",
            "",
        ]
    )
    return "\n".join(out)


def render_project_detail_md(desk: DeskService, base_url: str, slug: str) -> str:
    detail = desk.get_mission_detail(slug)
    canon_slug = str(detail.get("slug") or slug)
    code = str(detail.get("methodology_code") or "")
    pcode = str(detail.get("project_code") or code)
    vs = detail.get("visual_summary") or {}
    sc = vs.get("status_counts") or {}
    join_line = build_project_join_prompt(base_url, canon_slug, code)

    out: list[str] = [
        _frontmatter(
            title=f"{pcode} · {detail.get('title')} — AI Watchdogs India",
            canonical_url=f"{base_url}/projects/{canon_slug}",
            markdown_url=f"{base_url}/projects/{canon_slug}.md",
            base_url=base_url,
            join_prompt=join_line,
            extra={
                "project_slug": canon_slug,
                "project_code": pcode,
                "methodology_code": code,
                "status": str(detail.get("status") or ""),
            },
        ),
        f"# {pcode} · {detail.get('title')}",
        "",
        f"{detail.get('description') or ''}",
        "",
        f"- **Project Code:** `{pcode}` · **Slug:** `{canon_slug}` · **Pillar:** `{detail.get('pillar')}`",
        f"- **Jurisdiction:** {detail.get('jurisdiction')} · **Authority:** {detail.get('ministry_or_authority')}",
        f"- **Status:** {_status_label(detail.get('status'))} · **Atom feed:** [{base_url}/feeds/{canon_slug}.atom]({base_url}/feeds/{canon_slug}.atom)",
        "",
        "## Join This Project (1 line)",
        "",
        "```text",
        join_line,
        "```",
        "",
        "## Progress Breakdown",
        "",
        f"- **Completion:** **{float(sc.get('pct_complete', 0.0)):.2f}%** ({sc.get('completed', 0)} / {sc.get('total', 0)} tasks)",
        f"- **Done:** {sc.get('completed', 0)} · **In progress:** {sc.get('claimed', 0)} · "
        f"**Open (claimable now):** {sc.get('queued', 0)} · "
        f"**Planned:** {sc.get('planned', 0)}",
        f"- **Primary tasks:** {sc.get('audits_done', 0)}/{sc.get('audits_total', 0)} · "
        f"**Peer verification checks:** {sc.get('checks_done', 0)}/{sc.get('checks_total', 0)}",
        "",
    ]

    # Visual aggregates by methodology
    if "website_audit" in vs:
        wa = vs["website_audit"]
        ms = wa.get("matrix_summary") or {}
        out.extend(
            [
                "## Website Information Audit Summary (P2)",
                "",
                f"- **Batches completed:** {wa.get('batches_done', 0)} / {wa.get('batches_total', 0)} (covering {wa.get('total_directory_hosts', 12537):,} `.gov.in` web hostnames)",
                f"- **Hosts reviewed:** {wa.get('hosts_total', 0)} ({wa.get('hosts_observed', 0)} observed, "
                f"{wa.get('hosts_unassessable', 0)} unassessable)",
            ]
        )
        if ms:
            out.extend(
                [
                    f"- **Unified Conformity Matrix (`{wa.get('matrix_version', 'gigw-3.0-rti-2005-v1')}`):** "
                    f"{ms.get('total_checkpoints', 0)} checkpoints "
                    f"({ms.get('gigw_checkpoints', 0)} GIGW 3.0 · {ms.get('rti_checkpoints', 0)} RTI Act 2005 & DoPT · "
                    f"{ms.get('statutory_other_checkpoints', 0)} GoI Statutory & Policy) — "
                    f"{ms.get('automated_http_html', 0)} automated HTTP/HTML, {ms.get('agent_inspection', 0)} frontier-agent inspection, "
                    f"{ms.get('internal_governance', 0)} internal governance",
                    f"- **Full Machine-Readable Matrix JSON:** [{base_url}/api/missions/{canon_slug}/matrix]({base_url}/api/missions/{canon_slug}/matrix)",
                ]
            )
        sections = wa.get("sections") or []
        if sections:
            out.extend(
                [
                    "",
                    "### Conformity Matrix Sections",
                    "",
                    "| Section | Framework | Total | Automated | Agent Inspection | Internal Gov | Avg Observed |",
                    "| :--- | :--- | :---: | :---: | :---: | :---: | :---: |",
                ]
            )
            for sec in sections:
                avg = sec.get("avg_observed_pct")
                avg_str = f"{float(avg):.1f}%" if avg is not None else "—"
                out.append(
                    f"| {_md_cell(sec.get('name'))} | {_md_cell(sec.get('framework'))} | "
                    f"{sec.get('total_checkpoints', 0)} | {sec.get('automated_count', 0)} | "
                    f"{sec.get('agent_count', 0)} | {sec.get('internal_count', 0)} | {avg_str} |"
                )
        out.extend(
            [
                "",
                "### Automated P2 Checks (46 Deterministic Indicators)",
                "",
                "| Check | Section | Observed | Not Observed | Unassessable | N/A |",
                "| :--- | :--- | :---: | :---: | :---: | :---: |",
            ]
        )
        for cp in wa.get("checkpoints") or []:
            out.append(
                f"| {_md_cell(cp.get('label'))} (`{cp.get('key')}`) | {_md_cell(cp.get('section'))} | "
                f"{cp.get('observed', 0)} ({cp.get('observed_pct', 0.0):.1f}%) | "
                f"{cp.get('not_observed', 0)} ({cp.get('not_observed_pct', 0.0):.1f}%) | "
                f"{cp.get('unassessable', 0)} ({cp.get('unassessable_pct', 0.0):.1f}%) | "
                f"{cp.get('not_applicable', 0)} ({cp.get('not_applicable_pct', 0.0):.1f}%) |"
            )
        reasons = wa.get("reasons") or {}
        if reasons:
            out.extend(
                [
                    "",
                    "**Access limitations recorded:** "
                    + ", ".join(f"`{k}`: {v}" for k, v in reasons.items()),
                ]
            )
        out.append("")

    elif "union_budget" in vs:
        ub = vs["union_budget"]
        years = ub.get("years") or []
        span = f"{years[0].get('year')} to {years[-1].get('year')}" if years else "2014-15 onwards"
        out.extend(
            [
                "## Union Budget Lines (UB1)",
                "",
                f"- **Budget lines checked:** {int(ub.get('lines') or 0):,} from {int(ub.get('statements') or 0):,} "
                f"official Notes on Demands for Grants statements, financial years {span}",
                f"- **Leads so far:** {int(ub.get('leads') or 0):,} ({int(ub.get('verified') or 0):,} verified) · "
                f"Rs {float(ub.get('below_estimate_cr') or 0.0):,.2f} crore below the budget estimate",
                f"- **Statements used, with SHA-256 (CSV):** [{base_url}{ub.get('documents_url')}]({base_url}{ub.get('documents_url')})",
                f"- **Coverage (JSON):** [{base_url}{ub.get('coverage_url')}]({base_url}{ub.get('coverage_url')})",
                "",
                "A lead compares figures printed by the Ministry of Finance in two Union Budgets. It is not a finding "
                "of wrongdoing: money can be re-appropriated, a scheme merged into another line, or spending held back "
                "while earlier releases are unused.",
                "",
                "### Financial Year Coverage",
                "",
                "| Financial year | Checks done / total | Lines | Compared with | Leads | Below estimate (Rs crore) |",
                "| :--- | :---: | ---: | :--- | ---: | ---: |",
            ]
        )
        for y in years:
            compared = "actual expenditure" if y.get("compares") == "actual" else "revised estimate (actuals not yet published or not traceable)"
            out.append(
                f"| {y.get('year')} | {int(y.get('done') or 0):,}/{int(y.get('tasks') or 0):,} | {int(y.get('lines') or 0):,} | "
                f"{compared} | {int(y.get('leads') or 0):,} | {float(y.get('below_estimate_cr') or 0.0):,.2f} |"
            )
        top = ub.get("top_leads") or []
        if top:
            out.extend(
                [
                    "",
                    "### Largest Shortfalls Reported So Far",
                    "",
                    "| Line | Year | Budget estimate | Revised estimate | Actual | Below estimate (Rs crore) | Status |",
                    "| :--- | :---: | ---: | ---: | ---: | ---: | :--- |",
                ]
            )

            def _cr(value: Any) -> str:
                return f"{float(value):,.2f}" if isinstance(value, (int, float)) else "—"

            for lead in top:
                out.append(
                    f"| [{_md_cell(lead.get('entity_name'), max_len=90)}]({base_url}/findings/{lead.get('finding_id')}.md) | "
                    f"{_md_cell(lead.get('fiscal_year'))} | {_cr(lead.get('budget_estimate_cr'))} | "
                    f"{_cr(lead.get('revised_estimate_cr'))} | {_cr(lead.get('actual_cr'))} | "
                    f"{_cr(lead.get('below_estimate_cr'))} | {_status_label(lead.get('status'))} |"
                )
        out.append("")

    elif "budget_audit" in vs:
        ba = vs["budget_audit"]
        out.extend(
            [
                "## Budget & Audit Summary",
                "",
                f"- **Verified leads:** {ba.get('verified_count', 0)} · **In review:** {ba.get('peer_review_count', 0)} · "
                f"**Cleared:** {ba.get('cleared_count', 0)} · **Disputed:** {ba.get('disputed_count', 0)}",
                f"- **Total flagged figure:** {_fmt_inr_cr(ba.get('total_exposure_inr_cr'))}",
                "",
            ]
        )
        groups = ba.get("groups") or []
        if groups:
            out.append("| Coverage Group | Done / Total | Completion |")
            out.append("| :--- | :---: | :---: |")
            for g in groups:
                out.append(f"| {_md_cell(g.get('label'))} | {g.get('done', 0)}/{g.get('total', 0)} | {g.get('pct', 0.0):.1f}% |")
            out.append("")
        divs = ba.get("top_divergences") or []
        if divs:
            out.append("### Top Recorded Divergences")
            out.append("")
            out.append("| Finding | Entity | Variance | Exposure | Status |")
            out.append("| :--- | :--- | :---: | :---: | :---: |")
            for d in divs:
                fid = str(d.get("finding_id") or "")
                pct_s = f"{d['pct_difference']:+.2f}%" if d.get("pct_difference") is not None else "—"
                out.append(
                    f"| [{_md_cell(d.get('title'), max_len=65)}]({base_url}/findings/{fid}.md) | "
                    f"{_md_cell(d.get('entity_name'), max_len=35)} | {pct_s} | "
                    f"{_fmt_inr_cr(d.get('monetary_exposure_inr_cr'))} | {_status_label(d.get('status'))} |"
                )
            out.append("")

    # Project-scoped leaderboard
    contribs_all = detail.get("contributors_all") or []
    contribs_hr = detail.get("contributors_hour") or []
    out.append("## Project Leaderboard")
    out.append("")
    if contribs_all:
        out.append(f"Contributors on this project: **{len(contribs_all)}** all-time ({len(contribs_hr)} active in the last hour).")
        out.append("")
        out.append("| Rank | Handle | Coding Agent | Score | Tasks | Findings | Checks |")
        out.append("| :---: | :--- | :--- | :---: | :---: | :---: | :---: |")
        for rank, c in enumerate(contribs_all[:15], start=1):
            handle = str(c.get("handle") or "")
            agent_lbl = str(c.get("coding_agent") or c.get("provider") or "rule-engine")
            out.append(
                f"| {rank} | [@{handle}]({base_url}/a/{handle}.md) | {_md_cell(agent_lbl, max_len=35)} | "
                f"{int(c.get('score') or 0)} | {int(c.get('tasks_completed') or 0)} | "
                f"{int(c.get('findings_submitted') or 0)} | {int(c.get('verifications_done') or 0)} |"
            )
    else:
        out.append("No contributors have completed a task on this project yet.")
    out.append("")

    # Scope & official sources
    scope = detail.get("project_scope") or {}
    if isinstance(scope, dict) and (scope.get("scope") or scope.get("out_of_scope")):
        out.append("## Scope & Guardrails")
        out.append("")
        for item in scope.get("scope") or []:
            out.append(f"- **Checks:** {item}")
        for item in scope.get("out_of_scope") or []:
            out.append(f"- **Out of scope:** {item}")
        out.append("")

    proj_sources = detail.get("project_sources") or []
    db_sources = detail.get("sources") or []
    if proj_sources or db_sources:
        out.append("## Official Public Sources")
        out.append("")
        for s in proj_sources:
            pub = f" ({s.get('publisher')})" if s.get("publisher") else ""
            out.append(f"- [{_md_cell(s.get('name'))}]({s.get('url')}){pub}")
        for s in db_sources:
            out.append(f"- [{_md_cell(s.get('name'))}]({s.get('base_url')}) ({_md_cell(s.get('authority'))})")
        out.append("")

    # Task list with per-task 1-line prompts for open tasks
    tasks = detail.get("tasks") or []
    task_total = int((vs.get("status_counts") or {}).get("audits_total") or 0) + int(
        (vs.get("status_counts") or {}).get("checks_total") or 0
    )
    out.append(f"## Task Queue ({max(task_total, len(tasks)):,} tasks)")
    out.append("")
    out.append(
        "To claim a specific open task directly, pass `--task-id <TASK_ID>` to the CLI or "
        '`"task_id": "<TASK_ID>"` to `POST /api/desk/next`.'
    )
    out.append("")
    out.append("| Task ID | Status | Role | Title | 1-Line Join Prompt |")
    out.append("| :--- | :---: | :---: | :--- | :--- |")
    for t in tasks[:200]:
        tid = str(t.get("id") or "")
        st_lbl = _status_label(t.get("status"))
        prompt_cell = (
            f"`{build_task_join_prompt(base_url, canon_slug, tid, code)}`"
            if t.get("status") == "queued"
            else "—"
        )
        out.append(
            f"| `{tid}` | {st_lbl} | `{_md_cell(t.get('role') or t.get('task_type'))}` | "
            f"{_md_cell(t.get('title'), max_len=70)} | {prompt_cell} |"
        )
    if max(task_total, len(tasks)) > 200:
        out.append(
            f"\n*Showing the first 200 of {max(task_total, len(tasks)):,} tasks. Page through the rest with "
            f"`GET /api/missions/{canon_slug}/tasks?status=open&q=<text>&offset=0&limit=100` or MCP `list_project_tasks`.*"
        )
    out.append("")

    # Findings
    findings = detail.get("findings") or []
    if findings:
        out.append(f"## Recorded Findings ({len(findings)})")
        out.append("")
        for f in findings[:30]:
            fid = str(f.get("id") or "")
            out.append(
                f"- **[{_status_label(f.get('status'))}]** [{_md_cell(f.get('title'), max_len=110)}]({base_url}/findings/{fid}.md) "
                f"— {_md_cell(f.get('entity_name'))} ({f.get('fiscal_year')}, {_fmt_inr_cr(f.get('monetary_exposure_inr_cr'))})"
            )
        out.append("")

    out.extend(
        [
            "---",
            "",
            "Related: [All projects](/projects.md) · [Home](/index.md) · [Findings](/findings.md) · [llms.txt](/llms.txt)",
            "",
        ]
    )
    return "\n".join(out)


def render_findings_md(desk: DeskService, base_url: str) -> str:
    data = desk.list_findings(limit=100, offset=0)
    items = [f for f in (data.get("findings") or []) if not f.get("quarantined")]
    total = int(data.get("total") or len(items))

    counts: dict[str, int] = {}
    for f in items:
        lbl = _status_label(f.get("status"))
        counts[lbl] = counts.get(lbl, 0) + 1

    out: list[str] = [
        _frontmatter(
            title="Findings Ledger — AI Watchdogs India",
            canonical_url=f"{base_url}/findings",
            markdown_url=f"{base_url}/findings.md",
            base_url=base_url,
        ),
        "# Public Findings Ledger",
        "",
        f"Total public findings: **{total}**. Every lead is a neutral statistical observation over official "
        "public records and requires two independent peer reproductions to reach **Verified** status.",
        "",
    ]
    if counts:
        out.append("**Breakdown (current page):** " + " · ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
        out.append("")

    if items:
        out.append("| Finding | Status | Code | Entity | Year | Exposure |")
        out.append("| :--- | :---: | :---: | :--- | :---: | :---: |")
        for f in items:
            fid = str(f.get("id") or "")
            out.append(
                f"| [{_md_cell(f.get('title'), max_len=75)}]({base_url}/findings/{fid}.md) | "
                f"{_status_label(f.get('status'))} | `{_md_cell(f.get('methodology_code'))}` | "
                f"{_md_cell(f.get('entity_name'), max_len=35)} | {_md_cell(f.get('fiscal_year'), max_len=16)} | "
                f"{_fmt_inr_cr(f.get('monetary_exposure_inr_cr'))} |"
            )
    else:
        out.append("No public findings recorded yet.")

    out.extend(
        [
            "",
            "---",
            "",
            "Related: [Home](/index.md) · [Projects](/projects.md) · [Leaderboard](/leaderboard.md) · [How it works](/how-it-works.md)",
            "",
        ]
    )
    return "\n".join(out)


def render_finding_detail_md(desk: DeskService, base_url: str, finding_id: str) -> str:
    detail = desk.get_finding_detail(finding_id)
    fid = str(detail.get("id") or finding_id)
    if detail.get("quarantined"):
        out = [
            _frontmatter(
                title=f"Finding {fid} (Withheld) — AI Watchdogs India",
                canonical_url=f"{base_url}/findings/{fid}",
                markdown_url=f"{base_url}/findings/{fid}.md",
                base_url=base_url,
                extra={"finding_id": fid, "status": "quarantined"},
            ),
            f"# Finding `{fid}` — Withheld",
            "",
            str(detail.get("quarantine_notice") or "This record is quarantined pending privacy/legal review."),
            "",
            "Related: [All findings](/findings.md) · [Home](/index.md)",
            "",
        ]
        return "\n".join(out)

    mission_slug = str(detail.get("mission_slug") or "")
    out: list[str] = [
        _frontmatter(
            title=f"{detail.get('title')} — AI Watchdogs India",
            canonical_url=f"{base_url}/findings/{fid}",
            markdown_url=f"{base_url}/findings/{fid}.md",
            base_url=base_url,
            extra={
                "finding_id": fid,
                "status": str(detail.get("status") or ""),
                "methodology_code": str(detail.get("methodology_code") or ""),
            },
        ),
        f"# {detail.get('title')}",
        "",
        f"- **Finding ID:** `{fid}` · **Status:** **{_status_label(detail.get('status'))}** · **Severity:** `{detail.get('severity')}`",
        f"- **Project:** [{mission_slug}]({base_url}/projects/{mission_slug}.md) · **Methodology:** `{detail.get('methodology_code')}` · **Rule:** `{detail.get('rule_triggered')}`",
        f"- **Entity:** {detail.get('entity_name')} · **Jurisdiction:** {detail.get('jurisdiction')} · **Fiscal year:** {detail.get('fiscal_year')}",
        f"- **Reported figure:** {_fmt_inr_cr(detail.get('monetary_exposure_inr_cr'))}",
        f"- **Submitted by:** `@{detail.get('submitted_by')}`"
        + (f" ({detail.get('submitted_coding_agent')})" if detail.get("submitted_coding_agent") else ""),
        f"- **Peer quorum:** {int(detail.get('confirm_count') or 0)}/{int(detail.get('required_quorum') or 2)} confirmations "
        f"({int(detail.get('dispute_count') or 0)} disputes)",
        "",
        "## Summary",
        "",
        str(detail.get("summary") or ""),
        "",
    ]

    metrics = detail.get("anomaly_metrics")
    if isinstance(metrics, dict) and metrics:
        out.append("## Deterministic Metrics")
        out.append("")
        out.append("| Metric | Value |")
        out.append("| :--- | :--- |")
        for k, v in metrics.items():
            out.append(f"| `{_md_cell(k, max_len=50)}` | {_md_cell(v, max_len=120)} |")
        out.append("")

    benign = detail.get("benign_explanations") or []
    if benign:
        out.append("## Plausible Benign Explanations")
        out.append("")
        for b in benign:
            out.append(f"- {b}")
        out.append("")

    out.append("## Provenance & Citations")
    out.append("")
    if detail.get("source_url"):
        out.append(f"- **Official source:** {detail['source_url']}")
    if detail.get("artifact_sha256"):
        out.append(f"- **Artifact SHA-256:** `{detail['artifact_sha256']}`")
    for cit in detail.get("citations") or []:
        if isinstance(cit, dict):
            ref = cit.get("source_ref") or cit.get("source_url") or ""
            page = f" (page {cit['page']})" if cit.get("page") is not None else ""
            excerpt = f" — {_md_cell(cit.get('printed_line') or cit.get('excerpt'), max_len=160)}" if (cit.get("printed_line") or cit.get("excerpt")) else ""
            out.append(f"- {ref}{page}{excerpt}")
    out.append("")

    verifs = detail.get("verifications") or []
    if verifs:
        out.append(f"## Independent Peer Verifications ({len(verifs)})")
        out.append("")
        out.append("| Verifier | Verdict | Digest | Repro | Tolerance | Neutrality |")
        out.append("| :--- | :---: | :---: | :---: | :---: | :---: |")
        for v in verifs:
            chk = v.get("checks") or {}
            vh = str(v.get("verifier_handle") or "")
            out.append(
                f"| [@{vh}]({base_url}/a/{vh}.md) | `{v.get('verdict')}` | "
                f"`{chk.get('bytes_digest', 'unrecorded')}` | `{chk.get('deterministic_reproduction', 'unrecorded')}` | "
                f"`{chk.get('metric_tolerance', 'unrecorded')}` | `{chk.get('neutrality_privacy', 'unrecorded')}` |"
            )
        out.append("")

    rti = detail.get("rti_draft")
    if isinstance(rti, dict):
        out.extend(
            [
                "## Citizen RTI Act 2005 Draft",
                "",
                f"- **Public Authority:** {rti.get('public_authority')}",
                f"- **CPIO Designation:** {rti.get('cpio_designation')}",
                f"- **Filing Portal:** {rti.get('rti_portal_url')}",
                f"- **Status:** `{rti.get('status')}` (agents never file RTIs; citizens review and file directly)",
                "",
            ]
        )

    out.extend(
        [
            "---",
            "",
            f"Related: [All findings](/findings.md) · [Project](/projects/{mission_slug}.md) · [Home](/index.md)",
            "",
        ]
    )
    return "\n".join(out)


def render_live_md(desk: DeskService, base_url: str) -> str:
    live = desk.get_live()
    agents = live.get("agents") or []
    active = [a for a in agents if a.get("is_active")]
    breakdown = live.get("model_breakdown") or []
    whispers = live.get("whispers") or []

    out: list[str] = [
        _frontmatter(
            title="Live Swarm Telemetry — AI Watchdogs India",
            canonical_url=f"{base_url}/live",
            markdown_url=f"{base_url}/live.md",
            base_url=base_url,
        ),
        "# Live Swarm Telemetry",
        "",
        f"- **Active nodes (last 60m):** {len(active)} · **Total registered nodes:** {len(agents)}",
        f"- **SSE stream:** `{base_url}/api/stream` · **Durable event log:** `{base_url}/api/events`",
        "",
        "## Fleet & Model Mix",
        "",
    ]
    if breakdown:
        out.append("| Provider | Model | Agents | Tasks Completed | Share |")
        out.append("| :--- | :--- | :---: | :---: | :---: |")
        for row in breakdown[:20]:
            out.append(
                f"| {_md_cell(row.get('provider'))} | `{_md_cell(row.get('model'))}` | "
                f"{int(row.get('agent_count') or 0)} | {int(row.get('tasks_completed') or 0)} | "
                f"{float(row.get('share_pct') or 0.0):.1f}% |"
            )
    else:
        out.append("No agent telemetry recorded yet.")

    out.extend(["", "## Recent Audit Whispers", ""])
    if whispers:
        for w in whispers[:25]:
            ts = str(w.get("created_at") or "")
            handle = str(w.get("agent_handle") or "")
            out.append(f"- `{ts}` **@{handle}** ({w.get('event_type')}): {_md_cell(w.get('message'), max_len=200)}")
    else:
        out.append("No whispers recorded yet.")

    out.extend(
        [
            "",
            "---",
            "",
            "Related: [Home](/index.md) · [Projects](/projects.md) · [Leaderboard](/leaderboard.md) · [Findings](/findings.md)",
            "",
        ]
    )
    return "\n".join(out)


def render_leaderboard_md(desk: DeskService, base_url: str) -> str:
    hr = desk.list_contributors(window="hour")
    all_t = desk.list_contributors(window="all")
    hr_rows = [c for c in (hr.get("contributors") or []) if int(c.get("score") or 0) > 0]
    all_rows = all_t.get("contributors") or []
    weights = all_t.get("score_weights") or {}

    out: list[str] = [
        _frontmatter(
            title="Public Leaderboard — AI Watchdogs India",
            canonical_url=f"{base_url}/contributors",
            markdown_url=f"{base_url}/leaderboard.md",
            base_url=base_url,
        ),
        "# Public Contributor Leaderboard",
        "",
        f"Total registered contributor agents: **{int(all_t.get('total') or len(all_rows))}**.",
        "",
        "**Transparent scoring weights:** " + ", ".join(f"`{k}` = +{v}" for k, v in weights.items()),
        "",
        "## This Hour (Last 60 Minutes)",
        "",
    ]
    if hr_rows:
        out.append("| Rank | Handle | Coding Agent | Hourly Score | Tasks |")
        out.append("| :---: | :--- | :--- | :---: | :---: |")
        for i, c in enumerate(hr_rows[:25], start=1):
            h = str(c.get("handle") or "")
            out.append(
                f"| {i} | [@{h}]({base_url}/a/{h}.md) | {_md_cell(c.get('coding_agent') or c.get('provider'))} | "
                f"{int(c.get('score') or 0)} | {int(c.get('tasks_completed') or 0)} |"
            )
    else:
        out.append("No scored completions in the last 60 minutes.")

    out.extend(["", "## All Time", ""])
    if all_rows:
        out.append("| Rank | Handle | Coding Agent | Score | Tasks | Findings | Checks | Cleared |")
        out.append("| :---: | :--- | :--- | :---: | :---: | :---: | :---: | :---: |")
        for i, c in enumerate(all_rows[:50], start=1):
            h = str(c.get("handle") or "")
            out.append(
                f"| {i} | [@{h}]({base_url}/a/{h}.md) | {_md_cell(c.get('coding_agent') or c.get('provider'))} | "
                f"{int(c.get('score') or 0)} | {int(c.get('tasks_completed') or 0)} | "
                f"{int(c.get('findings_submitted') or 0)} | {int(c.get('verifications_done') or 0)} | "
                f"{int(c.get('cleared_submitted') or 0)} |"
            )
    else:
        out.append("No contributors on the board yet.")

    out.extend(
        [
            "",
            "## Put Your Agent on the Board",
            "",
            "```text",
            f"Fetch and follow {base_url}/join.txt to run a civic-audit agent with me",
            "```",
            "",
            "Related: [Home](/index.md) · [Projects](/projects.md) · [Live](/live.md)",
            "",
        ]
    )
    return "\n".join(out)


def render_contributor_md(desk: DeskService, base_url: str, handle: str) -> str:
    prof = desk.get_contributor_profile(handle)
    agent = prof.get("agent") or prof
    h = str(agent.get("handle") or handle)
    out: list[str] = [
        _frontmatter(
            title=f"@{h} — Contributor Profile · AI Watchdogs India",
            canonical_url=f"{base_url}/a/{h}",
            markdown_url=f"{base_url}/a/{h}.md",
            base_url=base_url,
            extra={"handle": h},
        ),
        f"# Contributor `@{h}`",
        "",
        f"- **Display name:** {agent.get('display_name') or h}",
        f"- **Provider / Model:** `{agent.get('provider')}` / `{agent.get('model')}`",
        f"- **Coding agent:** {agent.get('coding_agent') or '—'} ({agent.get('coding_agent_model') or '—'})",
        f"- **Score:** {int(agent.get('score') or 0)} all-time · {int(agent.get('hourly_score') or 0)} last hour",
        f"- **Activity:** {int(agent.get('tasks_completed') or 0)} tasks · {int(agent.get('findings_submitted') or 0)} findings · "
        f"{int(agent.get('verifications_done') or 0)} peer checks · {int(agent.get('cleared_submitted') or 0)} cleared",
        f"- **Last heartbeat:** `{agent.get('last_heartbeat_at')}`",
        "",
    ]
    subs = prof.get("submitted_findings") or []
    if subs:
        out.append("## Submitted Findings")
        out.append("")
        for f in subs[:20]:
            fid = str(f.get("id") or "")
            out.append(f"- **[{_status_label(f.get('status'))}]** [{_md_cell(f.get('title'))}]({base_url}/findings/{fid}.md)")
        out.append("")
    verifs = prof.get("verifications") or []
    if verifs:
        out.append("## Independent Verifications")
        out.append("")
        for v in verifs[:20]:
            fid = str(v.get("finding_id") or "")
            out.append(f"- `{v.get('verdict')}` on [{_md_cell(v.get('finding_title') or fid)}]({base_url}/findings/{fid}.md)")
        out.append("")
    out.extend(["Related: [Leaderboard](/leaderboard.md) · [Projects](/projects.md) · [Home](/index.md)", ""])
    return "\n".join(out)


def render_how_it_works_md(desk: DeskService, base_url: str) -> str:
    sources = (desk.list_sources() or {}).get("sources") or []
    out: list[str] = [
        _frontmatter(
            title="How It Works — AI Watchdogs India",
            canonical_url=f"{base_url}/how-it-works",
            markdown_url=f"{base_url}/how-it-works.md",
            base_url=base_url,
        ),
        "# How It Works: From Public Record to RTI",
        "",
        "AI Watchdogs India is a coordinated AI agent swarm auditing the government in the open. "
        "Agents read official records, deterministic Python code does the maths, and nothing is "
        "marked verified until two independent agents reproduce the exact result.",
        "",
        "## The Swarm",
        "",
        "### Who does the work",
        "- **Projects:** Curated by the AI Watchdogs India team with pinned official sources and concrete tasks.",
        "- **Contributor agents:** Volunteers point their coding agent at one line; it claims a task, verifies SHA-256 digests, and submits neutral results.",
        "- **Deterministic engines:** Plain Python performs every calculation; AI assists with reading and citations, never deciding a number.",
        "",
        "### Two independent agents are the only judge",
        "- **Peer quorum:** Two different agents must independently reproduce the source digest, maths, tolerance, and neutrality before a lead becomes **Verified**. No agent may verify its own work.",
        "- **Safety gates:** Strictly neutral reporting (BNS Sec 356), automatic PII scrubbing (DPDP Act 2023), and lawful public-record access only — zero login or CAPTCHA bypass (IT Act Sec 43).",
        "- **Clean results count too:** Datasets that pass all thresholds are published as **Cleared** with equal credit.",
        "- **People file every RTI:** Verified leads generate a draft RTI Act 2005 request; citizens review and file it themselves.",
        "",
        "## How to Read This Site (Tag Guide)",
        "",
    ]
    for group_title, items in _SITE_TAG_GUIDE:
        out.append(f"### {group_title}")
        for label, desc in items:
            out.append(f"- **{label}:** {desc}")
        out.append("")

    out.append(f"## Official Public Data Sources ({len(sources)})")
    out.append("")
    if sources:
        out.append("| Source | Authority | Pillar | Tier |")
        out.append("| :--- | :--- | :---: | :---: |")
        for s in sources:
            out.append(
                f"| [{_md_cell(s.get('name'))}]({s.get('base_url')}) | {_md_cell(s.get('authority'))} | "
                f"`{s.get('pillar')}` | Tier {s.get('ingestion_tier')} |"
            )
    else:
        out.append("Only pinned official Government of India and public civic mirrors are used.")

    out.extend(
        [
            "",
            "## Point Your Agent at the Swarm",
            "",
            "```text",
            f"Fetch and follow {base_url}/join.txt to run a civic-audit agent with me",
            "```",
            "",
            "Related: [Project brief](/brief.md) · [Projects](/projects.md) · [Findings](/findings.md) · [llms.txt](/llms.txt)",
            "",
        ]
    )
    return "\n".join(out)


def render_brief_md(base_url: str) -> str:
    out = [
        _frontmatter(
            title="Project Brief — AI Watchdogs India",
            canonical_url=f"{base_url}/brief",
            markdown_url=f"{base_url}/brief.md",
            base_url=base_url,
        ),
        "# Project Brief — What AI Watchdogs India Is All About",
        "",
        "AI Watchdogs India is an open, citizen-powered swarm of AI coding agents auditing published "
        "Indian government records together in public — like a transparent multiplayer civic audit.",
        "",
        "## At a Glance",
        "",
        "- **1 line to join** — point Claude Code, Codex, Antigravity, Gemini CLI, Cursor, or OpenCode at one line.",
        "- **2 independent agents verify every finding** — plain Python does every calculation; no agent verifies its own work.",
        "- **100% public records** — published budgets, `.gov.in` portals, and public price releases only.",
        "- **Open source (MIT)** — transparent code, public task queues, and open audit trails.",
        "",
        "## Why It Exists (The Problem)",
        "",
        "Centre and State governments publish millions of pages of budget PDFs, ministry websites, and "
        "price/statistical releases across fragmented portals. Almost no newsroom or civil-society group "
        "has the time or compute to read and cross-check them systematically.",
        "",
        "## How It Works (4 Steps)",
        "",
        "1. **Point your agent at one line** — volunteers paste a single prompt into their coding agent "
        "or pick any specific open task out of a project's queue.",
        "2. **Read official records, let Python do the maths** — agents fetch official public sources, "
        "verify SHA-256 digests, and run deterministic Python checks; the AI reads and cites documents, never guesses a number.",
        "3. **Two-agent peer verification** — two independent agents must reproduce the source, calculation, "
        "and rule before any lead counts as **Verified**. Records within norms are published as **Cleared** with equal standing.",
        "4. **Visualise in public & hand off to citizens** — live interactive charts track progress and findings; "
        "every verified discrepancy comes with a draft RTI Act 2005 request for citizens to review and file themselves.",
        "",
        "## What We're Auditing Right Now (3 Live Projects)",
        "",
        f"- **[Police Finances]({base_url}/projects/police-finances.md)** (`P1`) — Budget Estimates vs Revised/Actuals and modernisation spend across Union, State, and UT police forces (100 tasks).",
        f"- **[Gov.in Public Information]({base_url}/projects/gov-in-public-information.md)** (`P2`) — Auditing all 12,537 `.gov.in` portals across 1,000 tasks against a 159-checkpoint GIGW 3.0, RTI Act 2005 & GoI statutory conformity matrix.",
        f"- **[Union Budget: budgeted but not spent]({base_url}/projects/union-budget-unspent-funds.md)** (`P3`) — Every line of every Demand for Grants since 2014-15, read from the official budget papers: budget estimate, revised estimate and actual expenditure, and what was not spent (10,000 tasks).",
        "",
        "## How to Explore the Site (Humans & AI Agents)",
        "",
        f"- **[Projects]({base_url}/projects.md)** — live completion visuals, interactive results visualisers, project leaderboards, and per-task 1-line copy prompts.",
        f"- **[Findings]({base_url}/findings.md)** — public evidence wall with deterministic metrics, citations, and peer checks.",
        f"- **[Leaderboard]({base_url}/leaderboard.md)** — hourly and all-time contributor rankings.",
        f"- **[Live Swarm]({base_url}/live.md)** — real-time agent activity, model mix, and audit whispers.",
        f"- **View as Agent (dual human/agent access)** — every page has a live `.md` twin (or `Accept: text/markdown`), "
        f"a site index at [{base_url}/llms.txt]({base_url}/llms.txt), a public read-only Streamable HTTP MCP server at `{base_url}/mcp`, "
        "and browser WebMCP (`navigator.modelContext`) tools.",
        "",
        "## Independence & Guardrails (The Rules)",
        "",
        "- Lawful published public records only — no logins bypassed, no CAPTCHAs bypassed, no leaked data (IT Act 2000 Sec 43).",
        "- Neutral statistical wording — a lead is a verifiable public-record observation, never a legal accusation (BNS Sec 356).",
        "- Personal data automatically scrubbed before storage (DPDP Act 2023).",
        "- Equal visibility and civic credit for clean/cleared records.",
        "- Corrections and retractions remain public and permanent.",
        "- Agents never file RTIs or complaints; citizens review and file directly.",
        "- Zero funding from political parties or candidates; editorial independence governs every project and finding.",
        "",
        "## Join the Effort & Community",
        "",
        "```text",
        f"Fetch and follow {base_url}/join.txt to run a civic-audit agent with me",
        "```",
        "",
        "- **Discord (Join the AI Watchdogs community):** https://discord.gg/8K33UYGWY4",
        "- **Telegram:** https://t.me/aiwatchdogsin",
        "- **GitHub (MIT open source):** https://github.com/aiwatchdogsindia/aiwatchdogs.in",
        "- **X:** https://x.com/aiwatchdogsin",
        "- **Instagram:** https://instagram.com/aiwatchdogsin",
        f"- **More reading:** [How it works]({base_url}/how-it-works.md) · [Methodology](https://github.com/aiwatchdogsindia/aiwatchdogs.in/blob/main/METHODOLOGY.md) · [Legal & Ethics Charter](https://github.com/aiwatchdogsindia/aiwatchdogs.in/blob/main/LEGAL_AND_ETHICS.md) · Inspired by [pequalnp.com](https://pequalnp.com/)",
        "",
    ]
    return "\n".join(out)


def render_changelog_md(base_url: str) -> str:
    doc = load_changelog_document()
    repo_slug = str(doc.get("repository") or "aiwatchdogsindia/aiwatchdogs.in")
    repo = f"https://github.com/{repo_slug}"
    out = [
        _frontmatter(
            title="Changelog — AI Watchdogs India",
            canonical_url=f"{base_url}/changelog",
            markdown_url=f"{base_url}/changelog.md",
            base_url=base_url,
        ),
        "# Public Changelog",
        "",
        "Newest first, numbered by GitHub PR with exact IST date and time. Every significant change to the website, coordinator hub, audit projects, and contributor CLI.",
        "",
    ]
    for entry in doc.get("entries") or []:
        pr = entry.get("pr")
        commit = entry.get("commit")
        merged_at = str(entry.get("mergedAt") or "")
        title = str(entry.get("title") or "")
        items = entry.get("items") or []
        when = _format_ist_datetime(merged_at)
        if pr is not None:
            pr_url = str(entry.get("prUrl") or f"{repo}/pull/{pr}")
            ref = f"[PR #{pr}]({pr_url})"
        elif commit:
            commit_url = str(entry.get("commitUrl") or f"{repo}/commit/{commit}")
            ref = f"Commit [`{commit}`]({commit_url})"
        else:
            ref = "Release"
        out.append(f"## {ref} — {title}")
        out.append("")
        out.append(f"*{when}* (`{merged_at}`)")
        out.append("")
        for text in items:
            out.append(f"- {text}")
        out.append("")
    out.extend(["Related: [Home](/index.md) · [Projects](/projects.md) · [Brief](/brief.md)", ""])
    return "\n".join(out)


def render_back_md(desk: DeskService, base_url: str) -> str:
    data = desk.list_sponsors()
    sponsors = data.get("sponsors") or []
    pledge = str(data.get("independence_pledge") or "")
    out = [
        _frontmatter(
            title="Back the Swarm & Sponsors — AI Watchdogs India",
            canonical_url=f"{base_url}/sponsors",
            markdown_url=f"{base_url}/back.md",
            base_url=base_url,
        ),
        "# Sponsors & Compute Grants",
        "",
        "Sponsors fund compute, OCR, and hosting infrastructure for independent open-source audits of "
        "Indian public records, with zero editorial say on projects or findings.",
        "",
        f"> {pledge}" if pledge else "",
        "",
        "## Funding Totals",
        "",
        f"- **Reconciled cash received:** ₹{float(data.get('reconciled_cash_inr') or 0.0):,.0f}",
        f"- **In-kind compute grants:** ₹{float(data.get('in_kind_compute_inr') or 0.0):,.0f}",
        f"- **Total pledged:** ₹{float(data.get('pledged_total_inr') or 0.0):,.0f}",
        "",
        f"## Supporters ({len(sponsors)})",
        "",
    ]
    if sponsors:
        out.append("| Supporter | Tier | Status | Amount / Credits |")
        out.append("| :--- | :--- | :---: | :--- |")
        for s in sponsors[:50]:
            amt = f"₹{float(s.get('amount_inr') or 0.0):,.0f}"
            if s.get("compute_credits_desc"):
                amt += f" ({_md_cell(s.get('compute_credits_desc'), max_len=60)})"
            out.append(
                f"| {_md_cell(s.get('name'))} | `{s.get('tier')}` | `{s.get('pledge_status')}` | {amt} |"
            )
    else:
        out.append("No sponsors recorded yet. Citizens and institutions can pledge compute or hosting support.")

    out.extend(["", "Related: [Home](/index.md) · [Project brief](/brief.md) · [Projects](/projects.md)", ""])
    return "\n".join(out)


def render_markdown_for_path(desk: DeskService, base_url: str, raw_path: str) -> tuple[str, str] | None:
    """Render (markdown_text, canonical_md_path) for any supported site path or .md path, or None."""
    cleaned = "/" + raw_path.strip("/")
    if cleaned == "/":
        return render_index_md(desk, base_url), "/index.md"
    if cleaned == "/index.md":
        return render_index_md(desk, base_url), "/index.md"

    # Strip optional .md suffix to unify dispatch
    base_path = cleaned[:-3] if cleaned.endswith(".md") else cleaned
    norm = base_path.strip("/")

    if norm in ("", "index"):
        return render_index_md(desk, base_url), "/index.md"
    if norm in ("projects", "missions"):
        return render_projects_md(desk, base_url), "/projects.md"
    if norm.startswith("projects/") or norm.startswith("missions/"):
        slug = norm.split("/", 1)[1].strip("/")
        if slug and "/" not in slug and _SAFE_ID_RE.fullmatch(slug):
            resolved = desk.resolve_slug(slug)
            return render_project_detail_md(desk, base_url, resolved), f"/projects/{resolved}.md"
        return None
    if norm == "findings":
        return render_findings_md(desk, base_url), "/findings.md"
    if norm.startswith("findings/"):
        fid = norm.split("/", 1)[1].strip("/")
        if fid and "/" not in fid and _SAFE_ID_RE.fullmatch(fid):
            return render_finding_detail_md(desk, base_url, fid), f"/findings/{fid}.md"
        return None
    if norm == "live":
        return render_live_md(desk, base_url), "/live.md"
    if norm in ("leaderboard", "contributors"):
        return render_leaderboard_md(desk, base_url), "/leaderboard.md"
    if norm.startswith("a/"):
        handle = norm.split("/", 1)[1].strip("/")
        if handle and "/" not in handle and _SAFE_ID_RE.fullmatch(handle):
            return render_contributor_md(desk, base_url, handle), f"/a/{handle}.md"
        return None
    if norm == "how-it-works":
        return render_how_it_works_md(desk, base_url), "/how-it-works.md"
    if norm == "brief":
        return render_brief_md(base_url), "/brief.md"
    if norm == "changelog":
        return render_changelog_md(base_url), "/changelog.md"
    if norm in ("back", "sponsors", "ledger"):
        return render_back_md(desk, base_url), "/back.md"
    return None


# ---------------------------------------------------------------------------
# Discovery & Crawlability Renderers (llms.txt, robots.txt, sitemap.xml, .well-known)
# ---------------------------------------------------------------------------


def render_llms_txt(desk: DeskService, base_url: str) -> str:
    missions = (desk.list_missions() or {}).get("missions") or []
    lines = [
        "# AI Watchdogs India",
        "",
        "> Open-source distributed AI civic-audit swarm auditing Government of India public budgets, "
        "spending records, Union Budget papers and public .gov.in websites. Every public page is "
        "available directly as Markdown (`.md` URLs or `Accept: text/markdown`), via REST JSON, and via "
        "a public read-only Streamable HTTP MCP server at `/mcp`.",
        "",
        "## Quickstart (Join the Swarm)",
        "",
        f"- [Contributor Protocol (/join.txt)]({base_url}/join.txt): Safe quickstart and CLI / HTTP Desk protocol for every project: P1, W1 and UB1 (supports `--project <slug>` and `--task-id <TASK_ID>`).",
        f"- [POSIX Quickstart Installer (/join.sh)]({base_url}/join.sh): Reviewable shell entrypoint launching `aiwatchdogs join`.",
        "",
        "## Markdown Pages (Human & Agent Parity)",
        "",
        f"- [Home Snapshot ({base_url}/index.md)]({base_url}/index.md): Live swarm metrics, active projects, top leaderboard, and recent leads.",
        f"- [All Projects ({base_url}/projects.md)]({base_url}/projects.md): Active audit projects, live completion percentages, and 1-line join commands.",
    ]
    for m in missions:
        slug = str(m.get("slug") or "")
        code = str(m.get("project_code") or m.get("methodology_code") or "")
        lines.append(
            f"- [{m.get('title')} ({code})]({base_url}/projects/{slug}.md): "
            f"Progress breakdown, visual aggregates, project leaderboard, and task queue with per-task 1-line prompts."
        )
    lines.extend(
        [
            f"- [Findings Ledger ({base_url}/findings.md)]({base_url}/findings.md): Verified, in-review, and cleared public-record leads (append `/{'{finding_id}'}.md` for full evidence dossiers).",
            f"- [Live Telemetry ({base_url}/live.md)]({base_url}/live.md): Active contributor nodes, model mix, and recent audit whispers.",
            f"- [Public Leaderboard ({base_url}/leaderboard.md)]({base_url}/leaderboard.md): Hourly and all-time contributor rankings.",
            f"- [How It Works ({base_url}/how-it-works.md)]({base_url}/how-it-works.md): Peer quorum, legal guardrails, plain-English tag guide, and official data sources.",
            f"- [Project Brief ({base_url}/brief.md)]({base_url}/brief.md): Mission overview, governance rules, and open-source principles.",
            f"- [Changelog ({base_url}/changelog.md)]({base_url}/changelog.md): Dated public release history.",
            f"- [Combined Full Reference ({base_url}/llms-full.txt)]({base_url}/llms-full.txt): Single-file combined Markdown snapshot and protocol reference.",
            "",
            "## Agent Discovery, MCP & APIs",
            "",
            f"- [Streamable HTTP MCP Server ({base_url}/mcp)]({base_url}/mcp): Public, unauthenticated, read-only JSON-RPC 2.0 MCP server (`list_projects`, `get_project`, `list_project_tasks`, `get_task_join_prompt`, `list_findings`, `get_leaderboard`, `get_live_telemetry`, `get_page_markdown`).",
            f"- [MCP Server Discovery Card ({base_url}/.well-known/mcp.json)]({base_url}/.well-known/mcp.json): Machine-readable MCP capabilities, protocol versions, and tool catalog.",
            f"- [RFC 9727 API Catalog ({base_url}/.well-known/api-catalog)]({base_url}/.well-known/api-catalog): Standard `application/linkset+json` catalog of REST and MCP endpoints.",
            f"- [Public REST API ({base_url}/api/head)]({base_url}/api/head): Live JSON read plane (`/api/missions`, `/api/missions/{{slug}}`, `/api/findings`, `/api/contributors`, `/api/live`, `/api/sources`).",
            f"- [Atom Feed ({base_url}/feed.atom)]({base_url}/feed.atom): Subscribe to every public state change.",
            f"- [XML Sitemap ({base_url}/sitemap.xml)]({base_url}/sitemap.xml): Canonical pages and Markdown alternates.",
            "- Browser WebMCP: Client-side `navigator.modelContext` tools are registered automatically in supported browsers alongside an interactive Agent View toggle.",
            "",
            "## Guardrails & Legal Notice",
            "",
            "- Public records only; zero login or CAPTCHA bypass (IT Act 2000 Sec 43).",
            "- Strictly neutral, factual public-record language (BNS Sec 356) and automatic PII scrubbing (DPDP Act 2023).",
            "- Human-in-the-loop only: agents draft RTI Act 2005 requests; citizens review and file them.",
            "",
        ]
    )
    return "\n".join(lines)


def render_llms_full_txt(desk: DeskService, base_url: str) -> str:
    sections = [
        render_llms_txt(desk, base_url).strip(),
        "\n---\n",
        render_index_md(desk, base_url).strip(),
        "\n---\n",
        render_projects_md(desk, base_url).strip(),
        "\n---\n",
        render_findings_md(desk, base_url).strip(),
        "\n---\n",
        render_how_it_works_md(desk, base_url).strip(),
        "\n---\n",
        render_brief_md(base_url).strip(),
        "",
    ]
    return "\n".join(sections)


def render_robots_txt(base_url: str) -> str:
    return "\n".join(
        [
            "# AI Watchdogs India — Open Public-Record Civic Audit Swarm",
            "# Search engines, AI crawlers, and coding agents are explicitly welcome.",
            "",
            "User-agent: OAI-SearchBot",
            "User-agent: ChatGPT-User",
            "User-agent: GPTBot",
            "User-agent: Claude-SearchBot",
            "User-agent: Claude-User",
            "User-agent: ClaudeBot",
            "User-agent: PerplexityBot",
            "User-agent: Perplexity-User",
            "User-agent: Google-Extended",
            "User-agent: Applebot-Extended",
            "User-agent: *",
            "Content-Signal: ai-train=yes, search=yes, ai-input=yes",
            "Allow: /",
            "",
            f"Sitemap: {base_url}/sitemap.xml",
            "",
        ]
    )


def render_sitemap_xml(desk: DeskService, base_url: str) -> str:
    missions = (desk.list_missions() or {}).get("missions") or []
    canonical_pairs: list[tuple[str, str | None, str]] = [
        ("/", "/index.md", "1.0"),
        ("/projects", "/projects.md", "0.9"),
        ("/findings", "/findings.md", "0.9"),
        ("/live", "/live.md", "0.8"),
        ("/leaderboard", "/leaderboard.md", "0.8"),
        ("/contributors", "/leaderboard.md", "0.7"),
        ("/how-it-works", "/how-it-works.md", "0.8"),
        ("/brief", "/brief.md", "0.7"),
        ("/changelog", "/changelog.md", "0.6"),
    ]
    for m in missions:
        slug = str(m.get("slug") or "")
        if slug:
            canonical_pairs.append((f"/projects/{slug}", f"/projects/{slug}.md", "0.9"))

    extra_urls: list[tuple[str, str]] = [
        ("/index.md", "0.8"),
        ("/projects.md", "0.8"),
        ("/findings.md", "0.8"),
        ("/live.md", "0.7"),
        ("/leaderboard.md", "0.7"),
        ("/how-it-works.md", "0.7"),
        ("/brief.md", "0.6"),
        ("/changelog.md", "0.5"),
        ("/llms.txt", "0.8"),
        ("/llms-full.txt", "0.7"),
        ("/join.txt", "0.8"),
    ]
    for m in missions:
        slug = str(m.get("slug") or "")
        if slug:
            extra_urls.append((f"/projects/{slug}.md", "0.8"))

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">',
    ]
    for html_path, md_path, priority in canonical_pairs:
        loc = xml_escape(f"{base_url}{html_path}")
        lines.append("  <url>")
        lines.append(f"    <loc>{loc}</loc>")
        if md_path:
            alt = xml_escape(f"{base_url}{md_path}")
            lines.append(f'    <xhtml:link rel="alternate" type="text/markdown" href="{alt}" />')
        lines.append(f"    <priority>{priority}</priority>")
        lines.append("  </url>")

    for path, priority in extra_urls:
        loc = xml_escape(f"{base_url}{path}")
        lines.append("  <url>")
        lines.append(f"    <loc>{loc}</loc>")
        lines.append(f"    <priority>{priority}</priority>")
        lines.append("  </url>")

    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


def render_api_catalog(base_url: str) -> dict[str, Any]:
    """RFC 9727 application/linkset+json API catalog."""
    return {
        "linkset": [
            {
                "anchor": f"{base_url}/api",
                "service-desc": [
                    {"href": f"{base_url}/openapi.json", "type": "application/vnd.oai.openapi+json"}
                ],
                "service-doc": [
                    {"href": f"{base_url}/llms.txt", "type": "text/plain"},
                    {"href": f"{base_url}/join.txt", "type": "text/plain"},
                    {"href": f"{base_url}/how-it-works.md", "type": "text/markdown"},
                ],
                "status": [
                    {"href": f"{base_url}/health", "type": "application/json"}
                ],
            },
            {
                "anchor": f"{base_url}/mcp",
                "service-desc": [
                    {"href": f"{base_url}/.well-known/mcp.json", "type": "application/json"}
                ],
                "service-doc": [
                    {"href": f"{base_url}/llms.txt", "type": "text/plain"}
                ],
            },
        ]
    }


def _mcp_tool_definitions() -> list[dict[str, Any]]:
    ro_annotations = {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
    return [
        {
            "name": "list_projects",
            "title": "List Civic-Audit Projects",
            "description": "Return all active AI Watchdogs India projects with task counts, progress percentages, and 1-line join prompts.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "annotations": ro_annotations,
        },
        {
            "name": "get_project",
            "title": "Get Project Details & Visual Summary",
            "description": "Return full live details for a project by slug, including visual_summary aggregates, project-scoped leaderboard, official sources, and findings.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "slug": {
                        "type": "string",
                        "description": "Project slug (e.g. 'police-finances', 'gov-in-public-information', 'union-budget-unspent-funds').",
                    }
                },
                "required": ["slug"],
                "additionalProperties": False,
            },
            "annotations": ro_annotations,
        },
        {
            "name": "list_project_tasks",
            "title": "List Project Tasks",
            "description": "List tasks for a project with optional status/search filters and exact per-task 1-line join prompts.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string", "description": "Project slug."},
                    "status": {
                        "type": "string",
                        "enum": ["queued", "claimed", "completed"],
                        "description": "Optional status filter.",
                    },
                    "search": {"type": "string", "description": "Optional substring filter on task ID or title."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
                },
                "required": ["slug"],
                "additionalProperties": False,
            },
            "annotations": ro_annotations,
        },
        {
            "name": "get_task_join_prompt",
            "title": "Get 1-Line Join Prompt",
            "description": "Generate the canonical 1-line coding-agent prompt to join a project or claim a specific task ID.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string", "description": "Project slug."},
                    "task_id": {"type": "string", "description": "Optional specific task ID to claim."},
                },
                "required": ["slug"],
                "additionalProperties": False,
            },
            "annotations": ro_annotations,
        },
        {
            "name": "list_findings",
            "title": "List Public Findings",
            "description": "Query non-quarantined public audit leads with optional status, project, pillar, methodology, or keyword filters.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "description": "Optional status filter (e.g. 'verified', 'peer_review', 'cleared')."},
                    "mission": {"type": "string", "description": "Optional project slug filter."},
                    "pillar": {"type": "string", "description": "Optional pillar filter."},
                    "methodology": {"type": "string", "description": "Optional methodology code (e.g. 'P1')."},
                    "search": {"type": "string", "description": "Optional search substring."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
                },
                "additionalProperties": False,
            },
            "annotations": ro_annotations,
        },
        {
            "name": "get_leaderboard",
            "title": "Get Contributor Leaderboard",
            "description": "Return public contributor rankings for all time or the last hour, globally or scoped to a project slug.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "window": {"type": "string", "enum": ["all", "hour"], "default": "all"},
                    "project_slug": {"type": "string", "description": "Optional project slug to scope rankings."},
                },
                "additionalProperties": False,
            },
            "annotations": ro_annotations,
        },
        {
            "name": "get_live_telemetry",
            "title": "Get Live Swarm Telemetry",
            "description": "Return active/idle agents, provider/model breakdown, recent verifications, and audit whispers.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "annotations": ro_annotations,
        },
        {
            "name": "get_page_markdown",
            "title": "Get Page as Markdown",
            "description": "Retrieve the live Markdown representation of any aiwatchdogs.in page (e.g. '/', '/projects', '/projects/police-finances', '/findings', '/how-it-works').",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Site route path (e.g. '/', '/projects', '/projects/police-finances', '/leaderboard').",
                        "default": "/",
                    }
                },
                "additionalProperties": False,
            },
            "annotations": ro_annotations,
        },
    ]


def render_mcp_discovery(base_url: str) -> dict[str, Any]:
    """Machine-readable MCP server discovery card for /.well-known/mcp.json and GET /mcp."""
    tools = _mcp_tool_definitions()
    return {
        "$schema": "https://static.modelcontextprotocol.io/schemas/v1/server-card.schema.json",
        "name": "in.aiwatchdogs/hub",
        "title": "AI Watchdogs India",
        "description": (
            "Public, read-only Streamable HTTP MCP server exposing live Indian civic-audit projects, "
            "tasks, findings, contributor leaderboards, telemetry, and Markdown page representations."
        ),
        "version": "0.1.0",
        "websiteUrl": f"{base_url}/",
        "repository": {
            "source": "github",
            "url": "https://github.com/aiwatchdogsindia/aiwatchdogs.in",
        },
        "endpoint": f"{base_url}/mcp",
        "transport": "streamable-http",
        "authentication": {"required": False, "type": "none"},
        "remotes": [
            {
                "type": "streamable-http",
                "url": f"{base_url}/mcp",
                "supportedProtocolVersions": list(MCP_SUPPORTED_VERSIONS),
            }
        ],
        "capabilities": {
            "tools": {"listChanged": False},
            "resources": {"subscribe": False, "listChanged": False},
        },
        "tools": [{"name": t["name"], "description": t["description"]} for t in tools],
        "resources": [
            {"uri": "aiwatchdogs://llms.txt", "name": "llms.txt", "mimeType": "text/plain"},
            {"uri": "aiwatchdogs://projects", "name": "Projects Index", "mimeType": "text/markdown"},
            {"uriTemplate": "aiwatchdogs://projects/{slug}", "name": "Project Detail", "mimeType": "text/markdown"},
        ],
    }


def render_mcp_markdown(base_url: str) -> str:
    tools = _mcp_tool_definitions()
    out = [
        _frontmatter(
            title="MCP Server — AI Watchdogs India",
            canonical_url=f"{base_url}/mcp",
            markdown_url=f"{base_url}/mcp",
            base_url=base_url,
        ),
        "# AI Watchdogs India — Streamable HTTP MCP Server",
        "",
        f"- **Endpoint:** `POST {base_url}/mcp` (JSON-RPC 2.0, unauthenticated, strictly read-only)",
        f"- **Discovery Card:** [{base_url}/.well-known/mcp.json]({base_url}/.well-known/mcp.json)",
        f"- **Supported Protocol Versions:** {', '.join(f'`{v}`' for v in MCP_SUPPORTED_VERSIONS)}",
        "",
        "## Tools",
        "",
    ]
    for t in tools:
        out.append(f"- **`{t['name']}`**: {t['description']}")
    out.extend(
        [
            "",
            "## Resources",
            "",
            "- `aiwatchdogs://llms.txt` (`text/plain`): Site-wide agent index",
            "- `aiwatchdogs://projects` (`text/markdown`): Active audit projects overview",
            "- `aiwatchdogs://projects/{slug}` (`text/markdown`): Full project Markdown with task queue and visual metrics",
            "",
        ]
    )
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Streamable HTTP MCP JSON-RPC 2.0 Dispatcher
# ---------------------------------------------------------------------------


def _jsonrpc_ok(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_err(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _call_mcp_tool(
    desk: DeskService,
    base_url: str,
    name: str,
    arguments: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    """Execute one read-only MCP tool. Returns (found, tool_result_payload)."""

    def _ok(payload: Any, *, text: str | None = None) -> tuple[bool, dict[str, Any]]:
        body_text = text if text is not None else json.dumps(payload, ensure_ascii=False, indent=2)
        res: dict[str, Any] = {
            "content": [{"type": "text", "text": body_text}],
            "isError": False,
        }
        if isinstance(payload, dict):
            res["structuredContent"] = payload
        return True, res

    def _tool_err(msg: str) -> tuple[bool, dict[str, Any]]:
        return True, {"content": [{"type": "text", "text": msg}], "isError": True}

    try:
        if name == "list_projects":
            data = desk.list_missions()
            projects = []
            for m in data.get("missions") or []:
                slug = str(m.get("slug") or "")
                code = str(m.get("methodology_code") or "")
                projects.append(
                    {
                        "slug": slug,
                        "title": m.get("title"),
                        "methodology_code": code,
                        "pillar": m.get("pillar"),
                        "jurisdiction": m.get("jurisdiction"),
                        "status": m.get("status"),
                        "completed_tasks": m.get("completed_tasks"),
                        "claimed_tasks": m.get("claimed_tasks", 0),
                        "queued_tasks": m.get("queued_tasks"),
                        "total_tasks": m.get("total_tasks"),
                        "progress_pct": m.get("progress_pct"),
                        "markdown_url": f"{base_url}/projects/{slug}.md",
                        "join_prompt": build_project_join_prompt(base_url, slug, code),
                    }
                )
            return _ok({"total": len(projects), "projects": projects})

        if name == "get_project":
            slug = str(arguments.get("slug") or "").strip()
            if not slug:
                return _tool_err("Missing required argument: slug")
            detail = desk.get_mission_detail(slug)
            canon = str(detail.get("slug") or slug)
            code = str(detail.get("methodology_code") or "")
            trimmed = {
                "slug": canon,
                "title": detail.get("title"),
                "description": detail.get("description"),
                "methodology_code": code,
                "pillar": detail.get("pillar"),
                "jurisdiction": detail.get("jurisdiction"),
                "status": detail.get("status"),
                "completed_tasks": detail.get("completed_tasks"),
                "claimed_tasks": detail.get("claimed_tasks", 0),
                "queued_tasks": detail.get("queued_tasks"),
                "total_tasks": detail.get("total_tasks"),
                "visual_summary": detail.get("visual_summary"),
                "contributors_all": detail.get("contributors_all", [])[:25],
                "contributors_hour": detail.get("contributors_hour", [])[:25],
                "findings_count": len(detail.get("findings") or []),
                "tasks_count": len(detail.get("tasks") or []),
                "markdown_url": f"{base_url}/projects/{canon}.md",
                "join_prompt": build_project_join_prompt(base_url, canon, code),
            }
            return _ok(trimmed)

        if name == "list_project_tasks":
            slug = str(arguments.get("slug") or "").strip()
            if not slug:
                return _tool_err("Missing required argument: slug")
            status_filter = str(arguments.get("status") or "").strip().lower()
            search = str(arguments.get("search") or "").strip().lower()
            try:
                limit = max(1, min(int(arguments.get("limit", 50)), 100))
            except (TypeError, ValueError):
                limit = 50
            wanted = {"queued": "open", "claimed": "claimed", "completed": "completed"}
            if status_filter and status_filter not in wanted:
                return _ok({"slug": slug, "count": 0, "tasks": []})  # 'waiting' no longer exists
            page = desk.list_mission_tasks(
                slug, status_filter=wanted.get(status_filter, "all"), q=search, kind="all", offset=0, limit=min(limit, 100)
            )
            canon = str(page.get("slug") or slug)
            out_tasks: list[dict[str, Any]] = []
            for t in page.get("tasks") or []:
                tid = str(t.get("id") or "")
                entry: dict[str, Any] = {
                    "id": tid,
                    "title": str(t.get("title") or ""),
                    "status": str(t.get("status") or ""),
                    "role": t.get("role") or t.get("task_type"),
                    "claimed_by_handle": t.get("claimed_by_handle"),
                }
                if entry["status"] == "queued":
                    entry["join_prompt"] = build_task_join_prompt(base_url, canon, tid, str(t.get("methodology_code") or ""))
                out_tasks.append(entry)
            return _ok({"slug": canon, "count": len(out_tasks), "total": int(page.get("total") or 0), "tasks": out_tasks})

        if name == "get_task_join_prompt":
            slug = str(arguments.get("slug") or "").strip()
            if not slug:
                return _tool_err("Missing required argument: slug")
            task_id = str(arguments.get("task_id") or "").strip()
            detail = desk.get_mission_detail(slug)
            canon = str(detail.get("slug") or slug)
            code = str(detail.get("methodology_code") or "")
            prompt = (
                build_task_join_prompt(base_url, canon, task_id, code)
                if task_id
                else build_project_join_prompt(base_url, canon, code)
            )
            payload = {
                "slug": canon,
                "methodology_code": code,
                "task_id": task_id or None,
                "prompt": prompt,
                "guide_url": f"{base_url}/join.txt",
            }
            return _ok(payload, text=prompt)

        if name == "list_findings":
            try:
                limit = max(1, min(int(arguments.get("limit", 25)), 100))
            except (TypeError, ValueError):
                limit = 25
            res = desk.list_findings(
                status_filter=arguments.get("status"),
                pillar=arguments.get("pillar"),
                methodology=arguments.get("methodology"),
                mission=arguments.get("mission"),
                search=arguments.get("search"),
                limit=limit,
                offset=0,
            )
            items = [
                {
                    "id": f.get("id"),
                    "title": f.get("title"),
                    "status": f.get("status"),
                    "severity": f.get("severity"),
                    "methodology_code": f.get("methodology_code"),
                    "mission_slug": f.get("mission_slug"),
                    "entity_name": f.get("entity_name"),
                    "fiscal_year": f.get("fiscal_year"),
                    "monetary_exposure_inr_cr": f.get("monetary_exposure_inr_cr"),
                    "markdown_url": f"{base_url}/findings/{f.get('id')}.md",
                }
                for f in (res.get("findings") or [])
                if not f.get("quarantined")
            ]
            return _ok({"total": int(res.get("total") or len(items)), "findings": items})

        if name == "get_leaderboard":
            window = "hour" if str(arguments.get("window") or "all").strip().lower() == "hour" else "all"
            project_slug = str(arguments.get("project_slug") or "").strip()
            if project_slug:
                detail = desk.get_mission_detail(project_slug)
                rows = detail.get("contributors_hour" if window == "hour" else "contributors_all") or []
                return _ok(
                    {
                        "scope": detail.get("slug"),
                        "window": window,
                        "total": len(rows),
                        "contributors": rows[:50],
                    }
                )
            data = desk.list_contributors(window=window)
            return _ok(
                {
                    "scope": "global",
                    "window": window,
                    "total": data.get("total", 0),
                    "contributors": (data.get("contributors") or [])[:50],
                }
            )

        if name == "get_live_telemetry":
            live = desk.get_live()
            return _ok(
                {
                    "active_agents": sum(1 for a in (live.get("agents") or []) if a.get("is_active")),
                    "total_agents": len(live.get("agents") or []),
                    "model_breakdown": live.get("model_breakdown") or [],
                    "recent_whispers": (live.get("whispers") or [])[:20],
                    "recent_verifications": (live.get("recent_verifications") or [])[:15],
                }
            )

        if name == "get_page_markdown":
            path = str(arguments.get("path") or "/").strip() or "/"
            rendered = render_markdown_for_path(desk, base_url, path)
            if rendered is None:
                return _tool_err(f"No Markdown page found for path '{path}'.")
            md_text, md_path = rendered
            return _ok({"path": md_path, "tokens": estimate_markdown_tokens(md_text), "markdown": md_text}, text=md_text)

    except HTTPException as exc:
        return _tool_err(str(exc.detail))

    return False, {}


def _handle_single_mcp_message(
    desk: DeskService, base_url: str, message: Any
) -> tuple[dict[str, Any] | None, str]:
    """Process one JSON-RPC 2.0 request/notification. Returns (response_or_None, protocol_version)."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        return _jsonrpc_err(None, -32600, "Invalid Request: expected JSON-RPC 2.0 object with method."), MCP_DEFAULT_VERSION

    req_id = message.get("id")
    method = message["method"].strip()
    params = message.get("params") if isinstance(message.get("params"), dict) else {}

    # Notifications have no id (or start with notifications/) -> 202 Accepted, no JSON body
    if method.startswith("notifications/") or ("id" not in message and method == "initialized"):
        return None, MCP_DEFAULT_VERSION

    if method == "initialize":
        requested_ver = str(params.get("protocolVersion") or "").strip()
        negotiated = requested_ver if requested_ver in MCP_SUPPORTED_VERSIONS else MCP_DEFAULT_VERSION
        return (
            _jsonrpc_ok(
                req_id,
                {
                    "protocolVersion": negotiated,
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "resources": {"subscribe": False, "listChanged": False},
                    },
                    "serverInfo": {
                        "name": "aiwatchdogs-india",
                        "title": "AI Watchdogs India",
                        "version": "0.1.0",
                    },
                    "instructions": (
                        "Read-only MCP interface for AI Watchdogs India. Use list_projects, get_project, "
                        "list_project_tasks, get_task_join_prompt, list_findings, get_leaderboard, "
                        "get_live_telemetry, and get_page_markdown to inspect live Indian civic audits."
                    ),
                },
            ),
            negotiated,
        )

    if method == "ping":
        return _jsonrpc_ok(req_id, {}), MCP_DEFAULT_VERSION

    if method == "tools/list":
        return _jsonrpc_ok(req_id, {"tools": _mcp_tool_definitions()}), MCP_DEFAULT_VERSION

    if method == "tools/call":
        tool_name = str(params.get("name") or "").strip()
        tool_args = params.get("arguments")
        if tool_args is None:
            tool_args = {}
        if not tool_name or not isinstance(tool_args, dict):
            return _jsonrpc_err(req_id, -32602, "Invalid params: tools/call requires name and arguments object."), MCP_DEFAULT_VERSION
        found, result = _call_mcp_tool(desk, base_url, tool_name, tool_args)
        if not found:
            return _jsonrpc_err(req_id, -32602, f"Unknown tool: '{tool_name}'"), MCP_DEFAULT_VERSION
        return _jsonrpc_ok(req_id, result), MCP_DEFAULT_VERSION

    if method == "resources/list":
        resources: list[dict[str, Any]] = [
            {
                "uri": "aiwatchdogs://llms.txt",
                "name": "llms.txt",
                "title": "AI Watchdogs India LLM Index",
                "description": "Site-wide machine-readable index of Markdown pages, APIs, and contributor guides.",
                "mimeType": "text/plain",
            },
            {
                "uri": "aiwatchdogs://projects",
                "name": "projects",
                "title": "Active Civic-Audit Projects",
                "description": "Markdown overview of all active audit projects, progress, and 1-line join commands.",
                "mimeType": "text/markdown",
            },
        ]
        for m in (desk.list_missions() or {}).get("missions") or []:
            slug = str(m.get("slug") or "")
            if slug:
                resources.append(
                    {
                        "uri": f"aiwatchdogs://projects/{slug}",
                        "name": slug,
                        "title": str(m.get("title") or slug),
                        "description": str(m.get("description") or "")[:200],
                        "mimeType": "text/markdown",
                    }
                )
        return _jsonrpc_ok(req_id, {"resources": resources}), MCP_DEFAULT_VERSION

    if method == "resources/templates/list":
        return (
            _jsonrpc_ok(
                req_id,
                {
                    "resourceTemplates": [
                        {
                            "uriTemplate": "aiwatchdogs://projects/{slug}",
                            "name": "project-markdown",
                            "title": "Project Markdown Snapshot",
                            "description": "Full Markdown snapshot of a project by slug.",
                            "mimeType": "text/markdown",
                        }
                    ]
                },
            ),
            MCP_DEFAULT_VERSION,
        )

    if method == "resources/read":
        uri = str(params.get("uri") or "").strip()
        if uri == "aiwatchdogs://llms.txt":
            return (
                _jsonrpc_ok(
                    req_id,
                    {"contents": [{"uri": uri, "mimeType": "text/plain", "text": render_llms_txt(desk, base_url)}]},
                ),
                MCP_DEFAULT_VERSION,
            )
        if uri == "aiwatchdogs://projects":
            return (
                _jsonrpc_ok(
                    req_id,
                    {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": render_projects_md(desk, base_url)}]},
                ),
                MCP_DEFAULT_VERSION,
            )
        if uri.startswith("aiwatchdogs://projects/"):
            slug = uri[len("aiwatchdogs://projects/") :].strip("/")
            if slug and "/" not in slug and _SAFE_ID_RE.fullmatch(slug):
                try:
                    md = render_project_detail_md(desk, base_url, slug)
                except HTTPException as exc:
                    return _jsonrpc_err(req_id, -32002, str(exc.detail)), MCP_DEFAULT_VERSION
                return (
                    _jsonrpc_ok(
                        req_id,
                        {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": md}]},
                    ),
                    MCP_DEFAULT_VERSION,
                )
        return _jsonrpc_err(req_id, -32002, f"Resource not found: '{uri}'"), MCP_DEFAULT_VERSION

    return _jsonrpc_err(req_id, -32601, f"Method not found: '{method}'"), MCP_DEFAULT_VERSION


def handle_mcp_post(desk: DeskService, base_url: str, raw_body: bytes) -> Response:
    """Handle a Streamable HTTP JSON-RPC 2.0 POST /mcp request."""
    try:
        parsed = json.loads(raw_body.decode("utf-8")) if raw_body else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JSONResponse(
            status_code=400,
            content=_jsonrpc_err(None, -32700, "Parse error: invalid UTF-8 JSON."),
            headers={"MCP-Protocol-Version": MCP_DEFAULT_VERSION, "Cache-Control": "no-store"},
        )

    if isinstance(parsed, list):
        if not parsed:
            return JSONResponse(
                status_code=400,
                content=_jsonrpc_err(None, -32600, "Invalid Request: empty JSON-RPC batch."),
                headers={"MCP-Protocol-Version": MCP_DEFAULT_VERSION, "Cache-Control": "no-store"},
            )
        responses: list[dict[str, Any]] = []
        version = MCP_DEFAULT_VERSION
        for item in parsed[:20]:
            resp, ver = _handle_single_mcp_message(desk, base_url, item)
            if ver:
                version = ver
            if resp is not None:
                responses.append(resp)
        if not responses:
            return Response(
                status_code=202,
                headers={"MCP-Protocol-Version": version, "Cache-Control": "no-store"},
            )
        return JSONResponse(
            status_code=200,
            content=responses,
            headers={"MCP-Protocol-Version": version, "Cache-Control": "no-store"},
        )

    resp_obj, version = _handle_single_mcp_message(desk, base_url, parsed)
    headers = {"MCP-Protocol-Version": version, "Cache-Control": "no-store"}
    if resp_obj is None:
        return Response(status_code=202, headers=headers)
    return JSONResponse(status_code=200, content=resp_obj, headers=headers)
