import { Filter, RotateCcw, Search } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { FindingCard } from '../components/FindingCard';
import type { FindingItem } from '../types';
import { METHODOLOGY_DESCRIPTIONS, pillarLabel } from '../utils';

interface FindingsPageProps {
  onSelectFinding: (id: string | number) => void;
  refreshTick: number;
}

const STATUS_FILTERS = [
  { value: '', label: 'All Statuses' },
  { value: 'verified', label: 'Peer-Verified' },
  { value: 'peer_review', label: 'In Peer Review' },
  { value: 'cleared', label: 'Cleared / Explained' },
  { value: 'disputed', label: 'Disputed / Retracted' },
  { value: 'rti_filed', label: 'RTI Filed' },
];

const PILLAR_FILTERS = [
  '',
  'procurement',
  'audit',
  'budget',
  'scheme',
  'corporate_political',
];

export function FindingsPage({ onSelectFinding, refreshTick }: FindingsPageProps) {
  const [status, setStatus] = useState('');
  const [pillar, setPillar] = useState('');
  const [methodology, setMethodology] = useState('');
  const [search, setSearch] = useState('');
  const [findings, setFindings] = useState<FindingItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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
        })
        .then((list) => {
          if (!cancelled) setFindings(list);
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
  }, [status, pillar, methodology, search, refreshTick]);

  const resetFilters = () => {
    setStatus('');
    setPillar('');
    setMethodology('');
    setSearch('');
  };

  return (
    <div className="container" style={{ paddingTop: 28, paddingBottom: 40 }}>
      <div className="section-header">
        <div>
          <div className="section-kicker">Public Evidence Wall · Deterministic &amp; Neutral</div>
          <h1 className="section-title" style={{ fontSize: 32 }}>
            Findings &amp; Evidence Ledger
          </h1>
          <p className="section-subtitle">
            Click any card to open its full Evidence Dossier with deterministic calculations,
            SHA-256 document provenance, mandatory benign explanations, peer verification quorum,
            and 1-click RTI Act 2005 Sec 6(1) draft generator.
          </p>
        </div>
      </div>

      {/* Epistemic Visual Grammar Key */}
      <div
        className="card-sunk card-pad-sm"
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 14,
          flexWrap: 'wrap',
          marginBottom: 16,
          fontSize: 12,
        }}
      >
        <span className="font-mono" style={{ fontWeight: 600, color: 'var(--ink-secondary)' }}>
          EPISTEMIC KEY:
        </span>
        <span className="badge badge-verified">Peer-Verified (2/2 Solid)</span>
        <span className="badge badge-review">In Peer Review (Dashed · Unverified)</span>
        <span className="badge badge-cleared">Cleared / Explained</span>
        <span className="badge badge-disputed">Disputed / Retracted</span>
        <span className="badge badge-rti">RTI Filed / Draft Ready</span>
        <span className="badge-demo">SAMPLE DEMO DATA</span>
      </div>

      {/* Filter Bar */}
      <div className="filter-bar">
        <div style={{ position: 'relative', flex: 1, minWidth: 220 }}>
          <Search
            size={14}
            style={{
              position: 'absolute',
              left: 10,
              top: '50%',
              transform: 'translateY(-50%)',
              color: 'var(--ink-muted)',
            }}
          />
          <input
            type="text"
            className="input-field"
            style={{ width: '100%', paddingLeft: 32 }}
            placeholder="Search ministry, state, rule, title, or entity…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>

        <select
          className="select-field"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          aria-label="Filter by epistemic status"
        >
          {STATUS_FILTERS.map((opt) => (
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
              {p ? pillarLabel(p) : 'All Pillars'}
            </option>
          ))}
        </select>

        <select
          className="select-field"
          value={methodology}
          onChange={(e) => setMethodology(e.target.value)}
          aria-label="Filter by methodology"
        >
          <option value="">All Methodologies (M1–M8)</option>
          {Object.entries(METHODOLOGY_DESCRIPTIONS).map(([code, meta]) => (
            <option key={code} value={code}>
              {code} · {meta.name}
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
          fontSize: 12.5,
          color: 'var(--ink-muted)',
        }}
      >
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <Filter size={13} />
          <span className="font-mono tabular">
            Showing <strong>{findings.length}</strong> public audit record
            {findings.length === 1 ? '' : 's'}
          </span>
        </span>
      </div>

      {loading && <div className="state-banner">Querying public evidence ledger…</div>}
      {error && <div className="state-banner">Failed to load findings: {error}</div>}

      {!loading && !error && findings.length === 0 && (
        <div className="state-banner">
          No findings match your active filters. Try clearing filters or running an agent scan.
        </div>
      )}

      {!loading && !error && findings.length > 0 && (
        <div className="grid-2">
          {findings.map((f) => (
            <FindingCard key={f.id} finding={f} onSelect={onSelectFinding} />
          ))}
        </div>
      )}
    </div>
  );
}
