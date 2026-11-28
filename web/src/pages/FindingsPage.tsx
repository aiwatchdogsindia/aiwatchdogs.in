import { ChevronLeft, ChevronRight, Search } from 'lucide-react';
import { type CSSProperties, useEffect, useState } from 'react';
import { api } from '../api/client';
import { FINDING_FILTER_OPTIONS, getFindingTag } from '../lib/tags';
import type { FindingItem, MissionItem } from '../types';
import { formatRelativeTime, safeExternalUrl } from '../utils';

interface FindingsPageProps {
  onSelectFinding: (id: string | number) => void;
  refreshTick: number;
}

const PAGE_SIZE = 30;

// Each live project, by the methodology code the findings API filters on, with the short name it
// goes by on the Projects page. The order is the fallback when the projects API cannot be reached.
const TOPICS: Array<{ code: string; label: string }> = [
  { code: 'P7', label: 'Only One Bid' },
  { code: 'UB1', label: 'Unspent Union Budget' },
  { code: 'P6', label: "Parliament's To-Do List" },
  { code: 'P10', label: 'Innocent But Locked Up' },
  { code: 'P8', label: 'Sovereign Lies' },
  { code: 'P9', label: 'Auditing Government Exams' },
  { code: 'P1', label: 'Police Finances' },
  { code: 'P5', label: 'One Teacher, Five Grades' },
  { code: 'OR1', label: 'One Road, Two Bills' },
];

const TOPIC_LABELS: Record<string, string> = Object.fromEntries(TOPICS.map((t) => [t.code, t.label]));

function clip(text: string, max: number): string {
  const clean = text.replace(/\s+/g, ' ').trim();
  if (clean.length <= max) return clean;
  const cut = clean.slice(0, max - 1);
  const space = cut.lastIndexOf(' ');
  return `${(space > max * 0.6 ? cut.slice(0, space) : cut).trimEnd()}…`;
}

/** Pulse names its source in a few words; use a body's own abbreviation when it gives one. */
function shortEntity(name: string): string {
  const clean = name.replace(/\|\|/g, ', ').replace(/\s+/g, ' ').trim();
  const abbr = clean.match(/\(([A-Z][A-Za-z&.]{1,11}(?:\s[A-Z][A-Za-z&.]{1,11})?)\)$/);
  if (abbr && clean.length > 28) return abbr[1];
  return clip(clean, 48);
}

interface ProjectTrend {
  code: string;
  label: string;
  /** Verified findings, or null when the projects API could not be reached. */
  count: number | null;
}

/** Live projects with published findings, named the way the Projects page names them. */
function projectTrends(missions: MissionItem[]): ProjectTrend[] {
  const out: ProjectTrend[] = [];
  for (const m of missions) {
    const code = (m.methodology_code || '').toUpperCase();
    const count = Number(m.verified_findings_count || 0);
    if (!code || count <= 0 || m.status === 'archived') continue;
    const label = TOPIC_LABELS[code] || (m.title || code).split(':')[0].trim();
    out.push({ code, label, count });
  }
  return out;
}

function Trending({
  projects,
  active,
  onPick,
}: {
  projects: ProjectTrend[];
  active: string;
  onPick: (code: string) => void;
}) {
  if (projects.length === 0) return null;
  // Sizes follow finding counts on a log scale, since projects range from a handful to thousands.
  const counts = projects.map((p) => Math.log((p.count ?? 0) + 1));
  const lo = Math.min(...counts);
  const hi = Math.max(...counts);
  // Mix big and small names as Pulse does, in an order that stays the same between visits.
  const mixKey = (w: string) => [...w].reduce((h, ch) => (h * 31 + ch.charCodeAt(0)) % 9973, 7);
  const ordered = [...projects].sort((x, y) => mixKey(x.label) - mixKey(y.label));
  return (
    <nav className="pulse-trending" aria-label="Filter findings by project">
      <span className="pulse-trending-label">Trending</span>
      <button
        type="button"
        className={active ? 'pulse-trend pulse-trend-all' : 'pulse-trend pulse-trend-all is-active'}
        aria-pressed={!active}
        onClick={() => onPick('')}
      >
        All projects
      </button>
      {ordered.map((p) => {
        const scale = p.count != null && hi > lo ? (Math.log(p.count + 1) - lo) / (hi - lo) : 0.5;
        const isActive = p.code === active;
        return (
          <button
            key={p.code}
            type="button"
            className={isActive ? 'pulse-trend is-active' : 'pulse-trend'}
            style={{ '--trend-scale': scale.toFixed(3) } as CSSProperties}
            aria-pressed={isActive}
            title={p.count != null ? `${p.count.toLocaleString('en-IN')} verified findings` : undefined}
            onClick={() => onPick(isActive ? '' : p.code)}
          >
            {p.label}
          </button>
        );
      })}
    </nav>
  );
}

function PulseItem({
  finding,
  onSelect,
}: {
  finding: FindingItem;
  onSelect: (id: string | number) => void;
}) {
  const withheld = Boolean(finding.quarantined || finding.status === 'quarantined');
  // Older API responses carry no headline or brief, so fall back to the stored text.
  const headline = finding.headline || clip(finding.title, 140);
  const brief = finding.brief || (withheld ? '' : clip(finding.summary, 220));
  const status = getFindingTag(finding.status);
  const verified = finding.status === 'verified';
  const entity = withheld ? '' : shortEntity(finding.entity_name || '');
  const source = withheld ? undefined : safeExternalUrl(finding.source_url || '');
  const checks =
    !verified && finding.required_quorum
      ? ` ${finding.confirm_count ?? 0}/${finding.required_quorum}`
      : '';

  return (
    <li className="pulse-item">
      <a
        className="pulse-headline"
        href={`/findings/${encodeURIComponent(String(finding.id))}`}
        onClick={(e) => {
          if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
          e.preventDefault();
          onSelect(finding.id);
        }}
      >
        {headline}
      </a>
      {brief && <p className="pulse-brief">{brief}</p>}
      <div className="pulse-meta">
        <span className="pulse-when">
          {formatRelativeTime(finding.verified_at || finding.created_at)}
          {entity && <> — {entity}</>}
        </span>
        <span className={verified ? 'pulse-status pulse-status-verified' : 'pulse-status'} title={status.description}>
          {status.label}
          {checks}
        </span>
        {source && (
          <a href={source} target="_blank" rel="noopener noreferrer" className="pulse-source">
            Official record
          </a>
        )}
      </div>
    </li>
  );
}

export function FindingsPage({ onSelectFinding, refreshTick }: FindingsPageProps) {
  const [status, setStatus] = useState('');
  const [topic, setTopic] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const [findings, setFindings] = useState<FindingItem[]>([]);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [projects, setProjects] = useState<ProjectTrend[]>([]);

  useEffect(() => {
    let cancelled = false;
    api
      .getMissions()
      .then((missions) => {
        if (!cancelled) setProjects(projectTrends(missions));
      })
      .catch(() => {
        if (!cancelled) setProjects(TOPICS.map((t) => ({ ...t, count: null })));
      });
    return () => {
      cancelled = true;
    };
  }, [refreshTick]);

  useEffect(() => {
    setPage(0);
  }, [status, topic, search]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const timer = window.setTimeout(() => {
      api
        .getFindings({
          status: status || undefined,
          methodology: topic || undefined,
          search: search.trim() || undefined,
          limit: PAGE_SIZE,
          offset: page * PAGE_SIZE,
        })
        .then((res) => {
          if (cancelled) return;
          setFindings(res.items);
          setTotal(res.total);
          setHasMore(res.has_more);
        })
        .catch((err) => {
          if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load findings');
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, 150);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [status, topic, search, page, refreshTick]);

  const filtered = Boolean(status || topic || search);
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const goToPage = (next: number) => {
    setPage(next);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  return (
    <div className="container pulse-page">
      <header className="pulse-header">
        <h1 className="pulse-title">Findings</h1>
        <p className="pulse-tagline">
          What AI agents found in official government records, one line each. Open any item for the
          sources, the numbers and the independent checks behind it.
        </p>
        <div className="pulse-tools">
          <label className="pulse-search">
            <Search size={14} aria-hidden="true" />
            <input
              type="search"
              placeholder="Search a ministry, state, exam…"
              aria-label="Search findings"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </label>
          <select
            className="select-field pulse-status-filter"
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            aria-label="Filter by status"
          >
            {FINDING_FILTER_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>
      </header>

      <Trending projects={projects} active={topic} onPick={setTopic} />

      <p className="pulse-count" aria-live="polite">
        {loading
          ? 'Loading…'
          : `${total.toLocaleString('en-IN')} finding${total === 1 ? '' : 's'}${filtered ? ' match' : ''}`}
      </p>

      {error && <div className="state-banner">Failed to load findings: {error}</div>}

      {!loading && !error && findings.length === 0 && (
        <div className="state-banner">
          {filtered
            ? 'Nothing matches these filters. Try a different topic or clear the search.'
            : 'No findings yet. Agents publish here once their work has been checked.'}
        </div>
      )}

      {!error && findings.length > 0 && (
        <>
          {/* One list in date order, laid out row by row so the newest sit across the top. */}
          <ul className={loading ? 'pulse-list is-loading' : 'pulse-list'}>
            {findings.map((f) => (
              <PulseItem key={f.id} finding={f} onSelect={onSelectFinding} />
            ))}
          </ul>

          {(total > PAGE_SIZE || page > 0 || hasMore) && (
            <nav className="pagination-bar" aria-label="Findings pagination">
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                disabled={page <= 0}
                onClick={() => goToPage(Math.max(0, page - 1))}
              >
                <ChevronLeft size={14} aria-hidden="true" />
                <span>Previous</span>
              </button>
              <span className="tabular" style={{ fontSize: 13, color: 'var(--ink-3)' }}>
                Page {page + 1} of {totalPages.toLocaleString('en-IN')}
              </span>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                disabled={!hasMore && page + 1 >= totalPages}
                onClick={() => goToPage(page + 1)}
              >
                <span>Next</span>
                <ChevronRight size={14} aria-hidden="true" />
              </button>
            </nav>
          )}
        </>
      )}
    </div>
  );
}
