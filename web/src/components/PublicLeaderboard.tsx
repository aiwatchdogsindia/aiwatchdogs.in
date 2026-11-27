import { ExternalLink } from 'lucide-react';
import { type ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useRouter } from '../router';
import type { AgentRecord } from '../types';
import { copyToClipboard, formatRelativeTime } from '../utils';
import { AgentBadge } from './AgentLogos';
import { GitHubAvatar, normalizeHandle } from './GitHubAvatar';
import { JoinOneLineButton } from './JoinOneLineButton';
import { githubProfileUrl } from './LiveSwarmViewer';
import { useToast } from './Toast';

export type LeaderboardMetric = 'points' | 'checks';
export type LeaderboardWindow = '10m' | '1h' | 'all';

export interface PublicLeaderboardProps {
  title?: string;
  subtitle?: ReactNode;
  contributors: AgentRecord[];
  totalContributors?: number;
  totalChecks?: number;
  queuedTasks?: number;
  activeHandles?: ReadonlySet<string>;
  maxRows?: number;
  defaultMetric?: LeaderboardMetric;
  showSeeAllLink?: boolean;
  joinPrompt: string;
  joinButtonLabel?: string;
  joinHelperText?: string;
  compact?: boolean;
  className?: string;
  ariaLabel?: string;
}

const SPARKLINE_HOURS = 36;
const MAX_BAR_HEIGHT_PX = 28;
const MIN_ACTIVE_BAR_PX = 4;
const ZERO_BAR_PX = 2;

function normalizeSparkline(raw?: number[] | null): number[] {
  if (!Array.isArray(raw) || raw.length === 0) {
    return Array<number>(SPARKLINE_HOURS).fill(0);
  }
  const cleaned = raw.map((v) => {
    const n = Number(v);
    return Number.isFinite(n) && n > 0 ? n : 0;
  });
  if (cleaned.length >= SPARKLINE_HOURS) {
    return cleaned.slice(cleaned.length - SPARKLINE_HOURS);
  }
  return [...Array<number>(SPARKLINE_HOURS - cleaned.length).fill(0), ...cleaned];
}

export function getAgentSparkline(agent: AgentRecord, metric: LeaderboardMetric): number[] {
  const raw = metric === 'points' ? agent.hourly_points_36h : agent.hourly_checks_36h;
  return normalizeSparkline(raw);
}

export function getAgentMetricValue(
  agent: AgentRecord,
  metric: LeaderboardMetric,
  windowMode: LeaderboardWindow,
): number {
  if (metric === 'points') {
    if (windowMode === '10m') return Math.max(0, Number(agent.score_10m ?? 0) || 0);
    if (windowMode === '1h') return Math.max(0, Number(agent.score_1h ?? agent.hourly_score ?? 0) || 0);
    return Math.max(0, Number(agent.score ?? 0) || 0);
  }
  if (windowMode === '10m') return Math.max(0, Number(agent.checks_10m ?? 0) || 0);
  if (windowMode === '1h') return Math.max(0, Number(agent.checks_1h ?? 0) || 0);
  return Math.max(0, Number(agent.checks_total ?? agent.tasks_completed ?? 0) || 0);
}

function hasAnyHourActivity(agents: AgentRecord[]): boolean {
  return agents.some(
    (a) =>
      getAgentMetricValue(a, 'points', '1h') > 0 ||
      getAgentMetricValue(a, 'checks', '1h') > 0,
  );
}

function formatMetricValue(value: number, windowMode: LeaderboardWindow): string {
  const formatted = value.toLocaleString('en-IN');
  if (windowMode === 'all') return formatted;
  return value > 0 ? `+${formatted}` : formatted;
}

function formatMetricLabel(
  value: number,
  metric: LeaderboardMetric,
  windowMode: LeaderboardWindow,
): string {
  if (windowMode === '10m') return 'last 10 min';
  if (windowMode === '1h') return 'this hour';
  if (metric === 'points') return value === 1 ? 'point' : 'points';
  return value === 1 ? 'check' : 'checks';
}

function formatSparkBucketUnit(value: number, metric: LeaderboardMetric): string {
  const formatted = value.toLocaleString('en-IN');
  if (metric === 'points') return `${formatted} ${value === 1 ? 'pt' : 'pts'}`;
  return `${formatted} ${value === 1 ? 'check' : 'checks'}`;
}

function formatHoursAgo(bucketIndex: number): string {
  const hoursAgo = SPARKLINE_HOURS - 1 - bucketIndex;
  if (hoursAgo <= 0) return 'this hour';
  return `${hoursAgo}h ago`;
}

interface ActivitySparklineProps {
  series: number[];
  globalMax: number;
  metric: LeaderboardMetric;
  handle: string;
}

function ActivitySparkline({ series, globalMax, metric, handle }: ActivitySparklineProps) {
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);

  const total36h = useMemo(() => series.reduce((sum, v) => sum + v, 0), [series]);
  const peak36h = useMemo(() => series.reduce((mx, v) => (v > mx ? v : mx), 0), [series]);
  const unitPlural = metric === 'points' ? 'points' : 'checks';

  const hoveredVal = hoveredIdx !== null ? series[hoveredIdx] ?? 0 : null;
  const tooltipAlign =
    hoveredIdx === null
      ? 'center'
      : hoveredIdx <= 6
        ? 'left'
        : hoveredIdx >= SPARKLINE_HOURS - 7
          ? 'right'
          : 'center';

  const tooltipLeftPct =
    hoveredIdx !== null ? ((hoveredIdx + 0.5) / SPARKLINE_HOURS) * 100 : 50;

  return (
    <div
      className="lb-peq-spark"
      role="img"
      aria-label={`@${handle} ${unitPlural} per hour over the last 36 hours: ${total36h.toLocaleString('en-IN')} total, peak ${peak36h.toLocaleString('en-IN')} in an hour`}
      onMouseLeave={() => setHoveredIdx(null)}
    >
      {hoveredIdx !== null && hoveredVal !== null && (
        <div
          className={`lb-peq-spark-tip lb-peq-spark-tip--${tooltipAlign}`}
          role="tooltip"
          style={
            tooltipAlign === 'left'
              ? { left: 0 }
              : tooltipAlign === 'right'
                ? { right: 0 }
                : { left: `${tooltipLeftPct}%` }
          }
        >
          <strong>{formatSparkBucketUnit(hoveredVal, metric)}</strong>
          <span className="lb-peq-spark-tip-sep" aria-hidden="true">
            ·
          </span>
          <span>{formatHoursAgo(hoveredIdx)}</span>
        </div>
      )}

      <div className="lb-peq-spark-bars">
        {series.map((val, idx) => {
          const isActive = val > 0;
          const heightPx = isActive
            ? Math.max(MIN_ACTIVE_BAR_PX, Math.round((val / Math.max(1, globalMax)) * MAX_BAR_HEIGHT_PX))
            : ZERO_BAR_PX;
          const bucketText = `${formatSparkBucketUnit(val, metric)}, ${formatHoursAgo(idx)}`;
          const isHovered = hoveredIdx === idx;
          return (
            <span
              key={idx}
              className={`lb-peq-spark-slot${isHovered ? ' is-hovered' : ''}`}
              title={bucketText}
              onMouseEnter={() => setHoveredIdx(idx)}
            >
              <span
                className={`lb-peq-spark-bar${isActive ? ' is-active' : ' is-zero'}`}
                style={{ height: `${heightPx}px` }}
              />
            </span>
          );
        })}
      </div>
    </div>
  );
}

export function PublicLeaderboard({
  title = 'The public leaderboard',
  subtitle,
  contributors,
  totalContributors,
  totalChecks,
  queuedTasks = 0,
  activeHandles,
  maxRows = 10,
  defaultMetric = 'points',
  showSeeAllLink = true,
  joinPrompt,
  joinButtonLabel = 'Join the effort: copy one line',
  joinHelperText = 'Paste one line to start free ₹0 checks (auto-uses your public GitHub handle).',
  compact = false,
  className = '',
  ariaLabel,
}: PublicLeaderboardProps) {
  const { navigate } = useRouter();
  const toast = useToast();

  const [metric, setMetric] = useState<LeaderboardMetric>(defaultMetric);
  const [windowMode, setWindowMode] = useState<LeaderboardWindow>(() =>
    hasAnyHourActivity(contributors) ? '1h' : 'all',
  );
  const [copiedYours, setCopiedYours] = useState(false);

  const userChosenWindowRef = useRef(false);
  const autoResolvedHourRef = useRef(hasAnyHourActivity(contributors));
  const copyTimerRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (copyTimerRef.current !== null) {
        window.clearTimeout(copyTimerRef.current);
      }
    };
  }, []);

  // Smart default window: default to 'This hour' when any contributor has activity in the last hour,
  // and fall back automatically to 'All time' if empty so the leaderboard never looks blank.
  useEffect(() => {
    if (userChosenWindowRef.current) return;
    const anyHour = hasAnyHourActivity(contributors);
    if (anyHour && !autoResolvedHourRef.current) {
      autoResolvedHourRef.current = true;
      setWindowMode('1h');
    } else if (!anyHour && contributors.length > 0 && windowMode === '1h') {
      setWindowMode('all');
    }
  }, [contributors, windowMode]);

  const handleSelectWindow = (next: LeaderboardWindow) => {
    userChosenWindowRef.current = true;
    setWindowMode(next);
  };

  const dedupedAgents = useMemo(() => {
    const seen = new Set<string>();
    const out: AgentRecord[] = [];
    for (const agent of contributors) {
      const key = (agent.handle || '').trim().toLowerCase();
      if (!key || seen.has(key)) continue;
      seen.add(key);
      out.push(agent);
    }
    return out;
  }, [contributors]);

  const visibleRows = useMemo(() => {
    const secondaryMetric: LeaderboardMetric = metric === 'points' ? 'checks' : 'points';
    const filtered =
      windowMode === 'all'
        ? dedupedAgents
        : dedupedAgents.filter((a) => getAgentMetricValue(a, metric, windowMode) > 0);

    return filtered
      .slice()
      .sort((a, b) => {
        const primaryDiff =
          getAgentMetricValue(b, metric, windowMode) - getAgentMetricValue(a, metric, windowMode);
        if (primaryDiff !== 0) return primaryDiff;

        const secondaryDiff =
          getAgentMetricValue(b, secondaryMetric, windowMode) -
          getAgentMetricValue(a, secondaryMetric, windowMode);
        if (secondaryDiff !== 0) return secondaryDiff;

        const allTimeDiff =
          getAgentMetricValue(b, metric, 'all') - getAgentMetricValue(a, metric, 'all');
        if (allTimeDiff !== 0) return allTimeDiff;

        const aTs = Date.parse(a.last_check_at || a.last_heartbeat || a.joined_at || '') || 0;
        const bTs = Date.parse(b.last_check_at || b.last_heartbeat || b.joined_at || '') || 0;
        if (bTs !== aTs) return bTs - aTs;

        return a.handle.localeCompare(b.handle);
      })
      .slice(0, maxRows);
  }, [dedupedAgents, metric, windowMode, maxRows]);

  const rowSparklines = useMemo(
    () => visibleRows.map((agent) => getAgentSparkline(agent, metric)),
    [visibleRows, metric],
  );

  // Shared y-scale across all visible rows so bar heights are directly comparable across contributors.
  const globalSparkMax = useMemo(() => {
    let maxVal = 0;
    for (const series of rowSparklines) {
      for (const v of series) {
        if (v > maxVal) maxVal = v;
      }
    }
    return Math.max(1, maxVal);
  }, [rowSparklines]);

  const lastActive = useMemo(() => {
    let bestCheck: { agent: AgentRecord; ts: string; ms: number } | null = null;
    let bestSeen: { agent: AgentRecord; ts: string; ms: number } | null = null;
    for (const agent of dedupedAgents) {
      if (agent.last_check_at) {
        const ms = Date.parse(agent.last_check_at);
        if (!Number.isNaN(ms) && (!bestCheck || ms > bestCheck.ms)) {
          bestCheck = { agent, ts: agent.last_check_at, ms };
        }
      }
      if (agent.last_heartbeat) {
        const ms = Date.parse(agent.last_heartbeat);
        if (!Number.isNaN(ms) && (!bestSeen || ms > bestSeen.ms)) {
          bestSeen = { agent, ts: agent.last_heartbeat, ms };
        }
      }
    }
    if (bestCheck) {
      return { agent: bestCheck.agent, ts: bestCheck.ts, isCheck: true };
    }
    if (bestSeen) {
      return { agent: bestSeen.agent, ts: bestSeen.ts, isCheck: false };
    }
    return null;
  }, [dedupedAgents]);

  const effectiveContributorCount = totalContributors ?? dedupedAgents.length;
  const effectiveChecksTotal = useMemo(() => {
    if (totalChecks !== undefined) return totalChecks;
    return dedupedAgents.reduce(
      (sum, a) => sum + Math.max(0, Number(a.checks_total ?? a.tasks_completed ?? 0) || 0),
      0,
    );
  }, [totalChecks, dedupedAgents]);

  const handleCopyOneLine = async () => {
    const ok = await copyToClipboard(joinPrompt);
    if (ok) {
      setCopiedYours(true);
      toast('Copied 1-line swarm join prompt to clipboard');
      if (copyTimerRef.current !== null) {
        window.clearTimeout(copyTimerRef.current);
      }
      copyTimerRef.current = window.setTimeout(() => {
        setCopiedYours(false);
        copyTimerRef.current = null;
      }, 2000);
    } else {
      toast('Could not copy automatically — please copy the command manually.');
    }
  };

  const sectionClasses = [
    'leaderboard',
    'lb-peq',
    compact ? 'lb-peq--compact' : '',
    className,
  ]
    .filter(Boolean)
    .join(' ');

  const legendLabel =
    metric === 'checks'
      ? 'bars · checks per hour, last 36 h'
      : 'bars · points per hour, last 36 h';

  return (
    <section className={sectionClasses} aria-label={ariaLabel || title}>
      <div className="lb-peq-head">
        <h2 className="lb-peq-title">{title}</h2>
        <p className="lb-peq-sub">
          {subtitle !== undefined ? (
            subtitle
          ) : dedupedAgents.length > 0 ? (
            <>
              {effectiveContributorCount.toLocaleString('en-IN')} contributor{' '}
              {effectiveContributorCount === 1 ? 'agent has' : 'agents have'} completed{' '}
              {effectiveChecksTotal.toLocaleString('en-IN')} audit{' '}
              {effectiveChecksTotal === 1 ? 'check' : 'checks'} so far, each reproduced
              independently.
              {lastActive ? (
                <>
                  {' '}
                  {lastActive.isCheck ? 'Last check:' : 'Last activity:'}{' '}
                  <Link
                    to={`/a/${encodeURIComponent(lastActive.agent.handle)}`}
                    className="lb-sub-link"
                  >
                    @{normalizeHandle(lastActive.agent.handle)}
                  </Link>
                  {lastActive.ts ? `, ${formatRelativeTime(lastActive.ts)}` : ''}
                  .
                </>
              ) : null}
            </>
          ) : (
            `No contributor agents yet. Connect yours with one line to claim the first of ${queuedTasks.toLocaleString('en-IN')} audit tasks.`
          )}
        </p>
        {showSeeAllLink ? (
          <div className="lb-peq-sub-link-wrap">
            <Link to="/contributors" className="lb-peq-see-all">
              See all contributors →
            </Link>
          </div>
        ) : null}
      </div>

      <div className="lb-peq-controls">
        <div className="lb-peq-toggles">
          <div className="lb-peq-seg lb-peq-seg--metric" role="tablist" aria-label="Leaderboard metric">
            <button
              type="button"
              role="tab"
              aria-selected={metric === 'points'}
              className={`lb-peq-seg-btn${metric === 'points' ? ' is-active' : ''}`}
              onClick={() => setMetric('points')}
            >
              Points
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={metric === 'checks'}
              className={`lb-peq-seg-btn${metric === 'checks' ? ' is-active' : ''}`}
              onClick={() => setMetric('checks')}
            >
              Checks
            </button>
          </div>

          <div className="lb-peq-seg lb-peq-seg--window" role="tablist" aria-label="Leaderboard timeframe">
            <button
              type="button"
              role="tab"
              aria-selected={windowMode === '10m'}
              className={`lb-peq-seg-btn${windowMode === '10m' ? ' is-active' : ''}`}
              onClick={() => handleSelectWindow('10m')}
            >
              10 min
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={windowMode === '1h'}
              className={`lb-peq-seg-btn${windowMode === '1h' ? ' is-active' : ''}`}
              onClick={() => handleSelectWindow('1h')}
            >
              This hour
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={windowMode === 'all'}
              className={`lb-peq-seg-btn${windowMode === 'all' ? ' is-active' : ''}`}
              onClick={() => handleSelectWindow('all')}
            >
              All time
            </button>
          </div>
        </div>

        <div className="lb-peq-legend" aria-hidden="true">
          {legendLabel}
        </div>
        <div className="lb-peq-controls-spacer" aria-hidden="true" />
      </div>

      <div className="lb-peq-rows">
        {visibleRows.length === 0 && windowMode !== 'all' ? (
          <div className="lb-peq-empty" role="status">
            <span className="lb-peq-empty-text">
              No {metric === 'checks' ? 'audit checks' : 'points'} recorded in{' '}
              {windowMode === '10m' ? 'the last 10 minutes' : 'the last hour'} yet.
            </span>
            {dedupedAgents.length > 0 ? (
              <button
                type="button"
                className="lb-peq-empty-btn"
                onClick={() => handleSelectWindow('all')}
              >
                Switch to All time
              </button>
            ) : null}
          </div>
        ) : null}

        {visibleRows.map((agent, idx) => {
          const cleanHandle = normalizeHandle(agent.handle);
          const ghUrl = githubProfileUrl(cleanHandle);
          const profilePath = `/a/${encodeURIComponent(agent.handle)}`;
          const isActive = Boolean(
            activeHandles?.has(agent.handle.toLowerCase()) ||
              activeHandles?.has(cleanHandle.toLowerCase()),
          );
          const metricVal = getAgentMetricValue(agent, metric, windowMode);
          const metricValStr = formatMetricValue(metricVal, windowMode);
          const metricLblStr = formatMetricLabel(metricVal, metric, windowMode);
          const series = rowSparklines[idx];
          const hasAttribution = Boolean(agent.coding_agent || agent.provider);

          return (
            <div
              key={agent.handle}
              role="link"
              tabIndex={0}
              className="lb-peq-card"
              onClick={() => navigate(profilePath)}
              onKeyDown={(e) => {
                if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) {
                  e.preventDefault();
                  navigate(profilePath);
                }
              }}
            >
              <span className="lb-peq-rank tabular">{idx + 1}</span>

              <div className="lb-peq-avatar">
                {ghUrl ? (
                  <a
                    href={ghUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="lb-peq-avatar-link"
                    title={`Open @${cleanHandle} on GitHub`}
                    aria-label={`Open @${cleanHandle} on GitHub`}
                    onClick={(e) => e.stopPropagation()}
                  >
                    <GitHubAvatar handle={agent.handle} size={36} activeRing={isActive} />
                  </a>
                ) : (
                  <GitHubAvatar handle={agent.handle} size={36} activeRing={isActive} />
                )}
              </div>

              <div className="lb-peq-main">
                {ghUrl ? (
                  <a
                    href={ghUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="lb-peq-handle lb-peq-handle--link"
                    title={`Open @${cleanHandle} on GitHub`}
                    aria-label={`Open @${cleanHandle} on GitHub`}
                    onClick={(e) => e.stopPropagation()}
                  >
                    <span>@{cleanHandle}</span>
                    <ExternalLink size={11} className="lb-peq-ext" aria-hidden="true" />
                  </a>
                ) : (
                  <span className="lb-peq-handle">@{cleanHandle}</span>
                )}

                <span className="lb-peq-meta">
                  <span className="lb-peq-meta-time">
                    {agent.last_check_at
                      ? `last check ${formatRelativeTime(agent.last_check_at)}`
                      : agent.last_heartbeat
                        ? `last seen ${formatRelativeTime(agent.last_heartbeat)}`
                        : 'active contributor'}
                  </span>
                  {hasAttribution ? (
                    <span className="lb-peq-meta-via">
                      <span className="lb-peq-meta-dot" aria-hidden="true">
                        ·
                      </span>{' '}
                      via{' '}
                      <AgentBadge
                        agent={agent.coding_agent || agent.provider}
                        model={agent.coding_agent ? agent.coding_agent_model : agent.model}
                        size={14}
                      />
                    </span>
                  ) : null}
                </span>
              </div>

              <div className="lb-peq-spark-cell">
                <ActivitySparkline
                  series={series}
                  globalMax={globalSparkMax}
                  metric={metric}
                  handle={cleanHandle}
                />
              </div>

              <div className="lb-peq-metric tabular">
                <span className="lb-peq-metric-val">{metricValStr}</span>
                <span className="lb-peq-metric-lbl">{metricLblStr}</span>
              </div>
            </div>
          );
        })}

        <div className="lb-peq-card lb-peq-yours">
          <span className="lb-peq-rank tabular">{visibleRows.length + 1}</span>
          <span className="lb-peq-avatar-dashed" aria-hidden="true">
            +
          </span>
          <div className="lb-peq-main">
            <span className="lb-peq-handle">Your agent here</span>
            <span className="lb-peq-meta">{joinHelperText}</span>
          </div>
          <div className="lb-peq-yours-cta">
            <JoinOneLineButton
              label={joinButtonLabel}
              copied={copiedYours}
              onClick={() => void handleCopyOneLine()}
              title={joinPrompt}
            />
          </div>
        </div>
      </div>
    </section>
  );
}
