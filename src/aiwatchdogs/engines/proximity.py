"""Deterministic Corporate-Electoral-Procurement Proximity Disclosure Engine (M8)."""

from __future__ import annotations

import math
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
    if value is None or value == "" or isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    text = str(value).strip().replace(",", "").replace("₹", "").replace("INR", "").strip()
    try:
        parsed = float(text)
    except ValueError:
        return default
    return parsed if math.isfinite(parsed) else default


def _safe_int(value: Any, default: int = 0) -> int:
    flt = _to_float(value, float(default))
    if not math.isfinite(flt):
        return default
    try:
        return int(round(flt))
    except OverflowError:
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
) -> tuple[list[AuditLeadCandidate], int, float, str, dict[str, Any]]:
    """Strictly neutral SC-ordered SBI/ECI Electoral Bonds x MCA Top-100 CSR disclosure ledger analysis.

    - Never copies a CSR record's CIN onto an electoral bond row as an "exact CIN match" without
      a corroborating primary identifier.
    - Explicitly publishes ``join_basis`` ("normalized_name" vs "exact_cin"), ambiguity metadata,
      separate source records, and describes monetary amounts as ``disclosed_bond_value``.
    """
    eb_rows: list[dict[str, Any]] = []
    inline_csr_rows: list[dict[str, Any]] = []
    already_joined = False

    for rec in records:
        if any(
            k in rec
            for k in (
                "purchaser_name",
                "Name of the Purchaser",
                "political_party_name",
                "Name of the Political Party",
                "bond_number",
                "Bond Number",
            )
        ):
            eb_rows.append(rec)
            if rec.get("join_basis") or rec.get("matched_csr_cin") or rec.get("csr_company_name"):
                already_joined = True
        elif any(k in rec for k in ("cin", "CIN", "csr_total", "company_name")):
            inline_csr_rows.append(rec)

    strict_csr: dict[str, list[dict[str, Any]]] = {}
    core_csr: dict[str, list[dict[str, Any]]] = {}
    cin_csr: dict[str, dict[str, Any]] = {}
    if inline_csr_rows:
        strict_csr, core_csr, cin_csr = _build_csr_lookup(inline_csr_rows)
    elif isinstance(ctx.get("secondary_records"), list):
        strict_csr, core_csr, cin_csr = _build_csr_lookup(ctx["secondary_records"])
    elif not already_joined:
        # Never allow untrusted context to escalate allow_local_files or read local secondary_path.
        sec_ref = str(
            ctx.get("secondary_url") or ctx.get("secondary_dataset_url") or ""
        ).strip()
        if sec_ref:
            sec_expected = str(ctx.get("secondary_expected_sha256") or ctx.get("secondary_sha256") or "").strip() or None
            sec_ds = load_dataset(
                sec_ref,
                allow_local_files=False,
                expected_sha256=sec_expected,
            )
            strict_csr, core_csr, cin_csr = _build_csr_lookup(sec_ds.records)

    has_csr_mode = already_joined or bool(strict_csr or cin_csr)
    min_standalone_inr = float(ctx.get("min_disclosure_inr", 50.0 * _INR_CRORE))

    entities: dict[str, dict[str, Any]] = {}
    total_audited = 0.0

    for idx, rec in enumerate(eb_rows):
        amt = _to_float(rec.get("amount", rec.get("Denominations", 0)))
        total_audited += max(0.0, amt)
        purchaser = _norm_str(rec.get("purchaser_name") or rec.get("Name of the Purchaser"))
        party = _norm_str(
            rec.get("political_party_name") or rec.get("Name of the Political Party"),
            "Undisclosed / Unmatched Party",
        )
        if not purchaser:
            purchaser = party
        strict_k, core_k = normalize_company_name(purchaser)
        if not strict_k:
            continue

        # Determine exact vs normalized-name join metadata without fabricating primary CINs
        bond_source_cin = _norm_str(rec.get("bond_source_cin") or rec.get("cin")) or None
        matched_csr_cin = _norm_str(rec.get("matched_csr_cin")) or None
        csr_name = _norm_str(rec.get("csr_company_name")) or None
        csr_total_inr = _to_float(rec.get("csr_total_inr"), 0.0) if rec.get("csr_total_inr") is not None else 0.0
        csr_rank = rec.get("csr_rank")
        join_basis = _norm_str(rec.get("join_basis")) or None
        join_match_tier = _norm_str(rec.get("join_match_tier")) or None
        exact_verified = bool(rec.get("exact_identifier_verified", False))
        join_ambiguous = bool(rec.get("join_ambiguous", False))
        candidate_cins = list(rec.get("join_candidate_cins") or [])
        candidate_names = list(rec.get("join_candidate_names") or [])

        if join_basis is None and (strict_csr or cin_csr):
            if bond_source_cin and bond_source_cin.upper() in cin_csr:
                m = cin_csr[bond_source_cin.upper()]
                matched_csr_cin = m["cin"]
                csr_name = m["company_name"]
                csr_total_inr = m["csr_total_inr"]
                csr_rank = m.get("csr_rank")
                join_basis = "exact_cin"
                join_match_tier = "exact_cin"
                exact_verified = True
                join_ambiguous = False
                candidate_cins = [m["cin"]]
                candidate_names = [m["company_name"]]
            else:
                cands = strict_csr.get(strict_k) or (core_csr.get(core_k) if len(core_k) >= 6 else None) or []
                if cands:
                    join_basis = "normalized_name"
                    join_match_tier = (
                        "strict_normalized_name" if strict_k in strict_csr else "core_normalized_name"
                    )
                    exact_verified = False
                    join_ambiguous = len(cands) > 1
                    candidate_cins = sorted({c["cin"] for c in cands if c.get("cin")})
                    candidate_names = sorted({c["company_name"] for c in cands if c.get("company_name")})
                    if not join_ambiguous:
                        m = cands[0]
                        matched_csr_cin = m["cin"]
                        csr_name = m["company_name"]
                        csr_total_inr = m["csr_total_inr"]
                        csr_rank = m.get("csr_rank")

        is_csr_linked = bool(join_basis in {"exact_cin", "normalized_name"} and (matched_csr_cin or candidate_cins))
        if matched_csr_cin:
            group_key = f"csr:{matched_csr_cin}"
        elif candidate_cins:
            group_key = f"ambig:{','.join(sorted(candidate_cins))}:{strict_k}"
        else:
            group_key = f"name:{strict_k}"

        entry = entities.get(group_key)
        if entry is None:
            entry = {
                "display_name": csr_name or purchaser,
                "bond_source_purchaser_names": set(),
                "bond_source_cin": bond_source_cin,
                "matched_csr_cin": matched_csr_cin,
                "matched_csr_company_name": csr_name,
                "csr_total_inr": csr_total_inr,
                "csr_rank": csr_rank,
                "join_basis": join_basis or "unmatched",
                "join_match_tier": join_match_tier or "none",
                "exact_identifier_verified": exact_verified,
                "join_ambiguous": join_ambiguous,
                "join_candidate_cins": set(candidate_cins),
                "join_candidate_names": set(candidate_names),
                "is_csr_linked": is_csr_linked,
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

        entry["bond_source_purchaser_names"].add(purchaser)
        entry["join_candidate_cins"].update(candidate_cins)
        entry["join_candidate_names"].update(candidate_names)
        entry["eb_total_inr"] += max(0.0, amt)
        entry["bond_count"] += 1
        entry["row_indices"].append(int(rec.get("source_row_index") or (idx + 1)))
        entry["party_totals"][party] += max(0.0, amt)
        dt = _parse_date(
            rec.get("date_of_purchase")
            or rec.get("Date of Purchase")
            or rec.get("date_of_encashment")
            or rec.get("Date of Encashment")
        )
        if dt:
            entry["dates"].append(dt)
        if len(entry["sample_bonds"]) < 5:
            bond_no = _norm_str(rec.get("bond_number") or rec.get("Bond Number"), f"row-{idx + 1}")
            prefix = _norm_str(rec.get("prefix") or rec.get("Prefix"))
            entry["sample_bonds"].append((int(rec.get("source_row_index") or (idx + 1)), f"{prefix}{bond_no}", amt, party, dt))

    leads: list[AuditLeadCandidate] = []
    cleared_records = 0

    for entry in entities.values():
        eb_inr = entry["eb_total_inr"]
        matched_csr_cin = entry["matched_csr_cin"]
        csr_inr = entry["csr_total_inr"]
        exact_verified = entry["exact_identifier_verified"]
        join_basis = entry["join_basis"]
        join_ambiguous = entry["join_ambiguous"]
        cand_cins = sorted(entry["join_candidate_cins"])
        cand_names = sorted(entry["join_candidate_names"])
        raw_purchasers = sorted(entry["bond_source_purchaser_names"])

        if has_csr_mode:
            if not entry["is_csr_linked"] or eb_inr <= 0:
                cleared_records += entry["bond_count"]
                continue
        else:
            if eb_inr < min_standalone_inr:
                cleared_records += entry["bond_count"]
                continue

        party_totals: dict[str, float] = entry["party_totals"]
        top_party, top_party_inr = max(party_totals.items(), key=lambda kv: (kv[1], kv[0]))
        top_party_share_pct = (top_party_inr / eb_inr * 100.0) if eb_inr > 0 else 0.0
        dates: list[date] = entry["dates"]
        date_span = f"{min(dates).isoformat()} to {max(dates).isoformat()}" if dates else "2019-2024"
        fy_start = f"FY {min(dates).year}-{str(min(dates).year + 1)[-2:]}" if dates else "FY 2019-20"
        fy_end = f"FY {max(dates).year}-{str(max(dates).year + 1)[-2:]}" if dates else "FY 2023-24"
        fy_span = f"{fy_start} to {fy_end}" if fy_start != fy_end else fy_start

        safe_company = _neutralize_text(entry["display_name"])
        safe_party = _neutralize_text(top_party)
        eb_to_csr_ratio = (eb_inr / csr_inr) if (csr_inr > 0 and not join_ambiguous) else None

        if has_csr_mode:
            if exact_verified:
                match_label = f"Exact Corroborated CIN Match (CIN={matched_csr_cin})"
            elif join_ambiguous:
                match_label = (
                    f"Ambiguous Normalized-Name Match ({len(cand_cins)} candidate CSR CINs: "
                    f"{', '.join(cand_cins)}; no CIN in electoral bond source)"
                )
            else:
                match_label = (
                    f"Normalized-Name Linkage to MCA CSR CIN {matched_csr_cin} "
                    "(bond file contains purchaser names only, no CIN; corroborating identifier required for exact match)"
                )

            title = f"Disclosure / Proximity Lead: Electoral Bond & MCA CSR Normalized-Name Overlap — {safe_company}" if not exact_verified else (
                f"Disclosure / Proximity Lead: SC Electoral Bond & MCA CSR Registry Overlap — {safe_company}"
            )
            if join_ambiguous:
                csr_clause = (
                    f"Normalized purchaser name matches {len(cand_cins)} distinct MCA CSR records "
                    f"({', '.join(cand_cins)}); entity identity is ambiguous without a corroborating identifier. "
                )
            else:
                csr_clause = (
                    f"Separate MCA National CSR Registry record ({entry['matched_csr_company_name']}, "
                    f"CSR CIN {matched_csr_cin}) lists published CSR outlay of INR {csr_inr / _INR_CRORE:.2f} Cr"
                    + (f" (Top-100 Rank #{entry['csr_rank']})" if entry.get("csr_rank") else "")
                    + (
                        f", with disclosed bond value at {eb_to_csr_ratio:.2f}x annual CSR outlay. "
                        if eb_to_csr_ratio is not None
                        else ". "
                    )
                )
            rule_triggered = (
                f"SC SBI/ECI Electoral Bond Disclosure x MCA CSR Registry Cross-Match "
                f"(join_basis={join_basis}; {match_label}; disclosed_bond_value=INR {eb_inr / _INR_CRORE:.2f} Cr)"
            )
        else:
            title = f"Disclosure / Proximity Lead: High-Value Statutory Electoral Bond Disclosure — {safe_company}"
            csr_clause = ""
            rule_triggered = (
                f"Supreme Court SBI/ECI Public Electoral Bond Disclosure Summary "
                f"(disclosed_bond_value=INR {eb_inr / _INR_CRORE:.2f} Cr across {entry['bond_count']} bonds)"
            )

        safe_purchasers = [_neutralize_text(p) for p in raw_purchasers[:3]]
        summary = (
            f"Corporate-Electoral Public Disclosure summary for {safe_company}: SBI/ECI statutory "
            f"disclosures ({date_span}; bond purchaser name(s): {', '.join(safe_purchasers)}; "
            f"bond source CIN: {entry['bond_source_cin'] or 'absent in bond file'}) record "
            f"{entry['bond_count']} electoral bond(s) with disclosed bond value INR {eb_inr / _INR_CRORE:.2f} Cr "
            f"across {len(party_totals)} political recipient(s), with largest recipient {safe_party} receiving "
            f"{top_party_share_pct:.1f}% (INR {top_party_inr / _INR_CRORE:.2f} Cr). "
            f"Join basis: {join_basis} (exact_identifier_verified={exact_verified}; ambiguous={join_ambiguous}). "
            f"{csr_clause}"
            f"These inputs describe statutory disclosures only — no tender award establishes a procurement "
            f"relationship, and this summary makes no allegation of improper conduct, regulatory breach, or quid pro quo."
        )

        severity = "high" if eb_inr >= 100.0 * _INR_CRORE else "medium"
        citations = [
            {
                "record_id": f"{matched_csr_cin or safe_company}:{bno}",
                "row_id": r_idx,
                "source_url": entry["source_url"],
                "excerpt": (
                    f"Bond {bno} ({_neutralize_text(raw_purchasers[0])}): disclosed_bond_value=INR {b_amt:,.0f} -> "
                    f"{_neutralize_text(b_party)} ({b_dt.isoformat() if b_dt else 'date N/A'})"
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
                fiscal_year_start=fy_start,
                fiscal_year_end=fy_end,
                source_locator=f"rows={','.join(str(r) for r in entry['row_indices'][:10])}",
                amount_type="disclosed_bond_value",
                monetary_exposure_inr=round(eb_inr, 2),
                severity=severity,
                anomaly_metrics={
                    # Only populate bare `cin` when corroborated by primary record; never copy CSR CIN as exact!
                    "cin": matched_csr_cin if exact_verified else None,
                    "bond_source_cin": entry["bond_source_cin"],
                    "matched_csr_cin": matched_csr_cin,
                    "matched_csr_company_name": entry["matched_csr_company_name"],
                    "bond_source_purchaser_names": raw_purchasers,
                    "join_basis": join_basis,
                    "join_match_tier": entry["join_match_tier"],
                    "exact_identifier_verified": exact_verified,
                    "join_ambiguous": join_ambiguous,
                    "join_candidate_count": len(cand_cins),
                    "join_candidate_cins": cand_cins,
                    "join_candidate_names": cand_names,
                    "company_name": safe_company,
                    "disclosed_bond_value_inr": round(eb_inr, 2),
                    "disclosed_bond_value_crore": round(eb_inr / _INR_CRORE, 2),
                    "electoral_disclosure_inr": round(eb_inr, 2),
                    "electoral_disclosure_crore": round(eb_inr / _INR_CRORE, 2),
                    "csr_total_inr": round(csr_inr, 2) if not join_ambiguous else None,
                    "csr_total_crore": round(csr_inr / _INR_CRORE, 2) if not join_ambiguous else None,
                    "eb_to_csr_ratio": round(eb_to_csr_ratio, 2) if eb_to_csr_ratio is not None else None,
                    "bond_count": entry["bond_count"],
                    "distinct_parties": len(party_totals),
                    "top_party_name": safe_party,
                    "top_party_share_pct": round(top_party_share_pct, 2),
                    "amount_type": "disclosed_bond_value",
                    "procurement_relationship_established": False,
                    "disclosure_only": True,
                },
                rule_triggered=rule_triggered,
                citations=citations,
                benign_explanations=[
                    "Electoral bond purchases disclosed under Supreme Court orders were lawful corporate political contributions made under the then-prevailing Electoral Bond Scheme notified by the Government of India.",
                    "Linking bond purchaser names to MCA CSR records relies on normalized company names because the bond file lacks CINs; a corroborating corporate identifier is required to confirm legal entity identity.",
                    "These public disclosures contain no tender awards and establish no procurement relationship, favor, or quid pro quo.",
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
        f"(INR {total_audited / _INR_CRORE:,.2f} Cr in disclosed_bond_value): {lead_note}, {cleared_records} cleared record(s)."
    )
    return (
        top_leads,
        cleared_records,
        round(total_audited, 2),
        summary_text,
        {"unevaluable_count": 0, "amount_type": "disclosed_bond_value"},
    )


def run_m8_corporate_proximity(
    records: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> tuple[list[AuditLeadCandidate], int, float, str, dict[str, Any]]:
    """M8: Corporate-Electoral-Procurement Proximity Disclosure Lead (strictly neutral framing)."""
    ctx = context or {}
    if _is_electoral_bond_ledger(records):
        return _run_electoral_bond_csr_disclosure(records, ctx)

    new_entity_days_cutoff = int(ctx.get("new_entity_days", 180))
    major_contract_inr = float(ctx.get("major_contract_inr", 5_000_000.0))
    electoral_award_cutoff = float(ctx.get("electoral_award_cutoff_inr", 50_000_000.0))

    leads: list[AuditLeadCandidate] = []
    cleared_count = 0
    total_audited = 0.0

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
        fiscal_year = _norm_str(rec.get("fiscal_year"), "Unspecified")

        inc_dt = _parse_date(rec.get("incorporation_date"))
        award_dt = _parse_date(rec.get("award_date"))
        if rec.get("company_age_days") not in (None, ""):
            company_age_days: int | None = _safe_int(rec.get("company_age_days"), default=0)
        elif inc_dt and award_dt:
            company_age_days = max(0, (award_dt - inc_dt).days)
        else:
            company_age_days = None

        new_entity_flag = (
            company_age_days is not None
            and company_age_days < new_entity_days_cutoff
            and award_val >= major_contract_inr
        )

        bidders_list: list[dict[str, Any]] = []
        if isinstance(rec.get("competing_bidders"), list):
            bidders_list.extend(b for b in rec["competing_bidders"] if isinstance(b, dict))
        elif tender_id in tender_bidders and len(tender_bidders[tender_id]) >= 2:
            bidders_list.extend(b for b in tender_bidders[tender_id] if isinstance(b, dict))

        shared_dins, shared_addrs = _find_bidder_overlaps(bidders_list)
        overlap_flag = bool(shared_dins or shared_addrs)
        if overlap_flag and tender_id in seen_overlap_tenders:
            overlap_flag = False
        elif overlap_flag:
            seen_overlap_tenders.add(tender_id)

        electoral_inr = _to_float(
            rec.get("electoral_bond_inr", rec.get("political_disclosure_inr", 0))
        )
        disc_dt = _parse_date(rec.get("disclosure_date") or rec.get("bond_purchase_date"))
        days_gap: int | None = None
        if rec.get("days_between_disclosure_and_award") not in (None, ""):
            days_gap = abs(_safe_int(rec.get("days_between_disclosure_and_award"), default=0))
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
                f"disclosed_bond_value=INR {electoral_inr:,.0f}_within_{days_gap if days_gap is not None else 'N/A'}d_of_award"
            )

        severity = "high" if (len(signals) >= 2 or overlap_flag or award_val >= 100_000_000) else "medium"
        summary = (
            f"Corporate-Procurement Disclosure / Proximity Lead on tender {tender_id} ({buyer}, "
            f"{jurisdiction}, {fiscal_year}) involving {vendor} ({cin}, award INR {award_val:,.0f}). "
            f"Public registry cross-check notes structural disclosure signal(s): {'; '.join(signals)}. "
            f"This entry records factual public registry overlap for transparent eligibility verification "
            f"and makes no allegation of improper conduct or quid pro quo."
        )
        row_num = _safe_int(rec.get("source_row_index"), default=idx + 1) or (idx + 1)

        leads.append(
            AuditLeadCandidate(
                title=f"Disclosure / Proximity Lead: Corporate Registry Signal on {tender_id} — {buyer}",
                summary=summary,
                methodology_code="M8",
                entity_name=buyer,
                jurisdiction=jurisdiction,
                fiscal_year=fiscal_year,
                fiscal_year_start=fiscal_year if fiscal_year != "Unspecified" else None,
                fiscal_year_end=fiscal_year if fiscal_year != "Unspecified" else None,
                source_locator=f"row={row_num}",
                amount_type="award_value",
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
                    "disclosed_bond_value_inr": round(electoral_inr, 2),
                    "electoral_disclosure_inr": round(electoral_inr, 2),
                    "days_between_disclosure_and_award": days_gap,
                    "disclosure_only": True,
                },
                rule_triggered="MCA21 / ECI / Procurement Governance Disclosure Check (" + "; ".join(signals) + ")",
                citations=[
                    {
                        "record_id": tender_id,
                        "tender_id": tender_id,
                        "row_id": row_num,
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
    return (
        top_leads,
        cleared_count,
        round(total_audited, 2),
        summary_text,
        {"unevaluable_count": 0, "amount_type": "award_value"},
    )
