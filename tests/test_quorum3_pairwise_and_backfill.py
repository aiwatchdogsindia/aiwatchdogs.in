"""Tests for 3-of-3 pairwise cross-harness/cross-model quorum and retroactive migration v14."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aiwatchdogs.hub.app import create_app
from aiwatchdogs.hub.database import (
    LATEST_SCHEMA_VERSION,
    Database,
    _migrate_v14_quorum3_cross_harness_backfill,
    dumps_json,
)

ALL_PASS_CHECKS: dict[str, str] = {
    "bytes_digest": "pass",
    "deterministic_reproduction": "pass",
    "metric_tolerance": "pass",
    "neutrality_privacy": "pass",
}


def _register(
    client: TestClient,
    handle: str,
    *,
    coding_agent: str = "",
    coding_agent_model: str = "",
    provider: str = "rule-engine",
    model: str = "deterministic-python-v1",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "handle": handle,
        "display_name": handle,
        "provider": provider,
        "model": model,
        "mode": "auto",
    }
    if coding_agent:
        payload["coding_agent"] = coding_agent
    if coding_agent_model:
        payload["coding_agent_model"] = coding_agent_model
    resp = client.post("/api/agents/register", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _submit_finding(client: TestClient, author: dict[str, Any]) -> tuple[str, str, list[str]]:
    claim = client.post(
        "/api/desk/next",
        headers={"X-Agent-Token": author["api_token"]},
        json={
            "handle": author["handle"],
            "mode": "investigate",
            "preferred_mission": "assam-ocds-single-bid",
        },
    )
    assert claim.status_code == 200, claim.text
    c_data = claim.json()
    expected_sha = c_data["task"]["payload"]["expected_sha256"]
    sub = client.post(
        "/api/desk/submit",
        headers={"X-Agent-Token": author["api_token"]},
        json={
            "lease_id": c_data["lease_id"],
            "outcome": "finding",
            "finding": {
                "title": "Public Record Anomaly: Single-bidder share >35% in Assam OCDS",
                "summary": "Deterministic scan flagged 81 of 228 tenders (35.5%) with a single bidder.",
                "methodology_code": "M1",
                "entity_name": "Health Department, Assam",
                "jurisdiction": "State:Assam",
                "fiscal_year": "FY2016-22",
                "monetary_exposure_inr": 604_300_000.0,
                "severity": "high",
                "anomaly_metrics": {"numerator": 81, "denominator": 228, "single_bid_rate_pct": 35.53},
                "rule_triggered": "GFR-2017-R166",
                "artifact_sha256": expected_sha,
                "citations": [{"row_id": "OCID-1"}],
                "benign_explanations": ["Specialized hospital equipment may require OEM procurement under GFR Rule 166."],
                "suggested_authority": "Health and Family Welfare Department, Government of Assam",
            },
        },
    )
    assert sub.status_code == 200, sub.text
    body = sub.json()
    return body["finding_id"], expected_sha, list(body["peer_verify_task_ids"])


def _confirm_payload(fid: str, sha: str, *, review_type: str = "computational_reproduction") -> dict[str, Any]:
    return {
        "finding_id": fid,
        "verdict": "confirm",
        "review_type": review_type,
        "checks": dict(ALL_PASS_CHECKS),
        "reproduced_metrics": {"artifact_sha256": sha, "numerator": 81, "denominator": 228},
        "notes": "Independently reproduced digest and metrics.",
    }


def test_new_finding_spawns_three_peer_verify_tasks_and_promotes_on_third_confirm(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "quorum3.db", seed=True)
    try:
        with TestClient(app) as client:
            author = _register(client, "q3-author", coding_agent="Claude Code", coding_agent_model="claude-opus-4-6")
            v1 = _register(client, "q3-v1", coding_agent="Codex", coding_agent_model="gpt-5.4")
            v2 = _register(client, "q3-v2", coding_agent="Antigravity", coding_agent_model="gemini-3.1-pro")
            v3 = _register(client, "q3-v3", coding_agent="Cursor", coding_agent_model="deepseek-r1")

            fid, sha, pv_ids = _submit_finding(client, author)
            assert pv_ids == [f"tsk-pv-{fid}-1", f"tsk-pv-{fid}-2", f"tsk-pv-{fid}-3"]

            detail0 = client.get(f"/api/findings/{fid}").json()
            assert detail0["required_quorum"] == 3
            assert detail0["reproduction_summary"]["required_quorum"] == 3
            assert detail0["status"] == "peer_review"

            # 1st and 2nd confirms leave the finding in peer_review
            for expected_cnt, peer in ((1, v1), (2, v2)):
                c = client.post(
                    "/api/desk/next",
                    headers={"X-Agent-Token": peer["api_token"]},
                    json={"handle": peer["handle"], "mode": "verify"},
                )
                assert c.status_code == 200, c.text
                r = client.post(
                    "/api/desk/submit",
                    headers={"X-Agent-Token": peer["api_token"]},
                    json={
                        "lease_id": c.json()["lease_id"],
                        "outcome": "verification",
                        "verification": _confirm_payload(fid, sha),
                    },
                )
                assert r.status_code == 200, r.text
                rd = r.json()
                assert rd["finding_status"] == "peer_review"
                assert rd["confirm_count"] == expected_cnt
                assert rd["promoted_to_verified"] is False

            # 3rd confirm promotes to verified
            c3 = client.post(
                "/api/desk/next",
                headers={"X-Agent-Token": v3["api_token"]},
                json={"handle": v3["handle"], "mode": "verify"},
            )
            assert c3.status_code == 200, c3.text
            r3 = client.post(
                "/api/desk/submit",
                headers={"X-Agent-Token": v3["api_token"]},
                json={
                    "lease_id": c3.json()["lease_id"],
                    "outcome": "verification",
                    "verification": _confirm_payload(fid, sha),
                },
            )
            assert r3.status_code == 200, r3.text
            rd3 = r3.json()
            assert rd3["finding_status"] == "verified"
            assert rd3["confirm_count"] == 3
            assert rd3["verification_score"] == pytest.approx(1.0)
            assert rd3["promoted_to_verified"] is True
            assert rd3["rti_draft_id"] == f"rti-{fid}"

            with app.state.db.read() as conn:
                ver_row = conn.execute(
                    "SELECT change_note FROM rti_draft_versions WHERE finding_id = ?", (fid,)
                ).fetchone()
                assert ver_row is not None
                assert "3-of-3" in ver_row["change_note"]
    finally:
        app.state.db.close()


def test_pairwise_independence_across_author_and_all_three_reviewers_plus_active_lease_guard(
    tmp_path: Path,
) -> None:
    """All 4 participants (Author + V1 + V2 + V3) must be pairwise distinct in handle, coding_agent, and model."""
    app = create_app(db_path=tmp_path / "pairwise.db", seed=True)
    try:
        with TestClient(app) as client:
            # All agents share provider='rule-engine', model='deterministic-python-v1' (must NOT be used for checks!)
            author = _register(client, "pw-author", coding_agent="Claude Code", coding_agent_model="claude-opus-4-6")
            same_author_har = _register(client, "pw-same-ahar", coding_agent="claude code", coding_agent_model="gpt-5.4")
            same_author_mod = _register(client, "pw-same-amod", coding_agent="Codex", coding_agent_model="CLAUDE-OPUS-4-6")
            missing_both = _register(client, "pw-no-attr")
            missing_model = _register(client, "pw-no-mod", coding_agent="Aider")
            missing_harness = _register(client, "pw-no-har", coding_agent_model="llama-4")

            v1 = _register(client, "pw-v1", coding_agent="Codex", coding_agent_model="gpt-5.4")
            same_v1_har = _register(client, "pw-same-v1har", coding_agent="Codex", coding_agent_model="gemini-3.1-pro")
            same_v1_mod = _register(client, "pw-same-v1mod", coding_agent="Antigravity", coding_agent_model="gpt-5.4")

            v2 = _register(client, "pw-v2", coding_agent="Antigravity", coding_agent_model="gemini-3.1-pro")
            same_v2_har = _register(client, "pw-same-v2har", coding_agent="Antigravity", coding_agent_model="deepseek-r1")
            same_v2_mod = _register(client, "pw-same-v2mod", coding_agent="Cursor", coding_agent_model="gemini-3.1-pro")

            v3 = _register(client, "pw-v3", coding_agent="Cursor", coding_agent_model="deepseek-r1")

            fid, sha, pv_ids = _submit_finding(client, author)

            # 1. Blocked against author on auto-next (204) AND explicit task_id (409)
            for blocked in (same_author_har, same_author_mod, missing_both, missing_model, missing_harness):
                r_auto = client.post(
                    "/api/desk/next",
                    headers={"X-Agent-Token": blocked["api_token"]},
                    json={"handle": blocked["handle"], "mode": "verify"},
                )
                assert r_auto.status_code == 204, f"{blocked['handle']} should be skipped on auto-next"
                r_exp = client.post(
                    "/api/desk/next",
                    headers={"X-Agent-Token": blocked["api_token"]},
                    json={"handle": blocked["handle"], "task_id": pv_ids[0]},
                )
                assert r_exp.status_code == 409, f"{blocked['handle']} should get 409 on explicit claim"

            # 2. V1 claims slot #1 -> ACTIVE LEASE COLLISION GUARD blocks agents matching V1's harness or model!
            c1 = client.post(
                "/api/desk/next",
                headers={"X-Agent-Token": v1["api_token"]},
                json={"handle": v1["handle"], "mode": "verify"},
            )
            assert c1.status_code == 200, c1.text
            assert c1.json()["task"]["id"] == pv_ids[0]

            for lease_conflict in (same_v1_har, same_v1_mod):
                r_auto = client.post(
                    "/api/desk/next",
                    headers={"X-Agent-Token": lease_conflict["api_token"]},
                    json={"handle": lease_conflict["handle"], "mode": "verify"},
                )
                assert r_auto.status_code == 204, f"{lease_conflict['handle']} must be blocked by V1's active lease"
                r_exp = client.post(
                    "/api/desk/next",
                    headers={"X-Agent-Token": lease_conflict["api_token"]},
                    json={"handle": lease_conflict["handle"], "task_id": pv_ids[1]},
                )
                assert r_exp.status_code == 409

            # V2 (distinct from Author AND active-lease V1) CAN claim slot #2 concurrently!
            c2 = client.post(
                "/api/desk/next",
                headers={"X-Agent-Token": v2["api_token"]},
                json={"handle": v2["handle"], "mode": "verify"},
            )
            assert c2.status_code == 200, c2.text
            assert c2.json()["task"]["id"] == pv_ids[1]

            # Active lease guard also blocks V2 conflicts while V2 is still claimed
            for v2_conflict in (same_v2_har, same_v2_mod):
                assert (
                    client.post(
                        "/api/desk/next",
                        headers={"X-Agent-Token": v2_conflict["api_token"]},
                        json={"handle": v2_conflict["handle"], "mode": "verify"},
                    ).status_code
                    == 204
                )
                assert (
                    client.post(
                        "/api/desk/next",
                        headers={"X-Agent-Token": v2_conflict["api_token"]},
                        json={"handle": v2_conflict["handle"], "task_id": pv_ids[2]},
                    ).status_code
                    == 409
                )

            # 3. V1 and V2 submit confirmations -> still blocked for any V1/V2 harness or model conflicts
            for peer, lease_id in ((v1, c1.json()["lease_id"]), (v2, c2.json()["lease_id"])):
                r_sub = client.post(
                    "/api/desk/submit",
                    headers={"X-Agent-Token": peer["api_token"]},
                    json={
                        "lease_id": lease_id,
                        "outcome": "verification",
                        "verification": _confirm_payload(fid, sha),
                    },
                )
                assert r_sub.status_code == 200, r_sub.text

            for post_conflict in (same_v1_har, same_v1_mod, same_v2_har, same_v2_mod):
                assert (
                    client.post(
                        "/api/desk/next",
                        headers={"X-Agent-Token": post_conflict["api_token"]},
                        json={"handle": post_conflict["handle"], "mode": "verify"},
                    ).status_code
                    == 204
                )
                assert (
                    client.post(
                        "/api/desk/next",
                        headers={"X-Agent-Token": post_conflict["api_token"]},
                        json={"handle": post_conflict["handle"], "task_id": pv_ids[2]},
                    ).status_code
                    == 409
                )

            # 4. Defense-in-depth on _handle_submit_verification: even if a conflicting agent acquires a
            #    direct lease (e.g. changed harness/model after claiming), submit MUST reject with 409!
            c3 = client.post(
                "/api/desk/next",
                headers={"X-Agent-Token": v3["api_token"]},
                json={"handle": v3["handle"], "mode": "verify"},
            )
            assert c3.status_code == 200, c3.text
            c3_lease = c3.json()["lease_id"]

            # Simulate V3 mutating its harness to match V2 before submit -> 409 Conflict!
            with app.state.db.transaction() as conn:
                conn.execute("UPDATE agents SET coding_agent = 'Antigravity' WHERE handle = ?", (v3["handle"],))
            r_blocked_sub = client.post(
                "/api/desk/submit",
                headers={"X-Agent-Token": v3["api_token"]},
                json={
                    "lease_id": c3_lease,
                    "outcome": "verification",
                    "verification": _confirm_payload(fid, sha),
                },
            )
            assert r_blocked_sub.status_code == 409

            # Simulate V3 mutating its model to match Author before submit -> 409 Conflict!
            with app.state.db.transaction() as conn:
                conn.execute(
                    "UPDATE agents SET coding_agent = 'Cursor', coding_agent_model = 'claude-opus-4-6' WHERE handle = ?",
                    (v3["handle"],),
                )
            r_blocked_mod = client.post(
                "/api/desk/submit",
                headers={"X-Agent-Token": v3["api_token"]},
                json={
                    "lease_id": c3_lease,
                    "outcome": "verification",
                    "verification": _confirm_payload(fid, sha),
                },
            )
            assert r_blocked_mod.status_code == 409

            # Restore V3's distinct harness & model -> succeeds and promotes to verified!
            with app.state.db.transaction() as conn:
                conn.execute(
                    "UPDATE agents SET coding_agent = 'Cursor', coding_agent_model = 'deepseek-r1' WHERE handle = ?",
                    (v3["handle"],),
                )
            r_ok = client.post(
                "/api/desk/submit",
                headers={"X-Agent-Token": v3["api_token"]},
                json={
                    "lease_id": c3_lease,
                    "outcome": "verification",
                    "verification": _confirm_payload(fid, sha),
                },
            )
            assert r_ok.status_code == 200, r_ok.text
            assert r_ok.json()["finding_status"] == "verified"
            assert r_ok.json()["confirm_count"] == 3
    finally:
        app.state.db.close()


def test_unattributed_legacy_finding_still_verifies_with_three_distinct_handles(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "unattributed.db", seed=True)
    try:
        with TestClient(app) as client:
            author = _register(client, "leg-author")
            v1 = _register(client, "leg-v1")
            v2 = _register(client, "leg-v2")
            v3 = _register(client, "leg-v3")

            fid, sha, _ = _submit_finding(client, author)
            for idx, peer in enumerate((v1, v2, v3), start=1):
                c = client.post(
                    "/api/desk/next",
                    headers={"X-Agent-Token": peer["api_token"]},
                    json={"handle": peer["handle"], "mode": "verify"},
                )
                assert c.status_code == 200, c.text
                r = client.post(
                    "/api/desk/submit",
                    headers={"X-Agent-Token": peer["api_token"]},
                    json={
                        "lease_id": c.json()["lease_id"],
                        "outcome": "verification",
                        "verification": _confirm_payload(fid, sha),
                    },
                )
                assert r.status_code == 200, r.text
                if idx < 3:
                    assert r.json()["finding_status"] == "peer_review"
                else:
                    assert r.json()["finding_status"] == "verified"
                    assert r.json()["confirm_count"] == 3
    finally:
        app.state.db.close()


def test_split_vote_2_confirms_1_dispute_waits_for_slot3_then_adjudicator_promotes(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "split3.db", seed=True)
    try:
        with TestClient(app) as client:
            author = _register(client, "sp-author", coding_agent="Claude Code", coding_agent_model="claude-opus-4-6")
            v1 = _register(client, "sp-v1", coding_agent="Codex", coding_agent_model="gpt-5.4")
            v2 = _register(client, "sp-v2", coding_agent="Antigravity", coding_agent_model="gemini-3.1-pro")
            v3 = _register(client, "sp-v3", coding_agent="Cursor", coding_agent_model="deepseek-r1")
            v4 = _register(client, "sp-v4", coding_agent="Windsurf", coding_agent_model="qwen3-coder")

            fid, sha, _ = _submit_finding(client, author)

            # V1 confirms, V2 disputes while slot #3 is still open -> NO premature adjudication!
            c1 = client.post("/api/desk/next", headers={"X-Agent-Token": v1["api_token"]}, json={"mode": "verify"}).json()
            r1 = client.post(
                "/api/desk/submit",
                headers={"X-Agent-Token": v1["api_token"]},
                json={"lease_id": c1["lease_id"], "outcome": "verification", "verification": _confirm_payload(fid, sha)},
            ).json()
            assert r1["finding_status"] == "peer_review" and r1["adjudication_queued"] is False

            c2 = client.post("/api/desk/next", headers={"X-Agent-Token": v2["api_token"]}, json={"mode": "verify"}).json()
            r2 = client.post(
                "/api/desk/submit",
                headers={"X-Agent-Token": v2["api_token"]},
                json={
                    "lease_id": c2["lease_id"],
                    "outcome": "verification",
                    "verification": {
                        "finding_id": fid,
                        "verdict": "dispute",
                        "checks": {"bytes_digest": "pass", "metric_tolerance": "fail"},
                        "notes": "Disputing metric match.",
                    },
                },
            ).json()
            assert r2["finding_status"] == "peer_review"
            assert r2["adjudication_queued"] is False  # slot #3 is still open!

            # V3 confirms slot #3 -> 2C/1D (0.667 < 0.75), open_other == 0 -> queues tsk-adj-{fid}-3!
            c3 = client.post("/api/desk/next", headers={"X-Agent-Token": v3["api_token"]}, json={"mode": "verify"}).json()
            r3 = client.post(
                "/api/desk/submit",
                headers={"X-Agent-Token": v3["api_token"]},
                json={"lease_id": c3["lease_id"], "outcome": "verification", "verification": _confirm_payload(fid, sha)},
            ).json()
            assert r3["finding_status"] == "peer_review"
            assert r3["adjudication_queued"] is True
            assert r3["adjudication_task_id"] == f"tsk-adj-{fid}-3"

            # V4 claims adjudication tie-breaker and confirms -> 3C/1D (0.75) promotes to verified!
            c4 = client.post("/api/desk/next", headers={"X-Agent-Token": v4["api_token"]}, json={"mode": "verify"}).json()
            assert c4["task"]["id"] == f"tsk-adj-{fid}-3"
            assert c4["role"]["name"] == "adjudicator"
            r4 = client.post(
                "/api/desk/submit",
                headers={"X-Agent-Token": v4["api_token"]},
                json={
                    "lease_id": c4["lease_id"],
                    "outcome": "verification",
                    "verification": _confirm_payload(fid, sha, review_type="adjudication"),
                },
            ).json()
            assert r4["finding_status"] == "verified"
            assert r4["confirm_count"] == 3
            assert r4["dispute_count"] == 1
            assert r4["verification_score"] == pytest.approx(0.75)
            assert r4["promoted_to_verified"] is True
    finally:
        app.state.db.close()


def test_migration_v14_retroactive_backfill_idempotency_and_postgres_sql(tmp_path: Path) -> None:
    """Verify migration v14 moves <3 pairwise-independent verified findings back to peer_review and queues exact slots."""
    db_path = tmp_path / "backfill.db"
    app = create_app(db_path=db_path, seed=True)
    try:
        db = app.state.db
        assert db.schema_version() == LATEST_SCHEMA_VERSION
        now = "2026-11-26T10:00:00Z"

        with db.transaction() as conn:
            # Register synthetic author + verifiers
            agents_seed = [
                ("agt-a", "bf-author", "Claude Code", "claude-opus-4-6"),
                ("agt-v1", "bf-v1", "Codex", "gpt-5.4"),
                ("agt-v2", "bf-v2", "Antigravity", "gemini-3.1-pro"),
                ("agt-v3", "bf-v3", "Cursor", "deepseek-r1"),
                ("agt-dup-har", "bf-dup-har", "Codex", "gpt-4.1"),
                ("agt-same-auth", "bf-same-auth", "Claude Code", "claude-sonnet-4-6"),
            ]
            for aid, handle, har, mod in agents_seed:
                conn.execute(
                    """
                    INSERT INTO agents (
                        id, handle, display_name, provider, model, api_token_hash,
                        coding_agent, coding_agent_model, joined_at, last_heartbeat_at
                    ) VALUES (?, ?, ?, 'rule-engine', 'deterministic-python-v1', ?, ?, ?, ?, ?)
                    """,
                    (aid, handle, handle, f"tok-{aid}", har, mod, now, now),
                )

            def _insert_finding(fid: str, status: str, *, req_q: int = 2, quarantined: int = 0) -> None:
                conn.execute(
                    """
                    INSERT INTO findings (
                        id, mission_id, mission_slug, pillar, title, summary, methodology_code,
                        entity_name, jurisdiction, fiscal_year, monetary_exposure_inr, severity,
                        anomaly_metrics_json, rule_triggered, source_url, artifact_sha256,
                        submitted_by, submitted_by_agent_id, submitted_coding_agent, submitted_coding_agent_model,
                        status, quarantined, verification_score, confirm_count, dispute_count,
                        required_quorum, created_at, verified_at
                    ) VALUES (
                        ?, 'mission-m1', 'assam-ocds-single-bid', 'procurement', ?, 'Summary', 'M1',
                        'Dept', 'State:Assam', 'FY2021-22', 10000000.0, 'high',
                        '{"numerator":10,"denominator":20}', 'M1', 'https://example.gov.in', 'deadbeef',
                        'bf-author', 'agt-a', 'Claude Code', 'claude-opus-4-6',
                        ?, ?, 1.0, 2, 0, ?, ?, ?
                    )
                    """,
                    (
                        fid,
                        f"Finding {fid}",
                        status,
                        quarantined,
                        req_q,
                        now,
                        now if status in ("verified", "rti_filed") else None,
                    ),
                )

            def _insert_pv_task(tid: str, fid: str, status: str = "completed") -> None:
                payload = dumps_json({"finding_id": fid, "methodology_code": "M1", "expected_sha256": "deadbeef"})
                conn.execute(
                    """
                    INSERT INTO tasks (
                        id, mission_id, mission_slug, methodology_code, task_type, title,
                        instructions, payload_json, target_finding_id, excluded_handles_json,
                        status, dataset_key, financial_quantity_type, created_at, updated_at
                    ) VALUES (
                        ?, 'mission-m1', 'assam-ocds-single-bid', 'M1', 'peer_verify',
                        'Peer Verify (1/2): Template', 'Verify template', ?, ?, '["bf-author"]',
                        ?, 'ds-m1', 'tender_estimates', ?, ?
                    )
                    """,
                    (tid, payload, fid, status, now, now),
                )

            def _insert_verif(vid: str, fid: str, aid: str, handle: str, har: str, mod: str, ts: str) -> None:
                conn.execute(
                    """
                    INSERT INTO peer_verifications (
                        id, finding_id, verifier_node_id, contributor_id, verifier_handle,
                        provider, model_id, review_type, verdict, checks_json,
                        reproduced_metrics_json, manifest_json, coding_agent, coding_agent_model, created_at
                    ) VALUES (?, ?, ?, ?, ?, 'rule-engine', 'v1', 'computational_reproduction', 'confirm',
                              ?, '{"numerator":10}', '{}', ?, ?, ?)
                    """,
                    (vid, fid, aid, handle, handle, dumps_json(ALL_PASS_CHECKS), har, mod, ts),
                )

            # A: verified with 2 pairwise-independent confirms -> peer_review + 1 new task (-3)
            _insert_finding("fnd-bf-a", "verified")
            _insert_pv_task("tsk-pv-fnd-bf-a-1", "fnd-bf-a", "completed")
            _insert_pv_task("tsk-pv-fnd-bf-a-2", "fnd-bf-a", "completed")
            _insert_verif("pv-a1", "fnd-bf-a", "agt-v1", "bf-v1", "Codex", "gpt-5.4", "2026-11-26T10:01:00Z")
            _insert_verif("pv-a2", "fnd-bf-a", "agt-v2", "bf-v2", "Antigravity", "gemini-3.1-pro", "2026-11-26T10:02:00Z")

            # B: verified with 3 pairwise-independent confirms -> stays verified, 0 new tasks
            _insert_finding("fnd-bf-b", "verified")
            _insert_pv_task("tsk-pv-fnd-bf-b-1", "fnd-bf-b", "completed")
            _insert_pv_task("tsk-pv-fnd-bf-b-2", "fnd-bf-b", "completed")
            _insert_pv_task("tsk-pv-fnd-bf-b-3", "fnd-bf-b", "completed")
            _insert_verif("pv-b1", "fnd-bf-b", "agt-v1", "bf-v1", "Codex", "gpt-5.4", "2026-11-26T10:01:00Z")
            _insert_verif("pv-b2", "fnd-bf-b", "agt-v2", "bf-v2", "Antigravity", "gemini-3.1-pro", "2026-11-26T10:02:00Z")
            _insert_verif("pv-b3", "fnd-bf-b", "agt-v3", "bf-v3", "Cursor", "deepseek-r1", "2026-11-26T10:03:00Z")

            # C: verified with 2 confirms sharing harness 'Codex' -> valid=1 -> peer_review + 2 new tasks (-3, -4)
            _insert_finding("fnd-bf-c", "verified")
            _insert_pv_task("tsk-pv-fnd-bf-c-1", "fnd-bf-c", "completed")
            _insert_pv_task("tsk-pv-fnd-bf-c-2", "fnd-bf-c", "completed")
            _insert_verif("pv-c1", "fnd-bf-c", "agt-v1", "bf-v1", "Codex", "gpt-5.4", "2026-11-26T10:01:00Z")
            _insert_verif("pv-c2", "fnd-bf-c", "agt-dup-har", "bf-dup-har", "Codex", "gpt-4.1", "2026-11-26T10:02:00Z")

            # D: verified with 1 confirm matching author harness 'Claude Code' -> valid=0 -> peer_review + 3 new tasks (-3, -4, -5)
            _insert_finding("fnd-bf-d", "verified")
            _insert_pv_task("tsk-pv-fnd-bf-d-1", "fnd-bf-d", "completed")
            _insert_pv_task("tsk-pv-fnd-bf-d-2", "fnd-bf-d", "completed")
            _insert_verif("pv-d1", "fnd-bf-d", "agt-same-auth", "bf-same-auth", "Claude Code", "claude-sonnet-4-6", "2026-11-26T10:01:00Z")

            # E: peer_review with 1 valid confirm and 1 already-queued task (-2) -> needs only 1 new task (-3)
            _insert_finding("fnd-bf-e", "peer_review")
            _insert_pv_task("tsk-pv-fnd-bf-e-1", "fnd-bf-e", "completed")
            _insert_pv_task("tsk-pv-fnd-bf-e-2", "fnd-bf-e", "queued")
            _insert_verif("pv-e1", "fnd-bf-e", "agt-v1", "bf-v1", "Codex", "gpt-5.4", "2026-11-26T10:01:00Z")

            # F: rti_filed -> untouched!
            _insert_finding("fnd-bf-f", "rti_filed")
            # G: quarantined -> untouched!
            _insert_finding("fnd-bf-g", "quarantined", quarantined=1)
            # H: verified with NO existing peer_verify task rows (fallback payload path) -> 3 new tasks (-1..-3)
            _insert_finding("fnd-bf-h", "verified")

            # Put a stale summary_cache entry to ensure migration invalidates it
            conn.execute(
                "INSERT INTO summary_cache (cache_key, version_stamp, payload_json, updated_at) VALUES ('head', 'v1', '{}', ?)",
                (now,),
            )

            # Run migration v14!
            _migrate_v14_quorum3_cross_harness_backfill(conn, is_postgres=False)

        def _check_state() -> None:
            with db.read() as conn:
                rows = {
                    r["id"]: dict(r)
                    for r in conn.execute(
                        "SELECT id, status, confirm_count, required_quorum, verified_at FROM findings WHERE id LIKE 'fnd-bf-%'"
                    ).fetchall()
                }
                assert rows["fnd-bf-a"]["status"] == "peer_review"
                assert rows["fnd-bf-a"]["confirm_count"] == 2
                assert rows["fnd-bf-a"]["required_quorum"] == 3
                assert rows["fnd-bf-a"]["verified_at"] is None

                assert rows["fnd-bf-b"]["status"] == "verified"
                assert rows["fnd-bf-b"]["confirm_count"] == 3
                assert rows["fnd-bf-b"]["required_quorum"] == 3
                assert rows["fnd-bf-b"]["verified_at"] == now

                assert rows["fnd-bf-c"]["status"] == "peer_review"
                assert rows["fnd-bf-c"]["confirm_count"] == 1
                assert rows["fnd-bf-c"]["required_quorum"] == 3

                assert rows["fnd-bf-d"]["status"] == "peer_review"
                assert rows["fnd-bf-d"]["confirm_count"] == 0
                assert rows["fnd-bf-d"]["required_quorum"] == 3

                assert rows["fnd-bf-e"]["status"] == "peer_review"
                assert rows["fnd-bf-e"]["confirm_count"] == 1
                assert rows["fnd-bf-e"]["required_quorum"] == 3

                assert rows["fnd-bf-f"]["status"] == "rti_filed"
                assert rows["fnd-bf-f"]["required_quorum"] == 2  # untouched
                assert rows["fnd-bf-g"]["status"] == "quarantined"

                assert rows["fnd-bf-h"]["status"] == "peer_review"
                assert rows["fnd-bf-h"]["confirm_count"] == 0

                def _queued_ids(fid: str) -> list[str]:
                    return [
                        r["id"]
                        for r in conn.execute(
                            "SELECT id FROM tasks WHERE target_finding_id = ? AND status = 'queued' ORDER BY id ASC",
                            (fid,),
                        ).fetchall()
                    ]

                assert _queued_ids("fnd-bf-a") == ["tsk-pv-fnd-bf-a-3"]
                assert _queued_ids("fnd-bf-b") == []
                assert _queued_ids("fnd-bf-c") == ["tsk-pv-fnd-bf-c-3", "tsk-pv-fnd-bf-c-4"]
                assert _queued_ids("fnd-bf-d") == ["tsk-pv-fnd-bf-d-3", "tsk-pv-fnd-bf-d-4", "tsk-pv-fnd-bf-d-5"]
                assert _queued_ids("fnd-bf-e") == ["tsk-pv-fnd-bf-e-2", "tsk-pv-fnd-bf-e-3"]
                assert _queued_ids("fnd-bf-h") == ["tsk-pv-fnd-bf-h-1", "tsk-pv-fnd-bf-h-2", "tsk-pv-fnd-bf-h-3"]

                # Check cloned excluded_handles_json & payload_json
                t_a3 = conn.execute(
                    "SELECT payload_json, excluded_handles_json, dataset_key FROM tasks WHERE id = 'tsk-pv-fnd-bf-a-3'"
                ).fetchone()
                assert json.loads(t_a3["excluded_handles_json"]) == ["bf-author", "bf-v1", "bf-v2"]
                assert json.loads(t_a3["payload_json"])["expected_sha256"] == "deadbeef"
                assert t_a3["dataset_key"] == "ds-m1"

                # Check fallback payload for fnd-bf-h
                t_h1 = conn.execute("SELECT payload_json FROM tasks WHERE id = 'tsk-pv-fnd-bf-h-1'").fetchone()
                assert json.loads(t_h1["payload_json"])["finding_id"] == "fnd-bf-h"

                # Denormalized counters recomputed (only fnd-bf-b + fnd-bf-f count as verified/rti_filed)
                m_cnt = conn.execute(
                    "SELECT verified_findings_count FROM missions WHERE id = 'mission-m1'"
                ).fetchone()["verified_findings_count"]
                assert m_cnt == 2
                a_cnt = conn.execute(
                    "SELECT findings_verified FROM agents WHERE id = 'agt-a'"
                ).fetchone()["findings_verified"]
                assert a_cnt == 2
                cache_cnt = conn.execute("SELECT COUNT(*) AS c FROM summary_cache").fetchone()["c"]
                assert cache_cnt == 0

        _check_state()

        # Idempotency: running migration v14 a SECOND time changes nothing and creates 0 duplicate tasks!
        with db.transaction() as conn:
            _migrate_v14_quorum3_cross_harness_backfill(conn, is_postgres=False)
        _check_state()

        # Verify Finding D (which has 1 legacy disqualified confirm) promotes cleanly to verified with consensus=1.0
        # once 3 pairwise-independent verifiers confirm its 3 newly queued tasks!
        with TestClient(app) as client:
            for handle, har, mod in (
                ("bf-new-1", "Windsurf", "qwen3-coder"),
                ("bf-new-2", "Aider", "llama-4"),
                ("bf-new-3", "OpenCode", "mistral-large"),
            ):
                peer = _register(client, handle, coding_agent=har, coding_agent_model=mod)
                c = client.post(
                    "/api/desk/next",
                    headers={"X-Agent-Token": peer["api_token"]},
                    json={"handle": handle, "task_id": f"tsk-pv-fnd-bf-d-{3 if handle.endswith('1') else (4 if handle.endswith('2') else 5)}"},
                )
                assert c.status_code == 200, c.text
                r = client.post(
                    "/api/desk/submit",
                    headers={"X-Agent-Token": peer["api_token"]},
                    json={
                        "lease_id": c.json()["lease_id"],
                        "outcome": "verification",
                        "verification": {
                            "finding_id": "fnd-bf-d",
                            "verdict": "confirm",
                            "checks": dict(ALL_PASS_CHECKS),
                            "reproduced_metrics": {"artifact_sha256": "deadbeef", "numerator": 10, "denominator": 20},
                            "notes": "Independently reproduced backfilled finding D.",
                        },
                    },
                )
                assert r.status_code == 200, r.text
            d_after = client.get("/api/findings/fnd-bf-d").json()
            assert d_after["status"] == "verified"
            assert d_after["confirm_count"] == 3
            assert d_after["verification_score"] == pytest.approx(1.0)
            assert d_after["reproduction_summary"]["computational_confirm_count"] == 3
    finally:
        app.state.db.close()


def test_migration_v14_postgres_sql_branch_sets_default_and_batches() -> None:
    """Smoke-test the Postgres SQL branch of migration v14 against a recording fake connection."""
    executed: list[tuple[str, Any]] = []

    class _FakeCursor:
        def __init__(self, rows: list[dict[str, Any]]) -> None:
            self._rows = rows

        def fetchall(self) -> list[dict[str, Any]]:
            return self._rows

    class _FakePgConn:
        def execute(self, sql: str, params: Any = None) -> _FakeCursor:
            executed.append((sql.strip(), params))
            norm = " ".join(sql.split()).upper()
            if norm.startswith("SELECT ID, MISSION_ID, MISSION_SLUG, METHODOLOGY_CODE, TITLE, SUBMITTED_BY"):
                return _FakeCursor(
                    [
                        {
                            "id": "fnd-pg-1",
                            "mission_id": "m1",
                            "mission_slug": "s1",
                            "methodology_code": "P1",
                            "title": "PG Finding",
                            "submitted_by": "a1",
                            "submitted_coding_agent": "Claude Code",
                            "submitted_coding_agent_model": "claude-opus-4-6",
                            "status": "verified",
                            "confirm_count": 2,
                            "dispute_count": 0,
                            "required_quorum": 2,
                            "verification_score": 1.0,
                            "verified_at": "2026-11-26T00:00:00Z",
                        }
                    ]
                )
            if norm.startswith("SELECT PV.ID, PV.FINDING_ID"):
                return _FakeCursor(
                    [
                        {
                            "id": "pv-1",
                            "finding_id": "fnd-pg-1",
                            "contributor_id": "v1",
                            "verifier_handle": "v1",
                            "verdict": "confirm",
                            "checks_json": dumps_json(ALL_PASS_CHECKS),
                            "reproduced_metrics_json": '{"x":1}',
                            "coding_agent": "Codex",
                            "coding_agent_model": "gpt-5.4",
                            "created_at": "2026-11-26T00:01:00Z",
                        },
                        {
                            "id": "pv-2",
                            "finding_id": "fnd-pg-1",
                            "contributor_id": "v2",
                            "verifier_handle": "v2",
                            "verdict": "confirm",
                            "checks_json": dumps_json(ALL_PASS_CHECKS),
                            "reproduced_metrics_json": '{"x":1}',
                            "coding_agent": "Antigravity",
                            "coding_agent_model": "gemini-3.1-pro",
                            "created_at": "2026-11-26T00:02:00Z",
                        },
                    ]
                )
            if norm.startswith("SELECT T.ID, T.TARGET_FINDING_ID"):
                return _FakeCursor(
                    [
                        {"id": "tsk-pv-fnd-pg-1-1", "target_finding_id": "fnd-pg-1", "status": "completed"},
                        {"id": "tsk-pv-fnd-pg-1-2", "target_finding_id": "fnd-pg-1", "status": "completed"},
                    ]
                )
            return _FakeCursor([])

    _migrate_v14_quorum3_cross_harness_backfill(_FakePgConn(), is_postgres=True)

    sqls = [" ".join(s.split()) for s, _ in executed]
    assert any("ALTER TABLE findings ALTER COLUMN required_quorum SET DEFAULT 3" in s for s in sqls)
    assert any("UPDATE findings AS f SET required_quorum = 3" in s and "FROM (VALUES" in s for s in sqls)
    assert any("INSERT INTO tasks (" in s and "JOIN tasks tmpl ON tmpl.id = v.tmpl_id" in s for s in sqls)
    assert any("UPDATE missions SET verified_findings_count" in s for s in sqls)
    assert any("DELETE FROM summary_cache" in s for s in sqls)


def test_migration_v14_benchmark_1000_findings_under_one_second(tmp_path: Path) -> None:
    """1,000 synthetic verified findings with 2,000 verifications and 2,000 template tasks migrate in <1s."""
    db = Database(db_path=tmp_path / "bench.db")
    try:
        now = "2026-11-26T10:00:00Z"
        checks_s = dumps_json(ALL_PASS_CHECKS)
        with db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO missions (
                    id, slug, title, description, pillar, methodology_code,
                    jurisdiction, ministry_or_authority, verified_findings_count, created_at
                ) VALUES ('m-bench', 'bench', 'Bench', 'Bench', 'budget', 'P1', 'Union', 'MoF', 1000, ?)
                """,
                (now,),
            )
            conn.executemany(
                """
                INSERT INTO agents (
                    id, handle, display_name, provider, model, api_token_hash,
                    coding_agent, coding_agent_model, joined_at, last_heartbeat_at
                ) VALUES (?, ?, ?, 'rule-engine', 'v1', ?, ?, ?, ?, ?)
                """,
                [
                    ("a0", "bench-a", "A", "t0", "Claude Code", "claude-opus-4-6", now, now),
                    ("a1", "bench-v1", "V1", "t1", "Codex", "gpt-5.4", now, now),
                    ("a2", "bench-v2", "V2", "t2", "Antigravity", "gemini-3.1-pro", now, now),
                ],
            )
            finding_rows = [
                (
                    f"fnd-b-{i:04d}",
                    "m-bench",
                    "bench",
                    f"Bench Finding {i}",
                    now,
                    now,
                )
                for i in range(1000)
            ]
            conn.executemany(
                """
                INSERT INTO findings (
                    id, mission_id, mission_slug, pillar, title, summary, methodology_code,
                    entity_name, jurisdiction, fiscal_year, submitted_by, submitted_by_agent_id,
                    submitted_coding_agent, submitted_coding_agent_model, status,
                    verification_score, confirm_count, dispute_count, required_quorum, created_at, verified_at
                ) VALUES (?, ?, ?, 'budget', ?, 'Summary', 'P1', 'Entity', 'Union', 'FY2025-26',
                          'bench-a', 'a0', 'Claude Code', 'claude-opus-4-6', 'verified', 1.0, 2, 0, 2, ?, ?)
                """,
                finding_rows,
            )
            pv_rows = []
            task_rows = []
            for i in range(1000):
                fid = f"fnd-b-{i:04d}"
                pv_rows.append((f"pv-{i}-1", fid, "a1", "bench-v1", "bench-v1", checks_s, "Codex", "gpt-5.4", "2026-11-26T10:01:00Z"))
                pv_rows.append((f"pv-{i}-2", fid, "a2", "bench-v2", "bench-v2", checks_s, "Antigravity", "gemini-3.1-pro", "2026-11-26T10:02:00Z"))
                payload_s = dumps_json({"finding_id": fid, "methodology_code": "P1"})
                task_rows.append((f"tsk-pv-{fid}-1", payload_s, fid, now, now))
                task_rows.append((f"tsk-pv-{fid}-2", payload_s, fid, now, now))
            conn.executemany(
                """
                INSERT INTO peer_verifications (
                    id, finding_id, verifier_node_id, contributor_id, verifier_handle,
                    verdict, checks_json, reproduced_metrics_json, coding_agent, coding_agent_model, created_at
                ) VALUES (?, ?, ?, ?, ?, 'confirm', ?, '{"x":1}', ?, ?, ?)
                """,
                pv_rows,
            )
            conn.executemany(
                """
                INSERT INTO tasks (
                    id, mission_id, mission_slug, methodology_code, task_type, title,
                    instructions, payload_json, target_finding_id, excluded_handles_json,
                    status, created_at, updated_at
                ) VALUES (?, 'm-bench', 'bench', 'P1', 'peer_verify', 'Peer Verify (1/2)', 'Verify',
                          ?, ?, '["bench-a"]', 'completed', ?, ?)
                """,
                task_rows,
            )

            t0 = time.perf_counter()
            _migrate_v14_quorum3_cross_harness_backfill(conn, is_postgres=False)
            elapsed = time.perf_counter() - t0

        assert elapsed < 1.0, f"Migration v14 took {elapsed:.3f}s for 1,000 findings (limit 1.0s)"
        with db.read() as conn:
            pr_cnt = conn.execute("SELECT COUNT(*) AS c FROM findings WHERE status = 'peer_review' AND required_quorum = 3").fetchone()["c"]
            q_cnt = conn.execute("SELECT COUNT(*) AS c FROM tasks WHERE task_type = 'peer_verify' AND status = 'queued'").fetchone()["c"]
        assert pr_cnt == 1000
        assert q_cnt == 1000
    finally:
        db.close()


def test_finding_with_explicit_required_quorum_two_still_honors_stored_quorum(tmp_path: Path) -> None:
    """In-flight findings explicitly storing required_quorum=2 honor their stored quorum on submit."""
    app = create_app(db_path=tmp_path / "legacy_q2.db", seed=True)
    try:
        with TestClient(app) as client:
            author = _register(client, "lq2-author", coding_agent="Claude Code", coding_agent_model="claude-opus-4-6")
            v1 = _register(client, "lq2-v1", coding_agent="Codex", coding_agent_model="gpt-5.4")
            v2 = _register(client, "lq2-v2", coding_agent="Antigravity", coding_agent_model="gemini-3.1-pro")
            fid, sha, _ = _submit_finding(client, author)

            with app.state.db.transaction() as conn:
                conn.execute("UPDATE findings SET required_quorum = 2 WHERE id = ?", (fid,))

            for idx, peer in enumerate((v1, v2), start=1):
                c = client.post(
                    "/api/desk/next",
                    headers={"X-Agent-Token": peer["api_token"]},
                    json={"handle": peer["handle"], "mode": "verify"},
                )
                assert c.status_code == 200, c.text
                r = client.post(
                    "/api/desk/submit",
                    headers={"X-Agent-Token": peer["api_token"]},
                    json={
                        "lease_id": c.json()["lease_id"],
                        "outcome": "verification",
                        "verification": _confirm_payload(fid, sha),
                    },
                )
                assert r.status_code == 200, r.text
                if idx == 1:
                    assert r.json()["finding_status"] == "peer_review"
                else:
                    assert r.json()["finding_status"] == "verified"
                    assert r.json()["confirm_count"] == 2
                    assert r.json()["promoted_to_verified"] is True

            detail = client.get(f"/api/findings/{fid}").json()
            assert detail["required_quorum"] == 2
            assert detail["reproduction_summary"]["required_quorum"] == 2
    finally:
        app.state.db.close()
