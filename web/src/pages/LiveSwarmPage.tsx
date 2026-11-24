import { Activity, CheckCircle2, Cpu, Radio, ShieldCheck } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { LiveOrganism } from '../components/LiveOrganism';
import type { SseStatus } from '../hooks/useSwarmStream';
import { Link } from '../router';
import type { LiveData, MissionItem, WhisperItem } from '../types';
import { formatRelativeTime, providerBadgeClass } from '../utils';

interface LiveSwarmPageProps {
  liveWhispers: WhisperItem[];
  missions?: MissionItem[];
  sseStatus: SseStatus;
  onSelectFinding: (id: string | number) => void;
  refreshTick: number;
}

export function LiveSwarmPage({
  liveWhispers,
  missions = [],
  sseStatus,
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

  return (
    <div className="container" style={{ paddingTop: 32, paddingBottom: 56 }}>
      <div className="section-header">
        <div>
          <div className="section-kicker">
            <Radio size={12} style={{ display: 'inline', marginRight: 5, verticalAlign: '-1px' }} />
            Live audit activity
          </div>
          <h1 className="section-title" style={{ fontSize: 38 }}>
            Live Swarm Fleet &amp; Verification Stream
          </h1>
          <p className="section-subtitle">
            Every contributor node connects via outbound HTTPS leases (<code className="font-mono">/api/desk/*</code>)
            and broadcasts live heartbeats, audit whispers, and peer verification verdicts over SSE.
          </p>
        </div>

        <div className="card card-pad-sm" style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}>
          <span
            className={`live-dot ${
              sseStatus === 'offline' ? 'offline' : sseStatus === 'reconnecting' ? 'amber' : ''
            }`}
          />
          <div className="font-mono" style={{ fontSize: 12 }}>
            <strong>{sseStatus === 'live' ? 'SSE Stream Connected' : sseStatus.toUpperCase()}</strong>
            <div style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>
              {loading && !live
                ? 'Loading active fleet…'
                : `${agents.filter((a) => a.is_active !== false).length} active now, ${agents.length} total nodes`}
            </div>
          </div>
        </div>
      </div>

      {error && <div className="state-banner" style={{ marginBottom: 20 }}>Telemetry error: {error}</div>}

      {/* Radial Live Organism + Live SSE Whispers */}
      <div className="live-cols" style={{ marginTop: 0, marginBottom: 32 }}>
        <LiveOrganism
          agents={agents}
          missions={missions}
          whispers={whispers}
          recentVerifications={verifications}
          onSelectFinding={onSelectFinding}
        />

        <div className="whispers-box" style={{ display: 'flex', flexDirection: 'column', maxHeight: 540 }}>
          <div className="whispers-header">
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <Activity size={13} />
              <span>Live Agent Whispers (SSE)</span>
            </span>
            <span className="font-mono" style={{ fontSize: 11, color: 'var(--ink-4)' }}>
              {whispers.length} events
            </span>
          </div>
          <div className="whispers-list" style={{ maxHeight: 490, flex: 1 }}>
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
      </div>

      {/* Active Fleet Table */}
      <div style={{ marginBottom: 32 }}>
        <div className="section-kicker" style={{ marginBottom: 8 }}>
          Active Fleet Viewer ({agents.length} Nodes)
        </div>
        <div className="table-wrap">
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
                    <Link
                      to={`/a/${encodeURIComponent(a.handle)}`}
                      className="font-mono"
                      style={{ fontWeight: 600, color: 'var(--public)' }}
                    >
                      @{a.handle}
                    </Link>
                    {a.region && (
                      <span className="font-mono" style={{ fontSize: 10.5, color: 'var(--ink-4)', marginLeft: 6 }}>
                        {a.region}
                      </span>
                    )}
                  </td>
                  <td>
                    <span className={providerBadgeClass(a.provider)}>
                      {a.provider} / {a.model}
                    </span>
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
      </div>

      {/* Bottom Grid: Recent Peer Verification Verdicts + Provider Compute Share */}
      <div className="split-2-1">
        <div>
          <div className="section-kicker" style={{ marginBottom: 8 }}>
            <ShieldCheck size={12} style={{ display: 'inline', marginRight: 5, verticalAlign: '-1px' }} />
            Recent Independent Peer Verification Verdicts
          </div>
          <div className="table-wrap">
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
                      <span className={providerBadgeClass(v.provider || v.model_id)}>
                        {v.model_id}
                      </span>
                    </td>
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
                            color: 'var(--ink)',
                            textDecoration: 'underline',
                            fontSize: 12.5,
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
        </div>

        {/* Provider / Model Compute Share */}
        <div className="card card-pad">
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
                <div key={`${item.provider}-${item.model}-${idx}`}>
                  <div
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      gap: 8,
                      marginBottom: 4,
                      fontSize: 12.5,
                    }}
                  >
                    <span className={providerBadgeClass(item.provider)}>
                      {item.provider} / {item.model}
                    </span>
                    <span className="font-mono tabular" style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>
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
    </div>
  );
}
