"""Dual-backend database layer (Neon Postgres + SQLite WAL) with versioned migrations.

Automatically uses PostgreSQL (via psycopg v3) when ``DATABASE_URL`` or ``POSTGRES_URL``
is configured in the environment (or passed explicitly), and falls back to thread-safe
SQLite WAL mode for offline local development and pytest suites.

When running in production (``VERCEL=1`` or ``AIWATCHDOGS_REQUIRE_POSTGRES=1``) without
an explicit local ``db_path`` override, refuses to silently fall back to ephemeral
instance-local SQLite unless ``AIWATCHDOGS_ALLOW_EPHEMERAL_SQLITE=1`` is explicitly set.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import os
import queue
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

logger = logging.getLogger(__name__)

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover - psycopg[binary] is a core dependency
    psycopg = None  # type: ignore[assignment]
    dict_row = None  # type: ignore[assignment]

DEFAULT_DB_REL_PATH = Path("data/aiwatchdogs.db")
VERCEL_DB_PATH = Path("/tmp/aiwatchdogs-hub/aiwatchdogs.db")

HUB_TABLES: frozenset[str] = frozenset(
    {
        "schema_migrations",
        "seed_state",
        "public_sources",
        "missions",
        "agents",
        "evidence_artifacts",
        "tasks",
        "research_batches",
        "findings",
        "finding_corrections",
        "peer_verifications",
        "rti_drafts",
        "rti_draft_versions",
        "whispers",
        "events",
        "score_events",
        "sponsors",
        "treasury_ledger",
        "rate_limit_buckets",
        "summary_cache",
        "github_verification_challenges",
        "plain_texts",
        "plain_spend",
    }
)

_SAFE_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
_LOOPBACK_PG_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", ""})
_WEAK_SSLMODES = frozenset({"disable", "allow", "prefer"})

PG_SCHEMA_ADVISORY_LOCK_KEY = 84729104
PG_SEED_ADVISORY_LOCK_KEY = 84729105
PG_PROJECT_SYNC_ADVISORY_LOCK_KEY = 84729106
LATEST_SCHEMA_VERSION = 18

DB_INTEGRITY_ERRORS: tuple[type[BaseException], ...] = (
    (sqlite3.IntegrityError, psycopg.errors.IntegrityError)
    if psycopg is not None
    else (sqlite3.IntegrityError,)
)

_PG_TRANSIENT_ERRORS: tuple[type[BaseException], ...] = (
    (psycopg.OperationalError, psycopg.InterfaceError)
    if psycopg is not None
    else ()
)

RESEARCH_BATCHES_SQL = """
CREATE TABLE IF NOT EXISTS research_batches (
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    lease_id TEXT NOT NULL,
    batch_number INTEGER NOT NULL,
    report_json TEXT NOT NULL,
    PRIMARY KEY (task_id, lease_id, batch_number)
);
"""

SUMMARY_CACHE_SQL = """
CREATE TABLE IF NOT EXISTS summary_cache (
    cache_key TEXT PRIMARY KEY,
    version_stamp TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

GITHUB_VERIFICATION_CHALLENGES_SQL = """
CREATE TABLE IF NOT EXISTS github_verification_challenges (
    agent_id TEXT PRIMARY KEY REFERENCES agents(id) ON DELETE CASCADE,
    handle TEXT NOT NULL,
    challenge_token TEXT NOT NULL,
    method TEXT NOT NULL DEFAULT 'github_gist',
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

PLAIN_TEXTS_SQL = """
CREATE TABLE IF NOT EXISTS plain_texts (
    key TEXT PRIMARY KEY,
    source_hash TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    rejected_json TEXT NOT NULL DEFAULT '{}',
    complete INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

PLAIN_SPEND_SQL = """
CREATE TABLE IF NOT EXISTS plain_spend (
    day TEXT PRIMARY KEY,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    calls INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
"""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS seed_state (
    key TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS public_sources (
    id TEXT PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    pillar TEXT NOT NULL,
    authority TEXT NOT NULL,
    base_url TEXT NOT NULL,
    official_publisher TEXT NOT NULL DEFAULT '',
    source_landing_page TEXT NOT NULL DEFAULT '',
    mirror_name TEXT NOT NULL DEFAULT '',
    retrieval_url TEXT NOT NULL DEFAULT '',
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
    data_mode TEXT NOT NULL DEFAULT 'live_public',
    status TEXT NOT NULL DEFAULT 'active',
    priority INTEGER NOT NULL DEFAULT 1,
    total_tasks INTEGER NOT NULL DEFAULT 0,
    completed_tasks INTEGER NOT NULL DEFAULT 0,
    verified_findings_count INTEGER NOT NULL DEFAULT 0,
    cleared_count INTEGER NOT NULL DEFAULT 0,
    cleared_findings_count INTEGER NOT NULL DEFAULT 0,
    unevaluable_count INTEGER NOT NULL DEFAULT 0,
    monetary_audited_inr REAL NOT NULL DEFAULT 0.0,
    monetary_unit TEXT NOT NULL DEFAULT 'inr',
    financial_quantity_type TEXT NOT NULL DEFAULT '',
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
    revoked_at TEXT,
    token_rotated_at TEXT,
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
    official_publisher TEXT NOT NULL DEFAULT '',
    source_landing_page TEXT NOT NULL DEFAULT '',
    mirror_name TEXT NOT NULL DEFAULT '',
    retrieval_url TEXT NOT NULL DEFAULT '',
    secondary_url TEXT,
    secondary_sha256 TEXT,
    local_path TEXT,
    file_format TEXT NOT NULL,
    sha256 TEXT,
    byte_size INTEGER NOT NULL DEFAULT 0,
    is_demo_sample INTEGER NOT NULL DEFAULT 0,
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
    lease_started_at TEXT,
    lease_expires_at TEXT,
    available_at TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 3,
    progress_pct INTEGER NOT NULL DEFAULT 0,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    output_json TEXT,
    settlement_idempotency_key TEXT,
    settlement_response_json TEXT,
    dataset_key TEXT NOT NULL DEFAULT '',
    financial_quantity_type TEXT NOT NULL DEFAULT '',
    monetary_audited_inr REAL NOT NULL DEFAULT 0.0,
    reported_tokens BIGINT,
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
    source_id TEXT,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    editorial_interpretation TEXT NOT NULL DEFAULT '',
    methodology_code TEXT NOT NULL,
    entity_name TEXT NOT NULL,
    jurisdiction TEXT NOT NULL,
    fiscal_year TEXT NOT NULL,
    fiscal_year_start TEXT,
    fiscal_year_end TEXT,
    monetary_exposure_inr REAL NOT NULL DEFAULT 0.0,
    severity TEXT NOT NULL DEFAULT 'medium',
    anomaly_metrics_json TEXT NOT NULL DEFAULT '{}',
    rule_triggered TEXT NOT NULL DEFAULT '',
    engine_version TEXT NOT NULL DEFAULT '',
    ruleset_version TEXT NOT NULL DEFAULT '',
    as_of_date TEXT,
    source_locator_json TEXT NOT NULL DEFAULT '{}',
    provenance_json TEXT NOT NULL DEFAULT '{}',
    source_url TEXT NOT NULL DEFAULT '',
    artifact_sha256 TEXT,
    secondary_sha256 TEXT,
    dataset_path TEXT NOT NULL DEFAULT '',
    is_demo_sample INTEGER NOT NULL DEFAULT 0,
    citations_json TEXT NOT NULL DEFAULT '[]',
    benign_explanations_json TEXT NOT NULL DEFAULT '[]',
    suggested_authority TEXT NOT NULL DEFAULT '',
    submitted_by TEXT NOT NULL,
    submitted_by_agent_id TEXT REFERENCES agents(id),
    agent_trace_json TEXT NOT NULL DEFAULT '{}',
    legal_lint_passed INTEGER NOT NULL DEFAULT 1,
    pii_scrubbed INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'peer_review',
    editorial_status TEXT NOT NULL DEFAULT 'none',
    quarantined INTEGER NOT NULL DEFAULT 0,
    quarantine_reason TEXT NOT NULL DEFAULT '',
    verification_score REAL NOT NULL DEFAULT 0.0,
    confirm_count INTEGER NOT NULL DEFAULT 0,
    dispute_count INTEGER NOT NULL DEFAULT 0,
    required_quorum INTEGER NOT NULL DEFAULT 3,
    created_at TEXT NOT NULL,
    verified_at TEXT
);

CREATE TABLE IF NOT EXISTS finding_corrections (
    id TEXT PRIMARY KEY,
    finding_id TEXT NOT NULL REFERENCES findings(id),
    correction_type TEXT NOT NULL,
    previous_status TEXT,
    new_status TEXT,
    summary TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    source_evidence_url TEXT,
    reviewed_by TEXT NOT NULL DEFAULT 'editorial-review',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS peer_verifications (
    id TEXT PRIMARY KEY,
    finding_id TEXT NOT NULL REFERENCES findings(id),
    verifier_node_id TEXT NOT NULL REFERENCES agents(id),
    contributor_id TEXT NOT NULL,
    verifier_handle TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT '',
    model_id TEXT NOT NULL DEFAULT '',
    review_type TEXT NOT NULL DEFAULT 'computational_reproduction',
    verdict TEXT NOT NULL,
    checks_json TEXT NOT NULL DEFAULT '{}',
    reproduced_metrics_json TEXT NOT NULL DEFAULT '{}',
    manifest_json TEXT NOT NULL DEFAULT '{}',
    confidence REAL,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(finding_id, contributor_id)
);

CREATE TABLE IF NOT EXISTS rti_drafts (
    id TEXT PRIMARY KEY,
    finding_id TEXT UNIQUE NOT NULL REFERENCES findings(id),
    version INTEGER NOT NULL DEFAULT 1,
    public_authority TEXT NOT NULL,
    cpio_designation TEXT NOT NULL,
    rti_portal_url TEXT NOT NULL DEFAULT '',
    jurisdiction_level TEXT NOT NULL DEFAULT 'central',
    state_name TEXT,
    filing_channel TEXT NOT NULL DEFAULT 'central_online_portal',
    fee_note TEXT NOT NULL DEFAULT '',
    central_portal_warning TEXT NOT NULL DEFAULT '',
    statutory_sections_json TEXT NOT NULL DEFAULT '["Sec 6(1)", "Sec 2(f)", "Sec 4(1)(b)"]',
    subject TEXT NOT NULL,
    body_text TEXT NOT NULL,
    word_count INTEGER NOT NULL,
    requested_records_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'draft',
    editorial_notice TEXT NOT NULL DEFAULT '',
    filed_date TEXT,
    due_date_30d TEXT,
    official_reply_artifact_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS rti_draft_versions (
    id TEXT PRIMARY KEY,
    rti_draft_id TEXT NOT NULL REFERENCES rti_drafts(id),
    finding_id TEXT NOT NULL REFERENCES findings(id),
    version INTEGER NOT NULL,
    public_authority TEXT NOT NULL,
    cpio_designation TEXT NOT NULL,
    rti_portal_url TEXT NOT NULL DEFAULT '',
    jurisdiction_level TEXT NOT NULL DEFAULT 'central',
    filing_channel TEXT NOT NULL DEFAULT 'central_online_portal',
    subject TEXT NOT NULL,
    body_text TEXT NOT NULL,
    word_count INTEGER NOT NULL,
    requested_records_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'draft',
    editorial_notice TEXT NOT NULL DEFAULT '',
    change_note TEXT NOT NULL DEFAULT '',
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

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    finding_id TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rate_limit_buckets (
    bucket_key TEXT PRIMARY KEY,
    window_start INTEGER NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS score_events (
    id TEXT PRIMARY KEY,
    agent_id TEXT REFERENCES agents(id),
    agent_handle TEXT NOT NULL,
    event_type TEXT NOT NULL,
    points INTEGER NOT NULL,
    ref_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sponsors (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    handle_or_org TEXT,
    tier TEXT NOT NULL DEFAULT 'citizen_backer',
    amount_inr REAL NOT NULL DEFAULT 0.0,
    in_kind_value_inr REAL NOT NULL DEFAULT 0.0,
    compute_credits_desc TEXT NOT NULL DEFAULT '',
    pledge_status TEXT NOT NULL DEFAULT 'pledged',
    reconciled_receipt_ref TEXT,
    url TEXT,
    message TEXT NOT NULL DEFAULT '',
    featured INTEGER NOT NULL DEFAULT 0,
    is_demo_entry INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS treasury_ledger (
    id TEXT PRIMARY KEY,
    entry_date TEXT NOT NULL,
    entry_type TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('inflow', 'outflow')),
    settlement_status TEXT NOT NULL DEFAULT 'pledged',
    category TEXT NOT NULL,
    counterparty TEXT NOT NULL,
    description TEXT NOT NULL,
    amount_inr REAL NOT NULL,
    compute_credit_units TEXT NOT NULL DEFAULT '',
    receipt_ref TEXT NOT NULL,
    reconciled_receipt_ref TEXT,
    reconciled_at TEXT,
    sponsor_id TEXT,
    is_demo_entry INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_status_type ON tasks(status, task_type);
CREATE INDEX IF NOT EXISTS idx_tasks_status_lease_exp ON tasks(status, lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_tasks_mission_slug ON tasks(mission_slug);
CREATE INDEX IF NOT EXISTS idx_tasks_target_finding ON tasks(target_finding_id);
CREATE INDEX IF NOT EXISTS idx_tasks_available_at ON tasks(status, available_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_tasks_active_claim_finding ON tasks(target_finding_id, claimed_by_handle) WHERE status = 'claimed' AND target_finding_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_findings_status ON findings(status);
CREATE INDEX IF NOT EXISTS idx_findings_mission ON findings(mission_slug);
CREATE INDEX IF NOT EXISTS idx_findings_pillar ON findings(pillar);
CREATE INDEX IF NOT EXISTS idx_findings_methodology ON findings(methodology_code);
CREATE INDEX IF NOT EXISTS idx_findings_quarantined ON findings(quarantined);
CREATE INDEX IF NOT EXISTS idx_findings_submitted_by ON findings(submitted_by);
CREATE INDEX IF NOT EXISTS idx_corrections_finding ON finding_corrections(finding_id, created_at);
CREATE INDEX IF NOT EXISTS idx_peer_verif_finding ON peer_verifications(finding_id);
CREATE INDEX IF NOT EXISTS idx_peer_verif_contributor ON peer_verifications(contributor_id);
CREATE INDEX IF NOT EXISTS idx_peer_verif_handle ON peer_verifications(verifier_handle);
CREATE INDEX IF NOT EXISTS idx_rti_versions_finding ON rti_draft_versions(finding_id, version);
CREATE INDEX IF NOT EXISTS idx_whispers_created ON whispers(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_whispers_finding ON whispers(finding_id);
CREATE INDEX IF NOT EXISTS idx_whispers_handle_ts ON whispers(agent_handle, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_type_id ON events(event_type, id);
CREATE INDEX IF NOT EXISTS idx_events_finding_id ON events(finding_id);
CREATE INDEX IF NOT EXISTS idx_score_events_handle_ts ON score_events(agent_handle, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_score_events_created ON score_events(created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_score_events_dedup ON score_events(agent_handle, event_type, ref_id) WHERE ref_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_agents_api_token_hash ON agents(api_token_hash);
CREATE INDEX IF NOT EXISTS idx_agents_score ON agents(compute_score DESC);
CREATE INDEX IF NOT EXISTS idx_ledger_date ON treasury_ledger(entry_date DESC);
CREATE INDEX IF NOT EXISTS idx_rate_limit_updated ON rate_limit_buckets(updated_at);
CREATE INDEX IF NOT EXISTS idx_tasks_mission_status_type ON tasks(mission_id, status, task_type);
CREATE INDEX IF NOT EXISTS idx_tasks_claim_queue ON tasks(status, task_type, available_at, created_at, id);
CREATE INDEX IF NOT EXISTS idx_tasks_mission_slug_status ON tasks(mission_slug, status, task_type);
CREATE INDEX IF NOT EXISTS idx_tasks_status_updated ON tasks(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_tasks_claimed_agent_status ON tasks(claimed_by_agent_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_mission_completed_amt ON tasks(mission_id, status, monetary_audited_inr);
CREATE INDEX IF NOT EXISTS idx_findings_mission_status_quar ON findings(mission_id, status, quarantined);
""" + RESEARCH_BATCHES_SQL + SUMMARY_CACHE_SQL + GITHUB_VERIFICATION_CHALLENGES_SQL + PLAIN_TEXTS_SQL + PLAIN_SPEND_SQL

# Named editorial corrections for the 4 confirmed defective published dossiers from the
# 24 November 2026 public audit. Applied idempotently during migration v4 if those IDs exist.
LIVE_DOSSIER_EDITORIAL_CORRECTIONS: tuple[dict[str, Any], ...] = (
    {
        "finding_id": "fnd-2151675fe8f4",
        "correction_id": "cor-audit-2026-11-24-fnd-2151675fe8f4",
        "correction_type": "retraction",
        "new_status": "retracted",
        "editorial_status": "retracted",
        "reviewed_by": "editorial-audit-2026-11-24",
        "summary": (
            "Retracted from verified: ₹58,47,347 crore is FY 2024–25 14-day Treasury Bill "
            "repayments (Figure 2.6, printed p. 7), not an unresolved CAG audit impact."
        ),
        "details": (
            "Independent review of CAG Union Report No. 6 of 2026 (SHA-256 "
            "21ab04ae408b6f413b3e0e334ed1fe6005907a19ffb0db1d8a9bf9a603ab01fa, PDF page 27 / "
            "printed page 7, Figure 2.6) confirmed that ₹58,47,347 crore represents FY 2024–25 "
            "repayments of 14-day Treasury Bills. Paragraph 2.4 discusses receipts trends and "
            "supplies neither the attributed audit impact nor dated evidence of an Action Taken "
            "Note pending two years. The legacy parser selected a nearby figure and defaulted "
            "pending age to two years. Shared RTI draft placed on editorial hold."
        ),
        "source_evidence_url": (
            "https://d1jmc0u14z2tmp.cloudfront.net/uploads/download_audit_report/2026/"
            "6of2026UGEng-069ce496f1158e0.22682438.pdf#page=27"
        ),
        "rti_editorial_notice": (
            "EDITORIAL HOLD (2026-11-24): Underlying dossier retracted because ₹58,47,347 crore "
            "is FY 2024–25 14-day Treasury Bill repayments (Fig. 2.6, printed p. 7), not an "
            "unresolved CAG audit paragraph impact. Do not file this draft as-is."
        ),
    },
    {
        "finding_id": "fnd-25c6fbd61b32",
        "correction_id": "cor-audit-2026-11-24-fnd-25c6fbd61b32",
        "correction_type": "retraction",
        "new_status": "retracted",
        "editorial_status": "corrected",
        "reviewed_by": "editorial-audit-2026-11-24",
        "summary": (
            "Retracted same-day cluster claim: 113 Assam NHM rows share an OCDS release date "
            "(2023-06-21) and list tender estimates, not same-day contract awards."
        ),
        "details": (
            "Independent review of the checksum-matched Assam OCDS v2 CSV (commit "
            "1e12e516748130c15398b2501fb092bfa7413941) confirmed that the 113 distinct National "
            "Health Mission tenders totalling ₹52.93 crore contain tender estimates rather than "
            "award values and lack tender publication and award dates. The legacy loader mapped "
            "the shared OCDS release date (21 June 2023) into award_date, creating a false 0-day "
            "cluster even though the rows span 23 distinct bid-opening dates (20 July 2021 to "
            "10 April 2023), and applied July 2024 central GFR thresholds to earlier Assam state "
            "records. Shared RTI draft placed on editorial hold."
        ),
        "source_evidence_url": (
            "https://github.com/CivicDataLab/assam-tenders-data/blob/"
            "1e12e516748130c15398b2501fb092bfa7413941/data/ProcessedData/ocds-mapped-data/v2/"
            "ocds_mapped_data_fiscal_year_2016_2022_v2.csv"
        ),
        "rti_editorial_notice": (
            "EDITORIAL HOLD (2026-11-24): Same-day order-splitting claim retracted because the "
            "shared date 2023-06-21 is an OCDS release date rather than an award date and amounts "
            "are tender estimates. Do not file this draft as a same-day award cluster."
        ),
    },
    {
        "finding_id": "fnd-b6d573296cef",
        "correction_id": "cor-audit-2026-11-24-fnd-b6d573296cef",
        "correction_type": "editorial_review",
        "new_status": "under_review",
        "editorial_status": "under_review",
        "reviewed_by": "editorial-audit-2026-11-24",
        "summary": (
            "Withdrawn from verified for editorial review: Assam Tourism Demand 65 variance "
            "mixed unrelated agencies/heads and compared different fiscal years."
        ),
        "details": (
            "Independent review of the pinned Assam Budget OBDS FY 2022–23 workbook (commit "
            "f4965c2d92c02d5ea34319b7d42c78ed1cd03da1) confirmed that rows labelled Tourism "
            "include unrelated agencies and account heads (e.g. Sheet1 row 12566 Power Department "
            "major head 4801) and that the importer compared last-audited-year actuals against "
            "previous-year estimates from different fiscal periods. Withdrawn from verified "
            "status pending reconciliation to signed grant tables by account, authority, unit, "
            "and fiscal year. Shared RTI draft placed on editorial hold."
        ),
        "source_evidence_url": (
            "https://github.com/CivicDataLab/Assam-Budget-OBDS/blob/"
            "f4965c2d92c02d5ea34319b7d42c78ed1cd03da1/Budget%20OBDS/assam_expenditure_22-23.xlsx"
        ),
        "rti_editorial_notice": (
            "EDITORIAL HOLD (2026-11-24): Assam Tourism Demand 65 variance withdrawn pending "
            "reconciliation of mixed agency/head rows and fiscal-year columns against signed "
            "Assam grant tables. Do not file this draft with the ₹11,299.26 Cr variance figure."
        ),
    },
    {
        "finding_id": "fnd-bbc65f1fb61a",
        "correction_id": "cor-audit-2026-11-24-fnd-bbc65f1fb61a",
        "correction_type": "correction",
        "new_status": "under_review",
        "editorial_status": "corrected",
        "reviewed_by": "editorial-audit-2026-11-24",
        "summary": (
            "Withdrawn from verified / PAC claim corrected: missing PAC explanation was triggered "
            "using unobserved budget estimates and filing status defaulted to 0/false."
        ),
        "details": (
            "Independent review of the Krishna General Education dossier confirmed that the "
            "Public Accounts Committee (PAC) missing-explanation alert was computed from "
            "₹125.70 crore of actual monthly treasury spend where budgeted estimates and PAC "
            "filing status were unobserved in the source CSV and defaulted to zero/false. The "
            "Q4 expenditure share may be reviewed independently, while the missing-PAC claim is "
            "withdrawn pending official grant estimates and filing records. Shared RTI draft "
            "placed on editorial hold."
        ),
        "source_evidence_url": (
            "https://github.com/CivicDataLab/child-budget-tracker/blob/"
            "ff03978dbc64fc6eba95fe6ad931b074c52df3c0/data/processed_data/"
            "andhra_pradesh_child_treasury.csv"
        ),
        "rti_editorial_notice": (
            "EDITORIAL HOLD (2026-11-24): Missing PAC explanation claim withdrawn because "
            "estimates and filing status were unobserved in the source treasury table. State "
            "RTI filings for Andhra Pradesh must also be directed to the state SPIO channel, "
            "not the central rtionline.gov.in portal."
        ),
    },
)

_AUTOINC_RE = re.compile(r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b", re.IGNORECASE)
_INSERT_OR_IGNORE_RE = re.compile(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\b", re.IGNORECASE)
_DDL_REAL_RE = re.compile(r"^\s*(?:CREATE|ALTER)\s+TABLE\b", re.IGNORECASE)
_REAL_TYPE_RE = re.compile(r"\bREAL\b")


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
    """Serialize a Python object to strict RFC-8259 compact JSON (disallowing NaN/Infinity)."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def redact_database_url(dsn: str | None) -> str | None:
    """Mask password credentials in a PostgreSQL DSN before logging, repr, or inspection."""
    if not dsn:
        return None
    try:
        parsed = urlsplit(dsn)
        if not parsed.hostname:
            return "***"
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        port_part = f":{parsed.port}" if parsed.port else ""
        user_part = f"{parsed.username}:***@" if parsed.username else ""
        return urlunsplit((parsed.scheme, f"{user_part}{host}{port_part}", parsed.path, "", ""))
    except Exception:
        return "***"


def enforce_pg_sslmode(dsn: str) -> str:
    """Enforce PostgreSQL TLS (sslmode >= require) on non-loopback hosts."""
    normalized = "postgresql://" + dsn[len("postgres://") :] if dsn.startswith("postgres://") else dsn
    parsed = urlsplit(normalized)
    host = (parsed.hostname or "").strip().lower()
    is_loopback = host in _LOOPBACK_PG_HOSTS or host.endswith(".localhost")
    allow_insecure = os.environ.get("AIWATCHDOGS_ALLOW_INSECURE_PG", "").strip().lower() in {"1", "true", "yes"}
    if is_loopback or allow_insecure:
        return normalized

    query_items = parse_qsl(parsed.query, keep_blank_values=True)
    sslmode_vals = [v.strip().lower() for k, v in query_items if k.lower() == "sslmode"]
    if sslmode_vals:
        if any(mode in _WEAK_SSLMODES for mode in sslmode_vals):
            raise RuntimeError(
                f"Refusing insecure PostgreSQL sslmode ({sslmode_vals[-1]!r}) for non-loopback host {host!r}. "
                "Use sslmode=require or verify-full."
            )
        return normalized

    query_items.append(("sslmode", "require"))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query_items), parsed.fragment))


def is_postgres_url(value: str | Path | None) -> bool:
    """Return True if value looks like a PostgreSQL connection URL."""
    if not value or not isinstance(value, str):
        return False
    lowered = value.strip().lower()
    return lowered.startswith("postgres://") or lowered.startswith("postgresql://")


def is_postgres_required() -> bool:
    """Return True when production deployment requires durable PostgreSQL storage."""
    if os.environ.get("AIWATCHDOGS_ALLOW_EPHEMERAL_SQLITE", "").strip().lower() in {"1", "true", "yes"}:
        return False
    if os.environ.get("AIWATCHDOGS_REQUIRE_POSTGRES", "").strip().lower() in {"1", "true", "yes"}:
        return True
    return bool(os.environ.get("VERCEL"))


def resolve_database_url(database_url: str | None = None) -> str | None:
    """Resolve PostgreSQL URL from explicit argument or DATABASE_URL / POSTGRES_URL env vars."""
    if database_url is not None:
        cleaned = database_url.strip()
        return cleaned if is_postgres_url(cleaned) else None
    for env_key in ("DATABASE_URL", "POSTGRES_URL"):
        val = (os.environ.get(env_key) or "").strip()
        if is_postgres_url(val):
            return val
    return None


def resolve_db_path(db_path: str | Path | None = None) -> Path:
    """Resolve SQLite DB path from argument, AIWATCHDOGS_DB_PATH, Vercel /tmp, or local default."""
    if db_path is not None:
        resolved = Path(db_path)
    else:
        env_path = os.environ.get("AIWATCHDOGS_DB_PATH")
        if env_path:
            resolved = Path(env_path)
        elif os.environ.get("VERCEL"):
            resolved = VERCEL_DB_PATH
        else:
            resolved = DEFAULT_DB_REL_PATH
    if str(resolved) != ":memory:":
        resolved.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if resolved == VERCEL_DB_PATH or str(resolved.parent).startswith("/tmp/aiwatchdogs"):
            try:
                os.chmod(resolved.parent, 0o700)
            except OSError:
                pass
    return resolved


def split_sql_statements(script_sql: str) -> list[str]:
    """Split a multi-statement SQL script safely without breaking on semicolons inside quotes/comments."""
    statements: list[str] = []
    buf: list[str] = []
    in_single = False
    in_double = False
    in_line_comment = False
    in_block_comment = False
    i = 0
    n = len(script_sql)
    while i < n:
        ch = script_sql[i]
        nxt = script_sql[i + 1] if i + 1 < n else ""
        if in_line_comment:
            buf.append(ch)
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            buf.append(ch)
            if ch == "*" and nxt == "/":
                buf.append(nxt)
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if in_single:
            buf.append(ch)
            if ch == "'" and nxt == "'":
                buf.append(nxt)
                i += 2
                continue
            if ch == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            buf.append(ch)
            if ch == '"' and nxt == '"':
                buf.append(nxt)
                i += 2
                continue
            if ch == '"':
                in_double = False
            i += 1
            continue

        if ch == "-" and nxt == "-":
            buf.append(ch)
            buf.append(nxt)
            in_line_comment = True
            i += 2
            continue
        if ch == "/" and nxt == "*":
            buf.append(ch)
            buf.append(nxt)
            in_block_comment = True
            i += 2
            continue
        if ch == "'":
            in_single = True
            buf.append(ch)
            i += 1
            continue
        if ch == '"':
            in_double = True
            buf.append(ch)
            i += 1
            continue
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            i += 1
            continue

        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def translate_sql_for_postgres(sql: str, *, parameterized: bool = False) -> str | None:
    """Translate SQLite-style SQL into PostgreSQL-compatible SQL.

    Returns ``None`` for SQLite-only statements (such as ``PRAGMA``) that should be skipped.
    Replaces ``?`` placeholders with ``%s`` only outside quotes and comments.
    """
    stripped = sql.strip()
    if not stripped:
        return None
    if stripped.upper().startswith("PRAGMA"):
        return None

    # Handle SQLite DDL dialects
    if _DDL_REAL_RE.match(stripped):
        stripped = _AUTOINC_RE.sub("BIGSERIAL PRIMARY KEY", stripped)
        stripped = _REAL_TYPE_RE.sub("DOUBLE PRECISION", stripped)

    # Handle INSERT OR IGNORE INTO ... -> INSERT INTO ... ON CONFLICT DO NOTHING
    if _INSERT_OR_IGNORE_RE.match(stripped):
        has_semicolon = stripped.endswith(";")
        core = stripped[:-1].rstrip() if has_semicolon else stripped
        core = _INSERT_OR_IGNORE_RE.sub("INSERT INTO", core, count=1)
        if "ON CONFLICT" not in core.upper():
            core = f"{core} ON CONFLICT DO NOTHING"
        stripped = f"{core};" if has_semicolon else core

    if not parameterized:
        return stripped

    # Scan character-by-character so literal '?' inside quotes/comments stays untouched,
    # while every literal '%' is escaped as '%%' for psycopg3 pyformat safety.
    out: list[str] = []
    in_single = False
    in_double = False
    in_line_comment = False
    in_block_comment = False
    i = 0
    n = len(stripped)
    while i < n:
        ch = stripped[i]
        nxt = stripped[i + 1] if i + 1 < n else ""
        if in_line_comment:
            out.append("%%" if ch == "%" else ch)
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            out.append("%%" if ch == "%" else ch)
            if ch == "*" and nxt == "/":
                out.append(nxt)
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if in_single:
            if ch == "'" and nxt == "'":
                out.append("''")
                i += 2
                continue
            out.append("%%" if ch == "%" else ch)
            if ch == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            if ch == '"' and nxt == '"':
                out.append('""')
                i += 2
                continue
            out.append("%%" if ch == "%" else ch)
            if ch == '"':
                in_double = False
            i += 1
            continue

        if ch == "-" and nxt == "-":
            out.append("--")
            in_line_comment = True
            i += 2
            continue
        if ch == "/" and nxt == "*":
            out.append("/*")
            in_block_comment = True
            i += 2
            continue
        if ch == "'":
            in_single = True
            out.append(ch)
            i += 1
            continue
        if ch == '"':
            in_double = True
            out.append(ch)
            i += 1
            continue
        if ch == "%":
            out.append("%%")
            i += 1
            continue
        if ch == "?":
            out.append("%s")
            i += 1
            continue

        out.append(ch)
        i += 1

    return "".join(out)


class _NoOpCursor:
    """Empty cursor returned when skipping SQLite-only PRAGMA statements on Postgres."""

    rowcount: int = 0

    def fetchone(self) -> None:
        return None

    def fetchall(self) -> list[dict[str, Any]]:
        return []


class _PgConnectionAdapter:
    """Thin adapter exposing sqlite3.Connection-compatible execute/executescript over a pooled psycopg v3 conn."""

    def __init__(self, db: Database, conn: Any) -> None:
        self._db = db
        self._conn = conn
        self._stmt_count = 0

    @property
    def conn(self) -> Any:
        return self._conn

    def reset_stmt_count(self) -> None:
        self._stmt_count = 0

    def _active_conn(self) -> Any:
        if self._conn is None or self._conn.closed or getattr(self._conn, "broken", False):
            self._conn = self._db._replace_pg_connection(self._conn)
        return self._conn

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> Any:
        has_params = params is not None and len(params) > 0
        translated = translate_sql_for_postgres(sql, parameterized=has_params)
        if translated is None:
            return _NoOpCursor()

        conn = self._active_conn()
        try:
            if has_params:
                cursor = conn.execute(translated, tuple(params))  # type: ignore[arg-type]
            else:
                cursor = conn.execute(translated)
            self._stmt_count += 1
            return cursor
        except _PG_TRANSIENT_ERRORS:
            # Neon serverless connections can close while idle; transparently reconnect
            # and retry once if no prior statement has executed in this unit of work.
            if self._stmt_count > 0:
                raise
            conn = self._db._replace_pg_connection(self._conn)
            self._conn = conn
            if has_params:
                cursor = conn.execute(translated, tuple(params))  # type: ignore[arg-type]
            else:
                cursor = conn.execute(translated)
            self._stmt_count += 1
            return cursor

    def executemany(self, sql: str, params_seq: Sequence[Sequence[Any]]) -> Any:
        rows = [tuple(p) for p in params_seq]
        if not rows:
            return _NoOpCursor()
        translated = translate_sql_for_postgres(sql, parameterized=True)
        if translated is None:
            return _NoOpCursor()

        conn = self._active_conn()
        try:
            with conn.cursor() as cur:
                cur.executemany(translated, rows)
            self._stmt_count += 1
            return _NoOpCursor()
        except _PG_TRANSIENT_ERRORS:
            if self._stmt_count > 0:
                raise
            conn = self._db._replace_pg_connection(self._conn)
            self._conn = conn
            with conn.cursor() as cur:
                cur.executemany(translated, rows)
            self._stmt_count += 1
            return _NoOpCursor()

    def executescript(self, script_sql: str) -> None:
        for stmt in split_sql_statements(script_sql):
            self.execute(stmt)

    def commit(self) -> None:
        conn = self._conn
        if conn is not None and not conn.closed:
            conn.commit()

    def rollback(self) -> None:
        conn = self._conn
        if conn is not None and not conn.closed:
            conn.rollback()


# ---------------------------------------------------------------------------
# Versioned Idempotent Migrations (SQLite + PostgreSQL parity)
# ---------------------------------------------------------------------------

_V2_NEW_COLUMNS: tuple[tuple[str, str, str], ...] = (
    # public_sources
    ("public_sources", "official_publisher", "TEXT NOT NULL DEFAULT ''"),
    ("public_sources", "source_landing_page", "TEXT NOT NULL DEFAULT ''"),
    ("public_sources", "mirror_name", "TEXT NOT NULL DEFAULT ''"),
    ("public_sources", "retrieval_url", "TEXT NOT NULL DEFAULT ''"),
    # missions
    ("missions", "cleared_findings_count", "INTEGER NOT NULL DEFAULT 0"),
    ("missions", "unevaluable_count", "INTEGER NOT NULL DEFAULT 0"),
    ("missions", "monetary_unit", "TEXT NOT NULL DEFAULT 'inr'"),
    ("missions", "financial_quantity_type", "TEXT NOT NULL DEFAULT ''"),
    # evidence_artifacts
    ("evidence_artifacts", "official_publisher", "TEXT NOT NULL DEFAULT ''"),
    ("evidence_artifacts", "source_landing_page", "TEXT NOT NULL DEFAULT ''"),
    ("evidence_artifacts", "mirror_name", "TEXT NOT NULL DEFAULT ''"),
    ("evidence_artifacts", "retrieval_url", "TEXT NOT NULL DEFAULT ''"),
    ("evidence_artifacts", "secondary_url", "TEXT"),
    ("evidence_artifacts", "secondary_sha256", "TEXT"),
    # tasks
    ("tasks", "available_at", "TEXT"),
    ("tasks", "retry_count", "INTEGER NOT NULL DEFAULT 0"),
    ("tasks", "max_retries", "INTEGER NOT NULL DEFAULT 3"),
    ("tasks", "settlement_idempotency_key", "TEXT"),
    ("tasks", "settlement_response_json", "TEXT"),
    ("tasks", "dataset_key", "TEXT NOT NULL DEFAULT ''"),
    ("tasks", "financial_quantity_type", "TEXT NOT NULL DEFAULT ''"),
    ("tasks", "monetary_audited_inr", "REAL NOT NULL DEFAULT 0.0"),
    # findings
    ("findings", "source_id", "TEXT"),
    ("findings", "editorial_interpretation", "TEXT NOT NULL DEFAULT ''"),
    ("findings", "fiscal_year_start", "TEXT"),
    ("findings", "fiscal_year_end", "TEXT"),
    ("findings", "engine_version", "TEXT NOT NULL DEFAULT ''"),
    ("findings", "ruleset_version", "TEXT NOT NULL DEFAULT ''"),
    ("findings", "as_of_date", "TEXT"),
    ("findings", "source_locator_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("findings", "provenance_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("findings", "secondary_sha256", "TEXT"),
    ("findings", "editorial_status", "TEXT NOT NULL DEFAULT 'none'"),
    ("findings", "quarantined", "INTEGER NOT NULL DEFAULT 0"),
    ("findings", "quarantine_reason", "TEXT NOT NULL DEFAULT ''"),
    # peer_verifications
    ("peer_verifications", "review_type", "TEXT NOT NULL DEFAULT 'computational_reproduction'"),
    ("peer_verifications", "manifest_json", "TEXT NOT NULL DEFAULT '{}'"),
    # rti_drafts
    ("rti_drafts", "version", "INTEGER NOT NULL DEFAULT 1"),
    ("rti_drafts", "jurisdiction_level", "TEXT NOT NULL DEFAULT 'central'"),
    ("rti_drafts", "state_name", "TEXT"),
    ("rti_drafts", "filing_channel", "TEXT NOT NULL DEFAULT 'central_online_portal'"),
    ("rti_drafts", "fee_note", "TEXT NOT NULL DEFAULT ''"),
    ("rti_drafts", "central_portal_warning", "TEXT NOT NULL DEFAULT ''"),
    ("rti_drafts", "editorial_notice", "TEXT NOT NULL DEFAULT ''"),
    ("rti_drafts", "updated_at", "TEXT"),
    # sponsors
    ("sponsors", "in_kind_value_inr", "REAL NOT NULL DEFAULT 0.0"),
    ("sponsors", "pledge_status", "TEXT NOT NULL DEFAULT 'pledged'"),
    ("sponsors", "reconciled_receipt_ref", "TEXT"),
    # treasury_ledger
    ("treasury_ledger", "settlement_status", "TEXT NOT NULL DEFAULT 'pledged'"),
    ("treasury_ledger", "reconciled_receipt_ref", "TEXT"),
    ("treasury_ledger", "reconciled_at", "TEXT"),
    ("treasury_ledger", "sponsor_id", "TEXT"),
)

_V2_NEW_TABLES_AND_INDEXES_SQL = """
CREATE TABLE IF NOT EXISTS seed_state (
    key TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS finding_corrections (
    id TEXT PRIMARY KEY,
    finding_id TEXT NOT NULL REFERENCES findings(id),
    correction_type TEXT NOT NULL,
    previous_status TEXT,
    new_status TEXT,
    summary TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    source_evidence_url TEXT,
    reviewed_by TEXT NOT NULL DEFAULT 'editorial-review',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rti_draft_versions (
    id TEXT PRIMARY KEY,
    rti_draft_id TEXT NOT NULL REFERENCES rti_drafts(id),
    finding_id TEXT NOT NULL REFERENCES findings(id),
    version INTEGER NOT NULL,
    public_authority TEXT NOT NULL,
    cpio_designation TEXT NOT NULL,
    rti_portal_url TEXT NOT NULL DEFAULT '',
    jurisdiction_level TEXT NOT NULL DEFAULT 'central',
    filing_channel TEXT NOT NULL DEFAULT 'central_online_portal',
    subject TEXT NOT NULL,
    body_text TEXT NOT NULL,
    word_count INTEGER NOT NULL,
    requested_records_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'draft',
    editorial_notice TEXT NOT NULL DEFAULT '',
    change_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    finding_id TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rate_limit_buckets (
    bucket_key TEXT PRIMARY KEY,
    window_start INTEGER NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS score_events (
    id TEXT PRIMARY KEY,
    agent_id TEXT REFERENCES agents(id),
    agent_handle TEXT NOT NULL,
    event_type TEXT NOT NULL,
    points INTEGER NOT NULL,
    ref_id TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_available_at ON tasks(status, available_at);
CREATE INDEX IF NOT EXISTS idx_findings_quarantined ON findings(quarantined);
CREATE INDEX IF NOT EXISTS idx_corrections_finding ON finding_corrections(finding_id, created_at);
CREATE INDEX IF NOT EXISTS idx_rti_versions_finding ON rti_draft_versions(finding_id, version);
CREATE INDEX IF NOT EXISTS idx_events_type_id ON events(event_type, id);
CREATE INDEX IF NOT EXISTS idx_score_events_handle_ts ON score_events(agent_handle, created_at DESC);
"""


def _execute_statements(conn: Any, sql_script: str) -> None:
    for stmt in split_sql_statements(sql_script):
        conn.execute(stmt)


def _get_table_columns(conn: Any, table: str, *, is_postgres: bool) -> set[str]:
    if table not in HUB_TABLES:
        raise ValueError(f"Table '{table}' is not in HUB_TABLES allowlist.")
    if is_postgres:
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = ?",
            (table,),
        ).fetchall()
        return {str(r["column_name"]) for r in rows}
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(r["name"]) for r in rows}


def _ensure_column(
    conn: Any,
    table: str,
    column: str,
    col_def: str,
    *,
    is_postgres: bool,
    column_cache: dict[str, set[str]],
) -> None:
    if table not in HUB_TABLES:
        raise ValueError(f"Table '{table}' is not in HUB_TABLES allowlist.")
    if not _SAFE_IDENT_RE.fullmatch(column):
        raise ValueError(f"Unsafe SQL column identifier: {column!r}")
    cols = column_cache.get(table)
    if cols is None:
        cols = _get_table_columns(conn, table, is_postgres=is_postgres)
        column_cache[table] = cols
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
        cols.add(column)


def _migrate_v1_baseline(conn: Any, *, is_postgres: bool) -> None:
    _ = is_postgres
    # Create all tables first; indexes referencing v2 columns are created in v2 after
    # ALTER TABLE adds any missing columns on upgraded pre-v2 databases.
    for stmt in split_sql_statements(SCHEMA_SQL):
        if stmt.upper().startswith("CREATE TABLE"):
            conn.execute(stmt)


def _migrate_v2_audit_remediation_schema(conn: Any, *, is_postgres: bool) -> None:
    for stmt in split_sql_statements(_V2_NEW_TABLES_AND_INDEXES_SQL):
        if stmt.upper().startswith("CREATE TABLE"):
            conn.execute(stmt)
    col_cache: dict[str, set[str]] = {}
    for table, column, col_def in _V2_NEW_COLUMNS:
        _ensure_column(conn, table, column, col_def, is_postgres=is_postgres, column_cache=col_cache)
    if is_postgres:
        # Allow nullable unrecorded confidence & SHA-256 on upgraded PostgreSQL databases
        for alter_sql in (
            "ALTER TABLE peer_verifications ALTER COLUMN confidence DROP NOT NULL",
            "ALTER TABLE peer_verifications ALTER COLUMN confidence DROP DEFAULT",
            "ALTER TABLE evidence_artifacts ALTER COLUMN sha256 DROP NOT NULL",
            "ALTER TABLE findings ALTER COLUMN artifact_sha256 DROP NOT NULL",
        ):
            conn.execute(alter_sql)
    else:
        # On upgraded pre-v2 SQLite DBs, rebuild leaf table peer_verifications if confidence is NOT NULL
        pv_info = conn.execute("PRAGMA table_info(peer_verifications)").fetchall()
        conf_col = next((r for r in pv_info if str(r["name"]) == "confidence"), None)
        if conf_col is not None and int(conf_col["notnull"]) == 1:
            conn.execute("ALTER TABLE peer_verifications RENAME TO _peer_verifications_legacy")
            conn.execute(
                """
                CREATE TABLE peer_verifications (
                    id TEXT PRIMARY KEY,
                    finding_id TEXT NOT NULL REFERENCES findings(id),
                    verifier_node_id TEXT NOT NULL REFERENCES agents(id),
                    contributor_id TEXT NOT NULL,
                    verifier_handle TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT '',
                    model_id TEXT NOT NULL DEFAULT '',
                    review_type TEXT NOT NULL DEFAULT 'computational_reproduction',
                    verdict TEXT NOT NULL,
                    checks_json TEXT NOT NULL DEFAULT '{}',
                    reproduced_metrics_json TEXT NOT NULL DEFAULT '{}',
                    manifest_json TEXT NOT NULL DEFAULT '{}',
                    confidence REAL,
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE(finding_id, contributor_id)
                )
                """
            )
            conn.execute(
                """
                INSERT INTO peer_verifications (
                    id, finding_id, verifier_node_id, contributor_id, verifier_handle,
                    provider, model_id, review_type, verdict, checks_json,
                    reproduced_metrics_json, manifest_json, confidence, notes, created_at
                )
                SELECT
                    id, finding_id, verifier_node_id, contributor_id, verifier_handle,
                    provider, model_id, review_type, verdict, checks_json,
                    reproduced_metrics_json, manifest_json, confidence, notes, created_at
                FROM _peer_verifications_legacy
                """
            )
            conn.execute("DROP TABLE _peer_verifications_legacy")
    # Create v1/v2 indexes now that v2 tables and columns exist (v7 indexes run in migration v7)
    _v7_index_names = (
        "idx_tasks_status_lease_exp",
        "uq_tasks_active_claim_finding",
        "idx_findings_submitted_by",
        "idx_peer_verif_contributor",
        "idx_peer_verif_handle",
        "idx_whispers_finding",
        "idx_whispers_handle_ts",
        "idx_events_finding_id",
        "idx_score_events_created",
        "uq_score_events_dedup",
        "uq_agents_api_token_hash",
        "idx_rate_limit_updated",
    )
    for stmt in split_sql_statements(SCHEMA_SQL):
        if stmt.upper().startswith("CREATE INDEX") and not any(idx in stmt for idx in _v7_index_names):
            conn.execute(stmt)


def _migrate_v3_reconcile_treasury_and_scores(conn: Any, *, is_postgres: bool) -> None:
    _ = is_postgres
    # 1. Reconcile legacy treasury/sponsor rows (including rows defaulted to 'pledged' by v2)
    conn.execute(
        """
        UPDATE treasury_ledger
        SET settlement_status = CASE
            WHEN direction = 'outflow' THEN 'disbursed'
            WHEN entry_type = 'compute_grant' THEN 'in_kind_committed'
            WHEN reconciled_receipt_ref IS NOT NULL AND reconciled_receipt_ref != '' THEN 'received'
            ELSE 'pledged'
        END
        WHERE settlement_status IS NULL
           OR settlement_status = ''
           OR (direction = 'outflow' AND settlement_status = 'pledged')
           OR (entry_type = 'compute_grant' AND settlement_status = 'pledged')
           OR (direction = 'inflow' AND reconciled_receipt_ref IS NOT NULL AND reconciled_receipt_ref != ''
               AND settlement_status = 'pledged')
           OR (direction = 'inflow' AND (reconciled_receipt_ref IS NULL OR reconciled_receipt_ref = '')
               AND (category = 'Sponsor Pledge' OR receipt_ref LIKE 'RCPT-LIVE-%'))
        """
    )
    conn.execute(
        """
        UPDATE sponsors
        SET pledge_status = CASE
            WHEN reconciled_receipt_ref IS NOT NULL AND reconciled_receipt_ref != '' THEN 'received'
            WHEN amount_inr = 0 AND compute_credits_desc != '' THEN 'in_kind_committed'
            ELSE 'pledged'
        END
        WHERE pledge_status IS NULL
           OR pledge_status = ''
           OR (reconciled_receipt_ref IS NOT NULL AND reconciled_receipt_ref != '' AND pledge_status = 'pledged')
           OR (amount_inr = 0 AND compute_credits_desc != '' AND pledge_status = 'pledged')
        """
    )

    # 2. Backfill timestamped score_events once from historical activity if score_events is empty
    se_cnt_row = conn.execute("SELECT COUNT(*) AS c FROM score_events").fetchone()
    if se_cnt_row and int(se_cnt_row["c"]) > 0:
        return

    agent_by_handle = {
        str(r["handle"]): str(r["id"])
        for r in conn.execute("SELECT id, handle FROM agents").fetchall()
    }
    if not agent_by_handle:
        return

    for f in conn.execute(
        "SELECT id, submitted_by, submitted_by_agent_id, status, created_at, verified_at FROM findings"
    ).fetchall():
        handle = str(f["submitted_by"] or "")
        if not handle:
            continue
        aid = f["submitted_by_agent_id"] or agent_by_handle.get(handle)
        conn.execute(
            """
            INSERT OR IGNORE INTO score_events (id, agent_id, agent_handle, event_type, points, ref_id, created_at)
            VALUES (?, ?, ?, 'finding_submitted', 10, ?, ?)
            """,
            (f"se-bf-fnd-{f['id']}", aid, handle, f["id"], f["created_at"]),
        )
        if f["status"] in ("verified", "rti_filed"):
            conn.execute(
                """
                INSERT OR IGNORE INTO score_events (id, agent_id, agent_handle, event_type, points, ref_id, created_at)
                VALUES (?, ?, ?, 'finding_verified_author_bonus', 20, ?, ?)
                """,
                (
                    f"se-bf-ver-{f['id']}",
                    aid,
                    handle,
                    f["id"],
                    f["verified_at"] or f["created_at"],
                ),
            )

    for pv in conn.execute(
        "SELECT id, verifier_node_id, verifier_handle, created_at FROM peer_verifications"
    ).fetchall():
        conn.execute(
            """
            INSERT OR IGNORE INTO score_events (id, agent_id, agent_handle, event_type, points, ref_id, created_at)
            VALUES (?, ?, ?, 'peer_verification', 15, ?, ?)
            """,
            (f"se-bf-pv-{pv['id']}", pv["verifier_node_id"], pv["verifier_handle"], pv["id"], pv["created_at"]),
        )

    for t in conn.execute(
        "SELECT id, claimed_by_agent_id, claimed_by_handle, output_json, completed_at, updated_at "
        "FROM tasks WHERE status = 'completed' AND output_json IS NOT NULL"
    ).fetchall():
        out = loads_json(t["output_json"], {})
        if isinstance(out, dict) and out.get("outcome") == "cleared" and t["claimed_by_handle"]:
            conn.execute(
                """
                INSERT OR IGNORE INTO score_events (id, agent_id, agent_handle, event_type, points, ref_id, created_at)
                VALUES (?, ?, ?, 'cleared_scan', 10, ?, ?)
                """,
                (
                    f"se-bf-clr-{t['id']}",
                    t["claimed_by_agent_id"],
                    t["claimed_by_handle"],
                    t["id"],
                    t["completed_at"] or t["updated_at"],
                ),
            )


def _migrate_v4_editorial_corrections_live_dossiers(conn: Any, *, is_postgres: bool) -> None:
    _ = is_postgres
    from aiwatchdogs.core.rti import generate_rti_draft

    now = utc_now_iso()
    affected_missions: set[str] = set()
    correction_by_finding = {item["finding_id"]: item for item in LIVE_DOSSIER_EDITORIAL_CORRECTIONS}

    # 1. Record named editorial corrections and withdraw the 4 defective live dossiers from 'verified'
    for item in LIVE_DOSSIER_EDITORIAL_CORRECTIONS:
        fid = item["finding_id"]
        finding = conn.execute("SELECT * FROM findings WHERE id = ?", (fid,)).fetchone()
        if finding is None:
            continue

        prev_status = str(finding["status"] or "")
        conn.execute(
            """
            INSERT OR IGNORE INTO finding_corrections (
                id, finding_id, correction_type, previous_status, new_status,
                summary, details, source_evidence_url, reviewed_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item["correction_id"],
                fid,
                item["correction_type"],
                prev_status,
                item["new_status"],
                item["summary"],
                item["details"],
                item["source_evidence_url"],
                item["reviewed_by"],
                now,
            ),
        )
        conn.execute(
            """
            UPDATE findings
            SET status = ?, editorial_status = ?
            WHERE id = ?
            """,
            (item["new_status"], item["editorial_status"], fid),
        )
        affected_missions.add(str(finding["mission_id"]))

    # 2. Re-resolve ALL existing rti_drafts against their parent finding's jurisdiction/state channel
    #    (so pre-existing State RTI drafts never stay pinned to Central https://rtionline.gov.in/),
    #    and apply editorial_hold + editorial_notice for any of the 4 corrected dossiers in one v1->v2 snapshot.
    existing_rtis = conn.execute(
        """
        SELECT rd.*,
               f.title AS f_title,
               f.summary AS f_summary,
               f.methodology_code AS f_methodology_code,
               f.entity_name AS f_entity_name,
               f.jurisdiction AS f_jurisdiction,
               f.fiscal_year AS f_fiscal_year,
               f.monetary_exposure_inr AS f_monetary_exposure_inr,
               f.rule_triggered AS f_rule_triggered,
               f.citations_json AS f_citations_json,
               f.suggested_authority AS f_suggested_authority
        FROM rti_drafts rd
        JOIN findings f ON f.id = rd.finding_id
        """
    ).fetchall()

    for rd in existing_rtis:
        fid = str(rd["finding_id"])
        finding_dict = {
            "id": fid,
            "title": rd["f_title"],
            "summary": rd["f_summary"],
            "methodology_code": rd["f_methodology_code"],
            "entity_name": rd["f_entity_name"],
            "jurisdiction": rd["f_jurisdiction"],
            "fiscal_year": rd["f_fiscal_year"],
            "monetary_exposure_inr": float(rd["f_monetary_exposure_inr"] or 0.0),
            "rule_triggered": rd["f_rule_triggered"],
            "citations": loads_json(rd["f_citations_json"], []),
            "suggested_authority": rd["f_suggested_authority"],
        }
        rebuilt = generate_rti_draft(finding_dict, public_authority=rd["public_authority"])
        new_cpio = str(rebuilt["cpio_designation"])
        new_portal = str(rebuilt.get("rti_portal_url") or "")
        new_jlevel = str(rebuilt.get("jurisdiction_level") or "central")
        new_state = rebuilt.get("state_name")
        new_channel = str(rebuilt.get("filing_channel") or "central_online_portal")
        new_fee_note = str(rebuilt.get("fee_rules") or rebuilt.get("fee_note") or "")
        new_cwarn = str(rebuilt.get("central_portal_warning") or "")
        new_subject = str(rebuilt["subject"])
        new_body = str(rebuilt["body_text"])
        new_wc = int(rebuilt["word_count"])
        new_records_json = dumps_json(rebuilt.get("requested_records") or [])

        corr_item = correction_by_finding.get(fid)
        if corr_item is not None:
            new_status = "editorial_hold"
            new_notice = corr_item["rti_editorial_notice"]
            change_note = corr_item["summary"]
        else:
            new_status = str(rd["status"] or "draft")
            new_notice = str(rd["editorial_notice"] or "")
            change_note = "Reconciled state/central RTI jurisdiction, SPIO/CPIO channel, and filing portal"

        needs_update = (
            corr_item is not None
            or (rd["rti_portal_url"] or "") != new_portal
            or (rd["jurisdiction_level"] or "") != new_jlevel
            or (rd["filing_channel"] or "") != new_channel
            or (rd["cpio_designation"] or "") != new_cpio
            or (rd["fee_note"] or "") != new_fee_note
            or (rd["central_portal_warning"] or "") != new_cwarn
            or (rd["body_text"] or "") != new_body
        )
        if not needs_update:
            continue

        next_version = int(rd["version"] or 1) + 1
        conn.execute(
            """
            UPDATE rti_drafts
            SET cpio_designation = ?,
                rti_portal_url = ?,
                jurisdiction_level = ?,
                state_name = ?,
                filing_channel = ?,
                fee_note = ?,
                central_portal_warning = ?,
                subject = ?,
                body_text = ?,
                word_count = ?,
                requested_records_json = ?,
                status = ?,
                editorial_notice = ?,
                version = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                new_cpio,
                new_portal,
                new_jlevel,
                new_state,
                new_channel,
                new_fee_note,
                new_cwarn,
                new_subject,
                new_body,
                new_wc,
                new_records_json,
                new_status,
                new_notice,
                next_version,
                now,
                rd["id"],
            ),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO rti_draft_versions (
                id, rti_draft_id, finding_id, version, public_authority, cpio_designation,
                rti_portal_url, jurisdiction_level, filing_channel, subject, body_text,
                word_count, requested_records_json, status, editorial_notice, change_note, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"rtiv-audit-2026-11-24-{fid}",
                rd["id"],
                fid,
                next_version,
                rd["public_authority"],
                new_cpio,
                new_portal,
                new_jlevel,
                new_channel,
                new_subject,
                new_body,
                new_wc,
                new_records_json,
                new_status,
                new_notice,
                change_note,
                now,
            ),
        )

    for mid in affected_missions:
        cnt_row = conn.execute(
            """
            SELECT COUNT(*) AS c FROM findings
            WHERE mission_id = ? AND status IN ('verified', 'rti_filed') AND COALESCE(quarantined, 0) = 0
            """,
            (mid,),
        ).fetchone()
        verified_cnt = int(cnt_row["c"]) if cnt_row else 0
        conn.execute(
            "UPDATE missions SET verified_findings_count = ? WHERE id = ?",
            (verified_cnt, mid),
        )


_V5_PROJECT_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("missions", "posted_by_name", "TEXT NOT NULL DEFAULT ''"),
    ("missions", "posted_by_handle", "TEXT NOT NULL DEFAULT ''"),
    ("missions", "posted_by_url", "TEXT NOT NULL DEFAULT ''"),
    ("missions", "project_sources_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("missions", "project_scope_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("missions", "enlisted_pr_url", "TEXT NOT NULL DEFAULT ''"),
    ("missions", "is_community_project", "INTEGER NOT NULL DEFAULT 0"),
)

# The original eight hub-authored missions. Migration v5 archived them when the hub moved
# to team-curated projects; migration v9 removes them and everything recorded under them.
LEGACY_MISSION_IDS: tuple[str, ...] = tuple(f"mission-m{i}" for i in range(1, 9))


def legacy_missions_kept_active() -> bool:
    """Test/fork escape hatch: keep M1–M8 live (the hub's own test suite uses them as fixtures)."""
    return os.environ.get("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", "").strip().lower() in {"1", "true", "yes"}


def _migrate_v5_community_projects(conn: Any, *, is_postgres: bool) -> None:
    cache: dict[str, set[str]] = {}
    for table, column, col_def in _V5_PROJECT_COLUMNS:
        _ensure_column(conn, table, column, col_def, is_postgres=is_postgres, column_cache=cache)
    if legacy_missions_kept_active():
        return
    placeholders = ",".join("?" for _ in LEGACY_MISSION_IDS)
    conn.execute(
        f"UPDATE missions SET status = 'archived' WHERE id IN ({placeholders})",
        LEGACY_MISSION_IDS,
    )


_V6_CODING_AGENT_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("agents", "coding_agent", "TEXT NOT NULL DEFAULT ''"),
    ("agents", "coding_agent_model", "TEXT NOT NULL DEFAULT ''"),
    ("findings", "submitted_coding_agent", "TEXT NOT NULL DEFAULT ''"),
    ("findings", "submitted_coding_agent_model", "TEXT NOT NULL DEFAULT ''"),
    ("peer_verifications", "coding_agent", "TEXT NOT NULL DEFAULT ''"),
    ("peer_verifications", "coding_agent_model", "TEXT NOT NULL DEFAULT ''"),
)


def _migrate_v6_coding_agent_attribution(conn: Any, *, is_postgres: bool) -> None:
    from aiwatchdogs.core.coding_agents import _CANONICAL

    cache: dict[str, set[str]] = {}
    for table, column, col_def in _V6_CODING_AGENT_COLUMNS:
        _ensure_column(conn, table, column, col_def, is_postgres=is_postgres, column_cache=cache)
    # Backfill: agents that registered via the raw HTTP protocol put their coding agent in
    # `provider` (e.g. provider="antigravity", model="Gemini").
    for row in conn.execute("SELECT id, provider, model FROM agents WHERE coding_agent = ''").fetchall():
        canonical = _CANONICAL.get(str(row["provider"] or "").strip().lower())
        if canonical:
            conn.execute(
                "UPDATE agents SET coding_agent = ?, coding_agent_model = ? WHERE id = ?",
                (canonical, str(row["model"] or "")[:60], row["id"]),
            )
    conn.execute(
        """
        UPDATE findings SET
            submitted_coding_agent = COALESCE((SELECT a.coding_agent FROM agents a WHERE a.handle = findings.submitted_by), ''),
            submitted_coding_agent_model = COALESCE((SELECT a.coding_agent_model FROM agents a WHERE a.handle = findings.submitted_by), '')
        WHERE submitted_coding_agent = ''
        """
    )
    conn.execute(
        """
        UPDATE peer_verifications SET
            coding_agent = COALESCE((SELECT a.coding_agent FROM agents a WHERE a.handle = peer_verifications.verifier_handle), ''),
            coding_agent_model = COALESCE((SELECT a.coding_agent_model FROM agents a WHERE a.handle = peer_verifications.verifier_handle), '')
        WHERE coding_agent = ''
        """
    )


_V7_SECURITY_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("events", "finding_id", "TEXT"),
    ("agents", "revoked_at", "TEXT"),
    ("agents", "token_rotated_at", "TEXT"),
    ("tasks", "lease_started_at", "TEXT"),
)

_V7_PG_FINITE_CHECKS: tuple[tuple[str, str, str], ...] = (
    ("missions", "chk_missions_monetary_finite", "monetary_audited_inr >= 0 AND monetary_audited_inr < 'Infinity'::double precision AND monetary_audited_inr <> 'NaN'::double precision"),
    ("tasks", "chk_tasks_monetary_finite", "monetary_audited_inr >= 0 AND monetary_audited_inr < 'Infinity'::double precision AND monetary_audited_inr <> 'NaN'::double precision"),
    ("findings", "chk_findings_exposure_finite", "monetary_exposure_inr >= 0 AND monetary_exposure_inr < 'Infinity'::double precision AND monetary_exposure_inr <> 'NaN'::double precision"),
    ("sponsors", "chk_sponsors_amount_finite", "amount_inr >= 0 AND amount_inr < 'Infinity'::double precision AND amount_inr <> 'NaN'::double precision"),
    ("sponsors", "chk_sponsors_inkind_finite", "in_kind_value_inr >= 0 AND in_kind_value_inr < 'Infinity'::double precision AND in_kind_value_inr <> 'NaN'::double precision"),
    ("treasury_ledger", "chk_ledger_amount_finite", "amount_inr >= 0 AND amount_inr < 'Infinity'::double precision AND amount_inr <> 'NaN'::double precision"),
)


def _migrate_v7_security_hardening(conn: Any, *, is_postgres: bool) -> None:
    """Migration v7: operational indexes, rate-limit table, events.finding_id, BIGINT events, finite CHECKs."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rate_limit_buckets (
            bucket_key TEXT PRIMARY KEY,
            window_start INTEGER NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )
        """
    )
    cache: dict[str, set[str]] = {}
    for table, column, col_def in _V7_SECURITY_COLUMNS:
        _ensure_column(conn, table, column, col_def, is_postgres=is_postgres, column_cache=cache)

    # Backfill events.finding_id from payload_json for existing rows so quarantine redaction is indexed.
    event_rows = conn.execute(
        "SELECT id, payload_json FROM events WHERE finding_id IS NULL AND payload_json LIKE '%finding_id%'"
    ).fetchall()
    for row in event_rows:
        payload = loads_json(row["payload_json"], {})
        if isinstance(payload, dict):
            fid = str(payload.get("finding_id") or "").strip()
            if fid:
                conn.execute("UPDATE events SET finding_id = ? WHERE id = ?", (fid, row["id"]))

    # Remove any historical duplicate score_events sharing (agent_handle, event_type, ref_id) before unique index.
    conn.execute(
        """
        DELETE FROM score_events
        WHERE ref_id IS NOT NULL
          AND id NOT IN (
              SELECT MIN(id) FROM score_events WHERE ref_id IS NOT NULL
              GROUP BY agent_handle, event_type, ref_id
          )
        """
    )

    if is_postgres:
        # Upgrade events.id from 32-bit SERIAL to 64-bit BIGINT/BIGSERIAL cleanly.
        col_row = conn.execute(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'events' AND column_name = 'id'"
        ).fetchone()
        if col_row and str(col_row["data_type"]).lower() != "bigint":
            conn.execute("ALTER TABLE events ALTER COLUMN id TYPE BIGINT")
        seq_row = conn.execute("SELECT pg_get_serial_sequence('events', 'id') AS seq").fetchone()
        if seq_row and seq_row["seq"]:
            seq_name = str(seq_row["seq"])
            conn.execute(f"ALTER SEQUENCE {seq_name} AS BIGINT")

        # Sanitize any legacy non-finite / negative values before adding finite non-negative CHECK constraints.
        for table, constraint_name, expr in _V7_PG_FINITE_CHECKS:
            col = expr.split()[0]
            conn.execute(
                f"UPDATE {table} SET {col} = 0.0 "
                f"WHERE {col} IS NULL OR {col} < 0 OR {col} = 'NaN'::double precision "
                f"OR {col} = 'Infinity'::double precision OR {col} = '-Infinity'::double precision"
            )
            exists_row = conn.execute(
                """
                SELECT 1 AS found
                FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                WHERE n.nspname = current_schema() AND t.relname = ? AND c.conname = ?
                """,
                (table, constraint_name),
            ).fetchone()
            if not exists_row:
                conn.execute(f"ALTER TABLE {table} ADD CONSTRAINT {constraint_name} CHECK ({expr})")

    # Create all baseline + security indexes (including unique partial indexes) idempotently.
    for stmt in split_sql_statements(SCHEMA_SQL):
        upper = stmt.upper()
        if upper.startswith("CREATE INDEX") or upper.startswith("CREATE UNIQUE INDEX"):
            conn.execute(stmt)


_V8_USAGE_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("tasks", "work_type", "TEXT NOT NULL DEFAULT ''"),
    ("tasks", "estimated_tokens", "BIGINT"),
    ("tasks", "estimate_basis", "TEXT NOT NULL DEFAULT ''"),
    ("tasks", "usage_json", "TEXT NOT NULL DEFAULT ''"),
    ("missions", "planned_tasks_json", "TEXT NOT NULL DEFAULT '[]'"),
)


def _migrate_v8_token_usage(conn: Any, *, is_postgres: bool) -> None:
    """Migration v8: token estimates and reported usage per task (additive columns only)."""
    cache: dict[str, set[str]] = {}
    for table, column, col_def in _V8_USAGE_COLUMNS:
        _ensure_column(conn, table, column, col_def, is_postgres=is_postgres, column_cache=cache)


def _migrate_v9_purge_legacy_missions(conn: Any, *, is_postgres: bool) -> None:
    """Migration v9: delete the original eight missions and everything recorded under them.

    Removes their findings (with peer verifications, corrections, RTI drafts, events, whispers
    and score events), tasks, evidence artifacts and the seeded source directory. Agents whose
    only work was on those missions are removed too; any other agent keeps its account and has
    its counters recomputed from what remains. Projects loaded from ``projects/`` are untouched.
    """
    _ = is_postgres
    if legacy_missions_kept_active():
        return
    from aiwatchdogs.hub.seed import PUBLIC_SOURCES

    ids = LEGACY_MISSION_IDS
    ph = ",".join("?" for _ in ids)
    legacy_findings = f"SELECT id FROM findings WHERE mission_id IN ({ph})"
    legacy_tasks = f"SELECT id FROM tasks WHERE mission_id IN ({ph})"
    legacy_verifs = f"SELECT id FROM peer_verifications WHERE finding_id IN ({legacy_findings})"

    touched: set[str] = set()
    for sql in (
        f"SELECT DISTINCT submitted_by_agent_id AS a FROM findings WHERE mission_id IN ({ph})",
        f"SELECT DISTINCT verifier_node_id AS a FROM peer_verifications WHERE finding_id IN ({legacy_findings})",
        f"SELECT DISTINCT claimed_by_agent_id AS a FROM tasks WHERE mission_id IN ({ph})",
    ):
        touched.update(str(r["a"]) for r in conn.execute(sql, ids).fetchall() if r["a"])

    for ref_sql in (legacy_findings, legacy_tasks, legacy_verifs):
        conn.execute(f"DELETE FROM score_events WHERE ref_id IN ({ref_sql})", ids)
    conn.execute(
        f"DELETE FROM whispers WHERE finding_id IN ({legacy_findings}) "
        f"OR mission_slug IN (SELECT slug FROM missions WHERE id IN ({ph}))",
        ids + ids,
    )
    for table in ("events", "rti_draft_versions", "rti_drafts", "finding_corrections", "peer_verifications"):
        conn.execute(f"DELETE FROM {table} WHERE finding_id IN ({legacy_findings})", ids)
    conn.execute(f"DELETE FROM findings WHERE mission_id IN ({ph})", ids)
    conn.execute(f"DELETE FROM tasks WHERE mission_id IN ({ph})", ids)
    conn.execute(f"DELETE FROM evidence_artifacts WHERE mission_id IN ({ph})", ids)
    conn.execute(f"DELETE FROM missions WHERE id IN ({ph})", ids)

    source_ids = tuple(str(src["id"]) for src in PUBLIC_SOURCES)
    if source_ids:
        sph = ",".join("?" for _ in source_ids)
        conn.execute(
            f"DELETE FROM public_sources WHERE id IN ({sph}) "
            "AND id NOT IN (SELECT source_id FROM findings WHERE source_id IS NOT NULL) "
            "AND id NOT IN (SELECT source_id FROM evidence_artifacts WHERE source_id IS NOT NULL)",
            source_ids,
        )
    conn.execute("DELETE FROM seed_state WHERE key = 'canonical_seed'")

    for agent_id in sorted(touched):
        row = conn.execute("SELECT handle FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if row is None:
            continue
        handle = str(row["handle"])

        def _count(sql: str, params: tuple[Any, ...]) -> int:
            got = conn.execute(sql, params).fetchone()
            return int(got["c"] or 0) if got else 0

        findings_left = _count("SELECT COUNT(*) AS c FROM findings WHERE submitted_by_agent_id = ?", (agent_id,))
        verifs_left = _count("SELECT COUNT(*) AS c FROM peer_verifications WHERE verifier_node_id = ?", (agent_id,))
        tasks_left = _count("SELECT COUNT(*) AS c FROM tasks WHERE claimed_by_agent_id = ?", (agent_id,))
        if findings_left == 0 and verifs_left == 0 and tasks_left == 0:
            conn.execute("DELETE FROM score_events WHERE agent_id = ? OR agent_handle = ?", (agent_id, handle))
            conn.execute("DELETE FROM whispers WHERE agent_handle = ?", (handle,))
            conn.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
            continue
        conn.execute(
            """
            UPDATE agents SET
                tasks_completed = (SELECT COUNT(*) FROM tasks WHERE claimed_by_agent_id = ? AND status = 'completed'),
                findings_submitted = ?,
                findings_verified = (SELECT COUNT(*) FROM findings WHERE submitted_by_agent_id = ? AND status = 'verified'),
                verifications_done = ?,
                compute_score = (SELECT COALESCE(SUM(points), 0) FROM score_events WHERE agent_handle = ?)
            WHERE id = ?
            """,
            (agent_id, findings_left, agent_id, verifs_left, handle, agent_id),
        )


def _migrate_v10_research_batches(conn: Any, *, is_postgres: bool) -> None:
    conn.execute(RESEARCH_BATCHES_SQL)


_V11_SWARM_SCALE_INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_tasks_mission_status_type ON tasks(mission_id, status, task_type)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_claim_queue ON tasks(status, task_type, available_at, created_at, id)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_mission_slug_status ON tasks(mission_slug, status, task_type)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_status_updated ON tasks(status, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_claimed_agent_status ON tasks(claimed_by_agent_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_mission_completed_amt ON tasks(mission_id, status, monetary_audited_inr)",
    "CREATE INDEX IF NOT EXISTS idx_findings_mission_status_quar ON findings(mission_id, status, quarantined)",
)


def _migrate_v11_swarm_scale_indexes(conn: Any, *, is_postgres: bool) -> None:
    """Migration v11: composite indexes for concurrent multi-agent claims and project/dashboard queries."""
    _ = is_postgres
    for stmt in _V11_SWARM_SCALE_INDEXES:
        conn.execute(stmt)


_V12_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("agents", "registration_key_hash", "TEXT"),
    ("tasks", "claim_request_id", "TEXT"),
)

_V12_INDEXES: tuple[str, ...] = (
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_tasks_claim_request ON tasks(claimed_by_agent_id, claim_request_id) WHERE status = 'claimed' AND claim_request_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_tasks_queued_pv_order ON tasks(mission_id, (COALESCE(available_at, created_at)), created_at, id) WHERE status = 'queued' AND task_type = 'peer_verify'",
    "CREATE INDEX IF NOT EXISTS idx_tasks_queued_scan_order ON tasks(mission_id, (COALESCE(available_at, created_at)), created_at, id) WHERE status = 'queued' AND task_type NOT IN ('peer_verify', 'research')",
)


def _migrate_v12_idempotent_claims_and_claim_order_indexes(conn: Any, *, is_postgres: bool) -> None:
    """Migration v12: durable claim/registration idempotency columns and partial claim-order indexes."""
    cache: dict[str, set[str]] = {}
    for table, column, col_def in _V12_COLUMNS:
        _ensure_column(conn, table, column, col_def, is_postgres=is_postgres, column_cache=cache)
    for stmt in _V12_INDEXES:
        conn.execute(stmt)


def _migrate_v13_egress_cache_and_token_totals(conn: Any, *, is_postgres: bool) -> None:
    """Migration v13: durable summary_cache table and denormalized tasks.reported_tokens for low-egress reads."""
    from aiwatchdogs.core.usage import tokens_used

    conn.execute(SUMMARY_CACHE_SQL)
    cache: dict[str, set[str]] = {}
    _ensure_column(conn, "tasks", "reported_tokens", "BIGINT", is_postgres=is_postgres, column_cache=cache)
    rows = conn.execute(
        "SELECT id, usage_json FROM tasks "
        "WHERE reported_tokens IS NULL AND usage_json IS NOT NULL AND usage_json != '' AND usage_json != '{}'"
    ).fetchall()
    updates: list[tuple[int, str]] = []
    for row in rows:
        spent = tokens_used(loads_json(row["usage_json"], {}))
        if spent is not None:
            updates.append((int(spent), str(row["id"])))
    if updates:
        conn.executemany("UPDATE tasks SET reported_tokens = ? WHERE id = ?", updates)


_V14_BATCH_SIZE = 500
_V14_DEFAULT_PV_INSTRUCTIONS = (
    "Independently load the pinned source dataset, verify primary & secondary SHA-256 "
    "digests, re-run deterministic calculations with the bound engine/ruleset/as-of date, "
    "validate neutrality & zero-PII, and submit explicit pass/fail/unrecorded checks."
)


def _migrate_v14_quorum3_cross_harness_backfill(conn: Any, *, is_postgres: bool) -> None:
    """Migration v14: raise default required_quorum to 3 and retroactively re-audit live findings.

    Batched & idempotent (<2s for 10k findings over Neon Postgres):
    - Sets Postgres column default ``findings.required_quorum`` to 3.
    - Re-evaluates all live non-quarantined findings in ``('verified', 'peer_review', 'raw_lead')``
      using the shared pairwise-independent ``(handle, coding_agent, coding_agent_model)`` rule.
    - Keeps findings with >=3 pairwise-independent confirms (and consensus >= 0.75) verified;
      returns findings with <3 pairwise-independent confirms to ``peer_review`` (``verified_at = NULL``)
      and queues only the missing ``peer_verify`` slots using server-side template cloning.
    - Recomputes ``missions.verified_findings_count`` and ``agents.findings_verified`` and clears
      ``summary_cache``.
    """
    from aiwatchdogs.hub.quorum import DEFAULT_REQUIRED_QUORUM, evaluate_pairwise_quorum

    if is_postgres:
        conn.execute(f"ALTER TABLE findings ALTER COLUMN required_quorum SET DEFAULT {DEFAULT_REQUIRED_QUORUM}")

    # Bulk read #1: compact finding headers for all live non-quarantined candidates
    findings = conn.execute(
        """
        SELECT id, mission_id, mission_slug, methodology_code, title,
               submitted_by, submitted_coding_agent, submitted_coding_agent_model,
               status, confirm_count, dispute_count, required_quorum, verification_score, verified_at
        FROM findings
        WHERE status IN ('verified', 'peer_review', 'raw_lead')
          AND COALESCE(quarantined, 0) = 0
        """
    ).fetchall()
    if not findings:
        return

    # Bulk read #2: all peer_verifications for candidate findings in deterministic order
    verifs_by_fid: dict[str, list[Any]] = {}
    for pv in conn.execute(
        """
        SELECT pv.id, pv.finding_id, pv.contributor_id, pv.verifier_handle,
               pv.verdict, pv.checks_json, pv.reproduced_metrics_json,
               pv.coding_agent, pv.coding_agent_model, pv.created_at
        FROM peer_verifications pv
        JOIN findings f ON f.id = pv.finding_id
        WHERE f.status IN ('verified', 'peer_review', 'raw_lead')
          AND COALESCE(f.quarantined, 0) = 0
        ORDER BY pv.finding_id ASC, pv.created_at ASC, pv.id ASC
        """
    ).fetchall():
        verifs_by_fid.setdefault(str(pv["finding_id"]), []).append(pv)

    # Bulk read #3: existing peer_verify task IDs, open counts, and preferred template task ID (no payload_json!)
    existing_pv_ids_by_fid: dict[str, set[str]] = {}
    open_pv_count_by_fid: dict[str, int] = {}
    template_task_id_by_fid: dict[str, str] = {}
    for t in conn.execute(
        """
        SELECT t.id, t.target_finding_id, t.status
        FROM tasks t
        JOIN findings f ON f.id = t.target_finding_id
        WHERE t.task_type = 'peer_verify'
          AND f.status IN ('verified', 'peer_review', 'raw_lead')
          AND COALESCE(f.quarantined, 0) = 0
        ORDER BY t.target_finding_id ASC,
                 CASE WHEN t.id LIKE 'tsk-pv-%' THEN 0 ELSE 1 END ASC,
                 t.created_at ASC,
                 t.id ASC
        """
    ).fetchall():
        fid = str(t["target_finding_id"])
        tid = str(t["id"])
        existing_pv_ids_by_fid.setdefault(fid, set()).add(tid)
        if str(t["status"] or "") in ("queued", "claimed"):
            open_pv_count_by_fid[fid] = open_pv_count_by_fid.get(fid, 0) + 1
        if fid not in template_task_id_by_fid:
            template_task_id_by_fid[fid] = tid

    now = utc_now_iso()
    # (fid, confirm_count, dispute_count, verification_score, new_status, clear_verified_flag)
    finding_updates: list[tuple[str, int, int, float, str, int]] = []
    # (new_task_id, template_task_id, new_title, excluded_handles_json, now_ts)
    cloned_task_rows: list[tuple[str, str, str, str, str]] = []
    # (fid, new_task_id, new_title, excluded_handles_json)
    fallback_task_specs: list[tuple[str, str, str, str]] = []
    any_status_changed = False

    for f in findings:
        fid = str(f["id"])
        cur_status = str(f["status"] or "")
        cur_confirm = int(f["confirm_count"] or 0)
        cur_dispute = int(f["dispute_count"] or 0)
        cur_quorum = int(f["required_quorum"] or 0)
        cur_score = float(f["verification_score"] or 0.0)
        cur_verified_at = f["verified_at"]

        qres = evaluate_pairwise_quorum(
            author_handle=f["submitted_by"],
            author_harness=f["submitted_coding_agent"],
            author_model=f["submitted_coding_agent_model"],
            votes=verifs_by_fid.get(fid, ()),
            loads_json_fn=loads_json,
        )
        valid_confirms = int(qres["qualified_confirms"])
        dispute_count = int(qres["dispute_count"])
        consensus = float(qres["consensus"])
        voter_handles: set[str] = set(qres["voter_handles"])

        passes_quorum = (
            not qres["has_pii_or_legal_flag"]
            and valid_confirms >= DEFAULT_REQUIRED_QUORUM
            and consensus >= 0.75
            and (dispute_count == 0 or valid_confirms >= DEFAULT_REQUIRED_QUORUM)
        )

        if passes_quorum:
            target_status = "verified" if cur_status == "verified" else cur_status
            clear_verified = 0
            needed = 0
        else:
            target_status = "peer_review"
            clear_verified = 1
            open_pv = open_pv_count_by_fid.get(fid, 0)
            needed = max(0, DEFAULT_REQUIRED_QUORUM - valid_confirms - open_pv)

        if target_status != cur_status:
            any_status_changed = True

        needs_update = (
            cur_quorum != DEFAULT_REQUIRED_QUORUM
            or target_status != cur_status
            or valid_confirms != cur_confirm
            or dispute_count != cur_dispute
            or abs(consensus - cur_score) > 1e-6
            or (clear_verified == 1 and cur_verified_at is not None)
        )
        if needs_update:
            finding_updates.append(
                (fid, valid_confirms, dispute_count, consensus, target_status, clear_verified)
            )

        if needed > 0:
            used_ids = existing_pv_ids_by_fid.setdefault(fid, set())
            excl_json = dumps_json(sorted(voter_handles))
            title_prefix = str(f["title"] or "")[:96]
            tmpl_id = template_task_id_by_fid.get(fid)
            slot = 1
            created = 0
            while created < needed:
                cand_id = f"tsk-pv-{fid}-{slot}"
                if cand_id not in used_ids:
                    used_ids.add(cand_id)
                    new_title = f"Peer Verify ({slot}/{DEFAULT_REQUIRED_QUORUM}): {title_prefix}"
                    if tmpl_id is not None:
                        cloned_task_rows.append((cand_id, tmpl_id, new_title, excl_json, now))
                    else:
                        fallback_task_specs.append((fid, cand_id, new_title, excl_json))
                    created += 1
                slot += 1

    # Bulk write #1: update findings in chunks of 500
    if finding_updates:
        if is_postgres:
            for i in range(0, len(finding_updates), _V14_BATCH_SIZE):
                chunk = finding_updates[i : i + _V14_BATCH_SIZE]
                values_sql = ", ".join("(?, ?, ?, ?, ?, ?)" for _ in chunk)
                flat_params: list[Any] = []
                for row_vals in chunk:
                    flat_params.extend(row_vals)
                conn.execute(
                    f"""
                    UPDATE findings AS f
                    SET required_quorum = {DEFAULT_REQUIRED_QUORUM},
                        confirm_count = CAST(v.confirm_count AS INTEGER),
                        dispute_count = CAST(v.dispute_count AS INTEGER),
                        verification_score = CAST(v.vscore AS DOUBLE PRECISION),
                        status = v.status,
                        verified_at = CASE WHEN CAST(v.clear_verified AS INTEGER) = 1 THEN NULL ELSE f.verified_at END
                    FROM (VALUES {values_sql}) AS v(fid, confirm_count, dispute_count, vscore, status, clear_verified)
                    WHERE f.id = v.fid
                    """,
                    flat_params,
                )
        else:
            conn.executemany(
                f"""
                UPDATE findings
                SET required_quorum = {DEFAULT_REQUIRED_QUORUM},
                    confirm_count = ?,
                    dispute_count = ?,
                    verification_score = ?,
                    status = ?,
                    verified_at = CASE WHEN ? = 1 THEN NULL ELSE verified_at END
                WHERE id = ?
                """,
                [
                    (confirm_c, dispute_c, vscore, st, clear_v, fid)
                    for fid, confirm_c, dispute_c, vscore, st, clear_v in finding_updates
                ],
            )

    # Bulk write #2: server-side clone of peer_verify tasks from existing template tasks (0 payload transfer)
    if cloned_task_rows:
        if is_postgres:
            for i in range(0, len(cloned_task_rows), _V14_BATCH_SIZE):
                chunk = cloned_task_rows[i : i + _V14_BATCH_SIZE]
                values_sql = ", ".join("(?, ?, ?, ?, ?)" for _ in chunk)
                flat_params = []
                for row_vals in chunk:
                    flat_params.extend(row_vals)
                conn.execute(
                    f"""
                    INSERT INTO tasks (
                        id, mission_id, mission_slug, methodology_code, task_type,
                        title, instructions, payload_json, target_finding_id,
                        excluded_handles_json, status, dataset_key, financial_quantity_type,
                        created_at, updated_at
                    )
                    SELECT
                        v.new_id, tmpl.mission_id, tmpl.mission_slug, tmpl.methodology_code, 'peer_verify',
                        v.new_title, tmpl.instructions, tmpl.payload_json, tmpl.target_finding_id,
                        v.excl_json, 'queued', tmpl.dataset_key, tmpl.financial_quantity_type,
                        v.now_ts, v.now_ts
                    FROM (VALUES {values_sql}) AS v(new_id, tmpl_id, new_title, excl_json, now_ts)
                    JOIN tasks tmpl ON tmpl.id = v.tmpl_id
                    ON CONFLICT(id) DO NOTHING
                    """,
                    flat_params,
                )
        else:
            conn.executemany(
                """
                INSERT INTO tasks (
                    id, mission_id, mission_slug, methodology_code, task_type,
                    title, instructions, payload_json, target_finding_id,
                    excluded_handles_json, status, dataset_key, financial_quantity_type,
                    created_at, updated_at
                )
                SELECT
                    ?, tmpl.mission_id, tmpl.mission_slug, tmpl.methodology_code, 'peer_verify',
                    ?, tmpl.instructions, tmpl.payload_json, tmpl.target_finding_id,
                    ?, 'queued', tmpl.dataset_key, tmpl.financial_quantity_type,
                    ?, ?
                FROM tasks tmpl
                WHERE tmpl.id = ?
                ON CONFLICT(id) DO NOTHING
                """,
                [
                    (new_id, new_title, excl_json, ts, ts, tmpl_id)
                    for new_id, tmpl_id, new_title, excl_json, ts in cloned_task_rows
                ],
            )

    # Bulk write #3 (rare fallback): findings with no prior peer_verify task row to clone from
    if fallback_task_specs:
        needed_fids = sorted({fid for fid, _, _, _ in fallback_task_specs})
        full_by_fid: dict[str, Any] = {}
        for i in range(0, len(needed_fids), _V14_BATCH_SIZE):
            id_chunk = needed_fids[i : i + _V14_BATCH_SIZE]
            ph = ",".join("?" for _ in id_chunk)
            for row in conn.execute(
                f"""
                SELECT id, mission_id, mission_slug, methodology_code, title, summary,
                       entity_name, jurisdiction, fiscal_year, fiscal_year_start, fiscal_year_end,
                       monetary_exposure_inr, anomaly_metrics_json, rule_triggered,
                       engine_version, ruleset_version, as_of_date, source_locator_json,
                       provenance_json, source_url, artifact_id, artifact_sha256, secondary_sha256,
                       dataset_path, is_demo_sample, citations_json, benign_explanations_json,
                       submitted_by
                FROM findings
                WHERE id IN ({ph})
                """,
                id_chunk,
            ).fetchall():
                full_by_fid[str(row["id"])] = row

        fallback_insert_rows: list[tuple[Any, ...]] = []
        for fid, new_id, new_title, excl_json in fallback_task_specs:
            frow = full_by_fid.get(fid)
            if frow is None:
                continue
            prov = loads_json(frow["provenance_json"], {})
            if not isinstance(prov, dict):
                prov = {}
            retrieval_url = str(prov.get("retrieval_url") or frow["source_url"] or "")
            payload_obj = {
                "finding_id": fid,
                "submitted_by": str(frow["submitted_by"] or ""),
                "review_type": "computational_reproduction",
                "methodology_code": str(frow["methodology_code"] or ""),
                "dataset_url": retrieval_url,
                "retrieval_url": retrieval_url,
                "secondary_url": prov.get("secondary_url"),
                "secondary_sha256": frow["secondary_sha256"] or prov.get("secondary_sha256"),
                "dataset_path": str(frow["dataset_path"] or ""),
                "source_url": str(frow["source_url"] or ""),
                "source_landing_page": str(prov.get("source_landing_page") or frow["source_url"] or ""),
                "official_publisher": str(prov.get("official_publisher") or ""),
                "mirror_name": str(prov.get("mirror_name") or ""),
                "canonical_url": retrieval_url or str(frow["source_url"] or ""),
                "artifact_id": frow["artifact_id"],
                "artifact_sha256": frow["artifact_sha256"],
                "expected_sha256": frow["artifact_sha256"],
                "engine_version": str(frow["engine_version"] or ""),
                "ruleset_version": str(frow["ruleset_version"] or ""),
                "as_of_date": frow["as_of_date"],
                "source_locator": loads_json(frow["source_locator_json"], {}),
                "title": str(frow["title"] or ""),
                "summary": str(frow["summary"] or ""),
                "entity_name": str(frow["entity_name"] or ""),
                "jurisdiction": str(frow["jurisdiction"] or ""),
                "fiscal_year": str(frow["fiscal_year"] or ""),
                "fiscal_year_start": frow["fiscal_year_start"],
                "fiscal_year_end": frow["fiscal_year_end"],
                "monetary_exposure_inr": float(frow["monetary_exposure_inr"] or 0.0),
                "anomaly_metrics": loads_json(frow["anomaly_metrics_json"], {}),
                "rule_triggered": str(frow["rule_triggered"] or ""),
                "citations": loads_json(frow["citations_json"], []),
                "benign_explanations": loads_json(frow["benign_explanations_json"], []),
                "is_demo_sample": bool(frow["is_demo_sample"]),
            }
            fallback_insert_rows.append(
                (
                    new_id,
                    frow["mission_id"],
                    frow["mission_slug"],
                    frow["methodology_code"],
                    new_title,
                    _V14_DEFAULT_PV_INSTRUCTIONS,
                    dumps_json(payload_obj),
                    fid,
                    excl_json,
                    now,
                    now,
                )
            )

        if fallback_insert_rows:
            if is_postgres:
                for i in range(0, len(fallback_insert_rows), _V14_BATCH_SIZE):
                    chunk = fallback_insert_rows[i : i + _V14_BATCH_SIZE]
                    values_sql = ", ".join(
                        "(?, ?, ?, ?, 'peer_verify', ?, ?, ?, ?, ?, 'queued', '', '', ?, ?)"
                        for _ in chunk
                    )
                    flat_params = []
                    for row_vals in chunk:
                        flat_params.extend(row_vals)
                    conn.execute(
                        f"""
                        INSERT INTO tasks (
                            id, mission_id, mission_slug, methodology_code, task_type,
                            title, instructions, payload_json, target_finding_id,
                            excluded_handles_json, status, dataset_key, financial_quantity_type,
                            created_at, updated_at
                        ) VALUES {values_sql}
                        ON CONFLICT(id) DO NOTHING
                        """,
                        flat_params,
                    )
            else:
                conn.executemany(
                    """
                    INSERT INTO tasks (
                        id, mission_id, mission_slug, methodology_code, task_type,
                        title, instructions, payload_json, target_finding_id,
                        excluded_handles_json, status, dataset_key, financial_quantity_type,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'peer_verify', ?, ?, ?, ?, ?, 'queued', '', '', ?, ?)
                    ON CONFLICT(id) DO NOTHING
                    """,
                    fallback_insert_rows,
                )

    # Recompute denormalized counters & invalidate cached summaries when any finding status changed
    if any_status_changed or finding_updates or cloned_task_rows or fallback_task_specs:
        conn.execute(
            """
            UPDATE missions
            SET verified_findings_count = (
                SELECT COUNT(*) FROM findings f
                WHERE f.mission_id = missions.id
                  AND f.status IN ('verified', 'rti_filed')
                  AND COALESCE(f.quarantined, 0) = 0
            )
            """
        )
        conn.execute(
            """
            UPDATE agents
            SET findings_verified = (
                SELECT COUNT(*) FROM findings f
                WHERE f.submitted_by_agent_id = agents.id
                  AND f.status IN ('verified', 'rti_filed')
                  AND COALESCE(f.quarantined, 0) = 0
            )
            """
        )
        conn.execute("DELETE FROM summary_cache")


_V15_PEER_VERIFY_CLAIM_INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_tasks_claimed_target_finding ON tasks(target_finding_id) WHERE status = 'claimed' AND target_finding_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_findings_peer_review_mission ON findings(mission_id, id) WHERE status = 'peer_review' AND quarantined = 0",
    "CREATE INDEX IF NOT EXISTS idx_findings_unattributed_peer_review ON findings(status, quarantined, mission_id, submitted_coding_agent, submitted_coding_agent_model) WHERE status = 'peer_review' AND quarantined = 0 AND (TRIM(submitted_coding_agent) = '' OR TRIM(submitted_coding_agent_model) = '')",
)


def _migrate_v15_peer_verify_claim_indexes(conn: Any, *, is_postgres: bool) -> None:
    """Migration v15: partial indexes for O(1) active-lease conflict fetch and peer_verify claim fast-paths."""
    _ = is_postgres
    for stmt in _V15_PEER_VERIFY_CLAIM_INDEXES:
        conn.execute(stmt)


_V16_GITHUB_IDENTITY_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("agents", "github_verified", "INTEGER NOT NULL DEFAULT 0"),
    ("agents", "github_user_id", "TEXT"),
    ("agents", "github_verified_at", "TEXT"),
    ("agents", "github_verification_method", "TEXT NOT NULL DEFAULT ''"),
    ("agents", "registered_ip_hash", "TEXT NOT NULL DEFAULT ''"),
)

_V16_GITHUB_INDEXES: tuple[str, ...] = (
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_agents_github_user_id ON agents(github_user_id) WHERE github_user_id IS NOT NULL AND TRIM(github_user_id) != '' AND revoked_at IS NULL AND status != 'revoked'",
    "CREATE INDEX IF NOT EXISTS idx_agents_registered_ip_hash ON agents(registered_ip_hash) WHERE TRIM(registered_ip_hash) != ''",
    "CREATE INDEX IF NOT EXISTS idx_gh_challenges_expires ON github_verification_challenges(expires_at)",
)


def _migrate_v16_github_identity_and_sybil_guard(conn: Any, *, is_postgres: bool) -> None:
    """Migration v16: GitHub ownership proof columns/challenges and privacy-preserving Sybil IP hash."""
    col_cache: dict[str, set[str]] = {}
    for table, column, col_def in _V16_GITHUB_IDENTITY_COLUMNS:
        _ensure_column(conn, table, column, col_def, is_postgres=is_postgres, column_cache=col_cache)
    _execute_statements(conn, GITHUB_VERIFICATION_CHALLENGES_SQL)
    for stmt in _V16_GITHUB_INDEXES:
        conn.execute(stmt)





def _migrate_v17_plain_texts(conn: Any, *, is_postgres: bool) -> None:
    _execute_statements(conn, PLAIN_TEXTS_SQL)


def _migrate_v18_plain_spend(conn: Any, *, is_postgres: bool) -> None:
    _execute_statements(conn, PLAIN_SPEND_SQL)


MIGRATIONS: tuple[tuple[int, str, Callable[..., None]], ...] = (
    (1, "001_initial_schema", _migrate_v1_baseline),
    (2, "002_audit_remediation_schema", _migrate_v2_audit_remediation_schema),
    (3, "003_reconcile_treasury_and_scores", _migrate_v3_reconcile_treasury_and_scores),
    (4, "004_editorial_corrections_live_dossiers", _migrate_v4_editorial_corrections_live_dossiers),
    (5, "005_community_projects", _migrate_v5_community_projects),
    (6, "006_coding_agent_attribution", _migrate_v6_coding_agent_attribution),
    (7, "007_security_hardening", _migrate_v7_security_hardening),
    (8, "008_token_usage", _migrate_v8_token_usage),
    (9, "009_purge_legacy_missions", _migrate_v9_purge_legacy_missions),
    (10, "010_research_batches", _migrate_v10_research_batches),
    (11, "011_swarm_scale_indexes", _migrate_v11_swarm_scale_indexes),
    (12, "012_idempotent_claims_and_claim_order_indexes", _migrate_v12_idempotent_claims_and_claim_order_indexes),
    (13, "013_egress_cache_and_token_totals", _migrate_v13_egress_cache_and_token_totals),
    (14, "014_quorum3_cross_harness_backfill", _migrate_v14_quorum3_cross_harness_backfill),
    (15, "015_peer_verify_claim_indexes", _migrate_v15_peer_verify_claim_indexes),
    (16, "016_github_identity_and_sybil_guard", _migrate_v16_github_identity_and_sybil_guard),
    (17, "017_plain_texts", _migrate_v17_plain_texts),
    (18, "018_plain_spend", _migrate_v18_plain_spend),
)


class ReadOnlyDatabaseError(RuntimeError):
    """Raised when code tries to write through a read-only database."""


def is_read_only_deployment() -> bool:
    """Vercel previews share the live database, so they may only read it."""
    if os.environ.get("AIWATCHDOGS_READ_ONLY", "").strip().lower() in {"1", "true", "yes"}:
        return True
    return os.environ.get("VERCEL_ENV", "").strip().lower() in {"preview", "development"}


def _env_int_clamped(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        val = int(os.environ.get(name, str(default)).strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, min(val, maximum))


def _env_float_clamped(name: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        val = float(os.environ.get(name, str(default)).strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, min(val, maximum))


class Database:
    """Thread-safe dual-backend database wrapper (pooled Neon Postgres + SQLite WAL)."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        database_url: str | None = None,
        require_postgres: bool = False,
        read_only: bool = False,
    ) -> None:
        self.read_only = read_only
        self._lock = threading.RLock()
        self._tx_depth = 0
        self._conn: sqlite3.Connection | None = None
        self._pg_pool_max = _env_int_clamped("AIWATCHDOGS_PG_POOL_MAX", 8, minimum=1, maximum=32)
        self._pg_pool_timeout = _env_float_clamped("AIWATCHDOGS_PG_POOL_TIMEOUT", 15.0, minimum=1.0, maximum=60.0)
        self._pg_pool: queue.LifoQueue[Any] = queue.LifoQueue(maxsize=self._pg_pool_max)
        self._pg_pool_sem = threading.BoundedSemaphore(self._pg_pool_max)
        self._pg_pool_lock = threading.Lock()
        self._pg_all_conns: set[Any] = set()
        self._pg_closed = False
        self._local = threading.local()

        # Explicit local db_path always selects SQLite (so pytest tmp_path never hits Neon),
        # unless db_path itself is a postgres:// URL.
        if db_path is not None and is_postgres_url(db_path):
            pg_url: str | None = str(db_path).strip()
        elif database_url is not None:
            pg_url = resolve_database_url(database_url)
        elif db_path is None:
            pg_url = resolve_database_url(None)
        else:
            pg_url = None

        if pg_url:
            if psycopg is None:  # pragma: no cover
                raise RuntimeError(
                    "PostgreSQL DATABASE_URL is set, but 'psycopg[binary]' is not installed."
                )
            self.backend = "postgres"
            self.is_postgres = True
            self._database_url: str | None = enforce_pg_sslmode(pg_url)
            self.db_path: Path | None = None
            # Eagerly verify connectivity with one warm pooled connection on startup
            first_conn = self._open_pg_connection()
            self._pg_pool.put_nowait(first_conn)
        else:
            if db_path is None and (require_postgres or is_postgres_required()):
                raise RuntimeError(
                    "Durable PostgreSQL storage (DATABASE_URL or POSTGRES_URL) is required in "
                    "production (VERCEL=1 or AIWATCHDOGS_REQUIRE_POSTGRES=1). Refusing to fall "
                    "back to ephemeral local SQLite."
                )
            self.backend = "sqlite"
            self.is_postgres = False
            self._database_url = None
            self.db_path = resolve_db_path(db_path)
            self._conn = self._open_sqlite_connection()

        if not self.read_only:
            self.init_schema()
        elif self._conn is not None:
            self._conn.execute("PRAGMA query_only = ON")

    @property
    def database_url(self) -> str | None:
        """Return a credential-redacted view of the configured PostgreSQL DSN."""
        return redact_database_url(self._database_url)

    def __repr__(self) -> str:
        target = self.database_url if self.is_postgres else str(self.db_path)
        return f"Database(backend={self.backend!r}, target={target!r})"

    def _open_sqlite_connection(self) -> sqlite3.Connection:
        assert self.db_path is not None
        conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            timeout=30.0,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        if str(self.db_path) != ":memory:":
            for suffix in ("", "-wal", "-shm"):
                candidate = Path(f"{self.db_path}{suffix}")
                if candidate.exists():
                    try:
                        os.chmod(candidate, 0o600)
                    except OSError:
                        pass
        return conn

    def _open_pg_connection(self) -> Any:
        assert psycopg is not None and self._database_url is not None
        dsn = self._database_url
        # prepare_threshold=None is critical for Neon PgBouncer transaction pooling!
        conn = psycopg.connect(
            dsn,
            row_factory=dict_row,
            autocommit=False,
            prepare_threshold=None,
            connect_timeout=15,
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=5,
        )
        if self.read_only:
            conn.read_only = True  # every transaction is BEGIN READ ONLY; Postgres itself refuses writes
        with self._pg_pool_lock:
            self._pg_all_conns.add(conn)
        return conn

    def _discard_pg_connection(self, conn: Any) -> None:
        if conn is None:
            return
        with self._pg_pool_lock:
            self._pg_all_conns.discard(conn)
        if not getattr(conn, "closed", True):
            try:
                conn.close()
            except Exception:
                pass

    def _checkout_pg_connection(self) -> Any:
        if self._pg_closed:
            raise RuntimeError("Database pool is closed.")
        if not self._pg_pool_sem.acquire(timeout=self._pg_pool_timeout):
            raise RuntimeError(
                f"PostgreSQL connection pool exhausted (max={self._pg_pool_max}, timeout={self._pg_pool_timeout}s)."
            )
        try:
            while True:
                try:
                    conn = self._pg_pool.get_nowait()
                except queue.Empty:
                    return self._open_pg_connection()
                if conn is None or conn.closed or getattr(conn, "broken", False):
                    self._discard_pg_connection(conn)
                    continue
                return conn
        except Exception:
            self._pg_pool_sem.release()
            raise

    def _checkin_pg_connection(self, conn: Any) -> None:
        try:
            if (
                self._pg_closed
                or conn is None
                or conn.closed
                or getattr(conn, "broken", False)
            ):
                self._discard_pg_connection(conn)
                return
            try:
                self._pg_pool.put_nowait(conn)
            except queue.Full:
                self._discard_pg_connection(conn)
        finally:
            self._pg_pool_sem.release()

    def _replace_pg_connection(self, old_conn: Any) -> Any:
        self._discard_pg_connection(old_conn)
        return self._open_pg_connection()

    def _reconnect_pg(self) -> Any:
        """Close all idle pooled PostgreSQL connections and warm one fresh connection."""
        while True:
            try:
                old = self._pg_pool.get_nowait()
            except queue.Empty:
                break
            self._discard_pg_connection(old)
        fresh = self._open_pg_connection()
        try:
            self._pg_pool.put_nowait(fresh)
        except queue.Full:
            self._discard_pg_connection(fresh)
        return fresh

    def _read_applied_versions_unlocked(self, conn: Any) -> set[int]:
        try:
            rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
            return {int(r["version"]) for r in rows}
        except Exception:
            if self.is_postgres:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return set()

    def schema_version(self) -> int:
        """Return highest applied schema migration version."""
        with self.read() as conn:
            applied = self._read_applied_versions_unlocked(conn)
            return max(applied) if applied else 0

    def init_schema(self) -> None:
        """Apply all pending versioned schema and data migrations idempotently.

        Never skips schema upgrades merely because legacy table names exist. Instead checks
        ``schema_migrations`` cleanly (read-only fast path when already at
        ``LATEST_SCHEMA_VERSION``), serializes upgrades under a Postgres advisory lock or
        SQLite write transaction, and applies each unapplied migration in strict order.
        """
        expected_versions = {ver for ver, _, _ in MIGRATIONS}
        with self._lock:
            # Fast read-only check so warm serverless instances never acquire DDL locks unnecessarily
            with self.read() as conn:
                applied = self._read_applied_versions_unlocked(conn)
            if expected_versions.issubset(applied):
                return

            with self.transaction(statement_timeout_ms=30_000, lock_timeout_ms=15_000) as conn:
                if self.is_postgres:
                    conn.execute(f"SELECT pg_advisory_xact_lock({PG_SCHEMA_ADVISORY_LOCK_KEY})")

                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version INTEGER PRIMARY KEY,
                        name TEXT NOT NULL,
                        checksum TEXT NOT NULL,
                        applied_at TEXT NOT NULL
                    )
                    """
                )
                applied = {
                    int(r["version"])
                    for r in conn.execute("SELECT version FROM schema_migrations").fetchall()
                }
                for version, name, migrate_fn in MIGRATIONS:
                    if version in applied:
                        continue
                    migrate_fn(conn, is_postgres=self.is_postgres)
                    try:
                        fn_src = inspect.getsource(migrate_fn)
                    except (OSError, TypeError):
                        fn_src = ""
                    checksum = hashlib.sha256(f"{version}:{name}:{fn_src}".encode("utf-8")).hexdigest()[:16]
                    conn.execute(
                        """
                        INSERT INTO schema_migrations (version, name, checksum, applied_at)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(version) DO NOTHING
                        """,
                        (version, name, checksum, utc_now_iso()),
                    )

    @contextmanager
    def transaction(
        self,
        *,
        statement_timeout_ms: int = 10_000,
        lock_timeout_ms: int = 5_000,
    ) -> Iterator[Any]:
        """Yield a transactional database connection (pooled on Postgres; locked on SQLite)."""
        if self.read_only:
            raise ReadOnlyDatabaseError("This deployment is read-only.")
        if self.is_postgres:
            state = self._local
            cur_depth = int(getattr(state, "pg_depth", 0))
            cur_adapter = getattr(state, "pg_adapter", None)
            cur_mode = getattr(state, "pg_mode", None)
            if cur_depth > 0 and cur_adapter is not None and cur_mode == "write":
                state.pg_depth = cur_depth + 1
                try:
                    yield cur_adapter
                finally:
                    state.pg_depth = cur_depth
                return

            raw_conn = self._checkout_pg_connection()
            adapter = _PgConnectionAdapter(self, raw_conn)
            prev_state = (cur_adapter, cur_depth, cur_mode)
            state.pg_adapter = adapter
            state.pg_depth = 1
            state.pg_mode = "write"
            try:
                adapter.reset_stmt_count()
                adapter.execute(f"SET LOCAL statement_timeout = {int(statement_timeout_ms)}")
                adapter.execute(f"SET LOCAL lock_timeout = {int(lock_timeout_ms)}")
                yield adapter
                adapter.commit()
            except Exception:
                try:
                    adapter.rollback()
                except Exception:
                    pass
                raise
            finally:
                state.pg_adapter, state.pg_depth, state.pg_mode = prev_state
                self._checkin_pg_connection(adapter.conn)
            return

        with self._lock:
            assert self._conn is not None
            is_outer = self._tx_depth == 0
            self._tx_depth += 1
            try:
                if is_outer:
                    self._conn.execute("BEGIN IMMEDIATE")
                yield self._conn
                if is_outer:
                    self._conn.execute("COMMIT")
            except Exception:
                if is_outer:
                    try:
                        if self._conn.in_transaction:
                            self._conn.execute("ROLLBACK")
                    except Exception:
                        pass
                raise
            finally:
                self._tx_depth -= 1

    @contextmanager
    def read(self, *, statement_timeout_ms: int = 10_000) -> Iterator[Any]:
        """Yield a database connection for consistent read-only snapshots (concurrent on Postgres)."""
        if self.is_postgres:
            state = self._local
            cur_depth = int(getattr(state, "pg_depth", 0))
            cur_adapter = getattr(state, "pg_adapter", None)
            cur_mode = getattr(state, "pg_mode", None)
            if cur_depth > 0 and cur_adapter is not None:
                state.pg_depth = cur_depth + 1
                try:
                    yield cur_adapter
                finally:
                    state.pg_depth = cur_depth
                return

            raw_conn = self._checkout_pg_connection()
            adapter = _PgConnectionAdapter(self, raw_conn)
            prev_state = (cur_adapter, cur_depth, cur_mode)
            state.pg_adapter = adapter
            state.pg_depth = 1
            state.pg_mode = "read"
            try:
                adapter.reset_stmt_count()
                adapter.execute(f"SET LOCAL statement_timeout = {int(statement_timeout_ms)}")
                yield adapter
            finally:
                # Release implicit Postgres read transaction cleanly so PgBouncer never stays IDLE IN TRANSACTION
                try:
                    adapter.rollback()
                except Exception:
                    pass
                state.pg_adapter, state.pg_depth, state.pg_mode = prev_state
                self._checkin_pg_connection(adapter.conn)
            return

        with self._lock:
            assert self._conn is not None
            started_tx = self._tx_depth == 0 and not self._conn.in_transaction
            if started_tx:
                self._conn.execute("BEGIN DEFERRED")
            try:
                yield self._conn
            finally:
                if started_tx and self._conn.in_transaction:
                    try:
                        self._conn.execute("ROLLBACK")
                    except Exception:
                        pass

    def health_check(self) -> bool:
        """Verify database connectivity cleanly on SQLite or Neon Postgres."""
        try:
            with self.read() as conn:
                row = conn.execute("SELECT 1 AS ok").fetchone()
                return bool(row and row["ok"] == 1)
        except Exception:
            if self.is_postgres:
                try:
                    self._reconnect_pg()
                    with self.read() as conn:
                        row = conn.execute("SELECT 1 AS ok").fetchone()
                        return bool(row and row["ok"] == 1)
                except Exception:
                    return False
            return False

    def close(self) -> None:
        """Close all database connections in the pool."""
        with self._lock:
            if self.is_postgres:
                self._pg_closed = True
                while True:
                    try:
                        conn = self._pg_pool.get_nowait()
                    except queue.Empty:
                        break
                    self._discard_pg_connection(conn)
                with self._pg_pool_lock:
                    remaining = list(self._pg_all_conns)
                    self._pg_all_conns.clear()
                for conn in remaining:
                    if not getattr(conn, "closed", True):
                        try:
                            conn.close()
                        except Exception:
                            pass
            elif self._conn is not None:
                self._conn.close()
                self._conn = None
