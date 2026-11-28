"""Regression tests for hub telemetry consistency across all public read endpoints.

Covers all 6 audited telemetry fixes:
1. Author-bonus (`finding_verified_author_bonus`) timestamp attribution in sliding windows
   (10m, 1h, 36h sparklines) while keeping all-time scores intact, in both SQLite and Postgres branches.
2. Real `last_check_at` timestamp (latest completed/settled task) separate from `last_heartbeat_at`.
3. `/api/head` `top_contributors` deduplicated union (top-20 all-time, 1h/10m score & checks, plus live).
4. W1 (`website_batch_observed`) included in project-scoped leaderboards & 36h points sparklines.
5. Unified `LIVE_AGENT_WINDOW_SECONDS = 120` active window & consistent revoked-agent filtering.
6. `/api/live` `model_breakdown` grouping by `coding_agent` + `coding_agent_model` when present.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from aiwatchdogs.hub.app import create_app
from aiwatchdogs.hub.desk import LIVE_AGENT_WINDOW_SECONDS


def _iso_offset(**kwargs: int) -> str:
    return (
        (datetime.now(timezone.utc) + timedelta(**kwargs))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _register(
    client: TestClient,
    handle: str,
    *,
    provider: str = "rule-engine",
    model: str = "deterministic-python-v1",
    coding_agent: str | None = None,
    coding_agent_model: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "handle": handle,
        "provider": provider,
        "model": model,
    }
    if coding_agent is not None:
        payload["coding_agent"] = coding_agent
    if coding_agent_model is not None:
        payload["coding_agent_model"] = coding_agent_model
    resp = client.post("/api/agents/register", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_author_bonus_attributed_to_finding_submission_time_across_all_windows(
    tmp_path: Path,
) -> None:
    """An offline author's old finding verified now must NOT inflate 1h/10m leaderboards,
    while still counting toward all-time compute_score and landing in the finding's 36h bucket."""
    app = create_app(db_path=tmp_path / "author_bonus.db", seed=True)
    with TestClient(app) as client:
        offline_author = _register(client, "offline-author")
        recent_author = _register(client, "recent-author")
        ancient_author = _register(client, "ancient-author")
        verifier = _register(client, "active-verifier")

        ts_11h_ago = _iso_offset(hours=-11, minutes=-15)
        ts_40h_ago = _iso_offset(hours=-40)
        ts_5m_ago = _iso_offset(minutes=-5)
        ts_1m_ago = _iso_offset(minutes=-1)

        with app.state.db.read() as conn:
            m_row = conn.execute(
                "SELECT id, slug, pillar, methodology_code FROM missions LIMIT 1"
            ).fetchone()
            assert m_row is not None
            mission_id = str(m_row["id"])
            mission_slug = str(m_row["slug"])
            pillar = str(m_row["pillar"])
            mcode = str(m_row["methodology_code"])

        with app.state.db.transaction() as conn:
            # 1. Old finding submitted 11h ago by offline-author, verified 1m ago by active-verifier.
            conn.execute(
                """
                INSERT INTO findings (
                    id, mission_id, mission_slug, pillar, title, summary, methodology_code,
                    entity_name, jurisdiction, fiscal_year, monetary_exposure_inr, severity,
                    submitted_by, submitted_by_agent_id, status, created_at, verified_at
                ) VALUES (
                    'fnd-old-11h', ?, ?, ?, 'Old Lead', 'Submitted 11h ago', ?,
                    'Dept A', 'IN', '2025-26', 1000000.0, 'medium',
                    ?, ?, 'verified', ?, ?
                )
                """,
                (
                    mission_id,
                    mission_slug,
                    pillar,
                    mcode,
                    offline_author["handle"],
                    offline_author["agent_id"],
                    ts_11h_ago,
                    ts_1m_ago,
                ),
            )
            conn.execute(
                """
                INSERT INTO score_events (id, agent_id, agent_handle, event_type, points, ref_id, created_at)
                VALUES ('se-old-sub', ?, ?, 'finding_submitted', 10, 'fnd-old-11h', ?)
                """,
                (offline_author["agent_id"], offline_author["handle"], ts_11h_ago),
            )
            conn.execute(
                """
                INSERT INTO score_events (id, agent_id, agent_handle, event_type, points, ref_id, created_at)
                VALUES ('se-old-bonus', ?, ?, 'finding_verified_author_bonus', 20, 'fnd-old-11h', ?)
                """,
                (offline_author["agent_id"], offline_author["handle"], ts_1m_ago),
            )
            conn.execute(
                """
                INSERT INTO peer_verifications (
                    id, finding_id, verifier_node_id, contributor_id, verifier_handle,
                    provider, model_id, verdict, created_at
                ) VALUES ('pv-old-1', 'fnd-old-11h', ?, ?, ?, 'rule-engine', 'v1', 'confirm', ?)
                """,
                (verifier["agent_id"], verifier["handle"], verifier["handle"], ts_1m_ago),
            )
            conn.execute(
                """
                INSERT INTO score_events (id, agent_id, agent_handle, event_type, points, ref_id, created_at)
                VALUES ('se-verif-1', ?, ?, 'peer_verification', 15, 'pv-old-1', ?)
                """,
                (verifier["agent_id"], verifier["handle"], ts_1m_ago),
            )
            conn.execute(
                "UPDATE agents SET compute_score = 30, last_heartbeat_at = ? WHERE handle = ?",
                (ts_11h_ago, offline_author["handle"]),
            )
            conn.execute(
                "UPDATE agents SET compute_score = 15, last_heartbeat_at = ? WHERE handle = ?",
                (ts_1m_ago, verifier["handle"]),
            )

            # 2. Recent finding submitted 5m ago by recent-author, verified 1m ago.
            conn.execute(
                """
                INSERT INTO findings (
                    id, mission_id, mission_slug, pillar, title, summary, methodology_code,
                    entity_name, jurisdiction, fiscal_year, monetary_exposure_inr, severity,
                    submitted_by, submitted_by_agent_id, status, created_at, verified_at
                ) VALUES (
                    'fnd-recent-5m', ?, ?, ?, 'Recent Lead', 'Submitted 5m ago', ?,
                    'Dept B', 'IN', '2025-26', 1000000.0, 'medium',
                    ?, ?, 'verified', ?, ?
                )
                """,
                (
                    mission_id,
                    mission_slug,
                    pillar,
                    mcode,
                    recent_author["handle"],
                    recent_author["agent_id"],
                    ts_5m_ago,
                    ts_1m_ago,
                ),
            )
            conn.execute(
                """
                INSERT INTO score_events (id, agent_id, agent_handle, event_type, points, ref_id, created_at)
                VALUES ('se-rec-sub', ?, ?, 'finding_submitted', 10, 'fnd-recent-5m', ?)
                """,
                (recent_author["agent_id"], recent_author["handle"], ts_5m_ago),
            )
            conn.execute(
                """
                INSERT INTO score_events (id, agent_id, agent_handle, event_type, points, ref_id, created_at)
                VALUES ('se-rec-bonus', ?, ?, 'finding_verified_author_bonus', 20, 'fnd-recent-5m', ?)
                """,
                (recent_author["agent_id"], recent_author["handle"], ts_1m_ago),
            )
            conn.execute(
                "UPDATE agents SET compute_score = 30, last_heartbeat_at = ? WHERE handle = ?",
                (ts_5m_ago, recent_author["handle"]),
            )

            # 3. Ancient finding submitted 40h ago by ancient-author, verified 1m ago.
            conn.execute(
                """
                INSERT INTO findings (
                    id, mission_id, mission_slug, pillar, title, summary, methodology_code,
                    entity_name, jurisdiction, fiscal_year, monetary_exposure_inr, severity,
                    submitted_by, submitted_by_agent_id, status, created_at, verified_at
                ) VALUES (
                    'fnd-ancient-40h', ?, ?, ?, 'Ancient Lead', 'Submitted 40h ago', ?,
                    'Dept C', 'IN', '2025-26', 1000000.0, 'medium',
                    ?, ?, 'verified', ?, ?
                )
                """,
                (
                    mission_id,
                    mission_slug,
                    pillar,
                    mcode,
                    ancient_author["handle"],
                    ancient_author["agent_id"],
                    ts_40h_ago,
                    ts_1m_ago,
                ),
            )
            conn.execute(
                """
                INSERT INTO score_events (id, agent_id, agent_handle, event_type, points, ref_id, created_at)
                VALUES ('se-anc-sub', ?, ?, 'finding_submitted', 10, 'fnd-ancient-40h', ?)
                """,
                (ancient_author["agent_id"], ancient_author["handle"], ts_40h_ago),
            )
            conn.execute(
                """
                INSERT INTO score_events (id, agent_id, agent_handle, event_type, points, ref_id, created_at)
                VALUES ('se-anc-bonus', ?, ?, 'finding_verified_author_bonus', 20, 'fnd-ancient-40h', ?)
                """,
                (ancient_author["agent_id"], ancient_author["handle"], ts_1m_ago),
            )
            conn.execute(
                "UPDATE agents SET compute_score = 30, last_heartbeat_at = ? WHERE handle = ?",
                (ts_40h_ago, ancient_author["handle"]),
            )

        # Verify low-level score helpers directly
        desk = app.state.desk
        with app.state.db.read() as conn:
            all_map, hr_map = desk._fetch_score_maps(conn)
            assert all_map["offline-author"] == 30
            assert hr_map.get("offline-author", 0) == 0
            assert all_map["recent-author"] == 30
            assert hr_map["recent-author"] == 30
            assert all_map["ancient-author"] == 30
            assert hr_map.get("ancient-author", 0) == 0
            assert hr_map["active-verifier"] == 15

            assert desk._fetch_single_agent_scores(conn, "offline-author") == (30, 0)
            assert desk._fetch_single_agent_scores(conn, "recent-author") == (30, 30)
            assert desk._fetch_single_agent_scores(conn, "ancient-author") == (30, 0)
            assert desk._fetch_single_agent_scores(conn, "active-verifier") == (15, 15)

        def _assert_agents(by_handle: dict[str, dict[str, Any]]) -> None:
            off = by_handle["offline-author"]
            assert off["compute_score"] == 30
            assert off["score_1h"] == 0
            assert off["score_10m"] == 0
            assert off["hourly_score"] == 0
            assert off["hourly_points_36h"][35] == 0
            assert off["hourly_points_36h"][35 - 11] == 30  # 10 + 20 attributed to 11h-ago bucket
            assert sum(off["hourly_points_36h"]) == 30

            rec = by_handle["recent-author"]
            assert rec["compute_score"] == 30
            assert rec["score_1h"] == 30
            assert rec["score_10m"] == 30
            assert rec["hourly_points_36h"][35] == 30

            anc = by_handle["ancient-author"]
            assert anc["compute_score"] == 30
            assert anc["score_1h"] == 0
            assert anc["score_10m"] == 0
            assert sum(anc["hourly_points_36h"]) == 0

            ver = by_handle["active-verifier"]
            assert ver["compute_score"] == 15
            assert ver["score_1h"] == 15
            assert ver["score_10m"] == 15
            assert ver["hourly_points_36h"][35] == 15

        # 1. /api/head
        head = client.get("/api/head").json()
        _assert_agents({c["handle"]: c for c in head["top_contributors"]})

        # 2. /api/contributors?window=hour
        contribs_hr = client.get("/api/contributors?window=hour").json()["contributors"]
        _assert_agents({c["handle"]: c for c in contribs_hr})
        # recent-author (30) and active-verifier (15) must outrank offline-author (0)!
        hr_order = [c["handle"] for c in contribs_hr]
        assert hr_order.index("recent-author") < hr_order.index("offline-author")
        assert hr_order.index("active-verifier") < hr_order.index("offline-author")

        # 3. /api/contributors/{handle}
        off_prof = client.get("/api/contributors/offline-author").json()
        assert off_prof["compute_score"] == 30
        assert off_prof["score_1h"] == 0
        assert off_prof["score_10m"] == 0
        assert off_prof["hourly_points_36h"][35 - 11] == 30
        assert off_prof["hourly_points_36h"][35] == 0

        # 4. /api/missions/{slug}
        mission_detail = client.get(f"/api/missions/{mission_slug}").json()
        _assert_agents({c["handle"]: c for c in mission_detail["contributors_all"]})
    app.state.db.close()


def test_postgres_branch_sql_placeholders_and_clauses(tmp_path: Path, monkeypatch: Any) -> None:
    """Verify the Postgres-only SQL branches in `_fetch_activity_telemetry_maps` and
    `_fetch_mission_contributors` pass exact parameter counts and include the author-bonus
    LEFT JOIN and `website_batch_observed` filter."""
    app = create_app(db_path=tmp_path / "pg_spy.db", seed=True)
    desk = app.state.desk
    captured: list[tuple[str, tuple[Any, ...]]] = []

    class _FakeCursor:
        def fetchall(self) -> list[dict[str, Any]]:
            return []

        def fetchone(self) -> dict[str, Any] | None:
            return None

    class _PgSpyConn:
        def execute(self, sql: str, params: Any = ()) -> _FakeCursor:
            p_tuple = tuple(params) if not isinstance(params, tuple) else params
            assert sql.count("?") == len(p_tuple), (
                f"Placeholder mismatch ({sql.count('?')} vs {len(p_tuple)}): {sql}"
            )
            captured.append((" ".join(sql.split()), p_tuple))
            return _FakeCursor()

    monkeypatch.setattr(app.state.db, "is_postgres", True)
    spy = _PgSpyConn()

    desk._fetch_score_maps(spy)
    desk._fetch_single_agent_scores(spy, "tester")
    desk._fetch_activity_telemetry_maps(spy)
    desk._fetch_activity_telemetry_maps(spy, handle="tester")
    desk._compute_mission_contributors(spy, "mission-1")

    se_window_queries = [
        sql for sql, _ in captured
        if "FROM score_events se" in sql and "NOT EXISTS ( SELECT 1 FROM score_events se" not in sql
    ]
    # score_maps(1h), single_agent(1h), telemetry(all), telemetry(single), mission agg, mission 36h
    assert len(se_window_queries) == 6
    for sql in se_window_queries:
        assert "LEFT JOIN findings f ON se.event_type = 'finding_verified_author_bonus' AND f.id = se.ref_id" in sql
        assert "COALESCE(NULLIF(f.created_at, ''), se.created_at)" in sql

    mission_se_queries = [sql for sql in se_window_queries if "website_batch_observed" in sql]
    assert len(mission_se_queries) == 2  # mission aggregate + mission 36h sparkline
    app.state.db.close()


def test_last_check_at_distinguished_from_last_heartbeat_at(tmp_path: Path) -> None:
    """Agents that heartbeat without completing tasks must have `last_check_at = None`,
    while agents with completed tasks report their real task completion timestamp."""
    app = create_app(db_path=tmp_path / "last_check.db", seed=True)
    with TestClient(app) as client:
        hb_only = _register(client, "heartbeater-only")
        checker = _register(client, "actual-checker")

        ts_check = _iso_offset(minutes=-20)
        ts_hb = _iso_offset(seconds=-10)

        with app.state.db.read() as conn:
            m_row = conn.execute("SELECT id, slug, methodology_code FROM missions LIMIT 1").fetchone()
            assert m_row is not None
            mission_id = str(m_row["id"])
            mission_slug = str(m_row["slug"])
            mcode = str(m_row["methodology_code"])

        with app.state.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                    id, mission_id, mission_slug, methodology_code, task_type, title, instructions,
                    payload_json, status, claimed_by_agent_id, claimed_by_handle,
                    created_at, updated_at, completed_at
                ) VALUES (
                    'tsk-check-1', ?, ?, ?, 'anomaly_scan', 'Check 1', 'Scan',
                    '{}', 'completed', ?, ?, ?, ?, ?
                )
                """,
                (
                    mission_id,
                    mission_slug,
                    mcode,
                    checker["agent_id"],
                    checker["handle"],
                    ts_check,
                    ts_check,
                    ts_check,
                ),
            )
            conn.execute(
                "UPDATE agents SET tasks_completed = 1, compute_score = 10, last_heartbeat_at = ? WHERE handle = ?",
                (ts_hb, checker["handle"]),
            )
            conn.execute(
                "UPDATE agents SET last_heartbeat_at = ? WHERE handle = ?",
                (ts_hb, hb_only["handle"]),
            )

        # /api/head
        head = client.get("/api/head").json()
        head_map = {c["handle"]: c for c in head["top_contributors"]}
        assert head_map["heartbeater-only"]["last_check_at"] is None
        assert head_map["heartbeater-only"]["last_heartbeat_at"] == ts_hb
        assert head_map["actual-checker"]["last_check_at"] == ts_check
        assert head_map["actual-checker"]["last_heartbeat_at"] == ts_hb

        # /api/live
        live = client.get("/api/live").json()
        live_map = {c["handle"]: c for c in live["agents"]}
        assert live_map["heartbeater-only"]["last_check_at"] is None
        assert live_map["actual-checker"]["last_check_at"] == ts_check

        # /api/contributors
        contribs = client.get("/api/contributors").json()
        c_map = {c["handle"]: c for c in contribs["contributors"]}
        assert c_map["heartbeater-only"]["last_check_at"] is None
        assert c_map["actual-checker"]["last_check_at"] == ts_check

        # /api/contributors/{handle}
        p_hb = client.get("/api/contributors/heartbeater-only").json()
        assert p_hb["last_check_at"] is None
        assert p_hb["agent"]["last_check_at"] is None
        assert p_hb["last_heartbeat_at"] == ts_hb

        p_chk = client.get("/api/contributors/actual-checker").json()
        assert p_chk["last_check_at"] == ts_check
        assert p_chk["agent"]["last_check_at"] == ts_check
        assert p_chk["last_heartbeat_at"] == ts_hb

        # /api/missions/{slug}
        md = client.get(f"/api/missions/{mission_slug}").json()
        m_map = {c["handle"]: c for c in md["contributors_all"]}
        assert m_map["actual-checker"]["last_check_at"] == ts_check
    app.state.db.close()


def test_head_top_contributors_unions_all_windows_and_live_without_top10_truncation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`/api/head` `top_contributors` must include top hourly/10m score & check leaders and
    all currently live agents even when they rank outside the all-time top 10/20."""
    monkeypatch.setenv("AIWATCHDOGS_DISABLE_RATE_LIMIT", "1")
    app = create_app(db_path=tmp_path / "head_union.db", seed=True)
    with TestClient(app) as client:
        ts_old = _iso_offset(days=-5)
        ts_5m = _iso_offset(minutes=-5)
        ts_25m = _iso_offset(minutes=-25)
        ts_live = _iso_offset(seconds=-15)

        with app.state.db.read() as conn:
            m_row = conn.execute("SELECT id, slug, methodology_code FROM missions LIMIT 1").fetchone()
            assert m_row is not None
            mission_id = str(m_row["id"])
            mission_slug = str(m_row["slug"])
            mcode = str(m_row["methodology_code"])

        # Register 22 offline all-time veterans (scores 1000..1210)
        veterans = [_register(client, f"vet-{i:02d}") for i in range(22)]
        # Register 2 low-all-time hourly/10m stars + 2 freshly live idle agents
        star_10m = _register(client, "star-10m")
        star_1h = _register(client, "star-1h")
        live_zero = _register(client, "live-zero-pts")

        with app.state.db.transaction() as conn:
            for i, vet in enumerate(veterans):
                pts = 1000 + i * 10
                conn.execute(
                    "UPDATE agents SET compute_score = ?, tasks_completed = 50, last_heartbeat_at = ? WHERE handle = ?",
                    (pts, ts_old, vet["handle"]),
                )
                conn.execute(
                    """
                    INSERT INTO score_events (id, agent_id, agent_handle, event_type, points, ref_id, created_at)
                    VALUES (?, ?, ?, 'cleared_scan', ?, ?, ?)
                    """,
                    (f"se-vet-{i}", vet["agent_id"], vet["handle"], pts, f"tsk-vet-{i}", ts_old),
                )

            # star-10m: only 20 all-time pts (outside top 20 all-time!), but #1 in 10m
            conn.execute(
                """
                INSERT INTO tasks (
                    id, mission_id, mission_slug, methodology_code, task_type, title, instructions,
                    payload_json, status, claimed_by_agent_id, claimed_by_handle,
                    created_at, updated_at, completed_at
                ) VALUES (
                    'tsk-star-10m', ?, ?, ?, 'anomaly_scan', 'Star 10m', 'Scan',
                    '{}', 'completed', ?, ?, ?, ?, ?
                )
                """,
                (mission_id, mission_slug, mcode, star_10m["agent_id"], star_10m["handle"], ts_5m, ts_5m, ts_5m),
            )
            conn.execute(
                """
                INSERT INTO score_events (id, agent_id, agent_handle, event_type, points, ref_id, created_at)
                VALUES ('se-star-10m', ?, ?, 'cleared_scan', 20, 'tsk-star-10m', ?)
                """,
                (star_10m["agent_id"], star_10m["handle"], ts_5m),
            )
            conn.execute(
                "UPDATE agents SET compute_score = 20, tasks_completed = 1, last_heartbeat_at = ? WHERE handle = ?",
                (ts_5m, star_10m["handle"]),
            )

            # star-1h: only 15 all-time pts, offline (>120s), active 25m ago
            conn.execute(
                """
                INSERT INTO tasks (
                    id, mission_id, mission_slug, methodology_code, task_type, title, instructions,
                    payload_json, status, claimed_by_agent_id, claimed_by_handle,
                    created_at, updated_at, completed_at
                ) VALUES (
                    'tsk-star-1h', ?, ?, ?, 'anomaly_scan', 'Star 1h', 'Scan',
                    '{}', 'completed', ?, ?, ?, ?, ?
                )
                """,
                (mission_id, mission_slug, mcode, star_1h["agent_id"], star_1h["handle"], ts_25m, ts_25m, ts_25m),
            )
            conn.execute(
                """
                INSERT INTO score_events (id, agent_id, agent_handle, event_type, points, ref_id, created_at)
                VALUES ('se-star-1h', ?, ?, 'cleared_scan', 15, 'tsk-star-1h', ?)
                """,
                (star_1h["agent_id"], star_1h["handle"], ts_25m),
            )
            conn.execute(
                "UPDATE agents SET compute_score = 15, tasks_completed = 1, last_heartbeat_at = ? WHERE handle = ?",
                (ts_25m, star_1h["handle"]),
            )

            # live-zero-pts: 0 score, 0 tasks, freshly live (15s ago)
            conn.execute(
                "UPDATE agents SET compute_score = 0, tasks_completed = 0, last_heartbeat_at = ? WHERE handle = ?",
                (ts_live, live_zero["handle"]),
            )

        head = client.get("/api/head").json()
        top = head["top_contributors"]
        handles = [c["handle"] for c in top]
        assert len(handles) == len(set(handles))  # strictly deduplicated
        assert len(handles) <= 60
        # Top 20 veterans (vet-21 down to vet-02) included, vet-00/vet-01 outside top 20
        assert "vet-21" in handles
        assert "vet-02" in handles
        assert "vet-00" not in handles
        # Hourly/10m stars and freshly live agents MUST all be present despite low all-time score!
        assert "star-10m" in handles
        assert "star-1h" in handles
        assert "live-zero-pts" in handles

        by_handle = {c["handle"]: c for c in top}
        for h in ("star-10m", "star-1h", "live-zero-pts", "vet-21"):
            entry = by_handle[h]
            for field in (
                "score_10m",
                "score_1h",
                "checks_10m",
                "checks_1h",
                "hourly_points_36h",
                "hourly_checks_36h",
                "last_check_at",
            ):
                assert field in entry
            assert len(entry["hourly_points_36h"]) == 36
            assert len(entry["hourly_checks_36h"]) == 36

        assert by_handle["star-10m"]["score_10m"] == 20
        assert by_handle["star-10m"]["checks_10m"] == 1
        assert by_handle["star-10m"]["last_check_at"] == ts_5m
        assert by_handle["star-1h"]["score_1h"] == 15
        assert by_handle["star-1h"]["score_10m"] == 0
        assert by_handle["star-1h"]["checks_1h"] == 1
        assert by_handle["star-1h"]["last_check_at"] == ts_25m
        assert by_handle["live-zero-pts"]["is_active"] is True
        assert by_handle["live-zero-pts"]["last_check_at"] is None
    app.state.db.close()


def test_w1_website_batch_observed_scored_in_project_leaderboard(tmp_path: Path) -> None:
    """`website_batch_observed` (+10 pts/task) must count in W1 mission leaderboards
    and populate `hourly_points_36h` instead of falling back to 1 pt/task."""
    app = create_app(db_path=tmp_path / "w1_proj.db", seed=True)
    with TestClient(app) as client:
        observer = _register(client, "w1-spark-tester")
        mid = "project-gov-in-public-information"
        ts_5m = _iso_offset(minutes=-5)
        ts_30m = _iso_offset(minutes=-30)
        ts_3h = _iso_offset(hours=-3, minutes=-10)

        with app.state.db.transaction() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO missions (
                    id, slug, title, description, pillar, methodology_code, jurisdiction,
                    ministry_or_authority, status, priority, total_tasks, completed_tasks,
                    created_at, is_community_project
                ) VALUES (
                    ?, 'gov-in-public-information', 'Public Info', 'Desc', 'disclosure', 'W1',
                    'Union', 'MeitY', 'active', 1, 3, 3, ?, 1
                )
                """,
                (mid, ts_3h),
            )
            for tid, ts in (("w1-t1", ts_5m), ("w1-t2", ts_30m), ("w1-t3", ts_3h)):
                conn.execute(
                    """
                    INSERT INTO tasks (
                        id, mission_id, mission_slug, methodology_code, task_type, title, instructions,
                        payload_json, status, claimed_by_agent_id, claimed_by_handle,
                        created_at, updated_at, completed_at
                    ) VALUES (
                        ?, ?, 'gov-in-public-information', 'W1', 'anomaly_scan', ?, 'Observe',
                        '{}', 'completed', ?, ?, ?, ?, ?
                    )
                    """,
                    (tid, mid, f"Batch {tid}", observer["agent_id"], observer["handle"], ts, ts, ts),
                )
                conn.execute(
                    """
                    INSERT INTO score_events (id, agent_id, agent_handle, event_type, points, ref_id, created_at)
                    VALUES (?, ?, ?, 'website_batch_observed', 10, ?, ?)
                    """,
                    (f"se-{tid}", observer["agent_id"], observer["handle"], tid, ts),
                )
            conn.execute(
                "UPDATE agents SET tasks_completed = 3, compute_score = 30, last_heartbeat_at = ? WHERE handle = ?",
                (ts_5m, observer["handle"]),
            )

        detail = client.get("/api/missions/gov-in-public-information").json()
        assert len(detail["contributors_all"]) == 1
        c_all = detail["contributors_all"][0]
        assert c_all["handle"] == "w1-spark-tester"
        assert c_all["score"] == 30
        assert c_all["compute_score"] == 30
        assert c_all["score_1h"] == 20
        assert c_all["score_10m"] == 10
        assert c_all["checks_total"] == 3
        assert c_all["checks_1h"] == 2
        assert c_all["checks_10m"] == 1
        assert c_all["cleared_submitted"] == 0  # W1 observations are not cleared_scan
        assert c_all["hourly_points_36h"][35] == 20
        assert c_all["hourly_points_36h"][35 - 3] == 10
        assert sum(c_all["hourly_points_36h"]) == 30
        assert c_all["hourly_checks_36h"][35] == 2
        assert c_all["hourly_checks_36h"][35 - 3] == 1
        assert c_all["last_check_at"] == ts_5m

        assert len(detail["contributors_hour"]) == 1
        c_hr = detail["contributors_hour"][0]
        assert c_hr["score"] == 20
        assert c_hr["score_1h"] == 20
        assert c_hr["hourly_points_36h"][35] == 20
    app.state.db.close()


def test_unified_120s_live_window_and_revoked_filtering_across_endpoints(
    tmp_path: Path,
) -> None:
    """All endpoints must share LIVE_AGENT_WINDOW_SECONDS (120s) for active counts and
    consistently exclude revoked agents from totals, listings, and active status."""
    assert LIVE_AGENT_WINDOW_SECONDS == 120
    app = create_app(db_path=tmp_path / "live_window_revoked.db", seed=True)
    with TestClient(app) as client:
        fresh = _register(client, "fresh-live")
        stale_10m = _register(client, "stale-10m")
        offline_3h = _register(client, "offline-3h")
        revoked = _register(client, "revoked-fresh")

        rev_resp = client.post(
            "/api/agents/revoke",
            headers={"X-Agent-Token": revoked["api_token"]},
        )
        assert rev_resp.status_code == 200

        ts_fresh = _iso_offset(seconds=-30)
        ts_10m = _iso_offset(minutes=-10)
        ts_3h = _iso_offset(hours=-3)
        ts_rev = _iso_offset(seconds=-5)

        with app.state.db.transaction() as conn:
            conn.execute(
                "UPDATE agents SET last_heartbeat_at = ? WHERE handle = ?",
                (ts_fresh, fresh["handle"]),
            )
            conn.execute(
                "UPDATE agents SET last_heartbeat_at = ? WHERE handle = ?",
                (ts_10m, stale_10m["handle"]),
            )
            conn.execute(
                "UPDATE agents SET last_heartbeat_at = ? WHERE handle = ?",
                (ts_3h, offline_3h["handle"]),
            )
            # Even with a 5s-fresh heartbeat, revoked agent must NEVER be counted or marked active.
            conn.execute(
                "UPDATE agents SET last_heartbeat_at = ?, tasks_completed = 99 WHERE handle = ?",
                (ts_rev, revoked["handle"]),
            )

        swarm = client.get("/api/swarm/live").json()
        head = client.get("/api/head").json()
        live = client.get("/api/live").json()
        contribs = client.get("/api/contributors").json()

        # Unified totals (3 non-revoked) and active counts (1 within 120s) across ALL endpoints
        assert swarm["total_agents"] == 3
        assert swarm["working_now"] == 1
        assert "revoked-fresh" not in [n["handle"] for n in swarm["active_nodes"]]
        assert "revoked-fresh" not in [j["handle"] for j in swarm["recent_joins"]]

        assert head["stats"]["agents_total"] == 3
        assert head["stats"]["agents_active"] == 1
        assert [a["handle"] for a in head["active_agents"]] == ["fresh-live"]
        assert "revoked-fresh" not in [c["handle"] for c in head["top_contributors"]]
        assert "revoked-fresh" not in [j["handle"] for j in head["just_joined"]]
        head_by_h = {c["handle"]: c for c in head["top_contributors"]}
        assert head_by_h["fresh-live"]["is_active"] is True
        assert head_by_h["fresh-live"]["status"] == "active"
        assert head_by_h["stale-10m"]["is_active"] is False
        assert head_by_h["stale-10m"]["status"] == "idle"

        assert live["total_count"] == 3
        assert live["active_count"] == 1
        assert [a["handle"] for a in live["active_agents"]] == ["fresh-live"]
        assert "revoked-fresh" not in [a["handle"] for a in live["agents"]]
        assert sum(m["agent_count"] for m in live["model_breakdown"]) == 3
        assert sum(m["tasks_completed"] for m in live["model_breakdown"]) == 0

        assert contribs["total"] == 3
        c_by_h = {c["handle"]: c for c in contribs["contributors"]}
        assert "revoked-fresh" not in c_by_h
        assert c_by_h["fresh-live"]["is_active"] is True
        assert c_by_h["stale-10m"]["is_active"] is False

        # Direct profile lookup on revoked handle must never report active
        rev_prof = client.get("/api/contributors/revoked-fresh").json()
        assert rev_prof["is_active"] is False
        assert rev_prof["status"] == "revoked"
        assert rev_prof["agent"]["is_active"] is False
        assert rev_prof["agent"]["status"] == "revoked"
    app.state.db.close()


def test_live_model_breakdown_groups_by_coding_agent_and_model(tmp_path: Path) -> None:
    """`/api/live` `model_breakdown` must group by `coding_agent` + `coding_agent_model`
    when present instead of collapsing everything into `rule-engine / deterministic-python-v1`."""
    app = create_app(db_path=tmp_path / "model_breakdown.db", seed=True)
    with TestClient(app) as client:
        cc1 = _register(
            client,
            "cc-one",
            provider="rule-engine",
            model="deterministic-python-v1",
            coding_agent="Claude Code",
            coding_agent_model="claude-opus-4-6",
        )
        cc2 = _register(
            client,
            "cc-two",
            provider="rule-engine",
            model="deterministic-python-v1",
            coding_agent="Claude Code",
            coding_agent_model="claude-opus-4-6",
        )
        codex = _register(
            client,
            "codex-one",
            provider="rule-engine",
            model="deterministic-python-v1",
            coding_agent="Codex",
            coding_agent_model="gpt-5",
        )
        plain = _register(
            client,
            "plain-cpu",
            provider="rule-engine",
            model="deterministic-python-v1",
        )
        rev = _register(
            client,
            "revoked-cursor",
            provider="rule-engine",
            model="deterministic-python-v1",
            coding_agent="Cursor",
            coding_agent_model="claude-sonnet-4-5",
        )
        assert client.post("/api/agents/revoke", headers={"X-Agent-Token": rev["api_token"]}).status_code == 200

        with app.state.db.transaction() as conn:
            conn.execute("UPDATE agents SET tasks_completed = 6, compute_score = 60 WHERE handle = ?", (cc1["handle"],))
            conn.execute("UPDATE agents SET tasks_completed = 4, compute_score = 40 WHERE handle = ?", (cc2["handle"],))
            conn.execute("UPDATE agents SET tasks_completed = 5, compute_score = 50 WHERE handle = ?", (codex["handle"],))
            conn.execute("UPDATE agents SET tasks_completed = 5, compute_score = 50 WHERE handle = ?", (plain["handle"],))
            conn.execute("UPDATE agents SET tasks_completed = 100, compute_score = 1000 WHERE handle = ?", (rev["handle"],))

        live = client.get("/api/live").json()
        mb = live["model_breakdown"]
        assert len(mb) == 3

        by_key = {(m["coding_agent"], m["coding_agent_model"]): m for m in mb}
        assert ("Cursor", "claude-sonnet-4-5") not in by_key

        cc_grp = by_key[("Claude Code", "claude-opus-4-6")]
        assert cc_grp["agent_count"] == 2
        assert cc_grp["tasks_completed"] == 10
        assert cc_grp["compute_score"] == 100
        assert cc_grp["share_pct"] == 50.0
        assert cc_grp["provider"] == "rule-engine"
        assert cc_grp["model"] == "deterministic-python-v1"

        codex_grp = by_key[("Codex", "gpt-5")]
        assert codex_grp["agent_count"] == 1
        assert codex_grp["tasks_completed"] == 5
        assert codex_grp["compute_score"] == 50
        assert codex_grp["share_pct"] == 25.0

        plain_grp = by_key[(None, None)]
        assert plain_grp["provider"] == "rule-engine"
        assert plain_grp["model"] == "deterministic-python-v1"
        assert plain_grp["agent_count"] == 1
        assert plain_grp["tasks_completed"] == 5
        assert plain_grp["share_pct"] == 25.0
    app.state.db.close()
