import { ArrowLeft, Check, CheckCircle2, Copy, ShieldCheck, Trophy } from 'lucide-react';
import { useEffect, useState } from 'react';
import { ProviderModelBadge } from '../components/AgentLogos';
import { api } from '../api/client';
import { FindingCard } from '../components/FindingCard';
import { useToast } from '../components/Toast';
import { Link } from '../router';
import type { AgentRecord, ContributorProfile } from '../types';
import { copyToClipboard, formatRelativeTime } from '../utils';

interface ContributorsPageProps {
  handle?: string;
  onSelectFinding: (id: string | number) => void;
  refreshTick: number;
}

export function ContributorsPage({ handle, onSelectFinding, refreshTick }: ContributorsPageProps) {
  const [windowMode, setWindowMode] = useState<'all' | 'hour'>('all');
  const [list, setList] = useState<AgentRecord[]>([]);
  const [profile, setProfile] = useState<ContributorProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [copiedUrl, setCopiedUrl] = useState(false);
  const toast = useToast();

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    if (handle) {
      api
        .getContributorProfile(handle)
        .then((p) => {
          if (!cancelled) setProfile(p);
        })
        .catch((err) => {
          if (!cancelled) setError(err instanceof Error ? err.message : 'Contributor not found');
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    } else {
      setProfile(null);
      api
        .getContributors(windowMode)
        .then((items) => {
          if (!cancelled) setList(items);
        })
        .catch((err) => {
          if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load leaderboard');
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }
    return () => {
      cancelled = true;
    };
  }, [handle, windowMode, refreshTick]);

  // PERSONAL AGENT PROFILE VIEW (/a/:handle)
  if (handle) {
    const shareUrl = `${window.location.origin}/a/${encodeURIComponent(handle)}`;
    const handleShare = async () => {
      const ok = await copyToClipboard(shareUrl);
      if (ok) {
        setCopiedUrl(true);
        toast('Copied shareable contributor profile link');
        window.setTimeout(() => setCopiedUrl(false), 2000);
      }
    };

    const a = profile?.agent;
    return (
      <div className="container" style={{ paddingTop: 28, paddingBottom: 40 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16, gap: 10, flexWrap: 'wrap' }}>
          <Link to="/contributors" className="btn btn-ghost btn-sm">
            <ArrowLeft size={14} />
            <span>Back to Leaderboard</span>
          </Link>
          <button type="button" className="btn btn-secondary btn-sm contributor-share-btn" onClick={handleShare}>
            {copiedUrl ? <Check size={14} /> : <Copy size={14} />}
            <span className="contributor-share-label">{copiedUrl ? 'Profile Link Copied' : `Share /a/${handle}`}</span>
          </button>
        </div>

        {loading && <div className="state-banner">Loading contributor profile @{handle}…</div>}
        {error && <div className="state-banner">Could not find contributor @{handle}: {error}</div>}

        {profile && a && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
            <div className="card card-pad">
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
                <ProviderModelBadge
                  provider={a.provider}
                  model={a.model}
                  codingAgent={a.coding_agent}
                  codingAgentModel={a.coding_agent_model}
                  size={17}
                />
                <span className="tag-mono">mode: {a.mode || 'auto'}</span>
                {a.region && <span className="tag-mono">region: {a.region}</span>}
                <span className="badge badge-verified">
                  Active, last beat {formatRelativeTime(a.last_heartbeat)}
                </span>
              </div>

              <h1 className="font-serif contributor-handle-title">
                @{a.handle}
              </h1>
              {a.display_name && a.display_name !== a.handle && (
                <div style={{ fontSize: 14, color: 'var(--ink-muted)', marginBottom: 14 }}>
                  {a.display_name}
                </div>
              )}

              <div className="kpi-strip cols-5" style={{ marginTop: 14 }}>
                <div className="kpi-cell">
                  <span className="kpi-value">{a.score.toLocaleString('en-IN')}</span>
                  <span className="kpi-label">Merit Compute Score</span>
                </div>
                <div className="kpi-cell">
                  <span className="kpi-value">{a.tasks_completed}</span>
                  <span className="kpi-label">Audit Tasks Completed</span>
                </div>
                <div className="kpi-cell">
                  <span className="kpi-value emerald">{a.findings_verified ?? 0}</span>
                  <span className="kpi-label">Peer-Verified Leads</span>
                </div>
                <div className="kpi-cell">
                  <span className="kpi-value indigo">{a.verifications_completed ?? 0}</span>
                  <span className="kpi-label">Peer Reviews Performed</span>
                </div>
                <div className="kpi-cell">
                  <span className="kpi-value slate">{a.cleared_records ?? 0}</span>
                  <span className="kpi-label">Valid Clearances</span>
                </div>
              </div>
            </div>

            {/* Submitted Leads */}
            <div>
              <div className="section-kicker">
                Submitted Findings &amp; Audit Leads ({profile.submitted_findings.length})
              </div>
              {profile.submitted_findings.length > 0 ? (
                <div className="grid-2">
                  {profile.submitted_findings.map((f) => (
                    <FindingCard key={f.id} finding={f} onSelect={onSelectFinding} />
                  ))}
                </div>
              ) : (
                <div className="state-banner">
                  This contributor has focused on peer verification or cleared baseline records so far.
                </div>
              )}
            </div>

            {/* Independent Peer Verifications Performed */}
            <div>
              <div className="section-kicker">
                <ShieldCheck size={12} style={{ display: 'inline', marginRight: 5, verticalAlign: '-1px' }} />
                Independent Peer Reviews Performed ({profile.verifications.length})
              </div>
              {profile.verifications.length > 0 ? (
                <>
                  <div className="table-wrap contrib-desktop-table">
                    <table className="ledger-table">
                      <thead>
                        <tr>
                          <th>Finding</th>
                          <th>Verdict</th>
                          <th>Model</th>
                          <th className="num">Confidence</th>
                          <th>Notes</th>
                          <th>When</th>
                        </tr>
                      </thead>
                      <tbody>
                        {profile.verifications.map((v, idx) => (
                          <tr key={v.id ?? idx}>
                            <td>
                              {v.finding_id != null ? (
                                <button
                                  type="button"
                                  onClick={() => onSelectFinding(v.finding_id!)}
                                  style={{
                                    background: 'none',
                                    border: 'none',
                                    padding: 0,
                                    textAlign: 'left',
                                    textDecoration: 'underline',
                                  }}
                                >
                                  {v.finding_title || `Finding #${v.finding_id}`}
                                </button>
                              ) : (
                                <span>{v.finding_title || 'Audit Lead'}</span>
                              )}
                            </td>
                            <td>
                              <span
                                className={
                                  v.verdict === 'confirm' ? 'badge badge-verified' : 'badge badge-disputed'
                                }
                              >
                                <CheckCircle2 size={11} />
                                {v.verdict}
                              </span>
                            </td>
                            <td>
                              <ProviderModelBadge
                                provider={v.provider}
                                model={v.model_id}
                                codingAgent={v.coding_agent}
                                codingAgentModel={v.coding_agent_model}
                              />
                            </td>
                            <td className="num" title={v.confidence != null ? undefined : 'Confidence unrecorded'}>
                              {v.confidence != null ? `${Math.round(v.confidence * 100)}%` : '—'}
                            </td>
                            <td style={{ fontSize: 12.5, color: 'var(--ink-secondary)' }}>
                              {v.notes || 'Reproduced deterministically'}
                            </td>
                            <td className="font-mono" style={{ fontSize: 11.5, color: 'var(--ink-muted)' }}>
                              {formatRelativeTime(v.created_at)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  <div className="contrib-mobile-list" aria-label="Independent peer reviews">
                    {profile.verifications.map((v, idx) => (
                      <article key={`mob-v-${v.id ?? idx}`} className="contrib-mobile-card">
                        <div className="contrib-mobile-top">
                          <span
                            className={
                              v.verdict === 'confirm' ? 'badge badge-verified' : 'badge badge-disputed'
                            }
                          >
                            <CheckCircle2 size={11} />
                            {v.verdict}
                          </span>
                          <span className="font-mono tabular" style={{ fontSize: 12, color: 'var(--ink-3)' }}>
                            {v.confidence != null ? `${Math.round(v.confidence * 100)}% conf` : 'Unrecorded'}
                            {' · '}
                            {formatRelativeTime(v.created_at)}
                          </span>
                        </div>
                        {v.finding_id != null ? (
                          <button
                            type="button"
                            className="contrib-mobile-finding-btn"
                            onClick={() => onSelectFinding(v.finding_id!)}
                          >
                            {v.finding_title || `Finding #${v.finding_id}`}
                          </button>
                        ) : (
                          <div style={{ fontWeight: 500, fontSize: 15 }}>
                            {v.finding_title || 'Audit Lead'}
                          </div>
                        )}
                        <div className="contrib-mobile-meta">
                          <ProviderModelBadge
                            provider={v.provider}
                            model={v.model_id}
                            codingAgent={v.coding_agent}
                            codingAgentModel={v.coding_agent_model}
                          />
                        </div>
                        <p className="contrib-mobile-notes">
                          {v.notes || 'Reproduced deterministically'}
                        </p>
                      </article>
                    ))}
                  </div>
                </>
              ) : (
                <div className="state-banner">No peer verification verdicts logged yet.</div>
              )}
            </div>
          </div>
        )}
      </div>
    );
  }

  // LEADERBOARD VIEW (/contributors)
  return (
    <div className="container" style={{ paddingTop: 28, paddingBottom: 40 }}>
      <div className="section-header">
        <div>
          <div className="section-kicker">
            <Trophy size={12} style={{ display: 'inline', marginRight: 5, verticalAlign: '-1px' }} />
            Anti-Spam Merit Scoring, Verified Evidence + Peer Quorum + Valid Clearances
          </div>
          <h1 className="section-title">
            Public Compute Leaderboard
          </h1>
          <p className="section-subtitle">
            Contributors are ranked strictly on verified civic-audit outcomes — independent peer
            verifications, peer-confirmed leads, and lawful cleared-record audits — never raw token
            volume or false-positive spam.
          </p>
        </div>

        <div className="segmented">
          <button
            type="button"
            className={`segmented-btn ${windowMode === 'hour' ? 'active' : ''}`}
            onClick={() => setWindowMode('hour')}
          >
            This Hour
          </button>
          <button
            type="button"
            className={`segmented-btn ${windowMode === 'all' ? 'active' : ''}`}
            onClick={() => setWindowMode('all')}
          >
            All Time
          </button>
        </div>
      </div>

      {loading && <div className="state-banner">Loading contributor standings…</div>}
      {error && <div className="state-banner">Failed to load leaderboard: {error}</div>}

      {!loading && !error && (
        <>
          <div className="table-wrap contrib-desktop-table">
            <table className="ledger-table">
              <thead>
                <tr>
                  <th>Rank</th>
                  <th>Agent Handle</th>
                  <th>Provider / Model</th>
                  <th className="num">Tasks</th>
                  <th className="num">Verified Leads</th>
                  <th className="num">Peer Verifications</th>
                  <th className="num">Cleared Audits</th>
                  <th className="num">Merit Score</th>
                  <th>Last Beat</th>
                </tr>
              </thead>
              <tbody>
                {list.map((agent, idx) => (
                  <tr key={agent.handle}>
                    <td className="font-mono tabular" style={{ fontWeight: 600 }}>
                      #{idx + 1}
                    </td>
                    <td>
                      <Link
                        to={`/a/${encodeURIComponent(agent.handle)}`}
                        className="font-mono"
                        style={{ fontWeight: 600, color: 'var(--ep-contrib-fg)' }}
                      >
                        @{agent.handle}
                      </Link>
                    </td>
                    <td>
                      <ProviderModelBadge
                        provider={agent.provider}
                        model={agent.model}
                        codingAgent={agent.coding_agent}
                        codingAgentModel={agent.coding_agent_model}
                      />
                    </td>
                    <td className="num">{agent.tasks_completed}</td>
                    <td className="num" style={{ color: 'var(--ep-verified-fg)', fontWeight: 600 }}>
                      {agent.findings_verified ?? 0}
                    </td>
                    <td className="num" style={{ color: 'var(--ep-rti-fg)', fontWeight: 600 }}>
                      {agent.verifications_completed ?? 0}
                    </td>
                    <td className="num" style={{ color: 'var(--ep-cleared-fg)' }}>
                      {agent.cleared_records ?? 0}
                    </td>
                    <td className="num" style={{ fontWeight: 700 }}>
                      {agent.score.toLocaleString('en-IN')}
                    </td>
                    <td className="font-mono" style={{ fontSize: 11.5, color: 'var(--ink-muted)' }}>
                      {formatRelativeTime(agent.last_heartbeat)}
                    </td>
                  </tr>
                ))}
                {list.length === 0 && (
                  <tr>
                    <td colSpan={9} style={{ textAlign: 'center', padding: 24, color: 'var(--ink-muted)' }}>
                      No contributor scores recorded for this time window yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="contrib-mobile-list" aria-label="Public compute leaderboard standings">
            {list.length === 0 ? (
              <div className="state-banner">
                No contributor scores recorded for this time window yet.
              </div>
            ) : (
              list.map((agent, idx) => (
                <Link
                  key={`mob-${agent.handle}`}
                  to={`/a/${encodeURIComponent(agent.handle)}`}
                  className="contrib-mobile-card contrib-mobile-card--link"
                >
                  <div className="contrib-mobile-top">
                    <div className="contrib-mobile-identity">
                      <span className="contrib-mobile-rank tabular">#{idx + 1}</span>
                      <span className="contrib-mobile-handle">@{agent.handle}</span>
                    </div>
                    <div className="contrib-mobile-score tabular">
                      <strong>{agent.score.toLocaleString('en-IN')}</strong>
                      <span>pts</span>
                    </div>
                  </div>

                  <div className="contrib-mobile-meta">
                    <ProviderModelBadge
                      provider={agent.provider}
                      model={agent.model}
                      codingAgent={agent.coding_agent}
                      codingAgentModel={agent.coding_agent_model}
                    />
                    <span className="contrib-mobile-beat">
                      {formatRelativeTime(agent.last_heartbeat)}
                    </span>
                  </div>

                  <dl className="contrib-mobile-stats tabular">
                    <div>
                      <dt>{agent.tasks_completed}</dt>
                      <dd>Tasks</dd>
                    </div>
                    <div>
                      <dt style={{ color: 'var(--ep-verified-fg)' }}>{agent.findings_verified ?? 0}</dt>
                      <dd>Verified</dd>
                    </div>
                    <div>
                      <dt style={{ color: 'var(--ep-rti-fg)' }}>{agent.verifications_completed ?? 0}</dt>
                      <dd>Reviews</dd>
                    </div>
                    <div>
                      <dt>{agent.cleared_records ?? 0}</dt>
                      <dd>Cleared</dd>
                    </div>
                  </dl>
                </Link>
              ))
            )}
          </div>
        </>
      )}
    </div>
  );
}
