"""Tests for Project P4 (one-road-two-bills) and deterministic engine OR1."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aiwatchdogs.cli.config import AgentConfig
from aiwatchdogs.cli.runner import AgentRunner
from aiwatchdogs.core.safety import compute_sha256, lint_neutrality
from aiwatchdogs.engines import one_road_two_bills as or1
from aiwatchdogs.engines.registry import run_engine
from aiwatchdogs.hub.app import create_app
from aiwatchdogs.projects import (
    ONE_ROAD_TWO_BILLS_METHODOLOGY,
    ONE_ROAD_TWO_BILLS_PROJECT_SLUG,
    validate_project,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECTS_DIR = REPO_ROOT / "projects"


def _minimal_pdf_bytes(text: str) -> bytes:
    """Create a minimal valid single-page PDF containing ``text`` readable by pypdf."""
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    lines = escaped.splitlines()
    stream_cmds = ["BT", "/F1 11 Tf", "50 750 Td", "14 TL"]
    for i, line in enumerate(lines):
        if i == 0:
            stream_cmds.append(f"({line}) Tj")
        else:
            stream_cmds.append(f"T* ({line}) Tj")
    stream_cmds.append("ET")
    stream = "\n".join(stream_cmds).encode("latin-1", "replace")

    objs: list[bytes] = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n",
        f"4 0 obj\n<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"\nendstream\nendobj\n",
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]
    buf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objs:
        offsets.append(len(buf))
        buf.extend(obj)
    xref_pos = len(buf)
    buf.extend(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for off in offsets[1:]:
        buf.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    buf.extend(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("ascii"))
    return bytes(buf)


def _or1_ctx(page_text: str, *, page: int = 12, year: str = "2025") -> tuple[list[dict[str, Any]], dict[str, Any]]:
    lines = page_text.splitlines()
    records = [{"page": page, "raw_text": page_text}]
    ctx = {
        "retrieval_url": "https://d1jmc0u14z2tmp.cloudfront.net/uploads/download_audit_report/2025/test.pdf",
        "expected_sha256": "a" * 64,
        "source_landing_page": "https://cag.gov.in/en/audit-report/details/999999",
        "jurisdiction": "Union of India",
        "one_road_two_bills": {
            "report": {
                "cag_id": "999999",
                "report_no": f"Report No. 17 of {year}",
                "year": year,
                "title": "Performance Audit Report on Rural Works, Union Government",
                "sector": "Social Infrastructure",
            },
            "windows": [
                {
                    "key": f"r999999-p{page:03d}-b00",
                    "page": page,
                    "block_index": 0,
                    "line_start": 0,
                    "line_end": len(lines),
                }
            ],
            "thresholds": {"min_amount_inr": 0},
        },
    }
    return records, ctx


def test_or1_flags_duplicate_billing_and_cites_amount() -> None:
    page_text = (
        "6.3.1.2 Duplicate claim for rural electrification connections\n"
        "Despite getting the required grant under DDUGJY, grant of Rs. 7.53 crore from REC "
        "was also claimed under SAUBHAGYA, resulting in double payment to DISCOMs for the same work."
    )
    records, ctx = _or1_ctx(page_text, page=42)
    res = run_engine(ONE_ROAD_TWO_BILLS_METHODOLOGY, records, context=ctx)
    assert len(res.leads) == 1
    assert res.cleared_count == 0
    assert res.unevaluable_count == 0
    lead = res.leads[0]
    assert lead.methodology_code == "OR1"
    assert lead.rule_triggered == "OR1-DUPLICATE-BILLING"
    assert lead.fiscal_year == "FY2025"
    assert lead.monetary_exposure_inr == pytest.approx(75_300_000.0)
    assert lead.severity == "high"
    assert lead.anomaly_metrics["signal_category"] == "duplicate_payment_or_billing"
    assert lead.anomaly_metrics["page"] == 42
    assert lead.anomaly_metrics["para_number"] == "6.3.1.2"
    assert lead.citations[0]["page"] == 42
    assert lead.benign_explanations


def test_or1_flags_dual_agency_overlap_with_irregularity() -> None:
    page_text = (
        "4.2.1 Overlapping road construction across two agencies\n"
        "Audit scrutiny revealed that a 12 km stretch of rural road already constructed by CPWD "
        "was sanctioned and billed again under another agency (NHIDCL), resulting in irregular "
        "and avoidable expenditure of Rs. 4.20 crore."
    )
    records, ctx = _or1_ctx(page_text, page=18)
    res = run_engine(ONE_ROAD_TWO_BILLS_METHODOLOGY, records, context=ctx)
    assert len(res.leads) == 1
    lead = res.leads[0]
    assert lead.rule_triggered == "OR1-DUAL-AGENCY-OVERLAP"
    assert lead.anomaly_metrics["signal_category"] == "dual_agency_or_scheme_overlap"
    assert lead.monetary_exposure_inr == pytest.approx(42_000_000.0)


def test_or1_excludes_legitimate_cofunding_and_cost_sharing() -> None:
    page_text = (
        "3.1 Financing Pattern of Centrally Sponsored Scheme\n"
        "The project is jointly funded on a 60:40 Centre-State cost sharing basis between the "
        "Ministry of Rural Development and the State Government, with matching grant releases "
        "across both agencies totalling Rs. 120 crore as per scheme guidelines."
    )
    records, ctx = _or1_ctx(page_text, page=9)
    leads, cleared, _mon, _summary, meta = or1.run_or1_duplicate_billing(records, ctx)
    assert leads == []
    assert cleared == 1
    assert meta["excluded_legitimate_cofunding"] == 1


def test_or1_excludes_methodology_boilerplate_and_hyphenated_numbers() -> None:
    page_text = (
        "1.3 Audit Objectives and Methodology\n"
        "Audit checked whether internal controls were adequate to prevent duplicate payments across "
        "agencies. Forty-two bills paid between 2021 and 2023 were reviewed for compliance."
    )
    records, ctx = _or1_ctx(page_text, page=4)
    res = run_engine(ONE_ROAD_TWO_BILLS_METHODOLOGY, records, context=ctx)
    assert res.leads == []
    assert res.cleared_count == 1


def test_or1_neutralizes_accusatory_terms_and_scrubs_pii() -> None:
    page_text = (
        "5.1.4 Fraudulent duplicate vouchers submitted by contractor\n"
        "Audit observed double payment of Rs. 15 lakh against duplicate vouchers submitted by the "
        "contractor (contact 9876543210, PAN ABCDE1234F, email test@example.com)."
    )
    records, ctx = _or1_ctx(page_text, page=27)
    res = run_engine(ONE_ROAD_TWO_BILLS_METHODOLOGY, records, context=ctx)
    assert len(res.leads) == 1
    lead = res.leads[0]
    lint = lint_neutrality(lead.title, lead.summary, lead.benign_explanations)
    assert lint.passed
    assert "9876543210" not in lead.summary
    assert "ABCDE1234F" not in lead.summary
    assert "test@example.com" not in lead.summary


def test_or1_validate_context_rejects_malformed_specs() -> None:
    assert or1.validate_context({}, "tasks[0]")
    bad = {
        "one_road_two_bills": {
            "report": {"cag_id": "", "report_no": "R1", "year": "25", "title": ""},
            "windows": [{"key": "BAD KEY", "page": 0, "block_index": -1, "line_start": 5, "line_end": 2}],
        }
    }
    errs = or1.validate_context(bad, "tasks[0]")
    assert len(errs) >= 6


def test_real_p4_project_file_is_valid_and_matches_artifacts() -> None:
    proj_path = PROJECTS_DIR / f"{ONE_ROAD_TWO_BILLS_PROJECT_SLUG}.json"
    assert proj_path.is_file()
    data = json.loads(proj_path.read_text(encoding="utf-8"))
    assert data["slug"] == ONE_ROAD_TWO_BILLS_PROJECT_SLUG
    assert data["project_code"] == "P4"
    assert data["methodology_code"] == "OR1"
    assert len(data["tasks"]) == 10_000
    assert validate_project(data, filename_stem=ONE_ROAD_TWO_BILLS_PROJECT_SLUG) == []

    coverage = json.loads((PROJECTS_DIR / ONE_ROAD_TWO_BILLS_PROJECT_SLUG / "coverage.json").read_text(encoding="utf-8"))
    assert coverage["tasks"] == 10_000
    assert coverage["reports_used"] > 20

    with (PROJECTS_DIR / ONE_ROAD_TWO_BILLS_PROJECT_SLUG / "documents.csv").open("r", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len([r for r in rows if r["status"] == "used"]) == coverage["reports_used"]

    # Every OR1 URL and report title must be free of raw HTML entities or URL fragments.
    for row in rows:
        assert "&#" not in row["pdf_url"] and "&amp;" not in row["pdf_url"] and "#" not in row["pdf_url"]
        assert "&ndash;" not in row["title"] and "&amp;" not in row["title"]
    for task in data["tasks"]:
        url = task["payload"]["retrieval_url"]
        assert "&#" not in url and "&amp;" not in url and "#" not in url
        rep_title = task["payload"]["context"]["one_road_two_bills"]["report"]["title"]
        assert "&ndash;" not in rep_title and "&amp;" not in rep_title


def test_validator_rejects_html_entities_in_urls_and_no_ollama_or_local_cli_remnants() -> None:
    bad_proj = {
        "slug": "entity-url-test",
        "title": "Synthetic project testing HTML entity URL rejection",
        "summary": "Ensures validate_project rejects unescaped HTML entities in artifact URLs.",
        "posted_by": {"name": "AI Watchdogs India", "url": "https://aiwatchdogs.in"},
        "pillar": "audit",
        "status": "active",
        "jurisdiction": "Union of India",
        "authority": "CAG",
        "sources": [{"name": "CAG", "publisher": "CAG", "url": "https://cag.gov.in"}],
        "methodology_code": "OR1",
        "tasks": [
            {
                "id": "or1-entity-0001",
                "title": "Entity URL test task",
                "instructions": "Verify entity URL rejection.",
                "type": "run_check",
                "dataset_key": "cag-entity-1",
                "financial_quantity_type": "cag_report_monetary_figures",
                "methodology_code": "OR1",
                "estimated_tokens": 500,
                "estimate_basis": "test",
                "payload": {
                    "retrieval_url": "https://d1jmc0u14z2tmp.cloudfront.net/uploads/CAG&#039;s-Report.pdf",
                    "expected_sha256": "a" * 64,
                    "official_publisher": "CAG",
                    "source_landing_page": "https://cag.gov.in/en/audit-report/details/1",
                    "context": {
                        "one_road_two_bills": {
                            "report": {
                                "cag_id": "1",
                                "report_no": "Report No. 1 of 2025",
                                "year": "2025",
                                "title": "Test Report",
                                "sector": "Civil",
                            },
                            "windows": [{"key": "r1-p001-b00", "page": 1, "block_index": 0, "line_start": 0, "line_end": 5}],
                        }
                    },
                },
            }
        ],
    }
    errs = validate_project(bad_proj, filename_stem="entity-url-test")
    assert any("HTML entity" in e for e in errs), errs

    # Website, GitHub docs, CLI, and /join.txt must not advertise local Ollama or local CLI onboarding.
    from aiwatchdogs.hub.templates import render_join_txt

    join_txt = render_join_txt("https://aiwatchdogs.in").lower()
    assert "ollama" not in join_txt
    assert "join.sh" not in join_txt
    assert "local checkout" not in join_txt

    for rel in (
        "README.md",
        "CONTRIBUTING.md",
        "SPONSORS.md",
        "web/src/components/JoinModal.tsx",
        "web/src/components/Footer.tsx",
        "web/src/components/EyebrowBar.tsx",
        "web/src/components/LiveAgentStrip.tsx",
        "src/aiwatchdogs/cli/main.py",
    ):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8").lower()
        assert "ollama" not in text, f"Unexpected 'ollama' in {rel}"
        assert "shell installer" not in text, f"Unexpected 'shell installer' in {rel}"
        assert "from a local repo checkout" not in text, f"Unexpected local checkout copy in {rel}"
        assert "cli & frontier api" not in text, f"Unexpected CLI tab in {rel}"


def test_p4_hub_endpoints_and_two_agent_peer_verification(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(PROJECTS_DIR))
    monkeypatch.setenv("AIWATCHDOGS_ALLOW_PROJECT_CHOICE", "1")
    db_path = tmp_path / "p4_hub.db"
    app = create_app(db_path=str(db_path))

    # Build a small synthetic PDF so the CLI runner can run a real OR1 task end-to-end without network.
    pdf_text = (
        "6.3.1.2 Duplicate claim on rural road project\n"
        "Despite receiving funds under PMGSY, Rs. 3.50 crore was claimed again under another scheme, "
        "resulting in double payment for the same road work."
    )
    pdf_bytes = _minimal_pdf_bytes(pdf_text)
    pdf_sha = compute_sha256(pdf_bytes)
    pdf_url = "https://d1jmc0u14z2tmp.cloudfront.net/uploads/download_audit_report/2025/synthetic-or1.pdf"

    import httpx

    orig_get = httpx.Client.get
    orig_stream = httpx.Client.stream

    class _FakeResp:
        def __init__(self, content: bytes) -> None:
            self.status_code = 200
            self.headers = {"Content-Type": "application/pdf", "Content-Length": str(len(content))}
            self.content = content

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self, chunk_size: int = 65536):
            yield self.content

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def _fake_stream(self: Any, method: str, url: str, **kwargs: Any):
        if str(url) == pdf_url:
            return _FakeResp(pdf_bytes)
        return orig_stream(self, method, url, **kwargs)

    def _fake_get(self: Any, url: str, **kwargs: Any):
        if str(url) == pdf_url:
            return _FakeResp(pdf_bytes)
        return orig_get(self, url, **kwargs)

    monkeypatch.setattr(httpx.Client, "stream", _fake_stream)
    monkeypatch.setattr(httpx.Client, "get", _fake_get)

    with TestClient(app, base_url="http://testserver") as client:
        missions_resp = client.get("/api/missions")
        assert missions_resp.status_code == 200
        by_slug = {m["slug"]: m for m in missions_resp.json()["missions"]}
        assert ONE_ROAD_TWO_BILLS_PROJECT_SLUG in by_slug
        p4_meta = by_slug[ONE_ROAD_TWO_BILLS_PROJECT_SLUG]
        assert p4_meta["project_code"] == "P4"
        assert p4_meta["total_tasks"] == 10_000

        detail_resp = client.get(f"/api/missions/{ONE_ROAD_TWO_BILLS_PROJECT_SLUG}")
        assert detail_resp.status_code == 200
        detail = detail_resp.json()
        assert detail["project_code"] == "P4"
        assert detail["tasks_truncated"] is True
        vs = detail["visual_summary"]
        assert vs["methodology_code"] == "OR1"
        assert vs["one_road_two_bills"]["reports"] > 20

        assert client.get(f"/api/missions/{ONE_ROAD_TWO_BILLS_PROJECT_SLUG}/documents.csv").status_code == 200
        assert client.get(f"/api/missions/{ONE_ROAD_TWO_BILLS_PROJECT_SLUG}/coverage.json").status_code == 200

        # Insert one deterministic OR1 task pinned to the synthetic PDF and run Auditor + 2 Peer Verifiers via AgentRunner.
        task_id = "prj-or1-2025-r999999-0001"
        payload = {
            "retrieval_url": pdf_url,
            "dataset_url": pdf_url,
            "expected_sha256": pdf_sha,
            "official_publisher": "CAG of India",
            "source_landing_page": "https://cag.gov.in/en/audit-report/details/999999",
            "jurisdiction": "Union of India",
            "context": {
                "one_road_two_bills": {
                    "report": {
                        "cag_id": "999999",
                        "report_no": "Report No. 99 of 2025",
                        "year": "2025",
                        "title": "Synthetic CAG Union Report",
                        "sector": "Rural Roads",
                    },
                    "windows": [{"key": "r999999-p001-b00", "page": 1, "block_index": 0, "line_start": 0, "line_end": 3}],
                    "thresholds": {"min_amount_inr": 0},
                }
            },
        }
        with app.state.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                    id, mission_id, mission_slug, methodology_code, task_type,
                    title, instructions, payload_json, target_finding_id,
                    excluded_handles_json, status, dataset_key, financial_quantity_type,
                    work_type, estimated_tokens, estimate_basis, created_at, updated_at
                ) VALUES (?, ?, ?, 'OR1', 'anomaly_scan', 'Synthetic OR1 check', 'Run OR1', ?, NULL, '[]',
                          'queued', 'cag-union-2025-999999', 'cag_report_monetary_figures', 'run_check',
                          500, 'test', '2026-11-27T00:00:00Z', '2026-11-27T00:00:00Z')
                """,
                (task_id, f"project-{ONE_ROAD_TWO_BILLS_PROJECT_SLUG}", ONE_ROAD_TWO_BILLS_PROJECT_SLUG, json.dumps(payload)),
            )

        auditor = AgentRunner(
            AgentConfig(
                handle="or1-auditor-one",
                hub_url="http://testserver",
                provider="rule-engine",
                model="deterministic-cpu-v1",
                mode="investigate",
                max_tasks=1,
            ),
            http_client=client,
            config_path=tmp_path / "auditor.json",
        )
        res_a = auditor.run_loop(max_tasks=1, preferred_mission=ONE_ROAD_TWO_BILLS_PROJECT_SLUG, task_id=task_id)
        assert res_a.findings_submitted == 1

        for idx in (1, 2, 3):
            pv = AgentRunner(
                AgentConfig(
                    handle=f"or1-verifier-{idx}",
                    hub_url="http://testserver",
                    provider="rule-engine",
                    model="deterministic-cpu-v1",
                    mode="verify",
                    max_tasks=1,
                ),
                http_client=client,
                config_path=tmp_path / f"pv{idx}.json",
            )
            res_pv = pv.run_loop(max_tasks=1, preferred_mission=ONE_ROAD_TWO_BILLS_PROJECT_SLUG)
            assert res_pv.verifications_submitted == 1

        findings = client.get(f"/api/findings?mission={ONE_ROAD_TWO_BILLS_PROJECT_SLUG}").json()["findings"]
        assert len(findings) == 1
        assert findings[0]["status"] == "verified"
        assert findings[0]["methodology_code"] == "OR1"
