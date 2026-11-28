#!/usr/bin/env python3
"""Reconcile historical peer-verification votes cast by stale CLIs lacking an engine.

Identifies `peer_verifications` rows whose `notes` match `Unknown methodology_code` (or
equivalent missing-engine signatures, as documented in Issues #56, #57, and #69), previews
all affected votes, findings, and contributor points in `--dry-run` mode (the default),
and — when invoked with `--apply` — removes the invalid votes, deducts the unearned
15-point verification awards, recomputes finding consensus using canonical
`evaluate_pairwise_quorum`, and re-queues missing peer-verification slots where needed.

Usage:
    uv run python scripts/reconcile_unsupported_votes.py --db ./data/hub.db --dry-run
    uv run python scripts/reconcile_unsupported_votes.py --db ./data/hub.db --apply
    uv run python scripts/reconcile_unsupported_votes.py --postgres-url "$DATABASE_URL" --dry-run
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiwatchdogs.hub.database import Database, dumps_json, loads_json
from aiwatchdogs.hub.quorum import (
    DEFAULT_REQUIRED_QUORUM,
    evaluate_pairwise_quorum,
    is_unsupported_engine_vote,
)

VERIFY_POINTS_PER_VOTE = 15


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def reconcile_unsupported_votes(
    db: Database,
    *,
    apply: bool = False,
    handle_filter: str | None = None,
    finding_id_filter: str | None = None,
) -> dict[str, Any]:
    """Scan and optionally repair unsupported-engine peer-verification votes."""
    clean_handle = (handle_filter or "").strip().lstrip("@").lower() or None
    clean_fid = (finding_id_filter or "").strip() or None

    with db.transaction() as conn:
        clauses = ["1=1"]
        params: list[Any] = []
        if clean_handle:
            clauses.append("(LOWER(TRIM(COALESCE(pv.contributor_id, ''))) = ? OR LOWER(TRIM(COALESCE(pv.verifier_handle, ''))) = ?)")
            params.extend([clean_handle, clean_handle])
        if clean_fid:
            clauses.append("pv.finding_id = ?")
            params.append(clean_fid)

        rows = conn.execute(
            f"""
            SELECT pv.id, pv.finding_id, pv.verifier_node_id, pv.contributor_id, pv.verifier_handle,
                   pv.verdict, pv.checks_json, pv.reproduced_metrics_json, pv.notes, pv.created_at
            FROM peer_verifications pv
            WHERE {' AND '.join(clauses)}
            ORDER BY pv.created_at ASC, pv.id ASC
            """,
            params,
        ).fetchall()

        bad_votes: list[dict[str, Any]] = []
        bad_ids: set[str] = set()
        affected_findings: set[str] = set()
        points_by_handle: dict[str, int] = defaultdict(int)
        votes_by_handle: dict[str, int] = defaultdict(int)

        for r in rows:
            if not is_unsupported_engine_vote(r):
                continue
            vid = str(r["id"])
            fid = str(r["finding_id"])
            hdl = str(r["contributor_id"] or r["verifier_handle"] or "").strip().lower()
            bad_ids.add(vid)
            affected_findings.add(fid)
            if hdl:
                votes_by_handle[hdl] += 1
                points_by_handle[hdl] += VERIFY_POINTS_PER_VOTE
            bad_votes.append(
                {
                    "vote_id": vid,
                    "finding_id": fid,
                    "handle": hdl,
                    "verdict": str(r["verdict"] or ""),
                    "notes": str(r["notes"] or "")[:240],
                    "created_at": str(r["created_at"] or ""),
                }
            )

        finding_transitions: list[dict[str, Any]] = []
        requeued_tasks = 0
        now = _utc_now_iso()

        for fid in sorted(affected_findings):
            frow = conn.execute(
                """
                SELECT f.id, f.mission_id, f.mission_slug, f.methodology_code, f.title,
                       f.status, f.editorial_status, f.quarantined, f.required_quorum, f.verified_at,
                       f.submitted_by, f.submitted_by_agent_id,
                       f.submitted_coding_agent, f.submitted_coding_agent_model,
                       fa.github_verified AS author_gh_verified,
                       fa.github_user_id AS author_gh_uid,
                       fa.registered_ip_hash AS author_ip_hash
                FROM findings f
                LEFT JOIN agents fa ON fa.id = f.submitted_by_agent_id
                WHERE f.id = ?
                """,
                (fid,),
            ).fetchone()
            if frow is None:
                continue

            old_status = str(frow["status"] or "")
            if bool(frow["quarantined"]) or old_status == "quarantined":
                finding_transitions.append(
                    {"finding_id": fid, "old_status": old_status, "new_status": old_status, "skipped": "quarantined"}
                )
                continue

            all_votes = conn.execute(
                """
                SELECT pv.id, pv.contributor_id, pv.verifier_handle, pv.verdict, pv.checks_json,
                       pv.reproduced_metrics_json, pv.confidence, pv.coding_agent, pv.coding_agent_model,
                       pv.notes, pv.created_at,
                       pva.github_verified, pva.github_user_id, pva.registered_ip_hash
                FROM peer_verifications pv
                LEFT JOIN agents pva ON pva.id = pv.verifier_node_id
                WHERE pv.finding_id = ?
                ORDER BY pv.created_at ASC, pv.id ASC
                """,
                (fid,),
            ).fetchall()

            surviving = [v for v in all_votes if str(v["id"]) not in bad_ids]
            required = max(1, int(_row_get(frow, "required_quorum", DEFAULT_REQUIRED_QUORUM) or DEFAULT_REQUIRED_QUORUM))

            qres = evaluate_pairwise_quorum(
                author_handle=frow["submitted_by"],
                author_harness=_row_get(frow, "submitted_coding_agent", ""),
                author_model=_row_get(frow, "submitted_coding_agent_model", ""),
                votes=surviving,
                loads_json_fn=loads_json,
                author_github_verified=bool(int(_row_get(frow, "author_gh_verified", 0) or 0)),
                author_github_user_id=_row_get(frow, "author_gh_uid", ""),
                author_ip_hash=_row_get(frow, "author_ip_hash", ""),
                require_github_verified=False,
            )

            qc = int(qres["qualified_confirms"])
            dc = int(qres["dispute_count"])
            cons = float(qres["consensus"])
            if qres["has_pii_or_legal_flag"]:
                new_status, new_editorial = "quarantined", "quarantined"
            elif qc >= required and cons >= 0.75:
                new_status, new_editorial = "verified", "none"
            elif dc >= 2 and cons < 0.5:
                new_status = "retracted_false_positive" if qres["all_fp"] else "disputed"
                new_editorial = "none"
            else:
                new_status, new_editorial = "peer_review", "none"

            open_pv_row = conn.execute(
                "SELECT COUNT(*) AS c FROM tasks WHERE target_finding_id = ? AND task_type = 'peer_verify' AND status IN ('queued', 'claimed')",
                (fid,),
            ).fetchone()
            open_pv = int(open_pv_row["c"] or 0) if open_pv_row else 0
            needed_slots = max(0, (required - qc) - open_pv) if new_status == "peer_review" else 0

            finding_transitions.append(
                {
                    "finding_id": fid,
                    "old_status": old_status,
                    "new_status": new_status,
                    "surviving_confirms": qc,
                    "surviving_disputes": dc,
                    "consensus": cons,
                    "requeue_slots": needed_slots,
                }
            )

            if apply:
                removed_for_f = [str(v["id"]) for v in all_votes if str(v["id"]) in bad_ids]
                if removed_for_f:
                    ph = ",".join("?" for _ in removed_for_f)
                    conn.execute(f"DELETE FROM peer_verifications WHERE id IN ({ph})", tuple(removed_for_f))
                new_verified_at = now if (new_status == "verified" and not frow["verified_at"]) else (frow["verified_at"] if new_status == "verified" else None)
                conn.execute(
                    """
                    UPDATE findings
                    SET confirm_count = ?, dispute_count = ?, verification_score = ?,
                        status = ?, editorial_status = ?, verified_at = ?
                    WHERE id = ?
                    """,
                    (qc, dc, cons, new_status, new_editorial, new_verified_at, fid),
                )
                if needed_slots > 0:
                    sample_pv = conn.execute(
                        "SELECT payload_json, dataset_key, financial_quantity_type FROM tasks WHERE target_finding_id = ? AND task_type = 'peer_verify' ORDER BY created_at DESC LIMIT 1",
                        (fid,),
                    ).fetchone()
                    pv_payload_str = (
                        str(sample_pv["payload_json"])
                        if sample_pv is not None and sample_pv["payload_json"]
                        else dumps_json({"finding_id": fid, "methodology_code": frow["methodology_code"]})
                    )
                    sample_dkey = str(_row_get(sample_pv, "dataset_key", "") or "")
                    sample_fqt = str(_row_get(sample_pv, "financial_quantity_type", "") or "")
                    excluded_handles = sorted(qres["voter_handles"])
                    for slot in range(needed_slots):
                        tid = f"tsk-reconcile-{fid}-{secrets.token_hex(4)}"
                        conn.execute(
                            """
                            INSERT INTO tasks (
                                id, mission_id, mission_slug, methodology_code, task_type,
                                title, instructions, payload_json, target_finding_id,
                                excluded_handles_json, status, dataset_key, financial_quantity_type,
                                created_at, updated_at
                            ) VALUES (?, ?, ?, ?, 'peer_verify', ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)
                            """,
                            (
                                tid,
                                frow["mission_id"],
                                frow["mission_slug"],
                                frow["methodology_code"],
                                f"Peer Verify (reconciled {slot + 1}/{needed_slots}): {str(frow['title'] or '')[:84]}",
                                "Re-queued after voiding unsupported-engine peer verification vote.",
                                pv_payload_str,
                                fid,
                                dumps_json(excluded_handles),
                                sample_dkey,
                                sample_fqt,
                                now,
                                now,
                            ),
                        )
                        requeued_tasks += 1

        if apply:
            for hdl, pts in points_by_handle.items():
                vcount = votes_by_handle[hdl]
                conn.execute(
                    """
                    UPDATE agents
                    SET compute_score = CASE WHEN compute_score >= ? THEN compute_score - ? ELSE 0 END,
                        verifications_done = CASE WHEN verifications_done >= ? THEN verifications_done - ? ELSE 0 END,
                        tasks_completed = CASE WHEN tasks_completed >= ? THEN tasks_completed - ? ELSE 0 END
                    WHERE LOWER(TRIM(handle)) = ?
                    """,
                    (pts, pts, vcount, vcount, vcount, vcount, hdl),
                )

    return {
        "mode": "apply" if apply else "dry_run",
        "bad_votes_count": len(bad_votes),
        "affected_findings_count": len(affected_findings),
        "requeued_peer_verify_tasks": requeued_tasks,
        "points_adjustment_by_handle": dict(sorted(points_by_handle.items())),
        "votes_removed_by_handle": dict(sorted(votes_by_handle.items())),
        "finding_transitions": finding_transitions,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reconcile unsupported-engine peer-verification votes.")
    parser.add_argument("--db", type=Path, default=None, help="Path to SQLite hub DB.")
    parser.add_argument("--postgres-url", type=str, default=None, help="PostgreSQL connection URL.")
    parser.add_argument("--handle", type=str, default=None, help="Optional single contributor handle filter.")
    parser.add_argument("--finding-id", type=str, default=None, help="Optional single finding ID filter.")
    parser.add_argument("--apply", action="store_true", help="Apply reconciliation (default is dry-run).")
    args = parser.parse_args(argv)

    if not args.db and not args.postgres_url:
        parser.error("Provide --db <sqlite_path> or --postgres-url <url>.")

    db = Database(db_path=args.db, database_url=args.postgres_url)
    try:
        report = reconcile_unsupported_votes(
            db,
            apply=args.apply,
            handle_filter=args.handle,
            finding_id_filter=args.finding_id,
        )
    finally:
        db.close()

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
