"""Unit and integration tests for GET /api/swarm/live, SSE telemetry enrichments, and avatar CSP."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aiwatchdogs.hub.app import SECURITY_CSP, create_app
from aiwatchdogs.hub.database import translate_sql_for_postgres
from aiwatchdogs.hub.desk import LIVE_AGENT_WINDOW_SECONDS


def _iso_offset(*, minutes: int = 0, seconds: int = 0) -> str:
    dt = datetime.now(timezone.utc) + timedelta(minutes=minutes, seconds=seconds)
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _register(
    client: TestClient,
    handle: str,
    *,
    display_name: str | None = None,
    provider: str = "rule-engine",
    model: str = "deterministic-cpu-v1",
    coding_agent: str | None = None,
    coding_agent_model: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "handle": handle,
        "display_name": display_name or f"Contributor {handle}",
        "provider": provider,
        "model": model,
        "mode": "auto",
        "region": "IN",
    }
    if coding_agent is not None:
        payload["coding_agent"] = coding_agent
    if coding_agent_model is not None:
        payload["coding_agent_model"] = coding_agent_model
    resp = client.post("/api/agents/register", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _parse_csp_directives(csp: str) -> dict[str, list[str]]:
    directives: dict[str, list[str]] = {}
    for raw_part in csp.split(";"):
        part = raw_part.strip()
        if not part:
            continue
        tokens = part.split()
        directives[tokens[0].lower()] = tokens[1:]
    return directives


def test_swarm_live_empty_seeded_and_unseeded_states(tmp_path: Path) -> None:
    # 1. Seeded hub with 0 agents
    seeded_app = create_app(db_path=tmp_path / "seeded.db", seed=True)
    with TestClient(seeded_app) as client:
        resp = client.get("/api/swarm/live")
        assert resp.status_code == 200, resp.text
        assert "s-maxage=3" in resp.headers.get("cache-control", "")
        data = resp.json()

        assert isinstance(data["generated_at"], str) and data["generated_at"].endswith("Z")
        assert data["latest_event_id"] == 0
        assert data["live_window_seconds"] == LIVE_AGENT_WINDOW_SECONDS
        assert data["working_now"] == 0
        assert data["active_leases_count"] == 0
        assert data["total_agents"] == 0
        assert data["total_sessions"] == 0
        assert data["tasks_completed_total"] == 0
        assert data["tasks_last_hour"] == 0
        assert data["last_activity_at"] is None
        assert data["active_nodes"] == []
        assert data["recent_joins"] == []
        assert data["live_whispers"] == []
        assert data["progress"] == {
            "tasks_total": 16,
            "tasks_completed": 0,
            "tasks_in_progress": 0,
            "findings_verified": 0,
            "findings_in_review": 0,
        }
    seeded_app.state.db.close()

    # 2. Completely unseeded empty DB
    empty_app = create_app(db_path=tmp_path / "empty.db", seed=False)
    with TestClient(empty_app) as client:
        resp = client.get("/api/swarm/live")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["working_now"] == 0
        assert data["active_leases_count"] == 0
        assert data["total_agents"] == 0
        assert data["last_activity_at"] is None
        assert data["active_nodes"] == []
        assert data["recent_joins"] == []
        assert data["live_whispers"] == []
        assert data["progress"] == {
            "tasks_total": 0,
            "tasks_completed": 0,
            "tasks_in_progress": 0,
            "findings_verified": 0,
            "findings_in_review": 0,
        }
    empty_app.state.db.close()


def test_swarm_live_active_auditor_verifier_adjudicator_and_progress(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "live_roles.db", seed=True)
    with TestClient(app) as client:
        auditor = _register(
            client,
            "alice-auditor",
            display_name="Alice Auditor",
            coding_agent="Cursor",
            coding_agent_model="Grok 4.7",
        )
        verifier1 = _register(
            client,
            "bob-verifier",
            display_name="Bob Verifier",
            coding_agent="Claude Code",
            coding_agent_model="Claude Sonnet 4.6",
        )
        verifier2 = _register(
            client,
            "carol-adjudicator",
            display_name="Carol Adjudicator",
            coding_agent="Codex",
            coding_agent_model="GPT-5.2",
        )

        # Auditor claims an investigation task and sends a live heartbeat whisper
        claim = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": auditor["api_token"]},
            json={
                "handle": auditor["handle"],
                "mode": "investigate",
                "preferred_mission": "assam-ocds-single-bid",
            },
        )
        assert claim.status_code == 200, claim.text
        lease_id = claim.json()["lease_id"]
        task_id = claim.json()["task"]["id"]
        task_title = claim.json()["task"]["title"]
        real_sha = claim.json()["task"]["payload"]["expected_sha256"]

        beat = client.post(
            "/api/desk/beat",
            headers={"X-Agent-Token": auditor["api_token"]},
            json={
                "lease_id": lease_id,
                "progress_pct": 65,
                "whisper": "Verified SHA-256 on Assam OCDS CSV; computing single-bid shares.",
            },
        )
        assert beat.status_code == 200, beat.text

        snap1 = client.get("/api/swarm/live").json()
        assert snap1["total_agents"] == 3
        assert snap1["working_now"] == 3  # all 3 registered/seen within live window
        assert snap1["active_leases_count"] == 1
        assert snap1["total_sessions"] >= 1
        assert snap1["tasks_completed_total"] == 0
        assert snap1["tasks_last_hour"] == 0
        assert snap1["last_activity_at"] is not None
        assert snap1["latest_event_id"] > 0
        assert snap1["progress"]["tasks_in_progress"] == 1
        assert snap1["progress"]["tasks_completed"] == 0
        assert snap1["progress"]["tasks_total"] == 16

        # Active lease holder MUST come first in active_nodes
        first_node = snap1["active_nodes"][0]
        assert first_node["handle"] == "alice-auditor"
        assert first_node["display_name"] == "Alice Auditor"
        assert first_node["status"] == "working"
        assert first_node["role"] == "auditor"
        assert first_node["coding_agent"] == "Cursor"
        assert first_node["coding_agent_model"] == "Grok 4.7"
        assert first_node["provider"] == "rule-engine"
        assert first_node["model"] == "deterministic-cpu-v1"
        assert first_node["current_task_id"] == task_id
        assert first_node["current_task_title"] == task_title
        assert first_node["current_project_slug"] == "assam-ocds-single-bid"
        assert first_node["progress_pct"] == 65
        assert "single-bid shares" in (first_node["latest_whisper"] or "")
        assert first_node["tasks_completed"] == 0
        assert first_node["verifications_done"] == 0
        assert first_node["findings_submitted"] == 0
        assert first_node["last_seen_at"].endswith("Z")
        assert first_node["created_at"].endswith("Z")

        # Recently joined agents without active leases are "recent" with null role/task
        by_handle = {n["handle"]: n for n in snap1["active_nodes"]}
        assert by_handle["bob-verifier"]["status"] == "recent"
        assert by_handle["bob-verifier"]["role"] is None
        assert by_handle["bob-verifier"]["current_task_id"] is None
        assert by_handle["bob-verifier"]["progress_pct"] is None

        # Live whispers carry enriched coding_agent, role, task_id, and project_slug
        top_whisper = snap1["live_whispers"][0]
        assert top_whisper["handle"] == "alice-auditor"
        assert top_whisper["event_type"] == "whisper"
        assert top_whisper["coding_agent"] == "Cursor"
        assert top_whisper["role"] == "auditor"
        assert top_whisper["task_id"] == task_id
        assert top_whisper["project_slug"] == "assam-ocds-single-bid"
        assert "single-bid shares" in top_whisper["whisper"]

        # Auditor submits a neutral finding -> spawns 2 peer_verify tasks
        sub = client.post(
            "/api/desk/submit",
            headers={"X-Agent-Token": auditor["api_token"]},
            json={
                "lease_id": lease_id,
                "outcome": "finding",
                "whisper": "Submitted Assam M1 single-bid finding for 2-of-2 peer verification.",
                "finding": {
                    "title": "Public Record Anomaly: Elevated single-bid share in Assam OCDS",
                    "summary": "Deterministic scan observed elevated single-bid share across FY2016-22.",
                    "methodology_code": "M1",
                    "entity_name": "Health Department, Assam",
                    "jurisdiction": "State:Assam",
                    "fiscal_year": "FY2016-22",
                    "monetary_exposure_inr": 604300000,
                    "severity": "high",
                    "anomaly_metrics": {"single_bid_rate_pct": 35.5},
                    "rule_triggered": "GFR-2017-R166",
                    "artifact_sha256": real_sha,
                    "citations": [{"row_id": "OCID-1"}],
                    "benign_explanations": ["Proprietary hospital OEM procurement exception under GFR."],
                },
            },
        )
        assert sub.status_code == 200, sub.text
        finding_id = sub.json()["finding_id"]

        # Verifier 1 claims peer_verify task; mark 2nd peer_verify task as adjudication and have Verifier 2 claim it
        pv1 = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": verifier1["api_token"]},
            json={"handle": verifier1["handle"], "mode": "verify"},
        )
        assert pv1.status_code == 200, pv1.text
        pv1_task_id = pv1.json()["task"]["id"]
        pv1_lease = pv1.json()["lease_id"]

        with app.state.db.transaction() as conn:
            row2 = conn.execute(
                "SELECT id, payload_json FROM tasks WHERE task_type = 'peer_verify' AND status = 'queued' LIMIT 1"
            ).fetchone()
            assert row2 is not None
            adj_payload = json.loads(row2["payload_json"])
            adj_payload["review_type"] = "adjudication"
            conn.execute(
                "UPDATE tasks SET payload_json = ? WHERE id = ?",
                (json.dumps(adj_payload), row2["id"]),
            )

        pv2 = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": verifier2["api_token"]},
            json={"handle": verifier2["handle"], "mode": "verify"},
        )
        assert pv2.status_code == 200, pv2.text
        pv2_task_id = pv2.json()["task"]["id"]
        pv2_lease = pv2.json()["lease_id"]

        snap2 = client.get("/api/swarm/live").json()
        assert snap2["active_leases_count"] == 2
        assert snap2["tasks_completed_total"] == 1
        assert snap2["tasks_last_hour"] == 1
        assert snap2["progress"]["tasks_completed"] == 1
        assert snap2["progress"]["tasks_in_progress"] == 2
        assert snap2["progress"]["findings_in_review"] == 1
        assert snap2["progress"]["findings_verified"] == 0

        # Active lease holders (verifier1 & verifier2) come ahead of recent auditor
        assert {snap2["active_nodes"][0]["handle"], snap2["active_nodes"][1]["handle"]} == {
            "bob-verifier",
            "carol-adjudicator",
        }
        assert snap2["active_nodes"][2]["handle"] == "alice-auditor"
        assert snap2["active_nodes"][2]["status"] == "recent"
        assert snap2["active_nodes"][2]["findings_submitted"] == 1
        assert snap2["active_nodes"][2]["tasks_completed"] == 1

        nodes2 = {n["handle"]: n for n in snap2["active_nodes"]}
        assert nodes2["bob-verifier"]["status"] == "verifying"
        assert nodes2["bob-verifier"]["role"] == "peer_verifier"
        assert nodes2["bob-verifier"]["current_task_id"] == pv1_task_id
        assert nodes2["carol-adjudicator"]["status"] == "verifying"
        assert nodes2["carol-adjudicator"]["role"] == "adjudicator"
        assert nodes2["carol-adjudicator"]["current_task_id"] == pv2_task_id

        # Both confirm -> finding promoted to verified!
        for agent_info, lease in ((verifier1, pv1_lease), (verifier2, pv2_lease)):
            ver = client.post(
                "/api/desk/submit",
                headers={"X-Agent-Token": agent_info["api_token"]},
                json={
                    "lease_id": lease,
                    "outcome": "verification",
                    "verification": {
                        "finding_id": finding_id,
                        "verdict": "confirm",
                        "checks": {
                            "bytes_digest": "pass",
                            "deterministic_reproduction": "pass",
                            "metric_tolerance": "pass",
                            "neutrality_privacy": "pass",
                        },
                        "reproduced_metrics": {
                            "artifact_sha256": real_sha,
                            "single_bid_rate_pct": 35.5,
                        },
                        "confidence": 0.96,
                        "notes": "Independently reproduced Assam OCDS single-bid rate.",
                    },
                },
            )
            assert ver.status_code == 200, ver.text

        snap3 = client.get("/api/swarm/live").json()
        assert snap3["active_leases_count"] == 0
        assert snap3["tasks_completed_total"] == 3
        assert snap3["tasks_last_hour"] == 3
        assert snap3["progress"]["findings_verified"] == 1
        assert snap3["progress"]["findings_in_review"] == 0
        assert snap3["live_whispers"][0]["event_type"] == "finding_verified"
        assert snap3["live_whispers"][0]["role"] == "peer_verifier"
        assert snap3["live_whispers"][0]["finding_id"] == finding_id
    app.state.db.close()


def test_swarm_live_recent_joins_ordering_idle_expired_lease_and_revoked(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "ordering.db", seed=True)
    with TestClient(app) as client:
        old_idle = _register(client, "oldest-idle", coding_agent="Aider", coding_agent_model="Qwen 2.5")
        lease_stale_hb = _register(client, "mid-lease-holder", coding_agent="Cursor")
        expired_holder = _register(client, "expired-holder", coding_agent="Codex")
        revoked = _register(client, "revoked-bot", coding_agent="Claude Code")
        newest = _register(client, "newest-recent", coding_agent="Antigravity", coding_agent_model="Gemini 2.5")

        # mid-lease-holder claims a task with an active future lease
        c_active = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": lease_stale_hb["api_token"]},
            json={"handle": lease_stale_hb["handle"], "mode": "investigate"},
        )
        assert c_active.status_code == 200

        # expired-holder claims a task whose lease we backdate into the past
        c_expired = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": expired_holder["api_token"]},
            json={"handle": expired_holder["handle"], "mode": "investigate"},
        )
        assert c_expired.status_code == 200
        expired_task_id = c_expired.json()["task"]["id"]

        # Revoke revoked-bot
        rev = client.post("/api/agents/revoke", headers={"X-Agent-Token": revoked["api_token"]})
        assert rev.status_code == 200

        # Backdate timestamps deterministically
        t_oldest = _iso_offset(minutes=-120)
        t_mid_join = _iso_offset(minutes=-90)
        t_exp_join = _iso_offset(minutes=-60)
        t_newest_join = _iso_offset(minutes=-2)
        t_stale_hb = _iso_offset(minutes=-25)  # outside 15m live window
        t_expired_lease = _iso_offset(minutes=-5)

        with app.state.db.transaction() as conn:
            conn.execute(
                "UPDATE agents SET joined_at = ?, last_heartbeat_at = ? WHERE handle = ?",
                (t_oldest, t_oldest, old_idle["handle"]),
            )
            conn.execute(
                "UPDATE agents SET joined_at = ?, last_heartbeat_at = ? WHERE handle = ?",
                (t_mid_join, t_stale_hb, lease_stale_hb["handle"]),
            )
            conn.execute(
                "UPDATE agents SET joined_at = ?, last_heartbeat_at = ? WHERE handle = ?",
                (t_exp_join, t_stale_hb, expired_holder["handle"]),
            )
            conn.execute(
                "UPDATE agents SET joined_at = ?, last_heartbeat_at = ? WHERE handle = ?",
                (t_newest_join, t_newest_join, newest["handle"]),
            )
            conn.execute(
                "UPDATE tasks SET lease_expires_at = ? WHERE id = ?",
                (t_expired_lease, expired_task_id),
            )

        snap = client.get("/api/swarm/live").json()

        # total_agents includes all rows; revoked excluded from working_now, active_nodes, and recent_joins
        assert snap["total_agents"] == 5
        assert snap["active_leases_count"] == 1  # only non-expired lease counts
        assert snap["working_now"] == 2  # mid-lease-holder (active lease despite 25m hb) + newest-recent (2m hb)

        node_handles = [n["handle"] for n in snap["active_nodes"]]
        assert "revoked-bot" not in node_handles
        assert node_handles[0] == "mid-lease-holder"  # active lease holder first
        assert node_handles[1] == "newest-recent"  # recent second
        assert set(node_handles[2:]) == {"expired-holder", "oldest-idle"}  # idle last

        nodes = {n["handle"]: n for n in snap["active_nodes"]}
        assert nodes["mid-lease-holder"]["status"] == "working"
        assert nodes["newest-recent"]["status"] == "recent"
        assert nodes["expired-holder"]["status"] == "idle"
        assert nodes["expired-holder"]["current_task_id"] is None
        assert nodes["oldest-idle"]["status"] == "idle"

        # recent_joins strictly ordered newest created_at -> oldest created_at
        join_handles = [j["handle"] for j in snap["recent_joins"]]
        assert join_handles == [
            "newest-recent",
            "expired-holder",
            "mid-lease-holder",
            "oldest-idle",
        ]
        joins_by_handle = {j["handle"]: j for j in snap["recent_joins"]}
        assert joins_by_handle["newest-recent"]["is_working"] is True
        assert joins_by_handle["newest-recent"]["coding_agent"] == "Antigravity"
        assert joins_by_handle["newest-recent"]["coding_agent_model"] == "Gemini 2.5"
        assert joins_by_handle["mid-lease-holder"]["is_working"] is True
        assert joins_by_handle["expired-holder"]["is_working"] is False
        assert joins_by_handle["oldest-idle"]["is_working"] is False
    app.state.db.close()


def test_swarm_live_quarantine_redaction_and_sse_release_event(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "quarantine_live.db", seed=True)
    with TestClient(app) as client:
        author = _register(client, "q-author", coding_agent="Cursor")
        flagger = _register(client, "q-flagger", coding_agent="Codex")
        secret_marker = "SensitiveQuarantineLiveMarker991"

        claim = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": author["api_token"]},
            json={
                "handle": author["handle"],
                "mode": "investigate",
                "preferred_mission": "assam-ocds-single-bid",
            },
        )
        assert claim.status_code == 200
        lease_id = claim.json()["lease_id"]

        # Submit finding with marker, then flag_pii_or_legal to quarantine it
        sub = client.post(
            "/api/desk/submit",
            headers={"X-Agent-Token": author["api_token"]},
            json={
                "lease_id": lease_id,
                "outcome": "finding",
                "whisper": f"Submitted lead {secret_marker}",
                "finding": {
                    "title": f"Public Record Anomaly: {secret_marker}",
                    "summary": f"Deterministic summary referencing {secret_marker}.",
                    "methodology_code": "M1",
                    "entity_name": "Assam Irrigation",
                    "benign_explanations": ["Lawful procurement exception."],
                },
            },
        )
        assert sub.status_code == 200
        fid = sub.json()["finding_id"]

        pv = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": flagger["api_token"]},
            json={"handle": flagger["handle"], "mode": "verify"},
        )
        assert pv.status_code == 200
        flag = client.post(
            "/api/desk/submit",
            headers={"X-Agent-Token": flagger["api_token"]},
            json={
                "lease_id": pv.json()["lease_id"],
                "outcome": "verification",
                "verification": {
                    "finding_id": fid,
                    "verdict": "flag_pii_or_legal",
                    "notes": "Quarantining finding due to sensitive personal identifier.",
                },
            },
        )
        assert flag.status_code == 200

        # Marker MUST NOT leak via /api/swarm/live
        live_resp = client.get("/api/swarm/live")
        assert live_resp.status_code == 200
        assert secret_marker not in live_resp.text
        live_data = live_resp.json()
        assert live_data["progress"]["findings_verified"] == 0
        assert live_data["progress"]["findings_in_review"] == 0

        # Verify task_released durable SSE event is emitted on release
        c2 = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": author["api_token"]},
            json={"handle": author["handle"], "mode": "investigate"},
        )
        assert c2.status_code == 200
        rel = client.post(
            "/api/desk/release",
            headers={"X-Agent-Token": author["api_token"]},
            json={"lease_id": c2.json()["lease_id"], "reason": "operator_pause"},
        )
        assert rel.status_code == 200
        ev_resp = client.get("/api/events?since_id=0&event_type=task_released").json()
        assert len(ev_resp["events"]) == 1
        rel_ev = ev_resp["events"][0]
        assert rel_ev["agent_handle"] == "q-author"
        assert rel_ev["task_id"] == c2.json()["task"]["id"]
        assert rel_ev["reason"] == "operator_pause"

        # Verify agent_joined & task_claimed carry coding_agent enrichments
        join_evs = client.get("/api/events?since_id=0&event_type=agent_joined").json()["events"]
        assert any(e.get("handle") == "q-author" and e.get("coding_agent") == "Cursor" for e in join_evs)
        claim_evs = client.get("/api/events?since_id=0&event_type=task_claimed").json()["events"]
        assert any(
            e.get("agent_handle") == "q-author" and e.get("role") == "auditor" and e.get("task_title")
            for e in claim_evs
        )
    app.state.db.close()


def test_swarm_live_read_only_preview_mode_and_postgres_sql_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    db_file = tmp_path / "ro_live.db"
    setup_app = create_app(db_path=db_file, seed=True)
    with TestClient(setup_app) as client:
        _register(client, "preview-agent", coding_agent="Cursor")
    setup_app.state.db.close()

    monkeypatch.setenv("VERCEL_ENV", "preview")
    ro_app = create_app(db_path=db_file, seed=True)
    with TestClient(ro_app) as client:
        resp = client.get("/api/swarm/live")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total_agents"] == 1
        assert data["working_now"] == 1
        assert data["active_nodes"][0]["handle"] == "preview-agent"
    ro_app.state.db.close()

    # Verify Postgres translator handles window functions & EXISTS cleanly
    sample_sql = """
    SELECT agent_handle, message
    FROM (
        SELECT w.agent_handle, w.message,
               ROW_NUMBER() OVER (PARTITION BY w.agent_handle ORDER BY w.created_at DESC, w.id DESC) AS rn
        FROM whispers w
        WHERE w.agent_handle IN (?, ?)
          AND (w.finding_id IS NULL OR NOT EXISTS (SELECT 1 FROM findings qf WHERE qf.id = w.finding_id AND COALESCE(qf.quarantined, 0) = 1))
    ) ranked
    WHERE rn = 1
    """
    translated = translate_sql_for_postgres(sample_sql, parameterized=True)
    assert translated is not None
    assert translated.count("%s") == 2
    assert "?" not in translated
    assert "ROW_NUMBER() OVER" in translated


def test_csp_img_src_allows_github_avatars_in_app_and_vercel_json(tmp_path: Path) -> None:
    # 1. Verify FastAPI SecurityHeadersMiddleware CSP header on /api/swarm/live and /health
    app = create_app(db_path=tmp_path / "csp.db", seed=False)
    with TestClient(app) as client:
        resp = client.get("/api/swarm/live")
        assert resp.status_code == 200
        csp = resp.headers.get("content-security-policy", "")
        assert csp == SECURITY_CSP

        directives = _parse_csp_directives(csp)
        assert "https://github.com" in directives["img-src"]
        assert "https://avatars.githubusercontent.com" in directives["img-src"]
        # Strict isolation: GitHub origins MUST NOT leak into script-src, connect-src, or frame-src
        assert "https://github.com" not in directives["script-src"]
        assert "https://avatars.githubusercontent.com" not in directives["script-src"]
        assert "https://github.com" not in directives["connect-src"]
        assert "https://avatars.githubusercontent.com" not in directives["connect-src"]
        assert directives["object-src"] == ["'none'"]
        assert directives["frame-src"] == ["'none'"]
        assert directives["frame-ancestors"] == ["'none'"]
    app.state.db.close()

    # 2. Verify vercel.json CSP matches app.py SECURITY_CSP identically
    vercel_path = Path(__file__).resolve().parents[1] / "vercel.json"
    vercel_cfg = json.loads(vercel_path.read_text(encoding="utf-8"))
    root_headers = vercel_cfg["headers"][0]["headers"]
    csp_entries = [h["value"] for h in root_headers if h["key"].lower() == "content-security-policy"]
    assert len(csp_entries) == 1
    assert csp_entries[0] == SECURITY_CSP
