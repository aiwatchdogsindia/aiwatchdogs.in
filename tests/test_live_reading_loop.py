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


def _seed_r1_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sha: str = SAMPLE_SHA) -> Path:
    pdir = tmp_path / "projects"
    pdir.mkdir(exist_ok=True)
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
        "tasks": [
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
                    "context": {
                        "reading": {
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
                        }
                    },
                },
            }
        ],
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
    # Ensure planned_tasks now cleanly reflects the 6 truncated PDFs instead of all 17
    planned_titles = [p["title"] for p in data.get("planned_tasks", [])]
    assert any("6 truncated 2015-16" in t for t in planned_titles)
