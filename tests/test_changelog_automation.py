"""Tests for scripts/update_changelog.py and canonical changelog.json parity."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "update_changelog.py"


def _load_script() -> Any:
    spec = importlib.util.spec_from_file_location("update_changelog", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_clean_pr_title_and_bullet_extraction() -> None:
    mod = _load_script()
    assert mod.clean_pr_title("feat(web): unify logo pill (#30)") == "Unify logo pill"
    assert mod.clean_pr_title("fix: preserve routes") == "Preserve routes"

    body = """<!-- hidden comment -->
## Summary
- Added **159-checkpoint** conformity matrix ([docs](https://aiwatchdogs.in/brief)).
- Expanded `W1` engine to 42 read-only checks.
- [x] Should skip checklist items
- ![img](/tmp/shot.png)
- Screenshot: /tmp/shot.png | desktop view

## Test Plan
- Ran pytest across 275 tests.
- Checked mobile viewport.
"""
    bullets = mod.extract_pr_bullets(body, "feat: upgrade gov-in audit")
    assert bullets == [
        "Added 159-checkpoint conformity matrix (docs).",
        "Expanded W1 engine to 42 read-only checks.",
    ]

    # Empty or boilerplate-only body falls back cleanly to the cleaned title
    assert mod.extract_pr_bullets("## Testing\n- pytest\n", "fix(cli): clarify error output") == [
        "Clarify error output."
    ]


def test_merge_github_prs_preserves_curated_updates_timestamps_and_avoids_loops() -> None:
    mod = _load_script()
    base_doc = {
        "schemaVersion": 1,
        "repository": "aiwatchdogsindia/aiwatchdogs.in",
        "entries": [
            {
                "pr": 31,
                "prUrl": "https://github.com/aiwatchdogsindia/aiwatchdogs.in/pull/31",
                "mergedAt": "2026-11-26T05:00:00Z",
                "title": "Curated PR 31 heading",
                "items": ["Curated human-reviewed bullet one."],
            },
            {
                "commit": "8303cf6",
                "commitUrl": "https://github.com/aiwatchdogsindia/aiwatchdogs.in/commit/8303cf6",
                "mergedAt": "2026-11-23T17:17:46Z",
                "title": "Initial release",
                "items": ["First version."],
            },
        ],
    }

    prs = [
        # Authoritative merge timestamp update on existing PR #31 (must keep curated title & items!)
        {
            "number": 31,
            "title": "feat: raw PR 31 title",
            "body": "- Uncurated PR body bullet that must not overwrite curated copy.",
            "merged_at": "2026-11-26T05:52:19Z",
            "html_url": "https://github.com/aiwatchdogsindia/aiwatchdogs.in/pull/31",
            "base": {"ref": "main"},
            "head": {"ref": "feat/gov-in-upgrade"},
        },
        # Newly merged PR #32 -> added automatically
        {
            "number": 32,
            "title": "feat(hub): expose district summary endpoint",
            "body": "## Changes\n- Added `/api/districts/summary` read-only JSON endpoint.\n- Covered endpoint with regression tests.",
            "merged_at": "2026-11-27T08:15:00+00:00",
            "html_url": "https://github.com/aiwatchdogsindia/aiwatchdogs.in/pull/32",
            "base": {"ref": "main"},
            "head": {"ref": "feat/districts"},
        },
        # Automated changelog sync PR -> MUST be skipped to prevent loops
        {
            "number": 33,
            "title": "chore(changelog): sync merged PRs",
            "body": "- Automated sync",
            "merged_at": "2026-11-27T08:16:00Z",
            "html_url": "https://github.com/aiwatchdogsindia/aiwatchdogs.in/pull/33",
            "base": {"ref": "main"},
            "head": {"ref": "chore/auto-changelog-sync"},
        },
        # Unmerged closed PR -> ignored
        {
            "number": 34,
            "title": "feat: abandoned draft",
            "body": "- Never merged",
            "merged_at": None,
            "html_url": "https://github.com/aiwatchdogsindia/aiwatchdogs.in/pull/34",
            "base": {"ref": "main"},
            "head": {"ref": "draft/wip"},
        },
        # Merged into non-main branch -> ignored
        {
            "number": 35,
            "title": "feat: staging experiment",
            "body": "- Staging only",
            "merged_at": "2026-11-27T09:00:00Z",
            "html_url": "https://github.com/aiwatchdogsindia/aiwatchdogs.in/pull/35",
            "base": {"ref": "staging"},
            "head": {"ref": "feat/staging"},
        },
    ]

    merged = mod.merge_github_prs(base_doc, prs)
    entries = merged["entries"]
    assert [e.get("pr") or e.get("commit") for e in entries] == [32, 31, "8303cf6"]
    # PR #31 preserved curated copy + refreshed exact mergedAt
    assert entries[1]["title"] == "Curated PR 31 heading"
    assert entries[1]["items"] == ["Curated human-reviewed bullet one."]
    assert entries[1]["mergedAt"] == "2026-11-26T05:52:19Z"
    # PR #32 auto-extracted cleanly
    assert entries[0]["title"] == "Expose district summary endpoint"
    assert entries[0]["mergedAt"] == "2026-11-27T08:15:00Z"
    assert entries[0]["items"] == [
        "Added /api/districts/summary read-only JSON endpoint.",
        "Covered endpoint with regression tests.",
    ]


def test_sync_and_check_detect_drift_and_schema_errors(tmp_path: Path) -> None:
    mod = _load_script()
    backend = tmp_path / "backend_changelog.json"
    web = tmp_path / "web_changelog.json"
    sample = {
        "schemaVersion": 1,
        "repository": "aiwatchdogsindia/aiwatchdogs.in",
        "entries": [
            {
                "pr": 2,
                "prUrl": "https://github.com/aiwatchdogsindia/aiwatchdogs.in/pull/2",
                "mergedAt": "2026-11-25T10:00:00Z",
                "title": "Second PR",
                "items": ["Second change."],
            },
            {
                "pr": 1,
                "prUrl": "https://github.com/aiwatchdogsindia/aiwatchdogs.in/pull/1",
                "mergedAt": "2026-11-24T10:00:00Z",
                "title": "First PR",
                "items": ["First change."],
            },
        ],
    }
    backend.write_text(json.dumps(sample), encoding="utf-8")

    # Missing web mirror -> --check fails
    assert mod.main(["--check", "--backend-path", str(backend), "--web-path", str(web)]) == 1

    # --sync-only mirrors & formats both identically -> --check passes
    assert mod.main(["--sync-only", "--backend-path", str(backend), "--web-path", str(web)]) == 0
    assert mod.main(["--check", "--backend-path", str(backend), "--web-path", str(web)]) == 0
    assert backend.read_text(encoding="utf-8") == web.read_text(encoding="utf-8")

    # Drift between files -> --check fails
    web.write_text(web.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert mod.main(["--check", "--backend-path", str(backend), "--web-path", str(web)]) == 1

    # Unsorted entries -> validation fails
    bad = json.loads(backend.read_text(encoding="utf-8"))
    bad["entries"].reverse()
    raw_bad = mod.serialize_changelog(bad)
    backend.write_text(raw_bad, encoding="utf-8")
    web.write_text(raw_bad, encoding="utf-8")
    errs = mod.validate_changelog_files(backend, web)
    assert any("sorted newest-first" in e for e in errs), errs


def test_repo_canonical_changelog_is_valid_and_in_sync() -> None:
    mod = _load_script()
    errs = mod.validate_changelog_files(mod.DEFAULT_BACKEND_PATH, mod.DEFAULT_WEB_PATH)
    assert errs == [], errs


def test_changelog_workflow_handles_org_pr_restriction_gracefully() -> None:
    wf = (REPO_ROOT / ".github" / "workflows" / "changelog.yml").read_text(encoding="utf-8")
    assert "secrets.CHANGELOG_BOT_TOKEN || secrets.GITHUB_TOKEN" in wf
    assert "not permitted to create or approve pull requests" in wf
    assert "::warning" in wf
    assert "--min-new-prs 10" in wf
    assert "--min-new-prs 1" in wf


def test_min_new_prs_batches_updates_every_10_merged_prs(tmp_path: Path, monkeypatch: Any) -> None:
    mod = _load_script()
    backend = tmp_path / "backend_changelog.json"
    web = tmp_path / "web_changelog.json"
    base_doc = {
        "schemaVersion": 1,
        "repository": "aiwatchdogsindia/aiwatchdogs.in",
        "entries": [
            {
                "pr": 1,
                "prUrl": "https://github.com/aiwatchdogsindia/aiwatchdogs.in/pull/1",
                "mergedAt": "2026-11-20T10:00:00Z",
                "title": "Initial release",
                "items": ["Shipped initial version."],
            }
        ],
    }
    raw = mod.serialize_changelog(base_doc)
    backend.write_text(raw, encoding="utf-8")
    web.write_text(raw, encoding="utf-8")

    def _fake_prs(count: int) -> list[dict[str, Any]]:
        return [
            {
                "number": 10 + i,
                "title": f"feat: batch change {i}",
                "body": f"- Implemented batch change number {i} cleanly.",
                "merged_at": f"2026-11-28T10:{i:02d}:00Z",
                "html_url": f"https://github.com/aiwatchdogsindia/aiwatchdogs.in/pull/{10 + i}",
                "base": {"ref": "main"},
                "head": {"ref": f"feat/change-{i}"},
            }
            for i in range(1, count + 1)
        ]

    # 9 new merged PRs -> below threshold of 10, files stay untouched
    monkeypatch.setattr(mod, "fetch_merged_prs_from_github", lambda repo, token=None: _fake_prs(9))
    rc_9 = mod.main(
        ["--from-github", "--min-new-prs", "10", "--backend-path", str(backend), "--web-path", str(web)]
    )
    assert rc_9 == 0
    assert len(json.loads(backend.read_text(encoding="utf-8"))["entries"]) == 1
    assert backend.read_text(encoding="utf-8") == raw

    # 10 new merged PRs -> hits threshold of 10, both files updated in parity
    monkeypatch.setattr(mod, "fetch_merged_prs_from_github", lambda repo, token=None: _fake_prs(10))
    rc_10 = mod.main(
        ["--from-github", "--min-new-prs", "10", "--backend-path", str(backend), "--web-path", str(web)]
    )
    assert rc_10 == 0
    updated_doc = json.loads(backend.read_text(encoding="utf-8"))
    assert len(updated_doc["entries"]) == 11
    assert backend.read_text(encoding="utf-8") == web.read_text(encoding="utf-8")
