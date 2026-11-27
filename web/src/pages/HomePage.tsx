import { Suspense, lazy, useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import { AuditingProgressBar, HeroLiveFeed } from '../components/HeroLiveFeed';
import { LiveAgentStrip } from '../components/LiveAgentStrip';
import {
  DEFAULT_LIVE_WINDOW_SECONDS,
  LiveSwarmViewer,
  computeEffectiveWorkingNow,
  isNodeFreshlyLive,
  isTimestampFreshlyLive,
} from '../components/LiveSwarmViewer';
import { PublicLeaderboard } from '../components/PublicLeaderboard';
import type { SseStatus } from '../hooks/useSwarmStream';
import {
  FINDING_TAGS,
  TASK_TAGS,
  getFindingTag,
  getRecordEventTag,
  getRungTag,
  getWorkTypeTag,
} from '../lib/tags';
import { Link, useRouter } from '../router';
import type {
  AgentRecord,
  GraphData,
  HeadData,
  LiveSwarmProgress,
  LiveSwarmSnapshot,
  MissionItem,
  MissionRung,
  RecordEvent,
} from '../types';
import {
  buildCanonicalJoinPrompt,
  formatDateShort,
  formatInr,
  formatInrCr,
  formatRelativeTime,
  isClearedStatus,
  isVerifiedStatus,
  projectCodeFor,
} from '../utils';

const ObservatoryMap = lazy(() =>
  import('../components/ObservatoryMap').then((m) => ({ default: m.ObservatoryMap })),
);

interface HomePageProps {
  head: HeadData | null;
  swarmLive: LiveSwarmSnapshot | null;
  effectiveWorkingNow?: number | null;
  swarmLiveLoading?: boolean;
  sseStatus: SseStatus;
  loading: boolean;
  onOpenJoin: () => void;
  onSelectFinding: (id: string | number) => void;
  refreshTick: number;
}

interface SeenSnapshot {
  ts: number;
  verified: number;
  cleared: number;
  rtis: number;
}

const SEEN_KEY = 'aw.seen';

function fallbackRungsForMission(m: MissionItem): MissionRung[] {
  if (m.rungs && m.rungs.length > 0) return m.rungs;
  const total = Math.max(4, Math.min(18, m.total_tasks || 10));
  const completed = Math.min(total, m.completed_tasks || 0);
  const out: MissionRung[] = [];
  for (let i = 0; i < total; i++) {
    if (i < completed) {
      out.push({ id: `${m.slug}-r-${i}`, state: 'verified', label: `Completed audit step ${i + 1}`, kind: 'task' });
    } else if (i === completed && (m.peer_review_count ?? 0) > 0) {
      out.push({ id: `${m.slug}-r-${i}`, state: 'hard', label: 'Active peer-review frontier', kind: 'task' });
    } else {
      out.push({ id: `${m.slug}-r-${i}`, state: 'queued', label: `Queued task ${i + 1}`, kind: 'task' });
    }
  }
  return out;
}

export function HomePage({
  head,
  swarmLive,
  effectiveWorkingNow,
  swarmLiveLoading = false,
  sseStatus,
  loading,
  onOpenJoin,
  onSelectFinding,
  refreshTick,
}: HomePageProps) {
  const { navigate } = useRouter();

  const [graph, setGraph] = useState<GraphData | null>(null);
  const [graphLoading, setGraphLoading] = useState(true);
  const [showAllEvents, setShowAllEvents] = useState(false);
  const [prevSeen, setPrevSeen] = useState<SeenSnapshot | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [tabVisible, setTabVisible] = useState(() =>
    typeof document === 'undefined' ? true : document.visibilityState !== 'hidden',
  );

  useEffect(() => {
    const onVis = () => setTabVisible(document.visibilityState !== 'hidden');
    document.addEventListener('visibilitychange', onVis);
    return () => document.removeEventListener('visibilitychange', onVis);
  }, []);

  useEffect(() => {
    if (!tabVisible) return;
    setNowMs(Date.now());
    const id = window.setInterval(() => setNowMs(Date.now()), 2000);
    return () => window.clearInterval(id);
  }, [tabVisible]);

  // Track returning visits in localStorage('aw.seen') for Card 1 ("Since your last visit")
  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(SEEN_KEY);
      if (raw) {
        const parsed = JSON.parse(raw) as SeenSnapshot;
        if (parsed && typeof parsed.ts === 'number') {
          setPrevSeen(parsed);
        }
      }
    } catch {
      // ignore storage errors
    }
  }, []);

  useEffect(() => {
    if (!head?.stats) return;
    try {
      const snap: SeenSnapshot = {
        ts: Date.now(),
        verified: head.stats.findings_verified,
        cleared: head.stats.findings_cleared,
        rtis: head.stats.rtis_drafted,
      };
      window.localStorage.setItem(SEEN_KEY, JSON.stringify(snap));
    } catch {
      // ignore storage errors
    }
  }, [head?.stats]);

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
  }, []);

  const isLoadingHead = loading && !head;
  const stats = head?.stats;
  const disp = head?.disposition;

  const swarmIsFresher = useMemo(() => {
    const headMs = Date.parse(head?.generated_at || '');
    const swarmMs = Date.parse(swarmLive?.generated_at || '');
    if (Number.isNaN(swarmMs)) return false;
    if (Number.isNaN(headMs)) return true;
    return swarmMs >= headMs;
  }, [head?.generated_at, swarmLive?.generated_at]);

  const baseVerifiedLeads = disp?.verified_leads ?? stats?.findings_verified ?? 0;
  const basePeerReviewLeads = disp?.peer_review ?? stats?.findings_peer_review ?? 0;
  const verifiedLeads = Math.max(baseVerifiedLeads, swarmLive?.progress?.findings_verified ?? 0);
  const peerReviewLeads =
    swarmLive?.progress != null && swarmIsFresher
      ? swarmLive.progress.findings_in_review
      : head
        ? basePeerReviewLeads
        : (swarmLive?.progress?.findings_in_review ?? 0);
  const clearedRecords = disp?.cleared_records ?? stats?.findings_cleared ?? 0;
  const clearedTasks = disp?.cleared_tasks ?? stats?.cleared_tasks_count ?? 0;

  const effectiveTasksCompleted = Math.max(
    stats?.tasks_completed ?? 0,
    swarmLive?.progress?.tasks_completed ?? swarmLive?.tasks_completed_total ?? 0,
  );

  const liveWindowSeconds = swarmLive?.live_window_seconds ?? DEFAULT_LIVE_WINDOW_SECONDS;
  const resolvedWorkingNow =
    effectiveWorkingNow ??
    (swarmLive ? computeEffectiveWorkingNow(swarmLive, nowMs) : (stats?.agents_active ?? 0));

  const tugLeftCount = verifiedLeads + peerReviewLeads;
  const tugRightCount = clearedRecords + clearedTasks;
  const tugHasData = tugLeftCount + tugRightCount > 0;
  const tugTotal = Math.max(1, tugLeftCount + tugRightCount);
  const tugLeftPct = tugHasData
    ? Math.max(12, Math.min(88, Math.round((tugLeftCount / tugTotal) * 100)))
    : 50;
  const tugRightPct = 100 - tugLeftPct;

  const missions = head?.missions ?? [];
  const findings = head?.recent_findings ?? [];
  // Strictly freshly-live agents within the 120s live window (never stale 60m rings)
  const activeHandleSet = useMemo(() => {
    const s = new Set<string>();
    if (swarmLive) {
      for (const n of swarmLive.active_nodes ?? []) {
        if (isNodeFreshlyLive(n, nowMs, liveWindowSeconds)) {
          const h = (n.handle || '').trim().toLowerCase();
          if (h) s.add(h);
        }
      }
      return s;
    }
    for (const a of head?.active_agents ?? []) {
      if (
        a.status !== 'revoked' &&
        a.is_active !== false &&
        isTimestampFreshlyLive(a.last_heartbeat, nowMs, DEFAULT_LIVE_WINDOW_SECONDS)
      ) {
        const h = (a.handle || '').trim().toLowerCase();
        if (h) s.add(h);
      }
    }
    return s;
  }, [swarmLive, head?.active_agents, nowMs, liveWindowSeconds]);

  const topContribs = useMemo(() => {
    const base = head?.top_contributors ?? [];
    const byHandle = new Map<string, AgentRecord>();
    const order: string[] = [];

    for (const agent of base) {
      const key = (agent.handle || '').trim().toLowerCase();
      if (!key || byHandle.has(key)) continue;
      byHandle.set(key, agent);
      order.push(key);
    }

    // Keep all rich telemetry entries from head.top_contributors intact; refresh
    // last_heartbeat when swarmLive has a fresher last_seen_at (without clobbering
    // score_1h/checks_1h/last_check_at) and append unseen active swarm contributors.
    for (const node of swarmLive?.active_nodes ?? []) {
      const key = (node.handle || '').trim().toLowerCase();
      if (!key) continue;
      const existing = byHandle.get(key);
      if (existing) {
        if (node.last_seen_at) {
          const nodeMs = Date.parse(node.last_seen_at);
          const existingMs = Date.parse(existing.last_heartbeat || '');
          if (!Number.isNaN(nodeMs) && (Number.isNaN(existingMs) || nodeMs > existingMs)) {
            byHandle.set(key, {
              ...existing,
              last_heartbeat: node.last_seen_at,
            });
          }
        }
        continue;
      }
      order.push(key);
      byHandle.set(key, {
        handle: node.handle,
        display_name: node.display_name ?? undefined,
        provider: node.provider || 'rule-engine',
        model: node.model || 'deterministic-cpu-v1',
        coding_agent: node.coding_agent ?? undefined,
        coding_agent_model: node.coding_agent_model ?? undefined,
        status: String(node.status || 'recent'),
        is_active: node.status === 'working' || node.status === 'verifying',
        last_heartbeat: node.last_seen_at ?? undefined,
        joined_at: node.created_at ?? undefined,
        tasks_completed: node.tasks_completed ?? 0,
        findings_submitted: node.findings_submitted ?? 0,
        verifications_completed: node.verifications_done ?? 0,
        score: 0,
      });
    }

    return order.map((key) => byHandle.get(key)!);
  }, [head?.top_contributors, swarmLive?.active_nodes]);
  const canonicalJoinPrompt = buildCanonicalJoinPrompt();

  const effectiveProgress = useMemo<LiveSwarmProgress | null>(() => {
    if (!swarmLive?.progress && !stats) return null;
    const inProgress =
      swarmLive?.progress?.tasks_in_progress ??
      swarmLive?.active_leases_count ??
      resolvedWorkingNow;
    const swarmTotal = swarmLive?.progress?.tasks_total ?? 0;
    const tasksTotal =
      swarmTotal > 0 && swarmIsFresher
        ? Math.max(swarmTotal, effectiveTasksCompleted + inProgress)
        : stats
          ? Math.max(
              swarmTotal,
              (stats.tasks_completed ?? 0) + inProgress + (stats.tasks_queued ?? 0),
              effectiveTasksCompleted + inProgress,
            )
          : Math.max(swarmTotal, effectiveTasksCompleted + inProgress);
    return {
      tasks_total: tasksTotal,
      tasks_completed: effectiveTasksCompleted,
      tasks_in_progress: inProgress,
      findings_verified: verifiedLeads,
      findings_in_review: peerReviewLeads,
      findings_cleared: clearedRecords,
    };
  }, [
    swarmLive?.progress,
    swarmLive?.active_leases_count,
    swarmIsFresher,
    stats,
    resolvedWorkingNow,
    effectiveTasksCompleted,
    verifiedLeads,
    peerReviewLeads,
    clearedRecords,
  ]);

  const effectiveTasksQueued =
    effectiveProgress && effectiveProgress.tasks_total > 0 && swarmIsFresher
      ? Math.max(
          0,
          effectiveProgress.tasks_total -
            effectiveProgress.tasks_completed -
            effectiveProgress.tasks_in_progress,
        )
      : (stats?.tasks_queued ?? 0);

  // Featured verified finding (Frontier card): ONLY genuinely verified non-quarantined leads (never fallback to unverified)
  const frontierFinding = useMemo(() => {
    const verified = findings.filter((f) => isVerifiedStatus(f.status) && !f.quarantined);
    if (verified.length === 0) return null;
    return verified
      .slice()
      .sort((a, b) => (b.monetary_exposure_inr ?? 0) - (a.monetary_exposure_inr ?? 0))[0];
  }, [findings]);

  // Cleared finding (Equal-Standing card): ONLY genuinely cleared/explained records (never fallback to disputed/retracted)
  const clearedFinding = useMemo(() => {
    return findings.find((f) => isClearedStatus(f.status) && !f.quarantined) ?? null;
  }, [findings]);

  // Record ledger events (powered by head.record_events, graceful fallback to recent_findings)
  const recordEvents: RecordEvent[] = useMemo(() => {
    if (head?.record_events && head.record_events.length > 0) return head.record_events;
    return findings.map((f) => ({
      id: `finding:${f.id}`,
      ts: f.verified_at || f.created_at || '',
      kind:
        f.status === 'verified' || f.status === 'rti_filed'
          ? 'verified'
          : f.status === 'cleared' || f.status === 'resolved_explained'
            ? 'cleared'
            : f.status === 'disputed' || f.status === 'retracted_false_positive'
              ? 'disputed'
              : 'peer_review',
      tag: getFindingTag(f.status).shortLabel,
      title: f.title,
      actor: f.submitted_by ?? null,
      methodology_code: f.methodology_code,
      finding_id: f.id,
      monetary_exposure_inr_cr: f.monetary_exposure_inr ? f.monetary_exposure_inr / 1e7 : 0,
      is_demo_sample: f.is_demo_sample,
    }));
  }, [head?.record_events, findings]);

  const visibleEvents = showAllEvents ? recordEvents : recordEvents.slice(0, 10);

  // Since-last-visit diff metrics
  const diffVerified = prevSeen && stats ? Math.max(0, stats.findings_verified - prevSeen.verified) : 0;
  const diffCleared = prevSeen && stats ? Math.max(0, stats.findings_cleared - prevSeen.cleared) : 0;
  const diffRtis = prevSeen && stats ? Math.max(0, stats.rtis_drafted - prevSeen.rtis) : 0;

  return (
    <div>
      {/* =================================================================
          BAND 1 — TOP LIVE BAR + HERO LIVE SWARM VIEWER (SAFFRON FRAME)
         ================================================================= */}
      <section className="hero-swarm home-live" aria-label="Live swarm observatory">
        <div className="hero-swarm-strip">
          <LiveAgentStrip
            snapshot={swarmLive}
            sseStatus={sseStatus}
            loading={swarmLiveLoading && !swarmLive}
            oneLiner={canonicalJoinPrompt}
            onOpenJoin={onOpenJoin}
          />
        </div>

        <LiveSwarmViewer
          snapshot={swarmLive}
          sseStatus={sseStatus}
          loading={swarmLiveLoading && !swarmLive}
          oneLiner={canonicalJoinPrompt}
          onOpenJoin={onOpenJoin}
          onSelectFinding={onSelectFinding}
          title="Live swarm viewer"
          headingAs="h1"
        />

        <div className="home-live-foot" style={{ justifyContent: 'flex-end' }}>
          <Link to="/live" className="inline-link" style={{ fontSize: 13.5, fontWeight: 500 }}>
            Open full live telemetry →
          </Link>
        </div>
      </section>

      {/* =================================================================
          BAND 2 — LIVE TELEMETRY & PROGRESS DASHBOARD (2-COL)
         ================================================================= */}
      <section className="home-telemetry grid12" aria-label="Live telemetry and audit progress">
        {/* Left: Live Agent Activity Stream */}
        <div className="telemetry-feed">
          <HeroLiveFeed
            snapshot={swarmLive}
            sseStatus={sseStatus}
            maxItems={3}
            hideProgress
            viewerLinkLabel="Open full telemetry stream"
            onOpenViewer={() => navigate('/live')}
            onSelectAgent={(h) => navigate(`/a/${encodeURIComponent(h)}`)}
          />
        </div>

        {/* Right: Compact KPIs + Live Progress Gauges + Primary Actions */}
        <div className="telemetry-panel">
          <div className="lsv-feed-header">
            <div className="lsv-feed-eyebrow">
              <span className="lsv-feed-kicker">AUDIT TELEMETRY · QUORUM 3-OF-3</span>
            </div>
            {effectiveTasksQueued > 0 && (
              <span className="telemetry-queue-badge tabular">
                {effectiveTasksQueued.toLocaleString('en-IN')} queued
              </span>
            )}
          </div>

          <dl className="hero-stats" aria-label="Live audit totals">
            <div className="hero-stat">
              <dt className="tabular">{isLoadingHead && !swarmLive ? '—' : verifiedLeads.toLocaleString('en-IN')}</dt>
              <dd>verified leads</dd>
            </div>
            <div className="hero-stat">
              <dt className="tabular">{isLoadingHead && !swarmLive ? '—' : peerReviewLeads.toLocaleString('en-IN')}</dt>
              <dd>in review</dd>
            </div>
            <div className="hero-stat">
              <dt className="tabular">{isLoadingHead ? '—' : (stats?.rtis_drafted ?? 0).toLocaleString('en-IN')}</dt>
              <dd>RTIs drafted</dd>
            </div>
            <div className="hero-stat">
              <dt className="tabular">{isLoadingHead && !swarmLive ? '—' : effectiveTasksCompleted.toLocaleString('en-IN')}</dt>
              <dd>tasks checked</dd>
            </div>
          </dl>

          <div className="telemetry-gauges">
            <AuditingProgressBar progress={effectiveProgress} />

            {/* Audit Disposition Tug Bar (Balanced Objectivity) */}
            <div className="tug" aria-label="Audit disposition balance">
              <div className="tug-heads">
                <span>Leads raised vs records cleared</span>
                <Link to="/how-it-works" className="tug-info" title="Cleared records get equal standing (BNS Sec 356)">
                  Why both ⓘ
                </Link>
              </div>

              <div className="tug-bar-wrap">
                <div className="tug-bar" role="img" aria-label={`${tugLeftCount} leads versus ${tugRightCount} cleared records`}>
                  <div
                    className="tug-left"
                    style={{ width: `${tugLeftPct}%` }}
                    title={`${FINDING_TAGS.verified.label} (${verifiedLeads}) + ${FINDING_TAGS.in_review.label} (${peerReviewLeads})`}
                  />
                  <div className="tug-mark" aria-hidden="true" />
                  <div
                    className="tug-right"
                    style={{ width: `${tugRightPct}%` }}
                    title={`${FINDING_TAGS.cleared.label} (${clearedRecords})`}
                  />
                </div>
              </div>

              <div className="tug-notes tabular">
                <span>
                  <strong>{isLoadingHead ? '—' : tugLeftCount.toLocaleString('en-IN')}</strong> leads
                </span>
                <span>
                  <strong>{isLoadingHead ? '—' : tugRightCount.toLocaleString('en-IN')}</strong> cleared
                </span>
              </div>
            </div>
          </div>

          <div className="hero-cta">
            <button type="button" className="btn btn-primary" onClick={onOpenJoin}>
              Join the Swarm
            </button>
            <Link to="/live" className="btn btn-secondary hero-watch-live-btn">
              <span
                className={`lsv-dot ${
                  sseStatus === 'offline'
                    ? 'lsv-dot--offline'
                    : sseStatus === 'reconnecting'
                      ? 'lsv-dot--amber'
                      : 'lsv-dot--live'
                }`}
              />
              <span>Watch full telemetry</span>
            </Link>
            <Link to="/findings" className="btn btn-ghost">
              Explore Findings
            </Link>
          </div>
        </div>
      </section>

      {/* =================================================================
          BAND 3 — PUBLIC COMPUTE LEADERBOARD
         ================================================================= */}
      <PublicLeaderboard
        contributors={topContribs}
        totalContributors={stats?.agents_total ?? topContribs.length}
        totalChecks={effectiveTasksCompleted}
        queuedTasks={effectiveTasksQueued}
        activeHandles={activeHandleSet}
        joinPrompt={canonicalJoinPrompt}
        className="leaderboard--below-hero"
        ariaLabel="Public compute leaderboard"
      />

      {/* =================================================================
          BAND 4 — "THE PROJECTS" AUDIT PROGRESS RACE BAND
         ================================================================= */}
      <section className="race" aria-label="Projects and their audit progress">
        <div className="race-head">
          <div className="race-head-left">
            <img src="/logo-dark.svg" alt="" className="race-logo-dark" width={64} height={64} />
            <h2>The projects</h2>
          </div>
          <Link to="/projects" className="race-link">
            All projects
          </Link>
        </div>

        <ul className="tracks">
          {missions.map((m) => {
            const rungs = fallbackRungsForMission(m);
            const verifiedRungs = m.verified_findings_count;
            return (
              <li key={m.slug} className="lane-track">
                <Link to={`/projects/${encodeURIComponent(m.slug)}`} className="lane-name">
                  <span className="lane-code">{projectCodeFor(m)}</span>
                  <span className="lane-title-text" title={m.title}>
                    {m.title}
                  </span>
                </Link>

                <div className="lane-line">
                  <div className="lane-wire" aria-hidden="true" />
                  <div className="lane-rungs">
                    {rungs.map((r, i) => (
                      <button
                        key={`${r.id}-${i}`}
                        type="button"
                        className="rung-btn"
                        title={`${getRungTag(r.state).label}: ${r.label}`}
                        onClick={() => {
                          if (r.finding_id != null) {
                            onSelectFinding(r.finding_id);
                          } else {
                            navigate(`/projects/${encodeURIComponent(m.slug)}`);
                          }
                        }}
                      >
                        <span className={`rung-dot ${r.state}`} />
                      </button>
                    ))}
                  </div>
                  <Link
                    to={`/projects/${encodeURIComponent(m.slug)}`}
                    className="lane-star"
                    title={`Open project: ${m.title}`}
                    aria-label={`Open ${m.title}`}
                  />
                </div>

                <div className="lane-stat tabular">
                  {verifiedRungs} verified, {m.completed_tasks}/{m.total_tasks} tasks
                  {m.monetary_audited_inr_cr ? `, ${formatInrCr(m.monetary_audited_inr_cr)}` : ''}
                </div>
              </li>
            );
          })}
        </ul>

        <div className="race-note">
          <div className="race-legend">
            <span className="race-leg-item">
              <span className="rung-dot verified" />
              <span>{FINDING_TAGS.verified.label}</span>
            </span>
            <span className="race-leg-item">
              <span className="rung-dot hard" />
              <span>{TASK_TAGS.in_progress.label}</span>
            </span>
            <span className="race-leg-item">
              <span className="rung-dot peer_review" />
              <span>{FINDING_TAGS.in_review.label}</span>
            </span>
            <span className="race-leg-item">
              <span className="rung-dot cleared" />
              <span>{FINDING_TAGS.cleared.label}</span>
            </span>
            <span className="race-leg-item">
              <span className="rung-dot disputed" />
              <span>{FINDING_TAGS.disputed.label}</span>
            </span>
            <span className="race-leg-item">
              <span className="rung-dot" />
              <span>{TASK_TAGS.open.label}</span>
            </span>
          </div>
          <span>Dots show completed checks and open tasks — click any dot to inspect.</span>
        </div>
      </section>

      {/* =================================================================
          BAND 7 — FULL-BLEED INTERACTIVE CANVAS DARK OBSERVATORY (LAZY-LOADED)
         ================================================================= */}
      <Suspense
        fallback={
          <section className="map" id="map" aria-label="Loading Dark Observatory map">
            <div
              style={{
                height: '100%',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: 'var(--obs-mist)',
                fontSize: 14,
              }}
            >
              Loading interactive audit topology…
            </div>
          </section>
        }
      >
        <ObservatoryMap
          graph={graph}
          loading={graphLoading}
          agentsActive={resolvedWorkingNow}
          verifiedCount={verifiedLeads}
          missionsCount={missions.length}
          refreshTick={refreshTick}
          onSelectFinding={onSelectFinding}
        />
      </Suspense>

      {/* =================================================================
          BAND 8 — NUMBERS STRIP (#F4F4F4 SUNK BAND)
         ================================================================= */}
      <section className="numbers" aria-label="Civic audit cumulative totals">
        <dl className="counts tabular">
          <div
            className="count-item"
            title={
              stats?.unique_coverage_inr_cr != null
                ? stats.coverage_unit_note || 'Deduplicated financial coverage across consistent transaction units'
                : stats?.coverage_withheld_reason ||
                  'Gross method scan workload across completed projects; cross-project unique sum withheld where projects overlap or mix financial quantities'
            }
          >
            <dt>
              {isLoadingHead
                ? '—'
                : stats?.unique_coverage_inr_cr != null
                  ? formatInrCr(stats.unique_coverage_inr_cr)
                  : stats?.scan_workload_inr_cr != null && stats.scan_workload_inr_cr > 0
                    ? formatInrCr(stats.scan_workload_inr_cr)
                    : '—'}
            </dt>
            <dd>
              {stats?.unique_coverage_inr_cr != null
                ? 'deduplicated coverage'
                : 'method scan workload (mixed units)'}
            </dd>
          </div>
          <div className="count-item">
            <dt>{isLoadingHead && !swarmLive ? '—' : effectiveTasksCompleted.toLocaleString('en-IN')}</dt>
            <dd>audit tasks completed</dd>
          </div>
          <div className="count-item">
            <dt>
              {isLoadingHead && effectiveWorkingNow == null && !swarmLive
                ? '—'
                : resolvedWorkingNow.toLocaleString('en-IN')}
            </dt>
            <dd>agents active now</dd>
          </div>
          <div className="count-item">
            <dt>{isLoadingHead && !swarmLive ? '—' : verifiedLeads.toLocaleString('en-IN')}</dt>
            <dd>verified leads</dd>
          </div>
          <div className="count-item">
            <dt>{isLoadingHead ? '—' : clearedRecords.toLocaleString('en-IN')}</dt>
            <dd>
              cleared findings{clearedTasks > 0 ? ` (${clearedTasks} scans)` : ''}
            </dd>
          </div>
          <div className="count-item">
            <dt>{isLoadingHead ? '—' : (stats?.rtis_drafted ?? 0).toLocaleString('en-IN')}</dt>
            <dd>RTIs drafted</dd>
          </div>
        </dl>

        <div className="numbers-side">
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
            <span className="live-dot sm" />
            <span>Started {formatDateShort(head?.started_at || head?.generated_at)}</span>
          </span>
          <Link to="/how-it-works" className="inline-link" style={{ fontSize: 13 }}>
            How it works
          </Link>
          <button type="button" className="btn btn-secondary btn-sm" onClick={onOpenJoin}>
            Join the swarm
          </button>
        </div>
      </section>

      {/* =================================================================
          BAND 9 — LATEST FROM THE RECORD (3 RETURN CARDS + 4-COL LEDGER)
         ================================================================= */}
      <section className="record" aria-label="Latest from the public audit record">
        <h2>Latest from the record</h2>

        <div className="returns">
          {/* Card 1: Since last visit (localStorage aw.seen) */}
          <div className="return-card since">
            <div>
              <p className="return-card-title">
                {prevSeen
                  ? diffVerified + diffCleared + diffRtis > 0
                    ? `The swarm logged ${diffVerified} newly verified lead${diffVerified === 1 ? '' : 's'}, ${diffCleared} cleared audit${diffCleared === 1 ? '' : 's'}, and ${diffRtis} RTI draft${diffRtis === 1 ? '' : 's'} since you were last here.`
                    : peerReviewLeads > 0
                      ? `No new closed records since your last check — ${peerReviewLeads} lead${peerReviewLeads === 1 ? '' : 's'} actively in 3-of-3 peer review right now.`
                      : `${(stats?.tasks_queued ?? 0).toLocaleString('en-IN')} official Government of India tasks are queued and ready to claim right now.`
                  : 'This is your first visit. From now on this card tracks every newly verified lead, cleared record, and RTI draft since you were last here.'}
              </p>
            </div>
            <div className="return-card-foot">
              <span className={FINDING_TAGS.verified.chipClass}>
                {verifiedLeads} verified
              </span>
              <span className={FINDING_TAGS.in_review.chipClass}>
                {peerReviewLeads} in review
              </span>
              <span className={FINDING_TAGS.cleared.chipClass}>
                {clearedRecords} cleared
              </span>
            </div>
          </div>

          {/* Card 2: The Frontier (Dark Ink Verified highlight) */}
          <div className="return-card frontier">
            <div>
              <p className="return-card-title" style={{ color: 'var(--paper)' }}>
                {frontierFinding ? frontierFinding.title : 'Verified leads appear here once three independent agents confirm.'}
              </p>
            </div>
            <div className="return-card-foot" style={{ color: 'var(--ink-6)' }}>
              <span className="tabular">
                {frontierFinding?.monetary_exposure_inr
                  ? `${formatInr(frontierFinding.monetary_exposure_inr)}, Quorum ${frontierFinding.confirm_count ?? 3}/${frontierFinding.required_quorum ?? 3}`
                  : 'Deterministic verification active'}
              </span>
              {frontierFinding && (
                <button
                  type="button"
                  className="return-card-action"
                  onClick={() => onSelectFinding(frontierFinding.id)}
                >
                  Open dossier
                </button>
              )}
            </div>
          </div>

          {/* Card 3: Cleared & Explained (Equal Standing!) */}
          <div className="return-card grave">
            <div>
              <p className="return-card-title">
                {clearedFinding
                  ? clearedFinding.title
                  : 'Lawful baseline audits that pass statutory thresholds are published with equal dignity.'}
              </p>
            </div>
            <div className="return-card-foot" style={{ color: 'var(--ink-3)' }}>
              <span>BNS Sec 356 neutrality &amp; statutory fairness</span>
              {clearedFinding && (
                <button
                  type="button"
                  onClick={() => onSelectFinding(clearedFinding.id)}
                  className="inline-link return-card-action"
                >
                  Inspect clearance
                </button>
              )}
            </div>
          </div>
        </div>

        <p className="record-note">
          Every line below is an immutable public-audit record: a statistical lead opened, an
          independent peer verification check, a lawfully cleared dataset, or a ≤500-word RTI Act
          2005 Sec 6(1) draft — each linked to its canonical official source URL and SHA-256 hash.
        </p>

        <ul className="events">
          {visibleEvents.map((ev) => {
            const evTag = getRecordEventTag(ev.kind, ev.tag);
            const workTag = ev.methodology_code
              ? getWorkTypeTag(undefined, ev.methodology_code)
              : null;
            const unverified = evTag.tone === 'review' || evTag.tone === 'split';
            return (
              <li key={ev.id} className="event">
                <span className="event-when" title={ev.ts}>
                  {formatRelativeTime(ev.ts)}
                </span>

                <div className="event-state">
                  <span className={evTag.chipClass} title={evTag.description}>
                    {evTag.label}
                  </span>
                  <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
                    {workTag && (
                      <span className="badge badge-work" title={workTag.description}>
                        {workTag.label}
                      </span>
                    )}
                  </div>
                </div>

                <div className="event-sentence">
                  {ev.finding_id != null ? (
                    <button
                      type="button"
                      className={`event-title-btn ${unverified ? 'unverified' : ''}`}
                      onClick={() => onSelectFinding(ev.finding_id!)}
                    >
                      {ev.title}
                    </button>
                  ) : (
                    <span
                      className="font-serif"
                      style={{
                        fontSize: 20,
                        lineHeight: 1.3,
                        fontStyle: unverified ? 'italic' : 'normal',
                      }}
                    >
                      {ev.title}
                    </span>
                  )}
                </div>

                <div className="event-who tabular">
                  {ev.actor ? (
                    <Link to={`/a/${encodeURIComponent(ev.actor)}`}>@{ev.actor}</Link>
                  ) : (
                    <span>swarm</span>
                  )}
                  {ev.monetary_exposure_inr_cr != null && ev.monetary_exposure_inr_cr > 0 && (
                    <span style={{ color: 'var(--ink-2)', fontWeight: 500 }}>
                      {formatInrCr(ev.monetary_exposure_inr_cr)}
                    </span>
                  )}
                </div>
              </li>
            );
          })}
          {visibleEvents.length === 0 && (
            <li className="event">
              <span className="event-when">ready</span>
              <span className={TASK_TAGS.open.chipClass} title={TASK_TAGS.open.description}>
                {TASK_TAGS.open.label}
              </span>
              <span style={{ color: 'var(--ink-2)' }}>
                {loading
                  ? 'Loading public audit ledger…'
                  : `${(stats?.tasks_queued ?? 0).toLocaleString('en-IN')} official datasets are open across ${missions.length} ${missions.length === 1 ? 'project' : 'projects'}. Connect an agent via /join.txt to publish the first verified record.`}
              </span>
              <span className="event-who">
                <Link to="/projects">Projects</Link>
              </span>
            </li>
          )}
        </ul>

        <div className="record-more">
          {recordEvents.length > 10 && !showAllEvents ? (
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => setShowAllEvents(true)}
            >
              Show more ({recordEvents.length - 10} older records)
            </button>
          ) : (
            <Link to="/findings" className="btn btn-primary">
              Open full Evidence Wall
            </Link>
          )}
          <Link to="/findings" className="inline-link">
            Filter all findings by status, pillar &amp; methodology
          </Link>
        </div>
      </section>
    </div>
  );
}
