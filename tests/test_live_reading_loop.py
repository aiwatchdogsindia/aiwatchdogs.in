"""End-to-end tests for the live R1 AI document-reading loop (P3 Union Budget & generic projects)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from aiwatchdogs.cli.config import AgentConfig
from aiwatchdogs.cli.providers import BudgetTracker, ProviderInfo
from aiwatchdogs.cli.reading_runner import (
    build_coding_agent_reading_bundle,
    extract_pdf_pages_for_reading,
    run_llm_budget_extraction,
)
from aiwatchdogs.cli.runner import CLI_USER_AGENT, AgentRunner
from aiwatchdogs.core.models import LoadedDataset
from aiwatchdogs.engines.reading_verifier import verify_budget_extraction
from aiwatchdogs.hub.app import create_app
from aiwatchdogs.projects import validate_project

SAMPLE_SHA = "a" * 64
REPO_PROJECTS = Path(__file__).resolve().parents[1] / "projects"

SAMPLE_GT = {
    "actual_plan": 1231.66,
    "actual_non_plan": 585.00,
    "actual_total": 1816.66,
    "be_prev_plan": 1139.00,
    "be_prev_non_plan": 587.50,
    "be_prev_total": 1726.50,
    "re_prev_plan": 1113.00,
    "re_prev_non_plan": 587.50,
    "re_prev_total": 1700.50,
    "be_plan": 1139.00,
    "be_non_plan": 629.00,
    "be_total": 1768.00,
}

PAGE_1_TEXT = (
    "MINISTRY OF HOME AFFAIRS DEMAND NO. 57 Transfers to Union Territory Governments\n"
    "Actual 2013-2014 Budget 2014-2015 Revised 2014-2015 Budget 2015-2016\n"
    "Plan Non-Plan Total Plan Non-Plan Total Plan Non-Plan Total Plan Non-Plan Total\n"
    "1.01.01 Overall Gap 3602 ... 513.00 513.00 ... 513.00 513.00 ... 513.00 513.00 ... 556.00 556.00\n"
    "7602 9.96 72.00 81.96 ... 72.00 72.00 ... 72.00 72.00 ... 72.00 72.00\n"
    "1.02 NCT Delhi 3602 ... ... ... ... 2.50 2.50 ... 2.50 2.50 ... 1.00 1.00\n"
)
PAGE_2_TEXT = (
    "2.01 Puducherry Plan Assistance 3602 643.12 ... 643.12 814.00 ... 814.00 788.00 ... 788.00 744.01 ... 744.01\n"
    "2.02 NCT Delhi Plan Assistance 3602 582.51 ... 582.51 325.00 ... 325.00 325.00 ... 325.00 394.99 ... 394.99\n"
    "3. Actual Recoveries 3602 -3.93 ... -3.93 ... ... ... ... ... ... ... ... ...\n"
    "Grand Total 1231.66 585.00 1816.66 1139.00 587.50 1726.50 1113.00 587.50 1700.50 1139.00 629.00 1768.00\n"
)


def _valid_d57_extraction(sha: str = SAMPLE_SHA) -> dict[str, Any]:
    return {
        "document_sha256": sha,
        "page": 1,
        "pages": [1, 2],
        "column_headers": [
            "Actual 2013-2014",
            "Budget 2014-2015",
            "Revised 2014-2015",
            "Budget 2015-2016",
            "Plan",
            "Non-Plan",
            "Total",
        ],
        "printed_grand_total": dict(SAMPLE_GT),
        "rows": [
            {
                "page": 1,
                "line_number": "1.01.01",
                "label": "Puducherry Overall Gap (Revenue)",
                "actual_plan": 0.0, "actual_non_plan": 513.0, "actual_total": 513.0,
                "be_prev_plan": 0.0, "be_prev_non_plan": 513.0, "be_prev_total": 513.0,
                "re_prev_plan": 0.0, "re_prev_non_plan": 513.0, "re_prev_total": 513.0,
                "be_plan": 0.0, "be_non_plan": 556.0, "be_total": 556.0,
                "verbatim_quote": "1.01.01 Overall Gap 3602 ... 513.00 513.00",
            },
            {
                "page": 1,
                "line_number": "1.01.01-cap",
                "label": "Puducherry Overall Gap (Capital)",
                "actual_plan": 9.96, "actual_non_plan": 72.0, "actual_total": 81.96,
                "be_prev_plan": 0.0, "be_prev_non_plan": 72.0, "be_prev_total": 72.0,
                "re_prev_plan": 0.0, "re_prev_non_plan": 72.0, "re_prev_total": 72.0,
                "be_plan": 0.0, "be_non_plan": 72.0, "be_total": 72.0,
                "verbatim_quote": "7602 9.96 72.00 81.96 ... 72.00 72.00",
            },
            {
                "page": 1,
                "line_number": "1.02",
                "label": "NCT Delhi Non-Plan Gap",
                "actual_plan": 0.0, "actual_non_plan": 0.0, "actual_total": 0.0,
                "be_prev_plan": 0.0, "be_prev_non_plan": 2.5, "be_prev_total": 2.5,
                "re_prev_plan": 0.0, "re_prev_non_plan": 2.5, "re_prev_total": 2.5,
                "be_plan": 0.0, "be_non_plan": 1.0, "be_total": 1.0,
                "verbatim_quote": "1.02 NCT Delhi 3602 ... ... ... ... 2.50 2.50",
            },
            {
                "page": 2,
                "line_number": "2.01",
                "label": "Puducherry Plan Assistance",
                "actual_plan": 643.12, "actual_non_plan": 0.0, "actual_total": 643.12,
                "be_prev_plan": 814.0, "be_prev_non_plan": 0.0, "be_prev_total": 814.0,
                "re_prev_plan": 788.0, "re_prev_non_plan": 0.0, "re_prev_total": 788.0,
                "be_plan": 744.01, "be_non_plan": 0.0, "be_total": 744.01,
                "verbatim_quote": "2.01 Puducherry Plan Assistance 3602 643.12",
            },
            {
                "page": 2,
                "line_number": "2.02",
                "label": "NCT Delhi Plan Assistance",
                "actual_plan": 582.51, "actual_non_plan": 0.0, "actual_total": 582.51,
                "be_prev_plan": 325.0, "be_prev_non_plan": 0.0, "be_prev_total": 325.0,
                "re_prev_plan": 325.0, "re_prev_non_plan": 0.0, "re_prev_total": 325.0,
                "be_plan": 394.99, "be_non_plan": 0.0, "be_total": 394.99,
                "verbatim_quote": "2.02 NCT Delhi Plan Assistance 3602 582.51",
            },
            {
                "page": 2,
                "line_number": "3",
                "label": "Actual Recoveries",
                "actual_plan": -3.93, "actual_non_plan": 0.0, "actual_total": -3.93,
                "be_prev_plan": 0.0, "be_prev_non_plan": 0.0, "be_prev_total": 0.0,
                "re_prev_plan": 0.0, "re_prev_non_plan": 0.0, "re_prev_total": 0.0,
                "be_plan": 0.0, "be_non_plan": 0.0, "be_total": 0.0,
                "verbatim_quote": "3. Actual Recoveries 3602 -3.93 ... -3.93",
            },
        ],
    }


def _seed_r1_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sha: str = SAMPLE_SHA,
    *,
    include_page_texts: bool = True,
    min_rows: int = 2,
    golden_expected: dict[str, Any] | None = None,
    min_golden_accuracy: float | None = None,
    extra_tasks: list[dict[str, Any]] | None = None,
) -> Path:
    pdir = tmp_path / "projects"
    pdir.mkdir(exist_ok=True)
    reading_ctx: dict[str, Any] = {
        "document_type": "union_budget_sbe",
        "union_budget": "2015-16",
        "demand_no": 57,
        "ministry": "Ministry of Home Affairs",
        "department": "Transfers to Union Territory Governments",
        "page_count": 3,
        "table_pages": [1, 2],
        "periods": [
            "Actual 2013-2014",
            "Budget 2014-2015",
            "Revised 2014-2015",
            "Budget 2015-2016",
        ],
        "subcolumns": ["Plan", "Non-Plan", "Total"],
        "expected_grand_total": dict(SAMPLE_GT),
        "min_rows": min_rows,
    }
    if include_page_texts:
        reading_ctx["page_texts"] = {"1": PAGE_1_TEXT, "2": PAGE_2_TEXT}
    if golden_expected is not None:
        reading_ctx["golden_expected"] = golden_expected
    if min_golden_accuracy is not None:
        reading_ctx["min_golden_accuracy"] = min_golden_accuracy

    tasks: list[dict[str, Any]] = [
        {
            "id": "r1-ub-2015-16-d057",
            "title": "AI read · 2015-16 Demand 57: Transfers to Union Territory Governments (p.1–2, ₹1,768.00 Cr)",
            "instructions": "Extract Section A leaf rows so all 12 columns sum to Grand Total.",
            "type": "extract_data",
            "methodology_code": "R1",
            "dataset_key": "ub-reading-2015-16-d057",
            "financial_quantity_type": "budget_and_treasury_expenditure",
            "estimated_tokens": 12000,
            "estimate_basis": "2 table pages (~6,000 tokens/page).",
            "payload": {
                "artifact_title": "Union Budget 2015-16 Demand No. 57",
                "retrieval_url": "https://www.indiabudget.gov.in/budget2015-2016/ub2015-16/eb/sbe57.pdf",
                "expected_sha256": sha,
                "official_publisher": "Ministry of Finance, Government of India",
                "source_landing_page": "https://www.indiabudget.gov.in/budget2015-2016/ub2015-16/eb/stat2.htm",
                "context": {"reading": reading_ctx},
            },
        }
    ]
    if extra_tasks:
        tasks.extend(extra_tasks)

    proj = {
        "slug": "union-budget-unspent-funds",
        "project_code": "P3",
        "title": "Union Budget Reading Pilot Project",
        "summary": "Live R1 document-reading pilot verifying Union Budget PDF extractions deterministically.",
        "posted_by": {"name": "AI Watchdogs India"},
        "pillar": "budget",
        "jurisdiction": "Union of India",
        "authority": "Ministry of Finance",
        "methodology_code": "UB1",
        "status": "active",
        "priority": 1,
        "sources": [
            {
                "name": "Union Budget 2015-16 Demand 57",
                "publisher": "Ministry of Finance",
                "url": "https://www.indiabudget.gov.in/budget2015-2016/ub2015-16/eb/sbe57.pdf",
            }
        ],
        "tasks": tasks,
        "planned_tasks": [
            {
                "title": "Find complete official copies of 6 truncated 2015-16 budget PDFs",
                "type": "find_sources",
                "estimated_tokens": 2_000_000,
                "estimate_basis": "6 truncated 2-page PDFs on indiabudget.gov.in need complete mirrors.",
            }
        ],
    }
    assert validate_project(proj, filename_stem="union-budget-unspent-funds") == []
    (pdir / "union-budget-unspent-funds.json").write_text(json.dumps(proj), encoding="utf-8")
    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(pdir))
    return pdir


def test_multipage_reading_verifier_plan_nonplan_and_official_anchor() -> None:
    sub = _valid_d57_extraction()
    res = verify_budget_extraction(
        sub,
        expected_sha256=SAMPLE_SHA,
        page_texts={1: PAGE_1_TEXT, 2: PAGE_2_TEXT},
        expected_grand_total=SAMPLE_GT,
        max_page=3,
    )
    assert res.accepted is True
    assert res.verification_mode == "deterministic_verified"
    assert res.checks["vertical_grand_total"] == "pass"
    assert res.checks["grand_total_anchor"] == "pass"
    assert res.checks["horizontal_row_totals"] == "pass"
    assert res.checks["verbatim_quotes"] == "pass"
    assert res.checks["column_headers_anchored"] == "pass"

    # Fabricated self-consistent table with fake Grand Total must be REJECTED against official anchor!
    fake = {
        "document_sha256": SAMPLE_SHA,
        "page": 1,
        "column_headers": ["Plan", "Non-Plan", "Total"],
        "printed_grand_total": {k: 10.0 for k in SAMPLE_GT},
        "rows": [
            {
                "page": 1,
                "label": "Fake single row",
                **{k: 10.0 for k in SAMPLE_GT},
                "verbatim_quote": "1.01.01 Overall Gap",
            }
        ],
    }
    res_fake = verify_budget_extraction(fake, expected_sha256=SAMPLE_SHA, expected_grand_total=SAMPLE_GT)
    assert res_fake.accepted is False
    assert res_fake.checks["grand_total_anchor"] == "fail"
    assert any("anchor mismatch" in e.lower() for e in res_fake.errors)

    # Horizontal Plan + Non-Plan != Total error caught cleanly
    bad_horiz = _valid_d57_extraction()
    bad_horiz["rows"][0]["be_total"] = 999.0
    res_h = verify_budget_extraction(
        bad_horiz,
        expected_sha256=SAMPLE_SHA,
        page_texts={1: PAGE_1_TEXT, 2: PAGE_2_TEXT},
        expected_grand_total=SAMPLE_GT,
    )
    assert res_h.accepted is False
    assert res_h.checks["horizontal_row_totals"] == "fail"


def test_r1_capability_isolation_and_server_anti_hallucination_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_r1_project(tmp_path, monkeypatch)
    app = create_app(db_path=tmp_path / "r1_gate.db", seed=True)
    with TestClient(app, base_url="http://testserver") as client:
        reg_cpu = client.post(
            "/api/agents/register",
            headers={"User-Agent": CLI_USER_AGENT},
            json={
                "handle": "cpu-worker",
                "provider": "rule-engine",
                "model": "deterministic-python-v1",
                "supported_methodologies": ["P1", "UB1", "W1"],
            },
        ).json()
        auth_cpu = {"X-Agent-Token": reg_cpu["api_token"], "User-Agent": CLI_USER_AGENT}

        # Standard rule-engine worker (document_reading=False) NEVER gets handed R1 tasks
        r_empty = client.post(
            "/api/desk/next",
            headers=auth_cpu,
            json={"mode": "investigate", "supported_methodologies": ["UB1", "R1"], "document_reading": False},
        )
        assert r_empty.status_code == 204

        # Direct task_id claim without document_reading=True returns 409 Conflict
        r_direct = client.post(
            "/api/desk/next",
            headers=auth_cpu,
            json={"task_id": "prj-r1-ub-2015-16-d057", "document_reading": False},
        )
        assert r_direct.status_code == 409
        assert "document_reading=true" in r_direct.json()["detail"]

        # Reader worker with document_reading=True claims the R1 task cleanly
        r_claim = client.post(
            "/api/desk/next",
            headers=auth_cpu,
            json={
                "mode": "investigate",
                "document_reading": True,
                "supported_methodologies": ["R1"],
            },
        )
        assert r_claim.status_code == 200
        lease = r_claim.json()
        assert lease["task"]["methodology_code"] == "R1"
        assert lease["role"]["title"] == "Document reader"
        assert lease["role"]["submit_outcomes"] == "reading_extraction"

        # Wrong outcome rejected (422)
        r_wrong = client.post(
            "/api/desk/submit",
            headers=auth_cpu,
            json={"lease_id": lease["lease_id"], "outcome": "cleared"},
        )
        assert r_wrong.status_code == 422

        # Fake totals rejected by server-side official Grand Total anchor (422)
        bad_sub = _valid_d57_extraction()
        bad_sub["printed_grand_total"]["be_total"] = 9999.0
        r_bad = client.post(
            "/api/desk/submit",
            headers=auth_cpu,
            json={"lease_id": lease["lease_id"], "outcome": "reading_extraction", "extraction": bad_sub},
        )
        assert r_bad.status_code == 422
        assert "anchor mismatch" in r_bad.json()["detail"].lower()

        # Valid extraction accepted, settled, scored (+25 pts), and published via /readings/{task_id}
        good_sub = _valid_d57_extraction()
        r_ok = client.post(
            "/api/desk/submit",
            headers=auth_cpu,
            json={"lease_id": lease["lease_id"], "outcome": "reading_extraction", "extraction": good_sub},
        )
        assert r_ok.status_code == 200, r_ok.text
        body = r_ok.json()
        assert body["status"] == "accepted"
        assert body["rows_extracted"] == 6
        assert body["verification_mode"] == "deterministic_verified"
        assert body["reading_url"] == "/api/missions/union-budget-unspent-funds/readings/prj-r1-ub-2015-16-d057"

        # Published extraction endpoint serves full verified rows & receipt
        pub = client.get(body["reading_url"])
        assert pub.status_code == 200
        pub_json = pub.json()
        assert pub_json["contributor_handle"] == "cpu-worker"
        assert len(pub_json["extraction"]["rows"]) == 6
        assert pub_json["verification"]["accepted"] is True

        # Contributor received +25 verified reading points
        prof = client.get("/api/contributors/cpu-worker").json()
        assert prof["contributor"]["compute_score"] == 25

        # Backlog endpoint surfaces live_reading_tasks summary
        backlog = client.get("/api/reading/backlog").json()
        assert backlog["live_reading_tasks"]["total"] == 1
        assert backlog["live_reading_tasks"]["completed"] == 1
        assert backlog["live_reading_tasks"]["items"][0]["reading_url"] == body["reading_url"]


def test_autonomous_llm_runner_self_corrects_and_completes_r1_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_r1_project(tmp_path, monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key")
    app = create_app(db_path=tmp_path / "r1_runner.db", seed=True)

    # Mock LLM HTTP transport: attempt 1 drops the last row (Grand Total mismatch),
    # attempt 2 receives the deterministic error feedback and returns the exact balanced table!
    call_count = {"n": 0}
    prompts_seen: list[str] = []

    def _mock_llm_handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        req_body = json.loads(request.content.decode("utf-8"))
        user_msg = req_body["messages"][-1]["content"]
        prompts_seen.append(user_msg)
        if call_count["n"] == 1:
            incomplete = _valid_d57_extraction()
            incomplete["rows"] = incomplete["rows"][:-1]  # missing Actual Recoveries (-3.93)
            content = json.dumps(incomplete)
        else:
            content = json.dumps(_valid_d57_extraction())
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 1200, "completion_tokens": 600},
            },
        )

    mock_llm_client = httpx.Client(transport=httpx.MockTransport(_mock_llm_handler))

    with TestClient(app, base_url="http://testserver") as hub_client:
        cfg = AgentConfig(
            handle="ai-reader-bot",
            hub_url="http://testserver",
            provider="openai",
            model="gpt-4o-mini",
            mode="investigate",
            allow_paid_provider=True,
            max_usd=1.0,
            daily_max_usd=1.0,
        )
        prov = ProviderInfo(
            provider="openai",
            model="gpt-4o-mini",
            label="OpenAI gpt-4o-mini",
            base_url="https://api.openai.com/v1",
            api_key_env="OPENAI_API_KEY",
        )
        runner = AgentRunner(
            cfg,
            provider_cfg=prov,
            http_client=hub_client,
            config_path=tmp_path / "reader.json",
            llm_http_client=mock_llm_client,
        )

        # Stub PDF download with the exact pinned SHA-256 and page text records
        def _fake_load(*_args: Any, **_kwargs: Any) -> LoadedDataset:
            return LoadedDataset(
                source_ref="https://www.indiabudget.gov.in/budget2015-2016/ub2015-16/eb/sbe57.pdf",
                format="pdf",
                sha256=SAMPLE_SHA,
                expected_sha256=SAMPLE_SHA,
                digest_verified=True,
                byte_size=271000,
                records=[
                    {"page": 1, "raw_text": PAGE_1_TEXT},
                    {"page": 2, "raw_text": PAGE_2_TEXT},
                    {"page": 3, "raw_text": "Explanatory notes"},
                ],
                raw_text=PAGE_1_TEXT,
            )

        monkeypatch.setattr(runner, "_load_task_dataset", _fake_load)
        summary = runner.run_loop(max_tasks=1, preferred_mission="union-budget-unspent-funds", save_on_register=False)
        assert summary.tasks_completed == 1
        assert call_count["n"] == 2
        assert "PREVIOUS ATTEMPT FAILED DETERMINISTIC VERIFICATION" in prompts_seen[1]
        assert "Grand Total check failed" in prompts_seen[1]

        # Verify bundle helper produces clean coding-agent instructions too
        bundle = build_coding_agent_reading_bundle(
            {"lease_id": "lease-demo", "lease_expires_at": "2026-11-28T12:00:00Z", "task": {"id": "t1", "payload": {"context": {"reading": {"expected_grand_total": SAMPLE_GT}}}}},
            page_texts=extract_pdf_pages_for_reading(
                [{"page": 1, "raw_text": PAGE_1_TEXT}, {"page": 2, "raw_text": PAGE_2_TEXT}],
                table_pages=[1, 2],
            ),
            sha256=SAMPLE_SHA,
        )
        assert set(bundle["page_texts"].keys()) == {"1", "2"}
        assert bundle["extraction_template"]["printed_grand_total"]["be_total"] == 1768.0
    mock_llm_client.close()


@pytest.mark.production_missions
def test_production_p3_manifest_has_11_verified_r1_reading_tasks() -> None:
    p3_path = REPO_PROJECTS / "union-budget-unspent-funds.json"
    data = json.loads(p3_path.read_text(encoding="utf-8"))
    assert validate_project(data, filename_stem="union-budget-unspent-funds") == []
    r1 = [t for t in data["tasks"] if t.get("methodology_code") == "R1"]
    assert len(r1) == 11
    total_be = sum(t["payload"]["context"]["reading"]["expected_grand_total"]["be_total"] for t in r1)
    assert total_be > 230_000.0  # ₹2.32 lakh crore+ unlocked for live AI reading
    # Every pinned R1 task must reference an existing SHA-256-verified page_texts bundle on disk
    for t in r1:
        rc = t["payload"]["context"]["reading"]
        rel_path = rc.get("page_texts_path")
        expected_sha = rc.get("page_texts_sha256")
        assert rel_path and expected_sha
        bundle_file = REPO_PROJECTS / rel_path
        assert bundle_file.is_file(), f"Missing pinned reading bundle: {rel_path}"
        assert hashlib.sha256(bundle_file.read_bytes()).hexdigest() == expected_sha
        bundle_data = json.loads(bundle_file.read_text(encoding="utf-8"))
        assert len(bundle_data.get("pages") or {}) >= 1
    # Ensure planned_tasks now cleanly reflects the 6 truncated PDFs instead of all 17
    planned_titles = [p["title"] for p in data.get("planned_tasks", [])]
    assert any("6 truncated 2015-16" in t for t in planned_titles)


def test_hub_rejects_fabricated_one_row_table_matching_official_grand_total(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closes Gap #1: Hub MUST run server-side quote/header/figure checks against pinned page texts."""
    _seed_r1_project(tmp_path, monkeypatch, include_page_texts=True, min_rows=2)
    app = create_app(db_path=tmp_path / "anti_spoof.db", seed=True)
    with TestClient(app, base_url="http://testserver") as client:
        reg = client.post(
            "/api/agents/register",
            headers={"User-Agent": CLI_USER_AGENT},
            json={"handle": "spoof-tester", "provider": "openai", "model": "gpt-4o-mini"},
        ).json()
        auth = {"X-Agent-Token": reg["api_token"], "User-Agent": CLI_USER_AGENT}
        lease = client.post(
            "/api/desk/next",
            headers=auth,
            json={"mode": "investigate", "document_reading": True, "supported_methodologies": ["R1"]},
        ).json()

        # Attack A: 1-row invented table whose numbers equal the official Grand Total (fake quote)
        attack_a = {
            "document_sha256": SAMPLE_SHA,
            "page": 1,
            "pages": [1, 2],
            "column_headers": ["Actual 2013-2014", "Budget 2015-2016", "Plan", "Non-Plan", "Total"],
            "printed_grand_total": dict(SAMPLE_GT),
            "rows": [
                {
                    "page": 1,
                    "label": "Invented single row summing to Grand Total",
                    **SAMPLE_GT,
                    "verbatim_quote": "1.01.01 Fake row 1231.66 585.00 1816.66",
                }
            ],
        }
        r_a = client.post(
            "/api/desk/submit",
            headers=auth,
            json={"lease_id": lease["lease_id"], "outcome": "reading_extraction", "extraction": attack_a},
        )
        assert r_a.status_code == 422, r_a.text

        # Attack B: 2-row table copying the real "Grand Total" quote line to spoof verbatim quote check
        half_gt = {k: round(v / 2.0, 2) for k, v in SAMPLE_GT.items()}
        other_half = {k: round(SAMPLE_GT[k] - half_gt[k], 2) for k in SAMPLE_GT}
        attack_b = {
            "document_sha256": SAMPLE_SHA,
            "page": 1,
            "pages": [1, 2],
            "column_headers": ["Actual 2013-2014", "Budget 2015-2016", "Plan", "Non-Plan", "Total"],
            "printed_grand_total": dict(SAMPLE_GT),
            "rows": [
                {
                    "page": 2,
                    "label": "Fake Half 1",
                    **half_gt,
                    "verbatim_quote": "Grand Total 1231.66 585.00 1816.66",
                },
                {
                    "page": 2,
                    "label": "Fake Half 2",
                    **other_half,
                    "verbatim_quote": "Grand Total 1139.00 629.00 1768.00",
                },
            ],
        }
        r_b = client.post(
            "/api/desk/submit",
            headers=auth,
            json={"lease_id": lease["lease_id"], "outcome": "reading_extraction", "extraction": attack_b},
        )
        assert r_b.status_code == 422, r_b.text

        # Attack C: 2-row table with real Page-1 quotes paired with invented half-totals not on the page!
        attack_c = {
            "document_sha256": SAMPLE_SHA,
            "page": 1,
            "pages": [1, 2],
            "column_headers": ["Actual 2013-2014", "Budget 2015-2016", "Plan", "Non-Plan", "Total"],
            "printed_grand_total": dict(SAMPLE_GT),
            "rows": [
                {
                    "page": 1,
                    "label": "Spoofed Real Quote 1",
                    **half_gt,
                    "verbatim_quote": "1.01.01 Overall Gap 3602 ... 513.00 513.00",
                },
                {
                    "page": 2,
                    "label": "Spoofed Real Quote 2",
                    **other_half,
                    "verbatim_quote": "2.01 Puducherry Plan Assistance 3602 643.12",
                },
            ],
        }
        r_c = client.post(
            "/api/desk/submit",
            headers=auth,
            json={"lease_id": lease["lease_id"], "outcome": "reading_extraction", "extraction": attack_c},
        )
        assert r_c.status_code == 422, r_c.text

        # Contributor earned ZERO points and nothing was published!
        prof = client.get("/api/contributors/spoof-tester").json()
        assert prof["contributor"]["compute_score"] == 0
        pub = client.get("/api/missions/union-budget-unspent-funds/readings/prj-r1-ub-2015-16-d057")
        assert pub.status_code == 404


def test_image_only_scan_routes_to_second_model_and_scores_on_agreement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closes Gap #4a: textless/scan R1 task is held as pending_second_model until 2nd distinct handle+model agrees."""
    _seed_r1_project(tmp_path, monkeypatch, include_page_texts=False, min_rows=2)
    app = create_app(db_path=tmp_path / "scan_two_model.db", seed=True)
    with TestClient(app, base_url="http://testserver") as client:
        r1_agent = client.post(
            "/api/agents/register",
            headers={"User-Agent": CLI_USER_AGENT},
            json={
                "handle": "reader-alpha",
                "provider": "anthropic",
                "model": "claude-opus-4",
                "coding_agent": "Claude Code",
                "coding_agent_model": "claude-opus-4",
            },
        ).json()
        r_same_model = client.post(
            "/api/agents/register",
            headers={"User-Agent": CLI_USER_AGENT},
            json={
                "handle": "reader-clone",
                "provider": "anthropic",
                "model": "claude-opus-4",
                "coding_agent": "Claude Code",
                "coding_agent_model": "claude-opus-4",
            },
        ).json()
        r2_agent = client.post(
            "/api/agents/register",
            headers={"User-Agent": CLI_USER_AGENT},
            json={
                "handle": "reader-beta",
                "provider": "openai",
                "model": "gpt-5",
                "coding_agent": "Codex",
                "coding_agent_model": "gpt-5",
            },
        ).json()
        auth1 = {"X-Agent-Token": r1_agent["api_token"], "User-Agent": CLI_USER_AGENT}
        auth_clone = {"X-Agent-Token": r_same_model["api_token"], "User-Agent": CLI_USER_AGENT}
        auth2 = {"X-Agent-Token": r2_agent["api_token"], "User-Agent": CLI_USER_AGENT}

        # 1st reader claims and submits valid extraction on image-only scan (no pinned text layer)
        lease1 = client.post(
            "/api/desk/next",
            headers=auth1,
            json={"mode": "investigate", "document_reading": True, "supported_methodologies": ["R1"]},
        ).json()
        sub1 = client.post(
            "/api/desk/submit",
            headers=auth1,
            json={
                "lease_id": lease1["lease_id"],
                "outcome": "reading_extraction",
                "extraction": _valid_d57_extraction(),
            },
        )
        assert sub1.status_code == 200, sub1.text
        b1 = sub1.json()
        assert b1["status"] == "pending_second_model"
        assert b1["verification_mode"] == "requires_second_model"
        assert b1["verification_status"] == "pending_second_model"
        assert b1["reading_url"] is None
        assert b1["second_model_task_id"] == "prj-r1-ub-2015-16-d057-r2"

        # 1st reader gets 0 pts so far, and reading is NOT published yet
        assert client.get("/api/contributors/reader-alpha").json()["contributor"]["compute_score"] == 0
        assert client.get("/api/missions/union-budget-unspent-funds/readings/prj-r1-ub-2015-16-d057").status_code == 404

        # Same handle CANNOT claim the paired -r2 task
        same_handle_claim = client.post(
            "/api/desk/next",
            headers=auth1,
            json={"mode": "investigate", "document_reading": True, "supported_methodologies": ["R1"]},
        )
        assert same_handle_claim.status_code == 204

        # Different handle with SAME model is automatically skipped on queue claim (204) and blocked on explicit claim (409)
        clone_queue = client.post(
            "/api/desk/next",
            headers=auth_clone,
            json={"mode": "investigate", "document_reading": True, "supported_methodologies": ["R1"]},
        )
        assert clone_queue.status_code == 204
        clone_explicit = client.post(
            "/api/desk/next",
            headers=auth_clone,
            json={
                "task_id": "prj-r1-ub-2015-16-d057-r2",
                "document_reading": True,
                "supported_methodologies": ["R1"],
            },
        )
        assert clone_explicit.status_code == 409
        assert "distinct model" in clone_explicit.json()["detail"].lower()

        # 2nd reader with distinct handle AND distinct model claims -r2 and agrees
        lease2 = client.post(
            "/api/desk/next",
            headers=auth2,
            json={"mode": "investigate", "document_reading": True, "supported_methodologies": ["R1"]},
        )
        assert lease2.status_code == 200
        assert lease2.json()["task"]["id"] == "prj-r1-ub-2015-16-d057-r2"
        sub2 = client.post(
            "/api/desk/submit",
            headers=auth2,
            json={
                "lease_id": lease2.json()["lease_id"],
                "outcome": "reading_extraction",
                "extraction": _valid_d57_extraction(),
            },
        )
        assert sub2.status_code == 200, sub2.text
        b2 = sub2.json()
        assert b2["status"] == "accepted"
        assert b2["verification_status"] == "two_model_verified"
        assert b2["reading_url"] == "/api/missions/union-budget-unspent-funds/readings/prj-r1-ub-2015-16-d057"

        # BOTH readers receive +25 verified reading points and the reading is now published!
        assert client.get("/api/contributors/reader-alpha").json()["contributor"]["compute_score"] == 25
        assert client.get("/api/contributors/reader-beta").json()["contributor"]["compute_score"] == 25
        pub = client.get(b2["reading_url"])
        assert pub.status_code == 200
        assert pub.json()["verification_status"] == "two_model_verified"


def test_image_only_scan_disagreement_marks_disputed_without_points_or_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When two distinct models disagree on a scan reading, mark disputed_reading with 0 points and no publication."""
    _seed_r1_project(tmp_path, monkeypatch, include_page_texts=False, min_rows=2)
    # Use a scanned state budget document_type without pre-extracted expected_grand_total so two internally
    # self-consistent readings with different totals pass individual math and trigger the Hub's 2-model check!
    p3_file = tmp_path / "projects" / "union-budget-unspent-funds.json"
    p3_doc = json.loads(p3_file.read_text(encoding="utf-8"))
    rc = p3_doc["tasks"][0]["payload"]["context"]["reading"]
    rc["document_type"] = "state_budget_scan"
    rc.pop("expected_grand_total", None)
    assert validate_project(p3_doc, filename_stem="union-budget-unspent-funds") == []
    p3_file.write_text(json.dumps(p3_doc), encoding="utf-8")

    app = create_app(db_path=tmp_path / "scan_disagree.db", seed=True)
    with TestClient(app, base_url="http://testserver") as client:
        r1 = client.post(
            "/api/agents/register",
            headers={"User-Agent": CLI_USER_AGENT},
            json={"handle": "scan-r1", "provider": "anthropic", "model": "claude-opus-4"},
        ).json()
        r2 = client.post(
            "/api/agents/register",
            headers={"User-Agent": CLI_USER_AGENT},
            json={"handle": "scan-r2", "provider": "openai", "model": "gpt-5"},
        ).json()
        auth1 = {"X-Agent-Token": r1["api_token"], "User-Agent": CLI_USER_AGENT}
        auth2 = {"X-Agent-Token": r2["api_token"], "User-Agent": CLI_USER_AGENT}

        l1_resp = client.post(
            "/api/desk/next",
            headers=auth1,
            json={"mode": "investigate", "document_reading": True, "supported_methodologies": ["R1"]},
        )
        assert l1_resp.status_code == 200, l1_resp.text
        l1 = l1_resp.json()
        s1 = client.post(
            "/api/desk/submit",
            headers=auth1,
            json={"lease_id": l1["lease_id"], "outcome": "reading_extraction", "extraction": _valid_d57_extraction()},
        )
        assert s1.status_code == 200
        assert s1.json()["verification_status"] == "pending_second_model"

        # Reader 2 submits a self-consistent extraction with a shifted total (disagrees with Reader 1)
        alt_sub = _valid_d57_extraction()
        alt_sub["rows"][0]["be_non_plan"] += 100.0
        alt_sub["rows"][0]["be_total"] += 100.0
        alt_sub["printed_grand_total"]["be_non_plan"] += 100.0
        alt_sub["printed_grand_total"]["be_total"] += 100.0

        l2 = client.post(
            "/api/desk/next",
            headers=auth2,
            json={"mode": "investigate", "document_reading": True, "supported_methodologies": ["R1"]},
        ).json()
        s2 = client.post(
            "/api/desk/submit",
            headers=auth2,
            json={"lease_id": l2["lease_id"], "outcome": "reading_extraction", "extraction": alt_sub},
        )
        assert s2.status_code == 200, s2.text
        b2 = s2.json()
        assert b2["status"] == "disputed"
        assert b2["verification_status"] == "disputed_reading"
        assert b2["reading_url"] is None
        assert len(b2["disagreement_details"]) >= 1
        assert client.get("/api/contributors/scan-r1").json()["contributor"]["compute_score"] == 0
        assert client.get("/api/contributors/scan-r2").json()["contributor"]["compute_score"] == 0
        assert client.get("/api/missions/union-budget-unspent-funds/readings/prj-r1-ub-2015-16-d057").status_code == 404


def test_hub_enforces_seeded_golden_expected_calibration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closes Gap #4b: Hub enforces golden_expected / min_golden_accuracy when present on an R1 task."""
    golden = _valid_d57_extraction()
    _seed_r1_project(
        tmp_path,
        monkeypatch,
        include_page_texts=True,
        min_rows=2,
        golden_expected=golden,
        min_golden_accuracy=0.95,
    )
    app = create_app(db_path=tmp_path / "golden_cal.db", seed=True)
    with TestClient(app, base_url="http://testserver") as client:
        reg = client.post(
            "/api/agents/register",
            headers={"User-Agent": CLI_USER_AGENT},
            json={"handle": "golden-reader", "provider": "openai", "model": "gpt-4o-mini"},
        ).json()
        auth = {"X-Agent-Token": reg["api_token"], "User-Agent": CLI_USER_AGENT}
        lease = client.post(
            "/api/desk/next",
            headers=auth,
            json={"mode": "investigate", "document_reading": True, "supported_methodologies": ["R1"]},
        ).json()

        # Reordered / mislabeled extraction whose totals still sum to Grand Total and quotes still match page,
        # but fails row-by-row golden calibration accuracy (< 0.95) -> rejected 422!
        swapped = _valid_d57_extraction()
        swapped["rows"] = list(reversed(swapped["rows"]))
        r_bad = client.post(
            "/api/desk/submit",
            headers=auth,
            json={"lease_id": lease["lease_id"], "outcome": "reading_extraction", "extraction": swapped},
        )
        assert r_bad.status_code == 422, r_bad.text
        assert "golden" in r_bad.json()["detail"].lower()

        # Exact match passes calibration and records golden_accuracy == 1.0
        r_ok = client.post(
            "/api/desk/submit",
            headers=auth,
            json={"lease_id": lease["lease_id"], "outcome": "reading_extraction", "extraction": golden},
        )
        assert r_ok.status_code == 200, r_ok.text
        pub = client.get(r_ok.json()["reading_url"]).json()
        assert pub["verification"]["golden_accuracy"] == pytest.approx(1.0)
        assert pub["verification"]["checks"]["golden_calibration"] == "pass"


def test_scan_png_export_and_reading_first_join_txt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closes Gaps #3 and #4c: scan PNG export for image-only pages + reading-first /join.txt."""
    import shutil

    from pypdf import PdfWriter

    from aiwatchdogs.cli.reading_runner import render_pdf_pages_to_pngs

    # 1. Create a real blank/image-only 1-page PDF and verify render_pdf_pages_to_pngs
    pdf_path = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with pdf_path.open("wb") as fh:
        writer.write(fh)

    rendered = render_pdf_pages_to_pngs(pdf_path.read_bytes(), pages=[1], out_dir=tmp_path / "pages")
    if shutil.which("pdftoppm"):
        assert 1 in rendered
        assert Path(rendered[1]).is_file()
        assert Path(rendered[1]).suffix == ".png"
    else:
        assert rendered == {}

    bundle = build_coding_agent_reading_bundle(
        {"lease_id": "lease-scan", "task": {"id": "t-scan", "payload": {"context": {"reading": {}}}}},
        page_texts={1: ""},
        sha256=SAMPLE_SHA,
        page_images=rendered or {1: str(tmp_path / "pages" / "page-1.png")},
    )
    assert bundle["has_image_only_pages"] is True
    assert bundle["image_only_pages"] == [1]
    assert "1" in bundle["page_images"]

    # 2. Verify /join.txt puts AI document reading (read-claim / read-submit) BEFORE rule-engine join!
    _seed_r1_project(tmp_path, monkeypatch)
    app = create_app(db_path=tmp_path / "join_check.db", seed=True)
    with TestClient(app, base_url="http://testserver") as client:
        txt = client.get("/join.txt").text
        idx_read = txt.find("read-claim")
        idx_submit = txt.find("read-submit")
        idx_rule_engine = txt.find("--provider rule-engine")
        assert idx_read != -1 and idx_submit != -1 and idx_rule_engine != -1
        assert idx_read < idx_submit < idx_rule_engine
        lowered = txt.lower()
        for forbidden in ("demo", "sample", "ollama", "join.sh", "local checkout"):
            assert forbidden not in lowered


def test_summary_plus_zero_row_bypass_and_mixed_scan_and_missing_column_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression tests for Greptile P1 findings:
    1. Page-1 'Total ...' + all-zero row (and 'Revenue ...' + 'Capital ...' summary rows, or truncated summary quotes) MUST be rejected.
    2. Mixed PDF with a sparse/image-only page (like r1-ub-2022-23-d046 page 5) routes to 2nd model instead of failing quote lookup.
    3. 2nd-model corroboration MUST reject missing/extra columns even when shared columns match.
    """
    # 1. Summary-line + all-zero-row bypass against the REAL pinned r1-ub-2015-16-d057 text bundle!
    d57_bundle = json.loads((REPO_PROJECTS / "union-budget-unspent-funds/reading_pages/r1-ub-2015-16-d057.json").read_text(encoding="utf-8"))
    d57_pages = {int(k): v for k, v in d57_bundle["pages"].items()}
    d57_sha = d57_bundle["document_sha256"]
    zero_cols = {k: 0.0 for k in SAMPLE_GT}

    # Bypass 1A: Page-1 'Total 1231.66 ...' summary + all-zero real row
    bypass_total_zero = {
        "document_sha256": d57_sha,
        "page": 1,
        "pages": [1, 2],
        "column_headers": ["Actual 2013-2014", "Budget 2015-2016", "Plan", "Non-Plan", "Total"],
        "printed_grand_total": dict(SAMPLE_GT),
        "rows": [
            {
                "page": 1,
                "label": "Summary Transfer Row",
                **SAMPLE_GT,
                "verbatim_quote": "Total 1231.66 585.00 1816.66 1139.00 587.50 1726.50 1113.00 587.50 1700.50 1139.00 629.00 1768.00",
            },
            {
                "page": 1,
                "label": "Zero Padding Row",
                **zero_cols,
                "verbatim_quote": "1.01.01 Overall Gap 3602",
            },
        ],
    }
    res_1a = verify_budget_extraction(
        bypass_total_zero, expected_sha256=d57_sha, page_texts=d57_pages, expected_grand_total=SAMPLE_GT, min_rows=2
    )
    assert res_1a.accepted is False

    # Bypass 1B: Truncated quote stripping 'Total' (quoting only numbers from the Capital/Total summary line) + zero row
    bypass_trunc = json.loads(json.dumps(bypass_total_zero))
    bypass_trunc["rows"][0]["verbatim_quote"] = "1231.66 585.00 1816.66 1139.00 587.50 1726.50"
    res_1b = verify_budget_extraction(
        bypass_trunc, expected_sha256=d57_sha, page_texts=d57_pages, expected_grand_total=SAMPLE_GT, min_rows=2
    )
    assert res_1b.accepted is False

    # Bypass 1C: Page-1 'Revenue ...' + 'Capital ...' summary rows (both non-zero and summing to Grand Total!)
    bypass_rev_cap = {
        "document_sha256": d57_sha,
        "page": 1,
        "pages": [1, 2],
        "column_headers": ["Actual 2013-2014", "Budget 2015-2016", "Plan", "Non-Plan", "Total"],
        "printed_grand_total": dict(SAMPLE_GT),
        "rows": [
            {
                "page": 1,
                "label": "Revenue Summary",
                "actual_plan": 1221.70, "actual_non_plan": 513.00, "actual_total": 1734.70,
                "be_prev_plan": 1139.00, "be_prev_non_plan": 515.50, "be_prev_total": 1654.50,
                "re_prev_plan": 1113.00, "re_prev_non_plan": 515.50, "re_prev_total": 1628.50,
                "be_plan": 1139.00, "be_non_plan": 557.00, "be_total": 1696.00,
                "verbatim_quote": "Revenue 1221.70 513.00 1734.70 1139.00 515.50 1654.50",
            },
            {
                "page": 1,
                "label": "Capital Summary",
                "actual_plan": 9.96, "actual_non_plan": 72.00, "actual_total": 81.96,
                "be_prev_plan": 0.0, "be_prev_non_plan": 72.00, "be_prev_total": 72.00,
                "re_prev_plan": 0.0, "re_prev_non_plan": 72.00, "re_prev_total": 72.00,
                "be_plan": 0.0, "be_non_plan": 72.00, "be_total": 72.00,
                "verbatim_quote": "Capital 9.96 72.00 81.96 ... 72.00 72.00",
            },
        ],
    }
    res_1c = verify_budget_extraction(
        bypass_rev_cap, expected_sha256=d57_sha, page_texts=d57_pages, expected_grand_total=SAMPLE_GT, min_rows=2
    )
    assert res_1c.accepted is False

    # 2. Mixed scan PDF (page 1 text-backed, page 2 sparse < 20 chars like r1-ub-2022-23-d046 page 5)
    # Valid rows citing text-backed page 1 AND scanned page 2 MUST be accepted and routed to requires_second_model!
    mixed_pages = {1: PAGE_1_TEXT, 2: "NHM (NUHM & NRHM)"}
    mixed_res = verify_budget_extraction(
        _valid_d57_extraction(),
        expected_sha256=SAMPLE_SHA,
        page_texts=mixed_pages,
        expected_grand_total=SAMPLE_GT,
        min_rows=2,
    )
    assert mixed_res.accepted is True, mixed_res.errors
    assert mixed_res.verification_mode == "requires_second_model"
    assert mixed_res.checks["verbatim_quotes"] == "unrecorded"

    # Bad quote on the text-backed page 1 of a mixed PDF is STILL rejected!
    mixed_bad = _valid_d57_extraction()
    mixed_bad["rows"][0]["verbatim_quote"] = "1.01.01 Fabricated Line 513.00"
    mixed_bad_res = verify_budget_extraction(
        mixed_bad,
        expected_sha256=SAMPLE_SHA,
        page_texts=mixed_pages,
        expected_grand_total=SAMPLE_GT,
        min_rows=2,
    )
    assert mixed_bad_res.accepted is False

    # 3. 2nd-model scan corroboration rejects missing column subset even when shared column matches!
    _seed_r1_project(tmp_path, monkeypatch, include_page_texts=False, min_rows=2)
    p3_file = tmp_path / "projects" / "union-budget-unspent-funds.json"
    p3_doc = json.loads(p3_file.read_text(encoding="utf-8"))
    rc = p3_doc["tasks"][0]["payload"]["context"]["reading"]
    rc["document_type"] = "state_budget_scan"
    rc.pop("expected_grand_total", None)
    p3_file.write_text(json.dumps(p3_doc), encoding="utf-8")

    app = create_app(db_path=tmp_path / "scan_missing_col.db", seed=True)
    with TestClient(app, base_url="http://testserver") as client:
        a1 = client.post(
            "/api/agents/register",
            headers={"User-Agent": CLI_USER_AGENT},
            json={"handle": "col-r1", "provider": "anthropic", "model": "claude-opus-4"},
        ).json()
        a2 = client.post(
            "/api/agents/register",
            headers={"User-Agent": CLI_USER_AGENT},
            json={"handle": "col-r2", "provider": "openai", "model": "gpt-5"},
        ).json()
        h1 = {"X-Agent-Token": a1["api_token"], "User-Agent": CLI_USER_AGENT}
        h2 = {"X-Agent-Token": a2["api_token"], "User-Agent": CLI_USER_AGENT}

        # Reader 1 reports revenue + capital + total
        sub_full = {
            "document_sha256": SAMPLE_SHA,
            "page": 1,
            "column_headers": ["Revenue", "Capital", "Total"],
            "printed_grand_total": {"revenue": 100.0, "capital": 50.0, "total": 150.0},
            "rows": [
                {"page": 1, "label": "Scheme A", "revenue": 100.0, "capital": 0.0, "total": 100.0, "verbatim_quote": "1. Scheme A 100.00"},
                {"page": 1, "label": "Scheme B", "revenue": 0.0, "capital": 50.0, "total": 50.0, "verbatim_quote": "2. Scheme B 50.00"},
            ],
        }
        l1 = client.post("/api/desk/next", headers=h1, json={"mode": "investigate", "document_reading": True, "supported_methodologies": ["R1"]}).json()
        r1_resp = client.post("/api/desk/submit", headers=h1, json={"lease_id": l1["lease_id"], "outcome": "reading_extraction", "extraction": sub_full})
        assert r1_resp.status_code == 200
        assert r1_resp.json()["verification_status"] == "pending_second_model"

        # Reader 2 reports ONLY 'total': 150.0 (omitting revenue & capital!) -> MUST be disputed, NOT verified!
        sub_partial = {
            "document_sha256": SAMPLE_SHA,
            "page": 1,
            "column_headers": ["Total"],
            "printed_grand_total": {"total": 150.0},
            "rows": [
                {"page": 1, "label": "Scheme A", "total": 100.0, "verbatim_quote": "1. Scheme A 100.00"},
                {"page": 1, "label": "Scheme B", "total": 50.0, "verbatim_quote": "2. Scheme B 50.00"},
            ],
        }
        l2 = client.post("/api/desk/next", headers=h2, json={"mode": "investigate", "document_reading": True, "supported_methodologies": ["R1"]}).json()
        r2_resp = client.post("/api/desk/submit", headers=h2, json={"lease_id": l2["lease_id"], "outcome": "reading_extraction", "extraction": sub_partial})
        assert r2_resp.status_code == 200, r2_resp.text
        b2 = r2_resp.json()
        assert b2["status"] == "disputed"
        assert b2["verification_status"] == "disputed_reading"
        assert b2["reading_url"] is None
        assert any("missing column" in m.lower() for m in b2["disagreement_details"])
        assert client.get("/api/contributors/col-r1").json()["contributor"]["compute_score"] == 0
        assert client.get("/api/contributors/col-r2").json()["contributor"]["compute_score"] == 0
