"""Comprehensive integration tests for the AI Watchdogs India FastAPI Coordinator Hub."""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aiwatchdogs.hub.app import create_app
from aiwatchdogs.hub.seed import seed_database


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    db_file = tmp_path / "test_hub.db"
    application = create_app(db_path=db_file, seed=True)
    with TestClient(application) as tc:
        yield tc
    application.state.db.close()


def _register_agent(client: TestClient, handle: str, provider: str = "ollama", model: str = "qwen2.5:14b") -> dict:
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

    join_txt = client.get("/join.txt")
    assert join_txt.status_code == 200
    assert "text/plain" in join_txt.headers["content-type"]
    assert "OWNER CONSENT" in join_txt.text
    assert "/api/agents/register" in join_txt.text
    assert "/api/desk/next" in join_txt.text
    assert "/api/desk/beat" in join_txt.text
    assert "/api/desk/submit" in join_txt.text
    assert "benign_explanations" in join_txt.text

    join_sh = client.get("/join.sh")
    assert join_sh.status_code == 200
    assert "shellscript" in join_sh.headers["content-type"]
    assert join_sh.text.startswith("#!/usr/bin/env sh")
    assert "aiwatchdogs join" in join_sh.text


def test_seed_idempotency_and_public_read_endpoints(client: TestClient) -> None:
    # Re-running seeder must be a no-op
    assert seed_database(client.app.state.db) is False

    head = client.get("/api/head")
    assert head.status_code == 200
    hd = head.json()
    assert isinstance(hd["orchestrator_brief"], str) and len(hd["orchestrator_brief"]) > 20
    assert hd["stats"]["agents_total"] == 10
    assert hd["stats"]["agents_active"] == 10
    assert hd["stats"]["findings_verified"] == 6
    assert hd["stats"]["findings_peer_review"] == 3
    assert hd["stats"]["findings_cleared"] == 3
    assert hd["stats"]["rtis_drafted"] == 6
    assert hd["stats"]["tasks_queued"] >= 10
    assert len(hd["missions"]) == 8
    assert len(hd["just_joined"]) == 6
    assert len(hd["whispers"]) >= 10
    assert len(hd["sponsors_featured"]) >= 4

    # Sources catalog (all 15 Indian public sources)
    sources = client.get("/api/sources")
    assert sources.status_code == 200
    sdata = sources.json()
    assert sdata["total"] == 15
    assert {s["ingestion_tier"] for s in sdata["sources"]} == {1, 2, 3}

    # Missions list & detail
    missions = client.get("/api/missions")
    assert missions.status_code == 200
    assert missions.json()["total"] == 8

    m_detail = client.get("/api/missions/ocds-single-bid-assam-hp")
    assert m_detail.status_code == 200
    md = m_detail.json()
    assert md["slug"] == "ocds-single-bid-assam-hp"
    assert len(md["sources"]) >= 2
    assert len(md["findings"]) >= 2
    assert len(md["tasks"]) >= 1

    # Findings filters & detail
    verified = client.get("/api/findings?status=verified")
    assert verified.status_code == 200
    assert verified.json()["total"] == 6

    m5 = client.get("/api/findings?methodology=M5&search=March")
    assert m5.status_code == 200
    assert m5.json()["total"] >= 1

    f_detail = client.get("/api/findings/fnd-demo-m1-01")
    assert f_detail.status_code == 200
    fd = f_detail.json()
    assert fd["status"] == "verified"
    assert len(fd["verifications"]) == 2
    assert fd["rti_draft"] is not None
    assert fd["rti_draft"]["word_count"] <= 500
    assert len(fd["benign_explanations"]) >= 1

    # Graph endpoint
    graph = client.get("/api/graph")
    assert graph.status_code == 200
    gdata = graph.json()
    node_types = {n["type"] for n in gdata["nodes"]}
    assert {"ministry", "mission", "finding", "source"}.issubset(node_types)
    assert len(gdata["edges"]) >= 20

    # Live & Contributors endpoints
    live = client.get("/api/live")
    assert live.status_code == 200
    ldata = live.json()
    assert len(ldata["agents"]) == 10
    assert len(ldata["model_breakdown"]) >= 4

    contrib_all = client.get("/api/contributors?window=all")
    assert contrib_all.status_code == 200
    assert contrib_all.json()["total"] == 10

    contrib_hour = client.get("/api/contributors?window=hour")
    assert contrib_hour.status_code == 200
    assert contrib_hour.json()["window"] == "hour"

    profile = client.get("/api/contributors/satyameva-node")
    assert profile.status_code == 200
    pdata = profile.json()
    assert pdata["handle"] == "satyameva-node"
    assert pdata["contributor"]["handle"] == "satyameva-node"
    assert len(pdata["submitted_findings"]) >= 1
    assert len(pdata["verifications"]) >= 1


def test_sponsors_pledge_and_open_treasury_ledger_json_and_csv(client: TestClient) -> None:
    sponsors_before = client.get("/api/sponsors").json()
    before_total = sponsors_before["total_pledged_inr"]
    assert "deterministic evidence" in sponsors_before["independence_pledge"].lower()

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
    assert sponsors_after["total_pledged_inr"] == pytest.approx(before_total + 25000.0)

    ledger_json = client.get("/api/ledger?format=json")
    assert ledger_json.status_code == 200
    lj = ledger_json.json()
    assert lj["summary"]["total_inflow_inr"] > lj["summary"]["total_outflow_inr"]
    assert lj["summary"]["entry_count"] >= 10

    ledger_csv = client.get("/api/ledger?format=csv")
    assert ledger_csv.status_code == 200
    assert "text/csv" in ledger_csv.headers["content-type"]
    rows = list(csv.DictReader(io.StringIO(ledger_csv.text)))
    assert len(rows) == lj["summary"]["entry_count"]
    assert any(r["counterparty"] == "Citizens for Open Audits Foundation" for r in rows)


def test_full_desk_lifecycle_neutrality_gate_quorum_promotion_and_409_blocks(client: TestClient) -> None:
    # Register 3 independent contributor nodes
    author = _register_agent(client, "alpha-auditor", provider="ollama", model="qwen2.5:14b")
    peer1 = _register_agent(client, "beta-verifier", provider="gemini", model="gemini-2.5-pro")
    peer2 = _register_agent(client, "gamma-verifier", provider="rule-engine", model="deterministic-cpu-v1")

    # Author claims an anomaly_scan task specifically
    claim = client.post(
        "/api/desk/next",
        headers={"X-Agent-Token": author["api_token"]},
        json={"handle": "alpha-auditor", "mode": "investigate", "preferred_mission": "gem-split-tendering-threshold"},
    )
    assert claim.status_code == 200, claim.text
    claim_data = claim.json()
    lease_id = claim_data["lease_id"]
    assert claim_data["task"]["task_type"] == "anomaly_scan"
    assert claim_data["task"]["mission_slug"] == "gem-split-tendering-threshold"

    # Send heartbeat with live whisper
    beat = client.post(
        "/api/desk/beat",
        headers={"X-Agent-Token": author["api_token"]},
        json={"lease_id": lease_id, "progress_pct": 55, "whisper": "Checking GFR Rule 149 rolling 60-day PO window."},
    )
    assert beat.status_code == 200
    assert beat.json()["status"] == "active"

    # 1. Server-side neutrality rejection (accusatory term 'corrupt' / 'scam' -> 422)
    bad_submit = client.post(
        "/api/desk/submit",
        headers={"X-Agent-Token": author["api_token"]},
        json={
            "lease_id": lease_id,
            "outcome": "finding",
            "finding": {
                "title": "Massive corrupt scam uncovered in GeM orders",
                "summary": "This is obvious fraud and bribery.",
                "methodology_code": "M2",
                "entity_name": "Sample Unit",
                "jurisdiction": "State:Sample",
                "fiscal_year": "FY2024-25",
                "monetary_exposure_inr": 3500000,
                "severity": "high",
                "anomaly_metrics": {"cluster_po_count": 4},
                "rule_triggered": "GFR-2017-R149",
                "citations": [{"row_id": "PO-1"}],
                "benign_explanations": [],
                "suggested_authority": "Sample Dept",
            },
        },
    )
    assert bad_submit.status_code == 422
    violations = bad_submit.json()["detail"]["violations"]
    assert len(violations) >= 2

    # 2. Server-side neutrality rejection for missing benign_explanations -> 422
    missing_benign = client.post(
        "/api/desk/submit",
        headers={"X-Agent-Token": author["api_token"]},
        json={
            "lease_id": lease_id,
            "outcome": "finding",
            "finding": {
                "title": "Public Record Anomaly: Threshold-hugging purchase order cluster",
                "summary": "Deterministic scan flagged 4 awards between 92% and 98% of Rs 10L ceiling.",
                "methodology_code": "M2",
                "entity_name": "Sample Hospital Unit",
                "jurisdiction": "State:Sample",
                "fiscal_year": "FY2024-25",
                "monetary_exposure_inr": 3840000,
                "severity": "high",
                "anomaly_metrics": {"cluster_po_count": 4, "cluster_sum_inr": 3840000},
                "rule_triggered": "GFR-2017-R149",
                "citations": [{"row_id": "PO-1..4"}],
                "benign_explanations": [],
                "suggested_authority": "Directorate of Health Services",
            },
        },
    )
    assert missing_benign.status_code == 422

    # 3. Clean neutral finding with PII embedded gets scrubbed and accepted into peer_review + spawns 2 peer_verify tasks
    good_submit = client.post(
        "/api/desk/submit",
        headers={"X-Agent-Token": author["api_token"]},
        json={
            "lease_id": lease_id,
            "outcome": "finding",
            "whisper": "Submitted M2 threshold cluster for independent peer verification.",
            "finding": {
                "title": "Public Record Anomaly: 4 POs hugging Rs 10L GeM bidding ceiling",
                "summary": "Deterministic scan flagged 4 awards summing to Rs 38.40L within 30 days (contact john.doe@example.com scrubbed).",
                "methodology_code": "M2",
                "entity_name": "Sample State Medical Stores",
                "jurisdiction": "State:Sample",
                "fiscal_year": "FY2024-25",
                "monetary_exposure_inr": 3840000,
                "severity": "high",
                "anomaly_metrics": {"threshold_inr": 1000000, "cluster_po_count": 4, "cluster_sum_inr": 3840000},
                "rule_triggered": "GFR-2017-R149-Split-Threshold",
                "source_url": "https://gem.gov.in/",
                "citations": [{"row_id": "PO-1..4", "excerpt": "4 awards Rs 9.5L-9.7L"}],
                "benign_explanations": [
                    "Distinct emergency hospital wing requisitions can lawfully fall below individual GeM bidding limits."
                ],
                "suggested_authority": "State Directorate of Health Services",
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

    # Verify PII was scrubbed in stored finding
    stored = client.get(f"/api/findings/{finding_id}").json()
    assert "john.doe@example.com" not in stored["summary"]
    assert "[REDACTED_EMAIL]" in stored["summary"]

    # 4. Self-verification MUST be blocked (author should not get own peer_verify task, and direct attempt -> 409)
    author_verify_claim = client.post(
        "/api/desk/next",
        headers={"X-Agent-Token": author["api_token"]},
        json={"handle": "alpha-auditor", "mode": "verify", "preferred_mission": "gem-split-tendering-threshold"},
    )
    assert author_verify_claim.status_code == 204  # Only peer_verify tasks in this mission belong to alpha-auditor!

    # Test explicit 409 self-verification block by having author claim a different verify task and try targeting own finding_id
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
    # Release that unrelated lease cleanly
    rel = client.post(
        "/api/desk/release",
        headers={"X-Agent-Token": author["api_token"]},
        json={"lease_id": other_verify_claim.json()["lease_id"], "reason": "test_cleanup"},
    )
    assert rel.status_code == 200

    # 5. Independent Peer 1 claims & confirms the new finding (still peer_review: 1/2)
    p1_claim = client.post(
        "/api/desk/next",
        headers={"X-Agent-Token": peer1["api_token"]},
        json={"handle": "beta-verifier", "mode": "verify", "preferred_mission": "gem-split-tendering-threshold"},
    )
    assert p1_claim.status_code == 200, p1_claim.text
    assert p1_claim.json()["task"]["payload"]["finding_id"] == finding_id

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
                "reproduced_metrics": {"cluster_po_count": 4, "cluster_sum_inr": 3840000},
                "confidence": 0.95,
                "notes": "Independently reproduced M2 metrics.",
            },
        },
    )
    assert p1_sub.status_code == 200, p1_sub.text
    assert p1_sub.json()["finding_status"] == "peer_review"
    assert p1_sub.json()["confirm_count"] == 1
    assert p1_sub.json()["promoted_to_verified"] is False

    # Peer 1 must NOT be allowed to claim the 2nd verification slot for the same finding (double-verify prevention)
    p1_second_claim = client.post(
        "/api/desk/next",
        headers={"X-Agent-Token": peer1["api_token"]},
        json={"handle": "beta-verifier", "mode": "verify", "preferred_mission": "gem-split-tendering-threshold"},
    )
    assert p1_second_claim.status_code == 204

    # 6. Independent Peer 2 claims & confirms -> 2/2 Quorum PROMOTES to 'verified' & auto-generates RTI draft <=500 words!
    p2_claim = client.post(
        "/api/desk/next",
        headers={"X-Agent-Token": peer2["api_token"]},
        json={"handle": "gamma-verifier", "mode": "verify", "preferred_mission": "gem-split-tendering-threshold"},
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
                "reproduced_metrics": {"cluster_po_count": 4, "cluster_sum_inr": 3840000},
                "confidence": 0.97,
                "notes": "Second independent confirmation — math and neutrality clean.",
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

    # Verify finding dossier now includes 2 independent verifications & <=500-word RTI Act 2005 draft
    verified_dossier = client.get(f"/api/findings/{finding_id}").json()
    assert verified_dossier["status"] == "verified"
    assert len(verified_dossier["verifications"]) == 2
    assert verified_dossier["rti_draft"] is not None
    assert verified_dossier["rti_draft"]["word_count"] <= 500
    assert "Section 6(1)" in verified_dossier["rti_draft"]["body_text"]

    # Explicit POST /api/findings/{id}/rti-draft regeneration works too
    rti_regen = client.post(
        f"/api/findings/{finding_id}/rti-draft",
        json={"public_authority": "Department of Expenditure, Government of India"},
    )
    assert rti_regen.status_code == 200
    assert rti_regen.json()["public_authority"] == "Department of Expenditure, Government of India"
    assert rti_regen.json()["word_count"] <= 500


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
            "whisper": "All 18 procurement rows checked cleanly within statutory rules.",
            "metrics": {"records_scanned": 18, "cleared_count": 18, "monetary_audited_inr": 25000000},
        },
    )
    assert submit.status_code == 200
    assert submit.json()["outcome"] == "cleared"

    profile = client.get("/api/contributors/delta-cleaner").json()
    assert profile["cleared_submitted"] == 1
    assert profile["compute_score"] >= 10

    # SSE stream emits initial connected event cleanly
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
        root = tc.get("/missions/ocds-single-bid-assam-hp")
        assert root.status_code == 200
        assert "AI Watchdogs SPA" in root.text

        asset = tc.get("/assets/app.js")
        assert asset.status_code == 200
        assert "console.log" in asset.text

        missing_api = tc.get("/api/does-not-exist")
        assert missing_api.status_code == 404
    application.state.db.close()


def test_dashboard_parity_additive_fields_head_missions_and_graph(client: TestClient) -> None:
    # 1. /api/head additive fields
    head = client.get("/api/head")
    assert head.status_code == 200
    hd = head.json()

    assert isinstance(hd["started_at"], str) and hd["started_at"].endswith("Z")
    assert hd["started_at"] <= hd["generated_at"]

    just_in = hd["just_in"]
    assert just_in is not None
    assert {
        "id",
        "title",
        "methodology_code",
        "entity_name",
        "monetary_exposure_inr_cr",
        "verified_at",
        "is_demo_sample",
    }.issubset(just_in.keys())
    assert just_in["id"] == "fnd-demo-m7-01"  # newest seeded verified finding (52m ago)
    assert just_in["is_demo_sample"] is True

    recent_verifs = hd["recent_verifications"]
    assert 1 <= len(recent_verifs) <= 6
    v0 = recent_verifs[0]
    assert {
        "id",
        "finding_id",
        "finding_title",
        "methodology_code",
        "verifier_handle",
        "model_id",
        "model",
        "verdict",
        "created_at",
    }.issubset(v0.keys())

    sponsors_recent = hd["sponsors_recent"]
    assert 1 <= len(sponsors_recent) <= 12
    assert len(sponsors_recent) == 10  # 10 seeded sponsors
    s0 = sponsors_recent[0]
    assert {
        "id",
        "name",
        "tier",
        "amount_inr",
        "compute_grant",
        "note",
        "website_url",
        "is_demo",
        "created_at",
    }.issubset(s0.keys())
    # Ordered by created_at DESC (spn-10 was 1 day ago, spn-01 was 12 days ago)
    assert s0["id"] == "spn-10"
    assert s0["is_demo"] is True

    record_events = hd["record_events"]
    assert 1 <= len(record_events) <= 20
    valid_kinds = {"verified", "peer_review", "cleared", "disputed", "rti"}
    seen_kinds = set()
    for ev in record_events:
        assert {
            "id",
            "ts",
            "kind",
            "tag",
            "title",
            "actor",
            "methodology_code",
            "finding_id",
            "monetary_exposure_inr_cr",
            "is_demo_sample",
        }.issubset(ev.keys())
        assert ev["kind"] in valid_kinds
        seen_kinds.add(ev["kind"])
    assert {"verified", "peer_review", "cleared", "rti"}.issubset(seen_kinds)
    # Chronological descending order
    timestamps = [ev["ts"] for ev in record_events]
    assert timestamps == sorted(timestamps, reverse=True)

    # 2. Mission rungs in both /api/head and /api/missions
    valid_rung_states = {"verified", "hard", "peer_review", "cleared", "queued", "disputed"}
    missions_resp = client.get("/api/missions")
    assert missions_resp.status_code == 200
    for mission_list in (hd["missions"], missions_resp.json()["missions"]):
        assert len(mission_list) == 8
        for m in mission_list:
            assert isinstance(m["rungs"], list)
            assert 1 <= len(m["rungs"]) <= 24
            for rung in m["rungs"]:
                assert {"id", "state", "label", "kind"}.issubset(rung.keys())
                assert rung["state"] in valid_rung_states
                assert rung["kind"] in {"finding", "task"}
                if rung["kind"] == "finding":
                    assert rung["finding_id"] == rung["id"]

    # 3. /api/graph meta fields for Observatory Map clustering & community rings
    graph = client.get("/api/graph").json()
    mission_nodes = [n for n in graph["nodes"] if n["type"] == "mission"]
    finding_nodes = [n for n in graph["nodes"] if n["type"] == "finding"]
    assert len(mission_nodes) == 8
    assert len(finding_nodes) == 12
    for mn in mission_nodes:
        assert {"slug", "pillar", "methodology_code", "progress_pct"}.issubset(mn["meta"].keys())
        assert mn["meta"]["pillar"] == mn["pillar"]
    for fn in finding_nodes:
        assert {
            "mission_slug",
            "submitted_by",
            "is_community",
            "monetary_exposure_inr_cr",
            "methodology_code",
            "is_demo_sample",
        }.issubset(fn["meta"].keys())
        # All seeded findings come from seed agents -> is_community False
        assert fn["meta"]["is_community"] is False

    # 4. External/community contributor submission flips is_community=True in /api/graph
    ext_agent = _register_agent(client, "citizen-node-99", provider="ollama", model="qwen2.5:14b")
    claim = client.post(
        "/api/desk/next",
        headers={"X-Agent-Token": ext_agent["api_token"]},
        json={"handle": "citizen-node-99", "mode": "investigate", "preferred_mission": "gem-split-tendering-threshold"},
    )
    assert claim.status_code == 200
    submit = client.post(
        "/api/desk/submit",
        headers={"X-Agent-Token": ext_agent["api_token"]},
        json={
            "lease_id": claim.json()["lease_id"],
            "outcome": "finding",
            "finding": {
                "title": "Public Record Anomaly: Community-surfaced GeM threshold cluster",
                "summary": "Deterministic scan flagged 4 awards near the Rs 10L GeM bidding limit.",
                "methodology_code": "M2",
                "entity_name": "Community Sample Hospital",
                "jurisdiction": "State:Sample",
                "fiscal_year": "FY2024-25",
                "monetary_exposure_inr": 3900000,
                "severity": "high",
                "anomaly_metrics": {"cluster_po_count": 4},
                "rule_triggered": "GFR-2017-R149",
                "citations": [{"row_id": "PO-10..13"}],
                "benign_explanations": [
                    "Independent hospital departments may issue separate urgent requisitions within GFR rules."
                ],
                "suggested_authority": "State Directorate of Health Services",
            },
        },
    )
    assert submit.status_code == 200
    new_fid = submit.json()["finding_id"]

    graph_after = client.get("/api/graph").json()
    comm_node = next(n for n in graph_after["nodes"] if n["id"] == f"finding:{new_fid}")
    assert comm_node["meta"]["submitted_by"] == "citizen-node-99"
    assert comm_node["meta"]["is_community"] is True
    assert comm_node["meta"]["mission_slug"] == "gem-split-tendering-threshold"
    assert comm_node["meta"]["monetary_exposure_inr_cr"] == pytest.approx(0.39)


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


def test_vercel_serverless_db_default_forwarded_headers_and_bounded_sse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aiwatchdogs.hub.database import VERCEL_DB_PATH, resolve_db_path

    # 1. Database path defaults to /tmp/aiwatchdogs.db when VERCEL is set and AIWATCHDOGS_DB_PATH is unset
    monkeypatch.delenv("AIWATCHDOGS_DB_PATH", raising=False)
    monkeypatch.setenv("VERCEL", "1")
    assert resolve_db_path() == VERCEL_DB_PATH

    # Explicit AIWATCHDOGS_DB_PATH still takes precedence on Vercel
    custom_db = tmp_path / "override.db"
    monkeypatch.setenv("AIWATCHDOGS_DB_PATH", str(custom_db))
    assert resolve_db_path() == custom_db

    # 2. /join.txt and /join.sh respect X-Forwarded-Proto & X-Forwarded-Host -> https://aiwatchdogs.in
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

        # 3. SSE stream closes cleanly after bounded max_duration without hanging
        with tc.stream("GET", "/api/stream?keepalive=0.5&max_duration=0.6") as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]
            body = "".join(resp.iter_text())
            assert "event: connected" in body
            assert ": keepalive" in body
    application.state.db.close()
