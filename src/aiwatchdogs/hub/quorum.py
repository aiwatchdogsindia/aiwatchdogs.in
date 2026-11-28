"""Shared 3-of-3 pairwise cross-harness & cross-model peer-verification quorum rules.

Both ``DeskService`` (claim-time SQL filtering, explicit claim guard, submit-time validation,
and quorum tallying) and ``Database`` migration v14 (retroactive backfill) call these helpers so
live enforcement and retroactive migration follow one exact specification.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable

UNSUPPORTED_ENGINE_NOTE_RE = re.compile(
    r"\b(?:unknown\s+methodology(?:_code)?|unsupported\s+methodology|no\s+engine\s+for\s+methodology)\b",
    re.IGNORECASE,
)


def is_unsupported_engine_vote(vote: Any) -> bool:
    """Return True when a peer verification vote came from a client lacking the engine.

    A worker that could not execute the methodology must never change a finding's consensus
    or earn verification points.
    """
    notes = str(_get(vote, "notes", "") or "")
    return bool(UNSUPPORTED_ENGINE_NOTE_RE.search(notes))

DEFAULT_REQUIRED_QUORUM = 3
TASK_PROGRESS_WEIGHT = 70.0
REVIEW_PROGRESS_WEIGHT = 30.0

IN_REVIEW_FINDING_STATUSES: tuple[str, ...] = (
    "peer_review",
    "raw_lead",
    "needs_adjudication",
)
RESOLVED_REVIEW_FINDING_STATUSES: tuple[str, ...] = (
    "verified",
    "rti_filed",
    "official_response_logged",
    "disputed",
    "retracted",
    "retracted_false_positive",
)
REVIEWABLE_FINDING_STATUSES: tuple[str, ...] = (
    IN_REVIEW_FINDING_STATUSES + RESOLVED_REVIEW_FINDING_STATUSES
)

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


def _same_ip_sybil_conflict(
    *,
    cand_verified: bool,
    cand_gh_uid: str,
    cand_ip_hash: str,
    other_verified: bool,
    other_gh_uid: str,
    other_ip_hash: str,
) -> bool:
    """Return True when two participants share a registration IP hash without distinct verified GitHub IDs."""
    if not cand_ip_hash or not other_ip_hash or cand_ip_hash != other_ip_hash:
        return False
    # Two distinct GitHub-verified contributors on the same office/campus NAT are genuinely independent.
    if cand_verified and other_verified and cand_gh_uid and other_gh_uid and cand_gh_uid != other_gh_uid:
        return False
    return True


def check_pairwise_verifier_independence(
    *,
    finding_id: str,
    candidate_handle: Any,
    candidate_harness: Any,
    candidate_model: Any,
    author_handle: Any,
    author_harness: Any,
    author_model: Any,
    existing_participants: Iterable[tuple[Any, ...]] = (),
    candidate_github_verified: bool = False,
    candidate_github_user_id: Any = None,
    candidate_ip_hash: Any = None,
    author_github_verified: bool = False,
    author_github_user_id: Any = None,
    author_ip_hash: Any = None,
) -> str | None:
    """Validate pairwise independence of ``(handle, coding_agent, coding_agent_model, identity)`` across all participants.

    Compares the candidate verifier against the finding's author PLUS every existing entry in
    ``peer_verifications`` AND every other active ``claimed`` ``peer_verify`` lease on the same
    finding. Strictly uses ``coding_agent`` (harness) and ``coding_agent_model`` (model) — NEVER
    falling back to ``provider`` / ``model``. Also blocks same-GitHub-user-ID reuse and same-IP
    unverified Sybil sockpuppets from verifying the same finding.

    Returns a descriptive conflict message if violated, or ``None`` if eligible.
    """
    cand_handle_raw = str(candidate_handle or "").strip()
    cand_handle = norm_attr(cand_handle_raw)
    cand_harness_raw = str(candidate_harness or "").strip()
    cand_harness = norm_attr(cand_harness_raw)
    cand_model_raw = str(candidate_model or "").strip()
    cand_model = norm_attr(cand_model_raw)
    cand_gh_verified = bool(candidate_github_verified)
    cand_gh_uid = norm_attr(candidate_github_user_id)
    cand_ip = norm_attr(candidate_ip_hash)

    a_gh_verified = bool(author_github_verified)
    a_gh_uid = norm_attr(author_github_user_id)
    a_ip = norm_attr(author_ip_hash)

    if cand_handle and cand_handle == norm_attr(author_handle):
        return (
            f"Self-verification prohibited: agent '{cand_handle_raw}' cannot verify "
            f"its own finding '{finding_id}'."
        )
    if cand_gh_uid and a_gh_uid and cand_gh_uid == a_gh_uid:
        return (
            f"Self-verification prohibited: agent '{cand_handle_raw}' shares a verified GitHub "
            f"account with the author of finding '{finding_id}'."
        )
    if _same_ip_sybil_conflict(
        cand_verified=cand_gh_verified,
        cand_gh_uid=cand_gh_uid,
        cand_ip_hash=cand_ip,
        other_verified=a_gh_verified,
        other_gh_uid=a_gh_uid,
        other_ip_hash=a_ip,
    ):
        return (
            f"Sybil independence guard: unverified accounts from the same network cannot peer-verify "
            f"finding '{finding_id}'. Run `aiwatchdogs verify-github` to prove distinct GitHub identities."
        )

    used_harnesses: set[str] = set()
    used_models: set[str] = set()

    a_harness = norm_attr(author_harness)
    a_model = norm_attr(author_model)
    if a_harness:
        used_harnesses.add(a_harness)
    if a_model:
        used_models.add(a_model)

    for entry in existing_participants:
        source_label = entry[0]
        p_handle = entry[1]
        p_harness = entry[2]
        p_model = entry[3]
        p_gh_verified = bool(entry[4]) if len(entry) > 4 else False
        p_gh_uid = norm_attr(entry[5]) if len(entry) > 5 else ""
        p_ip = norm_attr(entry[6]) if len(entry) > 6 else ""

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
        if cand_gh_uid and p_gh_uid and cand_gh_uid == p_gh_uid:
            return (
                f"Duplicate verification prohibited: agent '{cand_handle_raw}' shares a verified "
                f"GitHub account with another reviewer on finding '{finding_id}'."
            )
        if _same_ip_sybil_conflict(
            cand_verified=cand_gh_verified,
            cand_gh_uid=cand_gh_uid,
            cand_ip_hash=cand_ip,
            other_verified=p_gh_verified,
            other_gh_uid=p_gh_uid,
            other_ip_hash=p_ip,
        ):
            return (
                f"Sybil independence guard: unverified accounts from the same network cannot both "
                f"peer-verify finding '{finding_id}'. Run `aiwatchdogs verify-github` to prove distinct GitHub identities."
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
    author_github_verified: bool = False,
    author_github_user_id: Any = None,
    author_ip_hash: Any = None,
    require_github_verified: bool = False,
) -> dict[str, Any]:
    """Compute pairwise-independent quorum counts over ``peer_verifications`` ordered ``created_at ASC, id ASC``.

    A ``confirm`` vote counts toward ``qualified_confirms`` only when:
    1. All 4 canonical checks are ``'pass'`` and ``reproduced_metrics`` is a non-empty dict.
    2. Its handle is distinct from the author and every previously counted confirm.
    3. Whenever the author or any previously counted confirm declared ``coding_agent``, this
       vote has a non-empty ``coding_agent`` distinct from all of them.
    4. Whenever the author or any previously counted confirm declared ``coding_agent_model``, this
       vote has a non-empty ``coding_agent_model`` distinct from all of them.
    5. Its verified ``github_user_id`` (if present) is distinct, and it does not share a
       registration IP hash with the author or an already-counted confirm unless both hold
       distinct verified GitHub accounts.

    ``consensus`` is computed over ``effective_votes = qualified_confirms + dispute_count`` so that
    legacy non-independent confirms retained in the audit trail never permanently poison consensus
    below 0.75 once 3 pairwise-independent confirmations arrive.
    """
    author_h_raw = str(author_handle or "").strip()
    author_h = norm_attr(author_h_raw)
    author_har = norm_attr(author_harness)
    author_mod = norm_attr(author_model)
    author_gh_verified = bool(author_github_verified)
    author_gh_uid = norm_attr(author_github_user_id)
    author_ip = norm_attr(author_ip_hash)

    seen_handles: set[str] = {author_h} if author_h else set()
    seen_dispute_handles: set[str] = {author_h} if author_h else set()
    seen_harnesses: set[str] = {author_har} if author_har else set()
    seen_models: set[str] = {author_mod} if author_mod else set()
    seen_identities: list[tuple[bool, str, str]] = [(author_gh_verified, author_gh_uid, author_ip)]

    qualified_confirms = 0
    dispute_count = 0
    has_pii_or_legal_flag = False
    raw_vote_count = 0
    counted_verdicts: list[str] = []
    voter_handles: set[str] = {author_h_raw} if author_h_raw else set()

    for v in votes:
        if is_unsupported_engine_vote(v):
            continue
        raw_vote_count += 1
        raw_handle = str(_get(v, "contributor_id") or _get(v, "verifier_handle") or "").strip()
        if raw_handle:
            voter_handles.add(raw_handle)
        v_handle = norm_attr(raw_handle)
        v_harness = norm_attr(_get(v, "coding_agent"))
        v_model = norm_attr(_get(v, "coding_agent_model"))
        v_verdict = str(_get(v, "verdict") or "").strip().lower()
        v_gh_verified = bool(int(_get(v, "github_verified", 0) or 0))
        v_gh_uid = norm_attr(_get(v, "github_user_id"))
        v_ip = norm_attr(_get(v, "registered_ip_hash"))

        if v_verdict == "flag_pii_or_legal":
            has_pii_or_legal_flag = True
            dispute_count += 1
            counted_verdicts.append(v_verdict)
        elif v_verdict == "confirm":
            if require_github_verified and not v_gh_verified:
                continue
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
            identity_conflict = False
            for o_verified, o_uid, o_ip in seen_identities:
                if v_gh_uid and o_uid and v_gh_uid == o_uid:
                    identity_conflict = True
                    break
                if _same_ip_sybil_conflict(
                    cand_verified=v_gh_verified,
                    cand_gh_uid=v_gh_uid,
                    cand_ip_hash=v_ip,
                    other_verified=o_verified,
                    other_gh_uid=o_uid,
                    other_ip_hash=o_ip,
                ):
                    identity_conflict = True
                    break
            if identity_conflict:
                continue
            qualified_confirms += 1
            seen_handles.add(v_handle)
            if v_harness:
                seen_harnesses.add(v_harness)
            if v_model:
                seen_models.add(v_model)
            seen_identities.append((v_gh_verified, v_gh_uid, v_ip))
            counted_verdicts.append(v_verdict)
        else:
            if v_handle and v_handle in seen_dispute_handles:
                continue
            if v_handle:
                seen_dispute_handles.add(v_handle)
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


def compute_project_progress(
    *,
    total_tasks: int,
    completed_tasks: int,
    review_slots_required: int = 0,
    review_slots_done: int = 0,
    in_review_findings: int = 0,
    peer_checks_total: int = 0,
    peer_checks_done: int = 0,
    peer_checks_open: int = 0,
    required_reviews: int = DEFAULT_REQUIRED_QUORUM,
) -> dict[str, Any]:
    """Compute weighted project completion (70% primary tasks + 30% independent reviews).

    - Completing all primary tasks accounts for 70% of progress when findings require
      independent peer reviews, leaving 30% for the required independent reviews
      (30 / N percent per review: e.g. 10% per review for 3 independent reviews,
      6% per review for 5 independent reviews).
    - Progress never reaches 100.0% while any primary task or independent review remains.
    - When no findings require peer review and no reviews remain (e.g. website checks or
      projects where all tasks cleared with zero findings), task completion maps cleanly
      from 0% to 100%.
    """
    safe_total = max(0, int(total_tasks or 0))
    safe_completed = max(0, min(safe_total, int(completed_tasks or 0)))
    eff_required_reviews = max(1, int(required_reviews or DEFAULT_REQUIRED_QUORUM))
    per_review_pct = round(REVIEW_PROGRESS_WEIGHT / eff_required_reviews, 2)

    if safe_total <= 0:
        return {
            "progress_pct": 0.0,
            "pct_complete": 0.0,
            "task_pct_complete": 0.0,
            "task_progress_pct": 0.0,
            "review_progress_pct": 0.0,
            "review_remaining_pct": 0.0,
            "task_weight_pct": TASK_PROGRESS_WEIGHT,
            "review_weight_pct": REVIEW_PROGRESS_WEIGHT,
            "required_reviews": eff_required_reviews,
            "per_review_pct": per_review_pct,
            "reviews_required": 0,
            "reviews_done": 0,
            "reviews_remaining": 0,
            "has_reviews_remaining": False,
        }

    task_ratio = safe_completed / safe_total
    raw_task_only_pct = task_ratio * 100.0

    eff_slots_required = max(0, int(review_slots_required or 0))
    eff_slots_done = max(0, int(review_slots_done or 0))
    in_review_cnt = max(0, int(in_review_findings or 0))
    open_pv = max(0, int(peer_checks_open or 0))
    total_pv = max(0, int(peer_checks_total or 0))
    done_pv = max(0, int(peer_checks_done or 0))

    if eff_slots_required == 0 and total_pv > 0:
        eff_slots_required = total_pv
        eff_slots_done = min(eff_slots_required, done_pv)
        if open_pv > 0 and eff_slots_done >= eff_slots_required:
            eff_slots_done = max(0, eff_slots_required - 1)
    elif eff_slots_required == 0 and in_review_cnt > 0:
        eff_slots_required = in_review_cnt * eff_required_reviews
        eff_slots_done = 0

    eff_slots_done = min(eff_slots_done, eff_slots_required)
    eff_slots_remaining = max(0, eff_slots_required - eff_slots_done)

    has_reviews_remaining = eff_slots_remaining > 0 or in_review_cnt > 0 or open_pv > 0
    if has_reviews_remaining and eff_slots_remaining == 0 and eff_slots_required > 0:
        eff_slots_done = max(0, eff_slots_required - 1)
        eff_slots_remaining = 1

    has_review_phase = eff_slots_required > 0 or has_reviews_remaining
    if eff_slots_required > 0:
        review_ratio = eff_slots_done / eff_slots_required
    elif has_reviews_remaining:
        review_ratio = 0.0
    else:
        review_ratio = 1.0

    if has_review_phase:
        raw_task_pct = task_ratio * TASK_PROGRESS_WEIGHT
        raw_review_pct = task_ratio * REVIEW_PROGRESS_WEIGHT * review_ratio
        raw_review_remaining_pct = task_ratio * REVIEW_PROGRESS_WEIGHT * (1.0 - review_ratio)
    else:
        raw_task_pct = raw_task_only_pct
        raw_review_pct = 0.0
        raw_review_remaining_pct = 0.0

    raw_total_pct = raw_task_pct + raw_review_pct
    has_incomplete_work = (safe_completed < safe_total) or has_reviews_remaining

    if has_incomplete_work:
        progress_pct = min(round(raw_total_pct, 1), 99.9)
        pct_complete = min(round(raw_total_pct, 2), 99.9)
        task_progress_pct = round(raw_task_pct, 2)
        review_progress_pct = max(0.0, round(pct_complete - task_progress_pct, 2)) if has_review_phase else 0.0
        if has_reviews_remaining and safe_completed > 0:
            if safe_completed >= safe_total:
                review_remaining_pct = max(0.1, round(100.0 - pct_complete, 2))
            else:
                review_remaining_pct = max(0.01, round(raw_review_remaining_pct, 2))
        else:
            review_remaining_pct = 0.0
    else:
        progress_pct = 100.0
        pct_complete = 100.0
        task_progress_pct = TASK_PROGRESS_WEIGHT if has_review_phase else 100.0
        review_progress_pct = REVIEW_PROGRESS_WEIGHT if has_review_phase else 0.0
        review_remaining_pct = 0.0

    return {
        "progress_pct": progress_pct,
        "pct_complete": pct_complete,
        "task_pct_complete": round(raw_task_only_pct, 2),
        "task_progress_pct": task_progress_pct,
        "review_progress_pct": review_progress_pct,
        "review_remaining_pct": review_remaining_pct,
        "task_weight_pct": TASK_PROGRESS_WEIGHT,
        "review_weight_pct": REVIEW_PROGRESS_WEIGHT,
        "required_reviews": eff_required_reviews,
        "per_review_pct": per_review_pct,
        "reviews_required": eff_slots_required,
        "reviews_done": eff_slots_done,
        "reviews_remaining": eff_slots_remaining,
        "has_reviews_remaining": has_reviews_remaining,
    }
