import rawChangelog from './changelog.json';

/**
 * Public changelog, newest first, numbered by GitHub PR with exact ISO-8601 UTC timestamps.
 * Entries live in `changelog.json` and stay byte-for-byte synced with
 * `src/aiwatchdogs/hub/changelog.json` via `scripts/update_changelog.py` and
 * `.github/workflows/changelog.yml`.
 */
export interface ChangelogEntry {
  /** GitHub pull request number (omitted only for pre-PR launch commits). */
  pr?: number;
  /** Canonical GitHub pull request URL. */
  prUrl?: string;
  /** Short commit SHA for pre-PR launch milestones. */
  commit?: string;
  /** Canonical GitHub commit URL for pre-PR launch milestones. */
  commitUrl?: string;
  /** Exact ISO-8601 UTC timestamp when the PR merged (or commit landed). */
  mergedAt: string;
  /** Concise plain-English release heading. */
  title: string;
  /** Plain-English summary items of what changed. */
  items: readonly string[];
}

export interface ChangelogDocument {
  schemaVersion: number;
  repository: string;
  entries: readonly ChangelogEntry[];
}

const doc = rawChangelog as ChangelogDocument;

export const CHANGELOG_REPOSITORY: string = doc.repository;
export const CHANGELOG_ENTRIES: readonly ChangelogEntry[] = doc.entries;
