"""Desk coordination logic, quorum verification, neutrality enforcement, and read queries."""

from __future__ import annotations

import csv
import io
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, status

from aiwatchdogs.core.rti import generate_rti_draft
from aiwatchdogs.core.safety import compute_sha256, lint_neutrality, scrub_pii, scrub_pii_in_obj
from aiwatchdogs.hub.database import Database, dumps_json, loads_json, utc_now_iso
from aiwatchdogs.hub.seed import INDEPENDENCE_PLEDGE
from aiwatchdogs.hub.sse import SSEBroker

HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")
LEASE_MINUTES = 45
VALID_VERDICTS = frozenset({"confirm", "dispute", "reject_false_positive", "flag_pii_or_legal"})


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _future_iso(minutes: int = LEASE_MINUTES) -> str:
    dt = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _inr_to_cr(amount: float | int | None) -> float:
    return round(float(amount or 0.0) / 10_000_000.0, 2)


class DeskService:
    """Implements Coordinator Hub read-plane queries and Desk write-plane coordination."""

    def __init__(self, db: Database, broker: SSEBroker) -> None:
        self.db = db
        self.broker = broker

    # ------------------------------------------------------------------
    # Row serializers
    # ------------------------------------------------------------------

    @staticmethod
    def _serialize_source(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "slug": row["slug"],
            "name": row["name"],
            "pillar": row["pillar"],
            "authority": row["authority"],
            "base_url": row["base_url"],
            "ingestion_tier": row["ingestion_tier"],
            "connector_type": row["connector_type"],
            "data_contents": row["data_contents"],
            "format_access": row["format_access"],
            "config": loads_json(row["config_json"], {}),
            "robots_policy": row["robots_policy"],
            "last_synced_at": row["last_synced_at"],
        }

    @staticmethod
    def _serialize_mission(
        row: sqlite3.Row,
        queued_tasks: int = 0,
        peer_review_count: int = 0,
    ) -> dict[str, Any]:
        total = int(row["total_tasks"] or 0)
        completed = int(row["completed_tasks"] or 0)
        progress_pct = round((completed / total) * 100.0, 1) if total > 0 else 0.0
        monetary = float(row["monetary_audited_inr"] or 0.0)
        return {
            "id": row["id"],
            "slug": row["slug"],
            "title": row["title"],
            "description": row["description"],
            "pillar": row["pillar"],
            "methodology_code": row["methodology_code"],
            "jurisdiction": row["jurisdiction"],
            "ministry_or_authority": row["ministry_or_authority"],
            "statutory_anchors": loads_json(row["statutory_anchors_json"], []),
            "source_ids": loads_json(row["source_ids_json"], []),
            "sample_dataset_path": row["sample_dataset_path"],
            "data_mode": row["data_mode"],
            "is_demo_sample": row["data_mode"] == "demo_sample",
            "status": row["status"],
            "priority": row["priority"],
            "total_tasks": total,
            "completed_tasks": completed,
            "queued_tasks": queued_tasks,
            "progress_pct": progress_pct,
            "verified_findings_count": int(row["verified_findings_count"] or 0),
            "peer_review_count": peer_review_count,
            "cleared_count": int(row["cleared_count"] or 0),
            "monetary_audited_inr": monetary,
            "monetary_audited_inr_cr": _inr_to_cr(monetary),
            "created_at": row["created_at"],
        }

    @staticmethod
    def _serialize_agent(row: sqlite3.Row, window_score: int | None = None) -> dict[str, Any]:
        last_hb = _parse_iso(row["last_heartbeat_at"])
        now = datetime.now(timezone.utc)
        age_sec = int((now - last_hb).total_seconds()) if last_hb else 999999
        is_online = age_sec <= 3600
        return {
            "id": row["id"],
            "agent_id": row["id"],
            "handle": row["handle"],
            "display_name": row["display_name"],
            "provider": row["provider"],
            "model": row["model"],
            "mode": row["mode"],
            "region": row["region"],
            "capabilities": loads_json(row["capabilities_json"], []),
            "status": "active" if is_online else "idle",
            "is_active": is_online,
            "heartbeat_age_seconds": max(0, age_sec),
            "tasks_completed": int(row["tasks_completed"] or 0),
            "findings_submitted": int(row["findings_submitted"] or 0),
            "findings_verified": int(row["findings_verified"] or 0),
            "verifications_done": int(row["verifications_done"] or 0),
            "cleared_submitted": int(row["cleared_submitted"] or 0),
            "compute_score": int(row["compute_score"] or 0),
            "score": int(window_score if window_score is not None else (row["compute_score"] or 0)),
            "window_score": int(window_score if window_score is not None else (row["compute_score"] or 0)),
            "hourly_score": int(row["hourly_score"] or 0),
            "current_task_id": row["current_task_id"],
            "joined_at": row["joined_at"],
            "last_heartbeat_at": row["last_heartbeat_at"],
            "profile_url": f"/a/{row['handle']}",
        }

    @staticmethod
    def _serialize_finding(row: sqlite3.Row, has_rti_draft: bool = False) -> dict[str, Any]:
        exposure = float(row["monetary_exposure_inr"] or 0.0)
        return {
            "id": row["id"],
            "mission_id": row["mission_id"],
            "mission_slug": row["mission_slug"],
            "pillar": row["pillar"],
            "task_id": row["task_id"],
            "artifact_id": row["artifact_id"],
            "title": row["title"],
            "summary": row["summary"],
            "methodology_code": row["methodology_code"],
            "entity_name": row["entity_name"],
            "jurisdiction": row["jurisdiction"],
            "fiscal_year": row["fiscal_year"],
            "monetary_exposure_inr": exposure,
            "monetary_exposure_inr_cr": _inr_to_cr(exposure),
            "severity": row["severity"],
            "anomaly_metrics": loads_json(row["anomaly_metrics_json"], {}),
            "rule_triggered": row["rule_triggered"],
            "source_url": row["source_url"],
            "artifact_sha256": row["artifact_sha256"],
            "dataset_path": row["dataset_path"],
            "is_demo_sample": bool(row["is_demo_sample"]),
            "citations": loads_json(row["citations_json"], []),
            "benign_explanations": loads_json(row["benign_explanations_json"], []),
            "suggested_authority": row["suggested_authority"],
            "submitted_by": row["submitted_by"],
            "agent_trace": loads_json(row["agent_trace_json"], {}),
            "legal_lint_passed": bool(row["legal_lint_passed"]),
            "pii_scrubbed": bool(row["pii_scrubbed"]),
            "status": row["status"],
            "verification_score": float(row["verification_score"] or 0.0),
            "confirm_count": int(row["confirm_count"] or 0),
            "dispute_count": int(row["dispute_count"] or 0),
            "required_quorum": int(row["required_quorum"] or 2),
            "has_rti_draft": has_rti_draft,
            "created_at": row["created_at"],
            "verified_at": row["verified_at"],
        }

    @staticmethod
    def _serialize_verification(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "finding_id": row["finding_id"],
            "verifier_node_id": row["verifier_node_id"],
            "contributor_id": row["contributor_id"],
            "verifier_handle": row["verifier_handle"],
            "provider": row["provider"],
            "model_id": row["model_id"],
            "verdict": row["verdict"],
            "checks": loads_json(row["checks_json"], {}),
            "reproduced_metrics": loads_json(row["reproduced_metrics_json"], {}),
            "confidence": float(row["confidence"] or 0.0),
            "notes": row["notes"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _serialize_rti(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "finding_id": row["finding_id"],
            "public_authority": row["public_authority"],
            "cpio_designation": row["cpio_designation"],
            "rti_portal_url": row["rti_portal_url"],
            "statutory_sections": loads_json(row["statutory_sections_json"], []),
            "subject": row["subject"],
            "body_text": row["body_text"],
            "word_count": int(row["word_count"]),
            "requested_records": loads_json(row["requested_records_json"], []),
            "status": row["status"],
            "filed_date": row["filed_date"],
            "due_date_30d": row["due_date_30d"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _serialize_whisper(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "agent_handle": row["agent_handle"],
            "provider": row["provider"],
            "model": row["model"],
            "mission_slug": row["mission_slug"],
            "finding_id": row["finding_id"],
            "event_type": row["event_type"],
            "message": row["message"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _serialize_sponsor(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "handle_or_org": row["handle_or_org"],
            "tier": row["tier"],
            "amount_inr": float(row["amount_inr"] or 0.0),
            "compute_credits_desc": row["compute_credits_desc"],
            "url": row["url"],
            "message": row["message"],
            "featured": bool(row["featured"]),
            "is_demo_entry": bool(row["is_demo_entry"]),
            "created_at": row["created_at"],
        }

    @staticmethod
    def _serialize_ledger(row: sqlite3.Row) -> dict[str, Any]:
        amt = float(row["amount_inr"] or 0.0)
        direction = row["direction"]
        return {
            "id": row["id"],
            "entry_date": row["entry_date"],
            "entry_type": row["entry_type"],
            "direction": direction,
            "category": row["category"],
            "counterparty": row["counterparty"],
            "description": row["description"],
            "amount_inr": amt,
            "signed_amount_inr": amt if direction == "inflow" else -amt,
            "compute_credit_units": row["compute_credit_units"],
            "receipt_ref": row["receipt_ref"],
            "is_demo_entry": bool(row["is_demo_entry"]),
            "created_at": row["created_at"],
        }

    def _insert_whisper(
        self,
        conn: sqlite3.Connection,
        *,
        agent_handle: str,
        provider: str,
        model: str,
        event_type: str,
        message: str,
        mission_slug: str | None = None,
        finding_id: str | None = None,
    ) -> dict[str, Any]:
        cleaned_msg, _ = scrub_pii(message)
        wid = f"wsp-{secrets.token_hex(6)}"
        created = utc_now_iso()
        conn.execute(
            """
            INSERT INTO whispers (
                id, agent_handle, provider, model, mission_slug,
                finding_id, event_type, message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (wid, agent_handle, provider, model, mission_slug, finding_id, event_type, cleaned_msg, created),
        )
        return {
            "id": wid,
            "agent_handle": agent_handle,
            "provider": provider,
            "model": model,
            "mission_slug": mission_slug,
            "finding_id": finding_id,
            "event_type": event_type,
            "message": cleaned_msg,
            "created_at": created,
        }

    def _reclaim_expired_leases(self, conn: sqlite3.Connection) -> int:
        now = utc_now_iso()
        expired = conn.execute(
            "SELECT id, claimed_by_agent_id FROM tasks WHERE status = 'claimed' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?",
            (now,),
        ).fetchall()
        for row in expired:
            conn.execute(
                """
                UPDATE tasks
                SET status = 'queued', claimed_by_agent_id = NULL, claimed_by_handle = NULL,
                    lease_id = NULL, lease_expires_at = NULL, progress_pct = 0, updated_at = ?
                WHERE id = ?
                """,
                (now, row["id"]),
            )
            if row["claimed_by_agent_id"]:
                conn.execute(
                    "UPDATE agents SET current_task_id = NULL WHERE id = ? AND current_task_id = ?",
                    (row["claimed_by_agent_id"], row["id"]),
                )
        return len(expired)

    def _authenticate_agent(
        self,
        conn: sqlite3.Connection,
        token: str | None,
        required: bool = True,
    ) -> sqlite3.Row | None:
        if not token:
            if required:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Missing X-Agent-Token header",
                )
            return None
        token_hash = compute_sha256(token.strip())
        agent = conn.execute("SELECT * FROM agents WHERE api_token_hash = ?", (token_hash,)).fetchone()
        if agent is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid X-Agent-Token",
            )
        return agent

    # ------------------------------------------------------------------
    # Public Read Plane
    # ------------------------------------------------------------------

    def get_head(self) -> dict[str, Any]:
        with self.db.read() as conn:
            missions = self._fetch_missions(conn)
            rti_finding_ids = {
                r["finding_id"]
                for r in conn.execute("SELECT finding_id FROM rti_drafts").fetchall()
            }

            all_agents = conn.execute("SELECT * FROM agents ORDER BY compute_score DESC, joined_at DESC").fetchall()
            serialized_agents = [self._serialize_agent(r) for r in all_agents]
            active_agents = [a for a in serialized_agents if a["is_active"]]

            just_joined_rows = conn.execute(
                "SELECT handle, display_name, provider, model, joined_at FROM agents ORDER BY joined_at DESC LIMIT 6"
            ).fetchall()
            just_joined = [
                {
                    "handle": r["handle"],
                    "display_name": r["display_name"],
                    "provider": r["provider"],
                    "model": r["model"],
                    "joined_at": r["joined_at"],
                }
                for r in just_joined_rows
            ]

            recent_finding_rows = conn.execute(
                """
                SELECT * FROM findings
                ORDER BY
                    CASE status
                        WHEN 'verified' THEN 0
                        WHEN 'peer_review' THEN 1
                        WHEN 'cleared' THEN 2
                        ELSE 3
                    END,
                    COALESCE(verified_at, created_at) DESC
                LIMIT 12
                """
            ).fetchall()
            recent_findings = [
                self._serialize_finding(r, has_rti_draft=(r["id"] in rti_finding_ids))
                for r in recent_finding_rows
            ]

            whisper_rows = conn.execute(
                "SELECT * FROM whispers ORDER BY created_at DESC LIMIT 25"
            ).fetchall()
            whispers = [self._serialize_whisper(r) for r in whisper_rows]

            sponsor_rows = conn.execute(
                "SELECT * FROM sponsors WHERE featured = 1 ORDER BY amount_inr DESC LIMIT 6"
            ).fetchall()
            sponsors_featured = [self._serialize_sponsor(r) for r in sponsor_rows]

            # Aggregate stats
            spend_inr = sum(m["monetary_audited_inr"] for m in missions)
            tasks_completed = conn.execute(
                "SELECT COUNT(*) AS c FROM tasks WHERE status IN ('completed', 'settled')"
            ).fetchone()["c"]
            tasks_queued = conn.execute("SELECT COUNT(*) AS c FROM tasks WHERE status = 'queued'").fetchone()["c"]
            status_counts = {
                r["status"]: r["c"]
                for r in conn.execute("SELECT status, COUNT(*) AS c FROM findings GROUP BY status").fetchall()
            }
            verified_cnt = status_counts.get("verified", 0) + status_counts.get("rti_filed", 0)
            peer_review_cnt = status_counts.get("peer_review", 0) + status_counts.get("raw_lead", 0)
            cleared_cnt = status_counts.get("cleared", 0) + status_counts.get("resolved_explained", 0)
            disputed_cnt = status_counts.get("disputed", 0) + status_counts.get("retracted_false_positive", 0)
            rtis_drafted = len(rti_finding_ids)
            sponsors_total = float(
                conn.execute("SELECT COALESCE(SUM(amount_inr), 0) AS s FROM sponsors").fetchone()["s"] or 0.0
            )

        orchestrator_brief = (
            f"Swarm focus: {peer_review_cnt} public-record leads await independent 2-of-2 peer verification "
            f"across 8 Indian civic-audit fronts ({tasks_queued} queued tasks ready for local Ollama, BYO API, "
            "or Rs 0 deterministic CPU nodes). Every finding requires SHA-256 provenance, neutral BNS Sec 356 "
            "wording, and benign explanations; cleared records earn equal standing."
        )

        return {
            "generated_at": utc_now_iso(),
            "orchestrator_brief": orchestrator_brief,
            "independence_pledge": INDEPENDENCE_PLEDGE,
            "stats": {
                "public_spend_audited_inr_cr": _inr_to_cr(spend_inr),
                "tasks_completed": int(tasks_completed),
                "tasks_queued": int(tasks_queued),
                "agents_active": len(active_agents),
                "agents_total": len(serialized_agents),
                "findings_verified": int(verified_cnt),
                "findings_peer_review": int(peer_review_cnt),
                "findings_cleared": int(cleared_cnt),
                "rtis_drafted": int(rtis_drafted),
                "sponsors_total_inr": round(sponsors_total, 2),
            },
            "disposition": {
                "verified_leads": int(verified_cnt),
                "peer_review": int(peer_review_cnt),
                "cleared_records": int(cleared_cnt),
                "disputed": int(disputed_cnt),
            },
            "just_joined": just_joined,
            "active_agents": active_agents,
            "missions": missions,
            "recent_findings": recent_findings,
            "whispers": whispers,
            "top_contributors": serialized_agents[:10],
            "sponsors_featured": sponsors_featured,
        }

    def _fetch_missions(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        queued_map = {
            r["mission_id"]: r["c"]
            for r in conn.execute(
                "SELECT mission_id, COUNT(*) AS c FROM tasks WHERE status = 'queued' GROUP BY mission_id"
            ).fetchall()
        }
        pr_map = {
            r["mission_id"]: r["c"]
            for r in conn.execute(
                "SELECT mission_id, COUNT(*) AS c FROM findings WHERE status IN ('peer_review', 'raw_lead') GROUP BY mission_id"
            ).fetchall()
        }
        rows = conn.execute("SELECT * FROM missions ORDER BY priority ASC, id ASC").fetchall()
        return [
            self._serialize_mission(
                r,
                queued_tasks=queued_map.get(r["id"], 0),
                peer_review_count=pr_map.get(r["id"], 0),
            )
            for r in rows
        ]

    def list_missions(self) -> dict[str, Any]:
        with self.db.read() as conn:
            missions = self._fetch_missions(conn)
        return {"missions": missions, "total": len(missions)}

    def get_mission_detail(self, slug: str) -> dict[str, Any]:
        with self.db.read() as conn:
            row = conn.execute("SELECT * FROM missions WHERE slug = ? OR id = ?", (slug, slug)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail=f"Mission '{slug}' not found")
            mid = row["id"]
            queued_cnt = conn.execute(
                "SELECT COUNT(*) AS c FROM tasks WHERE mission_id = ? AND status = 'queued'", (mid,)
            ).fetchone()["c"]
            pr_cnt = conn.execute(
                "SELECT COUNT(*) AS c FROM findings WHERE mission_id = ? AND status IN ('peer_review', 'raw_lead')",
                (mid,),
            ).fetchone()["c"]
            mission = self._serialize_mission(row, queued_tasks=queued_cnt, peer_review_count=pr_cnt)

            source_ids = mission["source_ids"]
            sources: list[dict[str, Any]] = []
            if source_ids:
                placeholders = ",".join("?" for _ in source_ids)
                s_rows = conn.execute(
                    f"SELECT * FROM public_sources WHERE id IN ({placeholders})", source_ids
                ).fetchall()
                sources = [self._serialize_source(r) for r in s_rows]

            rti_ids = {r["finding_id"] for r in conn.execute("SELECT finding_id FROM rti_drafts").fetchall()}
            f_rows = conn.execute(
                "SELECT * FROM findings WHERE mission_id = ? ORDER BY COALESCE(verified_at, created_at) DESC",
                (mid,),
            ).fetchall()
            findings = [self._serialize_finding(r, has_rti_draft=(r["id"] in rti_ids)) for r in f_rows]

            t_rows = conn.execute(
                "SELECT * FROM tasks WHERE mission_id = ? ORDER BY CASE status WHEN 'queued' THEN 0 WHEN 'claimed' THEN 1 ELSE 2 END, updated_at DESC LIMIT 30",
                (mid,),
            ).fetchall()
            tasks = [
                {
                    "id": t["id"],
                    "task_type": t["task_type"],
                    "methodology_code": t["methodology_code"],
                    "title": t["title"],
                    "status": t["status"],
                    "claimed_by_handle": t["claimed_by_handle"],
                    "progress_pct": t["progress_pct"],
                    "target_finding_id": t["target_finding_id"],
                    "created_at": t["created_at"],
                    "completed_at": t["completed_at"],
                }
                for t in t_rows
            ]

        return {**mission, "sources": sources, "findings": findings, "tasks": tasks}

    def list_findings(
        self,
        *,
        status_filter: str | None = None,
        pillar: str | None = None,
        methodology: str | None = None,
        mission: str | None = None,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[Any] = []
        if status_filter and status_filter != "all":
            clauses.append("status = ?")
            params.append(status_filter)
        if pillar and pillar != "all":
            clauses.append("pillar = ?")
            params.append(pillar)
        if methodology and methodology != "all":
            clauses.append("methodology_code = ?")
            params.append(methodology.upper())
        if mission:
            clauses.append("mission_slug = ?")
            params.append(mission)
        if search and search.strip():
            like = f"%{search.strip()}%"
            clauses.append(
                "(title LIKE ? OR summary LIKE ? OR entity_name LIKE ? OR jurisdiction LIKE ? OR rule_triggered LIKE ?)"
            )
            params.extend([like, like, like, like, like])

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.db.read() as conn:
            total = conn.execute(f"SELECT COUNT(*) AS c FROM findings {where_sql}", params).fetchone()["c"]
            rti_ids = {r["finding_id"] for r in conn.execute("SELECT finding_id FROM rti_drafts").fetchall()}
            rows = conn.execute(
                f"""
                SELECT * FROM findings {where_sql}
                ORDER BY
                    CASE status
                        WHEN 'verified' THEN 0
                        WHEN 'peer_review' THEN 1
                        WHEN 'cleared' THEN 2
                        ELSE 3
                    END,
                    COALESCE(verified_at, created_at) DESC
                LIMIT ? OFFSET ?
                """,
                [*params, max(1, min(limit, 500)), max(0, offset)],
            ).fetchall()
            findings = [self._serialize_finding(r, has_rti_draft=(r["id"] in rti_ids)) for r in rows]

        return {"findings": findings, "total": int(total)}

    def get_finding_detail(self, finding_id: str) -> dict[str, Any]:
        with self.db.read() as conn:
            row = conn.execute("SELECT * FROM findings WHERE id = ?", (finding_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail=f"Finding '{finding_id}' not found")
            verif_rows = conn.execute(
                "SELECT * FROM peer_verifications WHERE finding_id = ? ORDER BY created_at ASC",
                (finding_id,),
            ).fetchall()
            rti_row = conn.execute("SELECT * FROM rti_drafts WHERE finding_id = ?", (finding_id,)).fetchone()
            art_row = (
                conn.execute("SELECT * FROM evidence_artifacts WHERE id = ?", (row["artifact_id"],)).fetchone()
                if row["artifact_id"]
                else None
            )
            mis_row = conn.execute("SELECT * FROM missions WHERE id = ?", (row["mission_id"],)).fetchone()

            finding = self._serialize_finding(row, has_rti_draft=(rti_row is not None))
            verifications = [self._serialize_verification(v) for v in verif_rows]
            rti_draft = self._serialize_rti(rti_row) if rti_row is not None else None
            artifact = (
                {
                    "id": art_row["id"],
                    "title": art_row["title"],
                    "canonical_url": art_row["canonical_url"],
                    "archive_url": art_row["archive_url"],
                    "local_path": art_row["local_path"],
                    "file_format": art_row["file_format"],
                    "sha256": art_row["sha256"],
                    "byte_size": art_row["byte_size"],
                    "is_demo_sample": bool(art_row["is_demo_sample"]),
                    "pii_scrubbed": bool(art_row["pii_scrubbed"]),
                    "fetched_at": art_row["fetched_at"],
                }
                if art_row is not None
                else None
            )
            mission_ref = (
                {"id": mis_row["id"], "slug": mis_row["slug"], "title": mis_row["title"]}
                if mis_row is not None
                else None
            )

        return {
            **finding,
            "verifications": verifications,
            "rti_draft": rti_draft,
            "artifact": artifact,
            "mission": mission_ref,
        }

    def create_or_get_rti_draft(
        self, finding_id: str, public_authority: str | None = None
    ) -> dict[str, Any]:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM findings WHERE id = ?", (finding_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail=f"Finding '{finding_id}' not found")

            existing = conn.execute("SELECT * FROM rti_drafts WHERE finding_id = ?", (finding_id,)).fetchone()
            if existing is not None and not public_authority:
                return self._serialize_rti(existing)

            finding_dict = self._serialize_finding(row)
            draft = generate_rti_draft(finding_dict, public_authority=public_authority)
            now = utc_now_iso()
            draft_id = existing["id"] if existing is not None else f"rti-{finding_id}"

            if existing is not None:
                conn.execute(
                    """
                    UPDATE rti_drafts
                    SET public_authority = ?, cpio_designation = ?, rti_portal_url = ?,
                        statutory_sections_json = ?, subject = ?, body_text = ?,
                        word_count = ?, requested_records_json = ?
                    WHERE id = ?
                    """,
                    (
                        draft["public_authority"],
                        draft["cpio_designation"],
                        draft["rti_portal_url"],
                        dumps_json(draft["statutory_sections"]),
                        draft["subject"],
                        draft["body_text"],
                        draft["word_count"],
                        dumps_json(draft["requested_records"]),
                        draft_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO rti_drafts (
                        id, finding_id, public_authority, cpio_designation,
                        rti_portal_url, statutory_sections_json, subject, body_text,
                        word_count, requested_records_json, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?)
                    """,
                    (
                        draft_id,
                        finding_id,
                        draft["public_authority"],
                        draft["cpio_designation"],
                        draft["rti_portal_url"],
                        dumps_json(draft["statutory_sections"]),
                        draft["subject"],
                        draft["body_text"],
                        draft["word_count"],
                        dumps_json(draft["requested_records"]),
                        now,
                    ),
                )
            saved = conn.execute("SELECT * FROM rti_drafts WHERE id = ?", (draft_id,)).fetchone()
            result = self._serialize_rti(saved)

        self.broker.publish(
            "rti_drafted",
            {
                "finding_id": finding_id,
                "rti_draft_id": result["id"],
                "public_authority": result["public_authority"],
                "word_count": result["word_count"],
            },
        )
        return result

    def get_graph(self) -> dict[str, Any]:
        """Build structured Observatory graph: Authorities -> Missions -> Findings -> Official Sources."""
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        seen_nodes: set[str] = set()
        seen_edges: set[tuple[str, str, str]] = set()

        def add_node(node: dict[str, Any]) -> None:
            if node["id"] not in seen_nodes:
                seen_nodes.add(node["id"])
                nodes.append(node)

        def add_edge(source: str, target: str, relation: str) -> None:
            key = (source, target, relation)
            if key not in seen_edges:
                seen_edges.add(key)
                edges.append({"source": source, "target": target, "relation": relation})

        with self.db.read() as conn:
            missions = self._fetch_missions(conn)
            sources_by_id = {
                r["id"]: self._serialize_source(r)
                for r in conn.execute("SELECT * FROM public_sources").fetchall()
            }
            finding_rows = conn.execute(
                "SELECT * FROM findings ORDER BY COALESCE(verified_at, created_at) DESC LIMIT 40"
            ).fetchall()

        authority_map: dict[str, str] = {}
        mission_by_id: dict[str, dict[str, Any]] = {}

        for m in missions:
            mission_by_id[m["id"]] = m
            auth_label = m["ministry_or_authority"]
            auth_id = authority_map.get(auth_label)
            if not auth_id:
                auth_id = f"auth-{len(authority_map) + 1:02d}"
                authority_map[auth_label] = auth_id
                add_node(
                    {
                        "id": auth_id,
                        "type": "ministry",
                        "label": auth_label,
                        "status": "active",
                        "pillar": m["pillar"],
                        "value_inr_cr": m["monetary_audited_inr_cr"],
                        "meta": {"jurisdiction": m["jurisdiction"]},
                    }
                )

            m_node_id = f"mission:{m['slug']}"
            add_node(
                {
                    "id": m_node_id,
                    "type": "mission",
                    "label": m["title"],
                    "status": m["status"],
                    "pillar": m["pillar"],
                    "value_inr_cr": m["monetary_audited_inr_cr"],
                    "meta": {
                        "slug": m["slug"],
                        "methodology_code": m["methodology_code"],
                        "progress_pct": m["progress_pct"],
                        "verified_findings_count": m["verified_findings_count"],
                    },
                }
            )
            add_edge(auth_id, m_node_id, "oversees")

            for sid in m["source_ids"]:
                src = sources_by_id.get(sid)
                if src:
                    s_node_id = f"source:{src['slug']}"
                    add_node(
                        {
                            "id": s_node_id,
                            "type": "source",
                            "label": src["name"],
                            "status": f"tier_{src['ingestion_tier']}",
                            "pillar": src["pillar"],
                            "value_inr_cr": 0.0,
                            "meta": {
                                "base_url": src["base_url"],
                                "ingestion_tier": src["ingestion_tier"],
                                "connector_type": src["connector_type"],
                            },
                        }
                    )
                    add_edge(m_node_id, s_node_id, "ingests_from")

        for f_row in finding_rows:
            f = self._serialize_finding(f_row)
            f_node_id = f"finding:{f['id']}"
            add_node(
                {
                    "id": f_node_id,
                    "type": "finding",
                    "label": f["title"],
                    "status": f["status"],
                    "pillar": f["pillar"],
                    "value_inr_cr": f["monetary_exposure_inr_cr"],
                    "meta": {
                        "finding_id": f["id"],
                        "methodology_code": f["methodology_code"],
                        "entity_name": f["entity_name"],
                        "severity": f["severity"],
                        "confirm_count": f["confirm_count"],
                        "is_demo_sample": f["is_demo_sample"],
                    },
                }
            )
            add_edge(f"mission:{f['mission_slug']}", f_node_id, "surfaced")

            # Link finding to primary mission source node
            mis = mission_by_id.get(f["mission_id"])
            if mis and mis["source_ids"]:
                primary_src = sources_by_id.get(mis["source_ids"][0])
                if primary_src:
                    add_edge(f_node_id, f"source:{primary_src['slug']}", "evidenced_by")

        return {"nodes": nodes, "edges": edges}

    def get_live(self) -> dict[str, Any]:
        with self.db.read() as conn:
            agent_rows = conn.execute(
                "SELECT * FROM agents ORDER BY last_heartbeat_at DESC, compute_score DESC"
            ).fetchall()
            agents = [self._serialize_agent(r) for r in agent_rows]

            whisper_rows = conn.execute(
                "SELECT * FROM whispers ORDER BY created_at DESC LIMIT 40"
            ).fetchall()
            whispers = [self._serialize_whisper(r) for r in whisper_rows]

            verif_rows = conn.execute(
                """
                SELECT pv.*, f.title AS finding_title, f.methodology_code, f.status AS finding_status
                FROM peer_verifications pv
                JOIN findings f ON f.id = pv.finding_id
                ORDER BY pv.created_at DESC
                LIMIT 20
                """
            ).fetchall()
            recent_verifications = [
                {
                    **self._serialize_verification(r),
                    "finding_title": r["finding_title"],
                    "methodology_code": r["methodology_code"],
                    "finding_status": r["finding_status"],
                }
                for r in verif_rows
            ]

            breakdown_rows = conn.execute(
                """
                SELECT provider, model, COUNT(*) AS agent_count,
                       COALESCE(SUM(tasks_completed), 0) AS tasks_completed,
                       COALESCE(SUM(compute_score), 0) AS compute_score
                FROM agents
                GROUP BY provider, model
                ORDER BY tasks_completed DESC, agent_count DESC
                """
            ).fetchall()
            total_tasks = sum(int(r["tasks_completed"] or 0) for r in breakdown_rows) or 1
            model_breakdown = [
                {
                    "provider": r["provider"],
                    "model": r["model"],
                    "agent_count": int(r["agent_count"]),
                    "tasks_completed": int(r["tasks_completed"]),
                    "compute_score": int(r["compute_score"]),
                    "share_pct": round((int(r["tasks_completed"]) / total_tasks) * 100.0, 1),
                }
                for r in breakdown_rows
            ]

        return {
            "agents": agents,
            "whispers": whispers,
            "recent_verifications": recent_verifications,
            "model_breakdown": model_breakdown,
            "active_count": sum(1 for a in agents if a["is_active"]),
            "total_count": len(agents),
            "sse_subscribers": self.broker.subscriber_count,
        }

    def list_contributors(self, window: str = "all") -> dict[str, Any]:
        window_norm = "hour" if window == "hour" else "all"
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        with self.db.read() as conn:
            rows = conn.execute("SELECT * FROM agents").fetchall()
            # Dynamic hourly bonus from recent tasks & verifications in last 60 minutes
            recent_verif_counts = {
                r["verifier_handle"]: r["c"]
                for r in conn.execute(
                    "SELECT verifier_handle, COUNT(*) AS c FROM peer_verifications WHERE created_at >= ? GROUP BY verifier_handle",
                    (cutoff,),
                ).fetchall()
            }
            recent_finding_counts = {
                r["submitted_by"]: r["c"]
                for r in conn.execute(
                    "SELECT submitted_by, COUNT(*) AS c FROM findings WHERE created_at >= ? GROUP BY submitted_by",
                    (cutoff,),
                ).fetchall()
            }
            contributors: list[dict[str, Any]] = []
            for r in rows:
                handle = r["handle"]
                dyn_hour = (
                    int(r["hourly_score"] or 0)
                    + recent_verif_counts.get(handle, 0) * 15
                    + recent_finding_counts.get(handle, 0) * 10
                )
                score = dyn_hour if window_norm == "hour" else int(r["compute_score"] or 0)
                contributors.append(self._serialize_agent(r, window_score=score))

        contributors.sort(key=lambda c: (c["window_score"], c["tasks_completed"]), reverse=True)
        return {"window": window_norm, "contributors": contributors, "total": len(contributors)}

    def get_contributor_profile(self, handle: str) -> dict[str, Any]:
        norm_handle = handle.strip().lower()
        with self.db.read() as conn:
            row = conn.execute("SELECT * FROM agents WHERE handle = ?", (norm_handle,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail=f"Contributor '{handle}' not found")
            agent = self._serialize_agent(row)

            rti_ids = {r["finding_id"] for r in conn.execute("SELECT finding_id FROM rti_drafts").fetchall()}
            f_rows = conn.execute(
                "SELECT * FROM findings WHERE submitted_by = ? ORDER BY created_at DESC LIMIT 25",
                (norm_handle,),
            ).fetchall()
            submitted_findings = [self._serialize_finding(r, has_rti_draft=(r["id"] in rti_ids)) for r in f_rows]

            v_rows = conn.execute(
                """
                SELECT pv.*, f.title AS finding_title, f.methodology_code, f.status AS finding_status
                FROM peer_verifications pv
                JOIN findings f ON f.id = pv.finding_id
                WHERE pv.verifier_handle = ?
                ORDER BY pv.created_at DESC LIMIT 25
                """,
                (norm_handle,),
            ).fetchall()
            verifications = [
                {
                    **self._serialize_verification(r),
                    "finding_title": r["finding_title"],
                    "methodology_code": r["methodology_code"],
                    "finding_status": r["finding_status"],
                }
                for r in v_rows
            ]

            w_rows = conn.execute(
                "SELECT * FROM whispers WHERE agent_handle = ? ORDER BY created_at DESC LIMIT 20",
                (norm_handle,),
            ).fetchall()
            recent_whispers = [self._serialize_whisper(r) for r in w_rows]

        return {
            **agent,
            "agent": agent,
            "contributor": agent,
            "submitted_findings": submitted_findings,
            "verifications": verifications,
            "recent_whispers": recent_whispers,
        }

    def list_sponsors(self) -> dict[str, Any]:
        with self.db.read() as conn:
            rows = conn.execute("SELECT * FROM sponsors ORDER BY featured DESC, amount_inr DESC, created_at DESC").fetchall()
            sponsors = [self._serialize_sponsor(r) for r in rows]

        featured = [s for s in sponsors if s["featured"]]
        institutional = [s for s in sponsors if s["tier"] in ("institutional_compute", "founding_grant", "patron")]
        citizen_backers = [s for s in sponsors if s["tier"] == "citizen_backer"]
        total_pledged = round(sum(s["amount_inr"] for s in sponsors), 2)

        return {
            "independence_pledge": INDEPENDENCE_PLEDGE,
            "total_pledged_inr": total_pledged,
            "total_inr": total_pledged,
            "sponsors": sponsors,
            "featured": featured,
            "institutional": institutional,
            "citizen_backers": citizen_backers,
        }

    def create_sponsor_pledge(self, payload: dict[str, Any]) -> dict[str, Any]:
        name, _ = scrub_pii(str(payload.get("name") or "").strip())
        if not name:
            raise HTTPException(status_code=422, detail="Sponsor name is required")
        amount_inr = float(payload.get("amount_inr") or 0.0)
        if amount_inr < 0:
            raise HTTPException(status_code=422, detail="amount_inr must be non-negative")
        credits_desc, _ = scrub_pii(str(payload.get("compute_credits_desc") or "").strip())
        if amount_inr == 0 and not credits_desc:
            raise HTTPException(status_code=422, detail="Provide either amount_inr > 0 or compute_credits_desc")

        tier = str(payload.get("tier") or ("institutional_compute" if amount_inr >= 50000 else "citizen_backer")).strip()
        if tier not in {"institutional_compute", "founding_grant", "patron", "citizen_backer"}:
            tier = "citizen_backer"
        handle_or_org, _ = scrub_pii(str(payload.get("handle_or_org") or payload.get("handle") or "").strip())
        message, _ = scrub_pii(str(payload.get("message") or payload.get("note") or "").strip())
        url = str(payload.get("url") or "").strip() or None
        featured = 1 if (amount_inr >= 50000 or tier in ("institutional_compute", "founding_grant")) else 0

        sid = f"spn-{secrets.token_hex(6)}"
        lid = f"led-{secrets.token_hex(6)}"
        now = utc_now_iso()
        today = datetime.now(timezone.utc).date().isoformat()
        rcpt = f"RCPT-LIVE-{secrets.token_hex(4).upper()}"
        entry_type = "compute_grant" if credits_desc and amount_inr == 0 else "inflow_inr"

        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO sponsors (
                    id, name, handle_or_org, tier, amount_inr, compute_credits_desc,
                    url, message, featured, is_demo_entry, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (sid, name, handle_or_org or None, tier, amount_inr, credits_desc, url, message, featured, now),
            )
            conn.execute(
                """
                INSERT INTO treasury_ledger (
                    id, entry_date, entry_type, direction, category, counterparty,
                    description, amount_inr, compute_credit_units, receipt_ref,
                    is_demo_entry, created_at
                ) VALUES (?, ?, ?, 'inflow', ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    lid,
                    today,
                    entry_type,
                    "Compute Grant" if entry_type == "compute_grant" else "Sponsor Pledge",
                    name,
                    message or f"Public open-source pledge ({tier})",
                    amount_inr,
                    credits_desc,
                    rcpt,
                    now,
                ),
            )
            sp_row = conn.execute("SELECT * FROM sponsors WHERE id = ?", (sid,)).fetchone()
            led_row = conn.execute("SELECT * FROM treasury_ledger WHERE id = ?", (lid,)).fetchone()
            sponsor = self._serialize_sponsor(sp_row)
            ledger_entry = self._serialize_ledger(led_row)

        self.broker.publish(
            "sponsor_added",
            {
                "sponsor_id": sponsor["id"],
                "name": sponsor["name"],
                "tier": sponsor["tier"],
                "amount_inr": sponsor["amount_inr"],
                "compute_credits_desc": sponsor["compute_credits_desc"],
            },
        )
        return {"sponsor": sponsor, "ledger_entry": ledger_entry}

    def get_ledger(self) -> dict[str, Any]:
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT * FROM treasury_ledger ORDER BY entry_date DESC, created_at DESC"
            ).fetchall()
            entries = [self._serialize_ledger(r) for r in rows]

        total_inflow = round(sum(e["amount_inr"] for e in entries if e["direction"] == "inflow"), 2)
        total_outflow = round(sum(e["amount_inr"] for e in entries if e["direction"] == "outflow"), 2)
        compute_grants = round(
            sum(e["amount_inr"] for e in entries if e["entry_type"] == "compute_grant"), 2
        )
        net_balance = round(total_inflow - total_outflow, 2)
        return {
            "independence_pledge": INDEPENDENCE_PLEDGE,
            "summary": {
                "total_inflow_inr": total_inflow,
                "total_outflow_inr": total_outflow,
                "net_balance_inr": net_balance,
                "balance_inr": net_balance,
                "compute_grants_inr": compute_grants,
                "entry_count": len(entries),
            },
            "entries": entries,
        }

    def export_ledger_csv(self) -> str:
        ledger = self.get_ledger()
        buf = io.StringIO()
        fieldnames = [
            "id",
            "entry_date",
            "entry_type",
            "direction",
            "category",
            "counterparty",
            "description",
            "amount_inr",
            "signed_amount_inr",
            "compute_credit_units",
            "receipt_ref",
            "is_demo_entry",
        ]
        writer = csv.DictWriter(buf, fieldnames=fieldnames)
        writer.writeheader()
        for entry in ledger["entries"]:
            writer.writerow({k: entry.get(k, "") for k in fieldnames})
        return buf.getvalue()

    def list_sources(self) -> dict[str, Any]:
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT * FROM public_sources ORDER BY ingestion_tier ASC, pillar ASC, name ASC"
            ).fetchall()
            sources = [self._serialize_source(r) for r in rows]
        return {
            "sources": sources,
            "total": len(sources),
            "tiers": {
                "1": "Direct Open API / Bulk Structured Files — zero CAPTCHA, automated polite fetch.",
                "2": "Public Dashboards / HTML MIS Reports — no login, strict robots.txt & rate caps.",
                "3": "CAPTCHA/Portal-Gated — contributor-uploaded official exports or OCDS mirrors ONLY (zero CAPTCHA bypass).",
            },
        }

    # ------------------------------------------------------------------
    # Desk Coordination Plane
    # ------------------------------------------------------------------

    def register_agent(
        self,
        payload: dict[str, Any],
        base_url: str,
        existing_token: str | None = None,
    ) -> dict[str, Any]:
        raw_handle = str(payload.get("handle") or "").strip().lower()
        if not HANDLE_RE.match(raw_handle):
            raise HTTPException(
                status_code=422,
                detail="Handle must be 2-32 lowercase characters matching ^[a-z0-9][a-z0-9_-]{1,31}$",
            )
        display_name, _ = scrub_pii(str(payload.get("display_name") or raw_handle).strip())
        provider = str(payload.get("provider") or "rule-engine").strip().lower()
        model = str(payload.get("model") or "deterministic-cpu-v1").strip()
        mode = str(payload.get("mode") or "auto").strip().lower()
        if mode not in {"auto", "investigate", "verify"}:
            mode = "auto"
        region = str(payload.get("region") or "IN").strip()
        capabilities = payload.get("capabilities") or ["deterministic_audit", "peer_verify"]
        now = utc_now_iso()
        profile_url = f"{base_url.rstrip('/')}/a/{raw_handle}"

        with self.db.transaction() as conn:
            existing = conn.execute("SELECT * FROM agents WHERE handle = ?", (raw_handle,)).fetchone()
            if existing is not None:
                if existing_token and compute_sha256(existing_token.strip()) == existing["api_token_hash"]:
                    conn.execute(
                        """
                        UPDATE agents
                        SET display_name = ?, provider = ?, model = ?, mode = ?, region = ?,
                            capabilities_json = ?, status = 'active', last_heartbeat_at = ?
                        WHERE id = ?
                        """,
                        (
                            display_name,
                            provider,
                            model,
                            mode,
                            region,
                            dumps_json(capabilities),
                            now,
                            existing["id"],
                        ),
                    )
                    return {
                        "agent_id": existing["id"],
                        "handle": raw_handle,
                        "api_token": existing_token.strip(),
                        "profile_url": profile_url,
                        "status": "reconnected",
                    }
                raise HTTPException(
                    status_code=409,
                    detail=f"Handle '{raw_handle}' is already registered; pass X-Agent-Token to reconnect or pick a different handle.",
                )

            agent_id = f"agt-{secrets.token_hex(6)}"
            api_token = f"awd_{secrets.token_urlsafe(24)}"
            token_hash = compute_sha256(api_token)
            conn.execute(
                """
                INSERT INTO agents (
                    id, handle, display_name, provider, model, mode, region,
                    capabilities_json, api_token_hash, status, joined_at, last_heartbeat_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    agent_id,
                    raw_handle,
                    display_name,
                    provider,
                    model,
                    mode,
                    region,
                    dumps_json(capabilities),
                    token_hash,
                    now,
                    now,
                ),
            )
            whisper = self._insert_whisper(
                conn,
                agent_handle=raw_handle,
                provider=provider,
                model=model,
                event_type="agent_joined",
                message=f"Joined the swarm ({provider}/{model}, mode={mode}). Ready to audit public records.",
            )

        self.broker.publish(
            "agent_joined",
            {
                "agent_id": agent_id,
                "handle": raw_handle,
                "provider": provider,
                "model": model,
                "mode": mode,
                "profile_url": profile_url,
            },
        )
        self.broker.publish("whisper", whisper)
        return {
            "agent_id": agent_id,
            "handle": raw_handle,
            "api_token": api_token,
            "profile_url": profile_url,
            "status": "active",
        }

    def claim_next_task(
        self,
        *,
        token: str | None,
        handle: str | None = None,
        mode: str | None = None,
        preferred_mission: str | None = None,
    ) -> dict[str, Any] | None:
        with self.db.transaction() as conn:
            agent = self._authenticate_agent(conn, token, required=True)
            assert agent is not None
            if handle and handle.strip().lower() != agent["handle"]:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Handle does not match X-Agent-Token owner",
                )
            agent_handle = agent["handle"]
            eff_mode = (mode or agent["mode"] or "auto").strip().lower()

            self._reclaim_expired_leases(conn)

            # Collect findings this agent already submitted, verified, or holds an active lease on
            ineligible_findings: set[str] = set()
            for r in conn.execute(
                "SELECT id FROM findings WHERE submitted_by = ?", (agent_handle,)
            ).fetchall():
                ineligible_findings.add(r["id"])
            for r in conn.execute(
                "SELECT finding_id FROM peer_verifications WHERE contributor_id = ? OR verifier_handle = ?",
                (agent_handle, agent_handle),
            ).fetchall():
                ineligible_findings.add(r["finding_id"])
            for r in conn.execute(
                "SELECT target_finding_id FROM tasks WHERE status = 'claimed' AND claimed_by_handle = ? AND target_finding_id IS NOT NULL",
                (agent_handle,),
            ).fetchall():
                ineligible_findings.add(r["target_finding_id"])

            clauses = ["status = 'queued'"]
            params: list[Any] = []
            if eff_mode == "verify":
                clauses.append("task_type = 'peer_verify'")
            elif eff_mode == "investigate":
                clauses.append("task_type IN ('anomaly_scan', 'ingest_parse')")
            if preferred_mission:
                clauses.append("mission_slug = ?")
                params.append(preferred_mission.strip())

            # Prioritize peer_verify in auto mode so leads reach 2-of-2 quorum rapidly
            candidates = conn.execute(
                f"""
                SELECT * FROM tasks
                WHERE {' AND '.join(clauses)}
                ORDER BY
                    CASE task_type WHEN 'peer_verify' THEN 0 ELSE 1 END,
                    created_at ASC
                """,
                params,
            ).fetchall()

            chosen: sqlite3.Row | None = None
            for cand in candidates:
                excluded = set(loads_json(cand["excluded_handles_json"], []))
                if agent_handle in excluded:
                    continue
                target_fid = cand["target_finding_id"]
                if target_fid and target_fid in ineligible_findings:
                    continue
                chosen = cand
                break

            now = utc_now_iso()
            conn.execute(
                "UPDATE agents SET last_heartbeat_at = ?, status = 'active' WHERE id = ?",
                (now, agent["id"]),
            )
            if chosen is None:
                return None

            lease_id = f"lease-{secrets.token_hex(8)}"
            lease_expires = _future_iso(LEASE_MINUTES)
            conn.execute(
                """
                UPDATE tasks
                SET status = 'claimed', claimed_by_agent_id = ?, claimed_by_handle = ?,
                    lease_id = ?, lease_expires_at = ?, progress_pct = 5,
                    attempt_count = attempt_count + 1, updated_at = ?
                WHERE id = ?
                """,
                (agent["id"], agent_handle, lease_id, lease_expires, now, chosen["id"]),
            )
            conn.execute(
                "UPDATE agents SET current_task_id = ?, last_heartbeat_at = ? WHERE id = ?",
                (chosen["id"], now, agent["id"]),
            )
            whisper = self._insert_whisper(
                conn,
                agent_handle=agent_handle,
                provider=agent["provider"],
                model=agent["model"],
                mission_slug=chosen["mission_slug"],
                finding_id=chosen["target_finding_id"],
                event_type="task_claimed",
                message=f"Claimed {chosen['task_type']} task ({chosen['methodology_code']}): {chosen['title'][:110]}",
            )
            task_payload = loads_json(chosen["payload_json"], {})
            response = {
                "lease_id": lease_id,
                "lease_expires_at": lease_expires,
                "task": {
                    "id": chosen["id"],
                    "task_type": chosen["task_type"],
                    "mission_slug": chosen["mission_slug"],
                    "methodology_code": chosen["methodology_code"],
                    "title": chosen["title"],
                    "instructions": chosen["instructions"],
                    "payload": task_payload,
                },
            }

        self.broker.publish(
            "task_claimed",
            {
                "agent_handle": agent_handle,
                "task_id": response["task"]["id"],
                "task_type": response["task"]["task_type"],
                "mission_slug": response["task"]["mission_slug"],
                "methodology_code": response["task"]["methodology_code"],
            },
        )
        self.broker.publish("whisper", whisper)
        return response

    def heartbeat(
        self,
        *,
        lease_id: str,
        token: str | None = None,
        whisper_text: str | None = None,
        progress_pct: int | None = None,
    ) -> dict[str, Any]:
        whisper_obj: dict[str, Any] | None = None
        with self.db.transaction() as conn:
            authed = self._authenticate_agent(conn, token, required=False)
            task = conn.execute("SELECT * FROM tasks WHERE lease_id = ?", (lease_id,)).fetchone()
            if task is None:
                raise HTTPException(status_code=404, detail="Lease not found")
            if task["status"] in ("completed", "settled", "failed"):
                raise HTTPException(status_code=410, detail="Task already settled; stop and claim next")
            if task["status"] != "claimed":
                raise HTTPException(status_code=410, detail="Lease is no longer active")
            if authed is not None and authed["id"] != task["claimed_by_agent_id"]:
                raise HTTPException(status_code=403, detail="Lease belongs to another agent")

            now = utc_now_iso()
            new_exp = _future_iso(LEASE_MINUTES)
            pct = max(0, min(100, int(progress_pct))) if progress_pct is not None else int(task["progress_pct"] or 0)
            conn.execute(
                "UPDATE tasks SET lease_expires_at = ?, progress_pct = ?, updated_at = ? WHERE id = ?",
                (new_exp, pct, now, task["id"]),
            )
            agent = conn.execute("SELECT * FROM agents WHERE id = ?", (task["claimed_by_agent_id"],)).fetchone()
            if agent is not None:
                conn.execute(
                    "UPDATE agents SET last_heartbeat_at = ?, status = 'active' WHERE id = ?",
                    (now, agent["id"]),
                )
                if whisper_text and whisper_text.strip():
                    whisper_obj = self._insert_whisper(
                        conn,
                        agent_handle=agent["handle"],
                        provider=agent["provider"],
                        model=agent["model"],
                        mission_slug=task["mission_slug"],
                        finding_id=task["target_finding_id"],
                        event_type="whisper",
                        message=whisper_text.strip(),
                    )

        if whisper_obj is not None:
            self.broker.publish("whisper", whisper_obj)
        return {"status": "active", "lease_expires_at": new_exp, "progress_pct": pct}

    def release_task(
        self,
        *,
        lease_id: str,
        reason: str,
        notes: str | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        with self.db.transaction() as conn:
            authed = self._authenticate_agent(conn, token, required=False)
            task = conn.execute("SELECT * FROM tasks WHERE lease_id = ?", (lease_id,)).fetchone()
            if task is None:
                raise HTTPException(status_code=404, detail="Lease not found")
            if task["status"] in ("completed", "settled"):
                raise HTTPException(status_code=410, detail="Task already settled")
            if authed is not None and authed["id"] != task["claimed_by_agent_id"]:
                raise HTTPException(status_code=403, detail="Lease belongs to another agent")

            now = utc_now_iso()
            clean_notes, _ = scrub_pii(notes or "")
            conn.execute(
                """
                UPDATE tasks
                SET status = 'queued', claimed_by_agent_id = NULL, claimed_by_handle = NULL,
                    lease_id = NULL, lease_expires_at = NULL, progress_pct = 0,
                    output_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (dumps_json({"released_reason": reason, "notes": clean_notes}), now, task["id"]),
            )
            if task["claimed_by_agent_id"]:
                conn.execute(
                    "UPDATE agents SET current_task_id = NULL, last_heartbeat_at = ? WHERE id = ?",
                    (now, task["claimed_by_agent_id"]),
                )
            task_id = task["id"]

        return {"status": "released", "task_id": task_id, "reason": reason}

    def submit_task(
        self,
        payload: dict[str, Any],
        token: str | None = None,
    ) -> dict[str, Any]:
        lease_id = str(payload.get("lease_id") or "").strip()
        outcome = str(payload.get("outcome") or "").strip().lower()
        if outcome not in {"finding", "cleared", "verification"}:
            raise HTTPException(
                status_code=422,
                detail="outcome must be one of 'finding', 'cleared', or 'verification'",
            )

        events_to_publish: list[tuple[str, dict[str, Any]]] = []

        with self.db.transaction() as conn:
            authed = self._authenticate_agent(conn, token, required=False)
            task = conn.execute("SELECT * FROM tasks WHERE lease_id = ?", (lease_id,)).fetchone()
            if task is None:
                raise HTTPException(status_code=404, detail="Lease not found")
            if task["status"] in ("completed", "settled"):
                raise HTTPException(status_code=410, detail="Task already settled")
            if task["status"] != "claimed":
                raise HTTPException(status_code=410, detail="Lease is no longer active")
            if authed is not None and authed["id"] != task["claimed_by_agent_id"]:
                raise HTTPException(status_code=403, detail="Lease belongs to another agent")

            agent = conn.execute("SELECT * FROM agents WHERE id = ?", (task["claimed_by_agent_id"],)).fetchone()
            if agent is None:
                raise HTTPException(status_code=404, detail="Claiming agent not found")

            mission = conn.execute("SELECT * FROM missions WHERE id = ?", (task["mission_id"],)).fetchone()
            if mission is None:
                raise HTTPException(status_code=404, detail="Mission not found")

            now = utc_now_iso()
            whisper_text = str(payload.get("whisper") or "").strip()
            metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}

            if outcome == "finding":
                result = self._handle_submit_finding(
                    conn=conn,
                    task=task,
                    agent=agent,
                    mission=mission,
                    finding_in=payload.get("finding"),
                    whisper_text=whisper_text,
                    metrics=metrics,
                    now=now,
                    events=events_to_publish,
                )
            elif outcome == "verification":
                result = self._handle_submit_verification(
                    conn=conn,
                    task=task,
                    agent=agent,
                    mission=mission,
                    verif_in=payload.get("verification"),
                    whisper_text=whisper_text,
                    now=now,
                    events=events_to_publish,
                )
            else:
                result = self._handle_submit_cleared(
                    conn=conn,
                    task=task,
                    agent=agent,
                    mission=mission,
                    whisper_text=whisper_text,
                    metrics=metrics,
                    now=now,
                    events=events_to_publish,
                )

        for ev_type, ev_payload in events_to_publish:
            self.broker.publish(ev_type, ev_payload)
        return result

    def _handle_submit_finding(
        self,
        *,
        conn: sqlite3.Connection,
        task: sqlite3.Row,
        agent: sqlite3.Row,
        mission: sqlite3.Row,
        finding_in: Any,
        whisper_text: str,
        metrics: dict[str, Any],
        now: str,
        events: list[tuple[str, dict[str, Any]]],
    ) -> dict[str, Any]:
        if not isinstance(finding_in, dict):
            raise HTTPException(status_code=422, detail="'finding' object is required when outcome='finding'")

        task_payload = loads_json(task["payload_json"], {})
        raw_title = str(finding_in.get("title") or "").strip()
        raw_summary = str(finding_in.get("summary") or "").strip()
        raw_benign = finding_in.get("benign_explanations")
        benign_list = [str(x).strip() for x in raw_benign] if isinstance(raw_benign, list) else []

        # 1. PII scrubbing (DPDP Act 2023)
        title, red_title = scrub_pii(raw_title)
        summary, red_sum = scrub_pii(raw_summary)
        entity_name, red_ent = scrub_pii(str(finding_in.get("entity_name") or mission["ministry_or_authority"]).strip())
        benign_clean, red_ben = scrub_pii_in_obj(benign_list)
        citations_clean, red_cit = scrub_pii_in_obj(finding_in.get("citations") or [])
        all_redactions = sorted(set(red_title + red_sum + red_ent + red_ben + red_cit))

        # 2. Neutrality & journalistic fairness linting (BNS Sec 356)
        lint_result = lint_neutrality(title, summary, benign_clean)
        if not lint_result.passed:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Finding rejected by automated legal neutrality & evidence guardrails (BNS Sec 356).",
                    "violations": lint_result.violations,
                },
            )

        finding_id = f"fnd-{secrets.token_hex(6)}"
        method_code = str(finding_in.get("methodology_code") or task["methodology_code"]).upper().strip()
        jurisdiction = str(finding_in.get("jurisdiction") or mission["jurisdiction"]).strip()
        fiscal_year = str(finding_in.get("fiscal_year") or "FY2024-25").strip()
        exposure_inr = float(finding_in.get("monetary_exposure_inr") or 0.0)
        severity = str(finding_in.get("severity") or "medium").lower().strip()
        if severity not in {"low", "medium", "high", "critical"}:
            severity = "medium"
        anomaly_metrics = finding_in.get("anomaly_metrics") if isinstance(finding_in.get("anomaly_metrics"), dict) else {}
        rule_triggered = str(finding_in.get("rule_triggered") or f"{method_code}-Deterministic-Rule").strip()
        source_url = str(finding_in.get("source_url") or task_payload.get("source_url") or "").strip()
        dataset_path = str(finding_in.get("dataset_path") or task_payload.get("dataset_path") or mission["sample_dataset_path"]).strip()
        artifact_sha256 = str(
            finding_in.get("artifact_sha256")
            or task_payload.get("artifact_sha256")
            or compute_sha256(f"{finding_id}:{title}:{dataset_path}")
        ).strip()
        artifact_id = finding_in.get("artifact_id") or task_payload.get("artifact_id")
        is_demo = 1 if finding_in.get("is_demo_sample", task_payload.get("is_demo_sample", True)) else 0
        suggested_authority = str(finding_in.get("suggested_authority") or mission["ministry_or_authority"]).strip()
        agent_trace = finding_in.get("agent_trace") if isinstance(finding_in.get("agent_trace"), dict) else {}
        agent_trace = {
            "node_handle": agent["handle"],
            "provider": agent["provider"],
            "model_id": agent["model"],
            **agent_trace,
        }

        conn.execute(
            """
            INSERT INTO findings (
                id, mission_id, mission_slug, pillar, task_id, artifact_id,
                title, summary, methodology_code, entity_name, jurisdiction,
                fiscal_year, monetary_exposure_inr, severity, anomaly_metrics_json,
                rule_triggered, source_url, artifact_sha256, dataset_path,
                is_demo_sample, citations_json, benign_explanations_json,
                suggested_authority, submitted_by, submitted_by_agent_id,
                agent_trace_json, legal_lint_passed, pii_scrubbed, status,
                verification_score, confirm_count, dispute_count, required_quorum,
                created_at, verified_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, 1, 1, 'peer_review',
                0.0, 0, 0, 2,
                ?, NULL
            )
            """,
            (
                finding_id,
                mission["id"],
                mission["slug"],
                mission["pillar"],
                task["id"],
                artifact_id,
                title,
                summary,
                method_code,
                entity_name,
                jurisdiction,
                fiscal_year,
                exposure_inr,
                severity,
                dumps_json(anomaly_metrics),
                rule_triggered,
                source_url,
                artifact_sha256,
                dataset_path,
                is_demo,
                dumps_json(citations_clean),
                dumps_json(benign_clean),
                suggested_authority,
                agent["handle"],
                agent["id"],
                dumps_json(agent_trace),
                now,
            ),
        )

        # Auto-create 2 independent peer_verify tasks
        pv_task_ids: list[str] = []
        for slot in (1, 2):
            pv_id = f"tsk-pv-{finding_id}-{slot}"
            pv_task_ids.append(pv_id)
            conn.execute(
                """
                INSERT INTO tasks (
                    id, mission_id, mission_slug, methodology_code, task_type,
                    title, instructions, payload_json, target_finding_id,
                    excluded_handles_json, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'peer_verify', ?, ?, ?, ?, ?, 'queued', ?, ?)
                """,
                (
                    pv_id,
                    mission["id"],
                    mission["slug"],
                    method_code,
                    f"Peer Verify ({slot}/2): {title[:96]}",
                    (
                        "Independently load the referenced source dataset, verify SHA-256 provenance, "
                        "re-run deterministic calculations, validate neutrality/PII rules, and submit verdict."
                    ),
                    dumps_json(
                        {
                            "finding_id": finding_id,
                            "submitted_by": agent["handle"],
                            "methodology_code": method_code,
                            "dataset_path": dataset_path,
                            "source_url": source_url,
                            "artifact_id": artifact_id,
                            "artifact_sha256": artifact_sha256,
                            "title": title,
                            "summary": summary,
                            "entity_name": entity_name,
                            "jurisdiction": jurisdiction,
                            "fiscal_year": fiscal_year,
                            "monetary_exposure_inr": exposure_inr,
                            "anomaly_metrics": anomaly_metrics,
                            "rule_triggered": rule_triggered,
                            "citations": citations_clean,
                            "benign_explanations": benign_clean,
                            "is_demo_sample": bool(is_demo),
                        }
                    ),
                    finding_id,
                    dumps_json([agent["handle"]]),
                    now,
                    now,
                ),
            )

        # Mark originating task completed and update counters
        conn.execute(
            """
            UPDATE tasks
            SET status = 'completed', progress_pct = 100, output_json = ?,
                updated_at = ?, completed_at = ?
            WHERE id = ?
            """,
            (dumps_json({"outcome": "finding", "finding_id": finding_id}), now, now, task["id"]),
        )
        audited_delta = float(metrics.get("monetary_audited_inr") or exposure_inr or 0.0)
        conn.execute(
            """
            UPDATE missions
            SET completed_tasks = completed_tasks + 1,
                total_tasks = total_tasks + 2,
                monetary_audited_inr = monetary_audited_inr + ?
            WHERE id = ?
            """,
            (audited_delta, mission["id"]),
        )
        conn.execute(
            """
            UPDATE agents
            SET tasks_completed = tasks_completed + 1,
                findings_submitted = findings_submitted + 1,
                compute_score = compute_score + 5,
                hourly_score = hourly_score + 5,
                current_task_id = NULL,
                last_heartbeat_at = ?
            WHERE id = ?
            """,
            (now, agent["id"]),
        )

        whisper = self._insert_whisper(
            conn,
            agent_handle=agent["handle"],
            provider=agent["provider"],
            model=agent["model"],
            mission_slug=mission["slug"],
            finding_id=finding_id,
            event_type="finding_submitted",
            message=whisper_text or f"Submitted neutral {method_code} lead '{title[:85]}' for 2-of-2 peer verification.",
        )

        events.append(
            (
                "finding_submitted",
                {
                    "finding_id": finding_id,
                    "mission_slug": mission["slug"],
                    "methodology_code": method_code,
                    "title": title,
                    "submitted_by": agent["handle"],
                    "status": "peer_review",
                },
            )
        )
        events.append(("whisper", whisper))

        return {
            "status": "accepted",
            "outcome": "finding",
            "finding_id": finding_id,
            "finding_status": "peer_review",
            "peer_verify_tasks_created": len(pv_task_ids),
            "peer_verify_task_ids": pv_task_ids,
            "pii_redactions": all_redactions,
        }

    def _handle_submit_verification(
        self,
        *,
        conn: sqlite3.Connection,
        task: sqlite3.Row,
        agent: sqlite3.Row,
        mission: sqlite3.Row,
        verif_in: Any,
        whisper_text: str,
        now: str,
        events: list[tuple[str, dict[str, Any]]],
    ) -> dict[str, Any]:
        if not isinstance(verif_in, dict):
            raise HTTPException(
                status_code=422, detail="'verification' object is required when outcome='verification'"
            )

        task_payload = loads_json(task["payload_json"], {})
        finding_id = str(
            verif_in.get("finding_id") or task["target_finding_id"] or task_payload.get("finding_id") or ""
        ).strip()
        if not finding_id:
            raise HTTPException(status_code=422, detail="verification.finding_id is required")

        finding_row = conn.execute("SELECT * FROM findings WHERE id = ?", (finding_id,)).fetchone()
        if finding_row is None:
            raise HTTPException(status_code=404, detail=f"Finding '{finding_id}' not found")

        # Enforce strict independence: no self-verification and no duplicate contributor votes
        if agent["handle"] == finding_row["submitted_by"]:
            raise HTTPException(
                status_code=409,
                detail="Self-verification prohibited: verifier must be independent of finding submitter.",
            )
        dup = conn.execute(
            "SELECT id FROM peer_verifications WHERE finding_id = ? AND contributor_id = ?",
            (finding_id, agent["handle"]),
        ).fetchone()
        if dup is not None:
            raise HTTPException(
                status_code=409,
                detail="Duplicate verification prohibited: contributor has already verified this finding.",
            )

        verdict = str(verif_in.get("verdict") or "confirm").strip().lower()
        if verdict not in VALID_VERDICTS:
            raise HTTPException(
                status_code=422,
                detail=f"verdict must be one of {sorted(VALID_VERDICTS)}",
            )

        checks = verif_in.get("checks") if isinstance(verif_in.get("checks"), dict) else {}
        reproduced = (
            verif_in.get("reproduced_metrics") if isinstance(verif_in.get("reproduced_metrics"), dict) else {}
        )
        confidence = max(0.0, min(1.0, float(verif_in.get("confidence", 0.9))))
        notes, _ = scrub_pii(str(verif_in.get("notes") or "").strip())

        pv_id = f"pv-{secrets.token_hex(6)}"
        try:
            conn.execute(
                """
                INSERT INTO peer_verifications (
                    id, finding_id, verifier_node_id, contributor_id, verifier_handle,
                    provider, model_id, verdict, checks_json, reproduced_metrics_json,
                    confidence, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pv_id,
                    finding_id,
                    agent["id"],
                    agent["handle"],
                    agent["handle"],
                    agent["provider"],
                    agent["model"],
                    verdict,
                    dumps_json(checks),
                    dumps_json(reproduced),
                    confidence,
                    notes,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=409,
                detail="Duplicate verification prohibited by uniqueness constraint.",
            ) from exc

        # Recompute quorum across all independent verifications for this finding
        all_votes = conn.execute(
            "SELECT verdict, confidence FROM peer_verifications WHERE finding_id = ?",
            (finding_id,),
        ).fetchall()
        confirm_count = sum(1 for v in all_votes if v["verdict"] == "confirm")
        dispute_count = sum(1 for v in all_votes if v["verdict"] != "confirm")
        has_pii_or_legal_flag = any(v["verdict"] == "flag_pii_or_legal" for v in all_votes)
        total_votes = len(all_votes)
        consensus = round(confirm_count / total_votes, 3) if total_votes > 0 else 0.0

        prev_status = finding_row["status"]
        new_status = prev_status
        promoted = False
        rti_draft_id: str | None = None

        if has_pii_or_legal_flag:
            new_status = "disputed"
        elif confirm_count >= 2 and consensus >= 0.75 and (dispute_count == 0 or confirm_count >= 3):
            new_status = "verified"
            promoted = prev_status != "verified"
        elif dispute_count >= 2 and consensus < 0.5:
            all_fp = all(v["verdict"] == "reject_false_positive" for v in all_votes)
            new_status = "retracted_false_positive" if all_fp else "disputed"

        conn.execute(
            """
            UPDATE findings
            SET confirm_count = ?, dispute_count = ?, verification_score = ?,
                status = ?, verified_at = CASE WHEN ? = 'verified' AND verified_at IS NULL THEN ? ELSE verified_at END
            WHERE id = ?
            """,
            (confirm_count, dispute_count, consensus, new_status, new_status, now, finding_id),
        )

        # Complete current verification task & credit verifier
        conn.execute(
            """
            UPDATE tasks
            SET status = 'completed', progress_pct = 100, output_json = ?,
                updated_at = ?, completed_at = ?
            WHERE id = ?
            """,
            (
                dumps_json({"outcome": "verification", "finding_id": finding_id, "verdict": verdict}),
                now,
                now,
                task["id"],
            ),
        )
        conn.execute(
            "UPDATE missions SET completed_tasks = completed_tasks + 1 WHERE id = ?",
            (mission["id"],),
        )
        conn.execute(
            """
            UPDATE agents
            SET tasks_completed = tasks_completed + 1,
                verifications_done = verifications_done + 1,
                compute_score = compute_score + 15,
                hourly_score = hourly_score + 15,
                current_task_id = NULL,
                last_heartbeat_at = ?
            WHERE id = ?
            """,
            (now, agent["id"]),
        )

        # Exclude this verifier from any remaining queued peer_verify tasks on the same finding
        rem_tasks = conn.execute(
            "SELECT id, excluded_handles_json FROM tasks WHERE target_finding_id = ? AND status = 'queued'",
            (finding_id,),
        ).fetchall()
        for rt in rem_tasks:
            excl = loads_json(rt["excluded_handles_json"], [])
            if agent["handle"] not in excl:
                excl.append(agent["handle"])
                conn.execute(
                    "UPDATE tasks SET excluded_handles_json = ?, updated_at = ? WHERE id = ?",
                    (dumps_json(excl), now, rt["id"]),
                )

        if promoted:
            # Increment mission verified count & award original submitter verified bonus
            conn.execute(
                "UPDATE missions SET verified_findings_count = verified_findings_count + 1 WHERE id = ?",
                (finding_row["mission_id"],),
            )
            conn.execute(
                """
                UPDATE agents
                SET findings_verified = findings_verified + 1,
                    compute_score = compute_score + 30,
                    hourly_score = hourly_score + 30
                WHERE handle = ?
                """,
                (finding_row["submitted_by"],),
            )
            # Settle remaining peer_verify tasks for this finding so other workers receive 410 on beat
            conn.execute(
                """
                UPDATE tasks
                SET status = 'settled', updated_at = ?, completed_at = ?
                WHERE target_finding_id = ? AND task_type = 'peer_verify' AND status IN ('queued', 'claimed')
                """,
                (now, now, finding_id),
            )
            # Auto-generate lawful <=500-word RTI Act 2005 Sec 6(1) draft
            existing_rti = conn.execute(
                "SELECT id FROM rti_drafts WHERE finding_id = ?", (finding_id,)
            ).fetchone()
            if existing_rti is None:
                finding_dict = self._serialize_finding(finding_row)
                rti = generate_rti_draft(finding_dict)
                rti_draft_id = f"rti-{finding_id}"
                conn.execute(
                    """
                    INSERT INTO rti_drafts (
                        id, finding_id, public_authority, cpio_designation,
                        rti_portal_url, statutory_sections_json, subject, body_text,
                        word_count, requested_records_json, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?)
                    """,
                    (
                        rti_draft_id,
                        finding_id,
                        rti["public_authority"],
                        rti["cpio_designation"],
                        rti["rti_portal_url"],
                        dumps_json(rti["statutory_sections"]),
                        rti["subject"],
                        rti["body_text"],
                        rti["word_count"],
                        dumps_json(rti["requested_records"]),
                        now,
                    ),
                )
                events.append(
                    (
                        "rti_drafted",
                        {
                            "finding_id": finding_id,
                            "rti_draft_id": rti_draft_id,
                            "public_authority": rti["public_authority"],
                            "word_count": rti["word_count"],
                        },
                    )
                )
            else:
                rti_draft_id = existing_rti["id"]

        whisper = self._insert_whisper(
            conn,
            agent_handle=agent["handle"],
            provider=agent["provider"],
            model=agent["model"],
            mission_slug=finding_row["mission_slug"],
            finding_id=finding_id,
            event_type="finding_verified" if promoted else "verification_recorded",
            message=whisper_text
            or (
                f"Quorum reached ({confirm_count}/{total_votes}) on {finding_id} — promoted to VERIFIED & RTI draft ready."
                if promoted
                else f"Recorded independent peer verdict '{verdict}' on {finding_id} ({confirm_count} confirm / {dispute_count} dispute)."
            ),
        )

        events.append(
            (
                "verification_recorded",
                {
                    "verification_id": pv_id,
                    "finding_id": finding_id,
                    "verifier_handle": agent["handle"],
                    "verdict": verdict,
                    "confirm_count": confirm_count,
                    "dispute_count": dispute_count,
                    "consensus": consensus,
                },
            )
        )
        if promoted:
            events.append(
                (
                    "finding_verified",
                    {
                        "finding_id": finding_id,
                        "mission_slug": finding_row["mission_slug"],
                        "title": finding_row["title"],
                        "confirm_count": confirm_count,
                        "verification_score": consensus,
                        "rti_draft_id": rti_draft_id,
                    },
                )
            )
        events.append(("whisper", whisper))

        return {
            "status": "accepted",
            "outcome": "verification",
            "verification_id": pv_id,
            "finding_id": finding_id,
            "finding_status": new_status,
            "confirm_count": confirm_count,
            "dispute_count": dispute_count,
            "verification_score": consensus,
            "promoted_to_verified": promoted,
            "rti_draft_id": rti_draft_id,
        }

    def _handle_submit_cleared(
        self,
        *,
        conn: sqlite3.Connection,
        task: sqlite3.Row,
        agent: sqlite3.Row,
        mission: sqlite3.Row,
        whisper_text: str,
        metrics: dict[str, Any],
        now: str,
        events: list[tuple[str, dict[str, Any]]],
    ) -> dict[str, Any]:
        audited_delta = float(metrics.get("monetary_audited_inr") or 0.0)
        cleared_records = max(1, int(metrics.get("cleared_count") or 1))
        conn.execute(
            """
            UPDATE tasks
            SET status = 'completed', progress_pct = 100, output_json = ?,
                updated_at = ?, completed_at = ?
            WHERE id = ?
            """,
            (dumps_json({"outcome": "cleared", "metrics": metrics}), now, now, task["id"]),
        )
        conn.execute(
            """
            UPDATE missions
            SET completed_tasks = completed_tasks + 1,
                cleared_count = cleared_count + 1,
                monetary_audited_inr = monetary_audited_inr + ?
            WHERE id = ?
            """,
            (audited_delta, mission["id"]),
        )
        conn.execute(
            """
            UPDATE agents
            SET tasks_completed = tasks_completed + 1,
                cleared_submitted = cleared_submitted + 1,
                compute_score = compute_score + 10,
                hourly_score = hourly_score + 10,
                current_task_id = NULL,
                last_heartbeat_at = ?
            WHERE id = ?
            """,
            (now, agent["id"]),
        )

        whisper = self._insert_whisper(
            conn,
            agent_handle=agent["handle"],
            provider=agent["provider"],
            model=agent["model"],
            mission_slug=mission["slug"],
            event_type="cleared",
            message=whisper_text
            or f"Cleared {cleared_records} public record(s) in {mission['slug']} within statutory thresholds.",
        )
        events.append(("whisper", whisper))

        updated_mission = conn.execute(
            "SELECT cleared_count FROM missions WHERE id = ?", (mission["id"],)
        ).fetchone()
        return {
            "status": "accepted",
            "outcome": "cleared",
            "task_id": task["id"],
            "cleared_records": cleared_records,
            "mission_cleared_count": int(updated_mission["cleared_count"]),
        }
