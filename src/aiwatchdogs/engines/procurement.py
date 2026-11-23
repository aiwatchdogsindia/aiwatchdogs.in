"""Deterministic Indian public procurement audit engines (M1, M2, M3, M4)."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from statistics import median
from typing import Any

from aiwatchdogs.core.models import AuditLeadCandidate

# GFR 2017 statutory ceilings (incorporating July 2024 PPD revisions)
GFR_THRESHOLDS: tuple[tuple[float, str, str], ...] = (
    (50_000.0, "INR 50,000", "GFR 2017 Rule 149 (GeM Direct Purchase ceiling)"),
    (250_000.0, "INR 2.5 Lakh", "GFR 2017 Rule 155 (Local Purchase Committee ceiling)"),
    (1_000_000.0, "INR 10 Lakh", "GFR 2017 Rule 149 (GeM L1 vs Mandatory Online Bidding/RA ceiling)"),
    (5_000_000.0, "INR 50 Lakh", "GFR 2017 Rule 161 (Limited vs Open Advertised Tender ceiling)"),
)


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


def _to_int(value: Any, default: int = 0) -> int:
    return int(round(_to_float(value, float(default))))


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value).strip()
    if "T" in raw:
        raw = raw.split("T", 1)[0]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
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


def _extract_bid_window_days(rec: dict[str, Any]) -> int | None:
    explicit = rec.get("bid_window_days")
    if explicit is not None and str(explicit).strip() != "":
        return _to_int(explicit)
    start = _parse_date(rec.get("tender_start_date") or rec.get("published_date"))
    end = _parse_date(rec.get("tender_end_date") or rec.get("closing_date"))
    if start and end:
        return max(0, (end - start).days)
    return None


def _build_citation(rec: dict[str, Any], idx: int, excerpt: str) -> dict[str, Any]:
    record_id = (
        rec.get("tender_id")
        or rec.get("ocid")
        or rec.get("po_id")
        or rec.get("contract_id")
        or f"row-{idx + 1}"
    )
    return {
        "record_id": str(record_id),
        "row_id": idx + 1,
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


def run_m1_single_bidder(
    records: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> tuple[list[AuditLeadCandidate], int, float, str]:
    """M1: Single-bidder share >35%, repeat single-bidder vendor >=3, shortened bid windows (<14d)."""
    ctx = context or {}
    min_tenders = int(ctx.get("min_tenders", 4))
    single_bid_share_threshold = float(ctx.get("single_bid_share_threshold", 0.35))
    short_window_limit = int(ctx.get("short_window_days", 14))

    total_audited = 0.0
    by_buyer: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)

    for idx, rec in enumerate(records):
        val = _to_float(rec.get("award_value_inr", rec.get("value_inr", rec.get("amount_inr", 0))))
        total_audited += max(0.0, val)
        buyer = _norm_str(
            rec.get("buyer_name") or rec.get("entity_name") or rec.get("department"),
            "Unspecified Public Buyer",
        )
        by_buyer[buyer].append((idx, rec))

    leads: list[AuditLeadCandidate] = []
    cleared_count = 0

    for buyer, indexed_rows in sorted(by_buyer.items()):
        competitive = [
            (idx, r)
            for idx, r in indexed_rows
            if _norm_key(r.get("procurement_method"), "open") not in {"proprietary", "direct_exempt"}
        ]
        if not competitive:
            cleared_count += len(indexed_rows)
            continue

        single_bid_rows: list[tuple[int, dict[str, Any]]] = []
        short_window_single_rows: list[tuple[int, dict[str, Any]]] = []
        vendor_single_rows: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)

        for idx, rec in competitive:
            bid_count = _to_int(
                rec.get("bid_count")
                if rec.get("bid_count") is not None
                else rec.get("num_bids", rec.get("number_of_tenderers", 0))
            )
            window_days = _extract_bid_window_days(rec)
            if bid_count == 1:
                single_bid_rows.append((idx, rec))
                vendor = _norm_str(
                    rec.get("vendor_name") or rec.get("supplier_name") or rec.get("awardee"),
                    "Unspecified Vendor",
                )
                vendor_single_rows[vendor].append((idx, rec))
                if window_days is not None and window_days < short_window_limit:
                    short_window_single_rows.append((idx, rec))

        total_comp = len(competitive)
        single_share = len(single_bid_rows) / total_comp if total_comp else 0.0
        repeat_vendors = {
            v: rows for v, rows in vendor_single_rows.items() if len(rows) >= 3
        }

        share_triggered = total_comp >= min_tenders and single_share > single_bid_share_threshold
        repeat_triggered = bool(repeat_vendors)
        short_window_triggered = len(short_window_single_rows) >= 2

        if not (share_triggered or repeat_triggered or short_window_triggered):
            cleared_count += len(indexed_rows)
            continue

        exposure_inr = sum(
            _to_float(r.get("award_value_inr", r.get("value_inr", r.get("amount_inr", 0))))
            for _, r in single_bid_rows
        )
        sample_rec = single_bid_rows[0][1] if single_bid_rows else competitive[0][1]
        jurisdiction = _norm_str(sample_rec.get("jurisdiction"), "Union of India")
        fiscal_year = _norm_str(sample_rec.get("fiscal_year"), "FY 2024-25")

        rules_hit: list[str] = []
        if share_triggered:
            rules_hit.append(
                f"single_bid_share={single_share * 100:.1f}% > {single_bid_share_threshold * 100:.0f}%"
            )
        if repeat_triggered:
            top_repeat = max(repeat_vendors.items(), key=lambda kv: len(kv[1]))
            rules_hit.append(
                f"repeat_single_bid_vendor={top_repeat[0]} ({len(top_repeat[1])} awards)"
            )
        if short_window_triggered:
            rules_hit.append(
                f"short_bid_window<{short_window_limit}d_with_single_bid={len(short_window_single_rows)}"
            )

        citations = [
            _build_citation(
                rec,
                idx,
                (
                    f"Tender {rec.get('tender_id') or rec.get('ocid')}: "
                    f"bids={rec.get('bid_count', rec.get('number_of_tenderers', 1))}, "
                    f"window={_extract_bid_window_days(rec)}d, "
                    f"award=INR {_to_float(rec.get('award_value_inr', rec.get('value_inr', 0))):,.0f}"
                ),
            )
            for idx, rec in single_bid_rows[:5]
        ]

        summary = (
            f"Public record anomaly observed in {buyer} ({jurisdiction}, {fiscal_year}): "
            f"{len(single_bid_rows)} of {total_comp} competitive tenders ({single_share * 100:.1f}%) "
            f"resulted in single-bidder awards totaling INR {exposure_inr:,.0f}, including "
            f"{len(short_window_single_rows)} single-bid tender(s) with bid windows under "
            f"{short_window_limit} days. Triggers noted under CVC Single-Tender Guidelines and "
            f"GFR 2017 Rule 166: {'; '.join(rules_hit)}."
        )

        high_signal = single_share >= 0.50 or bool(repeat_vendors) or len(short_window_single_rows) >= 3
        leads.append(
            AuditLeadCandidate(
                title=f"Procurement Red-Flag Lead: Single-Bidder Concentration ({single_share * 100:.1f}%) — {buyer}",
                summary=summary,
                methodology_code="M1",
                entity_name=buyer,
                jurisdiction=jurisdiction,
                fiscal_year=fiscal_year,
                monetary_exposure_inr=round(exposure_inr, 2),
                severity=_severity_from_exposure(exposure_inr, high_signal=high_signal),
                anomaly_metrics={
                    "competitive_tenders": total_comp,
                    "single_bid_count": len(single_bid_rows),
                    "single_bid_share_pct": round(single_share * 100.0, 2),
                    "short_window_single_bid_count": len(short_window_single_rows),
                    "repeat_single_bid_vendors": {
                        vendor: len(rows) for vendor, rows in repeat_vendors.items()
                    },
                    "flagged_value_inr": round(exposure_inr, 2),
                },
                rule_triggered="CVC Single-Tender Guidelines & GFR 2017 Rule 166 (" + "; ".join(rules_hit) + ")",
                citations=citations,
                benign_explanations=[
                    "Specialized proprietary equipment or OEM spares may lawfully qualify for single-tender procurement under GFR 2017 Rule 166 with recorded competent authority justification.",
                    "Shortened bid windows can reflect urgent public-health, flood-relief, or emergency infrastructure restoration timelines approved by the department.",
                    "A single responsive bid may occur after open advertisement if other participating bidders failed routine technical or statutory eligibility checks.",
                ],
                suggested_authority=buyer,
            )
        )

    summary_text = (
        f"M1 scanned {len(records)} procurement records across {len(by_buyer)} buyers "
        f"(INR {total_audited:,.0f} audited): {len(leads)} lead(s), {cleared_count} cleared record(s)."
    )
    return leads, cleared_count, round(total_audited, 2), summary_text


def run_m2_split_tendering(
    records: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> tuple[list[AuditLeadCandidate], int, float, str]:
    """M2: Split-tendering / GFR 2017 threshold hugging (85%-99.9% of INR 50k/2.5L/10L/50L; >=3 POs in 60d)."""
    ctx = context or {}
    lower_ratio = float(ctx.get("lower_ratio", 0.85))
    upper_ratio = float(ctx.get("upper_ratio", 0.999))
    window_days_limit = int(ctx.get("window_days", 60))
    min_cluster_count = int(ctx.get("min_cluster_count", 3))

    total_audited = 0.0
    grouped: dict[tuple[str, str], list[tuple[int, dict[str, Any], float, date | None]]] = defaultdict(list)

    for idx, rec in enumerate(records):
        val = _to_float(rec.get("award_value_inr", rec.get("value_inr", rec.get("amount_inr", 0))))
        total_audited += max(0.0, val)
        buyer = _norm_str(
            rec.get("buyer_name") or rec.get("entity_name") or rec.get("department"),
            "Unspecified Public Buyer",
        )
        category = _norm_str(rec.get("item_category") or rec.get("category"), "General Goods & Services")
        award_dt = _parse_date(rec.get("award_date") or rec.get("po_date") or rec.get("date"))
        grouped[(buyer, category)].append((idx, rec, val, award_dt))

    leads: list[AuditLeadCandidate] = []
    flagged_record_indices: set[int] = set()

    for (buyer, category), rows in sorted(grouped.items()):
        for threshold_inr, threshold_label, gfr_anchor in GFR_THRESHOLDS:
            low_bound = lower_ratio * threshold_inr
            high_bound = upper_ratio * threshold_inr

            hugging = [
                item for item in rows if low_bound <= item[2] <= high_bound
            ]
            if len(hugging) < min_cluster_count:
                continue

            # Sort chronologically (putting rows with dates first, preserving index order otherwise).
            hugging.sort(key=lambda item: (item[3] or date(1970, 1, 1), item[0]))

            best_cluster: list[tuple[int, dict[str, Any], float, date | None]] = []
            best_sum = 0.0
            for start_i in range(len(hugging)):
                start_dt = hugging[start_i][3]
                window: list[tuple[int, dict[str, Any], float, date | None]] = []
                for end_i in range(start_i, len(hugging)):
                    end_dt = hugging[end_i][3]
                    if start_dt and end_dt and (end_dt - start_dt).days > window_days_limit:
                        break
                    window.append(hugging[end_i])
                window_sum = sum(x[2] for x in window)
                if len(window) >= min_cluster_count and window_sum > threshold_inr:
                    if window_sum > best_sum:
                        best_sum = window_sum
                        best_cluster = window

            if not best_cluster:
                continue

            for item in best_cluster:
                flagged_record_indices.add(item[0])

            dates = [x[3] for x in best_cluster if x[3] is not None]
            span_days = (max(dates) - min(dates)).days if len(dates) >= 2 else 0
            ratios = [round((x[2] / threshold_inr) * 100.0, 2) for x in best_cluster]
            sample_rec = best_cluster[0][1]
            jurisdiction = _norm_str(sample_rec.get("jurisdiction"), "Union of India")
            fiscal_year = _norm_str(sample_rec.get("fiscal_year"), "FY 2024-25")

            citations = [
                _build_citation(
                    rec,
                    idx,
                    (
                        f"PO {rec.get('po_id') or rec.get('tender_id')}: "
                        f"INR {val:,.0f} ({(val / threshold_inr) * 100:.1f}% of {threshold_label}) "
                        f"on {dt.isoformat() if dt else 'date N/A'}"
                    ),
                )
                for idx, rec, val, dt in best_cluster[:6]
            ]

            summary = (
                f"Public record anomaly in {buyer} ({jurisdiction}, {fiscal_year}) for "
                f"'{category}': {len(best_cluster)} purchase orders within {span_days} days each "
                f"clustered between {min(ratios):.1f}% and {max(ratios):.1f}% of the {threshold_label} "
                f"statutory threshold ({gfr_anchor}), aggregating INR {best_sum:,.0f} "
                f"({best_sum / threshold_inr:.2f}x the ceiling)."
            )

            leads.append(
                AuditLeadCandidate(
                    title=f"Procurement Red-Flag Lead: {len(best_cluster)} POs Clustering Below {threshold_label} — {buyer}",
                    summary=summary,
                    methodology_code="M2",
                    entity_name=buyer,
                    jurisdiction=jurisdiction,
                    fiscal_year=fiscal_year,
                    monetary_exposure_inr=round(best_sum, 2),
                    severity=_severity_from_exposure(best_sum, high_signal=len(best_cluster) >= 4),
                    anomaly_metrics={
                        "item_category": category,
                        "threshold_inr": threshold_inr,
                        "threshold_label": threshold_label,
                        "cluster_po_count": len(best_cluster),
                        "cluster_sum_inr": round(best_sum, 2),
                        "span_days": span_days,
                        "min_threshold_pct": min(ratios),
                        "max_threshold_pct": max(ratios),
                        "threshold_multiple": round(best_sum / threshold_inr, 2),
                    },
                    rule_triggered=f"{gfr_anchor}; CVC Splitting Circulars (>=3 POs in 85%-99.9% band within 60d summing > threshold)",
                    citations=citations,
                    benign_explanations=[
                        "Staggered budget allocations or phased sub-office indents can lawfully produce separate purchase orders across a 60-day period.",
                        "Emergent operational requirements or distinct delivery locations may necessitate independent call-offs under approved rate contracts.",
                        "Standardized unit pack sizes or GeM lot limits can naturally price individual orders just below administrative ceilings.",
                    ],
                    suggested_authority=buyer,
                )
            )

    cleared_count = len(records) - len(flagged_record_indices)
    summary_text = (
        f"M2 scanned {len(records)} purchase records across {len(grouped)} buyer-category groups "
        f"(INR {total_audited:,.0f} audited): {len(leads)} threshold-hugging lead(s), "
        f"{cleared_count} cleared record(s)."
    )
    return leads, cleared_count, round(total_audited, 2), summary_text


def run_m3_vendor_concentration(
    records: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> tuple[list[AuditLeadCandidate], int, float, str]:
    """M3: Repeat-winner vendor HHI > 0.35, top vendor share > 40%, or narrow bid-spread clustering."""
    ctx = context or {}
    hhi_threshold = float(ctx.get("hhi_threshold", 0.35))
    share_threshold = float(ctx.get("share_threshold", 0.40))
    min_group_tenders = int(ctx.get("min_group_tenders", 4))

    total_audited = 0.0
    grouped: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = defaultdict(list)

    for idx, rec in enumerate(records):
        val = _to_float(rec.get("award_value_inr", rec.get("value_inr", rec.get("l1_bid_inr", 0))))
        total_audited += max(0.0, val)
        buyer = _norm_str(
            rec.get("buyer_name") or rec.get("entity_name") or rec.get("department"),
            "Unspecified Public Buyer",
        )
        category = _norm_str(rec.get("item_category") or rec.get("category"), "General Procurement")
        grouped[(buyer, category)].append((idx, rec))

    leads: list[AuditLeadCandidate] = []
    cleared_count = 0

    for (buyer, category), rows in sorted(grouped.items()):
        vendor_totals: dict[str, float] = defaultdict(float)
        vendor_counts: dict[str, int] = defaultdict(int)
        group_value = 0.0

        spread_values: list[float] = []
        narrow_spread_rows: list[tuple[int, dict[str, Any], float]] = []

        for idx, rec in rows:
            val = _to_float(rec.get("award_value_inr", rec.get("value_inr", rec.get("l1_bid_inr", 0))))
            vendor = _norm_str(
                rec.get("vendor_name") or rec.get("supplier_name") or rec.get("awardee"),
                "Unspecified Vendor",
            )
            if val > 0:
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

        if group_value <= 0:
            cleared_count += len(rows)
            continue

        shares = {v: amt / group_value for v, amt in vendor_totals.items()}
        hhi = sum(s * s for s in shares.values())
        top_vendor, top_share = max(shares.items(), key=lambda kv: kv[1])
        narrow_ratio = (len(narrow_spread_rows) / len(spread_values)) if spread_values else 0.0

        hhi_triggered = len(rows) >= min_group_tenders and hhi > hhi_threshold
        share_triggered = len(rows) >= min_group_tenders and top_share > share_threshold
        narrow_triggered = len(narrow_spread_rows) >= 3 and narrow_ratio >= 0.60

        if not (hhi_triggered or share_triggered or narrow_triggered):
            cleared_count += len(rows)
            continue

        rules_hit: list[str] = []
        if hhi_triggered:
            rules_hit.append(f"HHI={hhi:.3f} > {hhi_threshold:.2f}")
        if share_triggered:
            rules_hit.append(f"top_vendor_share={top_share * 100:.1f}% > {share_threshold * 100:.0f}%")
        if narrow_triggered:
            avg_spread = sum(x[2] for x in narrow_spread_rows) / len(narrow_spread_rows)
            rules_hit.append(
                f"narrow_l2_l1_spread={len(narrow_spread_rows)}/{len(spread_values)} tenders (avg {avg_spread:.2f}%)"
            )

        sample_rec = rows[0][1]
        jurisdiction = _norm_str(sample_rec.get("jurisdiction"), "Union of India")
        fiscal_year = _norm_str(sample_rec.get("fiscal_year"), "FY 2024-25")
        exposure_inr = vendor_totals[top_vendor] if (hhi_triggered or share_triggered) else group_value

        citations = [
            _build_citation(
                rec,
                idx,
                (
                    f"Contract {rec.get('tender_id') or rec.get('contract_id')}: "
                    f"vendor={rec.get('vendor_name')}, "
                    f"award=INR {_to_float(rec.get('award_value_inr', rec.get('l1_bid_inr', 0))):,.0f}"
                ),
            )
            for idx, rec in rows[:5]
        ]

        summary = (
            f"Statistical divergence in vendor award concentration for {buyer} — '{category}' "
            f"({jurisdiction}, {fiscal_year}): across {len(rows)} tenders totaling "
            f"INR {group_value:,.0f}, HHI reached {hhi:.3f} with {top_vendor} winning "
            f"{top_share * 100:.1f}% ({vendor_counts[top_vendor]} contracts, INR {vendor_totals[top_vendor]:,.0f}). "
            f"Signals noted under CCI Bid-Rigging & OECD Procurement Indicators: {'; '.join(rules_hit)}."
        )

        leads.append(
            AuditLeadCandidate(
                title=f"Statistical Divergence: Vendor Concentration (HHI {hhi:.2f}) in {category} — {buyer}",
                summary=summary,
                methodology_code="M3",
                entity_name=buyer,
                jurisdiction=jurisdiction,
                fiscal_year=fiscal_year,
                monetary_exposure_inr=round(exposure_inr, 2),
                severity=_severity_from_exposure(exposure_inr, high_signal=(hhi >= 0.50 or narrow_triggered)),
                anomaly_metrics={
                    "item_category": category,
                    "tender_count": len(rows),
                    "distinct_vendors": len(vendor_totals),
                    "hhi": round(hhi, 4),
                    "top_vendor_name": top_vendor,
                    "top_vendor_share_pct": round(top_share * 100.0, 2),
                    "top_vendor_value_inr": round(vendor_totals[top_vendor], 2),
                    "narrow_spread_count": len(narrow_spread_rows),
                    "narrow_spread_ratio_pct": round(narrow_ratio * 100.0, 2),
                    "group_total_value_inr": round(group_value, 2),
                },
                rule_triggered="CCI Bid-Rigging Indicators & OECD Concentration Metrics (" + "; ".join(rules_hit) + ")",
                citations=citations,
                benign_explanations=[
                    "A niche technical domain or regional market may have only a small pool of qualified vendors capable of meeting statutory performance criteria.",
                    "Repeated awards to one supplier may reflect genuine price competitiveness or call-offs under a lawfully bid multi-year rate contract.",
                    "Narrow L1-L2 bid spreads can also arise when bidders independently benchmark against transparent GeM catalog prices or published PWD Schedule of Rates.",
                ],
                suggested_authority=buyer,
            )
        )

    summary_text = (
        f"M3 scanned {len(records)} contract records across {len(grouped)} buyer-category groups "
        f"(INR {total_audited:,.0f} audited): {len(leads)} concentration/spread lead(s), "
        f"{cleared_count} cleared record(s)."
    )
    return leads, cleared_count, round(total_audited, 2), summary_text


def run_m4_unit_price_outliers(
    records: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> tuple[list[AuditLeadCandidate], int, float, str]:
    """M4: Unit-price outliers (>2.5 MAD or >1.75x peer median) & >20% post-award escalation."""
    ctx = context or {}
    mad_cutoff = float(ctx.get("mad_cutoff", 2.5))
    median_ratio_cutoff = float(ctx.get("median_ratio_cutoff", 1.75))
    escalation_cutoff_pct = float(ctx.get("escalation_cutoff_pct", 20.0))

    total_audited = 0.0
    by_sku: dict[tuple[str, str], list[tuple[int, dict[str, Any], float]]] = defaultdict(list)

    for idx, rec in enumerate(records):
        unit_price = _to_float(rec.get("unit_price_inr", 0))
        qty = _to_float(rec.get("quantity", 1), 1.0)
        awarded = _to_float(rec.get("awarded_value_inr", rec.get("award_value_inr", unit_price * qty)))
        final_val = _to_float(rec.get("final_value_inr", rec.get("revised_value_inr", awarded)))
        total_audited += max(awarded, final_val, 0.0)

        if unit_price > 0:
            cat = _norm_key(rec.get("item_category") or rec.get("sku_category") or rec.get("item_name"), "item")
            unit = _norm_key(rec.get("unit"), "unit")
            by_sku[(cat, unit)].append((idx, rec, unit_price))

    # Precompute peer median & MAD per (category, unit)
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
        awarded = _to_float(rec.get("awarded_value_inr", rec.get("award_value_inr", unit_price * qty)))
        final_val = _to_float(rec.get("final_value_inr", rec.get("revised_value_inr", 0)))

        cat_display = _norm_str(
            rec.get("item_category") or rec.get("sku_category") or rec.get("item_name"),
            "Standard Item",
        )
        unit_display = _norm_str(rec.get("unit"), "unit")
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
                else:
                    mad_multiple = 0.0
                if (peer_n >= 4 and mad_val > 0 and mad_multiple > mad_cutoff and price_ratio > 1.35) or (
                    price_ratio > median_ratio_cutoff
                ):
                    price_outlier = True
                    excess_price_inr = max(0.0, (unit_price - ref_median) * qty)

        escalation_flag = False
        escalation_pct = 0.0
        escalation_inr = 0.0
        if awarded > 0 and final_val > awarded:
            escalation_inr = final_val - awarded
            escalation_pct = (escalation_inr / awarded) * 100.0
            if escalation_pct > escalation_cutoff_pct:
                escalation_flag = True

        if not (price_outlier or escalation_flag):
            continue

        flagged_indices.add(idx)
        buyer = _norm_str(
            rec.get("buyer_name") or rec.get("entity_name") or rec.get("department"),
            "Unspecified Public Buyer",
        )
        jurisdiction = _norm_str(rec.get("jurisdiction"), "Union of India")
        fiscal_year = _norm_str(rec.get("fiscal_year"), "FY 2024-25")
        tender_id = _norm_str(rec.get("tender_id") or rec.get("contract_id"), f"BOQ-{idx + 1}")
        exposure_inr = max(excess_price_inr, escalation_inr, 0.0)

        reasons: list[str] = []
        if price_outlier:
            reasons.append(
                f"unit_price=INR {unit_price:,.2f} vs peer_median=INR {med_price:,.2f} "
                f"({price_ratio:.2f}x median, {mad_multiple:.2f} MAD)"
            )
        if escalation_flag:
            reasons.append(
                f"post_award_escalation=+{escalation_pct:.1f}% (INR {awarded:,.0f} -> INR {final_val:,.0f})"
            )

        summary = (
            f"Public record anomaly on {tender_id} ({buyer}, {jurisdiction}, {fiscal_year}) for "
            f"'{cat_display}' ({unit_display}): {'; '.join(reasons)}. Estimated benchmark variance "
            f"is INR {exposure_inr:,.0f}."
        )

        lead_label = (
            "Unit-Price Outlier & Cost Escalation"
            if (price_outlier and escalation_flag)
            else ("Unit-Price Outlier" if price_outlier else "Post-Award Cost Escalation")
        )

        leads.append(
            AuditLeadCandidate(
                title=f"Procurement Red-Flag Lead: {lead_label} in {cat_display} — {buyer}",
                summary=summary,
                methodology_code="M4",
                entity_name=buyer,
                jurisdiction=jurisdiction,
                fiscal_year=fiscal_year,
                monetary_exposure_inr=round(exposure_inr, 2),
                severity=_severity_from_exposure(
                    exposure_inr,
                    high_signal=(price_ratio >= 2.25 or escalation_pct >= 35.0),
                ),
                anomaly_metrics={
                    "tender_id": tender_id,
                    "item_category": cat_display,
                    "unit": unit_display,
                    "quantity": qty,
                    "unit_price_inr": round(unit_price, 2),
                    "peer_median_unit_price_inr": round(med_price, 2),
                    "peer_mad_inr": round(mad_val, 2),
                    "median_ratio": round(price_ratio, 3),
                    "mad_multiple": round(mad_multiple, 2),
                    "awarded_value_inr": round(awarded, 2),
                    "final_value_inr": round(final_val, 2),
                    "escalation_pct": round(escalation_pct, 2),
                    "estimated_excess_inr": round(exposure_inr, 2),
                },
                rule_triggered="GeM/CPPP BOQ Benchmark & CVC Variation Guidelines (" + "; ".join(reasons) + ")",
                citations=[
                    _build_citation(
                        rec,
                        idx,
                        f"{tender_id} ({cat_display}): unit=INR {unit_price:,.2f}, awarded=INR {awarded:,.0f}, final=INR {final_val:,.0f}",
                    )
                ],
                benign_explanations=[
                    "Higher unit rates may include superior technical specifications, multi-year comprehensive warranty, AMC, installation, or operator training not present in baseline peer contracts.",
                    "Remote, hilly, or disaster-affected delivery locations can lawfully increase freight and installation unit prices.",
                    "Post-award variations above 20% may stem from duly approved scope expansion, site-condition changes, or statutory GST/price-variation clauses.",
                ],
                suggested_authority=buyer,
            )
        )

    cleared_count = len(records) - len(flagged_indices)
    summary_text = (
        f"M4 scanned {len(records)} BOQ/contract items across {len(by_sku)} normalized item categories "
        f"(INR {total_audited:,.0f} audited): {len(leads)} price/escalation lead(s), "
        f"{cleared_count} cleared record(s)."
    )
    return leads, cleared_count, round(total_audited, 2), summary_text
