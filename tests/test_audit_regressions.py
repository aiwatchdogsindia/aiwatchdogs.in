"""Source-grounded regression tests covering every finding in the 24 Nov 2026 Codex audit."""

from __future__ import annotations

import importlib
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from aiwatchdogs.cli.config import (
    ENV_CONFIG_PATH,
    AgentConfig,
    get_spend_ledger_path,
    load_config,
    load_daily_spend,
    normalize_hub_origin,
    resolve_reusable_token,
    save_config,
)
from aiwatchdogs.cli.main import app as cli_app
from aiwatchdogs.cli.providers import (
    RULE_ENGINE_PROVIDER,
    BudgetTracker,
    ProviderInfo,
    describe_outbound_destinations,
    detect_best_provider,
    enrich_finding_summary,
)
from aiwatchdogs.cli.runner import AgentRunner
from aiwatchdogs.connectors.loader import DigestMismatchError, load_dataset
from aiwatchdogs.core.rti import generate_rti_draft, resolve_rti_jurisdiction
from aiwatchdogs.core.safety import compute_sha256, scrub_pii_in_obj, verhoeff_generate_check_digit
from aiwatchdogs.engines.registry import (
    METHODOLOGY_CATALOG,
    get_engine_fingerprint,
    run_engine,
)
from aiwatchdogs.hub.app import create_app
from aiwatchdogs.hub.database import LATEST_SCHEMA_VERSION, Database
from aiwatchdogs.hub.seed import REAL_AUDIT_TASKS, seed_database
from aiwatchdogs.hub.templates import render_join_sh
from test_engines import _create_minimal_pdf, _create_minimal_xlsx

cli_runner = CliRunner()

_HEX40_RE = re.compile(r"/[0-9a-f]{40}/")


def _register(client: TestClient, handle: str, provider: str = "rule-engine") -> dict[str, Any]:
    resp = client.post(
        "/api/agents/register",
        json={
            "handle": handle,
            "display_name": f"Agent {handle}",
            "provider": provider,
            "model": "deterministic-cpu-v1",
            "mode": "auto",
            "region": "IN",
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _submit_clean_finding(
    client: TestClient,
    agent: dict[str, Any],
    *,
    preferred_mission: str = "assam-ocds-single-bid",
    extra_finding: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> tuple[str, str, dict[str, Any]]:
    claim = client.post(
        "/api/desk/next",
        headers={"X-Agent-Token": agent["api_token"]},
        json={"handle": agent["handle"], "mode": "investigate", "preferred_mission": preferred_mission},
    )
    assert claim.status_code == 200, claim.text
    lease_id = claim.json()["lease_id"]
    task_obj = claim.json().get("task") or {}
    task_method = str(task_obj.get("methodology_code") or "M1").upper()
    task_sha = str(
        (task_obj.get("payload") or {}).get("expected_sha256")
        or "01a3ab8b7dcd3023e10f2e14cf7685d16825932feb88a363d69bc1177af4a77e"
    )
    finding = {
        "title": "Public Record Anomaly: Single-Bidder Concentration in Public Tenders",
        "summary": "Deterministic scan observed 81 of 228 positive-bid tenders (35.53%) with 1 bid.",
        "methodology_code": task_method,
        "entity_name": "Jorhat Medical College, Assam",
        "jurisdiction": "State:Assam",
        "fiscal_year": "FY 2017-18 to FY 2021-22",
        "fiscal_year_start": "FY 2017-18",
        "fiscal_year_end": "FY 2021-22",
        "monetary_exposure_inr": 604_300_000.0,
        "severity": "high",
        "anomaly_metrics": {"numerator": 81, "denominator": 228, "single_bid_share_pct": 35.53},
        "rule_triggered": "CVC-Single-Tender-Guidelines",
        "engine_version": "2.0.0",
        "ruleset_version": f"{task_method}-2026.11-v2",
        "source_locator": "rows=1,2,3",
        "artifact_sha256": task_sha,
        "citations": [{"record_id": "OCID-JMC-1", "excerpt": "81/228 single bid"}],
        "benign_explanations": [
            "Specialized hospital medical equipment may lawfully attract a single qualified OEM under procurement rules."
        ],
        "suggested_authority": "Health and Family Welfare Department, Government of Assam",
    }
    if extra_finding:
        finding.update(extra_finding)
    headers = {"X-Agent-Token": agent["api_token"]}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    sub = client.post(
        "/api/desk/submit",
        headers=headers,
        json={"lease_id": lease_id, "outcome": "finding", "finding": finding},
    )
    assert sub.status_code == 200, sub.text
    return lease_id, sub.json()["finding_id"], sub.json()


# ===========================================================================
# Finding #1 — CAG Report 6/2026 Fig 2.6 T-Bill Repayment & Dated Disposition
# ===========================================================================


def test_finding1_cag_report_6_2026_tbill_not_attributed_and_no_fabricated_age(tmp_path: Path) -> None:
    # 1. Reproduce CAG Union Report No. 6 of 2026 Para 2.4 + adjacent Figure 2.6 (14-day T-Bills)
    cag_pdf = tmp_path / "cag_6_2026.pdf"
    _create_minimal_pdf(
        cag_pdf,
        [
            [
                "Comptroller and Auditor General of India - Report No. 6 of 2026",
                "Accounts of the Union Government for FY 2024-25",
            ],
            [
                "2.4 Receipts Trends",
                "Revenue and capital receipts of the Union Government showed steady growth in FY 2024-25.",
                "Figure 2.6: Repayments of 14-day Treasury Bills during FY 2024-25",
                "Repayments of 14-day Treasury Bills amounted to Rs. 58,47,347 crore in FY 2024-25.",
                "Source: Union Finance Accounts 2024-25.",
                "2.5 Infructuous expenditure on stalled regional laboratory construction",
                "Audit observed infructuous expenditure of Rs. 18.75 crore under Work No. 42 after site abandonment.",
                "The departmental ATN reply remained unresolved at audit close.",
            ],
        ],
    )
    ds = load_dataset(str(cag_pdf), allow_local_files=True)
    res = run_engine("M6", ds.records)

    para_ids = [lead.anomaly_metrics["para_id"] for lead in res.leads]
    # Para 2.4 / Fig 2.6 ₹58,47,347 Cr T-Bill repayment MUST NOT appear as an audit observation!
    assert "Para 2.4" not in para_ids
    assert all(lead.monetary_exposure_inr < 1e12 for lead in res.leads)
    assert all(
        (lead.anomaly_metrics.get("quantified_impact_crore") or 0) != pytest.approx(5847347.0)
        for lead in res.leads
    )

    # Para 2.5 sentence-linked amount with Indian abbreviations ("Rs. 18.75 crore", "No. 42") IS extracted,
    # while undated ATN status MUST NOT fabricate years_pending=2!
    assert para_ids == ["Para 2.5"]
    lead_25 = res.leads[0]
    assert lead_25.anomaly_metrics["quantified_impact_crore"] == pytest.approx(18.75)
    assert lead_25.anomaly_metrics["amount_linkage"] == "sentence_explicit"
    assert lead_25.anomaly_metrics["atn_status"] == "unresolved"
    assert lead_25.anomaly_metrics["years_pending"] is None
    assert lead_25.anomaly_metrics["disposition_evidence_date"] is None
    assert lead_25.anomaly_metrics["pac_atr_filed"] is None

    # 2. Legacy Para with unlinked macro figure & no dated follow-up -> amount=None, age=None
    unlinked_pdf = tmp_path / "cag_unlinked.pdf"
    _create_minimal_pdf(
        unlinked_pdf,
        [
            [
                "Report No. 6 of 2026",
                "Para 3.1 Review of departmental register maintenance",
                "Macro gross tax revenue stood at Rs. 35,00,000 crore during the fiscal year.",
                "Repeat audit observation from earlier audit cycle noted procedural gaps.",
                "Para 3.2 Avoidable expenditure with explicit dated follow-up",
                "Avoidable expenditure | Rs. 12.50 crore | Departmental delay",
                "ATN reply awaited since 15 March 2023 as of 15 March 2026 and PAC ATR not tabled.",
            ]
        ],
    )
    ds2 = load_dataset(str(unlinked_pdf), allow_local_files=True)
    res2 = run_engine("M6", ds2.records)
    by_para = {lead.anomaly_metrics["para_id"]: lead for lead in res2.leads}
    assert by_para["Para 3.1"].anomaly_metrics["quantified_impact_inr"] is None
    assert by_para["Para 3.1"].anomaly_metrics["amount_linkage"] == "unrecorded"
    assert by_para["Para 3.1"].anomaly_metrics["atn_status"] == "unrecorded"
    assert by_para["Para 3.1"].anomaly_metrics["years_pending"] is None

    assert by_para["Para 3.2"].anomaly_metrics["quantified_impact_crore"] == pytest.approx(12.5)
    assert by_para["Para 3.2"].anomaly_metrics["amount_linkage"] == "table_cell_explicit"
    assert by_para["Para 3.2"].anomaly_metrics["years_pending"] == pytest.approx(3.0, abs=0.05)
    assert by_para["Para 3.2"].anomaly_metrics["disposition_evidence_date"] == "2023-03-15..2026-03-15"
    assert by_para["Para 3.2"].anomaly_metrics["pac_atr_filed"] is False


# ===========================================================================
# Finding #2 — Assam NHM OCDS release_date vs award_date & Threshold Rules
# ===========================================================================


def test_finding2_assam_nhm_release_date_vs_award_date_and_thresholds(tmp_path: Path) -> None:
    # 1. Exact reproduction of the live Assam NHM 113-row dossier defect:
    #    shared OCDS release_date 2023-06-21, blank tender/datePublished, no award_date/award_value,
    #    23 distinct bid-opening dates spanning 2021-07-20..2023-04-10, tender estimates only.
    bid_dates = [
        f"2021-07-{20 + i:02d}"
        if i < 8
        else (f"2022-0{(i - 8) + 1}-15" if i < 16 else f"2023-0{(i - 16) + 1}-10")
        for i in range(23)
    ]
    nhm_50l_csv = tmp_path / "assam_nhm_50l.csv"
    nhm_rows = [
        "ocid,date,tender/id,tender/title,tenderclassification/description,tender/value/amount,tender/datePublished,tender/bidOpening/date,buyer/name,fiscal_year"
    ]
    for i in range(113):
        nhm_rows.append(
            f"ocds-nhm-{i:03d},2023-06-21,2022_NHM_{i:03d},NHM Supply {i},Medical Supplies,4683720,,{bid_dates[i % 23]},National Health Mission Assam,2022"
        )
    nhm_50l_csv.write_text("\n".join(nhm_rows) + "\n", encoding="utf-8")

    ds_nhm = load_dataset(str(nhm_50l_csv), allow_local_files=True)
    assert len(ds_nhm.records) == 113
    # Loader MUST keep release_date, bid_opening_date, award_date, tender_estimate_inr, award_value_inr distinct!
    r0 = ds_nhm.records[0]
    assert r0["release_date"] == "2023-06-21"
    assert r0["tender_published_date"] == ""
    assert r0["award_date"] == ""
    assert r0["bid_opening_date"] == bid_dates[0]
    assert r0["tender_estimate_inr"] == 4_683_720.0
    assert r0["award_value_inr"] is None
    assert r0["has_award"] is False

    # Because Assam resolves to Assam APPR 2020 (₹25L), NOT July 2024 Central GFR (₹50L),
    # ₹46.83L tender estimates do NOT trigger a bogus Central GFR ₹50L 0-day splitting cluster!
    res_nhm_50l = run_engine("M2", ds_nhm.records)
    assert res_nhm_50l.leads == []

    # 2. Undated Assam NHM rows within Assam APPR 2020 ₹25L band -> retained ONLY as low-severity
    #    price-band observation with span_days=None (NEVER 0-day!) and splitting_claim_supported=False.
    nhm_25l_csv = tmp_path / "assam_nhm_25l.csv"
    rows_25l = [
        "ocid,date,tender/id,tender/title,tenderclassification/description,tender/value/amount,tender/datePublished,tender/bidOpening/date,buyer/name,fiscal_year"
    ]
    for i in range(25):
        rows_25l.append(
            f"ocds-nhm25-{i:03d},2023-06-21,2022_NHM25_{i:03d},NHM Kit {i},Medical Supplies,{2_200_000 + (i % 5) * 50_000},,{bid_dates[i % 23]},National Health Mission Assam,2022"
        )
    nhm_25l_csv.write_text("\n".join(rows_25l) + "\n", encoding="utf-8")
    ds_25l = load_dataset(str(nhm_25l_csv), allow_local_files=True)
    res_25l = run_engine("M2", ds_25l.records)
    assert len(res_25l.leads) == 1
    lead = res_25l.leads[0]
    assert lead.severity == "low"
    assert lead.anomaly_metrics["span_days"] is None  # NEVER 0!
    assert lead.anomaly_metrics["date_basis"] == "unrecorded"
    assert lead.anomaly_metrics["amount_type"] == "tender_estimate"
    assert lead.anomaly_metrics["splitting_claim_supported"] is False
    assert lead.anomaly_metrics["has_award_evidence"] is False
    assert lead.anomaly_metrics["distinct_bid_opening_dates"] == 23
    assert lead.anomaly_metrics["threshold_inr"] == 2_500_000.0
    assert lead.anomaly_metrics["threshold_scope"] == "state_assam_appr_2020_reference"
    assert lead.anomaly_metrics["statutory_applicability_verified"] is False

    # 3. Unverified non-Assam State jurisdiction marks statutory_applicability_verified=False
    kerala_awards = [
        {
            "po_id": f"KL-{i}",
            "buyer_name": "Kerala State Works",
            "item_category": "Equipment",
            "award_date": f"2023-04-{10 + i * 5:02d}",
            "award_value_inr": 2_350_000 + i * 20_000,
            "jurisdiction": "State:Kerala",
            "fiscal_year": "FY 2023-24",
        }
        for i in range(3)
    ]
    res_kl = run_engine("M2", kerala_awards)
    assert len(res_kl.leads) == 1
    assert res_kl.leads[0].anomaly_metrics["threshold_scope"] == "reference_benchmark_only_state_rules_unverified"
    assert res_kl.leads[0].anomaly_metrics["statutory_applicability_verified"] is False


# ===========================================================================
# Finding #3 — Spend Deduplication (M1+M2) & Mixed-Unit Withholding
# ===========================================================================


def test_finding3_spend_deduplication_and_mixed_unit_withholding(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "cov.db", seed=True)
    try:
        with TestClient(app) as client:
            agent = _register(client, "cov-auditor")
            assam_inr = 159_319.45 * 1e7

            # Complete M1 and M2 scans on the SAME Assam OCDS dataset_key
            for mission_slug in ("assam-ocds-single-bid", "assam-gfr-split-tendering"):
                claim = client.post(
                    "/api/desk/next",
                    headers={"X-Agent-Token": agent["api_token"]},
                    json={"handle": agent["handle"], "mode": "investigate", "preferred_mission": mission_slug},
                )
                assert claim.status_code == 200
                sub = client.post(
                    "/api/desk/submit",
                    headers={"X-Agent-Token": agent["api_token"]},
                    json={
                        "lease_id": claim.json()["lease_id"],
                        "outcome": "cleared",
                        "metrics": {
                            "records_scanned": 39502,
                            "cleared_count": 39459,
                            "unevaluable_count": 43,
                            "monetary_audited_inr": assam_inr,
                        },
                    },
                )
                assert sub.status_code == 200

            head1 = client.get("/api/head").json()
            # Workload doubles, unique_coverage MUST NOT double-count shared Assam dataset!
            assert head1["stats"]["scan_workload_inr_cr"] == pytest.approx(318_638.9, abs=0.1)
            assert head1["stats"]["unique_coverage_inr_cr"] == pytest.approx(159_319.45, abs=0.1)
            assert head1["stats"]["unique_coverage_withheld"] is False
            assert head1["stats"]["cleared_scans"] == 2
            assert head1["stats"]["findings_cleared"] == 0
            assert head1["stats"]["unevaluable_groups"] == 86

            # Now complete M8 Electoral Bonds scan (different financial quantity unit!)
            claim_m8 = client.post(
                "/api/desk/next",
                headers={"X-Agent-Token": agent["api_token"]},
                json={
                    "handle": agent["handle"],
                    "mode": "investigate",
                    "preferred_mission": "electoral-bonds-mca-csr-disclosure",
                },
            )
            assert claim_m8.status_code == 200
            sub_m8 = client.post(
                "/api/desk/submit",
                headers={"X-Agent-Token": agent["api_token"]},
                json={
                    "lease_id": claim_m8.json()["lease_id"],
                    "outcome": "cleared",
                    "metrics": {
                        "records_scanned": 18871,
                        "cleared_count": 18871,
                        "monetary_audited_inr": 12_769.09 * 1e7,
                    },
                },
            )
            assert sub_m8.status_code == 200

            head2 = client.get("/api/head").json()
            # Mixed units -> aggregate unique_coverage MUST be withheld (None), workload remains separate!
            assert head2["stats"]["unique_coverage_withheld"] is True
            assert head2["stats"]["unique_coverage_inr_cr"] is None
            assert head2["stats"]["public_spend_audited_inr_cr"] is None
            assert head2["stats"]["unique_coverage_reason"]
            assert head2["stats"]["scan_workload_inr_cr"] == pytest.approx(331_407.99, abs=0.1)

            by_unit = {row["quantity_type"]: row for row in head2["stats"]["coverage_by_quantity_type"]}
            assert by_unit["tender_estimates"]["unique_datasets"] == 1
            assert by_unit["tender_estimates"]["deduplicated_inr_cr"] == pytest.approx(159_319.45, abs=0.1)
            assert by_unit["electoral_bond_disclosures"]["unique_datasets"] == 1
            assert by_unit["electoral_bond_disclosures"]["deduplicated_inr_cr"] == pytest.approx(12_769.09, abs=0.1)
    finally:
        app.state.db.close()


# ===========================================================================
# Finding #4 — Strict 4-Check Verification, Omitted Verdict, Split-Vote & CLI
# ===========================================================================


def test_finding4_hub_verification_enforcement_and_split_vote_adjudication(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "verif.db", seed=True)
    try:
        with TestClient(app) as client:
            author = _register(client, "v4-author")
            peer1 = _register(client, "v4-peer1")
            peer2 = _register(client, "v4-peer2")
            peer3 = _register(client, "v4-peer3")
            peer4 = _register(client, "v4-peer4")

            _, fid, _ = _submit_clean_finding(client, author)

            p1_claim = client.post(
                "/api/desk/next",
                headers={"X-Agent-Token": peer1["api_token"]},
                json={"handle": peer1["handle"], "mode": "verify"},
            )
            assert p1_claim.status_code == 200
            p1_lease = p1_claim.json()["lease_id"]

            # 1. Omitted verdict MUST be rejected with 422 (never defaulted to confirm!)
            r_no_verdict = client.post(
                "/api/desk/submit",
                headers={"X-Agent-Token": peer1["api_token"]},
                json={
                    "lease_id": p1_lease,
                    "outcome": "verification",
                    "verification": {
                        "finding_id": fid,
                        "checks": {
                            "bytes_digest": "pass",
                            "deterministic_reproduction": "pass",
                            "metric_tolerance": "pass",
                            "neutrality_privacy": "pass",
                        },
                        "reproduced_metrics": {"numerator": 81},
                    },
                },
            )
            assert r_no_verdict.status_code == 422

            # 2. Confirm with missing check (normalized to 'unrecorded') -> 422
            r_missing_chk = client.post(
                "/api/desk/submit",
                headers={"X-Agent-Token": peer1["api_token"]},
                json={
                    "lease_id": p1_lease,
                    "outcome": "verification",
                    "verification": {
                        "finding_id": fid,
                        "verdict": "confirm",
                        "checks": {"bytes_digest": "pass", "deterministic_reproduction": "pass"},
                        "reproduced_metrics": {"numerator": 81},
                    },
                },
            )
            assert r_missing_chk.status_code == 422

            # 3. Confirm with empty reproduced_metrics -> 422
            r_no_metrics = client.post(
                "/api/desk/submit",
                headers={"X-Agent-Token": peer1["api_token"]},
                json={
                    "lease_id": p1_lease,
                    "outcome": "verification",
                    "verification": {
                        "finding_id": fid,
                        "verdict": "confirm",
                        "checks": {
                            "bytes_digest": "pass",
                            "deterministic_reproduction": "pass",
                            "metric_tolerance": "pass",
                            "neutrality_privacy": "pass",
                        },
                        "reproduced_metrics": {},
                    },
                },
            )
            assert r_no_metrics.status_code == 422

            # 4. Confirm with conflicting ruleset_version -> 422
            r_bad_ruleset = client.post(
                "/api/desk/submit",
                headers={"X-Agent-Token": peer1["api_token"]},
                json={
                    "lease_id": p1_lease,
                    "outcome": "verification",
                    "verification": {
                        "finding_id": fid,
                        "verdict": "confirm",
                        "ruleset_version": "M1-stale-v0",
                        "checks": {
                            "bytes_digest": "pass",
                            "deterministic_reproduction": "pass",
                            "metric_tolerance": "pass",
                            "neutrality_privacy": "pass",
                        },
                        "reproduced_metrics": {"numerator": 81},
                    },
                },
            )
            assert r_bad_ruleset.status_code == 422

            # 5. Valid Peer 1 confirm (explicit 4 checks pass + metrics + omitted confidence stays None!)
            expected_sha = p1_claim.json()["task"]["payload"]["expected_sha256"]
            r_p1 = client.post(
                "/api/desk/submit",
                headers={"X-Agent-Token": peer1["api_token"]},
                json={
                    "lease_id": p1_lease,
                    "outcome": "verification",
                    "verification": {
                        "finding_id": fid,
                        "verdict": "confirm",
                        "review_type": "computational_reproduction",
                        "checks": {
                            "bytes_digest": "pass",
                            "deterministic_reproduction": "pass",
                            "metric_tolerance": "pass",
                            "neutrality_privacy": "pass",
                        },
                        "reproduced_metrics": {
                            "artifact_sha256": expected_sha,
                            "numerator": 81,
                            "denominator": 228,
                        },
                    },
                },
            )
            assert r_p1.status_code == 200, r_p1.text
            detail1 = client.get(f"/api/findings/{fid}").json()
            assert detail1["verifications"][0]["confidence"] is None  # NEVER defaulted to 0.9!
            assert detail1["verifications"][0]["all_checks_passed"] is True

            # 6. Peer 2 submits dispute with partial checks (omitted checks MUST serialize as 'unrecorded')
            p2_claim = client.post(
                "/api/desk/next",
                headers={"X-Agent-Token": peer2["api_token"]},
                json={"handle": peer2["handle"], "mode": "verify"},
            )
            assert p2_claim.status_code == 200
            r_p2 = client.post(
                "/api/desk/submit",
                headers={"X-Agent-Token": peer2["api_token"]},
                json={
                    "lease_id": p2_claim.json()["lease_id"],
                    "outcome": "verification",
                    "verification": {
                        "finding_id": fid,
                        "verdict": "dispute",
                        "checks": {"bytes_digest": "pass", "metric_tolerance": "fail"},
                        "notes": "Metric divergence observed.",
                    },
                },
            )
            assert r_p2.status_code == 200, r_p2.text
            p2_data = r_p2.json()
            # 1 confirm + 1 dispute while slot #3 is still open -> stays in peer_review without premature adjudication!
            assert p2_data["finding_status"] == "peer_review"
            assert p2_data["adjudication_queued"] is False

            detail2 = client.get(f"/api/findings/{fid}").json()
            p2_verif = [v for v in detail2["verifications"] if v["verifier_handle"] == "v4-peer2"][0]
            assert p2_verif["checks"] == {
                "bytes_digest": "pass",
                "deterministic_reproduction": "unrecorded",
                "metric_tolerance": "fail",
                "neutrality_privacy": "unrecorded",
            }

            # Peer 3 claims the 3rd initial peer_verify slot and confirms -> 2C/1D (0.667 < 0.75) queues tie-breaker!
            p3_claim = client.post(
                "/api/desk/next",
                headers={"X-Agent-Token": peer3["api_token"]},
                json={"handle": peer3["handle"], "mode": "verify"},
            )
            assert p3_claim.status_code == 200
            r_p3 = client.post(
                "/api/desk/submit",
                headers={"X-Agent-Token": peer3["api_token"]},
                json={
                    "lease_id": p3_claim.json()["lease_id"],
                    "outcome": "verification",
                    "verification": {
                        "finding_id": fid,
                        "verdict": "confirm",
                        "review_type": "computational_reproduction",
                        "checks": {
                            "bytes_digest": "pass",
                            "deterministic_reproduction": "pass",
                            "metric_tolerance": "pass",
                            "neutrality_privacy": "pass",
                        },
                        "reproduced_metrics": {
                            "artifact_sha256": expected_sha,
                            "numerator": 81,
                            "denominator": 228,
                        },
                    },
                },
            )
            assert r_p3.status_code == 200, r_p3.text
            p3_data = r_p3.json()
            assert p3_data["finding_status"] == "peer_review"
            assert p3_data["adjudication_queued"] is True
            assert p3_data["adjudication_task_id"] == f"tsk-adj-{fid}-3"

            # Author, Peer 1, Peer 2, and Peer 3 MUST all be excluded from claiming the tie-breaker adjudication!
            for excluded_agent in (author, peer1, peer2, peer3):
                ex_claim = client.post(
                    "/api/desk/next",
                    headers={"X-Agent-Token": excluded_agent["api_token"]},
                    json={"handle": excluded_agent["handle"], "mode": "verify"},
                )
                assert ex_claim.status_code == 204

            # Independent Peer 4 claims the tie-breaker adjudication and confirms -> 3C/1D (0.75) promotes to verified!
            p4_claim = client.post(
                "/api/desk/next",
                headers={"X-Agent-Token": peer4["api_token"]},
                json={"handle": peer4["handle"], "mode": "verify"},
            )
            assert p4_claim.status_code == 200
            assert p4_claim.json()["task"]["payload"]["review_type"] == "adjudication"
            r_p4 = client.post(
                "/api/desk/submit",
                headers={"X-Agent-Token": peer4["api_token"]},
                json={
                    "lease_id": p4_claim.json()["lease_id"],
                    "outcome": "verification",
                    "verification": {
                        "finding_id": fid,
                        "verdict": "confirm",
                        "review_type": "adjudication",
                        "checks": {
                            "bytes_digest": "pass",
                            "deterministic_reproduction": "pass",
                            "metric_tolerance": "pass",
                            "neutrality_privacy": "pass",
                        },
                        "reproduced_metrics": {
                            "artifact_sha256": expected_sha,
                            "numerator": 81,
                            "denominator": 228,
                        },
                        "notes": "Tie-breaker confirms reproduced metrics.",
                    },
                },
            )
            assert r_p4.status_code == 200, r_p4.text
            assert r_p4.json()["finding_status"] == "verified"
            assert r_p4.json()["confirm_count"] == 3
            assert r_p4.json()["verification_score"] == pytest.approx(0.75)
    finally:
        app.state.db.close()


def test_finding4_cli_strict_verifier_matching(tmp_path: Path) -> None:
    csv_path = tmp_path / "strict_m2.csv"
    csv_path.write_text(
        "\n".join(
            [
                "tender_id,buyer_name,vendor_name,item_category,award_date,award_value_inr,jurisdiction,fiscal_year",
                "PO-1,Central Medical Procurement Cell,Vendor A,Lab Kits,2024-08-02,4850000,Union of India,FY2024-25",
                "PO-2,Central Medical Procurement Cell,Vendor A,Lab Kits,2024-08-14,4910000,Union of India,FY2024-25",
                "PO-3,Central Medical Procurement Cell,Vendor A,Lab Kits,2024-08-29,4940000,Union of India,FY2024-25",
                "PO-4,Central Medical Procurement Cell,Vendor A,Lab Kits,2024-09-09,4880000,Union of India,FY2024-25",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    sha = compute_sha256(csv_path.read_bytes())
    ds = load_dataset(str(csv_path), allow_local_files=True)
    eng = run_engine("M2", ds.records)
    lead = eng.leads[0]

    def _mock_hub(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/desk/beat":
            return httpx.Response(200, json={"status": "active"})
        if request.url.path == "/api/desk/submit":
            return httpx.Response(200, json={"status": "accepted"})
        return httpx.Response(404)

    base_payload: dict[str, Any] = {
        "finding_id": "fnd-strict",
        "canonical_url": str(csv_path),
        "artifact_sha256": sha,
        "methodology_code": "M2",
        "title": lead.title,
        "summary": lead.summary,
        "entity_name": lead.entity_name,
        "fiscal_year": lead.fiscal_year,
        "fiscal_year_start": lead.fiscal_year_start,
        "fiscal_year_end": lead.fiscal_year_end,
        "source_locator": lead.source_locator,
        "engine_version": eng.engine_version,
        "ruleset_version": eng.ruleset_version,
        "monetary_exposure_inr": float(lead.monetary_exposure_inr),
        "anomaly_metrics": dict(lead.anomaly_metrics),
        "rule_triggered": lead.rule_triggered,
        "benign_explanations": list(lead.benign_explanations),
    }

    cfg = AgentConfig(handle="strict-cli-ver", hub_url="http://mockhub", api_token="awd_tok")
    with httpx.Client(transport=httpx.MockTransport(_mock_hub), base_url="http://mockhub") as client:
        runner = AgentRunner(cfg, http_client=client, allow_local_files=True)

        # (a) Fuzzy/substring entity mismatch -> dispute
        res_entity = runner.execute_task(
            {
                "lease_id": "l-ent",
                "task": {
                    "id": "t-ent",
                    "task_type": "peer_verify",
                    "methodology_code": "M2",
                    "payload": {**base_payload, "entity_name": "Central Medical"},
                },
            }
        )
        assert res_entity["verification"]["verdict"] == "dispute"
        assert res_entity["verification"]["checks"]["deterministic_reproduction"] == "fail"

        # (b) Fiscal period mismatch -> dispute
        res_fy = runner.execute_task(
            {
                "lease_id": "l-fy",
                "task": {
                    "id": "t-fy",
                    "task_type": "peer_verify",
                    "methodology_code": "M2",
                    "payload": {**base_payload, "fiscal_year": "FY2022-23"},
                },
            }
        )
        assert res_fy["verification"]["verdict"] == "dispute"
        assert res_fy["verification"]["checks"]["deterministic_reproduction"] == "fail"

        # (c) Ruleset version mismatch -> dispute
        res_rule = runner.execute_task(
            {
                "lease_id": "l-rv",
                "task": {
                    "id": "t-rv",
                    "task_type": "peer_verify",
                    "methodology_code": "M2",
                    "payload": {**base_payload, "ruleset_version": "M2-legacy-v1"},
                },
            }
        )
        assert res_rule["verification"]["verdict"] == "dispute"
        assert res_rule["verification"]["checks"]["deterministic_reproduction"] == "fail"

        # (d) Partial metrics (missing required numeric keys) -> dispute (NO partial-overlap pass!)
        partial_metrics = {"cluster_po_count": 4}
        res_partial = runner.execute_task(
            {
                "lease_id": "l-pm",
                "task": {
                    "id": "t-pm",
                    "task_type": "peer_verify",
                    "methodology_code": "M2",
                    "payload": {**base_payload, "anomaly_metrics": partial_metrics},
                },
            }
        )
        assert res_partial["verification"]["verdict"] == "dispute"
        assert res_partial["verification"]["checks"]["metric_tolerance"] == "fail"

        # (e) Missing version binding -> unrecorded -> needs_review
        no_ver_payload = {k: v for k, v in base_payload.items() if k not in {"engine_version", "ruleset_version"}}
        res_unrec = runner.execute_task(
            {
                "lease_id": "l-unrec",
                "task": {
                    "id": "t-unrec",
                    "task_type": "peer_verify",
                    "methodology_code": "M2",
                    "payload": no_ver_payload,
                },
            }
        )
        assert res_unrec["verification"]["verdict"] == "needs_review"
        assert res_unrec["verification"]["checks"]["deterministic_reproduction"] == "unrecorded"


# ===========================================================================
# Finding #5 — Assam OBDS Demand 65, Krishna Actuals-Only & PolicyStack Caveat
# ===========================================================================


def test_finding5_obds_mixed_agency_krishna_pac_and_policystack_caveat(tmp_path: Path) -> None:
    # 1. Assam OBDS Demand 65 carrying Power Department / MH 4801 -> quarantined to unevaluable!
    #    Meanwhile legitimate Tourism row with wild T-2 actuals_last_audited_year is NOT compared cross-FY!
    obds_xlsx = tmp_path / "assam_obds_demand65.xlsx"
    _create_minimal_xlsx(
        obds_xlsx,
        {
            "Sheet1": [
                [
                    "state_ut_name_eng",
                    "demand_grant_no_en",
                    "demand_grant_desc_en",
                    "dept_name_en",
                    "major_head_code",
                    "major_head_desc_en",
                    "budget_financial_year",
                    "budget_amount_unit",
                    "budget_estimates_previous_financial_year",
                    "revised_estimates_previous_financial_year",
                    "actuals_last_audited_year",
                ],
                # Mixed agency/head row mimicking Sheet1 row 12566 (Power Dept MH 4801 inside Demand 65 Tourism)
                [
                    "Assam",
                    "65",
                    "Tourism",
                    "Power Department",
                    "4801",
                    "Capital Outlay on Power Projects",
                    "2022-23",
                    "Lakhs",
                    10000,
                    1129926,
                    5000,
                ],
                # Legitimate Tourism row with modest same-FY BE/RE shift (5%), but huge T-2 actuals difference
                [
                    "Assam",
                    "65",
                    "Tourism",
                    "Tourism Department",
                    "3452",
                    "Tourism",
                    "2022-23",
                    "Lakhs",
                    2000,
                    2100,
                    25000,
                ],
            ]
        },
        use_inline_strings=True,
    )
    obds_ds = load_dataset(str(obds_xlsx), allow_local_files=True)
    obds_res = run_engine("M5", obds_ds.records)
    assert obds_res.leads == []
    assert obds_res.unevaluable_count == 1  # Power Dept MH 4801 quarantined
    assert obds_res.cleared_count == 1  # Tourism row cleared without cross-FY T-2 vs T-1 error

    # 2. Krishna General Education actuals-only (₹125.70 Cr > ₹100 Cr PAC threshold, unobserved estimates/PAC):
    #    Q4 share CAN be investigated, but NO false missing PAC explanatory note flag!
    krishna_rows = [
        {"District": "KRISHNA", "Year": "2016", "Month": m, "Major Head": "2202", "Major Head Description": "General Education", "Amount": 60_000_000}
        for m in ("APRIL", "MAY", "JUNE", "JULY", "AUG", "SEP", "OCT", "NOV", "DEC")
    ] + [
        {"District": "KRISHNA", "Year": "2017", "Month": "JAN", "Major Head": "2202", "Major Head Description": "General Education", "Amount": 167_000_000},
        {"District": "KRISHNA", "Year": "2017", "Month": "FEB", "Major Head": "2202", "Major Head Description": "General Education", "Amount": 200_000_000},
        {"District": "KRISHNA", "Year": "2017", "Month": "MAR", "Major Head": "2202", "Major Head Description": "General Education", "Amount": 350_000_000},
    ]
    krishna_res = run_engine("M5", krishna_rows)
    assert len(krishna_res.leads) == 1
    k_lead = krishna_res.leads[0]
    assert k_lead.anomaly_metrics["actual_inr"] == pytest.approx(1_257_000_000.0)
    assert k_lead.anomaly_metrics["be_inr"] is None
    assert k_lead.anomaly_metrics["re_inr"] is None
    assert k_lead.anomaly_metrics["pac_explanatory_note_tabled"] is None
    assert k_lead.anomaly_metrics["pac_explanatory_note_missing"] is None
    assert "pac" not in k_lead.rule_triggered.lower()

    # 3. Seeded PolicyStack task explicitly caveats MGNREGA FY 2020-21 mirror defect vs Demand No. 87
    ps_task = next(t for t in REAL_AUDIT_TASKS if t["id"] == "tsk-real-08")
    assert ps_task["payload"]["input_quality_status"] == "caveat_mislabeled_mirror_row"
    defect = ps_task["payload"]["known_data_defects"][0]
    assert defect["scheme"] == "MGNREGA"
    assert defect["mirror_reported_inr_cr"] == pytest.approx(98000.0)
    assert defect["official_actual_inr_cr"] == pytest.approx(111169.53)
    assert {"scheme": "MGNREGA", "fiscal_year": "FY2020-21"} in ps_task["payload"]["quarantined_rows"]


# ===========================================================================
# M1, M3, M4, M7, M8 Deep Regressions
# ===========================================================================


def test_m1_zero_vs_blank_bidder_counts_fy_span_and_unevaluable_disposition() -> None:
    records: list[dict[str, Any]] = []
    # Jorhat-like buyer: 81 single-bid, 147 multi-bid (228 positive -> 35.53%), 39 literal zero, 10 blank across 5 FYs
    fys = ["FY 2017-18", "FY 2018-19", "FY 2019-20", "FY 2020-21", "FY 2021-22"]
    for i in range(81):
        records.append(
            {
                "tender_id": f"JMC-SB-{i}",
                "buyer_name": "Jorhat Medical College",
                "bid_count": 1,
                "tender_estimate_inr": 7_460_000,
                "has_award": False,
                "jurisdiction": "State:Assam",
                "fiscal_year": fys[i % len(fys)],
            }
        )
    for i in range(147):
        records.append(
            {
                "tender_id": f"JMC-MB-{i}",
                "buyer_name": "Jorhat Medical College",
                "bid_count": 3,
                "tender_estimate_inr": 5_000_000,
                "has_award": False,
                "jurisdiction": "State:Assam",
                "fiscal_year": fys[i % len(fys)],
            }
        )
    for i in range(39):
        records.append(
            {
                "tender_id": f"JMC-ZERO-{i}",
                "buyer_name": "Jorhat Medical College",
                "bid_count": 0,
                "tender_estimate_inr": 2_000_000,
                "has_award": False,
                "jurisdiction": "State:Assam",
                "fiscal_year": "FY 2020-21",
            }
        )
    for i in range(10):
        records.append(
            {
                "tender_id": f"JMC-BLANK-{i}",
                "buyer_name": "Jorhat Medical College",
                "bid_count": None,
                "tender_estimate_inr": 1_000_000,
                "has_award": False,
                "jurisdiction": "State:Assam",
                "fiscal_year": "FY 2021-22",
            }
        )
    # Second buyer group with ZERO positive bidder counts -> MUST go to unevaluable_count, NEVER cleared_count!
    for i in range(5):
        records.append(
            {
                "tender_id": f"UNEV-{i}",
                "buyer_name": "Zero-Positive Buyer",
                "bid_count": 0 if i < 3 else None,
                "tender_estimate_inr": 1_000_000,
                "has_award": False,
                "jurisdiction": "State:Assam",
                "fiscal_year": "FY 2021-22",
            }
        )

    res = run_engine("M1", records)
    assert len(res.leads) == 1
    assert res.cleared_count == 0
    assert res.unevaluable_count == 5

    lead = res.leads[0]
    assert lead.fiscal_year == "FY 2017-18 to FY 2021-22"
    assert lead.fiscal_year_start == "FY 2017-18"
    assert lead.fiscal_year_end == "FY 2021-22"
    assert lead.amount_type == "tender_estimate"
    m = lead.anomaly_metrics
    assert m["numerator"] == 81
    assert m["denominator"] == 228
    assert m["multi_bid_count"] == 147
    assert m["zero_bid_count"] == 39
    assert m["unknown_bid_count"] == 10
    assert m["single_bid_share_pct"] == pytest.approx(35.53)


def test_m3_thresholds_triggered_honest_title_and_excluded_share() -> None:
    # Vendor A gets 43.54% (above 40% share threshold), while B, C, D share rest so HHI ~ 0.31 (< 0.35)!
    awarded = [
        {"tender_id": "T1", "buyer_name": "State PWD", "item_category": "Roads", "vendor_name": "Vendor A", "award_value_inr": 43_540_000, "has_award": True, "fiscal_year": "FY 2020-21"},
        {"tender_id": "T2", "buyer_name": "State PWD", "item_category": "Roads", "vendor_name": "Vendor B", "award_value_inr": 25_000_000, "has_award": True, "fiscal_year": "FY 2021-22"},
        {"tender_id": "T3", "buyer_name": "State PWD", "item_category": "Roads", "vendor_name": "Vendor C", "award_value_inr": 20_000_000, "has_award": True, "fiscal_year": "FY 2021-22"},
        {"tender_id": "T4", "buyer_name": "State PWD", "item_category": "Roads", "vendor_name": "Vendor D", "award_value_inr": 11_460_000, "has_award": True, "fiscal_year": "FY 2022-23"},
    ]
    # 2 unawarded rows in the same buyer-category -> excluded share must be explicitly published
    excluded = [
        {"tender_id": "T5", "buyer_name": "State PWD", "item_category": "Roads", "vendor_name": "", "tender_estimate_inr": 15_000_000, "has_award": False, "fiscal_year": "FY 2022-23"},
        {"tender_id": "T6", "buyer_name": "State PWD", "item_category": "Roads", "vendor_name": "", "tender_estimate_inr": 10_000_000, "has_award": False, "fiscal_year": "FY 2022-23"},
    ]
    res = run_engine("M3", awarded + excluded)
    assert len(res.leads) == 1
    lead = res.leads[0]
    m = lead.anomaly_metrics
    assert m["hhi"] < 0.35
    assert m["hhi_triggered"] is False
    assert m["top_supplier_share_triggered"] is True
    assert m["thresholds_triggered"] == ["top_supplier_share"]
    assert "below 0.35" in lead.title
    assert m["excluded_row_count"] == 2
    assert m["excluded_row_share_pct"] == pytest.approx(33.33)
    assert m["excluded_estimate_value_inr"] == pytest.approx(25_000_000.0)
    assert lead.fiscal_year_start == "FY 2020-21"
    assert lead.fiscal_year_end == "FY 2022-23"


def test_m4_award_vs_estimate_distinct_from_final_cost_overrun() -> None:
    records = [
        {
            "tender_id": "M4-EST-1",
            "buyer_name": "HP Health Dept",
            "item_category": "Diagnostic Unit",
            "tender_estimate_inr": 10_000_000,
            "award_value_inr": 14_440_000,
            "tender_published_date": "2020-02-01",
            "award_date": "2020-06-15",
            "has_award": True,
            "fiscal_year": "FY 2020-21",
        },
        {
            "tender_id": "M4-FINAL-2",
            "buyer_name": "State Bridge Corp",
            "item_category": "Bridge Deck",
            "tender_estimate_inr": 10_000_000,
            "award_value_inr": 10_000_000,
            "final_value_inr": 13_500_000,
            "award_date": "2020-01-10",
            "final_cost_date": "2022-03-31",
            "has_award": True,
            "fiscal_year": "FY 2020-21",
        },
    ]
    res = run_engine("M4", records)
    by_tid = {lead.anomaly_metrics["tender_id"]: lead for lead in res.leads}

    est_lead = by_tid["M4-EST-1"]
    assert est_lead.anomaly_metrics["comparison_basis"] == "award_value_vs_tender_estimate"
    assert est_lead.anomaly_metrics["is_final_cost_overrun"] is False
    assert est_lead.anomaly_metrics["final_cost_data_available"] is False
    assert est_lead.anomaly_metrics["final_value_inr"] is None
    assert est_lead.anomaly_metrics["tender_estimate_date"] == "2020-02-01"
    assert est_lead.anomaly_metrics["award_date"] == "2020-06-15"
    assert est_lead.anomaly_metrics["final_cost_date"] is None

    fin_lead = by_tid["M4-FINAL-2"]
    assert fin_lead.anomaly_metrics["comparison_basis"] == "final_value_vs_award_value"
    assert fin_lead.anomaly_metrics["is_final_cost_overrun"] is True
    assert fin_lead.anomaly_metrics["final_value_inr"] == pytest.approx(13_500_000.0)
    assert fin_lead.anomaly_metrics["final_cost_date"] == "2022-03-31"


def test_m7_pmgsy_missing_physical_progress_and_mplads_deterministic_as_of_date() -> None:
    # 1. PMGSY district-phase aggregate with fallback ID + missing physical completion row
    pmgsy_rows = [
        {
            "STATE_NAME": "Bihar",
            "DISTRICT_NAME": "Arwal",
            "PMGSY_SCHEME": "PMGSY-II",
            "LENGTH_OF_ROAD_WORK_SANCTIONED": 100.0,
            "COST_OF_WORKS_SANCTIONED": 80.0,
            "LENGTH_OF_ROAD_WORK_COMPLETED": 11.0,
            "EXPENDITURE_OCCURED": 76.0,
        },
        # Missing LENGTH_OF_ROAD_WORK_COMPLETED MUST stay None -> unevaluable, NEVER defaulted to 0!
        {
            "STATE_NAME": "Bihar",
            "DISTRICT_NAME": "Gaya",
            "PMGSY_SCHEME": "PMGSY-III",
            "LENGTH_OF_ROAD_WORK_SANCTIONED": 90.0,
            "COST_OF_WORKS_SANCTIONED": 60.0,
            "LENGTH_OF_ROAD_WORK_COMPLETED": "",
            "EXPENDITURE_OCCURED": 58.0,
        },
    ]
    pmgsy_res = run_engine("M7", pmgsy_rows)
    assert len(pmgsy_res.leads) == 1
    assert pmgsy_res.unevaluable_count == 1
    assert pmgsy_res.cleared_count == 0
    m = pmgsy_res.leads[0].anomaly_metrics
    assert m["work_id"] == "PMGSY-II-Arwal"
    assert m["aggregate_level"] == "district_phase_aggregate"
    assert m["is_fallback_identifier"] is True
    assert m["identifier_source"] == "synthetic_fallback_phase_district"
    assert m["physical_unit"] == "kilometres (km)"
    assert "Crore" in m["financial_unit"]

    # 2. MPLADS never calls date.today(): undated ongoing work without as_of_date -> unevaluable!
    mplads_rows = [
        {
            "MP NAME": "MP Test",
            "WORK": "WS/MP1/2023-24/101 - Hall",
            "STATE": "Bihar",
            "CONSTITUENCY": "PATNA",
            "IDA": "DM PATNA",
            "RECOMMENDED DATE": "2023-06-01",
            "ALLOCATION AMOUNT": "2500000",
            "STATUS": "Ongoing",
        }
    ]
    no_date_res = run_engine("M7", mplads_rows)
    assert no_date_res.leads == []
    assert no_date_res.unevaluable_count == 1

    dated_res_1 = run_engine("M7", mplads_rows, as_of_date="2025-12-01")
    dated_res_2 = run_engine("M7", mplads_rows, as_of_date="2026-06-01")
    assert len(dated_res_1.leads) == 1
    assert dated_res_1.leads[0].anomaly_metrics["as_of_date"] == "2025-12-01"
    assert dated_res_1.as_of_date == "2025-12-01"
    assert dated_res_1.script_hash != dated_res_2.script_hash
    assert get_engine_fingerprint("M7", as_of_date="2025-12-01")["as_of_date"] == "2025-12-01"


def test_m8_normalized_name_join_ambiguity_and_exact_cin_corroboration(tmp_path: Path) -> None:
    eb_csv = tmp_path / "eb.csv"
    eb_csv.write_text(
        "\n".join(
            [
                "date_of_encashment,political_party_name,prefix,bond_number,amount,date_of_purchase,purchaser_name,cin",
                "12/Apr/2023,PARTY A,OC,101,\"1,00,00,000\",05/Apr/2023,ACME INFRA LIMITED,",
                "13/Apr/2023,PARTY B,OC,102,\"2,00,00,000\",06/Apr/2023,DUPLICATE GROUP LTD,",
                "14/Apr/2023,PARTY A,OC,103,\"3,00,00,000\",07/Apr/2023,EXACT CORP LTD,L11111MH2000PLC000111",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    csr_csv = tmp_path / "csr.csv"
    csr_csv.write_text(
        "\n".join(
            [
                "company_name,cin,csr_total,csr_total_rank",
                "ACME INFRA LTD.,L22222DL2001PLC000222,12.5,15",
                "DUPLICATE GROUP LIMITED,L33333MH2002PLC000333,8.0,40",
                "DUPLICATE GROUP LTD.,L44444MH2003PLC000444,9.0,35",
                "EXACT CORP LIMITED,L11111MH2000PLC000111,20.0,5",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    ds = load_dataset(str(eb_csv), context={"secondary_path": str(csr_csv)}, allow_local_files=True)
    res = run_engine("M8", ds.records)
    by_name = {lead.entity_name: lead for lead in res.leads}

    # Unambiguous normalized-name match: bond_source_cin=None, cin=None, matched_csr_cin populated
    acme = by_name["ACME INFRA LTD."]
    assert acme.anomaly_metrics["join_basis"] == "normalized_name"
    assert acme.anomaly_metrics["bond_source_cin"] is None
    assert acme.anomaly_metrics["cin"] is None
    assert acme.anomaly_metrics["matched_csr_cin"] == "L22222DL2001PLC000222"
    assert acme.anomaly_metrics["exact_identifier_verified"] is False
    assert acme.anomaly_metrics["join_ambiguous"] is False
    assert acme.anomaly_metrics["amount_type"] == "disclosed_bond_value"

    # Ambiguous normalized-name match: 2 candidate CSR CINs, matched_csr_cin=None
    dup = by_name["DUPLICATE GROUP LTD"]
    assert dup.anomaly_metrics["join_basis"] == "normalized_name"
    assert dup.anomaly_metrics["join_ambiguous"] is True
    assert dup.anomaly_metrics["join_candidate_count"] == 2
    assert set(dup.anomaly_metrics["join_candidate_cins"]) == {
        "L33333MH2002PLC000333",
        "L44444MH2003PLC000444",
    }
    assert dup.anomaly_metrics["matched_csr_cin"] is None

    # Corroborated primary CIN -> exact_cin
    exact = by_name["EXACT CORP LIMITED"]
    assert exact.anomaly_metrics["join_basis"] == "exact_cin"
    assert exact.anomaly_metrics["exact_identifier_verified"] is True
    assert exact.anomaly_metrics["bond_source_cin"] == "L11111MH2000PLC000111"
    assert exact.anomaly_metrics["cin"] == "L11111MH2000PLC000111"


# ===========================================================================
# Provenance, Digest Fail-Closed, Cache Safety, Defensive Loader & Quarantine
# ===========================================================================


def test_primary_and_secondary_digest_mismatch_fail_closed_loader_and_runner(tmp_path: Path) -> None:
    prim = tmp_path / "primary.csv"
    prim.write_text(
        "date_of_encashment,political_party_name,prefix,bond_number,amount,date_of_purchase,purchaser_name\n"
        "12/Apr/2023,PARTY A,OC,101,\"1,00,00,000\",05/Apr/2023,VEDANTA LIMITED\n",
        encoding="utf-8",
    )
    sec = tmp_path / "secondary.csv"
    sec.write_text("company_name,cin,csr_total,csr_total_rank\nVEDANTA LTD.,L13209MH1965PLC291394,14.3,22\n", encoding="utf-8")
    prim_sha = compute_sha256(prim.read_bytes())

    # 1. Loader fails closed on primary digest mismatch
    with pytest.raises(DigestMismatchError, match="Primary artifact SHA-256 mismatch"):
        load_dataset(str(prim), allow_local_files=True, expected_sha256="a" * 64)

    # 2. Loader fails closed on secondary digest mismatch even when primary matches!
    with pytest.raises(DigestMismatchError, match="Primary artifact SHA-256 mismatch"):
        load_dataset(
            str(prim),
            context={"secondary_path": str(sec)},
            allow_local_files=True,
            expected_sha256=prim_sha,
            secondary_expected_sha256="b" * 64,
        )

    # 3. Runner releases lease with reason="digest_mismatch" on anomaly_scan (NEVER submits!)
    released: list[dict[str, Any]] = []
    submitted: list[dict[str, Any]] = []

    def _hub(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/desk/beat":
            return httpx.Response(200, json={"status": "active"})
        if request.url.path == "/api/desk/release":
            released.append(json.loads(request.content))
            return httpx.Response(200, json={"status": "released"})
        if request.url.path == "/api/desk/submit":
            submitted.append(json.loads(request.content))
            return httpx.Response(200, json={"status": "accepted"})
        return httpx.Response(404)

    cfg = AgentConfig(handle="digest-guard", hub_url="http://mockhub", api_token="awd_tok")
    with httpx.Client(transport=httpx.MockTransport(_hub), base_url="http://mockhub") as client:
        runner = AgentRunner(cfg, http_client=client, allow_local_files=True)
        scan_res = runner.execute_task(
            {
                "lease_id": "lease-sec-bad",
                "task": {
                    "id": "tsk-sec-bad",
                    "task_type": "anomaly_scan",
                    "methodology_code": "M8",
                    "payload": {
                        "dataset_path": str(prim),
                        "expected_sha256": prim_sha,
                        "secondary_url": str(sec),
                        "secondary_expected_sha256": "c" * 64,
                    },
                },
            }
        )
        assert scan_res["status"] == "error"
        assert scan_res["reason"] == "digest_mismatch"
        assert len(released) == 1 and released[0]["reason"] == "digest_mismatch"
        assert submitted == []

        # 4. On peer_verify, secondary digest mismatch records bytes_digest="fail" and NEVER confirms!
        pv_res = runner.execute_task(
            {
                "lease_id": "lease-pv-sec-bad",
                "task": {
                    "id": "tsk-pv-sec-bad",
                    "task_type": "peer_verify",
                    "methodology_code": "M8",
                    "payload": {
                        "finding_id": "fnd-m8-sec",
                        "dataset_path": str(prim),
                        "artifact_sha256": prim_sha,
                        "secondary_url": str(sec),
                        "secondary_sha256": "d" * 64,
                        "methodology_code": "M8",
                        "title": "Disclosure / Proximity Lead: Electoral Bond Overlap",
                        "summary": "Neutral disclosure summary.",
                        "entity_name": "VEDANTA LTD.",
                        "rule_triggered": "M8",
                        "benign_explanations": ["Lawful corporate disclosure."],
                    },
                },
            }
        )
        assert pv_res["verification"]["verdict"] == "dispute"
        assert pv_res["verification"]["checks"]["bytes_digest"] == "fail"
        assert pv_res["verification"]["checks"]["deterministic_reproduction"] == "unrecorded"


def test_cache_revalidation_0600_permissions_and_no_raw_pii_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_dir = tmp_path / "secure_cache"
    monkeypatch.setenv("AIWATCHDOGS_CACHE_DIR", str(cache_dir))

    clean_body = b'[{"tender_id": "T-1", "buyer_name": "Clean Dept"}]'
    pii_body = b'[{"tender_id": "T-2", "contact": "citizen.pan@gmail.com PAN ABCDE1234F"}]'
    calls = {"clean": 0, "pii": 0}

    class _Resp:
        def __init__(self, body: bytes) -> None:
            self.status_code = 200
            self.content = body
            self.headers = {"content-type": "application/json"}

        def raise_for_status(self) -> None:
            pass

    def _mock_get(self: Any, url: str) -> _Resp:
        if "pii" in url:
            calls["pii"] += 1
            return _Resp(pii_body)
        calls["clean"] += 1
        return _Resp(clean_body)

    monkeypatch.setattr(httpx.Client, "get", _mock_get)

    clean_url = "https://openbudgetsindia.org/clean.json"
    ds1 = load_dataset(clean_url, use_cache=True)
    assert calls["clean"] == 1
    assert stat.S_IMODE(cache_dir.stat().st_mode) == 0o700
    url_hash = compute_sha256(clean_url)
    bin_file = cache_dir / f"{url_hash}.bin"
    assert bin_file.exists()
    assert stat.S_IMODE(bin_file.stat().st_mode) == 0o600

    # Tamper cached bytes -> SHA-256 revalidation MUST purge and refetch!
    bin_file.write_bytes(b"CORRUPTED_BYTES")
    ds2 = load_dataset(clean_url, use_cache=True)
    assert calls["clean"] == 2
    assert ds2.sha256 == ds1.sha256

    # Payload containing raw PII MUST NEVER be written to disk cache even with use_cache=True!
    pii_url = "https://openbudgetsindia.org/pii.json"
    ds_pii = load_dataset(pii_url, use_cache=True)
    assert "pan" in ds_pii.pii_redactions and "email" in ds_pii.pii_redactions
    pii_hash = compute_sha256(pii_url)
    assert not (cache_dir / f"{pii_hash}.bin").exists()


def test_unattended_local_files_blocked_and_ssrf_size_zipbomb_xxe_guards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_csv = tmp_path / "secret.csv"
    local_csv.write_text("a,b\n1,2\n", encoding="utf-8")

    # 1. Default allow_local_files=False blocks local paths AND context CANNOT override it!
    with pytest.raises(PermissionError):
        load_dataset(str(local_csv))
    with pytest.raises(PermissionError):
        load_dataset(str(local_csv), context={"allow_local_files": True})

    # Unattended AgentRunner defaults allow_local_files=False and blocks local paths
    released: list[dict[str, Any]] = []

    def _hub(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/desk/beat":
            return httpx.Response(200, json={"status": "active"})
        if request.url.path == "/api/desk/release":
            released.append(json.loads(request.content))
            return httpx.Response(200, json={"status": "released"})
        return httpx.Response(404)

    cfg = AgentConfig(handle="unattended-node", hub_url="http://mockhub", api_token="awd_tok")
    with httpx.Client(transport=httpx.MockTransport(_hub), base_url="http://mockhub") as client:
        runner = AgentRunner(cfg, http_client=client)
        assert runner.allow_local_files is False
        res = runner.execute_task(
            {
                "lease_id": "l-local-block",
                "task": {
                    "id": "t-local-block",
                    "task_type": "anomaly_scan",
                    "methodology_code": "M1",
                    "payload": {"dataset_path": str(local_csv), "context": {"allow_local_files": True}},
                },
            }
        )
        assert res["status"] == "error"
        assert "allow_local_files=False" in res["error"]
        assert len(released) == 1

    # 2. SSRF & non-HTTPS URL guards
    for bad_url in (
        "http://openbudgetsindia.org/data.csv",
        "https://localhost/secret.csv",
        "https://127.0.0.1/secret.csv",
        "https://169.254.169.254/latest/meta-data/",
        "https://10.0.0.5/internal.csv",
        "https://metadata.google.internal/computeMetadata/v1/",
        "https://user:pass@openbudgetsindia.org/data.csv",
    ):
        with pytest.raises(PermissionError):
            load_dataset(bad_url)

    # 3. Download size cap guard
    big_file = tmp_path / "too_big.csv"
    big_file.write_bytes(b"a,b\n" + b"1,2\n" * 500)
    with pytest.raises(ValueError, match="exceeds limit"):
        load_dataset(str(big_file), allow_local_files=True, context={"max_download_bytes": 100})

    # 4. ZIP decompression bomb (declared size AND compression ratio) guards
    bomb_zip = tmp_path / "bomb.json.zip"
    with zipfile.ZipFile(bomb_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("package.json", (" " * 500_000).encode("utf-8"))
    with pytest.raises(ValueError, match="exceeds remaining limit|decompressed size"):
        load_dataset(
            str(bomb_zip),
            allow_local_files=True,
            context={"max_compression_ratio": 1000.0, "max_decompressed_bytes": 100_000},
        )
    with pytest.raises(ValueError, match="compression ratio"):
        load_dataset(
            str(bomb_zip),
            allow_local_files=True,
            context={"max_compression_ratio": 20.0, "max_decompressed_bytes": 1_000_000},
        )

    # 5. XML DTD / ENTITY (XXE) rejection in XLSX
    xxe_xlsx = tmp_path / "xxe.xlsx"
    with zipfile.ZipFile(xxe_xlsx, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><workbook/>',
        )
    with pytest.raises(ValueError, match="DOCTYPE|ENTITY"):
        load_dataset(str(xxe_xlsx), allow_local_files=True)


def test_recursive_pii_scrubbing_and_flag_pii_or_legal_full_quarantine(tmp_path: Path) -> None:
    # 1. Recursive PII scrubbing across nested dicts, lists, and PII-bearing dict keys
    valid_aadhaar = "36759834601" + verhoeff_generate_check_digit("36759834601")
    nested = {
        "officer@gmail.com": {"pan_holder": "ABCDE1234F", "nested_list": [f"Aadhaar {valid_aadhaar}", "Phone +91 9876543210"]},
        "safe_metric": 42.5,
    }
    cleaned, tags = scrub_pii_in_obj(nested)
    assert "officer@gmail.com" not in cleaned
    assert "[REDACTED_EMAIL]" in cleaned
    assert "ABCDE1234F" not in json.dumps(cleaned)
    assert valid_aadhaar not in json.dumps(cleaned)
    assert "9876543210" not in json.dumps(cleaned)
    assert set(tags) >= {"email", "pan", "aadhaar", "phone"}

    # 2. Hub recursive scrubbing on submission + flag_pii_or_legal full quarantine
    app = create_app(db_path=tmp_path / "quar.db", seed=True)
    try:
        with TestClient(app) as client:
            author = _register(client, "q-author")
            verifier = _register(client, "q-verifier")

            _, fid, submit_resp = _submit_clean_finding(
                client,
                author,
                extra_finding={
                    "anomaly_metrics": {
                        "numerator": 81,
                        "nested": {"contact_email": "private.person@gmail.com", "pan": "ABCDE1234F"},
                    },
                    "agent_trace": {"debug_phone": "+91 9876543210"},
                },
            )
            assert set(submit_resp["pii_redactions"]) >= {"email", "pan", "phone"}

            before_detail = client.get(f"/api/findings/{fid}").json()
            assert "private.person@gmail.com" not in json.dumps(before_detail)
            assert "ABCDE1234F" not in json.dumps(before_detail)
            assert "9876543210" not in json.dumps(before_detail)

            # Peer verifier flags PII / legal issue -> IMMEDIATE QUARANTINE across ALL public endpoints!
            pv_claim = client.post(
                "/api/desk/next",
                headers={"X-Agent-Token": verifier["api_token"]},
                json={"handle": verifier["handle"], "mode": "verify"},
            )
            assert pv_claim.status_code == 200
            pv_sub = client.post(
                "/api/desk/submit",
                headers={"X-Agent-Token": verifier["api_token"]},
                json={
                    "lease_id": pv_claim.json()["lease_id"],
                    "outcome": "verification",
                    "verification": {
                        "finding_id": fid,
                        "verdict": "flag_pii_or_legal",
                        "checks": {"neutrality_privacy": "fail"},
                        "notes": "Potential personal identifier in source excerpt.",
                    },
                },
            )
            assert pv_sub.status_code == 200
            assert pv_sub.json()["finding_status"] == "quarantined"
            assert pv_sub.json()["quarantined"] is True

            # Excluded from /api/findings list, /api/head, and /api/graph
            assert client.get("/api/findings").json()["total"] == 0
            head = client.get("/api/head").json()
            assert head["recent_findings"] == []
            assert head["stats"]["findings_quarantined"] == 1
            graph = client.get("/api/graph").json()
            assert not any(n["id"] == f"finding:{fid}" for n in graph["nodes"])

            # GET /api/findings/{id} serves ONLY redacted quarantine shell
            q_detail = client.get(f"/api/findings/{fid}").json()
            assert q_detail["quarantined"] is True
            assert q_detail["status"] == "quarantined"
            assert q_detail["anomaly_metrics"] == {}
            assert q_detail["agent_trace"] == {}
            assert q_detail["citations"] == []
            assert q_detail["verifications"] == []
            assert q_detail["artifact"] is None
            assert q_detail["rti_draft"] is None
            assert "Jorhat Medical College" not in json.dumps(q_detail)

            # POST /api/findings/{id}/rti-draft returns HTTP 403
            rti_q = client.post(f"/api/findings/{fid}/rti-draft", json={})
            assert rti_q.status_code == 403
    finally:
        app.state.db.close()


def test_pinned_commit_urls_and_no_synthetic_artifact_sha256(tmp_path: Path) -> None:
    # All GitHub URLs in METHODOLOGY_CATALOG and REAL_AUDIT_TASKS are pinned to 40-char commit SHAs
    for code, meta in METHODOLOGY_CATALOG.items():
        url = meta["reference_dataset_url"]
        if "raw.githubusercontent.com" in url:
            assert _HEX40_RE.search(url), f"{code} URL not commit-pinned: {url}"
            assert "/main/" not in url and "/master/" not in url

    for task in REAL_AUDIT_TASKS:
        for key in ("dataset_url", "retrieval_url", "secondary_url"):
            url = task["payload"].get(key)
            if url and "raw.githubusercontent.com" in url:
                assert _HEX40_RE.search(url), f"{task['id']}.{key} not commit-pinned: {url}"
                assert "/main/" not in url and "/master/" not in url

    # Missing artifact_sha256 stays None on Hub submission (never synthetically fabricated!)
    app = create_app(db_path=tmp_path / "nosha.db", seed=True)
    try:
        with app.state.db.transaction() as conn:
            row = conn.execute("SELECT id, payload_json FROM tasks WHERE id = 'tsk-real-01'").fetchone()
            p = json.loads(row["payload_json"])
            p.pop("expected_sha256", None)
            conn.execute("UPDATE tasks SET payload_json = ? WHERE id = 'tsk-real-01'", (json.dumps(p),))
        with TestClient(app) as client:
            agent = _register(client, "nosha-agent")
            _, fid, _ = _submit_clean_finding(
                client, agent, extra_finding={"artifact_sha256": None}
            )
            detail = client.get(f"/api/findings/{fid}").json()
            assert detail["artifact_sha256"] is None
            assert detail["artifact"]["sha256"] is None
    finally:
        app.state.db.close()


# ===========================================================================
# Contributor Budget, Credentials, Continuous Cap, Dry-Run & Model Text
# ===========================================================================


def test_budget_guardrails_zero_dollar_opt_in_daily_ledger_and_model_text_separation(tmp_path: Path) -> None:
    lead = {
        "title": "Public Record Anomaly: Split-tendering cluster below INR 10L ceiling",
        "summary": "Deterministic scan flagged 4 POs totaling INR 38.40 lakh (96% of INR 10 lakh ceiling).",
        "methodology_code": "M2",
        "entity_name": "District Works Division",
        "jurisdiction": "Union of India",
        "fiscal_year": "FY2024-25",
        "monetary_exposure_inr": 3_840_000.0,
        "rule_triggered": "GFR-2017-R149",
        "anomaly_metrics": {"po_count": 4, "threshold_inr": 1_000_000, "pct": 96.0},
        "benign_explanations": ["Staggered departmental requirements."],
    }

    http_calls = {"n": 0}

    def _counting_openai(request: httpx.Request) -> httpx.Response:
        http_calls["n"] += 1
        return httpx.Response(
            200,
            json={
                "usage": {"prompt_tokens": 200, "completion_tokens": 100, "cost": 0.03},
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "editorial_interpretation": "Four orders totaling INR 38.40 lakh warrant routine file review.",
                                    "benign_explanations": ["Separate urgent sub-division indents."],
                                }
                            )
                        }
                    }
                ],
            },
        )

    openai_cfg = ProviderInfo(
        provider="openai",
        model="gpt-4o-mini",
        label="OpenAI",
        api_key_env="OPENAI_API_KEY",
        base_url="https://api.openai.com/v1",
    )

    # 1. Default AgentConfig is $0.00 & allow_paid_provider=False; detect_best_provider ignores cloud keys
    default_cfg = AgentConfig(handle="zero-cost-node")
    assert default_cfg.max_usd == 0.0
    assert default_cfg.allow_paid_provider is False
    best = detect_best_provider(probe_local=False, env={"OPENAI_API_KEY": "sk-live"}, allow_paid_provider=False)
    assert best.provider == RULE_ENGINE_PROVIDER

    with httpx.Client(transport=httpx.MockTransport(_counting_openai)) as client:
        # 2. Without explicit opt-in -> BLOCKED before dispatch (0 HTTP calls even with max_usd=5.0)
        res_no_opt = enrich_finding_summary(
            lead,
            openai_cfg,
            http_client=client,
            env={"OPENAI_API_KEY": "sk-test"},
            allow_paid_provider=False,
            max_usd=5.0,
        )
        assert res_no_opt["budget_blocked"] is True
        assert res_no_opt["fallback_reason"] == "paid_provider_opt_in_required"
        assert http_calls["n"] == 0

        # 3. With opt-in but max_usd=0.0 -> BLOCKED before dispatch (0 HTTP calls!)
        res_zero = enrich_finding_summary(
            lead,
            openai_cfg,
            http_client=client,
            env={"OPENAI_API_KEY": "sk-test"},
            allow_paid_provider=True,
            max_usd=0.0,
        )
        assert res_zero["budget_blocked"] is True
        assert res_zero["fallback_reason"] == "zero_budget_enforced"
        assert http_calls["n"] == 0

        # 4. With opt-in + positive session & daily cap -> tracks atomic 0o600 daily ledger and stops at cap
        ledger_path = tmp_path / "spend_ledger.json"
        tracker = BudgetTracker(
            session_max_usd=0.05,
            daily_max_usd=0.05,
            allow_paid_provider=True,
            ledger_path=ledger_path,
            date_utc="2026-11-24",
        )
        res_ok = enrich_finding_summary(
            lead,
            openai_cfg,
            http_client=client,
            env={"OPENAI_API_KEY": "sk-test"},
            budget_tracker=tracker,
        )
        assert res_ok["used_llm"] is True
        assert res_ok["actual_cost_usd"] == pytest.approx(0.03)
        assert res_ok["summary"] == lead["summary"]  # Verified deterministic summary NEVER overwritten!
        assert res_ok["editorial_interpretation"]["authoritative"] is False
        assert http_calls["n"] == 1
        assert stat.S_IMODE(ledger_path.stat().st_mode) == 0o600
        assert load_daily_spend(ledger_path, date_utc="2026-11-24") == pytest.approx(0.03)

        # Second call exceeds remaining $0.02? Estimated cost is tiny (~$0.0005), so it dispatches,
        # records another $0.03 (total $0.06 >= $0.05), hard-stops, and third call is strictly blocked!
        enrich_finding_summary(
            lead, openai_cfg, http_client=client, env={"OPENAI_API_KEY": "sk-test"}, budget_tracker=tracker
        )
        assert http_calls["n"] == 2
        assert tracker.hard_stopped is True
        res_stopped = enrich_finding_summary(
            lead, openai_cfg, http_client=client, env={"OPENAI_API_KEY": "sk-test"}, budget_tracker=tracker
        )
        assert res_stopped["budget_blocked"] is True
        assert http_calls["n"] == 2  # Zero extra HTTP call after hard stop!

    # 5. Model-written numeric hallucination rejected with numeric_contradiction while charging HTTP 200 cost
    def _hallucinating_openai(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "cost": 0.01},
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "editorial_interpretation": "Total outlay reached INR 999 crore (87.5% breach).",
                                    "benign_explanations": ["Lawful institutional review."],
                                }
                            )
                        }
                    }
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(_hallucinating_openai)) as client:
        res_bad_num = enrich_finding_summary(
            lead,
            openai_cfg,
            http_client=client,
            env={"OPENAI_API_KEY": "sk-test"},
            allow_paid_provider=True,
            max_usd=1.0,
        )
        assert res_bad_num["used_llm"] is False
        assert "numeric_contradiction" in (res_bad_num["fallback_reason"] or "")
        assert res_bad_num["summary"] == lead["summary"]
        assert res_bad_num["editorial_interpretation"] is None
        assert res_bad_num["actual_cost_usd"] == pytest.approx(0.01)


def test_atomic_credential_preservation_on_409_rejoin_and_origin_scoping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_file = tmp_path / "creds" / "config.json"
    monkeypatch.setenv(ENV_CONFIG_PATH, str(cfg_file))

    # 1. Origin normalization & strict token reuse scoping (matching origin AND handle only)
    assert normalize_hub_origin("HTTPS://AIWATCHDOGS.IN:443/") == "https://aiwatchdogs.in"
    assert normalize_hub_origin("http://Example.org:80/") == "http://example.org"
    existing = AgentConfig(
        handle="civic-keeper",
        hub_url="https://aiwatchdogs.in:443/",
        api_token="awd_original_secret_token",
    )
    assert resolve_reusable_token(existing, hub_url="https://aiwatchdogs.in", handle="CIVIC-KEEPER") == "awd_original_secret_token"
    assert resolve_reusable_token(existing, hub_url="https://other-hub.in", handle="civic-keeper") == ""
    assert resolve_reusable_token(existing, hub_url="https://aiwatchdogs.in", handle="other-handle") == ""

    # 2. Persist original credentials atomically (0o600)
    save_config(existing, cfg_file)
    original_bytes = cfg_file.read_bytes()
    assert stat.S_IMODE(cfg_file.stat().st_mode) == 0o600

    # 3. Attempt `aiwatchdogs join` with a handle collision (HTTP 409) -> MUST preserve config.json byte-for-byte!
    def _hub_409(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/agents/register":
            return httpx.Response(409, json={"detail": "Handle already registered"})
        return httpx.Response(404)

    orig_init = AgentRunner.__init__
    mock_client = httpx.Client(transport=httpx.MockTransport(_hub_409), base_url="https://mockhub")
    try:
        def _patched_init(self: AgentRunner, config: AgentConfig, **kwargs: Any) -> None:
            kwargs["http_client"] = mock_client
            orig_init(self, config, **kwargs)

        monkeypatch.setattr(AgentRunner, "__init__", _patched_init)
        monkeypatch.setattr("aiwatchdogs.cli.main.detect_providers", lambda **kw: [ProviderInfo(provider="rule-engine", model="deterministic-python-v1", label="CPU", is_local=True, zero_cost=True)])

        res = cli_runner.invoke(
            cli_app,
            [
                "join",
                "--hub",
                "https://mockhub",
                "--handle",
                "taken-handle",
                "--provider",
                "rule-engine",
                "--yes",
                "--no-run",
            ],
        )
        assert res.exit_code == 1
        assert cfg_file.read_bytes() == original_bytes
        reloaded = load_config(cfg_file)
        assert reloaded is not None and reloaded.api_token == "awd_original_secret_token"
    finally:
        mock_client.close()


def test_continuous_mode_strict_max_tasks_cap_and_dry_run_outbound_destinations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1. Continuous mode strictly enforces max_tasks even when queue briefly empties then refills
    clean_csv = tmp_path / "c.csv"
    clean_csv.write_text(
        "tender_id,buyer_name,vendor_name,item_category,award_date,award_value_inr,jurisdiction,fiscal_year\n"
        "PO-1,Clean Dept,V1,Books,2024-08-01,10000,Union of India,FY2024-25\n",
        encoding="utf-8",
    )
    queue_responses: list[dict[str, Any] | None] = [
        {
            "lease_id": "l-1",
            "task": {"id": "t-1", "task_type": "anomaly_scan", "methodology_code": "M2", "payload": {"dataset_path": str(clean_csv)}},
        },
        None,  # Empty queue poll -> sleeps briefly and retries
        {
            "lease_id": "l-2",
            "task": {"id": "t-2", "task_type": "anomaly_scan", "methodology_code": "M2", "payload": {"dataset_path": str(clean_csv)}},
        },
        {
            "lease_id": "l-3-must-never-claim",
            "task": {"id": "t-3", "task_type": "anomaly_scan", "methodology_code": "M2", "payload": {"dataset_path": str(clean_csv)}},
        },
    ]

    def _hub(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/desk/next":
            nxt = queue_responses.pop(0) if queue_responses else None
            return httpx.Response(204) if nxt is None else httpx.Response(200, json=nxt)
        if request.url.path == "/api/desk/beat":
            return httpx.Response(200, json={"status": "active"})
        if request.url.path == "/api/desk/submit":
            return httpx.Response(200, json={"status": "accepted"})
        return httpx.Response(404)

    cfg = AgentConfig(handle="cap-node", hub_url="http://mockhub", api_token="awd_tok", max_tasks=2)
    with httpx.Client(transport=httpx.MockTransport(_hub), base_url="http://mockhub") as client:
        runner = AgentRunner(cfg, http_client=client, poll_interval=0.01, allow_local_files=True)
        summary = runner.run_loop(max_tasks=2, continuous=True)
    assert summary.tasks_claimed == 2
    assert summary.tasks_completed == 2
    assert len(queue_responses) == 1  # 3rd task was NEVER claimed!

    # 2. Outbound destinations descriptor + --dry-run (writes zero files, makes zero network calls)
    cfg_file = tmp_path / "dryrun_config.json"
    monkeypatch.setenv(ENV_CONFIG_PATH, str(cfg_file))
    plan = describe_outbound_destinations(
        "https://aiwatchdogs.in",
        ProviderInfo(provider="openai", model="gpt-4o-mini", label="OpenAI", base_url="https://api.openai.com/v1"),
        allow_paid_provider=False,
        max_usd=0.0,
    )
    assert plan["llm_provider"]["status"] == "blocked_by_guardrail"
    assert plan["llm_provider"]["will_contact"] is False
    assert [h.get("destination") for h in plan["dataset_hosts"]] == ["The official host named in each task"]

    res_dry = cli_runner.invoke(cli_app, ["join", "--dry-run", "--provider", "rule-engine"], env={"COLUMNS": "220"})
    assert res_dry.exit_code == 0, res_dry.output
    assert "Dry-run complete" in res_dry.stdout
    assert "The official host named in each task" in res_dry.stdout
    assert not cfg_file.exists()


def test_interactive_audit_alias_allows_local_files_and_digest_verification(tmp_path: Path) -> None:
    csv_path = tmp_path / "local_audit.csv"
    csv_path.write_text(
        "tender_id,buyer_name,vendor_name,item_category,award_date,award_value_inr,jurisdiction,fiscal_year\n"
        "PO-1,Central Dept,V1,Kits,2024-08-01,4850000,Union of India,FY2024-25\n"
        "PO-2,Central Dept,V1,Kits,2024-08-10,4900000,Union of India,FY2024-25\n"
        "PO-3,Central Dept,V1,Kits,2024-08-20,4920000,Union of India,FY2024-25\n",
        encoding="utf-8",
    )
    good_sha = compute_sha256(csv_path.read_bytes())

    ok = cli_runner.invoke(
        cli_app,
        ["audit", str(csv_path), "--methodology", "M2", "--expected-sha256", good_sha, "--json"],
    )
    assert ok.exit_code == 0, ok.output
    assert len(json.loads(ok.stdout)["leads"]) == 1

    bad = cli_runner.invoke(
        cli_app,
        ["audit", str(csv_path), "--methodology", "M2", "--expected-sha256", "0" * 64, "--json"],
    )
    assert bad.exit_code == 1


# ===========================================================================
# State vs Central RTI, Non-Mutating Previews, Treasury, Migrations, Ops
# ===========================================================================


def test_state_vs_central_rti_routing_and_non_mutating_hub_previews(tmp_path: Path) -> None:
    # 1. Jurisdiction routing across states & unmapped state postal fallback (NEVER central portal for states!)
    for jur, expected_portal in [
        ("State:Assam", "https://rtionline.assam.gov.in"),
        ("State:Andhra Pradesh", "https://rtionline.ap.gov.in"),
        ("State:Bihar", "https://jaankari.bihar.gov.in"),
        ("State:Himachal Pradesh", "https://rtionline.hp.gov.in"),
        ("State:Madhya Pradesh", "https://rtionline.mp.gov.in"),
        ("State:Sikkim", ""),  # unmapped state -> postal fallback, NEVER rtionline.gov.in!
    ]:
        info = resolve_rti_jurisdiction({"jurisdiction": jur, "entity_name": "State Dept"})
        assert info["jurisdiction_level"] == "state"
        assert info["rti_portal_url"] == expected_portal
        assert info["rti_portal_url"] != "https://rtionline.gov.in"
        assert info["central_portal_warning"] is not None

    # 2. Hub non-mutating previews for unverified findings & custom authority overrides
    app = create_app(db_path=tmp_path / "rti.db", seed=True)
    try:
        with TestClient(app) as client:
            author = _register(client, "rti-author")
            _, fid, _ = _submit_clean_finding(client, author)

            # Unverified peer_review finding -> returns personal preview and writes 0 rows to rti_drafts!
            prev = client.post(f"/api/findings/{fid}/rti-draft", json={})
            assert prev.status_code == 200
            pdata = prev.json()
            assert pdata["is_preview"] is True
            assert pdata["persisted"] is False
            assert pdata["jurisdiction_level"] == "state"
            assert pdata["rti_portal_url"] == "https://rtionline.assam.gov.in"

            with app.state.db.read() as conn:
                cnt = conn.execute("SELECT COUNT(*) AS c FROM rti_drafts WHERE finding_id = ?", (fid,)).fetchone()["c"]
                assert cnt == 0
    finally:
        app.state.db.close()


def test_treasury_pledges_vs_reconciled_cash_and_in_kind_compute(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "treasury.db", seed=True)
    try:
        with TestClient(app) as client:
            # 1. Self-declared cash pledge -> settlement_status='pledged', zero received cash
            p1 = client.post(
                "/api/sponsors/pledge",
                json={"name": "Civic Backer", "amount_inr": 50000, "tier": "patron"},
            )
            assert p1.status_code == 201
            assert p1.json()["ledger_entry"]["settlement_status"] == "pledged"
            assert p1.json()["ledger_entry"]["receipt_ref"].startswith("PLEDGE-")

            # 2. In-kind compute commitment -> settlement_status='in_kind_committed'
            p2 = client.post(
                "/api/sponsors/pledge",
                json={
                    "name": "Open Compute Lab",
                    "amount_inr": 0,
                    "in_kind_value_inr": 75000,
                    "compute_credits_desc": "500 GPU hours",
                },
            )
            assert p2.status_code == 201
            assert p2.json()["ledger_entry"]["settlement_status"] == "in_kind_committed"

            # 3. Reconciled cash receipt inserted into treasury_ledger DOES count in received cash
            with app.state.db.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO treasury_ledger (
                        id, entry_date, entry_type, direction, settlement_status, category,
                        counterparty, description, amount_inr, compute_credit_units, receipt_ref,
                        reconciled_receipt_ref, reconciled_at, sponsor_id, is_demo_entry, created_at
                    ) VALUES (
                        'led-reconciled-1', '2026-11-24', 'donation_inr', 'inflow', 'received',
                        'Reconciled Grant', 'Open Audit Trust', 'Bank-reconciled transfer',
                        100000.0, '', 'RCPT-BANK-001', 'UTR-202611240001', '2026-11-24T10:00:00Z',
                        NULL, 0, '2026-11-24T10:00:00Z'
                    )
                    """
                )

            tr = client.get("/api/treasury").json()["summary"]
            assert tr["pledged_inr"] == pytest.approx(50000.0)
            assert tr["in_kind_compute_inr"] == pytest.approx(75000.0)
            assert tr["received_cash_inr"] == pytest.approx(100000.0)
            assert tr["net_cash_balance_inr"] == pytest.approx(100000.0)
            assert tr["total_inflow_inr"] == pytest.approx(100000.0)
    finally:
        app.state.db.close()


def test_legacy_v0_to_v4_migration_idempotency_and_four_live_dossier_corrections(tmp_path: Path) -> None:
    legacy_db_path = tmp_path / "legacy_v0.db"
    # Build an authentic pre-migration v0 SQLite schema (without schema_migrations or v2 columns/tables)
    raw_conn = sqlite3.connect(str(legacy_db_path))
    raw_conn.executescript(
        """
        CREATE TABLE public_sources (
            id TEXT PRIMARY KEY, slug TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
            pillar TEXT NOT NULL, authority TEXT NOT NULL, base_url TEXT NOT NULL,
            ingestion_tier INTEGER NOT NULL, connector_type TEXT NOT NULL,
            data_contents TEXT NOT NULL DEFAULT '', format_access TEXT NOT NULL DEFAULT '',
            config_json TEXT NOT NULL DEFAULT '{}', robots_policy TEXT NOT NULL DEFAULT 'strict-polite',
            last_synced_at TEXT
        );
        CREATE TABLE missions (
            id TEXT PRIMARY KEY, slug TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
            description TEXT NOT NULL, pillar TEXT NOT NULL, methodology_code TEXT NOT NULL,
            jurisdiction TEXT NOT NULL, ministry_or_authority TEXT NOT NULL,
            statutory_anchors_json TEXT NOT NULL DEFAULT '[]', source_ids_json TEXT NOT NULL DEFAULT '[]',
            sample_dataset_path TEXT NOT NULL DEFAULT '', data_mode TEXT NOT NULL DEFAULT 'live_public',
            status TEXT NOT NULL DEFAULT 'active', priority INTEGER NOT NULL DEFAULT 1,
            total_tasks INTEGER NOT NULL DEFAULT 0, completed_tasks INTEGER NOT NULL DEFAULT 0,
            verified_findings_count INTEGER NOT NULL DEFAULT 0, cleared_count INTEGER NOT NULL DEFAULT 0,
            monetary_audited_inr REAL NOT NULL DEFAULT 0.0, created_at TEXT NOT NULL
        );
        CREATE TABLE agents (
            id TEXT PRIMARY KEY, handle TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL,
            provider TEXT NOT NULL, model TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'auto',
            region TEXT NOT NULL DEFAULT 'IN', capabilities_json TEXT NOT NULL DEFAULT '[]',
            api_token_hash TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
            tasks_completed INTEGER NOT NULL DEFAULT 0, findings_submitted INTEGER NOT NULL DEFAULT 0,
            findings_verified INTEGER NOT NULL DEFAULT 0, verifications_done INTEGER NOT NULL DEFAULT 0,
            cleared_submitted INTEGER NOT NULL DEFAULT 0, compute_score INTEGER NOT NULL DEFAULT 0,
            hourly_score INTEGER NOT NULL DEFAULT 0, current_task_id TEXT,
            joined_at TEXT NOT NULL, last_heartbeat_at TEXT NOT NULL
        );
        CREATE TABLE evidence_artifacts (
            id TEXT PRIMARY KEY, source_id TEXT, mission_id TEXT, title TEXT NOT NULL,
            canonical_url TEXT NOT NULL, archive_url TEXT, local_path TEXT,
            file_format TEXT NOT NULL, sha256 TEXT, byte_size INTEGER NOT NULL DEFAULT 0,
            is_demo_sample INTEGER NOT NULL DEFAULT 0, pii_scrubbed INTEGER NOT NULL DEFAULT 1,
            fetched_at TEXT NOT NULL, parsed_metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, mission_slug TEXT NOT NULL,
            methodology_code TEXT NOT NULL, task_type TEXT NOT NULL, title TEXT NOT NULL,
            instructions TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}',
            target_finding_id TEXT, excluded_handles_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'queued', claimed_by_agent_id TEXT,
            claimed_by_handle TEXT, lease_id TEXT UNIQUE, lease_expires_at TEXT,
            progress_pct INTEGER NOT NULL DEFAULT 0, attempt_count INTEGER NOT NULL DEFAULT 0,
            output_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT
        );
        CREATE TABLE findings (
            id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, mission_slug TEXT NOT NULL,
            pillar TEXT NOT NULL, task_id TEXT, artifact_id TEXT, title TEXT NOT NULL,
            summary TEXT NOT NULL, methodology_code TEXT NOT NULL, entity_name TEXT NOT NULL,
            jurisdiction TEXT NOT NULL, fiscal_year TEXT NOT NULL, monetary_exposure_inr REAL NOT NULL DEFAULT 0.0,
            severity TEXT NOT NULL DEFAULT 'medium', anomaly_metrics_json TEXT NOT NULL DEFAULT '{}',
            rule_triggered TEXT NOT NULL DEFAULT '', source_url TEXT NOT NULL DEFAULT '',
            artifact_sha256 TEXT, dataset_path TEXT NOT NULL DEFAULT '',
            is_demo_sample INTEGER NOT NULL DEFAULT 0, citations_json TEXT NOT NULL DEFAULT '[]',
            benign_explanations_json TEXT NOT NULL DEFAULT '[]', suggested_authority TEXT NOT NULL DEFAULT '',
            submitted_by TEXT NOT NULL, submitted_by_agent_id TEXT,
            agent_trace_json TEXT NOT NULL DEFAULT '{}', legal_lint_passed INTEGER NOT NULL DEFAULT 1,
            pii_scrubbed INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'peer_review',
            verification_score REAL NOT NULL DEFAULT 0.0, confirm_count INTEGER NOT NULL DEFAULT 0,
            dispute_count INTEGER NOT NULL DEFAULT 0, required_quorum INTEGER NOT NULL DEFAULT 2,
            created_at TEXT NOT NULL, verified_at TEXT
        );
        CREATE TABLE peer_verifications (
            id TEXT PRIMARY KEY, finding_id TEXT NOT NULL, verifier_node_id TEXT NOT NULL,
            contributor_id TEXT NOT NULL, verifier_handle TEXT NOT NULL, provider TEXT NOT NULL DEFAULT '',
            model_id TEXT NOT NULL DEFAULT '', verdict TEXT NOT NULL, checks_json TEXT NOT NULL DEFAULT '{}',
            reproduced_metrics_json TEXT NOT NULL DEFAULT '{}', confidence REAL NOT NULL DEFAULT 0.9,
            notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, UNIQUE(finding_id, contributor_id)
        );
        CREATE TABLE rti_drafts (
            id TEXT PRIMARY KEY, finding_id TEXT UNIQUE NOT NULL, public_authority TEXT NOT NULL,
            cpio_designation TEXT NOT NULL, rti_portal_url TEXT NOT NULL DEFAULT '',
            statutory_sections_json TEXT NOT NULL DEFAULT '[]', subject TEXT NOT NULL,
            body_text TEXT NOT NULL, word_count INTEGER NOT NULL,
            requested_records_json TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'draft',
            filed_date TEXT, due_date_30d TEXT, official_reply_artifact_id TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE whispers (
            id TEXT PRIMARY KEY, agent_handle TEXT NOT NULL, provider TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '', mission_slug TEXT, finding_id TEXT,
            event_type TEXT NOT NULL DEFAULT 'whisper', message TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE sponsors (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, handle_or_org TEXT,
            tier TEXT NOT NULL DEFAULT 'citizen_backer', amount_inr REAL NOT NULL DEFAULT 0.0,
            compute_credits_desc TEXT NOT NULL DEFAULT '', url TEXT, message TEXT NOT NULL DEFAULT '',
            featured INTEGER NOT NULL DEFAULT 0, is_demo_entry INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        );
        CREATE TABLE treasury_ledger (
            id TEXT PRIMARY KEY, entry_date TEXT NOT NULL, entry_type TEXT NOT NULL,
            direction TEXT NOT NULL, category TEXT NOT NULL, counterparty TEXT NOT NULL,
            description TEXT NOT NULL, amount_inr REAL NOT NULL,
            compute_credit_units TEXT NOT NULL DEFAULT '', receipt_ref TEXT NOT NULL,
            is_demo_entry INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        );
        """
    )
    # Seed legacy rows including all 4 audited live dossiers + shared RTI drafts + legacy pledge
    raw_conn.execute(
        "INSERT INTO missions (id, slug, title, description, pillar, methodology_code, jurisdiction, ministry_or_authority, verified_findings_count, created_at) "
        "VALUES ('mission-m6', 'cag-union-state-audit-paras', 'CAG', 'CAG', 'audit', 'M6', 'Union of India', 'CAG', 1, '2026-11-20T00:00:00Z')"
    )
    raw_conn.execute(
        "INSERT INTO missions (id, slug, title, description, pillar, methodology_code, jurisdiction, ministry_or_authority, verified_findings_count, created_at) "
        "VALUES ('mission-m2', 'assam-gfr-split-tendering', 'M2', 'M2', 'procurement', 'M2', 'State:Assam', 'Assam', 1, '2026-11-20T00:00:00Z')"
    )
    raw_conn.execute(
        "INSERT INTO missions (id, slug, title, description, pillar, methodology_code, jurisdiction, ministry_or_authority, verified_findings_count, created_at) "
        "VALUES ('mission-m5', 'fiscal-march-rush-budgets', 'M5', 'M5', 'budget', 'M5', 'All-India', 'MoF', 2, '2026-11-20T00:00:00Z')"
    )
    raw_conn.execute(
        "INSERT INTO agents (id, handle, display_name, provider, model, api_token_hash, joined_at, last_heartbeat_at) "
        "VALUES ('agt-legacy', 'legacy-node', 'Legacy', 'rule-engine', 'v1', 'h', '2026-11-20T00:00:00Z', '2026-11-20T00:00:00Z')"
    )
    for fid, mid, slug, mcode in (
        ("fnd-2151675fe8f4", "mission-m6", "cag-union-state-audit-paras", "M6"),
        ("fnd-25c6fbd61b32", "mission-m2", "assam-gfr-split-tendering", "M2"),
        ("fnd-b6d573296cef", "mission-m5", "fiscal-march-rush-budgets", "M5"),
        ("fnd-bbc65f1fb61a", "mission-m5", "fiscal-march-rush-budgets", "M5"),
    ):
        raw_conn.execute(
            "INSERT INTO findings (id, mission_id, mission_slug, pillar, title, summary, methodology_code, entity_name, jurisdiction, fiscal_year, submitted_by, submitted_by_agent_id, status, confirm_count, created_at, verified_at) "
            "VALUES (?, ?, ?, 'audit', 'Legacy Title', 'Legacy Summary', ?, 'Entity', 'Union of India', 'FY2024-25', 'legacy-node', 'agt-legacy', 'verified', 2, '2026-11-20T00:00:00Z', '2026-11-20T01:00:00Z')",
            (fid, mid, slug, mcode),
        )
        raw_conn.execute(
            "INSERT INTO rti_drafts (id, finding_id, public_authority, cpio_designation, rti_portal_url, subject, body_text, word_count, status, created_at) "
            "VALUES (?, ?, 'Authority', 'CPIO', 'https://rtionline.gov.in', 'Subj', 'Body', 10, 'draft', '2026-11-20T01:00:00Z')",
            (f"rti-{fid}", fid),
        )
    raw_conn.execute(
        "INSERT INTO treasury_ledger (id, entry_date, entry_type, direction, category, counterparty, description, amount_inr, receipt_ref, created_at) "
        "VALUES ('led-old-pledge', '2026-11-20', 'donation_inr', 'inflow', 'Sponsor Pledge', 'Old Pledge', 'Self declared', 15000.0, 'RCPT-LIVE-1234', '2026-11-20T00:00:00Z')"
    )
    raw_conn.commit()
    raw_conn.close()

    # Open with new Database + seed_database -> runs migrations v1..v4 + versioned seed upsert!
    db = Database(db_path=legacy_db_path)
    try:
        assert db.schema_version() == LATEST_SCHEMA_VERSION
        assert seed_database(db) is True
        # Second run is completely idempotent!
        db.init_schema()
        assert seed_database(db) is False

        with db.read() as conn:
            # All 4 live dossiers have named editorial corrections & updated statuses + RTI holds
            expected_statuses = {
                "fnd-2151675fe8f4": ("retracted", "retracted"),
                "fnd-25c6fbd61b32": ("retracted", "corrected"),
                "fnd-b6d573296cef": ("under_review", "under_review"),
                "fnd-bbc65f1fb61a": ("under_review", "corrected"),
            }
            for fid, (exp_status, exp_ed) in expected_statuses.items():
                frow = conn.execute("SELECT status, editorial_status FROM findings WHERE id = ?", (fid,)).fetchone()
                assert (frow["status"], frow["editorial_status"]) == (exp_status, exp_ed)
                corr = conn.execute("SELECT * FROM finding_corrections WHERE finding_id = ?", (fid,)).fetchall()
                assert len(corr) == 1
                rti = conn.execute("SELECT status, version, editorial_notice FROM rti_drafts WHERE finding_id = ?", (fid,)).fetchone()
                assert rti["status"] == "editorial_hold"
                assert rti["version"] == 2
                assert "EDITORIAL HOLD" in rti["editorial_notice"]
                vers = conn.execute("SELECT * FROM rti_draft_versions WHERE finding_id = ?", (fid,)).fetchall()
                assert len(vers) == 1

            # Mission verified counts recalculated accurately to 0
            for mid in ("mission-m2", "mission-m5", "mission-m6"):
                mcnt = conn.execute("SELECT verified_findings_count FROM missions WHERE id = ?", (mid,)).fetchone()["verified_findings_count"]
                assert mcnt == 0

            # Legacy self-declared pledge reconciled to 'pledged'
            lrow = conn.execute("SELECT settlement_status FROM treasury_ledger WHERE id = 'led-old-pledge'").fetchone()
            assert lrow["settlement_status"] == "pledged"

            # Historical score_events backfilled
            se_cnt = conn.execute("SELECT COUNT(*) AS c FROM score_events WHERE agent_handle = 'legacy-node'").fetchone()["c"]
            assert se_cnt >= 4
    finally:
        db.close()


def test_vercel_fail_closed_503_and_unhealthy_db_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1. Database fails closed when VERCEL=1 without DATABASE_URL / POSTGRES_URL
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.delenv("AIWATCHDOGS_ALLOW_EPHEMERAL_SQLITE", raising=False)

    with pytest.raises(RuntimeError, match="Durable PostgreSQL storage"):
        Database()

    # 2. api/index.py serves create_degraded_app returning HTTP 503 on /health and /api/*
    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    if "api.index" in sys.modules:
        del sys.modules["api.index"]
    api_mod = importlib.import_module("api.index")
    with TestClient(api_mod.app) as degraded_client:
        h = degraded_client.get("/health")
        assert h.status_code == 503
        assert h.json()["status"] == "degraded"
        assert h.json()["db_ok"] is False
        a = degraded_client.get("/api/head")
        assert a.status_code == 503
    del sys.modules["api.index"]

    # 3. Healthy app returns HTTP 503 on /health when DB health_check fails
    monkeypatch.delenv("VERCEL", raising=False)
    app = create_app(db_path=tmp_path / "health503.db", seed=False)
    try:
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
            monkeypatch.setattr(app.state.db, "health_check", lambda: False)
            unhealthy = client.get("/health")
            assert unhealthy.status_code == 503
            assert unhealthy.json()["status"] == "degraded"
            assert unhealthy.json()["db_ok"] is False
    finally:
        app.state.db.close()

    # 4. Self-healing degraded app recovers automatically after a transient startup failure
    from aiwatchdogs.hub.app import create_degraded_app

    attempts = {"count": 0}

    def _flaky_factory() -> Any:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("transient lock timeout")
        return create_app(db_path=tmp_path / "self_heal.db", seed=False)

    healing_app = create_degraded_app(
        reason="Transient cold-start DB error",
        retry_factory=_flaky_factory,
        retry_cooldown_s=0.0,
    )
    with TestClient(healing_app) as heal_client:
        # First retry attempt still fails -> HTTP 503 degraded with security headers
        r1 = heal_client.get("/health")
        assert r1.status_code == 503
        assert r1.json()["status"] == "degraded"
        assert r1.headers.get("x-content-type-options") == "nosniff"
        # Second attempt succeeds -> immediately returns HTTP 200 healthy!
        r2 = heal_client.get("/health")
        assert r2.status_code == 200
        assert r2.json()["status"] == "ok"
        assert r2.json()["db_ok"] is True
        # Subsequent requests stay pinned to the recovered app (no re-init)
        r3 = heal_client.get("/health")
        assert r3.status_code == 200
        assert attempts["count"] == 2


def test_lease_auth_server_authoritative_binding_idempotency_and_backoff_adjudication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AIWATCHDOGS_RETRY_BASE_SECONDS", "0")
    app = create_app(db_path=tmp_path / "ops.db", seed=True)
    try:
        with TestClient(app) as client:
            a1 = _register(client, "ops-agent-1")
            a2 = _register(client, "ops-agent-2")

            # 1. Missing/invalid X-Agent-Token -> 401
            assert client.post("/api/desk/next", json={"handle": "ops-agent-1"}).status_code == 401
            assert client.post(
                "/api/desk/next", headers={"X-Agent-Token": "awd_invalid"}, json={"handle": "ops-agent-1"}
            ).status_code == 401

            claim = client.post(
                "/api/desk/next",
                headers={"X-Agent-Token": a1["api_token"]},
                json={"handle": "ops-agent-1", "mode": "investigate", "preferred_mission": "assam-ocds-single-bid"},
            )
            assert claim.status_code == 200
            lease_id = claim.json()["lease_id"]
            task_id = claim.json()["task"]["id"]

            # 2. Wrong lease owner -> 403 on beat/release/submit
            assert client.post(
                "/api/desk/beat",
                headers={"X-Agent-Token": a2["api_token"]},
                json={"lease_id": lease_id},
            ).status_code == 403
            assert client.post(
                "/api/desk/submit",
                headers={"X-Agent-Token": a2["api_token"]},
                json={"lease_id": lease_id, "outcome": "cleared"},
            ).status_code == 403

            # 3. Server-authoritative task type enforcement: submitting 'verification' on anomaly_scan -> 422
            assert client.post(
                "/api/desk/submit",
                headers={"X-Agent-Token": a1["api_token"]},
                json={"lease_id": lease_id, "outcome": "verification", "verification": {"finding_id": "f1", "verdict": "confirm"}},
            ).status_code == 422

            # 4. Atomic single-settlement + Idempotency-Key replay (no double-crediting!)
            idem_key = "idem-clear-001"
            s1 = client.post(
                "/api/desk/submit",
                headers={"X-Agent-Token": a1["api_token"], "Idempotency-Key": idem_key},
                json={"lease_id": lease_id, "outcome": "cleared", "metrics": {"cleared_count": 5}},
            )
            assert s1.status_code == 200
            s2 = client.post(
                "/api/desk/submit",
                headers={"X-Agent-Token": a1["api_token"], "Idempotency-Key": idem_key},
                json={"lease_id": lease_id, "outcome": "cleared", "metrics": {"cleared_count": 5}},
            )
            assert s2.status_code == 200
            assert s2.json()["idempotent_replay"] is True
            assert s2.json()["task_id"] == task_id

            # Without matching idempotency key -> 410 Gone
            s3 = client.post(
                "/api/desk/submit",
                headers={"X-Agent-Token": a1["api_token"], "Idempotency-Key": "different-key"},
                json={"lease_id": lease_id, "outcome": "cleared"},
            )
            assert s3.status_code == 410

            # Score & completed count incremented ONLY ONCE!
            prof = client.get("/api/contributors/ops-agent-1").json()
            assert prof["tasks_completed"] == 1
            assert prof["compute_score"] == 10

            # 5. Expired lease -> 410 Gone
            c_exp = client.post(
                "/api/desk/next",
                headers={"X-Agent-Token": a1["api_token"]},
                json={"handle": "ops-agent-1", "mode": "investigate", "preferred_mission": "assam-gfr-split-tendering"},
            )
            exp_lease = c_exp.json()["lease_id"]
            with app.state.db.transaction() as conn:
                conn.execute("UPDATE tasks SET lease_expires_at = '2020-01-01T00:00:00Z' WHERE lease_id = ?", (exp_lease,))
            assert client.post(
                "/api/desk/submit",
                headers={"X-Agent-Token": a1["api_token"]},
                json={"lease_id": exp_lease, "outcome": "cleared"},
            ).status_code == 410

            # 6. Retry the same task until its third release requires adjudication.
            retry_task_id = None
            for attempt in range(1, 4):
                c_rel = client.post(
                    "/api/desk/next",
                    headers={"X-Agent-Token": a1["api_token"]},
                    json={"handle": "ops-agent-1", "mode": "investigate", "preferred_mission": "ocds-cost-escalation-watch"},
                )
                assert c_rel.status_code == 200
                lease = c_rel.json()
                if retry_task_id is None:
                    retry_task_id = lease["task"]["id"]
                assert lease["task"]["id"] == retry_task_id
                rel = client.post(
                    "/api/desk/release",
                    headers={"X-Agent-Token": a1["api_token"]},
                    json={"lease_id": lease["lease_id"], "reason": "transient_err"},
                )
                assert rel.status_code == 200
                assert rel.json()["retry_count"] == attempt
                if attempt < 3:
                    assert rel.json()["task_status"] == "queued"
                else:
                    assert rel.json()["task_status"] == "needs_adjudication"
    finally:
        app.state.db.close()


def test_durable_events_sse_replay_findings_pagination_event_scores_and_join_sh(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "events.db", seed=True)
    try:
        with TestClient(app) as client:
            # 1. Durable /api/events?since_id=... & SSE Last-Event-ID replay
            a1 = _register(client, "ev-agent-1")
            ev_all = client.get("/api/events?since_id=0").json()
            assert len(ev_all["events"]) >= 2
            ids = [e["id"] for e in ev_all["events"]]
            assert ids == sorted(ids)
            first_id = ids[0]

            ev_since = client.get(f"/api/events?since_id={first_id}").json()
            assert all(e["id"] > first_id for e in ev_since["events"])

            with client.stream(
                "GET",
                "/api/stream?max_events=2",
                headers={"Last-Event-ID": str(first_id)},
            ) as stream:
                assert stream.status_code == 200
                body = "".join(stream.iter_text())
                assert "event: connected" in body
                assert f"id: {ids[1]}" in body

            # 2. /api/findings pagination (total, limit, offset, has_more)
            for slug in ("assam-ocds-single-bid", "assam-gfr-split-tendering", "ocds-vendor-concentration"):
                _submit_clean_finding(client, a1, preferred_mission=slug)

            page1 = client.get("/api/findings?limit=2&offset=0").json()
            assert page1["total"] == 3
            assert len(page1["findings"]) == 2
            assert page1["limit"] == 2
            assert page1["offset"] == 0
            assert page1["has_more"] is True

            page2 = client.get("/api/findings?limit=2&offset=2").json()
            assert page2["total"] == 3
            assert len(page2["findings"]) == 1
            assert page2["has_more"] is False

            # 3. Event-derived leaderboard scores (all-time vs rolling 1-hour window)
            old_ts = (datetime.now(timezone.utc) - timedelta(hours=3)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            with app.state.db.transaction() as conn:
                conn.execute(
                    "UPDATE score_events SET created_at = ? WHERE id = (SELECT id FROM score_events ORDER BY id ASC LIMIT 1)",
                    (old_ts,),
                )
            all_board = client.get("/api/contributors?window=all").json()
            hour_board = client.get("/api/contributors?window=hour").json()
            assert all_board["contributors"][0]["score"] == 30
            assert hour_board["contributors"][0]["score"] == 20  # 1 event aged out of rolling 1h window!

            # 4. /join.sh POSIX syntax (`sh -n`), /dev/tty consent reopen, and pinned git uvx install
            join_sh = client.get("/join.sh").text
            assert "exec </dev/tty" in join_sh
            assert "git+https://github.com/aiwatchdogsindia/aiwatchdogs.in.git" in join_sh
            assert "uvx aiwatchdogs" not in join_sh
            sh_check = subprocess.run(["sh", "-n"], input=join_sh, text=True, capture_output=True, check=False)
            assert sh_check.returncode == 0, sh_check.stderr
    finally:
        app.state.db.close()


def test_finding_patterns_group_confirmed_findings_by_department(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "patterns.db", seed=True)
    try:
        with TestClient(app) as client:
            agent = _register(client, "pattern-agent")
            ids = []
            for slug in ("assam-ocds-single-bid", "assam-gfr-split-tendering", "ocds-vendor-concentration"):
                ids.append(_submit_clean_finding(client, agent, preferred_mission=slug)[1])
            assert client.get("/api/findings/patterns").json()["finding_count"] == 0

            with app.state.db.transaction() as conn:
                conn.execute(
                    "UPDATE findings SET status = 'verified', entity_name = ?, monetary_exposure_inr = ? WHERE id = ?",
                    ("Scheme A (Department of Higher Education)", 5_00_00_000.0, ids[0]),
                )
                conn.execute(
                    "UPDATE findings SET status = 'verified', entity_name = ?, monetary_exposure_inr = ?, "
                    "mission_slug = (SELECT mission_slug FROM findings WHERE id = ?), "
                    "methodology_code = (SELECT methodology_code FROM findings WHERE id = ?) WHERE id = ?",
                    ("Scheme B (Department of Higher Education)", 9_00_00_000.0, ids[0], ids[0], ids[1]),
                )
            app.state.desk._patterns_cache = None

            resp = client.get("/api/findings/patterns")
            assert resp.status_code == 200
            assert "s-maxage=60" in resp.headers["cache-control"]
            body = resp.json()
            assert body["finding_count"] == 2
            assert body["pattern_count"] == 1
            top = body["patterns"][0]
            assert top["group"] == "Department of Higher Education"
            assert top["group_kind"] == "department"
            assert top["count"] == 2
            assert top["total_inr"] == 14_00_00_000.0
            assert [it["label"] for it in top["items"]] == ["Scheme B", "Scheme A"]
            assert sum(body["by_methodology"].values()) == 2
            assert sum(body["by_jurisdiction"].values()) == 2

            with app.state.db.transaction() as conn:
                conn.execute("UPDATE findings SET status = 'rti_filed' WHERE id = ?", (ids[1],))
            scoped = (
                f"/api/findings?status=confirmed&mission={top['mission_slug']}"
                f"&methodology={top['methodology_code']}&group=Department of Higher Education&group_kind=department"
            )
            listed = client.get(scoped).json()
            assert listed["total"] == 2
            assert {f["id"] for f in listed["findings"]} == {ids[0], ids[1]}
            narrowed = client.get(scoped + "&search=Scheme B").json()
            assert [f["id"] for f in narrowed["findings"]] == [ids[1]]
            other = client.get(scoped.replace("Higher Education", "Fertilisers")).json()
            assert other["total"] == 0

            ranked = client.get("/api/findings?status=confirmed&sort=amount").json()
            assert [f["id"] for f in ranked["findings"]] == [ids[1], ids[0]]
            place = ranked["findings"][0]["jurisdiction"]
            assert client.get(f"/api/findings?status=confirmed&jurisdiction={place}").json()["total"] == 2
            assert client.get("/api/findings?status=confirmed&jurisdiction=Nowhere").json()["total"] == 0
            assert client.get("/api/findings?sort=bogus").status_code == 422

            with app.state.db.transaction() as conn:
                conn.execute(
                    "UPDATE findings SET verified_at = '2026-01-01T00:00:00Z' WHERE id = ?", (ids[0],)
                )
                conn.execute(
                    "UPDATE findings SET verified_at = '2026-06-01T00:00:00Z' WHERE id = ?", (ids[1],)
                )
            recent = client.get("/api/findings?status=confirmed&sort=recent").json()
            assert [f["id"] for f in recent["findings"]] == [ids[1], ids[0]]
            default_order = client.get("/api/findings?status=confirmed").json()
            assert [f["id"] for f in default_order["findings"]] == [ids[0], ids[1]]
            assert client.get(f"/api/findings/{ids[0]}").status_code == 200
    finally:
        app.state.db.close()
