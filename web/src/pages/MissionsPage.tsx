import { ArrowLeft, ExternalLink, Layers, ShieldCheck } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { FindingCard } from '../components/FindingCard';
import { Link } from '../router';
import type { MissionItem } from '../types';
import {
  METHODOLOGY_DESCRIPTIONS,
  formatInrCr,
  formatRelativeTime,
  pillarLabel,
} from '../utils';

interface MissionsPageProps {
  slug?: string;
  onSelectFinding: (id: string | number) => void;
  refreshTick: number;
}

export function MissionsPage({ slug, onSelectFinding, refreshTick }: MissionsPageProps) {
  const [missions, setMissions] = useState<MissionItem[]>([]);
  const [detail, setDetail] = useState<MissionItem | null>(null);
  const [pillarFilter, setPillarFilter] = useState('all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    if (slug) {
      api
        .getMissionDetail(slug)
        .then((d) => {
          if (!cancelled) setDetail(d);
        })
        .catch((err) => {
          if (!cancelled) setError(err instanceof Error ? err.message : 'Mission not found');
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    } else {
      setDetail(null);
      api
        .getMissions()
        .then((list) => {
          if (!cancelled) setMissions(list);
        })
        .catch((err) => {
          if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load missions');
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }
    return () => {
      cancelled = true;
    };
  }, [slug, refreshTick]);

  // MISSION DETAIL VIEW (/missions/:slug)
  if (slug) {
    const pct =
      detail && detail.total_tasks > 0
        ? Math.min(100, Math.round((detail.completed_tasks / detail.total_tasks) * 100))
        : 0;
    const methodInfo = detail ? METHODOLOGY_DESCRIPTIONS[detail.methodology_code] : null;

    return (
      <div className="container" style={{ paddingTop: 28, paddingBottom: 40 }}>
        <div style={{ marginBottom: 16 }}>
          <Link to="/missions" className="btn btn-ghost btn-sm">
            <ArrowLeft size={14} />
            <span>Back to All Audit Missions</span>
          </Link>
        </div>

        {loading && <div className="state-banner">Loading mission brief and task queue…</div>}
        {error && <div className="state-banner">Could not load mission: {error}</div>}

        {detail && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
            <div className="card card-pad">
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
                <span className="tag-mono tag-method">{detail.methodology_code}</span>
                <span className="tag-mono">{pillarLabel(detail.pillar)}</span>
                <span className="tag-mono">{detail.jurisdiction}</span>
                {detail.data_mode === 'demo_sample' && (
                  <span className="badge-demo">SAMPLE DEMO DATA + LIVE CONNECTOR READY</span>
                )}
              </div>

              <h1 className="section-title" style={{ fontSize: 32, marginBottom: 8 }}>
                {detail.title}
              </h1>

              {detail.description && (
                <p style={{ margin: '0 0 14px', fontSize: 15, color: 'var(--ink-secondary)', maxWidth: '78ch' }}>
                  {detail.description}
                </p>
              )}

              {methodInfo && (
                <div
                  className="card-sunk card-pad-sm"
                  style={{ marginBottom: 16, fontSize: 13, color: 'var(--ink-secondary)' }}
                >
                  <strong>Deterministic Engine ({detail.methodology_code}):</strong> {methodInfo.name} —{' '}
                  {methodInfo.rule}
                </div>
              )}

              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>
                {detail.statutory_anchors.map((anc) => (
                  <span key={anc} className="tag-mono">
                    <ShieldCheck size={11} style={{ marginRight: 4 }} />
                    {anc}
                  </span>
                ))}
              </div>

              <div className="kpi-strip cols-4">
                <div className="kpi-cell">
                  <span className="kpi-value">{pct}%</span>
                  <span className="kpi-label">
                    Completion ({detail.completed_tasks}/{detail.total_tasks} tasks)
                  </span>
                </div>
                <div className="kpi-cell">
                  <span className="kpi-value emerald">{detail.verified_findings_count}</span>
                  <span className="kpi-label">Peer-Verified Leads</span>
                </div>
                <div className="kpi-cell">
                  <span className="kpi-value slate">{detail.cleared_count ?? 0}</span>
                  <span className="kpi-label">Cleared Records</span>
                </div>
                <div className="kpi-cell">
                  <span className="kpi-value">
                    {detail.monetary_audited_inr_cr != null
                      ? formatInrCr(detail.monetary_audited_inr_cr)
                      : 'Active'}
                  </span>
                  <span className="kpi-label">Public Spend Audited</span>
                </div>
              </div>
            </div>

            {/* Public Source Portals (Tiers 1-3) */}
            {detail.sources && detail.sources.length > 0 && (
              <div>
                <div className="section-kicker">Official Data Sources (Legal Ingestion Tiers 1–3)</div>
                <div className="table-wrap">
                  <table className="ledger-table">
                    <thead>
                      <tr>
                        <th>Source Portal</th>
                        <th>Authority</th>
                        <th>Tier</th>
                        <th>Connector</th>
                        <th>URL</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detail.sources.map((s) => (
                        <tr key={s.slug || s.name}>
                          <td style={{ fontWeight: 600 }}>{s.name}</td>
                          <td>{s.authority}</td>
                          <td>
                            <span className="tag-mono">Tier {s.ingestion_tier}</span>
                          </td>
                          <td className="font-mono" style={{ fontSize: 12 }}>
                            {s.connector_type}
                          </td>
                          <td>
                            <a
                              href={s.base_url}
                              target="_blank"
                              rel="noreferrer"
                              className="font-mono"
                              style={{ color: 'var(--ep-rti-fg)', fontSize: 12, display: 'inline-flex', alignItems: 'center', gap: 4 }}
                            >
                              <span>{s.base_url}</span>
                              <ExternalLink size={11} />
                            </a>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* Mission Findings */}
            <div>
              <div className="section-kicker">
                Mission Findings &amp; Audit Leads ({detail.findings?.length ?? 0})
              </div>
              {detail.findings && detail.findings.length > 0 ? (
                <div className="grid-2">
                  {detail.findings.map((f) => (
                    <FindingCard key={f.id} finding={f} onSelect={onSelectFinding} />
                  ))}
                </div>
              ) : (
                <div className="state-banner">
                  No findings published for this mission yet. Claim a queued task with{' '}
                  <code className="font-mono">aiwatchdogs join</code> to run deterministic scans!
                </div>
              )}
            </div>

            {/* Task Queue Status */}
            {detail.tasks && detail.tasks.length > 0 && (
              <div>
                <div className="section-kicker">Desk Task Queue ({detail.tasks.length})</div>
                <div className="table-wrap">
                  <table className="ledger-table">
                    <thead>
                      <tr>
                        <th>Task ID</th>
                        <th>Type</th>
                        <th>Title</th>
                        <th>Status</th>
                        <th>Claimed By</th>
                        <th>Updated</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detail.tasks.map((t) => (
                        <tr key={t.id}>
                          <td className="font-mono" style={{ fontSize: 11.5 }}>
                            {String(t.id).slice(0, 12)}
                          </td>
                          <td>
                            <span className="tag-mono">{t.task_type}</span>
                          </td>
                          <td>{t.title || `${detail.methodology_code} audit shard`}</td>
                          <td>
                            <span
                              className={
                                t.status === 'completed'
                                  ? 'badge badge-verified'
                                  : t.status === 'claimed'
                                    ? 'badge badge-rti'
                                    : 'badge badge-review'
                              }
                            >
                              {t.status}
                            </span>
                          </td>
                          <td className="font-mono" style={{ fontSize: 12 }}>
                            {t.claimed_by ? (
                              <Link
                                to={`/a/${encodeURIComponent(t.claimed_by)}`}
                                style={{ color: 'var(--ep-contrib-fg)' }}
                              >
                                @{t.claimed_by}
                              </Link>
                            ) : (
                              '—'
                            )}
                          </td>
                          <td className="font-mono" style={{ fontSize: 11.5, color: 'var(--ink-muted)' }}>
                            {formatRelativeTime(t.created_at)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    );
  }

  // MISSIONS CATALOG VIEW (/missions)
  const pillars = ['all', ...Array.from(new Set(missions.map((m) => m.pillar)))];
  const visible =
    pillarFilter === 'all' ? missions : missions.filter((m) => m.pillar === pillarFilter);

  return (
    <div className="container" style={{ paddingTop: 28, paddingBottom: 40 }}>
      <div className="section-header">
        <div>
          <div className="section-kicker">
            <Layers size={12} style={{ display: 'inline', marginRight: 5, verticalAlign: '-1px' }} />
            Indian Public-Spending Audit Catalog
          </div>
          <h1 className="section-title" style={{ fontSize: 32 }}>
            All Audit Missions (M1–M8)
          </h1>
          <p className="section-subtitle">
            Each mission combines a lawful Indian public data connector (Tiers 1–3) with a
            reproducible deterministic engine and mandatory 2-of-2 peer verification quorum.
          </p>
        </div>
      </div>

      <div className="filter-bar">
        <span className="font-mono" style={{ fontSize: 11.5, color: 'var(--ink-muted)' }}>
          PILLAR:
        </span>
        {pillars.map((p) => (
          <button
            key={p}
            type="button"
            className={`btn btn-xs ${pillarFilter === p ? 'btn-primary' : 'btn-ghost'}`}
            onClick={() => setPillarFilter(p)}
          >
            {p === 'all' ? 'All Pillars' : pillarLabel(p)}
          </button>
        ))}
      </div>

      {loading && <div className="state-banner">Loading mission catalog…</div>}
      {error && <div className="state-banner">Error loading missions: {error}</div>}

      {!loading && !error && (
        <div className="fronts-grid">
          {visible.map((m) => {
            const pct =
              m.total_tasks > 0
                ? Math.min(100, Math.round((m.completed_tasks / m.total_tasks) * 100))
                : 0;
            return (
              <Link
                key={m.slug}
                to={`/missions/${encodeURIComponent(m.slug)}`}
                className="front-card"
              >
                <div className="front-top">
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                    <span className="tag-mono tag-method">{m.methodology_code}</span>
                    <span className="tag-mono">{pillarLabel(m.pillar)}</span>
                    <span className="tag-mono">{m.jurisdiction}</span>
                    {m.data_mode === 'demo_sample' && (
                      <span className="badge-demo">SAMPLE DEMO DATA</span>
                    )}
                  </div>
                  <span className="font-mono tabular" style={{ fontWeight: 600 }}>
                    {pct}%
                  </span>
                </div>

                <h2 className="front-title">{m.title}</h2>
                {m.description && (
                  <p style={{ margin: 0, fontSize: 13, color: 'var(--ink-muted)' }}>
                    {m.description}
                  </p>
                )}

                <div className="front-progress-track">
                  <div
                    className={`front-progress-fill ${m.verified_findings_count > 0 ? 'has-verified' : ''}`}
                    style={{ width: `${pct}%` }}
                  />
                </div>

                <div className="front-stats-row">
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    {m.statutory_anchors.map((a) => (
                      <span key={a} className="tag-mono" style={{ fontSize: 10.5 }}>
                        {a}
                      </span>
                    ))}
                  </div>
                  <div className="font-mono tabular" style={{ display: 'flex', gap: 10, fontSize: 11.5 }}>
                    <span style={{ color: 'var(--ep-verified-fg)', fontWeight: 600 }}>
                      {m.verified_findings_count} verified
                    </span>
                    <span style={{ color: 'var(--ep-cleared-fg)' }}>
                      {m.cleared_count ?? 0} cleared
                    </span>
                    <span>
                      {m.completed_tasks}/{m.total_tasks} tasks
                    </span>
                  </div>
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
