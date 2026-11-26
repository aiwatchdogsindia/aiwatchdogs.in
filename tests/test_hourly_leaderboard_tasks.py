"""The hourly leaderboard shows tasks done in the last hour, not all-time totals."""

from __future__ import annotations

from typing import Any

from aiwatchdogs.hub.agent_access import render_leaderboard_md


class _StubDesk:
    def list_contributors(self, window: str = "all") -> dict[str, Any]:
        row = {
            "handle": "alice",
            "coding_agent": "Claude Code",
            "score": 150 if window == "hour" else 40_000,
            "tasks_completed": 3156,
            "checks_1h": 12,
            "findings_submitted": 10,
            "verifications_done": 0,
            "cleared_submitted": 2,
        }
        return {"contributors": [row], "total": 1, "score_weights": {"finding_submitted": 10}}


def test_hourly_table_uses_last_hour_task_count() -> None:
    md = render_leaderboard_md(_StubDesk(), "https://aiwatchdogs.in")  # type: ignore[arg-type]
    hourly = md.split("## This Hour", 1)[1].split("## All Time", 1)[0]
    assert "| Tasks (last hour) |" in hourly
    assert "| 150 | 12 |" in hourly
    assert "3156" not in hourly
    assert "3156" in md.split("## All Time", 1)[1]
