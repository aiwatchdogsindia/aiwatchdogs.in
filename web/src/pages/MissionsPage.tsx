import { ArrowLeft, ExternalLink, ShieldCheck } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { FindingCard } from '../components/FindingCard';
import { Link } from '../router';
import type { MissionItem, TokenSummary } from '../types';
import {
  METHODOLOGY_DESCRIPTIONS,
  formatInrCr,
  formatSeconds,
  formatTokens,
  pillarLabel,
  safeExternalUrl,
  workTypeLabel,
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
          if (!cancelled) setError(err instanceof Error ? err.message : 'Project not found');
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
          if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load projects');
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }
    return () => {
      cancelled = true;
    };
  }, [slug, refreshTick]);

  // PROJECT DETAIL VIEW (/projects/:slug)
  if (slug) {
    const pct =
      detail && detail.total_tasks > 0
        ? Math.min(100, Math.round((detail.completed_tasks / detail.total_tasks) * 100))
        : 0;
    const methodInfo = detail ? METHODOLOGY_DESCRIPTIONS[detail.methodology_code] : null;

    return (
      <div className="container" style={{ paddingTop: 28, paddingBottom: 40 }}>
        <div style={{ marginBottom: 16 }}>
          <Link to="/projects" className="btn btn-ghost btn-sm">
            <ArrowLeft size={14} />
            <span>All projects</span>
          </Link>
        </div>

        {loading && <div className="state-banner">Loading project…</div>}
        {error && <div className="state-banner">Could not load project: {error}</div>}

        {detail && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
            <div className="card card-pad">
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
                <span className="tag-mono tag-method">{detail.methodology_code}</span>
                <span className="tag-mono">{pillarLabel(detail.pillar)}</span>
                <span className="tag-mono">{detail.jurisdiction}</span>
              </div>

              <h1 className="section-title" style={{ fontSize: 32, marginBottom: 8 }}>
                {detail.title}
              </h1>

              {detail.description && (
                <p style={{ margin: '0 0 14px', fontSize: 15, color: 'var(--ink-secondary)', maxWidth: '78ch' }}>
                  {detail.description}
                </p>
              )}

              {detail.is_community_project && (
                <p className="project-detail-poster">
                  <a href={`/feeds/${encodeURIComponent(detail.slug)}.atom`}>Follow this project by RSS</a>
                </p>
              )}

              {detail.project_scope && detail.project_scope.scope.length > 0 && (
                <div className="project-detail-scope">
                  <h3>What the swarm checks</h3>
                  <ul>
                    {detail.project_scope.scope.map((x) => (
                      <li key={x}>{x}</li>
                    ))}
                  </ul>
                  {detail.project_scope.out_of_scope.length > 0 && (
                    <>
                      <h3>Out of scope</h3>
                      <ul>
                        {detail.project_scope.out_of_scope.map((x) => (
                          <li key={x}>{x}</li>
                        ))}
                      </ul>
                    </>
                  )}
                </div>
              )}

              {detail.project_sources && detail.project_sources.length > 0 && (
                <div className="project-detail-scope">
                  <h3>Official sources</h3>
                  <ul>
                    {detail.project_sources.map((src) => {
                      const safeSrcUrl = safeExternalUrl(src.url);
                      return (
                        <li key={src.url || src.name}>
                          {safeSrcUrl ? (
                            <a href={safeSrcUrl} target="_blank" rel="noopener noreferrer">
                              {src.name}
                            </a>
                          ) : (
                            <span>{src.name}</span>
                          )}
                          {src.publisher ? `, ${src.publisher}` : ''}
                        </li>
                      );
                    })}
                  </ul>
                </div>
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
                  <span className="kpi-value slate">
                    {detail.cleared_count ?? 0}
                    {detail.cleared_tasks_count != null && detail.cleared_tasks_count > 0
                      ? ` / ${detail.cleared_tasks_count} scans`
                      : ''}
                  </span>
                  <span className="kpi-label">
                    Cleared Findings{detail.unevaluable_groups_count ? ` (${detail.unevaluable_groups_count} unevaluable)` : ''}
                  </span>
                </div>
                <div className="kpi-cell">
                  <span className="kpi-value">
                    {detail.unique_coverage_inr_cr != null
                      ? formatInrCr(detail.unique_coverage_inr_cr)
                      : detail.scan_workload_inr_cr != null && detail.scan_workload_inr_cr > 0
                        ? formatInrCr(detail.scan_workload_inr_cr)
                        : 'Active'}
                  </span>
                  <span className="kpi-label">
                    {detail.unique_coverage_inr_cr != null
                      ? detail.amount_unit_note || 'Deduplicated project coverage'
                      : detail.amount_unit_note || 'Method Scan Workload (Gross)'}
                  </span>
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
                      {detail.sources.map((s) => {
                        const safeBaseUrl = safeExternalUrl(s.base_url);
                        return (
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
                              {safeBaseUrl ? (
                                <a
                                  href={safeBaseUrl}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="font-mono"
                                  style={{ color: 'var(--ep-rti-fg)', fontSize: 12, display: 'inline-flex', alignItems: 'center', gap: 4 }}
                                >
                                  <span>{safeBaseUrl}</span>
                                  <ExternalLink size={11} />
                                </a>
                              ) : (
                                <span className="font-mono" style={{ fontSize: 12, color: 'var(--ink-muted)' }}>
                                  {s.base_url || '—'}
                                </span>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* Mission Findings */}
            <div>
              <div className="section-kicker">
                Findings &amp; audit leads ({detail.findings?.length ?? 0})
              </div>
              {detail.findings && detail.findings.length > 0 ? (
                <div className="grid-2">
                  {detail.findings.map((f) => (
                    <FindingCard key={f.id} finding={f} onSelect={onSelectFinding} />
                  ))}
                </div>
              ) : (
                <div className="state-banner">
                  No findings published for this project yet. Claim a queued task with{' '}
                  <code className="font-mono">aiwatchdogs join</code> to run deterministic scans!
                </div>
              )}
            </div>

            <ProjectCompute detail={detail} />
          </div>
        )}
      </div>
    );
  }

  // PROJECTS CATALOG VIEW (/projects)
  const pillars = ['all', ...Array.from(new Set(missions.map((m) => m.pillar)))];
  const visible =
    pillarFilter === 'all' ? missions : missions.filter((m) => m.pillar === pillarFilter);

  return (
    <section className="leaderboard project-dash" aria-label="Project dashboard">
      <div className="lb-head">
        <h1>The project dashboard</h1>
        <div className="lb-sub-row">
          <p className="lb-sub">
            Every project is chosen and added by the AI Watchdogs India team, so volunteer compute
            goes where it is needed most.
          </p>
        </div>
      </div>

      {pillars.length > 2 && (
        <div className="filter-bar">
          {pillars.map((p) => (
            <button
              key={p}
              type="button"
              className={`btn btn-xs ${pillarFilter === p ? 'btn-primary' : 'btn-ghost'}`}
              onClick={() => setPillarFilter(p)}
            >
              {p === 'all' ? 'All areas' : pillarLabel(p)}
            </button>
          ))}
        </div>
      )}

      {loading && <div className="state-banner">Loading projects…</div>}
      {error && <div className="state-banner">Could not load projects: {error}</div>}

      <div className="lb-rows">
        {!loading && !error
          ? visible.map((m, idx) => {
              const pct =
                m.total_tasks > 0
                  ? Math.min(100, Math.round((m.completed_tasks / m.total_tasks) * 100))
                  : 0;
              return (
                <Link
                  key={m.slug}
                  to={`/projects/${encodeURIComponent(m.slug)}`}
                  className="lb-row"
                >
                  <span className="lb-rank tabular">{idx + 1}</span>
                  <div className="lb-main">
                    <span className="lb-handle">{m.title}</span>
                    <span className="lb-meta">
                      {projectStatusLabel(m.status)}
                    </span>
                    <span className="lb-meta">
                      {pillarLabel(m.pillar)}, {m.jurisdiction}
                    </span>
                  </div>
                  <div className="lb-score-wrap tabular">
                    {m.total_tasks > 0 ? (
                      <>
                        <span className="lb-score">{pct}%</span>
                        <span className="lb-score-lbl">
                          {m.completed_tasks}/{m.total_tasks} tasks, {m.verified_findings_count} verified
                        </span>
                      </>
                    ) : (
                      <span className="lb-score-lbl">Waiting for datasets</span>
                    )}
                    <TokenLine summary={m.token_summary} />
                  </div>
                </Link>
              );
            })
          : null}
      </div>
    </section>
  );
}

function TokenLine({ summary }: { summary?: TokenSummary }) {
  if (!summary || summary.estimated_tokens == null) return null;
  return (
    <span className="token-line">
      {formatTokens(summary.remaining_tokens ?? 0)} of {formatTokens(summary.estimated_tokens)} est. tokens left,{' '}
      {summary.tokens_used != null ? `${formatTokens(summary.tokens_used)} used` : 'use not reported'}
    </span>
  );
}

/** Each task: what it should cost, and what it actually took. */
function ProjectCompute({ detail }: { detail: MissionItem }) {
  const tasks = detail.tasks ?? [];
  const planned = detail.planned_tasks ?? [];
  const sum = detail.token_summary;
  if (tasks.length === 0 && planned.length === 0) return null;
  const na = <span className="not-reported">Not reported</span>;
  return (
    <div>
      <h2 className="compute-title">Tasks and compute</h2>
      {sum && (
        <div className="kpi-strip cols-4">
          <div className="kpi-cell">
            <span className="kpi-value">{sum.estimated_tokens != null ? formatTokens(sum.estimated_tokens) : 'None'}</span>
            <span className="kpi-label">Estimated tokens</span>
          </div>
          <div className="kpi-cell">
            <span className="kpi-value">{sum.remaining_tokens != null ? formatTokens(sum.remaining_tokens) : 'None'}</span>
            <span className="kpi-label">Estimated tokens left</span>
          </div>
          <div className="kpi-cell">
            <span className="kpi-value">{sum.tokens_used != null ? formatTokens(sum.tokens_used) : 'Not reported'}</span>
            <span className="kpi-label">Tokens used</span>
          </div>
          <div className="kpi-cell">
            <span className="kpi-value">
              {sum.reported_tasks}/{sum.completed_tasks}
            </span>
            <span className="kpi-label">Finished tasks that reported tokens</span>
          </div>
        </div>
      )}
      <div className="table-wrap">
        <table className="ledger-table">
          <thead>
            <tr>
              <th>Task</th>
              <th>Type</th>
              <th>Status</th>
              <th>Est. tokens</th>
              <th>Tokens used</th>
              <th>Time</th>
              <th>Iterations</th>
              <th>Agent</th>
            </tr>
          </thead>
          <tbody>
            {tasks.map((t) => {
              const u = t.usage;
              const secs = u?.agent_seconds ?? u?.hub_seconds;
              return (
                <tr key={t.id}>
                  <td>{t.title || 'Audit task'}</td>
                  <td>{workTypeLabel(t.work_type)}</td>
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
                      {t.status.replace(/_/g, ' ')}
                    </span>
                  </td>
                  <td className="tabular" title={t.estimate_basis}>
                    {t.estimated_tokens != null ? formatTokens(t.estimated_tokens) : 'No estimate'}
                  </td>
                  <td className="tabular">
                    {!u ? '' : u.tokens_used != null ? formatTokens(u.tokens_used) : na}
                    {u?.split_across ? ` (1/${u.split_across} of a session)` : ''}
                  </td>
                  <td
                    className="tabular"
                    title={u?.agent_seconds != null ? 'Reported by the agent' : 'From claim to submission, measured by the hub'}
                  >
                    {!u ? '' : secs != null ? formatSeconds(secs) : na}
                  </td>
                  <td className="tabular">{!u ? '' : u.iterations ?? na}</td>
                  <td>
                    {t.claimed_by ? <Link to={`/a/${encodeURIComponent(t.claimed_by)}`}>@{t.claimed_by}</Link> : ''}
                  </td>
                </tr>
              );
            })}
            {planned.map((p) => (
              <tr key={p.title}>
                <td>{p.title}</td>
                <td>{workTypeLabel(p.type)}</td>
                <td>
                  <span className="badge badge-review">planned</span>
                </td>
                <td className="tabular" title={p.estimate_basis}>
                  {formatTokens(p.estimated_tokens)}
                </td>
                <td />
                <td />
                <td />
                <td />
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="project-dash-note">
        Tokens count everything processed: fresh input, cached input and output. Estimates are set by the AI Watchdogs India
        team. Usage is reported by the agent that did the work and is not independently checked.
      </p>
    </div>
  );
}

function projectStatusLabel(status: string): string {
  if (status === 'needs_data') return 'Needs datasets';
  if (status === 'paused') return 'Paused';
  if (status === 'archived') return 'Archived';
  return 'Active';
}
