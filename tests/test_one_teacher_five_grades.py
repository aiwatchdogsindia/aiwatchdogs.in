"""Tests for Project P5 (one-teacher-five-grades) and deterministic engine P5."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aiwatchdogs.cli.config import AgentConfig
from aiwatchdogs.cli.runner import AgentRunner
from aiwatchdogs.core.safety import compute_sha256
from aiwatchdogs.engines import one_teacher_five_grades as p5_engine
from aiwatchdogs.engines.registry import run_engine
from aiwatchdogs.hub.app import create_app
from aiwatchdogs.projects import (
    ONE_TEACHER_FIVE_GRADES_METHODOLOGY,
    ONE_TEACHER_FIVE_GRADES_PROJECT_SLUG,
    validate_project,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECTS_DIR = REPO_ROOT / "projects"


def _minimal_multipage_pdf_bytes(pages: list[str]) -> bytes:
    """Create a minimal valid multi-page PDF readable by pypdf."""
    objs: list[bytes] = []
    # 1: Catalog, 2: Pages, 3: Font, then 2 objs per page (Page, Contents)
    num_pages = len(pages)
    kid_refs = " ".join(f"{4 + 2 * i} 0 R" for i in range(num_pages))
    objs.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    objs.append(f"2 0 obj\n<< /Type /Pages /Kids [{kid_refs}] /Count {num_pages} >>\nendobj\n".encode("ascii"))
    objs.append(b"3 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n")

    for i, text in enumerate(pages):
        page_obj_id = 4 + 2 * i
        stream_obj_id = page_obj_id + 1
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        lines = escaped.splitlines() or [""]
        cmds = ["BT", "/F1 10 Tf", "40 760 Td", "12 TL"]
        for li, line in enumerate(lines):
            cmds.append(f"({line}) Tj" if li == 0 else f"T* ({line}) Tj")
        cmds.append("ET")
        stream = "\n".join(cmds).encode("latin-1", "replace")
        objs.append(
            f"{page_obj_id} 0 obj\n"
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {stream_obj_id} 0 R "
            f"/Resources << /Font << /F1 3 0 R >> >> >>\nendobj\n".encode("ascii")
        )
        objs.append(
            f"{stream_obj_id} 0 obj\n<< /Length {len(stream)} >>\nstream\n".encode("ascii")
            + stream
            + b"\nendstream\nendobj\n"
        )

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


def _synthetic_udise_records() -> list[dict[str, Any]]:
    t22 = "\n".join(
        [
            "32",
            "Table 2.2: State wise highlights of the UDISE+ 2025-26 data: Schools, Enrolments and Teachers",
            "India/State/UT",
            "Schools with Single Teachers Enrolments in Single Teacher Schools",
            "India 1466682 247219766 10273020 24 7 169 5663 20667 100843 2912147",
            "Andhra Pradesh 60843 8437049 353745 24 6 139 10 13 16357 231631",
            "Bihar 95285 20696191 728945 28 8 217 0 0 2006 162393",
            "Chandigarh 208 248376 10477 24 50 1194 0 0 0 0",
            "Daman & Diu and Dadra &",
            "Nagar Haveli 426 150892 5359 28 13 354 0 0 0 0",
            "Jharkhand 44378 7505425 209586 36 5 169 125 43 9827 440346",
        ]
    )
    t33 = "\n".join(
        [
            "42",
            "Table 3.3: Number of schools by management and school category, 2025-26: Government",
            "India 1005245 612066 204617 63260 16040 24942 12082 27683 26426 15465 2664",
            "Andhra Pradesh 44222 34642 1361 0 2286 4166 0 245 979 0 543",
            "Bihar 76435 38142 28549 224 46 11 11 114 890 8435 13",
            "Chandigarh 116 3 10 0 55 0 0 46 1 1 0",
            "Daman & Diu and Dadra &",
            "Nagar Haveli 353 145 143 3 7 3 15 2 4 31 0",
            "Jharkhand 35788 21261 11426 17 1484 74 223 407 479 350 67",
        ]
    )
    t43 = "\n".join(
        [
            "54",
            "Table 4.3: Number of teachers by management and school category, 2025-26: Government",
            "India 5134623 1754291 1300692 231309 197668 268252 72904 497095 569629 210581 32202",
            "Andhra Pradesh 201125 74616 7254 0 30153 57392 0 5740 18186 0 7784",
            "Bihar 536061 164481 240988 1618 445 68 122 2439 16983 108715 202",
            "Chandigarh 5518 43 284 0 2270 0 0 2865 29 27 0",
            "Daman & Diu and Dadra &",
            "Nagar Haveli 3264 546 1614 42 132 30 109 59 97 635 0",
            "Jharkhand 116372 35945 45088 100 14379 516 1427 7530 5216 5630 541",
        ]
    )
    t52 = "\n".join(
        [
            "86",
            "Table 5.2: Enrolment of students by school management and level of school education, 2025-26: Government",
            "India 118903585 3537362 53724337 33347829 87072166 16991487 11302570",
            "Andhra Pradesh 3493450 11119 1348002 1125171 2473173 762683 246475",
            "Bihar 19200000 100000 9800000 5000000 14800000 3000000 1300000",
            "Chandigarh 160000 8000 55000 40000 95000 30000 27000",
            "Daman & Diu and Dadra &",
            "Nagar Haveli 99009 10087 37864 25105 62969 15485 10468",
            "Jharkhand 4811600 41435 2220821 1349141 3569962 731469 468734",
        ]
    )
    return [
        {"page": 32, "raw_text": t22},
        {"page": 42, "raw_text": t33},
        {"page": 54, "raw_text": t43},
        {"page": 86, "raw_text": t52},
    ]


def _p5_ctx(state_name: str, *, academic_year: str = "2025-26", is_ut: bool = False) -> dict[str, Any]:
    return {
        "retrieval_url": "https://dashboard.udiseplus.gov.in/report2026/static/media/test.pdf",
        "expected_sha256": "b" * 64,
        "source_landing_page": "https://dashboard.udiseplus.gov.in/",
        "official_publisher": "Department of School Education & Literacy, Ministry of Education",
        "one_teacher_five_grades": {
            "academic_year": academic_year,
            "state_name": state_name,
            "state_key": p5_engine.canonical_state_key(state_name),
            "is_ut": is_ut,
            "source_document_title": f"UDISE+ {academic_year} Booklet (Existing Structure)",
            "table_pages": {"table_2_2": 32, "table_3_3": 42, "table_4_3": 54, "table_5_2": 86},
        },
    }


def test_p5_flags_severe_concentration_overcrowding_and_gov_understaffing() -> None:
    records = _synthetic_udise_records()
    res = run_engine(ONE_TEACHER_FIVE_GRADES_METHODOLOGY, records, context=_p5_ctx("Jharkhand"))
    assert res.unevaluable_count == 0
    assert res.cleared_count == 0
    assert len(res.leads) == 1
    lead = res.leads[0]
    assert lead.methodology_code == "P5"
    assert lead.rule_triggered == "P5-CONCENTRATION-OVERCROWDING"
    assert lead.severity == "critical"
    m = lead.anomaly_metrics
    assert m["signal_category"] == "severe_concentration_and_overcrowding"
    assert m["single_teacher_schools"] == 9827
    assert m["single_teacher_share_pct"] == pytest.approx(22.14)
    assert m["students_per_single_teacher"] == pytest.approx(44.81)
    assert m["gov_primary_1_5_schools"] == 21261
    assert m["gov_primary_teachers_per_school"] == pytest.approx(1.69)
    assert set(m["triggered_rules"]) == {
        "single_teacher_share_ge_10pct",
        "students_per_single_teacher_gt_30",
        "gov_primary_avg_teachers_lt_2",
    }


def test_p5_flags_high_concentration_and_overcrowding_only_separately() -> None:
    records = _synthetic_udise_records()

    ap = run_engine(ONE_TEACHER_FIVE_GRADES_METHODOLOGY, records, context=_p5_ctx("Andhra Pradesh"))
    assert len(ap.leads) == 1
    assert ap.leads[0].rule_triggered == "P5-HIGH-CONCENTRATION"
    assert ap.leads[0].anomaly_metrics["single_teacher_share_pct"] == pytest.approx(26.88)
    assert ap.leads[0].anomaly_metrics["gov_primary_teachers_per_school"] == pytest.approx(2.15)

    bihar = run_engine(ONE_TEACHER_FIVE_GRADES_METHODOLOGY, records, context=_p5_ctx("Bihar"))
    assert len(bihar.leads) == 1
    assert bihar.leads[0].rule_triggered == "P5-SEVERE-OVERCROWDING"
    assert bihar.leads[0].severity == "critical"
    assert bihar.leads[0].anomaly_metrics["students_per_single_teacher"] == pytest.approx(80.95)


def test_p5_clears_zero_single_teacher_uts_including_wrapped_names_and_marks_missing_unevaluable() -> None:
    records = _synthetic_udise_records()

    chd = run_engine(ONE_TEACHER_FIVE_GRADES_METHODOLOGY, records, context=_p5_ctx("Chandigarh", is_ut=True))
    assert chd.leads == []
    assert chd.cleared_count == 1
    assert chd.unevaluable_count == 0

    dnh = run_engine(
        ONE_TEACHER_FIVE_GRADES_METHODOLOGY,
        records,
        context=_p5_ctx("Dadra & Nagar Haveli and Daman & Diu", is_ut=True),
    )
    assert dnh.leads == []
    assert dnh.cleared_count == 1
    assert dnh.unevaluable_count == 0

    missing = run_engine(ONE_TEACHER_FIVE_GRADES_METHODOLOGY, records, context=_p5_ctx("Nonexistent State"))
    assert missing.leads == []
    assert missing.cleared_count == 0
    assert missing.unevaluable_count == 1


def test_scrub_pii_preserves_table_rows_while_redacting_split_phones() -> None:
    from aiwatchdogs.core.safety import scrub_pii

    table_line = "Chandigarh 142341 12976 41860 39390 81250 24445 23670"
    cleaned_tbl, tags_tbl = scrub_pii(table_line)
    assert cleaned_tbl == table_line
    assert "phone" not in tags_tbl

    for phone_sample in (
        "+91 98765 43210",
        "Call 98765 43210",
        "98765-43210",
        "98765 43210 91234 56789",
    ):
        cleaned_ph, tags_ph = scrub_pii(phone_sample)
        assert "[REDACTED_PHONE]" in cleaned_ph
        assert "phone" in tags_ph

    aadhaar_table_line = "Lakshadweep 12325 1309 5254 2805 8059 1804 1153"
    cleaned_atbl, tags_atbl = scrub_pii(aadhaar_table_line)
    assert cleaned_atbl == aadhaar_table_line
    assert "aadhaar" not in tags_atbl

    for aadhaar_sample in (
        "5254 2805 8059",
        "5254-2805-8059",
        "525428058059",
        "Aadhaar 12325 1309 5254 2805 8059 1804",
    ):
        cleaned_aa, tags_aa = scrub_pii(aadhaar_sample)
        assert "[REDACTED_AADHAAR]" in cleaned_aa
        assert "aadhaar" in tags_aa


def test_generated_p5_project_json_and_coverage_are_valid() -> None:
    project_path = PROJECTS_DIR / f"{ONE_TEACHER_FIVE_GRADES_PROJECT_SLUG}.json"
    data = json.loads(project_path.read_text(encoding="utf-8"))
    assert data["project_code"] == "P5"
    assert data["methodology_code"] == "P5"
    assert len(data["tasks"]) == 100
    assert validate_project(data, filename_stem=ONE_TEACHER_FIVE_GRADES_PROJECT_SLUG) == []

    by_yr = {"2025-26": 0, "2024-25": 0, "2023-24": 0}
    for task in data["tasks"]:
        yr = task["payload"]["context"]["one_teacher_five_grades"]["academic_year"]
        by_yr[yr] += 1
        assert task["id"].startswith("p5-")
    assert by_yr == {"2025-26": 36, "2024-25": 36, "2023-24": 28}

    cov = json.loads((PROJECTS_DIR / ONE_TEACHER_FIVE_GRADES_PROJECT_SLUG / "coverage.json").read_text(encoding="utf-8"))
    assert cov["total_tasks"] == 100
    assert cov["dry_run_summary"]["tasks_tested"] == 100
    assert cov["dry_run_summary"]["unevaluable"] == 0
    assert cov["national_baseline_2025_26"]["single_teacher_schools"] == 100843

    with (PROJECTS_DIR / ONE_TEACHER_FIVE_GRADES_PROJECT_SLUG / "documents.csv").open("r", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 3


def test_p5_hub_endpoints_and_two_agent_peer_verification(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(PROJECTS_DIR))
    monkeypatch.setenv("AIWATCHDOGS_ALLOW_PROJECT_CHOICE", "1")
    db_path = tmp_path / "p5_hub.db"
    app = create_app(db_path=str(db_path))

    recs = _synthetic_udise_records()
    pdf_bytes = _minimal_multipage_pdf_bytes([r["raw_text"] for r in recs])
    pdf_sha = compute_sha256(pdf_bytes)
    pdf_url = "https://dashboard.udiseplus.gov.in/report2026/static/media/synthetic-p5.pdf"

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
        assert ONE_TEACHER_FIVE_GRADES_PROJECT_SLUG in by_slug
        assert by_slug[ONE_TEACHER_FIVE_GRADES_PROJECT_SLUG]["project_code"] == "P5"
        assert by_slug[ONE_TEACHER_FIVE_GRADES_PROJECT_SLUG]["total_tasks"] == 100

        detail_resp = client.get(f"/api/missions/{ONE_TEACHER_FIVE_GRADES_PROJECT_SLUG}")
        assert detail_resp.status_code == 200
        detail = detail_resp.json()
        assert detail["project_code"] == "P5"
        vs = detail["visual_summary"]
        assert vs["methodology_code"] == "P5"
        assert vs["one_teacher_five_grades"]["total_tasks"] == 100

        assert client.get(f"/api/missions/{ONE_TEACHER_FIVE_GRADES_PROJECT_SLUG}/documents.csv").status_code == 200
        assert client.get(f"/api/missions/{ONE_TEACHER_FIVE_GRADES_PROJECT_SLUG}/coverage.json").status_code == 200
        md_resp = client.get(f"/projects/{ONE_TEACHER_FIVE_GRADES_PROJECT_SLUG}.md")
        assert md_resp.status_code == 200 and "One Teacher, Five Grades" in md_resp.text
        jt_resp = client.get("/join.txt")
        assert jt_resp.status_code == 200 and "one-teacher-five-grades" in jt_resp.text

        # Insert a synthetic 4-page P5 task (pages 1..4 matching our synthetic PDF) and verify Auditor + 2 Peers.
        task_id = "prj-p5-2025-26-synthetic-jharkhand"
        payload = {
            "retrieval_url": pdf_url,
            "dataset_url": pdf_url,
            "expected_sha256": pdf_sha,
            "official_publisher": "Ministry of Education",
            "source_landing_page": "https://dashboard.udiseplus.gov.in/",
            "context": {
                "one_teacher_five_grades": {
                    "academic_year": "2025-26",
                    "state_name": "Jharkhand",
                    "state_key": "jharkhand",
                    "is_ut": False,
                    "source_document_title": "Synthetic UDISE+ 2025-26",
                    "table_pages": {"table_2_2": 1, "table_3_3": 2, "table_4_3": 3, "table_5_2": 4},
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
                ) VALUES (?, ?, ?, 'P5', 'anomaly_scan', 'Synthetic P5 Jharkhand check', 'Run P5', ?, NULL, '[]',
                          'queued', 'udise-synthetic-jharkhand', 'school_staffing_and_enrolment', 'run_check',
                          500, 'test', '2026-11-27T00:00:00Z', '2026-11-27T00:00:00Z')
                """,
                (
                    task_id,
                    f"project-{ONE_TEACHER_FIVE_GRADES_PROJECT_SLUG}",
                    ONE_TEACHER_FIVE_GRADES_PROJECT_SLUG,
                    json.dumps(payload),
                ),
            )

        auditor = AgentRunner(
            AgentConfig(
                handle="p5-auditor-one",
                hub_url="http://testserver",
                provider="rule-engine",
                model="deterministic-cpu-v1",
                mode="investigate",
                max_tasks=1,
            ),
            http_client=client,
            config_path=tmp_path / "auditor.json",
        )
        res_a = auditor.run_loop(max_tasks=1, preferred_mission=ONE_TEACHER_FIVE_GRADES_PROJECT_SLUG, task_id=task_id)
        assert res_a.findings_submitted == 1

        for idx in (1, 2, 3):
            pv = AgentRunner(
                AgentConfig(
                    handle=f"p5-verifier-{idx}",
                    hub_url="http://testserver",
                    provider="rule-engine",
                    model="deterministic-cpu-v1",
                    mode="verify",
                    max_tasks=1,
                ),
                http_client=client,
                config_path=tmp_path / f"pv{idx}.json",
            )
            res_pv = pv.run_loop(max_tasks=1, preferred_mission=ONE_TEACHER_FIVE_GRADES_PROJECT_SLUG)
            assert res_pv.verifications_submitted == 1

        findings = client.get(f"/api/findings?mission={ONE_TEACHER_FIVE_GRADES_PROJECT_SLUG}").json()["findings"]
        assert len(findings) == 1
        assert findings[0]["status"] == "verified"
        assert findings[0]["methodology_code"] == "P5"
