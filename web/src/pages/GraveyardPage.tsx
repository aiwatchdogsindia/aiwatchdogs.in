import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import { FINDING_TAGS, getFindingTag } from '../lib/tags';
import { Link, useRouter } from '../router';
import '../styles/graveyard.css';
import type { GraveyardCause, GraveyardEntry } from '../types';
import { safeExternalUrl } from '../utils';

interface GraveyardPageProps {
  onSelectFinding: (id: string | number) => void;
  refreshTick: number;
}

const CAUSE_PILLS: Array<{ key: '' | GraveyardCause; label: string }> = [
  { key: '', label: 'All' },
  { key: 'false_positive', label: 'False positive' },
  { key: 'editorial_retraction', label: 'Retracted' },
  { key: 'peer_dispute', label: 'Peer dispute' },
  { key: 'withdrawn_review', label: 'Withdrawn for review' },
];

function isWideViewport(): boolean {
  if (typeof window === 'undefined' || !window.matchMedia) return true;
  return !window.matchMedia('(max-width: 760px)').matches;
}

function formatDayTime(iso: string): string {
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return iso || '—';
  return new Date(ts).toLocaleString('en-IN', {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

function formatLivedSpan(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}s`;
  const mins = Math.floor(s / 60);
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 48) return `${hrs}h`;
  const days = Math.floor(hrs / 24);
  return `${days}d`;
}

function chipForCause(entry: GraveyardEntry) {
  if (entry.cause === 'withdrawn_review') {
    return FINDING_TAGS.in_review;
  }
  return getFindingTag(entry.status);
}

export function GraveyardPage({ onSelectFinding, refreshTick }: GraveyardPageProps) {
  const { search, hash } = useRouter();

  const [cause, setCause] = useState<'' | GraveyardCause>('');
  const [project, setProject] = useState<string>(() => {
    const fromSearch = new URLSearchParams(search).get('project');
    if (fromSearch) return fromSearch;
    const fromHash = new URLSearchParams((hash || '').replace(/^#/, '')).get('project');
    return fromHash || '';
  });
  const [entries, setEntries] = useState<GraveyardEntry[]>([]);
  const [projects, setProjects] = useState<Array<{ slug: string; title: string }>>([]);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestSeqRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);

  const loadPage = useCallback(
    async (offset: number, limit: number, append: boolean) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      const reqId = ++requestSeqRef.current;

      if (append) {
        setLoadingMore(true);
      } else {
        setLoading(true);
        setLoadingMore(false);
      }
      setError(null);
      try {
        const res = await api.getGraveyard(
          {
            cause: cause || undefined,
            project: project || undefined,
            limit,
            offset,
          },
          { signal: controller.signal },
        );
        if (reqId !== requestSeqRef.current) return;
        setEntries((prev) => (append ? [...prev, ...res.entries] : res.entries));
        setTotal(res.total);
        setHasMore(res.has_more);
        if (res.projects.length > 0) {
          setProjects(res.projects);
        }
      } catch (err) {
        if (reqId !== requestSeqRef.current || (err instanceof Error && err.name === 'AbortError')) {
          return;
        }
        setError(err instanceof Error ? err.message : 'Failed to load graveyard');
      } finally {
        if (reqId === requestSeqRef.current) {
          setLoading(false);
          setLoadingMore(false);
        }
      }
    },
    [cause, project],
  );

  useEffect(() => {
    const initialLimit = isWideViewport() ? 6 : 2;
    void loadPage(0, initialLimit, false);
    return () => {
      requestSeqRef.current += 1;
      abortRef.current?.abort();
    };
  }, [loadPage, refreshTick]);

  const handleLoadMore = () => {
    const step = isWideViewport() ? 12 : 6;
    void loadPage(entries.length, step, true);
  };

  return (
    <div className="container graveyard-page">
      <div className="section-kicker">The graveyard</div>
      <h1 className="graveyard-title">Every lead that didn&apos;t survive, and why.</h1>
      <p className="graveyard-lead">
        A dead end is on the record too. Each one here names what the lead claimed, the peer check
        or editorial audit that ended it, and what stays on the public record so nobody repeats it.
      </p>

      <div className="graveyard-filters">
        <div className="graveyard-pills" role="group" aria-label="Cause">
          <span className="graveyard-filter-label">Cause</span>
          {CAUSE_PILLS.map((pill) => (
            <button
              key={pill.key || 'all'}
              type="button"
              aria-pressed={cause === pill.key}
              onClick={() => setCause(pill.key)}
            >
              {pill.label}
            </button>
          ))}
        </div>

        <div className="graveyard-picks">
          <label className="graveyard-pick">
            <span className="graveyard-filter-label">Project</span>
            <span className="graveyard-select-wrap">
              <select
                value={project}
                onChange={(e) => setProject(e.target.value)}
                aria-label="Filter graveyard by project"
              >
                <option value="">All projects</option>
                {projects.map((p) => (
                  <option key={p.slug} value={p.slug}>
                    {p.title}
                  </option>
                ))}
              </select>
            </span>
          </label>
          <span className="graveyard-count">
            {loading ? 'Loading…' : `${entries.length.toLocaleString('en-IN')} of ${total.toLocaleString('en-IN')}`}
          </span>
        </div>
      </div>

      {error ? (
        <div className="state-banner">Failed to load graveyard: {error}</div>
      ) : loading ? (
        <div className="state-banner">Reading retired leads from the public record…</div>
      ) : entries.length === 0 ? (
        <p className="graveyard-empty">Nothing of this kind has died yet.</p>
      ) : (
        <ul className="graves">
          {entries.map((entry) => {
            const chip = chipForCause(entry);
            const safeEvidence = safeExternalUrl(entry.source_evidence_url);
            return (
              <li key={entry.id} className={`grave is-${entry.status}`}>
                <p className="grave-top">
                  <span className={chip.badgeClass}>{entry.cause_label}</span>
                  <span className="grave-dates">
                    {formatDayTime(entry.proposed_at)} to {formatDayTime(entry.ended_at)} · lived{' '}
                    {formatLivedSpan(entry.lived_seconds)}
                  </span>
                </p>
                <h2>
                  <button
                    type="button"
                    className="grave-title-btn"
                    onClick={() => onSelectFinding(entry.id)}
                  >
                    {entry.id} · {entry.title}
                  </button>
                </h2>
                <p className="grave-claimed">Claimed: {entry.claimed}</p>
                <p className="grave-cause">
                  {entry.cause_detail}
                  {safeEvidence ? (
                    <>
                      {' '}
                      (
                      <a href={safeEvidence} target="_blank" rel="noopener noreferrer">
                        source evidence
                      </a>
                      )
                    </>
                  ) : null}
                </p>
                <p className="grave-survived">{entry.survived}</p>
                <Link
                  className="grave-prog"
                  to={`/projects/${encodeURIComponent(entry.mission_slug)}`}
                >
                  {entry.project_title}
                </Link>
              </li>
            );
          })}
        </ul>
      )}

      <div className="graveyard-foot">
        {hasMore && !loading ? (
          <button
            type="button"
            className="btn btn-primary"
            disabled={loadingMore}
            onClick={handleLoadMore}
          >
            {loadingMore ? 'Loading…' : 'Load more'}
          </button>
        ) : null}
        <a href="/graveyard.atom" className="inline-link">
          Follow the graveyard
        </a>
      </div>
    </div>
  );
}
