import { ExternalLink, HeartHandshake, ShieldCheck } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { SponsorPreset } from '../components/SponsorModal';
import type { SponsorItem } from '../types';
import { formatInrExact, safeExternalUrl } from '../utils';

interface SponsorsLedgerPageProps {
  activeView?: 'sponsors' | 'ledger';
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
  onOpenSponsor,
  refreshTick,
}: SponsorsLedgerPageProps) {
  const [sponsors, setSponsors] = useState<SponsorItem[]>([]);
  const [pledgedTotal, setPledgedTotal] = useState(0);
  const [reconciledCash, setReconciledCash] = useState(0);
  const [inKindCompute, setInKindCompute] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .getSponsors()
      .then((sp) => {
        if (cancelled) return;
        setSponsors(sp.sponsors);
        setPledgedTotal(sp.pledged_total_inr);
        setReconciledCash(sp.reconciled_cash_inr);
        setInKindCompute(sp.in_kind_compute_inr);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load sponsors');
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
            Civic Compute &amp; Infrastructure Support
          </div>
          <h1 className="section-title" style={{ fontSize: 32 }}>
            Sponsors &amp; Compute Grants
          </h1>
          <p className="section-subtitle">
            Sponsors fund compute, OCR, and hosting infrastructure for independent open-source
            audits of Indian public records, with zero editorial say on projects or findings.
          </p>
        </div>

        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
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
      </div>

      {/* Sponsors KPI Summary Strip */}
      <div className="kpi-strip cols-3" style={{ marginBottom: 28 }}>
        <div className="kpi-cell">
          <span className="kpi-value emerald">{loading ? '—' : formatInrExact(reconciledCash)}</span>
          <span className="kpi-label">Reconciled Cash Received (Verified Receipts)</span>
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

      {loading && <div className="state-banner">Loading sponsors and compute backers…</div>}
      {error && <div className="state-banner">Failed to load sponsors: {error}</div>}

      {!loading && !error && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 28, marginBottom: 32 }}>
          <div>
            <div className="section-kicker">
              Institutional Compute-Grant Sponsors &amp; Compute Patrons
            </div>
            <div className="grid-3">
              {institutional.map((s, idx) => {
                const sponsorUrl = safeExternalUrl(s.url);
                return (
                  <div key={s.id ?? idx} className="card card-pad" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                      <span className="tag-mono">{s.tier.replace(/_/g, ' ')}</span>
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
                      <h3 className="font-serif" style={{ margin: 0, fontSize: 20, lineHeight: 1.25 }}>
                        {sponsorUrl ? (
                          <a href={sponsorUrl} target="_blank" rel="noopener noreferrer">
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
                );
              })}
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
                      ₹0 reconciled cash · Zero editorial say
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
                        No backers yet — be the first to fund open compute.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
