"""Real PostgreSQL integration, migration, and concurrency tests.

Gated on the ``TEST_POSTGRES_URL`` environment variable (e.g.
``postgresql://postgres:postgres@127.0.0.1:5432/aiwatchdogs_test``) so local offline
pytest runs skip cleanly while CI executes these against a real ``postgres:16-alpine``
service container.
"""

from __future__ import annotations

import os
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg
import pytest
from psycopg.rows import dict_row

from aiwatchdogs.hub.database import LATEST_SCHEMA_VERSION, Database
from aiwatchdogs.hub.desk import DeskService
from aiwatchdogs.hub.seed import seed_database
from aiwatchdogs.hub.sse import SSEBroker
from aiwatchdogs.projects import UNION_BUDGET_PROJECT_SLUG, sync_projects

TEST_POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not TEST_POSTGRES_URL,
    reason="Set TEST_POSTGRES_URL to run real PostgreSQL migration and concurrency tests.",
)


def _with_search_path(dsn: str, schema: str) -> str:
    """Attach a per-test PostgreSQL search_path via libpq connection options."""
    normalized = "postgresql://" + dsn[len("postgres://") :] if dsn.startswith("postgres://") else dsn
    parts = urlsplit(normalized)
    query_items = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "options"]
    query_items.append(("options", f"-csearch_path={schema},public"))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query_items), parts.fragment))


@pytest.fixture()
def pg_schema_url() -> str:
    """Create an isolated PostgreSQL schema for each test and drop it cleanly afterwards."""
    schema = f"awd_test_{secrets.token_hex(6)}"
    base_dsn = (
        "postgresql://" + TEST_POSTGRES_URL[len("postgres://") :]
        if TEST_POSTGRES_URL.startswith("postgres://")
        else TEST_POSTGRES_URL
    )
    with psycopg.connect(base_dsn, autocommit=True) as admin_conn:
        admin_conn.execute(f'CREATE SCHEMA "{schema}"')
    try:
        yield _with_search_path(base_dsn, schema)
    finally:
        with psycopg.connect(base_dsn, autocommit=True) as admin_conn:
            admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def _exercise_union_budget_sql(db: Database, projects: Path) -> None:
    """Sync the synthetic UB1 project and run the paged task list and year rollup SQL against ``db``."""
    first = sync_projects(db, projects)
    assert first["changed"] is True and first["synced"] == 1, first
    desk = DeskService(db=db, broker=SSEBroker(db=db))
    slug = UNION_BUDGET_PROJECT_SLUG
    ids = ["prj-ub-2019-20-d042-l12-03", "prj-ub-2019-20-d042-l14", "prj-ub-2020-21-d042-l12"]

    # Paging: open tasks first, then by id.
    page = desk.list_mission_tasks(slug, status_filter="all", limit=2)
    assert page["counts"] == {"all": 3, "open": 3, "claimed": 0, "completed": 0}
    assert (page["total"], page["offset"], page["limit"]) == (3, 0, 2)
    assert [t["id"] for t in page["tasks"]] == ids[:2]
    assert [t["id"] for t in desk.list_mission_tasks(slug, offset=2, limit=2)["tasks"]] == ids[2:]

    # Claim one task, then filter by status.
    token = desk.register_agent({"handle": "pg-ub-auditor"}, "https://aiwatchdogs.in")["api_token"]
    lease = desk.claim_next_task(token=token, task_id="ub-2020-21-d042-l12")
    assert lease is not None and lease["task"]["id"] == ids[2]
    claimed = desk.list_mission_tasks(slug, status_filter="claimed")
    assert claimed["counts"] == {"all": 3, "open": 2, "claimed": 1, "completed": 0}
    assert claimed["total"] == 1 and [t["id"] for t in claimed["tasks"]] == [ids[2]]
    assert [t["id"] for t in desk.list_mission_tasks(slug, status_filter="open")["tasks"]] == ids[:2]
    assert desk.list_mission_tasks(slug, status_filter="completed")["tasks"] == []

    # LIKE escaping: '%' and '_' in the search are literal. Unescaped, "%50%_share%" would also
    # match the third task's "50 per cent share".
    literal = desk.list_mission_tasks(slug, q="50%_SHARE")
    assert literal["counts"]["all"] == 1 and [t["id"] for t in literal["tasks"]] == [ids[0]]
    assert desk.list_mission_tasks(slug, q="50 per cent")["counts"]["all"] == 1
    assert desk.list_mission_tasks(slug, q="%_s")["counts"]["all"] == 1  # unescaped, this matches every task
    assert desk.list_mission_tasks(slug, q="%")["counts"]["all"] == 1
    # The id is searched too, case-insensitively.
    by_year = desk.list_mission_tasks(slug, q="UB-2019-20")
    assert by_year["counts"]["all"] == 2 and [t["id"] for t in by_year["tasks"]] == ids[:2]

    # Mission detail: SQL status counts and the year rollup keyed on SUBSTR(id, 8, 7).
    detail = desk.get_mission_detail(slug)
    assert detail["total_tasks"] == 3
    assert (detail["tasks_listed"], detail["tasks_truncated"]) == (3, False)
    counts = detail["visual_summary"]["status_counts"]
    assert (counts["total"], counts["queued"], counts["claimed"], counts["waiting"]) == (3, 2, 1, 0)
    years = {y["year"]: y for y in detail["visual_summary"]["union_budget"]["years"]}
    assert sorted(years) == ["2019-20", "2020-21"]
    assert {k: years["2019-20"][k] for k in ("tasks", "open", "claimed", "done")} == {"tasks": 2, "open": 2, "claimed": 0, "done": 0}
    assert {k: years["2020-21"][k] for k in ("tasks", "open", "claimed", "done")} == {"tasks": 1, "open": 0, "claimed": 1, "done": 0}

    # Unchanged files: the second sync does no work.
    again = sync_projects(db, projects)
    assert again["changed"] is False, again


def test_postgres_union_budget_task_paging_search_and_year_rollup(
    pg_schema_url: str, union_budget_projects: Path,
) -> None:
    db = Database(database_url=pg_schema_url)
    try:
        _exercise_union_budget_sql(db, union_budget_projects)
    finally:
        db.close()


def test_postgres_legacy_v0_to_v4_migration_and_seed_idempotency(pg_schema_url: str) -> None:
    """Rehearse upgrading a populated pre-v2 PostgreSQL database to v4 with all 4 live dossiers."""
    with psycopg.connect(pg_schema_url, row_factory=dict_row, autocommit=False) as conn:
        conn.execute(
            """
            CREATE TABLE public_sources (
                id TEXT PRIMARY KEY, slug TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
                pillar TEXT NOT NULL, authority TEXT NOT NULL, base_url TEXT NOT NULL,
                ingestion_tier INTEGER NOT NULL, connector_type TEXT NOT NULL,
                data_contents TEXT NOT NULL DEFAULT '', format_access TEXT NOT NULL DEFAULT '',
                config_json TEXT NOT NULL DEFAULT '{}', robots_policy TEXT NOT NULL DEFAULT 'strict-polite',
                last_synced_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE missions (
                id TEXT PRIMARY KEY, slug TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
                description TEXT NOT NULL, pillar TEXT NOT NULL, methodology_code TEXT NOT NULL,
                jurisdiction TEXT NOT NULL, ministry_or_authority TEXT NOT NULL,
                statutory_anchors_json TEXT NOT NULL DEFAULT '[]', source_ids_json TEXT NOT NULL DEFAULT '[]',
                sample_dataset_path TEXT NOT NULL DEFAULT '', data_mode TEXT NOT NULL DEFAULT 'live_public',
                status TEXT NOT NULL DEFAULT 'active', priority INTEGER NOT NULL DEFAULT 1,
                total_tasks INTEGER NOT NULL DEFAULT 0, completed_tasks INTEGER NOT NULL DEFAULT 0,
                verified_findings_count INTEGER NOT NULL DEFAULT 0, cleared_count INTEGER NOT NULL DEFAULT 0,
                monetary_audited_inr DOUBLE PRECISION NOT NULL DEFAULT 0.0, created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE agents (
                id TEXT PRIMARY KEY, handle TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL,
                provider TEXT NOT NULL, model TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'auto',
                region TEXT NOT NULL DEFAULT 'IN', capabilities_json TEXT NOT NULL DEFAULT '[]',
                api_token_hash TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
                tasks_completed INTEGER NOT NULL DEFAULT 0, findings_submitted INTEGER NOT NULL DEFAULT 0,
                findings_verified INTEGER NOT NULL DEFAULT 0, verifications_done INTEGER NOT NULL DEFAULT 0,
                cleared_submitted INTEGER NOT NULL DEFAULT 0, compute_score INTEGER NOT NULL DEFAULT 0,
                hourly_score INTEGER NOT NULL DEFAULT 0, current_task_id TEXT,
                joined_at TEXT NOT NULL, last_heartbeat_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE evidence_artifacts (
                id TEXT PRIMARY KEY, source_id TEXT, mission_id TEXT, title TEXT NOT NULL,
                canonical_url TEXT NOT NULL, archive_url TEXT, local_path TEXT,
                file_format TEXT NOT NULL, sha256 TEXT NOT NULL, byte_size INTEGER NOT NULL DEFAULT 0,
                is_demo_sample INTEGER NOT NULL DEFAULT 0, pii_scrubbed INTEGER NOT NULL DEFAULT 1,
                fetched_at TEXT NOT NULL, parsed_metadata_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, mission_slug TEXT NOT NULL,
                methodology_code TEXT NOT NULL, task_type TEXT NOT NULL, title TEXT NOT NULL,
                instructions TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}',
                target_finding_id TEXT, excluded_handles_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'queued', claimed_by_agent_id TEXT,
                claimed_by_handle TEXT, lease_id TEXT UNIQUE, lease_expires_at TEXT,
                progress_pct INTEGER NOT NULL DEFAULT 0, attempt_count INTEGER NOT NULL DEFAULT 0,
                output_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE findings (
                id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, mission_slug TEXT NOT NULL,
                pillar TEXT NOT NULL, task_id TEXT, artifact_id TEXT, title TEXT NOT NULL,
                summary TEXT NOT NULL, methodology_code TEXT NOT NULL, entity_name TEXT NOT NULL,
                jurisdiction TEXT NOT NULL, fiscal_year TEXT NOT NULL,
                monetary_exposure_inr DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                severity TEXT NOT NULL DEFAULT 'medium', anomaly_metrics_json TEXT NOT NULL DEFAULT '{}',
                rule_triggered TEXT NOT NULL DEFAULT '', source_url TEXT NOT NULL DEFAULT '',
                artifact_sha256 TEXT NOT NULL, dataset_path TEXT NOT NULL DEFAULT '',
                is_demo_sample INTEGER NOT NULL DEFAULT 0, citations_json TEXT NOT NULL DEFAULT '[]',
                benign_explanations_json TEXT NOT NULL DEFAULT '[]', suggested_authority TEXT NOT NULL DEFAULT '',
                submitted_by TEXT NOT NULL, submitted_by_agent_id TEXT,
                agent_trace_json TEXT NOT NULL DEFAULT '{}', legal_lint_passed INTEGER NOT NULL DEFAULT 1,
                pii_scrubbed INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'peer_review',
                verification_score DOUBLE PRECISION NOT NULL DEFAULT 0.0, confirm_count INTEGER NOT NULL DEFAULT 0,
                dispute_count INTEGER NOT NULL DEFAULT 0, required_quorum INTEGER NOT NULL DEFAULT 2,
                created_at TEXT NOT NULL, verified_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE peer_verifications (
                id TEXT PRIMARY KEY, finding_id TEXT NOT NULL, verifier_node_id TEXT NOT NULL,
                contributor_id TEXT NOT NULL, verifier_handle TEXT NOT NULL, provider TEXT NOT NULL DEFAULT '',
                model_id TEXT NOT NULL DEFAULT '', verdict TEXT NOT NULL, checks_json TEXT NOT NULL DEFAULT '{}',
                reproduced_metrics_json TEXT NOT NULL DEFAULT '{}',
                confidence DOUBLE PRECISION NOT NULL DEFAULT 0.9,
                notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, UNIQUE(finding_id, contributor_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE rti_drafts (
                id TEXT PRIMARY KEY, finding_id TEXT UNIQUE NOT NULL, public_authority TEXT NOT NULL,
                cpio_designation TEXT NOT NULL, rti_portal_url TEXT NOT NULL DEFAULT '',
                statutory_sections_json TEXT NOT NULL DEFAULT '[]', subject TEXT NOT NULL,
                body_text TEXT NOT NULL, word_count INTEGER NOT NULL,
                requested_records_json TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'draft',
                filed_date TEXT, due_date_30d TEXT, official_reply_artifact_id TEXT, created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE whispers (
                id TEXT PRIMARY KEY, agent_handle TEXT NOT NULL, provider TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '', mission_slug TEXT, finding_id TEXT,
                event_type TEXT NOT NULL DEFAULT 'whisper', message TEXT NOT NULL, created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE sponsors (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, handle_or_org TEXT,
                tier TEXT NOT NULL DEFAULT 'citizen_backer',
                amount_inr DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                compute_credits_desc TEXT NOT NULL DEFAULT '', url TEXT, message TEXT NOT NULL DEFAULT '',
                featured INTEGER NOT NULL DEFAULT 0, is_demo_entry INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE treasury_ledger (
                id TEXT PRIMARY KEY, entry_date TEXT NOT NULL, entry_type TEXT NOT NULL,
                direction TEXT NOT NULL, category TEXT NOT NULL, counterparty TEXT NOT NULL,
                description TEXT NOT NULL, amount_inr DOUBLE PRECISION NOT NULL,
                compute_credit_units TEXT NOT NULL DEFAULT '', receipt_ref TEXT NOT NULL,
                is_demo_entry INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO missions (id, slug, title, description, pillar, methodology_code, jurisdiction, ministry_or_authority, verified_findings_count, created_at)
            VALUES
              ('mission-m6', 'cag-union-state-audit-paras', 'CAG', 'CAG', 'audit', 'M6', 'Union of India', 'CAG', 1, '2026-11-20T00:00:00Z'),
              ('mission-m2', 'assam-gfr-split-tendering', 'M2', 'M2', 'procurement', 'M2', 'State:Assam', 'Assam', 1, '2026-11-20T00:00:00Z'),
              ('mission-m5', 'fiscal-march-rush-budgets', 'M5', 'M5', 'budget', 'M5', 'All-India', 'MoF', 2, '2026-11-20T00:00:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO agents (id, handle, display_name, provider, model, api_token_hash, joined_at, last_heartbeat_at)
            VALUES ('agt-pg-legacy', 'pg-legacy', 'PG Legacy', 'rule-engine', 'v1', 'hash', '2026-11-20T00:00:00Z', '2026-11-20T00:00:00Z')
            """
        )
        for fid, mid, slug, mcode in (
            ("fnd-2151675fe8f4", "mission-m6", "cag-union-state-audit-paras", "M6"),
            ("fnd-25c6fbd61b32", "mission-m2", "assam-gfr-split-tendering", "M2"),
            ("fnd-b6d573296cef", "mission-m5", "fiscal-march-rush-budgets", "M5"),
            ("fnd-bbc65f1fb61a", "mission-m5", "fiscal-march-rush-budgets", "M5"),
        ):
            conn.execute(
                """
                INSERT INTO findings (id, mission_id, mission_slug, pillar, title, summary, methodology_code, entity_name, jurisdiction, fiscal_year, artifact_sha256, submitted_by, submitted_by_agent_id, status, confirm_count, created_at, verified_at)
                VALUES (%s, %s, %s, 'audit', 'Legacy Title', 'Legacy Summary', %s, 'Entity', 'Union of India', 'FY2024-25', 'abc', 'pg-legacy', 'agt-pg-legacy', 'verified', 2, '2026-11-20T00:00:00Z', '2026-11-20T01:00:00Z')
                """,
                (fid, mid, slug, mcode),
            )
            conn.execute(
                """
                INSERT INTO rti_drafts (id, finding_id, public_authority, cpio_designation, rti_portal_url, subject, body_text, word_count, status, created_at)
                VALUES (%s, %s, 'Authority', 'CPIO', 'https://rtionline.gov.in', 'Subj', 'Body', 10, 'draft', '2026-11-20T01:00:00Z')
                """,
                (f"rti-{fid}", fid),
            )
        conn.commit()

    db = Database(database_url=pg_schema_url)
    try:
        assert db.is_postgres is True
        assert db.health_check() is True
        assert db.schema_version() == LATEST_SCHEMA_VERSION
        assert seed_database(db) is True

        # Idempotent re-run
        db.init_schema()
        assert seed_database(db) is False

        with db.read() as conn:
            # Verify v2 ALTER COLUMN DROP NOT NULL succeeded on Postgres
            null_cols = {
                (r["table_name"], r["column_name"]): r["is_nullable"]
                for r in conn.execute(
                    "SELECT table_name, column_name, is_nullable FROM information_schema.columns "
                    "WHERE table_schema = current_schema() "
                    "AND (table_name, column_name) IN (("
                    "'peer_verifications','confidence'), ('evidence_artifacts','sha256'), ('findings','artifact_sha256'))"
                ).fetchall()
            }
            assert null_cols[("peer_verifications", "confidence")] == "YES"
            assert null_cols[("evidence_artifacts", "sha256")] == "YES"
            assert null_cols[("findings", "artifact_sha256")] == "YES"

            # All 4 live dossiers have editorial corrections & RTI editorial holds
            for fid, expected_status in (
                ("fnd-2151675fe8f4", "retracted"),
                ("fnd-25c6fbd61b32", "retracted"),
                ("fnd-b6d573296cef", "under_review"),
                ("fnd-bbc65f1fb61a", "under_review"),
            ):
                row = conn.execute("SELECT status FROM findings WHERE id = ?", (fid,)).fetchone()
                assert row["status"] == expected_status
                rti = conn.execute("SELECT status FROM rti_drafts WHERE finding_id = ?", (fid,)).fetchone()
                assert rti["status"] == "editorial_hold"
    finally:
        db.close()


def test_postgres_concurrent_skip_locked_claims_and_single_settlement(pg_schema_url: str) -> None:
    """Verify FOR UPDATE SKIP LOCKED single-row claims and atomic single-settlement across separate connections."""
    bootstrap_db = Database(database_url=pg_schema_url)
    try:
        seed_database(bootstrap_db)
        # Keep exactly 4 queued tasks so 6 concurrent workers compete cleanly for 4 slots
        with bootstrap_db.transaction() as conn:
            conn.execute(
                "DELETE FROM tasks WHERE id NOT IN ('tsk-real-01', 'tsk-real-02', 'tsk-real-03', 'tsk-real-04')"
            )
        bootstrap_desk = DeskService(bootstrap_db, SSEBroker(bootstrap_db))
        agents = [
            bootstrap_desk.register_agent(
                {"handle": f"pg-worker-{i}", "provider": "rule-engine", "model": "v1"},
                base_url="https://aiwatchdogs.in",
            )
            for i in range(6)
        ]

        # 1. Six separate PostgreSQL connections race to claim 4 tasks concurrently
        barrier = threading.Barrier(6)

        def _claim_worker(agent_info: dict) -> str | None:
            w_db = Database(database_url=pg_schema_url)
            try:
                w_desk = DeskService(w_db, SSEBroker(w_db))
                barrier.wait(timeout=15)
                claimed = w_desk.claim_next_task(
                    token=agent_info["api_token"],
                    handle=agent_info["handle"],
                    mode="investigate",
                )
                return claimed["task"]["id"] if claimed else None
            finally:
                w_db.close()

        with ThreadPoolExecutor(max_workers=6) as pool:
            claimed_ids = list(pool.map(_claim_worker, agents))

        non_null = [cid for cid in claimed_ids if cid is not None]
        assert len(non_null) == 4
        assert len(set(non_null)) == 4  # ZERO duplicate task claims!
        assert claimed_ids.count(None) == 2

        # 2. Two separate connections race to settle the SAME lease concurrently -> exactly 1 succeeds!
        with bootstrap_db.read() as conn:
            t_row = conn.execute(
                "SELECT lease_id, claimed_by_handle FROM tasks WHERE status = 'claimed' LIMIT 1"
            ).fetchone()
        owner = next(a for a in agents if a["handle"] == t_row["claimed_by_handle"])
        target_lease = t_row["lease_id"]
        settle_barrier = threading.Barrier(2)

        def _settle_worker(idx: int) -> tuple[str, int]:
            w_db = Database(database_url=pg_schema_url)
            try:
                w_desk = DeskService(w_db, SSEBroker(w_db))
                settle_barrier.wait(timeout=15)
                try:
                    w_desk.submit_task(
                        {"lease_id": target_lease, "outcome": "cleared", "metrics": {"cleared_count": 10}},
                        token=owner["api_token"],
                        idempotency_key=f"race-key-{idx}",
                    )
                    return ("ok", 200)
                except Exception as exc:
                    return ("err", getattr(exc, "status_code", 500))
            finally:
                w_db.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(_settle_worker, [0, 1]))

        assert sorted(outcomes) == [("err", 410), ("ok", 200)]
        prof = bootstrap_desk.get_contributor_profile(owner["handle"])
        assert prof["tasks_completed"] == 1
        assert prof["compute_score"] == 10
    finally:
        bootstrap_db.close()


def test_postgres_concurrent_peer_vote_aggregation_and_cross_instance_events(pg_schema_url: str) -> None:
    """Verify FOR UPDATE serialized vote aggregation and cross-instance durable event replay."""
    db_a = Database(database_url=pg_schema_url)
    db_b = Database(database_url=pg_schema_url)
    try:
        seed_database(db_a)
        desk_a = DeskService(db_a, SSEBroker(db_a))
        desk_b = DeskService(db_b, SSEBroker(db_b))

        author = desk_a.register_agent({"handle": "pg-author"}, base_url="https://aiwatchdogs.in")
        peer1 = desk_a.register_agent({"handle": "pg-peer-1"}, base_url="https://aiwatchdogs.in")
        peer2 = desk_a.register_agent({"handle": "pg-peer-2"}, base_url="https://aiwatchdogs.in")

        # Author claims & submits finding on Instance A
        claim = desk_a.claim_next_task(
            token=author["api_token"],
            handle=author["handle"],
            mode="investigate",
            preferred_mission="assam-ocds-single-bid",
        )
        assert claim is not None
        sub = desk_a.submit_task(
            {
                "lease_id": claim["lease_id"],
                "outcome": "finding",
                "finding": {
                    "title": "Public Record Anomaly: Concurrent Vote Test Lead",
                    "summary": "Deterministic summary for Postgres concurrent vote aggregation.",
                    "methodology_code": "M1",
                    "entity_name": "Assam Health",
                    "jurisdiction": "State:Assam",
                    "fiscal_year": "FY 2021-22",
                    "monetary_exposure_inr": 10_000_000.0,
                    "severity": "high",
                    "anomaly_metrics": {"numerator": 10, "denominator": 20},
                    "rule_triggered": "M1-Single-Bid",
                    "benign_explanations": ["Specialized OEM procurement."],
                },
            },
            token=author["api_token"],
        )
        fid = sub["finding_id"]

        # Instance B (separate connection) observes durable events written by Instance A!
        events_b = desk_b.list_events(since_id=0, event_type="finding_submitted")
        assert any(ev.get("finding_id") == fid for ev in events_b["events"])

        # Peer 1 & Peer 2 claim the two peer_verify slots
        c1 = desk_a.claim_next_task(token=peer1["api_token"], handle=peer1["handle"], mode="verify")
        c2 = desk_b.claim_next_task(token=peer2["api_token"], handle=peer2["handle"], mode="verify")
        assert c1 is not None and c2 is not None
        finding_sha = desk_a.get_finding_detail(fid).get("artifact_sha256")

        # Both peers submit 'confirm' votes SIMULTANEOUSLY across separate PostgreSQL connections!
        vote_barrier = threading.Barrier(2)

        def _vote_worker(args: tuple[dict, str]) -> dict:
            peer, lease = args
            w_db = Database(database_url=pg_schema_url)
            try:
                w_desk = DeskService(w_db, SSEBroker(w_db))
                vote_barrier.wait(timeout=15)
                return w_desk.submit_task(
                    {
                        "lease_id": lease,
                        "outcome": "verification",
                        "verification": {
                            "finding_id": fid,
                            "verdict": "confirm",
                            "checks": {
                                "bytes_digest": "pass",
                                "deterministic_reproduction": "pass",
                                "metric_tolerance": "pass",
                                "neutrality_privacy": "pass",
                            },
                            "reproduced_metrics": {
                                "artifact_sha256": finding_sha,
                                "numerator": 10,
                                "denominator": 20,
                            },
                            "confidence": 0.96,
                        },
                    },
                    token=peer["api_token"],
                )
            finally:
                w_db.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(_vote_worker, [(peer1, c1["lease_id"]), (peer2, c2["lease_id"])]))

        # Exactly one of the two concurrent confirmations promoted the finding (zero lost updates / double-credit!)
        assert sum(1 for r in results if r["promoted_to_verified"]) == 1
        final_detail = desk_a.get_finding_detail(fid)
        assert final_detail["status"] == "verified"
        assert final_detail["confirm_count"] == 2
        assert final_detail["verification_score"] == pytest.approx(1.0)
        assert len(final_detail["verifications"]) == 2
        assert final_detail["rti_draft"] is not None
        assert final_detail["rti_draft"]["rti_portal_url"] == "https://rtionline.assam.gov.in"

        # Author bonus credited exactly once (10 submitted + 20 verified = 30)
        author_prof = desk_a.get_contributor_profile(author["handle"])
        assert author_prof["findings_verified"] == 1
        assert author_prof["compute_score"] == 30
    finally:
        db_a.close()
        db_b.close()


def test_postgres_security_hardening_constraints_indexes_and_races(pg_schema_url: str) -> None:
    """Verify PG v7 security constraints (BIGINT events.id, finite CHECKs, indexes) & concurrent races."""
    db = Database(database_url=pg_schema_url)
    try:
        seed_database(db)
        desk = DeskService(db, SSEBroker(db))

        with db.read() as conn:
            # 1. events.id is BIGINT on PostgreSQL (prevents 32-bit SERIAL overflow)
            ev_col = conn.execute(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'events' AND column_name = 'id'"
            ).fetchone()
            assert ev_col is not None and ev_col["data_type"] == "bigint"

            # 2. Key v7 operational & unique indexes exist
            idx_names = {
                str(r["indexname"])
                for r in conn.execute(
                    "SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()"
                ).fetchall()
            }
            for expected_idx in (
                "uq_agents_api_token_hash",
                "uq_tasks_active_claim_finding",
                "uq_score_events_dedup",
                "idx_tasks_status_lease_exp",
                "idx_events_finding_id",
            ):
                assert expected_idx in idx_names, f"Missing PG index {expected_idx}"

        # 3. PostgreSQL CHECK constraint rejects NaN, +/-Infinity, and negative sponsor amounts at DB layer
        for bad_literal in ("'NaN'::double precision", "'Infinity'::double precision", "'-Infinity'::double precision", "-5.0"):
            with pytest.raises(Exception):
                with db.transaction() as conn:
                    conn.execute(
                        f"""
                        INSERT INTO sponsors (id, name, tier, amount_inr, created_at)
                        VALUES ('sp-bad-test', 'Bad Float Test', 'citizen_backer', {bad_literal}, '2026-11-25T00:00:00Z')
                        """
                    )

        # 4. Concurrent registration race on identical handle -> exactly 1 succeeds (200), other gets 409
        reg_barrier = threading.Barrier(2)

        def _reg_worker(_i: int) -> int:
            w_db = Database(database_url=pg_schema_url)
            try:
                w_desk = DeskService(w_db, SSEBroker(w_db))
                reg_barrier.wait(timeout=15)
                try:
                    w_desk.register_agent({"handle": "pg-race-handle"}, base_url="https://aiwatchdogs.in")
                    return 200
                except Exception as exc:
                    return getattr(exc, "status_code", 500)
            finally:
                w_db.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            reg_codes = sorted(pool.map(_reg_worker, [0, 1]))
        assert reg_codes == [200, 409]

        # 5. Concurrent RTI draft creation race -> both return cleanly without 500 IntegrityError
        author = desk.register_agent({"handle": "pg-rti-author"}, base_url="https://aiwatchdogs.in")
        claim = desk.claim_next_task(
            token=author["api_token"],
            handle=author["handle"],
            mode="investigate",
            preferred_mission="assam-ocds-single-bid",
        )
        assert claim is not None
        sub = desk.submit_task(
            {
                "lease_id": claim["lease_id"],
                "outcome": "finding",
                "finding": {
                    "title": "Public Record Anomaly: PG RTI Race Test",
                    "summary": "Deterministic summary for RTI draft concurrency.",
                    "methodology_code": "M1",
                    "entity_name": "Assam Health",
                    "benign_explanations": ["Lawful OEM exception."],
                },
            },
            token=author["api_token"],
        )
        fid = sub["finding_id"]
        rti_barrier = threading.Barrier(2)

        def _rti_worker(_i: int) -> str:
            w_db = Database(database_url=pg_schema_url)
            try:
                w_desk = DeskService(w_db, SSEBroker(w_db))
                rti_barrier.wait(timeout=15)
                draft = w_desk.create_or_get_rti_draft(fid)
                return str(draft["id"])
            finally:
                w_db.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            draft_ids = list(pool.map(_rti_worker, [0, 1]))
        assert len(set(draft_ids)) == 1
    finally:
        db.close()


def test_read_only_postgres_refuses_writes() -> None:
    Database(database_url=TEST_POSTGRES_URL).close()
    db = Database(database_url=TEST_POSTGRES_URL, read_only=True)
    try:
        for _ in range(2):  # still read-only after the first transaction ends
            with db.read() as conn:
                assert conn.execute("SELECT 1 AS ok").fetchone()["ok"] == 1
                with pytest.raises(Exception, match="read-only transaction"):
                    conn.execute("UPDATE missions SET title = 'x'")
        db._reconnect_pg()
        with db.read() as conn, pytest.raises(Exception, match="read-only transaction"):
            conn.execute("DELETE FROM missions")
    finally:
        db.close()
