"""Lawful RTI Act, 2005 Section 6(1) & Section 2(f) draft generator (<= 500 words)."""

from __future__ import annotations

from typing import Any

from aiwatchdogs.core.models import AuditLeadCandidate
from aiwatchdogs.core.safety import scrub_pii

RTI_PORTAL_URL = "https://rtionline.gov.in"
MAX_RTI_WORDS = 500

_METHODOLOGY_RECORDS: dict[str, list[str]] = {
    "M1": [
        "Certified copies of the single-tender justification certificate and competent authority approval recorded under GFR 2017 Rule 166 or applicable state procurement rules.",
        "Certified copies of Technical Evaluation Committee minutes showing reasons for bid rejection or disqualification in the referenced tenders.",
        "Certified copies of the published Notice Inviting Tender (NIT), corrigenda, and approval notes for any bid submission period under 14 days.",
    ],
    "M2": [
        "Certified copies of the consolidated annual procurement plan and demand assessment for the referenced item category during the fiscal year.",
        "Certified copies of purchase orders, Local Purchase Committee certificates (GFR Rule 155), and competent authority sanction notes for the referenced procurements.",
        "Certified copies of any internal audit observations or circular compliance records under GFR 2017 Rules 149, 154, 155, and 161.",
    ],
    "M3": [
        "Certified copies of the bid comparative statement (L1/L2/L3 financial tabulations) and tender evaluation minutes for the referenced contracts.",
        "Certified copies of eligibility criteria approval notes and pre-bid meeting minutes for the referenced tenders.",
        "Certified copies of vendor empanelment or bidder qualification lists maintained for the referenced procurement category.",
    ],
    "M4": [
        "Certified copies of the Rate Reasonability Certificate, Local Purchase Committee market survey, and GeM L1 price comparison sheet used prior to award.",
        "Certified copies of the original Bill of Quantities (BOQ), award letter, and all post-award amendment or variation order sanctions.",
        "Certified copies of competent authority financial concurrence notes approving any contract value revision above the awarded L1 amount.",
    ],
    "M5": [
        "Certified copies of the approved Monthly Expenditure Plan (MEP) and Quarterly Expenditure Plan (QEP) for the referenced fiscal year.",
        "Certified copies of Ministry of Finance cash-management relaxation or CapEx waiver letters, if issued, permitting Q4 or March expenditure above GFR Rule 62(3) ceilings.",
        "Certified copies of supplementary grant proposals, re-appropriation orders, and Public Accounts Committee (PAC) Explanatory Notes submitted for budget variances.",
    ],
    "M6": [
        "Certified copy of the latest Action Taken Note (ATN) uploaded on APMS or submitted to the Audit Office regarding the referenced CAG Audit Paragraph.",
        "Certified copies of documentary records showing recovery status, departmental inquiry closure orders, or remedial circulars issued pursuant to the audit paragraph.",
        "Certified copy of the Public Accounts Committee (PAC) Action Taken Reply (ATR) correspondence, if tabled.",
    ],
    "M7": [
        "Certified copies of the sanctioned Detailed Project Report (DPR) or administrative sanction order and fund-release Utilization Certificates (UCs).",
        "Certified copies of the Measurement Book (MB) abstract, physical progress inspection reports (NQM/SQM/District Authority), and geotagged stage inspection logs.",
        "Certified copies of Gram Sabha Social Audit Action Taken Reports (ATRs), recovery notices, or Defect Liability Period (DLP) notices issued for the referenced works.",
    ],
    "M8": [
        "Certified copies of the pre-qualification and technical eligibility verification checklist confirming vendor turnover, experience, and bid-capacity criteria.",
        "Certified copies of conflict-of-interest declarations and integrity pact undertakings submitted by participating bidders under the referenced tender.",
        "Certified copies of Tender Evaluation Committee minutes recording bidder ownership and eligibility scrutiny prior to contract award.",
    ],
}

_DEFAULT_RECORDS: list[str] = [
    "Certified copies of the administrative approval and financial sanction order for the referenced public expenditure record.",
    "Certified copies of the evaluation committee minutes, comparative statement, and utilization or completion certificate on file.",
    "Certified copies of proactive disclosure records published under Section 4(1)(b) of the RTI Act, 2005 for the referenced activity.",
]


def _format_inr(amount: float) -> str:
    if amount >= 10_000_000:
        return f"INR {amount / 10_000_000:.2f} crore"
    if amount >= 100_000:
        return f"INR {amount / 100_000:.2f} lakh"
    if amount > 0:
        return f"INR {amount:,.0f}"
    return "recorded public outlay"


def _extract_reference_ids(finding: dict[str, Any]) -> str:
    refs: list[str] = []
    citations = finding.get("citations") or []
    for cit in citations[:4]:
        if not isinstance(cit, dict):
            continue
        for key in ("record_id", "row_id", "tender_id", "para_id", "work_id", "ocid"):
            val = cit.get(key)
            if val and str(val) not in refs:
                refs.append(str(val))
    if not refs:
        rule = finding.get("rule_triggered")
        if rule:
            refs.append(str(rule))
    return ", ".join(refs[:4]) if refs else "cited public portal records"


def _trim_to_word_limit(text: str, max_words: int = MAX_RTI_WORDS) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    # Trim cleanly on line boundaries where possible, then fallback to hard word cap.
    lines = text.splitlines()
    kept: list[str] = []
    running = 0
    for line in lines:
        line_words = line.split()
        if running + len(line_words) <= max_words:
            kept.append(line)
            running += len(line_words)
        else:
            remaining = max_words - running
            if remaining > 0:
                kept.append(" ".join(line_words[:remaining]))
            break
    result = "\n".join(kept).strip()
    if len(result.split()) > max_words:
        result = " ".join(result.split()[:max_words])
    return result


def generate_rti_draft(
    finding: dict[str, Any] | AuditLeadCandidate,
    public_authority: str | None = None,
) -> dict[str, Any]:
    """Generate a lawful RTI Act, 2005 Sec 6(1) & Sec 2(f) application draft (<= 500 words).

    Requests only specific existing public records (never accusatory statements or "why" questions),
    references https://rtionline.gov.in, notes the INR 10 fee / BPL Sec 7(5) exemption, and notes
    the 30-day statutory reply deadline under Section 7(1).
    """
    data = finding.model_dump() if isinstance(finding, AuditLeadCandidate) else dict(finding)

    authority = (
        (public_authority or "").strip()
        or str(data.get("suggested_authority") or "").strip()
        or str(data.get("entity_name") or "").strip()
        or "Concerned Public Authority, Government of India"
    )
    jurisdiction = str(data.get("jurisdiction") or "Union of India").strip()
    fiscal_year = str(data.get("fiscal_year") or "Relevant Fiscal Year").strip()
    entity_name = str(data.get("entity_name") or authority).strip()
    method_code = str(data.get("methodology_code") or "M1").upper().strip()
    exposure_inr = float(data.get("monetary_exposure_inr") or 0.0)

    is_state = jurisdiction.lower().startswith("state:") or (
        jurisdiction.lower() not in {"union", "union of india", "india", "central", ""}
    )
    pio_title = (
        "State Public Information Officer (SPIO)"
        if is_state
        else "Central Public Information Officer (CPIO)"
    )
    cpio_designation = f"{pio_title}, {authority}"

    requested_records = list(_METHODOLOGY_RECORDS.get(method_code, _DEFAULT_RECORDS))
    ref_ids = _extract_reference_ids(data)
    exposure_str = _format_inr(exposure_inr)

    raw_subject = (
        f"RTI Application u/s 6(1) for certified public records regarding {entity_name} "
        f"({fiscal_year}, Ref: {ref_ids})"
    )
    subject, _ = scrub_pii(raw_subject)
    # Keep subject crisp
    if len(subject.split()) > 35:
        subject = " ".join(subject.split()[:35])

    numbered_records = "\n".join(
        f"{idx}. {rec}" for idx, rec in enumerate(requested_records, start=1)
    )

    body_lines = [
        f"To,",
        f"The {cpio_designation}",
        f"Jurisdiction: {jurisdiction}",
        f"Online Filing Portal: {RTI_PORTAL_URL}",
        "",
        f"Subject: {subject}",
        "",
        "Sir/Madam,",
        "",
        (
            f"Under Section 6(1) read with Section 2(f) and Section 4(1)(b) of the Right to "
            f"Information Act, 2005, I respectfully request certified copies (electronic PDF or "
            f"photocopy) of the following existing public records held by {authority} pertaining "
            f"to {entity_name} for {fiscal_year} (Reference IDs: {ref_ids}; noted public outlay "
            f"approx. {exposure_str}):"
        ),
        "",
        numbered_records,
        "",
        (
            "Where any sought record is already proactively published under Section 4(1)(b), "
            "kindly provide the exact official URL and document reference."
        ),
        "",
        (
            "Fee & Timeline Note: The prescribed application fee of Rs. 10 (Rupees Ten only) "
            "is enclosed / payable via https://rtionline.gov.in (applicants holding a valid Below "
            "Poverty Line card are exempt from fee under Section 7(5) of the RTI Act, 2005). "
            "Kindly furnish the requested information within the statutory period of 30 days under "
            "Section 7(1), or transfer under Section 6(3) within 5 days if held by another public "
            "authority. Details of the First Appellate Authority under Section 19(1) may also be "
            "indicated in the reply."
        ),
        "",
        "Applicant Name: [Citizen Name]",
        "Address / Email: [Citizen Address / Email]",
        "Date: [DD/MM/YYYY]",
    ]

    raw_body = "\n".join(body_lines)
    scrubbed_body, _ = scrub_pii(raw_body)
    final_body = _trim_to_word_limit(scrubbed_body, MAX_RTI_WORDS)
    word_count = len(final_body.split())

    return {
        "public_authority": authority,
        "cpio_designation": cpio_designation,
        "rti_portal_url": RTI_PORTAL_URL,
        "statutory_sections": [
            "Section 6(1)",
            "Section 2(f)",
            "Section 4(1)(b)",
            "Section 7(1)",
            "Section 7(5)",
        ],
        "subject": subject,
        "body_text": final_body,
        "word_count": word_count,
        "requested_records": requested_records,
    }
