import {
  ArrowDownLeft,
  ArrowUpRight,
  Download,
  ExternalLink,
  HeartHandshake,
  ShieldCheck,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { SponsorPreset } from '../components/SponsorModal';
import { Link } from '../router';
import type { LedgerEntry, SponsorItem } from '../types';
import { formatInrExact } from '../utils';

interface SponsorsLedgerPageProps {
  activeView: 'sponsors' | 'ledger';
  onOpenSponsor: (preset?: SponsorPreset) => void;
  refreshTick: number;
}

const PLEDGE_PRESETS: Array<{ label: string; preset: SponsorPreset }> = [
  { label: '₹500', preset: { amountInr: '500', tier: 'citizen_backer', computeDesc: '' } },
  { label: '₹2,500', preset: { amountInr: '2500', tier: 'citizen_backer', computeDesc: '' } },
  { label: '₹10,000', preset: { amountInr: '10000', tier: 'patron', computeDesc: '' } },
  {
    label: 'Compute Grant',
    preset: {
      amountInr: '50000',
      tier: 'institutional_compute',
      computeDesc: '50M LLM tokens / OCR GPU credits',
    },
  },
];

export function SponsorsLedgerPage({
  activeView,
  onOpenSponsor,
  refreshTick,
}: SponsorsLedgerPageProps) {
  const [sponsors, setSponsors] = useState<SponsorItem[]>([]);
  const [pledgedTotal, setPledgedTotal] = useState(0);
  const [ledger, setLedger] = useState<LedgerEntry[]>([]);
  const [reconciledCash, setReconciledCash] = useState(0);
  const [inKindCompute, setInKindCompute] = useState(0);
  const [outflow, setOutflow] = useState(0);
  const [netCashBalance, setNetCashBalance] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([api.getSponsors(), api.getLedger()])
      .then(([sp, ld]) => {
        if (cancelled) return;
        setSponsors(sp.sponsors);
        setPledgedTotal(Math.max(sp.pledged_total_inr, ld.summary.pledged_total_inr));
        setLedger(ld.entries);
        setReconciledCash(ld.summary.reconciled_cash_inr);
        setInKindCompute(Math.max(sp.in_kind_compute_inr, ld.summary.in_kind_compute_inr));
        setOutflow(ld.summary.total_outflow_inr);
        setNetCashBalance(ld.summary.net_cash_balance_inr);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load treasury');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [refreshTick]);

  const institutional = sponsors
    .filter((s) =>
      ['institutional_compute', 'founding_grant', 'patron', 'institutional_grant', 'compute_patron'].includes(
        s.tier,
      ) || s.amount_inr >= 25000,
    )
    .sort((a, b) => b.amount_inr - a.amount_inr);
  const citizenBackers = sponsors
    .filter(
      (s) =>
        !(
          ['institutional_compute', 'founding_grant', 'patron', 'institutional_grant', 'compute_patron'].includes(
            s.tier,
          ) || s.amount_inr >= 25000
        ),
    )
    .sort((a, b) => b.amount_inr - a.amount_inr);

  return (
    <div className="container" style={{ paddingTop: 28, paddingBottom: 40 }}>
      <div className="section-header">
        <div>
          <div className="section-kicker">
            OpenCollective-Style Civic Treasury, Radical Budget Transparency
          </div>
          <h1 className="section-title" style={{ fontSize: 32 }}>
            {activeView === 'ledger'
              ? 'Open Public Treasury & Compute Ledger'
              : 'Sponsors, Compute Grants & Open Ledger'}
          </h1>
          <p className="section-subtitle">
            A public-spending watchdog must practice total budget transparency itself. Every rupee,
            donated API credit grant, and hosting/OCR infrastructure expense is published openly.
          </p>
        </div>

        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
          <div className="segmented">
            <Link
              to="/sponsors"
              className={`segmented-btn ${activeView === 'sponsors' ? 'active' : ''}`}
            >
              Sponsors Wall
            </Link>
            <Link
              to="/ledger"
              className={`segmented-btn ${activeView === 'ledger' ? 'active' : ''}`}
            >
              Public Ledger ({ledger.length})
            </Link>
          </div>
          <button type="button" className="btn btn-primary" onClick={() => onOpenSponsor()}>
            <HeartHandshake size={15} />
            <span>Sponsor / Donate Compute</span>
          </button>
        </div>
      </div>

      {/* Editorial Independence Banner */}
      <div
        className="card card-pad-sm"
        style={{
          marginBottom: 22,
          borderLeft: '3px solid var(--ep-verified-fg)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 14,
          flexWrap: 'wrap',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <ShieldCheck size={18} color="var(--ep-verified-fg)" style={{ flexShrink: 0 }} />
          <span className="font-serif" style={{ fontSize: 16, fontStyle: 'italic' }}>
            “Sponsors fund compute &amp; infrastructure. Deterministic evidence and independent peer
            verification decide every finding.”
          </span>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <a
            href="/api/ledger?format=csv"
            download="aiwatchdogs-treasury-ledger.csv"
            className="btn btn-secondary btn-xs"
          >
            <Download size={12} />
            <span>Export CSV</span>
          </a>
          <a
            href="/api/ledger?format=json"
            download="aiwatchdogs-treasury-ledger.json"
            className="btn btn-secondary btn-xs"
          >
            <Download size={12} />
            <span>Export JSON</span>
          </a>
        </div>
      </div>

      {/* Treasury KPI Summary Strip — Strictly separates Reconciled Cash, Pledges & In-Kind Compute */}
      <div className="kpi-strip cols-5" style={{ marginBottom: 28 }}>
        <div className="kpi-cell">
          <span className="kpi-value emerald">{loading ? '—' : formatInrExact(reconciledCash)}</span>
          <span className="kpi-label">Reconciled Cash Received (Verified Receipts)</span>
        </div>
        <div className="kpi-cell">
          <span className="kpi-value">{loading ? '—' : formatInrExact(outflow)}</span>
          <span className="kpi-label">Verified Hosting &amp; Infra Outflows</span>
        </div>
        <div className="kpi-cell">
          <span className="kpi-value indigo">{loading ? '—' : formatInrExact(netCashBalance)}</span>
          <span className="kpi-label">Net Reconciled Cash Balance</span>
        </div>
        <div className="kpi-cell">
          <span className="kpi-value slate">{loading ? '—' : formatInrExact(pledgedTotal)}</span>
          <span className="kpi-label">
            Self-Declared Pledges (Unreconciled)
          </span>
        </div>
        <div className="kpi-cell">
          <span className="kpi-value slate">{loading ? '—' : formatInrExact(inKindCompute)}</span>
          <span className="kpi-label">In-Kind Compute / API Credits</span>
        </div>
      </div>

      {loading && <div className="state-banner">Loading public treasury and sponsors…</div>}
      {error && <div className="state-banner">Failed to load treasury: {error}</div>}

      {!loading && !error && (
        <>
          {/* SPONSORS SECTION (Primary on /sponsors, secondary on /ledger) */}
          {activeView === 'sponsors' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 28, marginBottom: 32 }}>
              <div>
                <div className="section-kicker">
                  Institutional Compute-Grant Sponsors &amp; Compute Patrons
                </div>
                <div className="grid-3">
                  {institutional.map((s, idx) => (
                    <div key={s.id ?? idx} className="card card-pad" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                        <span className="tag-mono">{s.tier.replace(/_/g, ' ')}</span>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
                        <h3 className="font-serif" style={{ margin: 0, fontSize: 20, lineHeight: 1.25 }}>
                          {s.url ? (
                            <a href={s.url} target="_blank" rel="noreferrer">
                              {s.name}
                              <ExternalLink
                                size={12}
                                style={{ display: 'inline', marginLeft: 5, verticalAlign: 'middle' }}
                              />
                            </a>
                          ) : (
                            s.name
                          )}
                        </h3>
                        <span className="font-mono tabular" style={{ fontWeight: 700, fontSize: 15 }}>
                          {formatInrExact(s.amount_inr)}
                        </span>
                      </div>
                      {s.amount_label && (
                        <div className="font-mono" style={{ fontSize: 11.5, color: 'var(--ep-rti-fg)' }}>
                          {s.amount_label}
                        </div>
                      )}
                      {s.note && (
                        <p style={{ margin: 0, fontSize: 13, color: 'var(--ink-muted)' }}>{s.note}</p>
                      )}
                    </div>
                  ))}
                  {institutional.length === 0 && (
                    <div
                      className="card card-pad"
                      style={{
                        gridColumn: '1 / -1',
                        borderStyle: 'dashed',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        gap: 18,
                        flexWrap: 'wrap',
                      }}
                    >
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                        <div className="section-kicker" style={{ marginBottom: 0 }}>
                          ₹0 reconciled cash, 100% open ledger
                        </div>
                        <div className="font-serif" style={{ fontSize: 21, lineHeight: 1.3 }}>
                          No backers yet — be the first to fund open compute
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginTop: 4 }}>
                          <button
                            type="button"
                            className="slot your"
                            onClick={() =>
                              onOpenSponsor({
                                amountInr: '50000',
                                tier: 'institutional_compute',
                                computeDesc: 'OCR & LLM compute grant',
                              })
                            }
                          >
                            + Your org / compute here
                          </button>
                        </div>
                      </div>
                      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                        {PLEDGE_PRESETS.map((item) => (
                          <button
                            key={item.label}
                            type="button"
                            className="amount-chip"
                            onClick={() => onOpenSponsor(item.preset)}
                          >
                            {item.label}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </div>

              <div>
                <div className="section-kicker">Ranked Citizen Backers Wall</div>
                <div className="table-wrap">
                  <table className="ledger-table">
                    <thead>
                      <tr>
                        <th>Rank</th>
                        <th>Backer</th>
                        <th>Handle / Affiliation</th>
                        <th>Note / Compute Grant</th>
                        <th className="num">Pledge (₹)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {citizenBackers.map((s, idx) => (
                        <tr key={s.id ?? idx}>
                          <td className="font-mono tabular">#{idx + 1}</td>
                          <td style={{ fontWeight: 600 }}>{s.name}</td>
                          <td className="font-mono" style={{ fontSize: 12, color: 'var(--ink-muted)' }}>
                            {s.handle || '—'}
                          </td>
                          <td style={{ fontSize: 13, color: 'var(--ink-secondary)' }}>
                            {s.amount_label ? `[${s.amount_label}] ` : ''}
                            {s.note || 'Open public finance transparency'}
                          </td>
                          <td className="num" style={{ fontWeight: 600 }}>
                            {formatInrExact(s.amount_inr)}
                          </td>
                        </tr>
                      ))}
                      {citizenBackers.length === 0 && (
                        <tr>
                          <td colSpan={5} style={{ textAlign: 'center', color: 'var(--ink-muted)', padding: 20 }}>
                            No backers yet — be the first to fund open compute. Every pledge appears on the 100% open ledger above.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}

          {/* OPEN PUBLIC BUDGET & COMPUTE-GRANT LEDGER TABLE (Shown on both, primary on /ledger) */}
          <div>
            <div
              style={{
                display: 'flex',
                alignItems: 'flex-end',
                justifyContent: 'space-between',
                gap: 12,
                marginBottom: 12,
                flexWrap: 'wrap',
              }}
            >
              <div>
                <div className="section-kicker">Line-Item Open Treasury Ledger</div>
                <h2 className="section-title" style={{ fontSize: 23 }}>
                  Public Budget &amp; Compute-Grant Transactions
                </h2>
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <a
                  href="/api/ledger?format=csv"
                  download="aiwatchdogs-treasury-ledger.csv"
                  className="btn btn-secondary btn-sm"
                >
                  <Download size={13} />
                  <span>Export CSV</span>
                </a>
                <a
                  href="/api/ledger?format=json"
                  download="aiwatchdogs-treasury-ledger.json"
                  className="btn btn-secondary btn-sm"
                >
                  <Download size={13} />
                  <span>Export JSON</span>
                </a>
              </div>
            </div>

            <div className="table-wrap">
              <table className="ledger-table">
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Type</th>
                    <th>Counterparty</th>
                    <th>Description &amp; Compute Units</th>
                    <th>Receipt Ref</th>
                    <th className="num">Amount (₹)</th>
                  </tr>
                </thead>
                <tbody>
                  {ledger.map((row, idx) => {
                    const status = (row.settlement_status || 'pledged').toLowerCase();
                    const isOutflow = row.direction === 'outflow' || status === 'verified_outflow';
                    const isInKind =
                      !isOutflow &&
                      (status === 'in_kind' ||
                        status === 'in_kind_committed' ||
                        status === 'in_kind_fulfilled' ||
                        row.direction === 'in_kind' ||
                        row.category === 'compute_grant' ||
                        Boolean(row.compute_credits_desc));
                    const isReconciledIn =
                      !isOutflow &&
                      !isInKind &&
                      (status === 'received' || status === 'reconciled') &&
                      row.direction === 'inflow';
                    const badgeClass = isReconciledIn
                      ? 'badge badge-verified'
                      : isOutflow
                        ? 'badge badge-cleared'
                        : isInKind
                          ? 'badge badge-rti'
                          : 'badge badge-review';
                    const statusLabel = isReconciledIn
                      ? 'reconciled cash'
                      : isOutflow
                        ? row.category.replace(/_/g, ' ')
                        : isInKind
                          ? 'in-kind compute'
                          : 'self-declared pledge';
                    return (
                      <tr key={row.id ?? idx}>
                        <td className="font-mono" style={{ fontSize: 12, whiteSpace: 'nowrap' }}>
                          {row.entry_date}
                        </td>
                        <td>
                          <span className={badgeClass}>
                            {isReconciledIn ? (
                              <ArrowDownLeft size={11} />
                            ) : isOutflow ? (
                              <ArrowUpRight size={11} />
                            ) : null}
                            <span>{statusLabel}</span>
                          </span>
                        </td>
                        <td style={{ fontWeight: 600 }}>{row.counterparty}</td>
                        <td style={{ fontSize: 13, color: 'var(--ink-secondary)' }}>
                          <div>{row.description}</div>
                          {row.compute_credits_desc && (
                            <div className="font-mono" style={{ fontSize: 11.5, color: 'var(--ep-rti-fg)', marginTop: 2 }}>
                              In-kind credits: {row.compute_credits_desc}
                            </div>
                          )}
                        </td>
                        <td className="font-mono" style={{ fontSize: 11.5, color: 'var(--ink-muted)' }}>
                          {row.receipt_ref || '—'}
                        </td>
                        <td
                          className="num"
                          style={{
                            fontWeight: 600,
                            color: isReconciledIn ? 'var(--ep-verified-fg)' : 'var(--ink-primary)',
                          }}
                        >
                          {isReconciledIn ? '+' : isOutflow ? '-' : ''}
                          {formatInrExact(row.amount_inr)}
                        </td>
                      </tr>
                    );
                  })}
                  {ledger.length === 0 && (
                    <tr>
                      <td colSpan={6} style={{ textAlign: 'center', color: 'var(--ink-muted)', padding: 24 }}>
                        ₹0 reconciled cash, ₹0 self-declared pledges — reconciled cash receipts, self-declared pledges, in-kind compute grants, and hosting expenses are tracked separately here.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
