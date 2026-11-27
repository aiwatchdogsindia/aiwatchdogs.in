"""Regression tests for Neon Postgres egress reduction, durable W1 summary caching, and Vercel CDN headers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from aiwatchdogs.engines.gigw_matrix import W1_CHECKS
from aiwatchdogs.hub.app import create_app
from aiwatchdogs.hub.database import Database, LATEST_SCHEMA_VERSION, dumps_json, utc_now_iso
from aiwatchdogs.hub.desk import DeskService
from aiwatchdogs.hub.sse import SSEBroker


def test_public_read_endpoints_emit_cdn_cache_headers(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "hub.db", seed=True)
    with TestClient(app) as client:
        reg = client.post(
            "/api/agents/register",
            json={"handle": "cache-tester", "provider": "rule-engine", "model": "v1"},
        ).json()
        assert reg["api_token"]

        cached_paths = [
            ("/api/head", "s-maxage=30"),
            ("/api/missions", "s-maxage=30"),
            ("/api/findings", "s-maxage=60"),
            ("/api/graveyard", "s-maxage=30"),
            ("/api/graph", "s-maxage=300"),
            ("/api/live", "s-maxage=60"),
            ("/api/swarm/live", "s-maxage=10"),
            ("/api/contributors", "s-maxage=30"),
            ("/api/contributors/cache-tester", "s-maxage=60"),
            ("/api/sponsors", "s-maxage=60"),
            ("/api/ledger", "s-maxage=60"),
            ("/api/ledger?format=csv", "s-maxage=60"),
            ("/api/sources", "s-maxage=60"),
            ("/api/events?since_id=0", "s-maxage=5"),
            ("/api/missions/gov-in-public-information/matrix", "s-maxage=86400"),
            ("/feed.atom", "s-maxage=300"),
            ("/llms.txt", "s-maxage=1800"),
            ("/sitemap.xml", "s-maxage=1800"),
            ("/index.md", "s-maxage=1800"),
        ]
        for path, expected_directive in cached_paths:
            resp = client.get(path)
            assert resp.status_code == 200, f"{path} returned {resp.status_code}: {resp.text}"
            cc = resp.headers.get("cache-control", "")
            assert "public" in cc and expected_directive in cc, f"{path} got Cache-Control={cc!r}"

        # /llms-full.txt redirects to /llms.txt with long CDN cache (zero DB queries).
        llms_full = client.get("/llms-full.txt", follow_redirects=False)
        assert llms_full.status_code == 308
        assert llms_full.headers["location"] == "/llms.txt"
        assert "s-maxage=86400" in llms_full.headers.get("cache-control", "")

        # /api/findings defaults to 24 per page.
        assert client.get("/api/findings").json()["limit"] == 24

        # Dynamic/sensitive onboarding scripts stay strictly uncached.
        for path in ("/join.txt", "/join.sh"):
            resp = client.get(path)
            assert resp.status_code == 200
            assert resp.headers.get("cache-control") == "no-store"


def test_vercel_mode_disables_sse_and_mcp_by_default(tmp_path: Path, monkeypatch) -> None:
    app = create_app(db_path=tmp_path / "vercel_guard.db", seed=True)
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("AIWATCHDOGS_ENABLE_SSE", raising=False)
    monkeypatch.delenv("AIWATCHDOGS_ENABLE_MCP", raising=False)
    with TestClient(app) as client:
        sse_resp = client.get("/api/stream")
        assert sse_resp.status_code == 503
        assert "s-maxage=300" in sse_resp.headers.get("cache-control", "")

        mcp_resp = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert mcp_resp.status_code == 410
        assert "s-maxage=300" in mcp_resp.headers.get("cache-control", "")


def test_findings_list_omits_bulky_blobs_while_detail_returns_full_dossier(tmp_path: Path) -> None:
    db = Database(db_path=tmp_path / "slim_findings.db")
    now = utc_now_iso()
    with db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO missions (
                id, slug, title, description, pillar, methodology_code, jurisdiction,
                ministry_or_authority, status, priority, total_tasks, completed_tasks, created_at
            ) VALUES ('m-1', 'budget-m1', 'Budget M1', 'Desc', 'budget', 'P1', 'Union', 'MoF', 'active', 1, 1, 1, ?)
            """,
            (now,),
        )
        conn.execute(
            """
            INSERT INTO findings (
                id, mission_id, mission_slug, pillar, title, summary, methodology_code, entity_name,
                jurisdiction, fiscal_year, monetary_exposure_inr, severity, rule_triggered,
                citations_json, benign_explanations_json, agent_trace_json, submitted_by, status, created_at
            ) VALUES (
                'fnd-slim-1', 'm-1', 'budget-m1', 'budget', 'Test Lead', 'Summary text', 'P1', 'Delhi Police',
                'Delhi', '2025-26', 10000000.0, 'medium', 'P1_RULE',
                ?, ?, ?, 'auditor-1', 'verified', ?
            )
            """,
            (
                dumps_json([{"page": 4, "excerpt": "Long citation excerpt"}]),
                dumps_json(["Benign statutory explanation"]),
                dumps_json({"node_id": "auditor-1", "trace": "heavy debug trace"}),
                now,
            ),
        )
    desk = DeskService(db=db, broker=SSEBroker(db=db))
    listed = desk.list_findings()["findings"][0]
    assert listed["id"] == "fnd-slim-1"
    assert listed["citations"] == []
    assert listed["benign_explanations"] == []
    assert listed["agent_trace"] == {}

    detail = desk.get_finding_detail("fnd-slim-1")
    assert len(detail["citations"]) == 1
    assert detail["benign_explanations"] == ["Benign statutory explanation"]
    assert detail["agent_trace"]["node_id"] == "auditor-1"
    db.close()


def test_w1_summary_cache_survives_cold_restart_and_invalidates_on_update(tmp_path: Path) -> None:
    db_path = tmp_path / "w1_cache.db"
    db = Database(db_path=db_path)
    now = utc_now_iso()
    mid = "project-gov-in-public-information"

    with db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO missions (
                id, slug, title, description, pillar, methodology_code, jurisdiction,
                ministry_or_authority, status, priority, total_tasks, completed_tasks,
                created_at, is_community_project
            ) VALUES (?, 'gov-in-public-information', 'Public Info', 'Desc', 'disclosure', 'W1',
                      'Union', 'MeitY', 'active', 1, 2, 1, ?, 1)
            """,
            (mid, now),
        )
        checks_1 = {k: ("observed" if i % 2 == 0 else "not_observed") for i, k in enumerate(W1_CHECKS)}
        out_1 = {
            "observations": [
                {
                    "domain": "meity.gov.in",
                    "observed_at": now,
                    "outcome": "observed",
                    "reason": None,
                    "checks": checks_1,
                    "pages": ["https://meity.gov.in/"],
                }
            ]
        }
        conn.execute(
            """
            INSERT INTO tasks (
                id, mission_id, mission_slug, methodology_code, task_type, title, instructions,
                payload_json, status, output_json, created_at, updated_at, completed_at
            ) VALUES ('w1-b1', ?, 'gov-in-public-information', 'W1', 'anomaly_scan', 'Batch 1', 'Check',
                      '{}', 'completed', ?, ?, ?, ?)
            """,
            (mid, dumps_json(out_1), now, now, now),
        )
        conn.execute(
            """
            INSERT INTO tasks (
                id, mission_id, mission_slug, methodology_code, task_type, title, instructions,
                payload_json, status, output_json, created_at, updated_at, completed_at
            ) VALUES ('w1-b2', ?, 'gov-in-public-information', 'W1', 'anomaly_scan', 'Batch 2', 'Check',
                      '{}', 'queued', NULL, ?, ?, NULL)
            """,
            (mid, now, now),
        )

    desk1 = DeskService(db=db, broker=SSEBroker(db=db))
    first_detail = desk1.get_mission_detail("gov-in-public-information")
    first_wa = first_detail["visual_summary"]["website_audit"]
    assert first_wa["hosts_observed"] == 1
    assert first_wa["batches_done"] == 1

    # summary_cache should now hold the compact aggregate (~few KB, not full matrix/observations).
    with db.read() as conn:
        row = conn.execute(
            "SELECT version_stamp, payload_json FROM summary_cache WHERE cache_key = ?",
            (f"w1_visual:{mid}",),
        ).fetchone()
    assert row is not None
    assert len(row["payload_json"]) < 16_000

    # Cold restart (brand new DeskService with empty in-memory cache) must match identically
    # without scanning tasks.output_json.
    desk2 = DeskService(db=db, broker=SSEBroker(db=db))
    with db.transaction() as conn:
        # Null out output_json directly to prove cold start reads summary_cache and NOT output_json.
        conn.execute("UPDATE tasks SET output_json = NULL WHERE id = 'w1-b1'")
    second_detail = desk2.get_mission_detail("gov-in-public-information")
    assert second_detail["visual_summary"]["website_audit"] == first_wa

    # Read-only cold restart must also hit summary_cache cleanly without writing.
    ro_db = Database(db_path=db_path, read_only=True)
    ro_desk = DeskService(db=ro_db, broker=SSEBroker(db=ro_db))
    ro_detail = ro_desk.get_mission_detail("gov-in-public-information")
    assert ro_detail["visual_summary"]["website_audit"] == first_wa
    ro_db.close()

    # Completing batch 2 invalidates the version_stamp and recomputes from tasks.output_json.
    checks_2 = {k: "observed" for k in W1_CHECKS}
    out_2 = {
        "observations": [
            {
                "domain": "india.gov.in",
                "observed_at": "2026-11-27T12:00:00Z",
                "outcome": "observed",
                "reason": None,
                "checks": checks_2,
                "pages": ["https://india.gov.in/"],
            }
        ]
    }
    with db.transaction() as conn:
        conn.execute("UPDATE tasks SET output_json = ? WHERE id = 'w1-b1'", (dumps_json(out_1),))
        conn.execute(
            "UPDATE tasks SET status = 'completed', output_json = ?, completed_at = ?, updated_at = ? WHERE id = 'w1-b2'",
            (dumps_json(out_2), "2026-11-27T12:00:00Z", "2026-11-27T12:00:00Z"),
        )
    third_detail = desk2.get_mission_detail("gov-in-public-information")
    third_wa = third_detail["visual_summary"]["website_audit"]
    assert third_wa["hosts_observed"] == 2
    assert third_wa["batches_done"] == 2
    db.close()


def test_migration_v13_backfills_reported_tokens_and_aggregates_in_sql(tmp_path: Path) -> None:
    db_path = tmp_path / "tok.db"
    db = Database(db_path=db_path)
    assert db.schema_version() == LATEST_SCHEMA_VERSION
    now = utc_now_iso()
    mid = "mission-tok-test"

    with db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO missions (
                id, slug, title, description, pillar, methodology_code, jurisdiction,
                ministry_or_authority, status, priority, total_tasks, completed_tasks, created_at
            ) VALUES (?, 'tok-test', 'Tok Test', 'Desc', 'budget', 'P1', 'Union', 'MoF', 'active', 1, 2, 2, ?)
            """,
            (mid, now),
        )
        # Simulate a legacy row whose usage_json exists before migration v13 backfill.
        usage_a = json.dumps({"agent": {"input_tokens": 1200, "cached_input_tokens": 300, "output_tokens": 500}})
        usage_b = json.dumps({"engine": {"input_tokens": 0, "output_tokens": 0}})
        conn.execute(
            """
            INSERT INTO tasks (
                id, mission_id, mission_slug, methodology_code, task_type, title, instructions,
                payload_json, status, estimated_tokens, usage_json, reported_tokens, created_at, updated_at, completed_at
            ) VALUES ('t1', ?, 'tok-test', 'P1', 'anomaly_scan', 'T1', 'Run', '{}', 'completed', 5000, ?, NULL, ?, ?, ?)
            """,
            (mid, usage_a, now, now, now),
        )
        conn.execute(
            """
            INSERT INTO tasks (
                id, mission_id, mission_slug, methodology_code, task_type, title, instructions,
                payload_json, status, estimated_tokens, usage_json, reported_tokens, created_at, updated_at, completed_at
            ) VALUES ('t2', ?, 'tok-test', 'P1', 'anomaly_scan', 'T2', 'Run', '{}', 'completed', 5000, ?, NULL, ?, ?, ?)
            """,
            (mid, usage_b, now, now, now),
        )
        # Re-run v13 migration by deleting its marker row.
        conn.execute("DELETE FROM schema_migrations WHERE version = 13")

    db.init_schema()
    desk = DeskService(db=db, broker=SSEBroker(db=db))
    with db.read() as conn:
        m_rows = conn.execute("SELECT * FROM missions WHERE id = ?", (mid,)).fetchall()
        summaries: dict[str, Any] = desk._token_summaries(conn, m_rows)
        t1_tok = conn.execute("SELECT reported_tokens FROM tasks WHERE id = 't1'").fetchone()["reported_tokens"]
        t2_tok = conn.execute("SELECT reported_tokens FROM tasks WHERE id = 't2'").fetchone()["reported_tokens"]

    assert t1_tok == 2000
    assert t2_tok is None  # zero-token code-only engine check stays None
    assert summaries[mid]["tokens_used"] == 2000
    assert summaries[mid]["reported_tasks"] == 1
    assert summaries[mid]["completed_tasks"] == 2
    db.close()
