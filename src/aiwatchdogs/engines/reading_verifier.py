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
_NUM_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])(-?\d[\d,]*(?:\.\d+)?)(?![A-Za-z0-9_])")
_GRAND_TOTAL_QUOTE_RE = re.compile(r"\bgrand\s+total\b", re.IGNORECASE)

DEFAULT_ABS_TOLERANCE = 0.05  # ₹0.05 crore (standard two-decimal budget rounding)
DEFAULT_REL_TOLERANCE = 0.001  # 0.1%


def _extract_numeric_tokens(text: str) -> set[float]:
    """Extract all finite numeric values appearing in a page or quote snippet."""
    vals: set[float] = set()
    for match in _NUM_TOKEN_RE.finditer(str(text or "")):
        raw = match.group(1).replace(",", "")
        try:
            f = float(raw)
        except ValueError:
            continue
        if math.isfinite(f):
            vals.add(round(f, 4))
    return vals


def _num_present_in_set(val: float, token_set: set[float], tol: float = 0.005) -> bool:
    rounded = round(val, 4)
    if rounded in token_set:
        return True
    return any(abs(rounded - tok) <= tol for tok in token_set)


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


def _check_horizontal_triples(
    record: dict[str, Any],
    *,
    label_prefix: str,
    abs_tol: float,
    rel_tol: float,
) -> tuple[int, int, list[str]]:
    """Check Revenue+Capital=Total, Plan+Non-Plan=Total, Charged+Voted=Total (both bare and prefixed)."""
    checked = 0
    failed = 0
    errors: list[str] = []
    triple_specs: list[tuple[str, str, str]] = [
        ("revenue", "capital", "total"),
        ("plan", "non_plan", "total"),
        ("charged", "voted", "total"),
    ]
    # Also discover multi-period prefixed triples such as actual_plan / actual_non_plan / actual_total
    # or be_2022_23_revenue / be_2022_23_capital / be_2022_23_total.
    keys = [str(k) for k in record.keys()]
    seen_triples: set[tuple[str, str, str]] = set(triple_specs)
    for key in keys:
        for left_suf, right_suf, tot_suf in (
            ("_revenue", "_capital", "_total"),
            ("_plan", "_non_plan", "_total"),
            ("_charged", "_voted", "_total"),
        ):
            if key.endswith(left_suf):
                prefix = key[: -len(left_suf)]
                triple = (f"{prefix}{left_suf}", f"{prefix}{right_suf}", f"{prefix}{tot_suf}")
                if triple not in seen_triples:
                    seen_triples.add(triple)
                    triple_specs.append(triple)

    for k_left, k_right, k_tot in triple_specs:
        left_val = _to_float(record.get(k_left))
        right_val = _to_float(record.get(k_right))
        tot_val = _to_float(record.get(k_tot))
        if left_val is not None and right_val is not None and tot_val is not None:
            checked += 1
            if not _within_tol(left_val + right_val, tot_val, abs_tol=abs_tol, rel_tol=rel_tol):
                failed += 1
                errors.append(
                    f"{label_prefix} horizontal mismatch: {k_left} ({left_val:.2f}) + "
                    f"{k_right} ({right_val:.2f}) != {k_tot} ({tot_val:.2f})."
                )
    return checked, failed, errors


def verify_budget_extraction(
    submission: dict[str, Any],
    *,
    expected_sha256: str | None = None,
    page_text: str | None = None,
    page_texts: dict[int, str] | None = None,
    expected_grand_total: dict[str, float] | None = None,
    max_page: int | None = None,
    min_rows: int = 1,
    require_text_verification: bool = False,
    golden_expected: dict[str, Any] | None = None,
    min_golden_accuracy: float | None = None,
    abs_tolerance: float = DEFAULT_ABS_TOLERANCE,
    rel_tolerance: float = DEFAULT_REL_TOLERANCE,
) -> ReadingVerificationResult:
    """Deterministically verify an AI-extracted budget table submission (single-page or multi-page)."""
    errors: list[str] = []
    warnings: list[str] = []
    checks: dict[str, str] = {
        "document_digest": "unrecorded",
        "vertical_grand_total": "unrecorded",
        "grand_total_anchor": "unrecorded",
        "horizontal_row_totals": "unrecorded",
        "column_headers_anchored": "unrecorded",
        "verbatim_quotes": "unrecorded",
        "row_figures_grounded": "unrecorded",
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
    pages_list = submission.get("pages")
    if page_no is None and isinstance(pages_list, list) and pages_list:
        first_p = pages_list[0]
        if isinstance(first_p, int) and not isinstance(first_p, bool) and first_p >= 1:
            page_no = first_p
    if not isinstance(page_no, int) or isinstance(page_no, bool) or page_no < 1:
        errors.append("'page' must be a positive integer (>= 1).")
    elif max_page is not None and page_no > max_page:
        errors.append(f"'page' ({page_no}) exceeds document page count ({max_page}).")

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
    if min_rows > 1 and len(rows) < min_rows:
        errors.append(
            f"Expected at least {min_rows} extracted leaf rows for this multi-line statement, got {len(rows)}."
        )

    # Build normalized text and numeric-token lookups across one or more pages
    norm_pages: dict[int, str] = {}
    page_num_tokens: dict[int, set[float]] = {}
    if isinstance(page_texts, dict):
        for p_idx, p_txt in page_texts.items():
            try:
                p_int = int(p_idx)
            except (TypeError, ValueError):
                continue
            raw_p = str(p_txt or "")
            norm_pages[p_int] = _norm_text(raw_p)
            page_num_tokens[p_int] = _extract_numeric_tokens(raw_p)
    if page_text is not None:
        key_p = page_no if isinstance(page_no, int) and page_no >= 1 else 1
        norm_pages.setdefault(key_p, _norm_text(page_text))
        page_num_tokens.setdefault(key_p, _extract_numeric_tokens(page_text))
    combined_norm_text = " ".join(v for v in norm_pages.values() if v)
    combined_num_tokens: set[float] = set()
    for tset in page_num_tokens.values():
        combined_num_tokens.update(tset)
    has_text_layer = len(combined_norm_text) >= 20
    if require_text_verification and not has_text_layer:
        errors.append("Server-side text verification is required for this document, but no page text layer was loaded.")

    # 3. Column headers anchoring (anti-column-swap check)
    raw_headers = submission.get("column_headers")
    if isinstance(raw_headers, list) and any(str(h or "").strip() for h in raw_headers):
        if has_text_layer:
            missing_hdrs = [
                str(h).strip()
                for h in raw_headers
                if str(h or "").strip() and _norm_text(str(h)) not in combined_norm_text
            ]
            if missing_hdrs:
                checks["column_headers_anchored"] = "fail"
                errors.append(f"Column header(s) not found on cited page(s): {', '.join(missing_hdrs[:3])}.")
            else:
                checks["column_headers_anchored"] = "pass"
        else:
            checks["column_headers_anchored"] = "unrecorded"
            warnings.append("No text layer provided; column headers require text verification or 2-model check.")
    else:
        checks["column_headers_anchored"] = "fail"
        errors.append("'column_headers' must quote the printed table column headings to prevent column swaps.")

    # 4. Vertical Grand Total check + Official Grand Total Anchor + Horizontal row cross-check
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

    # Official Grand Total anchor check (prevents fabricated self-consistent tables)
    if isinstance(expected_grand_total, dict) and expected_grand_total:
        anchor_mismatches: list[str] = []
        for col, raw_exp in expected_grand_total.items():
            exp_val = _to_float(raw_exp)
            if exp_val is None:
                continue
            obs_gt = printed_gt.get(col)
            if obs_gt is None:
                anchor_mismatches.append(f"{col}: missing from printed_grand_total (expected {exp_val:.2f})")
            elif not _within_tol(obs_gt, exp_val, abs_tol=abs_tolerance, rel_tol=rel_tolerance):
                anchor_mismatches.append(f"{col}: submitted Grand Total={obs_gt:.2f} != official={exp_val:.2f}")
        if anchor_mismatches:
            checks["grand_total_anchor"] = "fail"
            errors.append("Official Grand Total anchor mismatch: " + "; ".join(anchor_mismatches))
        else:
            checks["grand_total_anchor"] = "pass"

    column_sums: dict[str, float] = {col: 0.0 for col in printed_gt}
    horizontal_checked = 0
    horizontal_failed = 0
    quote_checked = 0
    quote_failed = 0
    grounded_checked = 0
    grounded_failed = 0

    if printed_gt:
        gt_h_chk, gt_h_fail, gt_h_errs = _check_horizontal_triples(
            printed_gt,
            label_prefix="printed_grand_total",
            abs_tol=abs_tolerance,
            rel_tol=rel_tolerance,
        )
        horizontal_checked += gt_h_chk
        horizontal_failed += gt_h_fail
        errors.extend(gt_h_errs)

    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"rows[{idx}] must be an object.")
            continue

        row_page = row.get("page", page_no)
        if not isinstance(row_page, int) or isinstance(row_page, bool) or row_page < 1:
            errors.append(f"rows[{idx}].page must be a positive integer (>= 1).")
            row_page = page_no if isinstance(page_no, int) else 1
        elif max_page is not None and row_page > max_page:
            errors.append(f"rows[{idx}].page ({row_page}) exceeds document page count ({max_page}).")

        # Accumulate vertical totals for every column declared in printed_grand_total
        row_nonzero_vals: list[tuple[str, float]] = []
        for col in printed_gt:
            val = _to_float(row.get(col))
            if val is None:
                errors.append(f"rows[{idx}] is missing numeric value for column '{col}'.")
            else:
                column_sums[col] += val
                if abs(val) > 1e-6:
                    row_nonzero_vals.append((col, val))

        # Horizontal checks (Revenue+Capital=Total, Plan+Non-Plan=Total, Charged+Voted=Total, incl. prefixed)
        h_chk, h_fail, h_errs = _check_horizontal_triples(
            row,
            label_prefix=f"rows[{idx}]",
            abs_tol=abs_tolerance,
            rel_tol=rel_tolerance,
        )
        horizontal_checked += h_chk
        horizontal_failed += h_fail
        errors.extend(h_errs)

        # Verbatim quote verification + numeric figure grounding against text layer
        quote = str(row.get("verbatim_quote") or "").strip()
        if not quote:
            quote_failed += 1
            errors.append(f"rows[{idx}] is missing required 'verbatim_quote'.")
        elif _GRAND_TOTAL_QUOTE_RE.search(quote):
            quote_failed += 1
            errors.append(f"rows[{idx}] verbatim_quote must cite a leaf row, not the Grand Total summary line.")
        elif has_text_layer:
            quote_checked += 1
            norm_q = _norm_text(quote)
            target_text = norm_pages.get(row_page) or combined_norm_text
            if norm_q not in target_text and norm_q not in combined_norm_text:
                quote_failed += 1
                errors.append(f"rows[{idx}] verbatim_quote not found on page {row_page}.")
            elif row_nonzero_vals:
                grounded_checked += 1
                page_nums = page_num_tokens.get(row_page) or combined_num_tokens
                missing_on_page = [
                    f"{col}={val:.2f}"
                    for col, val in row_nonzero_vals
                    if not _num_present_in_set(val, page_nums) and not _num_present_in_set(val, combined_num_tokens)
                ]
                quote_nums = _extract_numeric_tokens(quote)
                any_in_quote = any(_num_present_in_set(val, quote_nums) for _, val in row_nonzero_vals)
                if missing_on_page:
                    grounded_failed += 1
                    errors.append(
                        f"rows[{idx}] figure(s) not printed on page {row_page}: {', '.join(missing_on_page[:3])}."
                    )
                elif not any_in_quote:
                    grounded_failed += 1
                    errors.append(
                        f"rows[{idx}] verbatim_quote does not include any of the row's non-zero numeric figures."
                    )

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

    if grounded_failed > 0:
        checks["row_figures_grounded"] = "fail"
    elif grounded_checked > 0:
        checks["row_figures_grounded"] = "pass"
    elif has_text_layer:
        checks["row_figures_grounded"] = "pass"

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
            if min_golden_accuracy is not None:
                if golden_accuracy >= float(min_golden_accuracy):
                    checks["golden_calibration"] = "pass"
                else:
                    checks["golden_calibration"] = "fail"
                    errors.append(
                        f"Golden calibration accuracy {golden_accuracy:.2%} below minimum required "
                        f"{float(min_golden_accuracy):.2%}."
                    )

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

    # Deterministically verified ONLY when the document's text layer verified quotes, headers,
    # row figures, and totals (not merely self-consistent numbers without text verification).
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
