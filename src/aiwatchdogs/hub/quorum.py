"""Shared 3-of-3 pairwise cross-harness & cross-model peer-verification quorum rules.

Both ``DeskService`` (claim-time SQL filtering, explicit claim guard, submit-time validation,
and quorum tallying) and ``Database`` migration v14 (retroactive backfill) call these helpers so
live enforcement and retroactive migration follow one exact specification.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

DEFAULT_REQUIRED_QUORUM = 3

CANONICAL_CHECKS: tuple[str, ...] = (
    "bytes_digest",
    "deterministic_reproduction",
    "metric_tolerance",
    "neutrality_privacy",
)
LEGACY_CHECK_TO_CANONICAL: dict[str, str] = {
    "provenance_ok": "bytes_digest",
    "math_reproduced": "deterministic_reproduction",
    "rule_valid": "metric_tolerance",
    "legal_pii_ok": "neutrality_privacy",
}
VALID_CHECK_STATES: frozenset[str] = frozenset({"pass", "fail", "unrecorded"})


def _get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        val = row.get(key, default)
        return default if val is None else val
    try:
        val = row[key]
        return default if val is None else val
    except (KeyError, IndexError, TypeError):
        return default


def norm_attr(value: Any) -> str:
    """Return case-insensitive trimmed attribution string (empty when unattributed)."""
    return str(value or "").strip().lower()


def normalize_check_state(raw_val: Any, *, field_name: str = "", strict: bool = False) -> str:
    """Normalize a single verification check state; missing values ALWAYS become ``'unrecorded'``."""
    if raw_val is None:
        return "unrecorded"
    if isinstance(raw_val, bool):
        return "pass" if raw_val else "fail"
    if isinstance(raw_val, str):
        norm = raw_val.strip().lower()
        if norm in VALID_CHECK_STATES:
            return norm
        if norm in {"true", "ok", "passed"}:
            return "pass"
        if norm in {"false", "failed"}:
            return "fail"
        if norm in {"", "unknown", "none", "null", "n/a"}:
            return "unrecorded"
    if strict:
        raise ValueError(f"checks.{field_name} must be 'pass', 'fail', or 'unrecorded'")
    return "unrecorded"


def normalize_checks(raw_checks: Any, *, strict: bool = False) -> dict[str, str]:
    """Normalize verification checks to the 4 canonical tri-state keys.

    Missing checks ALWAYS normalize to ``"unrecorded"`` — never ``"pass"``.
    """
    result: dict[str, str] = {key: "unrecorded" for key in CANONICAL_CHECKS}
    if not isinstance(raw_checks, dict):
        if strict and raw_checks is not None:
            raise ValueError("verification.checks must be an object")
        return result

    for legacy_key, canon_key in LEGACY_CHECK_TO_CANONICAL.items():
        if legacy_key in raw_checks:
            result[canon_key] = normalize_check_state(
                raw_checks[legacy_key], field_name=legacy_key, strict=strict
            )

    for canon_key in CANONICAL_CHECKS:
        if canon_key in raw_checks:
            result[canon_key] = normalize_check_state(
                raw_checks[canon_key], field_name=canon_key, strict=strict
            )

    return result


def all_checks_passed(checks: dict[str, str]) -> bool:
    """Return True iff every canonical check recorded explicit ``'pass'``."""
    return all(checks.get(k) == "pass" for k in CANONICAL_CHECKS)


def build_attr_conflict_sql(col_expr: str, candidate_norm: str) -> tuple[str, list[Any]]:
    """Build a SQLite + Postgres portable predicate matching rows that conflict with ``candidate_norm``.

    - When ``candidate_norm`` is non-empty: matches rows where ``col_expr`` is non-empty and equals
      ``candidate_norm`` case-insensitively.
    - When ``candidate_norm`` is empty: matches any row where ``col_expr`` is non-empty (so an
      unattributed verifier cannot audit a finding whose participants declared attribution, while
      fully unattributed legacy findings do not deadlock).
    """
    if candidate_norm:
        return (
            f"(TRIM(COALESCE({col_expr}, '')) != '' AND LOWER(TRIM({col_expr})) = ?)",
            [candidate_norm],
        )
    return (f"TRIM(COALESCE({col_expr}, '')) != ''", [])


def check_pairwise_verifier_independence(
    *,
    finding_id: str,
    candidate_handle: Any,
    candidate_harness: Any,
    candidate_model: Any,
    author_handle: Any,
    author_harness: Any,
    author_model: Any,
    existing_participants: Iterable[tuple[str, Any, Any, Any]] = (),
) -> str | None:
    """Validate pairwise independence of ``(handle, coding_agent, coding_agent_model)`` across all participants.

    Compares the candidate verifier against the finding's author PLUS every existing entry in
    ``peer_verifications`` AND every other active ``claimed`` ``peer_verify`` lease on the same
    finding. Strictly uses ``coding_agent`` (harness) and ``coding_agent_model`` (model) — NEVER
    falling back to ``provider`` / ``model``.

    Returns a descriptive conflict message if violated, or ``None`` if eligible.
    """
    cand_handle_raw = str(candidate_handle or "").strip()
    cand_handle = norm_attr(cand_handle_raw)
    cand_harness_raw = str(candidate_harness or "").strip()
    cand_harness = norm_attr(cand_harness_raw)
    cand_model_raw = str(candidate_model or "").strip()
    cand_model = norm_attr(cand_model_raw)

    if cand_handle and cand_handle == norm_attr(author_handle):
        return (
            f"Self-verification prohibited: agent '{cand_handle_raw}' cannot verify "
            f"its own finding '{finding_id}'."
        )

    used_harnesses: set[str] = set()
    used_models: set[str] = set()

    a_harness = norm_attr(author_harness)
    a_model = norm_attr(author_model)
    if a_harness:
        used_harnesses.add(a_harness)
    if a_model:
        used_models.add(a_model)

    for source_label, p_handle, p_harness, p_model in existing_participants:
        if cand_handle and norm_attr(p_handle) == cand_handle:
            if source_label == "active_lease":
                return (
                    f"Agent '{cand_handle_raw}' already holds an active verification lease "
                    f"for finding '{finding_id}'."
                )
            return (
                f"Duplicate verification prohibited: agent '{cand_handle_raw}' has already "
                f"verified finding '{finding_id}'."
            )
        nh = norm_attr(p_harness)
        nm = norm_attr(p_model)
        if nh:
            used_harnesses.add(nh)
        if nm:
            used_models.add(nm)

    if used_harnesses:
        if not cand_harness:
            return (
                f"Pairwise cross-harness independence required for finding '{finding_id}': "
                "the author or another reviewer/active lease declared a coding_agent harness, "
                "so verifier must declare a distinct non-empty coding_agent."
            )
        if cand_harness in used_harnesses:
            return (
                f"Pairwise cross-harness independence required for finding '{finding_id}': "
                f"verifier harness '{cand_harness_raw}' is already used by the author or "
                "another peer reviewer/active lease on this finding."
            )

    if used_models:
        if not cand_model:
            return (
                f"Pairwise cross-model independence required for finding '{finding_id}': "
                "the author or another reviewer/active lease declared a coding_agent_model, "
                "so verifier must declare a distinct non-empty coding_agent_model."
            )
        if cand_model in used_models:
            return (
                f"Pairwise cross-model independence required for finding '{finding_id}': "
                f"verifier model '{cand_model_raw}' is already used by the author or "
                "another peer reviewer/active lease on this finding."
            )

    return None


def evaluate_pairwise_quorum(
    *,
    author_handle: Any,
    author_harness: Any,
    author_model: Any,
    votes: Iterable[Any],
    loads_json_fn: Callable[[str | None, Any], Any],
) -> dict[str, Any]:
    """Compute pairwise-independent quorum counts over ``peer_verifications`` ordered ``created_at ASC, id ASC``.

    A ``confirm`` vote counts toward ``qualified_confirms`` only when:
    1. All 4 canonical checks are ``'pass'`` and ``reproduced_metrics`` is a non-empty dict.
    2. Its handle is distinct from the author and every previously counted confirm.
    3. Whenever the author or any previously counted confirm declared ``coding_agent``, this
       vote has a non-empty ``coding_agent`` distinct from all of them.
    4. Whenever the author or any previously counted confirm declared ``coding_agent_model``, this
       vote has a non-empty ``coding_agent_model`` distinct from all of them.

    ``consensus`` is computed over ``effective_votes = qualified_confirms + dispute_count`` so that
    legacy non-independent confirms retained in the audit trail never permanently poison consensus
    below 0.75 once 3 pairwise-independent confirmations arrive.
    """
    author_h_raw = str(author_handle or "").strip()
    author_h = norm_attr(author_h_raw)
    author_har = norm_attr(author_harness)
    author_mod = norm_attr(author_model)

    seen_handles: set[str] = {author_h} if author_h else set()
    seen_harnesses: set[str] = {author_har} if author_har else set()
    seen_models: set[str] = {author_mod} if author_mod else set()

    qualified_confirms = 0
    dispute_count = 0
    has_pii_or_legal_flag = False
    raw_vote_count = 0
    counted_verdicts: list[str] = []
    voter_handles: set[str] = {author_h_raw} if author_h_raw else set()

    for v in votes:
        raw_vote_count += 1
        raw_handle = str(_get(v, "contributor_id") or _get(v, "verifier_handle") or "").strip()
        if raw_handle:
            voter_handles.add(raw_handle)
        v_handle = norm_attr(raw_handle)
        v_harness = norm_attr(_get(v, "coding_agent"))
        v_model = norm_attr(_get(v, "coding_agent_model"))
        v_verdict = str(_get(v, "verdict") or "").strip().lower()

        if v_verdict == "flag_pii_or_legal":
            has_pii_or_legal_flag = True
            dispute_count += 1
            counted_verdicts.append(v_verdict)
        elif v_verdict == "confirm":
            v_checks = normalize_checks(loads_json_fn(_get(v, "checks_json"), {}), strict=False)
            v_metrics = loads_json_fn(_get(v, "reproduced_metrics_json"), {})
            if not (all_checks_passed(v_checks) and isinstance(v_metrics, dict) and len(v_metrics) > 0):
                continue
            if not v_handle or v_handle in seen_handles:
                continue
            if seen_harnesses and (not v_harness or v_harness in seen_harnesses):
                continue
            if seen_models and (not v_model or v_model in seen_models):
                continue
            qualified_confirms += 1
            seen_handles.add(v_handle)
            if v_harness:
                seen_harnesses.add(v_harness)
            if v_model:
                seen_models.add(v_model)
            counted_verdicts.append(v_verdict)
        else:
            dispute_count += 1
            counted_verdicts.append(v_verdict)

    effective_votes = qualified_confirms + dispute_count
    consensus = round(qualified_confirms / effective_votes, 3) if effective_votes > 0 else 0.0
    all_fp = bool(counted_verdicts) and all(vd == "reject_false_positive" for vd in counted_verdicts)

    return {
        "qualified_confirms": qualified_confirms,
        "dispute_count": dispute_count,
        "effective_votes": effective_votes,
        "raw_vote_count": raw_vote_count,
        "consensus": consensus,
        "has_pii_or_legal_flag": has_pii_or_legal_flag,
        "all_fp": all_fp,
        "voter_handles": voter_handles,
    }
