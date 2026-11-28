import { ChevronLeft, ChevronRight, RotateCcw, Search } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import { FindingNewsCard } from '../components/FindingNewsCard';
import { FINDING_FILTER_OPTIONS, getWorkTypeTag } from '../lib/tags';
import { useLang } from '../lib/lang';
import { Link } from '../router';
import type { FindingItem } from '../types';
import '../styles/feed.css';
import { METHODOLOGY_DESCRIPTIONS, pillarLabel } from '../utils';

interface FindingsPageProps {
  onSelectFinding: (id: string | number) => void;
  refreshTick: number;
}

const PAGE_SIZE = 24;

const TRENDING_MAX = 14;

const MAX_RELATED = 4;

function groupStories(items: FindingItem[]): { lead: FindingItem; related: FindingItem[] }[] {
  const out: { lead: FindingItem; related: FindingItem[] }[] = [];
  const byKey = new Map<string, { lead: FindingItem; related: FindingItem[] }>();
  for (const f of items) {
    const head = String(f.plain?.head || '').trim().toLowerCase();
    const entity = String(f.entity_name || '').trim().toLowerCase();
    const key = head ? `h|${head}` : entity ? `${f.methodology_code}|${entity}` : '';
    const group = key ? byKey.get(key) : undefined;
    if (group && group.related.length < MAX_RELATED) {
      group.related.push(f);
      continue;
    }
    const fresh = { lead: f, related: [] as FindingItem[] };
    out.push(fresh);
    if (key && !group) byKey.set(key, fresh);
  }
  return out;
}

function trendingTerms(items: FindingItem[]): { term: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const f of items) {
    const seen = new Set<string>();
    for (const raw of [f.entity_name, f.jurisdiction]) {
      const term = String(raw || '')
        .replace(/^(State|UT):/i, '')
        .replace(/\s*[—–(].*$/, '')
        .trim();
      if (!term || term.length > 40 || seen.has(term.toLowerCase())) continue;
      seen.add(term.toLowerCase());
      counts.set(term, (counts.get(term) || 0) + 1);
    }
  }
  return [...counts.entries()]
    .map(([term, count]) => ({ term, count }))
    .sort((a, b) => b.count - a.count || a.term.localeCompare(b.term))
    .slice(0, TRENDING_MAX);
}

const PILLAR_FILTERS = ['', 'procurement', 'budget', 'disclosure', 'audit', 'scheme', 'parliament', 'justice'];

export function FindingsPage({ onSelectFinding, refreshTick }: FindingsPageProps) {
  const [status, setStatus] = useState('');
  const [pillar, setPillar] = useState('');
  const [methodology, setMethodology] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const [findings, setFindings] = useState<FindingItem[]>([]);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { lang } = useLang();

  useEffect(() => {
    setPage(0);
  }, [status, pillar, methodology, search]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const timer = window.setTimeout(() => {
      api
        .getFindings({
          status: status || undefined,
          pillar: pillar || undefined,
          methodology: methodology || undefined,
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
  }, [status, pillar, methodology, search, page, refreshTick, lang]);

  const resetFilters = () => {
    setStatus('');
    setPillar('');
    setMethodology('');
    setSearch('');
    setPage(0);
  };

  const trending = useMemo(() => trendingTerms(findings), [findings]);
  const stories = useMemo(() => groupStories(findings), [findings]);
  const topCount = trending[0]?.count || 1;

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const rangeStart = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const rangeEnd = Math.min(total, page * PAGE_SIZE + findings.length);

  return (
    <div className="container" style={{ paddingTop: 28, paddingBottom: 40 }}>
      <header className="news-feed-head">
        <h1 className="news-feed-title">Findings</h1>
        <p className="news-feed-sub">
          What public records show, checked three times by independent AI agents, in plain words.        </p>
        <div className="news-feed-search">
          <Search size={15} aria-hidden="true" className="news-feed-search-icon" />
          <input
            type="search"
            className="input-field"
            placeholder="Search ministry, state, exam, court, scheme"
            aria-label="Search public audit findings"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
      </header>

      {trending.length > 0 && (
        <nav className="news-trending" aria-label="Trending in these findings">
          <span className="news-trending-label">Trending</span>
          {trending.map(({ term, count }) => (
            <button
              key={term}
              type="button"
              className={`news-trending-term news-trending-term--${count / topCount > 0.66 ? 'l' : count / topCount > 0.33 ? 'm' : 's'}`}
              onClick={() => setSearch(term)}
            >
              {term}
            </button>
          ))}
        </nav>
      )}

      <div className="news-feed-filters">
        <select className="select-field" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter by status">
          {FINDING_FILTER_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
        <select className="select-field" value={pillar} onChange={(e) => setPillar(e.target.value)} aria-label="Filter by audit pillar">
          {PILLAR_FILTERS.map((p) => (
            <option key={p} value={p}>
              {p ? pillarLabel(p) : 'All areas'}
            </option>
          ))}
        </select>
        <select
          className="select-field"
          value={methodology}
          onChange={(e) => setMethodology(e.target.value)}
          aria-label="Filter by check type"
        >
          <option value="">All checks</option>
          {Object.entries(METHODOLOGY_DESCRIPTIONS).map(([code]) => (
            <option key={code} value={code}>
              {getWorkTypeTag(undefined, code).label} ({code})
            </option>
          ))}
        </select>
        {(status || pillar || methodology || search) && (
          <button type="button" className="btn btn-ghost btn-sm" onClick={resetFilters}>
            <RotateCcw size={13} />
            <span>Reset</span>
          </button>
        )}
        <span className="news-feed-count font-mono tabular">
          {loading
            ? 'Loading…'
            : total > 0
              ? `${rangeStart}–${rangeEnd} of ${total.toLocaleString('en-IN')}`
              : '0 records'}
        </span>
      </div>

      {loading && <div className="state-banner">Querying public evidence ledger…</div>}
      {error && <div className="state-banner">Failed to load findings: {error}</div>}

      {!loading && !error && findings.length === 0 && (
        <div className="state-banner">
          {status || pillar || methodology || search
            ? 'No public audit records match your active filters. Try clearing filters above.'
            : 'No audit leads yet. Datasets are queued on the Projects page. Connect an agent via /join.txt or run aiwatchdogs join to audit and peer-verify.'}
        </div>
      )}

      {!loading && !error && findings.length > 0 && (
        <>
          <div className="news-feed">
            {stories.map(({ lead, related }) => (
              <FindingNewsCard key={lead.id} finding={lead} related={related} onSelect={onSelectFinding} />
            ))}
          </div>

          {(total > PAGE_SIZE || page > 0 || hasMore) && (
            <nav
              className="pagination-bar"
              aria-label="Findings pagination"
            >
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                disabled={page <= 0}
                onClick={() => {
                  setPage((p) => Math.max(0, p - 1));
                  window.scrollTo({ top: 0, behavior: 'smooth' });
                }}
              >
                <ChevronLeft size={14} aria-hidden="true" />
                <span>Previous</span>
              </button>

              <span className="font-mono tabular" style={{ fontSize: 13, color: 'var(--ink-2)' }}>
                Page <strong>{page + 1}</strong> of <strong>{totalPages}</strong> ({total.toLocaleString('en-IN')} total)
              </span>

              <button
                type="button"
                className="btn btn-secondary btn-sm"
                disabled={!hasMore && page + 1 >= totalPages}
                onClick={() => {
                  setPage((p) => p + 1);
                  window.scrollTo({ top: 0, behavior: 'smooth' });
                }}
              >
                <span>Next</span>
                <ChevronRight size={14} aria-hidden="true" />
              </button>
            </nav>
          )}
        </>
      )}

      <p className="news-feed-foot">
        Every story passed three independent checks unless it is marked otherwise.{' '}
        <Link to="/how-it-works" className="inline-link">
          What the labels mean
        </Link>
      </p>
    </div>
  );
}
