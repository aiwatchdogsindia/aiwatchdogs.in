import copy

import httpx
import pytest

from aiwatchdogs.inflation import (
    DATASET_URL,
    PILOT_PERIODS,
    PROJECT_SLUG,
    _download_dataset,
    build_pilot,
    report_schema,
    validate_report,
)


RICE = "01.1.1.1.1.01"
RENT = "04.1.1.0.2.01"
RURAL = ("01", "rural", RICE)
URBAN = ("01", "urban", RENT)
WEIGHTS = {RURAL: 25, URBAN: 75}


def make_report(cell=RURAL, prices=(100,)):
    state, sector, code = cell
    return {
        "summary": "Historical prices in the assigned panel.",
        "sources": [{
            "id": "public-price-table", "kind": "retailer", "publisher": "Example Market",
            "retrieved_on": "2026-11-25", "url": "https://example.com/prices/archive",
            "locator": "Archived public price table, column for each month.",
        }],
        "observations": [{
            "item_code": code, "state_code": state, "sector": sector,
            "observed_on": f"{period}-15", "market_id": "district-market-a", "outlet_id": "shop-a",
            "specification_id": "grade-a-rice" if code == RICE else "one-bedroom-rent",
            "unit": "kg" if code == RICE else "month", "quantity": 1, "price_inr": price,
            "price_basis": "retail_offer", "total_verified": True,
            "source_id": "public-price-table", "evidence": "Same specification and all-in price.",
        } for period, price in zip(PILOT_PERIODS, prices)],
        "gaps": [],
        "deliverable": "The report contains digitized archival price observations.",
    }


def payload(role="collection", **scope):
    return {"context": {"inflation_scope": {"role": role, **scope}}}


def entry(report, task="prj-cpi-rural", batch=1):
    return {"task_id": task, "batch": batch, "report": report}


def test_report_requires_pilot_scope_sources_neutrality_and_public_nonpersonal_data():
    report = make_report()
    scope = payload(state_code="01", sector="rural", item_codes=[RICE])
    assert validate_report(report, scope)["observations"][0]["price_inr"] == 100
    assert "observations" in report_schema()["required"]
    with pytest.raises(ValueError, match="Only price collection"):
        validate_report(report, payload("method"))
    with pytest.raises(ValueError, match="outside the assigned state"):
        validate_report(report, payload(state_code="01", sector="urban"))
    with pytest.raises(ValueError, match="outside the assigned item"):
        validate_report(report, payload("specialist", item_codes=[RENT]))
    changes = [
        ("observations", "observed_on", "2026-12-15"),
        ("observations", "observed_on", "2026-02-30"),
        ("observations", "source_id", "missing"),
        ("observations", "price_inr", 0.0001),
        ("observations", "price_inr", float("nan")),
        ("observations", "price_inr", True),
        ("sources", "url", "https://127.0.0.1/records"),
        ("sources", "url", "https://example.com/records?access_token=demo"),
        ("sources", "url", "https://example.com/records?member=personal%40example.com"),
        ("sources", "retrieved_on", "2025-10-01"),
    ]
    for section, field, value in changes:
        changed = copy.deepcopy(report)
        changed[section][0][field] = value
        with pytest.raises(ValueError, match="Invalid inflation report fields"):
            validate_report(changed, scope)
    report["deliverable"] = "For the receipt contact personal@example.com"
    with pytest.raises(ValueError, match="de-identified"):
        validate_report(report, scope)


def test_method_task_accepts_documented_coverage_gap_without_inventing_price():
    report = make_report()
    report["sources"] = []
    report["observations"] = []
    report["gaps"] = [{"reason": "historical panel unavailable", "detail": "Field samples require additional archival data."}]
    validated = validate_report(report, payload("method"))
    assert len(validated["gaps"]) == 1
    report["gaps"] = []
    with pytest.raises(ValueError):
        validate_report(report, payload("method"))


def test_pilot_combines_batches_deduplicates_exact_rows_and_quarantines_conflicts():
    original = make_report(prices=tuple(range(100, 113)))
    duplicate = copy.deepcopy(original)
    duplicate["observations"] = duplicate["observations"][:1]
    conflict = make_report(prices=(650,))
    off_scope = make_report(("02", "urban", RICE))
    dataset = [entry(original), entry(duplicate, batch=2), entry(off_scope, "prj-out-of-scope"), {"report": None}]
    panel = build_pilot(dataset, WEIGHTS)
    assert len(panel["panel"]) == 13
    assert len(panel["panel"][0]["evidence"]) == 2
    assert panel["panel"][0]["evidence"][0]["batch"] in (1, 2)
    assert panel["coverage"]["complete_candidate_weight_pct"] == 25
    assert panel["national_pilot"]["status"] == "withheld"
    assert panel["national_cpi_2024_base"] is None
    assert build_pilot(dataset[::-1], WEIGHTS)["panel_sha256"] == panel["panel_sha256"]
    broken = build_pilot(dataset + [entry(conflict, "prj-conflict")], WEIGHTS)
    assert len(broken["panel"]) == 12
    assert broken["coverage"]["complete_candidate_weight_pct"] == 0
    assert {row["reason"] for row in broken["quarantine"]} == {
        "invalid_report", "outside_positive_weight_cells", "conflicting_prices",
    }
    contradict = build_pilot([entry(original), entry(conflict)], WEIGHTS)
    assert contradict["panel"] == []
    assert contradict["quarantine"][0]["reason"] == "conflicting_batch_versions"


def test_national_pilot_requires_reviewed_series_and_specialist_levels():
    rural_report = make_report(prices=(100, 140) + (100,) * 10 + (110,))
    urban_report = make_report(URBAN, (200,) * 12 + (220,))
    reports = [entry(rural_report), entry(urban_report, "prj-cpi-rent")]
    initial = build_pilot(reports, WEIGHTS)
    series = {row["item_code"]: row["series_id"] for row in initial["panel"]}
    approval = {
        "reviewer": "Independent Methods Group", "sample_method": "Prespecified example sampling frame.",
        "source_review": "Evidence checked against source tables.",
        "specialist_review": "Rent reviewed with appropriate starting weights.",
        "panel_sha256": initial["panel_sha256"], "approved_series": [series[RICE]],
    }
    assert build_pilot(reports, WEIGHTS, approval)["national_pilot"]["status"] == "withheld"
    approval["specialist_cells"] = [{
        "state_code": "01", "sector": "urban", "item_code": RENT,
        "monthly_levels": [100] * 12 + [120], "evidence_series": [series[RENT]],
        "calculation": "Reviewed weighted dwelling category index and full audit trail.",
    }]
    released = build_pilot(reports, WEIGHTS, approval)
    assert released["national_pilot"]["status"] == "experimental"
    assert released["national_pilot"]["annual_change_pct"] == pytest.approx(17.5)
    months = released["national_pilot"]["monthly_levels"]
    assert tuple(months) == PILOT_PERIODS
    assert months["2025-10"] == 100
    assert months["2025-11"] == pytest.approx(110)
    assert months["2026-10"] == pytest.approx(117.5)
    assert released["national_cpi_2024_base"] is None
    approval["panel_sha256"] = "a" * 64
    with pytest.raises(ValueError, match="does not match the panel"):
        build_pilot(reports, WEIGHTS, approval)


def test_dataset_download_follows_opaque_cursor(monkeypatch):
    retained = httpx.Client
    query = []

    def handler(request):
        assert str(request.url).startswith(DATASET_URL)
        query.append(dict(request.url.params))
        cursor = request.url.params.get("after")
        return httpx.Response(200, json={
            "slug": PROJECT_SLUG,
            "completed_tasks": 1,
            "reports": [{"task_id": "prj-cpi-rural", "batch": 2 if cursor else 1}],
            "next_after": None if cursor else "opaque:cursor",
        })

    monkeypatch.setattr(httpx, "Client", lambda **kw: retained(transport=httpx.MockTransport(handler), **kw))
    assert [row["batch"] for row in _download_dataset()["reports"]] == [1, 2]
    assert query == [{"limit": "20"}, {"limit": "20", "after": "opaque:cursor"}]


def test_dataset_download_restarts_when_completion_sorts_before_cursor(monkeypatch):
    retained = httpx.Client
    pages = iter([
        (None, 2, ["middle"], "old:cursor"),
        ("old:cursor", 3, ["z-last"], None),
        (None, 3, ["a-first"], "new:cursor"),
        ("new:cursor", 3, ["middle", "z-last"], None),
    ])

    def handler(request):
        after, completed, ids, cursor = next(pages)
        assert request.url.params.get("after") == after
        return httpx.Response(200, json={
            "slug": PROJECT_SLUG, "completed_tasks": completed,
            "reports": [{"task_id": f"prj-{task_id}", "batch": 1} for task_id in ids],
            "next_after": cursor,
        })

    monkeypatch.setattr(httpx, "Client", lambda **kw: retained(transport=httpx.MockTransport(handler), **kw))
    snapshot = _download_dataset()
    assert snapshot["completed_tasks"] == 3
    assert [row["task_id"] for row in snapshot["reports"]] == ["prj-a-first", "prj-middle", "prj-z-last"]


def test_dataset_download_rejects_incomplete_snapshot_after_three_attempts(monkeypatch):
    retained = httpx.Client
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={
            "slug": PROJECT_SLUG, "completed_tasks": 2,
            "reports": [{"task_id": "prj-cpi-rural", "batch": 1}],
            "next_after": None,
        })

    monkeypatch.setattr(httpx, "Client", lambda **kw: retained(transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(ValueError, match="consistent inflation snapshot after three attempts"):
        _download_dataset()
    assert len(requests) == 3


def test_dataset_download_rejects_duplicate_report_batches(monkeypatch):
    retained = httpx.Client
    duplicate = {"task_id": "prj-cpi-rural", "batch": 1}

    def handler(request):
        return httpx.Response(200, json={
            "slug": PROJECT_SLUG, "completed_tasks": 1,
            "reports": [duplicate, duplicate], "next_after": None,
        })

    monkeypatch.setattr(httpx, "Client", lambda **kw: retained(transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(ValueError, match="duplicate report batch"):
        _download_dataset()
