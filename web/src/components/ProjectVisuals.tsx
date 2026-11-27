import {
  BarChart3,
  Check,
  Copy,
  ExternalLink,
  Globe,
  Layers,
  RotateCcw,
  Search,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  FINDING_TAGS,
  TASK_TAGS,
  WEBSITE_OUTCOME_TAGS,
  WORK_TYPE_TAGS,
  getFindingTag,
  getTaskTag,
  getWorkTypeTag,
} from '../lib/tags';
import type {
  FindingItem,
  MissionItem,
  MissionTask,
  ProjectVisualSummary,
  VisualWebsiteCheckpoint,
  VisualWebsiteMatrixItem,
  WebsiteVerificationMode,
} from '../types';
import { formatInrCr, safeExternalUrl } from '../utils';

export type { ProjectVisualSummary };

export interface CompactProgressBarProps {
  completed: number;
  claimed?: number;
  queued?: number;
  total: number;
  verified?: number;
  peerReview?: number;
  label?: string;
}

export interface ProjectProgressWidgetProps {
  mission: MissionItem;
  visualSummary?: ProjectVisualSummary;
  tasks: MissionTask[];
  onCopyTaskPrompt?: (task: MissionTask) => void;
}

export interface ProjectResultsVisualizerProps {
  mission: MissionItem;
  visualSummary?: ProjectVisualSummary;
  findings?: FindingItem[];
  onSelectFinding?: (id: string | number) => void;
  /** Sets the task table's search text, e.g. to a financial year such as "2019-20". */
  onFilterTasks?: (text: string) => void;
}

type StatusBucket = 'completed' | 'claimed' | 'queued' | 'waiting';

const STATUS_META: Record<
  StatusBucket,
  { label: string; color: string; badgeClass: string; dashed?: boolean }
> = {
  completed: {
    label: TASK_TAGS.done.label,
    color: 'var(--pv-done)',
    badgeClass: TASK_TAGS.done.badgeClass,
  },
  claimed: {
    label: TASK_TAGS.in_progress.label,
    color: 'var(--pv-claimed)',
    badgeClass: TASK_TAGS.in_progress.badgeClass,
  },
  queued: {
    label: TASK_TAGS.open.label,
    color: 'var(--pv-open)',
    badgeClass: TASK_TAGS.open.badgeClass,
  },
  waiting: {
    label: TASK_TAGS.waiting.label,
    color: 'var(--pv-waiting)',
    badgeClass: TASK_TAGS.waiting.badgeClass,
    dashed: true,
  },
};

const DEFAULT_W1_CHECKPOINTS: Array<{
  key: string;
  label: string;
  hint: string;
}> = [
  { key: 'contact_link', label: 'Contact link', hint: 'GIGW 10 · Navigation link to Contact Us or Directory' },
  { key: 'email', label: 'Email', hint: 'GIGW 10 · Published official email indicator' },
  { key: 'postal_address', label: 'Postal address', hint: 'GIGW 10 · Official street or postal address indicator' },
  { key: 'telephone', label: 'Telephone', hint: 'GIGW 10 · Official telephone or helpline indicator' },
  { key: 'about_link', label: 'About link', hint: 'GIGW 9 · Landing page link explaining the public body' },
  { key: 'updated_date', label: 'Updated date', hint: 'GIGW 5 · Last reviewed or updated date indicator' },
  { key: 'help_link', label: 'Help link', hint: 'GIGW 14 · Landing page help or support link' },
];

const W1_REASON_LABELS: Record<string, string> = {
  robots_restricted: 'Robots.txt restricted',
  fetch_failed: 'Fetch failed / timeout',
  unsupported_page: 'Unsupported page format',
  contact_unavailable: 'Contact page unavailable',
  rti_unavailable: 'RTI page unavailable',
};

function calcPct(part: number, total: number): number {
  if (!Number.isFinite(part) || !Number.isFinite(total) || total <= 0) return 0;
  return Math.max(0, Math.min(100, (part / total) * 100));
}

function fmtPct(pct: number): string {
  if (!Number.isFinite(pct)) return '0.0%';
  return `${pct.toFixed(1)}%`;
}

function buildDashedArcArray(
  arcLen: number,
  circumference: number,
  dash = 6,
  gap = 4,
): string {
  if (arcLen <= 0) return `0 ${circumference.toFixed(2)}`;
  const parts: number[] = [];
  let used = 0;
  while (used < arcLen) {
    const d = Math.min(dash, arcLen - used);
    parts.push(Number(d.toFixed(2)));
    used += d;
    if (used >= arcLen) break;
    const g = Math.min(gap, arcLen - used);
    parts.push(Number(g.toFixed(2)));
    used += g;
  }
  const remaining = Math.max(0, circumference - arcLen);
  if (parts.length % 2 === 0) {
    parts[parts.length - 1] = Number((parts[parts.length - 1] + remaining).toFixed(2));
  } else {
    parts.push(Number(remaining.toFixed(2)));
  }
  return parts.join(' ');
}

function formatSignedPct(val: number): string {
  const sign = val > 0 ? '+' : '';
  return `${sign}${val.toFixed(1)}%`;
}

function formatReasonLabel(key: string): string {
  return W1_REASON_LABELS[key] || key.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase());
}

function classifyTaskStatus(task: MissionTask): StatusBucket {
  if (task.status === 'completed' || task.status === 'settled') return 'completed';
  if (task.status === 'claimed') return 'claimed';
  if (task.status === 'queued') return 'queued';
  return 'waiting';
}

function extractNumericMetric(metrics: Record<string, unknown> | undefined): number | null {
  if (!metrics) return null;
  const candidates = [
    metrics.pct_difference,
    metrics.variance_pct,
    metrics.re_vs_be_pct,
    metrics.actual_vs_be_pct,
    metrics.yoy_pct,
  ];
  for (const val of candidates) {
    if (typeof val === 'number' && Number.isFinite(val)) return val;
    if (typeof val === 'string' && val.trim() !== '') {
      const parsed = Number(val);
      if (Number.isFinite(parsed)) return parsed;
    }
  }
  return null;
}

/* ============================================================================
   1. COMPACT PROGRESS BAR (For /projects index cards)
   ============================================================================ */

export function CompactProgressBar({
  completed,
  claimed = 0,
  queued,
  total,
  verified,
  peerReview,
  label,
}: CompactProgressBarProps) {
  const [hovered, setHovered] = useState<'ring' | 'completed' | 'claimed' | 'queued' | null>(null);

  const safeTotal = Math.max(0, total);
  const safeDone = Math.max(0, Math.min(safeTotal, completed));
  const safeClaimed = Math.max(0, Math.min(safeTotal - safeDone, claimed));
  const safeQueued =
    queued != null
      ? Math.max(0, Math.min(safeTotal - safeDone - safeClaimed, queued))
      : Math.max(0, safeTotal - safeDone - safeClaimed);
  const remainder = Math.max(0, safeTotal - safeDone - safeClaimed - safeQueued);
  const openCount = safeQueued + remainder;

  const donePct = calcPct(safeDone, safeTotal);
  const claimedPct = calcPct(safeClaimed, safeTotal);
  const openPct = calcPct(openCount, safeTotal);

  const ringRadius = 10;
  const ringCircumference = 2 * Math.PI * ringRadius;
  const ringOffset = ringCircumference * (1 - donePct / 100);

  if (safeTotal === 0) {
    return (
      <div className="pv-compact pv-compact--empty">
        <span className="pv-compact-empty-label">{label || 'Waiting for datasets'}</span>
      </div>
    );
  }

  const tooltipContent = (() => {
    if (hovered === 'completed') {
      return `${safeDone.toLocaleString('en-IN')} / ${safeTotal.toLocaleString('en-IN')} · ${fmtPct(donePct)} ${TASK_TAGS.done.label}`;
    }
    if (hovered === 'claimed') {
      return `${safeClaimed.toLocaleString('en-IN')} / ${safeTotal.toLocaleString('en-IN')} · ${fmtPct(claimedPct)} ${TASK_TAGS.in_progress.label}`;
    }
    if (hovered === 'queued') {
      return `${openCount.toLocaleString('en-IN')} / ${safeTotal.toLocaleString('en-IN')} · ${fmtPct(openPct)} ${TASK_TAGS.open.label}`;
    }
    if (hovered === 'ring') {
      const parts = [
        `${safeDone.toLocaleString('en-IN')} / ${safeTotal.toLocaleString('en-IN')} · ${fmtPct(donePct)} ${TASK_TAGS.done.label}`,
      ];
      if (safeClaimed > 0) {
        parts.push(
          `${safeClaimed.toLocaleString('en-IN')} ${TASK_TAGS.in_progress.label} (${fmtPct(claimedPct)})`,
        );
      }
      parts.push(`${openCount.toLocaleString('en-IN')} ${TASK_TAGS.open.label} (${fmtPct(openPct)})`);
      return parts.join(' · ');
    }
    return null;
  })();

  const tooltipAlign =
    hovered === 'ring' || hovered === 'completed'
      ? 'left'
      : hovered === 'queued' && openPct > 45
        ? 'right'
        : 'center';

  return (
    <div className="pv-compact" onMouseLeave={() => setHovered(null)}>
      {tooltipContent ? (
        <div
          className={`pv-tooltip pv-tooltip--compact pv-tooltip--${tooltipAlign}`}
          role="tooltip"
        >
          {tooltipContent}
        </div>
      ) : null}

      <div className="pv-compact-top">
        <span
          className="pv-compact-ring-pill"
          tabIndex={0}
          role="note"
          aria-label={`${fmtPct(donePct)} ${TASK_TAGS.done.label} (${safeDone} of ${safeTotal})`}
          onMouseEnter={() => setHovered('ring')}
          onFocus={() => setHovered('ring')}
          onBlur={() => setHovered(null)}
        >
          <svg
            className="pv-mini-ring"
            width="24"
            height="24"
            viewBox="0 0 26 26"
            aria-hidden="true"
          >
            <circle
              cx="13"
              cy="13"
              r={ringRadius}
              fill="none"
              stroke="var(--rule)"
              strokeWidth="3"
            />
            {donePct > 0 ? (
              <circle
                cx="13"
                cy="13"
                r={ringRadius}
                fill="none"
                stroke="var(--pv-done)"
                strokeWidth="3"
                strokeLinecap="round"
                strokeDasharray={ringCircumference}
                strokeDashoffset={ringOffset}
                transform="rotate(-90 13 13)"
              />
            ) : null}
          </svg>
          <span className="pv-compact-pct tabular">{fmtPct(donePct)}</span>
        </span>

        <span className="pv-compact-meta tabular">
          {label ? (
            label
          ) : (
            <>
              <strong>{safeDone.toLocaleString('en-IN')}</strong>/{safeTotal.toLocaleString('en-IN')} tasks
            </>
          )}
        </span>

        {verified != null && verified > 0 ? (
          <span className="pv-compact-stat pv-compact-stat--verified tabular">
            {verified.toLocaleString('en-IN')} {FINDING_TAGS.verified.label}
          </span>
        ) : null}
        {peerReview != null && peerReview > 0 ? (
          <span className="pv-compact-stat pv-compact-stat--review tabular">
            {peerReview.toLocaleString('en-IN')} {FINDING_TAGS.in_review.label}
          </span>
        ) : null}
      </div>

      <div
        className="pv-compact-track"
        role="group"
        aria-label={`Progress breakdown: ${fmtPct(donePct)} ${TASK_TAGS.done.label}, ${fmtPct(claimedPct)} ${TASK_TAGS.in_progress.label}, ${fmtPct(openPct)} ${TASK_TAGS.open.label}`}
      >
        {safeDone > 0 ? (
          <span
            className={`pv-compact-seg pv-compact-seg--done${hovered === 'completed' ? ' is-active' : ''}`}
            style={{ width: `${Math.max(donePct, 3)}%` }}
            tabIndex={0}
            role="note"
            aria-label={`${safeDone} of ${safeTotal}, ${fmtPct(donePct)} ${TASK_TAGS.done.label}`}
            onMouseEnter={() => setHovered('completed')}
            onFocus={() => setHovered('completed')}
            onBlur={() => setHovered(null)}
          />
        ) : null}
        {safeClaimed > 0 ? (
          <span
            className={`pv-compact-seg pv-compact-seg--claimed${hovered === 'claimed' ? ' is-active' : ''}`}
            style={{ width: `${Math.max(claimedPct, 3)}%` }}
            tabIndex={0}
            role="note"
            aria-label={`${safeClaimed} of ${safeTotal}, ${fmtPct(claimedPct)} ${TASK_TAGS.in_progress.label}`}
            onMouseEnter={() => setHovered('claimed')}
            onFocus={() => setHovered('claimed')}
            onBlur={() => setHovered(null)}
          />
        ) : null}
        {openCount > 0 ? (
          <span
            className={`pv-compact-seg pv-compact-seg--open${hovered === 'queued' ? ' is-active' : ''}`}
            style={{ flex: '1 1 0%' }}
            tabIndex={0}
            role="note"
            aria-label={`${openCount} of ${safeTotal}, ${fmtPct(openPct)} ${TASK_TAGS.open.label}`}
            onMouseEnter={() => setHovered('queued')}
            onFocus={() => setHovered('queued')}
            onBlur={() => setHovered(null)}
          />
        ) : null}
      </div>
    </div>
  );
}

/* ============================================================================
   2. PROJECT PROGRESS WIDGET (Top-of-project Donut + Interactive Task Mosaic)
   ============================================================================ */

const MOSAIC_PAGE_SIZE = 100;

export function ProjectProgressWidget({
  mission,
  visualSummary,
  tasks,
  onCopyTaskPrompt,
}: ProjectProgressWidgetProps) {
  const [activeBucket, setActiveBucket] = useState<StatusBucket | null>(null);
  const [hoveredTileIdx, setHoveredTileIdx] = useState<number | null>(null);
  const [copiedTaskId, setCopiedTaskId] = useState<string | null>(null);
  const [mosaicPage, setMosaicPage] = useState(0);
  const copyTimerRef = useRef<number | null>(null);

  useEffect(() => {
    setMosaicPage(0);
    setHoveredTileIdx(null);
  }, [mission.slug]);

  useEffect(() => {
    return () => {
      if (copyTimerRef.current !== null) {
        window.clearTimeout(copyTimerRef.current);
      }
    };
  }, []);

  // A large project's detail payload carries only its first open tasks, so the mosaic shows
  // those and every count comes from the hub's SQL status counts instead of `tasks`.
  const truncated = Boolean(mission.tasks_truncated);

  // Primary audit tasks for the mosaic grid (excluding peer_verify/verify_lead unless only peer checks exist)
  const mosaicTasks = useMemo(() => {
    const primary = tasks.filter(
      (t) => t.task_type !== 'peer_verify' && t.task_type !== 'verify_lead',
    );
    const pool = primary.length > 0 ? primary : tasks;
    if (!truncated) return pool;
    const open = pool.filter((t) => t.status === 'queued');
    return (open.length > 0 ? open : pool).slice(0, MOSAIC_PAGE_SIZE * 2);
  }, [tasks, truncated]);

  const counts = useMemo(() => {
    const sc = visualSummary?.status_counts;
    if (sc && sc.total > 0) {
      return {
        completed: sc.completed,
        claimed: sc.claimed,
        queued: sc.queued,
        waiting: sc.waiting,
        planned: sc.planned,
        total: sc.total,
        pctComplete: sc.pct_complete,
        auditsDone: sc.audits_done,
        auditsTotal: sc.audits_total,
        checksDone: sc.checks_done,
        checksTotal: sc.checks_total,
      };
    }

    if (!truncated && mosaicTasks.length > 0) {
      let completedCount = 0;
      let claimedCount = 0;
      let queuedCount = 0;
      let waitingCount = 0;
      for (const t of mosaicTasks) {
        const b = classifyTaskStatus(t);
        if (b === 'completed') completedCount += 1;
        else if (b === 'claimed') claimedCount += 1;
        else if (b === 'queued') queuedCount += 1;
        else waitingCount += 1;
      }
      const totalCount = mosaicTasks.length;
      const peerChecks = tasks.filter(
        (t) => t.task_type === 'peer_verify' || t.task_type === 'verify_lead',
      );
      const peerDone = peerChecks.filter(
        (t) => t.status === 'completed' || t.status === 'settled',
      ).length;
      return {
        completed: completedCount,
        claimed: claimedCount,
        queued: queuedCount,
        waiting: waitingCount,
        planned: mission.planned_tasks?.length ?? 0,
        total: totalCount,
        pctComplete: calcPct(completedCount, totalCount),
        auditsDone: completedCount,
        auditsTotal: totalCount,
        checksDone: peerDone,
        checksTotal: peerChecks.length,
      };
    }

    const totalCount = Math.max(0, mission.total_tasks || 0);
    const completedCount = Math.min(totalCount, mission.completed_tasks || 0);
    const claimedCount = Math.min(totalCount - completedCount, Math.max(0, mission.claimed_tasks ?? 0));
    const queuedCount = Math.max(0, totalCount - completedCount - claimedCount);
    return {
      completed: completedCount,
      claimed: claimedCount,
      queued: queuedCount,
      waiting: 0,
      planned: mission.planned_tasks?.length ?? 0,
      total: totalCount,
      pctComplete: calcPct(completedCount, totalCount),
      auditsDone: completedCount,
      auditsTotal: totalCount,
      checksDone: 0,
      checksTotal: 0,
    };
  }, [visualSummary, truncated, mosaicTasks, tasks, mission]);

  const segments = useMemo(() => {
    const order: StatusBucket[] = ['completed', 'claimed', 'queued', 'waiting'];
    const raw: Array<{ key: StatusBucket; count: number; pct: number }> = order.map((key) => {
      const count = counts[key];
      return { key, count, pct: calcPct(count, counts.total) };
    });

    const radius = 74;
    const circumference = 2 * Math.PI * radius;
    let cumulative = 0;

    return raw.map((seg) => {
      const arcLength = counts.total > 0 ? (seg.count / counts.total) * circumference : 0;
      const dashOffset = -cumulative;
      cumulative += arcLength;
      return {
        ...seg,
        ...STATUS_META[seg.key],
        arcLength,
        dashOffset,
        circumference,
      };
    });
  }, [counts]);

  const activeSeg = activeBucket
    ? segments.find((s) => s.key === activeBucket) ?? null
    : null;

  const mosaicPageCount = Math.max(1, Math.ceil(mosaicTasks.length / MOSAIC_PAGE_SIZE));
  const safeMosaicPage = Math.min(mosaicPage, mosaicPageCount - 1);
  const mosaicStart = mosaicTasks.length > 120 ? safeMosaicPage * MOSAIC_PAGE_SIZE : 0;
  const mosaicEnd =
    mosaicTasks.length > 120
      ? Math.min(mosaicTasks.length, mosaicStart + MOSAIC_PAGE_SIZE)
      : mosaicTasks.length;
  const visibleMosaicTasks = useMemo(
    () => mosaicTasks.slice(mosaicStart, mosaicEnd),
    [mosaicTasks, mosaicStart, mosaicEnd],
  );

  const hoveredTile = hoveredTileIdx !== null ? mosaicTasks[hoveredTileIdx] ?? null : null;
  const hoveredTileBucket = hoveredTile ? classifyTaskStatus(hoveredTile) : null;
  const hoveredTileTag = hoveredTile
    ? getTaskTag(hoveredTile.status, hoveredTileBucket === 'waiting')
    : null;
  // Each tile's share of the whole project, not of the tiles on screen.
  const projectTaskTotal = truncated ? counts.auditsTotal || counts.total : mosaicTasks.length;
  const tileWeightPct = projectTaskTotal > 0 ? 100 / projectTaskTotal : 0;

  const handleTileClick = (task: MissionTask, bucket: StatusBucket, globalIdx: number) => {
    setHoveredTileIdx(globalIdx);
    if (bucket !== 'queued' || !onCopyTaskPrompt) return;
    onCopyTaskPrompt(task);
    const idStr = String(task.id);
    setCopiedTaskId(idStr);
    if (copyTimerRef.current !== null) {
      window.clearTimeout(copyTimerRef.current);
    }
    copyTimerRef.current = window.setTimeout(() => {
      setCopiedTaskId(null);
      copyTimerRef.current = null;
    }, 2000);
  };

  return (
    <section
      className="card card-pad pv-progress-widget"
      aria-label="Project progress overview"
    >
      <div className="pv-progress-grid">
        {/* LEFT: Interactive SVG Donut */}
        <div className="pv-donut-panel">
          <div className="pv-panel-head">
            <span className="kicker">Completion breakdown</span>
            <span className="pv-count-pill tabular">
              {counts.completed.toLocaleString('en-IN')} / {counts.total.toLocaleString('en-IN')}
            </span>
          </div>

          <div
            className="pv-donut-stage"
            onMouseLeave={() => setActiveBucket(null)}
          >
            {activeSeg ? (
              <div className="pv-tooltip pv-tooltip--donut" role="tooltip">
                <strong>{activeSeg.label}:</strong>{' '}
                <span className="tabular">
                  {activeSeg.count.toLocaleString('en-IN')} / {counts.total.toLocaleString('en-IN')} (
                  {fmtPct(activeSeg.pct)})
                </span>
              </div>
            ) : null}

            <svg
              className="pv-donut-svg"
              viewBox="0 0 196 196"
              width="196"
              height="196"
              role="img"
              aria-label={`Project progress chart: ${fmtPct(counts.pctComplete)} ${TASK_TAGS.done.label} (${counts.completed} of ${counts.total} tasks)`}
            >
              <circle
                cx="98"
                cy="98"
                r="74"
                fill="none"
                stroke="var(--sunk)"
                strokeWidth="22"
              />
              <g transform="rotate(-90 98 98)">
                {segments.map((seg) => {
                  if (seg.count <= 0) return null;
                  const isActive = activeBucket === seg.key;
                  const isDimmed = activeBucket !== null && !isActive;
                  const strokeGap = counts.total > seg.count ? 1.5 : 0;
                  const visibleArc = Math.max(0.5, seg.arcLength - strokeGap);
                  return (
                    <circle
                      key={seg.key}
                      cx="98"
                      cy="98"
                      r="74"
                      fill="none"
                      stroke={seg.color}
                      strokeWidth={isActive ? 26 : 22}
                      strokeDasharray={
                        seg.dashed
                          ? buildDashedArcArray(visibleArc, seg.circumference)
                          : `${visibleArc.toFixed(2)} ${Math.max(0, seg.circumference - visibleArc).toFixed(2)}`
                      }
                      strokeDashoffset={seg.dashOffset}
                      className={`pv-donut-arc${isActive ? ' is-active' : ''}${isDimmed ? ' is-dimmed' : ''}`}
                      tabIndex={0}
                      role="button"
                      aria-label={`${seg.label}: ${seg.count} of ${counts.total} (${fmtPct(seg.pct)})`}
                      onPointerEnter={(e) => {
                        if (e.pointerType === 'mouse') setActiveBucket(seg.key);
                      }}
                      onClick={() => setActiveBucket((prev) => (prev === seg.key ? null : seg.key))}
                      onFocus={() => setActiveBucket(seg.key)}
                      onBlur={() => setActiveBucket(null)}
                    />
                  );
                })}
              </g>
            </svg>

            <div className="pv-donut-center" aria-live="polite">
              <span className="pv-donut-pct tabular">
                {activeSeg ? fmtPct(activeSeg.pct) : fmtPct(counts.pctComplete)}
              </span>
              <span className="pv-donut-lbl">
                {activeSeg ? activeSeg.label : TASK_TAGS.done.label}
              </span>
              <span className="pv-donut-sub tabular">
                {activeSeg
                  ? `${activeSeg.count.toLocaleString('en-IN')} / ${counts.total.toLocaleString('en-IN')}`
                  : `${counts.completed.toLocaleString('en-IN')} / ${counts.total.toLocaleString('en-IN')} tasks`}
              </span>
            </div>
          </div>

          <div className="pv-legend-list" role="list" aria-label="Task status breakdown">
            {segments
              .filter((s) => s.key !== 'waiting' || s.count > 0)
              .map((seg) => {
                const isActive = activeBucket === seg.key;
                return (
                  <button
                    key={seg.key}
                    type="button"
                    role="listitem"
                    aria-pressed={isActive}
                    className={`pv-legend-item${isActive ? ' is-active' : ''}`}
                    onPointerEnter={(e) => {
                      if (e.pointerType === 'mouse') setActiveBucket(seg.key);
                    }}
                    onPointerLeave={(e) => {
                      if (e.pointerType === 'mouse') setActiveBucket(null);
                    }}
                    onClick={() => setActiveBucket((prev) => (prev === seg.key ? null : seg.key))}
                    onFocus={() => setActiveBucket(seg.key)}
                    onBlur={() => setActiveBucket(null)}
                  >
                    <span
                      className={`pv-swatch pv-swatch--${seg.key}`}
                      aria-hidden="true"
                    />
                    <span className="pv-legend-name">{seg.label}</span>
                    <span className="pv-legend-val tabular">
                      {seg.count.toLocaleString('en-IN')} · {fmtPct(seg.pct)}
                    </span>
                  </button>
                );
              })}
          </div>
        </div>

        {/* RIGHT: Interactive Task Mosaic Grid */}
        <div className="pv-mosaic-panel">
          <div className="pv-panel-head">
            <div>
              <span className="kicker">
                {truncated
                  ? `Task mosaic · first ${mosaicTasks.length.toLocaleString('en-IN')} open tasks`
                  : 'Task progress mosaic'}
              </span>
              <p className="pv-mosaic-hint">
                {truncated
                  ? `${counts.total.toLocaleString('en-IN')} tasks in all; the table below searches every one. `
                  : ''}
                Hover any tile for details
                {onCopyTaskPrompt ? ' · click an open tile to copy its 1-line prompt' : ''}.
              </p>
            </div>
            <div className="pv-mosaic-badges">
              {copiedTaskId ? (
                <span className="pv-copied-pill" role="status" aria-live="polite">
                  <Check size={12} />
                  Copied 1-line prompt!
                </span>
              ) : null}
              {counts.checksTotal > 0 ? (
                <span className={`${WORK_TYPE_TAGS.peer_check.badgeClass} tabular`}>
                  {WORK_TYPE_TAGS.peer_check.label}: {counts.checksDone}/{counts.checksTotal}
                </span>
              ) : null}
              {counts.planned > 0 ? (
                <span className={`${TASK_TAGS.planned.badgeClass} tabular`}>
                  +{counts.planned} {TASK_TAGS.planned.label}
                </span>
              ) : null}
            </div>
          </div>

          {mosaicTasks.length > 0 ? (
            <div
              className="pv-mosaic-wrap"
              onMouseLeave={() => setHoveredTileIdx(null)}
            >
              {mosaicTasks.length > 120 ? (
                <div className="pv-mosaic-pager" role="navigation" aria-label="Task mosaic blocks">
                  <span className="pv-mosaic-pager-label tabular">
                    {truncated ? 'Open tasks' : 'Tasks'} {(mosaicStart + 1).toLocaleString('en-IN')}–
                    {mosaicEnd.toLocaleString('en-IN')} of{' '}
                    {mosaicTasks.length.toLocaleString('en-IN')}
                    {truncated ? ' shown' : ''}
                  </span>
                  <div className="pv-mosaic-blocks">
                    {Array.from({ length: mosaicPageCount }, (_, pageIdx) => {
                      const from = pageIdx * MOSAIC_PAGE_SIZE + 1;
                      const to = Math.min(mosaicTasks.length, (pageIdx + 1) * MOSAIC_PAGE_SIZE);
                      const isCurrent = pageIdx === safeMosaicPage;
                      return (
                        <button
                          key={pageIdx}
                          type="button"
                          className={`pv-mosaic-block-btn tabular${isCurrent ? ' is-active' : ''}`}
                          aria-current={isCurrent ? 'page' : undefined}
                          onClick={() => {
                            setMosaicPage(pageIdx);
                            setHoveredTileIdx(null);
                          }}
                        >
                          {from}–{to}
                        </button>
                      );
                    })}
                  </div>
                </div>
              ) : null}

              <div
                className="pv-mosaic-grid"
                role="grid"
                aria-label={
                  truncated
                    ? `First ${mosaicTasks.length} open tasks of ${counts.total}`
                    : `${mosaicTasks.length} project tasks`
                }
              >
                {visibleMosaicTasks.map((task, localIdx) => {
                  const globalIdx = mosaicStart + localIdx;
                  const bucket = classifyTaskStatus(task);
                  const taskTag = getTaskTag(task.status, bucket === 'waiting');
                  const isOpen = bucket === 'queued';
                  const isCopied = copiedTaskId === String(task.id);
                  const isHovered = hoveredTileIdx === globalIdx;
                  const isBucketFiltered = activeBucket !== null && activeBucket !== bucket;
                  const contributor = task.claimed_by_handle || task.claimed_by;

                  return (
                    <button
                      key={task.id}
                      type="button"
                      className={`pv-tile pv-tile--${bucket}${isHovered ? ' is-hovered' : ''}${isBucketFiltered ? ' is-dimmed' : ''}${isCopied ? ' is-copied' : ''}`}
                      onMouseEnter={() => setHoveredTileIdx(globalIdx)}
                      onFocus={() => setHoveredTileIdx(globalIdx)}
                      onBlur={() => setHoveredTileIdx(null)}
                      onClick={() => handleTileClick(task, bucket, globalIdx)}
                      aria-label={`${task.title || `Task ${globalIdx + 1}`}: ${taskTag.label}, ${fmtPct(tileWeightPct)} of project${contributor ? `, by @${contributor}` : ''}${isOpen && onCopyTaskPrompt ? '. Click to copy 1-line prompt' : ''}`}
                      title={task.title || `Task ${globalIdx + 1}`}
                    >
                      {isCopied ? <Check size={11} aria-hidden="true" /> : null}
                    </button>
                  );
                })}
              </div>

              {/* Floating hover tooltip + persistent inspector */}
              {hoveredTile && hoveredTileBucket && hoveredTileTag ? (
                <div className="pv-tooltip pv-tooltip--mosaic" role="tooltip">
                  <div className="pv-tooltip-title">
                    {hoveredTile.title || `Task ${String(hoveredTile.id)}`}
                  </div>
                  <div className="pv-tooltip-row tabular">
                    <strong>{hoveredTileTag.label}</strong>
                    <span>·</span>
                    <span>
                      1 / {projectTaskTotal.toLocaleString('en-IN')} ({fmtPct(tileWeightPct)})
                    </span>
                    {hoveredTile.claimed_by_handle || hoveredTile.claimed_by ? (
                      <>
                        <span>·</span>
                        <span>@{hoveredTile.claimed_by_handle || hoveredTile.claimed_by}</span>
                      </>
                    ) : null}
                  </div>
                  {hoveredTileBucket === 'queued' && onCopyTaskPrompt ? (
                    <div className="pv-tooltip-hint">Click tile to copy 1-line prompt</div>
                  ) : null}
                </div>
              ) : null}

              <div
                className={`pv-mosaic-inspector${hoveredTile ? ' is-active' : ''}`}
                role="status"
                aria-live="polite"
              >
                {hoveredTile && hoveredTileBucket && hoveredTileTag ? (
                  <>
                    <div className="pv-inspector-top">
                      <span
                        className={`pv-swatch pv-swatch--${hoveredTileBucket}`}
                        aria-hidden="true"
                      />
                      <span className="pv-inspector-title">
                        {hoveredTile.title || `Task ${String(hoveredTile.id)}`}
                      </span>
                    </div>
                    <div className="pv-inspector-meta tabular">
                      <span className={hoveredTileTag.badgeClass}>{hoveredTileTag.label}</span>
                      <span>
                        1 / {projectTaskTotal.toLocaleString('en-IN')} · {fmtPct(tileWeightPct)} of project
                      </span>
                      {hoveredTile.claimed_by_handle || hoveredTile.claimed_by ? (
                        <span className="pv-inspector-actor">
                          @{hoveredTile.claimed_by_handle || hoveredTile.claimed_by}
                        </span>
                      ) : null}
                      {hoveredTileBucket === 'queued' && onCopyTaskPrompt ? (
                        <span className="pv-inspector-cta">
                          <Copy size={11} aria-hidden="true" />
                          Click tile to copy 1-line prompt
                        </span>
                      ) : null}
                    </div>
                  </>
                ) : (
                  <div className="pv-inspector-idle tabular">
                    <span>
                      <strong>{counts.queued.toLocaleString('en-IN')}</strong> {TASK_TAGS.open.label} (
                      {fmtPct(calcPct(counts.queued, counts.total))})
                    </span>
                    <span aria-hidden="true">·</span>
                    <span>
                      <strong>{counts.completed.toLocaleString('en-IN')}</strong> {TASK_TAGS.done.label} (
                      {fmtPct(counts.pctComplete)})
                    </span>
                    {counts.claimed > 0 ? (
                      <>
                        <span aria-hidden="true">·</span>
                        <span>
                          <strong>{counts.claimed.toLocaleString('en-IN')}</strong>{' '}
                          {TASK_TAGS.in_progress.label} ({fmtPct(calcPct(counts.claimed, counts.total))})
                        </span>
                      </>
                    ) : null}
                    {counts.waiting > 0 ? (
                      <>
                        <span aria-hidden="true">·</span>
                        <span>
                          <strong>{counts.waiting.toLocaleString('en-IN')}</strong>{' '}
                          {TASK_TAGS.waiting.label} ({fmtPct(calcPct(counts.waiting, counts.total))})
                        </span>
                      </>
                    ) : null}
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="pv-empty-box">
              No queued tasks yet. Once datasets are published, every task tile appears here.
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

/* ============================================================================
   3. PROJECT RESULTS VISUALISER (P1 Divergences / W1 GIGW / UB1 Union Budget lines)
   ============================================================================ */

export function ProjectResultsVisualizer({
  mission,
  visualSummary,
  findings,
  onSelectFinding,
  onFilterTasks,
}: ProjectResultsVisualizerProps) {
  const code = (visualSummary?.methodology_code || mission.methodology_code || '').toUpperCase();

  if (code === 'W1') {
    return <WebsiteAuditVisualizer mission={mission} visualSummary={visualSummary} />;
  }
  if (code === 'UB1') {
    return (
      <UnionBudgetVisualizer
        mission={mission}
        visualSummary={visualSummary}
        onSelectFinding={onSelectFinding}
        onFilterTasks={onFilterTasks}
      />
    );
  }
  if (code === 'OR1') {
    return (
      <OneRoadTwoBillsVisualizer
        mission={mission}
        visualSummary={visualSummary}
        onSelectFinding={onSelectFinding}
        onFilterTasks={onFilterTasks}
      />
    );
  }
  return (
    <BudgetAuditVisualizer
      mission={mission}
      visualSummary={visualSummary}
      findings={findings}
      onSelectFinding={onSelectFinding}
    />
  );
}

/* ---------- 3A. P1 / Budget Audit Divergence & Force Coverage ---------- */

function BudgetAuditVisualizer({
  mission,
  visualSummary,
  findings = [],
  onSelectFinding,
}: {
  mission: MissionItem;
  visualSummary?: ProjectVisualSummary;
  findings?: FindingItem[];
  onSelectFinding?: (id: string | number) => void;
}) {
  const [hoveredDivIdx, setHoveredDivIdx] = useState<number | null>(null);
  const [hoveredGroupIdx, setHoveredGroupIdx] = useState<number | null>(null);

  const budget = visualSummary?.budget_audit;
  const workTag = getWorkTypeTag(undefined, mission.methodology_code);

  const divergences = useMemo(() => {
    if (budget?.top_divergences && budget.top_divergences.length > 0) {
      return budget.top_divergences;
    }
    return findings
      .filter((f) => !f.quarantined)
      .slice(0, 12)
      .map((f) => ({
        finding_id: String(f.id),
        entity_name: f.entity_name || f.jurisdiction || 'Public authority',
        title: f.title,
        pct_difference: extractNumericMetric(f.anomaly_metrics),
        monetary_exposure_inr_cr: f.monetary_exposure_inr ? f.monetary_exposure_inr / 1e7 : 0,
        status: String(f.status || 'peer_review'),
        severity: String(f.severity || 'medium'),
        fiscal_year: f.fiscal_year || '',
        rule_triggered: f.rule_triggered || '',
      }));
  }, [budget, findings]);

  const groups = budget?.groups ?? [];
  const verifiedCount = budget?.verified_count ?? mission.verified_findings_count ?? 0;
  const peerReviewCount = budget?.peer_review_count ?? mission.peer_review_count ?? 0;
  const clearedCount = budget?.cleared_count ?? mission.cleared_count ?? 0;
  const disputedCount = budget?.disputed_count ?? 0;
  const totalExposureCr =
    budget?.total_exposure_inr_cr ??
    mission.unique_coverage_inr_cr ??
    mission.monetary_audited_inr_cr ??
    0;

  const hasPctValues = divergences.some((d) => d.pct_difference !== null);
  const maxAbsPct = useMemo(() => {
    const vals = divergences
      .map((d) => (d.pct_difference !== null ? Math.abs(d.pct_difference) : 0))
      .filter((v) => Number.isFinite(v));
    return Math.max(25, ...vals);
  }, [divergences]);

  const maxExposure = useMemo(() => {
    const vals = divergences.map((d) => Math.max(0, d.monetary_exposure_inr_cr || 0));
    return Math.max(1, ...vals);
  }, [divergences]);

  const thresholdPosPct = Math.min(92, (20 / maxAbsPct) * 100);

  return (
    <section className="card card-pad pv-results" aria-label="Budget audit visual results">
      <div className="pv-results-head">
        <div className="pv-results-title-wrap">
          <span className="kicker">
            <BarChart3 size={12} aria-hidden="true" />
            Live results · {workTag.label}
          </span>
          <h2 className="pv-results-title">Budget variances &amp; force coverage</h2>
        </div>

        <div className="pv-summary-pills tabular">
          <span className={FINDING_TAGS.verified.badgeClass}>
            {verifiedCount.toLocaleString('en-IN')} {FINDING_TAGS.verified.label}
          </span>
          <span className={FINDING_TAGS.in_review.badgeClass}>
            {peerReviewCount.toLocaleString('en-IN')} {FINDING_TAGS.in_review.label}
          </span>
          <span className={FINDING_TAGS.cleared.badgeClass}>
            {clearedCount.toLocaleString('en-IN')} {FINDING_TAGS.cleared.label}
          </span>
          {disputedCount > 0 ? (
            <span className={FINDING_TAGS.disputed.badgeClass}>
              {disputedCount.toLocaleString('en-IN')} {FINDING_TAGS.disputed.label}
            </span>
          ) : null}
          {totalExposureCr > 0 ? (
            <span className="tag-mono">{formatInrCr(totalExposureCr)} flagged</span>
          ) : null}
        </div>
      </div>

      <div className="pv-results-split">
        {/* Divergence Bar Chart */}
        <div className="pv-chart-card">
          <div className="pv-chart-subhead">
            <h3>Top budget variances</h3>
            <div className="pv-mini-legend">
              <span>
                <i className="pv-dot pv-dot--verified" /> {FINDING_TAGS.verified.label}
              </span>
              <span>
                <i className="pv-dot pv-dot--review" /> {FINDING_TAGS.in_review.label}
              </span>
              <span>
                <i className="pv-dot pv-dot--disputed" /> {FINDING_TAGS.disputed.label}
              </span>
            </div>
          </div>

          {divergences.length > 0 ? (
            <div className="pv-div-list" onMouseLeave={() => setHoveredDivIdx(null)}>
              {divergences.map((item, idx) => {
                const findingTag = getFindingTag(item.status);
                const barKind =
                  findingTag.tone === 'verified' || findingTag.tone === 'rti'
                    ? 'verified'
                    : findingTag.tone === 'disputed'
                      ? 'disputed'
                      : findingTag.tone === 'cleared' || findingTag.tone === 'neutral'
                        ? 'cleared'
                        : 'review';
                const widthPct =
                  hasPctValues && item.pct_difference !== null
                    ? Math.max(4, Math.min(100, (Math.abs(item.pct_difference) / maxAbsPct) * 100))
                    : Math.max(
                        4,
                        Math.min(100, ((item.monetary_exposure_inr_cr || 0) / maxExposure) * 100),
                      );
                const isHovered = hoveredDivIdx === idx;

                return (
                  <div key={`${item.finding_id}-${idx}`} className="pv-div-row-wrap">
                    {isHovered ? (
                      <div className="pv-tooltip pv-tooltip--bar" role="tooltip">
                        <div className="pv-tooltip-title">{item.title}</div>
                        <div className="pv-tooltip-row tabular">
                          <span>{item.entity_name}</span>
                          {item.fiscal_year ? <span>· FY {item.fiscal_year}</span> : null}
                        </div>
                        <div className="pv-tooltip-row tabular">
                          {item.pct_difference !== null ? (
                            <strong>Variance {formatSignedPct(item.pct_difference)}</strong>
                          ) : null}
                          {item.monetary_exposure_inr_cr > 0 ? (
                            <span>{formatInrCr(item.monetary_exposure_inr_cr)}</span>
                          ) : null}
                          <span>{findingTag.label}</span>
                          {item.rule_triggered ? <span>· {item.rule_triggered}</span> : null}
                        </div>
                        {onSelectFinding ? (
                          <div className="pv-tooltip-hint">Click to inspect full finding dossier</div>
                        ) : null}
                      </div>
                    ) : null}

                    <button
                      type="button"
                      className={`pv-div-row${isHovered ? ' is-hovered' : ''}${onSelectFinding ? ' is-clickable' : ''}`}
                      onMouseEnter={() => setHoveredDivIdx(idx)}
                      onFocus={() => setHoveredDivIdx(idx)}
                      onBlur={() => setHoveredDivIdx(null)}
                      onClick={() => onSelectFinding?.(item.finding_id)}
                      aria-label={`${item.entity_name}: ${item.pct_difference !== null ? formatSignedPct(item.pct_difference) : formatInrCr(item.monetary_exposure_inr_cr)}, ${findingTag.label}`}
                    >
                      <div className="pv-div-label">
                        <span className="pv-div-entity">{item.entity_name}</span>
                        <span className="pv-div-meta">
                          {item.fiscal_year || item.rule_triggered || findingTag.label}
                        </span>
                      </div>

                      <div className="pv-div-track" aria-hidden="true">
                        {hasPctValues ? (
                          <span
                            className="pv-div-threshold"
                            style={{ left: `${thresholdPosPct}%` }}
                            title="20% variance threshold"
                          />
                        ) : null}
                        <span
                          className={`pv-div-bar pv-div-bar--${barKind}`}
                          style={{ width: `${widthPct}%` }}
                        />
                      </div>

                      <div className="pv-div-val tabular">
                        <strong>
                          {item.pct_difference !== null
                            ? formatSignedPct(item.pct_difference)
                            : formatInrCr(item.monetary_exposure_inr_cr)}
                        </strong>
                        {item.pct_difference !== null && item.monetary_exposure_inr_cr > 0 ? (
                          <span className="pv-div-exposure">
                            {formatInrCr(item.monetary_exposure_inr_cr)}
                          </span>
                        ) : null}
                      </div>
                    </button>
                  </div>
                );
              })}
              {hasPctValues ? (
                <div className="pv-axis-note">
                  Dashed vertical line marks the 20% review threshold. Dashed bars are{' '}
                  {FINDING_TAGS.in_review.label}; solid green bars are{' '}
                  {FINDING_TAGS.verified.label}.
                </div>
              ) : null}
            </div>
          ) : (
            <div className="pv-empty-box">
              No budget divergences reported yet. As contributor agents check police budget lines
              under MH 2055 &amp; 4055, flagged variances appear here as clickable bars.
            </div>
          )}
        </div>

        {/* Coverage by Force Category */}
        <div className="pv-chart-card">
          <div className="pv-chart-subhead">
            <h3>Coverage by force category</h3>
            <span className="pv-chart-note">Hover bar for exact %</span>
          </div>

          {groups.length > 0 ? (
            <div className="pv-group-list" onMouseLeave={() => setHoveredGroupIdx(null)}>
              {groups.map((grp, idx) => {
                const isHovered = hoveredGroupIdx === idx;
                const remaining = Math.max(0, grp.total - grp.done);
                const exactPct = grp.total > 0 ? calcPct(grp.done, grp.total) : grp.pct;

                return (
                  <div
                    key={grp.label}
                    className={`pv-group-row${isHovered ? ' is-hovered' : ''}`}
                    tabIndex={0}
                    role="note"
                    aria-label={`${grp.label}: ${grp.done} of ${grp.total} ${TASK_TAGS.done.label} (${fmtPct(exactPct)})`}
                    onMouseEnter={() => setHoveredGroupIdx(idx)}
                    onFocus={() => setHoveredGroupIdx(idx)}
                    onBlur={() => setHoveredGroupIdx(null)}
                  >
                    {isHovered ? (
                      <div className="pv-tooltip pv-tooltip--bar" role="tooltip">
                        <strong>{grp.label}:</strong>{' '}
                        <span className="tabular">
                          {grp.done.toLocaleString('en-IN')} / {grp.total.toLocaleString('en-IN')} ·{' '}
                          {fmtPct(exactPct)} {TASK_TAGS.done.label} (
                          {remaining.toLocaleString('en-IN')} {TASK_TAGS.open.label})
                        </span>
                      </div>
                    ) : null}

                    <div className="pv-group-head">
                      <span className="pv-group-label">{grp.label}</span>
                      <span className="pv-group-stat tabular">
                        <strong>{fmtPct(exactPct)}</strong> ({grp.done}/{grp.total})
                      </span>
                    </div>
                    <div className="pv-hbar-track">
                      <span
                        className="pv-hbar-fill"
                        style={{ width: `${exactPct > 0 ? Math.max(exactPct, 2) : 0}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="pv-empty-box">
              Force category coverage updates automatically once budget tasks are categorised.
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

/* ---------- 3B. W1 Website Audit: Exhaustive GIGW 3.0 + RTI Act 2005 + GoI Statutory Matrix ---------- */

type FrameworkFilter = 'all' | 'gigw' | 'rti' | 'statutory';
type ModeFilter = 'all' | WebsiteVerificationMode;

const W1_INITIAL_CHECKS = 10;
const MATRIX_INITIAL_ROWS = 15;

const VERIFICATION_MODE_META: Record<
  WebsiteVerificationMode,
  { label: string; shortLabel: string; badgeClass: string; hint: string }
> = {
  automated_http_html: {
    label: 'Automated',
    shortLabel: 'Automated',
    badgeClass: 'badge badge-verified',
    hint: 'Checked deterministically via read-only HTTP, TLS, and HTML DOM rules',
  },
  agent_inspection: {
    label: 'Frontier agent',
    shortLabel: 'Agent',
    badgeClass: 'badge badge-progress',
    hint: 'Evaluated by a frontier AI agent reading public pages and linked disclosures',
  },
  internal_governance: {
    label: 'Internal governance',
    shortLabel: 'Governance',
    badgeClass: 'badge badge-waiting',
    hint: 'Internal agency governance obligation; public disclosure indicators noted where observable',
  },
};

const AUTOMATION_COVERAGE_LABELS: Record<string, string> = {
  full: 'Full automated HTTP/HTML/TLS check',
  partial_public_indicator: 'Partial public indicator via automated check',
  none: 'Manual / qualitative or internal only',
};

const APPLICABILITY_LABELS: Record<string, string> = {
  all_gov_in_websites: 'All .gov.in websites',
  all_public_authorities_non_exempt:
    'All non-exempt public authorities (excluding RTI Sec 24 Second Schedule)',
  appropriate_government_central_and_state: 'Central & State governments',
  central_govt_and_union_bodies: 'Union & Central Government bodies',
  ministries_and_departments: 'Ministries & Departments',
  portals_collecting_citizen_data: 'Portals collecting citizen data',
};

function formatApplicabilityLabel(raw?: string | null): string {
  const key = (raw || '').trim();
  if (!key) return '';
  if (APPLICABILITY_LABELS[key]) return APPLICABILITY_LABELS[key];
  const spaced = key.replace(/_/g, ' ');
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function classifyFramework(framework?: string | null): Exclude<FrameworkFilter, 'all'> {
  const f = (framework || '').trim().toLowerCase();
  if (f.includes('gigw')) return 'gigw';
  if (f.includes('rti') || f.includes('dopt')) return 'rti';
  return 'statutory';
}

function WebsiteAuditVisualizer({
  mission,
  visualSummary,
}: {
  mission: MissionItem;
  visualSummary?: ProjectVisualSummary;
}) {
  const [hoveredCell, setHoveredCell] = useState<{
    key: string;
    segment: 'observed' | 'not_observed' | 'unassessable' | 'not_applicable' | 'row';
  } | null>(null);
  const [hoveredReason, setHoveredReason] = useState<string | null>(null);
  const [frameworkFilter, setFrameworkFilter] = useState<FrameworkFilter>('all');
  const [selectedSectionId, setSelectedSectionId] = useState<string>('all');
  const [modeFilter, setModeFilter] = useState<ModeFilter>('all');
  const [matrixQuery, setMatrixQuery] = useState('');
  const [showAllW1, setShowAllW1] = useState(false);
  const [showAllMatrix, setShowAllMatrix] = useState(false);

  const web = visualSummary?.website_audit;
  const batchesDone = web?.batches_done ?? mission.completed_tasks ?? 0;
  const batchesTotal = web?.batches_total ?? mission.total_tasks ?? 1000;
  const hostsObserved = web?.hosts_observed ?? 0;
  const hostsUnassessable = web?.hosts_unassessable ?? 0;
  const hostsTotal = web?.hosts_total ?? hostsObserved + hostsUnassessable;
  const totalDirectoryHosts = web?.total_directory_hosts ?? 0;
  const hasObservations = batchesDone > 0 && hostsTotal > 0;

  const matrixSummary = web?.matrix_summary;
  const sections = useMemo(() => web?.sections ?? [], [web?.sections]);
  const matrixItems = useMemo(() => web?.matrix ?? [], [web?.matrix]);
  const hasRichMatrix = Boolean(matrixSummary && matrixItems.length > 0);

  const foundTag = WEBSITE_OUTCOME_TAGS.found;
  const missingTag = WEBSITE_OUTCOME_TAGS.missing;
  const unreachableTag = WEBSITE_OUTCOME_TAGS.unreachable;

  const checkpoints = useMemo<VisualWebsiteCheckpoint[]>(() => {
    if (web?.checkpoints && web.checkpoints.length > 0) {
      return web.checkpoints;
    }
    return DEFAULT_W1_CHECKPOINTS.map((c) => ({
      key: c.key,
      label: c.label,
      description: c.hint,
      observed: 0,
      not_observed: 0,
      unassessable: 0,
      not_applicable: 0,
      observed_pct: 0,
      not_observed_pct: 0,
      unassessable_pct: 0,
      not_applicable_pct: 0,
    }));
  }, [web?.checkpoints]);

  const sectionById = useMemo(
    () => Object.fromEntries(sections.map((s) => [s.id, s])),
    [sections],
  );

  const w1Sections = useMemo(() => {
    const present = new Set<string>();
    for (const cp of checkpoints) {
      if (cp.section) present.add(cp.section);
    }
    if (sections.length > 0) {
      return sections.filter((s) => present.has(s.id) || (s.w1_check_keys?.length ?? 0) > 0);
    }
    return Array.from(present).map((id) => ({
      id,
      name: id,
      short_name: id.replace(/_/g, ' '),
      framework: '',
      total_checkpoints: 0,
      automated_count: 0,
      agent_count: 0,
      internal_count: 0,
      w1_check_keys: [],
      avg_observed_pct: null,
    }));
  }, [checkpoints, sections]);

  const filteredCheckpoints = useMemo(() => {
    let list = checkpoints;
    if (selectedSectionId !== 'all') {
      const sec = sectionById[selectedSectionId];
      const secKeySet = sec?.w1_check_keys?.length ? new Set(sec.w1_check_keys) : null;
      list = list.filter(
        (cp) => cp.section === selectedSectionId || (secKeySet ? secKeySet.has(cp.key) : false),
      );
    } else if (frameworkFilter !== 'all') {
      list = list.filter((cp) => classifyFramework(cp.framework) === frameworkFilter);
    }
    return list;
  }, [checkpoints, selectedSectionId, sectionById, frameworkFilter]);

  const visibleCheckpoints =
    showAllW1 || selectedSectionId !== 'all' || frameworkFilter !== 'all'
      ? filteredCheckpoints
      : filteredCheckpoints.slice(0, W1_INITIAL_CHECKS);

  const filteredMatrix = useMemo<VisualWebsiteMatrixItem[]>(() => {
    const q = matrixQuery.trim().toLowerCase();
    return matrixItems.filter((item) => {
      if (frameworkFilter !== 'all' && classifyFramework(item.framework) !== frameworkFilter) {
        return false;
      }
      if (selectedSectionId !== 'all' && item.section_id !== selectedSectionId) {
        return false;
      }
      if (modeFilter !== 'all' && item.verification_mode !== modeFilter) {
        return false;
      }
      if (!q) return true;
      return (
        item.id.toLowerCase().includes(q) ||
        item.title.toLowerCase().includes(q) ||
        item.requirement_text.toLowerCase().includes(q) ||
        (item.section_name || '').toLowerCase().includes(q) ||
        (item.citation || '').toLowerCase().includes(q) ||
        (item.wcag_reference || '').toLowerCase().includes(q) ||
        (item.risks_addressed || []).some((r) => r.toLowerCase().includes(q)) ||
        (item.w1_check_keys || []).some((k) => k.toLowerCase().includes(q))
      );
    });
  }, [matrixItems, frameworkFilter, selectedSectionId, modeFilter, matrixQuery]);

  const visibleMatrix = showAllMatrix ? filteredMatrix : filteredMatrix.slice(0, MATRIX_INITIAL_ROWS);
  const hasActiveMatrixFilters =
    frameworkFilter !== 'all' ||
    selectedSectionId !== 'all' ||
    modeFilter !== 'all' ||
    matrixQuery.trim().length > 0;

  const reasonEntries = useMemo(() => {
    if (!web?.reasons) return [];
    return Object.entries(web.reasons)
      .filter(([, count]) => count > 0)
      .sort((a, b) => b[1] - a[1]);
  }, [web?.reasons]);

  const hintByKey = useMemo(
    () => Object.fromEntries(DEFAULT_W1_CHECKPOINTS.map((c) => [c.key, c.hint])),
    [],
  );

  const hasAnyNotApplicable = useMemo(
    () => checkpoints.some((cp) => (cp.not_applicable ?? 0) > 0),
    [checkpoints],
  );

  const handleSelectSection = (secId: string) => {
    setSelectedSectionId((prev) => (prev === secId ? 'all' : secId));
    setShowAllMatrix(false);
  };

  const handleSelectFramework = (fw: FrameworkFilter) => {
    setFrameworkFilter(fw);
    if (fw !== 'all' && selectedSectionId !== 'all') {
      const sec = sectionById[selectedSectionId];
      if (sec && classifyFramework(sec.framework) !== fw) {
        setSelectedSectionId('all');
      }
    }
    setShowAllMatrix(false);
  };

  const handleToggleMode = (mode: ModeFilter) => {
    setModeFilter((prev) => (prev === mode ? 'all' : mode));
    setShowAllMatrix(false);
  };

  const handleResetFilters = () => {
    setFrameworkFilter('all');
    setSelectedSectionId('all');
    setModeFilter('all');
    setMatrixQuery('');
    setShowAllMatrix(false);
  };

  const totalCheckpointsCount = matrixSummary?.total_checkpoints ?? checkpoints.length;

  return (
    <section className="card card-pad pv-results" aria-label="GIGW 3.0 and RTI conformity audit results">
      {/* HEADER */}
      <div className="pv-results-head">
        <div className="pv-results-title-wrap">
          <span className="kicker">
            <Globe size={12} aria-hidden="true" />
            {hasRichMatrix
              ? `GIGW 3.0 · RTI Act 2005 · GoI Statutory Conformity`
              : `GIGW 3.0 checkpoints · ${WORK_TYPE_TAGS.website_check.label}`}
          </span>
          <h2 className="pv-results-title">
            {hasRichMatrix
              ? `${totalCheckpointsCount.toLocaleString('en-IN')}-checkpoint .gov.in conformity matrix`
              : `${checkpoints.length}-checkpoint .gov.in disclosure profile`}
          </h2>
        </div>

        <div className="pv-summary-pills tabular">
          <span className="tag-mono">
            Batches {TASK_TAGS.done.label}: {batchesDone}/{batchesTotal} (
            {fmtPct(calcPct(batchesDone, batchesTotal))})
          </span>
          {totalDirectoryHosts > 0 ? (
            <span className="tag-mono">
              {totalDirectoryHosts.toLocaleString('en-IN')} .gov.in domains
            </span>
          ) : null}
          <span className={foundTag.badgeClass}>
            {hostsObserved.toLocaleString('en-IN')} hosts checked
          </span>
          {hostsUnassessable > 0 ? (
            <span className={unreachableTag.badgeClass}>
              {hostsUnassessable.toLocaleString('en-IN')} {unreachableTag.label}
            </span>
          ) : null}
        </div>
      </div>

      {/* FRAMEWORK & VERIFICATION MODE OVERVIEW STRIP */}
      {matrixSummary ? (
        <div className="pv-matrix-overview" aria-label="Conformity matrix overview by verification mode">
          <button
            type="button"
            className={`pv-overview-tile${modeFilter === 'all' && frameworkFilter === 'all' && selectedSectionId === 'all' ? ' is-active' : ''}`}
            onClick={handleResetFilters}
          >
            <span className="pv-overview-kicker">Unified checklist</span>
            <strong className="pv-overview-num tabular">
              {matrixSummary.total_checkpoints.toLocaleString('en-IN')}
            </strong>
            <span className="pv-overview-sub tabular">
              {matrixSummary.gigw_checkpoints} GIGW 3.0 · {matrixSummary.rti_checkpoints} RTI ·{' '}
              {matrixSummary.statutory_other_checkpoints} GoI Statutory
            </span>
          </button>

          <button
            type="button"
            className={`pv-overview-tile pv-overview-tile--auto${modeFilter === 'automated_http_html' ? ' is-active' : ''}`}
            onClick={() => handleToggleMode('automated_http_html')}
            aria-pressed={modeFilter === 'automated_http_html'}
          >
            <span className="pv-overview-kicker">
              <i className="pv-dot pv-dot--verified" /> Automated HTTP/HTML/TLS
            </span>
            <strong className="pv-overview-num tabular">
              {matrixSummary.automated_http_html.toLocaleString('en-IN')}
            </strong>
            <span className="pv-overview-sub tabular">
              Across {matrixSummary.w1_automated_checks_count} deterministic checks
            </span>
          </button>

          <button
            type="button"
            className={`pv-overview-tile pv-overview-tile--agent${modeFilter === 'agent_inspection' ? ' is-active' : ''}`}
            onClick={() => handleToggleMode('agent_inspection')}
            aria-pressed={modeFilter === 'agent_inspection'}
          >
            <span className="pv-overview-kicker">
              <i className="pv-dot pv-dot--claimed" /> Frontier agent inspection
            </span>
            <strong className="pv-overview-num tabular">
              {matrixSummary.agent_inspection.toLocaleString('en-IN')}
            </strong>
            <span className="pv-overview-sub">
              Qualitative disclosures, WCAG &amp; RTI Sec 4(1)(b)
            </span>
          </button>

          <button
            type="button"
            className={`pv-overview-tile pv-overview-tile--internal${modeFilter === 'internal_governance' ? ' is-active' : ''}`}
            onClick={() => handleToggleMode('internal_governance')}
            aria-pressed={modeFilter === 'internal_governance'}
          >
            <span className="pv-overview-kicker">
              <i className="pv-dot pv-dot--unassess" /> Internal governance
            </span>
            <strong className="pv-overview-num tabular">
              {matrixSummary.internal_governance.toLocaleString('en-IN')}
            </strong>
            <span className="pv-overview-sub">
              Agency processes (public indicators noted)
            </span>
          </button>
        </div>
      ) : null}

      {/* PANEL A: 8-SECTION CATEGORY BREAKDOWN */}
      {sections.length > 0 ? (
        <div className="pv-sections-block" aria-label="Conformity sections breakdown">
          <div className="pv-chart-subhead">
            <h3>
              <Layers size={14} aria-hidden="true" /> Coverage across {sections.length} conformity
              sections
            </h3>
            <span className="pv-chart-note">
              Click any section to filter checks and matrix rows
            </span>
          </div>

          <div className="pv-sections-grid">
            {sections.map((sec) => {
              const isSelected = selectedSectionId === sec.id;
              const isFwDimmed =
                frameworkFilter !== 'all' && classifyFramework(sec.framework) !== frameworkFilter;
              const total = Math.max(1, sec.total_checkpoints);
              const autoPct = calcPct(sec.automated_count, total);
              const agentPct = calcPct(sec.agent_count, total);
              const intPct = calcPct(sec.internal_count, total);

              return (
                <button
                  key={sec.id}
                  type="button"
                  className={`pv-section-card${isSelected ? ' is-selected' : ''}${isFwDimmed ? ' is-dimmed' : ''}`}
                  onClick={() => handleSelectSection(sec.id)}
                  aria-pressed={isSelected}
                >
                  <div className="pv-section-top">
                    <span className="pv-section-fw">{sec.framework}</span>
                    <span className="pv-section-count tabular">{sec.total_checkpoints}</span>
                  </div>
                  <div className="pv-section-name">{sec.name}</div>
                  <div
                    className="pv-section-bar"
                    aria-hidden="true"
                    title={`${sec.automated_count} automated, ${sec.agent_count} frontier agent, ${sec.internal_count} internal`}
                  >
                    {sec.automated_count > 0 ? (
                      <span
                        className="pv-section-seg pv-section-seg--auto"
                        style={{ width: `${autoPct}%` }}
                      />
                    ) : null}
                    {sec.agent_count > 0 ? (
                      <span
                        className="pv-section-seg pv-section-seg--agent"
                        style={{ width: `${agentPct}%` }}
                      />
                    ) : null}
                    {sec.internal_count > 0 ? (
                      <span
                        className="pv-section-seg pv-section-seg--internal"
                        style={{ width: `${intPct}%` }}
                      />
                    ) : null}
                  </div>
                  <div className="pv-section-foot tabular">
                    <span>
                      {sec.automated_count} auto · {sec.agent_count} agent
                      {sec.internal_count > 0 ? ` · ${sec.internal_count} gov` : ''}
                    </span>
                    <strong className={sec.avg_observed_pct != null ? 'pv-section-obs' : 'pv-section-pending'}>
                      {sec.avg_observed_pct != null ? `${fmtPct(sec.avg_observed_pct)} obs` : 'Pending'}
                    </strong>
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      ) : null}

      {/* PANEL B: AUTOMATED W1 CHECKS STACKED BAR CHART */}
      <div className="pv-chart-card pv-w1-panel">
        <div className="pv-chart-subhead">
          <div className="pv-w1-subhead-title">
            <h3>
              Automated HTTP/HTML/TLS checks ({filteredCheckpoints.length}
              {filteredCheckpoints.length !== checkpoints.length ? ` of ${checkpoints.length}` : ''})
            </h3>
            <span className="pv-chart-note">
              Deterministic read-only checks run across every reachable .gov.in host in each batch
            </span>
          </div>

          <div className="pv-gigw-legend">
            <span>
              <i className="pv-dot pv-dot--verified" /> {foundTag.label}
            </span>
            <span>
              <i className="pv-dot pv-dot--not-obs" /> {missingTag.label}
            </span>
            <span>
              <i className="pv-dot pv-dot--unassess" /> {unreachableTag.label}
            </span>
            {hasAnyNotApplicable ? (
              <span>
                <i className="pv-dot pv-dot--review" /> Exempt / N/A
              </span>
            ) : null}
          </div>
        </div>

        {w1Sections.length > 1 ? (
          <div className="pv-w1-filters" role="group" aria-label="Filter automated checks by section">
            <button
              type="button"
              className={`btn btn-xs ${selectedSectionId === 'all' && frameworkFilter === 'all' ? 'btn-primary' : 'btn-ghost'}`}
              onClick={() => {
                setSelectedSectionId('all');
                setFrameworkFilter('all');
              }}
            >
              All ({checkpoints.length})
            </button>
            {w1Sections.map((sec) => (
              <button
                key={sec.id}
                type="button"
                className={`btn btn-xs ${selectedSectionId === sec.id ? 'btn-primary' : 'btn-ghost'}`}
                onClick={() => handleSelectSection(sec.id)}
              >
                {sec.short_name || sec.name}
              </button>
            ))}
          </div>
        ) : null}

        {!hasObservations ? (
          <div className="pv-zero-banner" style={{ marginBottom: 12 }}>
            <strong>0 of {batchesTotal.toLocaleString('en-IN')} batches reported yet.</strong> Stacked{' '}
            {foundTag.label} / {missingTag.label} / {unreachableTag.label} percentages across all{' '}
            {checkpoints.length} automated HTTP/HTML/TLS checks fill in automatically as volunteers submit
            website batches. Hover any check below to see what the reader verifies.
          </div>
        ) : null}

        {visibleCheckpoints.length > 0 ? (
          <div className="pv-gigw-chart" onMouseLeave={() => setHoveredCell(null)}>
            {visibleCheckpoints.map((cp) => {
              const isRowHovered = hoveredCell?.key === cp.key;
              const naCount = cp.not_applicable ?? 0;
              const rowTotal = cp.observed + cp.not_observed + cp.unassessable + naCount;
              const obsPct = rowTotal > 0 ? calcPct(cp.observed, rowTotal) : cp.observed_pct;
              const notObsPct = rowTotal > 0 ? calcPct(cp.not_observed, rowTotal) : cp.not_observed_pct;
              const unassPct = rowTotal > 0 ? calcPct(cp.unassessable, rowTotal) : cp.unassessable_pct;
              const naPct = rowTotal > 0 ? calcPct(naCount, rowTotal) : (cp.not_applicable_pct ?? 0);
              const cpHint =
                cp.description || hintByKey[cp.key] || 'Checked on landing page and linked public pages';
              const cpIds = cp.checkpoint_ids && cp.checkpoint_ids.length > 0 ? cp.checkpoint_ids : null;

              return (
                <div
                  key={cp.key}
                  className={`pv-gigw-row${isRowHovered ? ' is-hovered' : ''}`}
                  onMouseEnter={() => {
                    if (!hasObservations) setHoveredCell({ key: cp.key, segment: 'row' });
                  }}
                >
                  {isRowHovered ? (
                    <div className="pv-tooltip pv-tooltip--bar" role="tooltip">
                      <div className="pv-tooltip-title">
                        {cp.label}
                        {cpIds ? ` (${cpIds.slice(0, 4).join(', ')}${cpIds.length > 4 ? '…' : ''})` : ''}
                      </div>
                      {hasObservations && rowTotal > 0 ? (
                        <div className="pv-tooltip-row tabular">
                          <span className={hoveredCell?.segment === 'observed' ? 'is-emph' : ''}>
                            {foundTag.label}: {cp.observed.toLocaleString('en-IN')} ({fmtPct(obsPct)})
                          </span>
                          <span>·</span>
                          <span className={hoveredCell?.segment === 'not_observed' ? 'is-emph' : ''}>
                            {missingTag.label}: {cp.not_observed.toLocaleString('en-IN')} (
                            {fmtPct(notObsPct)})
                          </span>
                          <span>·</span>
                          <span className={hoveredCell?.segment === 'unassessable' ? 'is-emph' : ''}>
                            {unreachableTag.label}: {cp.unassessable.toLocaleString('en-IN')} (
                            {fmtPct(unassPct)})
                          </span>
                          {naCount > 0 ? (
                            <>
                              <span>·</span>
                              <span className={hoveredCell?.segment === 'not_applicable' ? 'is-emph' : ''}>
                                Exempt/N/A: {naCount.toLocaleString('en-IN')} ({fmtPct(naPct)})
                              </span>
                            </>
                          ) : null}
                        </div>
                      ) : (
                        <div className="pv-tooltip-row">
                          {cpHint} · 0.0% {foundTag.label.toLowerCase()} so far
                        </div>
                      )}
                    </div>
                  ) : null}

                  <div className="pv-gigw-label" title={cpHint}>
                    <span>{cp.label}</span>
                    {cp.framework ? <span className="pv-gigw-sub">{cp.framework}</span> : null}
                  </div>

                  {hasObservations && rowTotal > 0 ? (
                    <div
                      className="pv-gigw-track"
                      role="group"
                      aria-label={`${cp.label}: ${fmtPct(obsPct)} ${foundTag.label}, ${fmtPct(notObsPct)} ${missingTag.label}, ${fmtPct(unassPct)} ${unreachableTag.label}${naCount > 0 ? `, ${fmtPct(naPct)} exempt or N/A` : ''}`}
                    >
                      {cp.observed > 0 ? (
                        <button
                          type="button"
                          className={`pv-gigw-seg pv-gigw-seg--obs${hoveredCell?.key === cp.key && hoveredCell.segment === 'observed' ? ' is-active' : ''}`}
                          style={{ width: `${obsPct}%` }}
                          onPointerEnter={(e) => {
                            if (e.pointerType === 'mouse') {
                              setHoveredCell({ key: cp.key, segment: 'observed' });
                            }
                          }}
                          onClick={() =>
                            setHoveredCell((prev) =>
                              prev?.key === cp.key && prev.segment === 'observed'
                                ? null
                                : { key: cp.key, segment: 'observed' },
                            )
                          }
                          onFocus={() => setHoveredCell({ key: cp.key, segment: 'observed' })}
                          onBlur={() => setHoveredCell(null)}
                          aria-label={`${cp.label} ${foundTag.label}: ${cp.observed} hosts (${fmtPct(obsPct)})`}
                        />
                      ) : null}
                      {cp.not_observed > 0 ? (
                        <button
                          type="button"
                          className={`pv-gigw-seg pv-gigw-seg--notobs${hoveredCell?.key === cp.key && hoveredCell.segment === 'not_observed' ? ' is-active' : ''}`}
                          style={{ width: `${notObsPct}%` }}
                          onPointerEnter={(e) => {
                            if (e.pointerType === 'mouse') {
                              setHoveredCell({ key: cp.key, segment: 'not_observed' });
                            }
                          }}
                          onClick={() =>
                            setHoveredCell((prev) =>
                              prev?.key === cp.key && prev.segment === 'not_observed'
                                ? null
                                : { key: cp.key, segment: 'not_observed' },
                            )
                          }
                          onFocus={() => setHoveredCell({ key: cp.key, segment: 'not_observed' })}
                          onBlur={() => setHoveredCell(null)}
                          aria-label={`${cp.label} ${missingTag.label}: ${cp.not_observed} hosts (${fmtPct(notObsPct)})`}
                        />
                      ) : null}
                      {cp.unassessable > 0 ? (
                        <button
                          type="button"
                          className={`pv-gigw-seg pv-gigw-seg--unass${hoveredCell?.key === cp.key && hoveredCell.segment === 'unassessable' ? ' is-active' : ''}`}
                          style={{ width: `${unassPct}%` }}
                          onPointerEnter={(e) => {
                            if (e.pointerType === 'mouse') {
                              setHoveredCell({ key: cp.key, segment: 'unassessable' });
                            }
                          }}
                          onClick={() =>
                            setHoveredCell((prev) =>
                              prev?.key === cp.key && prev.segment === 'unassessable'
                                ? null
                                : { key: cp.key, segment: 'unassessable' },
                            )
                          }
                          onFocus={() => setHoveredCell({ key: cp.key, segment: 'unassessable' })}
                          onBlur={() => setHoveredCell(null)}
                          aria-label={`${cp.label} ${unreachableTag.label}: ${cp.unassessable} hosts (${fmtPct(unassPct)})`}
                        />
                      ) : null}
                      {naCount > 0 ? (
                        <button
                          type="button"
                          className={`pv-gigw-seg pv-gigw-seg--na${hoveredCell?.key === cp.key && hoveredCell.segment === 'not_applicable' ? ' is-active' : ''}`}
                          style={{ width: `${naPct}%` }}
                          onPointerEnter={(e) => {
                            if (e.pointerType === 'mouse') {
                              setHoveredCell({ key: cp.key, segment: 'not_applicable' });
                            }
                          }}
                          onClick={() =>
                            setHoveredCell((prev) =>
                              prev?.key === cp.key && prev.segment === 'not_applicable'
                                ? null
                                : { key: cp.key, segment: 'not_applicable' },
                            )
                          }
                          onFocus={() => setHoveredCell({ key: cp.key, segment: 'not_applicable' })}
                          onBlur={() => setHoveredCell(null)}
                          aria-label={`${cp.label} exempt or not applicable: ${naCount} hosts (${fmtPct(naPct)})`}
                        />
                      ) : null}
                    </div>
                  ) : (
                    <div
                      className="pv-gigw-track pv-gigw-track--skeleton"
                      tabIndex={0}
                      role="note"
                      aria-label={`${cp.label}: 0.0% ${foundTag.label}, waiting for first batch`}
                      onFocus={() => setHoveredCell({ key: cp.key, segment: 'row' })}
                      onBlur={() => setHoveredCell(null)}
                    >
                      <span className="pv-gigw-skeleton-hint">Awaiting first batch</span>
                    </div>
                  )}

                  <div className="pv-gigw-val tabular">
                    <strong>{fmtPct(obsPct)}</strong>
                    <span>{foundTag.label}</span>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="pv-empty-box">
            No automated HTTP/HTML/TLS checks map directly to this filter — inspect the full conformity matrix
            below for frontier-agent and governance checkpoints.
          </div>
        )}

        {selectedSectionId === 'all' &&
        frameworkFilter === 'all' &&
        filteredCheckpoints.length > W1_INITIAL_CHECKS ? (
          <div className="pv-list-more">
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => setShowAllW1((v) => !v)}
            >
              {showAllW1
                ? `Show fewer automated checks`
                : `Show all ${filteredCheckpoints.length} automated checks`}
            </button>
          </div>
        ) : null}

        {/* Access-Limitation Summary Pills */}
        <div className="pv-reasons-bar" style={{ marginTop: 14 }}>
          <span className="kicker">Why sites were {unreachableTag.label.toLowerCase()}</span>
          {reasonEntries.length > 0 ? (
            <div className="pv-reason-pills" onMouseLeave={() => setHoveredReason(null)}>
              {reasonEntries.map(([key, count]) => {
                const shareOfHosts = calcPct(count, Math.max(1, hostsTotal));
                const shareOfUnass = calcPct(count, Math.max(1, hostsUnassessable));
                return (
                  <span
                    key={key}
                    className="pv-reason-pill tabular"
                    tabIndex={0}
                    role="note"
                    onPointerEnter={(e) => {
                      if (e.pointerType === 'mouse') setHoveredReason(key);
                    }}
                    onClick={() => setHoveredReason((prev) => (prev === key ? null : key))}
                    onFocus={() => setHoveredReason(key)}
                    onBlur={() => setHoveredReason(null)}
                  >
                    {hoveredReason === key ? (
                      <span className="pv-tooltip pv-tooltip--pill" role="tooltip">
                        {formatReasonLabel(key)}: {count.toLocaleString('en-IN')} hosts (
                        {fmtPct(shareOfHosts)} of total · {fmtPct(shareOfUnass)} of{' '}
                        {unreachableTag.label.toLowerCase()})
                      </span>
                    ) : null}
                    <span>{formatReasonLabel(key)}</span>
                    <strong>{count.toLocaleString('en-IN')}</strong>
                  </span>
                );
              })}
            </div>
          ) : (
            <span className="pv-reasons-empty">
              No {unreachableTag.label.toLowerCase()} sites logged yet (robots.txt blocks, timeouts,
              and unavailable contact pages are tracked separately from {missingTag.label.toLowerCase()}{' '}
              items).
            </span>
          )}
        </div>
      </div>

      {/* PANEL C: EXHAUSTIVE SEARCHABLE & EXPANDABLE CONFORMITY MATRIX */}
      {matrixItems.length > 0 ? (
        <div className="pv-chart-card pv-matrix-explorer" aria-label="Exhaustive conformity matrix explorer">
          <div className="pv-matrix-head">
            <div>
              <h3 className="pv-matrix-title">
                Exhaustive conformity checklist ({filteredMatrix.length.toLocaleString('en-IN')} of{' '}
                {matrixItems.length.toLocaleString('en-IN')})
              </h3>
              <p className="pv-matrix-sub">
                Every official GIGW 3.0 Annexure II &amp; Guidelines checkpoint, RTI Act 2005
                proactive disclosure clause, and GoI statutory/policy requirement checked across
                the .gov.in directory. Click any row for full requirement text, citations, mapped
                automated checks, and legal caveats.
              </p>
            </div>
            {web?.matrix_endpoint ? (
              <a href={web.matrix_endpoint} className="inline-link pv-matrix-json-link">
                Raw matrix JSON
              </a>
            ) : null}
          </div>

          {/* Framework tabs + Verification Mode pills + Section dropdown + Search */}
          <div className="pv-matrix-toolbar">
            <div className="pv-matrix-tabs" role="group" aria-label="Filter by legal framework">
              {(
                [
                  ['all', `All (${matrixSummary?.total_checkpoints ?? matrixItems.length})`],
                  ['gigw', `GIGW 3.0 (${matrixSummary?.gigw_checkpoints ?? 88})`],
                  ['rti', `RTI Act 2005 (${matrixSummary?.rti_checkpoints ?? 42})`],
                  ['statutory', `Statutory & Policy (${matrixSummary?.statutory_other_checkpoints ?? 29})`],
                ] as const
              ).map(([fwKey, fwLabel]) => (
                <button
                  key={fwKey}
                  type="button"
                  aria-pressed={frameworkFilter === fwKey}
                  className={`btn btn-xs ${frameworkFilter === fwKey ? 'btn-primary' : 'btn-ghost'}`}
                  onClick={() => handleSelectFramework(fwKey)}
                >
                  {fwLabel}
                </button>
              ))}
            </div>

            <div className="pv-matrix-controls">
              <div className="pv-matrix-search-wrap">
                <Search size={13} className="pv-matrix-search-icon" aria-hidden="true" />
                <input
                  type="search"
                  className="input-field pv-matrix-search"
                  placeholder="Search ID (GIGW-Q-01, RTI-4-1-b-xvi), WCAG, risk code, or requirement…"
                  aria-label="Search conformity checkpoints"
                  value={matrixQuery}
                  onChange={(e) => {
                    setMatrixQuery(e.target.value);
                    setShowAllMatrix(false);
                  }}
                />
              </div>

              <select
                className="select-field pv-matrix-select"
                aria-label="Filter by conformity section"
                value={selectedSectionId}
                onChange={(e) => {
                  setSelectedSectionId(e.target.value);
                  setShowAllMatrix(false);
                }}
              >
                <option value="all">All {sections.length} sections</option>
                {sections.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name} ({s.total_checkpoints})
                  </option>
                ))}
              </select>

              <select
                className="select-field pv-matrix-select"
                aria-label="Filter by verification mode"
                value={modeFilter}
                onChange={(e) => {
                  setModeFilter(e.target.value as ModeFilter);
                  setShowAllMatrix(false);
                }}
              >
                <option value="all">All verification modes</option>
                <option value="automated_http_html">
                  Automated HTTP/HTML ({matrixSummary?.automated_http_html ?? ''})
                </option>
                <option value="agent_inspection">
                  Frontier agent ({matrixSummary?.agent_inspection ?? ''})
                </option>
                <option value="internal_governance">
                  Internal governance ({matrixSummary?.internal_governance ?? ''})
                </option>
              </select>

              {hasActiveMatrixFilters ? (
                <button
                  type="button"
                  className="btn btn-ghost btn-xs"
                  onClick={handleResetFilters}
                >
                  <RotateCcw size={12} aria-hidden="true" />
                  <span>Reset</span>
                </button>
              ) : null}
            </div>
          </div>

          {visibleMatrix.length > 0 ? (
            <div className="pv-matrix-list">
              {visibleMatrix.map((item) => {
                const modeMeta =
                  VERIFICATION_MODE_META[item.verification_mode] ??
                  VERIFICATION_MODE_META.agent_inspection;
                const safeSrc = safeExternalUrl(item.source_url);
                const covLabel =
                  AUTOMATION_COVERAGE_LABELS[item.automation_coverage] || item.automation_coverage;

                return (
                  <details key={item.id} className="pv-matrix-item">
                    <summary className="pv-matrix-summary">
                      <span className="pv-matrix-id font-mono">{item.id}</span>
                      <div className="pv-matrix-main">
                        <span className="pv-matrix-item-title">{item.title}</span>
                        <span className="pv-matrix-item-meta">
                          {item.section_name}
                          {item.citation ? ` · ${item.citation}` : ''}
                        </span>
                      </div>
                      <div className="pv-matrix-badges">
                        {item.wcag_level ? (
                          <span className="tag-mono pv-wcag-pill">WCAG {item.wcag_level}</span>
                        ) : null}
                        <span className={modeMeta.badgeClass} title={modeMeta.hint}>
                          {modeMeta.label}
                        </span>
                        <span className="pv-matrix-obs tabular">
                          {item.observed_pct != null
                            ? `${fmtPct(item.observed_pct)}`
                            : item.verification_mode === 'automated_http_html'
                              ? 'Pending'
                              : modeMeta.shortLabel}
                        </span>
                      </div>
                    </summary>

                    <div className="pv-matrix-detail">
                      <p className="pv-matrix-req">{item.requirement_text}</p>

                      <dl className="pv-matrix-dl">
                        <div>
                          <dt>Framework &amp; citation</dt>
                          <dd>
                            <span>
                              <strong>{item.framework}</strong>
                              {item.citation ? ` — ${item.citation}` : ''}
                            </span>
                            {safeSrc ? (
                              <a
                                href={safeSrc}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="inline-link pv-matrix-source-link"
                              >
                                <span>Official source</span>
                                <ExternalLink size={11} aria-hidden="true" />
                              </a>
                            ) : null}
                          </dd>
                        </div>

                        {item.conformance_level || item.wcag_reference ? (
                          <div>
                            <dt>Conformance</dt>
                            <dd>
                              {[item.conformance_level, item.wcag_reference]
                                .filter(Boolean)
                                .join(' · ')}
                            </dd>
                          </div>
                        ) : null}

                        <div>
                          <dt>Verification coverage</dt>
                          <dd>
                            <span>{covLabel}</span>
                            {item.w1_check_keys.length > 0 ? (
                              <span className="pv-matrix-keys">
                                {item.w1_check_keys.map((k) => (
                                  <code key={k} className="tag-mono">
                                    {k}
                                  </code>
                                ))}
                              </span>
                            ) : null}
                          </dd>
                        </div>

                        {item.risks_addressed && item.risks_addressed.length > 0 ? (
                          <div>
                            <dt>GIGW risk codes</dt>
                            <dd className="pv-matrix-keys">
                              {item.risks_addressed.map((r) => (
                                <span key={r} className="tag-mono">
                                  {r}
                                </span>
                              ))}
                            </dd>
                          </div>
                        ) : null}

                        {item.public_disclosure_indicator ? (
                          <div>
                            <dt>Public indicator</dt>
                            <dd>{item.public_disclosure_indicator}</dd>
                          </div>
                        ) : null}

                        {item.applicability ? (
                          <div>
                            <dt>Applies to</dt>
                            <dd>
                              {formatApplicabilityLabel(item.applicability)}
                              {item.applicable_hosts_assessed != null &&
                              item.applicable_hosts_assessed > 0
                                ? ` (${item.applicable_hosts_assessed.toLocaleString('en-IN')} applicable ${item.applicable_hosts_assessed === 1 ? 'host' : 'hosts'} assessed)`
                                : ''}
                            </dd>
                          </div>
                        ) : null}

                        {item.caveats ? (
                          <div>
                            <dt>Guardrails &amp; caveats</dt>
                            <dd>{item.caveats}</dd>
                          </div>
                        ) : null}
                      </dl>
                    </div>
                  </details>
                );
              })}
            </div>
          ) : (
            <div className="pv-empty-box">
              No conformity checkpoints match your active filters. Try clearing your search or
              filters above.
            </div>
          )}

          {filteredMatrix.length > MATRIX_INITIAL_ROWS ? (
            <div className="pv-list-more">
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => setShowAllMatrix((v) => !v)}
              >
                {showAllMatrix
                  ? 'Show fewer checkpoints'
                  : `Show all ${filteredMatrix.length.toLocaleString('en-IN')} matching checkpoints`}
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

/* ---------- 3C. UB1 Union Budget lines: budget estimate vs revised estimate vs actual ---------- */

const UB1_METHOD_URL =
  'https://github.com/aiwatchdogsindia/aiwatchdogs.in/blob/main/projects/union-budget-unspent-funds/METHODOLOGY.md';

/** "FY2019-20" -> "2019-20". */
function plainFiscalYear(fy: string): string {
  return fy.replace(/^FY\s*/i, '');
}

function fmtCount(n: number | null | undefined): string {
  return Number.isFinite(n) ? Number(n).toLocaleString('en-IN') : '0';
}

function UnionBudgetVisualizer({
  mission,
  visualSummary,
  onSelectFinding,
  onFilterTasks,
}: {
  mission: MissionItem;
  visualSummary?: ProjectVisualSummary;
  onSelectFinding?: (id: string | number) => void;
  onFilterTasks?: (text: string) => void;
}) {
  const ub = visualSummary?.union_budget;
  const workTag = getWorkTypeTag(undefined, mission.methodology_code);
  const slugPath = encodeURIComponent(mission.slug);

  const years = ub?.years ?? [];
  const topLeads = ub?.top_leads ?? [];
  const yearCount = ub?.financial_years?.length || years.length;
  const firstYear = ub?.financial_years?.[0] ?? years[0]?.year;
  const lastYear =
    ub?.financial_years?.[ub.financial_years.length - 1] ?? years[years.length - 1]?.year;
  const leads = ub?.leads ?? 0;
  const belowCr = ub?.below_estimate_cr ?? 0;
  const verified = ub?.verified ?? mission.verified_findings_count ?? 0;
  const inReview = ub?.peer_review ?? mission.peer_review_count ?? 0;
  const documentsUrl = ub?.documents_url || `/api/missions/${slugPath}/documents.csv`;
  const coverageUrl = ub?.coverage_url || `/api/missions/${slugPath}/coverage.json`;
  const maxYearBelow = Math.max(1, ...years.map((y) => y.below_estimate_cr || 0));

  return (
    <section className="card card-pad pv-results" aria-label="Union Budget results">
      <div className="pv-results-head">
        <div className="pv-results-title-wrap">
          <span className="kicker">
            <BarChart3 size={12} aria-hidden="true" />
            Live results · {workTag.label}
          </span>
          <h2 className="pv-results-title">Budgeted and spent, line by line</h2>
        </div>

        <div className="pv-summary-pills tabular">
          <span className="tag-mono">{fmtCount(ub?.lines)} budget lines</span>
          <span className="tag-mono">{fmtCount(ub?.statements)} statements read</span>
          {yearCount > 0 ? (
            <span className="tag-mono">
              {yearCount} financial years
              {firstYear && lastYear ? ` (${firstYear} to ${lastYear})` : ''}
            </span>
          ) : null}
          <span className="tag-mono">
            {fmtCount(leads)} {leads === 1 ? 'lead' : 'leads'} · {formatInrCr(belowCr)} below estimate
            so far
          </span>
          {verified > 0 ? (
            <span className={FINDING_TAGS.verified.badgeClass}>
              {fmtCount(verified)} {FINDING_TAGS.verified.label}
            </span>
          ) : null}
          {inReview > 0 ? (
            <span className={FINDING_TAGS.in_review.badgeClass}>
              {fmtCount(inReview)} {FINDING_TAGS.in_review.label}
            </span>
          ) : null}
        </div>
      </div>

      <div className="pv-results-split">
        {/* Year-by-year progress */}
        <div className="pv-chart-card">
          <div className="pv-chart-subhead">
            <h3>Year by year</h3>
            <div className="pv-mini-legend">
              <span>
                <i className="pv-dot pv-dot--verified" aria-hidden="true" /> {TASK_TAGS.done.label}
              </span>
              <span>
                <i className="pv-dot pv-dot--claimed" aria-hidden="true" /> {TASK_TAGS.in_progress.label}
              </span>
              <span>
                <i className="pv-dot pv-dot--open" aria-hidden="true" /> {TASK_TAGS.open.label}
              </span>
            </div>
          </div>

          {years.length > 0 ? (
            <div className="pv-ub-years-wrap">
              <table className="pv-ub-table pv-ub-years">
                <caption className="pv-ub-caption">
                  Checks done per financial year, budget lines read and the amount reported below the
                  budget estimate so far.
                  {onFilterTasks ? ' Choose a year to list its tasks.' : ''}
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Year</th>
                    <th scope="col">Checked</th>
                    <th scope="col" className="num">
                      Lines
                    </th>
                    <th scope="col" className="num">
                      Below estimate
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {years.map((y) => {
                    const total = Math.max(0, y.tasks);
                    const donePct = calcPct(y.done, total);
                    const claimedPct = calcPct(y.claimed, total);
                    const isRevised = y.compares === 'revised';
                    return (
                      <tr key={y.year}>
                        <th scope="row" className="pv-ub-year">
                          {onFilterTasks ? (
                            <button
                              type="button"
                              className="pv-ub-link"
                              onClick={() => onFilterTasks(y.year)}
                              aria-label={`Show tasks for ${y.year}`}
                            >
                              {y.year}
                            </button>
                          ) : (
                            <span>{y.year}</span>
                          )}
                          {isRevised ? (
                            <span className="pv-ub-note">
                              {y.actuals_reason === 'not_published'
                                ? 'Revised vs budget; actuals not published yet'
                                : y.actuals_reason === 'unmatched'
                                  ? 'Revised vs budget; actuals not matched after regrouping'
                                  : 'Revised vs budget; actuals unavailable or unmatched'}
                            </span>
                          ) : null}
                        </th>
                        <td className="pv-ub-years-checked">
                          <span
                            className="pv-ub-bar"
                            role="img"
                            aria-label={`${fmtCount(y.done)} of ${fmtCount(total)} checks ${TASK_TAGS.done.label}, ${fmtCount(y.claimed)} ${TASK_TAGS.in_progress.label}`}
                          >
                            {donePct > 0 ? (
                              <span
                                className="pv-ub-bar-seg pv-ub-bar-seg--done"
                                style={{ width: `${Math.max(donePct, 1.5)}%` }}
                              />
                            ) : null}
                            {claimedPct > 0 ? (
                              <span
                                className="pv-ub-bar-seg pv-ub-bar-seg--claimed"
                                style={{ width: `${Math.max(claimedPct, 1.5)}%` }}
                              />
                            ) : null}
                          </span>
                          <span className="pv-ub-sub tabular">
                            {fmtCount(y.done)}/{fmtCount(total)} · {fmtPct(donePct)}
                            {y.claimed > 0 ? ` · ${fmtCount(y.claimed)} running` : ''}
                          </span>
                        </td>
                        <td className="num pv-ub-years-lines" data-label="Lines">
                          <span className="pv-ub-cell-val">{fmtCount(y.lines)}</span>
                          {!isRevised && y.lines > 0 ? (
                            <span
                              className="pv-ub-sub"
                              title="Lines traced to their actual expenditure in the next year's budget"
                            >
                              {fmtCount(y.with_actuals)} with actuals
                            </span>
                          ) : null}
                        </td>
                        <td className="num pv-ub-years-below" data-label="Below estimate">
                          <span className="pv-ub-cell-val">
                            {y.below_estimate_cr > 0 ? formatInrCr(y.below_estimate_cr) : '—'}
                          </span>
                          <span className="pv-ub-sub">
                            {fmtCount(y.leads)} {y.leads === 1 ? 'lead' : 'leads'}
                          </span>
                          {y.below_estimate_cr > 0 ? (
                            <span className="pv-ub-mini-track" aria-hidden="true">
                              <span
                                className="pv-ub-mini-fill"
                                style={{
                                  width: `${Math.max(3, (y.below_estimate_cr / maxYearBelow) * 100)}%`,
                                }}
                              />
                            </span>
                          ) : null}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="pv-empty-box">
              Year-by-year progress appears here once the hub publishes this project&apos;s coverage.
            </div>
          )}
        </div>

        {/* How to read this + public files */}
        <div className="pv-chart-card pv-ub-guide">
          <div className="pv-chart-subhead">
            <h3>How to read this</h3>
          </div>
          <p>
            Each check reads one numbered line of the Union Budget&apos;s Notes on Demands for Grants
            from two official PDFs: the budget and revised estimates from one year&apos;s budget, and
            the actual expenditure from the next. A lead is published when 10% or more of the line&apos;s
            budget estimate, and at least {formatInrCr(1)}, was not spent.
          </p>
          <p>
            A lead compares figures printed by the Ministry of Finance. It is not a finding of
            wrongdoing. Ordinary reasons include late approvals, lower demand, money re-appropriated
            to another line, or a scheme merged into another line.
          </p>
          <p>Two other agents re-check every lead before it is marked {FINDING_TAGS.verified.label}.</p>
          <div className="pv-ub-links">
            <a className="inline-link" href={documentsUrl}>
              Statements used (CSV)
            </a>
            <a className="inline-link" href={coverageUrl}>
              Coverage (JSON)
            </a>
            <a className="inline-link" href={UB1_METHOD_URL} target="_blank" rel="noopener noreferrer">
              Method <ExternalLink size={11} aria-hidden="true" className="pv-ub-ext" />
            </a>
          </div>
          {ub?.generated_on ? (
            <p className="pv-chart-note">Statement index built {ub.generated_on}.</p>
          ) : null}
        </div>
      </div>

      {/* Largest shortfalls */}
      <div className="pv-ub-leads">
        <div className="pv-chart-subhead">
          <h3>Largest shortfalls reported so far</h3>
          {topLeads.length > 0 ? (
            <span className="pv-chart-note">₹ crore, as printed in the budget papers</span>
          ) : null}
        </div>
        {topLeads.length > 0 ? (
          <>
            <p className="pv-scroll-hint pv-scroll-hint--tablet-only" aria-hidden="true">
              Swipe table horizontally for estimates, actuals &amp; status →
            </p>
            <div className="table-wrap pv-ub-leads-wrap">
            <table className="ledger-table pv-ub-table pv-ub-leads-table">
              <caption className="pv-ub-caption">
                Budget lines with the largest amount reported below their budget estimate.
                {onSelectFinding ? ' Open a line to see its evidence.' : ''}
              </caption>
              <thead>
                <tr>
                  <th scope="col">Budget line</th>
                  <th scope="col">Year</th>
                  <th scope="col" className="num">
                    Budget estimate
                  </th>
                  <th scope="col" className="num">
                    Revised estimate
                  </th>
                  <th scope="col" className="num">
                    Actual
                  </th>
                  <th scope="col" className="num">
                    Below estimate
                  </th>
                  <th scope="col">Status</th>
                </tr>
              </thead>
              <tbody>
                {topLeads.map((lead) => {
                  const tag = getFindingTag(lead.status);
                  const noActuals = lead.compare === 'revised_vs_budget_estimate';
                  const noActualsLabel =
                    lead.actuals_reason === 'not_published'
                      ? 'not published yet'
                      : lead.actuals_reason === 'unmatched'
                        ? 'not matched'
                        : 'not available';
                  const noActualsTitle =
                    lead.actuals_reason === 'not_published'
                      ? 'Actuals not published yet'
                      : lead.actuals_reason === 'unmatched'
                        ? 'Actuals were published but this line could not be matched after budget regrouping'
                        : 'Actuals not published yet or line could not be matched';
                  return (
                    <tr key={lead.finding_id}>
                      <td className="pv-ub-line">
                        {onSelectFinding ? (
                          <button
                            type="button"
                            className="pv-ub-link"
                            onClick={() => onSelectFinding(lead.finding_id)}
                          >
                            {lead.entity_name || lead.title}
                          </button>
                        ) : (
                          <span>{lead.entity_name || lead.title}</span>
                        )}
                        {lead.entity_name && lead.title && lead.title !== lead.entity_name ? (
                          <span className="pv-ub-sub">{lead.title}</span>
                        ) : null}
                      </td>
                      <td className="tabular pv-ub-lead-year" data-label="Year">
                        {plainFiscalYear(lead.fiscal_year)}
                      </td>
                      <td className="num pv-ub-lead-be" data-label="Budget est.">
                        {formatInrCr(lead.budget_estimate_cr)}
                      </td>
                      <td className="num pv-ub-lead-re" data-label="Revised est.">
                        {formatInrCr(lead.revised_estimate_cr)}
                      </td>
                      <td
                        className="num pv-ub-lead-actual"
                        data-label="Actual"
                        title={noActuals ? noActualsTitle : undefined}
                      >
                        <span className="pv-ub-cell-val">{formatInrCr(lead.actual_cr)}</span>
                        {noActuals ? <span className="pv-ub-sub">{noActualsLabel}</span> : null}
                      </td>
                      <td className="num pv-ub-lead-below" data-label="Below est.">
                        <strong>{formatInrCr(lead.below_estimate_cr)}</strong>
                        {lead.below_estimate_pct != null ? (
                          <span className="pv-ub-sub">{lead.below_estimate_pct.toFixed(1)}%</span>
                        ) : null}
                      </td>
                      <td className="pv-ub-lead-status">
                        <span className={tag.badgeClass} title={tag.description}>
                          {tag.label}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            </div>
          </>
        ) : (
          <div className="pv-empty-box">
            Leads appear here as agents finish checks. Each one cites the page and printed row of both
            budget documents.
          </div>
        )}
      </div>
    </section>
  );
}

/* ---------- 3D. OR1 One Road, Two Bills: Union duplicate billing & dual-agency claims ---------- */

const OR1_METHOD_URL =
  'https://github.com/aiwatchdogsindia/aiwatchdogs.in/blob/main/projects/one-road-two-bills/METHODOLOGY.md';

function signalLabel(signal: string): string {
  if (signal === 'duplicate_payment_or_billing') return 'Duplicate billing / paid twice';
  if (signal === 'dual_agency_or_scheme_overlap') return 'Dual-agency / scheme overlap';
  return signal || 'Audit lead';
}

function OneRoadTwoBillsVisualizer({
  mission,
  visualSummary,
  onSelectFinding,
  onFilterTasks,
}: {
  mission: MissionItem;
  visualSummary?: ProjectVisualSummary;
  onSelectFinding?: (id: string | number) => void;
  onFilterTasks?: (text: string) => void;
}) {
  const or1 = visualSummary?.one_road_two_bills;
  const workTag = getWorkTypeTag(undefined, mission.methodology_code);
  const slugPath = encodeURIComponent(mission.slug);

  const years = or1?.years ?? [];
  const topLeads = or1?.top_leads ?? [];
  const leads = or1?.leads ?? 0;
  const flaggedCr = or1?.flagged_inr_cr ?? 0;
  const verified = or1?.verified ?? mission.verified_findings_count ?? 0;
  const inReview = or1?.peer_review ?? mission.peer_review_count ?? 0;
  const documentsUrl = or1?.documents_url || `/api/missions/${slugPath}/documents.csv`;
  const coverageUrl = or1?.coverage_url || `/api/missions/${slugPath}/coverage.json`;
  const maxYearFlagged = Math.max(1, ...years.map((y) => y.flagged_inr_cr || 0));

  return (
    <section className="card card-pad pv-results" aria-label="One Road Two Bills results">
      <div className="pv-results-head">
        <div className="pv-results-title-wrap">
          <span className="kicker">
            <BarChart3 size={12} aria-hidden="true" />
            Live results · {workTag.label}
          </span>
          <h2 className="pv-results-title">Duplicate billing &amp; dual-agency claims</h2>
        </div>

        <div className="pv-summary-pills tabular">
          <span className="tag-mono">{fmtCount(or1?.reports)} CAG Union reports</span>
          <span className="tag-mono">{fmtCount(or1?.pages)} pages read</span>
          <span className="tag-mono">{fmtCount(or1?.blocks)} section blocks</span>
          <span className="tag-mono">
            {fmtCount(leads)} {leads === 1 ? 'lead' : 'leads'}
            {flaggedCr > 0 ? ` · ${formatInrCr(flaggedCr)} cited` : ''}
          </span>
          {verified > 0 ? (
            <span className={FINDING_TAGS.verified.badgeClass}>
              {fmtCount(verified)} {FINDING_TAGS.verified.label}
            </span>
          ) : null}
          {inReview > 0 ? (
            <span className={FINDING_TAGS.in_review.badgeClass}>
              {fmtCount(inReview)} {FINDING_TAGS.in_review.label}
            </span>
          ) : null}
        </div>
      </div>

      <div className="pv-results-split">
        <div className="pv-chart-card">
          <div className="pv-chart-subhead">
            <h3>Year by year</h3>
            <div className="pv-mini-legend">
              <span>
                <i className="pv-dot pv-dot--verified" aria-hidden="true" /> {TASK_TAGS.done.label}
              </span>
              <span>
                <i className="pv-dot pv-dot--claimed" aria-hidden="true" /> {TASK_TAGS.in_progress.label}
              </span>
              <span>
                <i className="pv-dot pv-dot--open" aria-hidden="true" /> {TASK_TAGS.open.label}
              </span>
            </div>
          </div>

          {years.length > 0 ? (
            <div className="pv-ub-years-wrap">
              <table className="pv-ub-table pv-ub-years">
                <caption className="pv-ub-caption">
                  Checks done per report year, CAG Union reports and pages covered, and cited amounts so far.
                  {onFilterTasks ? ' Choose a year to list its tasks.' : ''}
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Year</th>
                    <th scope="col">Checked</th>
                    <th scope="col" className="num">
                      Reports / Pages
                    </th>
                    <th scope="col" className="num">
                      Cited amount
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {years.map((y) => {
                    const total = Math.max(0, y.tasks);
                    const donePct = calcPct(y.done, total);
                    const claimedPct = calcPct(y.claimed, total);
                    return (
                      <tr key={y.year}>
                        <th scope="row" className="pv-ub-year">
                          {onFilterTasks ? (
                            <button
                              type="button"
                              className="pv-ub-link"
                              onClick={() => onFilterTasks(y.year)}
                              aria-label={`Show tasks for ${y.year}`}
                            >
                              {y.year}
                            </button>
                          ) : (
                            <span>{y.year}</span>
                          )}
                        </th>
                        <td className="pv-ub-years-checked">
                          <span
                            className="pv-ub-bar"
                            role="img"
                            aria-label={`${fmtCount(y.done)} of ${fmtCount(total)} checks ${TASK_TAGS.done.label}, ${fmtCount(y.claimed)} ${TASK_TAGS.in_progress.label}`}
                          >
                            {donePct > 0 ? (
                              <span
                                className="pv-ub-bar-seg pv-ub-bar-seg--done"
                                style={{ width: `${Math.max(donePct, 1.5)}%` }}
                              />
                            ) : null}
                            {claimedPct > 0 ? (
                              <span
                                className="pv-ub-bar-seg pv-ub-bar-seg--claimed"
                                style={{ width: `${Math.max(claimedPct, 1.5)}%` }}
                              />
                            ) : null}
                          </span>
                          <span className="pv-ub-sub tabular">
                            {fmtCount(y.done)}/{fmtCount(total)} · {fmtPct(donePct)}
                            {y.claimed > 0 ? ` · ${fmtCount(y.claimed)} running` : ''}
                          </span>
                        </td>
                        <td className="num pv-ub-years-lines" data-label="Reports / Pages">
                          <span className="pv-ub-cell-val">{fmtCount(y.reports)} reports</span>
                          <span className="pv-ub-sub">{fmtCount(y.pages)} pages</span>
                        </td>
                        <td className="num pv-ub-years-below" data-label="Cited amount">
                          <span className="pv-ub-cell-val">
                            {y.flagged_inr_cr > 0 ? formatInrCr(y.flagged_inr_cr) : '—'}
                          </span>
                          <span className="pv-ub-sub">
                            {fmtCount(y.leads)} {y.leads === 1 ? 'lead' : 'leads'}
                          </span>
                          {y.flagged_inr_cr > 0 ? (
                            <span className="pv-ub-mini-track" aria-hidden="true">
                              <span
                                className="pv-ub-mini-fill"
                                style={{
                                  width: `${Math.max(3, (y.flagged_inr_cr / maxYearFlagged) * 100)}%`,
                                }}
                              />
                            </span>
                          ) : null}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="pv-empty-box">
              Year-by-year coverage appears here once the hub loads this project&apos;s CAG report index.
            </div>
          )}
        </div>

        <div className="pv-chart-card pv-ub-guide">
          <div className="pv-chart-subhead">
            <h3>How to read this</h3>
          </div>
          <p>
            Each check inspects non-overlapping paragraph blocks from checksummed official CAG Union
            Government Audit Report PDFs from the past two years. It looks for documented irregularities
            where a finished work or bill was paid twice, billed twice, or claimed by two agencies.
          </p>
          <p>
            Legitimate cost-sharing and co-funding (such as 60:40 or 50:50 Centre–State shares, matching
            grants, or pooled funds) are explicitly cleared unless an explicit double-billing irregularity
            is recorded.
          </p>
          <p>Two other agents independently verify every lead before it is marked {FINDING_TAGS.verified.label}.</p>
          <div className="pv-ub-links">
            <a className="inline-link" href={documentsUrl}>
              CAG reports used (CSV)
            </a>
            <a className="inline-link" href={coverageUrl}>
              Coverage (JSON)
            </a>
            <a className="inline-link" href={OR1_METHOD_URL} target="_blank" rel="noopener noreferrer">
              Method <ExternalLink size={11} aria-hidden="true" className="pv-ub-ext" />
            </a>
          </div>
          {or1?.generated_on ? (
            <p className="pv-chart-note">Report index built {or1.generated_on}.</p>
          ) : null}
        </div>
      </div>

      <div className="pv-ub-leads">
        <div className="pv-chart-subhead">
          <h3>Duplicate billing &amp; dual-claim findings reported so far</h3>
          {topLeads.length > 0 ? (
            <span className="pv-chart-note">₹ crore cited in the CAG audit text</span>
          ) : null}
        </div>
        {topLeads.length > 0 ? (
          <div className="table-wrap pv-ub-leads-wrap">
            <table className="ledger-table pv-ub-table pv-ub-leads-table">
              <caption className="pv-ub-caption">
                CAG Union audit observations citing duplicate billing or dual-agency overlap.
                {onSelectFinding ? ' Open a row to see its exact page citation and excerpt.' : ''}
              </caption>
              <thead>
                <tr>
                  <th scope="col">Agency / Entity</th>
                  <th scope="col">Report</th>
                  <th scope="col" className="num">
                    Page
                  </th>
                  <th scope="col">Signal</th>
                  <th scope="col" className="num">
                    Cited amount
                  </th>
                  <th scope="col">Status</th>
                </tr>
              </thead>
              <tbody>
                {topLeads.map((lead) => {
                  const tag = getFindingTag(lead.status);
                  return (
                    <tr key={lead.finding_id}>
                      <td className="pv-ub-line">
                        {onSelectFinding ? (
                          <button
                            type="button"
                            className="pv-ub-link"
                            onClick={() => onSelectFinding(lead.finding_id)}
                          >
                            {lead.entity_name || lead.title}
                          </button>
                        ) : (
                          <span>{lead.entity_name || lead.title}</span>
                        )}
                        {lead.entity_name && lead.title && lead.title !== lead.entity_name ? (
                          <span className="pv-ub-sub">{lead.title}</span>
                        ) : null}
                      </td>
                      <td className="tabular pv-ub-lead-year" data-label="Report">
                        {lead.report_no || plainFiscalYear(lead.fiscal_year)}
                      </td>
                      <td className="num tabular" data-label="Page">
                        {lead.page ?? '—'}
                      </td>
                      <td data-label="Signal">{signalLabel(lead.signal_category)}</td>
                      <td className="num pv-ub-lead-below" data-label="Cited amount">
                        <strong>
                          {lead.flagged_inr_cr > 0 ? formatInrCr(lead.flagged_inr_cr) : 'Not quantified'}
                        </strong>
                      </td>
                      <td className="pv-ub-lead-status">
                        <span className={tag.badgeClass} title={tag.description}>
                          {tag.label}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="pv-empty-box">
            Leads appear here as agents finish checks. Each one cites the exact CAG Union report, page,
            paragraph, and SHA-256 checksum.
          </div>
        )}
      </div>
    </section>
  );
}
