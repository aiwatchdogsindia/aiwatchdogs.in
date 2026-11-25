"""Indian legal-safety guardrails: DPDP Act 2023 PII scrubbing, BNS Sec 356 neutrality linting, URL & CSV safety, SHA-256/HMAC."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import math
import os
import re
import unicodedata
from typing import Any
from urllib.parse import urlsplit

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

# Separators commonly found in Indian PDF/CSV exports (ASCII space, hyphen, Unicode dashes).
_DASH_SEP_RE = re.compile(r"[\s\-\u2010-\u2015]")
_SEP = r"[\s\-\u2010-\u2015]"

# Candidate 12-digit Aadhaar (UIDAI numbers always begin with digits 2-9).
_AADHAAR_CANDIDATE_RE = re.compile(rf"(?<!\d)([2-9]\d{{3}}{_SEP}?\d{{4}}{_SEP}?\d{{4}})(?!\d)")

# Standalone 10-character Indian PAN (case-insensitive; excludes embedded 15-char GSTIN / 21-char CIN).
_PAN_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{5}[0-9]{4}[A-Za-z](?![A-Za-z0-9])")

# Indian Financial System Code (IFSC): 4 letters + '0' + 6 alphanumerics (case-insensitive).
_IFSC_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{4}0[A-Za-z0-9]{6}(?![A-Za-z0-9])")

# Bank account numbers (9-18 digits, optionally dash/space grouped) in explicit banking context.
_BANK_ACCT_CONTEXT_RE = re.compile(
    rf"(?i)(\b(?:bank\s*(?:account|acct|a/c)(?:\s*(?:no\.?|number|#))?|account\s*(?:no\.?|number|#)?|a/c\s*(?:no\.?|number|#)?|acct\s*(?:no\.?|number|#)?|sb\s*a/c|ca\s*no\.?|beneficiary\s*(?:account|a/c)|ifsc\s*[:\-\u2010-\u2015]?\s*\[?REDACTED_IFSC\]?)\s*[:#\-\u2010-\u2015]?\s*)(\d(?:{_SEP}?\d){{8,17}})(?!\d)"
)

# Digits immediately following an IFSC token within 60 chars (including across newlines).
_POST_IFSC_ACCT_RE = re.compile(
    rf"(\[REDACTED_IFSC\][\s\S]{{0,60}}?)(\b\d(?:{_SEP}?\d){{8,17}}\b)"
)

# Indian mobile numbers:
# 1) Explicit +91 / 91 prefix or leading 0 with optional spacing/hyphen/Unicode dash.
# Numbers glued to letters are not phone numbers: keeps digit runs inside hex digests intact.
_PHONE_PREFIXED_RE = re.compile(
    rf"(?<![\w])(?:\+?91{_SEP}?|0)([6-9]\d{{4}}{_SEP}?\d{{5}}|[6-9]\d{{2}}{_SEP}\d{{3}}{_SEP}\d{{4}})(?![\w])"
)
# 2) Bare 10-digit mobile numbers starting with 6-9 (filtered to avoid round fiscal rupee amounts).
_PHONE_BARE_RE = re.compile(r"(?<![\w₹.])([6-9]\d{9})(?![\w])")
_PHONE_BARE_SPLIT_RE = re.compile(
    rf"(?<![\w])([6-9]\d{{4}}{_SEP}\d{{5}}|[6-9]\d{{2}}{_SEP}\d{{3}}{_SEP}\d{{4}})(?![\w])"
)

# Email addresses — linear-time anchored pattern (prevents O(N^2) backtracking ReDoS).
_EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+-])([A-Za-z0-9._%+-]{1,64})@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)(?![A-Za-z0-9.-])"
)

# Sensitive structured-record keys whose bare digit/string values are scrubbed even without inline labels.
_SENSITIVE_BANK_ACCT_KEYS = frozenset(
    {
        "bank_account",
        "bank_account_no",
        "bank_account_number",
        "account_no",
        "account_number",
        "acct_no",
        "acct_number",
        "beneficiary_account",
        "beneficiary_account_no",
        "beneficiary_account_number",
    }
)
_SENSITIVE_PHONE_KEYS = frozenset(
    {
        "phone",
        "phone_no",
        "phone_number",
        "mobile",
        "mobile_no",
        "mobile_number",
        "contact_phone",
        "personal_phone",
    }
)
_SENSITIVE_AADHAAR_KEYS = frozenset({"aadhaar", "aadhar", "aadhaar_no", "aadhaar_number", "uidai"})
_SENSITIVE_PAN_KEYS = frozenset({"pan", "pan_no", "pan_number", "personal_pan"})

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
# Uses letter-only lookarounds (?<![a-z])/(?![a-z]) so digit/underscore compounds (e.g. fraud2025, mega_scam)
# cannot evade matching after NFKC normalization and invisible-character stripping.
_FORBIDDEN_TERM_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("scam", re.compile(r"(?<![a-z])scam(?:s|med|mer|mers)?(?![a-z])", re.IGNORECASE)),
    ("corrupt", re.compile(r"(?<![a-z])corrupt(?:ed|ing|ly)?(?![a-z])", re.IGNORECASE)),
    ("corruption", re.compile(r"(?<![a-z])corruption(?![a-z])", re.IGNORECASE)),
    ("bribe", re.compile(r"(?<![a-z])brib(?:e|es|ed|ing|ery)(?![a-z])", re.IGNORECASE)),
    ("fraud", re.compile(r"(?<![a-z])fraud(?:s|ulent|ulently|ster|sters)?(?![a-z])", re.IGNORECASE)),
    ("kickback", re.compile(r"(?<![a-z])kickback(?:s)?(?![a-z])", re.IGNORECASE)),
    ("guilty", re.compile(r"(?<![a-z])guilty(?![a-z])", re.IGNORECASE)),
    ("stolen", re.compile(r"(?<![a-z])stolen(?![a-z])", re.IGNORECASE)),
    ("rigged", re.compile(r"(?<![a-z])rigged(?![a-z])", re.IGNORECASE)),
    ("crook", re.compile(r"(?<![a-z])crook(?:s|ed)?(?![a-z])", re.IGNORECASE)),
    ("loot", re.compile(r"(?<![a-z])loot(?:s|ed|ing|er|ers)?(?![a-z])", re.IGNORECASE)),
    ("embezzl", re.compile(r"(?<![a-z])embezzl(?:e|es|ed|ing|ement)(?![a-z])", re.IGNORECASE)),
)

# Blocked hostnames & suffixes for SSRF / safe public URL validation.
_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata.google.internal",
        "metadata",
        "169.254.169.254",
        "100.100.100.200",
        "0.0.0.0",
    }
)
_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")
_DNS_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_NON_CANONICAL_IPV4_RE = re.compile(r"^(?:0x[0-9a-f]+|0[0-7]+|\d+)(?:\.(?:0x[0-9a-f]+|0[0-7]+|\d+)){0,3}$")
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")


def is_public_ip_address(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True only when an IP address is strictly globally routable."""
    if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped is not None:
        ip_obj = ip_obj.ipv4_mapped
    if isinstance(ip_obj, ipaddress.IPv4Address) and ip_obj in _CGNAT_NET:
        return False
    return bool(
        ip_obj.is_global
        and not ip_obj.is_private
        and not ip_obj.is_loopback
        and not ip_obj.is_link_local
        and not ip_obj.is_multicast
        and not ip_obj.is_reserved
        and not ip_obj.is_unspecified
    )


def validate_safe_http_url(
    url: Any,
    *,
    require_https: bool = True,
    allow_private: bool = False,
    max_length: int = 2048,
) -> str:
    """Validate and return a clean HTTP(S) URL rejecting dangerous schemes, credentials, and private hosts."""
    if not isinstance(url, str):
        raise ValueError("URL must be a string.")
    cleaned = url.strip()
    if not cleaned:
        raise ValueError("URL must not be empty.")
    if len(cleaned) > max_length:
        raise ValueError(f"URL exceeds maximum length of {max_length} characters.")
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in cleaned):
        raise ValueError("URL must not contain whitespace or control characters.")

    parsed = urlsplit(cleaned)
    scheme = (parsed.scheme or "").lower()
    allowed_schemes = {"https"} if require_https else {"https", "http"}
    if scheme not in allowed_schemes:
        expected = "https://" if require_https else "http:// or https://"
        raise ValueError(f"URL must use {expected} (got {scheme or 'missing scheme'}).")
    if parsed.username or parsed.password:
        raise ValueError("URL must not embed username/password credentials.")

    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise ValueError("URL must include a valid hostname.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"URL has an invalid port: {exc}") from exc
    if port is not None and not (1 <= port <= 65535):
        raise ValueError("URL port is out of range.")

    if not allow_private:
        if host in _BLOCKED_HOSTNAMES or host.endswith((".localhost", ".local", ".internal", ".arpa")):
            raise ValueError(f"Blocked internal/private hostname: {host!r}")
        try:
            ip_obj = ipaddress.ip_address(host)
        except ValueError:
            # Reject non-canonical IPv4 literals (decimal 2130706433, hex 0x7f000001, shorthand 127.1, octal).
            if _NON_CANONICAL_IPV4_RE.match(host):
                raise ValueError(f"Non-canonical numeric host rejected: {host!r}")
            labels = host.split(".")
            if len(labels) < 2 or not all(_DNS_LABEL_RE.match(lbl) for lbl in labels):
                raise ValueError(f"Invalid public hostname: {host!r}")
            if labels[-1].isdigit():
                raise ValueError(f"Numeric TLD hostname rejected: {host!r}")
        else:
            if not is_public_ip_address(ip_obj):
                raise ValueError(f"Blocked non-public IP address: {host!r}")

    return cleaned


def sanitize_csv_cell(value: Any) -> str:
    """Neutralize spreadsheet formula triggers (=, +, -, @, tab, CR/LF) in exported CSV cells."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return "0"
        return str(value)
    text = str(value).replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    stripped = text.lstrip(" \t\r\n\x00\x0b\x0c")
    if stripped and stripped[0] in _CSV_FORMULA_PREFIXES:
        return f"'{text}"
    if text and text[0] in ("\t", "\r", "\n"):
        return f"'{text}"
    return text


def escape_like(value: str) -> str:
    """Escape SQL LIKE wildcard characters (\\, %, _) for use with ESCAPE '\\\\'."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def parse_finite_float(
    value: Any,
    *,
    field: str = "value",
    min_value: float | None = 0.0,
    max_value: float | None = 1e14,
    default: float | None = None,
) -> float:
    """Strictly parse a finite bounded float, rejecting bool, NaN, Infinity, and out-of-range values."""
    if value is None or value == "":
        if default is not None:
            return default
        raise ValueError(f"{field} is required.")
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite number, not a boolean.")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a valid finite number.") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{field} must be finite (NaN/Infinity not allowed).")
    if min_value is not None and parsed < min_value:
        raise ValueError(f"{field} must be >= {min_value}.")
    if max_value is not None and parsed > max_value:
        raise ValueError(f"{field} must be <= {max_value}.")
    return parsed


def parse_bounded_int(
    value: Any,
    *,
    field: str = "value",
    min_value: int = 0,
    max_value: int = 10_000_000,
    default: int | None = None,
) -> int:
    """Strictly parse a bounded integer, rejecting bool, NaN/Infinity, and overflow values."""
    if value is None or value == "":
        if default is not None:
            return default
        raise ValueError(f"{field} is required.")
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer, not a boolean.")
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError(f"{field} must be a finite integer.")
        parsed = int(value)
    elif isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            if default is not None:
                return default
            raise ValueError(f"{field} is required.")
        try:
            parsed = int(cleaned)
        except ValueError:
            try:
                flt = float(cleaned)
            except ValueError as exc:
                raise ValueError(f"{field} must be an integer.") from exc
            if not math.isfinite(flt) or not flt.is_integer():
                raise ValueError(f"{field} must be a finite integer.")
            parsed = int(flt)
    else:
        raise ValueError(f"{field} must be an integer.")
    if parsed < min_value or parsed > max_value:
        raise ValueError(f"{field} must be between {min_value} and {max_value}.")
    return parsed


def _normalize_for_lint(text: str) -> str:
    """Apply NFKC normalization, strip invisible Cf/Cc characters, and normalize underscores."""
    normalized = unicodedata.normalize("NFKC", text or "")
    stripped = "".join(
        ch for ch in normalized if unicodedata.category(ch) not in {"Cf", "Cc"} or ch in ("\n", "\t", " ")
    )
    return stripped.replace("_", " ")


def verhoeff_validate(number: str) -> bool:
    """Return True if a digit string passes the Verhoeff checksum algorithm."""
    digits = _DASH_SEP_RE.sub("", number)
    if not digits.isdigit() or not digits:
        return False
    checksum = 0
    for idx, ch in enumerate(reversed(digits)):
        checksum = _VERHOEFF_D[checksum][_VERHOEFF_P[idx % 8][int(ch)]]
    return checksum == 0


def verhoeff_generate_check_digit(partial_digits: str) -> str:
    """Compute the Verhoeff check digit for a partial numeric string."""
    digits = _DASH_SEP_RE.sub("", partial_digits)
    if not digits.isdigit() or not digits:
        raise ValueError("partial_digits must contain only numeric digits")
    checksum = 0
    for idx, ch in enumerate(reversed(digits)):
        checksum = _VERHOEFF_D[checksum][_VERHOEFF_P[(idx + 1) % 8][int(ch)]]
    return str(_VERHOEFF_INV[checksum])


def _is_valid_aadhaar(candidate: str) -> bool:
    digits = _DASH_SEP_RE.sub("", candidate)
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

    # 2. Standalone PAN (case-insensitive)
    def _replace_pan(match: re.Match[str]) -> str:
        _record_redaction(redactions, "pan")
        return "[REDACTED_PAN]"

    cleaned = _PAN_RE.sub(_replace_pan, cleaned)

    # 3. Bank IFSC codes (case-insensitive)
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

    # 5. Personal email addresses (short-circuit if '@' absent)
    if "@" in cleaned:
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


def scrub_sensitive_field_value(key: str, val: Any, redactions: list[str]) -> tuple[bool, Any]:
    """Key-aware redaction for structured CSV/JSON/XLSX records whose labels are dictionary keys."""
    if not isinstance(key, str) or val is None or isinstance(val, bool):
        return False, val
    norm_key = re.sub(r"[^a-z0-9]+", "_", key.strip().lower()).strip("_")
    if norm_key in _SENSITIVE_BANK_ACCT_KEYS:
        raw = str(val).strip()
        digits = _DASH_SEP_RE.sub("", raw)
        if digits.isdigit() and 9 <= len(digits) <= 18:
            _record_redaction(redactions, "bank_account")
            return True, "[REDACTED_BANK_ACCOUNT]"
    if norm_key in _SENSITIVE_PHONE_KEYS:
        raw = str(val).strip()
        digits = _DASH_SEP_RE.sub("", raw.lstrip("+"))
        if digits.isdigit() and (len(digits) == 10 or (len(digits) == 12 and digits.startswith("91"))):
            _record_redaction(redactions, "phone")
            return True, "[REDACTED_PHONE]"
    if norm_key in _SENSITIVE_AADHAAR_KEYS:
        raw = str(val).strip()
        if _is_valid_aadhaar(raw):
            _record_redaction(redactions, "aadhaar")
            return True, "[REDACTED_AADHAAR]"
    if norm_key in _SENSITIVE_PAN_KEYS:
        raw = str(val).strip()
        if _PAN_RE.fullmatch(raw):
            _record_redaction(redactions, "pan")
            return True, "[REDACTED_PAN]"
    return False, val


def scrub_pii_in_obj(value: Any, *, max_depth: int = 32) -> tuple[Any, list[str]]:
    """Recursively scrub PII from strings inside nested dicts, lists, tuples, and sets.

    Both string values and string dictionary keys containing personal PII are redacted,
    and key-aware banking/identity fields are scrubbed even without inline text prefixes.
    Bounded by ``max_depth`` to prevent runaway recursion on untrusted payloads.
    """
    redactions: list[str] = []

    def _walk(item: Any, depth: int) -> Any:
        if depth > max_depth:
            return "[TRUNCATED_DEPTH]"
        if isinstance(item, str):
            cleaned, tags = scrub_pii(item)
            for tag in tags:
                _record_redaction(redactions, tag)
            return cleaned
        if isinstance(item, list):
            return [_walk(v, depth + 1) for v in item]
        if isinstance(item, tuple):
            return tuple(_walk(v, depth + 1) for v in item)
        if isinstance(item, set):
            return {_walk(v, depth + 1) for v in item}
        if isinstance(item, dict):
            out: dict[Any, Any] = {}
            for k, v in item.items():
                clean_k = k
                if isinstance(k, str):
                    candidate_k, k_tags = scrub_pii(k)
                    if k_tags:
                        for tag in k_tags:
                            _record_redaction(redactions, tag)
                        clean_k = candidate_k
                    matched, redacted_v = scrub_sensitive_field_value(k, v, redactions)
                    if matched:
                        out[clean_k] = redacted_v
                        continue
                out[clean_k] = _walk(v, depth + 1)
            return out
        return item

    return _walk(value, 0), redactions


redact_pii_recursive = scrub_pii_in_obj


def contains_pii(value: Any, *, max_depth: int = 32) -> tuple[bool, list[str]]:
    """Return ``(has_pii, redaction_categories)`` after recursively inspecting ``value``."""
    _, tags = scrub_pii_in_obj(value, max_depth=max_depth)
    return bool(tags), tags


def scan_forbidden_terms(text: str) -> list[str]:
    """Return forbidden accusatory term labels found after NFKC & invisible-character normalization."""
    normalized = _normalize_for_lint(text)
    return [label for label, pattern in _FORBIDDEN_TERM_PATTERNS if pattern.search(normalized)]


def lint_neutrality(
    title: str,
    summary: str,
    benign_explanations: list[str],
    extra_fields: dict[str, str] | None = None,
) -> LegalLintResult:
    """Validate that a lead uses neutral, non-defamatory civic-audit framing.

    Enforces BNS Sec 356 public-interest fair reporting standards:
    - Non-empty title and summary
    - Zero accusatory or defamatory terms (resistant to Unicode/zero-width obfuscation)
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

    fields_to_scan: list[tuple[str, str]] = [
        ("title", title or ""),
        ("summary", summary or ""),
        ("benign_explanations", " ".join(valid_benign)),
    ]
    if extra_fields:
        for k, v in extra_fields.items():
            if isinstance(v, str) and v.strip():
                fields_to_scan.append((k, v))

    for field_name, content in fields_to_scan:
        for label in scan_forbidden_terms(content):
            violations.append(
                f"Accusatory or defamatory term '{label}' is disallowed in {field_name}; "
                "use neutral factual framing such as 'public record anomaly' or 'statistical divergence'."
            )

    return LegalLintResult(passed=len(violations) == 0, violations=violations)


def compute_sha256(content: bytes | str) -> str:
    """Compute a lowercase hexadecimal SHA-256 digest for bytes or UTF-8 text."""
    data = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(data).hexdigest()


def compute_token_digest(token: str, *, pepper: str | None = None) -> str:
    """Compute a keyed HMAC-SHA256 digest when a server pepper is configured, else SHA-256."""
    secret = pepper if pepper is not None else os.environ.get("AIWATCHDOGS_TOKEN_PEPPER", "").strip()
    raw = token.encode("utf-8")
    if secret:
        return "hmac:" + hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return hashlib.sha256(raw).hexdigest()


def verify_token_digest(token: str, stored_hash: str, *, pepper: str | None = None) -> bool:
    """Constant-time token digest verification supporting both HMAC-SHA256 and legacy SHA-256."""
    if not token or not stored_hash:
        return False
    secret = pepper if pepper is not None else os.environ.get("AIWATCHDOGS_TOKEN_PEPPER", "").strip()
    if stored_hash.startswith("hmac:"):
        if not secret:
            return False
        expected = "hmac:" + hmac.new(secret.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, stored_hash)
    return hmac.compare_digest(compute_sha256(token), stored_hash)
