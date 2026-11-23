"""SQLite (WAL mode) database layer for AI Watchdogs India Coordinator Hub."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

DEFAULT_DB_REL_PATH = Path("data/aiwatchdogs.db")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS public_sources (
    id TEXT PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    pillar TEXT NOT NULL,
    authority TEXT NOT NULL,
    base_url TEXT NOT NULL,
    ingestion_tier INTEGER NOT NULL CHECK (ingestion_tier IN (1, 2, 3)),
    connector_type TEXT NOT NULL,
    data_contents TEXT NOT NULL DEFAULT '',
    format_access TEXT NOT NULL DEFAULT '',
    config_json TEXT NOT NULL DEFAULT '{}',
    robots_policy TEXT NOT NULL DEFAULT 'strict-polite',
    last_synced_at TEXT
);

CREATE TABLE IF NOT EXISTS missions (
    id TEXT PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    pillar TEXT NOT NULL,
    methodology_code TEXT NOT NULL,
    jurisdiction TEXT NOT NULL,
    ministry_or_authority TEXT NOT NULL,
    statutory_anchors_json TEXT NOT NULL DEFAULT '[]',
    source_ids_json TEXT NOT NULL DEFAULT '[]',
    sample_dataset_path TEXT NOT NULL DEFAULT '',
    data_mode TEXT NOT NULL DEFAULT 'demo_sample',
    status TEXT NOT NULL DEFAULT 'active',
    priority INTEGER NOT NULL DEFAULT 1,
    total_tasks INTEGER NOT NULL DEFAULT 0,
    completed_tasks INTEGER NOT NULL DEFAULT 0,
    verified_findings_count INTEGER NOT NULL DEFAULT 0,
    cleared_count INTEGER NOT NULL DEFAULT 0,
    monetary_audited_inr REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    handle TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'auto',
    region TEXT NOT NULL DEFAULT 'IN',
    capabilities_json TEXT NOT NULL DEFAULT '[]',
    api_token_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    tasks_completed INTEGER NOT NULL DEFAULT 0,
    findings_submitted INTEGER NOT NULL DEFAULT 0,
    findings_verified INTEGER NOT NULL DEFAULT 0,
    verifications_done INTEGER NOT NULL DEFAULT 0,
    cleared_submitted INTEGER NOT NULL DEFAULT 0,
    compute_score INTEGER NOT NULL DEFAULT 0,
    hourly_score INTEGER NOT NULL DEFAULT 0,
    current_task_id TEXT,
    joined_at TEXT NOT NULL,
    last_heartbeat_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence_artifacts (
    id TEXT PRIMARY KEY,
    source_id TEXT REFERENCES public_sources(id),
    mission_id TEXT REFERENCES missions(id),
    title TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    archive_url TEXT,
    local_path TEXT,
    file_format TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    byte_size INTEGER NOT NULL DEFAULT 0,
    is_demo_sample INTEGER NOT NULL DEFAULT 1,
    pii_scrubbed INTEGER NOT NULL DEFAULT 1,
    fetched_at TEXT NOT NULL,
    parsed_metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL REFERENCES missions(id),
    mission_slug TEXT NOT NULL,
    methodology_code TEXT NOT NULL,
    task_type TEXT NOT NULL,
    title TEXT NOT NULL,
    instructions TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    target_finding_id TEXT,
    excluded_handles_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'queued',
    claimed_by_agent_id TEXT REFERENCES agents(id),
    claimed_by_handle TEXT,
    lease_id TEXT UNIQUE,
    lease_expires_at TEXT,
    progress_pct INTEGER NOT NULL DEFAULT 0,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    output_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS findings (
    id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL REFERENCES missions(id),
    mission_slug TEXT NOT NULL,
    pillar TEXT NOT NULL,
    task_id TEXT REFERENCES tasks(id),
    artifact_id TEXT REFERENCES evidence_artifacts(id),
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    methodology_code TEXT NOT NULL,
    entity_name TEXT NOT NULL,
    jurisdiction TEXT NOT NULL,
    fiscal_year TEXT NOT NULL,
    monetary_exposure_inr REAL NOT NULL DEFAULT 0.0,
    severity TEXT NOT NULL DEFAULT 'medium',
    anomaly_metrics_json TEXT NOT NULL DEFAULT '{}',
    rule_triggered TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    artifact_sha256 TEXT NOT NULL DEFAULT '',
    dataset_path TEXT NOT NULL DEFAULT '',
    is_demo_sample INTEGER NOT NULL DEFAULT 1,
    citations_json TEXT NOT NULL DEFAULT '[]',
    benign_explanations_json TEXT NOT NULL DEFAULT '[]',
    suggested_authority TEXT NOT NULL DEFAULT '',
    submitted_by TEXT NOT NULL,
    submitted_by_agent_id TEXT REFERENCES agents(id),
    agent_trace_json TEXT NOT NULL DEFAULT '{}',
    legal_lint_passed INTEGER NOT NULL DEFAULT 1,
    pii_scrubbed INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'peer_review',
    verification_score REAL NOT NULL DEFAULT 0.0,
    confirm_count INTEGER NOT NULL DEFAULT 0,
    dispute_count INTEGER NOT NULL DEFAULT 0,
    required_quorum INTEGER NOT NULL DEFAULT 2,
    created_at TEXT NOT NULL,
    verified_at TEXT
);

CREATE TABLE IF NOT EXISTS peer_verifications (
    id TEXT PRIMARY KEY,
    finding_id TEXT NOT NULL REFERENCES findings(id),
    verifier_node_id TEXT NOT NULL REFERENCES agents(id),
    contributor_id TEXT NOT NULL,
    verifier_handle TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT '',
    model_id TEXT NOT NULL DEFAULT '',
    verdict TEXT NOT NULL,
    checks_json TEXT NOT NULL DEFAULT '{}',
    reproduced_metrics_json TEXT NOT NULL DEFAULT '{}',
    confidence REAL NOT NULL DEFAULT 0.9,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(finding_id, contributor_id)
);

CREATE TABLE IF NOT EXISTS rti_drafts (
    id TEXT PRIMARY KEY,
    finding_id TEXT UNIQUE NOT NULL REFERENCES findings(id),
    public_authority TEXT NOT NULL,
    cpio_designation TEXT NOT NULL,
    rti_portal_url TEXT NOT NULL DEFAULT 'https://rtionline.gov.in/',
    statutory_sections_json TEXT NOT NULL DEFAULT '["Sec 6(1)", "Sec 2(f)", "Sec 4(1)(b)"]',
    subject TEXT NOT NULL,
    body_text TEXT NOT NULL,
    word_count INTEGER NOT NULL,
    requested_records_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'draft',
    filed_date TEXT,
    due_date_30d TEXT,
    official_reply_artifact_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS whispers (
    id TEXT PRIMARY KEY,
    agent_handle TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    mission_slug TEXT,
    finding_id TEXT,
    event_type TEXT NOT NULL DEFAULT 'whisper',
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sponsors (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    handle_or_org TEXT,
    tier TEXT NOT NULL DEFAULT 'citizen_backer',
    amount_inr REAL NOT NULL DEFAULT 0.0,
    compute_credits_desc TEXT NOT NULL DEFAULT '',
    url TEXT,
    message TEXT NOT NULL DEFAULT '',
    featured INTEGER NOT NULL DEFAULT 0,
    is_demo_entry INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS treasury_ledger (
    id TEXT PRIMARY KEY,
    entry_date TEXT NOT NULL,
    entry_type TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('inflow', 'outflow')),
    category TEXT NOT NULL,
    counterparty TEXT NOT NULL,
    description TEXT NOT NULL,
    amount_inr REAL NOT NULL,
    compute_credit_units TEXT NOT NULL DEFAULT '',
    receipt_ref TEXT NOT NULL,
    is_demo_entry INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_status_type ON tasks(status, task_type);
CREATE INDEX IF NOT EXISTS idx_tasks_mission_slug ON tasks(mission_slug);
CREATE INDEX IF NOT EXISTS idx_tasks_target_finding ON tasks(target_finding_id);
CREATE INDEX IF NOT EXISTS idx_findings_status ON findings(status);
CREATE INDEX IF NOT EXISTS idx_findings_mission ON findings(mission_slug);
CREATE INDEX IF NOT EXISTS idx_findings_pillar ON findings(pillar);
CREATE INDEX IF NOT EXISTS idx_findings_methodology ON findings(methodology_code);
CREATE INDEX IF NOT EXISTS idx_peer_verif_finding ON peer_verifications(finding_id);
CREATE INDEX IF NOT EXISTS idx_whispers_created ON whispers(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agents_score ON agents(compute_score DESC);
CREATE INDEX IF NOT EXISTS idx_ledger_date ON treasury_ledger(entry_date DESC);
"""


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format with Z suffix."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def loads_json(raw: str | None, default: Any) -> Any:
    """Safely deserialize a JSON column string."""
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return default


def dumps_json(value: Any) -> str:
    """Serialize a Python object to compact JSON for SQLite storage."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def resolve_db_path(db_path: str | Path | None = None) -> Path:
    """Resolve SQLite DB path from argument, AIWATCHDOGS_DB_PATH, or default."""
    if db_path is not None:
        resolved = Path(db_path)
    else:
        env_path = os.environ.get("AIWATCHDOGS_DB_PATH")
        resolved = Path(env_path) if env_path else DEFAULT_DB_REL_PATH
    if str(resolved) != ":memory:":
        resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


class Database:
    """Thread-safe SQLite (WAL) database wrapper with clean transaction scopes."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = resolve_db_path(db_path)
        self._lock = threading.RLock()
        self._conn = self._open_connection()
        self.init_schema()

    def _open_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            timeout=30.0,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        return conn

    def init_schema(self) -> None:
        """Create all Hub tables and indexes idempotently."""
        with self._lock:
            self._conn.executescript(SCHEMA_SQL)
            self._conn.commit()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Acquire write lock and yield a transactional SQLite connection."""
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        """Acquire lock and yield SQLite connection for consistent reads."""
        with self._lock:
            yield self._conn

    def health_check(self) -> bool:
        """Verify SQLite connectivity and WAL mode."""
        try:
            with self.read() as conn:
                row = conn.execute("SELECT 1 AS ok").fetchone()
                return bool(row and row["ok"] == 1)
        except sqlite3.Error:
            return False

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._lock:
            self._conn.close()
