import { ArrowUpRight, ChevronLeft, ChevronRight, Search, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { FINDING_TAGS, getFindingTag, getWorkTypeTag } from '../lib/tags';
import { Link } from '../router';
import type { FindingItem, FindingPattern, FindingPatterns } from '../types';
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

interface PatternScope {
  label: string;
  mission: string;
  methodology: string;
  search: string;
}

function yearSpan(p: FindingPattern): string {
  const from = fiscalLabel(p.fy_from);
  const to = fiscalLabel(p.fy_to);
  if (!from) return '';
  return from === to ? from : `${from} to ${to.replace(/^FY /, '')}`;
}

function CheckMix({ data }: { data: FindingPatterns }) {
  const totals = new Map<string, number>();
  for (const p of data.patterns) {
    const label = getWorkTypeTag(undefined, p.methodology_code).label;
    totals.set(label, (totals.get(label) ?? 0) + p.count);
  }
  const rows = [...totals.entries()].sort((a, b) => b[1] - a[1]);
  const sum = rows.reduce((acc, [, n]) => acc + n, 0) || 1;
  return (
    <figure className="fp-mix">
      <div className="fp-mix-bar" role="img" aria-label="Confirmed findings by type of check">
        {rows.map(([label, n], i) => (
          <span key={label} className={`fp-mix-seg fp-tone-${i % 5}`} style={{ flexGrow: n }} title={`${label}: ${n}`} />
        ))}
      </div>
      <figcaption className="fp-mix-legend">
        {rows.map(([label, n], i) => (
          <span key={label}>
            <i className={`fp-mix-dot fp-tone-${i % 5}`} aria-hidden="true" />
            {label} <b>{Math.round((n / sum) * 100)}%</b>
          </span>
        ))}
      </figcaption>
    </figure>
  );
}

function PatternCard({
  p,
  onOpen,
  onSelectFinding,
}: {
  p: FindingPattern;
  onOpen: (p: FindingPattern) => void;
  onSelectFinding: (id: string | number) => void;
}) {
  const check = getWorkTypeTag(undefined, p.methodology_code).label;
  const max = Math.max(0, ...p.items.map((it) => it.amount_inr));
  const facts = [
    `${p.count.toLocaleString('en-IN')} finding${p.count === 1 ? '' : 's'}`,
    yearSpan(p),
    p.jurisdiction_count > 1 ? `${p.jurisdiction_count} states and UTs` : '',
  ].filter(Boolean);
  return (
    <article className="fp-card">
      <header className="fp-card-head">
        <div>
          <div className="fp-card-check">{check}</div>
          <h3 className="fp-card-title">{p.group}</h3>
          <div className="fp-card-facts">{facts.join(' · ')}</div>
        </div>
        {p.total_inr > 0 && (
          <div className="fp-card-total">
            <span>{formatInr(p.total_inr)}</span>
            <small>total flagged</small>
          </div>
        )}
      </header>
      <ul className="fp-bars">
        {p.items.map((it) => (
          <li key={it.id}>
            <button type="button" className="fp-bar-row" onClick={() => onSelectFinding(it.id)}>
              <span className="fp-bar-label">
                {it.label}
                {it.fiscal_year && <span className="fp-bar-year">{fiscalLabel(it.fiscal_year)}</span>}
              </span>
              <span className="fp-bar-track" aria-hidden="true">
                {max > 0 && <span className="fp-bar-fill" style={{ width: `${Math.max(2, (it.amount_inr / max) * 100)}%` }} />}
              </span>
              <span className="fp-bar-value">{it.amount_inr > 0 ? formatInr(it.amount_inr) : '—'}</span>
            </button>
          </li>
        ))}
      </ul>
      <footer className="fp-card-foot">
        <span>{p.mission_title}</span>
        <button type="button" className="fp-card-more" onClick={() => onOpen(p)}>
          {p.count > p.items.length ? `See all ${p.count.toLocaleString('en-IN')}` : 'Open as list'}
          <ArrowUpRight size={14} aria-hidden="true" />
        </button>
      </footer>
    </article>
  );
}

export function FindingsPage({ onSelectFinding, refreshTick }: FindingsPageProps) {
  const [view, setView] = useState<'summary' | 'records'>('summary');
  const [patterns, setPatterns] = useState<FindingPatterns | null>(null);
  const [patternsError, setPatternsError] = useState(false);
  const [scope, setScope] = useState<PatternScope | null>(null);
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
  }, [status, search, scope]);

  useEffect(() => {
    let cancelled = false;
    api
      .getFindingPatterns()
      .then((res) => {
        if (!cancelled) setPatterns(res);
      })
      .catch(() => {
        if (!cancelled) {
          setPatternsError(true);
          setView((v) => (v === 'summary' ? 'records' : v));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [refreshTick]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const timer = window.setTimeout(() => {
      api
        .getFindings({
          status: status || undefined,
          mission: scope?.mission || undefined,
          methodology: scope?.methodology || undefined,
          search: [scope?.search, search.trim()].filter(Boolean).join(' ') || undefined,
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
  }, [status, search, scope, page, refreshTick]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const activeTab = STATUS_TABS.find((t) => t.value === status) ?? STATUS_TABS[0];

  const openPattern = (p: FindingPattern) => {
    setScope({
      label: `${p.group} · ${getWorkTypeTag(undefined, p.methodology_code).label}`,
      mission: p.mission_slug,
      methodology: p.methodology_code,
      search: p.group,
    });
    setStatus('verified');
    setSearch('');
    setView('records');
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

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
          Issues spotted in public spending records, each traced to an official document. Nothing is called
          confirmed until two independent checks agree.{' '}
          <Link to="/how-it-works" className="fl-link">
            How this works
          </Link>
        </p>
      </header>

      {!patternsError && (
        <div className="fp-switch" role="tablist" aria-label="Choose a view">
          <button
            type="button"
            role="tab"
            aria-selected={view === 'summary'}
            className={`fp-switch-btn ${view === 'summary' ? 'is-active' : ''}`}
            onClick={() => setView('summary')}
          >
            Summary
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={view === 'records'}
            className={`fp-switch-btn ${view === 'records' ? 'is-active' : ''}`}
            onClick={() => setView('records')}
          >
            All records
          </button>
        </div>
      )}

      {view === 'summary' && (
        <section className="fp" aria-label="Summary of confirmed findings">
          {!patterns ? (
            <div className="fl-empty">Loading summary…</div>
          ) : patterns.patterns.length === 0 ? (
            <div className="fl-empty">No confirmed findings yet.</div>
          ) : (
            <>
              <div className="fp-overview">
                <p className="fp-overview-text">
                  <b>{patterns.finding_count.toLocaleString('en-IN')}</b> confirmed findings, grouped into{' '}
                  <b>{patterns.pattern_count.toLocaleString('en-IN')}</b> patterns by project, type of check and
                  department.
                  {patterns.total_inr > 0 && (
                    <>
                      {' '}
                      Together they flag <b>{formatInr(patterns.total_inr)}</b>.
                    </>
                  )}
                </p>
                <CheckMix data={patterns} />
              </div>
              <div className="fp-grid">
                {patterns.patterns.map((p) => (
                  <PatternCard key={p.key} p={p} onOpen={openPattern} onSelectFinding={onSelectFinding} />
                ))}
              </div>
              {patterns.pattern_count > patterns.patterns.length && (
                <p className="fp-more-note">
                  Showing the {patterns.patterns.length} largest of {patterns.pattern_count.toLocaleString('en-IN')} patterns.{' '}
                  <button type="button" className="fl-link fp-linkbtn" onClick={() => setView('records')}>
                    Browse every record
                  </button>
                </p>
              )}
            </>
          )}
        </section>
      )}

      {view === 'records' && (
        <>
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

          {scope && (
            <div className="fp-scope">
              <span>Showing one pattern: {scope.label}</span>
              <button type="button" onClick={() => setScope(null)} aria-label="Show all records">
                <X size={14} aria-hidden="true" />
                Clear
              </button>
            </div>
          )}

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
        </>
      )}
    </div>
  );
}
