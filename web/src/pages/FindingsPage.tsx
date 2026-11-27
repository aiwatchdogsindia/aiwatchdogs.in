import { ChevronLeft, ChevronRight, Filter, RotateCcw, Search } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { FindingCard } from '../components/FindingCard';
import {
  ACTION_TAGS,
  FINDING_FILTER_OPTIONS,
  FINDING_TAGS,
  getWorkTypeTag,
} from '../lib/tags';
import { Link } from '../router';
import type { FindingItem } from '../types';
import { METHODOLOGY_DESCRIPTIONS, pillarLabel } from '../utils';

interface FindingsPageProps {
  onSelectFinding: (id: string | number) => void;
  refreshTick: number;
}

const PAGE_SIZE = 24;

const KEY_TAGS = [
  FINDING_TAGS.verified,
  FINDING_TAGS.in_review,
  FINDING_TAGS.split_vote,
  FINDING_TAGS.cleared,
  FINDING_TAGS.disputed,
  FINDING_TAGS.not_enough_data,
  ACTION_TAGS.rti_draft,
  ACTION_TAGS.rti_filed,
];

const PILLAR_FILTERS = ['', 'budget', 'disclosure', 'audit', 'scheme', 'parliament'];

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
  }, [status, pillar, methodology, search, page, refreshTick]);

  const resetFilters = () => {
    setStatus('');
    setPillar('');
    setMethodology('');
    setSearch('');
    setPage(0);
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const rangeStart = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const rangeEnd = Math.min(total, page * PAGE_SIZE + findings.length);

  return (
    <div className="container" style={{ paddingTop: 28, paddingBottom: 40 }}>
      <div className="section-header">
        <div>
          <div className="section-kicker">Public Evidence Wall, Deterministic &amp; Neutral</div>
          <h1 className="section-title">
            Findings &amp; Evidence Ledger
          </h1>
          <p className="section-subtitle">
            Click any card to open its full Evidence Dossier with deterministic calculations,
            cryptographic provenance, editorial correction history, mandatory benign explanations,
            explicit peer verification checks, and RTI Act 2005 Sec 6(1) draft generator.
          </p>
        </div>
      </div>

      {/* Plain-English Tag Key */}
      <div className="card-sunk card-pad-sm findings-tag-key">
        <span className="findings-tag-key-label">Tag key:</span>
        {KEY_TAGS.map((tag) => (
          <span key={tag.id} className={tag.badgeClass} title={tag.description}>
            {tag.label}
          </span>
        ))}
        <Link to="/how-it-works" className="inline-link" style={{ fontSize: 12.5, marginLeft: 'auto' }}>
          Full tag guide
        </Link>
      </div>

      {/* Filter Bar */}
      <div className="filter-bar">
        <div className="filter-search-wrap">
          <Search
            size={14}
            aria-hidden="true"
            style={{
              position: 'absolute',
              left: 10,
              top: '50%',
              transform: 'translateY(-50%)',
              color: 'var(--ink-3)',
            }}
          />
          <input
            type="text"
            className="input-field"
            style={{ width: '100%', paddingLeft: 32 }}
            placeholder="Search ministry, state, rule, title, or entity…"
            aria-label="Search public audit findings"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>

        <select
          className="select-field"
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

        <select
          className="select-field"
          value={pillar}
          onChange={(e) => setPillar(e.target.value)}
          aria-label="Filter by audit pillar"
        >
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
      </div>

      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: 14,
          fontSize: 13,
          color: 'var(--ink-3)',
          flexWrap: 'wrap',
          gap: 10,
        }}
      >
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <Filter size={13} aria-hidden="true" />
          <span className="font-mono tabular">
            {loading ? (
              'Querying public evidence ledger…'
            ) : total > 0 ? (
              <>
                Showing <strong>{rangeStart}–{rangeEnd}</strong> of <strong>{total.toLocaleString('en-IN')}</strong>{' '}
                public audit record{total === 1 ? '' : 's'}
              </>
            ) : (
              'Showing 0 public audit records'
            )}
          </span>
        </span>

        {!loading && total > PAGE_SIZE && (
          <span className="font-mono tabular" style={{ fontSize: 12.5 }}>
            Page {page + 1} of {totalPages}
          </span>
        )}
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
          <div className="grid-2">
            {findings.map((f) => (
              <FindingCard key={f.id} finding={f} onSelect={onSelectFinding} />
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
    </div>
  );
}
