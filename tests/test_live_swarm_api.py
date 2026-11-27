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
        assert "s-maxage=10" in resp.headers.get("cache-control", "")
        assert "stale-while-revalidate=30" in resp.headers.get("cache-control", "")
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
        t_newest_join = _iso_offset(seconds=-45)  # inside 120s live window
        t_stale_hb = _iso_offset(minutes=-25)  # outside 120s live window
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

        # total_agents includes all rows; revoked and stale-heartbeat agents excluded from working_now
        assert snap["total_agents"] == 5
        assert snap["active_leases_count"] == 1  # only non-expired lease counts
        assert snap["working_now"] == 1  # ONLY newest-recent (45s hb); stalled mid-lease-holder excluded!

        node_handles = [n["handle"] for n in snap["active_nodes"]]
        assert "revoked-bot" not in node_handles
        assert node_handles[0] == "newest-recent"  # fresh heartbeat comes ahead of stalled lease holder
        assert set(node_handles[1:]) == {"mid-lease-holder", "expired-holder", "oldest-idle"}

        nodes = {n["handle"]: n for n in snap["active_nodes"]}
        assert nodes["mid-lease-holder"]["status"] == "idle"
        assert nodes["mid-lease-holder"]["role"] is None
        assert nodes["mid-lease-holder"]["progress_pct"] is None
        assert nodes["mid-lease-holder"]["current_task_id"] is None
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
        assert joins_by_handle["mid-lease-holder"]["is_working"] is False
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


def test_swarm_live_stalled_lease_downgrades_to_idle_and_recovers_on_heartbeat(tmp_path: Path) -> None:
    """Stalled agents holding unexpired 45m leases MUST downgrade to idle once heartbeat > 120s old."""
    assert LIVE_AGENT_WINDOW_SECONDS == 120
    app = create_app(db_path=tmp_path / "stalled_lease.db", seed=True)
    with TestClient(app) as client:
        stalled = _register(client, "stalled-auditor", coding_agent="Cursor")
        claim = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": stalled["api_token"]},
            json={"handle": stalled["handle"], "mode": "investigate"},
        )
        assert claim.status_code == 200, claim.text
        lease_id = claim.json()["lease_id"]
        task_id = claim.json()["task"]["id"]

        beat = client.post(
            "/api/desk/beat",
            headers={"X-Agent-Token": stalled["api_token"]},
            json={"lease_id": lease_id, "progress_pct": 20, "whisper": "Downloading dataset..."},
        )
        assert beat.status_code == 200, beat.text

        # Fresh heartbeat (< 120s): actively working with progress_pct=20
        snap_live = client.get("/api/swarm/live").json()
        assert snap_live["live_window_seconds"] == 120
        assert snap_live["working_now"] == 1
        node_live = snap_live["active_nodes"][0]
        assert node_live["handle"] == "stalled-auditor"
        assert node_live["status"] == "working"
        assert node_live["role"] == "auditor"
        assert node_live["progress_pct"] == 20
        assert node_live["current_task_id"] == task_id
        assert snap_live["recent_joins"][0]["is_working"] is True

        # Backdate heartbeat to 180s ago (> 120s) while the 45-minute lease is still unexpired
        stale_hb = _iso_offset(seconds=-180)
        with app.state.db.transaction() as conn:
            conn.execute(
                "UPDATE agents SET last_heartbeat_at = ? WHERE handle = ?",
                (stale_hb, stalled["handle"]),
            )

        snap_stalled = client.get("/api/swarm/live").json()
        assert snap_stalled["working_now"] == 0
        assert snap_stalled["active_leases_count"] == 1
        node_stalled = snap_stalled["active_nodes"][0]
        assert node_stalled["handle"] == "stalled-auditor"
        assert node_stalled["status"] == "idle"
        assert node_stalled["role"] is None
        assert node_stalled["progress_pct"] is None
        assert node_stalled["current_task_id"] is None
        assert node_stalled["current_task_title"] is None
        assert node_stalled["current_project_slug"] is None
        assert snap_stalled["recent_joins"][0]["is_working"] is False

        # A second freshly heartbeating agent is reported as working and sorted ahead of stalled agent
        fresh = _register(client, "fresh-auditor", coding_agent="Claude Code")
        claim2 = client.post(
            "/api/desk/next",
            headers={"X-Agent-Token": fresh["api_token"]},
            json={"handle": fresh["handle"], "mode": "investigate"},
        )
        assert claim2.status_code == 200, claim2.text
        beat2 = client.post(
            "/api/desk/beat",
            headers={"X-Agent-Token": fresh["api_token"]},
            json={"lease_id": claim2.json()["lease_id"], "progress_pct": 40},
        )
        assert beat2.status_code == 200, beat2.text

        snap_mixed = client.get("/api/swarm/live").json()
        assert snap_mixed["working_now"] == 1
        assert [n["handle"] for n in snap_mixed["active_nodes"]] == ["fresh-auditor", "stalled-auditor"]
        assert snap_mixed["active_nodes"][0]["status"] == "working"
        assert snap_mixed["active_nodes"][0]["progress_pct"] == 40
        assert snap_mixed["active_nodes"][1]["status"] == "idle"
        assert snap_mixed["active_nodes"][1]["progress_pct"] is None

        # Once stalled-auditor sends a new keepalive heartbeat, it immediately returns to working
        revive = client.post(
            "/api/desk/beat",
            headers={"X-Agent-Token": stalled["api_token"]},
            json={"lease_id": lease_id},
        )
        assert revive.status_code == 200, revive.text
        snap_revived = client.get("/api/swarm/live").json()
        assert snap_revived["working_now"] == 2
        revived_map = {n["handle"]: n for n in snap_revived["active_nodes"]}
        assert revived_map["stalled-auditor"]["status"] == "working"
        assert revived_map["stalled-auditor"]["progress_pct"] == 20
        assert revived_map["stalled-auditor"]["current_task_id"] == task_id
    app.state.db.close()


def test_runner_lease_keepalive_keeps_long_tasks_live_and_stops_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AgentRunner sends periodic bare keepalive beats during long tasks and stops before submit/release."""
    import threading
    import time

    import httpx

    from aiwatchdogs.cli.config import AgentConfig
    from aiwatchdogs.cli.runner import AgentRunner
    from aiwatchdogs.connectors.loader import LoadedDataset
    from aiwatchdogs.core.models import EngineRunResult

    events_lock = threading.Lock()
    recorded_calls: list[tuple[str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8")) if request.content else {}
        with events_lock:
            recorded_calls.append((request.url.path, body))
        if request.url.path == "/api/desk/beat":
            return httpx.Response(200, json={"status": "active", "progress_pct": body.get("progress_pct") or 15})
        if request.url.path == "/api/desk/submit":
            return httpx.Response(200, json={"status": "completed"})
        if request.url.path == "/api/desk/release":
            return httpx.Response(200, json={"status": "released"})
        return httpx.Response(404, json={"detail": "not found"})

    def fake_slow_load_dataset(*_args: Any, **_kwargs: Any) -> LoadedDataset:
        time.sleep(0.18)
        return LoadedDataset(
            records=[{"tender_id": "T1"}],
            raw_text="tender_id\nT1\n",
            sha256="a" * 64,
            byte_size=13,
            format="csv",
            is_demo_sample=False,
            source_ref="https://example.gov.in/slow.csv",
            pii_redactions=[],
        )

    def fake_run_engine(*_args: Any, **_kwargs: Any) -> EngineRunResult:
        return EngineRunResult(
            methodology_code="M1",
            records_scanned=1,
            monetary_audited_inr=0.0,
            leads=[],
            cleared_count=1,
            summary="Cleared 1 record.",
            script_hash="b" * 64,
        )

    monkeypatch.setattr("aiwatchdogs.cli.runner.load_dataset", fake_slow_load_dataset)
    monkeypatch.setattr("aiwatchdogs.cli.runner.run_engine", fake_run_engine)

    cfg = AgentConfig(
        handle="keepalive-bot",
        hub_url="https://hub.example.test",
        api_token="tok-test-123",
    )
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://hub.example.test")
    runner = AgentRunner(cfg, http_client=client, keepalive_interval=0.05)

    lease = {
        "lease_id": "lease-slow-1",
        "task": {
            "id": "task-slow-1",
            "task_type": "anomaly_scan",
            "mission_slug": "assam-ocds-single-bid",
            "methodology_code": "M1",
            "title": "Slow Gov PDF",
            "payload": {"dataset_url": "https://example.gov.in/slow.csv", "expected_sha256": "a" * 64},
        },
    }
    res = runner.execute_task(lease)
    assert res["status"] == "completed"
    assert res["outcome"] == "cleared"

    # Wait briefly to prove no keepalive fires after submit()
    time.sleep(0.10)

    with events_lock:
        calls = list(recorded_calls)
    submit_idx = next(i for i, (p, _) in enumerate(calls) if p == "/api/desk/submit")
    # Zero beats may arrive after submit
    assert all(p != "/api/desk/beat" for p, _ in calls[submit_idx + 1 :])
    # At least 2 bare keepalive beats (no whisper, no progress_pct) fired while download slept
    keepalive_beats = [
        body for p, body in calls[:submit_idx]
        if p == "/api/desk/beat" and "whisper" not in body and "progress_pct" not in body
    ]
    assert len(keepalive_beats) >= 2, f"Expected >=2 keepalive beats, got {calls}"

    # Verify error path stops keepalive cleanly before release()
    with events_lock:
        recorded_calls.clear()

    def failing_load(*_args: Any, **_kwargs: Any) -> LoadedDataset:
        time.sleep(0.08)
        raise RuntimeError("boom")

    monkeypatch.setattr("aiwatchdogs.cli.runner.load_dataset", failing_load)
    err_res = runner.execute_task(lease)
    assert err_res["status"] == "error"
    time.sleep(0.10)
    with events_lock:
        err_calls = list(recorded_calls)
    rel_idx = next(i for i, (p, _) in enumerate(err_calls) if p == "/api/desk/release")
    assert all(p != "/api/desk/beat" for p, _ in err_calls[rel_idx + 1 :])
    client.close()


def test_leaderboard_activity_telemetry_windows_and_36h_sparklines(tmp_path: Path) -> None:
    """Contributors API, /api/head, profile, and mission leaderboard expose 10m/1h windows & 36h sparklines."""
    app = create_app(db_path=tmp_path / "telemetry_lb.db", seed=True)
    with TestClient(app) as client:
        alpha = _register(client, "alpha-bot", coding_agent="Claude Code")
        idle_bot = _register(client, "quiet-bot", coding_agent="Cursor")

        # Seed deterministic score_events & completed tasks across sliding windows and 36h buckets
        t_5m = _iso_offset(minutes=-5)             # inside 10m, 1h, bucket 35
        t_30m = _iso_offset(minutes=-30)           # inside 1h, bucket 35 (NOT 10m)
        t_2h15m = _iso_offset(minutes=-135)        # bucket 33 (NOT 10m/1h)
        t_35h20m = _iso_offset(minutes=-(35 * 60 + 20))  # bucket 0 (oldest 36h slot)
        t_40h = _iso_offset(minutes=-(40 * 60))    # older than 36h (all-time only)

        with app.state.db.transaction() as conn:
            mission_row = conn.execute("SELECT id, slug FROM missions LIMIT 1").fetchone()
            assert mission_row is not None
            mission_id = str(mission_row["id"])
            mission_slug = str(mission_row["slug"])

            # Insert 5 completed tasks + matching score_events for alpha-bot
            samples = [
                ("t-5m", t_5m, 10),
                ("t-30m", t_30m, 25),
                ("t-2h", t_2h15m, 15),
                ("t-35h", t_35h20m, 40),
                ("t-40h", t_40h, 100),
            ]
            for tid, ts, pts in samples:
                conn.execute(
                    """
                    INSERT INTO tasks (
                        id, mission_id, mission_slug, methodology_code, task_type,
                        title, instructions, status, claimed_by_agent_id, claimed_by_handle,
                        created_at, updated_at, completed_at
                    )
                    VALUES (?, ?, ?, 'M1', 'anomaly_scan', ?, 'run check', 'completed', ?, ?, ?, ?, ?)
                    """,
                    (
                        tid,
                        mission_id,
                        mission_slug,
                        f"Task {tid}",
                        alpha["agent_id"],
                        alpha["handle"],
                        ts,
                        ts,
                        ts,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO score_events (id, agent_id, agent_handle, event_type, points, ref_id, created_at)
                    VALUES (?, ?, ?, 'cleared_scan', ?, ?, ?)
                    """,
                    (f"se-{tid}", alpha["agent_id"], alpha["handle"], pts, tid, ts),
                )
            conn.execute(
                "UPDATE agents SET tasks_completed = 5, compute_score = 190 WHERE handle = ?",
                (alpha["handle"],),
            )

        def _assert_alpha(rec: dict[str, Any]) -> None:
            assert rec["score_10m"] == 10
            assert rec["score_1h"] == 35
            assert rec["hourly_score"] == 35
            assert rec["checks_10m"] == 1
            assert rec["checks_1h"] == 2
            assert rec["checks_total"] == 5
            pts36 = rec["hourly_points_36h"]
            chk36 = rec["hourly_checks_36h"]
            assert isinstance(pts36, list) and len(pts36) == 36
            assert isinstance(chk36, list) and len(chk36) == 36
            assert pts36[35] == 35
            assert pts36[33] == 15
            assert pts36[0] == 40
            assert sum(pts36) == 90  # 40h-old 100pts excluded from 36h sparkline
            assert chk36[35] == 2
            assert chk36[33] == 1
            assert chk36[0] == 1
            assert sum(chk36) == 4  # 40h-old task excluded from 36h sparkline

        # 1. GET /api/contributors
        contrib_resp = client.get("/api/contributors").json()
        by_handle = {c["handle"]: c for c in contrib_resp["contributors"]}
        _assert_alpha(by_handle[alpha["handle"]])
        quiet = by_handle[idle_bot["handle"]]
        assert quiet["score_10m"] == 0
        assert quiet["score_1h"] == 0
        assert quiet["checks_10m"] == 0
        assert quiet["checks_1h"] == 0
        assert quiet["checks_total"] == 0
        assert quiet["hourly_points_36h"] == [0] * 36
        assert quiet["hourly_checks_36h"] == [0] * 36

        # 2. GET /api/head top_contributors
        head_resp = client.get("/api/head").json()
        head_by_handle = {c["handle"]: c for c in head_resp["top_contributors"]}
        _assert_alpha(head_by_handle[alpha["handle"]])

        # 3. GET /api/contributors/{handle}
        prof_resp = client.get(f"/api/contributors/{alpha['handle']}").json()
        _assert_alpha(prof_resp["agent"])
        _assert_alpha(prof_resp)

        # 4. GET /api/missions/{slug} project leaderboard
        mission_resp = client.get(f"/api/missions/{mission_slug}").json()
        proj_by_handle = {c["handle"]: c for c in mission_resp["contributors_all"]}
        _assert_alpha(proj_by_handle[alpha["handle"]])
        proj_hr_by_handle = {c["handle"]: c for c in mission_resp["contributors_hour"]}
        _assert_alpha(proj_hr_by_handle[alpha["handle"]])
    app.state.db.close()
