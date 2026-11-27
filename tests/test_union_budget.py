"""UB1: Union Budget lines, budget estimate vs revised estimate vs actual (engines/union_budget.py)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from aiwatchdogs.connectors.loader import load_dataset
from aiwatchdogs.core.safety import lint_neutrality
from aiwatchdogs.engines import union_budget as ub
from aiwatchdogs.engines.registry import run_engine
from aiwatchdogs.projects import validate_project

REPO_PROJECTS = Path(__file__).resolve().parents[1] / "projects"
EST_URL = "https://www.indiabudget.gov.in/budget2020-21/doc/eb/sbe42.pdf"
ACT_URL = "https://www.indiabudget.gov.in/budget2021-22/doc/eb/sbe44.pdf"
PMSSY = "Pradhan Mantri Swasthya Suraksha Yojana"

# Notes on Demands for Grants as the pinned PDF reader returns them: Union Budget 2020-21 prints
# the budget and revised estimates of 2019-20; Union Budget 2021-22 prints its actuals.
ESTIMATES = [
    "Notes on Demands for Grants, 2020-2021  57",
    "No. 42/Department of Health and Family Welfare",
    "MINISTRY OF HEALTH AND FAMILY WELFARE",
    "DEMAND NO. 42",
    "Department of Health and Family Welfare",
    "(In ` crores)",
    "Actual 2018-2019 Budget 2019-2020 Revised 2019-2020 Budget 2020-2021",
    "Revenue Capital Total Revenue Capital Total Revenue Capital Total Revenue Capital Total",
    "Gross 211.00 ... 211.00 292.00 ... 292.00 196.00 ... 196.00 263.00 ... 263.00",
    "A. The Budget allocations, net of recoveries, are given below:",
    "CENTRE'S EXPENDITURE",
    "1. Secretariat 10.00 ... 10.00 12.00 ... 12.00 11.00 ... 11.00 13.00 ... 13.00",
    "2. National AIDS Control Programme 50.00 ... 50.00 100.00 ... 100.00 60.00 ... 60.00 120.00 ... 120.00",
    f"3. {PMSSY}",
    "3.01 Support from Gross Budgetary Support 20.00 ... 20.00 30.00 ... 30.00 10.00 ... 10.00 35.00 ... 35.00",
    "3.02 Support from National Investment Fund ... ... ... 10.00 ... 10.00 25.00 ... 25.00 5.00 ... 5.00",
    f"Total- {PMSSY} 20.00 ... 20.00 40.00 ... 40.00 35.00 ... 35.00 40.00 ... 40.00",
    "4. Capital Support to Public Sector Undertakings 1.00 ... 1.00 50.00 ... 50.00 5.00 ... 5.00 20.00 ... 20.00",
    "5. Grants to Autonomous Bodies 30.00 ... 30.00 50.00 ... 50.00 40.00 ... 40.00 45.00 ... 45.00",
    "6. Health Research 30.00 ... 30.00 40.00 ... 40.00 30.00 ... 30.00 30.00 ... 30.00",
    "Grand Total 141.00 ... 141.00 292.00 ... 292.00 196.00 ... 196.00 263.00 ... 263.00",
]
ACTUALS = [
    "Notes on Demands for Grants, 2021-2022  61",
    "No. 44/Department of Health and Family Welfare",
    "MINISTRY OF HEALTH AND FAMILY WELFARE",
    "DEMAND NO. 44",
    "Department of Health and Family Welfare",
    "(In ` crores)",
    "Actual 2019-2020 Budget 2020-2021 Revised 2020-2021 Budget 2021-2022",
    "Revenue Capital Total Revenue Capital Total Revenue Capital Total Revenue Capital Total",
    "A. The Budget allocations, net of recoveries, are given below:",
    "1. Secretariat 11.50 ... 11.50 13.00 ... 13.00 12.00 ... 12.00 14.00 ... 14.00",
    "2. National AIDS Control Programme 55.00 ... 55.00 120.00 ... 120.00 110.00 ... 110.00 130.00 ... 130.00",
    f"3. {PMSSY}",
    "3.01 Support from Gross Budgetary Support 30.00 ... 30.00 35.00 ... 35.00 30.00 ... 30.00 36.00 ... 36.00",
    "3.02 Support from National Investment Fund 2.00 ... 2.00 5.00 ... 5.00 5.00 ... 5.00 4.00 ... 4.00",
    "4. Capital Support to Public Sector Undertakings ... ... ... 20.00 ... 20.00 20.00 ... 20.00 ... ... ...",
    "5. Grants to Autonomous Bodies ... ... ... 45.00 ... 45.00 45.00 ... 45.00 46.00 ... 46.00",
    "6. Health Research 25.00 ... 25.00 31.00 ... 31.00 31.00 ... 31.00 32.00 ... 32.00",
    "Grand Total 123.50 ... 123.50 269.00 ... 269.00 253.00 ... 253.00 262.00 ... 262.00",
]
# Union Budget 2015-16, Plan / Non-Plan layout: a line split by major head closes on its 'Total'.
OLD_LAYOUT = [
    "Notes on Demands for Grants, 2015-2016 12",
    "MINISTRY OF AGRICULTURE",
    "DEMAND NO. 1",
    "Department of Agriculture and Cooperation",
    "Actual 2013-2014 Budget 2014-2015 Revised 2014-2015 Budget 2015-2016",
    "Plan Non-Plan Total Plan Non-Plan Total Plan Non-Plan Total Plan Non-Plan Total",
    "Revenue 185.08 73.17 258.25 200.00 84.66 284.66 240.80 83.84 324.64 13.00 85.74 98.74",
    "1. Secretariat - Economic Services 3451 10.61 62.56 73.17 13.00 71.66 84.66 12.80 71.04 83.84 13.00 85.74 98.74",
    "2. Development and strengthening of seed infrastructure facilities for",
    "production and distribution of Seeds",
    "2401 88.48 ... 88.48 86.00 ... 86.00 101.00 ... 101.00 ... ... ...",
    "3601 96.60 ... 96.60 101.00 ... 101.00 127.00 ... 127.00 ... ... ...",
    "Total 185.08 ... 185.08 187.00 ... 187.00 228.00 ... 228.00 ... ... ...",
    "Grand Total 195.69 62.56 258.25 200.00 71.66 271.66 240.80 71.04 311.84 13.00 85.74 98.74",
]


def _records(estimates: list[str], actuals: list[str] | None = None) -> list[dict]:
    out = [{"page": 1, "raw_text": "\n".join(estimates), "document": "primary"}]
    if actuals is not None:
        out.append({"page": 1, "raw_text": "\n".join(actuals), "document": "secondary"})
    return out


def _row(number: str, name: str) -> dict:
    return {"number": number, "name": name}


def _spec(*, with_actuals: bool = True, year: str = "2019-20") -> dict:
    lines = [
        {"key": "1", "estimates": [_row("1", "Secretariat")]},
        {"key": "2", "estimates": [_row("2", "National AIDS Control Programme")]},
        {
            "key": "3",
            "name": PMSSY,
            "estimates": [
                _row("3.01", f"{PMSSY} > Support from Gross Budgetary Support"),
                _row("3.02", f"{PMSSY} > Support from National Investment Fund"),
            ],
        },
        {"key": "4", "estimates": [_row("4", "Capital Support to Public Sector Undertakings")]},
        {"key": "5", "estimates": [_row("5", "Grants to Autonomous Bodies")]},
        {"key": "6", "estimates": [_row("6", "Health Research")]},
    ]
    spec: dict = {"financial_year": year, "estimates_document": {"budget": "2020-21", "demand_no": 42}, "lines": lines}
    if with_actuals:
        spec["actuals_document"] = {"budget": "2021-22", "demand_no": 44}
        for line in lines:
            line["actuals"] = [dict(row) for row in line["estimates"]]
            line["link"] = "name+figure"
    return spec


def _context(spec: dict) -> dict:
    ctx = {"union_budget": spec, "retrieval_url": EST_URL, "expected_sha256": "a" * 64}
    if "actuals_document" in spec:
        ctx.update(secondary_url=ACT_URL, secondary_expected_sha256="b" * 64)
    return ctx


def _by_key(leads: list) -> dict:
    return {lead.anomaly_metrics["line_key"]: lead for lead in leads}


def test_rows_two_printed_lines_on_one_text_line_and_figures_in_names() -> None:
    line = (
        "1. Rate 7.5 scheme 1.00 ... 1.00 2.00 ... 2.00 3.00 ... 3.00 4.00 ... 4.00 "
        "2. Next 5.00 ... 5.00 6.00 ... 6.00 7.00 ... 7.00 8.00 ... 8.00"
    )
    segments = ub.split_segments(line)
    assert [seg[0] for seg in segments] == [["1.", "Rate", "7.5", "scheme"], ["2.", "Next"]]
    assert all(seg[1] is not None and len(seg[1]) == ub.VALUES_PER_ROW for seg in segments)
    assert ub.split_segments("A heading without figures") == [(["A", "heading", "without", "figures"], None)]


def test_statement_header_table_and_lines_are_read() -> None:
    doc = ub.parse_statement({1: "\n".join(ESTIMATES)})
    assert doc.budget == "2020-21"
    assert doc.demand_no == 42 and doc.demand_label == "Demand No. 42"
    assert doc.ministry == "MINISTRY OF HEALTH AND FAMILY WELFARE"
    assert doc.department == "Department of Health and Family Welfare"
    assert doc.periods == ("2018-19", "2019-20", "2019-20", "2020-21")
    assert doc.subcolumns == ub.SUBCOLUMNS_NEW
    lines = {line.number: line for line in ub.numbered_lines(doc)}
    assert set(lines) == {"1", "2", "3.01", "3.02", "4", "5", "6"}  # heading 3 has no figures of its own
    assert lines["3.01"].parents == (PMSSY,)
    assert lines["3.01"].full_name == f"{PMSSY} > Support from Gross Budgetary Support"
    assert lines["2"].values[ub.COL_BUDGET_PREV] == 100.0
    assert lines["2"].values[ub.COL_REVISED_PREV] == 60.0
    assert lines["3.02"].values[ub.COL_ACTUAL] is None  # '...' is nil, never zero by accident


def test_old_layout_line_split_by_major_head_is_read_from_its_printed_total() -> None:
    doc = ub.parse_statement({1: "\n".join(OLD_LAYOUT)})
    assert doc.subcolumns == ub.SUBCOLUMNS_OLD and doc.periods == ("2013-14", "2014-15", "2014-15", "2015-16")
    lines = {line.number: line for line in ub.numbered_lines(doc)}
    assert lines["1"].values[ub.COL_BUDGET_PREV] == 84.66 and lines["1"].basis == "row"
    seeds = lines["2"]
    assert seeds.basis == "total"  # the two major heads add up to the printed Total row
    assert seeds.name == "Development and strengthening of seed infrastructure facilities for production and distribution of Seeds"
    assert (seeds.values[ub.COL_ACTUAL], seeds.values[ub.COL_BUDGET_PREV], seeds.values[ub.COL_REVISED_PREV]) == (
        185.08,
        187.0,
        228.0,
    )
    assert seeds.values[ub.COL_BUDGET] is None


def test_actual_below_estimate_rows_added_nil_actuals_and_regrouped_lines() -> None:
    leads, cleared, audited, summary, meta = ub.run_ub1_union_budget_lines(
        _records(ESTIMATES, ACTUALS), _context(_spec())
    )
    found = _by_key(leads)
    assert set(found) == {"2", "3", "4"}
    assert cleared == 1  # Secretariat: 0.50 of 12.00 unspent, under 10%
    assert meta["unevaluable_count"] == 2
    reasons = {r["key"]: r.get("reason", "") for r in meta["readings"] if r["status"] == "unevaluable"}
    assert "revised estimate stood" in reasons["5"]  # nil actual, but the revised estimate was 40 of 50
    assert "different budget estimates" in reasons["6"]  # 30.00 vs 31.00 for 2020-21: regrouped
    assert audited == pytest.approx((12 + 100 + 40 + 50 + 50 + 40) * ub.CRORE)

    aids = found["2"]
    assert aids.title == (
        "National AIDS Control Programme (Department of Health and Family Welfare): actual expenditure "
        "in 2019-20 was 45.0% below the budget estimate"
    )
    assert aids.fiscal_year == "FY2019-20" and aids.methodology_code == "UB1"
    assert aids.rule_triggered == "UB1-ACTUAL-BELOW-BE>10%"
    assert aids.monetary_exposure_inr == pytest.approx(45 * ub.CRORE)
    metrics = aids.anomaly_metrics
    assert (metrics["budget_estimate"], metrics["revised_estimate"], metrics["actual"]) == (100.0, 60.0, 55.0)
    assert (metrics["unspent"], metrics["unspent_pct"], metrics["revised_cut"]) == (45.0, 45.0, 40.0)
    assert metrics["link"] == "name+figure" and aids.severity == "medium"
    assert [(c["role"], c["source_url"], c["page"]) for c in aids.citations] == [
        ("estimates", EST_URL, 1),
        ("actuals", ACT_URL, 1),
    ]
    assert "Union Budget 2020-21" in aids.summary and "Union Budget 2021-22" in aids.summary
    assert "not a finding of wrongdoing" in aids.summary
    assert aids.suggested_authority == "Ministry of Health and Family Welfare"

    pmssy = found["3"]  # two funding rows of one scheme, added on both sides
    assert pmssy.entity_name == f"{PMSSY} (Department of Health and Family Welfare)"
    assert pmssy.anomaly_metrics["rows_added"] == 2
    assert (pmssy.anomaly_metrics["budget_estimate"], pmssy.anomaly_metrics["actual"]) == (40.0, 32.0)
    assert len(pmssy.citations) == 4
    assert "lines 3.01 and 3.02 (added together)" in pmssy.summary

    psu = found["4"]  # nil actual, carried into 2020-21, revised estimate cut to 5 of 50
    assert psu.anomaly_metrics["actual_printed"] is False and psu.anomaly_metrics["unspent"] == 50.0
    assert "no actual expenditure is printed for 2019-20" in psu.title

    for lead in leads:
        assert lint_neutrality(lead.title, lead.summary, lead.benign_explanations).passed
    assert "3 below the budget estimate" in summary


def test_without_actuals_the_revised_estimate_is_compared() -> None:
    leads, cleared, _, _, meta = ub.run_ub1_union_budget_lines(_records(ESTIMATES), _context(_spec(with_actuals=False)))
    found = _by_key(leads)
    assert found["2"].rule_triggered == "UB1-RE-BELOW-BE>10%"
    assert found["2"].anomaly_metrics["revised_cut"] == 40.0 and found["2"].anomaly_metrics["revised_cut_pct"] == 40.0
    assert "No actual expenditure for 2019-20 is compared here" in found["2"].summary
    # 3: 5 of 40 dropped (12.5%); 4: 45 of 50; 5: 10 of 50; 6: 10 of 40
    assert set(found) == {"2", "3", "4", "5", "6"}
    assert cleared == 1 and meta["unevaluable_count"] == 0  # Secretariat: 1 of 12 dropped (8.3%)


def test_wrong_statement_or_wrong_line_name_is_unevaluable_not_guessed() -> None:
    leads, cleared, _, summary, meta = ub.run_ub1_union_budget_lines(
        _records(ESTIMATES, ACTUALS), _context(_spec(year="2018-19"))
    )
    assert leads == [] and cleared == 0 and meta["unevaluable_count"] == 6
    assert "does not print budget and revised estimates for 2018-19" in summary

    spec = _spec()
    spec["lines"][1]["estimates"][0]["name"] = "Some other programme"
    spec["lines"][2]["actuals"][1]["number"] = "3.09"
    _, _, _, _, meta = ub.run_ub1_union_budget_lines(_records(ESTIMATES, ACTUALS), _context(spec))
    reasons = {r["key"]: r["reason"] for r in meta["readings"] if r["status"] == "unevaluable"}
    assert "different name" in reasons["2"]
    assert "3.09 is not printed" in reasons["3"]

    spec = _spec()
    spec["estimates_document"]["demand_no"] = 43
    leads, _, _, summary, _ = ub.run_ub1_union_budget_lines(_records(ESTIMATES, ACTUALS), _context(spec))
    assert leads == [] and "not the Demand the task names" in summary


def test_entity_names_fit_the_hub_and_neutral_text() -> None:
    long_name = " > ".join(["A heading"] * 30 + ["The scheme itself"])
    label = ub.entity_label(long_name, "Department of Health and Family Welfare")
    assert label == "The scheme itself (Department of Health and Family Welfare)"
    assert len(ub.entity_label("x" * 500, "Department of Revenue")) == ub.MAX_ENTITY_LENGTH
    assert ub.neutral_text("Anti Corruption Bureau grants") == "Vigilance Bureau grants"
    assert ub.display_ministry("MINISTRY OF HEALTH AND FAMILY WELFARE") == "Ministry of Health and Family Welfare"


def test_registry_run_is_reproducible_and_lint_clean() -> None:
    records, ctx = _records(ESTIMATES, ACTUALS), _context(_spec())
    first = run_engine("UB1", records, ctx)
    second = run_engine("UB1", records, ctx)
    assert first.script_hash == second.script_hash
    assert [lead.model_dump() for lead in first.leads] == [lead.model_dump() for lead in second.leads]
    assert first.ruleset_version == "UB1-2026.11-v1" and len(first.leads) == 3
    assert first.unevaluable_count == 2 and first.amount_type == "budget_and_treasury_expenditure"


def test_task_context_validation() -> None:
    assert ub.validate_context({"union_budget": _spec()}, "tasks[0]") == []
    assert ub.validate_context({"union_budget": _spec(with_actuals=False)}, "tasks[0]") == []
    assert ub.validate_context({}, "tasks[0]") == ["tasks[0].payload.context.union_budget is required for UB1 tasks."]

    spec = _spec()
    spec["estimates_document"]["budget"] = "2021-22"
    assert any("budget after 2019-20" in e for e in ub.validate_context({"union_budget": spec}, "t"))
    spec = _spec()
    spec["lines"][0]["estimates"] = []
    assert any("1 to 12 printed rows" in e for e in ub.validate_context({"union_budget": spec}, "t"))
    spec = _spec()
    spec["lines"][1]["key"] = "1"
    assert any("unique in the task" in e for e in ub.validate_context({"union_budget": spec}, "t"))
    spec = _spec()
    spec["lines"][0]["link"] = "figure"
    assert any(".link must be one of" in e for e in ub.validate_context({"union_budget": spec}, "t"))
    spec = _spec()
    del spec["actuals_document"]
    assert any("needs" in e and "actuals_document" in e for e in ub.validate_context({"union_budget": spec}, "t"))


def _pdf(lines: list[str]) -> bytes:
    """A one-page PDF that prints ``lines`` (enough for the pinned reader to extract them)."""

    def esc(text: str) -> str:
        return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    content = "BT /F1 7 Tf 20 820 Td 9 TL " + " ".join(f"({esc(line)}) Tj T*" for line in lines) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 842 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def test_loader_keeps_both_statements_page_by_page_and_the_check_reads_them(tmp_path: Path) -> None:
    estimates, actuals = tmp_path / "sbe42.pdf", tmp_path / "sbe44.pdf"
    estimates.write_bytes(_pdf(ESTIMATES))
    actuals.write_bytes(_pdf(ACTUALS))
    ds = load_dataset(
        str(estimates),
        allow_local_files=True,
        use_cache=False,
        context={"secondary_path": str(actuals), "secondary_document": "pages"},
    )
    assert [(r["document"], r["page"]) for r in ds.records] == [("primary", 1), ("secondary", 1)]
    assert ds.secondary_sha256 is not None
    result = run_engine("UB1", ds.records, _context(_spec()))
    assert sorted(lead.anomaly_metrics["line_key"] for lead in result.leads) == ["2", "3", "4"]


def test_repository_project_is_ten_thousand_pinned_ub1_tasks() -> None:
    data = json.loads((REPO_PROJECTS / "union-budget-unspent-funds.json").read_text(encoding="utf-8"))
    assert validate_project(data, filename_stem="union-budget-unspent-funds") == []
    assert (data["project_code"], data["methodology_code"], data["pillar"]) == ("P3", "UB1", "budget")
    tasks = data["tasks"]
    assert len(tasks) == 10_000 and len({t["id"] for t in tasks}) == 10_000

    with (REPO_PROJECTS / "union-budget-unspent-funds" / "documents.csv").open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    documents = {row["url"]: row["sha256"] for row in rows}
    healthy_urls = {row["url"] for row in rows if row["status"] == "adds up to the Grand Total"}
    lines = 0
    years: dict[str, int] = {}
    for task in tasks:
        payload = task["payload"]
        spec = payload["context"]["union_budget"]
        assert payload["retrieval_url"].startswith("https://www.indiabudget.gov.in/")
        assert payload["retrieval_url"] in healthy_urls
        assert documents[payload["retrieval_url"]] == payload["expected_sha256"]
        if "secondary_url" in payload:
            assert payload["secondary_url"] in healthy_urls
            assert documents[payload["secondary_url"]] == payload["secondary_expected_sha256"]
            assert payload["context"]["secondary_document"] == "pages"
        assert task["id"].startswith(f"ub-{spec['financial_year']}-")  # the project page groups by this
        lines += len(spec["lines"])
        years[spec["financial_year"]] = years.get(spec["financial_year"], 0) + 1

    coverage = json.loads((REPO_PROJECTS / "union-budget-unspent-funds" / "coverage.json").read_text(encoding="utf-8"))
    assert coverage["tasks"] == 10_000 and coverage["lines"] == lines
    assert {year: row["tasks"] for year, row in coverage["by_year"].items()} == years
    assert sorted(years) == [ub.financial_year(y) for y in range(2014, 2026)]


def test_link_year_rejects_unclosed_estimates_and_actuals_statements() -> None:
    import collections
    import importlib.util

    script_path = Path(__file__).resolve().parents[1] / "scripts" / "build_union_budget_project.py"
    spec_mod = importlib.util.spec_from_file_location("build_union_budget_project", script_path)
    assert spec_mod and spec_mod.loader
    mod = importlib.util.module_from_spec(spec_mod)
    import sys

    sys.modules["build_union_budget_project"] = mod
    spec_mod.loader.exec_module(mod)

    est_doc = ub.parse_statement({1: "\n".join(ESTIMATES)})
    act_doc = ub.parse_statement({1: "\n".join(ACTUALS)})
    est_lines = ub.numbered_lines(est_doc)
    act_lines = ub.numbered_lines(act_doc)

    def _make_stmt(budget: str, num: int, url: str, doc: ub.SbeDocument, lines: list[ub.LineReading], closed: bool):
        return mod.Statement(
            budget=budget,
            number=num,
            url=url,
            path=Path(f"sbe{num}.pdf"),
            sha256="a" * 64,
            doc=doc,
            lines=lines,
            closed=closed,
        )

    # 1. Unclosed estimates statement must be rejected completely even when name+figure matches.
    left_out_est: collections.Counter[str] = collections.Counter()
    items_est_unclosed = mod.link_year(
        "2019-20",
        {
            "2020-21": {42: _make_stmt("2020-21", 42, EST_URL, est_doc, est_lines, closed=False)},
            "2021-22": {44: _make_stmt("2021-22", 44, ACT_URL, act_doc, act_lines, closed=True)},
        },
        left_out_est,
    )
    assert items_est_unclosed == []
    assert left_out_est["statement does not add up to its Grand Total"] > 0

    # 2. Unclosed actuals statement must NOT be linked even when name+figure matches.
    left_out_act: collections.Counter[str] = collections.Counter()
    items_act_unclosed = mod.link_year(
        "2019-20",
        {
            "2020-21": {42: _make_stmt("2020-21", 42, EST_URL, est_doc, est_lines, closed=True)},
            "2021-22": {44: _make_stmt("2021-22", 44, ACT_URL, act_doc, act_lines, closed=False)},
        },
        left_out_act,
    )
    assert len(items_act_unclosed) > 0
    assert all(it.actuals is None and it.act is None for it in items_act_unclosed)


def test_shared_ub1_task_submits_and_verifies_every_qualifying_shortfall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from aiwatchdogs.cli.config import AgentConfig
    from aiwatchdogs.cli.runner import AgentRunner
    from aiwatchdogs.core.safety import compute_sha256
    from aiwatchdogs.hub.app import create_app

    monkeypatch.setenv("AIWATCHDOGS_PROJECTS_DIR", str(REPO_PROJECTS))
    monkeypatch.delenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", raising=False)

    est_pdf = tmp_path / "sbe42.pdf"
    act_pdf = tmp_path / "sbe44.pdf"
    est_pdf.write_bytes(_pdf(ESTIMATES))
    act_pdf.write_bytes(_pdf(ACTUALS))
    est_sha = compute_sha256(est_pdf.read_bytes())
    act_sha = compute_sha256(act_pdf.read_bytes())

    app = create_app(db_path=tmp_path / "ub1_shared.db", seed=True)
    with app.state.db.transaction() as conn:
        # Clear non-UB1 open tasks and point one UB1 task to our local multi-lead fixture.
        conn.execute("DELETE FROM tasks WHERE mission_slug != 'union-budget-unspent-funds'")
        row = conn.execute(
            "SELECT id, payload_json FROM tasks WHERE mission_slug = 'union-budget-unspent-funds' LIMIT 1"
        ).fetchone()
        assert row is not None
        task_id = str(row["id"])
        conn.execute("DELETE FROM tasks WHERE id != ?", (task_id,))
        payload = {
            "retrieval_url": str(est_pdf),
            "expected_sha256": est_sha,
            "secondary_url": str(act_pdf),
            "secondary_expected_sha256": act_sha,
            "official_publisher": "Ministry of Finance, Government of India",
            "source_landing_page": "https://www.indiabudget.gov.in/",
            "artifact_title": "Notes on Demands for Grants 2020-21, Demand No. 42",
            "fiscal_year": "FY2019-20",
            "context": {"secondary_document": "pages", "union_budget": _spec()},
        }
        conn.execute("UPDATE tasks SET payload_json = ? WHERE id = ?", (json.dumps(payload), task_id))
        conn.execute("UPDATE missions SET total_tasks = 1 WHERE slug = 'union-budget-unspent-funds'")

    with TestClient(app, base_url="http://testserver") as client:
        auditor = AgentRunner(
            AgentConfig(
                handle="ub1-shared-auditor",
                hub_url="http://testserver",
                provider="rule-engine",
                model="deterministic-cpu-v1",
                mode="investigate",
                max_tasks=1,
            ),
            http_client=client,
            config_path=tmp_path / "aud.json",
            allow_local_files=True,
        )
        res = auditor.run_loop(max_tasks=1, preferred_mission="union-budget-unspent-funds")
        assert res.tasks_completed == 1
        assert res.findings_submitted == 3
        assert res.errors == 0
        resp = res.results[0]["response"]
        assert resp["findings_created"] == 3
        assert len(resp["finding_ids"]) == 3
        assert resp["peer_verify_tasks_created"] == 9

        # Three independent verifiers confirm all 3 findings (9 peer_verify tasks total).
        for idx, handle in enumerate(("ub1-verifier-1", "ub1-verifier-2", "ub1-verifier-3"), start=1):
            ver = AgentRunner(
                AgentConfig(
                    handle=handle,
                    hub_url="http://testserver",
                    provider="rule-engine",
                    model="deterministic-cpu-v1",
                    mode="verify",
                    max_tasks=3,
                ),
                http_client=client,
                config_path=tmp_path / f"ver_{idx}.json",
                allow_local_files=True,
            )
            vres = ver.run_loop(max_tasks=3, preferred_mission="union-budget-unspent-funds")
            assert vres.tasks_completed == 3
            assert vres.verifications_submitted == 3
            assert all(r["verification"]["verdict"] == "confirm" for r in vres.results)

        for fid in resp["finding_ids"]:
            f_detail = client.get(f"/api/findings/{fid}").json()
            assert f_detail["status"] == "verified"
            assert f_detail["confirm_count"] == 3

        # Also verify actuals_reason distinction on _union_budget_summary (2015-16 unmatched vs 2025-26 not_published)
        with app.state.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO findings (
                    id, mission_id, mission_slug, pillar, title, summary, methodology_code,
                    entity_name, jurisdiction, fiscal_year, monetary_exposure_inr, severity,
                    anomaly_metrics_json, rule_triggered, submitted_by, status, created_at
                ) VALUES
                    ('fnd-ub-1516', (SELECT id FROM missions WHERE slug='union-budget-unspent-funds'),
                     'union-budget-unspent-funds', 'budget', '2015-16 shortfall', 'Summary', 'UB1',
                     'Dept A', 'Union of India', 'FY2015-16', 5000000000.0, 'high',
                     '{"compare":"revised_vs_budget_estimate","budget_estimate":100.0,"revised_estimate":50.0,"revised_cut_pct":50.0}',
                     'UB1-RE-BELOW-BE>10%', 'ub1-shared-auditor', 'peer_review', '2026-11-26T00:00:00Z'),
                    ('fnd-ub-2526', (SELECT id FROM missions WHERE slug='union-budget-unspent-funds'),
                     'union-budget-unspent-funds', 'budget', '2025-26 shortfall', 'Summary', 'UB1',
                     'Dept B', 'Union of India', 'FY2025-26', 4000000000.0, 'high',
                     '{"compare":"revised_vs_budget_estimate","budget_estimate":100.0,"revised_estimate":60.0,"revised_cut_pct":40.0}',
                     'UB1-RE-BELOW-BE>10%', 'ub1-shared-auditor', 'peer_review', '2026-11-26T00:00:00Z')
                """
            )
        detail = client.get("/api/missions/union-budget-unspent-funds").json()
        ub_vis = detail["visual_summary"]["union_budget"]
        years_by_y = {y["year"]: y for y in ub_vis["years"]}
        assert years_by_y["2015-16"]["compares"] == "revised"
        assert years_by_y["2015-16"]["actuals_reason"] == "unmatched"
        assert years_by_y["2025-26"]["compares"] == "revised"
        assert years_by_y["2025-26"]["actuals_reason"] == "not_published"
        assert years_by_y["2019-20"]["compares"] == "actual"
        assert years_by_y["2019-20"]["actuals_reason"] is None
        leads_by_id = {ld["finding_id"]: ld for ld in ub_vis["top_leads"]}
        assert leads_by_id["fnd-ub-1516"]["actuals_reason"] == "unmatched"
        assert leads_by_id["fnd-ub-2526"]["actuals_reason"] == "not_published"
    app.state.db.close()


def test_sync_projects_ub1_split_releases_stale_lease_and_skips_completed_lines(tmp_path: Path) -> None:
    """When a shared UB1 task splits, claimed leases are reset with the new payload and completed lines are not duplicated."""
    from aiwatchdogs.hub.database import Database
    from aiwatchdogs.projects import project_task_id, sync_projects

    full_proj = json.loads((REPO_PROJECTS / "union-budget-unspent-funds.json").read_text(encoding="utf-8"))
    shared_task = next(
        t for t in full_proj["tasks"] if len(t["payload"]["context"]["union_budget"]["lines"]) >= 2
    )
    lines = list(shared_task["payload"]["context"]["union_budget"]["lines"])
    half = len(lines) // 2
    first_half = lines[:half]
    second_half = lines[half:]

    pdir = tmp_path / "projects"
    pdir.mkdir()
    proj_v1 = {k: v for k, v in full_proj.items() if k != "tasks"}
    proj_v1["tasks"] = [shared_task]
    (pdir / "union-budget-unspent-funds.json").write_text(json.dumps(proj_v1), encoding="utf-8")

    # Scenario A: task is claimed when manifest splits -> stale lease is released and payload updated.
    db_claimed = Database(db_path=tmp_path / "claimed_split.db")
    try:
        assert sync_projects(db_claimed, pdir)["changed"] is True
        tid1 = project_task_id(shared_task["id"])
        with db_claimed.transaction() as conn:
            conn.execute(
                """
                INSERT INTO agents (
                    id, handle, display_name, provider, model, api_token_hash, status,
                    current_task_id, joined_at, last_heartbeat_at
                ) VALUES (
                    'agt-1', 'ub-claimer', 'UB Claimer', 'rule-engine', 'deterministic-cpu-v1',
                    'tok-hash-1', 'active', ?, '2026-11-26T00:00:00Z', '2026-11-26T00:00:00Z'
                )
                """,
                (tid1,),
            )
            conn.execute(
                """
                UPDATE tasks SET status = 'claimed', claimed_by_agent_id = 'agt-1',
                    claimed_by_handle = 'ub-claimer', lease_id = 'lease-old'
                WHERE id = ?
                """,
                (tid1,),
            )

        task_a = json.loads(json.dumps(shared_task))
        task_a["payload"]["context"]["union_budget"]["lines"] = first_half
        task_b = json.loads(json.dumps(shared_task))
        task_b["id"] = f"{shared_task['id']}-split-b"
        task_b["dataset_key"] = task_b["id"]
        task_b["payload"]["context"]["union_budget"]["lines"] = second_half

        proj_v2 = {k: v for k, v in full_proj.items() if k != "tasks"}
        proj_v2["tasks"] = [task_a, task_b]
        (pdir / "union-budget-unspent-funds.json").write_text(json.dumps(proj_v2), encoding="utf-8")

        assert sync_projects(db_claimed, pdir)["changed"] is True
        with db_claimed.read() as conn:
            row_a = conn.execute("SELECT status, lease_id, payload_json FROM tasks WHERE id = ?", (tid1,)).fetchone()
            row_b = conn.execute(
                "SELECT id FROM tasks WHERE id = ?", (project_task_id(task_b["id"]),)
            ).fetchone()
            agt = conn.execute("SELECT current_task_id FROM agents WHERE id = 'agt-1'").fetchone()
        assert row_a["status"] == "queued"
        assert row_a["lease_id"] is None
        assert agt["current_task_id"] is None
        updated_lines = json.loads(row_a["payload_json"])["context"]["union_budget"]["lines"]
        assert [l["key"] for l in updated_lines] == [l["key"] for l in first_half]
        assert row_b is not None
    finally:
        db_claimed.close()

    # Scenario B: original multi-line task is completed -> split sibling covering completed lines is skipped.
    (pdir / "union-budget-unspent-funds.json").write_text(json.dumps(proj_v1), encoding="utf-8")
    db_done = Database(db_path=tmp_path / "done_split.db")
    try:
        assert sync_projects(db_done, pdir)["changed"] is True
        tid1 = project_task_id(shared_task["id"])
        with db_done.transaction() as conn:
            conn.execute(
                "UPDATE tasks SET status = 'completed', completed_at = '2026-11-26T00:05:00Z' WHERE id = ?",
                (tid1,),
            )

        (pdir / "union-budget-unspent-funds.json").write_text(json.dumps(proj_v2), encoding="utf-8")
        assert sync_projects(db_done, pdir)["changed"] is True
        with db_done.read() as conn:
            row_a = conn.execute("SELECT status FROM tasks WHERE id = ?", (tid1,)).fetchone()
            row_b = conn.execute(
                "SELECT id FROM tasks WHERE id = ?", (project_task_id(task_b["id"]),)
            ).fetchone()
        assert row_a["status"] == "completed"
        assert row_b is None
    finally:
        db_done.close()
