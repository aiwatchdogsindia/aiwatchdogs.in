"""Deterministic Indian welfare & infrastructure scheme verification engine (M7: PMGSY, MPLADS, MGNREGA)."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from aiwatchdogs.core.models import AuditLeadCandidate

_INR_CRORE = 10_000_000.0
_INR_LAKH = 100_000.0

_NEUTRAL_REPLACE_RE = re.compile(
    r"\b(?:scam(?:s|med|mer|mers)?|corrupt(?:ed|ing|ly|ion)?|brib(?:e|es|ed|ing|ery)|"
    r"fraud(?:s|ulent|ulently|ster|sters)?|kickback(?:s)?|guilty|stolen|rigged|"
    r"crook(?:s|ed)?|loot(?:s|ed|ing|er|ers)?|embezzl(?:e|es|ed|ing|ement))\b",
    re.IGNORECASE,
)


def _neutralize_text(text: str) -> str:
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


def _to_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or str(value).strip() == "":
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    return default


def _norm_str(value: Any, fallback: str = "") -> str:
    text = str(value).strip() if value is not None else ""
    return text if text else fallback


def _fmt_jurisdiction(val: Any, fallback: str = "Union of India") -> str:
    text = _norm_str(val)
    if not text:
        return fallback
    if text.startswith(("State:", "UT:", "Union")):
        return text
    return f"State:{text.title()}"


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value).strip().split("T", 1)[0]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d/%b/%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _detect_scheme(rec: dict[str, Any]) -> str:
    explicit = _norm_str(rec.get("scheme")).upper()
    if explicit in {"PMGSY", "MPLADS", "MGNREGA"}:
        return explicit
    if any(
        k in rec and str(rec.get(k) or "").strip()
        for k in (
            "sanctioned_km",
            "completed_km",
            "dpr_cost_inr",
            "COST_OF_WORKS_SANCTIONED",
            "EXPENDITURE_OCCURED",
            "PMGSY_SCHEME",
            "LENGTH_OF_ROAD_WORK_SANCTIONED",
        )
    ):
        return "PMGSY"
    if any(
        k in rec and str(rec.get(k) or "").strip()
        for k in (
            "wage_spend_inr",
            "material_spend_inr",
            "social_audit_flagged_inr",
            "Wages(Rs. In Lakhs)",
            "Material and skilled Wages(Rs. In Lakhs)",
            "Total Exp(Rs. in Lakhs.)",
        )
    ):
        return "MGNREGA"
    if any(
        k in rec and str(rec.get(k) or "").strip()
        for k in (
            "months_elapsed",
            "has_geotag_photo",
            "constituency",
            "CONSTITUENCY",
            "ALLOCATION AMOUNT",
            "RECOMMENDED DATE",
            "IDA",
        )
    ):
        return "MPLADS"
    return "UNKNOWN"


def _eval_pmgsy(idx: int, rec: dict[str, Any]) -> tuple[AuditLeadCandidate | None, float]:
    # Support canonical INR fields AND real OMMAS PMGSY_DATASET.csv (costs in INR Crore x 1e7)
    if rec.get("sanctioned_cost_inr") not in (None, "") or rec.get("sanctioned_inr") not in (None, ""):
        sanctioned_cost = _to_float(rec.get("sanctioned_cost_inr", rec.get("sanctioned_inr", 0)))
    else:
        sanctioned_cost = _to_float(rec.get("COST_OF_WORKS_SANCTIONED", 0)) * _INR_CRORE

    if rec.get("expenditure_inr") not in (None, "") or rec.get("released_inr") not in (None, ""):
        expenditure = _to_float(rec.get("expenditure_inr", rec.get("released_inr", 0)))
    else:
        expenditure = _to_float(rec.get("EXPENDITURE_OCCURED", 0)) * _INR_CRORE

    dpr_cost = _to_float(rec.get("dpr_cost_inr", sanctioned_cost))
    awarded_cost = _to_float(rec.get("awarded_cost_inr", sanctioned_cost))
    sanctioned_km = _to_float(
        rec.get("sanctioned_km", rec.get("LENGTH_OF_ROAD_WORK_SANCTIONED", 0))
    )
    completed_km = _to_float(
        rec.get("completed_km", rec.get("LENGTH_OF_ROAD_WORK_COMPLETED", 0))
    )
    delay_months = _to_float(rec.get("delay_months", 0))
    dlp_unsat = _to_bool(rec.get("dlp_quality_unsatisfactory"), False)

    audited = max(sanctioned_cost, expenditure, dpr_cost, 0.0)

    fin_pct = (
        _to_float(rec.get("financial_progress_pct"))
        if rec.get("financial_progress_pct") not in (None, "")
        else ((expenditure / sanctioned_cost) * 100.0 if sanctioned_cost > 0 else 0.0)
    )
    has_phys_metric = rec.get("physical_progress_pct") not in (None, "") or sanctioned_km > 0
    phys_pct = (
        _to_float(rec.get("physical_progress_pct"))
        if rec.get("physical_progress_pct") not in (None, "")
        else ((completed_km / sanctioned_km) * 100.0 if sanctioned_km > 0 else 0.0)
    )
    low_bid_discount_pct = ((dpr_cost - awarded_cost) / dpr_cost * 100.0) if (dpr_cost > 0 and awarded_cost > 0) else 0.0

    triggers: list[str] = []
    if has_phys_metric and fin_pct >= 90.0 and phys_pct < 70.0:
        triggers.append(f"financial_progress={fin_pct:.1f}% (>=90%) vs physical_km={phys_pct:.1f}% (<70%)")
    if low_bid_discount_pct > 25.0 and delay_months >= 6:
        triggers.append(f"low_bid={low_bid_discount_pct:.1f}%_below_DPR_with_{delay_months:.0f}m_delay")
    if dlp_unsat:
        triggers.append("NQM/SQM_DLP_quality_unsatisfactory")

    if not triggers:
        return None, audited

    state_raw = _norm_str(rec.get("STATE_NAME") or rec.get("state"))
    district_raw = _norm_str(rec.get("DISTRICT_NAME") or rec.get("district"))
    pmgsy_phase = _norm_str(rec.get("PMGSY_SCHEME"), "PMGSY")

    default_work_id = (
        f"{pmgsy_phase}-{district_raw.replace(' ', '_')}"
        if district_raw
        else f"PMGSY-{idx + 1}"
    )
    work_id = _norm_str(rec.get("work_id") or rec.get("package_id"), default_work_id)
    default_entity = (
        f"SRRDA {district_raw.title()} ({pmgsy_phase}, {state_raw.title()})"
        if district_raw and state_raw
        else "State Rural Roads Development Agency"
    )
    entity = _neutralize_text(_norm_str(rec.get("entity_name") or rec.get("implementing_agency"), default_entity))
    jurisdiction = _fmt_jurisdiction(rec.get("jurisdiction") or state_raw)
    fiscal_year = _norm_str(rec.get("fiscal_year"), "Cumulative OMMAS")

    gap_fraction = max(0.0, min(1.0, (fin_pct - phys_pct) / 100.0))
    exposure = max(expenditure * gap_fraction, expenditure * 0.25 if dlp_unsat else 0.0)
    severity = "critical" if (fin_pct >= 95.0 and phys_pct < 50.0) else (
        "high" if len(triggers) >= 2 or exposure >= 10_000_000 else "medium"
    )

    return (
        AuditLeadCandidate(
            title=f"PMGSY Physical-Financial Gap Lead ({fin_pct:.0f}% Paid vs {phys_pct:.0f}% Built) — {work_id}",
            summary=(
                f"Public record anomaly on PMGSY road package/district {work_id} under {entity} ({jurisdiction}, "
                f"{fiscal_year}): OMMAS progress shows {fin_pct:.1f}% financial disbursement "
                f"(INR {expenditure:,.0f} of INR {sanctioned_cost:,.0f}) against {phys_pct:.1f}% physical "
                f"road length completed ({completed_km:.2f} km of {sanctioned_km:.2f} km). "
                f"Indicators: {'; '.join(triggers)}."
            ),
            methodology_code="M7",
            entity_name=entity,
            jurisdiction=jurisdiction,
            fiscal_year=fiscal_year,
            monetary_exposure_inr=round(exposure, 2),
            severity=severity,
            anomaly_metrics={
                "scheme": "PMGSY",
                "work_id": work_id,
                "sanctioned_cost_inr": round(sanctioned_cost, 2),
                "expenditure_inr": round(expenditure, 2),
                "financial_progress_pct": round(fin_pct, 2),
                "sanctioned_km": sanctioned_km,
                "completed_km": completed_km,
                "physical_progress_pct": round(phys_pct, 2),
                "low_bid_discount_pct": round(low_bid_discount_pct, 2),
                "delay_months": delay_months,
                "dlp_quality_unsatisfactory": dlp_unsat,
            },
            rule_triggered="PMGSY OMMAS Progress & Quality Rules (" + "; ".join(triggers) + ")",
            citations=[
                {
                    "record_id": work_id,
                    "work_id": work_id,
                    "row_id": idx + 1,
                    "source_url": _norm_str(rec.get("source_url"), "https://ommas.nic.in"),
                    "excerpt": f"PMGSY {work_id} ({entity}): fin={fin_pct:.1f}%, phys={phys_pct:.1f}% ({completed_km:.2f}/{sanctioned_km:.2f} km)",
                }
            ],
            benign_explanations=[
                "Physical completion data on OMMAS can lag behind actual site progress while final Measurement Book entries await online verification.",
                "Upfront cross-drainage structures, bridges, or hill-cutting sub-works can legitimately absorb a high share of project cost before final black-topping km are logged.",
                "Forest clearance, utility shifting, or monsoon damage can pause final road surfacing despite earlier material procurement disbursements.",
            ],
            suggested_authority=entity,
        ),
        audited,
    )


def _eval_mplads(
    idx: int,
    rec: dict[str, Any],
    as_of_override: date | None = None,
) -> tuple[AuditLeadCandidate | None, float]:
    sanctioned = _to_float(
        rec.get("sanctioned_inr", rec.get("sanctioned_cost_inr", rec.get("ALLOCATION AMOUNT", 0)))
    )
    has_release_col = any(
        rec.get(k) not in (None, "") for k in ("released_inr", "expenditure_inr", "funds_released_pct")
    )
    released = _to_float(rec.get("released_inr", rec.get("expenditure_inr", 0)))
    audited = max(sanctioned, released, 0.0)

    released_pct = (
        _to_float(rec.get("funds_released_pct"))
        if rec.get("funds_released_pct") not in (None, "")
        else ((released / sanctioned) * 100.0 if (sanctioned > 0 and has_release_col) else 0.0)
    )
    status = _norm_str(rec.get("work_status") or rec.get("status") or rec.get("STATUS"), "in_progress").lower()
    has_geotag_col = rec.get("has_geotag_photo") not in (None, "")
    has_geotag = _to_bool(rec.get("has_geotag_photo"), True)

    # Unsanctioned recommendations have not been approved for execution yet; skip them cleanly
    if status in {"unsanctioned", "rejected", "cancelled", ""}:
        return None, audited

    months_elapsed = _to_float(rec.get("months_elapsed"), -1.0)
    if months_elapsed < 0:
        s_dt = _parse_date(rec.get("sanction_date") or rec.get("RECOMMENDED DATE"))
        as_of = as_of_override or _parse_date(rec.get("as_of_date")) or date.today()
        months_elapsed = max(0.0, (as_of - s_dt).days / 30.44) if s_dt else 0.0

    triggers: list[str] = []
    if has_release_col:
        if status != "completed" and months_elapsed > 12.0 and released_pct >= 80.0:
            triggers.append(
                f"incomplete_after_{months_elapsed:.1f}m (>12m)_with_{released_pct:.1f}%_released (>=80%)"
            )
        if has_geotag_col and released_pct >= 80.0 and not has_geotag:
            triggers.append(f"missing_geotag_photo_with_{released_pct:.1f}%_released")
    else:
        # Real MPLADS portal extract without disbursement/geotag columns: flag Ongoing/Sanctioned >12m old
        if status in {"ongoing", "sanctioned", "in_progress"} and months_elapsed > 12.0 and sanctioned > 0:
            triggers.append(
                f"status={status}_exceeding_12m_guideline ({months_elapsed:.1f}m since recommendation)"
            )

    if not triggers:
        return None, audited

    work_text = _norm_str(rec.get("WORK"))
    extracted_wid = work_text.split(" - ", 1)[0].strip() if " - " in work_text and not work_text.startswith("NA") else ""
    work_id = _norm_str(rec.get("work_id") or extracted_wid, f"MPLADS-{idx + 1}")
    constituency = _norm_str(rec.get("CONSTITUENCY") or rec.get("constituency"))
    ida = _norm_str(rec.get("IDA"))
    entity = _neutralize_text(
        _norm_str(
            rec.get("entity_name")
            or rec.get("implementing_agency")
            or rec.get("district_authority")
            or ida
            or (f"MPLADS Nodal Authority — {constituency.title()}" if constituency else ""),
            "District MPLADS Nodal Authority",
        )
    )
    jurisdiction = _fmt_jurisdiction(rec.get("jurisdiction") or rec.get("state") or rec.get("STATE"))
    fiscal_year = _norm_str(rec.get("fiscal_year"), "MPLADS Registry")
    exposure = released if (has_release_col and released > 0) else sanctioned

    severity = "high" if (len(triggers) >= 2 or months_elapsed >= 24.0 or exposure >= 5_000_000.0) else "medium"
    if has_release_col:
        detail_clause = (
            f"{released_pct:.1f}% of sanctioned funds (INR {released:,.0f} of INR {sanctioned:,.0f}) "
            f"have been released; status is '{status}' after {months_elapsed:.1f} months "
            f"with geotagged verification={has_geotag}."
        )
    else:
        detail_clause = (
            f"allocated outlay INR {sanctioned:,.0f} remains in '{status}' status after "
            f"{months_elapsed:.1f} months since recommendation (portal extract omits disbursement/geotag fields)."
        )

    return (
        AuditLeadCandidate(
            title=f"MPLADS Asset Verification Lead: Delayed Work ({months_elapsed:.0f}m) — {work_id}",
            summary=(
                f"Public record anomaly on MPLADS work {work_id} under {entity} ({jurisdiction}, "
                f"{fiscal_year}): {detail_clause} Indicators: {'; '.join(triggers)}."
            ),
            methodology_code="M7",
            entity_name=entity,
            jurisdiction=jurisdiction,
            fiscal_year=fiscal_year,
            monetary_exposure_inr=round(exposure, 2),
            severity=severity,
            anomaly_metrics={
                "scheme": "MPLADS",
                "work_id": work_id,
                "sanctioned_inr": round(sanctioned, 2),
                "released_inr": round(released, 2),
                "funds_released_pct": round(released_pct, 2),
                "months_elapsed": round(months_elapsed, 1),
                "work_status": status,
                "has_geotag_photo": has_geotag if has_geotag_col else None,
            },
            rule_triggered="MPLADS e-SAKSHI Guidelines (>12m completion limit & asset verification: " + "; ".join(triggers) + ")",
            citations=[
                {
                    "record_id": work_id,
                    "work_id": work_id,
                    "row_id": idx + 1,
                    "source_url": _norm_str(rec.get("source_url"), "https://mplads.mospi.gov.in"),
                    "excerpt": (
                        f"MPLADS {work_id} ({entity}): status={status}, elapsed={months_elapsed:.1f}m, "
                        f"allocated=INR {sanctioned:,.0f}"
                    ),
                }
            ],
            benign_explanations=[
                "Physical work may be substantially finished on the ground while the Implementing Agency awaits final inspection and e-SAKSHI portal status/photo update.",
                "Model Code of Conduct periods, municipal land/NOC clearances, or adverse weather can extend execution past the standard 12-month guideline.",
                "Allocated funds may remain parked in the District Nodal Account pending final Utilization Certificate verification or tendering completion.",
            ],
            suggested_authority=entity,
        ),
        audited,
    )


def _eval_mgnrega(idx: int, rec: dict[str, Any]) -> tuple[AuditLeadCandidate | None, float]:
    # Support canonical INR fields AND real NREGA Data.csv (Rs. In Lakhs x 1e5)
    if rec.get("wage_spend_inr") not in (None, ""):
        wage_inr = _to_float(rec.get("wage_spend_inr", 0))
    else:
        wage_inr = _to_float(rec.get("Wages(Rs. In Lakhs)", 0)) * _INR_LAKH

    if rec.get("material_spend_inr") not in (None, ""):
        material_inr = _to_float(rec.get("material_spend_inr", 0))
    else:
        material_inr = _to_float(rec.get("Material and skilled Wages(Rs. In Lakhs)", 0)) * _INR_LAKH

    total_exp_lakh_inr = _to_float(rec.get("Total Exp(Rs. in Lakhs.)", 0)) * _INR_LAKH
    sa_flagged = _to_float(rec.get("social_audit_flagged_inr", 0))
    sa_recovered = _to_float(rec.get("social_audit_recovered_inr", 0))

    total_wm = wage_inr + material_inr
    audited = max(total_wm, total_exp_lakh_inr, sa_flagged, 0.0)
    material_share_pct = (material_inr / total_wm * 100.0) if total_wm > 0 else 0.0
    unrecovered = max(0.0, sa_flagged - sa_recovered)
    unrecovered_pct = (unrecovered / sa_flagged * 100.0) if sa_flagged > 0 else 0.0

    triggers: list[str] = []
    if total_wm > 0 and material_share_pct > 40.0:
        triggers.append(f"material_ratio={material_share_pct:.1f}% > 40% statutory ceiling (60:40 breach)")
    if sa_flagged > 0 and unrecovered >= 100_000.0 and unrecovered_pct >= 50.0:
        triggers.append(
            f"unrecovered_R9.2_social_audit=INR {unrecovered:,.0f} ({unrecovered_pct:.1f}% pending)"
        )

    if not triggers:
        return None, audited

    state_raw = _norm_str(rec.get("state_name") or rec.get("state"))
    district_raw = _norm_str(rec.get("district_name") or rec.get("district"))
    default_wid = (
        f"MGNREGA-{state_raw[:3].upper()}-{district_raw.replace(' ', '_')}"
        if district_raw and state_raw
        else f"MGNREGA-{idx + 1}"
    )
    work_id = _norm_str(rec.get("work_id") or rec.get("block_code"), default_wid)
    default_entity = (
        f"{district_raw.title()} District MGNREGA ({state_raw.title()})"
        if district_raw and state_raw
        else "MGNREGA Programme Officer"
    )
    entity = _neutralize_text(
        _norm_str(rec.get("entity_name") or rec.get("gram_panchayat") or rec.get("block_name"), default_entity)
    )
    jurisdiction = _fmt_jurisdiction(rec.get("jurisdiction") or state_raw)
    fiscal_year = _norm_str(rec.get("fiscal_year"), "MGNREGA MIS")

    excess_material = max(0.0, material_inr - 0.40 * total_wm) if material_share_pct > 40.0 else 0.0
    exposure = excess_material + unrecovered
    severity = "critical" if (material_share_pct >= 55.0 or unrecovered >= 5_000_000.0 or exposure >= 100_000_000.0) else (
        "high" if len(triggers) >= 2 or exposure >= 10_000_000.0 else "medium"
    )

    return (
        AuditLeadCandidate(
            title=f"MGNREGA Compliance Lead: 60:40 Ratio ({material_share_pct:.1f}% Material) — {entity}",
            summary=(
                f"Public record anomaly in MGNREGA expenditure & social audit records for {entity} "
                f"({jurisdiction}, {fiscal_year}): material expenditure share is {material_share_pct:.1f}% "
                f"(wage INR {wage_inr:,.0f}, material INR {material_inr:,.0f}; excess above 40% ceiling INR {excess_material:,.0f}) "
                f"and unrecovered R9.2 Social Audit flagged outlay stands at INR {unrecovered:,.0f} of INR {sa_flagged:,.0f}. "
                f"Indicators: {'; '.join(triggers)}."
            ),
            methodology_code="M7",
            entity_name=entity,
            jurisdiction=jurisdiction,
            fiscal_year=fiscal_year,
            monetary_exposure_inr=round(exposure, 2),
            severity=severity,
            anomaly_metrics={
                "scheme": "MGNREGA",
                "work_id": work_id,
                "wage_spend_inr": round(wage_inr, 2),
                "material_spend_inr": round(material_inr, 2),
                "material_share_pct": round(material_share_pct, 2),
                "wage_share_pct": round(100.0 - material_share_pct, 2) if total_wm > 0 else 0.0,
                "excess_material_inr": round(excess_material, 2),
                "social_audit_flagged_inr": round(sa_flagged, 2),
                "social_audit_recovered_inr": round(sa_recovered, 2),
                "social_audit_unrecovered_inr": round(unrecovered, 2),
                "unrecovered_pct": round(unrecovered_pct, 2),
            },
            rule_triggered="MGNREGA Sec 22 / Schedule II (60:40 Ratio) & Sec 17 R9.2 Social Audit (" + "; ".join(triggers) + ")",
            citations=[
                {
                    "record_id": work_id,
                    "work_id": work_id,
                    "row_id": idx + 1,
                    "source_url": _norm_str(rec.get("source_url"), "https://nrega.nic.in"),
                    "excerpt": f"MGNREGA {entity}: material={material_share_pct:.1f}% (wage INR {wage_inr:,.0f}, mat INR {material_inr:,.0f})",
                }
            ],
            benign_explanations=[
                "Under MGNREGA rules, the 60:40 wage-to-material ratio is formally reconciled at the District level across the full fiscal year rather than at a single interim snapshot.",
                "Spikes in recorded material share can occur when delayed central wage tranches are pending release while prior material bills are cleared.",
                "Social audit recovery proceedings or durable-asset convergence works may be actively undergoing reconciliation through departmental hearings.",
            ],
            suggested_authority=entity,
        ),
        audited,
    )


def run_m7_welfare_schemes(
    records: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> tuple[list[AuditLeadCandidate], int, float, str]:
    """M7: Unified verification for PMGSY, MPLADS, and MGNREGA physical/financial records."""
    ctx = context or {}
    as_of_override = _parse_date(ctx.get("as_of_date"))

    leads: list[AuditLeadCandidate] = []
    cleared_count = 0
    total_audited = 0.0

    for idx, rec in enumerate(records):
        scheme = _detect_scheme(rec)
        lead: AuditLeadCandidate | None = None
        audited = 0.0
        if scheme == "PMGSY":
            lead, audited = _eval_pmgsy(idx, rec)
        elif scheme == "MPLADS":
            lead, audited = _eval_mplads(idx, rec, as_of_override=as_of_override)
        elif scheme == "MGNREGA":
            lead, audited = _eval_mgnrega(idx, rec)
        else:
            audited = _to_float(rec.get("sanctioned_inr", rec.get("expenditure_inr", 0)))

        total_audited += audited
        if lead is not None:
            leads.append(lead)
        else:
            cleared_count += 1

    sorted_leads = sorted(
        leads,
        key=lambda item: (-float(item.monetary_exposure_inr), item.entity_name, item.title),
    )
    total_detected = len(sorted_leads)
    max_leads = int(ctx.get("max_leads", 5))
    top_leads = sorted_leads[:max_leads] if (max_leads > 0 and total_detected > max_leads) else sorted_leads

    lead_note = (
        f"top {len(top_leads)} of {total_detected} verification lead(s)"
        if total_detected > len(top_leads)
        else f"{len(top_leads)} verification lead(s)"
    )
    summary_text = (
        f"M7 scanned {len(records)} PMGSY/MPLADS/MGNREGA scheme records "
        f"(INR {total_audited:,.0f} audited): {lead_note}, {cleared_count} cleared record(s)."
    )
    return top_leads, cleared_count, round(total_audited, 2), summary_text
