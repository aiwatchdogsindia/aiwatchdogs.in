"""Tests for AI agent accessibility: .md routes, Accept negotiation, discovery endpoints, and Streamable HTTP MCP."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aiwatchdogs.hub.agent_access import prefers_markdown
from aiwatchdogs.hub.app import create_app

PROJECTS_DIR = Path(__file__).resolve().parents[1] / "projects"


def _register_and_submit_p1_finding(client: TestClient) -> tuple[str, str]:
    reg = client.post(
        "/api/agents/register",
        json={
            "handle": "md-auditor-01",
            "display_name": "Markdown Auditor",
            "provider": "rule-engine",
            "model": "deterministic-python-v1",
            "coding_agent": "Claude Code",
            "coding_agent_model": "claude-sonnet",
            "mode": "auto",
        },
    ).json()
    headers = {"X-Agent-Token": reg["api_token"]}
    lease = client.post(
        "/api/desk/next",
        headers=headers,
        json={"task_id": "prj-metro-police-delhi-notes-2025-26"},
    ).json()
    sha = lease["task"]["payload"]["expected_sha256"]
    sub = client.post(
        "/api/desk/submit",
        headers=headers,
        json={
            "lease_id": lease["lease_id"],
            "outcome": "finding",
            "finding": {
                "title": "Delhi Police: revised estimate vs budget estimate is 16.5% below",
                "summary": (
                    "Official Union Demand 51 figures show a 16.5% divergence between revised and budget "
                    "estimates for public review, not a finding of wrongdoing."
                ),
                "methodology_code": "P1",
                "entity_name": "Delhi Police",
                "jurisdiction": "Union of India (Delhi Police)",
                "fiscal_year": "FY2024-25",
                "monetary_exposure_inr": 180_000_000.0,
                "severity": "medium",
                "anomaly_metrics": {"pct_difference": -16.5, "re": 835.0, "be": 1000.0},
                "rule_triggered": "P1-RE-VS-BE>10%",
                "source_url": "https://www.indiabudget.gov.in/budget2025-26/",
                "artifact_sha256": sha,
                "citations": [{"source_ref": "https://www.indiabudget.gov.in/budget2025-26/doc/eb/sbe51.pdf", "page": 1}],
                "benign_explanations": ["Capital works may be re-phased across years for tendering timelines."],
                "suggested_authority": "Ministry of Home Affairs, Government of India",
            },
        },
    )
    assert sub.status_code == 200, sub.text
    return reg["handle"], sub.json()["finding_id"]


# ---------------------------------------------------------------------------
# 1. Accept Header Negotiation Logic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, False),
        ("", False),
        ("*/*", False),
        ("text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", False),
        ("text/markdown", True),
        ("text/x-markdown", True),
        ("text/markdown;q=0.9, text/html;q=0.8", True),
        ("text/html;q=1.0, text/markdown;q=0.5", False),
        ("text/markdown;q=0", False),
        ("text/markdown, text/html", True),
        ("text/html, text/markdown", False),
    ],
)
def test_prefers_markdown_q_values(header: str | None, expected: bool) -> None:
    assert prefers_markdown(header) is expected


# ---------------------------------------------------------------------------
# 2. Explicit .md Routes + Accept Negotiation + Link Headers
# ---------------------------------------------------------------------------


@pytest.mark.production_missions
def test_markdown_routes_content_negotiation_and_link_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(PROJECTS_DIR))
    monkeypatch.delenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", raising=False)

    app = create_app(db_path=tmp_path / "agent_md.db", seed=True)
    with TestClient(app) as client:
        handle, finding_id = _register_and_submit_p1_finding(client)

        md_routes = [
            ("/index.md", "AI Watchdogs India"),
            ("/projects.md", "police-finances"),
            ("/projects/police-finances.md", "Union & Central Forces"),
            ("/projects/gov-in-public-information.md", "Website Information Audit Summary"),
            ("/projects/union-budget-unspent-funds.md", "## Union Budget Lines (UB1)"),
            ("/findings.md", finding_id),
            (f"/findings/{finding_id}.md", "Delhi Police"),
            ("/live.md", "Live Swarm Telemetry"),
            ("/leaderboard.md", handle),
            ("/contributors.md", handle),
            (f"/a/{handle}.md", f"@{handle}"),
            ("/how-it-works.md", "BNS Sec 356"),
            ("/brief.md", "Project Brief"),
            ("/changelog.md", "Public Changelog"),
            ("/back.md", "Sponsors & Compute Grants"),
        ]

        for path, expected_snippet in md_routes:
            resp = client.get(path)
            assert resp.status_code == 200, f"{path}: {resp.status_code} {resp.text[:200]}"
            assert resp.headers["content-type"].startswith("text/markdown"), path
            assert "Accept" in resp.headers.get("vary", ""), path
            assert int(resp.headers["x-markdown-tokens"]) > 0, path
            link_hdr = resp.headers.get("link", "")
            assert '</llms.txt>; rel="describedby"' in link_hdr, path
            assert '</.well-known/api-catalog>; rel="api-catalog"' in link_hdr, path
            assert '</mcp>; rel="mcp"' in link_hdr, path
            assert resp.text.startswith("---\n"), path
            assert expected_snippet in resp.text, f"Missing {expected_snippet!r} in {path}"

        # Unknown project / finding .md returns clean 404
        assert client.get("/projects/does-not-exist.md").status_code == 404
        assert client.get("/findings/fnd-nonexistent.md").status_code == 404

        # /changelog.md shows PR numbers and IST date + time
        changelog_md = client.get("/changelog.md").text
        assert "PR #31" in changelog_md
        assert "PR #30" in changelog_md
        assert "IST" in changelog_md

        # /projects/gov-in-public-information.md shows 159-checkpoint conformity matrix + 42 W1 checks
        gov_md = client.get("/projects/gov-in-public-information.md").text
        assert "159 checkpoints" in gov_md
        assert "Conformity Matrix Sections" in gov_md
        assert "/api/missions/gov-in-public-information/matrix" in gov_md

        # /projects/union-budget-unspent-funds.md shows the UB1 lines and the year-by-year coverage table
        ub_md = client.get("/projects/union-budget-unspent-funds.md").text
        assert "## Union Budget Lines (UB1)" in ub_md
        assert "### Financial Year Coverage" in ub_md
        assert "2014-15" in ub_md and "2025-26" in ub_md

        # /brief.md reflects full explainer, dual human/agent access, Discord + Telegram, and no Treasury Ledger
        brief_md = client.get("/brief.md").text
        assert "police-finances.md" in brief_md
        assert "gov-in-public-information.md" in brief_md
        assert "union-budget-unspent-funds.md" in brief_md
        assert "Union Budget: budgeted but not spent" in brief_md
        assert "independent-india-inflation" not in brief_md
        assert "WebMCP" in brief_md
        assert "https://t.me/aiwatchdogsin" in brief_md
        assert "https://discord.gg/8K33UYGWY4" in brief_md
        assert "treasury ledger" not in brief_md.lower()

        # Accept: text/markdown negotiation on canonical HTML routes
        negotiated_routes = [
            ("/", "AI Watchdogs India"),
            ("/projects", "Audit Projects"),
            ("/projects/police-finances", "police-finances"),
            ("/findings", "Public Findings Ledger"),
            (f"/findings/{finding_id}", "Delhi Police"),
            ("/live", "Live Swarm Telemetry"),
            ("/leaderboard", handle),
            ("/how-it-works", "How It Works"),
            ("/brief", "Project Brief"),
            ("/changelog", "Public Changelog"),
            ("/back", "Sponsors & Compute Grants"),
        ]
        for route, snippet in negotiated_routes:
            r_md = client.get(route, headers={"Accept": "text/markdown, text/html;q=0.5"})
            assert r_md.status_code == 200, route
            assert r_md.headers["content-type"].startswith("text/markdown"), route
            assert "Accept" in r_md.headers.get("vary", ""), route
            assert int(r_md.headers["x-markdown-tokens"]) > 0, route
            assert snippet in r_md.text, route

            # Standard browser Accept MUST still get HTML + Link discovery headers + Vary: Accept
            r_html = client.get(
                route,
                headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
            )
            assert r_html.status_code == 200, route
            assert r_html.headers["content-type"].startswith("text/html"), route
            assert "Accept" in r_html.headers.get("vary", ""), route
            html_link = r_html.headers.get("link", "")
            assert '</llms.txt>; rel="describedby"' in html_link, route
            assert 'rel="alternate"; type="text/markdown"' in html_link, route
            assert '</.well-known/api-catalog>; rel="api-catalog"' in html_link, route
            assert '</mcp>; rel="mcp"' in html_link, route

        # API read endpoints carry RFC 8288 discovery Link headers
        api_resp = client.get("/api/missions")
        assert api_resp.status_code == 200
        assert '</.well-known/api-catalog>; rel="api-catalog"' in api_resp.headers.get("link", "")


# ---------------------------------------------------------------------------
# 3. Discovery Endpoints: /llms.txt, /llms-full.txt, /robots.txt, /sitemap.xml, /.well-known/*
# ---------------------------------------------------------------------------


@pytest.mark.production_missions
def test_agent_discovery_and_crawlability_endpoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(PROJECTS_DIR))
    monkeypatch.delenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", raising=False)

    app = create_app(db_path=tmp_path / "discovery.db", seed=True)
    with TestClient(app) as client:
        # /llms.txt
        llms = client.get("/llms.txt")
        assert llms.status_code == 200
        assert llms.headers["content-type"].startswith("text/plain")
        assert "# AI Watchdogs India" in llms.text
        assert "/index.md" in llms.text
        assert "/projects/police-finances.md" in llms.text
        assert "/projects/gov-in-public-information.md" in llms.text
        assert "/projects/union-budget-unspent-funds.md" in llms.text
        assert "/projects/parliaments-to-do-list.md" in llms.text
        assert "independent-inflation-join.txt" not in llms.text
        assert "/join.txt" in llms.text
        quickstart_line = next(
            (line for line in llms.text.splitlines() if "Contributor Protocol (/join.txt)" in line),
            "",
        )
        assert "P1, W1, UB1, OR1, P5, P6, P7, P8, P9 and P10" in quickstart_line
        assert "/mcp" in llms.text
        assert "WebMCP" in llms.text

        # /llms-full.txt redirects to lightweight /llms.txt index to prevent crawler DB egress amplification
        llms_full = client.get("/llms-full.txt", follow_redirects=False)
        assert llms_full.status_code == 308
        assert llms_full.headers["location"] == "/llms.txt"
        assert "s-maxage=86400" in llms_full.headers.get("cache-control", "")

        # /robots.txt
        robots = client.get("/robots.txt")
        assert robots.status_code == 200
        assert "Content-Signal: ai-train=yes, search=yes, ai-input=yes" in robots.text
        assert "Allow: /" in robots.text
        assert "Sitemap:" in robots.text
        assert "/sitemap.xml" in robots.text

        # /sitemap.xml
        sm = client.get("/sitemap.xml")
        assert sm.status_code == 200
        assert "xml" in sm.headers["content-type"]
        root = ET.fromstring(sm.text)
        locs = [el.text or "" for el in root.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
        assert any(loc.endswith("/projects/police-finances") for loc in locs)
        assert any(loc.endswith("/projects/police-finances.md") for loc in locs)
        assert any(loc.endswith("/llms.txt") for loc in locs)
        assert any(loc.endswith("/projects/union-budget-unspent-funds") for loc in locs)
        assert not any("independent-inflation-join.txt" in loc for loc in locs)

        # /.well-known/api-catalog (RFC 9727)
        cat = client.get("/.well-known/api-catalog")
        assert cat.status_code == 200
        assert "application/linkset+json" in cat.headers["content-type"]
        cat_json = cat.json()
        assert isinstance(cat_json.get("linkset"), list) and len(cat_json["linkset"]) >= 2
        api_entry = cat_json["linkset"][0]
        assert any(item["href"].endswith("/openapi.json") for item in api_entry["service-desc"])
        assert any(item["href"].endswith("/llms.txt") for item in api_entry["service-doc"])
        assert any(entry["anchor"].endswith("/mcp") for entry in cat_json["linkset"])

        # /.well-known/mcp.json + GET /mcp
        mcp_well = client.get("/.well-known/mcp.json")
        assert mcp_well.status_code == 200
        mcp_card = mcp_well.json()
        assert mcp_card["endpoint"].endswith("/mcp")
        assert mcp_card["authentication"]["required"] is False
        assert len(mcp_card["tools"]) == 8

        mcp_get_json = client.get("/mcp")
        assert mcp_get_json.status_code == 200
        assert mcp_get_json.json()["endpoint"].endswith("/mcp")

        mcp_get_md = client.get("/mcp", headers={"Accept": "text/markdown"})
        assert mcp_get_md.status_code == 200
        assert mcp_get_md.headers["content-type"].startswith("text/markdown")
        assert "list_projects" in mcp_get_md.text


# ---------------------------------------------------------------------------
# 4. Streamable HTTP MCP Server (`POST /mcp` JSON-RPC 2.0)
# ---------------------------------------------------------------------------


@pytest.mark.production_missions
def test_streamable_http_mcp_jsonrpc_lifecycle_tools_and_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(PROJECTS_DIR))
    monkeypatch.delenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", raising=False)

    app = create_app(db_path=tmp_path / "mcp_test.db", seed=True)
    with TestClient(app) as client:
        handle, finding_id = _register_and_submit_p1_finding(client)

        def rpc(method: str, params: dict[str, Any] | None = None, req_id: Any = 1) -> Any:
            body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
            if req_id is not None:
                body["id"] = req_id
            if params is not None:
                body["params"] = params
            return client.post("/mcp", json=body)

        # 1. initialize + notifications/initialized + ping
        r_init = rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test"}}, req_id=1)
        assert r_init.status_code == 200
        assert r_init.headers.get("mcp-protocol-version") == "2025-11-25"
        init_res = r_init.json()["result"]
        assert init_res["protocolVersion"] == "2025-11-25"
        assert init_res["serverInfo"]["name"] == "aiwatchdogs-india"
        assert "tools" in init_res["capabilities"]
        assert "resources" in init_res["capabilities"]

        r_notif = rpc("notifications/initialized", req_id=None)
        assert r_notif.status_code == 202

        r_ping = rpc("ping", req_id=2)
        assert r_ping.status_code == 200
        assert r_ping.json()["result"] == {}

        # 2. tools/list
        r_tools = rpc("tools/list", req_id=3)
        assert r_tools.status_code == 200
        tool_names = {t["name"] for t in r_tools.json()["result"]["tools"]}
        assert tool_names == {
            "list_projects",
            "get_project",
            "list_project_tasks",
            "get_task_join_prompt",
            "list_findings",
            "get_leaderboard",
            "get_live_telemetry",
            "get_page_markdown",
        }

        # 3. tools/call across all 8 tools
        r_lp = rpc("tools/call", {"name": "list_projects", "arguments": {}}, req_id=4)
        assert r_lp.status_code == 200
        lp_data = r_lp.json()["result"]["structuredContent"]
        expected_slugs = {
            "police-finances",
            "gov-in-public-information",
            "union-budget-unspent-funds",
            "one-road-two-bills",
            "one-teacher-five-grades",
        }
        if (PROJECTS_DIR / "parliaments-to-do-list.json").exists():
            expected_slugs.add("parliaments-to-do-list")
        if (PROJECTS_DIR / "only-one-bid.json").exists():
            expected_slugs.add("only-one-bid")
        if (PROJECTS_DIR / "sovereign-lies.json").exists():
            expected_slugs.add("sovereign-lies")
        if (PROJECTS_DIR / "auditing-government-exams.json").exists():
            expected_slugs.add("auditing-government-exams")
        if (PROJECTS_DIR / "innocent-but-locked-up.json").exists():
            expected_slugs.add("innocent-but-locked-up")
        assert lp_data["total"] == len(expected_slugs)
        assert {p["slug"] for p in lp_data["projects"]} == expected_slugs

        r_gp = rpc("tools/call", {"name": "get_project", "arguments": {"slug": "police-finances"}}, req_id=5)
        gp_data = r_gp.json()["result"]["structuredContent"]
        assert gp_data["slug"] == "police-finances"
        assert gp_data["visual_summary"]["methodology_code"] == "P1"

        r_tasks = rpc(
            "tools/call",
            {"name": "list_project_tasks", "arguments": {"slug": "police-finances", "status": "queued", "limit": 5}},
            req_id=6,
        )
        t_data = r_tasks.json()["result"]["structuredContent"]
        assert t_data["count"] == 5
        # Paged from the database: 99 queued P1 audits plus the 3 peer checks the submitted finding queued.
        assert t_data["total"] == 102
        assert t_data["offset"] == 0
        assert t_data["has_more"] is True
        assert t_data["next_offset"] == 5
        assert all("join_prompt" in t for t in t_data["tasks"])

        r_tasks_p2 = rpc(
            "tools/call",
            {
                "name": "list_project_tasks",
                "arguments": {"slug": "police-finances", "status": "queued", "limit": 5, "offset": t_data["next_offset"]},
            },
            req_id=601,
        )
        t_data2 = r_tasks_p2.json()["result"]["structuredContent"]
        assert t_data2["offset"] == 5
        assert t_data2["count"] == 5
        assert {t["id"] for t in t_data["tasks"]}.isdisjoint({t["id"] for t in t_data2["tasks"]})

        r_tasks_end = rpc(
            "tools/call",
            {
                "name": "list_project_tasks",
                "arguments": {"slug": "police-finances", "status": "queued", "limit": 10, "offset": 99},
            },
            req_id=602,
        )
        t_end = r_tasks_end.json()["result"]["structuredContent"]
        assert t_end["count"] == 3
        assert t_end["has_more"] is False
        assert t_end["next_offset"] is None

        r_prompt_p1 = rpc(
            "tools/call",
            {
                "name": "get_task_join_prompt",
                "arguments": {"slug": "police-finances", "task_id": "prj-police-goa-2055-2026-27"},
            },
            req_id=7,
        )
        assert "prj-police-goa-2055-2026-27" in r_prompt_p1.json()["result"]["structuredContent"]["prompt"]
        assert "/join.txt" in r_prompt_p1.json()["result"]["structuredContent"]["prompt"]

        r_prompt_ub = rpc(
            "tools/call",
            {
                "name": "get_task_join_prompt",
                "arguments": {"slug": "union-budget-unspent-funds", "task_id": "prj-ub-2014-15-d002-l2-02-01"},
            },
            req_id=8,
        )
        ub_prompt = r_prompt_ub.json()["result"]["structuredContent"]
        assert "/join.txt" in ub_prompt["prompt"]
        assert "prj-ub-2014-15-d002-l2-02-01" in ub_prompt["prompt"]
        assert ub_prompt["guide_url"].endswith("/join.txt")

        r_find = rpc("tools/call", {"name": "list_findings", "arguments": {"mission": "police-finances"}}, req_id=9)
        f_data = r_find.json()["result"]["structuredContent"]
        assert f_data["total"] == 1
        assert f_data["findings"][0]["id"] == finding_id

        r_lb = rpc("tools/call", {"name": "get_leaderboard", "arguments": {"project_slug": "police-finances"}}, req_id=10)
        lb_data = r_lb.json()["result"]["structuredContent"]
        assert lb_data["contributors"][0]["handle"] == handle

        r_live = rpc("tools/call", {"name": "get_live_telemetry", "arguments": {}}, req_id=11)
        assert r_live.json()["result"]["structuredContent"]["total_agents"] >= 1

        r_page = rpc("tools/call", {"name": "get_page_markdown", "arguments": {"path": "/projects/police-finances"}}, req_id=12)
        assert r_page.json()["result"]["isError"] is False
        assert "# " in r_page.json()["result"]["content"][0]["text"]

        # 4. resources/list & resources/read
        r_res_list = rpc("resources/list", req_id=13)
        uris = {r["uri"] for r in r_res_list.json()["result"]["resources"]}
        assert "aiwatchdogs://llms.txt" in uris
        assert "aiwatchdogs://projects" in uris
        assert "aiwatchdogs://projects/police-finances" in uris

        r_read_llms = rpc("resources/read", {"uri": "aiwatchdogs://llms.txt"}, req_id=14)
        assert "# AI Watchdogs India" in r_read_llms.json()["result"]["contents"][0]["text"]

        r_read_proj = rpc("resources/read", {"uri": "aiwatchdogs://projects/police-finances"}, req_id=15)
        assert "Union & Central Forces" in r_read_proj.json()["result"]["contents"][0]["text"]

        # 5. Error handling: unknown method (-32601), unknown tool (-32602), invalid JSON (-32700)
        assert rpc("unknown/method", req_id=16).json()["error"]["code"] == -32601
        assert rpc("tools/call", {"name": "no_such_tool", "arguments": {}}, req_id=17).json()["error"]["code"] == -32602
        bad_json = client.post("/mcp", content=b"{not-json", headers={"Content-Type": "application/json"})
        assert bad_json.status_code == 400
        assert bad_json.json()["error"]["code"] == -32700


def test_vercel_json_routes_agent_endpoints_ahead_of_spa_fallbacks() -> None:
    import json
    import re

    vercel_path = Path(__file__).resolve().parents[1] / "vercel.json"
    cfg = json.loads(vercel_path.read_text(encoding="utf-8"))
    rewrites = cfg["rewrites"]

    # Never rewrite directly to /api/index.py (Vercel rewrites scope["path"] to /api/index.py);
    # instead let Vercel's FastAPI catch-all preserve real paths for .md, /llms.txt, /.well-known/*, /mcp!
    assert all(r["destination"] != "/api/index.py" for r in rewrites)

    first_spa_idx = next(i for i, r in enumerate(rewrites) if r["destination"] == "/index.html")
    md_rules = rewrites[:first_spa_idx]
    spa_rules = rewrites[first_spa_idx:]

    # Accept: text/markdown negotiation rules come before SPA /index.html fallbacks
    assert len(md_rules) >= 10
    assert all(r["destination"].endswith(".md") for r in md_rules)
    assert all(
        any(h.get("type") == "header" and h.get("key") == "accept" and "text/markdown" in h.get("value", "") for h in r.get("has", []))
        for r in md_rules
    )

    # SPA /index.html fallback regexes MUST NOT swallow .md paths or discovery/MCP paths
    protected_paths = (
        "/index.md",
        "/projects.md",
        "/projects/police-finances.md",
        "/findings/fnd-123.md",
        "/graveyard.md",
        "/graveyard.atom",
        "/a/civic-owl.md",
        "/llms.txt",
        "/llms-full.txt",
        "/robots.txt",
        "/sitemap.xml",
        "/.well-known/api-catalog",
        "/.well-known/mcp.json",
        "/mcp",
    )
    for rule in spa_rules:
        pat = re.compile(f"^{rule['source']}$")
        for p in protected_paths:
            assert pat.match(p) is None, f"SPA rule {rule['source']!r} wrongly matches {p!r}"

