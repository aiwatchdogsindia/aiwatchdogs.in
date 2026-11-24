"""Deterministic Indian public procurement audit engines (M1, M2, M3, M4)."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime
from statistics import median
from typing import Any

from aiwatchdogs.core.models import AuditLeadCandidate

# Effective date of MoF DoE O.M. No. F.1/3/2024-PPD revising Central GFR 2017 thresholds
GFR_JULY_2024_EFFECTIVE_DATE = date(2024, 7, 10)

# Central Government GFR 2017 thresholds (original: effective 2017-03-08 to 2024-07-09)
UNION_GFR_2017_PRE_JUL2024: tuple[tuple[float, str, str], ...] = (
    (25_000.0, "INR 25,000", "GFR 2017 Rule 149(i)/154 (GeM Direct / Without Quotation ceiling, pre-Jul 2024)"),
    (250_000.0, "INR 2.5 Lakh", "GFR 2017 Rule 155 (Local Purchase Committee ceiling, pre-Jul 2024)"),
    (500_000.0, "INR 5 Lakh", "GFR 2017 Rule 149(ii) (GeM L1 vs Online Bidding/RA ceiling, pre-Jul 2024)"),
    (2_500_000.0, "INR 25 Lakh", "GFR 2017 Rule 161 (Limited vs Open Advertised Tender ceiling, pre-Jul 2024)"),
)

# Central Government GFR 2017 thresholds (revised by MoF DoE O.M. F.1/3/2024-PPD dated 10 July 2024)
UNION_GFR_2017_POST_JUL2024: tuple[tuple[float, str, str], ...] = (
    (50_000.0, "INR 50,000", "GFR 2017 Rule 149(i)/154 (GeM Direct / Without Quotation ceiling, Jul 2024 PPD revision)"),
    (500_000.0, "INR 5 Lakh", "GFR 2017 Rule 155 (Local Purchase Committee ceiling, Jul 2024 PPD revision)"),
    (1_000_000.0, "INR 10 Lakh", "GFR 2017 Rule 149(ii) (GeM L1 vs Online Bidding/RA ceiling, Jul 2024 PPD revision)"),
    (5_000_000.0, "INR 50 Lakh", "GFR 2017 Rule 161 (Limited vs Open Advertised Tender ceiling, Jul 2024 PPD revision)"),
)

# Assam State reference thresholds (Assam Public Procurement Act 2017 / APPR 2020 & DFP Rules)
ASSAM_APPR_2020_THRESHOLDS: tuple[tuple[float, str, str], ...] = (
    (
        2_500_000.0,
        "INR 25 Lakh",
        "Assam Public Procurement Rules 2020 / Assam e-Procurement Open Tender reference threshold (subject to Assam DFP Rules)",
    ),
)

# Backwards-compatible alias pointing to current Central post-July 2024 schedule
GFR_THRESHOLDS = UNION_GFR_2017_POST_JUL2024

_NEUTRAL_REPLACE_RE = re.compile(
    r"\b(?:scam(?:s|med|mer|mers)?|corrupt(?:ed|ing|ly|ion)?|brib(?:e|es|ed|ing|ery)|"
    r"fraud(?:s|ulent|ulently|ster|sters)?|kickback(?:s)?|guilty|stolen|rigged|"
    r"crook(?:s|ed)?|loot(?:s|ed|ing|er|ers)?|embezzl(?:e|es|ed|ing|ement))\b",
    re.IGNORECASE,
)

_FY_YEAR_RE = re.compile(r"(20\d{2})")


def _neutralize_external_text(text: str) -> str:
    """Neutralize prohibited BNS Sec 356 terms if present inside external public record strings."""
    return _NEUTRAL_REPLACE_RE.sub("compliance-review", text)


def _to_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("₹", "").replace("INR", "").strip()
    try:
        return float(text)
    except ValueError:
        return default


def _to_float_opt(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("₹", "").replace("INR", "").strip()
    if not text or text in {"--", "-", "NA", "N/A", "null", "None"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _to_int_opt(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text or text in {"--", "-", "NA", "N/A", "null", "None"}:
        return None
    try:
        return int(round(float(text.replace(",", ""))))
    except ValueError:
        return None


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value).strip()
    if not raw or raw in {"--", "-", "NA", "N/A"}:
        return None
    if "T" in raw:
        raw_iso = raw.split("T", 1)[0]
        try:
            return datetime.strptime(raw_iso, "%Y-%m-%d").date()
        except ValueError:
            pass

    for fmt in (
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%d-%m %H:%M:%S",
        "%Y-%d-%m %H:%M",
        "%Y-%d-%m",
        "%d-%b-%Y %I:%M %p",
        "%d-%b-%Y %H:%M",
        "%d-%b-%Y",
        "%d/%b/%Y %I:%M %p",
        "%d/%b/%Y",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _norm_str(value: Any, fallback: str = "") -> str:
    text = str(value).strip() if value is not None else ""
    return text if text else fallback


def _norm_key(value: Any, fallback: str = "general") -> str:
    text = _norm_str(value, fallback).lower()
    return " ".join(text.split())


def _fy_sort_key(fy_label: str) -> tuple[int, str]:
    m = _FY_YEAR_RE.search(fy_label)
    return (int(m.group(1)) if m else 9999, fy_label)


def _extract_fiscal_year_span(
    rows: list[dict[str, Any]],
) -> tuple[str, str | None, str | None, list[str]]:
    """Return ``(display_span, fy_start, fy_end, sorted_distinct_fys)`` across a record group."""
    seen: set[str] = set()
    for rec in rows:
        fy = _norm_str(rec.get("fiscal_year"))
        if fy and fy.lower() not in {"unspecified", "unknown", "na", "n/a"}:
            seen.add(fy)
    if not seen:
        return "Unspecified", None, None, []
    ordered = sorted(seen, key=_fy_sort_key)
    if len(ordered) == 1:
        return ordered[0], ordered[0], ordered[0], ordered
    return f"{ordered[0]} to {ordered[-1]}", ordered[0], ordered[-1], ordered


def _extract_typed_value(rec: dict[str, Any]) -> tuple[float, str]:
    """Extract monetary amount alongside its explicit semantic type (award_value vs tender_estimate)."""
    has_award_flag = rec.get("has_award")

    # 1. Explicit award values (ONLY when not flagged has_award=False)
    if has_award_flag is not False:
        for key in ("awarded_value_inr", "award_amount_inr", "awards/0/value/amount"):
            val = _to_float_opt(rec.get(key))
            if val is not None and val > 0:
                return val, "award_value"
        aw_val = _to_float_opt(rec.get("award_value_inr"))
        if aw_val is not None and aw_val > 0:
            return aw_val, "award_value"

    # 2. Tender estimate values (strictly kept distinct from awards)
    for key in ("tender_estimate_inr", "tender_value_inr", "tender/value/amount"):
        val = _to_float_opt(rec.get(key))
        if val is not None and val > 0:
            return val, "tender_estimate"

    # 3. Generic PO / contract value fallbacks (e.g. non-OCDS PO tables)
    for key in ("po_value_inr", "value_inr", "amount_inr", "l1_bid_inr"):
        val = _to_float_opt(rec.get(key))
        if val is not None and val > 0:
            explicit_type = _norm_str(rec.get("amount_type"))
            return val, explicit_type or "award_value"

    return 0.0, "unrecorded"


def _summarize_amount_type(types: list[str]) -> str:
    non_empty = {t for t in types if t and t != "unrecorded"}
    if not non_empty:
        return "unrecorded"
    if len(non_empty) == 1:
        return next(iter(non_empty))
    return "mixed_award_and_estimate"


def _extract_proc_value(rec: dict[str, Any]) -> float:
    """Extract procurement monetary value (award first when genuinely present, else tender estimate)."""
    val, _ = _extract_typed_value(rec)
    return val


def _extract_bid_window_days(rec: dict[str, Any]) -> int | None:
    start = _parse_date(
        rec.get("tender_start_date")
        or rec.get("tender/tenderPeriod/startDate")
        or rec.get("tender_published_date")
        or rec.get("published_date")
        or rec.get("tender/datePublished")
    )
    end = _parse_date(
        rec.get("tender_end_date")
        or rec.get("tender/tenderPeriod/endDate")
        or rec.get("closing_date")
    )
    if start and end:
        return max(0, (end - start).days)
    explicit = rec.get("bid_window_days")
    if explicit is None or str(explicit).strip() == "":
        explicit = rec.get("tender/tenderPeriod/durationInDays")
    return _to_int_opt(explicit)


def _row_locator(rows: list[tuple[int, dict[str, Any]]], limit: int = 10) -> str:
    ids = [
        str(rec.get("source_row_index") or (idx + 1))
        for idx, rec in rows[:limit]
    ]
    return f"rows={','.join(ids)}" if ids else "rows=none"


def _build_citation(rec: dict[str, Any], idx: int, excerpt: str) -> dict[str, Any]:
    record_id = (
        rec.get("tender_id")
        or rec.get("tender/id")
        or rec.get("ocid")
        or rec.get("po_id")
        or rec.get("contract_id")
        or f"row-{idx + 1}"
    )
    return {
        "record_id": str(record_id),
        "row_id": int(rec.get("source_row_index") or (idx + 1)),
        "source_url": _norm_str(rec.get("source_url"), "https://eprocure.gov.in"),
        "excerpt": excerpt,
    }


def _severity_from_exposure(exposure_inr: float, high_signal: bool = False) -> str:
    if exposure_inr >= 100_000_000 or (high_signal and exposure_inr >= 50_000_000):
        return "critical"
    if exposure_inr >= 10_000_000 or high_signal:
        return "high"
    if exposure_inr >= 1_000_000:
        return "medium"
    return "low"


def _cap_leads_deterministically(
    leads: list[AuditLeadCandidate],
    ctx: dict[str, Any],
    default_cap: int = 5,
) -> tuple[list[AuditLeadCandidate], int]:
    """Sort leads deterministically by exposure desc and cap to top N."""
    sorted_leads = sorted(
        leads,
        key=lambda lead: (-float(lead.monetary_exposure_inr), lead.entity_name, lead.title),
    )
    total_detected = len(sorted_leads)
    max_leads = int(ctx.get("max_leads", default_cap))
    if max_leads > 0 and len(sorted_leads) > max_leads:
        return sorted_leads[:max_leads], total_detected
    return sorted_leads, total_detected


def _resolve_threshold_schedule(
    jurisdiction: str,
    event_dates: list[date],
    fiscal_years: list[str],
) -> dict[str, Any]:
    """Resolve governing procurement threshold schedule by jurisdiction and effective date.

    Never blindly applies July 2024 Central GFR 2017 ceilings to earlier or State-level records.
    """
    jur_lower = (jurisdiction or "").strip().lower()
    is_central = jur_lower in {"", "union", "union of india", "central", "india", "government of india"}

    if is_central:
        # Determine pre- vs post-10 July 2024 regime from actual dates or fiscal year labels
        if event_dates:
            min_dt = min(event_dates)
            max_dt = max(event_dates)
            if min_dt >= GFR_JULY_2024_EFFECTIVE_DATE:
                return {
                    "thresholds": UNION_GFR_2017_POST_JUL2024,
                    "governing_instrument": "Central GFR 2017 (as revised by MoF DoE O.M. F.1/3/2024-PPD dated 10 Jul 2024)",
                    "threshold_scope": "central_union_gfr_post_2024_07_10",
                    "threshold_effective_date": "2024-07-10",
                    "statutory_applicability_verified": True,
                }
            if max_dt < GFR_JULY_2024_EFFECTIVE_DATE:
                return {
                    "thresholds": UNION_GFR_2017_PRE_JUL2024,
                    "governing_instrument": "Central GFR 2017 (original ceilings prior to 10 Jul 2024 PPD revision)",
                    "threshold_scope": "central_union_gfr_pre_2024_07_10",
                    "threshold_effective_date": "2017-03-08",
                    "statutory_applicability_verified": True,
                }
            return {
                "thresholds": UNION_GFR_2017_PRE_JUL2024,
                "governing_instrument": "Central GFR 2017 (records span 10 Jul 2024 PPD revision — verify effective date per order)",
                "threshold_scope": "central_union_gfr_mixed_pre_post_2024_07_10",
                "threshold_effective_date": "2017-03-08/2024-07-10",
                "statutory_applicability_verified": False,
            }

        # Fallback to fiscal year labels when event dates are absent
        start_years = [_fy_sort_key(fy)[0] for fy in fiscal_years if _fy_sort_key(fy)[0] < 9999]
        if start_years and max(start_years) <= 2023:
            return {
                "thresholds": UNION_GFR_2017_PRE_JUL2024,
                "governing_instrument": "Central GFR 2017 (original ceilings prior to 10 Jul 2024 PPD revision)",
                "threshold_scope": "central_union_gfr_pre_2024_07_10",
                "threshold_effective_date": "2017-03-08",
                "statutory_applicability_verified": False,
            }
        if start_years and min(start_years) >= 2025:
            return {
                "thresholds": UNION_GFR_2017_POST_JUL2024,
                "governing_instrument": "Central GFR 2017 (as revised by MoF DoE O.M. F.1/3/2024-PPD dated 10 Jul 2024)",
                "threshold_scope": "central_union_gfr_post_2024_07_10",
                "threshold_effective_date": "2024-07-10",
                "statutory_applicability_verified": False,
            }
        return {
            "thresholds": UNION_GFR_2017_POST_JUL2024,
            "governing_instrument": "Central GFR 2017 (date unrecorded — confirm applicable pre/post 10 Jul 2024 regime)",
            "threshold_scope": "central_union_gfr_date_unverified",
            "threshold_effective_date": "unverified",
            "statutory_applicability_verified": False,
        }

    # State:Assam — apply Assam Public Procurement Rules 2020 reference threshold, NOT July 2024 Central GFR!
    if "assam" in jur_lower:
        return {
            "thresholds": ASSAM_APPR_2020_THRESHOLDS,
            "governing_instrument": "Assam Public Procurement Act 2017 / Assam Public Procurement Rules 2020 (subject to Assam DFP Rules)",
            "threshold_scope": "state_assam_appr_2020_reference",
            "threshold_effective_date": "2020-07-01",
            "statutory_applicability_verified": False,
        }

    # Other State jurisdictions: explicitly mark as unverified reference benchmark rather than asserting Central GFR
    return {
        "thresholds": UNION_GFR_2017_PRE_JUL2024,
        "governing_instrument": f"Reference price-band benchmark only ({jurisdiction} State procurement rules & DFP thresholds unverified)",
        "threshold_scope": "reference_benchmark_only_state_rules_unverified",
        "threshold_effective_date": "unverified",
        "statutory_applicability_verified": False,
    }


def run_m1_single_bidder(
    records: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> tuple[list[AuditLeadCandidate], int, float, str, dict[str, Any]]:
    """M1: Single-bidder share >35%, repeat single-bidder vendor >=3, shortened bid windows (<14d).

    Strictly separates literal ``0`` bidder counts from blank/missing (``unknown``) fields,
    publishes full numerator/denominator/zero/unknown counts, full fiscal-year span, and
    amount type, and routes buyer groups with zero positive bidder counts to ``unevaluable``.
    """
    ctx = context or {}
    min_tenders = int(ctx.get("min_tenders", 4))
    single_bid_share_threshold = float(ctx.get("single_bid_share_threshold", 0.35))
    short_window_limit = int(ctx.get("short_window_days", 14))

    total_audited = 0.0
    all_amount_types: list[str] = []
    by_buyer: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)

    for idx, rec in enumerate(records):
        val, vtype = _extract_typed_value(rec)
        total_audited += max(0.0, val)
        all_amount_types.append(vtype)
        buyer = _norm_str(
            rec.get("buyer_name") or rec.get("buyer/name") or rec.get("entity_name") or rec.get("department"),
            "Unspecified Public Buyer",
        )
        by_buyer[buyer].append((idx, rec))

    leads: list[AuditLeadCandidate] = []
    cleared_count = 0
    unevaluable_count = 0
    unevaluable_buyer_groups = 0

    for buyer, indexed_rows in sorted(by_buyer.items()):
        competitive = [
            (idx, r)
            for idx, r in indexed_rows
            if _norm_key(r.get("procurement_method") or r.get("tender/procurementMethod"), "open")
            not in {"proprietary", "direct_exempt"}
        ]
        if not competitive:
            cleared_count += len(indexed_rows)
            continue

        positive_bid_count = 0
        multi_bid_count = 0
        zero_bid_count = 0
        unknown_bid_count = 0
        single_bid_rows: list[tuple[int, dict[str, Any]]] = []
        short_window_single_rows: list[tuple[int, dict[str, Any]]] = []
        vendor_single_rows: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)

        for idx, rec in competitive:
            raw_bc = rec.get("bid_count")
            if raw_bc is None or str(raw_bc).strip() == "":
                raw_bc = rec.get(
                    "num_bids",
                    rec.get("number_of_tenderers", rec.get("tender/numberOfTenderers")),
                )
            bid_count = _to_int_opt(raw_bc)
            if bid_count is None or bid_count < 0:
                unknown_bid_count += 1
                continue
            if bid_count == 0:
                zero_bid_count += 1
                continue

            positive_bid_count += 1
            window_days = _extract_bid_window_days(rec)
            if bid_count == 1:
                single_bid_rows.append((idx, rec))
                vendor = _norm_str(
                    rec.get("vendor_name")
                    or rec.get("supplier_name")
                    or rec.get("awardee")
                    or rec.get("awards/0/suppliers/0/name"),
                    "",
                )
                if vendor and vendor.lower() != "unspecified vendor":
                    vendor_single_rows[vendor].append((idx, rec))
                if window_days is not None and window_days < short_window_limit:
                    short_window_single_rows.append((idx, rec))
            else:
                multi_bid_count += 1

        # Buyer groups with zero positive bidder counts are explicitly UNEVALUABLE, never cleared!
        if positive_bid_count == 0:
            unevaluable_count += len(indexed_rows)
            unevaluable_buyer_groups += 1
            continue

        single_share = len(single_bid_rows) / positive_bid_count
        repeat_vendors = {
            v: rows for v, rows in vendor_single_rows.items() if len(rows) >= 3
        }

        share_triggered = positive_bid_count >= min_tenders and single_share > single_bid_share_threshold
        repeat_triggered = bool(repeat_vendors)
        short_window_triggered = len(short_window_single_rows) >= 2

        if not (share_triggered or repeat_triggered or short_window_triggered):
            cleared_count += len(indexed_rows)
            continue

        typed_single = [_extract_typed_value(r) for _, r in single_bid_rows]
        exposure_inr = sum(v for v, _ in typed_single)
        lead_amount_type = _summarize_amount_type([t for _, t in typed_single])
        sample_rec = single_bid_rows[0][1] if single_bid_rows else competitive[0][1]
        jurisdiction = _norm_str(sample_rec.get("jurisdiction"), "Union of India")
        fy_display, fy_start, fy_end, fy_list = _extract_fiscal_year_span([r for _, r in competitive])
        safe_buyer = _neutralize_external_text(buyer)
        coverage_pct = round((positive_bid_count / len(competitive)) * 100.0, 2)

        rules_hit: list[str] = []
        if share_triggered:
            rules_hit.append(
                f"single_bid_share={len(single_bid_rows)}/{positive_bid_count} ({single_share * 100:.2f}%) > {single_bid_share_threshold * 100:.0f}%"
            )
        if repeat_triggered:
            top_repeat = max(repeat_vendors.items(), key=lambda kv: (len(kv[1]), kv[0]))
            rules_hit.append(
                f"repeat_single_bid_vendor={_neutralize_external_text(top_repeat[0])} ({len(top_repeat[1])} awards)"
            )
        if short_window_triggered:
            rules_hit.append(
                f"short_bid_window<{short_window_limit}d_with_single_bid={len(short_window_single_rows)}"
            )

        amount_label = (
            "award values"
            if lead_amount_type == "award_value"
            else ("tender estimates" if lead_amount_type == "tender_estimate" else "recorded amounts")
        )

        citations = [
            _build_citation(
                rec,
                idx,
                (
                    f"Tender {rec.get('tender_id') or rec.get('ocid')}: "
                    f"bids={rec.get('bid_count', rec.get('tender/numberOfTenderers', 1))}, "
                    f"window={_extract_bid_window_days(rec)}d, "
                    f"{_extract_typed_value(rec)[1]}=INR {_extract_typed_value(rec)[0]:,.0f}"
                ),
            )
            for idx, rec in single_bid_rows[:5]
        ]

        summary = (
            f"Public record anomaly observed in {safe_buyer} ({jurisdiction}, {fy_display}): "
            f"{len(single_bid_rows)} of {positive_bid_count} competitive tenders with positive bidder counts "
            f"({single_share * 100:.2f}%; total group rows={len(indexed_rows)}, multi-bid={multi_bid_count}, "
            f"literal zero-bid={zero_bid_count}, blank/unknown bid count={unknown_bid_count}) "
            f"recorded a single bidder totaling INR {exposure_inr:,.0f} in {amount_label}, including "
            f"{len(short_window_single_rows)} single-bid tender(s) with bid windows under {short_window_limit} days. "
            f"Triggers noted under CVC Single-Tender Guidelines and GFR 2017 Rule 166 (or state equivalent): "
            f"{'; '.join(rules_hit)}."
        )

        high_signal = single_share >= 0.50 or bool(repeat_vendors) or len(short_window_single_rows) >= 3
        leads.append(
            AuditLeadCandidate(
                title=f"Procurement Red-Flag Lead: Single-Bidder Concentration ({single_share * 100:.1f}%) — {safe_buyer}",
                summary=summary,
                methodology_code="M1",
                entity_name=safe_buyer,
                jurisdiction=jurisdiction,
                fiscal_year=fy_display,
                fiscal_year_start=fy_start,
                fiscal_year_end=fy_end,
                source_locator=_row_locator(single_bid_rows),
                amount_type=lead_amount_type,
                monetary_exposure_inr=round(exposure_inr, 2),
                severity=_severity_from_exposure(exposure_inr, high_signal=high_signal),
                anomaly_metrics={
                    "numerator": len(single_bid_rows),
                    "denominator": positive_bid_count,
                    "competitive_tenders": positive_bid_count,
                    "competitive_tenders_total": len(competitive),
                    "total_buyer_tenders": len(indexed_rows),
                    "single_bid_count": len(single_bid_rows),
                    "multi_bid_count": multi_bid_count,
                    "zero_bid_count": zero_bid_count,
                    "unknown_bid_count": unknown_bid_count,
                    "positive_bid_coverage_pct": coverage_pct,
                    "single_bid_share_pct": round(single_share * 100.0, 2),
                    "short_window_single_bid_count": len(short_window_single_rows),
                    "repeat_single_bid_vendors": {
                        vendor: len(rows) for vendor, rows in repeat_vendors.items()
                    },
                    "flagged_value_inr": round(exposure_inr, 2),
                    "amount_type": lead_amount_type,
                    "fiscal_year_start": fy_start,
                    "fiscal_year_end": fy_end,
                    "fiscal_years_observed": fy_list,
                },
                rule_triggered="CVC Single-Tender Guidelines & GFR 2017 Rule 166 / State Equivalent (" + "; ".join(rules_hit) + ")",
                citations=citations,
                benign_explanations=[
                    "Specialized proprietary equipment or OEM spares may lawfully qualify for single-tender procurement under GFR 2017 Rule 166 or applicable state rules with recorded competent authority justification.",
                    "Shortened bid windows can reflect urgent public-health, flood-relief, or emergency infrastructure restoration timelines approved by the department.",
                    "A single responsive bid may occur after open advertisement if other participating bidders failed routine technical or statutory eligibility checks.",
                ],
                suggested_authority=safe_buyer,
            )
        )

    top_leads, total_detected = _cap_leads_deterministically(leads, ctx)
    lead_note = (
        f"top {len(top_leads)} of {total_detected} lead(s)"
        if total_detected > len(top_leads)
        else f"{len(top_leads)} lead(s)"
    )
    overall_amount_type = _summarize_amount_type(all_amount_types)
    summary_text = (
        f"M1 scanned {len(records)} procurement records across {len(by_buyer)} buyers "
        f"(INR {total_audited:,.0f} in {overall_amount_type}): {lead_note}, "
        f"{cleared_count} cleared record(s), {unevaluable_count} unevaluable record(s) "
        f"across {unevaluable_buyer_groups} buyer group(s) with no positive bidder counts."
    )
    return (
        top_leads,
        cleared_count,
        round(total_audited, 2),
        summary_text,
        {
            "unevaluable_count": unevaluable_count,
            "unevaluable_buyer_groups": unevaluable_buyer_groups,
            "amount_type": overall_amount_type,
        },
    )


def run_m2_split_tendering(
    records: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> tuple[list[AuditLeadCandidate], int, float, str, dict[str, Any]]:
    """M2: Split-tendering & threshold-hugging detection with strict date/value & jurisdiction discipline.

    - Never uses OCDS ``release_date`` as ``award_date`` and never treats ``tender_estimate`` as ``award_value``.
    - Emits split-tendering / same-day order claims ONLY when genuine ``award_date``/``po_date`` and
      award/PO values exist.
    - Never turns undated groups into 0-day clusters (``span_days=None`` when undated).
    - Retains tender-estimate price-band observations ONLY with explicit ``tender_estimate`` labels
      and ``splitting_claim_supported=False``.
    - Resolves governing jurisdiction & effective date rather than blindly applying July 2024 Central
      GFR thresholds to earlier or State records.
    """
    ctx = context or {}
    lower_ratio = float(ctx.get("lower_ratio", 0.85))
    upper_ratio = float(ctx.get("upper_ratio", 0.999))
    window_days_limit = int(ctx.get("window_days", 60))
    min_cluster_count = int(ctx.get("min_cluster_count", 3))

    total_audited = 0.0
    all_amount_types: list[str] = []

    # tuple: (idx, rec, val, val_type, order_dt, tender_pub_dt, bid_open_dt)
    grouped: dict[
        tuple[str, str],
        list[tuple[int, dict[str, Any], float, str, date | None, date | None, date | None]],
    ] = defaultdict(list)

    for idx, rec in enumerate(records):
        val, val_type = _extract_typed_value(rec)
        total_audited += max(0.0, val)
        all_amount_types.append(val_type)
        buyer = _norm_str(
            rec.get("buyer_name") or rec.get("buyer/name") or rec.get("entity_name") or rec.get("department"),
            "Unspecified Public Buyer",
        )
        category = _norm_str(
            rec.get("item_category")
            or rec.get("tenderclassification/description")
            or rec.get("tender/classification/description")
            or rec.get("category"),
            "General Goods & Services",
        )
        # Strictly separate order/award dates from tender publication, bid opening, and OCDS release dates.
        order_dt = _parse_date(
            rec.get("award_date")
            or rec.get("po_date")
            or rec.get("order_date")
            or rec.get("award/date")
            or rec.get("awards/0/date")
        )
        tender_pub_dt = _parse_date(
            rec.get("tender_published_date")
            or rec.get("tender/datePublished")
            or rec.get("tender_start_date")
            or rec.get("tender/tenderPeriod/startDate")
        )
        bid_open_dt = _parse_date(
            rec.get("bid_opening_date")
            or rec.get("tender/bidOpening/date")
        )
        grouped[(buyer, category)].append(
            (idx, rec, val, val_type, order_dt, tender_pub_dt, bid_open_dt)
        )

    leads: list[AuditLeadCandidate] = []
    flagged_record_indices: set[int] = set()
    unevaluable_indices: set[int] = set()

    for (buyer, category), rows in sorted(grouped.items()):
        sample_jur = _norm_str(rows[0][1].get("jurisdiction"), "Union of India")
        all_group_event_dates = [
            d for item in rows for d in (item[4], item[5]) if d is not None
        ]
        _, _, _, group_fys = _extract_fiscal_year_span([item[1] for item in rows])
        schedule = _resolve_threshold_schedule(sample_jur, all_group_event_dates, group_fys)

        for threshold_inr, threshold_label, rule_anchor in schedule["thresholds"]:
            low_bound = lower_ratio * threshold_inr
            high_bound = upper_ratio * threshold_inr

            hugging = [item for item in rows if low_bound <= item[2] <= high_bound]
            if len(hugging) < min_cluster_count:
                continue

            # Track A: Genuine award/PO temporal clustering (requires real award_value AND real award/PO date)
            dated_awards = sorted(
                [h for h in hugging if h[3] == "award_value" and h[4] is not None],
                key=lambda item: (item[4], item[0]),  # type: ignore[arg-type]
            )
            best_award_cluster: list[tuple[int, dict[str, Any], float, str, date | None, date | None, date | None]] = []
            best_award_sum = 0.0
            if len(dated_awards) >= min_cluster_count:
                right = 0
                running_sum = 0.0
                for left in range(len(dated_awards)):
                    start_dt = dated_awards[left][4]
                    while right < len(dated_awards) and (dated_awards[right][4] - start_dt).days <= window_days_limit:  # type: ignore[operator]
                        running_sum += dated_awards[right][2]
                        right += 1
                    count = right - left
                    if count >= min_cluster_count and running_sum > threshold_inr and running_sum > best_award_sum:
                        best_award_sum = running_sum
                        best_award_cluster = dated_awards[left:right]
                    running_sum -= dated_awards[left][2]

            if best_award_cluster:
                for item in best_award_cluster:
                    flagged_record_indices.add(item[0])
                dates = [x[4] for x in best_award_cluster if x[4] is not None]
                span_days: int | None = (max(dates) - min(dates)).days if len(dates) >= 2 else None
                ratios = [round((x[2] / threshold_inr) * 100.0, 2) for x in best_award_cluster]
                fy_display, fy_start, fy_end, fy_list = _extract_fiscal_year_span(
                    [x[1] for x in best_award_cluster]
                )
                # Re-resolve threshold metadata specifically on this cluster's dates
                cluster_sched = _resolve_threshold_schedule(sample_jur, dates, fy_list)
                safe_buyer = _neutralize_external_text(buyer)
                safe_cat = _neutralize_external_text(category)

                citations = [
                    _build_citation(
                        rec,
                        idx,
                        (
                            f"PO/Award {rec.get('po_id') or rec.get('award_id') or rec.get('tender_id') or rec.get('ocid')}: "
                            f"award_value=INR {val:,.0f} ({(val / threshold_inr) * 100:.1f}% of {threshold_label}) "
                            f"on award_date={dt.isoformat() if dt else 'unrecorded'}"
                        ),
                    )
                    for idx, rec, val, _, dt, _, _ in best_award_cluster[:6]
                ]

                summary = (
                    f"Public record anomaly in {safe_buyer} ({sample_jur}, {fy_display}) for '{safe_cat}': "
                    f"{len(best_award_cluster)} awarded purchase orders within {span_days} day(s) "
                    f"(award dates {min(dates).isoformat()} to {max(dates).isoformat()}) clustered between "
                    f"{min(ratios):.1f}% and {max(ratios):.1f}% of {threshold_label} ({rule_anchor}), "
                    f"aggregating INR {best_award_sum:,.0f} in award values "
                    f"({best_award_sum / threshold_inr:.2f}x the ceiling). "
                    f"Governing instrument scope: {cluster_sched['governing_instrument']}."
                )

                leads.append(
                    AuditLeadCandidate(
                        title=f"Procurement Red-Flag Lead: {len(best_award_cluster)} Awarded POs Clustering Below {threshold_label} — {safe_buyer}",
                        summary=summary,
                        methodology_code="M2",
                        entity_name=safe_buyer,
                        jurisdiction=sample_jur,
                        fiscal_year=fy_display,
                        fiscal_year_start=fy_start,
                        fiscal_year_end=fy_end,
                        source_locator=_row_locator([(x[0], x[1]) for x in best_award_cluster]),
                        amount_type="award_value",
                        monetary_exposure_inr=round(best_award_sum, 2),
                        severity=_severity_from_exposure(best_award_sum, high_signal=len(best_award_cluster) >= 4),
                        anomaly_metrics={
                            "item_category": safe_cat,
                            "threshold_inr": threshold_inr,
                            "threshold_label": threshold_label,
                            "cluster_po_count": len(best_award_cluster),
                            "cluster_sum_inr": round(best_award_sum, 2),
                            "span_days": span_days,
                            "date_basis": "award_date",
                            "amount_type": "award_value",
                            "evidence_type": "award_order_temporal_cluster",
                            "splitting_claim_supported": True,
                            "has_award_evidence": True,
                            "min_threshold_pct": min(ratios),
                            "max_threshold_pct": max(ratios),
                            "threshold_multiple": round(best_award_sum / threshold_inr, 2),
                            "governing_instrument": cluster_sched["governing_instrument"],
                            "threshold_scope": cluster_sched["threshold_scope"],
                            "threshold_effective_date": cluster_sched["threshold_effective_date"],
                            "statutory_applicability_verified": cluster_sched["statutory_applicability_verified"],
                            "fiscal_year_start": fy_start,
                            "fiscal_year_end": fy_end,
                        },
                        rule_triggered=(
                            f"{rule_anchor}; CVC Splitting Circulars "
                            "(>=3 dated awarded POs in 85%-99.9% band within 60d summing > threshold)"
                        ),
                        citations=citations,
                        benign_explanations=[
                            "Staggered budget allocations or phased sub-office indents can lawfully produce separate purchase orders across a 60-day period.",
                            "Emergent operational requirements or distinct delivery locations may necessitate independent call-offs under approved rate contracts.",
                            "Standardized unit pack sizes or GeM lot limits can naturally price individual orders just below administrative ceilings.",
                        ],
                        suggested_authority=safe_buyer,
                    )
                )
                continue

            # Track B: Tender-estimate / undated price-band observation ONLY (NEVER a splitting or 0-day claim!)
            dated_tenders = sorted(
                [h for h in hugging if h[5] is not None],
                key=lambda item: (item[5], item[0]),  # type: ignore[arg-type]
            )
            obs_cluster: list[tuple[int, dict[str, Any], float, str, date | None, date | None, date | None]] = []
            obs_sum = 0.0
            obs_date_basis = "unrecorded"
            obs_span_days: int | None = None

            if len(dated_tenders) >= min_cluster_count:
                right = 0
                running_sum = 0.0
                for left in range(len(dated_tenders)):
                    start_dt = dated_tenders[left][5]
                    while right < len(dated_tenders) and (dated_tenders[right][5] - start_dt).days <= window_days_limit:  # type: ignore[operator]
                        running_sum += dated_tenders[right][2]
                        right += 1
                    count = right - left
                    if count >= min_cluster_count and running_sum > threshold_inr and running_sum > obs_sum:
                        obs_sum = running_sum
                        obs_cluster = dated_tenders[left:right]
                    running_sum -= dated_tenders[left][2]
                if obs_cluster:
                    pub_dates = [x[5] for x in obs_cluster if x[5] is not None]
                    obs_date_basis = "tender_published_date"
                    obs_span_days = (max(pub_dates) - min(pub_dates)).days if len(pub_dates) >= 2 else None

            if not obs_cluster:
                # Undated tender estimates in price band: retain as non-temporal price-band observation ONLY;
                # NEVER assign span_days=0 or make a same-day order / splitting claim!
                u_sum = sum(x[2] for x in hugging)
                if len(hugging) >= min_cluster_count and u_sum > threshold_inr:
                    obs_cluster = hugging
                    obs_sum = u_sum
                    obs_date_basis = "unrecorded"
                    obs_span_days = None

            if not obs_cluster:
                continue

            for item in obs_cluster:
                flagged_record_indices.add(item[0])

            ratios = [round((x[2] / threshold_inr) * 100.0, 2) for x in obs_cluster]
            obs_amount_type = _summarize_amount_type([x[3] for x in obs_cluster])
            fy_display, fy_start, fy_end, fy_list = _extract_fiscal_year_span([x[1] for x in obs_cluster])
            bid_open_dates = sorted({x[6] for x in obs_cluster if x[6] is not None})
            bid_open_span_days = (
                (max(bid_open_dates) - min(bid_open_dates)).days if len(bid_open_dates) >= 2 else None
            )
            safe_buyer = _neutralize_external_text(buyer)
            safe_cat = _neutralize_external_text(category)

            citations = [
                _build_citation(
                    rec,
                    idx,
                    (
                        f"Tender {rec.get('tender_id') or rec.get('ocid')}: "
                        f"{vtype}=INR {val:,.0f} ({(val / threshold_inr) * 100:.1f}% of {threshold_label}); "
                        f"tender_published={pub_dt.isoformat() if pub_dt else 'unrecorded'}; "
                        f"bid_opening={bo_dt.isoformat() if bo_dt else 'unrecorded'}; "
                        f"award_date=unrecorded"
                    ),
                )
                for idx, rec, val, vtype, _, pub_dt, bo_dt in obs_cluster[:6]
            ]

            timing_clause = (
                f"published across {obs_span_days} day(s) by tender publication date"
                if obs_span_days is not None
                else (
                    f"with award and tender publication dates unrecorded "
                    f"({len(bid_open_dates)} distinct bid-opening date(s) spanning {bid_open_span_days} days)"
                    if bid_open_dates
                    else "with award and tender publication dates unrecorded"
                )
            )
            summary = (
                f"Price-band observation (tender estimates only; no award-date evidence) in {safe_buyer} "
                f"({sample_jur}, {fy_display}) for '{safe_cat}': {len(obs_cluster)} tender records "
                f"{timing_clause} carry {obs_amount_type} values between {min(ratios):.1f}% and "
                f"{max(ratios):.1f}% of the {threshold_label} reference band ({rule_anchor}), "
                f"totalling INR {obs_sum:,.0f} in {obs_amount_type}. "
                f"Because neither award dates nor award values are present in these source rows, this "
                f"observation does NOT establish same-day ordering or statutory split-tendering; "
                f"governing scope: {schedule['governing_instrument']}."
            )

            # Cap severity strictly for estimate-only observations lacking award/order evidence
            obs_severity = "medium" if obs_date_basis == "tender_published_date" else "low"

            leads.append(
                AuditLeadCandidate(
                    title=f"Price-Band Observation (Tender Estimates): {len(obs_cluster)} Tenders Near {threshold_label} — {safe_buyer}",
                    summary=summary,
                    methodology_code="M2",
                    entity_name=safe_buyer,
                    jurisdiction=sample_jur,
                    fiscal_year=fy_display,
                    fiscal_year_start=fy_start,
                    fiscal_year_end=fy_end,
                    source_locator=_row_locator([(x[0], x[1]) for x in obs_cluster]),
                    amount_type=obs_amount_type,
                    monetary_exposure_inr=round(obs_sum, 2),
                    severity=obs_severity,
                    anomaly_metrics={
                        "item_category": safe_cat,
                        "threshold_inr": threshold_inr,
                        "threshold_label": threshold_label,
                        "cluster_po_count": len(obs_cluster),
                        "tender_count": len(obs_cluster),
                        "cluster_sum_inr": round(obs_sum, 2),
                        "span_days": obs_span_days,
                        "date_basis": obs_date_basis,
                        "amount_type": obs_amount_type,
                        "evidence_type": "tender_estimate_price_band_observation",
                        "splitting_claim_supported": False,
                        "has_award_evidence": False,
                        "distinct_bid_opening_dates": len(bid_open_dates),
                        "bid_opening_span_days": bid_open_span_days,
                        "min_threshold_pct": min(ratios),
                        "max_threshold_pct": max(ratios),
                        "threshold_multiple": round(obs_sum / threshold_inr, 2),
                        "governing_instrument": schedule["governing_instrument"],
                        "threshold_scope": schedule["threshold_scope"],
                        "threshold_effective_date": schedule["threshold_effective_date"],
                        "statutory_applicability_verified": schedule["statutory_applicability_verified"],
                        "fiscal_year_start": fy_start,
                        "fiscal_year_end": fy_end,
                    },
                    rule_triggered=(
                        f"Price-Band Observation Only ({rule_anchor}; "
                        f"amount_type={obs_amount_type}; award dates unrecorded — splitting claim not established)"
                    ),
                    citations=citations,
                    benign_explanations=[
                        "Source rows contain tender estimates without award dates or award values; distinct tenders may have been issued across months or fiscal years and may not have been awarded.",
                        "Applicable State Public Procurement Rules and Delegation of Financial Powers (DFP) monetary limits may differ materially from Central GFR 2017 ceilings.",
                        "Standardized departmental cost estimates, recurring district-level works, or phased budget releases can naturally fall within the same nominal price band.",
                    ],
                    suggested_authority=safe_buyer,
                )
            )

    # Track rows lacking both value and award date as unevaluable if not flagged
    for idx, rec in enumerate(records):
        if idx in flagged_record_indices:
            continue
        val, _ = _extract_typed_value(rec)
        if val <= 0:
            unevaluable_indices.add(idx)

    cleared_count = len(records) - len(flagged_record_indices) - len(unevaluable_indices)
    top_leads, total_detected = _cap_leads_deterministically(leads, ctx)
    lead_note = (
        f"top {len(top_leads)} of {total_detected} threshold/price-band lead(s)"
        if total_detected > len(top_leads)
        else f"{len(top_leads)} threshold/price-band lead(s)"
    )
    overall_amount_type = _summarize_amount_type(all_amount_types)
    summary_text = (
        f"M2 scanned {len(records)} procurement records across {len(grouped)} buyer-category groups "
        f"(INR {total_audited:,.0f} in {overall_amount_type}): {lead_note}, "
        f"{cleared_count} cleared record(s), {len(unevaluable_indices)} unevaluable record(s)."
    )
    return (
        top_leads,
        cleared_count,
        round(total_audited, 2),
        summary_text,
        {
            "unevaluable_count": len(unevaluable_indices),
            "amount_type": overall_amount_type,
        },
    )


def run_m3_vendor_concentration(
    records: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> tuple[list[AuditLeadCandidate], int, float, str, dict[str, Any]]:
    """M3: Repeat-winner vendor HHI > 0.35, top vendor share > 40%, or narrow bid-spread clustering.

    Explicitly emits which threshold fired (``hhi`` vs ``top_supplier_share`` vs ``narrow_bid_spread``),
    the full multi-year fiscal window, and the excluded (unawarded / unidentified supplier) share.
    """
    ctx = context or {}
    hhi_threshold = float(ctx.get("hhi_threshold", 0.35))
    share_threshold = float(ctx.get("share_threshold", 0.40))
    min_group_tenders = int(ctx.get("min_group_tenders", 4))

    total_audited = 0.0
    grouped_awarded: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    grouped_excluded: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    unawarded_cleared = 0

    for idx, rec in enumerate(records):
        buyer = _norm_str(
            rec.get("buyer_name") or rec.get("buyer/name") or rec.get("entity_name") or rec.get("department"),
            "Unspecified Public Buyer",
        )
        category = _norm_str(
            rec.get("item_category")
            or rec.get("tenderclassification/description")
            or rec.get("tender/classification/description")
            or rec.get("category"),
            "General Procurement",
        )
        vendor = _norm_str(
            rec.get("vendor_name")
            or rec.get("supplier_name")
            or rec.get("awardee")
            or rec.get("awards/0/suppliers/0/name"),
            "",
        )
        val = 0.0
        if rec.get("has_award") is True:
            val = _to_float(rec.get("award_amount_inr", rec.get("award_value_inr", 0)))
        elif rec.get("has_award") is False:
            val = 0.0
        else:
            val = _to_float(
                rec.get(
                    "award_value_inr",
                    rec.get("awards/0/value/amount", rec.get("value_inr", rec.get("l1_bid_inr", 0))),
                )
            )
        total_audited += max(0.0, val)
        if not vendor or val <= 0:
            unawarded_cleared += 1
            grouped_excluded[(buyer, category)].append((idx, rec))
            continue

        grouped_awarded[(buyer, category)].append((idx, rec))

    leads: list[AuditLeadCandidate] = []
    cleared_count = unawarded_cleared

    for (buyer, category), rows in sorted(grouped_awarded.items()):
        vendor_totals: dict[str, float] = defaultdict(float)
        vendor_counts: dict[str, int] = defaultdict(int)
        group_value = 0.0

        spread_values: list[float] = []
        narrow_spread_rows: list[tuple[int, dict[str, Any], float]] = []

        for idx, rec in rows:
            val = (
                _to_float(rec.get("award_amount_inr", rec.get("award_value_inr", 0)))
                if rec.get("has_award") is True
                else _to_float(
                    rec.get(
                        "award_value_inr",
                        rec.get("awards/0/value/amount", rec.get("value_inr", rec.get("l1_bid_inr", 0))),
                    )
                )
            )
            vendor = _norm_str(
                rec.get("vendor_name")
                or rec.get("supplier_name")
                or rec.get("awardee")
                or rec.get("awards/0/suppliers/0/name"),
                "",
            )
            if val > 0 and vendor:
                vendor_totals[vendor] += val
                vendor_counts[vendor] += 1
                group_value += val

            l1 = _to_float(rec.get("l1_bid_inr", val))
            l2 = _to_float(rec.get("l2_bid_inr", 0))
            if rec.get("bid_spread_pct") is not None and str(rec.get("bid_spread_pct")).strip() != "":
                spread_pct = _to_float(rec.get("bid_spread_pct"))
                if spread_pct > 0:
                    spread_values.append(spread_pct)
                    if spread_pct <= 3.0:
                        narrow_spread_rows.append((idx, rec, spread_pct))
            elif l1 > 0 and l2 > l1:
                spread_pct = ((l2 - l1) / l1) * 100.0
                spread_values.append(spread_pct)
                if spread_pct <= 3.0:
                    narrow_spread_rows.append((idx, rec, spread_pct))

        if group_value <= 0 or not vendor_totals:
            cleared_count += len(rows)
            continue

        shares = {v: amt / group_value for v, amt in vendor_totals.items()}
        hhi = sum(s * s for s in shares.values())
        top_vendor, top_share = max(shares.items(), key=lambda kv: (kv[1], kv[0]))
        narrow_ratio = (len(narrow_spread_rows) / len(spread_values)) if spread_values else 0.0

        hhi_triggered = len(rows) >= min_group_tenders and hhi > hhi_threshold
        share_triggered = len(rows) >= min_group_tenders and top_share > share_threshold
        narrow_triggered = len(narrow_spread_rows) >= 3 and narrow_ratio >= 0.60

        if not (hhi_triggered or share_triggered or narrow_triggered):
            cleared_count += len(rows)
            continue

        thresholds_triggered: list[str] = []
        rules_hit: list[str] = []
        if hhi_triggered:
            thresholds_triggered.append("hhi")
            rules_hit.append(f"HHI={hhi:.3f} > {hhi_threshold:.2f}")
        if share_triggered:
            thresholds_triggered.append("top_supplier_share")
            rules_hit.append(f"top_supplier_share={top_share * 100:.2f}% > {share_threshold * 100:.0f}%")
        if narrow_triggered:
            thresholds_triggered.append("narrow_bid_spread")
            avg_spread = sum(x[2] for x in narrow_spread_rows) / len(narrow_spread_rows)
            rules_hit.append(
                f"narrow_l2_l1_spread={len(narrow_spread_rows)}/{len(spread_values)} tenders (avg {avg_spread:.2f}%)"
            )

        excluded_rows = grouped_excluded.get((buyer, category), [])
        total_group_rows = len(rows) + len(excluded_rows)
        excluded_row_share_pct = round((len(excluded_rows) / total_group_rows) * 100.0, 2) if total_group_rows else 0.0
        excluded_est_value = sum(_extract_proc_value(r) for _, r in excluded_rows)
        total_group_est_plus_award = group_value + excluded_est_value
        excluded_value_share_pct = (
            round((excluded_est_value / total_group_est_plus_award) * 100.0, 2)
            if total_group_est_plus_award > 0
            else 0.0
        )

        sample_rec = rows[0][1]
        jurisdiction = _norm_str(sample_rec.get("jurisdiction"), "Union of India")
        fy_display, fy_start, fy_end, fy_list = _extract_fiscal_year_span([r for _, r in rows])
        exposure_inr = vendor_totals[top_vendor] if (hhi_triggered or share_triggered) else group_value
        safe_buyer = _neutralize_external_text(buyer)
        safe_cat = _neutralize_external_text(category)
        safe_vendor = _neutralize_external_text(top_vendor)

        citations = [
            _build_citation(
                rec,
                idx,
                (
                    f"Contract {rec.get('tender_id') or rec.get('contract_id') or rec.get('ocid')}: "
                    f"vendor={rec.get('vendor_name') or rec.get('awards/0/suppliers/0/name')}, "
                    f"award_value=INR {_to_float(rec.get('award_amount_inr') if rec.get('has_award') is True else rec.get('award_value_inr', rec.get('l1_bid_inr', 0))):,.0f}"
                ),
            )
            for idx, rec in rows[:5]
        ]

        if hhi_triggered and share_triggered:
            title_signal = f"HHI {hhi:.2f} & Top-Supplier Share {top_share * 100:.1f}%"
        elif hhi_triggered:
            title_signal = f"HHI {hhi:.2f} > {hhi_threshold:.2f}"
        elif share_triggered:
            title_signal = f"Top-Supplier Share {top_share * 100:.1f}% > {share_threshold * 100:.0f}% (HHI {hhi:.2f} below {hhi_threshold:.2f})"
        else:
            title_signal = "Narrow L1-L2 Bid-Spread Clustering"

        hhi_status_clause = (
            f"HHI {hhi:.3f} exceeded {hhi_threshold:.2f}"
            if hhi_triggered
            else f"HHI {hhi:.3f} remained below the {hhi_threshold:.2f} threshold"
        )
        summary = (
            f"Statistical divergence in vendor award concentration for {safe_buyer} — '{safe_cat}' "
            f"({jurisdiction}, {fy_display}): across {len(rows)} awarded contracts with identified suppliers "
            f"totaling INR {group_value:,.0f} (excluding {len(excluded_rows)} unawarded/unidentified row(s), "
            f"{excluded_row_share_pct:.1f}% of {total_group_rows} group rows), {hhi_status_clause}, while "
            f"top supplier {safe_vendor} held {top_share * 100:.2f}% ({vendor_counts[top_vendor]} contracts, "
            f"INR {vendor_totals[top_vendor]:,.0f}). Fired threshold(s): {', '.join(thresholds_triggered)} "
            f"({'; '.join(rules_hit)}). Concentration warrants review of market structure, specifications, "
            f"and procurement context."
        )

        leads.append(
            AuditLeadCandidate(
                title=f"Statistical Divergence: {title_signal} in {safe_cat} — {safe_buyer}",
                summary=summary,
                methodology_code="M3",
                entity_name=safe_buyer,
                jurisdiction=jurisdiction,
                fiscal_year=fy_display,
                fiscal_year_start=fy_start,
                fiscal_year_end=fy_end,
                source_locator=_row_locator(rows),
                amount_type="award_value",
                monetary_exposure_inr=round(exposure_inr, 2),
                severity=_severity_from_exposure(exposure_inr, high_signal=(hhi >= 0.50 or narrow_triggered)),
                anomaly_metrics={
                    "item_category": safe_cat,
                    "tender_count": len(rows),
                    "total_group_rows": total_group_rows,
                    "excluded_row_count": len(excluded_rows),
                    "excluded_row_share_pct": excluded_row_share_pct,
                    "excluded_estimate_value_inr": round(excluded_est_value, 2),
                    "excluded_value_share_pct": excluded_value_share_pct,
                    "distinct_vendors": len(vendor_totals),
                    "hhi": round(hhi, 4),
                    "hhi_threshold": hhi_threshold,
                    "hhi_triggered": hhi_triggered,
                    "top_vendor_name": safe_vendor,
                    "top_vendor_share_pct": round(top_share * 100.0, 2),
                    "top_supplier_share_threshold_pct": round(share_threshold * 100.0, 2),
                    "top_supplier_share_triggered": share_triggered,
                    "narrow_spread_triggered": narrow_triggered,
                    "thresholds_triggered": thresholds_triggered,
                    "top_vendor_value_inr": round(vendor_totals[top_vendor], 2),
                    "narrow_spread_count": len(narrow_spread_rows),
                    "narrow_spread_ratio_pct": round(narrow_ratio * 100.0, 2),
                    "group_total_value_inr": round(group_value, 2),
                    "amount_type": "award_value",
                    "fiscal_year_start": fy_start,
                    "fiscal_year_end": fy_end,
                    "fiscal_years_observed": fy_list,
                },
                rule_triggered="CCI Bid-Rigging Indicators & OECD Concentration Metrics (" + "; ".join(rules_hit) + ")",
                citations=citations,
                benign_explanations=[
                    "A niche technical domain or regional market may have only a small pool of qualified vendors capable of meeting statutory performance criteria.",
                    "Repeated awards to one supplier across multiple fiscal years may reflect genuine price competitiveness or call-offs under a lawfully bid multi-year rate contract.",
                    "Narrow L1-L2 bid spreads can also arise when bidders independently benchmark against transparent GeM catalog prices or published PWD Schedule of Rates.",
                ],
                suggested_authority=safe_buyer,
            )
        )

    top_leads, total_detected = _cap_leads_deterministically(leads, ctx)
    lead_note = (
        f"top {len(top_leads)} of {total_detected} concentration/spread lead(s)"
        if total_detected > len(top_leads)
        else f"{len(top_leads)} concentration/spread lead(s)"
    )
    summary_text = (
        f"M3 scanned {len(records)} contract records across {len(grouped_awarded)} awarded buyer-category groups "
        f"(INR {total_audited:,.0f} in award_value): {lead_note}, {cleared_count} cleared/excluded record(s)."
    )
    return (
        top_leads,
        cleared_count,
        round(total_audited, 2),
        summary_text,
        {
            "unevaluable_count": 0,
            "excluded_unawarded_count": unawarded_cleared,
            "amount_type": "award_value",
        },
    )


def run_m4_unit_price_outliers(
    records: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> tuple[list[AuditLeadCandidate], int, float, str, dict[str, Any]]:
    """M4: Unit-price outliers (>2.5 MAD or >1.75x peer median), post-award escalation, and award-vs-estimate variance.

    Explicitly distinguishes ``award_value`` vs ``tender_estimate`` and records associated dates;
    never describes an award-vs-estimate gap as a final contract cost overrun without completion/disbursement data.
    """
    ctx = context or {}
    mad_cutoff = float(ctx.get("mad_cutoff", 2.5))
    median_ratio_cutoff = float(ctx.get("median_ratio_cutoff", 1.75))
    escalation_cutoff_pct = float(ctx.get("escalation_cutoff_pct", 20.0))
    min_tender_estimate_inr = float(ctx.get("min_tender_estimate_inr", 10_000.0))
    min_escalation_inr = float(ctx.get("min_escalation_inr", 100_000.0))

    total_audited = 0.0
    by_sku: dict[tuple[str, str], list[tuple[int, dict[str, Any], float]]] = defaultdict(list)

    def _get_award_val(rec: dict[str, Any], unit_price: float, qty: float) -> tuple[float, bool]:
        if rec.get("has_award") is False:
            return 0.0, False
        for key in ("awarded_value_inr", "award_amount_inr", "awards/0/value/amount"):
            val = _to_float_opt(rec.get(key))
            if val is not None and val > 0:
                return val, True
        aw_val = _to_float_opt(rec.get("award_value_inr"))
        if aw_val is not None and aw_val > 0:
            return aw_val, True
        if unit_price > 0:
            return unit_price * qty, False
        return 0.0, False

    for idx, rec in enumerate(records):
        unit_price = _to_float(rec.get("unit_price_inr", 0))
        qty = _to_float(rec.get("quantity", 1), 1.0)
        tender_est = _to_float(
            rec.get("tender_estimate_inr", rec.get("tender_value_inr", rec.get("tender/value/amount", 0)))
        )
        awarded, _ = _get_award_val(rec, unit_price, qty)
        final_val = _to_float(rec.get("final_value_inr", rec.get("revised_value_inr", 0)))
        total_audited += max(tender_est, awarded, final_val, 0.0)

        if unit_price > 0:
            cat = _norm_key(rec.get("item_category") or rec.get("sku_category") or rec.get("item_name"), "item")
            unit = _norm_key(rec.get("unit"), "unit")
            by_sku[(cat, unit)].append((idx, rec, unit_price))

    sku_stats: dict[tuple[str, str], tuple[float, float, int]] = {}
    for key, items in by_sku.items():
        prices = [x[2] for x in items]
        med = float(median(prices))
        mad = float(median([abs(p - med) for p in prices]))
        sku_stats[key] = (med, mad, len(prices))

    leads: list[AuditLeadCandidate] = []
    flagged_indices: set[int] = set()

    for idx, rec in enumerate(records):
        unit_price = _to_float(rec.get("unit_price_inr", 0))
        qty = max(1.0, _to_float(rec.get("quantity", 1), 1.0))
        tender_est = _to_float(
            rec.get("tender_estimate_inr", rec.get("tender_value_inr", rec.get("tender/value/amount", 0)))
        )
        awarded, has_explicit_award = _get_award_val(rec, unit_price, qty)
        final_val = _to_float(rec.get("final_value_inr", rec.get("revised_value_inr", 0)))
        has_final_val = (
            rec.get("final_value_inr") not in (None, "") or rec.get("revised_value_inr") not in (None, "")
        ) and final_val > 0

        est_dt = _parse_date(
            rec.get("tender_estimate_date")
            or rec.get("tender_published_date")
            or rec.get("tender/datePublished")
            or rec.get("tender_start_date")
        )
        aw_dt = _parse_date(rec.get("award_date") or rec.get("awards/0/date"))
        fin_dt = _parse_date(
            rec.get("final_cost_date") or rec.get("completion_date") or rec.get("disbursement_date")
        )

        cat_display = _norm_str(
            rec.get("item_category")
            or rec.get("tenderclassification/description")
            or rec.get("sku_category")
            or rec.get("item_name"),
            "Standard Item",
        )
        unit_display = _norm_str(rec.get("unit"), "contract" if unit_price <= 0 else "unit")
        key = (_norm_key(cat_display, "item"), _norm_key(unit_display, "unit"))

        price_outlier = False
        med_price = 0.0
        mad_val = 0.0
        price_ratio = 1.0
        mad_multiple = 0.0
        excess_price_inr = 0.0

        if unit_price > 0:
            benchmark = _to_float(rec.get("benchmark_unit_price_inr", 0))
            med_val, mad_calc, peer_n = sku_stats.get(key, (0.0, 0.0, 0))
            ref_median = med_val if peer_n >= 4 else (benchmark if benchmark > 0 else med_val)
            if ref_median > 0:
                med_price = ref_median
                mad_val = mad_calc
                price_ratio = unit_price / ref_median
                if mad_val > 0:
                    mad_multiple = (unit_price - ref_median) / mad_val
                if (peer_n >= 4 and mad_val > 0 and mad_multiple > mad_cutoff and price_ratio > 1.35) or (
                    price_ratio > median_ratio_cutoff
                ):
                    price_outlier = True
                    excess_price_inr = max(0.0, (unit_price - ref_median) * qty)

        # Check 4B: Post-award cost variation (final/revised contract value vs awarded value)
        escalation_flag = False
        escalation_pct = 0.0
        escalation_inr = 0.0
        escalation_kind = "none"
        comparison_basis = "unit_price_benchmark"
        is_final_cost_overrun = False
        lead_amount_type = "unit_price"

        if has_final_val and awarded > 0 and final_val > awarded:
            escalation_inr = final_val - awarded
            escalation_pct = (escalation_inr / awarded) * 100.0
            if escalation_pct > escalation_cutoff_pct:
                escalation_flag = True
                escalation_kind = "post_award_escalation"
                comparison_basis = "final_value_vs_award_value"
                is_final_cost_overrun = True
                lead_amount_type = "final_vs_award_value"

        # Check 4C: Award value vs published tender estimate (NOT a final cost overrun!)
        if (
            not escalation_flag
            and has_explicit_award
            and tender_est >= min_tender_estimate_inr
            and awarded > tender_est
        ):
            est_excess = awarded - tender_est
            est_pct = (est_excess / tender_est) * 100.0
            if est_pct > escalation_cutoff_pct and est_excess >= min_escalation_inr:
                escalation_flag = True
                escalation_inr = est_excess
                escalation_pct = est_pct
                escalation_kind = "award_vs_estimate_escalation"
                comparison_basis = "award_value_vs_tender_estimate"
                is_final_cost_overrun = False
                lead_amount_type = "award_vs_tender_estimate"

        if not (price_outlier or escalation_flag):
            continue

        flagged_indices.add(idx)
        buyer = _norm_str(
            rec.get("buyer_name") or rec.get("buyer/name") or rec.get("entity_name") or rec.get("department"),
            "Unspecified Public Buyer",
        )
        jurisdiction = _norm_str(rec.get("jurisdiction"), "Union of India")
        fiscal_year = _norm_str(rec.get("fiscal_year"), "Unspecified")
        tender_id = _norm_str(rec.get("tender_id") or rec.get("ocid") or rec.get("contract_id"), f"BOQ-{idx + 1}")
        exposure_inr = max(excess_price_inr, escalation_inr, 0.0)
        safe_buyer = _neutralize_external_text(buyer)
        safe_cat = _neutralize_external_text(cat_display)

        est_dt_iso = est_dt.isoformat() if est_dt else None
        aw_dt_iso = aw_dt.isoformat() if aw_dt else None
        fin_dt_iso = fin_dt.isoformat() if fin_dt else None

        reasons: list[str] = []
        if price_outlier:
            reasons.append(
                f"unit_price=INR {unit_price:,.2f} vs peer_median=INR {med_price:,.2f} "
                f"({price_ratio:.2f}x median, {mad_multiple:.2f} MAD)"
            )
        if escalation_flag and escalation_kind == "award_vs_estimate_escalation":
            reasons.append(
                f"award_value=INR {awarded:,.0f} (date={aw_dt_iso or 'unrecorded'}) vs "
                f"tender_estimate=INR {tender_est:,.0f} (date={est_dt_iso or 'unrecorded'}): +{escalation_pct:.1f}%"
            )
        elif escalation_flag:
            reasons.append(
                f"final_value=INR {final_val:,.0f} (date={fin_dt_iso or 'unrecorded'}) vs "
                f"award_value=INR {awarded:,.0f} (date={aw_dt_iso or 'unrecorded'}): +{escalation_pct:.1f}%"
            )

        overrun_caveat = (
            " Final contract completion cost and disbursement records are not present in this dataset, "
            "so this award-versus-estimate variance does not establish a final cost overrun."
            if escalation_kind == "award_vs_estimate_escalation"
            else ""
        )
        summary = (
            f"Public record anomaly on {tender_id} ({safe_buyer}, {jurisdiction}, {fiscal_year}) for "
            f"'{safe_cat}' ({unit_display}): {'; '.join(reasons)}. Computed difference "
            f"is INR {exposure_inr:,.0f}.{overrun_caveat}"
        )

        if price_outlier and escalation_flag:
            lead_label = (
                "Unit-Price Outlier & Award vs Estimate Variance"
                if escalation_kind == "award_vs_estimate_escalation"
                else "Unit-Price Outlier & Post-Award Cost Escalation"
            )
        elif price_outlier:
            lead_label = "Unit-Price Outlier"
        elif escalation_kind == "award_vs_estimate_escalation":
            lead_label = "Award vs Tender-Estimate Variance"
        else:
            lead_label = "Post-Award Cost Escalation"

        leads.append(
            AuditLeadCandidate(
                title=f"Procurement Red-Flag Lead: {lead_label} in {safe_cat} — {safe_buyer}",
                summary=summary,
                methodology_code="M4",
                entity_name=safe_buyer,
                jurisdiction=jurisdiction,
                fiscal_year=fiscal_year,
                fiscal_year_start=fiscal_year if fiscal_year != "Unspecified" else None,
                fiscal_year_end=fiscal_year if fiscal_year != "Unspecified" else None,
                source_locator=f"row={rec.get('source_row_index') or (idx + 1)}",
                amount_type=lead_amount_type,
                monetary_exposure_inr=round(exposure_inr, 2),
                severity=_severity_from_exposure(
                    exposure_inr,
                    high_signal=(price_ratio >= 2.25 or escalation_pct >= 35.0),
                ),
                anomaly_metrics={
                    "tender_id": tender_id,
                    "item_category": safe_cat,
                    "unit": unit_display,
                    "quantity": qty,
                    "unit_price_inr": round(unit_price, 2) if unit_price > 0 else None,
                    "peer_median_unit_price_inr": round(med_price, 2) if med_price > 0 else None,
                    "peer_mad_inr": round(mad_val, 2) if med_price > 0 else None,
                    "median_ratio": round(price_ratio, 3) if price_outlier or unit_price > 0 else None,
                    "mad_multiple": round(mad_multiple, 2) if price_outlier or unit_price > 0 else None,
                    "tender_estimate_inr": round(tender_est, 2) if tender_est > 0 else None,
                    "award_value_inr": round(awarded, 2) if awarded > 0 else None,
                    "awarded_value_inr": round(awarded, 2) if awarded > 0 else None,
                    "final_value_inr": round(final_val, 2) if has_final_val else None,
                    "tender_estimate_date": est_dt_iso,
                    "award_date": aw_dt_iso,
                    "final_cost_date": fin_dt_iso,
                    "escalation_pct": round(escalation_pct, 2),
                    "escalation_kind": escalation_kind,
                    "comparison_basis": comparison_basis,
                    "is_final_cost_overrun": is_final_cost_overrun,
                    "final_cost_data_available": has_final_val,
                    "amount_type": lead_amount_type,
                    "estimated_excess_inr": round(exposure_inr, 2),
                },
                rule_triggered="GeM/CPPP BOQ Benchmark & CVC Variation Guidelines (" + "; ".join(reasons) + ")",
                citations=[
                    _build_citation(
                        rec,
                        idx,
                        (
                            f"{tender_id} ({safe_cat}): tender_estimate=INR {tender_est:,.0f} "
                            f"({est_dt_iso or 'date N/A'}), award_value=INR {awarded:,.0f} "
                            f"({aw_dt_iso or 'date N/A'}), final_value="
                            + (f"INR {final_val:,.0f}" if has_final_val else "unrecorded")
                        ),
                    )
                ],
                benign_explanations=[
                    "Higher unit rates or award values relative to initial tender estimates may reflect outdated Schedule-of-Rates estimates, market inflation, or revised technical specifications.",
                    "Remote, hilly, or disaster-affected delivery locations can lawfully increase freight, raw material, and execution costs above baseline estimates.",
                    "Comparable scope, approved variation orders, and actual disbursement records are required before drawing any conclusion regarding final contract cost.",
                ],
                suggested_authority=safe_buyer,
            )
        )

    cleared_count = len(records) - len(flagged_indices)
    top_leads, total_detected = _cap_leads_deterministically(leads, ctx)
    lead_note = (
        f"top {len(top_leads)} of {total_detected} price/variance lead(s)"
        if total_detected > len(top_leads)
        else f"{len(top_leads)} price/variance lead(s)"
    )
    summary_text = (
        f"M4 scanned {len(records)} BOQ/contract items across {len(by_sku)} normalized item categories "
        f"(INR {total_audited:,.0f} audited): {lead_note}, {cleared_count} cleared record(s)."
    )
    return (
        top_leads,
        cleared_count,
        round(total_audited, 2),
        summary_text,
        {
            "unevaluable_count": 0,
            "amount_type": "award_vs_tender_estimate",
        },
    )
