"""Registry and dispatcher for the deterministic Indian civic-audit engines (M1-M8, P1, UB1, OR1, P5, P6, P7, P8, P9, P10)."""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from aiwatchdogs.connectors import loader as connector_loader
from aiwatchdogs.core import safety as core_safety
from aiwatchdogs.core.models import AuditLeadCandidate, EngineRunResult
from aiwatchdogs.core.safety import compute_sha256, lint_neutrality, scrub_pii, scrub_pii_in_obj
from aiwatchdogs.engines import (
    auditing_government_exams,
    fiscal,
    innocent_but_locked_up,
    one_road_two_bills,
    one_teacher_five_grades,
    only_one_bid,
    parliaments_to_do_list,
    police,
    procurement,
    proximity,
    schemes,
    sovereign_lies,
    union_budget,
)

ENGINE_VERSION = "2.0.0"

RULESET_VERSIONS: dict[str, str] = {
    "M1": "M1-2026.11-v2",
    "M2": "M2-2026.11-v2",
    "M3": "M3-2026.11-v2",
    "M4": "M4-2026.11-v2",
    "M5": "M5-2026.11-v2",
    "M6": "M6-2026.11-v2",
    "M7": "M7-2026.11-v2",
    "M8": "M8-2026.11-v2",
    "P1": "P1-2026.11-v1",
    "UB1": "UB1-2026.11-v1",
    "OR1": "OR1-2026.11-v1",
    "P5": "P5-2026.11-v1",
    "P6": "P6-2026.11-v1",
    "P7": "P7-2026.11-v1",
    "P8": "P8-2026.11-v1",
    "P9": "P9-2026.11-v1",
    "P10": "P10-2026.11-v1",
}

# Ephemeral / non-rule context keys excluded from deterministic rule-config hash
_NON_RULE_CONTEXT_KEYS = frozenset(
    {
        "use_cache",
        "max_download_bytes",
        "max_decompressed_bytes",
        "max_compression_ratio",
        "max_zip_members",
        "max_parse_rows",
        "max_pdf_pages",
        "dataset_url",
        "source_url",
        "canonical_url",
        "artifact_title",
        "secondary_url",
        "secondary_dataset_url",
        "secondary_path",
        "secondary_records",
        "expected_sha256",
        "secondary_expected_sha256",
        "secondary_sha256",
        "official_publisher",
        "source_landing_page",
        "mirror_name",
        "retrieval_url",
        "allow_local_files",
        "as_of_date",
    }
)

EngineCallable = Callable[
    [list[dict[str, Any]], dict[str, Any] | None],
    tuple[Any, ...],
]

METHODOLOGY_CATALOG: dict[str, dict[str, Any]] = {
    "M1": {
        "code": "M1",
        "name": "Single-Bidder & Shortened Bid-Window Concentration",
        "pillar": "procurement",
        "ruleset_version": RULESET_VERSIONS["M1"],
        "statutory_anchors": ["GFR-2017-R166", "CVC-Single-Tender-Guidelines", "OECD-Procurement-RedFlags"],
        "description": (
            "Detects public buyers with >35% single-bidder competitive tenders among positive bidder counts "
            "(separating literal zero from blank/unknown counts, reporting full fiscal-year span and amount type, "
            "and marking zero-positive-bid groups unevaluable), repeat single-bidder vendors (>=3), or shortened bid windows (<14 days)."
        ),
        "reference_dataset_url": "https://raw.githubusercontent.com/CivicDataLab/assam-tenders-data/1e12e516748130c15398b2501fb092bfa7413941/data/ProcessedData/ocds-mapped-data/v2/ocds_mapped_data_fiscal_year_2016_2022_v2.csv",
        "module": "aiwatchdogs.engines.procurement",
    },
    "M2": {
        "code": "M2",
        "name": "Split-Tendering & Jurisdiction-Aware Threshold Hugging",
        "pillar": "procurement",
        "ruleset_version": RULESET_VERSIONS["M2"],
        "statutory_anchors": ["GFR-2017-R149", "GFR-2017-R154", "GFR-2017-R155", "GFR-2017-R161", "APPR-2020", "CVC-Splitting"],
        "description": (
            "Detects rolling 60-day clusters of >=3 dated awarded POs hugging 85%-99.9% of jurisdiction- and "
            "effective-date-resolved ceilings summing above the ceiling; separately retains undated tender-estimate "
            "price-band observations without claiming same-day ordering or statutory splitting."
        ),
        "reference_dataset_url": "https://raw.githubusercontent.com/CivicDataLab/assam-tenders-data/1e12e516748130c15398b2501fb092bfa7413941/data/ProcessedData/ocds-mapped-data/v2/ocds_mapped_data_fiscal_year_2016_2022_v2.csv",
        "module": "aiwatchdogs.engines.procurement",
    },
    "M3": {
        "code": "M3",
        "name": "Repeat-Winner Vendor Concentration & Bid-Spread Clustering",
        "pillar": "procurement",
        "ruleset_version": RULESET_VERSIONS["M3"],
        "statutory_anchors": ["CCI-Bid-Rigging-Indicators", "OECD-Collusion-Guidelines"],
        "description": (
            "Computes buyer-category Herfindahl-Hirschman Index (HHI > 0.35), top-supplier share (>40%), "
            "and narrow L1-L2 bid-spread clustering (<=3%), explicitly publishing which threshold fired, "
            "the multi-year fiscal window, and excluded unawarded share."
        ),
        "reference_dataset_url": "https://raw.githubusercontent.com/CivicDataLab/assam-tenders-data/1e12e516748130c15398b2501fb092bfa7413941/data/ProcessedData/ocds-mapped-data/current/ocds_mapped_data.json.zip",
        "module": "aiwatchdogs.engines.procurement",
    },
    "M4": {
        "code": "M4",
        "name": "Unit-Price Outlier, Award-vs-Estimate Variance & Post-Award Escalation",
        "pillar": "procurement",
        "ruleset_version": RULESET_VERSIONS["M4"],
        "statutory_anchors": ["GeM-L1-Benchmarking", "CVC-Variation-Orders"],
        "description": (
            "Flags normalized BOQ item unit prices exceeding peer median by >2.5 MAD (or >1.75x median), "
            "post-award variations >20% (where final values exist), and award-vs-estimate variances >20% "
            "explicitly distinguished from final contract cost overruns."
        ),
        "reference_dataset_url": "https://raw.githubusercontent.com/CivicDataLab/himachal-pradesh-health-procurement-OCDS/e28aec0b039ab5c7eba4da758b20c297cd1f83a4/HP%20Health%20Procurements(FY%202017-18%20to%202020-21%20July).xlsx",
        "module": "aiwatchdogs.engines.procurement",
    },
    "M5": {
        "code": "M5",
        "name": "March Rush Expenditure Pacing & Same-Year Budget Variance",
        "pillar": "budget",
        "ruleset_version": RULESET_VERSIONS["M5"],
        "statutory_anchors": ["GFR-2017-R62(3)", "MoF-Cash-Management", "PAC-100Cr-Explanatory-Note"],
        "description": (
            "Flags ministries, state treasuries, and matched OBDS grant/account heads breaching Q4 >33% "
            "or March >15% ceilings and >25% same-year BE/RE vs Actuals variances while preserving unobserved "
            "estimates and PAC explanatory note filing status as None."
        ),
        "reference_dataset_url": "https://raw.githubusercontent.com/CivicDataLab/child-budget-tracker/ff03978dbc64fc6eba95fe6ad931b074c52df3c0/data/processed_data/andhra_pradesh_child_treasury.csv",
        "module": "aiwatchdogs.engines.fiscal",
    },
    "M6": {
        "code": "M6",
        "name": "CAG Linked Audit Para & Dated Disposition Tracker",
        "pillar": "audit",
        "ruleset_version": RULESET_VERSIONS["M6"],
        "statutory_anchors": ["CAG-Audit-Reports", "APMS-ATN-Guidelines", "PAC-ATR"],
        "description": (
            "Extracts quantified CAG audit paragraphs requiring explicit heading/sentence/table-cell linkage "
            "for rupee impacts and dated follow-up evidence for ATN disposition and pending duration."
        ),
        "reference_dataset_url": "https://d1jmc0u14z2tmp.cloudfront.net/uploads/download_audit_report/2025/Report-of-CAG-March-2024-English-for-Approval_02-Feb-069ca6947a16ae2.43363906.pdf",
        "module": "aiwatchdogs.engines.fiscal",
    },
    "M7": {
        "code": "M7",
        "name": "PMGSY, MPLADS & MGNREGA Physical-Financial Verification",
        "pillar": "scheme",
        "ruleset_version": RULESET_VERSIONS["M7"],
        "statutory_anchors": ["PMGSY-OMMAS", "MPLADS-eSAKSHI", "MGNREGA-Sec22-60-40", "MGNREGA-Sec17-SocialAudit"],
        "description": (
            "Verifies PMGSY financial vs road-km gaps (preserving missing physical progress as None and marking "
            "aggregate/fallback IDs), deterministic MPLADS works age using explicit as_of_date, and MGNREGA "
            "60:40 wage-material ratios & R9.2 social audit recovery."
        ),
        "reference_dataset_url": "https://raw.githubusercontent.com/aditya-kr86/PMGSY_Multiclass_ML/1538fd11fd6e64233bd494c79af55e6811266f34/data/PMGSY_DATASET.csv",
        "module": "aiwatchdogs.engines.schemes",
    },
    "M8": {
        "code": "M8",
        "name": "Corporate-Electoral-Procurement Proximity Disclosure Lead",
        "pillar": "corporate_political",
        "ruleset_version": RULESET_VERSIONS["M8"],
        "statutory_anchors": ["MCA21-Public-Registry", "ECI-Public-Disclosures", "GFR-Conflict-of-Interest"],
        "description": (
            "Surfaces neutral governance disclosure leads for <180-day newly incorporated major winners, "
            "shared-DIN competing bidders, and SC-disclosed Electoral Bond x MCA CSR normalized-name overlaps "
            "with explicit join_basis, ambiguity metadata, and disclosed_bond_value framing."
        ),
        "reference_dataset_url": "https://raw.githubusercontent.com/saisantoshv3/electoral_bonds/aa8b9e022d29f762fd174fa9371ad2e02a85e777/data/final.csv",
        "module": "aiwatchdogs.engines.proximity",
    },
    "P1": {
        "code": "P1",
        "name": "Police Budget-Head Variance (official budget documents)",
        "pillar": "budget",
        "ruleset_version": RULESET_VERSIONS["P1"],
        "statutory_anchors": ["Article-202-203-Constitution", "GFR-2017-R62", "MH-2055-4055-LMMH"],
        "description": (
            "Reads police budget heads (Major Heads 2055 / 4055) from checksummed official budget PDFs at "
            "task-specified pages and printed lines, then compares Actuals, Budget Estimates and Revised "
            "Estimates against stated thresholds. Every figure is cited with page and printed line."
        ),
        "reference_dataset_url": "https://www.indiabudget.gov.in/budget2025-26/doc/eb/sbe51.pdf",
        "module": "aiwatchdogs.engines.police",
    },
    "UB1": {
        "code": "UB1",
        "name": "Union Budget Lines: Budget Estimate vs Revised Estimate vs Actual",
        "pillar": "budget",
        "ruleset_version": RULESET_VERSIONS["UB1"],
        "statutory_anchors": ["Article-112-114-Constitution", "GFR-2017-R62", "FRBM-Act-2003"],
        "description": (
            "Reads numbered lines of the Notes on Demands for Grants from two checksummed Union Budget PDFs: "
            "the budget and revised estimates of a financial year from the budget presented one year later, "
            "and the actual expenditure from the budget presented two years later. Reports the part of a "
            "line's budget estimate that was not spent, citing page and printed row for every figure."
        ),
        "reference_dataset_url": "https://www.indiabudget.gov.in/budget2025-26/doc/eb/sbe46.pdf",
        "module": "aiwatchdogs.engines.union_budget",
    },
    "OR1": {
        "code": "OR1",
        "name": "One Road, Two Bills: Union Duplicate Billing & Dual-Agency Claims",
        "pillar": "audit",
        "ruleset_version": RULESET_VERSIONS["OR1"],
        "statutory_anchors": ["Constitution-Art-151", "CAG-DPC-Act-1971", "GFR-2017-R21", "GFR-2017-R62"],
        "description": (
            "Reads non-overlapping section windows of checksummed Union Government CAG Audit Report PDFs "
            "(past 2 years) to identify documented duplicate billing, double payments, two receipts, or "
            "irregular dual-agency claims on executed projects while excluding legitimate cost-sharing."
        ),
        "reference_dataset_url": "https://d1jmc0u14z2tmp.cloudfront.net/uploads/download_audit_report/2026/5of2026UGEnglish-069ce38b4a151b0.15050638.pdf",
        "module": "aiwatchdogs.engines.one_road_two_bills",
    },
    "P5": {
        "code": "P5",
        "name": "One Teacher, Five Grades: Single-Teacher & Primary School Concentration",
        "pillar": "scheme",
        "ruleset_version": RULESET_VERSIONS["P5"],
        "statutory_anchors": [
            "Constitution-Art-21A",
            "RTE-Act-2009-Sec19-Schedule",
            "UDISE-Plus-MoE",
            "Samagra-Shiksha-Framework",
        ],
        "description": (
            "Reads checksummed official Ministry of Education UDISE+ national booklets across Tables 2.2, "
            "3.3, 4.3, and 5.2 to measure single-teacher school concentration, students per single-teacher "
            "school vs RTE 30:1 norms, and Government Primary (Classes I-V) average teachers per school vs RTE "
            "2-teacher minimum."
        ),
        "reference_dataset_url": "https://dashboard.udiseplus.gov.in/report2026/static/media/UDISE+2025_26_Booklet_existing.edd7cd16d934a10377ba.pdf",
        "module": "aiwatchdogs.engines.one_teacher_five_grades",
    },
    "P6": {
        "code": "P6",
        "name": "Parliament's To-Do List: Ministerial Assurances & Follow-Up Verification",
        "pillar": "parliament",
        "ruleset_version": RULESET_VERSIONS["P6"],
        "statutory_anchors": [
            "Lok-Sabha-Rule-323",
            "Rajya-Sabha-Rule-211A",
            "MPA-Assurances-Manual",
            "RTI-Act-2005-Sec-4-1-b",
        ],
        "description": (
            "Reads checksummed official Digital Sansad Lok Sabha and Rajya Sabha Government Assurance "
            "records (plus linked Question and Implementation Report PDFs) across Union Ministries, "
            "citing the original promise, recorded status, and what the follow-up actually answered."
        ),
        "reference_dataset_url": "https://sansad.in/api_ls/govtAssurance/assurances?loksabha=18&session=7&qNo=544&qType=SQ&page=1&size=20&locale=en",
        "module": "aiwatchdogs.engines.parliaments_to_do_list",
    },
    "P7": {
        "code": "P7",
        "name": "Only One Bid: Single-Bidder Tenders & Repeat-Winner Procurement Patterns",
        "pillar": "procurement",
        "ruleset_version": RULESET_VERSIONS["P7"],
        "statutory_anchors": [
            "GFR-2017-R166",
            "GFR-2017-R173(xx)",
            "CVC-Single-Bid-Guidelines",
            "DoE-Procurement-Manual",
            "Competition-Act-2002-Sec3(3)(d)",
            "RTI-Act-2005-Sec4(1)(b)",
        ],
        "description": (
            "Audits pinned public Indian government procurement tenders and contract awards (2016–2026) "
            "for unexplained single-bidder competitive tenders and repeat-winner concentration within "
            "procuring-authority cohorts while transparently clearing documented PAC/OEM and emergency exemptions."
        ),
        "reference_dataset_url": "https://raw.githubusercontent.com/CivicDataLab/assam-tenders-data/1e12e516748130c15398b2501fb092bfa7413941/data/ProcessedData/ocds-mapped-data/v2/ocds_mapped_data_fiscal_year_2016_2022_v2.csv",
        "module": "aiwatchdogs.engines.only_one_bid",
    },
    "P8": {
        "code": "P8",
        "name": "Sovereign Lies: Indian Government Scheme Promises vs Subsequent Walk-Backs",
        "pillar": "scheme",
        "ruleset_version": RULESET_VERSIONS["P8"],
        "statutory_anchors": [
            "Constitution-Art-265",
            "Income-Tax-Act-Sec-47-viic",
            "Finance-Act-2026",
            "RBI-SGB-Notifications",
            "RTI-Act-2005-Sec-4-1-b",
        ],
        "description": (
            "Verifies checksummed official Union and State/UT government launch notifications, Finance Acts, "
            "RBI circulars, Parliamentary records, and CAG audit reports to compare launch scheme commitments "
            "against subsequent tax reversals, benefit cuts, discontinuations, eligibility narrowing, lock-in "
            "changes, or arrears — anchored by the Sovereign Gold Bond (SGB) Scheme while transparently clearing "
            "honored or grandfathered commitments."
        ),
        "reference_dataset_url": "https://www.indiabudget.gov.in/doc/memo.pdf",
        "module": "aiwatchdogs.engines.sovereign_lies",
    },
    "P9": {
        "code": "P9",
        "name": "Auditing Government Exams: Indian Public Examination Conduct & Disruption Audit",
        "pillar": "audit",
        "ruleset_version": RULESET_VERSIONS["P9"],
        "statutory_anchors": [
            "Public-Examinations-Prevention-of-Unfair-Means-Act-2024",
            "Constitution-Art-14-16",
            "Constitution-Art-315-320",
            "RTI-Act-2005-Sec-4-1-b",
        ],
        "description": (
            "Verifies checksummed official Union and State/UT public examination notices, Parliamentary "
            "replies, and agency bulletins across a 3-year window (Nov 2023 – Nov 2026) to audit whether "
            "government examinations were conducted flawlessly as scheduled or experienced delays, "
            "postponements, reschedules, cancellations before or after conduct, or technical/partial re-exam "
            "disruptions — while transparently clearing verified flawless examinations."
        ),
        "reference_dataset_url": "https://www.nta.ac.in/",
        "module": "aiwatchdogs.engines.auditing_government_exams",
    },
    "P10": {
        "code": "P10",
        "name": "Innocent But Locked Up: Post-Acquittal Detention Overruns & Contempt of Court Compliance",
        "pillar": "justice",
        "ruleset_version": RULESET_VERSIONS["P10"],
        "statutory_anchors": [
            "Constitution-Art-21",
            "Contempt-of-Courts-Act-1971",
            "BNSS-2023-Sec-479-482",
            "SC-FASTER-Compliance-Directives",
            "RTI-Act-2005-Sec-4-1-b",
        ],
        "description": (
            "Verifies checksummed official Indian Supreme Court and High Court orders, prison/police "
            "compliance records, NHRC/SHRC orders, NALSA/UTRC reports, Digital Sansad records, and CAG "
            "audits (2021–2026) tracking cases where a court ordered acquittal or release, prisons delayed "
            "release, contempt or habeas corpus action followed, and release or remediation was ordered — "
            "while transparently clearing verified timely-release compliance controls."
        ),
        "reference_dataset_url": "https://api.sci.gov.in/",
        "module": "aiwatchdogs.engines.innocent_but_locked_up",
    },
}

_ENGINE_DISPATCH: dict[str, tuple[EngineCallable, Any]] = {
    "M1": (procurement.run_m1_single_bidder, procurement),
    "M2": (procurement.run_m2_split_tendering, procurement),
    "M3": (procurement.run_m3_vendor_concentration, procurement),
    "M4": (procurement.run_m4_unit_price_outliers, procurement),
    "M5": (fiscal.run_m5_march_rush, fiscal),
    "M6": (fiscal.run_m6_cag_paras, fiscal),
    "M7": (schemes.run_m7_welfare_schemes, schemes),
    "M8": (proximity.run_m8_corporate_proximity, proximity),
    "P1": (police.run_p1_police_budget_heads, police),
    "UB1": (union_budget.run_ub1_union_budget_lines, union_budget),
    "OR1": (one_road_two_bills.run_or1_duplicate_billing, one_road_two_bills),
    "P5": (one_teacher_five_grades.run_p5_single_teacher_schools, one_teacher_five_grades),
    "P6": (parliaments_to_do_list.run_p6_parliamentary_assurances, parliaments_to_do_list),
    "P7": (only_one_bid.run_p7_only_one_bid, only_one_bid),
    "P8": (sovereign_lies.run_p8_sovereign_lies, sovereign_lies),
    "P9": (auditing_government_exams.run_p9_auditing_government_exams, auditing_government_exams),
    "P10": (innocent_but_locked_up.run_p10_innocent_but_locked_up, innocent_but_locked_up),
}


def _hash_module(module: Any) -> str:
    source_path = inspect.getsourcefile(module)
    if source_path and Path(source_path).exists():
        return compute_sha256(Path(source_path).read_bytes())
    return compute_sha256(inspect.getsource(module))


def _normalize_as_of_date(value: str | date | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    raw = text.split("T", 1)[0]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return raw


def _canonical_rule_config(context: dict[str, Any] | None) -> dict[str, Any]:
    if not context:
        return {}
    out: dict[str, Any] = {}
    for key in sorted(context.keys()):
        if key in _NON_RULE_CONTEXT_KEYS:
            continue
        val = context[key]
        if isinstance(val, (str, int, float, bool)) or val is None:
            out[key] = val
        elif isinstance(val, (list, dict)):
            try:
                json.dumps(val, sort_keys=True)
                out[key] = val
            except TypeError:
                out[key] = str(val)
    return out


def get_engine_fingerprint(
    methodology_code: str,
    *,
    config: dict[str, Any] | None = None,
    as_of_date: str | date | datetime | None = None,
) -> dict[str, Any]:
    """Compute a complete deterministic fingerprint manifest covering engine, loader, safety, ruleset, config, and as_of_date."""
    code = methodology_code.upper().strip()
    if code not in _ENGINE_DISPATCH:
        raise ValueError(
            f"Unknown methodology code: {methodology_code!r}. Expected one of {sorted(_ENGINE_DISPATCH)}."
        )
    _, engine_module = _ENGINE_DISPATCH[code]
    ruleset_version = RULESET_VERSIONS[code]
    normalized_as_of = _normalize_as_of_date(
        as_of_date if as_of_date is not None else (config or {}).get("as_of_date")
    )
    rule_cfg = _canonical_rule_config(config)
    config_hash = compute_sha256(json.dumps(rule_cfg, sort_keys=True, separators=(",", ":")))

    engine_module_sha256 = _hash_module(engine_module)
    loader_sha256 = _hash_module(connector_loader)
    core_safety_sha256 = _hash_module(core_safety)
    registry_sha256 = _hash_module(inspect.getmodule(get_engine_fingerprint))

    manifest_core = {
        "methodology_code": code,
        "engine_version": ENGINE_VERSION,
        "ruleset_version": ruleset_version,
        "engine_module_sha256": engine_module_sha256,
        "loader_sha256": loader_sha256,
        "core_safety_sha256": core_safety_sha256,
        "registry_sha256": registry_sha256,
        "config_hash": config_hash,
        "as_of_date": normalized_as_of,
    }
    combined_hash = compute_sha256(
        json.dumps(manifest_core, sort_keys=True, separators=(",", ":"))
    )
    return {
        **manifest_core,
        "effective_config": rule_cfg,
        "combined_hash": combined_hash,
    }


def get_engine_script_hash(
    methodology_code: str,
    *,
    config: dict[str, Any] | None = None,
    as_of_date: str | date | datetime | None = None,
) -> str:
    """Compute the deterministic combined SHA-256 fingerprint for an engine + loader + ruleset + config + as_of_date."""
    return str(
        get_engine_fingerprint(methodology_code, config=config, as_of_date=as_of_date)["combined_hash"]
    )


def _sanitize_and_lint_lead(lead: AuditLeadCandidate, ruleset_version: str) -> AuditLeadCandidate:
    """Recursively scrub any accidental PII from all lead fields and enforce BNS Sec 356 neutrality linting."""
    clean_title, _ = scrub_pii(lead.title)
    clean_summary, _ = scrub_pii(lead.summary)
    clean_entity, _ = scrub_pii(lead.entity_name)
    clean_authority, _ = scrub_pii(lead.suggested_authority)
    clean_rule, _ = scrub_pii(lead.rule_triggered)
    clean_jurisdiction, _ = scrub_pii(lead.jurisdiction)
    clean_fy, _ = scrub_pii(lead.fiscal_year)
    clean_locator = scrub_pii(lead.source_locator)[0] if lead.source_locator else lead.source_locator
    clean_benign, _ = scrub_pii_in_obj(lead.benign_explanations)
    clean_citations, _ = scrub_pii_in_obj(lead.citations)
    clean_metrics, _ = scrub_pii_in_obj(lead.anomaly_metrics)

    sanitized = lead.model_copy(
        update={
            "title": clean_title,
            "summary": clean_summary,
            "entity_name": clean_entity,
            "suggested_authority": clean_authority,
            "rule_triggered": clean_rule,
            "jurisdiction": clean_jurisdiction,
            "fiscal_year": clean_fy,
            "source_locator": clean_locator,
            "benign_explanations": clean_benign,
            "citations": clean_citations,
            "anomaly_metrics": clean_metrics,
            "ruleset_version": lead.ruleset_version or ruleset_version,
        }
    )
    extra_lint: dict[str, str] = {
        "entity_name": sanitized.entity_name,
        "suggested_authority": sanitized.suggested_authority,
    }
    citation_excerpts = " ".join(
        str(c.get("excerpt") or "")
        for c in (sanitized.citations or [])
        if isinstance(c, dict) and c.get("excerpt")
    )
    if citation_excerpts.strip():
        extra_lint["citations.excerpt"] = citation_excerpts
    lint = lint_neutrality(
        sanitized.title,
        sanitized.summary,
        sanitized.benign_explanations,
        extra_fields=extra_lint,
    )
    if not lint.passed:
        raise ValueError(
            f"Engine {sanitized.methodology_code} emitted lead failing neutrality lint: {lint.violations}"
        )
    return sanitized


_STRIPPED_ENGINE_CTX_KEYS = frozenset(
    {
        "allow_local_files",
        "use_cache",
        "secondary_path",
        "max_download_bytes",
        "max_decompressed_bytes",
        "max_compression_ratio",
        "max_zip_members",
        "max_parse_rows",
        "max_pdf_pages",
        "max_pdf_chars",
    }
)


def run_engine(
    methodology_code: str,
    records: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
    *,
    as_of_date: str | date | datetime | None = None,
) -> EngineRunResult:
    """Run one deterministic civic-audit engine (M1-M8, P1, UB1) with full fingerprint manifest, neutrality & recursive PII safety."""
    code = methodology_code.upper().strip()
    if code not in _ENGINE_DISPATCH:
        raise ValueError(
            f"Unknown methodology code: {methodology_code!r}. Expected one of {sorted(_ENGINE_DISPATCH)}."
        )

    effective_ctx = {
        k: v for k, v in (context or {}).items() if k not in _STRIPPED_ENGINE_CTX_KEYS
    }
    normalized_as_of = _normalize_as_of_date(
        as_of_date if as_of_date is not None else effective_ctx.get("as_of_date")
    )
    if normalized_as_of is not None:
        effective_ctx["as_of_date"] = normalized_as_of

    engine_fn, _ = _ENGINE_DISPATCH[code]
    raw_result = engine_fn(records, effective_ctx or None)
    if len(raw_result) == 5:
        raw_leads, cleared_count, monetary_audited, summary, engine_meta = raw_result
    else:
        raw_leads, cleared_count, monetary_audited, summary = raw_result
        engine_meta = {}

    ruleset_version = RULESET_VERSIONS[code]
    verified_leads = [_sanitize_and_lint_lead(lead, ruleset_version) for lead in raw_leads]
    fingerprint = get_engine_fingerprint(code, config=effective_ctx, as_of_date=normalized_as_of)

    return EngineRunResult(
        methodology_code=code,
        records_scanned=len(records),
        monetary_audited_inr=monetary_audited,
        leads=verified_leads,
        cleared_count=cleared_count,
        unevaluable_count=int(engine_meta.get("unevaluable_count", 0)),
        script_hash=str(fingerprint["combined_hash"]),
        summary=summary,
        engine_version=ENGINE_VERSION,
        ruleset_version=ruleset_version,
        as_of_date=normalized_as_of or engine_meta.get("as_of_date"),
        fingerprint_manifest=fingerprint,
        amount_type=engine_meta.get("amount_type"),
    )
