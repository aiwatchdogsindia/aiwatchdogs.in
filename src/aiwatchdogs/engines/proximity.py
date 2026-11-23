"""Deterministic Corporate-Electoral-Procurement Proximity Disclosure Engine (M8)."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any

from aiwatchdogs.core.models import AuditLeadCandidate


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


def _norm_str(value: Any, fallback: str = "") -> str:
    text = str(value).strip() if value is not None else ""
    return text if text else fallback


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    raw = str(value).strip().split("T", 1)[0]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _find_bidder_overlaps(competing_bidders: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """Return (shared_dins, shared_addresses) across distinct bidding entities on one tender."""
    din_to_vendors: dict[str, set[str]] = defaultdict(set)
    addr_to_vendors: dict[str, set[str]] = defaultdict(set)

    for bidder in competing_bidders:
        vname = _norm_str(bidder.get("vendor_name") or bidder.get("name") or bidder.get("cin"), "Bidder")
        for din in _as_list(bidder.get("dins") or bidder.get("director_dins")):
            din_to_vendors[din].add(vname)
        addr = _norm_str(bidder.get("registered_address")).lower()
        if addr:
            addr_to_vendors[addr].add(vname)

    shared_dins = sorted(din for din, vendors in din_to_vendors.items() if len(vendors) >= 2)
    shared_addrs = sorted(addr for addr, vendors in addr_to_vendors.items() if len(vendors) >= 2)
    return shared_dins, shared_addrs


def run_m8_corporate_proximity(
    records: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> tuple[list[AuditLeadCandidate], int, float, str]:
    """M8: Corporate-Electoral-Procurement Proximity Disclosure Lead (strictly neutral framing).

    Flags structural public-record governance disclosures:
    - Newly incorporated entity (<180 days old at award) winning a major contract (>= INR 50 Lakh)
    - Shared Director Identification Numbers (DINs) or registered address across competing bidders
    - Temporal proximity (<=180 days) between public electoral disclosure and major award (>= INR 5 Cr)
    """
    ctx = context or {}
    new_entity_days_cutoff = int(ctx.get("new_entity_days", 180))
    major_contract_inr = float(ctx.get("major_contract_inr", 5_000_000.0))
    electoral_award_cutoff = float(ctx.get("electoral_award_cutoff_inr", 50_000_000.0))

    leads: list[AuditLeadCandidate] = []
    cleared_count = 0
    total_audited = 0.0

    # Also detect flat-row shared-DIN overlap when multiple records share the same tender_id.
    tender_bidders: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in records:
        tid = _norm_str(rec.get("tender_id"))
        if tid and (rec.get("director_dins") or rec.get("dins") or rec.get("registered_address")):
            tender_bidders[tid].append(
                {
                    "vendor_name": _norm_str(rec.get("vendor_name") or rec.get("cin"), "Bidder"),
                    "dins": _as_list(rec.get("director_dins") or rec.get("dins")),
                    "registered_address": _norm_str(rec.get("registered_address")),
                }
            )

    seen_overlap_tenders: set[str] = set()

    for idx, rec in enumerate(records):
        award_val = _to_float(rec.get("award_value_inr", rec.get("value_inr", 0)))
        total_audited += max(0.0, award_val)

        tender_id = _norm_str(rec.get("tender_id"), f"PROX-{idx + 1}")
        buyer = _norm_str(
            rec.get("buyer_name") or rec.get("entity_name") or rec.get("department"),
            "Public Procuring Authority",
        )
        vendor = _norm_str(rec.get("vendor_name") or rec.get("supplier_name"), "Awarded Corporate Entity")
        cin = _norm_str(rec.get("cin"), "CIN-UNSPECIFIED")
        jurisdiction = _norm_str(rec.get("jurisdiction"), "Union of India")
        fiscal_year = _norm_str(rec.get("fiscal_year"), "FY 2024-25")

        inc_dt = _parse_date(rec.get("incorporation_date"))
        award_dt = _parse_date(rec.get("award_date"))
        if rec.get("company_age_days") not in (None, ""):
            company_age_days: int | None = int(round(_to_float(rec.get("company_age_days"))))
        elif inc_dt and award_dt:
            company_age_days = max(0, (award_dt - inc_dt).days)
        else:
            company_age_days = None

        # Check 8A: Newly incorporated entity winning major contract
        new_entity_flag = (
            company_age_days is not None
            and company_age_days < new_entity_days_cutoff
            and award_val >= major_contract_inr
        )

        # Check 8B: Shared DINs or registered address across competing bidders on the same tender
        bidders_list: list[dict[str, Any]] = []
        if isinstance(rec.get("competing_bidders"), list):
            bidders_list.extend(rec["competing_bidders"])
        elif tender_id in tender_bidders and len(tender_bidders[tender_id]) >= 2:
            bidders_list.extend(tender_bidders[tender_id])

        shared_dins, shared_addrs = _find_bidder_overlaps(bidders_list)
        overlap_flag = bool(shared_dins or shared_addrs)
        if overlap_flag and tender_id in seen_overlap_tenders:
            # Already emitted overlap for this tender_id from another flat row.
            overlap_flag = False
        elif overlap_flag:
            seen_overlap_tenders.add(tender_id)

        # Check 8C: Public electoral disclosure temporal proximity (strictly disclosure note)
        electoral_inr = _to_float(
            rec.get("electoral_bond_inr", rec.get("political_disclosure_inr", 0))
        )
        disc_dt = _parse_date(rec.get("disclosure_date") or rec.get("bond_purchase_date"))
        days_gap: int | None = None
        if rec.get("days_between_disclosure_and_award") not in (None, ""):
            days_gap = abs(int(round(_to_float(rec.get("days_between_disclosure_and_award")))))
        elif disc_dt and award_dt:
            days_gap = abs((award_dt - disc_dt).days)

        disclosure_proximity_flag = (
            electoral_inr > 0
            and award_val >= electoral_award_cutoff
            and (days_gap is None or days_gap <= 180)
        )

        if not (new_entity_flag or overlap_flag or disclosure_proximity_flag):
            cleared_count += 1
            continue

        signals: list[str] = []
        if new_entity_flag:
            signals.append(
                f"vendor_incorporated_{company_age_days}d_before_award (<{new_entity_days_cutoff}d) for INR {award_val:,.0f}"
            )
        if overlap_flag:
            overlap_parts = []
            if shared_dins:
                overlap_parts.append(f"shared_DINs={','.join(shared_dins)}")
            if shared_addrs:
                overlap_parts.append(f"shared_registered_address_count={len(shared_addrs)}")
            signals.append("competing_bidder_overlap(" + "; ".join(overlap_parts) + ")")
        if disclosure_proximity_flag:
            signals.append(
                f"public_electoral_disclosure=INR {electoral_inr:,.0f}_within_{days_gap if days_gap is not None else 'N/A'}d_of_award"
            )

        severity = "high" if (len(signals) >= 2 or overlap_flag or award_val >= 100_000_000) else "medium"
        summary = (
            f"Corporate-Procurement Disclosure / Proximity Lead on tender {tender_id} ({buyer}, "
            f"{jurisdiction}, {fiscal_year}) involving {vendor} ({cin}, award INR {award_val:,.0f}). "
            f"Public registry cross-check notes structural disclosure signal(s): {'; '.join(signals)}. "
            f"This entry records factual public registry overlap for transparent eligibility verification "
            f"and makes no allegation of improper conduct or quid pro quo."
        )

        leads.append(
            AuditLeadCandidate(
                title=f"Disclosure / Proximity Lead: Corporate Registry Signal on {tender_id} — {buyer}",
                summary=summary,
                methodology_code="M8",
                entity_name=buyer,
                jurisdiction=jurisdiction,
                fiscal_year=fiscal_year,
                monetary_exposure_inr=round(award_val, 2),
                severity=severity,
                anomaly_metrics={
                    "tender_id": tender_id,
                    "vendor_name": vendor,
                    "cin": cin,
                    "award_value_inr": round(award_val, 2),
                    "company_age_days": company_age_days,
                    "shared_dins": shared_dins,
                    "shared_address_count": len(shared_addrs),
                    "electoral_disclosure_inr": round(electoral_inr, 2),
                    "days_between_disclosure_and_award": days_gap,
                    "disclosure_only": True,
                },
                rule_triggered="MCA21 / ECI / Procurement Governance Disclosure Check (" + "; ".join(signals) + ")",
                citations=[
                    {
                        "record_id": tender_id,
                        "tender_id": tender_id,
                        "row_id": idx + 1,
                        "source_url": _norm_str(rec.get("source_url"), "https://mca.gov.in"),
                        "excerpt": f"Tender {tender_id} ({vendor}, {cin}): award=INR {award_val:,.0f}; signals={'; '.join(signals)}",
                    }
                ],
                benign_explanations=[
                    "A newly incorporated company may be a lawful Special Purpose Vehicle (SPV) or joint-venture subsidiary formed by established infrastructure parents meeting bid eligibility norms.",
                    "Corporate political donations disclosed through statutory electoral mechanisms are independent public acts and do not by themselves imply any link to procurement evaluation.",
                    "Shared directors across group entities or co-located corporate service offices may reflect routine corporate structures previously disclosed in conflict-of-interest filings.",
                ],
                suggested_authority=buyer,
            )
        )

    summary_text = (
        f"M8 scanned {len(records)} corporate-procurement records (INR {total_audited:,.0f} audited): "
        f"{len(leads)} neutral disclosure/proximity lead(s), {cleared_count} cleared record(s)."
    )
    return leads, cleared_count, round(total_audited, 2), summary_text
