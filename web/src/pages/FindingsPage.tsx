import { ArrowUpRight, ChevronLeft, ChevronRight, Search } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { FINDING_TAGS, getFindingTag } from '../lib/tags';
import { Link } from '../router';
import type { FindingItem } from '../types';
import { formatDateShort, formatInr } from '../utils';

interface FindingsPageProps {
  onSelectFinding: (id: string | number) => void;
  refreshTick: number;
}

const PAGE_SIZE = 25;

const STATUS_TABS: Array<{ value: string; label: string; hint: string }> = [
  { value: '', label: 'All', hint: 'Every record, newest first.' },
  { value: 'verified', label: 'Confirmed', hint: 'Two independent checks reached the same result.' },
  { value: 'peer_review', label: 'Being checked', hint: 'Flagged once, waiting for a second independent check.' },
  { value: 'cleared', label: 'No issue found', hint: 'Checked and found to be in order.' },
  { value: 'disputed', label: 'Disputed', hint: 'Checks disagreed, so this is not treated as a result.' },
];

function plainStatus(status: string): { label: string; tone: string } {
  const tag = getFindingTag(status);
  if (tag.id === FINDING_TAGS.verified.id) return { label: 'Confirmed', tone: 'ok' };
  if (tag.id === FINDING_TAGS.cleared.id) return { label: 'No issue', tone: 'muted' };
  if (tag.id === FINDING_TAGS.in_review.id) return { label: 'Being checked', tone: 'pending' };
  if (tag.tone === 'disputed') return { label: 'Disputed', tone: 'bad' };
  return { label: tag.label, tone: 'muted' };
}

function amountFor(f: FindingItem): string {
  if (f.quarantined || f.status === 'quarantined') return 'Withheld';
  if (f.monetary_exposure_inr) return formatInr(f.monetary_exposure_inr);
  return f.amount_label || '—';
}

function splitTitle(f: FindingItem): { who: string; what: string; dept: string } {
  const title = (f.title || '').trim();
  const cut = title.indexOf(': ');
  let who = f.entity_name || '';
  let what = title;
  if (cut > 0 && cut < title.length - 2) {
    who = title.slice(0, cut);
    const rest = title.slice(cut + 2);
    what = rest.charAt(0).toUpperCase() + rest.slice(1);
  }
  let dept = '';
  const m = who.match(/^(.*\S)\s*\(((?:Department|Ministry|Dept)[^()]*)\)\s*$/i);
  if (m) {
    who = m[1];
    dept = m[2];
  }
  if (!who || who === what) return { who: what, what: '', dept };
  return { who, what, dept };
}

function fiscalLabel(fy?: string): string {
  if (!fy) return '';
  return /^fy/i.test(fy) ? fy.replace(/^fy\s*/i, 'FY ') : `FY ${fy}`;
}

export function FindingsPage({ onSelectFinding, refreshTick }: FindingsPageProps) {
  const [status, setStatus] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const [findings, setFindings] = useState<FindingItem[]>([]);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setPage(0);
  }, [status, search]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const timer = window.setTimeout(() => {
      api
        .getFindings({
          status: status || undefined,
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
  }, [status, search, page, refreshTick]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const activeTab = STATUS_TABS.find((t) => t.value === status) ?? STATUS_TABS[0];

  const go = (next: number) => {
    setPage(next);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  return (
    <div className="fl">
      <header className="fl-head">
        <div className="fl-eyebrow">Findings</div>
        <h1 className="fl-title">What the checks have found</h1>
        <p className="fl-lede">
          Each line is one issue spotted in public spending records. Nothing is called confirmed until two
          independent checks agree. Open any line to see the source documents and the working.{' '}
          <Link to="/how-it-works" className="fl-link">
            How this works
          </Link>
        </p>
      </header>

      <div className="fl-controls">
        <div className="fl-tabs" role="tablist" aria-label="Filter by status">
          {STATUS_TABS.map((t) => (
            <button
              key={t.value || 'all'}
              type="button"
              role="tab"
              aria-selected={status === t.value}
              className={`fl-tab ${status === t.value ? 'is-active' : ''}`}
              onClick={() => setStatus(t.value)}
            >
              {t.label}
            </button>
          ))}
        </div>
        <label className="fl-search">
          <Search size={15} aria-hidden="true" />
          <input
            type="search"
            placeholder="Search a ministry, state or scheme"
            aria-label="Search findings"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
      </div>

      <div className="fl-meta">
        <span>{activeTab.hint}</span>
        <span className="fl-count">
          {loading ? 'Loading…' : `${total.toLocaleString('en-IN')} record${total === 1 ? '' : 's'}`}
        </span>
      </div>

      {error && <div className="fl-empty">Could not load findings. {error}</div>}

      {!loading && !error && findings.length === 0 && (
        <div className="fl-empty">
          {status || search ? 'Nothing matches that search.' : 'No findings have been published yet.'}
        </div>
      )}

      {findings.length > 0 && (
        <ol className={`fl-list ${loading ? 'is-loading' : ''}`}>
          <li className="fl-row fl-row--head" aria-hidden="true">
            <span>Date</span>
            <span>Finding</span>
            <span className="fl-num">Amount</span>
            <span>Status</span>
          </li>
          {findings.map((f) => {
            const st = plainStatus(f.status);
            const { who, what, dept } = splitTitle(f);
            const where = [dept, f.jurisdiction, fiscalLabel(f.fiscal_year)].filter(Boolean).join(' · ');
            return (
              <li key={f.id}>
                <button type="button" className="fl-row" onClick={() => onSelectFinding(f.id)}>
                  <span className="fl-date">{formatDateShort(f.verified_at || f.created_at)}</span>
                  <span className="fl-main">
                    <span className="fl-name">{who}</span>
                    {what && <span className="fl-what">{what}</span>}
                    {where && <span className="fl-where">{where}</span>}
                  </span>
                  <span className="fl-num fl-amount">{amountFor(f)}</span>
                  <span className="fl-status">
                    <span className={`fl-pill fl-pill--${st.tone}`}>{st.label}</span>
                    <ArrowUpRight size={15} className="fl-arrow" aria-hidden="true" />
                  </span>
                </button>
              </li>
            );
          })}
        </ol>
      )}

      {(total > PAGE_SIZE || page > 0) && (
        <nav className="fl-pager" aria-label="Pages">
          <button type="button" className="fl-pagebtn" disabled={page <= 0} onClick={() => go(Math.max(0, page - 1))}>
            <ChevronLeft size={15} aria-hidden="true" />
            Newer
          </button>
          <span>
            Page {page + 1} of {totalPages.toLocaleString('en-IN')}
          </span>
          <button
            type="button"
            className="fl-pagebtn"
            disabled={!hasMore && page + 1 >= totalPages}
            onClick={() => go(page + 1)}
          >
            Older
            <ChevronRight size={15} aria-hidden="true" />
          </button>
        </nav>
      )}
    </div>
  );
}
