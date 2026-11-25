"""Migration v9 removes the original eight missions and everything recorded under them."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest

from aiwatchdogs.hub.database import Database, _migrate_v9_purge_legacy_missions
from aiwatchdogs.hub.seed import seed_database

TEST_POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "").strip()
T = "2026-11-20T00:00:00Z"


@pytest.fixture()
def pg_url() -> Iterator[str]:
    import psycopg

    base = TEST_POSTGRES_URL.replace("postgres://", "postgresql://", 1)
    schema = f"awd_purge_{secrets.token_hex(6)}"
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
    parts = urlsplit(base)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "options"]
    query.append(("options", f"-csearch_path={schema},public"))
    try:
        yield urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    finally:
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def _populate(db: Database) -> None:
    """Legacy missions (seeded) plus one project, with work by three kinds of agent."""
    assert seed_database(db) is True  # conftest keeps the legacy missions for fixtures
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO missions (id, slug, title, description, pillar, methodology_code, jurisdiction, "
            "ministry_or_authority, is_community_project, created_at) "
            "VALUES ('project-keep', 'keep-me', 'Keep', 'Keep', 'budget', 'P1', 'Delhi', 'MHA', 1, ?)",
            (T,),
        )
        for aid, handle in (("agt-old", "old-only"), ("agt-both", "both-kinds"), ("agt-new", "project-only")):
            conn.execute(
                "INSERT INTO agents (id, handle, display_name, provider, model, api_token_hash, tasks_completed, "
                "findings_submitted, verifications_done, compute_score, joined_at, last_heartbeat_at) "
                "VALUES (?, ?, ?, 'rule-engine', 'v1', ?, 9, 9, 9, 999, ?, ?)",
                (aid, handle, handle, f"hash-{aid}", T, T),
            )
        conn.execute(
            "INSERT INTO tasks (id, mission_id, mission_slug, methodology_code, task_type, title, instructions, "
            "status, claimed_by_agent_id, created_at, updated_at) "
            "VALUES ('prj-keep-1', 'project-keep', 'keep-me', 'P1', 'anomaly_scan', 't', 'i', 'completed', 'agt-both', ?, ?)",
            (T, T),
        )
        for fid, mid, slug, agent, handle in (
            ("fnd-old", "mission-m1", "assam-ocds-single-bid", "agt-old", "old-only"),
            ("fnd-keep", "project-keep", "keep-me", "agt-both", "both-kinds"),
        ):
            conn.execute(
                "INSERT INTO findings (id, mission_id, mission_slug, pillar, title, summary, methodology_code, "
                "entity_name, jurisdiction, fiscal_year, artifact_sha256, submitted_by, submitted_by_agent_id, "
                "status, confirm_count, created_at, verified_at) "
                "VALUES (?, ?, ?, 'budget', 'T', 'S', 'M1', 'E', 'J', 'FY', 'abc', ?, ?, 'verified', 2, ?, ?)",
                (fid, mid, slug, handle, agent, T, T),
            )
            conn.execute(
                "INSERT INTO rti_drafts (id, finding_id, public_authority, cpio_designation, rti_portal_url, "
                "subject, body_text, word_count, status, created_at) "
                "VALUES (?, ?, 'A', 'CPIO', 'https://rtionline.gov.in', 'S', 'B', 1, 'draft', ?)",
                (f"rti-{fid}", fid, T),
            )
            conn.execute(
                "INSERT INTO rti_draft_versions (id, rti_draft_id, finding_id, version, public_authority, "
                "cpio_designation, subject, body_text, word_count, created_at) "
                "VALUES (?, ?, ?, 1, 'A', 'CPIO', 'S', 'B', 1, ?)",
                (f"rtv-{fid}", f"rti-{fid}", fid, T),
            )
            conn.execute(
                "INSERT INTO finding_corrections (id, finding_id, correction_type, summary, created_at) "
                "VALUES (?, ?, 'note', 's', ?)",
                (f"cor-{fid}", fid, T),
            )
            conn.execute(
                "INSERT INTO events (event_type, finding_id, payload_json, created_at) VALUES ('finding_verified', ?, '{}', ?)",
                (fid, T),
            )
            conn.execute(
                "INSERT INTO whispers (id, agent_handle, mission_slug, finding_id, message, created_at) "
                "VALUES (?, ?, ?, ?, 'm', ?)",
                (f"wsp-{fid}", handle, slug, fid, T),
            )
            conn.execute(
                "INSERT INTO score_events (id, agent_id, agent_handle, event_type, points, ref_id, created_at) "
                "VALUES (?, ?, ?, 'finding_submitted', 10, ?, ?)",
                (f"se-{fid}", agent, handle, fid, T),
            )
        # 'both-kinds' verified the old finding; 'project-only' verified the kept one.
        for pvid, fid, agent, handle in (
            ("pv-old", "fnd-old", "agt-both", "both-kinds"),
            ("pv-keep", "fnd-keep", "agt-new", "project-only"),
        ):
            conn.execute(
                "INSERT INTO peer_verifications (id, finding_id, verifier_node_id, contributor_id, verifier_handle, "
                "verdict, created_at) VALUES (?, ?, ?, ?, ?, 'confirm', ?)",
                (pvid, fid, agent, agent, handle, T),
            )
            conn.execute(
                "INSERT INTO score_events (id, agent_id, agent_handle, event_type, points, ref_id, created_at) "
                "VALUES (?, ?, ?, 'peer_verification', 15, ?, ?)",
                (f"se-{pvid}", agent, handle, pvid, T),
            )
        conn.execute(
            "INSERT INTO whispers (id, agent_handle, event_type, message, created_at) "
            "VALUES ('wsp-join-old', 'old-only', 'agent_joined', 'joined', ?)",
            (T,),
        )


def _ids(conn: Any, sql: str) -> list[str]:
    return sorted(str(next(iter(dict(r).values()))) for r in conn.execute(sql).fetchall())


def _check_purged(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    _populate(db)
    monkeypatch.delenv("AIWATCHDOGS_KEEP_LEGACY_MISSIONS", raising=False)
    for _ in range(2):  # running it twice changes nothing more
        with db.transaction() as conn:
            _migrate_v9_purge_legacy_missions(conn, is_postgres=db.is_postgres)

    with db.read() as conn:
        assert _ids(conn, "SELECT id FROM missions") == ["project-keep"]
        assert _ids(conn, "SELECT id FROM tasks") == ["prj-keep-1"]
        assert _ids(conn, "SELECT id FROM findings") == ["fnd-keep"]
        assert _ids(conn, "SELECT id FROM rti_drafts") == ["rti-fnd-keep"]
        assert _ids(conn, "SELECT id FROM rti_draft_versions") == ["rtv-fnd-keep"]
        assert _ids(conn, "SELECT id FROM finding_corrections") == ["cor-fnd-keep"]
        assert _ids(conn, "SELECT id FROM peer_verifications") == ["pv-keep"]
        assert _ids(conn, "SELECT finding_id FROM events") == ["fnd-keep"]
        assert _ids(conn, "SELECT id FROM whispers") == ["wsp-fnd-keep"]
        assert _ids(conn, "SELECT id FROM score_events") == ["se-fnd-keep", "se-pv-keep"]
        assert _ids(conn, "SELECT id FROM public_sources") == []
        assert _ids(conn, "SELECT id FROM evidence_artifacts") == []
        # The agent with only old work is gone; the others stay.
        assert _ids(conn, "SELECT handle FROM agents") == ["both-kinds", "project-only"]
        both = dict(conn.execute("SELECT * FROM agents WHERE handle = 'both-kinds'").fetchone())
        assert (both["tasks_completed"], both["findings_submitted"], both["verifications_done"]) == (1, 1, 0)
        assert both["compute_score"] == 10
        # An agent that never touched the old missions is left exactly as it was.
        untouched = dict(conn.execute("SELECT * FROM agents WHERE handle = 'project-only'").fetchone())
        assert untouched["compute_score"] == 999

    # With the legacy missions no longer kept, seeding never brings them back.
    assert seed_database(db) is False
    with db.read() as conn:
        assert _ids(conn, "SELECT id FROM missions") == ["project-keep"]


def test_purge_legacy_missions_sqlite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = Database(db_path=tmp_path / "purge.db")
    try:
        _check_purged(db, monkeypatch)
    finally:
        db.close()


@pytest.mark.skipif(not TEST_POSTGRES_URL, reason="Set TEST_POSTGRES_URL to run against real PostgreSQL.")
def test_purge_legacy_missions_postgres(pg_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    db = Database(database_url=pg_url)
    try:
        assert db.is_postgres is True
        _check_purged(db, monkeypatch)
    finally:
        db.close()
