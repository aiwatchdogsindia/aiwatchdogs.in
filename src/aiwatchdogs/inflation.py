"""Validate independent price research and build an offline, reproducible CPI pilot."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date
import hashlib
import json
from math import exp, isclose, isfinite, log
from pathlib import Path
import re
from typing import Annotated, Any, Literal
from urllib.parse import parse_qsl, unquote, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from aiwatchdogs.core.safety import contains_pii, scan_forbidden_terms, validate_safe_http_url

PILOT_PERIODS = tuple([f"2025-{month:02d}" for month in range(10, 13)] + [f"2026-{month:02d}" for month in range(1, 11)])
WEIGHTS_SHA256 = "49175174072f69c44147396b91602d4278c4c3c12d509bbde12e89fadaf38d22"
PROJECT_SLUG = "independent-india-inflation"
DATASET_URL = f"https://aiwatchdogs.in/api/missions/{PROJECT_SLUG}/dataset"
SPECIALIST_ITEMS = frozenset({
    # Food and fuel supplied through PDS, housing, utilities, rail, post, telecom and streaming.
    "01.1.1.1.1.11", "01.1.1.1.1.12", "01.1.1.2.1.03", "01.1.5.1.1.06",
    "01.1.7.6.1.09", "01.1.8.1.1.03", "01.1.9.3.1.03", "04.5.3.0.1.02",
    "04.1.1.0.2.01", "04.5.1.0.2.01", "04.5.2.2.1.01", "07.2.2.1.1.01",
    "07.2.2.2.1.01", "07.3.1.1.2.01", "07.4.1.2.2.01", "08.3.4.0.2.01",
    "08.3.4.0.2.02", "08.3.9.2.2.02",
})

Sector = Literal["rural", "urban"]
StateCode = Annotated[str, StringConstraints(pattern=r"^(0[1-9]|1[0-9]|2[0-57-9]|3[0-7])$")]
ItemCode = Annotated[str, StringConstraints(pattern=r"^[0-9]{2}(\.[0-9]){4}\.[0-9]{2}$")]
Period = Annotated[str, StringConstraints(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")]
DateStr = Annotated[str, StringConstraints(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")]
Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,119}$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=300)]
LongText = Annotated[str, StringConstraints(min_length=8, max_length=5000)]


class _Record(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)


class PriceSource(_Record):
    id: Identifier
    url: Annotated[str, StringConstraints(max_length=1024)]
    publisher: ShortText
    retrieved_on: DateStr
    locator: ShortText
    kind: Literal["retailer", "provider", "administrative", "scanner", "field", "receipt", "methodology"]
    sha256: Sha256 | None = None

    @field_validator("url")
    @classmethod
    def public_url(cls, value: str) -> str:
        safe = validate_safe_http_url(value, max_length=1024)
        private_keys = {"token", "auth", "access_token", "signature", "sig", "key", "api_key", "apikey", "password"}
        for param, _ in parse_qsl(urlsplit(safe).query, max_num_fields=40):
            if param.lower() in private_keys or param.lower().startswith(("x-amz-", "x-goog-")):
                raise ValueError("Evidence links must not carry access credentials.")
        if contains_pii(unquote(safe))[0]:
            raise ValueError("Evidence links must be de-identified.")
        return safe

    @field_validator("retrieved_on")
    @classmethod
    def valid_date(cls, value: str) -> str:
        date.fromisoformat(value)
        return value


class PriceObservation(_Record):
    item_code: ItemCode
    state_code: StateCode
    sector: Sector
    observed_on: DateStr
    market_id: Identifier
    outlet_id: Identifier
    specification_id: Identifier
    unit: Identifier
    quantity: Annotated[float, Field(gt=0, le=1e12, allow_inf_nan=False)]
    price_inr: Annotated[float, Field(ge=0, le=1e12, allow_inf_nan=False)]
    price_basis: Literal["transaction", "retail_offer", "tariff", "mrp", "wholesale", "published_average"]
    total_verified: bool
    source_id: Identifier
    evidence: ShortText

    @field_validator("price_inr")
    @classmethod
    def valid_money(cls, value: float) -> float:
        if 0 < value < 0.01:
            raise ValueError("Prices must be in rupees; fractional prices under one paise are invalid.")
        return value or 0.0

    @field_validator("observed_on")
    @classmethod
    def pilot_date(cls, value: str) -> str:
        date.fromisoformat(value)
        if value[:7] not in PILOT_PERIODS:
            raise ValueError("Observation month is outside the October 2025 to October 2026 pilot.")
        return value

    @model_validator(mode="after")
    def weighted_geography(self) -> PriceObservation:
        if self.state_code == "04" and self.sector == "rural":
            raise ValueError("Annex 5.3 has no positive-weight Chandigarh rural cell.")
        return self


class CoverageGap(_Record):
    reason: ShortText
    detail: LongText
    item_code: ItemCode | None = None
    period: Period | None = None

    @field_validator("period")
    @classmethod
    def pilot_period(cls, value: str | None) -> str | None:
        if value is not None and value not in PILOT_PERIODS:
            raise ValueError("Gap month is outside the pilot. Describe other periods in detail.")
        return value


class InflationReport(_Record):
    summary: LongText
    sources: Annotated[list[PriceSource], Field(max_length=200)]
    observations: Annotated[list[PriceObservation], Field(max_length=200)]
    gaps: Annotated[list[CoverageGap], Field(max_length=200)]
    deliverable: LongText

    @model_validator(mode="after")
    def source_references(self) -> InflationReport:
        sources = {source.id: source for source in self.sources}
        if len(sources) != len(self.sources):
            raise ValueError("Source IDs must be unique within a report.")
        if not self.sources and not self.gaps:
            raise ValueError("Submit at least one source or documented coverage gap.")
        for quote in self.observations:
            source = sources.get(quote.source_id)
            if source is None or source.kind == "methodology":
                raise ValueError("Each observation must reference a source reporting an actual price.")
            if quote.observed_on > source.retrieved_on:
                raise ValueError("An observation date cannot be after its source retrieval date.")
        return self


class _Scope(_Record):
    role: Literal["collection", "specialist", "method", "review"]
    state_code: StateCode | None = None
    sector: Sector | None = None
    item_codes: list[ItemCode] | None = None


def report_schema() -> dict[str, Any]:
    return InflationReport.model_json_schema()


def _validated_report(report: Any) -> dict[str, Any]:
    try:
        parsed = InflationReport.model_validate(report)
    except ValueError as exc:
        # Input can contain credentials or personal data. Return field names and error kinds only.
        errors = getattr(exc, "errors", None)
        if errors is None:
            raise ValueError("Invalid inflation report.") from None
        issues = errors(include_url=False, include_context=False, include_input=False)
        fields = [
            ".".join(map(str, issue["loc"][:-1] if issue["type"] == "extra_forbidden" else issue["loc"]))
            or "report" for issue in issues[:8]
        ]
        raise ValueError(f"Invalid inflation report fields ({', '.join(fields)}).") from None
    data = parsed.model_dump(mode="json", exclude_none=True)
    has_pii, _ = contains_pii(data)
    if has_pii:
        raise ValueError("Inflation reports must contain de-identified public evidence.")
    text = [parsed.summary, parsed.deliverable]
    text.extend(gap.reason + " " + gap.detail for gap in parsed.gaps)
    text.extend(quote.evidence for quote in parsed.observations)
    if any(scan_forbidden_terms(value) for value in text):
        raise ValueError("Use neutral, factual language in the research report.")
    return data


def validate_report(report: dict[str, Any], task_payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a public submission against the role and boundaries of its assigned task."""
    context = task_payload.get("context") or {}
    try:
        # A seeded project task must supply this scope. It cannot be passed by the submitting agent.
        scope = _Scope.model_validate(context.get("inflation_scope"))
    except (AttributeError, ValueError):
        raise ValueError("Inflation task has no valid research scope.") from None
    if scope.role == "collection" and (scope.state_code is None or scope.sector is None):
        raise ValueError("A price collection task requires a state and rural or urban sector.")
    data = _validated_report(report)
    if data["observations"] and scope.role not in {"collection", "specialist"}:
        raise ValueError("Only price collection and specialist tasks can submit observations.")
    item_codes = set(scope.item_codes) if scope.item_codes is not None else None
    if any(
        (scope.state_code is not None and quote["state_code"] != scope.state_code)
        or (scope.sector is not None and quote["sector"] != scope.sector)
        for quote in data["observations"]
    ):
        raise ValueError("An observation falls outside the assigned state or sector.")
    if item_codes is not None and any(
        row.get("item_code") is not None and row["item_code"] not in item_codes
        for row in data["observations"] + data["gaps"]
    ):
        raise ValueError("A price observation or gap falls outside the assigned item codes.")
    return data


# The weights are a complete public input from the Data Libertarian extraction of MoSPI Annex 5.3.
Cell = tuple[str, str, str]


def _load_weights(path: Path) -> dict[Cell, float]:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != WEIGHTS_SHA256:
        raise ValueError("Weight ledger checksum does not match the pinned CPI 2024 extract.")
    records = json.loads(raw)["records"]
    weights = {
        (row["stateCode"], row["sector"], row["itemCode"]): float(row["combinedWeight"])
        for row in records
        if row["combinedWeight"] > 0
    }
    if len(weights) != 23208 or not isclose(sum(weights.values()), 100):
        raise ValueError("The ledger must contain all positive-weight state, sector and item cells.")
    return weights


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _cell(row: dict[str, Any]) -> Cell:
    return row["state_code"], row["sector"], row["item_code"]


def _series_id(row: dict[str, Any]) -> str:
    return _digest([*_cell(row), *(row[key] for key in (
        "market_id", "outlet_id", "specification_id", "unit", "quantity", "price_basis"
    ))])


def _coverage(panel: list[dict[str, Any]], weights: dict[Cell, float]) -> tuple[dict[str, Any], dict[str, Any]]:
    reported_cells = {_cell(row) for row in panel}
    comparable: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in panel:
        if row["comparison_candidate"]:
            comparable[row["series_id"]].append(row)
    complete = {sid: rows for sid, rows in comparable.items() if len(rows) == len(PILOT_PERIODS)}
    complete_cells = {_cell(rows[0]) for rows in complete.values()}
    by_sector = {}
    for sector in ("rural", "urban"):
        sector_cells = {cell for cell in weights if cell[1] == sector}
        expected_weight = sum(weights[cell] for cell in sector_cells)
        by_sector[sector] = {
            "expected_cells": len(sector_cells),
            "reported_cells": len(reported_cells & sector_cells),
            "complete_candidate_cells": len(complete_cells & sector_cells),
            "complete_candidate_weight_pct": (
                100 * sum(weights[cell] for cell in complete_cells & sector_cells) / expected_weight
                if expected_weight else None
            ),
        }
    return {
        "expected_cells": len(weights),
        "reported_cells": len(reported_cells),
        "complete_candidate_cells": len(complete_cells),
        "complete_candidate_weight_pct": 100 * sum(weights[cell] for cell in complete_cells) / sum(weights.values()),
        "by_sector": by_sector,
    }, complete


class ReviewedSpecialistCell(_Record):
    state_code: StateCode
    sector: Sector
    item_code: ItemCode
    monthly_levels: Annotated[
        list[Annotated[float, Field(ge=1e-12, le=1e12, allow_inf_nan=False)]],
        Field(min_length=len(PILOT_PERIODS), max_length=len(PILOT_PERIODS)),
    ]
    evidence_series: Annotated[list[Sha256], Field(min_length=1)]
    calculation: LongText


class PilotApproval(_Record):
    reviewer: ShortText
    sample_method: LongText
    source_review: LongText
    specialist_review: LongText
    panel_sha256: Sha256
    approved_series: Annotated[list[Sha256], Field(min_length=1)]
    specialist_cells: list[ReviewedSpecialistCell] = Field(default_factory=list)


def _pilot_rate(
    complete: dict[str, Any], panel: list[dict[str, Any]], weights: dict[Cell, float], panel_sha256: str,
    approval: dict[str, Any] | None,
) -> dict[str, Any]:
    if approval is None:
        return {"status": "withheld", "reason": "A reviewed sampling frame bound to this panel is required."}
    decision = PilotApproval.model_validate(approval)
    approved = set(decision.approved_series)
    if decision.panel_sha256 != panel_sha256 or len(approved) != len(decision.approved_series):
        raise ValueError("Sample approval does not match the panel or repeats a series ID.")
    if not approved <= complete.keys():
        raise ValueError("Sample approval includes series without complete comparable monthly evidence.")
    grouped: dict[Cell, list[list[float]]] = defaultdict(list)
    for sid in sorted(approved):
        rows = complete[sid]
        cell = _cell(rows[0])
        if cell[2] in SPECIALIST_ITEMS:
            raise ValueError("Specialist items require reviewed cell indices, not ordinary series approval.")
        grouped[cell].append([row["price_inr"] for row in rows])
    specialist_cells = {cell for cell in weights if cell[2] in SPECIALIST_ITEMS}
    specialist_levels = {}
    specialist_evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in panel:
        if row["total_verified"] and row["price_basis"] in {"transaction", "retail_offer", "tariff"}:
            specialist_evidence[row["series_id"]].append(row)
    for cell in decision.specialist_cells:
        key = cell.state_code, cell.sector, cell.item_code
        if key not in specialist_cells or key in specialist_levels or not isclose(cell.monthly_levels[0], 100):
            raise ValueError("Each reviewed specialist cell must be unique, in scope, and October-normalized to 100.")
        observed = set()
        for sid in cell.evidence_series:
            rows = specialist_evidence.get(sid)
            if not rows or _cell(rows[0]) != key:
                raise ValueError("Specialist cell review references an ineligible or unrelated source series.")
            observed.update(row["period"] for row in rows)
        if observed != set(PILOT_PERIODS):
            raise ValueError("Specialist cell reviews require documented independent evidence for each pilot month.")
        specialist_levels[key] = cell.monthly_levels
    if weights.keys() - specialist_cells != grouped.keys() or specialist_cells != specialist_levels.keys():
        return {"status": "withheld", "reason": "Approved complete ordinary series and specialist indices do not cover every positive-weight cell."}

    # For a fixed panel, multiplying the consecutive Jevons links telescopes to the
    # geometric mean of the ratios to the first period. All months remain mandatory.
    weight_total = sum(weights.values())
    aggregate = [0.0] * len(PILOT_PERIODS)
    for cell, levels in specialist_levels.items():
        for index, level in enumerate(levels):
            aggregate[index] += weights[cell] * level / weight_total
    for cell, series in grouped.items():
        bases = [log(prices[0]) for prices in series]
        for index, prices in enumerate(zip(*series, strict=True)):
            relative = exp(sum(log(price) - base for price, base in zip(prices, bases, strict=True)) / len(series))
            aggregate[index] += weights[cell] * 100 * relative / weight_total
    rate = 100 * (aggregate[-1] / aggregate[0] - 1)
    if not isfinite(rate):
        raise ValueError("Pilot result must be finite.")
    return {
        "status": "experimental", "base_month": PILOT_PERIODS[0], "comparison_month": PILOT_PERIODS[-1],
        "annual_change_pct": rate, "reviewer": decision.reviewer, "approved_series": len(approved),
        "reviewed_specialist_cells": len(specialist_levels),
        "monthly_levels": dict(zip(PILOT_PERIODS, aggregate, strict=True)),
        "note": "Full-weight independent October-baseline pilot. The MoSPI 2024-base index is a separate series.",
    }


def build_pilot(
    reports: list[dict[str, Any]], weights: dict[Cell, float], approval: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Combine accepted submission pages into documented comparable price series."""
    prices: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    quarantined = []
    batches: dict[tuple[str, int], dict[str, dict[str, Any]]] = defaultdict(dict)
    for entry in reports:
        try:
            task_id = entry["task_id"]
            if not isinstance(task_id, str) or not re.fullmatch(r"prj-[a-z0-9-]{1,60}", task_id):
                raise ValueError("Invalid task identifier.")
            batch = entry.get("batch", 1)
            if type(batch) is not int or not 1 <= batch <= 64:
                raise ValueError("Invalid batch number.")
            report = _validated_report(entry["report"])
        except (KeyError, TypeError, ValueError):
            quarantined.append({"reason": "invalid_report"})
            continue
        batches[task_id, batch][_digest(report)] = report
    for (task_id, batch), versions in sorted(batches.items()):
        if len(versions) != 1:
            quarantined.append({"task_id": task_id, "batch": batch, "reason": "conflicting_batch_versions"})
            continue
        report = next(iter(versions.values()))
        sources = {source["id"]: source for source in report["sources"]}
        for quote in report["observations"]:
            if _cell(quote) not in weights:
                quarantined.append({"task_id": task_id, "reason": "outside_positive_weight_cells"})
                continue
            month = quote["observed_on"][:7]
            series_id = _series_id(quote)
            source = sources[quote["source_id"]]
            attrs = {key: value for key, value in quote.items() if key not in {"source_id", "evidence", "observed_on"}}
            evidence = {
                "task_id": task_id, "batch": batch, "source": source,
                "observed_on": quote["observed_on"], "locator": quote["evidence"],
            }
            prices[series_id, month].append({**attrs, "evidence": evidence})

    panel = []
    for (series_id, month), rows in sorted(prices.items()):
        if len({row["price_inr"] for row in rows}) > 1:
            quarantined.append({"series_id": series_id, "period": month, "reason": "conflicting_prices"})
            continue
        verified = all(row["total_verified"] for row in rows)
        common = {key: value for key, value in rows[0].items() if key not in {"evidence", "total_verified"}}
        evidence = {_digest(row["evidence"]): row["evidence"] for row in rows}
        panel.append({
            **common,
            "period": month,
            "series_id": series_id,
            "total_verified": verified,
            "comparison_candidate": verified and common["price_inr"] > 0 and common["price_basis"] in {
                "transaction", "retail_offer", "tariff"
            },
            "evidence": [evidence[digest] for digest in sorted(evidence)],
        })
    panel_sha256 = _digest(panel)
    coverage, complete = _coverage(panel, weights)
    return {
        "slug": PROJECT_SLUG, "periods": PILOT_PERIODS,
        "panel_sha256": panel_sha256, "panel": panel, "quarantine": quarantined,
        "coverage": coverage, "national_pilot": _pilot_rate(complete, panel, weights, panel_sha256, approval),
        "national_cpi_2024_base": None,
    }


def _download_dataset() -> dict[str, Any]:
    with httpx.Client(timeout=30.0, follow_redirects=False) as client:
        for _ in range(3):
            reports = []
            cursors: set[str] = set()
            batches: set[tuple[str, int]] = set()
            task_ids: set[str] = set()
            completed = None
            params: dict[str, str | int] = {"limit": 20}
            while True:
                response = client.get(DATASET_URL, params=params)
                response.raise_for_status()
                page = response.json()
                if not isinstance(page, dict) or page.get("slug") != PROJECT_SLUG or not isinstance(page.get("reports"), list):
                    raise ValueError("Unexpected inflation dataset response.")
                count = page.get("completed_tasks")
                if type(count) is not int or count < 0:
                    raise ValueError("Invalid completed task count in the inflation dataset.")
                if completed is not None and count != completed:
                    break  # Newly completed tasks can sort before the current cursor.
                completed = count
                for row in page["reports"]:
                    if not isinstance(row, dict):
                        raise ValueError("Invalid report batch in the inflation dataset.")
                    task_id, batch = row.get("task_id"), row.get("batch")
                    if not isinstance(task_id, str) or not task_id or type(batch) is not int or not 1 <= batch <= 64:
                        raise ValueError("Invalid report batch in the inflation dataset.")
                    if (task_id, batch) in batches:
                        raise ValueError("Inflation dataset pagination returned a duplicate report batch.")
                    batches.add((task_id, batch))
                    task_ids.add(task_id)
                    reports.append(row)
                cursor = page.get("next_after")
                if cursor is None:
                    if len(task_ids) == completed:
                        return {**page, "reports": reports}
                    break  # The reported count and the returned rows did not agree.
                if not isinstance(cursor, str) or cursor in cursors or len(cursors) >= 6400:
                    raise ValueError("Inflation dataset pagination did not advance.")
                cursors.add(cursor)
                params["after"] = cursor
    raise ValueError("Could not download a consistent inflation snapshot after three attempts. Retry once submissions settle.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("datasets", nargs="*", type=Path, help="Saved JSON pages from the public project dataset")
    parser.add_argument("--download", action="store_true", help="Read all pages from the public AI Watchdogs dataset")
    parser.add_argument("--save-dataset", type=Path, help="Save the downloaded report snapshot for reproduction")
    parser.add_argument("--weights", required=True, type=Path, help="Pinned cpi-state-item-weights.json")
    parser.add_argument("--approval", type=Path, help="Optional independent reviewer sign-off on the exact panel")
    parser.add_argument("--output", type=Path, help="Write compiled JSON to this path instead of stdout")
    args = parser.parse_args()
    if args.download == bool(args.datasets) or (args.save_dataset and not args.download):
        parser.error("Specify saved dataset pages or --download. Use --save-dataset with --download.")
    try:
        reports = []
        pages = [_download_dataset()] if args.download else [json.loads(path.read_bytes()) for path in args.datasets]
        for page in pages:
            if not isinstance(page, dict) or page.get("slug") != PROJECT_SLUG or not isinstance(page.get("reports"), list):
                raise ValueError("Dataset must be an independent inflation project export.")
            reports.extend(page["reports"])
        approval = json.loads(args.approval.read_bytes()) if args.approval else None
        result = build_pilot(reports, _load_weights(args.weights), approval)
        if args.save_dataset:
            args.save_dataset.write_text(json.dumps(pages[0], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, httpx.HTTPError) as exc:
        parser.error(str(exc))
    output = json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)


if __name__ == "__main__":
    main()
