"""Deterministic Corporate-Electoral-Procurement Proximity Disclosure Engine (M8)."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime
from typing import Any

from aiwatchdogs.connectors.loader import _build_csr_lookup, load_dataset, normalize_company_name
from aiwatchdogs.core.models import AuditLeadCandidate

_INR_CRORE = 10_000_000.0

_NEUTRAL_REPLACE_RE = re.compile(
    r"\b(?:scam(?:s|med|mer|mers)?|corrupt(?:ed|ing|ly|ion)?|brib(?:e|es|ed|ing|ery)|"
    r"fraud(?:s|ulent|ulently|ster|sters)?|kickback(?:s)?|guilty|stolen|rigged|"
    r"crook(?:s|ed)?|loot(?:s|ed|ing|er|ers)?|embezzl(?:e|es|ed|ing|ement))\b",
    re.IGNORECASE,
)


def _neutralize_text(text: str) -> str:
    return _NEUTRAL_REPLACE_RE.sub("governance-review", text)


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
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value).strip().split("T", 1)[0]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d/%b/%Y", "%d-%b-%Y"):
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


def _is_electoral_bond_ledger(records: list[dict[str, Any]]) -> bool:
    if not records:
        return False
    sample = records[0]
    eb_keys = {
        "purchaser_name",
        "Name of the Purchaser",
        "political_party_name",
        "Name of the Political Party",
        "bond_number",
        "Bond Number",
        "date_of_purchase",
        "Date of Purchase",
        "date_of_encashment",
        "Date of Encashment",
    }
    return bool(set(sample.keys()) & eb_keys)


def _run_electoral_bond_csr_disclosure(
    records: list[dict[str, Any]],
    ctx: dict[str, Any],
) -> tuple[list[AuditLeadCandidate], int, float, str]:
    """Strictly neutral SC-ordered SBI/ECI Electoral Bonds x MCA Top-100 CSR disclosure ledger analysis."""
    # Separate any inline CSR rows if caller passed a concatenated list, or load secondary_url if not yet joined
    eb_rows: list[dict[str, Any]] = []
    inline_csr_rows: list[dict[str, Any]] = []
    already_enriched = False

    for rec in records:
        if any(k in rec for k in ("purchaser_name", "Name of the Purchaser", "political_party_name", "Name of the Political Party", "bond_number", "Bond Number")):
            eb_rows.append(rec)
            if rec.get("cin") and (rec.get("csr_total_inr") is not None or rec.get("csr_company_name")):
                already_enriched = True
        elif any(k in rec for k in ("cin", "CIN", "csr_total", "company_name")):
            inline_csr_rows.append(rec)

    strict_csr: dict[str, dict[str, Any]] = {}
    core_csr: dict[str, dict[str, Any]] = {}
    if inline_csr_rows:
        strict_csr, core_csr = _build_csr_lookup(inline_csr_rows)
    elif isinstance(ctx.get("secondary_records"), list):
        strict_csr, core_csr = _build_csr_lookup(ctx["secondary_records"])
    elif not already_enriched:
        sec_ref = str(
            ctx.get("secondary_url") or ctx.get("secondary_dataset_url") or ctx.get("secondary_path") or ""
        ).strip()
        if sec_ref:
            sec_ds = load_dataset(sec_ref)
            strict_csr, core_csr = _build_csr_lookup(sec_ds.records)

    has_csr_mode = already_enriched or bool(strict_csr)
    min_standalone_inr = float(ctx.get("min_disclosure_inr", 50.0 * _INR_CRORE))

    # Aggregate bond transactions by normalized purchaser entity
    entities: dict[str, dict[str, Any]] = {}
    total_audited = 0.0

    for idx, rec in enumerate(eb_rows):
        amt = _to_float(rec.get("amount", rec.get("Denominations", 0)))
        total_audited += max(0.0, amt)
        purchaser = _norm_str(rec.get("purchaser_name") or rec.get("Name of the Purchaser"))
        party = _norm_str(rec.get("political_party_name") or rec.get("Name of the Political Party"), "Undisclosed / Unmatched Party")
        if not purchaser:
            # Redemption-only file (party-side ledger): group by political party
            purchaser = party
        strict_k, core_k = normalize_company_name(purchaser)
        if not strict_k:
            continue

        cin = _norm_str(rec.get("cin"))
        csr_name = _norm_str(rec.get("csr_company_name"))
        csr_total_inr = _to_float(rec.get("csr_total_inr"), 0.0)
        csr_rank = rec.get("csr_rank")
        if not cin and strict_csr:
            match = strict_csr.get(strict_k) or (core_csr.get(core_k) if len(core_k) >= 6 else None)
            if match:
                cin = match["cin"]
                csr_name = match["company_name"]
                csr_total_inr = match["csr_total_inr"]
                csr_rank = match.get("csr_rank")

        group_key = cin if cin else strict_k
        entry = entities.get(group_key)
        if entry is None:
            entry = {
                "display_name": csr_name or purchaser,
                "raw_purchaser": purchaser,
                "cin": cin,
                "csr_total_inr": csr_total_inr,
                "csr_rank": csr_rank,
                "eb_total_inr": 0.0,
                "bond_count": 0,
                "row_indices": [],
                "party_totals": defaultdict(float),
                "dates": [],
                "sample_bonds": [],
                "source_url": _norm_str(rec.get("source_url"), "https://www.eci.gov.in"),
                "secondary_source_url": _norm_str(rec.get("secondary_source_url"), "https://www.csr.gov.in"),
            }
            entities[group_key] = entry

        entry["eb_total_inr"] += max(0.0, amt)
        entry["bond_count"] += 1
        entry["row_indices"].append(idx)
        entry["party_totals"][party] += max(0.0, amt)
        dt = _parse_date(rec.get("date_of_purchase") or rec.get("Date of Purchase") or rec.get("date_of_encashment") or rec.get("Date of Encashment"))
        if dt:
            entry["dates"].append(dt)
        if len(entry["sample_bonds"]) < 5:
            bond_no = _norm_str(rec.get("bond_number") or rec.get("Bond Number"), f"row-{idx + 1}")
            prefix = _norm_str(rec.get("prefix") or rec.get("Prefix"))
            entry["sample_bonds"].append((idx, f"{prefix}{bond_no}", amt, party, dt))

    leads: list[AuditLeadCandidate] = []
    cleared_records = 0

    for entry in entities.values():
        eb_inr = entry["eb_total_inr"]
        cin = entry["cin"]
        csr_inr = entry["csr_total_inr"]

        if has_csr_mode:
            # Cross-disclosure mode: emit strictly factual leads for verified MCA CSR corporate overlaps
            if not cin or eb_inr <= 0:
                cleared_records += entry["bond_count"]
                continue
        else:
            # Standalone Electoral Bonds mode: summarize top high-value disclosed corporate purchasers
            if eb_inr < min_standalone_inr:
                cleared_records += entry["bond_count"]
                continue

        party_totals: dict[str, float] = entry["party_totals"]
        top_party, top_party_inr = max(party_totals.items(), key=lambda kv: (kv[1], kv[0]))
        top_party_share_pct = (top_party_inr / eb_inr * 100.0) if eb_inr > 0 else 0.0
        dates: list[date] = entry["dates"]
        date_span = f"{min(dates).isoformat()} to {max(dates).isoformat()}" if dates else "2019–2024"
        fy_span = f"FY {min(dates).year}-{str(max(dates).year)[-2:]}" if dates else "FY 2019-24"

        safe_company = _neutralize_text(entry["display_name"])
        safe_party = _neutralize_text(top_party)
        eb_to_csr_ratio = (eb_inr / csr_inr) if csr_inr > 0 else None

        if has_csr_mode:
            title = f"Disclosure / Proximity Lead: SC Electoral Bond & MCA CSR Registry Overlap — {safe_company}"
            csr_clause = (
                f"MCA National CSR Registry records CIN {cin} with published CSR outlay of "
                f"INR {csr_inr / _INR_CRORE:.2f} Cr"
                + (f" (Top-100 Rank #{entry['csr_rank']})" if entry.get("csr_rank") else "")
                + (f", placing disclosed bond purchases at {eb_to_csr_ratio:.2f}x annual CSR outlay. " if eb_to_csr_ratio is not None else ". ")
            )
            rule_triggered = (
                f"Supreme Court SBI/ECI Electoral Bond Disclosure x MCA CSR Registry Cross-Match "
                f"(CIN={cin}; EB=INR {eb_inr / _INR_CRORE:.2f} Cr; CSR=INR {csr_inr / _INR_CRORE:.2f} Cr)"
            )
        else:
            title = f"Disclosure / Proximity Lead: High-Value Statutory Electoral Bond Disclosure — {safe_company}"
            csr_clause = ""
            rule_triggered = (
                f"Supreme Court SBI/ECI Public Electoral Bond Disclosure Summary "
                f"(EB=INR {eb_inr / _INR_CRORE:.2f} Cr across {entry['bond_count']} bonds)"
            )

        summary = (
            f"Corporate-Electoral Public Disclosure summary for {safe_company}"
            f"{f' (CIN: {cin})' if cin else ''}: SBI/ECI statutory disclosures ({date_span}) record "
            f"{entry['bond_count']} electoral bond(s) totaling INR {eb_inr / _INR_CRORE:.2f} Cr across "
            f"{len(party_totals)} political recipient(s), with largest recipient {safe_party} receiving "
            f"{top_party_share_pct:.1f}% (INR {top_party_inr / _INR_CRORE:.2f} Cr). {csr_clause}"
            f"This entry summarizes factual public statutory disclosures for civic transparency and makes "
            f"no allegation of improper conduct, regulatory breach, or quid pro quo."
        )

        severity = "high" if eb_inr >= 100.0 * _INR_CRORE else "medium"
        citations = [
            {
                "record_id": f"{cin or safe_company}:{bno}",
                "row_id": r_idx + 1,
                "source_url": entry["source_url"],
                "excerpt": (
                    f"Bond {bno} ({safe_company}): INR {b_amt:,.0f} -> { _neutralize_text(b_party) } "
                    f"({b_dt.isoformat() if b_dt else 'date N/A'})"
                ),
            }
            for r_idx, bno, b_amt, b_party, b_dt in entry["sample_bonds"]
        ]

        leads.append(
            AuditLeadCandidate(
                title=title,
                summary=summary,
                methodology_code="M8",
                entity_name=safe_company,
                jurisdiction="Union of India",
                fiscal_year=fy_span,
                monetary_exposure_inr=round(eb_inr, 2),
                severity=severity,
                anomaly_metrics={
                    "cin": cin or "CIN-UNSPECIFIED",
                    "company_name": safe_company,
                    "electoral_disclosure_inr": round(eb_inr, 2),
                    "electoral_disclosure_crore": round(eb_inr / _INR_CRORE, 2),
                    "csr_total_inr": round(csr_inr, 2),
                    "csr_total_crore": round(csr_inr / _INR_CRORE, 2),
                    "eb_to_csr_ratio": round(eb_to_csr_ratio, 2) if eb_to_csr_ratio is not None else None,
                    "bond_count": entry["bond_count"],
                    "distinct_parties": len(party_totals),
                    "top_party_name": safe_party,
                    "top_party_share_pct": round(top_party_share_pct, 2),
                    "disclosure_only": True,
                },
                rule_triggered=rule_triggered,
                citations=citations,
                benign_explanations=[
                    "Electoral bond purchases disclosed under Supreme Court orders were lawful corporate political contributions made under the then-prevailing Electoral Bond Scheme notified by the Government of India.",
                    "Corporate Social Responsibility (CSR) filings under Section 135 of the Companies Act, 2013 reflect independent statutory social spending by India's largest listed companies and overlap naturally with major corporate taxpayers.",
                    "Public registry overlap between corporate CSR disclosures and statutory electoral disclosures is presented solely as a factual transparency ledger and does not imply any procurement favor or quid pro quo.",
                ],
                suggested_authority="Election Commission of India / Ministry of Corporate Affairs",
            )
        )

    sorted_leads = sorted(
        leads,
        key=lambda item: (-float(item.monetary_exposure_inr), item.entity_name, item.title),
    )
    total_detected = len(sorted_leads)
    max_leads = int(ctx.get("max_leads", 5))
    top_leads = sorted_leads[:max_leads] if (max_leads > 0 and total_detected > max_leads) else sorted_leads

    lead_note = (
        f"top {len(top_leads)} of {total_detected} neutral disclosure lead(s)"
        if total_detected > len(top_leads)
        else f"{len(top_leads)} neutral disclosure lead(s)"
    )
    summary_text = (
        f"M8 scanned {len(eb_rows)} electoral bond records across {len(entities)} entities "
        f"(INR {total_audited / _INR_CRORE:,.2f} Cr disclosed): {lead_note}, {cleared_records} cleared record(s)."
    )
    return top_leads, cleared_records, round(total_audited, 2), summary_text


def run_m8_corporate_proximity(
    records: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> tuple[list[AuditLeadCandidate], int, float, str]:
    """M8: Corporate-Electoral-Procurement Proximity Disclosure Lead (strictly neutral framing).

    Supports both:
    - Real SC-ordered SBI/ECI Electoral Bonds x MCA Top-100 CSR public disclosure ledger
    - Procurement corporate-proximity checks (<180d newly incorporated major winners, shared-DIN/address
      competing bidders, and temporal proximity between public electoral disclosures and major awards)
    """
    ctx = context or {}
    if _is_electoral_bond_ledger(records):
        return _run_electoral_bond_csr_disclosure(records, ctx)

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
        buyer = _neutralize_text(
            _norm_str(
                rec.get("buyer_name") or rec.get("entity_name") or rec.get("department"),
                "Public Procuring Authority",
            )
        )
        vendor = _neutralize_text(
            _norm_str(rec.get("vendor_name") or rec.get("supplier_name"), "Awarded Corporate Entity")
        )
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

    sorted_leads = sorted(
        leads,
        key=lambda item: (-float(item.monetary_exposure_inr), item.entity_name, item.title),
    )
    total_detected = len(sorted_leads)
    max_leads = int(ctx.get("max_leads", 5))
    top_leads = sorted_leads[:max_leads] if (max_leads > 0 and total_detected > max_leads) else sorted_leads

    summary_text = (
        f"M8 scanned {len(records)} corporate-procurement records (INR {total_audited:,.0f} audited): "
        f"{len(top_leads)} neutral disclosure/proximity lead(s), {cleared_count} cleared record(s)."
    )
    return top_leads, cleared_count, round(total_audited, 2), summary_text
