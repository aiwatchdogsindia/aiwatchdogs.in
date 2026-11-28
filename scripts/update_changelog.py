#!/usr/bin/env python3
"""Sync and validate the canonical PR-numbered changelog JSON for backend and frontend.

Stdlib-only script (zero third-party dependencies) so it can run in any Python 3.11+
environment and inside GitHub Actions without installing packages.

Modes:
  --from-github   Fetch merged PRs targeting ``main`` from the GitHub REST API, preserve
                  curated titles/items on existing PRs while refreshing authoritative
                  ``mergedAt`` timestamps, add newly merged PRs automatically (skipping
                  automated changelog-sync PRs), and write both canonical JSON files.
  --sync-only     Re-format/sort canonical ``src/aiwatchdogs/hub/changelog.json`` and
                  mirror it byte-for-byte to ``web/src/lib/changelog.json`` offline.
  --check         Validate schema, timestamps, ordering, uniqueness, and exact parity
                  between ``src/aiwatchdogs/hub/changelog.json`` and
                  ``web/src/lib/changelog.json`` (non-zero exit on any problem).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BACKEND_PATH = REPO_ROOT / "src" / "aiwatchdogs" / "hub" / "changelog.json"
DEFAULT_WEB_PATH = REPO_ROOT / "web" / "src" / "lib" / "changelog.json"
DEFAULT_REPO = "aiwatchdogsindia/aiwatchdogs.in"
SCHEMA_VERSION = 1

_ISO_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_CONVENTIONAL_PREFIX_RE = re.compile(
    r"^(?:feat|fix|chore|docs|style|refactor|perf|test|build|ci|security|project)(?:\([^)]+\))?!?:\s*",
    re.IGNORECASE,
)
_AUTO_CHANGELOG_TITLE_RE = re.compile(r"^chore\(changelog\)\s*:", re.IGNORECASE)
_AUTO_CHANGELOG_BRANCHES = frozenset({"chore/auto-changelog-sync", "auto/changelog-sync"})
# PRs merged and reverted within minutes (already summarized under their revert PR #72).
SUPPRESSED_PR_NUMBERS: frozenset[int] = frozenset({65, 66, 67, 68})
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_SKIP_SECTION_RE = re.compile(
    r"^\s*#{1,6}\s*(?:test(?:ing|s|\s+plan)?|verification|qa|screenshots?|checklist|how\s+to\s+test|ci|notes?\s+for\s+reviewers?)\b",
    re.IGNORECASE,
)
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+(.*)$")
_CHECKBOX_RE = re.compile(r"^\[[ xX]\]\s*")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_INLINE_CODE_WRAP_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")


def normalize_utc_iso(raw: str) -> str:
    """Parse an ISO-8601 timestamp and normalize to UTC ``YYYY-MM-DDTHH:MM:SSZ``."""
    text = str(raw or "").strip()
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean_pr_title(raw_title: str) -> str:
    """Convert a PR title into a clean, sentence-case plain-English changelog heading."""
    cleaned = _CONVENTIONAL_PREFIX_RE.sub("", str(raw_title or "").strip()).strip()
    cleaned = re.sub(r"\s*\(#\d+\)\s*$", "", cleaned).strip()
    if not cleaned:
        return "Repository update"
    return cleaned[0].upper() + cleaned[1:]


def _clean_markdown_line(line: str) -> str:
    text = _CHECKBOX_RE.sub("", line.strip())
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _BOLD_RE.sub(r"\1", text)
    text = _INLINE_CODE_WRAP_RE.sub(r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_pr_bullets(body: str | None, fallback_title: str, *, max_items: int = 8) -> list[str]:
    """Extract concise plain-English changelog bullets from a PR description."""
    raw = _HTML_COMMENT_RE.sub("", str(body or ""))
    items: list[str] = []
    seen: set[str] = set()
    in_skipped_section = False
    in_code_fence = False

    for raw_line in raw.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            in_code_fence = not in_code_fence
            continue
        if in_code_fence:
            continue
        if stripped.startswith("#"):
            in_skipped_section = bool(_SKIP_SECTION_RE.match(stripped))
            continue
        if in_skipped_section:
            continue
        match = _BULLET_RE.match(raw_line)
        if not match:
            continue
        # Skip raw image / screenshot lines and checkbox test items.
        candidate = match.group(1).strip()
        if candidate.startswith("![") or _CHECKBOX_RE.match(candidate):
            continue
        cleaned = _clean_markdown_line(candidate)
        if len(cleaned) < 12 or cleaned.lower().startswith(("screenshot:", "co-authored-by:", "signed-off-by:")):
            continue
        if len(cleaned) > 360:
            cleaned = cleaned[:357].rstrip() + "..."
        norm_key = cleaned.lower()
        if norm_key not in seen:
            seen.add(norm_key)
            items.append(cleaned)
            if len(items) >= max_items:
                break

    if not items:
        fallback = clean_pr_title(fallback_title)
        if not fallback.endswith("."):
            fallback += "."
        items = [fallback]
    return items


def is_automated_changelog_pr(pr_obj: dict[str, Any]) -> bool:
    """Return True if *pr_obj* is an automated changelog-sync PR (or suppressed reverted stub) that should not self-log."""
    raw_num = pr_obj.get("number")
    if isinstance(raw_num, int) and not isinstance(raw_num, bool) and raw_num in SUPPRESSED_PR_NUMBERS:
        return True
    title = str(pr_obj.get("title") or "").strip()
    if _AUTO_CHANGELOG_TITLE_RE.match(title):
        return True
    head = pr_obj.get("head")
    if isinstance(head, dict):
        ref = str(head.get("ref") or "").strip().lower()
        if ref in _AUTO_CHANGELOG_BRANCHES or ref.startswith("chore/auto-changelog"):
            return True
    return False


def _sort_key(entry: dict[str, Any]) -> tuple[str, int, str]:
    merged_at = str(entry.get("mergedAt") or "")
    pr_num = int(entry["pr"]) if isinstance(entry.get("pr"), int) else -1
    commit = str(entry.get("commit") or "")
    return (merged_at, pr_num, commit)


def normalize_document(doc: dict[str, Any], *, repository: str = DEFAULT_REPO) -> dict[str, Any]:
    """Normalize, deduplicate, and sort a changelog JSON document newest-first."""
    repo = str(doc.get("repository") or repository).strip() or repository
    raw_entries = doc.get("entries")
    if not isinstance(raw_entries, list):
        raise ValueError("changelog.json 'entries' must be a list")

    deduped: list[dict[str, Any]] = []
    seen_prs: set[int] = set()
    seen_commits: set[str] = set()

    for raw in raw_entries:
        if not isinstance(raw, dict):
            raise ValueError("Each changelog entry must be a JSON object")
        entry: dict[str, Any] = {}
        pr = raw.get("pr")
        commit = raw.get("commit")
        if pr is not None:
            if isinstance(pr, bool) or not isinstance(pr, int) or pr <= 0:
                raise ValueError(f"Invalid PR number: {pr!r}")
            if pr in seen_prs:
                continue
            seen_prs.add(pr)
            entry["pr"] = pr
            entry["prUrl"] = str(raw.get("prUrl") or f"https://github.com/{repo}/pull/{pr}")
        elif commit is not None:
            cstr = str(commit).strip()
            if not re.fullmatch(r"[0-9a-f]{7,40}", cstr):
                raise ValueError(f"Invalid commit SHA: {commit!r}")
            if cstr in seen_commits:
                continue
            seen_commits.add(cstr)
            entry["commit"] = cstr
            entry["commitUrl"] = str(raw.get("commitUrl") or f"https://github.com/{repo}/commit/{cstr}")
        else:
            raise ValueError("Every changelog entry must specify either 'pr' or 'commit'")

        entry["mergedAt"] = normalize_utc_iso(str(raw.get("mergedAt") or ""))
        title = str(raw.get("title") or "").strip()
        if not title:
            raise ValueError(f"Changelog entry {pr or commit} is missing 'title'")
        entry["title"] = title
        items = raw.get("items")
        if not isinstance(items, list) or not items or not all(isinstance(x, str) and x.strip() for x in items):
            raise ValueError(f"Changelog entry {pr or commit} must have a non-empty list of string 'items'")
        entry["items"] = [x.strip() for x in items]
        deduped.append(entry)

    deduped.sort(key=_sort_key, reverse=True)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "repository": repo,
        "entries": deduped,
    }


def merge_github_prs(doc: dict[str, Any], prs: list[dict[str, Any]], *, repository: str = DEFAULT_REPO) -> dict[str, Any]:
    """Merge closed/merged GitHub PR records into *doc*, preserving curated copy on existing PRs."""
    normalized = normalize_document(doc, repository=repository)
    repo = normalized["repository"]
    by_pr: dict[int, dict[str, Any]] = {
        int(e["pr"]): dict(e) for e in normalized["entries"] if isinstance(e.get("pr"), int)
    }
    non_pr_entries = [dict(e) for e in normalized["entries"] if not isinstance(e.get("pr"), int)]

    for pr_obj in prs:
        if not isinstance(pr_obj, dict):
            continue
        merged_at = pr_obj.get("merged_at")
        if not merged_at:
            continue
        base = pr_obj.get("base")
        if isinstance(base, dict) and base.get("ref") and base.get("ref") != "main":
            continue
        if is_automated_changelog_pr(pr_obj):
            continue
        num = pr_obj.get("number")
        if isinstance(num, bool) or not isinstance(num, int) or num <= 0:
            continue
        norm_ts = normalize_utc_iso(str(merged_at))
        pr_url = str(pr_obj.get("html_url") or f"https://github.com/{repo}/pull/{num}")

        if num in by_pr:
            # Preserve curated human/editorial title and bullets; sync authoritative merge timestamp & URL.
            by_pr[num]["mergedAt"] = norm_ts
            by_pr[num]["prUrl"] = pr_url
        else:
            raw_title = str(pr_obj.get("title") or f"Pull request #{num}")
            by_pr[num] = {
                "pr": num,
                "prUrl": pr_url,
                "mergedAt": norm_ts,
                "title": clean_pr_title(raw_title),
                "items": extract_pr_bullets(pr_obj.get("body"), raw_title),
            }

    combined = {
        "schemaVersion": SCHEMA_VERSION,
        "repository": repo,
        "entries": [*by_pr.values(), *non_pr_entries],
    }
    return normalize_document(combined, repository=repo)


def fetch_merged_prs_from_github(repository: str, *, token: str | None = None, max_pages: int = 5) -> list[dict[str, Any]]:
    """Fetch closed PRs targeting ``main`` via the GitHub REST API (stdlib urllib)."""
    prs: list[dict[str, Any]] = []
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "aiwatchdogs-changelog-sync/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    for page in range(1, max_pages + 1):
        url = (
            f"https://api.github.com/repos/{repository}/pulls"
            f"?state=closed&base=main&sort=updated&direction=desc&per_page=100&page={page}"
        )
        req = Request(url, headers=headers)
        try:
            with urlopen(req, timeout=20) as resp:
                batch = json.loads(resp.read().decode("utf-8"))
        except (HTTPError, URLError) as exc:
            raise RuntimeError(f"Failed to fetch merged PRs from {url}: {exc}") from exc
        if not isinstance(batch, list) or not batch:
            break
        prs.extend(item for item in batch if isinstance(item, dict) and item.get("merged_at"))
        if len(batch) < 100:
            break
    return prs


def count_new_pr_entries(existing_doc: dict[str, Any], updated_doc: dict[str, Any]) -> int:
    """Return the number of newly added PR entries in *updated_doc* compared to *existing_doc*."""
    old_prs = {
        int(e["pr"])
        for e in (existing_doc.get("entries") or [])
        if isinstance(e, dict) and isinstance(e.get("pr"), int) and not isinstance(e.get("pr"), bool)
    }
    new_prs = {
        int(e["pr"])
        for e in (updated_doc.get("entries") or [])
        if isinstance(e, dict) and isinstance(e.get("pr"), int) and not isinstance(e.get("pr"), bool)
    }
    return len(new_prs - old_prs)


def serialize_changelog(doc: dict[str, Any]) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def validate_changelog_files(backend_path: Path, web_path: Path) -> list[str]:
    """Validate schema, timestamps, ordering, uniqueness, and exact backend/web JSON parity."""
    errors: list[str] = []
    if not backend_path.is_file():
        errors.append(f"Missing canonical backend changelog: {backend_path}")
    if not web_path.is_file():
        errors.append(f"Missing frontend changelog mirror: {web_path}")
    if errors:
        return errors

    backend_raw = backend_path.read_text(encoding="utf-8")
    web_raw = web_path.read_text(encoding="utf-8")
    try:
        backend_doc = json.loads(backend_raw)
    except json.JSONDecodeError as exc:
        return [f"Invalid JSON in {backend_path}: {exc}"]
    try:
        web_doc = json.loads(web_raw)
    except json.JSONDecodeError as exc:
        return [f"Invalid JSON in {web_path}: {exc}"]

    if backend_doc.get("schemaVersion") != SCHEMA_VERSION:
        errors.append(f"Expected schemaVersion={SCHEMA_VERSION}, got {backend_doc.get('schemaVersion')!r}")

    entries = backend_doc.get("entries")
    if not isinstance(entries, list) or not entries:
        errors.append("changelog.json must contain a non-empty 'entries' array")
        return errors

    seen_prs: set[int] = set()
    seen_commits: set[str] = set()
    prev_ts: str | None = None
    for idx, entry in enumerate(entries):
        where = f"entries[{idx}]"
        if not isinstance(entry, dict):
            errors.append(f"{where} must be a JSON object")
            continue
        has_pr = "pr" in entry and entry["pr"] is not None
        has_commit = "commit" in entry and entry["commit"] is not None
        if has_pr == has_commit:
            errors.append(f"{where} must set exactly one of 'pr' or 'commit'")
        if has_pr:
            pr = entry["pr"]
            if isinstance(pr, bool) or not isinstance(pr, int) or pr <= 0:
                errors.append(f"{where}.pr must be a positive integer")
            elif pr in seen_prs:
                errors.append(f"Duplicate PR number #{pr} at {where}")
            else:
                seen_prs.add(pr)
        if has_commit:
            commit = str(entry["commit"])
            if commit in seen_commits:
                errors.append(f"Duplicate commit {commit} at {where}")
            else:
                seen_commits.add(commit)
        ts = entry.get("mergedAt")
        if not isinstance(ts, str) or not _ISO_UTC_RE.fullmatch(ts):
            errors.append(f"{where}.mergedAt must be UTC ISO-8601 (YYYY-MM-DDTHH:MM:SSZ), got {ts!r}")
        else:
            if prev_ts is not None and ts > prev_ts:
                errors.append(f"Entries are not sorted newest-first at {where} ({ts} > {prev_ts})")
            prev_ts = ts
        if not isinstance(entry.get("title"), str) or not entry["title"].strip():
            errors.append(f"{where}.title must be a non-empty string")
        items = entry.get("items")
        if not isinstance(items, list) or not items or not all(isinstance(x, str) and x.strip() for x in items):
            errors.append(f"{where}.items must be a non-empty list of strings")

    if backend_raw != web_raw or backend_doc != web_doc:
        errors.append(
            f"{backend_path} and {web_path} are out of sync. Run: python3 scripts/update_changelog.py --sync-only"
        )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--from-github", action="store_true", help="Fetch merged PRs from GitHub and update both JSON files")
    mode.add_argument("--sync-only", action="store_true", help="Normalize backend JSON and mirror it to web/src/lib/changelog.json")
    mode.add_argument("--check", action="store_true", help="Verify schema, ordering, and exact backend/web parity")
    parser.add_argument("--repo", default=DEFAULT_REPO, help=f"GitHub owner/repo (default: {DEFAULT_REPO})")
    parser.add_argument("--backend-path", type=Path, default=DEFAULT_BACKEND_PATH)
    parser.add_argument("--web-path", type=Path, default=DEFAULT_WEB_PATH)
    parser.add_argument("--backend-only", action="store_true", help="Write only backend JSON (skip writing web JSON)")
    parser.add_argument(
        "--min-new-prs",
        type=int,
        default=1,
        help="With --from-github, only write updates once at least this many new merged PRs have accumulated (default: 1).",
    )
    args = parser.parse_args(argv)

    if args.check:
        errors = validate_changelog_files(args.backend_path, args.web_path)
        if errors:
            for err in errors:
                print(f"ERROR: {err}", file=sys.stderr)
            return 1
        doc = json.loads(args.backend_path.read_text(encoding="utf-8"))
        print(f"OK: {len(doc['entries'])} changelog entries verified and in sync.")
        return 0

    existing = json.loads(args.backend_path.read_text(encoding="utf-8"))
    if args.from_github:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or None
        prs = fetch_merged_prs_from_github(args.repo, token=token)
        updated = merge_github_prs(existing, prs, repository=args.repo)
        new_pr_count = count_new_pr_entries(existing, updated)
        min_new = max(1, int(args.min_new_prs))
        if min_new > 1 and new_pr_count < min_new:
            print(
                f"Holding changelog sync: {new_pr_count} new merged PR(s) since last update "
                f"(waiting for batch of {min_new})."
            )
            return 0
    else:
        updated = normalize_document(existing, repository=args.repo)

    rendered = serialize_changelog(updated)
    args.backend_path.parent.mkdir(parents=True, exist_ok=True)
    args.backend_path.write_text(rendered, encoding="utf-8")
    if not args.backend_only:
        args.web_path.parent.mkdir(parents=True, exist_ok=True)
        args.web_path.write_text(rendered, encoding="utf-8")
    print(f"Updated changelog with {len(updated['entries'])} entries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
