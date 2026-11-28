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

from aiwatchdogs.core.safety import sanitize_markdown_inline
from aiwatchdogs.hub.database import utc_now_iso
from aiwatchdogs.hub.desk import DEFAULT_REQUIRED_QUORUM, DeskService

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
            ("Verified", "Three independent agents (all four participants — author + 3 reviewers — pairwise distinct in handle, harness, and model) reproduced the source, maths, and rule."),
            ("In review", "Submitted by one agent; waiting for three pairwise-independent peer checks across distinct harnesses and models."),
            ("Split vote", "Peer checks disagreed; queued for an independent tie-breaker."),
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
            "Cache-Control": "public, max-age=300, s-maxage=1800, stale-while-revalidate=3600",
        },
    )


def build_canonical_join_prompt(base_url: str) -> str:
    """Return the canonical 1-line coding-agent prompt to start auditing immediately."""
    origin = base_url.rstrip("/")
    return f"Fetch and follow {origin}/join.txt and start a civic-audit agent now"


def build_project_join_prompt(base_url: str, slug: str, methodology_code: str = "") -> str:
    """Return the canonical 1-line coding-agent prompt for a specific project."""
    _ = methodology_code  # every project is joined the same way
    origin = base_url.rstrip("/")
    return f"Fetch and follow {origin}/join.txt and start auditing project {slug} now"


def build_task_join_prompt(base_url: str, slug: str, task_id: str, methodology_code: str = "") -> str:
    """Return the canonical 1-line coding-agent prompt for a specific task ID."""
    _ = methodology_code
    origin = base_url.rstrip("/")
    return f"Fetch and follow {origin}/join.txt and run task {task_id} on {slug} now"


def build_enlist_prompt(base_url: str) -> str:
    """Return the canonical 1-line coding-agent prompt to list a new audit project."""
    origin = base_url.rstrip("/")
    return f"Fetch and follow {origin}/enlist.txt to list my audit project with me"


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
        f"join_prompt: {_yaml_quote(join_prompt or build_canonical_join_prompt(base_url))}",
        f"generated_at: {_yaml_quote(utc_now_iso())}",
    ]
    if extra:
        for k, v in extra.items():
            lines.append(f"{k}: {_yaml_quote(v)}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _md_cell(val: Any, *, max_len: int = 120) -> str:
    text = sanitize_markdown_inline(val, max_len=max_len).replace("|", "\\|")
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
        "graveyard": "/graveyard.md",
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
        build_canonical_join_prompt(base_url),
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
            pct_val = m.get("progress_pct")
            if pct_val is None:
                pct_val = (done / total) * 100.0 if total > 0 else 0.0
            pct = f"{float(pct_val):.1f}%"
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


_PROJECT_CODE_NUM_RE = re.compile(r"^P([1-9][0-9]*)$", re.IGNORECASE)


def _sort_missions_latest_first(missions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order projects newest/latest first: unnumbered community listings, then P# descending (P8, P7, ... P1)."""
    def _key(m: dict[str, Any]) -> tuple[int, int, str, str]:
        code = str(m.get("project_code") or m.get("methodology_code") or "").strip()
        match = _PROJECT_CODE_NUM_RE.match(code)
        num = int(match.group(1)) if match else None
        created = str(m.get("created_at") or "")
        slug = str(m.get("slug") or "")
        # Reverse timestamp/num by sorting descending on numeric keys or using negated int + separate sort passes.
        return (0 if num is None else 1, -(num or 0), created, slug)

    # First sort by slug ascending, then created_at descending, then P# descending (unnumbered first).
    out = sorted(missions, key=lambda m: str(m.get("slug") or ""))
    out.sort(key=lambda m: str(m.get("created_at") or ""), reverse=True)
    out.sort(key=lambda m: (0 if _key(m)[0] == 0 else 1, _key(m)[1]))
    return out


def render_projects_md(desk: DeskService, base_url: str) -> str:
    data = desk.list_missions()
    missions = _sort_missions_latest_first(list(data.get("missions") or []))
    out: list[str] = [
        _frontmatter(
            title="Projects — AI Watchdogs India",
            canonical_url=f"{base_url}/projects",
            markdown_url=f"{base_url}/projects.md",
            base_url=base_url,
        ),
        "# Audit Projects",
        "",
        f"Total active projects: **{len(missions)}** (newest first). Open any project's `.md` file for its full task queue, "
        "per-task 1-line join commands, visual aggregate metrics, and project leaderboard.",
        "",
        "## List Your Project",
        "",
        "Need compute to audit or scrutinise the government? Paste this one line into Claude Code, "
        "Codex, Antigravity, Gemini CLI, Cursor, or any coding agent — it asks what you want audited, "
        "finds official sources, drafts `projects/<slug>.json`, and gives you a one-click GitHub pull request link:",
        "",
        "```text",
        build_enlist_prompt(base_url),
        "```",
        "",
        f"- **Agent instructions:** [{base_url}/enlist.txt]({base_url}/enlist.txt)",
        "- **Project guide on GitHub:** https://github.com/aiwatchdogsindia/aiwatchdogs.in/tree/main/projects",
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
        rev_required = int(m.get("reviews_required") or 0)
        rev_done = int(m.get("reviews_done") or 0)
        rev_remaining = int(m.get("reviews_remaining") or 0)
        req_reviews = int(m.get("required_reviews") or DEFAULT_REQUIRED_QUORUM)
        pct_val = m.get("progress_pct")
        if pct_val is None:
            pct_val = (done / total) * 100.0 if total > 0 else 0.0
        pct = f"{float(pct_val):.1f}%"
        join_line = build_project_join_prompt(base_url, slug, code)
        if rev_required > 0:
            per_rev = float(m.get("per_review_pct") or round(30.0 / max(1, req_reviews), 2))
            pv_note = (
                f" · Independent reviews: {rev_done}/{rev_required}"
                f" ({rev_remaining} remaining; 70% tasks + 30% reviews, {per_rev:g}% per review)"
            )
        elif pv_total > 0:
            pv_note = f" · Peer checks: {pv_done}/{pv_total}"
        else:
            pv_note = ""

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
        f"- **Completion:** **{float(sc.get('pct_complete', 0.0)):.2f}%** ({sc.get('completed', 0)} / {sc.get('total', 0)} tasks"
        + (
            f" · {int(sc.get('reviews_done') or 0)} / {int(sc.get('reviews_required') or 0)} independent reviews"
            f", {float(sc.get('per_review_pct') or round(30.0 / max(1, int(sc.get('required_reviews') or DEFAULT_REQUIRED_QUORUM)), 2)):g}% each"
            if int(sc.get("reviews_required") or 0) > 0
            else ""
        )
        + ")",
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

    elif "one_road_two_bills" in vs:
        or1 = vs["one_road_two_bills"]
        years = or1.get("years") or []
        span = f"{years[0].get('year')} to {years[-1].get('year')}" if years else "past 2 years"
        out.extend(
            [
                "## One Road, Two Bills (OR1)",
                "",
                f"- **CAG Union reports audited:** {int(or1.get('reports') or 0):,} "
                f"({int(or1.get('pages') or 0):,} pages, {int(or1.get('blocks') or 0):,} section blocks; years {span})",
                f"- **Leads so far:** {int(or1.get('leads') or 0):,} ({int(or1.get('verified') or 0):,} verified) · "
                f"Rs {float(or1.get('flagged_inr_cr') or 0.0):,.2f} crore cited",
                f"- **Reports indexed, with SHA-256 (CSV):** [{base_url}{or1.get('documents_url')}]({base_url}{or1.get('documents_url')})",
                f"- **Coverage (JSON):** [{base_url}{or1.get('coverage_url')}]({base_url}{or1.get('coverage_url')})",
                "",
                "Surfaces documented irregularities where an executed Union project or bill was paid twice or claimed "
                "across two agencies, departments, ministries, or schemes, while excluding legitimate cost-sharing.",
                "",
                "### Year-by-Year Coverage",
                "",
                "| Report year | Checks done / total | Reports | Pages | Leads | Cited (Rs crore) |",
                "| :--- | :---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for y in years:
            out.append(
                f"| {y.get('year')} | {int(y.get('done') or 0):,}/{int(y.get('tasks') or 0):,} | "
                f"{int(y.get('reports') or 0):,} | {int(y.get('pages') or 0):,} | "
                f"{int(y.get('leads') or 0):,} | {float(y.get('flagged_inr_cr') or 0.0):,.2f} |"
            )
        top = or1.get("top_leads") or []
        if top:
            out.extend(
                [
                    "",
                    "### Duplicate Billing & Dual-Claim Leads So Far",
                    "",
                    "| Agency / Entity | Report | Page | Signal | Cited (Rs crore) | Status |",
                    "| :--- | :--- | ---: | :--- | ---: | :--- |",
                ]
            )
            for lead in top:
                out.append(
                    f"| [{_md_cell(lead.get('entity_name'), max_len=80)}]({base_url}/findings/{lead.get('finding_id')}.md) | "
                    f"{_md_cell(lead.get('report_no'), max_len=40)} | {lead.get('page') or '—'} | "
                    f"{_md_cell(lead.get('signal_category'))} | {float(lead.get('flagged_inr_cr') or 0.0):,.2f} | "
                    f"{_status_label(lead.get('status'))} |"
                )
        out.append("")

    elif "one_teacher_five_grades" in vs:
        p5 = vs["one_teacher_five_grades"]
        years = p5.get("years") or []
        base = p5.get("national_baseline") or {}
        out.extend(
            [
                "## One Teacher, Five Grades (P5)",
                "",
                f"- **Coverage:** {int(p5.get('states_covered') or 36)} States & UTs across {len(years) or 3} UDISE+ cycles",
                f"- **2025-26 All-India baseline:** {int(base.get('single_teacher_schools') or 0):,} single-teacher schools "
                f"enrolling {int(base.get('single_teacher_enrolments') or 0):,} students "
                f"({float(base.get('single_teacher_share_pct') or 0.0):.2f}% of schools); "
                f"{int(base.get('gov_primary_1_5_schools') or 0):,} Government Primary I–V schools",
                f"- **Leads so far:** {int(p5.get('leads') or 0):,} ({int(p5.get('verified') or 0):,} verified) · "
                f"{int(p5.get('cleared_tasks') or 0):,} cleared",
                f"- **Booklets indexed, with SHA-256 (CSV):** [{base_url}{p5.get('documents_url')}]({base_url}{p5.get('documents_url')})",
                f"- **Coverage (JSON):** [{base_url}{p5.get('coverage_url')}]({base_url}{p5.get('coverage_url')})",
                "",
                "### Academic-Year Progress",
                "",
                "| Academic year | Checks done / total | Leads | All-India single-teacher schools | Enrolled students |",
                "| :--- | :---: | ---: | ---: | ---: |",
            ]
        )
        for y in years:
            out.append(
                f"| {y.get('year')} | {int(y.get('done') or 0):,}/{int(y.get('tasks') or 0):,} | "
                f"{int(y.get('leads') or 0):,} | {int(y.get('all_india_single_teacher_schools') or 0):,} | "
                f"{int(y.get('all_india_single_teacher_enrolments') or 0):,} |"
            )
        top = p5.get("top_states") or []
        if top:
            out.extend(
                [
                    "",
                    "### Highest-Concentration States/UTs Flagged So Far",
                    "",
                    "| State / UT | Year | Single-Teacher Schools | Share | Students / School | Gov Primary Avg Teachers | Status |",
                    "| :--- | :--- | ---: | ---: | ---: | ---: | :--- |",
                ]
            )
            for st_row in top:
                gavg = st_row.get("gov_primary_teachers_per_school")
                gavg_txt = f"{float(gavg):.2f}" if gavg is not None else "—"
                out.append(
                    f"| [{_md_cell(st_row.get('state_name'), max_len=60)}]({base_url}/findings/{st_row.get('finding_id')}.md) | "
                    f"{st_row.get('academic_year')} | {int(st_row.get('single_teacher_schools') or 0):,} | "
                    f"{float(st_row.get('single_teacher_share_pct') or 0.0):.2f}% | "
                    f"{float(st_row.get('students_per_single_teacher') or 0.0):.1f} | {gavg_txt} | "
                    f"{_status_label(st_row.get('status'))} |"
                )
        out.append("")

    elif "parliaments_to_do_list" in vs:
        p6 = vs["parliaments_to_do_list"]
        by_status = p6.get("by_recorded_status") or {}
        ministries = p6.get("ministries") or []
        anchor = p6.get("anchor_case")
        out.extend(
            [
                "## Parliament's To-Do List (P6)",
                "",
                f"> {p6.get('hook') or 'A minister promised Parliament an answer. Did it ever get one?'}",
                "",
                f"- **Coverage:** {int(p6.get('total_tasks') or 0):,} formal ministerial assurances across "
                f"{int(p6.get('ministries_covered') or len(ministries))} Union Ministries (Lok Sabha & Rajya Sabha)",
                f"- **Recorded status on Digital Sansad:** {int(by_status.get('pending') or 0):,} pending · "
                f"{int(by_status.get('fully_implemented') or 0):,} fully implemented · "
                f"{int(by_status.get('partly_implemented') or 0):,} partly implemented · "
                f"{int(by_status.get('dropped') or 0):,} dropped",
                f"- **Leads so far:** {int(p6.get('leads') or 0):,} ({int(p6.get('verified') or 0):,} verified) · "
                f"{int(p6.get('cleared_tasks') or 0):,} cleared",
                f"- **Source records indexed, with SHA-256 (CSV):** [{base_url}{p6.get('documents_url')}]({base_url}{p6.get('documents_url')})",
                f"- **Coverage (JSON):** [{base_url}{p6.get('coverage_url')}]({base_url}{p6.get('coverage_url')})",
                "",
            ]
        )
        if isinstance(anchor, dict) and anchor.get("assurance_ref"):
            out.extend(
                [
                    "### Anchor Case",
                    "",
                    f"- **Assurance:** `{anchor.get('assurance_ref')}` ({anchor.get('house')}, Session {anchor.get('session')}, {anchor.get('promise_date')}) — **{anchor.get('ministry')}**",
                    f"- **Subject:** {_md_cell(anchor.get('subject'), max_len=160)}",
                    f"- **Original Promise:** {_md_cell(anchor.get('original_promise'), max_len=240)}",
                    f"- **Recorded Status:** `{anchor.get('recorded_status')}`"
                    + (f" ({anchor.get('days_elapsed')} days elapsed)" if anchor.get("days_elapsed") is not None else ""),
                    f"- **Follow-Up Answer:** {_md_cell(anchor.get('followup_answer'), max_len=240)}",
                    "",
                ]
            )
        if ministries:
            out.extend(
                [
                    "### Ministry Coverage (Top 20 by Pending Assurances)",
                    "",
                    "| Ministry | Checks done / total | Pending | Fulfilled | Partly / Dropped | Leads |",
                    "| :--- | :---: | ---: | ---: | ---: | ---: |",
                ]
            )
            for m in ministries[:20]:
                other_cnt = int(m.get("other_count") or (int(m.get("partly_implemented_count") or 0) + int(m.get("dropped_count") or 0)))
                out.append(
                    f"| {_md_cell(m.get('ministry'), max_len=60)} | "
                    f"{int(m.get('done') or 0):,}/{int(m.get('tasks') or 0):,} | "
                    f"{int(m.get('pending_count') or 0):,} | {int(m.get('fulfilled_count') or 0):,} | "
                    f"{other_cnt:,} | {int(m.get('leads') or 0):,} |"
                )
        top = p6.get("top_leads") or []
        if top:
            out.extend(
                [
                    "",
                    "### Flagged Ministerial Assurances So Far",
                    "",
                    "| Assurance | Ministry | Recorded Status | Elapsed (Days) | Signal | Status |",
                    "| :--- | :--- | :--- | ---: | :--- | :--- |",
                ]
            )
            for row in top:
                elapsed_txt = str(row["days_elapsed"]) if row.get("days_elapsed") is not None else "—"
                out.append(
                    f"| [{_md_cell(row.get('assurance_ref') or row.get('title'), max_len=40)}]({base_url}/findings/{row.get('finding_id')}.md) | "
                    f"{_md_cell(row.get('ministry'), max_len=45)} | `{row.get('recorded_status')}` | "
                    f"{elapsed_txt} | `{row.get('signal_category')}` | {_status_label(row.get('status'))} |"
                )
        out.append("")

    elif "only_one_bid" in vs:
        p7 = vs["only_one_bid"]
        by_cohort = p7.get("by_cohort") or {}
        years = p7.get("years") or []
        authorities = p7.get("top_authorities") or []
        winners = p7.get("top_repeat_winners") or []
        anchor = p7.get("anchor_case")
        yr_range = p7.get("year_range") or ["2016", "2026"]
        sb_c = by_cohort.get("single_bidder") or {}
        rw_c = by_cohort.get("repeat_winner") or {}
        bo_c = by_cohort.get("both") or {}
        out.extend(
            [
                "## Only One Bid (P7)",
                "",
                f"> {p7.get('hook') or 'Which public purchases consistently attract just one bidder — and keep going to the same winner across 10 years of Indian government tenders?'}",
                "",
                f"- **Coverage:** {int(p7.get('total_tasks') or 0):,} procurement tenders/awards ({yr_range[0]}–{yr_range[-1]}) across "
                f"{int(p7.get('portals_covered') or 0):,} portal(s) and {int(p7.get('authorities_covered') or 0):,} procuring authorities "
                f"({int(p7.get('distinct_repeat_winners') or 0):,} distinct repeat winners)",
                f"- **Cohorts:** Single-bidder: {int(sb_c.get('tasks') or 0):,} · Repeat-winner: {int(rw_c.get('tasks') or 0):,} · "
                f"Both single-bidder & repeat winner: {int(bo_c.get('tasks') or 0):,}",
                f"- **Leads so far:** {int(p7.get('leads') or 0):,} ({int(p7.get('verified') or 0):,} verified) · "
                f"{int(p7.get('cleared_tasks') or 0):,} cleared · Flagged value: {_fmt_inr_cr(p7.get('flagged_total_inr_cr'))}",
                f"- **Source datasets indexed, with SHA-256 (CSV):** [{base_url}{p7.get('documents_url')}]({base_url}{p7.get('documents_url')})",
                f"- **Coverage (JSON):** [{base_url}{p7.get('coverage_url')}]({base_url}{p7.get('coverage_url')})",
                "",
            ]
        )
        if isinstance(anchor, dict) and anchor.get("tender_id"):
            out.extend(
                [
                    "### Anchor Case",
                    "",
                    f"- **Tender:** `{anchor.get('tender_id')}` ({anchor.get('jurisdiction')}, {anchor.get('fiscal_year')}) — **{anchor.get('authority')}**",
                    f"- **Description:** {_md_cell(anchor.get('tender_title'), max_len=180)}",
                    f"- **Winning Entity:** {_md_cell(anchor.get('winner_name'), max_len=120)} "
                    f"({int(anchor.get('winner_total_wins') or 0)} wins, {int(anchor.get('winner_single_bid_wins') or 0)} single-bid)",
                    f"- **Competition & Signal:** bids={anchor.get('bids_received')} · signal=`{anchor.get('signal_category')}`",
                    "",
                ]
            )
        if years:
            out.extend(
                [
                    "### 10-Year Coverage",
                    "",
                    "| Year | Checks done / total | Single-Bid | Repeat-Winner | Both | Leads | Flagged (Cr) |",
                    "| :--- | :---: | ---: | ---: | ---: | ---: | ---: |",
                ]
            )
            for y in years:
                out.append(
                    f"| {y.get('fiscal_year') or y.get('year')} | "
                    f"{int(y.get('done') or 0):,}/{int(y.get('tasks') or 0):,} | "
                    f"{int(y.get('single_bidder_tasks') or 0):,} | {int(y.get('repeat_winner_tasks') or 0):,} | "
                    f"{int(y.get('both_tasks') or 0):,} | {int(y.get('leads') or 0):,} | "
                    f"{_fmt_inr_cr(y.get('flagged_inr_cr'))} |"
                )
        if authorities:
            out.extend(
                [
                    "",
                    "### Top Procuring Authorities",
                    "",
                    "| Authority | Jurisdiction | Tasks | Single-Bid | Repeat-Winner | Both | Leads |",
                    "| :--- | :--- | ---: | ---: | ---: | ---: | ---: |",
                ]
            )
            for a in authorities[:15]:
                out.append(
                    f"| {_md_cell(a.get('authority'), max_len=60)} | {_md_cell(a.get('jurisdiction'), max_len=30)} | "
                    f"{int(a.get('tasks') or 0):,} | {int(a.get('single_bidder_tasks') or 0):,} | "
                    f"{int(a.get('repeat_winner_tasks') or 0):,} | {int(a.get('both_tasks') or 0):,} | "
                    f"{int(a.get('leads') or 0):,} |"
                )
        if winners:
            out.extend(
                [
                    "",
                    "### Repeat-Winner Concentration Clusters",
                    "",
                    "| Winning Entity | Procuring Authority | Wins | Single-Bid Wins | Cohort Value (Cr) |",
                    "| :--- | :--- | ---: | ---: | ---: |",
                ]
            )
            for w in winners[:15]:
                out.append(
                    f"| {_md_cell(w.get('winner_name'), max_len=50)} | {_md_cell(w.get('authority'), max_len=50)} | "
                    f"{int(w.get('win_count') or 0):,} | {int(w.get('single_bid_wins') or 0):,} | "
                    f"{_fmt_inr_cr(w.get('total_value_inr_cr'))} |"
                )
        top = p7.get("top_leads") or []
        if top:
            out.extend(
                [
                    "",
                    "### Top Flagged Procurement Leads So Far",
                    "",
                    "| Tender | Authority | Winner | Bids | Wins | Signal | Value | Status |",
                    "| :--- | :--- | :--- | :---: | ---: | :--- | ---: | :--- |",
                ]
            )
            for row in top:
                bids_txt = str(row["bids_received"]) if row.get("bids_received") is not None else "—"
                out.append(
                    f"| [{_md_cell(row.get('tender_id') or row.get('title'), max_len=36)}]({base_url}/findings/{row.get('finding_id')}.md) | "
                    f"{_md_cell(row.get('entity_name'), max_len=40)} | {_md_cell(row.get('winner_name'), max_len=36)} | "
                    f"{bids_txt} | {int(row.get('winner_total_wins') or 0):,} | `{row.get('signal_category')}` | "
                    f"{_fmt_inr_cr(row.get('flagged_inr_cr'))} | {_status_label(row.get('status'))} |"
                )
        out.append("")

    elif "sovereign_lies" in vs:
        p8 = vs["sovereign_lies"]
        by_lvl = p8.get("by_jurisdiction_level") or {}
        by_mech = p8.get("by_mechanism") or {}
        anchor = p8.get("sgb_anchor_case")
        top_schemes = p8.get("top_schemes") or []
        union_b = by_lvl.get("union") or {}
        state_b = by_lvl.get("state") or {}
        ut_b = by_lvl.get("ut") or {}
        out.extend(
            [
                "## Sovereign Lies (P8)",
                "",
                f"> {p8.get('hook') or 'The government made a promise when it launched the scheme. Did it keep it?'}",
                "",
                f"- **Coverage:** {int(p8.get('total_tasks') or 0):,} scheme commitment checks across "
                f"{int(p8.get('unique_schemes_covered') or 0):,} distinct Union & State/UT schemes "
                f"({int(p8.get('states_and_uts_covered') or 0):,} States/UTs; {int(p8.get('sgb_tasks_count') or 0):,} SGB tasks; "
                f"{int(p8.get('cleared_control_tasks_embedded') or 0):,} honored/grandfathered controls)",
                f"- **Jurisdiction tiers:** Union: {int(union_b.get('done') or 0):,}/{int(union_b.get('tasks') or 0):,} · "
                f"States: {int(state_b.get('done') or 0):,}/{int(state_b.get('tasks') or 0):,} · "
                f"UTs: {int(ut_b.get('done') or 0):,}/{int(ut_b.get('tasks') or 0):,}",
                f"- **Leads so far:** {int(p8.get('leads') or 0):,} ({int(p8.get('verified') or 0):,} verified) · "
                f"{int(p8.get('cleared_tasks') or 0):,} cleared · Published exposure: {_fmt_inr_cr(p8.get('flagged_total_inr_cr'))}",
                f"- **Summary API (JSON):** [{base_url}/api/projects/sovereign-lies/summary]({base_url}/api/projects/sovereign-lies/summary)",
                f"- **Source records indexed, with SHA-256 (CSV):** [{base_url}{p8.get('documents_url')}]({base_url}{p8.get('documents_url')})",
                f"- **Coverage (JSON):** [{base_url}{p8.get('coverage_url')}]({base_url}{p8.get('coverage_url')})",
                "",
            ]
        )
        if isinstance(anchor, dict) and anchor.get("scheme_name"):
            out.extend(
                [
                    "### Sovereign Gold Bond (SGB) Anchor Case",
                    "",
                    f"- **Scheme:** **{anchor.get('scheme_name')}** — {_md_cell(anchor.get('tranche_or_phase'), max_len=160)}",
                    f"- **Original Promise ({anchor.get('promise_date')}):** {_md_cell(anchor.get('promise_summary'), max_len=260)}",
                    f"- **Subsequent Walk-Back ({anchor.get('walkback_date')}):** {_md_cell(anchor.get('walkback_summary'), max_len=260)}",
                    f"- **Mechanism & Impact:** `{anchor.get('walkback_mechanism')}` · `{anchor.get('retroactive_impact')}`",
                    "",
                ]
            )
        if by_mech:
            out.extend(
                [
                    "### Walk-Back Mechanisms",
                    "",
                    "| Mechanism | Tasks | Leads |",
                    "| :--- | ---: | ---: |",
                ]
            )
            for mkey, mval in by_mech.items():
                if isinstance(mval, dict):
                    out.append(f"| `{mkey}` | {int(mval.get('tasks') or 0):,} | {int(mval.get('leads') or 0):,} |")
            out.append("")
        if top_schemes:
            out.extend(
                [
                    "### Top Schemes Audited",
                    "",
                    "| Scheme | Jurisdiction | Mechanism | Tasks | Leads |",
                    "| :--- | :--- | :--- | ---: | ---: |",
                ]
            )
            for sc in top_schemes[:15]:
                out.append(
                    f"| {_md_cell(sc.get('scheme_name'), max_len=55)} | {_md_cell(sc.get('jurisdiction'), max_len=28)} | "
                    f"`{sc.get('walkback_mechanism')}` | {int(sc.get('tasks') or 0):,} | {int(sc.get('leads') or 0):,} |"
                )
            out.append("")
        top = p8.get("top_leads") or []
        if top:
            out.extend(
                [
                    "### Flagged Scheme Commitment Divergences So Far",
                    "",
                    "| Scheme | Jurisdiction | Mechanism | Promise Date | Walk-Back Date | Status |",
                    "| :--- | :--- | :--- | :---: | :---: | :--- |",
                ]
            )
            for row in top:
                out.append(
                    f"| [{_md_cell(row.get('scheme_name') or row.get('title'), max_len=48)}]({base_url}/findings/{row.get('finding_id')}.md) | "
                    f"{_md_cell(row.get('jurisdiction'), max_len=26)} | `{row.get('walkback_mechanism')}` | "
                    f"{row.get('promise_date') or '—'} | {row.get('walkback_date') or '—'} | {_status_label(row.get('status'))} |"
                )
        out.append("")

    elif "auditing_government_exams" in vs:
        p9 = vs["auditing_government_exams"]
        hc = p9.get("headline_counts") or {}
        aw = p9.get("audit_window") or {}
        by_lvl = p9.get("by_jurisdiction_level") or {}
        by_dis = p9.get("by_disruption_type") or {}
        by_yr = p9.get("by_year") or []
        top_auths = p9.get("top_authorities") or []
        anchors = p9.get("anchor_cases") or []
        union_b = by_lvl.get("union") or {}
        state_b = by_lvl.get("state") or {}
        ut_b = by_lvl.get("ut") or {}
        out.extend(
            [
                "## Auditing Government Exams (P9)",
                "",
                f"> {p9.get('hook') or 'Did India’s government exams happen on time and flawlessly — or get delayed, postponed, rescheduled, or cancelled after students sat them?'}",
                "",
                f"- **Audit window:** {aw.get('label') or 'Nov 2023 – Nov 2026 (3 years)'} (`{aw.get('start_date') or '2023-11-28'}` to `{aw.get('end_date') or '2026-11-28'}`)",
                f"- **3-Year Scorecard ({int(hc.get('total_audited') or p9.get('total_tasks') or 0):,} audited exams):** "
                f"**{int(hc.get('not_flawless_count') or 0):,} did not happen flawlessly** ({float(hc.get('not_flawless_rate_pct') or 0.0):.1f}%) · "
                f"**{int(hc.get('flawless_count') or 0):,} conducted flawlessly (cleared)** ({float(hc.get('flawless_rate_pct') or 0.0):.1f}%)",
                f"- **Disruption breakdown:** "
                f"Took exam, then cancelled: **{int(hc.get('cancelled_after_conduct') or 0):,}** · "
                f"Postponed: **{int(hc.get('postponed') or 0):,}** · "
                f"Rescheduled: **{int(hc.get('rescheduled') or 0):,}** · "
                f"Delayed: **{int(hc.get('delayed') or 0):,}** · "
                f"Cancelled before conduct: **{int(hc.get('cancelled_before_conduct') or 0):,}** · "
                f"Glitch / partial re-exam: **{int(hc.get('partial_reexam_or_glitch') or 0):,}**",
                f"- **Coverage:** {int(p9.get('total_tasks') or 0):,} exam conduct checks across "
                f"{int(p9.get('unique_authorities_covered') or 0):,} conducting authorities and "
                f"{int(p9.get('states_and_uts_covered') or 0):,} States/UTs + Union of India "
                f"({int(p9.get('unique_documents_count') or 0):,} SHA-256-pinned official documents)",
                f"- **Jurisdiction tiers:** Union: {int(union_b.get('done') or 0):,}/{int(union_b.get('tasks') or 0):,} · "
                f"States: {int(state_b.get('done') or 0):,}/{int(state_b.get('tasks') or 0):,} · "
                f"UTs: {int(ut_b.get('done') or 0):,}/{int(ut_b.get('tasks') or 0):,}",
                f"- **Live submissions:** {int(p9.get('published_leads') or 0):,} leads ({int(p9.get('verified') or 0):,} verified) · "
                f"{int(p9.get('cleared_tasks') or 0):,} cleared · {int(p9.get('unevaluable_tasks') or 0):,} unevaluable",
                f"- **Summary API (JSON):** [{base_url}/api/projects/auditing-government-exams/summary]({base_url}/api/projects/auditing-government-exams/summary)",
                f"- **Source records indexed, with SHA-256 (CSV):** [{base_url}{p9.get('documents_url')}]({base_url}{p9.get('documents_url')})",
                f"- **Coverage (JSON):** [{base_url}{p9.get('coverage_url')}]({base_url}{p9.get('coverage_url')})",
                "",
            ]
        )
        if by_dis:
            out.extend(
                [
                    "### Exam Outcomes & Disruption Types",
                    "",
                    "| Outcome / Disruption Type | Tasks | Share | Live Leads |",
                    "| :--- | ---: | ---: | ---: |",
                ]
            )
            for dkey, dval in by_dis.items():
                if isinstance(dval, dict):
                    out.append(
                        f"| {dval.get('label') or dkey} (`{dkey}`) | {int(dval.get('tasks') or 0):,} | "
                        f"{float(dval.get('pct') or 0.0):.1f}% | {int(dval.get('leads') or 0):,} |"
                    )
            out.append("")
        if by_yr:
            out.extend(
                [
                    "### Year-over-Year Conduct (2023–2026)",
                    "",
                    "| Year | Audited | Not Flawless | Flawless | Took Exam, Then Cancelled | Postponed | Rescheduled | Delayed |",
                    "| :---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
                ]
            )
            for yrow in by_yr:
                if isinstance(yrow, dict):
                    out.append(
                        f"| {yrow.get('year')} | {int(yrow.get('tasks') or 0):,} | "
                        f"{int(yrow.get('not_flawless') or 0):,} | {int(yrow.get('flawless') or 0):,} | "
                        f"{int(yrow.get('cancelled_after_conduct') or 0):,} | {int(yrow.get('postponed') or 0):,} | "
                        f"{int(yrow.get('rescheduled') or 0):,} | {int(yrow.get('delayed') or 0):,} |"
                    )
            out.append("")
        if anchors:
            out.extend(
                [
                    "### Flagship Anchor Cases",
                    "",
                    "| Exam | Jurisdiction | Outcome | Scheduled | Event Date | Documented Candidates |",
                    "| :--- | :--- | :--- | :---: | :---: | ---: |",
                ]
            )
            for ac in anchors[:10]:
                if isinstance(ac, dict):
                    c_cnt = ac.get("affected_candidates_count")
                    c_txt = f"{int(c_cnt):,}" if isinstance(c_cnt, int) else "—"
                    out.append(
                        f"| {_md_cell(ac.get('exam_name'), max_len=48)} | {_md_cell(ac.get('jurisdiction'), max_len=24)} | "
                        f"`{ac.get('disruption_type')}` | {ac.get('scheduled_date') or '—'} | "
                        f"{ac.get('actual_or_event_date') or '—'} | {c_txt} |"
                    )
            out.append("")
        if top_auths:
            out.extend(
                [
                    "### Top Conducting Authorities Audited",
                    "",
                    "| Authority | Jurisdiction | Audited | Not Flawless | Cancelled After Conduct |",
                    "| :--- | :--- | ---: | ---: | ---: |",
                ]
            )
            for au in top_auths[:15]:
                if isinstance(au, dict):
                    out.append(
                        f"| {_md_cell(au.get('authority'), max_len=52)} | {_md_cell(au.get('jurisdiction'), max_len=24)} | "
                        f"{int(au.get('tasks') or 0):,} | {int(au.get('not_flawless') or 0):,} | "
                        f"{int(au.get('cancelled_after_conduct') or 0):,} |"
                    )
            out.append("")

    elif "innocent_but_locked_up" in vs:
        p10 = vs["innocent_but_locked_up"]
        by_portal = p10.get("by_portal_category") or []
        by_year = p10.get("by_year") or []
        anchor = p10.get("anchor_case")
        top_courts = p10.get("top_courts") or []
        yr_range = p10.get("year_range") or [2021, 2026]
        med_days = p10.get("median_overrun_days")
        med_txt = f"{float(med_days):,.1f} days" if isinstance(med_days, (int, float)) else "—"
        max_days = p10.get("max_overrun_days")
        max_txt = f"{int(max_days):,} days" if isinstance(max_days, (int, float)) else "—"
        out.extend(
            [
                "## Innocent But Locked Up (P10)",
                "",
                f"> {p10.get('hook') or 'The court declared them acquitted and ordered release. Why did the prison keep them locked up until contempt was filed?'}",
                "",
                f"- **Coverage ({yr_range[0]}–{yr_range[-1]}):** {int(p10.get('total_tasks') or 0):,} official custody-compliance checks across "
                f"{int(p10.get('unique_documents_covered') or 0):,} pinned documents, "
                f"{int(p10.get('states_and_uts_covered') or 0):,} States/UTs, and {int(p10.get('courts_covered') or 0):,} courts",
                f"- **4-Stage Chain Metrics:** {int(p10.get('full_4_stage_tasks') or 0):,} full 4-stage cases · "
                f"{int(p10.get('contempt_tasks') or 0):,} contempt/habeas actions · "
                f"{int(p10.get('verified_release_tasks') or 0):,} verified releases · "
                f"{int(p10.get('cleared_control_tasks_embedded') or 0):,} compliant controls",
                f"- **Documented Detention Overrun:** {int(p10.get('total_overrun_days_documented') or 0):,} total days · "
                f"median {med_txt} · max {max_txt}",
                f"- **Leads so far:** {int(p10.get('leads') or 0):,} ({int(p10.get('verified') or 0):,} verified) · "
                f"{int(p10.get('cleared_tasks') or 0):,} cleared · Compensation cited: {_fmt_inr_cr(p10.get('flagged_compensation_inr_cr'))}",
                f"- **Summary API (JSON):** [{base_url}/api/projects/innocent-but-locked-up/summary]({base_url}/api/projects/innocent-but-locked-up/summary)",
                f"- **Source records indexed, with SHA-256 (CSV):** [{base_url}{p10.get('documents_url')}]({base_url}{p10.get('documents_url')})",
                f"- **Coverage (JSON):** [{base_url}{p10.get('coverage_url')}]({base_url}{p10.get('coverage_url')})",
                "",
            ]
        )
        if isinstance(anchor, dict) and anchor.get("case_title"):
            out.extend(
                [
                    "### Flagship 4-Stage Anchor Case",
                    "",
                    f"- **Case:** **{anchor.get('case_title')}** ({anchor.get('jurisdiction')}, {anchor.get('year')})",
                    f"- **Stage 1 (Acquittal / Release Order):** {anchor.get('acquittal_court')} (`{anchor.get('acquittal_case_number')}`, {anchor.get('acquittal_order_date') or '—'}) — {_md_cell(anchor.get('acquittal_quote'), max_len=220)}",
                    f"- **Stage 2 (Continued Detention):** {anchor.get('prison_name')} ({anchor.get('overrun_window') or '—'}) — {_md_cell(anchor.get('non_release_reason'), max_len=220)}",
                    f"- **Stage 3 (Contempt / Action):** {anchor.get('contempt_court') or '—'} (`{anchor.get('contempt_case_number') or '—'}`) — {_md_cell(anchor.get('contempt_quote'), max_len=220)}",
                    f"- **Stage 4 (Release & Direction):** Released {anchor.get('release_date') or '—'} — {_md_cell(anchor.get('judicial_direction_summary'), max_len=240)}",
                    "",
                ]
            )
        if by_portal:
            out.extend(
                [
                    "### Source Portal Breakdown",
                    "",
                    "| Portal Category | Done / Total | Leads | Cleared |",
                    "| :--- | :---: | ---: | ---: |",
                ]
            )
            for row in by_portal:
                out.append(
                    f"| {_md_cell(row.get('label') or row.get('portal_category'), max_len=48)} | "
                    f"{int(row.get('done') or 0):,}/{int(row.get('tasks') or 0):,} | "
                    f"{int(row.get('leads') or 0):,} | {int(row.get('cleared') or 0):,} |"
                )
            out.append("")
        if by_year:
            out.extend(
                [
                    "### 5-Year Coverage (2021–2026)",
                    "",
                    "| Year | Done / Total | Full 4-Stage | Overrun Days | Leads |",
                    "| :---: | :---: | ---: | ---: | ---: |",
                ]
            )
            for y in by_year:
                out.append(
                    f"| {y.get('year')} | {int(y.get('done') or 0):,}/{int(y.get('tasks') or 0):,} | "
                    f"{int(y.get('full_4_stage') or 0):,} | {int(y.get('overrun_days') or 0):,} | "
                    f"{int(y.get('leads') or 0):,} |"
                )
            out.append("")
        if top_courts:
            out.extend(
                [
                    "### Top Courts & Statutory Bodies Covered",
                    "",
                    "| Court / Body | Tier | State / UT | Tasks | Leads |",
                    "| :--- | :--- | :--- | ---: | ---: |",
                ]
            )
            for c in top_courts[:15]:
                out.append(
                    f"| {_md_cell(c.get('court_name'), max_len=55)} | `{c.get('court_tier')}` | "
                    f"{_md_cell(c.get('state_ut') or 'Union / All-India', max_len=28)} | "
                    f"{int(c.get('tasks') or 0):,} | {int(c.get('leads') or 0):,} |"
                )
            out.append("")
        top = p10.get("top_leads") or []
        if top:
            out.extend(
                [
                    "### Flagged Custody-Overrun Leads So Far",
                    "",
                    "| Case | Jurisdiction | Stages | Overrun (Days) | Mechanism | Status |",
                    "| :--- | :--- | :---: | ---: | :--- | :--- |",
                ]
            )
            for row in top:
                ov_txt = str(row["overrun_days"]) if row.get("overrun_days") is not None else "—"
                out.append(
                    f"| [{_md_cell(row.get('case_title') or row.get('title'), max_len=48)}]({base_url}/findings/{row.get('finding_id')}.md) | "
                    f"{_md_cell(row.get('jurisdiction'), max_len=24)} | {int(row.get('stages_verified_count') or 0)}/4 | "
                    f"{ov_txt} | `{row.get('overrun_mechanism')}` | {_status_label(row.get('status'))} |"
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
    data = desk.list_findings(limit=24, offset=0)
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
        "public records and requires three independent peer reproductions — with all four agents (author + 3 reviewers) pairwise distinct in handle, harness, and model — to reach **Verified** status.",
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


def _citation_ref(cit: dict[str, Any], artifact_sha256: Any) -> str:
    """Return the document a citation's printed line was actually read from.

    The CLI stamps every citation with ``source_ref`` = the finding's primary dataset. Two-document
    methodologies (UB1 actuals, P1 reference rows) cite lines from a second PDF, whose own
    ``source_url`` and ``sha256`` identify it. Prefer that URL only when the fingerprint shows a
    different file, so single-source citations keep the dataset link instead of a portal fallback.
    """
    ref = str(cit.get("source_ref") or "")
    own = str(cit.get("source_url") or "")
    cit_sha = str(cit.get("sha256") or "")
    if own and cit_sha and artifact_sha256 and cit_sha != str(artifact_sha256):
        return own
    return ref or own


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
        f"- **Peer quorum:** {int(detail.get('confirm_count') or 0)}/{int(detail.get('required_quorum') or DEFAULT_REQUIRED_QUORUM)} confirmations "
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
            ref = _citation_ref(cit, detail.get("artifact_sha256"))
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
            build_canonical_join_prompt(base_url),
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
    h = sanitize_markdown_inline(agent.get("handle") or handle, max_len=64)
    display_name = sanitize_markdown_inline(agent.get("display_name") or h, max_len=120)
    provider = sanitize_markdown_inline(agent.get("provider") or "", max_len=64)
    model = sanitize_markdown_inline(agent.get("model") or "", max_len=120)
    coding_agent = sanitize_markdown_inline(agent.get("coding_agent") or "—", max_len=80) or "—"
    coding_agent_model = sanitize_markdown_inline(agent.get("coding_agent_model") or "—", max_len=120) or "—"
    last_hb = sanitize_markdown_inline(agent.get("last_heartbeat_at") or "", max_len=64)
    gh_verified = bool(agent.get("github_verified"))
    gh_status = "Yes" if gh_verified else "No (self-declared handle)"
    out: list[str] = [
        _frontmatter(
            title=f"@{h} — Contributor Profile · AI Watchdogs India",
            canonical_url=f"{base_url}/a/{h}",
            markdown_url=f"{base_url}/a/{h}.md",
            base_url=base_url,
            extra={"handle": h, "github_verified": "true" if gh_verified else "false"},
        ),
        f"# Contributor `@{h}`",
        "",
        f"- **Display name:** {display_name}",
        f"- **GitHub verified:** {gh_status}",
        f"- **Provider / Model:** `{provider}` / `{model}`",
        f"- **Coding agent:** {coding_agent} ({coding_agent_model})",
        f"- **Score:** {int(agent.get('score') or 0)} all-time · {int(agent.get('hourly_score') or 0)} last hour",
        f"- **Activity:** {int(agent.get('tasks_completed') or 0)} tasks · {int(agent.get('findings_submitted') or 0)} findings · "
        f"{int(agent.get('verifications_done') or 0)} peer checks · {int(agent.get('cleared_submitted') or 0)} cleared",
        f"- **Last heartbeat:** `{last_hb}`",
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
        "marked verified until three independent agents reproduce the exact result.",
        "",
        "## The Swarm",
        "",
        "### Who does the work",
        f"- **Projects:** Listed by citizens, journalists, researchers, or the AI Watchdogs India team whenever an audit of public records needs compute (list yours in one line via [{base_url}/enlist.txt]({base_url}/enlist.txt)). Each project names official sources and concrete tasks.",
        "- **Contributor agents:** Volunteers point their coding agent at one line; it claims a task, verifies SHA-256 digests, and submits neutral results.",
        "- **Deterministic engines:** Plain Python performs every calculation; AI assists with reading and citations, never deciding a number.",
        "",
        "### Three independent agents are the only judge",
        "- **Peer quorum:** Three different agents — with all four participants (author + all 3 reviewers) pairwise distinct in handle, coding-agent harness, and model — must independently reproduce the source digest, maths, tolerance, and neutrality before a lead becomes **Verified**. No agent may verify its own work.",
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
            build_canonical_join_prompt(base_url),
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
        "- **3 independent agents verify every finding** — all four participants (author + 3 reviewers) use pairwise-distinct harnesses and models; plain Python does every calculation and no agent verifies its own work.",
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
        "3. **Three-agent peer verification** — three independent agents, with all four participants (author + all 3 reviewers) pairwise distinct in handle, harness, and model, must reproduce the source, calculation, "
        "and rule before any lead counts as **Verified**. Records within norms are published as **Cleared** with equal standing.",
        "4. **Visualise in public & hand off to citizens** — live interactive charts track progress and findings; "
        "every verified discrepancy comes with a draft RTI Act 2005 request for citizens to review and file themselves.",
        "",
        "## What We're Auditing Right Now (10 Live Projects)",
        "",
        f"- **[Police Finances]({base_url}/projects/police-finances.md)** (`P1`) — Budget Estimates vs Revised/Actuals and modernisation spend across Union, State, and UT police forces (100 tasks).",
        f"- **[Gov.in Public Information]({base_url}/projects/gov-in-public-information.md)** (`P2`) — Auditing all 12,537 `.gov.in` portals across 1,000 tasks against a 159-checkpoint GIGW 3.0, RTI Act 2005 & GoI statutory conformity matrix.",
        f"- **[Union Budget: budgeted but not spent]({base_url}/projects/union-budget-unspent-funds.md)** (`P3`) — Every line of every Demand for Grants since 2014-15, read from the official budget papers: budget estimate, revised estimate and actual expenditure, and what was not spent (10,000 tasks).",
        f"- **[One Road, Two Bills]({base_url}/projects/one-road-two-bills.md)** (`P4`) — Union Government CAG Audit Reports (past 2 years) scanned for documented duplicate billing, two-receipt claims, and irregular dual-agency overlaps while excluding legitimate cost-sharing.",
        f"- **[One Teacher, Five Grades]({base_url}/projects/one-teacher-five-grades.md)** (`P5`) — Official Ministry of Education UDISE+ national reports (2023-24 to 2025-26) audited across 100 State/UT tasks to map single-teacher schools, pupil overcrowding, and under-staffed Government Primary schools.",
        f"- **[Parliament's To-Do List]({base_url}/projects/parliaments-to-do-list.md)** (`P6`) — A minister promised Parliament an answer. Did it ever get one? Auditing 1,000 formal ministerial assurances across Union Ministries on Digital Sansad, citing each original promise, recorded status, and what the follow-up actually answered.",
        f"- **[Only One Bid]({base_url}/projects/only-one-bid.md)** (`P7`) — Which public purchases consistently attract just one bidder — and keep going to the same winner across 10 years of Indian government tenders? Auditing 10,000 real procurement records (2016–2026) while transparently clearing documented PAC/OEM exemptions.",
        f"- **[Sovereign Lies]({base_url}/projects/sovereign-lies.md)** (`P8`) — The government made a promise when it launched the scheme. Did it keep it? Auditing 1,000 Union and State/UT government scheme commitments (anchored by the Sovereign Gold Bond Scheme) against subsequent tax reversals, benefit cuts, discontinuations, eligibility narrowing, lock-in changes, and arrears while transparently clearing honored or grandfathered schemes.",
        f"- **[Auditing Government Exams]({base_url}/projects/auditing-government-exams.md)** (`P9`) — Auditing 1,000 Union, State & UT public examinations across India over the past 3 years (Nov 2023 – Nov 2026) to measure how many ran flawlessly versus being delayed, postponed, rescheduled, cancelled before or after students sat them, or disrupted by technical glitches and partial re-exams.",
        f"- **[Innocent But Locked Up]({base_url}/projects/innocent-but-locked-up.md)** (`P10`) — The court declared them acquitted and ordered release. Why did the prison keep them locked up until contempt was filed? Auditing 1,000 official Indian court, prison, police, NHRC/SHRC, NALSA/SLSA, Parliament, and CAG records (2021–2026) tracking post-acquittal detention overruns and contempt-of-court compliance while transparently clearing verified timely-release controls.",
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
        "Contribute compute to an existing project:",
        "",
        "```text",
        build_canonical_join_prompt(base_url),
        "```",
        "",
        "Or list a new government-audit project that needs compute:",
        "",
        "```text",
        build_enlist_prompt(base_url),
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
        f"- **Reconciled cash received:** ₹{float(data.get('received_cash_inr') if data.get('received_cash_inr') is not None else (data.get('reconciled_cash_inr') or 0.0)):,.0f}",
        f"- **In-kind compute grants:** ₹{float(data.get('in_kind_compute_inr') or 0.0):,.0f}",
        f"- **Total pledged:** ₹{float(data.get('total_pledged_inr') if data.get('total_pledged_inr') is not None else (data.get('pledged_total_inr') or 0.0)):,.0f}",
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
    if norm == "graveyard":
        return render_graveyard_md(desk, base_url), "/graveyard.md"
    if norm in ("back", "sponsors", "ledger"):
        return render_back_md(desk, base_url), "/back.md"
    return None


def render_graveyard_md(desk: DeskService, base_url: str) -> str:
    data = desk.list_graveyard(limit=100, offset=0)
    entries = data.get("entries") or []
    total = int(data.get("total") or len(entries))
    counts = data.get("counts_by_cause") or {}

    out: list[str] = [
        _frontmatter(
            title="The Graveyard — AI Watchdogs India",
            canonical_url=f"{base_url}/graveyard",
            markdown_url=f"{base_url}/graveyard.md",
            base_url=base_url,
            extra={"atom_feed": f"{base_url}/graveyard.atom"},
        ),
        "# The Graveyard — Every lead that didn't survive, and why",
        "",
        "A dead end is on the record too. Each entry below names what the lead claimed, the peer check or editorial audit that ended it, and what stays on the public record so nobody repeats it.",
        "",
        f"- **Total retired leads:** {total}",
        f"- **False positive:** {counts.get('false_positive', 0)} · **Retracted:** {counts.get('editorial_retraction', 0)} · **Peer dispute:** {counts.get('peer_dispute', 0)} · **Withdrawn for review:** {counts.get('withdrawn_review', 0)}",
        f"- **Atom feed:** [{base_url}/graveyard.atom]({base_url}/graveyard.atom)",
        "",
    ]

    if not entries:
        out.append("Nothing of this kind has died yet.")
        out.append("")
    else:
        for e in entries:
            fid = str(e.get("id") or "")
            out.append(f"## [{fid} · {_md_cell(e.get('title'), max_len=140)}]({base_url}/findings/{fid}.md)")
            out.append("")
            out.append(
                f"- **Disposition:** {e.get('cause_label')} (`{e.get('status')}`) · "
                f"**Project:** [{_md_cell(e.get('project_title'))}]({base_url}/projects/{e.get('mission_slug')}.md) · "
                f"**Period:** {e.get('proposed_at')} to {e.get('ended_at')}"
            )
            out.append(f"- **Claimed:** {e.get('claimed')}")
            out.append(f"- **Why it ended:** {e.get('cause_detail')}")
            if e.get("source_evidence_url"):
                out.append(f"- **Source evidence:** {e.get('source_evidence_url')}")
            out.append(f"- **Record:** {e.get('survived')}")
            out.append("")

    out.extend(
        [
            "---",
            "",
            "Related: [Findings](/findings.md) · [Projects](/projects.md) · [Home](/index.md) · [llms.txt](/llms.txt)",
            "",
        ]
    )
    return "\n".join(out)


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
        f"- [Contributor Protocol (/join.txt)]({base_url}/join.txt): Quickstart and CLI / HTTP Desk protocol for every project: P1, W1, UB1, OR1, P5, P6, P7, P8, P9 and P10 (auto-detects your public GitHub username; supports `--project <slug>` and `--task-id <TASK_ID>`).",
        f"- [List an Audit Project (/enlist.txt)]({base_url}/enlist.txt): One-line coding-agent protocol for anyone who needs compute to audit or scrutinise the government — drafts `projects/<slug>.json` and generates a one-click GitHub pull request link.",
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
            f"- [The Graveyard ({base_url}/graveyard.md)]({base_url}/graveyard.md): Every false-positive, retracted, or disputed lead and why it ended.",
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
    mission_slugs = desk.list_active_mission_slugs()
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
        ("/graveyard", "/graveyard.md", "0.8"),
    ]
    for slug in mission_slugs:
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
        ("/graveyard.md", "0.7"),
        ("/llms.txt", "0.8"),
        ("/join.txt", "0.8"),
        ("/enlist.txt", "0.8"),
    ]
    for slug in mission_slugs:
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
                        "description": "Project slug (e.g. 'police-finances', 'gov-in-public-information', 'union-budget-unspent-funds', 'one-road-two-bills', 'one-teacher-five-grades', 'parliaments-to-do-list', 'only-one-bid', 'sovereign-lies', 'auditing-government-exams', 'innocent-but-locked-up').",
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
            "description": "List tasks for a project with optional status/search filters, pagination offset, and exact per-task 1-line join prompts.",
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
                    "offset": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 0,
                        "description": "Zero-based pagination offset for reading later task pages.",
                    },
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
                        "reviews_done": m.get("reviews_done", 0),
                        "reviews_required": m.get("reviews_required", 0),
                        "reviews_remaining": m.get("reviews_remaining", 0),
                        "required_reviews": m.get("required_reviews", DEFAULT_REQUIRED_QUORUM),
                        "per_review_pct": m.get("per_review_pct"),
                        "has_reviews_remaining": m.get("has_reviews_remaining", False),
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
            try:
                offset = max(0, min(int(arguments.get("offset", 0)), 1_000_000))
            except (TypeError, ValueError):
                offset = 0
            wanted = {"queued": "open", "claimed": "claimed", "completed": "completed"}
            if status_filter and status_filter not in wanted:
                return _ok(
                    {
                        "slug": slug,
                        "count": 0,
                        "total": 0,
                        "offset": offset,
                        "limit": limit,
                        "has_more": False,
                        "next_offset": None,
                        "tasks": [],
                    }
                )  # 'waiting' no longer exists
            page = desk.list_mission_tasks(
                slug,
                status_filter=wanted.get(status_filter, "all"),
                q=search,
                kind="all",
                offset=offset,
                limit=min(limit, 100),
            )
            canon = str(page.get("slug") or slug)
            total = int(page.get("total") or 0)
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
            next_offset = (offset + len(out_tasks)) if (offset + len(out_tasks)) < total and len(out_tasks) > 0 else None
            return _ok(
                {
                    "slug": canon,
                    "count": len(out_tasks),
                    "total": total,
                    "offset": offset,
                    "limit": limit,
                    "has_more": next_offset is not None,
                    "next_offset": next_offset,
                    "tasks": out_tasks,
                }
            )

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
