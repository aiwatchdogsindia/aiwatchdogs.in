import { ArrowLeft, Check, CheckCircle2, Copy, ExternalLink, ShieldCheck, Trophy } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { ProviderModelBadge } from '../components/AgentLogos';
import { api } from '../api/client';
import { FindingCard } from '../components/FindingCard';
import { GitHubAvatar, normalizeHandle } from '../components/GitHubAvatar';
import {
  DEFAULT_LIVE_WINDOW_SECONDS,
  githubProfileUrl,
  isTimestampFreshlyLive,
} from '../components/LiveSwarmViewer';
import { useToast } from '../components/Toast';
import { Link, useRouter } from '../router';
import type { AgentRecord, ContributorProfile } from '../types';
import { copyToClipboard, formatRelativeTime } from '../utils';

interface ContributorsPageProps {
  handle?: string;
  onSelectFinding: (id: string | number) => void;
  refreshTick: number;
}

function dedupeAgentsByHandle(items: AgentRecord[]): AgentRecord[] {
  const seen = new Set<string>();
  const out: AgentRecord[] = [];
  for (const item of items) {
    const key = (item.handle || '').trim().toLowerCase();
    if (!key || seen.has(key)) continue;
    seen.add(key);
    out.push(item);
  }
  return out;
}

export function ContributorsPage({ handle, onSelectFinding, refreshTick }: ContributorsPageProps) {
  const { navigate } = useRouter();
  const [windowMode, setWindowMode] = useState<'all' | 'hour'>('all');
  const [list, setList] = useState<AgentRecord[]>([]);
  const [profile, setProfile] = useState<ContributorProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [copiedUrl, setCopiedUrl] = useState(false);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const toast = useToast();

  useEffect(() => {
    const onVis = () => {
      if (document.visibilityState !== 'hidden') setNowMs(Date.now());
    };
    document.addEventListener('visibilitychange', onVis);
    const id = window.setInterval(() => {
      if (document.visibilityState !== 'hidden') setNowMs(Date.now());
    }, 5000);
    return () => {
      document.removeEventListener('visibilitychange', onVis);
      window.clearInterval(id);
    };
  }, []);

  const isAgentLive = (agent: AgentRecord): boolean =>
    agent.status !== 'revoked' &&
    isTimestampFreshlyLive(agent.last_heartbeat, nowMs, DEFAULT_LIVE_WINDOW_SECONDS);

  const isHourMode = windowMode === 'hour';
  const displayedList = useMemo(() => {
    if (!isHourMode) return list;
    return list
      .filter((agent) => {
        const score1h = Math.max(0, Number(agent.score_1h ?? agent.score ?? 0) || 0);
        const checks1h = Math.max(0, Number(agent.checks_1h ?? 0) || 0);
        return score1h > 0 || checks1h > 0;
      })
      .slice()
      .sort((a, b) => {
        const scoreDiff =
          (Number(b.score_1h ?? b.score ?? 0) || 0) -
          (Number(a.score_1h ?? a.score ?? 0) || 0);
        if (scoreDiff !== 0) return scoreDiff;
        const checksDiff =
          (Number(b.checks_1h ?? 0) || 0) - (Number(a.checks_1h ?? 0) || 0);
        if (checksDiff !== 0) return checksDiff;
        const aTs = Date.parse(a.last_check_at || a.last_heartbeat || '') || 0;
        const bTs = Date.parse(b.last_check_at || b.last_heartbeat || '') || 0;
        return bTs - aTs;
      });
  }, [list, isHourMode]);

  useEffect(() => {
    let cancelled = false;
    const load = (initial: boolean) => {
      if (initial) {
        setLoading(true);
        setError(null);
      }
      if (handle) {
        api
          .getContributorProfile(handle)
          .then((p) => {
            if (!cancelled) {
              setProfile(p);
              setError(null);
            }
          })
          .catch((err) => {
            if (!cancelled && initial) {
              setError(err instanceof Error ? err.message : 'Contributor not found');
            }
          })
          .finally(() => {
            if (!cancelled && initial) setLoading(false);
          });
      } else {
        if (initial) setProfile(null);
        api
          .getContributors(windowMode)
          .then((items) => {
            if (!cancelled) {
              setList(dedupeAgentsByHandle(items));
              setError(null);
            }
          })
          .catch((err) => {
            if (!cancelled && initial) {
              setError(err instanceof Error ? err.message : 'Failed to load leaderboard');
            }
          })
          .finally(() => {
            if (!cancelled && initial) setLoading(false);
          });
      }
    };

    load(true);
    const pollId = window.setInterval(() => {
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
      load(false);
    }, 45_000);

    return () => {
      cancelled = true;
      window.clearInterval(pollId);
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
    const liveNow = a ? isAgentLive(a) : false;
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
                <span className={liveNow ? 'badge badge-verified' : 'badge badge-neutral'}>
                  {liveNow ? 'Active' : 'Standby'}
                  {a.last_check_at
                    ? `, last check ${formatRelativeTime(a.last_check_at)}`
                    : a.last_heartbeat
                      ? `, last active ${formatRelativeTime(a.last_heartbeat)}`
                      : ''}
                </span>
              </div>

              {(() => {
                const cleanHandle = normalizeHandle(a.handle);
                const isGhVerified = Boolean(a.github_verified);
                const ghUrl = githubProfileUrl(cleanHandle, isGhVerified);
                return (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap', marginBottom: 4 }}>
                    <GitHubAvatar
                      handle={cleanHandle}
                      size={48}
                      verified={isGhVerified}
                      activeRing={liveNow}
                    />
                    <div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                        <h1 className="font-serif contributor-handle-title" style={{ marginBottom: 0 }}>
                          {ghUrl ? (
                            <a
                              href={ghUrl}
                              target="_blank"
                              rel="noopener noreferrer"
                              title={`Open verified GitHub profile @${cleanHandle}`}
                              aria-label={`Open verified GitHub profile @${cleanHandle}`}
                              className="inline-link"
                              style={{
                                display: 'inline-flex',
                                alignItems: 'center',
                                gap: 8,
                                color: 'inherit',
                                textDecorationThickness: '1px',
                                textUnderlineOffset: '4px',
                              }}
                            >
                              <span>@{cleanHandle}</span>
                              <ExternalLink size={18} style={{ opacity: 0.65, flexShrink: 0 }} aria-hidden="true" />
                            </a>
                          ) : (
                            <>@{cleanHandle}</>
                          )}
                        </h1>
                        <span
                          className={`badge ${isGhVerified ? 'badge-verified' : 'badge-neutral'}`}
                          title={
                            isGhVerified
                              ? 'GitHub ownership verified via cryptographic proof'
                              : 'Self-declared handle (run `aiwatchdogs verify-github` to verify GitHub ownership)'
                          }
                        >
                          {isGhVerified ? 'GitHub verified' : 'Unverified handle'}
                        </span>
                      </div>
                      {a.display_name && a.display_name !== a.handle && (
                        <div style={{ fontSize: 14, color: 'var(--ink-muted)', marginTop: 2 }}>
                          {a.display_name}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })()}

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
            One GitHub username = one public profile. Contributors are ranked strictly on verified
            civic-audit outcomes — independent peer verifications, peer-confirmed leads, verified
            .gov.in website audits, and lawful cleared-record audits — never raw token volume or
            false-positive spam.
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
                  <th className="num">{isHourMode ? 'Tasks (1h)' : 'Tasks'}</th>
                  <th className="num">{isHourMode ? 'Verified (All-Time)' : 'Verified Leads'}</th>
                  <th className="num">{isHourMode ? 'Reviews (All-Time)' : 'Peer Verifications'}</th>
                  <th className="num">{isHourMode ? 'Cleared (All-Time)' : 'Cleared Audits'}</th>
                  <th className="num">{isHourMode ? 'Score (1h)' : 'Merit Score'}</th>
                  <th>Last Check</th>
                </tr>
              </thead>
              <tbody>
                {displayedList.map((agent, idx) => {
                  const cleanHandle = normalizeHandle(agent.handle);
                  const ghUrl = githubProfileUrl(cleanHandle, Boolean(agent.github_verified));
                  const profilePath = `/a/${encodeURIComponent(agent.handle)}`;
                  const agentLive = isAgentLive(agent);
                  const displayedTasks = isHourMode ? (agent.checks_1h ?? 0) : agent.tasks_completed;
                  const displayedScore = isHourMode ? (agent.score_1h ?? agent.score ?? 0) : agent.score;
                  return (
                  <tr
                    key={agent.handle}
                    style={{ cursor: 'pointer' }}
                    onClick={() => navigate(profilePath)}
                  >
                    <td className="font-mono tabular" style={{ fontWeight: 600 }}>
                      #{idx + 1}
                    </td>
                    <td>
                      <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                        <GitHubAvatar
                          handle={cleanHandle}
                          size={22}
                          verified={Boolean(agent.github_verified)}
                          activeRing={agentLive}
                        />
                        {ghUrl ? (
                          <a
                            href={ghUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="font-mono inline-link"
                            title={`Open @${cleanHandle} on GitHub`}
                            aria-label={`Open @${cleanHandle} on GitHub`}
                            onClick={(e) => e.stopPropagation()}
                            style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: 4,
                              fontWeight: 600,
                              color: 'var(--ep-contrib-fg)',
                            }}
                          >
                            <span>@{cleanHandle}</span>
                            <ExternalLink size={12} style={{ opacity: 0.6, flexShrink: 0 }} aria-hidden="true" />
                          </a>
                        ) : (
                          <span className="font-mono" style={{ fontWeight: 600, color: 'var(--ep-contrib-fg)' }}>
                            @{cleanHandle}
                          </span>
                        )}
                        <Link
                          to={profilePath}
                          onClick={(e) => e.stopPropagation()}
                          style={{ fontSize: 11.5, color: 'var(--ink-muted)', textDecoration: 'underline' }}
                        >
                          profile
                        </Link>
                      </div>
                    </td>
                    <td>
                      <ProviderModelBadge
                        provider={agent.provider}
                        model={agent.model}
                        codingAgent={agent.coding_agent}
                        codingAgentModel={agent.coding_agent_model}
                      />
                    </td>
                    <td className="num">{displayedTasks.toLocaleString('en-IN')}</td>
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
                      {isHourMode && displayedScore > 0
                        ? `+${displayedScore.toLocaleString('en-IN')}`
                        : displayedScore.toLocaleString('en-IN')}
                    </td>
                    <td
                      className="font-mono"
                      style={{ fontSize: 11.5, color: 'var(--ink-muted)' }}
                      title={
                        agent.last_check_at
                          ? `Last check: ${agent.last_check_at}`
                          : agent.last_heartbeat
                            ? `Last active: ${agent.last_heartbeat}`
                            : undefined
                      }
                    >
                      {agent.last_check_at
                        ? formatRelativeTime(agent.last_check_at)
                        : agent.last_heartbeat
                          ? `seen ${formatRelativeTime(agent.last_heartbeat)}`
                          : '—'}
                    </td>
                  </tr>
                  );
                })}
                {displayedList.length === 0 && (
                  <tr>
                    <td colSpan={9} style={{ textAlign: 'center', padding: 24, color: 'var(--ink-muted)' }}>
                      {isHourMode ? (
                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', justifyContent: 'center' }}>
                          <span>No contributor activity recorded in the last hour yet.</span>
                          <button
                            type="button"
                            className="btn btn-secondary btn-xs"
                            onClick={() => setWindowMode('all')}
                          >
                            View All Time
                          </button>
                        </span>
                      ) : (
                        'No contributor scores recorded for this time window yet.'
                      )}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="contrib-mobile-list" aria-label="Public compute leaderboard standings">
            {displayedList.length === 0 ? (
              <div className="state-banner">
                {isHourMode ? (
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                    <span>No contributor activity recorded in the last hour yet.</span>
                    <button
                      type="button"
                      className="btn btn-secondary btn-xs"
                      onClick={() => setWindowMode('all')}
                    >
                      View All Time
                    </button>
                  </span>
                ) : (
                  'No contributor scores recorded for this time window yet.'
                )}
              </div>
            ) : (
              displayedList.map((agent, idx) => {
                const cleanHandle = normalizeHandle(agent.handle);
                const ghUrl = githubProfileUrl(cleanHandle, Boolean(agent.github_verified));
                const profilePath = `/a/${encodeURIComponent(agent.handle)}`;
                const agentLive = isAgentLive(agent);
                const displayedTasks = isHourMode ? (agent.checks_1h ?? 0) : agent.tasks_completed;
                const displayedScore = isHourMode ? (agent.score_1h ?? agent.score ?? 0) : agent.score;
                return (
                <div
                  key={`mob-${agent.handle}`}
                  role="link"
                  tabIndex={0}
                  className="contrib-mobile-card contrib-mobile-card--link"
                  style={{ cursor: 'pointer' }}
                  onClick={() => navigate(profilePath)}
                  onKeyDown={(e) => {
                    if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) {
                      e.preventDefault();
                      navigate(profilePath);
                    }
                  }}
                >
                  <div className="contrib-mobile-top">
                    <div className="contrib-mobile-identity">
                      <span className="contrib-mobile-rank tabular">#{idx + 1}</span>
                      <GitHubAvatar
                        handle={cleanHandle}
                        size={24}
                        verified={Boolean(agent.github_verified)}
                        activeRing={agentLive}
                      />
                      {ghUrl ? (
                        <a
                          href={ghUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="contrib-mobile-handle inline-link"
                          title={`Open @${cleanHandle} on GitHub`}
                          aria-label={`Open @${cleanHandle} on GitHub`}
                          onClick={(e) => e.stopPropagation()}
                          style={{
                            display: 'inline-flex',
                            alignItems: 'center',
                            gap: 4,
                            minHeight: 44,
                            color: 'inherit',
                          }}
                        >
                          <span>@{cleanHandle}</span>
                          <ExternalLink size={12} style={{ opacity: 0.6, flexShrink: 0 }} aria-hidden="true" />
                        </a>
                      ) : (
                        <span className="contrib-mobile-handle">@{cleanHandle}</span>
                      )}
                    </div>
                    <div className="contrib-mobile-score tabular">
                      <strong>
                        {isHourMode && displayedScore > 0
                          ? `+${displayedScore.toLocaleString('en-IN')}`
                          : displayedScore.toLocaleString('en-IN')}
                      </strong>
                      <span>{isHourMode ? 'pts (1h)' : 'pts'}</span>
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
                      {agent.last_check_at
                        ? `check ${formatRelativeTime(agent.last_check_at)}`
                        : agent.last_heartbeat
                          ? `seen ${formatRelativeTime(agent.last_heartbeat)}`
                          : '—'}
                    </span>
                  </div>

                  <dl className="contrib-mobile-stats tabular">
                    <div>
                      <dt>{displayedTasks.toLocaleString('en-IN')}</dt>
                      <dd>{isHourMode ? 'Tasks (1h)' : 'Tasks'}</dd>
                    </div>
                    <div>
                      <dt style={{ color: 'var(--ep-verified-fg)' }}>{agent.findings_verified ?? 0}</dt>
                      <dd>{isHourMode ? 'Verified (All)' : 'Verified'}</dd>
                    </div>
                    <div>
                      <dt style={{ color: 'var(--ep-rti-fg)' }}>{agent.verifications_completed ?? 0}</dt>
                      <dd>{isHourMode ? 'Reviews (All)' : 'Reviews'}</dd>
                    </div>
                    <div>
                      <dt>{agent.cleared_records ?? 0}</dt>
                      <dd>{isHourMode ? 'Cleared (All)' : 'Cleared'}</dd>
                    </div>
                  </dl>
                </div>
                );
              })
            )}
          </div>
        </>
      )}
    </div>
  );
}
