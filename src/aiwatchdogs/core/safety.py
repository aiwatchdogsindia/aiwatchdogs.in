"""Indian legal-safety guardrails: DPDP Act 2023 PII scrubbing, BNS Sec 356 neutrality linting, SHA-256."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from aiwatchdogs.core.models import LegalLintResult

# Verhoeff dihedral group D5 multiplication table, permutation table, and inverses
# Used by UIDAI to validate 12-digit Indian Aadhaar numbers.
_VERHOEFF_D: tuple[tuple[int, ...], ...] = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)

_VERHOEFF_P: tuple[tuple[int, ...], ...] = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)

_VERHOEFF_INV: tuple[int, ...] = (0, 4, 3, 2, 1, 5, 6, 7, 8, 9)

# Candidate 12-digit Aadhaar (UIDAI numbers always begin with digits 2-9).
_AADHAAR_CANDIDATE_RE = re.compile(r"(?<!\d)([2-9]\d{3}[\s-]?\d{4}[\s-]?\d{4})(?!\d)")

# Standalone 10-character Indian PAN (excludes embedded 15-char GSTIN / 21-char CIN).
_PAN_RE = re.compile(r"(?<![A-Z0-9])[A-Z]{5}[0-9]{4}[A-Z](?![A-Z0-9])")

# Indian Financial System Code (IFSC): 4 letters + '0' + 6 alphanumerics.
_IFSC_RE = re.compile(r"(?<![A-Z0-9])[A-Z]{4}0[A-Z0-9]{6}(?![A-Z0-9])")

# Bank account numbers (9-18 digits) in explicit banking context.
_BANK_ACCT_CONTEXT_RE = re.compile(
    r"(?i)(\b(?:bank\s*(?:account|acct|a/c)|account\s*(?:no\.?|number|#)?|a/c\s*(?:no\.?)?|acct\s*(?:no\.?)?|sb\s*a/c|ca\s*no\.?|beneficiary\s*(?:account|a/c)|ifsc\s*[:\-]?\s*\[?REDACTED_IFSC\]?)\s*[:#\-]?\s*)(\d{9,18})(?!\d)"
)

# Digits immediately following an IFSC token within 60 chars.
_POST_IFSC_ACCT_RE = re.compile(r"(\[REDACTED_IFSC\][^\n]{0,60}?)(\b\d{9,18}\b)")

# Indian mobile numbers:
# 1) Explicit +91 prefix or leading 0 with optional spacing/hyphen.
_PHONE_PREFIXED_RE = re.compile(
    r"(?<!\d)(?:\+91[\s-]?|0)([6-9]\d{4}[\s-]?\d{5})(?!\d)"
)
# 2) Bare 10-digit mobile numbers starting with 6-9 (filtered to avoid round fiscal rupee amounts).
_PHONE_BARE_RE = re.compile(r"(?<![\d₹.])([6-9]\d{9})(?!\d)")
_PHONE_BARE_SPLIT_RE = re.compile(r"(?<!\d)([6-9]\d{4}[\s-]\d{5})(?!\d)")

# Email addresses.
_EMAIL_RE = re.compile(r"\b([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")

# Official generic public-office mailboxes on .gov.in / .nic.in that are public statutory contacts.
_OFFICIAL_ROLE_LOCAL_PARTS = frozenset(
    {
        "cpio",
        "pio",
        "rti",
        "rticell",
        "rti-cell",
        "vigilance",
        "nodal",
        "info",
        "helpdesk",
        "support",
        "office",
    }
)

# Defamatory / accusatory patterns prohibited under BNS Sec 356 journalistic neutrality rules.
_FORBIDDEN_TERM_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("scam", re.compile(r"\bscam(?:s|med|mer|mers)?\b", re.IGNORECASE)),
    ("corrupt", re.compile(r"\bcorrupt(?:ed|ing|ly)?\b", re.IGNORECASE)),
    ("corruption", re.compile(r"\bcorruption\b", re.IGNORECASE)),
    ("bribe", re.compile(r"\bbrib(?:e|es|ed|ing|ery)\b", re.IGNORECASE)),
    ("fraud", re.compile(r"\bfraud(?:s|ulent|ulently|ster|sters)?\b", re.IGNORECASE)),
    ("kickback", re.compile(r"\bkickback(?:s)?\b", re.IGNORECASE)),
    ("guilty", re.compile(r"\bguilty\b", re.IGNORECASE)),
    ("stolen", re.compile(r"\bstolen\b", re.IGNORECASE)),
    ("rigged", re.compile(r"\brigged\b", re.IGNORECASE)),
    ("crook", re.compile(r"\bcrook(?:s|ed)?\b", re.IGNORECASE)),
    ("loot", re.compile(r"\bloot(?:s|ed|ing|er|ers)?\b", re.IGNORECASE)),
    ("embezzl", re.compile(r"\bembezzl(?:e|es|ed|ing|ement)\b", re.IGNORECASE)),
)


def verhoeff_validate(number: str) -> bool:
    """Return True if a digit string passes the Verhoeff checksum algorithm."""
    digits = re.sub(r"[\s-]", "", number)
    if not digits.isdigit() or not digits:
        return False
    checksum = 0
    for idx, ch in enumerate(reversed(digits)):
        checksum = _VERHOEFF_D[checksum][_VERHOEFF_P[idx % 8][int(ch)]]
    return checksum == 0


def verhoeff_generate_check_digit(partial_digits: str) -> str:
    """Compute the Verhoeff check digit for a partial numeric string."""
    digits = re.sub(r"[\s-]", "", partial_digits)
    if not digits.isdigit() or not digits:
        raise ValueError("partial_digits must contain only numeric digits")
    checksum = 0
    for idx, ch in enumerate(reversed(digits)):
        checksum = _VERHOEFF_D[checksum][_VERHOEFF_P[(idx + 1) % 8][int(ch)]]
    return str(_VERHOEFF_INV[checksum])


def _is_valid_aadhaar(candidate: str) -> bool:
    digits = re.sub(r"[\s-]", "", candidate)
    if len(digits) != 12 or not digits.isdigit():
        return False
    if digits[0] in ("0", "1"):
        return False
    # Reject trivial repeated digits like 222222222222 (cannot pass Verhoeff anyway, belt & suspenders).
    if len(set(digits)) == 1:
        return False
    return verhoeff_validate(digits)


def _record_redaction(redactions: list[str], tag: str) -> None:
    if tag not in redactions:
        redactions.append(tag)


def scrub_pii(text: str) -> tuple[str, list[str]]:
    """Scrub personal Indian PII from text per DPDP Act 2023 & Aadhaar Act 2016.

    Redacts:
    - 12-digit Aadhaar numbers validated by the Verhoeff checksum
    - Standalone 10-character PAN numbers (preserving 15-char GSTINs and 21-char CINs)
    - Indian bank IFSC codes and associated bank account numbers
    - Indian mobile phone numbers
    - Personal email addresses (preserving generic .gov.in/.nic.in CPIO/RTI role mailboxes)

    Returns ``(redacted_text, redaction_categories)``.
    """
    if not text:
        return text, []

    redactions: list[str] = []
    cleaned = text

    # 1. Aadhaar (12-digit Verhoeff-validated)
    def _replace_aadhaar(match: re.Match[str]) -> str:
        raw = match.group(1)
        if _is_valid_aadhaar(raw):
            _record_redaction(redactions, "aadhaar")
            return "[REDACTED_AADHAAR]"
        return match.group(0)

    cleaned = _AADHAAR_CANDIDATE_RE.sub(_replace_aadhaar, cleaned)

    # 2. Standalone PAN
    def _replace_pan(match: re.Match[str]) -> str:
        _record_redaction(redactions, "pan")
        return "[REDACTED_PAN]"

    cleaned = _PAN_RE.sub(_replace_pan, cleaned)

    # 3. Bank IFSC codes
    def _replace_ifsc(match: re.Match[str]) -> str:
        _record_redaction(redactions, "ifsc")
        return "[REDACTED_IFSC]"

    cleaned = _IFSC_RE.sub(_replace_ifsc, cleaned)

    # 4. Bank account numbers (in explicit banking context or adjacent to IFSC)
    def _replace_bank_acct(match: re.Match[str]) -> str:
        _record_redaction(redactions, "bank_account")
        return f"{match.group(1)}[REDACTED_BANK_ACCOUNT]"

    cleaned = _BANK_ACCT_CONTEXT_RE.sub(_replace_bank_acct, cleaned)
    cleaned = _POST_IFSC_ACCT_RE.sub(_replace_bank_acct, cleaned)

    # 5. Personal email addresses
    def _replace_email(match: re.Match[str]) -> str:
        local = match.group(1).lower()
        domain = match.group(2).lower()
        if (domain.endswith(".gov.in") or domain.endswith(".nic.in")) and (
            local in _OFFICIAL_ROLE_LOCAL_PARTS
        ):
            return match.group(0)
        _record_redaction(redactions, "email")
        return "[REDACTED_EMAIL]"

    cleaned = _EMAIL_RE.sub(_replace_email, cleaned)

    # 6. Indian mobile numbers
    def _replace_prefixed_phone(match: re.Match[str]) -> str:
        _record_redaction(redactions, "phone")
        return "[REDACTED_PHONE]"

    cleaned = _PHONE_PREFIXED_RE.sub(_replace_prefixed_phone, cleaned)
    cleaned = _PHONE_BARE_SPLIT_RE.sub(_replace_prefixed_phone, cleaned)

    def _replace_bare_phone(match: re.Match[str]) -> str:
        digits = match.group(1)
        start = match.start()
        prefix_window = cleaned[max(0, start - 24) : start].lower()
        # Avoid false-positive redaction of large round fiscal amounts (e.g. ₹750,00,00,000 -> 7500000000).
        if digits.endswith("00000") or re.search(
            r"(?:inr|rs\.?|rupees|amount|value|budget|spend|crore|lakh|cost)\s*[:=\-]?\s*$",
            prefix_window,
        ):
            return match.group(0)
        _record_redaction(redactions, "phone")
        return "[REDACTED_PHONE]"

    cleaned = _PHONE_BARE_RE.sub(_replace_bare_phone, cleaned)

    return cleaned, redactions


def scrub_pii_in_obj(value: Any) -> tuple[Any, list[str]]:
    """Recursively scrub PII from strings inside nested dicts/lists."""
    redactions: list[str] = []

    def _walk(item: Any) -> Any:
        if isinstance(item, str):
            cleaned, tags = scrub_pii(item)
            for tag in tags:
                _record_redaction(redactions, tag)
            return cleaned
        if isinstance(item, list):
            return [_walk(v) for v in item]
        if isinstance(item, dict):
            return {k: _walk(v) for k, v in item.items()}
        return item

    return _walk(value), redactions


def lint_neutrality(
    title: str,
    summary: str,
    benign_explanations: list[str],
) -> LegalLintResult:
    """Validate that a lead uses neutral, non-defamatory civic-audit framing.

    Enforces BNS Sec 356 public-interest fair reporting standards:
    - Non-empty title and summary
    - Zero accusatory or defamatory terms
    - At least one non-empty plausible lawful / benign explanation
    """
    violations: list[str] = []

    if not isinstance(title, str) or not title.strip():
        violations.append("Title must be a non-empty string.")
    if not isinstance(summary, str) or not summary.strip():
        violations.append("Summary must be a non-empty string.")

    valid_benign = [
        item.strip()
        for item in (benign_explanations or [])
        if isinstance(item, str) and item.strip()
    ]
    if not valid_benign:
        violations.append(
            "At least one non-empty benign_explanations entry is required for fair public-interest framing."
        )

    fields_to_scan = [
        ("title", title or ""),
        ("summary", summary or ""),
        ("benign_explanations", " ".join(valid_benign)),
    ]
    for field_name, content in fields_to_scan:
        for label, pattern in _FORBIDDEN_TERM_PATTERNS:
            if pattern.search(content):
                violations.append(
                    f"Accusatory or defamatory term '{label}' is disallowed in {field_name}; "
                    "use neutral factual framing such as 'public record anomaly' or 'statistical divergence'."
                )

    return LegalLintResult(passed=len(violations) == 0, violations=violations)


def compute_sha256(content: bytes | str) -> str:
    """Compute a lowercase hexadecimal SHA-256 digest for bytes or UTF-8 text."""
    data = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(data).hexdigest()
