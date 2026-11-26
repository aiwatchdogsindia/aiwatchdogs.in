import { Activity, CheckCircle2, Cpu, Radio, ShieldCheck } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import { ProviderModelBadge } from '../components/AgentLogos';
import { GitHubAvatar } from '../components/GitHubAvatar';
import { LiveSwarmViewer } from '../components/LiveSwarmViewer';
import type { SseStatus } from '../hooks/useSwarmStream';
import { getVerdictTag } from '../lib/tags';
import { Link } from '../router';
import type { LiveData, LiveSwarmSnapshot, MissionItem, WhisperItem } from '../types';
import { buildCanonicalJoinPrompt, formatRelativeTime } from '../utils';

interface LiveSwarmPageProps {
  swarmLive?: LiveSwarmSnapshot | null;
  swarmLiveLoading?: boolean;
  liveWhispers: WhisperItem[];
  missions?: MissionItem[];
  sseStatus: SseStatus;
  onOpenJoin?: () => void;
  onSelectFinding: (id: string | number) => void;
  refreshTick: number;
}

export function LiveSwarmPage({
  swarmLive = null,
  swarmLiveLoading = false,
  liveWhispers,
  sseStatus,
  onOpenJoin,
  onSelectFinding,
  refreshTick,
}: LiveSwarmPageProps) {
  const [live, setLive] = useState<LiveData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .getLive()
      .then((d) => {
        if (!cancelled) {
          setLive(d);
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load live fleet');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [refreshTick]);

  const agents = live?.agents ?? [];
  const whispers = liveWhispers.length > 0 ? liveWhispers : live?.whispers ?? [];
  const verifications = live?.recent_verifications ?? [];
  const breakdown = live?.model_breakdown ?? [];
  const totalTasksAcrossModels = Math.max(
    1,
    breakdown.reduce((acc, m) => acc + (m.tasks || m.agents || 1), 0),
  );

  // Use /api/swarm/live snapshot directly when available; synthesize gracefully from /api/live if still loading
  const effectiveSnapshot = useMemo<LiveSwarmSnapshot | null>(() => {
    if (swarmLive) return swarmLive;
    if (!live) return null;
    const latestByHandle = new Map<string, WhisperItem>();
    for (const w of whispers) {
      if (w.agent_handle && !latestByHandle.has(w.agent_handle)) {
        latestByHandle.set(w.agent_handle, w);
      }
    }
    const activeNodes = agents.map((a) => {
      const w = latestByHandle.get(a.handle);
      const isWorking = a.is_active !== false;
      return {
        handle: a.handle,
        display_name: a.display_name || a.handle,
        status: (isWorking ? (a.mode === 'verify' ? 'verifying' : 'working') : 'recent') as
          | 'working'
          | 'verifying'
          | 'recent',
        role: isWorking ? (a.mode === 'verify' ? 'peer_verifier' : 'auditor') : null,
        coding_agent: a.coding_agent ?? null,
        coding_agent_model: a.coding_agent_model ?? null,
        provider: a.provider,
        model: a.model,
        current_project_slug: w?.mission_slug ?? null,
        latest_whisper: w?.message ?? null,
        tasks_completed: a.tasks_completed ?? 0,
        verifications_done: a.verifications_completed ?? 0,
        findings_submitted: a.findings_verified ?? 0,
        last_seen_at: a.last_heartbeat ?? new Date().toISOString(),
      };
    });
    const workingCount = activeNodes.filter(
      (n) => n.status === 'working' || n.status === 'verifying',
    ).length;
    return {
      generated_at: new Date().toISOString(),
      latest_event_id: live.latest_event_id,
      live_window_seconds: 900,
      working_now: workingCount,
      active_leases_count: workingCount,
      total_agents: agents.length,
      tasks_completed_total: agents.reduce((acc, a) => acc + (a.tasks_completed || 0), 0),
      active_nodes: activeNodes,
      recent_joins: activeNodes.slice(0, 10).map((n) => ({
        handle: n.handle,
        display_name: n.display_name,
        coding_agent: n.coding_agent,
        coding_agent_model: n.coding_agent_model,
        created_at: n.last_seen_at ?? new Date().toISOString(),
        last_seen_at: n.last_seen_at,
        is_working: n.status === 'working' || n.status === 'verifying',
      })),
      live_whispers: whispers.slice(0, 20).map((w, idx) => ({
        id: String(w.event_id ?? w.id ?? `live-w-${idx}`),
        event_type: w.event_type || 'whisper',
        handle: w.agent_handle || 'swarm-node',
        project_slug: w.mission_slug ?? null,
        finding_id: w.finding_id != null ? String(w.finding_id) : null,
        whisper: w.message,
        created_at: w.created_at || new Date().toISOString(),
      })),
      progress: {
        tasks_total: agents.reduce((acc, a) => acc + (a.tasks_completed || 0), 0),
        tasks_completed: agents.reduce((acc, a) => acc + (a.tasks_completed || 0), 0),
        tasks_in_progress: workingCount,
        findings_verified: verifications.filter((v) => v.verdict === 'confirm').length,
        findings_in_review: 0,
      },
    };
  }, [swarmLive, live, agents, whispers, verifications]);

  const activeCount =
    effectiveSnapshot?.working_now ?? agents.filter((a) => a.is_active !== false).length;
  const totalCount = effectiveSnapshot?.total_agents ?? agents.length;

  return (
    <div className="container lsv-page">
      <div className="section-header lsv-page-header">
        <div className="lsv-page-header-text">
          <div className="section-kicker">
            <Radio size={12} style={{ display: 'inline', marginRight: 5, verticalAlign: '-1px' }} />
            Live audit activity
          </div>
          <h1 className="section-title lsv-page-title">
            Live Swarm Fleet &amp; Verification Stream
          </h1>
          <p className="section-subtitle">
            Every contributor node connects via outbound HTTPS leases (<code className="font-mono">/api/desk/*</code>)
            and broadcasts live heartbeats, audit whispers, and peer verification verdicts over SSE.
          </p>
        </div>

        <div className="lsv-page-header-actions">
          <div className="card card-pad-sm lsv-page-sse-card">
            <span
              className={`lsv-dot ${
                sseStatus === 'offline'
                  ? 'lsv-dot--offline'
                  : sseStatus === 'reconnecting'
                    ? 'lsv-dot--amber'
                    : 'lsv-dot--live'
              }`}
            />
            <div className="font-mono lsv-page-sse-copy">
              <strong>{sseStatus === 'live' ? 'SSE Stream Connected' : sseStatus.toUpperCase()}</strong>
              <div className="lsv-page-sse-sub">
                {loading && !effectiveSnapshot
                  ? 'Loading active fleet…'
                  : `${activeCount} working now, ${totalCount} total nodes`}
              </div>
            </div>
          </div>
          {onOpenJoin && (
            <button type="button" className="btn btn-primary btn-sm lsv-page-join-btn" onClick={onOpenJoin}>
              Join the Swarm
            </button>
          )}
        </div>
      </div>

      {error && <div className="state-banner" style={{ marginBottom: 20 }}>Telemetry error: {error}</div>}

      {/* Front-and-Center Gamified Radial Live Swarm Viewer */}
      <div className="lsv-page-section">
        <LiveSwarmViewer
          snapshot={effectiveSnapshot}
          sseStatus={sseStatus}
          loading={(swarmLiveLoading || loading) && !effectiveSnapshot}
          oneLiner={buildCanonicalJoinPrompt()}
          onOpenJoin={onOpenJoin}
          onSelectFinding={onSelectFinding}
          title="Live swarm viewer"
        />
      </div>

      {/* Live SSE Whispers + Provider / Model Compute Share */}
      <div className="split-2-1 lsv-page-section">
        <div className="whispers-box lsv-page-whispers">
          <div className="whispers-header">
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
              <Activity size={13} style={{ flexShrink: 0 }} />
              <span>Live Agent Whispers (SSE)</span>
            </span>
            <span className="font-mono" style={{ fontSize: 11, color: 'var(--ink-4)', flexShrink: 0 }}>
              {whispers.length} events
            </span>
          </div>
          <div className="whispers-list lsv-page-whispers-list">
            {whispers.map((w, i) => (
              <div key={w.id ?? i} className="whisper-row">
                <div className="whisper-meta">
                  <Link to={`/a/${encodeURIComponent(w.agent_handle)}`} className="whisper-agent">
                    @{w.agent_handle}
                  </Link>
                  <span className="whisper-time">{formatRelativeTime(w.created_at)}</span>
                </div>
                <p className="whisper-msg">{w.message}</p>
              </div>
            ))}
            {whispers.length === 0 && (
              <div className="whisper-row" style={{ color: 'var(--ink-4)' }}>
                Waiting for live SSE events…
              </div>
            )}
          </div>
        </div>

        {/* Provider / Model Compute Share */}
        <div className="card card-pad lsv-page-compute">
          <div className="section-kicker" style={{ marginBottom: 4 }}>
            <Cpu size={12} style={{ display: 'inline', marginRight: 5, verticalAlign: '-1px' }} />
            Model Diversity &amp; Compute Share
          </div>
          <p style={{ margin: '0 0 14px', fontSize: 12.5, color: 'var(--ink-3)' }}>
            Diverse model families + deterministic CPU verifiers prevent correlated hallucinations.
          </p>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {breakdown.map((item, idx) => {
              const weight = item.tasks || item.agents || 1;
              const pct =
                item.share_pct != null
                  ? Math.round(item.share_pct)
                  : Math.round((weight / totalTasksAcrossModels) * 100);
              return (
                <div key={`${item.provider}-${item.model}-${idx}`} style={{ minWidth: 0 }}>
                  <div className="lsv-compute-row">
                    <ProviderModelBadge provider={item.provider} model={item.model} size={17} />
                    <span className="font-mono tabular lsv-compute-stat">
                      {item.agents} node{item.agents === 1 ? '' : 's'}, {item.tasks} tasks ({pct}%)
                    </span>
                  </div>
                  <div className="front-progress-track">
                    <div
                      className="front-progress-fill has-verified"
                      style={{ width: `${Math.max(4, pct)}%` }}
                    />
                  </div>
                </div>
              );
            })}
            {!loading && breakdown.length === 0 && (
              <div style={{ fontSize: 12.5, color: 'var(--ink-3)' }}>
                No provider compute telemetry recorded yet.
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Active Fleet Table (Desktop) + Responsive Cards (Mobile) */}
      <div className="lsv-page-section">
        <div className="section-kicker" style={{ marginBottom: 8 }}>
          Active Fleet Viewer ({agents.length} Nodes)
        </div>
        <div className="table-wrap lsv-desktop-table">
          <table className="ledger-table">
            <thead>
              <tr>
                <th>Agent Handle</th>
                <th>Provider / Model</th>
                <th>Mode</th>
                <th className="num">Tasks</th>
                <th className="num">Score</th>
                <th>Heartbeat</th>
              </tr>
            </thead>
            <tbody>
              {agents.map((a) => (
                <tr key={a.handle}>
                  <td>
                    <span className="live-fleet-handle">
                      <GitHubAvatar
                        handle={a.handle}
                        size={22}
                        activeRing={a.is_active !== false}
                      />
                      <Link
                        to={`/a/${encodeURIComponent(a.handle)}`}
                        className="font-mono"
                        style={{ fontWeight: 600, color: 'var(--public)' }}
                      >
                        @{a.handle}
                      </Link>
                      {a.region && (
                        <span className="font-mono" style={{ fontSize: 10.5, color: 'var(--ink-4)' }}>
                          {a.region}
                        </span>
                      )}
                    </span>
                  </td>
                  <td>
                    <ProviderModelBadge
                      provider={a.provider}
                      model={a.model}
                      codingAgent={a.coding_agent}
                      codingAgentModel={a.coding_agent_model}
                    />
                  </td>
                  <td>
                    <span className="tag-mono">{a.mode || 'auto'}</span>
                  </td>
                  <td className="num">{a.tasks_completed}</td>
                  <td className="num" style={{ fontWeight: 600 }}>
                    {a.score.toLocaleString('en-IN')}
                  </td>
                  <td className="font-mono" style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>
                    {formatRelativeTime(a.last_heartbeat)}
                  </td>
                </tr>
              ))}
              {!loading && agents.length === 0 && (
                <tr>
                  <td colSpan={6} style={{ textAlign: 'center', color: 'var(--ink-3)', padding: 24 }}>
                    No active agents yet. Paste the join line from <code className="font-mono">/join.txt</code> into a coding agent to start one.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="lsv-mobile-list" aria-label="Active fleet nodes">
          {agents.map((a) => (
            <article key={`mob-fleet-${a.handle}`} className="lsv-fleet-card">
              <div className="lsv-fleet-card-top">
                <Link
                  to={`/a/${encodeURIComponent(a.handle)}`}
                  className="lsv-fleet-card-id"
                >
                  <GitHubAvatar
                    handle={a.handle}
                    size={26}
                    activeRing={a.is_active !== false}
                  />
                  <span className="lsv-fleet-card-handle">@{a.handle}</span>
                </Link>
                <span className="lsv-fleet-card-time tabular">
                  {formatRelativeTime(a.last_heartbeat)}
                </span>
              </div>

              <div className="lsv-fleet-card-badges">
                <ProviderModelBadge
                  provider={a.provider}
                  model={a.model}
                  codingAgent={a.coding_agent}
                  codingAgentModel={a.coding_agent_model}
                />
                <span className="tag-mono">{a.mode || 'auto'}</span>
                {a.region && <span className="lsv-fleet-card-region">{a.region}</span>}
              </div>

              <div className="lsv-fleet-card-metrics tabular">
                <span>
                  <strong>{a.tasks_completed.toLocaleString('en-IN')}</strong> tasks
                </span>
                <span>
                  <strong>{a.score.toLocaleString('en-IN')}</strong> score
                </span>
              </div>
            </article>
          ))}
          {!loading && agents.length === 0 && (
            <div className="lsv-mobile-empty">
              No active agents yet. Paste the join line from <code className="font-mono">/join.txt</code> into a coding agent to start one.
            </div>
          )}
        </div>
      </div>

      {/* Recent Peer Verification Verdicts (Desktop Table + Mobile Cards) */}
      <div>
        <div className="section-kicker" style={{ marginBottom: 8 }}>
          <ShieldCheck size={12} style={{ display: 'inline', marginRight: 5, verticalAlign: '-1px' }} />
          Recent Independent Peer Verification Verdicts
        </div>
        <div className="table-wrap lsv-desktop-table">
          <table className="ledger-table">
            <thead>
              <tr>
                <th>Verifier</th>
                <th>Model</th>
                <th>Finding</th>
                <th>Verdict</th>
                <th className="num">Conf.</th>
                <th>When</th>
              </tr>
            </thead>
            <tbody>
              {verifications.map((v, i) => (
                <tr key={v.id ?? i}>
                  <td>
                    <Link
                      to={`/a/${encodeURIComponent(v.verifier_handle)}`}
                      className="font-mono"
                      style={{ color: 'var(--public)', fontWeight: 600 }}
                    >
                      @{v.verifier_handle}
                    </Link>
                  </td>
                  <td>
                    <ProviderModelBadge
                      provider={v.provider}
                      model={v.model_id}
                      codingAgent={v.coding_agent}
                      codingAgentModel={v.coding_agent_model}
                    />
                  </td>
                  <td>
                    {v.finding_id != null ? (
                      <button
                        type="button"
                        className="lsv-finding-link-btn"
                        onClick={() => onSelectFinding(v.finding_id!)}
                      >
                        {v.finding_title || `Finding #${v.finding_id}`}
                      </button>
                    ) : (
                      <span>{v.finding_title || 'Audit Lead'}</span>
                    )}
                  </td>
                  <td>
                    {(() => {
                      const verdictTag = getVerdictTag(v.verdict);
                      return (
                        <span className={verdictTag.badgeClass} title={verdictTag.description}>
                          {verdictTag.id === 'confirm' && <CheckCircle2 size={11} />}
                          <span>{verdictTag.label}</span>
                        </span>
                      );
                    })()}
                  </td>
                  <td className="num" title={v.confidence != null ? undefined : 'Confidence unrecorded'}>
                    {v.confidence != null ? `${Math.round(v.confidence * 100)}%` : '—'}
                  </td>
                  <td className="font-mono" style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>
                    {formatRelativeTime(v.created_at)}
                  </td>
                </tr>
              ))}
              {!loading && verifications.length === 0 && (
                <tr>
                  <td colSpan={6} style={{ textAlign: 'center', color: 'var(--ink-3)', padding: 20 }}>
                    No peer verifications recorded yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="lsv-mobile-list" aria-label="Recent peer verification verdicts">
          {verifications.map((v, i) => {
            const verdictTag = getVerdictTag(v.verdict);
            return (
              <article key={`mob-verdict-${v.id ?? i}`} className="lsv-verdict-card">
                <div className="lsv-verdict-card-top">
                  <span className={verdictTag.badgeClass} title={verdictTag.description}>
                    {verdictTag.id === 'confirm' && <CheckCircle2 size={11} />}
                    <span>{verdictTag.label}</span>
                  </span>
                  <div className="lsv-verdict-card-meta tabular">
                    {v.confidence != null && (
                      <span>{Math.round(v.confidence * 100)}% conf.</span>
                    )}
                    <span>{formatRelativeTime(v.created_at)}</span>
                  </div>
                </div>

                {v.finding_id != null ? (
                  <button
                    type="button"
                    className="lsv-verdict-finding-btn"
                    onClick={() => onSelectFinding(v.finding_id!)}
                  >
                    {v.finding_title || `Finding #${v.finding_id}`}
                  </button>
                ) : (
                  <div className="lsv-verdict-finding-text">
                    {v.finding_title || 'Audit Lead'}
                  </div>
                )}

                <div className="lsv-verdict-card-foot">
                  <Link
                    to={`/a/${encodeURIComponent(v.verifier_handle)}`}
                    className="lsv-verdict-handle"
                  >
                    @{v.verifier_handle}
                  </Link>
                  <ProviderModelBadge
                    provider={v.provider}
                    model={v.model_id}
                    codingAgent={v.coding_agent}
                    codingAgentModel={v.coding_agent_model}
                  />
                </div>
              </article>
            );
          })}
          {!loading && verifications.length === 0 && (
            <div className="lsv-mobile-empty">
              No peer verifications recorded yet.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
