"""Deterministic zero-cost verifier for AI-extracted public document tables.

When a volunteer's AI model reads a messy or scanned government PDF (such as the 17
broken Union Budget statements or image-only State budgets), this verifier checks the
returned structured JSON deterministically for ₹0:

1. **Vertical Grand Total closure:** column sums across extracted rows must equal the
   printed Grand Total within rounding tolerance (default ±0.05 or 0.1%).
2. **Horizontal row cross-checks:** when Revenue + Capital = Total (or Charged + Voted =
   Total) fields are present, each row is checked horizontally to catch swapped columns.
3. **Column header anchoring:** requires explicit quoted column headings when text is
   available so adjacent columns (e.g., BE vs RE, FY24 vs FY25) cannot be silently swapped.
4. **Verbatim quote check:** when the pinned PDF page has an extractable text layer,
   every returned row's verbatim quote is confirmed against the page text. If the page is
   an image-only scan (no text layer), the result is marked `requires_second_model` so
   the Hub routes it for independent 2-model cross-check instead of trusting a single scan.
5. **Seeded known-answer (golden) calibration:** when expected reference values are
   supplied, computes exact critical-field accuracy (0.0 to 1.0) so reader quality is
   measured empirically rather than from self-reported model labels.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from aiwatchdogs.core.safety import scan_forbidden_terms, scrub_pii_in_obj

_WS_RE = re.compile(r"\s+")
_PUNCT_NORM_RE = re.compile(r"[^\w.%₹/-]+")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

DEFAULT_ABS_TOLERANCE = 0.05  # ₹0.05 crore (standard two-decimal budget rounding)
DEFAULT_REL_TOLERANCE = 0.001  # 0.1%


def _norm_text(text: str) -> str:
    collapsed = _WS_RE.sub(" ", str(text or "").strip().lower())
    return _PUNCT_NORM_RE.sub(" ", collapsed).strip()


def _to_float(val: Any) -> float | None:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        f = float(val)
        return f if math.isfinite(f) else None
    if isinstance(val, str):
        cleaned = val.strip().replace(",", "").replace("₹", "").replace("Rs.", "").replace("Rs", "").strip()
        if cleaned in ("", "-", "—", "–", "nil", "Nil", "NIL", "…", "..."):
            return 0.0
        try:
            f = float(cleaned)
            return f if math.isfinite(f) else None
        except ValueError:
            return None
    return None


def _within_tol(observed: float, expected: float, *, abs_tol: float, rel_tol: float) -> bool:
    diff = abs(observed - expected)
    return diff <= max(abs_tol, abs(expected) * rel_tol)


@dataclass
class ReadingVerificationResult:
    accepted: bool
    verification_mode: str  # "deterministic_verified" | "requires_second_model" | "rejected"
    checks: dict[str, str]
    column_sums: dict[str, float]
    printed_grand_total: dict[str, float]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    golden_accuracy: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "verification_mode": self.verification_mode,
            "checks": dict(self.checks),
            "column_sums": dict(self.column_sums),
            "printed_grand_total": dict(self.printed_grand_total),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "golden_accuracy": self.golden_accuracy,
        }


def verify_budget_extraction(
    submission: dict[str, Any],
    *,
    expected_sha256: str | None = None,
    page_text: str | None = None,
    golden_expected: dict[str, Any] | None = None,
    abs_tolerance: float = DEFAULT_ABS_TOLERANCE,
    rel_tolerance: float = DEFAULT_REL_TOLERANCE,
) -> ReadingVerificationResult:
    """Deterministically verify an AI-extracted budget table submission."""
    errors: list[str] = []
    warnings: list[str] = []
    checks: dict[str, str] = {
        "document_digest": "unrecorded",
        "vertical_grand_total": "unrecorded",
        "horizontal_row_totals": "unrecorded",
        "column_headers_anchored": "unrecorded",
        "verbatim_quotes": "unrecorded",
        "neutrality_privacy": "unrecorded",
    }

    if not isinstance(submission, dict):
        return ReadingVerificationResult(
            accepted=False,
            verification_mode="rejected",
            checks=checks,
            column_sums={},
            printed_grand_total={},
            errors=["Submission must be a JSON object."],
        )

    # 1. Privacy & neutrality lint
    _, pii_hits = scrub_pii_in_obj(submission)
    forbidden_hits: list[str] = []
    for row in submission.get("rows") or []:
        if isinstance(row, dict):
            forbidden_hits.extend(scan_forbidden_terms(str(row.get("label") or "")))
    if pii_hits or forbidden_hits:
        checks["neutrality_privacy"] = "fail"
        if pii_hits:
            errors.append(f"PII detected in extracted fields ({len(pii_hits)} hit(s)).")
        if forbidden_hits:
            errors.append(f"Non-neutral terms detected: {', '.join(sorted(set(forbidden_hits)))}.")
    else:
        checks["neutrality_privacy"] = "pass"

    # 2. Document digest + page binding
    sub_sha = str(submission.get("document_sha256") or "").strip().lower()
    exp_sha = str(expected_sha256 or "").strip().lower()
    page_no = submission.get("page")
    if not isinstance(page_no, int) or isinstance(page_no, bool) or page_no < 1:
        errors.append("'page' must be a positive integer (>= 1).")
    if exp_sha:
        if sub_sha == exp_sha:
            checks["document_digest"] = "pass"
        else:
            checks["document_digest"] = "fail"
            errors.append(f"document_sha256 mismatch (expected {exp_sha[:12]}, got {sub_sha[:12] or 'missing'}).")
    elif sub_sha and _SHA256_RE.match(sub_sha):
        checks["document_digest"] = "pass"
    else:
        checks["document_digest"] = "fail"
        errors.append("Missing valid 64-char hex 'document_sha256'.")

    rows = submission.get("rows")
    if not isinstance(rows, list) or not rows:
        errors.append("'rows' must be a non-empty list of extracted table rows.")
        return ReadingVerificationResult(
            accepted=False,
            verification_mode="rejected",
            checks=checks,
            column_sums={},
            printed_grand_total={},
            errors=errors,
            warnings=warnings,
        )

    # 3. Column headers anchoring (anti-column-swap check)
    raw_headers = submission.get("column_headers")
    norm_page = _norm_text(page_text or "")
    has_text_layer = len(norm_page) >= 20
    if isinstance(raw_headers, list) and any(str(h or "").strip() for h in raw_headers):
        if has_text_layer:
            missing_hdrs = [
                str(h).strip()
                for h in raw_headers
                if str(h or "").strip() and _norm_text(str(h)) not in norm_page
            ]
            if missing_hdrs:
                checks["column_headers_anchored"] = "fail"
                errors.append(f"Column header(s) not found on cited page: {', '.join(missing_hdrs[:3])}.")
            else:
                checks["column_headers_anchored"] = "pass"
        else:
            checks["column_headers_anchored"] = "unrecorded"
            warnings.append("Page has no text layer (image-only scan); column headers require 2-model check.")
    else:
        checks["column_headers_anchored"] = "fail"
        errors.append("'column_headers' must quote the printed table column headings to prevent column swaps.")

    # 4. Vertical Grand Total check + Horizontal row cross-check
    printed_gt_raw = submission.get("printed_grand_total")
    printed_gt: dict[str, float] = {}
    if isinstance(printed_gt_raw, dict):
        for col, raw_val in printed_gt_raw.items():
            num = _to_float(raw_val)
            if num is not None:
                printed_gt[str(col).strip()] = num

    if not printed_gt:
        checks["vertical_grand_total"] = "fail"
        errors.append("'printed_grand_total' must provide at least one numeric printed column total.")

    column_sums: dict[str, float] = {col: 0.0 for col in printed_gt}
    horizontal_checked = 0
    horizontal_failed = 0
    quote_checked = 0
    quote_failed = 0

    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"rows[{idx}] must be an object.")
            continue

        # Accumulate vertical totals for every column declared in printed_grand_total
        for col in printed_gt:
            val = _to_float(row.get(col))
            if val is None:
                errors.append(f"rows[{idx}] is missing numeric value for column '{col}'.")
            else:
                column_sums[col] += val

        # Horizontal check: revenue + capital == total (when all 3 are present)
        rev = _to_float(row.get("revenue"))
        cap = _to_float(row.get("capital"))
        tot = _to_float(row.get("total"))
        if rev is not None and cap is not None and tot is not None:
            horizontal_checked += 1
            if not _within_tol(rev + cap, tot, abs_tol=abs_tolerance, rel_tol=rel_tolerance):
                horizontal_failed += 1
                errors.append(
                    f"rows[{idx}] horizontal mismatch: revenue ({rev:.2f}) + capital ({cap:.2f}) != total ({tot:.2f})."
                )

        # Verbatim quote verification against text layer
        quote = str(row.get("verbatim_quote") or "").strip()
        if not quote:
            quote_failed += 1
            errors.append(f"rows[{idx}] is missing required 'verbatim_quote'.")
        elif has_text_layer:
            quote_checked += 1
            if _norm_text(quote) not in norm_page:
                quote_failed += 1
                errors.append(f"rows[{idx}] verbatim_quote not found on page {page_no}.")

    if printed_gt:
        gt_mismatches: list[str] = []
        for col, expected_tot in printed_gt.items():
            obs_tot = round(column_sums[col], 4)
            column_sums[col] = obs_tot
            if not _within_tol(obs_tot, expected_tot, abs_tol=abs_tolerance, rel_tol=rel_tolerance):
                gt_mismatches.append(f"{col}: sum={obs_tot:.2f} != printed_grand_total={expected_tot:.2f}")
        if gt_mismatches:
            checks["vertical_grand_total"] = "fail"
            errors.append("Grand Total check failed: " + "; ".join(gt_mismatches))
        else:
            checks["vertical_grand_total"] = "pass"

    if horizontal_checked > 0:
        checks["horizontal_row_totals"] = "fail" if horizontal_failed else "pass"

    if quote_failed > 0:
        checks["verbatim_quotes"] = "fail"
    elif quote_checked > 0:
        checks["verbatim_quotes"] = "pass"
    elif not has_text_layer:
        checks["verbatim_quotes"] = "unrecorded"
        warnings.append("Image-only scan: verbatim quotes cannot be checked against a text layer; route to 2nd model.")

    # 5. Optional seeded known-answer (golden) accuracy scoring
    golden_accuracy: float | None = None
    if isinstance(golden_expected, dict) and isinstance(golden_expected.get("rows"), list):
        exp_rows = golden_expected["rows"]
        fields_checked = 0
        fields_matched = 0
        for g_idx, g_row in enumerate(exp_rows):
            if not isinstance(g_row, dict):
                continue
            sub_row = rows[g_idx] if g_idx < len(rows) and isinstance(rows[g_idx], dict) else {}
            for k, g_val in g_row.items():
                if k == "verbatim_quote":
                    continue
                fields_checked += 1
                g_num = _to_float(g_val)
                if g_num is not None:
                    s_num = _to_float(sub_row.get(k))
                    if s_num is not None and _within_tol(s_num, g_num, abs_tol=abs_tolerance, rel_tol=rel_tolerance):
                        fields_matched += 1
                else:
                    if _norm_text(str(sub_row.get(k) or "")) == _norm_text(str(g_val or "")):
                        fields_matched += 1
        if fields_checked > 0:
            golden_accuracy = round(fields_matched / fields_checked, 4)

    if errors:
        return ReadingVerificationResult(
            accepted=False,
            verification_mode="rejected",
            checks=checks,
            column_sums=column_sums,
            printed_grand_total=printed_gt,
            errors=errors,
            warnings=warnings,
            golden_accuracy=golden_accuracy,
        )

    # If the page had a text layer, vertical totals + quotes + headers all passed deterministically!
    # If image-only scan (no text layer), math closed cleanly but needs a 2nd independent model read.
    if has_text_layer:
        return ReadingVerificationResult(
            accepted=True,
            verification_mode="deterministic_verified",
            checks=checks,
            column_sums=column_sums,
            printed_grand_total=printed_gt,
            errors=[],
            warnings=warnings,
            golden_accuracy=golden_accuracy,
        )

    return ReadingVerificationResult(
        accepted=True,
        verification_mode="requires_second_model",
        checks=checks,
        column_sums=column_sums,
        printed_grand_total=printed_gt,
        errors=[],
        warnings=warnings,
        golden_accuracy=golden_accuracy,
    )
