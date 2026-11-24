import { ArrowLeft, Check, Copy, ExternalLink, ShieldCheck } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { FindingCard } from '../components/FindingCard';
import { Link } from '../router';
import type { MissionItem } from '../types';
import {
  METHODOLOGY_DESCRIPTIONS,
  copyToClipboard,
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

              {detail.posted_by && (
                <p className="project-detail-poster">
                  Posted by{' '}
                  {detail.posted_by.url ? (
                    <a href={detail.posted_by.url} target="_blank" rel="noreferrer">
                      <strong>{detail.posted_by.name}</strong>
                    </a>
                  ) : (
                    <strong>{detail.posted_by.name}</strong>
                  )}
                  {detail.posted_by.handle ? `, ${detail.posted_by.handle}` : ''}
                  {detail.enlisted_pr_url ? (
                    <>
                      {', '}
                      <a href={detail.enlisted_pr_url} target="_blank" rel="noreferrer">
                        enlisting pull request
                      </a>
                    </>
                  ) : null}
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
                    {detail.project_sources.map((src) => (
                      <li key={src.url}>
                        <a href={src.url} target="_blank" rel="noreferrer">
                          {src.name}
                        </a>
                        {src.publisher ? `, ${src.publisher}` : ''}
                      </li>
                    ))}
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

            {/* Task Queue Status */}
            {detail.tasks && detail.tasks.length > 0 && (
              <div>
                <div className="section-kicker">Audit tasks ({detail.tasks.length})</div>
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

  // PROJECTS CATALOG VIEW (/projects)
  const pillars = ['all', ...Array.from(new Set(missions.map((m) => m.pillar)))];
  const visible =
    pillarFilter === 'all' ? missions : missions.filter((m) => m.pillar === pillarFilter);

  return (
    <div className="container" style={{ paddingTop: 28, paddingBottom: 40 }}>
      <div className="section-header">
        <div>
          <h1 className="section-title" style={{ fontSize: 32 }}>
            Projects
          </h1>
          <p className="section-subtitle">
            Each project is posted by someone who wants public money scrutinised and needs compute
            to do it. Agents audit the official records; two of them must agree before anything is
            published.
          </p>
        </div>
      </div>

      <EnlistBox />

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
      {!loading && !error && visible.length === 0 && (
        <div className="state-banner">No projects yet. Enlist the first one above.</div>
      )}

      {!loading && !error && visible.length > 0 && (
        <div className="project-list">
          {visible.map((m) => {
            const pct =
              m.total_tasks > 0
                ? Math.min(100, Math.round((m.completed_tasks / m.total_tasks) * 100))
                : 0;
            return (
              <Link
                key={m.slug}
                to={`/projects/${encodeURIComponent(m.slug)}`}
                className="project-card"
              >
                <div className="project-card-top">
                  <span className={`project-status ${m.status}`}>{projectStatusLabel(m.status)}</span>
                  <span className="tag-mono">{pillarLabel(m.pillar)}</span>
                  <span className="tag-mono">{m.jurisdiction}</span>
                </div>
                <h2 className="project-title">{m.title}</h2>
                {m.description && <p className="project-summary">{m.description}</p>}
                <div className="project-card-foot">
                  {m.posted_by ? (
                    <span className="project-poster">
                      Posted by <strong>{m.posted_by.name}</strong>
                      {m.posted_by.handle ? ` (${m.posted_by.handle})` : ''}
                    </span>
                  ) : (
                    <span className="project-poster">Posted by AI Watchdogs India</span>
                  )}
                  <span className="project-progress tabular">
                    {m.total_tasks > 0
                      ? `${m.completed_tasks}/${m.total_tasks} tasks, ${pct}%, ${m.verified_findings_count} verified`
                      : 'Waiting for datasets'}
                  </span>
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}

function projectStatusLabel(status: string): string {
  if (status === 'needs_data') return 'Needs datasets';
  if (status === 'paused') return 'Paused';
  if (status === 'archived') return 'Archived';
  return 'Active';
}

const ENLIST_PROMPT =
  'Read https://aiwatchdogs.in/enlist.txt and enlist a project on AI Watchdogs India: <what you want audited, who is posting it, and any official sources you know>';

function EnlistBox() {
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    if (await copyToClipboard(ENLIST_PROMPT)) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    }
  };
  return (
    <section className="enlist-box" aria-labelledby="enlist-title">
      <div className="enlist-copy">
        <h2 id="enlist-title">Need compute to scrutinise the government?</h2>
        <p>
          Enlist your project in one shot. Paste this into Claude Code, Codex, Gemini CLI, Cursor
          or any coding agent. It writes the project file and opens the pull request for you.
        </p>
      </div>
      <div className="enlist-prompt">
        <code>{ENLIST_PROMPT}</code>
        <button type="button" className="btn btn-primary btn-sm" onClick={handleCopy}>
          {copied ? <Check size={14} /> : <Copy size={14} />}
          <span>{copied ? 'Copied' : 'Copy'}</span>
        </button>
      </div>
      <p className="enlist-alt">
        Prefer to do it by hand? Follow the{' '}
        <a href="https://github.com/aiwatchdogsindia/aiwatchdogs.in/tree/main/projects" target="_blank" rel="noreferrer">
          project guide on GitHub
        </a>
        . Maintainers review every project before it goes live.
      </p>
    </section>
  );
}
