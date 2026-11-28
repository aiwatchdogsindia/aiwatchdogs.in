import { ChevronLeft, ChevronRight, Search } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { FINDING_FILTER_OPTIONS, getFindingTag } from '../lib/tags';
import type { FindingItem } from '../types';
import { formatRelativeTime, safeExternalUrl } from '../utils';

interface FindingsPageProps {
  onSelectFinding: (id: string | number) => void;
  refreshTick: number;
}

const PAGE_SIZE = 30;

// One chip per kind of check, named the way a reader would say it. `code` is the check's
// methodology code, which is what the findings API filters on.
const TOPICS: Array<{ code: string; label: string }> = [
  { code: '', label: 'All' },
  { code: 'UB1', label: 'Unspent budget' },
  { code: 'P7', label: 'Tenders' },
  { code: 'P9', label: 'Exams' },
  { code: 'P10', label: 'Custody' },
  { code: 'P8', label: 'Scheme promises' },
  { code: 'P6', label: 'Parliament promises' },
  { code: 'P1', label: 'Police budgets' },
  { code: 'P5', label: 'Schools' },
  { code: 'OR1', label: 'Duplicate billing' },
];

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

// Words that say nothing on their own, left out of the trending line.
const STOPWORDS = new Set(
  (
    'a an and are as at be been but by for from had has have in into is it its of on or over than that the ' +
    'their them then there these this those to under up was were which while with within without per each ' +
    'all any also after before between both did does during more most no not only other same some such ' +
    'very about against below above out off one two three four five six seven eight nine ten its own ' +
    'listed shows show recorded records record name named lists list issued gave given set held who ' +
    'down struck high covers ruled order person directed plea had has partial'
  ).split(' '),
);

interface TrendTerm {
  word: string;
  count: number;
}

/** The most repeated words across recent headlines, the way Pulse builds its trending line. */
function trendingTerms(findings: FindingItem[], max = 42): TrendTerm[] {
  const counts = new Map<string, number>();
  for (const f of findings) {
    const text = (f.headline || f.title || '').toLowerCase();
    const seen = new Set<string>();
    for (const raw of text.split(/[^a-z\u0900-\u097f&'-]+/)) {
      const word = raw.replace(/^['-]+|['-]+$/g, '').replace(/'s$/, '');
      if (word.length < 3 || STOPWORDS.has(word) || seen.has(word)) continue;
      seen.add(word);
      counts.set(word, (counts.get(word) || 0) + 1);
    }
  }
  return [...counts.entries()]
    .filter(([, n]) => n >= 2)
    .sort((x, y) => y[1] - x[1] || x[0].localeCompare(y[0]))
    .slice(0, max)
    .map(([word, count]) => ({ word, count }));
}

function Trending({ terms, onPick }: { terms: TrendTerm[]; onPick: (word: string) => void }) {
  if (terms.length === 0) return null;
  const top = terms[0].count;
  // Mix big and small words as Pulse does, in an order that stays the same between renders.
  const mixKey = (w: string) => [...w].reduce((h, ch) => (h * 31 + ch.charCodeAt(0)) % 9973, 7);
  const ordered = [...terms].sort((x, y) => mixKey(x.word) - mixKey(y.word));
  return (
    <nav className="pulse-trending" aria-label="Trending words in recent findings">
      <span className="pulse-trending-label">Trending</span>
      {ordered.map((t) => {
        const scale = Math.sqrt(t.count / top);
        return (
          <button
            key={t.word}
            type="button"
            className="pulse-trend"
            style={{ fontSize: `${(13 + scale * 17).toFixed(1)}px` }}
            title={`${t.count} recent findings mention “${t.word}”`}
            onClick={() => onPick(t.word)}
          >
            {t.word}
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

function columnsFor(width: number): number {
  if (width >= 1000) return 3;
  if (width >= 640) return 2;
  return 1;
}

function useColumnCount(): number {
  const [count, setCount] = useState(() => columnsFor(window.innerWidth));
  useEffect(() => {
    const onResize = () => setCount(columnsFor(window.innerWidth));
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  return count;
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
  const [trending, setTrending] = useState<TrendTerm[]>([]);
  const columns = useColumnCount();

  useEffect(() => {
    let cancelled = false;
    api
      .getFindings({ methodology: topic || undefined, limit: 100, offset: 0 })
      .then((res) => {
        if (!cancelled) setTrending(trendingTerms(res.items));
      })
      .catch(() => {
        if (!cancelled) setTrending([]);
      });
    return () => {
      cancelled = true;
    };
  }, [topic, refreshTick]);

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

      <Trending terms={trending} onPick={setSearch} />

      <nav className="pulse-topics" aria-label="Filter findings by topic">
        {TOPICS.map((t) => (
          <button
            key={t.code || 'all'}
            type="button"
            className={t.code === topic ? 'pulse-topic is-active' : 'pulse-topic'}
            aria-pressed={t.code === topic}
            onClick={() => setTopic(t.code)}
          >
            {t.label}
          </button>
        ))}
      </nav>

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
          {/* Newest items go across the top row, then down, as on Pulse. */}
          <div className={loading ? 'pulse-list is-loading' : 'pulse-list'}>
            {Array.from({ length: columns }, (_, col) => (
              <ul key={col} className="pulse-column">
                {findings
                  .filter((_, i) => i % columns === col)
                  .map((f) => (
                    <PulseItem key={f.id} finding={f} onSelect={onSelectFinding} />
                  ))}
              </ul>
            ))}
          </div>

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
