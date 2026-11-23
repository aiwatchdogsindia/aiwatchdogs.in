"""Deterministic Indian welfare & infrastructure scheme verification engine (M7: PMGSY, MPLADS, MGNREGA)."""

from __future__ import annotations

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


def _detect_scheme(rec: dict[str, Any]) -> str:
    explicit = _norm_str(rec.get("scheme")).upper()
    if explicit in {"PMGSY", "MPLADS", "MGNREGA"}:
        return explicit
    if any(k in rec and str(rec.get(k) or "").strip() for k in ("sanctioned_km", "completed_km", "dpr_cost_inr")):
        return "PMGSY"
    if any(k in rec and str(rec.get(k) or "").strip() for k in ("wage_spend_inr", "material_spend_inr", "social_audit_flagged_inr")):
        return "MGNREGA"
    if any(k in rec and str(rec.get(k) or "").strip() for k in ("months_elapsed", "has_geotag_photo", "constituency")):
        return "MPLADS"
    return "UNKNOWN"


def _eval_pmgsy(idx: int, rec: dict[str, Any]) -> tuple[AuditLeadCandidate | None, float]:
    sanctioned_cost = _to_float(rec.get("sanctioned_cost_inr", rec.get("sanctioned_inr", 0)))
    expenditure = _to_float(rec.get("expenditure_inr", rec.get("released_inr", 0)))
    dpr_cost = _to_float(rec.get("dpr_cost_inr", sanctioned_cost))
    awarded_cost = _to_float(rec.get("awarded_cost_inr", sanctioned_cost))
    sanctioned_km = _to_float(rec.get("sanctioned_km", 0))
    completed_km = _to_float(rec.get("completed_km", 0))
    delay_months = _to_float(rec.get("delay_months", 0))
    dlp_unsat = _to_bool(rec.get("dlp_quality_unsatisfactory"), False)

    audited = max(sanctioned_cost, expenditure, dpr_cost, 0.0)

    fin_pct = (
        _to_float(rec.get("financial_progress_pct"))
        if rec.get("financial_progress_pct") not in (None, "")
        else ((expenditure / sanctioned_cost) * 100.0 if sanctioned_cost > 0 else 0.0)
    )
    phys_pct = (
        _to_float(rec.get("physical_progress_pct"))
        if rec.get("physical_progress_pct") not in (None, "")
        else ((completed_km / sanctioned_km) * 100.0 if sanctioned_km > 0 else 0.0)
    )
    low_bid_discount_pct = ((dpr_cost - awarded_cost) / dpr_cost * 100.0) if (dpr_cost > 0 and awarded_cost > 0) else 0.0

    triggers: list[str] = []
    if fin_pct >= 90.0 and phys_pct < 70.0:
        triggers.append(f"financial_progress={fin_pct:.1f}% (>=90%) vs physical_km={phys_pct:.1f}% (<70%)")
    if low_bid_discount_pct > 25.0 and delay_months >= 6:
        triggers.append(f"low_bid={low_bid_discount_pct:.1f}%_below_DPR_with_{delay_months:.0f}m_delay")
    if dlp_unsat:
        triggers.append("NQM/SQM_DLP_quality_unsatisfactory")

    if not triggers:
        return None, audited

    work_id = _norm_str(rec.get("work_id") or rec.get("package_id"), f"PMGSY-{idx + 1}")
    entity = _norm_str(rec.get("entity_name") or rec.get("implementing_agency"), "State Rural Roads Development Agency")
    jurisdiction = _norm_str(rec.get("jurisdiction") or rec.get("state"), "Union of India")
    fiscal_year = _norm_str(rec.get("fiscal_year"), "FY 2024-25")
    gap_fraction = max(0.0, (fin_pct - phys_pct) / 100.0)
    exposure = max(expenditure * gap_fraction, expenditure * 0.25 if dlp_unsat else 0.0)

    severity = "critical" if (fin_pct >= 95.0 and phys_pct < 50.0) else ("high" if len(triggers) >= 2 or exposure >= 10_000_000 else "medium")

    return (
        AuditLeadCandidate(
            title=f"PMGSY Physical-Financial Gap Lead ({fin_pct:.0f}% Paid vs {phys_pct:.0f}% Built) — {work_id}",
            summary=(
                f"Public record anomaly on PMGSY road package {work_id} under {entity} ({jurisdiction}, "
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
                    "excerpt": f"PMGSY {work_id}: fin={fin_pct:.1f}%, phys={phys_pct:.1f}% ({completed_km}/{sanctioned_km} km)",
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


def _eval_mplads(idx: int, rec: dict[str, Any]) -> tuple[AuditLeadCandidate | None, float]:
    sanctioned = _to_float(rec.get("sanctioned_inr", rec.get("sanctioned_cost_inr", 0)))
    released = _to_float(rec.get("released_inr", rec.get("expenditure_inr", 0)))
    audited = max(sanctioned, released, 0.0)

    released_pct = (
        _to_float(rec.get("funds_released_pct"))
        if rec.get("funds_released_pct") not in (None, "")
        else ((released / sanctioned) * 100.0 if sanctioned > 0 else 0.0)
    )
    status = _norm_str(rec.get("work_status") or rec.get("status"), "in_progress").lower()
    has_geotag = _to_bool(rec.get("has_geotag_photo"), True)

    months_elapsed = _to_float(rec.get("months_elapsed"), -1.0)
    if months_elapsed < 0:
        s_dt = _parse_date(rec.get("sanction_date"))
        as_of = _parse_date(rec.get("as_of_date")) or date.today()
        months_elapsed = ((as_of - s_dt).days / 30.44) if s_dt else 0.0

    triggers: list[str] = []
    if status != "completed" and months_elapsed > 12.0 and released_pct >= 80.0:
        triggers.append(
            f"incomplete_after_{months_elapsed:.1f}m (>12m)_with_{released_pct:.1f}%_released (>=80%)"
        )
    if released_pct >= 80.0 and not has_geotag:
        triggers.append(f"missing_geotag_photo_with_{released_pct:.1f}%_released")

    if not triggers:
        return None, audited

    work_id = _norm_str(rec.get("work_id"), f"MPLADS-{idx + 1}")
    entity = _norm_str(
        rec.get("entity_name") or rec.get("implementing_agency") or rec.get("district_authority"),
        "District MPLADS Nodal Authority",
    )
    jurisdiction = _norm_str(rec.get("jurisdiction") or rec.get("state"), "Union of India")
    fiscal_year = _norm_str(rec.get("fiscal_year"), "FY 2024-25")

    severity = "high" if (len(triggers) >= 2 or months_elapsed >= 24.0) else "medium"
    return (
        AuditLeadCandidate(
            title=f"MPLADS Asset Verification Lead: Delayed/Ungeotagged Work ({months_elapsed:.0f}m) — {work_id}",
            summary=(
                f"Public record anomaly on MPLADS work {work_id} under {entity} ({jurisdiction}, "
                f"{fiscal_year}): {released_pct:.1f}% of sanctioned funds (INR {released:,.0f} of "
                f"INR {sanctioned:,.0f}) have been released; status is '{status}' after "
                f"{months_elapsed:.1f} months with geotagged verification={has_geotag}. "
                f"Indicators: {'; '.join(triggers)}."
            ),
            methodology_code="M7",
            entity_name=entity,
            jurisdiction=jurisdiction,
            fiscal_year=fiscal_year,
            monetary_exposure_inr=round(released, 2),
            severity=severity,
            anomaly_metrics={
                "scheme": "MPLADS",
                "work_id": work_id,
                "sanctioned_inr": round(sanctioned, 2),
                "released_inr": round(released, 2),
                "funds_released_pct": round(released_pct, 2),
                "months_elapsed": round(months_elapsed, 1),
                "work_status": status,
                "has_geotag_photo": has_geotag,
            },
            rule_triggered="MPLADS e-SAKSHI Guidelines (>12m completion limit & geotag mandate: " + "; ".join(triggers) + ")",
            citations=[
                {
                    "record_id": work_id,
                    "work_id": work_id,
                    "row_id": idx + 1,
                    "source_url": _norm_str(rec.get("source_url"), "https://mplads.mospi.gov.in"),
                    "excerpt": f"MPLADS {work_id}: status={status}, elapsed={months_elapsed:.1f}m, released={released_pct:.1f}%, geotag={has_geotag}",
                }
            ],
            benign_explanations=[
                "Physical work may be substantially finished on the ground while the Implementing Agency awaits final inspection and e-SAKSHI portal photo upload.",
                "Model Code of Conduct periods, municipal land/NOC clearances, or adverse weather can extend execution past the standard 12-month guideline.",
                "Second-tranche funds may remain parked in the District Nodal Account pending final Utilization Certificate verification.",
            ],
            suggested_authority=entity,
        ),
        audited,
    )


def _eval_mgnrega(idx: int, rec: dict[str, Any]) -> tuple[AuditLeadCandidate | None, float]:
    wage_inr = _to_float(rec.get("wage_spend_inr", 0))
    material_inr = _to_float(rec.get("material_spend_inr", 0))
    sa_flagged = _to_float(rec.get("social_audit_flagged_inr", 0))
    sa_recovered = _to_float(rec.get("social_audit_recovered_inr", 0))

    total_wm = wage_inr + material_inr
    audited = max(total_wm, sa_flagged, 0.0)
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

    work_id = _norm_str(rec.get("work_id") or rec.get("block_code"), f"MGNREGA-{idx + 1}")
    entity = _norm_str(
        rec.get("entity_name") or rec.get("gram_panchayat") or rec.get("block_name"),
        "MGNREGA Programme Officer",
    )
    jurisdiction = _norm_str(rec.get("jurisdiction") or rec.get("state"), "Union of India")
    fiscal_year = _norm_str(rec.get("fiscal_year"), "FY 2024-25")

    excess_material = max(0.0, material_inr - 0.40 * total_wm) if material_share_pct > 40.0 else 0.0
    exposure = excess_material + unrecovered
    severity = "critical" if (material_share_pct >= 55.0 or unrecovered >= 5_000_000.0) else (
        "high" if len(triggers) >= 2 or exposure >= 1_000_000.0 else "medium"
    )

    return (
        AuditLeadCandidate(
            title=f"MGNREGA Compliance Lead: 60:40 Ratio / Social Audit Recovery — {entity}",
            summary=(
                f"Public record anomaly in MGNREGA expenditure & social audit records for {entity} "
                f"({jurisdiction}, {fiscal_year}): material expenditure share is {material_share_pct:.1f}% "
                f"(wage INR {wage_inr:,.0f}, material INR {material_inr:,.0f}) and unrecovered R9.2 "
                f"Social Audit flagged outlay stands at INR {unrecovered:,.0f} of INR {sa_flagged:,.0f}. "
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
                    "excerpt": f"MGNREGA {entity}: material={material_share_pct:.1f}%, SA_unrecovered=INR {unrecovered:,.0f}",
                }
            ],
            benign_explanations=[
                "Under MGNREGA rules, the 60:40 wage-to-material ratio is formally reconciled at the District level rather than solely at an individual Gram Panchayat.",
                "Spikes in recorded material share can occur when delayed central wage tranches are pending release while prior material bills are cleared.",
                "Social audit recovery proceedings may be actively underway through installment deductions, departmental hearings, or document verification.",
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
    _ = context
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
            lead, audited = _eval_mplads(idx, rec)
        elif scheme == "MGNREGA":
            lead, audited = _eval_mgnrega(idx, rec)
        else:
            audited = _to_float(rec.get("sanctioned_inr", rec.get("expenditure_inr", 0)))

        total_audited += audited
        if lead is not None:
            leads.append(lead)
        else:
            cleared_count += 1

    summary_text = (
        f"M7 scanned {len(records)} PMGSY/MPLADS/MGNREGA scheme records "
        f"(INR {total_audited:,.0f} audited): {len(leads)} verification lead(s), "
        f"{cleared_count} cleared record(s)."
    )
    return leads, cleared_count, round(total_audited, 2), summary_text
