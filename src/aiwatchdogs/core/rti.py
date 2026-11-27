"""Lawful RTI Act, 2005 Section 6(1) & Section 2(f) draft generator (<= 500 words).

Resolves record-holder jurisdiction (Central vs State) so State-authority RTI drafts
never direct citizens to the Central portal (https://rtionline.gov.in), which returns
state-subject applications without refund. Preserves strict human-only filing.
"""

from __future__ import annotations

import math
import re
from typing import Any

from aiwatchdogs.core.models import AuditLeadCandidate
from aiwatchdogs.core.safety import scan_forbidden_terms, scrub_pii

RTI_PORTAL_URL = "https://rtionline.gov.in"
CENTRAL_RTI_PORTAL_URL = RTI_PORTAL_URL
MAX_RTI_WORDS = 500

_CENTRAL_PORTAL_WARNING = (
    "Do NOT file state-authority RTI applications on the Central portal (rtionline.gov.in); "
    "applications pertaining to State public authorities are returned without refund."
)

# Official State RTI portals, filing channels, and statutory fee rules.
_STATE_RTI_REGISTRY: dict[str, dict[str, Any]] = {
    "assam": {
        "state_name": "Assam",
        "portal_url": "https://rtionline.assam.gov.in",
        "filing_channel": "Assam State RTI Online Portal (https://rtionline.assam.gov.in) or speed/registered post to the concerned SPIO",
        "fee_rules": (
            "Rs. 10 under the Assam Right to Information (Regulation of Fee and Cost) Rules, 2005 "
            "(payable online on rtionline.assam.gov.in or via Treasury Challan / Court Fee Stamp / IPO / DD; "
            "BPL applicants are exempt under Section 7(5) of the RTI Act, 2005)"
        ),
    },
    "andhra pradesh": {
        "state_name": "Andhra Pradesh",
        "portal_url": "https://rtionline.ap.gov.in",
        "filing_channel": "Andhra Pradesh State RTI Online Portal (https://rtionline.ap.gov.in) or speed/registered post to the concerned SPIO",
        "fee_rules": (
            "Rs. 10 (Rs. 5 at Mandal/Village level where prescribed) under the Andhra Pradesh RTI "
            "(Regulation of Fee and Cost) Rules, 2005 (payable via state portal, Court Fee Stamp, "
            "Treasury Challan, or IPO; BPL applicants are exempt under Section 7(5))"
        ),
    },
    "bihar": {
        "state_name": "Bihar",
        "portal_url": "https://jaankari.bihar.gov.in",
        "filing_channel": "Bihar Jaankari RTI Portal (https://jaankari.bihar.gov.in) or speed/registered post to the concerned SPIO",
        "fee_rules": (
            "Rs. 10 under the Bihar Right to Information Rules, 2005 (payable via Jaankari portal, "
            "non-judicial stamp, Treasury Challan, or IPO/DD; BPL applicants are exempt under Section 7(5))"
        ),
    },
    "himachal pradesh": {
        "state_name": "Himachal Pradesh",
        "portal_url": "https://rtionline.hp.gov.in",
        "filing_channel": "Himachal Pradesh RTI Online Portal (https://rtionline.hp.gov.in) or speed/registered post to the concerned SPIO",
        "fee_rules": (
            "Rs. 10 under the Himachal Pradesh Right to Information Rules, 2006 (payable online or via "
            "Treasury Challan / IPO / DD; BPL applicants are exempt under Section 7(5))"
        ),
    },
    "madhya pradesh": {
        "state_name": "Madhya Pradesh",
        "portal_url": "https://rtionline.mp.gov.in",
        "filing_channel": "Madhya Pradesh RTI Online Portal (https://rtionline.mp.gov.in) or speed/registered post to the concerned SPIO",
        "fee_rules": (
            "Rs. 10 under the Madhya Pradesh RTI (Fees and Appeal) Rules, 2005 (payable online or via "
            "non-judicial stamp / Treasury Challan / IPO / DD; BPL applicants are exempt under Section 7(5))"
        ),
    },
    "maharashtra": {
        "state_name": "Maharashtra",
        "portal_url": "https://rtionline.maharashtra.gov.in",
        "filing_channel": "Maharashtra RTI Online Portal (https://rtionline.maharashtra.gov.in) or speed/registered post to the concerned SPIO",
        "fee_rules": (
            "Rs. 10 under the Maharashtra Right to Information Rules, 2005 (payable online or via "
            "Court Fee Stamp / IPO / DD; BPL applicants are exempt under Section 7(5))"
        ),
    },
    "delhi": {
        "state_name": "Delhi",
        "portal_url": "https://rtionline.delhi.gov.in",
        "filing_channel": "Delhi NCT RTI Online Portal (https://rtionline.delhi.gov.in) or speed/registered post to the concerned PIO",
        "fee_rules": (
            "Rs. 10 under the Delhi Right to Information (Regulation of Fee and Cost) Rules "
            "(payable online or via IPO / DD / cash receipt; BPL applicants are exempt under Section 7(5))"
        ),
    },
    "uttar pradesh": {
        "state_name": "Uttar Pradesh",
        "portal_url": "https://rtionline.up.gov.in",
        "filing_channel": "Uttar Pradesh RTI Online Portal (https://rtionline.up.gov.in) or speed/registered post to the concerned SPIO",
        "fee_rules": (
            "Rs. 10 under the Uttar Pradesh Right to Information Rules, 2015 (payable online or via "
            "Treasury Challan / IPO / DD; BPL applicants are exempt under Section 7(5))"
        ),
    },
    "tamil nadu": {
        "state_name": "Tamil Nadu",
        "portal_url": "https://rtionline.tn.gov.in",
        "filing_channel": "Tamil Nadu RTI Online Portal (https://rtionline.tn.gov.in) or speed/registered post to the concerned SPIO",
        "fee_rules": (
            "Rs. 10 under the Tamil Nadu RTI (Fees) Rules, 2005 (payable online or via Court Fee Stamp / "
            "Treasury Challan / IPO; BPL applicants are exempt under Section 7(5))"
        ),
    },
    "karnataka": {
        "state_name": "Karnataka",
        "portal_url": "https://rtionline.karnataka.gov.in",
        "filing_channel": "Karnataka RTI Online Portal (https://rtionline.karnataka.gov.in) or speed/registered post to the concerned SPIO",
        "fee_rules": (
            "Rs. 10 under the Karnataka Right to Information Rules, 2005 (payable online or via "
            "IPO / DD / Treasury Challan; BPL applicants are exempt under Section 7(5))"
        ),
    },
    "kerala": {
        "state_name": "Kerala",
        "portal_url": "https://rtionline.kerala.gov.in",
        "filing_channel": "Kerala RTI Online Portal (https://rtionline.kerala.gov.in) or speed/registered post to the concerned SPIO",
        "fee_rules": (
            "Rs. 10 under the Kerala Right to Information (Regulation of Fee and Cost) Rules, 2006 "
            "(payable online or via Court Fee Stamp / Treasury Challan / IPO; BPL applicants are exempt under Section 7(5))"
        ),
    },
    "rajasthan": {
        "state_name": "Rajasthan",
        "portal_url": "https://rti.rajasthan.gov.in",
        "filing_channel": "Rajasthan RTI Portal (https://rti.rajasthan.gov.in) or speed/registered post to the concerned SPIO",
        "fee_rules": (
            "Rs. 10 under the Rajasthan Right to Information Rules, 2005 (payable via state portal, "
            "IPO / DD / Treasury Challan; BPL applicants are exempt under Section 7(5))"
        ),
    },
    "gujarat": {
        "state_name": "Gujarat",
        "portal_url": "https://onlinerti.gujarat.gov.in",
        "filing_channel": "Gujarat Online RTI Portal (https://onlinerti.gujarat.gov.in) or speed/registered post to the concerned SPIO",
        "fee_rules": (
            "Rs. 20 under the Gujarat Right to Information Rules, 2010 (payable online or via "
            "non-judicial stamp / IPO / DD; BPL applicants are exempt under Section 7(5))"
        ),
    },
    "odisha": {
        "state_name": "Odisha",
        "portal_url": "https://rtiodisha.gov.in",
        "filing_channel": "Odisha RTI Central Monitoring Mechanism (https://rtiodisha.gov.in) or speed/registered post to the concerned PIO",
        "fee_rules": (
            "Rs. 10 under the Odisha Right to Information Rules, 2005 (payable via portal, Treasury "
            "Challan, or IPO; BPL applicants are exempt under Section 7(5))"
        ),
    },
    "west bengal": {
        "state_name": "West Bengal",
        "portal_url": "https://wbic.gov.in",
        "filing_channel": "West Bengal State Information Commission guidance (https://wbic.gov.in) — submit by speed/registered post or in person to the concerned SPIO",
        "fee_rules": (
            "Rs. 10 under the West Bengal Right to Information Rules, 2006 (payable via Court Fee Stamp / "
            "IPO / DD / Treasury Challan; BPL applicants are exempt under Section 7(5))"
        ),
    },
    "haryana": {
        "state_name": "Haryana",
        "portal_url": "https://rtionline.haryana.gov.in",
        "filing_channel": "Haryana RTI Online Portal (https://rtionline.haryana.gov.in) or speed/registered post to the concerned SPIO",
        "fee_rules": (
            "Rs. 10 under the Haryana Right to Information Rules, 2009 (payable online or via "
            "IPO / DD / Treasury Challan; BPL applicants are exempt under Section 7(5))"
        ),
    },
    "punjab": {
        "state_name": "Punjab",
        "portal_url": "https://rtionline.punjab.gov.in",
        "filing_channel": "Punjab RTI Online Portal (https://rtionline.punjab.gov.in) or speed/registered post to the concerned SPIO",
        "fee_rules": (
            "Rs. 10 under the Punjab Right to Information Rules, 2017 (payable online or via "
            "IPO / DD / Treasury Challan; BPL applicants are exempt under Section 7(5))"
        ),
    },
    "telangana": {
        "state_name": "Telangana",
        "portal_url": "https://rti.telangana.gov.in",
        "filing_channel": "Telangana RTI Portal (https://rti.telangana.gov.in) or speed/registered post to the concerned SPIO",
        "fee_rules": (
            "Rs. 10 under the Telangana RTI (Regulation of Fee and Cost) Rules (payable via state portal, "
            "Court Fee Stamp, or IPO; BPL applicants are exempt under Section 7(5))"
        ),
    },
    "jharkhand": {
        "state_name": "Jharkhand",
        "portal_url": "https://rtionline.jharkhand.gov.in",
        "filing_channel": "Jharkhand RTI Online Portal (https://rtionline.jharkhand.gov.in) or speed/registered post to the concerned SPIO",
        "fee_rules": (
            "Rs. 10 under the Jharkhand Right to Information Rules, 2005 (payable online or via "
            "IPO / DD / Treasury Challan; BPL applicants are exempt under Section 7(5))"
        ),
    },
    "meghalaya": {
        "state_name": "Meghalaya",
        "portal_url": "",
        "filing_channel": "Physical / Speed Post filing to the concerned State Public Information Officer (SPIO), Government of Meghalaya",
        "fee_rules": (
            "Rs. 10 under the Meghalaya Right to Information Rules, 2005 (payable via Treasury Challan / "
            "IPO / DD; BPL applicants are exempt under Section 7(5))"
        ),
    },
}

# Short/alias state cues for resolving multi-state or All-India findings from authority/entity text.
_STATE_ALIASES: tuple[tuple[str, str], ...] = (
    ("himachal pradesh", "himachal pradesh"),
    ("andhra pradesh", "andhra pradesh"),
    ("madhya pradesh", "madhya pradesh"),
    ("uttar pradesh", "uttar pradesh"),
    ("west bengal", "west bengal"),
    ("tamil nadu", "tamil nadu"),
    ("maharashtra", "maharashtra"),
    ("karnataka", "karnataka"),
    ("rajasthan", "rajasthan"),
    ("telangana", "telangana"),
    ("jharkhand", "jharkhand"),
    ("meghalaya", "meghalaya"),
    ("gujarat", "gujarat"),
    ("haryana", "haryana"),
    ("kerala", "kerala"),
    ("odisha", "odisha"),
    ("orissa", "odisha"),
    ("punjab", "punjab"),
    ("assam", "assam"),
    ("bihar", "bihar"),
    ("delhi", "delhi"),
    ("hp i&ph", "himachal pradesh"),
    ("hptenders", "himachal pradesh"),
    ("assamtenders", "assam"),
    ("bodoland", "assam"),
    ("jorhat", "assam"),
    ("arwal", "bihar"),
    ("araria", "bihar"),
    ("krishna", "andhra pradesh"),
    ("bhopal", "madhya pradesh"),
)

_CENTRAL_AUTHORITY_CUES: tuple[str, ...] = (
    "government of india",
    "union of india",
    "ministry of ",
    "controller general of accounts",
    "comptroller and auditor general",
    "election commission of india",
    "ministry of corporate affairs",
    "department of posts",
    "central coalfields",
    "nlc india",
    "union government",
)

_METHODOLOGY_RECORDS: dict[str, list[str]] = {
    "M1": [
        "Certified copies of the single-tender justification certificate and competent authority approval recorded under applicable financial and procurement rules (GFR 2017 Rule 166 or equivalent State Procurement Rules).",
        "Certified copies of Technical Evaluation Committee minutes showing reasons for bid rejection or disqualification in the referenced tenders.",
        "Certified copies of the published Notice Inviting Tender (NIT), corrigenda, and approval notes for any bid submission period under 14 days.",
    ],
    "M2": [
        "Certified copies of the consolidated annual procurement plan and demand assessment for the referenced item category during the fiscal year.",
        "Certified copies of purchase orders, award letters, purchase committee certificates, and competent authority sanction notes for the referenced procurements.",
        "Certified copies of any internal audit observations or circular compliance records governing tendering monetary ceilings for this authority.",
    ],
    "M3": [
        "Certified copies of the bid comparative statement (L1/L2/L3 financial tabulations) and tender evaluation minutes for the referenced contracts.",
        "Certified copies of eligibility criteria approval notes and pre-bid meeting minutes for the referenced tenders.",
        "Certified copies of vendor empanelment or bidder qualification lists maintained for the referenced procurement category.",
    ],
    "M4": [
        "Certified copies of the Rate Reasonability Certificate, market survey, and L1 price comparison sheet used prior to award.",
        "Certified copies of the original Bill of Quantities (BOQ), award letter, actual disbursement ledger, and any post-award amendment or variation order sanctions.",
        "Certified copies of competent authority financial concurrence notes approving any contract value above the published tender estimate or awarded amount.",
    ],
    "M5": [
        "Certified copies of the approved Monthly Expenditure Plan (MEP) and Quarterly Expenditure Plan (QEP) for the referenced fiscal year.",
        "Certified copies of Finance Department cash-management relaxation or CapEx waiver letters, if issued, permitting Q4 or March expenditure above standard pacing ceilings.",
        "Certified copies of signed Grant/Appropriation accounts, supplementary grant proposals, re-appropriation orders, and PAC Explanatory Notes where applicable.",
    ],
    "M6": [
        "Certified copy of the latest Action Taken Note (ATN) uploaded on APMS or submitted to the Audit Office regarding the referenced CAG Audit Paragraph.",
        "Certified copies of documentary records showing recovery status, departmental inquiry closure orders, or remedial circulars issued pursuant to the audit paragraph.",
        "Certified copy of the Public Accounts Committee (PAC) Action Taken Reply (ATR) correspondence, if tabled.",
    ],
    "M7": [
        "Certified copies of package-level Detailed Project Reports (DPRs), administrative sanction orders, and fund-release Utilization Certificates (UCs) for the referenced works.",
        "Certified copies of the Measurement Book (MB) abstract, physical progress inspection reports (NQM/SQM/District Authority), and geotagged stage inspection logs.",
        "Certified copies of Gram Sabha Social Audit Action Taken Reports (ATRs), recovery notices, or Defect Liability Period (DLP) notices issued for the referenced works.",
    ],
    "M8": [
        "Certified copies of statutory proactive disclosure records published under Section 4(1)(b) of the RTI Act, 2005 relevant to the referenced public disclosures.",
        "Certified copies of corporate registration / CIN verification records or conflict-of-interest declarations where a public tender award exists.",
        "Certified copies of Tender Evaluation Committee minutes recording bidder ownership and eligibility scrutiny, if any public procurement contract was awarded.",
    ],
    "P6": [
        "Certified copy of the latest Implementation Report or Action Taken Status submitted to the Ministry of Parliamentary Affairs (OAMS) and the Committee on Government Assurances for the referenced parliamentary assurance.",
        "Certified copies of all extension requests sought from and granted by the Committee on Government Assurances, along with recorded reasons for pendency or dropping.",
        "Certified copies of supporting official correspondence, DPR/progress status sheets, or follow-up replies prepared pursuant to the original ministerial assurance.",
    ],
    "P7": [
        "Certified copies of any Proprietary Article Certificate (PAC), single-tender justification note, or competent authority approval recording rate reasonableness for the referenced single-bidder tender (GFR 2017 Rule 166 / CVC Single-Bid Guidelines or applicable State Procurement Rules).",
        "Certified copies of the Technical and Financial Bid Evaluation Committee minutes (including reasons recorded for disqualification of any non-responsive bids) and the comparative bid statement.",
        "Certified copy of the Award of Contract (AOC) / work order register for the referenced procuring authority and category covering repeat awards disclosed under Section 4(1)(b) of the RTI Act, 2005.",
    ],
}

_DEFAULT_RECORDS: list[str] = [
    "Certified copies of the administrative approval and financial sanction order for the referenced public expenditure record.",
    "Certified copies of the evaluation committee minutes, comparative statement, and utilization or completion certificate on file.",
    "Certified copies of proactive disclosure records published under Section 4(1)(b) of the RTI Act, 2005 for the referenced activity.",
]


_CTRL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]+")


def _clean_single_line(raw: Any, *, max_len: int = 160, default: str = "") -> str:
    """Scrub PII, strip control/newline characters, collapse whitespace, and cap length."""
    text = str(raw or "")
    text = _CTRL_CHARS_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return default
    scrubbed, _ = scrub_pii(text[: max_len * 2])
    cleaned = re.sub(r"\s+", " ", scrubbed).strip()[:max_len]
    return cleaned or default


def _format_inr(amount: float) -> str:
    if not math.isfinite(amount) or amount <= 0:
        return "recorded public outlay"
    if amount >= 10_000_000:
        return f"INR {amount / 10_000_000:.2f} crore"
    if amount >= 100_000:
        return f"INR {amount / 100_000:.2f} lakh"
    if amount > 0:
        return f"INR {amount:,.0f}"
    return "recorded public outlay"


def _extract_reference_ids(finding: dict[str, Any]) -> str:
    refs: list[str] = []
    metrics = finding.get("anomaly_metrics") if isinstance(finding.get("anomaly_metrics"), dict) else {}
    for mkey in ("assurance_ref", "para_number", "tender_id", "ocid"):
        val = metrics.get(mkey)
        cleaned = _clean_single_line(val, max_len=64) if val else ""
        if cleaned and cleaned not in refs:
            refs.append(cleaned)
    citations = finding.get("citations") or []
    for cit in citations[:4]:
        if not isinstance(cit, dict):
            continue
        for key in ("assurance_ref", "record_id", "row_id", "tender_id", "para_id", "work_id", "ocid"):
            val = cit.get(key)
            cleaned = _clean_single_line(val, max_len=64) if val else ""
            if cleaned and cleaned not in refs:
                refs.append(cleaned)
    if not refs:
        rule = finding.get("rule_triggered")
        if rule:
            cleaned_rule = _clean_single_line(rule, max_len=64)
            if cleaned_rule:
                refs.append(cleaned_rule)
    return ", ".join(refs[:4]) if refs else "cited public portal records"


def _match_state_key(text: str) -> str | None:
    lower = text.lower().strip()
    if not lower:
        return None
    if lower.startswith("state:"):
        candidate = lower.split(":", 1)[1].strip()
        if candidate in _STATE_RTI_REGISTRY:
            return candidate
    for cue, state_key in _STATE_ALIASES:
        if re.search(rf"(?<![a-z]){re.escape(cue)}(?![a-z])", lower):
            return state_key
    return None


def resolve_rti_jurisdiction(
    finding: dict[str, Any] | AuditLeadCandidate,
    public_authority: str | None = None,
) -> dict[str, Any]:
    """Resolve the record-holder jurisdiction (Central vs State), portal, channel, and fee rules."""
    data = finding.model_dump() if isinstance(finding, AuditLeadCandidate) else dict(finding)

    raw_jurisdiction = str(data.get("jurisdiction") or "").strip()
    authority_text = (public_authority or "").strip()
    suggested_text = str(data.get("suggested_authority") or "").strip()
    entity_text = str(data.get("entity_name") or "").strip()

    # 1. Explicit public_authority override takes highest priority when clearly central or state.
    state_key: str | None = None
    explicit_central = False
    if authority_text:
        auth_lower = authority_text.lower()
        if any(cue in auth_lower for cue in _CENTRAL_AUTHORITY_CUES):
            explicit_central = True
        else:
            state_key = _match_state_key(authority_text)

    # 2. Check finding jurisdiction if not resolved by explicit override.
    if not explicit_central and state_key is None:
        jur_lower = raw_jurisdiction.lower()
        # "Union of India (Delhi Police)" names a body run by the Union: a force such as Delhi Police
        # answers to the Home Ministry, so its records are asked for through the central route even
        # though the force is named after a State or Union Territory.
        if jur_lower in {"union", "union of india", "india", "central", "government of india"} or jur_lower.startswith(
            "union of india ("
        ):
            explicit_central = True
        elif jur_lower.startswith("state:") and "&" not in jur_lower and "," not in jur_lower:
            raw_state = raw_jurisdiction.split(":", 1)[1].strip()
            state_key = _match_state_key(raw_state) or raw_state.lower()
        else:
            # Multi-state / All-India / unprefixed jurisdiction: inspect authority & entity first.
            state_key = (
                _match_state_key(suggested_text)
                or _match_state_key(entity_text)
                or _match_state_key(raw_jurisdiction)
            )
            if state_key is None:
                combined_auth = f"{suggested_text} {entity_text}".lower()
                if any(cue in combined_auth for cue in _CENTRAL_AUTHORITY_CUES) or jur_lower in {"", "all-india"}:
                    explicit_central = True

    if explicit_central or state_key is None:
        return {
            "jurisdiction": "Union of India",
            "jurisdiction_level": "central",
            "state_name": None,
            "pio_title": "Central Public Information Officer (CPIO)",
            "rti_portal_url": CENTRAL_RTI_PORTAL_URL,
            "filing_channel": f"Central RTI Online Portal ({CENTRAL_RTI_PORTAL_URL}) or speed/registered post to the concerned CPIO",
            "fee_rules": (
                "Rs. 10 under Rule 3 of the Right to Information Rules, 2012 (payable online via "
                f"{CENTRAL_RTI_PORTAL_URL} or via Indian Postal Order / DD / Bankers Cheque; "
                "BPL applicants are exempt under Section 7(5) of the RTI Act, 2005)"
            ),
            "central_portal_warning": None,
        }

    entry = _STATE_RTI_REGISTRY.get(state_key)
    if entry is not None:
        state_name = entry["state_name"]
        return {
            "jurisdiction": f"State:{state_name}",
            "jurisdiction_level": "state",
            "state_name": state_name,
            "pio_title": "State Public Information Officer (SPIO)",
            "rti_portal_url": entry["portal_url"],
            "filing_channel": entry["filing_channel"],
            "fee_rules": entry["fee_rules"],
            "central_portal_warning": _CENTRAL_PORTAL_WARNING,
        }

    # Unmapped state/UT: never route to rtionline.gov.in!
    pretty_state = _clean_single_line(state_key.title(), max_len=64, default="Concerned State")
    return {
        "jurisdiction": f"State:{pretty_state}",
        "jurisdiction_level": "state",
        "state_name": pretty_state,
        "pio_title": "State Public Information Officer (SPIO)",
        "rti_portal_url": "",
        "filing_channel": (
            f"Official {pretty_state} State RTI portal (if available) or speed/registered post "
            f"to the concerned SPIO, Government of {pretty_state}"
        ),
        "fee_rules": (
            f"Prescribed State RTI application fee (typically Rs. 10 under {pretty_state} State RTI Rules, "
            "payable via Treasury Challan / Court Fee Stamp / IPO / DD; BPL applicants are exempt under Section 7(5))"
        ),
        "central_portal_warning": _CENTRAL_PORTAL_WARNING,
    }


def _trim_to_word_limit(text: str, max_words: int = MAX_RTI_WORDS) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
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


def _compose_bounded_rti_body(
    header_and_middle: list[str],
    mandatory_footer: list[str],
    max_words: int = MAX_RTI_WORDS,
) -> str:
    """Trim only the dynamic middle body if needed so mandatory statutory notices are never dropped."""
    footer_text = "\n".join(mandatory_footer).strip()
    footer_words = len(footer_text.split())
    middle_budget = max(40, max_words - footer_words)
    middle_text = _trim_to_word_limit("\n".join(header_and_middle).strip(), middle_budget)
    combined = f"{middle_text}\n\n{footer_text}".strip()
    if len(combined.split()) > max_words:
        combined = _trim_to_word_limit(combined, max_words)
    return combined


def generate_rti_draft(
    finding: dict[str, Any] | AuditLeadCandidate,
    public_authority: str | None = None,
) -> dict[str, Any]:
    """Generate a lawful RTI Act, 2005 Sec 6(1) & Sec 2(f) application draft (<= 500 words).

    Resolves Central vs State record-holder jurisdiction so state-authority drafts use the
    appropriate State RTI portal/channel and fee rules — never routing state applicants to
    https://rtionline.gov.in. Preserves strict human-only review and filing.
    """
    data = finding.model_dump() if isinstance(finding, AuditLeadCandidate) else dict(finding)

    cleaned_override = _clean_single_line(public_authority, max_len=160) if public_authority else ""
    if cleaned_override:
        forbidden = scan_forbidden_terms(cleaned_override)
        if forbidden:
            raise ValueError(
                f"public_authority contains disallowed accusatory term(s): {', '.join(forbidden)}"
            )

    authority = (
        cleaned_override
        or _clean_single_line(data.get("suggested_authority"), max_len=160)
        or _clean_single_line(data.get("entity_name"), max_len=160)
        or "Concerned Public Authority"
    )
    resolved = resolve_rti_jurisdiction(data, public_authority=cleaned_override or None)
    jurisdiction = _clean_single_line(resolved["jurisdiction"], max_len=100, default="Union of India")
    jurisdiction_level = resolved["jurisdiction_level"]
    pio_title = resolved["pio_title"]
    portal_url = resolved["rti_portal_url"]
    filing_channel = _clean_single_line(resolved["filing_channel"], max_len=240)
    fee_rules = _clean_single_line(resolved["fee_rules"], max_len=300)
    central_warning = resolved["central_portal_warning"]

    fiscal_year = _clean_single_line(data.get("fiscal_year"), max_len=48, default="Relevant Fiscal Year")
    entity_name = _clean_single_line(data.get("entity_name"), max_len=160, default=authority)
    method_code = _clean_single_line(data.get("methodology_code"), max_len=16, default="M1").upper()
    raw_exposure = data.get("monetary_exposure_inr")
    try:
        exposure_inr = float(raw_exposure or 0.0) if not isinstance(raw_exposure, bool) else 0.0
    except (TypeError, ValueError):
        exposure_inr = 0.0
    if not math.isfinite(exposure_inr) or exposure_inr < 0:
        exposure_inr = 0.0
    cpio_designation = _clean_single_line(f"{pio_title}, {authority}", max_len=220)

    requested_records = list(_METHODOLOGY_RECORDS.get(method_code, _DEFAULT_RECORDS))
    ref_ids = _extract_reference_ids(data)
    exposure_str = _format_inr(exposure_inr)

    raw_subject = (
        f"RTI Application u/s 6(1) for certified public records regarding {entity_name} "
        f"({fiscal_year}, Ref: {ref_ids})"
    )
    subject, _ = scrub_pii(raw_subject)
    if len(subject.split()) > 35:
        subject = " ".join(subject.split()[:35])

    numbered_records = "\n".join(
        f"{idx}. {rec}" for idx, rec in enumerate(requested_records, start=1)
    )

    portal_line = (
        f"Filing Channel: {filing_channel}"
        if jurisdiction_level == "state"
        else f"Online Filing Portal: {portal_url}"
    )

    body_middle = [
        "To,",
        f"The {cpio_designation}",
        f"Jurisdiction: {jurisdiction} ({jurisdiction_level.title()} Public Authority)",
        portal_line,
        "",
        f"Subject: {subject}",
        "",
        "Sir/Madam,",
        "",
        (
            f"Under Section 6(1) read with Section 2(f) and Section 4(1)(b) of the Right to "
            f"Information Act, 2005, I respectfully request certified copies (electronic PDF or "
            f"photocopy) of the following existing public records held by {authority} pertaining "
            f"to {entity_name} for {fiscal_year} (Reference IDs: {ref_ids}; noted record figure "
            f"approx. {exposure_str}):"
        ),
        "",
        numbered_records,
        "",
        (
            "Where any sought record is already proactively published under Section 4(1)(b), "
            "kindly provide the exact official URL and document reference."
        ),
    ]

    mandatory_footer = [
        (
            f"Fee & Timeline Note: Prescribed fee: {fee_rules}. "
            "Kindly furnish the requested information within the statutory period of 30 days under "
            "Section 7(1), or transfer under Section 6(3) within 5 days if held by another public "
            "authority. Details of the First Appellate Authority under Section 19(1) may also be "
            "indicated in the reply."
        ),
    ]
    if central_warning:
        mandatory_footer.extend(["", f"Jurisdiction Notice: {central_warning}"])

    mandatory_footer.extend(
        [
            "",
            "Human Filing Note: Draft for citizen review and manual submission only (never auto-filed).",
            "Applicant Name: [Citizen Name]",
            "Address / Email: [Citizen Address / Email]",
            "Date: [DD/MM/YYYY]",
        ]
    )

    scrubbed_middle = [scrub_pii(line)[0] for line in body_middle]
    scrubbed_footer = [scrub_pii(line)[0] for line in mandatory_footer]
    final_body = _compose_bounded_rti_body(scrubbed_middle, scrubbed_footer, MAX_RTI_WORDS)
    word_count = len(final_body.split())

    return {
        "public_authority": authority,
        "cpio_designation": cpio_designation,
        "jurisdiction": jurisdiction,
        "jurisdiction_level": jurisdiction_level,
        "state_name": resolved["state_name"],
        "rti_portal_url": portal_url,
        "filing_channel": filing_channel,
        "fee_rules": fee_rules,
        "central_portal_warning": central_warning,
        "human_filing_required": True,
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
