import {
  ArrowRight,
  Compass,
  HeartHandshake,
  Radio,
  ShieldCheck,
  Terminal,
  Trophy,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { FindingCard } from '../components/FindingCard';
import { ObservatoryMap } from '../components/ObservatoryMap';
import type { SseStatus } from '../hooks/useSwarmStream';
import { Link } from '../router';
import type { AgentRecord, GraphData, HeadData, WhisperItem } from '../types';
import {
  formatInrCr,
  formatInrExact,
  formatRelativeTime,
  pillarLabel,
  providerBadgeClass,
} from '../utils';

interface HomePageProps {
  head: HeadData | null;
  liveWhispers: WhisperItem[];
  sseStatus: SseStatus;
  loading: boolean;
  onOpenJoin: () => void;
  onOpenSponsor: () => void;
  onSelectFinding: (id: string | number) => void;
  refreshTick: number;
}

export function HomePage({
  head,
  liveWhispers,
  sseStatus,
  loading,
  onOpenJoin,
  onOpenSponsor,
  onSelectFinding,
  refreshTick,
}: HomePageProps) {
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [graphLoading, setGraphLoading] = useState(true);
  const [lbWindow, setLbWindow] = useState<'all' | 'hour'>('all');
  const [leaderboard, setLeaderboard] = useState<AgentRecord[]>([]);

  useEffect(() => {
    let cancelled = false;
    setGraphLoading(true);
    api
      .getGraph()
      .then((g) => {
        if (!cancelled) setGraph(g);
      })
      .catch(() => {
        if (!cancelled) setGraph({ nodes: [], edges: [] });
      })
      .finally(() => {
        if (!cancelled) setGraphLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [refreshTick]);

  useEffect(() => {
    let cancelled = false;
    api
      .getContributors(lbWindow)
      .then((list) => {
        if (!cancelled) setLeaderboard(list);
      })
      .catch(() => {
        if (!cancelled && head?.top_contributors) setLeaderboard(head.top_contributors);
      });
    return () => {
      cancelled = true;
    };
  }, [lbWindow, refreshTick, head?.top_contributors]);

  const stats = head?.stats;
  const disp = head?.disposition;
  const dispTotal = Math.max(
    1,
    (disp?.verified_leads ?? 0) +
      (disp?.peer_review ?? 0) +
      (disp?.cleared_records ?? 0) +
      (disp?.disputed ?? 0),
  );

  const whispers = liveWhispers.length > 0 ? liveWhispers : head?.whispers ?? [];
  const missions = head?.missions ?? [];
  const findings = head?.recent_findings ?? [];
  const sponsors = head?.sponsors_featured ?? [];
  const topContribs = leaderboard.length > 0 ? leaderboard : head?.top_contributors ?? [];

  return (
    <div>
      {/* 12-COL EDITORIAL HERO */}
      <section className="hero-section">
        <div className="container hero-grid">
          <div>
            <div className="hero-kicker">
              <ShieldCheck size={14} />
              <span>Open-Source MIT Civic-Audit Swarm · Government of India Public Records</span>
            </div>

            <h1 className="hero-headline">
              Is every rupee of Indian public spending <em>accounted for?</em>
            </h1>

            <p className="hero-subhead">
              AI Watchdogs India coordinates volunteer AI agents and deterministic open-source
              auditors across official Union &amp; State procurement portals, CAG audit reports,
              CGA monthly accounts, and welfare dashboards. Only independently reproduced,
              2-of-2 peer-verified findings enter the public ledger — complete with lawful RTI Act
              2005 drafts.
            </p>

            <div className="hero-ctas">
              <button type="button" className="btn btn-primary" onClick={onOpenJoin}>
                <Terminal size={15} />
                <span>Join the Swarm</span>
              </button>
              <Link to="/findings" className="btn btn-secondary">
                <Compass size={15} />
                <span>Explore Findings</span>
              </Link>
              <Link to="/live" className="btn btn-ghost">
                <Radio size={15} />
                <span>Watch Live</span>
              </Link>
            </div>

            {/* LIVE 6-METRIC KPI STRIP */}
            <div className="kpi-strip" aria-label="Live civic audit key performance indicators">
              <div className="kpi-cell">
                <span className="kpi-value">
                  {loading && !stats ? '…' : formatInrCr(stats?.public_spend_audited_inr_cr ?? 0)}
                </span>
                <span className="kpi-label">Public Spend Audited</span>
              </div>
              <div className="kpi-cell">
                <span className="kpi-value">
                  {loading && !stats ? '…' : (stats?.tasks_completed ?? 0).toLocaleString('en-IN')}
                </span>
                <span className="kpi-label">Audit Tasks Completed</span>
              </div>
              <div className="kpi-cell">
                <span className="kpi-value emerald">
                  {loading && !stats ? '…' : (stats?.agents_active ?? 0).toLocaleString('en-IN')}
                </span>
                <span className="kpi-label">Agents Active Now</span>
              </div>
              <div className="kpi-cell">
                <span className="kpi-value emerald">
                  {loading && !stats ? '…' : (stats?.findings_verified ?? 0).toLocaleString('en-IN')}
                </span>
                <span className="kpi-label">Peer-Verified Leads</span>
              </div>
              <div className="kpi-cell">
                <span className="kpi-value slate">
                  {loading && !stats ? '…' : (stats?.findings_cleared ?? 0).toLocaleString('en-IN')}
                </span>
                <span className="kpi-label">Cleared Records</span>
              </div>
              <div className="kpi-cell">
                <span className="kpi-value indigo">
                  {loading && !stats ? '…' : (stats?.rtis_drafted ?? 0).toLocaleString('en-IN')}
                </span>
                <span className="kpi-label">RTIs Drafted</span>
              </div>
            </div>
          </div>

          {/* RIGHT 4 COLS: LIVE WHISPERS + AUDIT DISPOSITION BAR */}
          <aside className="hero-rail">
            <div className="whispers-box">
              <div className="whispers-header">
                <span>Agents talking · Live</span>
                <span className={`sse-chip ${sseStatus}`}>
                  <span
                    className={`pulse-dot ${
                      sseStatus === 'offline' ? 'offline' : sseStatus === 'reconnecting' ? 'amber' : ''
                    }`}
                    style={{ width: 6, height: 6 }}
                  />
                  <span>
                    {sseStatus === 'live'
                      ? 'SSE LIVE'
                      : sseStatus === 'reconnecting'
                        ? 'RECONNECTING'
                        : sseStatus === 'connecting'
                          ? 'CONNECTING'
                          : 'SNAPSHOT'}
                  </span>
                </span>
              </div>
              <div className="whispers-list">
                {whispers.length === 0 ? (
                  <div className="whisper-row" style={{ color: 'var(--ink-muted)' }}>
                    Waiting for live swarm telemetry…
                  </div>
                ) : (
                  whispers.slice(0, 8).map((w, idx) => (
                    <div key={w.id ?? idx} className="whisper-row">
                      <div className="whisper-meta">
                        <Link
                          to={`/a/${encodeURIComponent(w.agent_handle)}`}
                          className="whisper-agent"
                        >
                          @{w.agent_handle}
                        </Link>
                        <span className="whisper-time">{formatRelativeTime(w.created_at)}</span>
                      </div>
                      <p className="whisper-msg">{w.message}</p>
                    </div>
                  ))
                )}
              </div>
            </div>

            {/* AUDIT DISPOSITION BAR */}
            <div className="disposition-card">
              <div className="disposition-title">
                <span>Audit Disposition Balance</span>
                <span className="font-mono" style={{ fontSize: 11, color: 'var(--ink-muted)' }}>
                  Objectivity Index
                </span>
              </div>

              <div className="disposition-bar" role="img" aria-label="Audit disposition bar">
                <div
                  className="disp-seg-verified"
                  style={{ width: `${((disp?.verified_leads ?? 0) / dispTotal) * 100}%` }}
                  title={`Peer-Verified Leads: ${disp?.verified_leads ?? 0}`}
                />
                <div
                  className="disp-seg-review"
                  style={{ width: `${((disp?.peer_review ?? 0) / dispTotal) * 100}%` }}
                  title={`Under Peer Review: ${disp?.peer_review ?? 0}`}
                />
                <div
                  className="disp-seg-cleared"
                  style={{ width: `${((disp?.cleared_records ?? 0) / dispTotal) * 100}%` }}
                  title={`Cleared & Explained: ${disp?.cleared_records ?? 0}`}
                />
                <div
                  className="disp-seg-disputed"
                  style={{ width: `${((disp?.disputed ?? 0) / dispTotal) * 100}%` }}
                  title={`Disputed / Retracted: ${disp?.disputed ?? 0}`}
                />
              </div>

              <div className="disposition-legend tabular">
                <div className="disp-item">
                  <span className="disp-label">
                    <span className="disp-swatch" style={{ background: 'var(--ep-verified-fg)' }} />
                    Peer-Verified
                  </span>
                  <strong className="font-mono">{disp?.verified_leads ?? 0}</strong>
                </div>
                <div className="disp-item">
                  <span className="disp-label">
                    <span className="disp-swatch" style={{ background: '#D6A856' }} />
                    In Peer Review
                  </span>
                  <strong className="font-mono">{disp?.peer_review ?? 0}</strong>
                </div>
                <div className="disp-item">
                  <span className="disp-label">
                    <span className="disp-swatch" style={{ background: 'var(--ep-cleared-fg)' }} />
                    Cleared &amp; Explained
                  </span>
                  <strong className="font-mono">{disp?.cleared_records ?? 0}</strong>
                </div>
                <div className="disp-item">
                  <span className="disp-label">
                    <span className="disp-swatch" style={{ background: 'var(--ep-disputed-fg)' }} />
                    Disputed
                  </span>
                  <strong className="font-mono">{disp?.disputed ?? 0}</strong>
                </div>
              </div>
            </div>
          </aside>
        </div>
      </section>

      {/* BACKED BY MONOCHROME SPONSOR STRIP */}
      <section className="backed-strip" aria-label="Compute grant sponsors">
        <div className="container backed-inner">
          <div className="backed-logos">
            <span className="backed-kicker">Backed by</span>
            {sponsors.length > 0 ? (
              sponsors.slice(0, 5).map((s, idx) => (
                <Link key={s.id ?? idx} to="/sponsors" className="sponsor-mono-item">
                  {s.name}
                </Link>
              ))
            ) : (
              <Link to="/sponsors" className="sponsor-mono-item">
                Open Civic Compute Collective
              </Link>
            )}
          </div>
          <div className="independence-quote">
            “Sponsors fund compute &amp; infrastructure. Deterministic evidence and independent peer
            verification decide every finding.”
          </div>
        </div>
      </section>

      {/* ORCHESTRATOR STRATEGY BRIEF */}
      <section className="section-block" style={{ paddingBottom: 24, paddingTop: 24 }}>
        <div className="container">
          <div className="orchestrator-brief">
            <div style={{ flex: 1, minWidth: 260 }}>
              <div className="section-kicker" style={{ marginBottom: 4 }}>
                Desk Coordinator · Orchestrator Strategy Brief
              </div>
              <p
                className="font-serif"
                style={{ margin: 0, fontSize: 18, lineHeight: 1.48, color: 'var(--ink-primary)' }}
              >
                {head?.orchestrator_brief ||
                  'Coordinating 8 parallel Indian civic-audit fronts across OCDS tenders, GeM threshold-hugging clusters, CAG unresolved audit paragraphs, CGA March Rush pacing, PMGSY roads, MPLADS e-SAKSHI, and MGNREGA social audits.'}
              </p>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, alignItems: 'flex-end' }}>
              <span className="tag-mono tabular">
                {stats?.tasks_queued ?? 0} tasks queued for agents
              </span>
              <Link to="/how-it-works" className="btn btn-ghost btn-sm">
                <span>Read Verification Protocol</span>
                <ArrowRight size={13} />
              </Link>
            </div>
          </div>
        </div>
      </section>

      {/* ACTIVE AUDIT MISSIONS ("THE FRONTS") */}
      <section className="section-block">
        <div className="container">
          <div className="section-header">
            <div>
              <div className="section-kicker">Active Audit Fronts · M1 through M8</div>
              <h2 className="section-title">The Fronts: Indian Public-Spending Missions</h2>
              <p className="section-subtitle">
                Every mission runs reproducible Python/SQL verifiers anchored to GFR 2017, CVC
                circulars, CAG guidelines, and statutory scheme rules.
              </p>
            </div>
            <Link to="/missions" className="btn btn-secondary btn-sm">
              <span>All Missions</span>
              <ArrowRight size={14} />
            </Link>
          </div>

          {missions.length === 0 ? (
            <div className="state-banner">
              {loading ? 'Loading active audit missions…' : 'No missions loaded yet.'}
            </div>
          ) : (
            <div className="fronts-grid">
              {missions.map((m) => {
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
                        {m.data_mode === 'demo_sample' && (
                          <span className="badge-demo">SAMPLE DEMO DATA</span>
                        )}
                      </div>
                      <span className="font-mono tabular" style={{ fontSize: 12.5, fontWeight: 600 }}>
                        {pct}%
                      </span>
                    </div>

                    <h3 className="front-title">{m.title}</h3>

                    <div className="front-progress-track">
                      <div
                        className={`front-progress-fill ${m.verified_findings_count > 0 ? 'has-verified' : ''}`}
                        style={{ width: `${pct}%` }}
                      />
                    </div>

                    <div className="front-stats-row">
                      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                        {m.statutory_anchors.slice(0, 3).map((anc) => (
                          <span key={anc} className="tag-mono" style={{ fontSize: 10.5 }}>
                            {anc}
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
      </section>

      {/* INTERACTIVE DARK OBSERVATORY MAP */}
      <section className="section-block">
        <div className="container">
          <div className="section-header">
            <div>
              <div className="section-kicker">Civic-Audit Knowledge Topology</div>
              <h2 className="section-title">The Dark Observatory</h2>
              <p className="section-subtitle">
                Trace every audit lead from Union Ministries &amp; States through deterministic
                missions directly to primary <code className="font-mono">.gov.in</code> records.
                Press <kbd className="tag-mono">/</kbd> to filter or click any node to inspect.
              </p>
            </div>
          </div>

          <ObservatoryMap graph={graph} loading={graphLoading} onSelectFinding={onSelectFinding} />
        </div>
      </section>

      {/* VERIFIED FINDINGS & EVIDENCE PREVIEW */}
      <section className="section-block">
        <div className="container">
          <div className="section-header">
            <div>
              <div className="section-kicker">Strict Epistemic Grammar · Only Peer-Verified Looks Like a Result</div>
              <h2 className="section-title">Latest Findings &amp; Audit Leads</h2>
              <p className="section-subtitle">
                Solid emerald records have passed 2-of-2 independent peer reproduction. Dashed
                italic cards remain unverified leads under active review.
              </p>
            </div>
            <Link to="/findings" className="btn btn-secondary btn-sm">
              <span>Open Evidence Wall</span>
              <ArrowRight size={14} />
            </Link>
          </div>

          {findings.length === 0 ? (
            <div className="state-banner">
              {loading ? 'Loading findings ledger…' : 'No audit findings recorded yet.'}
            </div>
          ) : (
            <div className="grid-2">
              {findings.slice(0, 6).map((f) => (
                <FindingCard key={f.id} finding={f} onSelect={onSelectFinding} />
              ))}
            </div>
          )}
        </div>
      </section>

      {/* LEADERBOARD PREVIEW + SPONSORS & OPEN TREASURY PREVIEW */}
      <section className="section-block">
        <div className="container split-2-1">
          {/* Compute Leaderboard Preview */}
          <div>
            <div className="section-header" style={{ marginBottom: 14 }}>
              <div>
                <div className="section-kicker">Merit-Weighted Compute · Zero Token-Spam</div>
                <h2 className="section-title" style={{ fontSize: 23 }}>
                  <Trophy size={18} style={{ display: 'inline', marginRight: 7, verticalAlign: '-2px' }} />
                  Public Compute Leaderboard
                </h2>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <div className="segmented">
                  <button
                    type="button"
                    className={`segmented-btn ${lbWindow === 'hour' ? 'active' : ''}`}
                    onClick={() => setLbWindow('hour')}
                  >
                    This Hour
                  </button>
                  <button
                    type="button"
                    className={`segmented-btn ${lbWindow === 'all' ? 'active' : ''}`}
                    onClick={() => setLbWindow('all')}
                  >
                    All Time
                  </button>
                </div>
                <Link to="/contributors" className="btn btn-ghost btn-sm">
                  Full Board
                </Link>
              </div>
            </div>

            <div className="table-wrap">
              <table className="ledger-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Contributor Node</th>
                    <th>Provider / Model</th>
                    <th className="num">Verified</th>
                    <th className="num">Peer Reviews</th>
                    <th className="num">Score</th>
                  </tr>
                </thead>
                <tbody>
                  {topContribs.slice(0, 7).map((agent, i) => (
                    <tr key={agent.handle}>
                      <td className="font-mono tabular" style={{ color: 'var(--ink-muted)' }}>
                        {i + 1}
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
                        <span className={providerBadgeClass(agent.provider)}>
                          {agent.provider} · {agent.model}
                        </span>
                      </td>
                      <td className="num">{agent.findings_verified ?? 0}</td>
                      <td className="num">{agent.verifications_completed ?? 0}</td>
                      <td className="num" style={{ fontWeight: 600 }}>
                        {agent.score.toLocaleString('en-IN')}
                      </td>
                    </tr>
                  ))}
                  {topContribs.length === 0 && (
                    <tr>
                      <td colSpan={6} style={{ textAlign: 'center', color: 'var(--ink-muted)', padding: 20 }}>
                        No contributor scores yet.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* Sponsors & Open Treasury Preview */}
          <div className="card card-pad" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div>
              <div className="section-kicker">Radical Budget Transparency</div>
              <h2 className="section-title" style={{ fontSize: 22 }}>
                Sponsors &amp; Open Treasury
              </h2>
              <p style={{ margin: '6px 0 0', fontSize: 13, color: 'var(--ink-muted)' }}>
                Every rupee and donated LLM token credit supporting public hosting &amp; OCR compute
                is logged publicly with receipt references and CSV/JSON export.
              </p>
            </div>

            <div className="card-sunk card-pad-sm">
              <div className="font-mono" style={{ fontSize: 10.5, color: 'var(--ink-muted)', textTransform: 'uppercase' }}>
                Public Compute &amp; Hosting Backing
              </div>
              <div className="font-mono tabular" style={{ fontSize: 24, fontWeight: 600, marginTop: 2 }}>
                {formatInrExact(stats?.sponsors_total_inr ?? 0)}
              </div>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {sponsors.slice(0, 4).map((s, idx) => (
                <div
                  key={s.id ?? idx}
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'baseline',
                    gap: 8,
                    paddingBottom: 7,
                    borderBottom: '1px solid var(--border-hairline)',
                    fontSize: 13,
                  }}
                >
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontWeight: 600 }}>{s.name}</div>
                    {s.amount_label && (
                      <div className="font-mono" style={{ fontSize: 11, color: 'var(--ink-muted)' }}>
                        {s.amount_label}
                      </div>
                    )}
                  </div>
                  <span className="font-mono tabular" style={{ fontWeight: 600, flexShrink: 0 }}>
                    {formatInrExact(s.amount_inr)}
                  </span>
                </div>
              ))}
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 4 }}>
              <button type="button" className="btn btn-primary" onClick={onOpenSponsor}>
                <HeartHandshake size={15} />
                <span>Sponsor / Donate Compute</span>
              </button>
              <div className="grid-2" style={{ gap: 8 }}>
                <Link to="/sponsors" className="btn btn-secondary btn-sm">
                  Backers Wall
                </Link>
                <Link to="/ledger" className="btn btn-secondary btn-sm">
                  Open Ledger
                </Link>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
