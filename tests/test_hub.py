"""Comprehensive integration tests for the AI Watchdogs India FastAPI Coordinator Hub."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aiwatchdogs.hub.app import create_app
from aiwatchdogs.hub.database import (
    HUB_TABLES,
    SCHEMA_SQL,
    VERCEL_DB_PATH,
    resolve_database_url,
    resolve_db_path,
    split_sql_statements,
    translate_sql_for_postgres,
)
from aiwatchdogs.hub.seed import seed_database

EXPECTED_REAL_MISSION_SLUGS = {
    "assam-ocds-single-bid",
    "assam-gfr-split-tendering",
    "ocds-vendor-concentration",
    "ocds-cost-escalation-watch",
    "fiscal-march-rush-budgets",
    "cag-union-state-audit-paras",
    "rural-schemes-pmgsy-mgnrega-mplads",
    "electoral-bonds-mca-csr-disclosure",
}


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    db_file = tmp_path / "test_hub.db"
    application = create_app(db_path=db_file, seed=True)
    with TestClient(application) as tc:
        yield tc
    application.state.db.close()


def _register_agent(
    client: TestClient,
    handle: str,
    provider: str = "ollama",
    model: str = "qwen2.5:14b",
) -> dict:
    resp = client.post(
        "/api/agents/register",
        json={
            "handle": handle,
            "display_name": f"Node {handle}",
            "provider": provider,
            "model": model,
            "mode": "auto",
            "region": "BLR-IN",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["handle"] == handle
    assert data["api_token"].startswith("awd_")
    assert data["profile_url"].endswith(f"/a/{handle}")
    return data


def test_health_and_onboarding_templates(client: TestClient) -> None:
    health = client.get("/health")
    assert health.status_code == 200
    hdata = health.json()
    assert hdata["status"] == "ok"
    assert hdata["db_ok"] is True
    assert hdata["db_backend"] == "sqlite"

    join_txt = client.get("/join.txt")
    assert join_txt.status_code == 200
    assert "text/plain" in join_txt.headers["content-type"]
    assert "OWNER CONSENT" in join_txt.text
    assert "/api/agents/register" in join_txt.text
    assert "/api/desk/next" in join_txt.text
    assert "/api/desk/beat" in join_txt.text
    assert "/api/desk/submit" in join_txt.text
    assert "dataset_url" in join_txt.text
    assert "expected_sha256" in join_txt.text
    assert "benign_explanations" in join_txt.text
    assert "datasets/sample" not in join_txt.text
    assert "demo" not in join_txt.text.lower()
    assert "sample" not in join_txt.text.lower()

    join_sh = client.get("/join.sh")
    assert join_sh.status_code == 200
    assert "shellscript" in join_sh.headers["content-type"]
    assert join_sh.text.startswith("#!/usr/bin/env sh")
    assert "aiwatchdogs join" in join_sh.text
    assert "datasets/sample" not in join_sh.text


def test_seed_idempotency_zero_fake_data_and_real_goi_work_queue(client: TestClient) -> None:
    # Re-running seeder must be a no-op
    assert seed_database(client.app.state.db) is False

    # 1. /api/head starts 100% honest: ZERO fake agents, findings, whispers, or sponsors
    head = client.get("/api/head")
    assert head.status_code == 200
    hd = head.json()
    assert isinstance(hd["started_at"], str) and hd["started_at"].endswith("Z")
    assert hd["started_at"] <= hd["generated_at"]
    assert "demo" not in hd["orchestrator_brief"].lower()
    assert "sample" not in hd["orchestrator_brief"].lower()
    assert "16" in hd["orchestrator_brief"]

    assert hd["stats"]["agents_total"] == 0
    assert hd["stats"]["agents_active"] == 0
    assert hd["stats"]["findings_verified"] == 0
    assert hd["stats"]["findings_peer_review"] == 0
    assert hd["stats"]["findings_cleared"] == 0
    assert hd["stats"]["rtis_drafted"] == 0
    assert hd["stats"]["sponsors_total_inr"] == 0.0
    assert hd["stats"]["tasks_queued"] == 16
    assert hd["stats"]["tasks_completed"] == 0
    assert hd["just_in"] is None
    assert hd["just_joined"] == []
    assert hd["active_agents"] == []
    assert hd["recent_findings"] == []
    assert hd["recent_verifications"] == []
    assert hd["whispers"] == []
    assert hd["record_events"] == []
    assert hd["sponsors_featured"] == []
    assert hd["sponsors_recent"] == []

    # 2. All 15 canonical Indian public data sources (Tiers 1, 2, 3)
    sources = client.get("/api/sources")
    assert sources.status_code == 200
    sdata = sources.json()
    assert sdata["total"] == 15
    assert {s["ingestion_tier"] for s in sdata["sources"]} == {1, 2, 3}

    # 3. All 8 real live_public missions with real queued Government of India tasks
    missions_resp = client.get("/api/missions")
    assert missions_resp.status_code == 200
    missions = missions_resp.json()["missions"]
    assert len(missions) == 8
    assert {m["slug"] for m in missions} == EXPECTED_REAL_MISSION_SLUGS

    total_queued_across_missions = 0
    for m in missions:
        assert m["data_mode"] == "live_public"
        assert m["is_demo_sample"] is False
        assert m["sample_dataset_path"] == ""
        assert m["queued_tasks"] >= 1
        assert len(m["rungs"]) >= 1
        total_queued_across_missions += m["queued_tasks"]
    assert total_queued_across_missions == 16

    # Verify mission detail & all 16 task payloads have real HTTPS URLs & 64-hex SHA-256
    m_detail = client.get("/api/missions/assam-ocds-single-bid")
    assert m_detail.status_code == 200
    md = m_detail.json()
    assert md["slug"] == "assam-ocds-single-bid"
    assert md["data_mode"] == "live_public"
    assert len(md["sources"]) >= 2
    assert md["findings"] == []
    assert len(md["tasks"]) == 1
    assert md["tasks"][0]["dataset_url"].startswith("https://")
    assert len(md["tasks"][0]["expected_sha256"]) == 64

    with client.app.state.db.read() as conn:
        t_rows = conn.execute("SELECT id, methodology_code, payload_json FROM tasks WHERE status = 'queued'").fetchall()
        assert len(t_rows) == 16
        for tr in t_rows:
            payload = json.loads(tr["payload_json"])
            assert payload["dataset_url"].startswith("https://")
            assert payload["source_url"].startswith("https://")
            assert len(payload["expected_sha256"]) == 64
            assert payload["methodology_code"] == tr["methodology_code"]
            assert payload["jurisdiction"]
            assert payload["fiscal_year"]
            assert payload["description"]
            assert payload["artifact_title"]
            assert payload["is_demo_sample"] is False
            assert "datasets/sample" not in tr["payload_json"]
            if tr["methodology_code"] == "M8":
                assert payload["secondary_url"].startswith("https://")

    # 4. Graph renders ministries, 8 real missions, and official sources cleanly even with 0 findings
    graph = client.get("/api/graph")
    assert graph.status_code == 200
    gdata = graph.json()
    node_types = {n["type"] for n in gdata["nodes"]}
    assert {"ministry", "mission", "source"}.issubset(node_types)
    assert "finding" not in node_types
    mission_nodes = [n for n in gdata["nodes"] if n["type"] == "mission"]
    assert len(mission_nodes) == 8
    assert all(n["meta"]["is_demo_sample"] is False for n in mission_nodes)
    assert len(gdata["edges"]) >= 20

    # 5. Live, Contributors, Sponsors, Ledger all handle fresh empty state gracefully
    live = client.get("/api/live").json()
    assert live["agents"] == []
    assert live["whispers"] == []
    assert live["model_breakdown"] == []
    assert live["total_count"] == 0

    contrib_all = client.get("/api/contributors?window=all").json()
    assert contrib_all == {"window": "all", "contributors": [], "total": 0}
    contrib_hour = client.get("/api/contributors?window=hour").json()
    assert contrib_hour == {"window": "hour", "contributors": [], "total": 0}


def test_sponsors_pledge_and_open_treasury_ledger_json_and_csv(client: TestClient) -> None:
    sponsors_before = client.get("/api/sponsors").json()
    assert sponsors_before["total_pledged_inr"] == 0.0
    assert sponsors_before["sponsors"] == []
    assert "deterministic evidence" in sponsors_before["independence_pledge"].lower()

    ledger_before = client.get("/api/ledger?format=json").json()
    assert ledger_before["summary"]["entry_count"] == 0
    assert ledger_before["summary"]["net_balance_inr"] == 0.0
    assert ledger_before["entries"] == []

    pledge = client.post(
        "/api/sponsors/pledge",
        json={
            "name": "Citizens for Open Audits Foundation",
            "handle_or_org": "@cfoa-in",
            "tier": "patron",
            "amount_inr": 25000,
            "compute_credits_desc": "100M open inference tokens",
            "message": "Keep every finding verifiable and non-partisan.",
        },
    )
    assert pledge.status_code == 201, pledge.text
    pl = pledge.json()
    assert pl["sponsor"]["name"] == "Citizens for Open Audits Foundation"
    assert pl["sponsor"]["is_demo_entry"] is False
    assert pl["ledger_entry"]["direction"] == "inflow"
    assert pl["ledger_entry"]["amount_inr"] == 25000.0

    sponsors_after = client.get("/api/sponsors").json()
    assert sponsors_after["total_pledged_inr"] == pytest.approx(25000.0)
    assert len(sponsors_after["sponsors"]) == 1

    ledger_json = client.get("/api/ledger?format=json")
    assert ledger_json.status_code == 200
    lj = ledger_json.json()
    assert lj["summary"]["total_inflow_inr"] == pytest.approx(25000.0)
    assert lj["summary"]["total_outflow_inr"] == 0.0
    assert lj["summary"]["entry_count"] == 1

    ledger_csv = client.get("/api/ledger?format=csv")
    assert ledger_csv.status_code == 200
    assert "text/csv" in ledger_csv.headers["content-type"]
    rows = list(csv.DictReader(io.StringIO(ledger_csv.text)))
    assert len(rows) == 1
    assert rows[0]["counterparty"] == "Citizens for Open Audits Foundation"


def test_full_desk_lifecycle_neutrality_gate_quorum_promotion_and_409_blocks(client: TestClient) -> None:
    # Register 3 real independent contributor nodes
    author = _register_agent(client, "alpha-auditor", provider="ollama", model="qwen2.5:14b")
    peer1 = _register_agent(client, "beta-verifier", provider="gemini", model="gemini-2.5-pro")
    peer2 = _register_agent(client, "gamma-verifier", provider="rule-engine", model="deterministic-cpu-v1")

    # Author claims the real M2 Assam GFR Rule 161 anomaly_scan task
    claim = client.post(
        "/api/desk/next",
        headers={"X-Agent-Token": author["api_token"]},
        json={"handle": "alpha-auditor", "mode": "investigate", "preferred_mission": "assam-gfr-split-tendering"},
    )
    assert claim.status_code == 200, claim.text
    claim_data = claim.json()
    lease_id = claim_data["lease_id"]
    assert claim_data["task"]["task_type"] == "anomaly_scan"
    assert claim_data["task"]["mission_slug"] == "assam-gfr-split-tendering"
    assert claim_data["task"]["payload"]["dataset_url"].startswith("https://")
    assert claim_data["task"]["payload"]["is_demo_sample"] is False

    # Send heartbeat with live whisper
    beat = client.post(
        "/api/desk/beat",
        headers={"X-Agent-Token": author["api_token"]},
        json={
            "lease_id": lease_id,
            "progress_pct": 55,
            "whisper": "Verified SHA-256 on Assam OCDS FY16-22; scanning GFR Rule 161 ₹50L clusters.",
        },
    )
    assert beat.status_code == 200
    assert beat.json()["status"] == "active"

    # 1. Server-side neutrality rejection (accusatory terms -> 422)
    bad_submit = client.post(
        "/api/desk/submit",
        headers={"X-Agent-Token": author["api_token"]},
        json={
            "lease_id": lease_id,
            "outcome": "finding",
            "finding": {
                "title": "Massive corrupt scam uncovered in Assam tenders",
                "summary": "This is obvious fraud and bribery.",
                "methodology_code": "M2",
                "entity_name": "BTC PWD Assam",
                "jurisdiction": "State:Assam",
                "fiscal_year": "FY2016-22",
                "monetary_exposure_inr": 19200000,
                "severity": "high",
                "anomaly_metrics": {"cluster_po_count": 4},
                "rule_triggered": "GFR-2017-R161",
                "citations": [{"row_id": "OCID-1"}],
                "benign_explanations": [],
                "suggested_authority": "Finance Department, Government of Assam",
            },
        },
    )
    assert bad_submit.status_code == 422
    assert len(bad_submit.json()["detail"]["violations"]) >= 2

    # 2. Server-side neutrality rejection for missing benign_explanations -> 422
    missing_benign = client.post(
        "/api/desk/submit",
        headers={"X-Agent-Token": author["api_token"]},
        json={
            "lease_id": lease_id,
            "outcome": "finding",
            "finding": {
                "title": "Public Record Anomaly: Threshold-hugging procurement cluster in Assam OCDS",
                "summary": "Deterministic scan flagged 4 tenders between ₹42.5L and ₹49.95L within 60 days.",
                "methodology_code": "M2",
                "entity_name": "BTC-PWD Assam",
                "jurisdiction": "State:Assam",
                "fiscal_year": "FY2016-22",
                "monetary_exposure_inr": 19200000,
                "severity": "high",
                "anomaly_metrics": {"cluster_po_count": 4, "cluster_sum_inr": 19200000},
                "rule_triggered": "GFR-2017-R161",
                "citations": [{"row_id": "OCID-1..4"}],
                "benign_explanations": [],
                "suggested_authority": "Finance Department, Government of Assam",
            },
        },
    )
    assert missing_benign.status_code == 422

    # 3. Clean neutral finding with embedded PII -> scrubbed, accepted, spawns 2 peer_verify tasks & real artifact
    good_submit = client.post(
        "/api/desk/submit",
        headers={"X-Agent-Token": author["api_token"]},
        json={
            "lease_id": lease_id,
            "outcome": "finding",
            "whisper": "Submitted M2 Assam GFR Rule 161 cluster for independent 2-of-2 peer verification.",
            "finding": {
                "title": "Public Record Anomaly: 4 Assam tenders hugging ₹50L GFR Rule 161 ceiling",
                "summary": "Deterministic scan flagged 4 tenders summing to ₹1.92 Cr within 45 days (contact officer@assam.example scrubbed).",
                "methodology_code": "M2",
                "entity_name": "BTC-PWD Assam",
                "jurisdiction": "State:Assam",
                "fiscal_year": "FY2016-22",
                "monetary_exposure_inr": 19200000,
                "severity": "high",
                "anomaly_metrics": {"threshold_inr": 5000000, "cluster_po_count": 4, "cluster_sum_inr": 19200000},
                "rule_triggered": "GFR-2017-R161-Split-Threshold",
                "source_url": "https://assam.open-contracting.in/datasets",
                "artifact_sha256": "01a3ab8b7dcd3023e10f2e14cf7685d16825932feb88a363d69bc1177af4a77e",
                "citations": [{"row_id": "OCID-1..4", "excerpt": "4 tenders ₹47.5L–₹49.2L"}],
                "benign_explanations": [
                    "Distinct road stretch work packages across separate rural sub-divisions may lawfully fall under individual administrative sanctions."
                ],
                "suggested_authority": "Finance Department, Government of Assam",
            },
        },
    )
    assert good_submit.status_code == 200, good_submit.text
    gs = good_submit.json()
    finding_id = gs["finding_id"]
    assert gs["finding_status"] == "peer_review"
    assert gs["peer_verify_tasks_created"] == 2
    assert "email" in gs["pii_redactions"]

    # Heartbeat on already completed lease must return 410 Gone
    settled_beat = client.post(
        "/api/desk/beat",
        headers={"X-Agent-Token": author["api_token"]},
        json={"lease_id": lease_id},
    )
    assert settled_beat.status_code == 410

    # Verify PII scrubbed, is_demo_sample is False, and real evidence_artifact was created
    stored = client.get(f"/api/findings/{finding_id}").json()
    assert "officer@assam.example" not in stored["summary"]
    assert "[REDACTED_EMAIL]" in stored["summary"]
    assert stored["is_demo_sample"] is False
    assert stored["artifact"] is not None
    assert stored["artifact"]["sha256"] == "01a3ab8b7dcd3023e10f2e14cf7685d16825932feb88a363d69bc1177af4a77e"
    assert stored["artifact"]["is_demo_sample"] is False

    # Case-insensitive search works across SQLite & Postgres
    search_resp = client.get("/api/findings?methodology=M2&search=assam")
    assert search_resp.status_code == 200
    assert search_resp.json()["total"] == 1

    # 4. Self-verification MUST be blocked (author gets 204 for own peer_verify, and 409 if targeting own finding)
    author_verify_claim = client.post(
        "/api/desk/next",
        headers={"X-Agent-Token": author["api_token"]},
        json={"handle": "alpha-auditor", "mode": "verify"},
    )
    assert author_verify_claim.status_code == 204

    # Have peer1 submit an independent finding on M1 so a peer_verify task exists for alpha-auditor to claim
    m1_claim = client.post(
        "/api/desk/next",
        headers={"X-Agent-Token": peer1["api_token"]},
        json={"handle": "beta-verifier", "mode": "investigate", "preferred_mission": "assam-ocds-single-bid"},
    )
    assert m1_claim.status_code == 200
    m1_submit = client.post(
        "/api/desk/submit",
        headers={"X-Agent-Token": peer1["api_token"]},
        json={
            "lease_id": m1_claim.json()["lease_id"],
            "outcome": "finding",
            "finding": {
                "title": "Public Record Anomaly: Single-bidder share >35% in Assam Health OCDS",
                "summary": "81 of 228 tenders (35.5%) received a single bid totaling ₹60.43 Cr.",
                "methodology_code": "M1",
                "entity_name": "Health and Family Welfare Department, Assam",
                "jurisdiction": "State:Assam",
                "fiscal_year": "FY2016-22",
                "monetary_exposure_inr": 604300000,
                "severity": "high",
                "anomaly_metrics": {"single_bid_rate_pct": 35.53},
                "rule_triggered": "GFR-2017-R166",
                "citations": [{"source_ref": "assam_ocds_v2.csv"}],
                "benign_explanations": ["Specialized hospital equipment may require proprietary OEM procurement."],
                "suggested_authority": "Health and Family Welfare Department, Government of Assam",
            },
        },
    )
    assert m1_submit.status_code == 200

    # Author claims M1 peer_verify lease and attempts to target their own M2 finding_id -> HTTP 409!
    other_verify_claim = client.post(
        "/api/desk/next",
        headers={"X-Agent-Token": author["api_token"]},
        json={"handle": "alpha-auditor", "mode": "verify"},
    )
    assert other_verify_claim.status_code == 200
    self_attempt = client.post(
        "/api/desk/submit",
        headers={"X-Agent-Token": author["api_token"]},
        json={
            "lease_id": other_verify_claim.json()["lease_id"],
            "outcome": "verification",
            "verification": {
                "finding_id": finding_id,
                "verdict": "confirm",
                "checks": {"provenance_ok": True, "math_reproduced": True},
                "confidence": 0.99,
            },
        },
    )
    assert self_attempt.status_code == 409
    rel = client.post(
        "/api/desk/release",
        headers={"X-Agent-Token": author["api_token"]},
        json={"lease_id": other_verify_claim.json()["lease_id"], "reason": "test_cleanup"},
    )
    assert rel.status_code == 200

    # 5. Independent Peer 1 claims & confirms the M2 finding (1/2, still peer_review)
    p1_claim = client.post(
        "/api/desk/next",
        headers={"X-Agent-Token": peer1["api_token"]},
        json={"handle": "beta-verifier", "mode": "verify", "preferred_mission": "assam-gfr-split-tendering"},
    )
    assert p1_claim.status_code == 200, p1_claim.text
    p1_payload = p1_claim.json()["task"]["payload"]
    assert p1_payload["finding_id"] == finding_id
    assert p1_payload["dataset_url"].startswith("https://")
    assert p1_payload["expected_sha256"] == "01a3ab8b7dcd3023e10f2e14cf7685d16825932feb88a363d69bc1177af4a77e"

    p1_sub = client.post(
        "/api/desk/submit",
        headers={"X-Agent-Token": peer1["api_token"]},
        json={
            "lease_id": p1_claim.json()["lease_id"],
            "outcome": "verification",
            "verification": {
                "finding_id": finding_id,
                "verdict": "confirm",
                "checks": {"provenance_ok": True, "math_reproduced": True, "rule_valid": True, "legal_pii_ok": True},
                "reproduced_metrics": {"cluster_po_count": 4, "cluster_sum_inr": 19200000},
                "confidence": 0.95,
                "notes": "Independently reproduced Assam OCDS M2 metrics and verified SHA-256.",
            },
        },
    )
    assert p1_sub.status_code == 200, p1_sub.text
    assert p1_sub.json()["finding_status"] == "peer_review"
    assert p1_sub.json()["confirm_count"] == 1
    assert p1_sub.json()["promoted_to_verified"] is False

    # Peer 1 cannot claim 2nd verification slot for the same finding (double-verify prevention)
    p1_second_claim = client.post(
        "/api/desk/next",
        headers={"X-Agent-Token": peer1["api_token"]},
        json={"handle": "beta-verifier", "mode": "verify", "preferred_mission": "assam-gfr-split-tendering"},
    )
    assert p1_second_claim.status_code == 204

    # 6. Independent Peer 2 claims & confirms -> 2/2 Quorum PROMOTES to 'verified' & auto-generates <=500w RTI draft!
    p2_claim = client.post(
        "/api/desk/next",
        headers={"X-Agent-Token": peer2["api_token"]},
        json={"handle": "gamma-verifier", "mode": "verify", "preferred_mission": "assam-gfr-split-tendering"},
    )
    assert p2_claim.status_code == 200, p2_claim.text
    assert p2_claim.json()["task"]["payload"]["finding_id"] == finding_id

    p2_sub = client.post(
        "/api/desk/submit",
        headers={"X-Agent-Token": peer2["api_token"]},
        json={
            "lease_id": p2_claim.json()["lease_id"],
            "outcome": "verification",
            "verification": {
                "finding_id": finding_id,
                "verdict": "confirm",
                "checks": {"provenance_ok": True, "math_reproduced": True, "rule_valid": True, "legal_pii_ok": True},
                "reproduced_metrics": {"cluster_po_count": 4, "cluster_sum_inr": 19200000},
                "confidence": 0.97,
                "notes": "Second independent confirmation — SHA-256, math, and neutrality verified.",
            },
        },
    )
    assert p2_sub.status_code == 200, p2_sub.text
    p2_res = p2_sub.json()
    assert p2_res["finding_status"] == "verified"
    assert p2_res["confirm_count"] == 2
    assert p2_res["verification_score"] == pytest.approx(1.0)
    assert p2_res["promoted_to_verified"] is True
    assert p2_res["rti_draft_id"] == f"rti-{finding_id}"

    # Verify finding dossier includes 2 independent verifications & <=500-word RTI Act 2005 draft
    verified_dossier = client.get(f"/api/findings/{finding_id}").json()
    assert verified_dossier["status"] == "verified"
    assert len(verified_dossier["verifications"]) == 2
    assert verified_dossier["rti_draft"] is not None
    assert verified_dossier["rti_draft"]["word_count"] <= 500
    assert "Section 6(1)" in verified_dossier["rti_draft"]["body_text"]

    # Explicit POST /api/findings/{id}/rti-draft regeneration works cleanly
    rti_regen = client.post(
        f"/api/findings/{finding_id}/rti-draft",
        json={"public_authority": "Finance Department, Government of Assam"},
    )
    assert rti_regen.status_code == 200
    assert rti_regen.json()["public_authority"] == "Finance Department, Government of Assam"
    assert rti_regen.json()["word_count"] <= 500

    # Verify /api/head, /api/graph, and /api/contributors reflect real live activity
    hd = client.get("/api/head").json()
    assert hd["stats"]["agents_total"] == 3
    assert hd["stats"]["findings_verified"] == 1
    assert hd["stats"]["findings_peer_review"] == 1
    assert hd["stats"]["rtis_drafted"] == 1
    assert hd["just_in"] is not None
    assert hd["just_in"]["id"] == finding_id
    assert hd["just_in"]["is_demo_sample"] is False
    assert len(hd["recent_verifications"]) == 2
    timestamps = [ev["ts"] for ev in hd["record_events"]]
    assert timestamps == sorted(timestamps, reverse=True)

    graph = client.get("/api/graph").json()
    finding_nodes = [n for n in graph["nodes"] if n["type"] == "finding"]
    assert len(finding_nodes) == 2
    # Every real finding comes from real registered community agents -> is_community is True!
    assert all(fn["meta"]["is_community"] is True for fn in finding_nodes)
    assert all(fn["meta"]["is_demo_sample"] is False for fn in finding_nodes)

    profile = client.get("/api/contributors/alpha-auditor").json()
    assert profile["handle"] == "alpha-auditor"
    assert profile["findings_verified"] == 1
    assert len(profile["submitted_findings"]) == 1


def test_cleared_outcome_credits_contributor_equally_and_sse_stream_connects(client: TestClient) -> None:
    node = _register_agent(client, "delta-cleaner", provider="rule-engine", model="deterministic-cpu-v1")
    claim = client.post(
        "/api/desk/next",
        headers={"X-Agent-Token": node["api_token"]},
        json={"handle": "delta-cleaner", "mode": "investigate"},
    )
    assert claim.status_code == 200
    submit = client.post(
        "/api/desk/submit",
        headers={"X-Agent-Token": node["api_token"]},
        json={
            "lease_id": claim.json()["lease_id"],
            "outcome": "cleared",
            "whisper": "All scanned official records passed deterministic thresholds cleanly.",
            "metrics": {"records_scanned": 48, "cleared_count": 48, "monetary_audited_inr": 250000000},
        },
    )
    assert submit.status_code == 200
    assert submit.json()["outcome"] == "cleared"

    profile = client.get("/api/contributors/delta-cleaner").json()
    assert profile["cleared_submitted"] == 1
    assert profile["compute_score"] >= 10

    with client.stream("GET", "/api/stream?max_events=1") as stream_resp:
        assert stream_resp.status_code == 200
        assert "text/event-stream" in stream_resp.headers["content-type"]
        body = "".join(stream_resp.iter_text())
        assert "event: connected" in body
        assert "AI Watchdogs India live telemetry stream" in body


def test_spa_fallback_and_unknown_api_404(tmp_path: Path) -> None:
    dist_dir = tmp_path / "web_dist"
    assets_dir = dist_dir / "assets"
    assets_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text("<html><body>AI Watchdogs SPA</body></html>", encoding="utf-8")
    (assets_dir / "app.js").write_text("console.log('ok');", encoding="utf-8")

    application = create_app(db_path=tmp_path / "spa.db", seed=False, web_dist=dist_dir)
    with TestClient(application) as tc:
        root = tc.get("/missions/assam-ocds-single-bid")
        assert root.status_code == 200
        assert "AI Watchdogs SPA" in root.text

        asset = tc.get("/assets/app.js")
        assert asset.status_code == 200
        assert "console.log" in asset.text

        missing_api = tc.get("/api/does-not-exist")
        assert missing_api.status_code == 404
    application.state.db.close()


def test_head_and_graph_graceful_on_empty_unseeded_db(tmp_path: Path) -> None:
    application = create_app(db_path=tmp_path / "empty.db", seed=False)
    with TestClient(application) as tc:
        hd = tc.get("/api/head").json()
        assert hd["started_at"] == hd["generated_at"]
        assert hd["just_in"] is None
        assert hd["recent_verifications"] == []
        assert hd["sponsors_recent"] == []
        assert hd["record_events"] == []
        assert hd["missions"] == []

        gdata = tc.get("/api/graph").json()
        assert gdata == {"nodes": [], "edges": []}
    application.state.db.close()


def test_postgres_sql_translation_and_vercel_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 1. PostgreSQL SQL translator parity checks
    assert translate_sql_for_postgres("PRAGMA journal_mode = WAL;") is None
    assert (
        translate_sql_for_postgres(
            "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, amt REAL NOT NULL);"
        )
        == "CREATE TABLE t (id SERIAL PRIMARY KEY, amt DOUBLE PRECISION NOT NULL);"
    )
    assert (
        translate_sql_for_postgres("INSERT OR IGNORE INTO t (id, v) VALUES (?, ?);", parameterized=True)
        == "INSERT INTO t (id, v) VALUES (%s, %s) ON CONFLICT DO NOTHING;"
    )
    # Preserves literal '?' inside single-quoted string and escapes literal '%'
    assert (
        translate_sql_for_postgres(
            "SELECT 'what? 50%' AS note FROM t WHERE id = ? AND LOWER(name) LIKE LOWER(?)",
            parameterized=True,
        )
        == "SELECT 'what? 50%%' AS note FROM t WHERE id = %s AND LOWER(name) LIKE LOWER(%s)"
    )
    # Full SCHEMA_SQL splits cleanly into executable DDL statements with DOUBLE PRECISION
    stmts = split_sql_statements(SCHEMA_SQL)
    assert len(stmts) >= 20
    translated = [translate_sql_for_postgres(s) for s in stmts]
    assert all(t is not None for t in translated)
    assert any("DOUBLE PRECISION" in (t or "") for t in translated)
    # HUB_TABLES stays strictly in sync with CREATE TABLE statements in SCHEMA_SQL
    import re
    schema_tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS\s+([a-z_]+)", SCHEMA_SQL))
    assert schema_tables == HUB_TABLES

    # 2. DATABASE_URL / POSTGRES_URL detection
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@ep-demo.aws.neon.tech/neondb?sslmode=require")
    assert resolve_database_url() == "postgresql://user:pass@ep-demo.aws.neon.tech/neondb?sslmode=require"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_URL", "postgres://user:pass@ep-demo.aws.neon.tech/neondb")
    assert resolve_database_url() == "postgres://user:pass@ep-demo.aws.neon.tech/neondb"
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    assert resolve_database_url() is None

    # 3. SQLite fallback defaults on Vercel when DATABASE_URL is unset
    monkeypatch.delenv("AIWATCHDOGS_DB_PATH", raising=False)
    monkeypatch.setenv("VERCEL", "1")
    assert resolve_db_path() == VERCEL_DB_PATH

    custom_db = tmp_path / "override.db"
    monkeypatch.setenv("AIWATCHDOGS_DB_PATH", str(custom_db))
    assert resolve_db_path() == custom_db

    # 4. /join.txt and /join.sh respect X-Forwarded-Proto & X-Forwarded-Host -> https://aiwatchdogs.in
    monkeypatch.delenv("AIWATCHDOGS_PUBLIC_URL", raising=False)
    application = create_app(db_path=tmp_path / "vercel_test.db", seed=True)
    with TestClient(application) as tc:
        fwd_headers = {
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "aiwatchdogs.in",
        }
        join_txt = tc.get("/join.txt", headers=fwd_headers)
        assert join_txt.status_code == 200
        assert "https://aiwatchdogs.in" in join_txt.text
        assert "http://aiwatchdogs.in" not in join_txt.text

        join_sh = tc.get("/join.sh", headers=fwd_headers)
        assert join_sh.status_code == 200
        assert "https://aiwatchdogs.in" in join_sh.text
        assert "http://aiwatchdogs.in" not in join_sh.text

        # 5. SSE stream closes cleanly after bounded max_duration without hanging
        with tc.stream("GET", "/api/stream?keepalive=0.5&max_duration=0.6") as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]
            body = "".join(resp.iter_text())
            assert "event: connected" in body
            assert ": keepalive" in body
    application.state.db.close()
