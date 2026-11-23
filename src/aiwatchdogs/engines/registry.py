"""Registry and dispatcher for all 8 deterministic Indian civic-audit engines (M1-M8)."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aiwatchdogs.core.models import AuditLeadCandidate, EngineRunResult
from aiwatchdogs.core.safety import compute_sha256, lint_neutrality, scrub_pii, scrub_pii_in_obj
from aiwatchdogs.engines import fiscal, procurement, proximity, schemes

EngineCallable = Callable[
    [list[dict[str, Any]], dict[str, Any] | None],
    tuple[list[AuditLeadCandidate], int, float, str],
]

METHODOLOGY_CATALOG: dict[str, dict[str, Any]] = {
    "M1": {
        "code": "M1",
        "name": "Single-Bidder & Shortened Bid-Window Concentration",
        "pillar": "procurement",
        "statutory_anchors": ["GFR-2017-R166", "CVC-Single-Tender-Guidelines", "OECD-Procurement-RedFlags"],
        "description": "Detects public buyers with >35% single-bidder competitive tenders, repeat single-bidder vendors (>=3), or shortened bid windows (<14 days).",
        "reference_dataset_url": "https://raw.githubusercontent.com/CivicDataLab/assam-tenders-data/main/data/ProcessedData/ocds-mapped-data/v2/ocds_mapped_data_fiscal_year_2016_2022_v2.csv",
        "module": "aiwatchdogs.engines.procurement",
    },
    "M2": {
        "code": "M2",
        "name": "Split-Tendering & GFR 2017 Threshold Hugging",
        "pillar": "procurement",
        "statutory_anchors": ["GFR-2017-R149", "GFR-2017-R154", "GFR-2017-R155", "GFR-2017-R161", "CVC-Splitting"],
        "description": "Detects rolling 60-day clusters of >=3 purchase orders hugging 85%-99.9% of INR 50k, 2.5L, 10L, or 50L ceilings whose sum exceeds the ceiling.",
        "reference_dataset_url": "https://raw.githubusercontent.com/CivicDataLab/assam-tenders-data/main/data/ProcessedData/ocds-mapped-data/v2/ocds_mapped_data_fiscal_year_2016_2022_v2.csv",
        "module": "aiwatchdogs.engines.procurement",
    },
    "M3": {
        "code": "M3",
        "name": "Repeat-Winner Vendor Concentration & Bid-Spread Clustering",
        "pillar": "procurement",
        "statutory_anchors": ["CCI-Bid-Rigging-Indicators", "OECD-Collusion-Guidelines"],
        "description": "Computes buyer-category Herfindahl-Hirschman Index (HHI > 0.35), top-vendor share (>40%), and narrow L1-L2 bid-spread clustering (<=3%).",
        "reference_dataset_url": "https://raw.githubusercontent.com/CivicDataLab/assam-tenders-data/main/data/ProcessedData/ocds-mapped-data/current/ocds_mapped_data.json.zip",
        "module": "aiwatchdogs.engines.procurement",
    },
    "M4": {
        "code": "M4",
        "name": "Unit-Price Outlier & Post-Award Cost Escalation",
        "pillar": "procurement",
        "statutory_anchors": ["GeM-L1-Benchmarking", "CVC-Variation-Orders"],
        "description": "Flags normalized BOQ item unit prices exceeding peer median by >2.5 MAD (or >1.75x median), post-award variations >20%, and awards exceeding tender estimates by >20%.",
        "reference_dataset_url": "https://raw.githubusercontent.com/CivicDataLab/himachal-pradesh-health-procurement-OCDS/master/HP%20Health%20Procurements(FY%202017-18%20to%202020-21%20July).xlsx",
        "module": "aiwatchdogs.engines.procurement",
    },
    "M5": {
        "code": "M5",
        "name": "March Rush Expenditure Pacing & Budget Variance",
        "pillar": "budget",
        "statutory_anchors": ["GFR-2017-R62(3)", "MoF-Cash-Management", "PAC-100Cr-Explanatory-Note"],
        "description": "Flags ministries, state treasuries, and OBDS demands breaching Q4 >33% or March >15% ceilings and >25% BE/RE vs Actuals variances.",
        "reference_dataset_url": "https://raw.githubusercontent.com/CivicDataLab/child-budget-tracker/develop_0.1/data/processed_data/andhra_pradesh_child_treasury.csv",
        "module": "aiwatchdogs.engines.fiscal",
    },
    "M6": {
        "code": "M6",
        "name": "CAG Unresolved Audit Para & Repeat-Finding Tracker",
        "pillar": "audit",
        "statutory_anchors": ["CAG-Audit-Reports", "APMS-ATN-Guidelines", "PAC-ATR"],
        "description": "Extracts and quantifies high-value unresolved or multi-cycle recurring CAG audit paragraphs from structured tables or official CAG PDF reports.",
        "reference_dataset_url": "https://d1jmc0u14z2tmp.cloudfront.net/uploads/download_audit_report/2025/Report-of-CAG-March-2024-English-for-Approval_02-Feb-069ca6947a16ae2.43363906.pdf",
        "module": "aiwatchdogs.engines.fiscal",
    },
    "M7": {
        "code": "M7",
        "name": "PMGSY, MPLADS & MGNREGA Physical-Financial Verification",
        "pillar": "scheme",
        "statutory_anchors": ["PMGSY-OMMAS", "MPLADS-eSAKSHI", "MGNREGA-Sec22-60-40", "MGNREGA-Sec17-SocialAudit"],
        "description": "Verifies PMGSY financial vs km gaps, MPLADS >12m delayed/ungeotagged works, and MGNREGA 60:40 ratio breaches & unrecovered R9.2 social audit amounts.",
        "reference_dataset_url": "https://raw.githubusercontent.com/aditya-kr86/PMGSY_Multiclass_ML/main/data/PMGSY_DATASET.csv",
        "module": "aiwatchdogs.engines.schemes",
    },
    "M8": {
        "code": "M8",
        "name": "Corporate-Electoral-Procurement Proximity Disclosure Lead",
        "pillar": "corporate_political",
        "statutory_anchors": ["MCA21-Public-Registry", "ECI-Public-Disclosures", "GFR-Conflict-of-Interest"],
        "description": "Surfaces neutral governance disclosure leads for <180-day newly incorporated major winners, shared-DIN competing bidders, and SC-disclosed Electoral Bond x MCA CSR overlaps.",
        "reference_dataset_url": "https://raw.githubusercontent.com/saisantoshv3/electoral_bonds/main/data/final.csv",
        "module": "aiwatchdogs.engines.proximity",
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
}


def get_engine_script_hash(methodology_code: str) -> str:
    """Compute a deterministic SHA-256 hash of the engine source module."""
    code = methodology_code.upper().strip()
    if code not in _ENGINE_DISPATCH:
        raise ValueError(f"Unknown methodology code: {methodology_code!r}. Expected one of {sorted(_ENGINE_DISPATCH)}.")
    _, module = _ENGINE_DISPATCH[code]
    source_path = inspect.getsourcefile(module)
    if source_path and Path(source_path).exists():
        return compute_sha256(Path(source_path).read_bytes())
    return compute_sha256(inspect.getsource(module))


def _sanitize_and_lint_lead(lead: AuditLeadCandidate) -> AuditLeadCandidate:
    """Scrub any accidental PII from lead fields and enforce BNS Sec 356 neutrality linting."""
    clean_title, _ = scrub_pii(lead.title)
    clean_summary, _ = scrub_pii(lead.summary)
    clean_benign, _ = scrub_pii_in_obj(lead.benign_explanations)
    clean_citations, _ = scrub_pii_in_obj(lead.citations)

    sanitized = lead.model_copy(
        update={
            "title": clean_title,
            "summary": clean_summary,
            "benign_explanations": clean_benign,
            "citations": clean_citations,
        }
    )
    lint = lint_neutrality(sanitized.title, sanitized.summary, sanitized.benign_explanations)
    if not lint.passed:
        raise ValueError(
            f"Engine {sanitized.methodology_code} emitted lead failing neutrality lint: {lint.violations}"
        )
    return sanitized


def run_engine(
    methodology_code: str,
    records: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> EngineRunResult:
    """Run one deterministic civic-audit engine (M1-M8) and enforce neutrality + PII safety."""
    code = methodology_code.upper().strip()
    if code not in _ENGINE_DISPATCH:
        raise ValueError(f"Unknown methodology code: {methodology_code!r}. Expected one of {sorted(_ENGINE_DISPATCH)}.")

    engine_fn, _ = _ENGINE_DISPATCH[code]
    raw_leads, cleared_count, monetary_audited, summary = engine_fn(records, context)
    verified_leads = [_sanitize_and_lint_lead(lead) for lead in raw_leads]
    script_hash = get_engine_script_hash(code)

    return EngineRunResult(
        methodology_code=code,
        records_scanned=len(records),
        monetary_audited_inr=monetary_audited,
        leads=verified_leads,
        cleared_count=cleared_count,
        script_hash=script_hash,
        summary=summary,
    )
