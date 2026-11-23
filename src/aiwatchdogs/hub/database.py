"""Dual-backend database layer (Neon Postgres + SQLite WAL) for AI Watchdogs India Coordinator Hub.

Automatically uses PostgreSQL (via psycopg v3) when ``DATABASE_URL`` or ``POSTGRES_URL``
is configured in the environment (or passed explicitly), and falls back to thread-safe
SQLite WAL mode for offline local development and pytest suites.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover - psycopg[binary] is a core dependency
    psycopg = None  # type: ignore[assignment]
    dict_row = None  # type: ignore[assignment]

DEFAULT_DB_REL_PATH = Path("data/aiwatchdogs.db")
VERCEL_DB_PATH = Path("/tmp/aiwatchdogs.db")

HUB_TABLES: frozenset[str] = frozenset(
    {
        "public_sources",
        "missions",
        "agents",
        "evidence_artifacts",
        "tasks",
        "findings",
        "peer_verifications",
        "rti_drafts",
        "whispers",
        "sponsors",
        "treasury_ledger",
    }
)
PG_SCHEMA_ADVISORY_LOCK_KEY = 84729104
PG_SEED_ADVISORY_LOCK_KEY = 84729105

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
    data_mode TEXT NOT NULL DEFAULT 'live_public',
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
    is_demo_entry INTEGER NOT NULL DEFAULT 0,
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
    is_demo_entry INTEGER NOT NULL DEFAULT 0,
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

_AUTOINC_RE = re.compile(r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b", re.IGNORECASE)
_INSERT_OR_IGNORE_RE = re.compile(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\b", re.IGNORECASE)
_CREATE_TABLE_RE = re.compile(r"^\s*CREATE\s+TABLE\b", re.IGNORECASE)
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
    """Serialize a Python object to compact JSON for database storage."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def is_postgres_url(value: str | Path | None) -> bool:
    """Return True if value looks like a PostgreSQL connection URL."""
    if not value or not isinstance(value, str):
        return False
    lowered = value.strip().lower()
    return lowered.startswith("postgres://") or lowered.startswith("postgresql://")


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
        resolved.parent.mkdir(parents=True, exist_ok=True)
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
    if _CREATE_TABLE_RE.match(stripped):
        stripped = _AUTOINC_RE.sub("SERIAL PRIMARY KEY", stripped)
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
    """Thin adapter exposing sqlite3.Connection-compatible execute/executescript over psycopg v3."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._stmt_count = 0

    def reset_stmt_count(self) -> None:
        self._stmt_count = 0

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> Any:
        has_params = params is not None and len(params) > 0
        translated = translate_sql_for_postgres(sql, parameterized=has_params)
        if translated is None:
            return _NoOpCursor()

        conn = self._db._ensure_pg_connection()
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
            conn = self._db._reconnect_pg()
            if has_params:
                cursor = conn.execute(translated, tuple(params))  # type: ignore[arg-type]
            else:
                cursor = conn.execute(translated)
            self._stmt_count += 1
            return cursor

    def executescript(self, script_sql: str) -> None:
        for stmt in split_sql_statements(script_sql):
            self.execute(stmt)

    def commit(self) -> None:
        conn = self._db._pg_conn
        if conn is not None and not conn.closed:
            conn.commit()

    def rollback(self) -> None:
        conn = self._db._pg_conn
        if conn is not None and not conn.closed:
            conn.rollback()


class Database:
    """Thread-safe dual-backend database wrapper (Neon Postgres + SQLite WAL)."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        database_url: str | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._tx_depth = 0
        self._conn: sqlite3.Connection | None = None
        self._pg_conn: Any = None
        self._pg_adapter: _PgConnectionAdapter | None = None

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
            self.database_url: str | None = pg_url
            self.db_path: Path | None = None
            self._pg_conn = self._open_pg_connection()
            self._pg_adapter = _PgConnectionAdapter(self)
        else:
            self.backend = "sqlite"
            self.is_postgres = False
            self.database_url = None
            self.db_path = resolve_db_path(db_path)
            self._conn = self._open_sqlite_connection()

        self.init_schema()

    def _open_sqlite_connection(self) -> sqlite3.Connection:
        assert self.db_path is not None
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

    def _open_pg_connection(self) -> Any:
        assert psycopg is not None and self.database_url is not None
        dsn = self.database_url
        if dsn.startswith("postgres://"):
            dsn = "postgresql://" + dsn[len("postgres://") :]
        # prepare_threshold=None is critical for Neon PgBouncer transaction pooling!
        return psycopg.connect(
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

    def _ensure_pg_connection(self) -> Any:
        if self._pg_conn is None or self._pg_conn.closed or getattr(self._pg_conn, "broken", False):
            self._pg_conn = self._open_pg_connection()
        return self._pg_conn

    def _reconnect_pg(self) -> Any:
        old = self._pg_conn
        if old is not None and not old.closed:
            try:
                old.close()
            except Exception:
                pass
        self._pg_conn = self._open_pg_connection()
        return self._pg_conn

    def init_schema(self) -> None:
        """Create all Hub tables and indexes idempotently on SQLite or Postgres.

        On PostgreSQL, first performs a read-only ``information_schema.tables`` check so
        warm/cold serverless instances never acquire table-level ``ShareLock`` via
        ``CREATE INDEX IF NOT EXISTS`` once the schema is already initialized. When
        bootstrapping a fresh database, serializes DDL under a transaction-scoped
        advisory lock.
        """
        with self._lock:
            if self.is_postgres:
                with self.read() as conn:
                    rows = conn.execute(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = current_schema()"
                    ).fetchall()
                    existing = {str(r["table_name"]) for r in rows}
                if HUB_TABLES.issubset(existing):
                    return

                with self.transaction() as conn:
                    conn.execute(f"SELECT pg_advisory_xact_lock({PG_SCHEMA_ADVISORY_LOCK_KEY})")
                    rows = conn.execute(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = current_schema()"
                    ).fetchall()
                    existing = {str(r["table_name"]) for r in rows}
                    if HUB_TABLES.issubset(existing):
                        return
                    conn.executescript(SCHEMA_SQL)
            else:
                assert self._conn is not None
                self._conn.executescript(SCHEMA_SQL)
                self._conn.commit()

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        """Acquire write lock and yield a transactional database connection."""
        with self._lock:
            is_outer = self._tx_depth == 0
            self._tx_depth += 1
            try:
                if self.is_postgres:
                    assert self._pg_adapter is not None
                    if is_outer:
                        self._ensure_pg_connection()
                        self._pg_adapter.reset_stmt_count()
                    yield self._pg_adapter
                    if is_outer:
                        self._pg_adapter.commit()
                else:
                    assert self._conn is not None
                    yield self._conn
                    if is_outer:
                        self._conn.commit()
            except Exception:
                if is_outer:
                    if self.is_postgres and self._pg_adapter is not None:
                        try:
                            self._pg_adapter.rollback()
                        except Exception:
                            pass
                    elif self._conn is not None:
                        self._conn.rollback()
                raise
            finally:
                self._tx_depth -= 1

    @contextmanager
    def read(self) -> Iterator[Any]:
        """Acquire lock and yield database connection for consistent reads."""
        with self._lock:
            if self.is_postgres:
                assert self._pg_adapter is not None
                is_outer = self._tx_depth == 0
                if is_outer:
                    self._ensure_pg_connection()
                    self._pg_adapter.reset_stmt_count()
                try:
                    yield self._pg_adapter
                finally:
                    # Release implicit Postgres read transaction cleanly so PgBouncer never stays IDLE IN TRANSACTION
                    if is_outer:
                        try:
                            self._pg_adapter.rollback()
                        except Exception:
                            pass
            else:
                assert self._conn is not None
                yield self._conn

    def health_check(self) -> bool:
        """Verify database connectivity cleanly on SQLite or Neon Postgres."""
        try:
            with self.read() as conn:
                row = conn.execute("SELECT 1 AS ok").fetchone()
                return bool(row and row["ok"] == 1)
        except Exception:
            if self.is_postgres:
                try:
                    with self._lock:
                        self._reconnect_pg()
                    with self.read() as conn:
                        row = conn.execute("SELECT 1 AS ok").fetchone()
                        return bool(row and row["ok"] == 1)
                except Exception:
                    return False
            return False

    def close(self) -> None:
        """Close the underlying database connection."""
        with self._lock:
            if self.is_postgres:
                if self._pg_conn is not None and not self._pg_conn.closed:
                    self._pg_conn.close()
                self._pg_conn = None
            elif self._conn is not None:
                self._conn.close()
                self._conn = None
