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
import {
  computeProjectProgress,
  formatPerReviewPct,
  formatProgressPct,
} from '../lib/progress';
import type {
  FindingItem,
  MissionItem,
  MissionTask,
  ProjectVisualSummary,
  VisualAuditingGovernmentExams,
  VisualExamAnchorCase,
  VisualExamAuthority,
  VisualExamDisruptionBucket,
  VisualExamHeadlineCounts,
  VisualExamJurisdiction,
  VisualExamLead,
  VisualExamTierCounts,
  VisualExamYear,
  VisualInnocentButLockedUpCourt,
  VisualInnocentButLockedUpCourtTierCounts,
  VisualInnocentButLockedUpLead,
  VisualInnocentButLockedUpMechanismCounts,
  VisualInnocentButLockedUpPortal,
  VisualInnocentButLockedUpState,
  VisualInnocentButLockedUpYear,
  VisualOnlyOneBidAuthority,
  VisualOnlyOneBidCohortCounts,
  VisualOnlyOneBidRepeatWinner,
  VisualParliamentHouseCounts,
  VisualParliamentMinistry,
  VisualSovereignLiesCategory,
  VisualSovereignLiesJurisdiction,
  VisualSovereignLiesLead,
  VisualSovereignLiesMechanismCounts,
  VisualSovereignLiesScheme,
  VisualSovereignLiesTierCounts,
  VisualWebsiteCheckpoint,
  VisualWebsiteMatrixItem,
  WebsiteVerificationMode,
} from '../types';
import {
  formatAcquittalOrderType,
  formatAssuranceStatus,
  formatChainStatus,
  formatCourtTier,
  formatCustodyOverrunMechanism,
  formatCandidateCountCompact,
  formatElapsedPromiseSpan,
  formatExamAuthorityType,
  formatExamCategory,
  formatExamDelaySpan,
  formatExamDisruption,
  formatExamDisruptionReason,
  formatInr,
  formatInrCr,
  formatJurisdictionLabel,
  formatOverrunDays,
  formatPortalCategory,
  formatProcurementAmountType,
  formatProcurementSignal,
  formatRetroactiveImpact,
  formatSchemeCategory,
  formatSingleBidMechanism,
  formatWalkbackMechanism,
  safeExternalUrl,
} from '../utils';

export type { ProjectVisualSummary };

export interface CompactProgressBarProps {
  completed: number;
  claimed?: number;
  queued?: number;
  total: number;
  verified?: number;
  peerReview?: number;
  reviewsRequired?: number;
  reviewsDone?: number;
  reviewsRemaining?: number;
  requiredReviews?: number;
  peerChecksTotal?: number;
  peerChecksDone?: number;
  peerChecksOpen?: number;
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

type TaskStatusBucket = 'completed' | 'claimed' | 'queued' | 'waiting';
type StatusBucket = TaskStatusBucket | 'review';

const STATUS_META: Record<
  StatusBucket,
  { label: string; color: string; badgeClass: string; dashed?: boolean }
> = {
  completed: {
    label: TASK_TAGS.done.label,
    color: 'var(--pv-done)',
    badgeClass: TASK_TAGS.done.badgeClass,
  },
  review: {
    label: FINDING_TAGS.in_review.label,
    color: 'var(--pv-review)',
    badgeClass: FINDING_TAGS.in_review.badgeClass,
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

function classifyTaskStatus(task: MissionTask): TaskStatusBucket {
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
  reviewsRequired,
  reviewsDone,
  reviewsRemaining,
  requiredReviews,
  peerChecksTotal,
  peerChecksDone,
  peerChecksOpen,
  label,
}: CompactProgressBarProps) {
  const [hovered, setHovered] = useState<'ring' | 'completed' | 'review' | 'claimed' | 'queued' | null>(null);

  const safeTotal = Math.max(0, total);
  const safeDone = Math.max(0, Math.min(safeTotal, completed));
  const safeClaimed = Math.max(0, Math.min(safeTotal - safeDone, claimed));
  const safeQueued =
    queued != null
      ? Math.max(0, Math.min(safeTotal - safeDone - safeClaimed, queued))
      : Math.max(0, safeTotal - safeDone - safeClaimed);
  const remainder = Math.max(0, safeTotal - safeDone - safeClaimed - safeQueued);
  const openCount = safeQueued + remainder;

  const progress = computeProjectProgress({
    completed: safeDone,
    total: safeTotal,
    verified,
    peerReview,
    reviewsRequired,
    reviewsDone,
    reviewsRemaining,
    requiredReviews,
    peerChecksTotal,
    peerChecksDone,
    peerChecksOpen,
  });

  const donePct = progress.pctComplete;
  const reviewRemainingPct = progress.reviewRemainingPct;
  const claimedPct = calcPct(safeClaimed, safeTotal);
  const openPct = calcPct(openCount, safeTotal);
  const displayPct = formatProgressPct(progress.progressPct, progress.hasIncompleteWork);
  const perReviewLabel = formatPerReviewPct(progress.perReviewPct);

  const ringRadius = 10;
  const ringCircumference = 2 * Math.PI * ringRadius;
  const visualDoneRingPct =
    progress.hasIncompleteWork && donePct > 95 ? 95 : donePct;
  const visualCompletedPlusReviewPct = Math.min(
    100,
    donePct + reviewRemainingPct,
  );
  const ringOffset = ringCircumference * (1 - visualDoneRingPct / 100);
  const reviewRingOffset = ringCircumference * (1 - visualCompletedPlusReviewPct / 100);

  if (safeTotal === 0) {
    return (
      <div className="pv-compact pv-compact--empty">
        <span className="pv-compact-empty-label">{label || 'Waiting for datasets'}</span>
      </div>
    );
  }

  const tooltipContent = (() => {
    if (hovered === 'completed') {
      if (progress.hasReviewPhase) {
        return `Tasks ${safeDone.toLocaleString('en-IN')}/${safeTotal.toLocaleString('en-IN')} (${fmtPct(progress.taskProgressPct)} of ${progress.taskWeightPct}%) + Reviews ${progress.reviewsDone.toLocaleString('en-IN')}/${progress.reviewsRequired.toLocaleString('en-IN')} (${fmtPct(progress.reviewProgressPct)} of ${progress.reviewWeightPct}%) · ${displayPct}`;
      }
      return `${safeDone.toLocaleString('en-IN')} / ${safeTotal.toLocaleString('en-IN')} · ${displayPct} ${TASK_TAGS.done.label}`;
    }
    if (hovered === 'review') {
      return `${progress.reviewsRemaining.toLocaleString('en-IN')} / ${progress.reviewsRequired.toLocaleString('en-IN')} independent reviews left · ${fmtPct(reviewRemainingPct)} (${perReviewLabel} × ${progress.requiredReviews} reviews)`;
    }
    if (hovered === 'claimed') {
      return `${safeClaimed.toLocaleString('en-IN')} / ${safeTotal.toLocaleString('en-IN')} · ${fmtPct(claimedPct)} ${TASK_TAGS.in_progress.label}`;
    }
    if (hovered === 'queued') {
      return `${openCount.toLocaleString('en-IN')} / ${safeTotal.toLocaleString('en-IN')} · ${fmtPct(openPct)} ${TASK_TAGS.open.label}`;
    }
    if (hovered === 'ring') {
      const parts: string[] = [];
      if (progress.hasReviewPhase) {
        parts.push(
          `Tasks ${safeDone.toLocaleString('en-IN')}/${safeTotal.toLocaleString('en-IN')} (${fmtPct(progress.taskProgressPct)}/${progress.taskWeightPct}%)`,
        );
        parts.push(
          `Reviews ${progress.reviewsDone.toLocaleString('en-IN')}/${progress.reviewsRequired.toLocaleString('en-IN')} (${fmtPct(progress.reviewProgressPct)}/${progress.reviewWeightPct}%, ${perReviewLabel} each)`,
        );
        if (progress.reviewsRemaining > 0) {
          parts.push(`${progress.reviewsRemaining.toLocaleString('en-IN')} reviews left`);
        }
      } else {
        parts.push(
          `${safeDone.toLocaleString('en-IN')} / ${safeTotal.toLocaleString('en-IN')} · ${displayPct} ${TASK_TAGS.done.label}`,
        );
      }
      if (safeClaimed > 0) {
        parts.push(
          `${safeClaimed.toLocaleString('en-IN')} ${TASK_TAGS.in_progress.label} (${fmtPct(claimedPct)})`,
        );
      }
      if (openCount > 0) {
        parts.push(`${openCount.toLocaleString('en-IN')} ${TASK_TAGS.open.label} (${fmtPct(openPct)})`);
      }
      return parts.join(' · ');
    }
    return null;
  })();

  const tooltipAlign =
    hovered === 'ring' || hovered === 'completed'
      ? 'left'
      : (hovered === 'review' && donePct > 55) || (hovered === 'queued' && openPct > 45)
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
          aria-label={
            progress.hasReviewPhase
              ? `${displayPct} ${TASK_TAGS.done.label} (${safeDone} of ${safeTotal} tasks, ${progress.reviewsDone} of ${progress.reviewsRequired} independent reviews)`
              : `${displayPct} ${TASK_TAGS.done.label} (${safeDone} of ${safeTotal})`
          }
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
            {reviewRemainingPct > 0 ? (
              <circle
                cx="13"
                cy="13"
                r={ringRadius}
                fill="none"
                stroke="var(--pv-review)"
                strokeWidth="3"
                strokeDasharray={ringCircumference}
                strokeDashoffset={reviewRingOffset}
                transform="rotate(-90 13 13)"
              />
            ) : null}
            {donePct > 0 ? (
              <circle
                cx="13"
                cy="13"
                r={ringRadius}
                fill="none"
                stroke="var(--pv-done)"
                strokeWidth="3"
                strokeLinecap={reviewRemainingPct > 0 ? 'butt' : 'round'}
                strokeDasharray={ringCircumference}
                strokeDashoffset={ringOffset}
                transform="rotate(-90 13 13)"
              />
            ) : null}
          </svg>
          <span className="pv-compact-pct tabular">{displayPct}</span>
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
        aria-label={`Progress breakdown: ${displayPct} ${TASK_TAGS.done.label}${reviewRemainingPct > 0 ? `, ${fmtPct(reviewRemainingPct)} ${FINDING_TAGS.in_review.label}` : ''}${claimedPct > 0 ? `, ${fmtPct(claimedPct)} ${TASK_TAGS.in_progress.label}` : ''}${openPct > 0 ? `, ${fmtPct(openPct)} ${TASK_TAGS.open.label}` : ''}`}
      >
        {donePct > 0 ? (
          <span
            className={`pv-compact-seg pv-compact-seg--done${hovered === 'completed' ? ' is-active' : ''}`}
            style={{ flex: `${Math.max(donePct, 1)} 1 0%`, minWidth: '5px' }}
            tabIndex={0}
            role="note"
            aria-label={`${displayPct} ${TASK_TAGS.done.label} (${safeDone} of ${safeTotal} tasks${progress.hasReviewPhase ? `, ${progress.reviewsDone} of ${progress.reviewsRequired} independent reviews` : ''})`}
            onMouseEnter={() => setHovered('completed')}
            onFocus={() => setHovered('completed')}
            onBlur={() => setHovered(null)}
          />
        ) : null}
        {reviewRemainingPct > 0 ? (
          <span
            className={`pv-compact-seg pv-compact-seg--review${hovered === 'review' ? ' is-active' : ''}`}
            style={{ flex: `${Math.max(reviewRemainingPct, 0.5)} 1 0%`, minWidth: '6px' }}
            tabIndex={0}
            role="note"
            aria-label={`${progress.reviewsRemaining} of ${progress.reviewsRequired} independent reviews remaining (${fmtPct(reviewRemainingPct)})`}
            onMouseEnter={() => setHovered('review')}
            onFocus={() => setHovered('review')}
            onBlur={() => setHovered(null)}
          />
        ) : null}
        {safeClaimed > 0 ? (
          <span
            className={`pv-compact-seg pv-compact-seg--claimed${hovered === 'claimed' ? ' is-active' : ''}`}
            style={{ flex: `${Math.max(claimedPct, 1)} 1 0%`, minWidth: '5px' }}
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
            style={{ flex: `${Math.max(openPct, 1)} 1 0%`, minWidth: '5px' }}
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
    let base: {
      completed: number;
      claimed: number;
      queued: number;
      waiting: number;
      planned: number;
      total: number;
      auditsDone: number;
      auditsTotal: number;
      checksDone: number;
      checksTotal: number;
      checksOpen: number;
    };

    if (sc && sc.total > 0) {
      base = {
        completed: sc.completed,
        claimed: sc.claimed,
        queued: sc.queued,
        waiting: sc.waiting,
        planned: sc.planned,
        total: sc.total,
        auditsDone: sc.audits_done,
        auditsTotal: sc.audits_total,
        checksDone: sc.checks_done,
        checksTotal: sc.checks_total,
        checksOpen: mission.peer_checks_open ?? Math.max(0, sc.checks_total - sc.checks_done),
      };
    } else if (!truncated && mosaicTasks.length > 0) {
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
      const peerOpen = peerChecks.filter(
        (t) => t.status === 'queued' || t.status === 'claimed',
      ).length;
      base = {
        completed: completedCount,
        claimed: claimedCount,
        queued: queuedCount,
        waiting: waitingCount,
        planned: mission.planned_tasks?.length ?? 0,
        total: totalCount,
        auditsDone: completedCount,
        auditsTotal: totalCount,
        checksDone: peerDone,
        checksTotal: peerChecks.length,
        checksOpen: peerOpen,
      };
    } else {
      const totalCount = Math.max(0, mission.total_tasks || 0);
      const completedCount = Math.min(totalCount, mission.completed_tasks || 0);
      const claimedCount = Math.min(totalCount - completedCount, Math.max(0, mission.claimed_tasks ?? 0));
      const queuedCount = Math.max(0, totalCount - completedCount - claimedCount);
      base = {
        completed: completedCount,
        claimed: claimedCount,
        queued: queuedCount,
        waiting: 0,
        planned: mission.planned_tasks?.length ?? 0,
        total: totalCount,
        auditsDone: completedCount,
        auditsTotal: totalCount,
        checksDone: mission.peer_checks_done ?? 0,
        checksTotal: mission.peer_checks_total ?? 0,
        checksOpen: mission.peer_checks_open ?? 0,
      };
    }

    const progress = computeProjectProgress({
      completed: base.completed,
      total: base.total,
      verified: mission.verified_findings_count,
      peerReview: mission.peer_review_count,
      reviewsRequired: sc?.reviews_required ?? mission.reviews_required,
      reviewsDone: sc?.reviews_done ?? mission.reviews_done,
      reviewsRemaining: sc?.reviews_remaining ?? mission.reviews_remaining,
      requiredReviews: sc?.required_reviews ?? mission.required_reviews,
      peerChecksTotal: base.checksTotal,
      peerChecksDone: base.checksDone,
      peerChecksOpen: base.checksOpen,
    });

    return {
      ...base,
      pctComplete: progress.pctComplete,
      progress,
    };
  }, [visualSummary, truncated, mosaicTasks, tasks, mission]);

  const segments = useMemo(() => {
    const order: StatusBucket[] = ['completed', 'review', 'claimed', 'queued', 'waiting'];
    const raw: Array<{ key: StatusBucket; count: number; pct: number }> = order.map((key) => {
      if (key === 'completed') {
        return { key, count: counts.completed, pct: counts.progress.pctComplete };
      }
      if (key === 'review') {
        return {
          key,
          count: counts.progress.reviewsRemaining,
          pct: counts.progress.reviewRemainingPct,
        };
      }
      const count = counts[key];
      return { key, count, pct: calcPct(count, counts.total) };
    });

    const radius = 74;
    const circumference = 2 * Math.PI * radius;
    const activeTotalPct = raw.reduce((sum, s) => sum + (s.pct > 0 ? s.pct : 0), 0);
    let cumulative = 0;

    return raw.map((seg) => {
      const normalizedShare = activeTotalPct > 0 ? seg.pct / activeTotalPct : 0;
      const arcLength = counts.total > 0 && seg.pct > 0 ? normalizedShare * circumference : 0;
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
  const displayOverallPct = formatProgressPct(
    counts.progress.progressPct,
    counts.progress.hasIncompleteWork,
  );
  const perReviewLabel = formatPerReviewPct(counts.progress.perReviewPct);
  const visibleSegmentCount = segments.filter((s) => s.pct > 0).length;

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
                  {activeSeg.key === 'review'
                    ? `${counts.progress.reviewsRemaining.toLocaleString('en-IN')} / ${counts.progress.reviewsRequired.toLocaleString('en-IN')} left (${fmtPct(activeSeg.pct)} · ${perReviewLabel} per review)`
                    : activeSeg.key === 'completed' && counts.progress.hasReviewPhase
                      ? `${counts.completed.toLocaleString('en-IN')}/${counts.total.toLocaleString('en-IN')} tasks (${fmtPct(counts.progress.taskProgressPct)}) + ${counts.progress.reviewsDone.toLocaleString('en-IN')}/${counts.progress.reviewsRequired.toLocaleString('en-IN')} reviews (${fmtPct(counts.progress.reviewProgressPct)})`
                      : `${activeSeg.count.toLocaleString('en-IN')} / ${counts.total.toLocaleString('en-IN')} (${fmtPct(activeSeg.pct)})`}
                </span>
              </div>
            ) : null}

            <svg
              className="pv-donut-svg"
              viewBox="0 0 196 196"
              width="196"
              height="196"
              role="img"
              aria-label={`Project progress chart: ${displayOverallPct} ${TASK_TAGS.done.label} (${counts.completed} of ${counts.total} tasks${counts.progress.hasReviewPhase ? `, ${counts.progress.reviewsDone} of ${counts.progress.reviewsRequired} independent reviews` : ''})`}
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
                  if (seg.pct <= 0) return null;
                  const isActive = activeBucket === seg.key;
                  const isDimmed = activeBucket !== null && !isActive;
                  const strokeGap = visibleSegmentCount > 1 ? 1.5 : 0;
                  const visibleArc = Math.max(1.5, seg.arcLength - strokeGap);
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
                      aria-label={
                        seg.key === 'review'
                          ? `${seg.label}: ${counts.progress.reviewsRemaining} of ${counts.progress.reviewsRequired} independent reviews remaining (${fmtPct(seg.pct)})`
                          : `${seg.label}: ${seg.count} of ${counts.total} (${fmtPct(seg.pct)})`
                      }
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
                {activeSeg
                  ? activeSeg.key === 'completed'
                    ? displayOverallPct
                    : fmtPct(activeSeg.pct)
                  : displayOverallPct}
              </span>
              <span className="pv-donut-lbl">
                {activeSeg ? activeSeg.label : TASK_TAGS.done.label}
              </span>
              <span className="pv-donut-sub tabular">
                {activeSeg
                  ? activeSeg.key === 'review'
                    ? `${counts.progress.reviewsRemaining.toLocaleString('en-IN')} / ${counts.progress.reviewsRequired.toLocaleString('en-IN')} left`
                    : `${activeSeg.count.toLocaleString('en-IN')} / ${counts.total.toLocaleString('en-IN')}`
                  : `${counts.completed.toLocaleString('en-IN')} / ${counts.total.toLocaleString('en-IN')} tasks`}
              </span>
              {!activeSeg && counts.progress.hasReviewPhase ? (
                <span className="pv-donut-sub pv-donut-sub--reviews tabular">
                  {counts.progress.reviewsDone.toLocaleString('en-IN')}/
                  {counts.progress.reviewsRequired.toLocaleString('en-IN')} reviews
                </span>
              ) : null}
            </div>
          </div>

          <div className="pv-legend-list" role="list" aria-label="Task status breakdown">
            {segments
              .filter(
                (s) =>
                  (s.key !== 'waiting' || s.count > 0) &&
                  (s.key !== 'review' || s.pct > 0 || counts.progress.hasReviewsRemaining),
              )
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
                    <span className="pv-legend-name">
                      {seg.key === 'review' ? 'Reviews left' : seg.label}
                    </span>
                    <span className="pv-legend-val tabular">
                      {seg.key === 'review'
                        ? `${counts.progress.reviewsRemaining.toLocaleString('en-IN')} · ${fmtPct(seg.pct)}`
                        : `${seg.count.toLocaleString('en-IN')} · ${seg.key === 'completed' ? displayOverallPct : fmtPct(seg.pct)}`}
                    </span>
                  </button>
                );
              })}
          </div>

          {counts.progress.hasReviewPhase ? (
            <p className="pv-weight-note tabular">
              {counts.progress.taskWeightPct}% tasks + {counts.progress.reviewWeightPct}% reviews (
              {perReviewLabel} × {counts.progress.requiredReviews} independent reviews)
            </p>
          ) : null}
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
                  const isBucketFiltered =
                    activeBucket !== null && activeBucket !== 'review' && activeBucket !== bucket;
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
  if (code === 'P5') {
    return (
      <OneTeacherFiveGradesVisualizer
        mission={mission}
        visualSummary={visualSummary}
        onSelectFinding={onSelectFinding}
        onFilterTasks={onFilterTasks}
      />
    );
  }
  if (code === 'P6') {
    return (
      <ParliamentsToDoListVisualizer
        mission={mission}
        visualSummary={visualSummary}
        onSelectFinding={onSelectFinding}
        onFilterTasks={onFilterTasks}
      />
    );
  }
  if (code === 'P7') {
    return (
      <OnlyOneBidVisualiser
        mission={mission}
        visualSummary={visualSummary}
        onSelectFinding={onSelectFinding}
        onFilterTasks={onFilterTasks}
      />
    );
  }
  if (code === 'P8') {
    return (
      <SovereignLiesVisualiser
        mission={mission}
        visualSummary={visualSummary}
        onSelectFinding={onSelectFinding}
        onFilterTasks={onFilterTasks}
      />
    );
  }
  if (code === 'P9') {
    return (
      <AuditingGovernmentExamsVisualiser
        mission={mission}
        visualSummary={visualSummary}
        onSelectFinding={onSelectFinding}
        onFilterTasks={onFilterTasks}
      />
    );
  }
  if (code === 'P10') {
    return (
      <InnocentButLockedUpVisualiser
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
          <p>Three other agents re-check every lead before it is marked {FINDING_TAGS.verified.label}.</p>
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
          <p>Three other agents independently verify every lead before it is marked {FINDING_TAGS.verified.label}.</p>
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

/* ---------- 3E. P5 One Teacher, Five Grades: single-teacher & primary staffing concentration ---------- */

const P5_METHOD_URL =
  'https://github.com/aiwatchdogsindia/aiwatchdogs.in/blob/main/projects/one-teacher-five-grades/METHODOLOGY.md';

function p5SignalLabel(signal: string): string {
  if (signal === 'severe_concentration_and_overcrowding') return 'Concentration + overcrowding / under-staffing';
  if (signal === 'high_single_teacher_concentration') return 'High single-teacher share (>=10%)';
  if (signal === 'severe_single_teacher_overcrowding') return 'Overcrowded (>30 pupils/teacher)';
  if (signal === 'gov_primary_understaffed') return 'Gov primary avg <2 teachers';
  return signal || 'Staffing signal';
}

function OneTeacherFiveGradesVisualizer({
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
  const p5 = visualSummary?.one_teacher_five_grades;
  const workTag = getWorkTypeTag(undefined, mission.methodology_code);
  const slugPath = encodeURIComponent(mission.slug);

  const years = p5?.years ?? [];
  const topStates = p5?.top_states ?? [];
  const base = p5?.national_baseline;
  const leads = p5?.leads ?? 0;
  const verified = p5?.verified ?? mission.verified_findings_count ?? 0;
  const inReview = p5?.peer_review ?? mission.peer_review_count ?? 0;
  const documentsUrl = p5?.documents_url || `/api/missions/${slugPath}/documents.csv`;
  const coverageUrl = p5?.coverage_url || `/api/missions/${slugPath}/coverage.json`;
  const maxShare = Math.max(10, ...topStates.map((s) => s.single_teacher_share_pct || 0));

  return (
    <section className="card card-pad pv-results" aria-label="One Teacher Five Grades results">
      <div className="pv-results-head">
        <div className="pv-results-title-wrap">
          <span className="kicker">
            <BarChart3 size={12} aria-hidden="true" />
            Live results · {workTag.label}
          </span>
          <h2 className="pv-results-title">Single-teacher &amp; primary staffing concentration</h2>
        </div>

        <div className="pv-summary-pills tabular">
          <span className="tag-mono">{fmtCount(p5?.states_covered ?? 36)} States &amp; UTs</span>
          {base?.single_teacher_schools ? (
            <span className="tag-mono">
              {fmtCount(base.single_teacher_schools)} single-teacher schools (2025-26 All-India)
            </span>
          ) : null}
          {base?.single_teacher_enrolments ? (
            <span className="tag-mono">{fmtCount(base.single_teacher_enrolments)} pupils</span>
          ) : null}
          <span className="tag-mono">
            {fmtCount(leads)} {leads === 1 ? 'state lead' : 'state leads'}
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
            <h3>Academic cycle by cycle</h3>
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
                  UDISE+ State/UT verification progress and All-India baseline per academic year.
                  {onFilterTasks ? ' Choose a year to filter tasks.' : ''}
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Year</th>
                    <th scope="col">Checked</th>
                    <th scope="col" className="num">
                      All-India single-teacher
                    </th>
                    <th scope="col" className="num">
                      Flagged States/UTs
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
                        <td className="num pv-ub-years-lines" data-label="All-India single-teacher">
                          <span className="pv-ub-cell-val">
                            {fmtCount(y.all_india_single_teacher_schools)} schools
                          </span>
                          <span className="pv-ub-sub">
                            {fmtCount(y.all_india_single_teacher_enrolments)} students
                          </span>
                        </td>
                        <td className="num pv-ub-years-below" data-label="Flagged States/UTs">
                          <span className="pv-ub-cell-val">{fmtCount(y.leads)} flagged</span>
                          <span className="pv-ub-sub">{fmtCount(total)} checks</span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="pv-empty-box">
              Academic-year progress appears here once the hub loads the UDISE+ booklet index.
            </div>
          )}
        </div>

        <div className="pv-chart-card pv-ub-guide">
          <div className="pv-chart-subhead">
            <h3>How to read this</h3>
          </div>
          <p>
            Each task downloads the checksummed Ministry of Education UDISE+ national booklet PDF and
            cross-checks Table 2.2 (single-teacher schools &amp; pupils across all managements) against
            Government-only Tables 3.3, 4.3, and 5.2 (Primary Classes I–V).
          </p>
          <p>
            States/UTs are surfaced where single-teacher share is &ge;10%, pupils per single-teacher school
            exceed 30:1 (RTE Schedule norm), or Government Primary schools average below 2.0 teachers.
          </p>
          <p>Three independent agents verify every state observation before it is marked {FINDING_TAGS.verified.label}.</p>
          <div className="pv-ub-links">
            <a className="inline-link" href={documentsUrl}>
              UDISE+ booklets (CSV)
            </a>
            <a className="inline-link" href={coverageUrl}>
              Coverage (JSON)
            </a>
            <a className="inline-link" href={P5_METHOD_URL} target="_blank" rel="noopener noreferrer">
              Method <ExternalLink size={11} aria-hidden="true" className="pv-ub-ext" />
            </a>
          </div>
          {p5?.generated_on ? (
            <p className="pv-chart-note">UDISE+ index built {p5.generated_on}.</p>
          ) : null}
        </div>
      </div>

      <div className="pv-ub-leads">
        <div className="pv-chart-subhead">
          <h3>Highest single-teacher school concentrations flagged so far</h3>
          {topStates.length > 0 ? (
            <span className="pv-chart-note">Ranked by single-teacher share (%) &amp; count</span>
          ) : null}
        </div>
        {topStates.length > 0 ? (
          <div className="table-wrap pv-ub-leads-wrap">
            <table className="ledger-table pv-ub-table pv-ub-leads-table">
              <caption className="pv-ub-caption">
                States and UTs flagged from official UDISE+ Tables 2.2, 3.3, 4.3, and 5.2.
                {onSelectFinding ? ' Open a row to inspect full school, teacher, and enrolment counts.' : ''}
              </caption>
              <thead>
                <tr>
                  <th scope="col">State / UT</th>
                  <th scope="col">Year</th>
                  <th scope="col" className="num">
                    Single-teacher
                  </th>
                  <th scope="col" className="num">
                    Share (%)
                  </th>
                  <th scope="col" className="num">
                    Students / school
                  </th>
                  <th scope="col" className="num">
                    Gov Primary avg teachers
                  </th>
                  <th scope="col">Status</th>
                </tr>
              </thead>
              <tbody>
                {topStates.map((st) => {
                  const tag = getFindingTag(st.status);
                  const barPct = Math.max(4, (st.single_teacher_share_pct / maxShare) * 100);
                  return (
                    <tr key={st.finding_id}>
                      <td className="pv-ub-line">
                        {onSelectFinding ? (
                          <button
                            type="button"
                            className="pv-ub-link"
                            onClick={() => onSelectFinding(st.finding_id)}
                          >
                            {st.state_name}
                          </button>
                        ) : (
                          <span>{st.state_name}</span>
                        )}
                        <span className="pv-ub-sub">{p5SignalLabel(st.signal_category)}</span>
                      </td>
                      <td className="tabular pv-ub-lead-year" data-label="Year">
                        {st.academic_year}
                      </td>
                      <td className="num tabular" data-label="Single-teacher">
                        <strong>{fmtCount(st.single_teacher_schools)}</strong>
                        <span className="pv-ub-sub">{fmtCount(st.single_teacher_enrolments)} pupils</span>
                      </td>
                      <td className="num pv-ub-lead-below" data-label="Share (%)">
                        <strong>{st.single_teacher_share_pct.toFixed(1)}%</strong>
                        <span className="pv-ub-mini-track" aria-hidden="true">
                          <span className="pv-ub-mini-fill" style={{ width: `${barPct}%` }} />
                        </span>
                      </td>
                      <td className="num tabular" data-label="Students / school">
                        {st.students_per_single_teacher.toFixed(1)}
                      </td>
                      <td className="num tabular" data-label="Gov Primary avg teachers">
                        {st.gov_primary_teachers_per_school != null
                          ? `${st.gov_primary_teachers_per_school.toFixed(2)}`
                          : '—'}
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
            Concentrations appear here as agents verify States &amp; UTs across 2023-24 to 2025-26 UDISE+ reports.
          </div>
        )}
      </div>
    </section>
  );
}

/* ---------- 3F. P6 Parliament's To-Do List: ministerial assurances & follow-up answers ---------- */

const P6_METHOD_URL =
  'https://github.com/aiwatchdogsindia/aiwatchdogs.in/blob/main/projects/parliaments-to-do-list/METHODOLOGY.md';
export const P6_DEFAULT_HOOK = 'A minister promised Parliament an answer. Did it ever get one?';
const P6_INITIAL_MINISTRIES = 12;

export function p6SignalLabel(signal: string): string {
  if (signal === 'overdue_pending_assurance') return 'Pending past 90-day norm';
  if (signal === 'partly_implemented_assurance') return 'Partly implemented';
  if (signal === 'dropped_without_fulfillment') return 'Dropped without fulfillment';
  if (signal === 'delayed_or_unreadable_followup') return 'Delayed or unreadable follow-up';
  return signal ? signal.replace(/_/g, ' ') : 'Assurance lead';
}

export function p6NormalizeRecordedStatuses(byStatus?: Record<string, number> | null): {
  pending: number;
  fulfilled: number;
  partly: number;
  dropped: number;
  other: number;
  total: number;
} {
  let pending = 0;
  let fulfilled = 0;
  let partly = 0;
  let dropped = 0;
  let other = 0;

  for (const [rawKey, rawVal] of Object.entries(byStatus || {})) {
    const n = Math.max(0, Number(rawVal) || 0);
    if (!n) continue;
    const key = rawKey.trim().toLowerCase().replace(/[\s-]+/g, '_');
    if (key === 'pending') pending += n;
    else if (key === 'fully_implemented' || key === 'fulfilled' || key === 'fimp' || key === 'implemented') {
      fulfilled += n;
    } else if (key === 'partly_implemented' || key === 'partially_implemented' || key === 'partly_fulfilled') {
      partly += n;
    } else if (key === 'dropped') dropped += n;
    else other += n;
  }

  return {
    pending,
    fulfilled,
    partly,
    dropped,
    other,
    total: pending + fulfilled + partly + dropped + other,
  };
}

export function p6SummarizeHouses(
  byHouse?: Record<string, VisualParliamentHouseCounts | number> | null,
): { lokSabha: number; rajyaSabha: number } {
  let lokSabha = 0;
  let rajyaSabha = 0;
  for (const [rawKey, val] of Object.entries(byHouse || {})) {
    const count =
      typeof val === 'number'
        ? Math.max(0, val)
        : val && typeof val === 'object'
          ? Math.max(0, Number(val.tasks) || 0)
          : 0;
    const k = rawKey.trim().toLowerCase().replace(/[\s_-]+/g, '');
    if (k === 'loksabha' || k === 'ls') lokSabha += count;
    else if (k === 'rajyasabha' || k === 'rs') rajyaSabha += count;
  }
  return { lokSabha, rajyaSabha };
}

export function p6FilterMinistries(
  ministries: VisualParliamentMinistry[],
  query: string,
  statusFilter: 'all' | 'pending' | 'fulfilled' = 'all',
): VisualParliamentMinistry[] {
  const q = query.trim().toLowerCase();
  return ministries.filter((m) => {
    if (statusFilter === 'pending' && (m.pending_count ?? 0) <= 0) return false;
    if (statusFilter === 'fulfilled' && (m.fulfilled_count ?? 0) <= 0) return false;
    if (!q) return true;
    return (
      m.ministry.toLowerCase().includes(q) ||
      (m.ministry_key || '').toLowerCase().includes(q)
    );
  });
}

/** Exact ministry prefix token matching every P6 task title (`"${ministry} · ..."`). */
export function p6MinistryFilterQuery(ministry: string): string {
  const clean = ministry.trim();
  return clean ? `${clean} ·` : '';
}

function p6ShortText(text?: string | null, maxLen = 155): string {
  const clean = (text || '').replace(/\s+/g, ' ').trim();
  if (!clean) return '—';
  return clean.length > maxLen ? `${clean.slice(0, maxLen - 1).trimEnd()}…` : clean;
}

function p6HouseShort(house?: string | null): string {
  const h = (house || '').trim().toLowerCase();
  if (h === 'lok sabha' || h === 'ls') return 'LS';
  if (h === 'rajya sabha' || h === 'rs') return 'RS';
  return house || '—';
}

function ParliamentsToDoListVisualizer({
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
  const p6 = visualSummary?.parliaments_to_do_list;
  const workTag = getWorkTypeTag(undefined, mission.methodology_code);
  const slugPath = encodeURIComponent(mission.slug);

  const [ministryQuery, setMinistryQuery] = useState('');
  const [ministryStatusFilter, setMinistryStatusFilter] = useState<'all' | 'pending' | 'fulfilled'>('all');
  const [showAllMinistries, setShowAllMinistries] = useState(false);

  const hook = p6?.hook?.trim() || P6_DEFAULT_HOOK;
  const ministries = p6?.ministries ?? [];
  const topLeads = p6?.top_leads ?? [];
  const anchor = p6?.anchor_case ?? null;
  const leads = p6?.leads ?? 0;
  const verified = p6?.verified ?? mission.verified_findings_count ?? 0;
  const inReview = p6?.peer_review ?? mission.peer_review_count ?? 0;
  const totalTasks = p6?.total_tasks ?? mission.total_tasks ?? 0;
  const ministriesCovered = p6?.ministries_covered ?? ministries.length;
  const documentsUrl = p6?.documents_url || `/api/missions/${slugPath}/documents.csv`;
  const coverageUrl = p6?.coverage_url || `/api/missions/${slugPath}/coverage.json`;

  const statusTotals = useMemo(
    () => p6NormalizeRecordedStatuses(p6?.by_recorded_status),
    [p6?.by_recorded_status],
  );
  const houseTotals = useMemo(() => p6SummarizeHouses(p6?.by_house), [p6?.by_house]);

  const filteredMinistries = useMemo(
    () => p6FilterMinistries(ministries, ministryQuery, ministryStatusFilter),
    [ministries, ministryQuery, ministryStatusFilter],
  );
  const visibleMinistries =
    showAllMinistries || ministryQuery.trim().length > 0 || ministryStatusFilter !== 'all'
      ? filteredMinistries
      : filteredMinistries.slice(0, P6_INITIAL_MINISTRIES);

  const anchorStatus = anchor ? formatAssuranceStatus(anchor.recorded_status) : null;
  const anchorPrimaryUrl = safeExternalUrl(anchor?.source_url);
  const anchorSecondaryUrl = safeExternalUrl(anchor?.secondary_url);

  const statusDenominator = statusTotals.total || totalTasks;
  const pendingPct = calcPct(statusTotals.pending, statusDenominator);
  const partlyPct = calcPct(statusTotals.partly, statusDenominator);
  const fulfilledPct = calcPct(statusTotals.fulfilled, statusDenominator);
  const droppedPct = calcPct(statusTotals.dropped, statusDenominator);

  return (
    <section className="card card-pad pv-results" aria-label="Parliament's To-Do List results">
      <div className="pv-results-head">
        <div className="pv-results-title-wrap">
          <span className="kicker">
            <BarChart3 size={12} aria-hidden="true" />
            Live results · {workTag.label}
          </span>
          <h2 className="pv-results-title">Parliament&rsquo;s To-Do List</h2>
          <p className="pv-p6-hook">{hook}</p>
        </div>

        <div className="pv-summary-pills tabular">
          <span className="tag-mono">{fmtCount(totalTasks)} assurances</span>
          {ministriesCovered > 0 ? (
            <span className="tag-mono">{fmtCount(ministriesCovered)} ministries</span>
          ) : null}
          {houseTotals.lokSabha > 0 || houseTotals.rajyaSabha > 0 ? (
            <span className="tag-mono">
              {fmtCount(houseTotals.lokSabha)} Lok Sabha · {fmtCount(houseTotals.rajyaSabha)} Rajya Sabha
            </span>
          ) : null}
          <span className="tag-mono">
            {fmtCount(leads)} {leads === 1 ? 'assurance flagged' : 'assurances flagged'}
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

      {/* Recorded status breakdown across the tracked assurances */}
      {statusTotals.total > 0 ? (
        <div className="pv-p6-overview">
          <div className="pv-p6-status-bar-wrap">
            <div
              className="pv-p6-status-bar"
              role="img"
              aria-label={`Recorded status across ${fmtCount(statusTotals.total)} assurances: ${fmtCount(statusTotals.pending)} pending (${fmtPct(pendingPct)}), ${fmtCount(statusTotals.partly)} partly implemented (${fmtPct(partlyPct)}), ${fmtCount(statusTotals.fulfilled)} fully implemented (${fmtPct(fulfilledPct)}), ${fmtCount(statusTotals.dropped)} dropped (${fmtPct(droppedPct)})`}
            >
              {pendingPct > 0 ? (
                <span
                  className="pv-p6-seg pv-p6-seg--pending"
                  style={{ width: `${pendingPct}%` }}
                  title={`Pending: ${fmtCount(statusTotals.pending)} (${fmtPct(pendingPct)})`}
                />
              ) : null}
              {partlyPct > 0 ? (
                <span
                  className="pv-p6-seg pv-p6-seg--partly"
                  style={{ width: `${partlyPct}%` }}
                  title={`Partly implemented: ${fmtCount(statusTotals.partly)} (${fmtPct(partlyPct)})`}
                />
              ) : null}
              {fulfilledPct > 0 ? (
                <span
                  className="pv-p6-seg pv-p6-seg--fulfilled"
                  style={{ width: `${fulfilledPct}%` }}
                  title={`Fully implemented: ${fmtCount(statusTotals.fulfilled)} (${fmtPct(fulfilledPct)})`}
                />
              ) : null}
              {droppedPct > 0 ? (
                <span
                  className="pv-p6-seg pv-p6-seg--dropped"
                  style={{ width: `${droppedPct}%` }}
                  title={`Dropped: ${fmtCount(statusTotals.dropped)} (${fmtPct(droppedPct)})`}
                />
              ) : null}
            </div>
          </div>

          <div className="pv-matrix-overview pv-p6-kpis">
            <div className="pv-overview-tile pv-p6-kpi pv-p6-kpi--pending">
              <span className="pv-overview-kicker">Pending on Digital Sansad</span>
              <span className="pv-overview-num tabular">{fmtCount(statusTotals.pending)}</span>
              <span className="pv-overview-sub tabular">{fmtPct(pendingPct)} of tracked assurances</span>
            </div>
            <div className="pv-overview-tile pv-p6-kpi pv-p6-kpi--fulfilled">
              <span className="pv-overview-kicker">Fully implemented</span>
              <span className="pv-overview-num tabular">{fmtCount(statusTotals.fulfilled)}</span>
              <span className="pv-overview-sub tabular">{fmtPct(fulfilledPct)} with follow-up statement</span>
            </div>
            <div className="pv-overview-tile pv-p6-kpi pv-p6-kpi--partly">
              <span className="pv-overview-kicker">Partly implemented</span>
              <span className="pv-overview-num tabular">{fmtCount(statusTotals.partly)}</span>
              <span className="pv-overview-sub tabular">{fmtPct(partlyPct)} still awaiting full reply</span>
            </div>
            <div className="pv-overview-tile pv-p6-kpi pv-p6-kpi--dropped">
              <span className="pv-overview-kicker">Dropped</span>
              <span className="pv-overview-num tabular">{fmtCount(statusTotals.dropped)}</span>
              <span className="pv-overview-sub tabular">{fmtPct(droppedPct)} closed without fulfillment</span>
            </div>
          </div>
        </div>
      ) : null}

      {/* Featured Anchor Case (e.g., Jhunjhunu–Fatehpur pending assurance) */}
      {anchor ? (
        <article className="pv-p6-anchor" aria-label="Featured Digital Sansad assurance case">
          <div className="pv-p6-anchor-head">
            <div className="pv-p6-anchor-meta">
              <span className="pv-p6-anchor-eyebrow">Featured case · Digital Sansad</span>
              <span className="tag-mono">{anchor.house}</span>
              {anchor.session ? <span className="tag-mono">Session {anchor.session}</span> : null}
              <span className="tag-mono font-mono">{anchor.assurance_ref}</span>
              <span className="tag-mono">{anchor.ministry}</span>
              {anchor.promise_date ? (
                <span className="tag-mono tabular">Promised {anchor.promise_date}</span>
              ) : null}
            </div>
            {anchorStatus ? (
              <span className={anchorStatus.chipClass}>
                {anchorStatus.label}
                {anchor.days_elapsed != null ? ` · ${fmtCount(anchor.days_elapsed)}d` : ''}
              </span>
            ) : null}
          </div>

          <h3 className="pv-p6-anchor-title">{anchor.subject}</h3>
          {anchor.member || anchor.minister ? (
            <p className="pv-p6-anchor-people">
              {anchor.member ? (
                <span>
                  <strong>MP:</strong> {anchor.member}
                </span>
              ) : null}
              {anchor.member && anchor.minister ? <span aria-hidden="true"> · </span> : null}
              {anchor.minister ? (
                <span>
                  <strong>Minister:</strong> {anchor.minister}
                </span>
              ) : null}
            </p>
          ) : null}

          <div className="pv-p6-anchor-grid">
            <div className="pv-p6-anchor-col">
              <div className="pv-p6-anchor-label">01 · Original promise</div>
              <p className="pv-p6-anchor-text pv-p6-anchor-quote">
                &ldquo;{anchor.original_promise}&rdquo;
              </p>
            </div>
            <div className="pv-p6-anchor-col">
              <div className="pv-p6-anchor-label">02 · Recorded status</div>
              <p className="pv-p6-anchor-text">
                <strong>{anchorStatus?.label || anchor.recorded_status}</strong>
                {anchor.days_elapsed != null
                  ? ` — ${fmtCount(anchor.days_elapsed)} days elapsed since ${anchor.promise_date} (90-day parliamentary norm).`
                  : '.'}
              </p>
            </div>
            <div className="pv-p6-anchor-col">
              <div className="pv-p6-anchor-label">03 · What the follow-up answered</div>
              <p className="pv-p6-anchor-text">{anchor.followup_answer}</p>
            </div>
          </div>

          <div className="pv-p6-anchor-actions">
            {anchor.finding_id && onSelectFinding ? (
              <button
                type="button"
                className="btn btn-secondary btn-xs"
                onClick={() => onSelectFinding(anchor.finding_id!)}
              >
                Inspect full dossier
              </button>
            ) : onFilterTasks ? (
              <button
                type="button"
                className="btn btn-secondary btn-xs"
                onClick={() => onFilterTasks(anchor.assurance_ref || p6MinistryFilterQuery(anchor.ministry))}
              >
                Find task in queue
              </button>
            ) : null}
            {anchorPrimaryUrl ? (
              <a
                className="inline-link"
                href={anchorPrimaryUrl}
                target="_blank"
                rel="noopener noreferrer"
              >
                Digital Sansad record <ExternalLink size={11} aria-hidden="true" className="pv-ub-ext" />
              </a>
            ) : null}
            {anchorSecondaryUrl ? (
              <a
                className="inline-link"
                href={anchorSecondaryUrl}
                target="_blank"
                rel="noopener noreferrer"
              >
                Question PDF <ExternalLink size={11} aria-hidden="true" className="pv-ub-ext" />
              </a>
            ) : null}
          </div>
        </article>
      ) : null}

      {/* Ministry breakdown + Methodology guide */}
      <div className="pv-results-split">
        <div className="pv-chart-card">
          <div className="pv-chart-subhead">
            <h3>Ministry by ministry ({fmtCount(ministries.length)})</h3>
            <div className="pv-mini-legend">
              <span>
                <i className="pv-dot pv-p6-dot--pending" aria-hidden="true" /> Pending
              </span>
              <span>
                <i className="pv-dot pv-p6-dot--partly" aria-hidden="true" /> Partly
              </span>
              <span>
                <i className="pv-dot pv-p6-dot--fulfilled" aria-hidden="true" /> Fulfilled
              </span>
              <span>
                <i className="pv-dot pv-p6-dot--dropped" aria-hidden="true" /> Dropped
              </span>
            </div>
          </div>

          {ministries.length > 0 ? (
            <>
              <div className="pv-p6-ministry-controls">
                <div className="pv-p6-search-wrap">
                  <Search size={13} aria-hidden="true" className="pv-p6-search-icon" />
                  <input
                    type="search"
                    className="input-field pv-p6-search"
                    value={ministryQuery}
                    onChange={(e) => setMinistryQuery(e.target.value)}
                    placeholder={`Filter ${fmtCount(ministries.length)} ministries…`}
                    aria-label="Filter Union Ministries"
                  />
                </div>
                <div className="pv-p6-filter-pills" role="group" aria-label="Filter ministries by assurance status">
                  {(['all', 'pending', 'fulfilled'] as const).map((f) => (
                    <button
                      key={f}
                      type="button"
                      className={`btn btn-xs ${ministryStatusFilter === f ? 'btn-primary' : 'btn-ghost'}`}
                      onClick={() => setMinistryStatusFilter(f)}
                    >
                      {f === 'all' ? 'All' : f === 'pending' ? 'With pending' : 'With fulfilled'}
                    </button>
                  ))}
                </div>
              </div>

              {visibleMinistries.length > 0 ? (
                <div className="pv-ub-years-wrap">
                  <table className="pv-ub-table pv-ub-years pv-p6-ministries-table">
                    <caption className="pv-ub-caption">
                      Formal ministerial assurances tracked across Digital Sansad, ordered by pending count.
                      {onFilterTasks ? ' Choose a ministry to filter its tasks below.' : ''}
                    </caption>
                    <thead>
                      <tr>
                        <th scope="col">Ministry</th>
                        <th scope="col">Recorded status &amp; checked</th>
                        <th scope="col" className="num">
                          Pending
                        </th>
                        <th scope="col" className="num">
                          Fulfilled / Flagged
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {visibleMinistries.map((m) => {
                        const total = Math.max(0, m.tasks);
                        const pending = Math.max(0, m.pending_count ?? 0);
                        const fulfilled = Math.max(0, m.fulfilled_count ?? 0);
                        const partly = Math.max(0, m.partly_implemented_count ?? 0);
                        const dropped = Math.max(
                          0,
                          m.dropped_count ?? m.other_count ?? Math.max(0, total - pending - fulfilled - partly),
                        );
                        const statusTotal = Math.max(total, pending + fulfilled + partly + dropped);
                        const mPendingPct = calcPct(pending, statusTotal);
                        const mPartlyPct = calcPct(partly, statusTotal);
                        const mFulfilledPct = calcPct(fulfilled, statusTotal);
                        const mDroppedPct = calcPct(dropped, statusTotal);
                        const donePct = calcPct(m.done, total);
                        const hasHouses =
                          (m.lok_sabha_tasks ?? 0) > 0 || (m.rajya_sabha_tasks ?? 0) > 0;

                        const filterQuery = p6MinistryFilterQuery(m.ministry);

                        return (
                          <tr
                            key={m.ministry_key || m.ministry}
                            className={onFilterTasks ? 'pv-p6-ministry-row--interactive' : undefined}
                            onClick={
                              onFilterTasks
                                ? (e) => {
                                    const target = e.target as HTMLElement | null;
                                    if (target?.closest('a, button')) return;
                                    if (window.getSelection?.()?.toString()) return;
                                    onFilterTasks(filterQuery);
                                  }
                                : undefined
                            }
                          >
                            <th scope="row" className="pv-ub-year pv-p6-ministry-name">
                              {onFilterTasks ? (
                                <button
                                  type="button"
                                  className="pv-ub-link"
                                  onClick={() => onFilterTasks(filterQuery)}
                                  aria-label={`Show tasks for ${m.ministry}`}
                                >
                                  {m.ministry}
                                </button>
                              ) : (
                                <span>{m.ministry}</span>
                              )}
                              {hasHouses ? (
                                <span className="pv-ub-sub">
                                  {fmtCount(m.lok_sabha_tasks ?? 0)} LS · {fmtCount(m.rajya_sabha_tasks ?? 0)} RS
                                </span>
                              ) : null}
                            </th>
                            <td className="pv-ub-years-checked">
                              <span
                                className="pv-ub-bar"
                                role="img"
                                aria-label={`${m.ministry}: ${fmtCount(pending)} pending, ${fmtCount(partly)} partly implemented, ${fmtCount(fulfilled)} fully implemented, ${fmtCount(dropped)} dropped; ${fmtCount(m.done)} of ${fmtCount(total)} tasks checked`}
                              >
                                {mPendingPct > 0 ? (
                                  <span
                                    className="pv-ub-bar-seg pv-p6-seg--pending"
                                    style={{ width: `${Math.max(mPendingPct, 2)}%` }}
                                  />
                                ) : null}
                                {mPartlyPct > 0 ? (
                                  <span
                                    className="pv-ub-bar-seg pv-p6-seg--partly"
                                    style={{ width: `${Math.max(mPartlyPct, 2)}%` }}
                                  />
                                ) : null}
                                {mFulfilledPct > 0 ? (
                                  <span
                                    className="pv-ub-bar-seg pv-p6-seg--fulfilled"
                                    style={{ width: `${Math.max(mFulfilledPct, 2)}%` }}
                                  />
                                ) : null}
                                {mDroppedPct > 0 ? (
                                  <span
                                    className="pv-ub-bar-seg pv-p6-seg--dropped"
                                    style={{ width: `${Math.max(mDroppedPct, 2)}%` }}
                                  />
                                ) : null}
                              </span>
                              <span className="pv-ub-sub tabular">
                                {fmtCount(m.done)}/{fmtCount(total)} checked ({fmtPct(donePct)})
                                {m.claimed > 0 ? ` · ${fmtCount(m.claimed)} running` : ''}
                              </span>
                            </td>
                            <td className="num pv-ub-years-lines" data-label="Pending">
                              <span className="pv-ub-cell-val">{fmtCount(pending)}</span>
                              <span className="pv-ub-sub">{fmtPct(mPendingPct)} of {fmtCount(total)}</span>
                            </td>
                            <td className="num pv-ub-years-below" data-label="Fulfilled / Flagged">
                              <span className="pv-ub-cell-val">{fmtCount(fulfilled)} fulfilled</span>
                              <span className="pv-ub-sub">
                                {m.leads > 0
                                  ? `${fmtCount(m.leads)} ${m.leads === 1 ? 'lead' : 'leads'}`
                                  : partly > 0
                                    ? `${fmtCount(partly)} partly`
                                    : `${fmtCount(total)} tracked`}
                              </span>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="pv-empty-box">No ministries match &ldquo;{ministryQuery}&rdquo;.</div>
              )}

              {ministryQuery.trim().length === 0 &&
              ministryStatusFilter === 'all' &&
              filteredMinistries.length > P6_INITIAL_MINISTRIES ? (
                <div className="pv-list-more">
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    onClick={() => setShowAllMinistries((v) => !v)}
                  >
                    {showAllMinistries
                      ? `Show top ${P6_INITIAL_MINISTRIES} ministries`
                      : `Show all ${fmtCount(filteredMinistries.length)} ministries`}
                  </button>
                </div>
              ) : null}
            </>
          ) : (
            <div className="pv-empty-box">
              Ministry-by-ministry coverage appears here once the hub loads the Digital Sansad assurance index.
            </div>
          )}
        </div>

        <div className="pv-chart-card pv-ub-guide">
          <div className="pv-chart-subhead">
            <h3>How to read this</h3>
          </div>
          <p>
            Every task verifies a formal ministerial assurance directly from Digital Sansad (18th Lok Sabha
            and Rajya Sabha) using pinned SHA-256 JSON records and official Question or Implementation Report
            PDFs.
          </p>
          <p>
            For each assurance, the deterministic check cites three verifiable facts: <strong>(1) the original
            promise</strong>, <strong>(2) the recorded status</strong> against Parliament&rsquo;s 90-day
            fulfillment norm, and <strong>(3) what the follow-up actually answered</strong> (including flagging
            scanned image-only Implementation Report PDFs lacking a machine-readable text layer).
          </p>
          <p>
            Assurances still pending past 90 days, partly implemented, dropped without fulfillment, or fulfilled
            late / with unreadable follow-ups are raised as leads; timely fulfilled or &le;90-day assurances are
            published as {FINDING_TAGS.cleared.label}. Three independent agents verify every lead before it is
            marked {FINDING_TAGS.verified.label}.
          </p>
          <div className="pv-ub-links">
            <a className="inline-link" href={documentsUrl}>
              Digital Sansad sources (CSV)
            </a>
            <a className="inline-link" href={coverageUrl}>
              Coverage (JSON)
            </a>
            <a className="inline-link" href={P6_METHOD_URL} target="_blank" rel="noopener noreferrer">
              Method <ExternalLink size={11} aria-hidden="true" className="pv-ub-ext" />
            </a>
          </div>
          {p6?.generated_on ? (
            <p className="pv-chart-note">
              Digital Sansad index built {p6.generated_on}
              {p6.as_of_date && p6.as_of_date !== p6.generated_on ? ` · as of ${p6.as_of_date}` : ''}.
            </p>
          ) : null}
        </div>
      </div>

      {/* Reported assurance leads table */}
      <div className="pv-ub-leads">
        <div className="pv-chart-subhead">
          <h3>Assurances flagged so far</h3>
          {topLeads.length > 0 ? (
            <span className="pv-chart-note">Original promise, recorded status &amp; follow-up answer</span>
          ) : null}
        </div>
        {topLeads.length > 0 ? (
          <div className="table-wrap pv-ub-leads-wrap">
            <table className="ledger-table pv-ub-table pv-ub-leads-table pv-p6-leads-table">
              <caption className="pv-ub-caption">
                Ministerial assurances verified against Digital Sansad records.
                {onSelectFinding ? ' Open any row to read the full promise, status, follow-up answer, and citations.' : ''}
              </caption>
              <thead>
                <tr>
                  <th scope="col">Ministry &amp; Assurance</th>
                  <th scope="col">House</th>
                  <th scope="col">Recorded status</th>
                  <th scope="col">Promise &amp; follow-up answer</th>
                  <th scope="col">Audit status</th>
                </tr>
              </thead>
              <tbody>
                {topLeads.map((lead) => {
                  const tag = getFindingTag(lead.status);
                  const recStatus = formatAssuranceStatus(lead.recorded_status);
                  return (
                    <tr key={lead.finding_id}>
                      <td className="pv-ub-line">
                        {onSelectFinding ? (
                          <button
                            type="button"
                            className="pv-ub-link"
                            onClick={() => onSelectFinding(lead.finding_id)}
                          >
                            {lead.ministry} · {lead.assurance_ref}
                          </button>
                        ) : (
                          <span>
                            {lead.ministry} · {lead.assurance_ref}
                          </span>
                        )}
                        <span className="pv-ub-sub">
                          {p6SignalLabel(lead.signal_category)}
                          {lead.title ? ` · ${p6ShortText(lead.title, 90)}` : ''}
                        </span>
                      </td>
                      <td className="tabular pv-ub-lead-year" data-label="House">
                        <span>
                          {p6HouseShort(lead.house)}
                          {lead.session ? ` · S${lead.session}` : ''}
                        </span>
                        {lead.promise_date ? (
                          <span className="pv-ub-sub">{lead.promise_date}</span>
                        ) : null}
                      </td>
                      <td className="pv-p6-lead-rec" data-label="Recorded status">
                        <span className={recStatus.chipClass}>{recStatus.label}</span>
                        {lead.days_elapsed != null ? (
                          <span className="pv-ub-sub tabular">{fmtCount(lead.days_elapsed)}d elapsed</span>
                        ) : null}
                      </td>
                      <td className="pv-p6-lead-summary" data-label="Promise & follow-up">
                        <div className="pv-p6-excerpt">
                          <strong>Promise:</strong> {p6ShortText(lead.original_promise, 120)}
                        </div>
                        <div className="pv-p6-excerpt pv-p6-excerpt--followup">
                          <strong>Follow-up:</strong> {p6ShortText(lead.followup_answer, 120)}
                        </div>
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
            Assurance leads appear here as contributor agents verify tasks. Every entry cites the
            verbatim original promise, recorded Digital Sansad status, and what the follow-up actually
            answered with SHA-256 provenance.
          </div>
        )}
      </div>
    </section>
  );
}

/* ---------- 3G. P7 Only One Bid: single-bidder tenders & repeat-winner procurement patterns (2016–2026) ---------- */

const P7_METHOD_URL =
  'https://github.com/aiwatchdogsindia/aiwatchdogs.in/blob/main/projects/only-one-bid/METHODOLOGY.md';
export const P7_DEFAULT_HOOK =
  'When a public tender draws only one bid — or the same contractor keeps winning — what do the official records show?';
const P7_INITIAL_AUTHORITIES = 12;
const P7_INITIAL_WINNERS = 10;

export type P7CohortKey = 'single_bidder' | 'repeat_winner' | 'both';

export interface P7NormalizedCohort {
  key: P7CohortKey;
  label: string;
  shortLabel: string;
  tasks: number;
  done: number;
  claimed: number;
  open: number;
  pct: number;
  leads: number;
  flagged_inr_cr: number;
}

export interface P7NormalizedSignal {
  key:
    | 'single_bidder_repeat_winner'
    | 'unexplained_single_bidder'
    | 'repeat_winner_concentration'
    | 'short_window_or_process_gap';
  label: string;
  shortLabel: string;
  chipClass: string;
  fillClass: string;
  count: number;
  sharePct: number;
}

export function p7CohortLabel(cohort: string): string {
  const k = (cohort || '').trim().toLowerCase().replace(/[\s-]+/g, '_');
  if (k === 'single_bidder' || k === 'sb') return 'Single Bidder';
  if (k === 'repeat_winner' || k === 'rw') return 'Repeat Winner';
  if (k === 'both' || k === 'bo' || k === 'single_bidder_repeat_winner') {
    return 'Both: Single Bid + Repeat Winner';
  }
  return cohort ? cohort.replace(/_/g, ' ') : 'Procurement Cohort';
}

export function p7SignalLabel(signal: string): string {
  return formatProcurementSignal(signal).label;
}

/** Exact procuring-authority prefix token matching every P7 task title (`"${authority} · FY..."`). */
export function p7AuthorityFilterQuery(authority?: string | null): string {
  const clean = (authority || '').trim();
  return clean ? `${clean} ·` : '';
}

/** Concise bid-count readout for a P7 top-lead row, preserving qualified-only and 0 vs null hygiene. */
export function p7FormatLeadBids(lead: {
  bids_received?: number | null;
  technically_qualified_bids?: number | null;
  effective_bids?: number | null;
}): string {
  if (
    lead.bids_received != null &&
    lead.technically_qualified_bids != null &&
    lead.bids_received !== lead.technically_qualified_bids
  ) {
    return `${fmtCount(lead.bids_received)} rec → ${fmtCount(lead.technically_qualified_bids)} qual`;
  }
  if (lead.effective_bids != null) {
    return `${fmtCount(lead.effective_bids)} ${lead.effective_bids === 1 ? 'bid' : 'bids'}`;
  }
  if (lead.bids_received != null) {
    return `${fmtCount(lead.bids_received)} ${lead.bids_received === 1 ? 'bid' : 'bids'}`;
  }
  if (lead.technically_qualified_bids != null) {
    return `${fmtCount(lead.technically_qualified_bids)} qualified ${
      lead.technically_qualified_bids === 1 ? 'bid' : 'bids'
    }`;
  }
  return 'Bids unrecorded';
}

/** Honest display helper for Repeat-Winner Authority Share cell (never fabricates "0.0% single-bid"). */
export function p7FormatRepeatWinnerShare(w: {
  winner_share_by_count_pct?: number | null;
  share_by_count_pct?: number | null;
  winner_share_by_value_pct?: number | null;
  share_by_value_pct?: number | null;
  winner_total_wins?: number | null;
  total_wins?: number | null;
  win_count?: number | null;
  winner_single_bid_wins?: number | null;
  single_bid_wins?: number | null;
  tasks?: number | null;
}): { primary: string; secondary: string | null; barPct: number | null } {
  const shareCount = w.winner_share_by_count_pct ?? w.share_by_count_pct ?? null;
  const shareVal = w.winner_share_by_value_pct ?? w.share_by_value_pct ?? null;
  const wins = w.winner_total_wins ?? w.total_wins ?? w.win_count ?? 0;
  const singleWins = w.winner_single_bid_wins ?? w.single_bid_wins ?? 0;
  const tasksLabel = w.tasks != null && w.tasks > 0 ? `${fmtCount(w.tasks)} tasks` : null;

  if (shareCount != null) {
    return {
      primary: `${shareCount.toFixed(1)}% count`,
      secondary: shareVal != null ? `${shareVal.toFixed(1)}% value` : tasksLabel,
      barPct: Math.min(100, Math.max(4, shareCount)),
    };
  }
  if (shareVal != null) {
    return {
      primary: `${shareVal.toFixed(1)}% value`,
      secondary: tasksLabel,
      barPct: Math.min(100, Math.max(4, shareVal)),
    };
  }
  if (singleWins > 0 && wins > 0) {
    return {
      primary: `${fmtCount(singleWins)}/${fmtCount(wins)} single-bid`,
      secondary: tasksLabel,
      barPct: Math.min(100, Math.max(4, calcPct(singleWins, wins))),
    };
  }
  return {
    primary: '—',
    secondary: tasksLabel,
    barPct: null,
  };
}

/** Filter token for a 10-year row in P7 (`FY2021-22` when provided, else the year token). */
export function p7YearFilterQuery(year: string, fiscalYear?: string): string {
  const fy = (fiscalYear || '').trim();
  if (fy) return fy;
  return (year || '').trim();
}

/** Normalize Track A `year_range` (either `["2016", "2026"]` or `"2016–2026"`) into a display range. */
export function p7FormatYearRange(raw?: string | string[] | null): string {
  if (Array.isArray(raw)) {
    const clean = raw.map((x) => String(x || '').trim()).filter(Boolean);
    if (clean.length >= 2) return `${clean[0]}–${clean[clean.length - 1]}`;
    if (clean.length === 1) return clean[0];
    return '2016–2026';
  }
  if (typeof raw === 'string' && raw.trim()) {
    return raw.trim();
  }
  return '2016–2026';
}

export function p7NormalizeCohorts(
  byCohort?: Record<string, VisualOnlyOneBidCohortCounts | number> | null,
): {
  singleBidder: P7NormalizedCohort;
  repeatWinner: P7NormalizedCohort;
  both: P7NormalizedCohort;
  totalTasks: number;
  totalDone: number;
  totalClaimed: number;
  totalOpen: number;
} {
  const readBucket = (
    key: P7CohortKey,
    shortLabel: string,
    aliases: string[],
  ): P7NormalizedCohort => {
    let raw: VisualOnlyOneBidCohortCounts | number | undefined;
    for (const [k, v] of Object.entries(byCohort || {})) {
      const norm = k.trim().toLowerCase().replace(/[\s-]+/g, '_');
      if (aliases.includes(norm)) {
        raw = v;
        break;
      }
    }
    if (typeof raw === 'number') {
      const tasks = Math.max(0, raw);
      return {
        key,
        label: p7CohortLabel(key),
        shortLabel,
        tasks,
        done: 0,
        claimed: 0,
        open: tasks,
        pct: 0,
        leads: 0,
        flagged_inr_cr: 0,
      };
    }
    const obj = raw && typeof raw === 'object' ? raw : null;
    const tasks = Math.max(0, Number(obj?.tasks) || 0);
    const done = Math.max(0, Number(obj?.done) || 0);
    const claimed = Math.max(0, Number(obj?.claimed) || 0);
    const open =
      obj?.open != null ? Math.max(0, Number(obj.open) || 0) : Math.max(0, tasks - done - claimed);
    const pct =
      obj?.pct != null && Number.isFinite(Number(obj.pct))
        ? Math.max(0, Number(obj.pct))
        : calcPct(done, tasks);
    return {
      key,
      label: p7CohortLabel(key),
      shortLabel,
      tasks,
      done,
      claimed,
      open,
      pct,
      leads: Math.max(0, Number(obj?.leads) || 0),
      flagged_inr_cr: Math.max(0, Number(obj?.flagged_inr_cr) || 0),
    };
  };

  const singleBidder = readBucket('single_bidder', 'Single Bidder', ['single_bidder', 'sb']);
  const repeatWinner = readBucket('repeat_winner', 'Repeat Winner', ['repeat_winner', 'rw']);
  const both = readBucket('both', 'Both: Single Bid + Repeat', [
    'both',
    'bo',
    'single_bidder_repeat_winner',
  ]);

  return {
    singleBidder,
    repeatWinner,
    both,
    totalTasks: singleBidder.tasks + repeatWinner.tasks + both.tasks,
    totalDone: singleBidder.done + repeatWinner.done + both.done,
    totalClaimed: singleBidder.claimed + repeatWinner.claimed + both.claimed,
    totalOpen: singleBidder.open + repeatWinner.open + both.open,
  };
}

export function p7NormalizeSignals(bySignal?: Record<string, number> | null): {
  items: P7NormalizedSignal[];
  total: number;
} {
  const ordered: Array<{
    key: P7NormalizedSignal['key'];
    fillClass: string;
  }> = [
    { key: 'single_bidder_repeat_winner', fillClass: 'pv-p7-signal-fill--combined' },
    { key: 'unexplained_single_bidder', fillClass: 'pv-p7-signal-fill--single' },
    { key: 'repeat_winner_concentration', fillClass: 'pv-p7-signal-fill--repeat' },
    { key: 'short_window_or_process_gap', fillClass: 'pv-p7-signal-fill--process' },
  ];

  const counts = ordered.map(({ key }) => Math.max(0, Number(bySignal?.[key]) || 0));
  const total = counts.reduce((acc, n) => acc + n, 0);

  const items: P7NormalizedSignal[] = ordered.map(({ key, fillClass }, idx) => {
    const meta = formatProcurementSignal(key);
    const count = counts[idx];
    return {
      key,
      label: meta.label,
      shortLabel: meta.shortLabel,
      chipClass: meta.chipClass,
      fillClass,
      count,
      sharePct: calcPct(count, total),
    };
  });

  return { items, total };
}

export function p7FilterAuthorities(
  authorities: VisualOnlyOneBidAuthority[],
  query: string,
  cohortFilter: 'all' | P7CohortKey = 'all',
  jurisdictionFilter = 'all',
): VisualOnlyOneBidAuthority[] {
  const q = query.trim().toLowerCase();
  const jur = jurisdictionFilter.trim().toLowerCase();
  return authorities.filter((a) => {
    if (cohortFilter === 'single_bidder' && (a.single_bidder_tasks ?? 0) <= 0) return false;
    if (cohortFilter === 'repeat_winner' && (a.repeat_winner_tasks ?? 0) <= 0) return false;
    if (cohortFilter === 'both' && (a.both_tasks ?? 0) <= 0) return false;
    if (jur && jur !== 'all' && (a.jurisdiction || '').trim().toLowerCase() !== jur) return false;
    if (!q) return true;
    return (
      a.authority.toLowerCase().includes(q) ||
      (a.authority_key || '').toLowerCase().includes(q) ||
      (a.jurisdiction || '').toLowerCase().includes(q) ||
      (a.portal || '').toLowerCase().includes(q)
    );
  });
}

export function p7FilterRepeatWinners(
  winners: VisualOnlyOneBidRepeatWinner[],
  query: string,
  jurisdictionFilter = 'all',
): VisualOnlyOneBidRepeatWinner[] {
  const q = query.trim().toLowerCase();
  const jur = jurisdictionFilter.trim().toLowerCase();
  return winners.filter((w) => {
    if (jur && jur !== 'all' && (w.jurisdiction || '').trim().toLowerCase() !== jur) return false;
    if (!q) return true;
    return (
      w.winner_name.toLowerCase().includes(q) ||
      (w.normalized_winner_key || '').toLowerCase().includes(q) ||
      w.authority.toLowerCase().includes(q) ||
      (w.jurisdiction || '').toLowerCase().includes(q) ||
      (w.portal || '').toLowerCase().includes(q)
    );
  });
}

function p7ShortText(text?: string | null, maxLen = 140): string {
  const clean = (text || '').replace(/\s+/g, ' ').trim();
  if (!clean) return '—';
  return clean.length > maxLen ? `${clean.slice(0, maxLen - 1).trimEnd()}…` : clean;
}

export function OnlyOneBidVisualiser({
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
  const p7 = visualSummary?.only_one_bid;
  const workTag = getWorkTypeTag(undefined, mission.methodology_code);
  const slugPath = encodeURIComponent(mission.slug);

  const [selectedCohort, setSelectedCohort] = useState<'all' | P7CohortKey>('all');
  const [selectedJurisdiction, setSelectedJurisdiction] = useState('all');
  const [authorityQuery, setAuthorityQuery] = useState('');
  const [showAllAuthorities, setShowAllAuthorities] = useState(false);
  const [winnerQuery, setWinnerQuery] = useState('');
  const [showAllWinners, setShowAllWinners] = useState(false);

  const hook = p7?.hook?.trim() || P7_DEFAULT_HOOK;
  const yearRange = p7FormatYearRange(p7?.year_range);
  const totalTasks = p7?.total_tasks ?? mission.total_tasks ?? 0;
  const portalsCovered = p7?.portals_covered ?? 0;
  const authorities = p7?.top_authorities ?? [];
  const authoritiesCovered = p7?.authorities_covered ?? authorities.length;
  const repeatWinners = p7?.top_repeat_winners ?? [];
  const distinctRepeatWinners = p7?.distinct_repeat_winners ?? repeatWinners.length;
  const years = p7?.years ?? [];
  const jurisdictions = p7?.jurisdictions ?? [];
  const topLeads = p7?.top_leads ?? [];
  const anchor = p7?.anchor_case ?? null;

  const leads = p7?.leads ?? 0;
  const verified = p7?.verified ?? mission.verified_findings_count ?? 0;
  const inReview = p7?.peer_review ?? mission.peer_review_count ?? 0;
  const clearedTasks = p7?.cleared_tasks ?? mission.cleared_tasks_count ?? 0;
  const flaggedTotalCr = p7?.flagged_total_inr_cr ?? 0;
  const documentsUrl = p7?.documents_url || `/api/missions/${slugPath}/documents.csv`;
  const coverageUrl = p7?.coverage_url || `/api/missions/${slugPath}/coverage.json`;

  const cohorts = useMemo(() => p7NormalizeCohorts(p7?.by_cohort), [p7?.by_cohort]);
  const signals = useMemo(() => p7NormalizeSignals(p7?.by_signal), [p7?.by_signal]);

  const filteredAuthorities = useMemo(
    () => p7FilterAuthorities(authorities, authorityQuery, selectedCohort, selectedJurisdiction),
    [authorities, authorityQuery, selectedCohort, selectedJurisdiction],
  );
  const visibleAuthorities =
    showAllAuthorities ||
    authorityQuery.trim().length > 0 ||
    selectedCohort !== 'all' ||
    selectedJurisdiction !== 'all'
      ? filteredAuthorities
      : filteredAuthorities.slice(0, P7_INITIAL_AUTHORITIES);

  const filteredWinners = useMemo(
    () => p7FilterRepeatWinners(repeatWinners, winnerQuery, selectedJurisdiction),
    [repeatWinners, winnerQuery, selectedJurisdiction],
  );
  const visibleWinners =
    showAllWinners || winnerQuery.trim().length > 0 || selectedJurisdiction !== 'all'
      ? filteredWinners
      : filteredWinners.slice(0, P7_INITIAL_WINNERS);

  const cohortDenominator = cohorts.totalTasks || totalTasks;
  const singleCohortPct = calcPct(cohorts.singleBidder.tasks, cohortDenominator);
  const repeatCohortPct = calcPct(cohorts.repeatWinner.tasks, cohortDenominator);
  const bothCohortPct = calcPct(cohorts.both.tasks, cohortDenominator);

  const maxYearFlagged = Math.max(1, ...years.map((y) => y.flagged_inr_cr || 0));

  const anchorSignal = anchor?.signal_category
    ? formatProcurementSignal(anchor.signal_category)
    : null;
  const anchorMech = anchor?.single_bid_mechanism
    ? formatSingleBidMechanism(anchor.single_bid_mechanism)
    : null;
  const anchorTargetInr = anchor
    ? anchor.award_value_inr != null && anchor.award_value_inr > 0
      ? anchor.award_value_inr
      : anchor.tender_estimate_inr != null && anchor.tender_estimate_inr > 0
        ? anchor.tender_estimate_inr
        : anchor.target_value_inr_cr != null && anchor.target_value_inr_cr > 0
          ? anchor.target_value_inr_cr * 1e7
          : null
    : null;
  const anchorCohortInr = anchor
    ? anchor.winner_cohort_total_inr != null && anchor.winner_cohort_total_inr > 0
      ? anchor.winner_cohort_total_inr
      : anchor.winner_cohort_total_inr_cr != null && anchor.winner_cohort_total_inr_cr > 0
        ? anchor.winner_cohort_total_inr_cr * 1e7
        : null
    : null;
  const anchorPrimaryUrl = safeExternalUrl(anchor?.record_url || anchor?.source_url);
  const anchorSecondaryUrl = safeExternalUrl(anchor?.secondary_url);

  const cohortCards: Array<{
    bucket: P7NormalizedCohort;
    pct: number;
    btnClass: string;
    desc: string;
  }> = [
    {
      bucket: cohorts.singleBidder,
      pct: singleCohortPct,
      btnClass: 'pv-p7-cohort-btn--single',
      desc: 'One effective bid recorded',
    },
    {
      bucket: cohorts.repeatWinner,
      pct: repeatCohortPct,
      btnClass: 'pv-p7-cohort-btn--repeat',
      desc: 'Multi-award concentration cohort',
    },
    {
      bucket: cohorts.both,
      pct: bothCohortPct,
      btnClass: 'pv-p7-cohort-btn--both',
      desc: 'Single-bid award to repeat winner',
    },
  ];

  return (
    <section className="card card-pad pv-results" aria-label="Only One Bid procurement audit results">
      {/* Header + Summary Pills */}
      <div className="pv-results-head">
        <div className="pv-results-title-wrap">
          <span className="kicker">
            <BarChart3 size={12} aria-hidden="true" />
            Live results · {workTag.label}
          </span>
          <h2 className="pv-results-title">Only One Bid ({yearRange})</h2>
          <p className="pv-p6-hook">{hook}</p>
        </div>

        <div className="pv-summary-pills tabular">
          <span className="tag-mono">{fmtCount(totalTasks)} tender tasks</span>
          {portalsCovered > 0 ? (
            <span className="tag-mono">
              {fmtCount(portalsCovered)} {portalsCovered === 1 ? 'portal' : 'portals'}
            </span>
          ) : null}
          {authoritiesCovered > 0 ? (
            <span className="tag-mono">{fmtCount(authoritiesCovered)} authorities</span>
          ) : null}
          {distinctRepeatWinners > 0 ? (
            <span className="tag-mono">{fmtCount(distinctRepeatWinners)} repeat winners</span>
          ) : null}
          <span className="tag-mono">
            {fmtCount(leads)} {leads === 1 ? 'pattern lead' : 'pattern leads'}
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
          {clearedTasks > 0 ? (
            <span className={FINDING_TAGS.cleared.badgeClass}>
              {fmtCount(clearedTasks)} {FINDING_TAGS.cleared.label}
            </span>
          ) : null}
        </div>
      </div>

      {/* Headline KPIs + Interactive Cohort & 4-Signal Breakdown */}
      <div className="pv-p7-overview">
        <div className="pv-p7-kpi-grid">
          <div className="pv-overview-tile pv-p7-kpi--tasks">
            <span className="pv-overview-kicker">Procurement tasks ({yearRange})</span>
            <span className="pv-overview-num tabular">{fmtCount(totalTasks)}</span>
            <span className="pv-overview-sub tabular">
              {portalsCovered > 0 ? `${fmtCount(portalsCovered)} portals · ` : ''}
              {fmtCount(authoritiesCovered)} procuring authorities
            </span>
          </div>
          <div className="pv-overview-tile pv-p7-kpi--leads">
            <span className="pv-overview-kicker">Pattern leads &amp; cleared</span>
            <span className="pv-overview-num tabular">{fmtCount(leads)}</span>
            <span className="pv-overview-sub tabular">
              {fmtCount(verified)} verified · {fmtCount(inReview)} in review · {fmtCount(clearedTasks)} cleared
            </span>
          </div>
          <div className="pv-overview-tile pv-p7-kpi--winners">
            <span className="pv-overview-kicker">Distinct repeat winners</span>
            <span className="pv-overview-num tabular">{fmtCount(distinctRepeatWinners)}</span>
            <span className="pv-overview-sub tabular">
              Entity-normalized multi-award cohorts
            </span>
          </div>
          <div className="pv-overview-tile pv-p7-kpi--value">
            <span className="pv-overview-kicker">Flagged target value</span>
            <span className="pv-overview-num tabular">
              {flaggedTotalCr > 0 ? formatInrCr(flaggedTotalCr) : '₹0 Cr'}
            </span>
            <span className="pv-overview-sub">
              Target tenders only (never double-counted)
            </span>
          </div>
        </div>

        {/* Interactive Cohort Breakdown */}
        {cohorts.totalTasks > 0 ? (
          <div className="pv-p7-cohort-panel">
            <div className="pv-p7-cohort-head">
              <h3>Cohort breakdown (select a cohort to filter authorities)</h3>
              <div className="pv-mini-legend">
                <span>
                  <i className="pv-dot pv-p7-dot--single" aria-hidden="true" /> Single Bidder
                </span>
                <span>
                  <i className="pv-dot pv-p7-dot--repeat" aria-hidden="true" /> Repeat Winner
                </span>
                <span>
                  <i className="pv-dot pv-p7-dot--both" aria-hidden="true" /> Both
                </span>
                {selectedCohort !== 'all' ? (
                  <button
                    type="button"
                    className="btn btn-ghost btn-xs"
                    onClick={() => setSelectedCohort('all')}
                  >
                    <RotateCcw size={11} aria-hidden="true" />
                    <span>All cohorts</span>
                  </button>
                ) : null}
              </div>
            </div>

            <div
              className="pv-p7-cohort-bar"
              role="img"
              aria-label={`Procurement cohorts across ${fmtCount(cohorts.totalTasks)} tasks: ${fmtCount(cohorts.singleBidder.tasks)} Single Bidder (${fmtPct(singleCohortPct)}), ${fmtCount(cohorts.repeatWinner.tasks)} Repeat Winner (${fmtPct(repeatCohortPct)}), ${fmtCount(cohorts.both.tasks)} Both Single Bid and Repeat Winner (${fmtPct(bothCohortPct)})`}
            >
              {singleCohortPct > 0 ? (
                <span
                  className="pv-p7-seg pv-p7-seg--single"
                  style={{ width: `${singleCohortPct}%` }}
                  title={`Single Bidder: ${fmtCount(cohorts.singleBidder.tasks)} (${fmtPct(singleCohortPct)})`}
                />
              ) : null}
              {repeatCohortPct > 0 ? (
                <span
                  className="pv-p7-seg pv-p7-seg--repeat"
                  style={{ width: `${repeatCohortPct}%` }}
                  title={`Repeat Winner: ${fmtCount(cohorts.repeatWinner.tasks)} (${fmtPct(repeatCohortPct)})`}
                />
              ) : null}
              {bothCohortPct > 0 ? (
                <span
                  className="pv-p7-seg pv-p7-seg--both"
                  style={{ width: `${bothCohortPct}%` }}
                  title={`Both: Single Bid + Repeat Winner: ${fmtCount(cohorts.both.tasks)} (${fmtPct(bothCohortPct)})`}
                />
              ) : null}
            </div>

            <div className="pv-p7-cohort-cards" role="group" aria-label="Filter by procurement cohort">
              {cohortCards.map(({ bucket, pct, btnClass, desc }) => {
                const isSelected = selectedCohort === bucket.key;
                return (
                  <button
                    key={bucket.key}
                    type="button"
                    aria-pressed={isSelected}
                    className={`pv-p7-cohort-btn ${btnClass}`}
                    onClick={() =>
                      setSelectedCohort((prev) => (prev === bucket.key ? 'all' : bucket.key))
                    }
                  >
                    <span className="pv-p7-cohort-btn-top">
                      <span className="pv-p7-cohort-name">{bucket.label}</span>
                      <span className="pv-p7-cohort-share tabular">{fmtPct(pct)}</span>
                    </span>
                    <span className="pv-p7-cohort-num tabular">{fmtCount(bucket.tasks)} tasks</span>
                    <span className="pv-p7-cohort-sub tabular">
                      {fmtCount(bucket.done)}/{fmtCount(bucket.tasks)} checked ({fmtPct(bucket.pct)})
                      {bucket.claimed > 0 ? ` · ${fmtCount(bucket.claimed)} running` : ''}
                    </span>
                    <span className="pv-p7-cohort-sub">{desc}</span>
                  </button>
                );
              })}
            </div>
          </div>
        ) : null}

        {/* 4-Signal Breakdown */}
        <div className="pv-p7-cohort-panel">
          <div className="pv-p7-cohort-head">
            <h3>Signal categories (4 deterministic procurement checks)</h3>
            <span className="pv-chart-note tabular">
              {signals.total > 0
                ? `${fmtCount(signals.total)} flagged ${signals.total === 1 ? 'lead' : 'leads'} across 4 rule groups`
                : 'Counts populate as contributor agents verify procurement tasks'}
            </span>
          </div>
          <div className="pv-p7-signals-grid">
            {signals.items.map((sig) => (
              <div key={sig.key} className="pv-p7-signal-row">
                <div className="pv-p7-signal-top">
                  <span className={sig.chipClass}>{sig.label}</span>
                  <span className="pv-p7-signal-count tabular">
                    <strong>{fmtCount(sig.count)}</strong>
                    {signals.total > 0 ? ` (${fmtPct(sig.sharePct)})` : ''}
                  </span>
                </div>
                <span className="pv-p7-signal-track" aria-hidden="true">
                  <span
                    className={`pv-p7-signal-fill ${sig.fillClass}`}
                    style={{ width: `${sig.count > 0 ? Math.max(4, sig.sharePct) : 0}%` }}
                  />
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Featured Anchor Case Dossier Card */}
      {anchor ? (
        <article className="pv-p6-anchor" aria-label="Featured public procurement record">
          <div className="pv-p6-anchor-head">
            <div className="pv-p6-anchor-meta">
              <span className="pv-p6-anchor-eyebrow">Featured case · Official procurement record</span>
              {anchor.portal ? <span className="tag-mono">{anchor.portal}</span> : null}
              {anchor.jurisdiction ? <span className="tag-mono">{anchor.jurisdiction}</span> : null}
              <span className="tag-mono">{anchor.authority}</span>
              {anchor.fiscal_year ? (
                <span className="tag-mono tabular">{anchor.fiscal_year}</span>
              ) : null}
              {anchor.tender_id || anchor.ocid ? (
                <span className="tag-mono font-mono">{anchor.tender_id || anchor.ocid}</span>
              ) : null}
              {anchor.procurement_method ? (
                <span className="tag-mono">{anchor.procurement_method}</span>
              ) : null}
            </div>
            {anchorSignal ? (
              <span className={anchorSignal.chipClass}>{anchorSignal.label}</span>
            ) : null}
          </div>

          <h3 className="pv-p6-anchor-title">
            {anchor.tender_title || anchor.title || `${anchor.authority} — ${anchor.tender_id || 'Featured Tender'}`}
          </h3>

          {anchor.summary ? <p className="pv-p6-anchor-people">{anchor.summary}</p> : null}

          <div className="pv-p6-anchor-grid">
            <div className="pv-p6-anchor-col">
              <div className="pv-p6-anchor-label">01 · Bid competition</div>
              <p className="pv-p6-anchor-text tabular">
                {anchor.bids_received != null ? (
                  <>
                    <strong>{fmtCount(anchor.bids_received)}</strong>{' '}
                    {anchor.bids_received === 1 ? 'bid received' : 'bids received'}
                  </>
                ) : anchor.effective_bids != null ? (
                  <>
                    <strong>{fmtCount(anchor.effective_bids)}</strong> effective{' '}
                    {anchor.effective_bids === 1 ? 'bid' : 'bids'}
                  </>
                ) : (
                  'Bid count unrecorded in summary'
                )}
                {anchor.technically_qualified_bids != null
                  ? ` · ${fmtCount(anchor.technically_qualified_bids)} technically qualified`
                  : ''}
                {anchorMech ? ` (${anchorMech})` : ''}
                {anchor.bid_window_days != null
                  ? ` · ${fmtCount(anchor.bid_window_days)}-day bid window.`
                  : '.'}
              </p>
            </div>

            <div className="pv-p6-anchor-col">
              <div className="pv-p6-anchor-label">02 · Repeat-winner pattern</div>
              <p className="pv-p6-anchor-text tabular">
                <strong>{anchor.winner_name || 'Awardee unrecorded'}</strong>
                {anchor.winner_total_wins != null && anchor.winner_total_wins > 0
                  ? ` — ${fmtCount(anchor.winner_total_wins)} recorded awards at this authority`
                  : ''}
                {anchor.winner_single_bid_wins != null && anchor.winner_single_bid_wins > 0
                  ? ` (${fmtCount(anchor.winner_single_bid_wins)} single-bid)`
                  : ''}
                {anchor.winner_share_by_count_pct != null
                  ? ` · ${anchor.winner_share_by_count_pct.toFixed(1)}% share`
                  : ''}
                {anchor.consecutive_win_streak != null && anchor.consecutive_win_streak >= 2
                  ? ` · ${fmtCount(anchor.consecutive_win_streak)}-win streak`
                  : ''}
                .
              </p>
            </div>

            <div className="pv-p6-anchor-col">
              <div className="pv-p6-anchor-label">03 · Target value vs cohort &amp; rule</div>
              <p className="pv-p6-anchor-text tabular">
                <strong>
                  {formatProcurementAmountType(anchor.amount_type)}:{' '}
                  {anchorTargetInr != null ? formatInr(anchorTargetInr) : 'Unrecorded'}
                </strong>
                {anchorCohortInr != null
                  ? ` (Repeat cohort context: ${formatInr(anchorCohortInr)} — tracked separately).`
                  : '.'}
                {anchor.governing_instrument ? ` Benchmark: ${anchor.governing_instrument}.` : ''}
              </p>
            </div>
          </div>

          <div className="pv-p6-anchor-actions">
            {anchor.finding_id && onSelectFinding ? (
              <button
                type="button"
                className="btn btn-secondary btn-xs"
                onClick={() => onSelectFinding(anchor.finding_id!)}
              >
                Inspect full dossier
              </button>
            ) : onFilterTasks ? (
              <button
                type="button"
                className="btn btn-secondary btn-xs"
                onClick={() =>
                  onFilterTasks(anchor.tender_id || p7AuthorityFilterQuery(anchor.authority))
                }
              >
                Find task in queue
              </button>
            ) : null}
            {anchorPrimaryUrl ? (
              <a
                className="inline-link"
                href={anchorPrimaryUrl}
                target="_blank"
                rel="noopener noreferrer"
              >
                Official procurement record <ExternalLink size={11} aria-hidden="true" className="pv-ub-ext" />
              </a>
            ) : null}
            {anchorSecondaryUrl ? (
              <a
                className="inline-link"
                href={anchorSecondaryUrl}
                target="_blank"
                rel="noopener noreferrer"
              >
                Companion shard <ExternalLink size={11} aria-hidden="true" className="pv-ub-ext" />
              </a>
            ) : null}
          </div>
        </article>
      ) : null}

      {/* 10-Year Trend (2016–2026) + How to Read This & Official Source Downloads */}
      <div className="pv-results-split">
        <div className="pv-chart-card">
          <div className="pv-chart-subhead">
            <h3>10-year trend ({yearRange})</h3>
            <div className="pv-mini-legend">
              <span>
                <i className="pv-dot pv-p7-dot--single" aria-hidden="true" /> Single Bidder
              </span>
              <span>
                <i className="pv-dot pv-p7-dot--repeat" aria-hidden="true" /> Repeat Winner
              </span>
              <span>
                <i className="pv-dot pv-p7-dot--both" aria-hidden="true" /> Both
              </span>
            </div>
          </div>

          {years.length > 0 ? (
            <div className="pv-ub-years-wrap">
              <table className="pv-ub-table pv-ub-years">
                <caption className="pv-ub-caption">
                  Procurement tasks, cohort split, verification progress, and flagged target value per year.
                  {onFilterTasks ? ' Choose a year to filter tasks below.' : ''}
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Year</th>
                    <th scope="col">Cohort split &amp; progress</th>
                    <th scope="col" className="num">
                      Cohort counts
                    </th>
                    <th scope="col" className="num">
                      Leads / Flagged
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {years.map((y) => {
                    const total = Math.max(0, y.tasks);
                    const sb = Math.max(0, y.single_bidder_tasks ?? 0);
                    const rw = Math.max(0, y.repeat_winner_tasks ?? 0);
                    const bo = Math.max(0, y.both_tasks ?? 0);
                    const cohortSum = Math.max(total, sb + rw + bo);
                    const sbPct = calcPct(sb, cohortSum);
                    const rwPct = calcPct(rw, cohortSum);
                    const boPct = calcPct(bo, cohortSum);
                    const donePct = calcPct(y.done, total);
                    const yearFilter = p7YearFilterQuery(y.year, y.fiscal_year);

                    return (
                      <tr key={y.year}>
                        <th scope="row" className="pv-ub-year">
                          {onFilterTasks ? (
                            <button
                              type="button"
                              className="pv-ub-link"
                              onClick={() => onFilterTasks(yearFilter)}
                              aria-label={`Show tasks for ${y.fiscal_year || y.year}`}
                            >
                              {y.fiscal_year ? plainFiscalYear(y.fiscal_year) : y.year}
                            </button>
                          ) : (
                            <span>{y.fiscal_year ? plainFiscalYear(y.fiscal_year) : y.year}</span>
                          )}
                        </th>
                        <td className="pv-ub-years-checked">
                          <span
                            className="pv-ub-bar"
                            role="img"
                            aria-label={`${y.year}: ${fmtCount(sb)} single bidder, ${fmtCount(rw)} repeat winner, ${fmtCount(bo)} both; ${fmtCount(y.done)} of ${fmtCount(total)} tasks checked (${fmtPct(donePct)})`}
                          >
                            {sbPct > 0 ? (
                              <span
                                className="pv-ub-bar-seg pv-p7-seg--single"
                                style={{ width: `${Math.max(sbPct, 2)}%` }}
                              />
                            ) : null}
                            {rwPct > 0 ? (
                              <span
                                className="pv-ub-bar-seg pv-p7-seg--repeat"
                                style={{ width: `${Math.max(rwPct, 2)}%` }}
                              />
                            ) : null}
                            {boPct > 0 ? (
                              <span
                                className="pv-ub-bar-seg pv-p7-seg--both"
                                style={{ width: `${Math.max(boPct, 2)}%` }}
                              />
                            ) : null}
                          </span>
                          <span className="pv-ub-sub tabular">
                            {fmtCount(y.done)}/{fmtCount(total)} checked ({fmtPct(donePct)})
                            {y.claimed > 0 ? ` · ${fmtCount(y.claimed)} running` : ''}
                          </span>
                        </td>
                        <td className="num pv-ub-years-lines" data-label="Cohort counts">
                          <span className="pv-ub-cell-val">{fmtCount(total)} tasks</span>
                          <span className="pv-ub-sub">
                            {fmtCount(sb)} SB · {fmtCount(rw)} RW · {fmtCount(bo)} Both
                          </span>
                        </td>
                        <td className="num pv-ub-years-below" data-label="Leads / Flagged">
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
              Ten-year procurement coverage ({yearRange}) appears here once the hub loads the Only One Bid index.
            </div>
          )}

          {jurisdictions.length > 0 ? (
            <div className="pv-p7-jurisdictions" role="group" aria-label="Filter by jurisdiction">
              <span className="pv-p7-jur-label">Jurisdictions:</span>
              <button
                type="button"
                className={`btn btn-xs ${selectedJurisdiction === 'all' ? 'btn-primary' : 'btn-ghost'}`}
                onClick={() => setSelectedJurisdiction('all')}
              >
                All ({fmtCount(jurisdictions.length)})
              </button>
              {jurisdictions.map((j) => {
                const active = selectedJurisdiction === j.jurisdiction;
                return (
                  <button
                    key={j.jurisdiction}
                    type="button"
                    className={`btn btn-xs ${active ? 'btn-primary' : 'btn-ghost'}`}
                    onClick={() =>
                      setSelectedJurisdiction((prev) =>
                        prev === j.jurisdiction ? 'all' : j.jurisdiction,
                      )
                    }
                  >
                    {j.jurisdiction} ({fmtCount(j.tasks)})
                  </button>
                );
              })}
            </div>
          ) : null}
        </div>

        <div className="pv-chart-card pv-ub-guide">
          <div className="pv-chart-subhead">
            <h3>How to read this</h3>
          </div>
          <p>
            Every task re-computes bid counts, entity-normalized awardee shares, and bid windows
            directly from SHA-256-verified official Indian procurement disclosures ({yearRange})
            against <strong>GFR 2017 Rules 166 &amp; 173(xx)</strong>, <strong>CVC Single-Bid
            Guidelines</strong>, and State procurement rules where applicable.
          </p>
          <p>
            Single-bidder tenders with documented PAC/OEM, proprietary, or emergency exemptions,
            competitive multi-bid awards within concentration thresholds, and de minimis purchases
            under ₹5 lakh (GFR R154/155) are published as {FINDING_TAGS.cleared.label} with equal
            standing.
          </p>
          <p>
            To prevent double-counting, each lead&rsquo;s exposure counts <strong>only the target
            tender&rsquo;s own awarded or estimated value</strong>; multi-award cohort totals are
            always displayed separately for context. Every lead carries at least three lawful
            benign explanations and requires three independent peer verifications before reaching{' '}
            {FINDING_TAGS.verified.label}.
          </p>
          <div className="pv-ub-links">
            <a className="inline-link" href={documentsUrl}>
              Procurement sources (CSV)
            </a>
            <a className="inline-link" href={coverageUrl}>
              Coverage (JSON)
            </a>
            <a className="inline-link" href={P7_METHOD_URL} target="_blank" rel="noopener noreferrer">
              Method <ExternalLink size={11} aria-hidden="true" className="pv-ub-ext" />
            </a>
          </div>
          {p7?.generated_on ? (
            <p className="pv-chart-note">
              Procurement index built {p7.generated_on}
              {p7.as_of_date && p7.as_of_date !== p7.generated_on ? ` · as of ${p7.as_of_date}` : ''}.
            </p>
          ) : null}
        </div>
      </div>

      {/* Searchable Procuring Authorities Table */}
      <div className="pv-chart-card pv-p7-section-card">
        <div className="pv-chart-subhead">
          <h3>Procuring authorities ({fmtCount(authorities.length)})</h3>
          <span className="pv-chart-note">
            Click any authority to filter its exact tender tasks below
          </span>
        </div>

        {authorities.length > 0 ? (
          <>
            <div className="pv-p6-ministry-controls">
              <div className="pv-p6-search-wrap">
                <Search size={13} aria-hidden="true" className="pv-p6-search-icon" />
                <input
                  type="search"
                  className="input-field pv-p6-search"
                  value={authorityQuery}
                  onChange={(e) => setAuthorityQuery(e.target.value)}
                  placeholder={`Filter ${fmtCount(authorities.length)} authorities, states, or portals…`}
                  aria-label="Filter procuring authorities"
                />
              </div>
              <div className="pv-p6-filter-pills" role="group" aria-label="Filter authorities by cohort">
                {(['all', 'single_bidder', 'repeat_winner', 'both'] as const).map((c) => (
                  <button
                    key={c}
                    type="button"
                    aria-pressed={selectedCohort === c}
                    className={`btn btn-xs ${selectedCohort === c ? 'btn-primary' : 'btn-ghost'}`}
                    onClick={() => setSelectedCohort(c)}
                  >
                    {c === 'all'
                      ? 'All'
                      : c === 'single_bidder'
                        ? 'Single Bidder'
                        : c === 'repeat_winner'
                          ? 'Repeat Winner'
                          : 'Both'}
                  </button>
                ))}
              </div>
            </div>

            {visibleAuthorities.length > 0 ? (
              <div className="pv-ub-years-wrap">
                <table className="pv-ub-table pv-ub-years pv-p6-ministries-table">
                  <caption className="pv-ub-caption">
                    Procuring authorities covered in the 2016–2026 procurement dataset.
                    {onFilterTasks ? ' Choose an authority to filter its exact tasks below.' : ''}
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">Procuring authority</th>
                      <th scope="col">Cohort split &amp; checked</th>
                      <th scope="col" className="num">
                        Single-bid / Repeat
                      </th>
                      <th scope="col" className="num">
                        Leads / Flagged
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleAuthorities.map((a) => {
                      const total = Math.max(0, a.tasks);
                      const sb = Math.max(0, a.single_bidder_tasks ?? 0);
                      const rw = Math.max(0, a.repeat_winner_tasks ?? 0);
                      const bo = Math.max(0, a.both_tasks ?? 0);
                      const sumC = Math.max(total, sb + rw + bo);
                      const sbPct = calcPct(sb, sumC);
                      const rwPct = calcPct(rw, sumC);
                      const boPct = calcPct(bo, sumC);
                      const doneCount = a.done != null ? Math.max(0, a.done) : null;
                      const claimedCount = a.claimed != null ? Math.max(0, a.claimed) : 0;
                      const donePct = doneCount != null ? calcPct(doneCount, total) : 0;
                      const filterQuery = p7AuthorityFilterQuery(a.authority);

                      return (
                        <tr
                          key={a.authority_key || a.authority}
                          className={onFilterTasks ? 'pv-p6-ministry-row--interactive' : undefined}
                          onClick={
                            onFilterTasks
                              ? (e) => {
                                  const target = e.target as HTMLElement | null;
                                  if (target?.closest('a, button')) return;
                                  if (window.getSelection?.()?.toString()) return;
                                  onFilterTasks(filterQuery);
                                }
                              : undefined
                          }
                        >
                          <th scope="row" className="pv-ub-year pv-p7-authority-name">
                            {onFilterTasks ? (
                              <button
                                type="button"
                                className="pv-ub-link"
                                onClick={() => onFilterTasks(filterQuery)}
                                aria-label={`Show tasks for ${a.authority}`}
                              >
                                {a.authority}
                              </button>
                            ) : (
                              <span>{a.authority}</span>
                            )}
                            {a.jurisdiction || a.portal ? (
                              <span className="pv-ub-sub">
                                {[a.jurisdiction, a.portal].filter(Boolean).join(' · ')}
                              </span>
                            ) : null}
                          </th>
                          <td className="pv-ub-years-checked">
                            <span
                              className="pv-ub-bar"
                              role="img"
                              aria-label={`${a.authority}: ${fmtCount(sb)} single bidder, ${fmtCount(rw)} repeat winner, ${fmtCount(bo)} both${doneCount != null ? `; ${fmtCount(doneCount)} of ${fmtCount(total)} tasks checked` : `; ${fmtCount(total)} tender tasks`}`}
                            >
                              {sbPct > 0 ? (
                                <span
                                  className="pv-ub-bar-seg pv-p7-seg--single"
                                  style={{ width: `${Math.max(sbPct, 2)}%` }}
                                />
                              ) : null}
                              {rwPct > 0 ? (
                                <span
                                  className="pv-ub-bar-seg pv-p7-seg--repeat"
                                  style={{ width: `${Math.max(rwPct, 2)}%` }}
                                />
                              ) : null}
                              {boPct > 0 ? (
                                <span
                                  className="pv-ub-bar-seg pv-p7-seg--both"
                                  style={{ width: `${Math.max(boPct, 2)}%` }}
                                />
                              ) : null}
                            </span>
                            <span className="pv-ub-sub tabular">
                              {doneCount != null
                                ? `${fmtCount(doneCount)}/${fmtCount(total)} checked (${fmtPct(donePct)})${
                                    claimedCount > 0 ? ` · ${fmtCount(claimedCount)} running` : ''
                                  }`
                                : `${fmtCount(total)} tender tasks`}
                            </span>
                          </td>
                          <td className="num pv-ub-years-lines" data-label="Single-bid / Repeat">
                            <span className="pv-ub-cell-val">
                              {a.single_bid_rate_pct != null
                                ? `${a.single_bid_rate_pct.toFixed(1)}% SB rate`
                                : `${fmtCount(sb + bo)} single-bid`}
                            </span>
                            <span className="pv-ub-sub">
                              {fmtCount(sb)} SB · {fmtCount(rw)} RW · {fmtCount(bo)} Both
                            </span>
                          </td>
                          <td className="num pv-ub-years-below" data-label="Leads / Flagged">
                            <span className="pv-ub-cell-val">
                              {fmtCount(a.leads)} {a.leads === 1 ? 'lead' : 'leads'}
                            </span>
                            <span className="pv-ub-sub">
                              {a.flagged_inr_cr != null && a.flagged_inr_cr > 0
                                ? formatInrCr(a.flagged_inr_cr)
                                : `${fmtCount(total)} tasks`}
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
                No procuring authorities match the current search or cohort filter.
              </div>
            )}

            {authorityQuery.trim().length === 0 &&
            selectedCohort === 'all' &&
            selectedJurisdiction === 'all' &&
            filteredAuthorities.length > P7_INITIAL_AUTHORITIES ? (
              <div className="pv-list-more">
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => setShowAllAuthorities((v) => !v)}
                >
                  {showAllAuthorities
                    ? `Show top ${P7_INITIAL_AUTHORITIES} authorities`
                    : `Show all ${fmtCount(filteredAuthorities.length)} authorities`}
                </button>
              </div>
            ) : null}
          </>
        ) : (
          <div className="pv-empty-box">
            Procuring authority coverage appears here once the hub loads the procurement index.
          </div>
        )}
      </div>

      {/* Top Repeat-Winner Concentration Clusters (Strictly Neutral Wording) */}
      <div className="pv-chart-card pv-p7-section-card">
        <div className="pv-chart-subhead">
          <h3>Repeat-Winner Concentration Clusters ({fmtCount(repeatWinners.length)})</h3>
          <span className="pv-chart-note">
            Cohort totals shown for context — separate from individual tender exposure
          </span>
        </div>
        <p className="pv-p7-neutral-note">
          Statistical multi-award concentration patterns observed in published government award
          records. Repeat awards may reflect lawful rate contracts, specialised OEM/PAC capability,
          or competitive L1 bidding; inspect individual dossiers for statutory context and benign
          explanations.
        </p>

        {repeatWinners.length > 0 ? (
          <>
            <div className="pv-p6-ministry-controls">
              <div className="pv-p6-search-wrap">
                <Search size={13} aria-hidden="true" className="pv-p6-search-icon" />
                <input
                  type="search"
                  className="input-field pv-p6-search"
                  value={winnerQuery}
                  onChange={(e) => setWinnerQuery(e.target.value)}
                  placeholder={`Filter ${fmtCount(repeatWinners.length)} repeat-winner clusters or authorities…`}
                  aria-label="Filter repeat-winner concentration clusters"
                />
              </div>
            </div>

            {visibleWinners.length > 0 ? (
              <div className="table-wrap pv-ub-leads-wrap">
                <table className="ledger-table pv-ub-table pv-ub-leads-table">
                  <caption className="pv-ub-caption">
                    Top repeat-winner concentration clusters by authority across 2016–2026 procurement disclosures.
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">Awardee &amp; Procuring Authority</th>
                      <th scope="col" className="num">
                        Recorded Wins
                      </th>
                      <th scope="col" className="num">
                        Authority Share
                      </th>
                      <th scope="col" className="num">
                        Cohort Total (Context)
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleWinners.map((w, idx) => {
                      const wins = w.winner_total_wins ?? w.total_wins ?? w.win_count ?? 0;
                      const singleWins = w.winner_single_bid_wins ?? w.single_bid_wins ?? 0;
                      const shareInfo = p7FormatRepeatWinnerShare(w);
                      const cohortCr = w.cohort_total_inr_cr ?? w.total_value_inr_cr ?? null;
                      const cohortInr =
                        w.winner_cohort_total_inr != null && w.winner_cohort_total_inr > 0
                          ? w.winner_cohort_total_inr
                          : w.cohort_total_inr != null && w.cohort_total_inr > 0
                            ? w.cohort_total_inr
                            : cohortCr != null && cohortCr > 0
                              ? cohortCr * 1e7
                              : null;
                      const sampleIds = w.related_tender_ids ?? w.sample_tender_ids ?? [];
                      const authFilter = p7AuthorityFilterQuery(w.authority);

                      return (
                        <tr key={`${w.normalized_winner_key || w.winner_name}-${w.authority_key || w.authority}-${idx}`}>
                          <td className="pv-ub-line pv-p7-winner-col">
                            <strong>{w.winner_name}</strong>
                            <span className="pv-ub-sub">
                              {onFilterTasks ? (
                                <button
                                  type="button"
                                  className="pv-ub-link"
                                  onClick={() => onFilterTasks(authFilter)}
                                  aria-label={`Filter tasks for ${w.authority}`}
                                >
                                  {w.authority}
                                </button>
                              ) : (
                                w.authority
                              )}
                              {w.jurisdiction || w.portal
                                ? ` · ${[w.jurisdiction, w.portal].filter(Boolean).join(' · ')}`
                                : ''}
                            </span>
                            {sampleIds.length > 0 ? (
                              <span className="pv-ub-sub font-mono">
                                Tenders: {sampleIds.slice(0, 3).join(', ')}
                                {sampleIds.length > 3 ? ` +${sampleIds.length - 3}` : ''}
                              </span>
                            ) : null}
                          </td>
                          <td className="num tabular" data-label="Recorded wins">
                            <span className="pv-ub-cell-val">
                              <strong>{fmtCount(wins)}</strong> {wins === 1 ? 'award' : 'awards'}
                            </span>
                            <span className="pv-ub-sub">
                              {fmtCount(singleWins)} single-bid
                              {w.consecutive_win_streak != null && w.consecutive_win_streak >= 2
                                ? ` · ${fmtCount(w.consecutive_win_streak)}-win streak`
                                : ''}
                            </span>
                          </td>
                          <td className="num tabular pv-ub-lead-below" data-label="Authority share">
                            <span className="pv-ub-cell-val">{shareInfo.primary}</span>
                            {shareInfo.secondary ? (
                              <span className="pv-ub-sub">{shareInfo.secondary}</span>
                            ) : null}
                            {shareInfo.barPct != null ? (
                              <span className="pv-ub-mini-track" aria-hidden="true">
                                <span
                                  className="pv-ub-mini-fill"
                                  style={{ width: `${shareInfo.barPct}%` }}
                                />
                              </span>
                            ) : null}
                          </td>
                          <td className="num tabular" data-label="Cohort total (context)">
                            <span className="pv-ub-cell-val">
                              {cohortInr != null ? formatInr(cohortInr) : 'Unrecorded'}
                            </span>
                            {w.finding_id && onSelectFinding ? (
                              <span className="pv-ub-sub">
                                <button
                                  type="button"
                                  className="pv-ub-link"
                                  onClick={() => onSelectFinding(w.finding_id!)}
                                >
                                  Inspect lead
                                </button>
                              </span>
                            ) : w.year_range ? (
                              <span className="pv-ub-sub">{w.year_range}</span>
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
                {winnerQuery.trim().length > 0 ? (
                  <>No repeat-winner clusters match &ldquo;{winnerQuery.trim()}&rdquo;.</>
                ) : (
                  'No repeat-winner clusters match the selected jurisdiction filter.'
                )}
              </div>
            )}

            {winnerQuery.trim().length === 0 &&
            selectedJurisdiction === 'all' &&
            filteredWinners.length > P7_INITIAL_WINNERS ? (
              <div className="pv-list-more">
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => setShowAllWinners((v) => !v)}
                >
                  {showAllWinners
                    ? `Show top ${P7_INITIAL_WINNERS} clusters`
                    : `Show all ${fmtCount(filteredWinners.length)} clusters`}
                </button>
              </div>
            ) : null}
          </>
        ) : (
          <div className="pv-empty-box">
            Repeat-winner concentration clusters appear here once the procurement coverage index loads.
          </div>
        )}
      </div>

      {/* Reported Procurement Pattern Leads Table */}
      <div className="pv-ub-leads">
        <div className="pv-chart-subhead">
          <h3>Procurement pattern leads reported so far</h3>
          {topLeads.length > 0 ? (
            <span className="pv-chart-note">
              Target tender value separated from repeat-winner cohort total
            </span>
          ) : null}
        </div>
        {topLeads.length > 0 ? (
          <div className="table-wrap pv-ub-leads-wrap">
            <table className="ledger-table pv-ub-table pv-ub-leads-table">
              <caption className="pv-ub-caption">
                Single-bidder and repeat-winner procurement leads verified against official records.
                {onSelectFinding ? ' Open any row to read the full tender dossier and citations.' : ''}
              </caption>
              <thead>
                <tr>
                  <th scope="col">Authority &amp; Tender</th>
                  <th scope="col">Period &amp; Bids</th>
                  <th scope="col">Awardee &amp; Pattern</th>
                  <th scope="col" className="num">
                    Target Tender Value
                  </th>
                  <th scope="col">Audit Status</th>
                </tr>
              </thead>
              <tbody>
                {topLeads.map((lead) => {
                  const tag = getFindingTag(lead.status);
                  const sig = formatProcurementSignal(lead.signal_category);
                  const authLabel = lead.authority || lead.entity_name || 'Public Authority';
                  const targetInr =
                    lead.target_value_inr != null && lead.target_value_inr > 0
                      ? lead.target_value_inr
                      : lead.monetary_exposure_inr != null && lead.monetary_exposure_inr > 0
                        ? lead.monetary_exposure_inr
                        : (lead.target_value_inr_cr ?? lead.flagged_inr_cr ?? 0) > 0
                          ? (lead.target_value_inr_cr ?? lead.flagged_inr_cr ?? 0) * 1e7
                          : null;
                  const cohortInr =
                    lead.winner_cohort_total_inr != null && lead.winner_cohort_total_inr > 0
                      ? lead.winner_cohort_total_inr
                      : lead.winner_cohort_total_inr_cr != null && lead.winner_cohort_total_inr_cr > 0
                        ? lead.winner_cohort_total_inr_cr * 1e7
                        : null;
                  const mech = formatSingleBidMechanism(lead.single_bid_mechanism);

                  return (
                    <tr key={lead.finding_id}>
                      <td className="pv-ub-line pv-p7-lead-tender">
                        {onSelectFinding ? (
                          <button
                            type="button"
                            className="pv-ub-link"
                            onClick={() => onSelectFinding(lead.finding_id)}
                          >
                            {authLabel}
                            {lead.tender_id ? ` · ${lead.tender_id}` : ''}
                          </button>
                        ) : (
                          <span>
                            {authLabel}
                            {lead.tender_id ? ` · ${lead.tender_id}` : ''}
                          </span>
                        )}
                        <span className="pv-ub-sub">
                          <span className={sig.chipClass}>{sig.shortLabel}</span>
                          {lead.title ? ` · ${p7ShortText(lead.title, 85)}` : ''}
                        </span>
                      </td>
                      <td className="tabular pv-ub-lead-year pv-p7-lead-bids" data-label="Period & bids">
                        <span>{lead.fiscal_year ? plainFiscalYear(lead.fiscal_year) : '—'}</span>
                        <span className="pv-ub-sub">
                          {p7FormatLeadBids(lead)}
                          {mech ? ` · ${mech}` : ''}
                          {lead.bid_window_days != null ? ` · ${fmtCount(lead.bid_window_days)}d window` : ''}
                        </span>
                      </td>
                      <td className="pv-p7-lead-winner" data-label="Awardee & pattern">
                        <span>{lead.winner_name || '—'}</span>
                        <span className="pv-ub-sub tabular">
                          {lead.winner_total_wins != null && lead.winner_total_wins > 0
                            ? `${fmtCount(lead.winner_total_wins)} wins`
                            : ''}
                          {lead.winner_single_bid_wins != null && lead.winner_single_bid_wins > 0
                            ? ` (${fmtCount(lead.winner_single_bid_wins)} SB)`
                            : ''}
                          {lead.winner_share_by_count_pct != null && lead.winner_share_by_count_pct > 0
                            ? ` · ${lead.winner_share_by_count_pct.toFixed(1)}%`
                            : ''}
                          {lead.consecutive_win_streak != null && lead.consecutive_win_streak >= 2
                            ? ` · ${fmtCount(lead.consecutive_win_streak)}x streak`
                            : ''}
                        </span>
                      </td>
                      <td className="num tabular pv-ub-lead-below" data-label="Target tender value">
                        <strong>{targetInr != null ? formatInr(targetInr) : 'Unrecorded'}</strong>
                        {cohortInr != null ? (
                          <span className="pv-ub-sub">Cohort: {formatInr(cohortInr)}</span>
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
        ) : (
          <div className="pv-empty-box">
            Procurement pattern leads appear here as contributor agents verify tasks. Every entry
            cites the official tender record, repeat-winner cohort (where applicable), and
            jurisdiction-matched statutory benchmark with SHA-256 provenance.
          </div>
        )}
      </div>
    </section>
  );
}

/* ---------- 3H. P8 Sovereign Lies: launch promises vs. subsequent revisions across Union & State/UT schemes ---------- */

const P8_METHOD_URL =
  'https://github.com/aiwatchdogsindia/aiwatchdogs.in/blob/main/projects/sovereign-lies/METHODOLOGY.md';
export const P8_DEFAULT_HOOK =
  'The government made a promise when it launched the scheme. Did it keep it?';
const P8_INITIAL_JURISDICTIONS = 12;
const P8_INITIAL_SCHEMES = 12;

export type P8TierKey = 'union' | 'state' | 'ut';

export type P8MechanismKey =
  | 'retroactive_tax_reversal'
  | 'midstream_benefit_cut'
  | 'premature_discontinuation'
  | 'eligibility_narrowing'
  | 'lockin_or_exit_restriction'
  | 'arrears_or_unfunded_promise'
  | 'grandfathered_or_honored';

export interface P8NormalizedTier {
  key: P8TierKey;
  label: string;
  shortLabel: string;
  tasks: number;
  done: number;
  claimed: number;
  open: number;
  pct: number;
  leads: number;
  cleared: number;
  flagged_inr_cr: number;
}

export interface P8NormalizedMechanism {
  key: P8MechanismKey;
  label: string;
  shortLabel: string;
  chipClass: string;
  fillClass: string;
  isCleared: boolean;
  tasks: number;
  leads: number;
  sharePct: number;
}

export interface P8NormalizedCategory {
  category: string;
  label: string;
  shortLabel: string;
  tasks: number;
  done: number;
  leads: number;
  cleared: number;
  flagged_inr_cr: number;
}

export interface P8NormalizedImpact {
  key: string;
  label: string;
  shortLabel: string;
  chipClass: string;
  count: number;
  sharePct: number;
}

export function p8TierLabel(tier: string): string {
  const k = (tier || '').trim().toLowerCase();
  if (k === 'union' || k === 'central') return 'Union of India';
  if (k === 'state' || k === 'states') return 'States';
  if (k === 'ut' || k === 'uts' || k === 'union_territory') return 'Union Territories';
  return tier || 'Jurisdiction';
}

export function p8MechanismLabel(mechanism: string): string {
  return formatWalkbackMechanism(mechanism).label;
}

export function p8CategoryLabel(category: string): string {
  return formatSchemeCategory(category).label;
}

export function p8ImpactLabel(impact: string): string {
  return formatRetroactiveImpact(impact).label;
}

/** Clean search token to jump to a scheme's tasks in the project task queue. */
export function p8SchemeFilterQuery(schemeName?: string | null): string {
  return (schemeName || '').trim();
}

/** Exact jurisdiction prefix token matching every P8 task title (`"${jur_short} · ..."`). */
export function p8JurisdictionFilterQuery(
  jurisdiction?: string | null,
  stateOrUt?: string | null,
): string {
  const cleanState = (stateOrUt || '').trim();
  if (cleanState) return `${cleanState} ·`;
  const cleanJur = (jurisdiction || '').trim();
  if (!cleanJur) return '';
  if (/^state:/i.test(cleanJur)) {
    const name = cleanJur.slice(6).trim();
    return name ? `${name} ·` : '';
  }
  if (/^ut:/i.test(cleanJur)) {
    const name = cleanJur.slice(3).trim();
    return name ? `${name} ·` : '';
  }
  return `${cleanJur} ·`;
}

/** Clear active jurisdiction filter if switching to a tier that excludes it. */
export function p8ReconcileJurisdictionForTier(
  selectedJurisdiction: string,
  nextTier: 'all' | P8TierKey,
  jurisdictions: VisualSovereignLiesJurisdiction[],
): string {
  if (!selectedJurisdiction || selectedJurisdiction === 'all' || nextTier === 'all') {
    return selectedJurisdiction;
  }
  const matched = jurisdictions.find((j) => j.jurisdiction === selectedJurisdiction);
  if (matched) {
    return (matched.jurisdiction_level || '').trim().toLowerCase() === nextTier
      ? selectedJurisdiction
      : 'all';
  }
  const lower = selectedJurisdiction.trim().toLowerCase();
  const inferredTier: P8TierKey = lower.startsWith('state:')
    ? 'state'
    : lower.startsWith('ut:')
      ? 'ut'
      : 'union';
  return inferredTier === nextTier ? selectedJurisdiction : 'all';
}

export function p8NormalizeTiers(
  byTier?: Record<string, VisualSovereignLiesTierCounts | number> | null,
): {
  union: P8NormalizedTier;
  state: P8NormalizedTier;
  ut: P8NormalizedTier;
  totalTasks: number;
  totalDone: number;
  totalLeads: number;
  totalCleared: number;
} {
  const readTier = (
    key: P8TierKey,
    shortLabel: string,
    aliases: string[],
  ): P8NormalizedTier => {
    let raw: VisualSovereignLiesTierCounts | number | undefined;
    for (const [k, v] of Object.entries(byTier || {})) {
      const norm = k.trim().toLowerCase().replace(/[\s-]+/g, '_');
      if (aliases.includes(norm)) {
        raw = v;
        break;
      }
    }
    if (typeof raw === 'number') {
      const tasks = Math.max(0, raw);
      return {
        key,
        label: p8TierLabel(key),
        shortLabel,
        tasks,
        done: 0,
        claimed: 0,
        open: tasks,
        pct: 0,
        leads: 0,
        cleared: 0,
        flagged_inr_cr: 0,
      };
    }
    const obj = raw && typeof raw === 'object' ? raw : null;
    const tasks = Math.max(0, Number(obj?.tasks) || 0);
    const done = Math.max(0, Number(obj?.done) || 0);
    const claimed = Math.max(0, Number(obj?.claimed) || 0);
    const open =
      obj?.open != null ? Math.max(0, Number(obj.open) || 0) : Math.max(0, tasks - done - claimed);
    const pct =
      obj?.pct != null && Number.isFinite(Number(obj.pct))
        ? Math.max(0, Number(obj.pct))
        : calcPct(done, tasks);
    return {
      key,
      label: p8TierLabel(key),
      shortLabel,
      tasks,
      done,
      claimed,
      open,
      pct,
      leads: Math.max(0, Number(obj?.leads) || 0),
      cleared: Math.max(0, Number(obj?.cleared) || 0),
      flagged_inr_cr: Math.max(0, Number(obj?.flagged_inr_cr) || 0),
    };
  };

  const union = readTier('union', 'Union', ['union', 'central', 'union_of_india']);
  const state = readTier('state', 'States', ['state', 'states']);
  const ut = readTier('ut', 'UTs', ['ut', 'uts', 'union_territory', 'union_territories']);

  return {
    union,
    state,
    ut,
    totalTasks: union.tasks + state.tasks + ut.tasks,
    totalDone: union.done + state.done + ut.done,
    totalLeads: union.leads + state.leads + ut.leads,
    totalCleared: union.cleared + state.cleared + ut.cleared,
  };
}

export function p8NormalizeMechanisms(
  byMechanism?: Record<string, VisualSovereignLiesMechanismCounts | number> | null,
  bySignal?: Record<string, number> | null,
): {
  items: P8NormalizedMechanism[];
  totalTasks: number;
  totalLeads: number;
} {
  const ordered: Array<{
    key: P8MechanismKey;
    ruleKey: string;
    fillClass: string;
  }> = [
    {
      key: 'retroactive_tax_reversal',
      ruleKey: 'P8-RETROACTIVE-TAX-REVERSAL',
      fillClass: 'pv-p8-mech-fill--retro-tax',
    },
    {
      key: 'midstream_benefit_cut',
      ruleKey: 'P8-MIDSTREAM-BENEFIT-CUT',
      fillClass: 'pv-p8-mech-fill--cut',
    },
    {
      key: 'premature_discontinuation',
      ruleKey: 'P8-PREMATURE-DISCONTINUATION',
      fillClass: 'pv-p8-mech-fill--discontinued',
    },
    {
      key: 'eligibility_narrowing',
      ruleKey: 'P8-ELIGIBILITY-NARROWING',
      fillClass: 'pv-p8-mech-fill--eligibility',
    },
    {
      key: 'lockin_or_exit_restriction',
      ruleKey: 'P8-LOCKIN-OR-EXIT-RESTRICTION',
      fillClass: 'pv-p8-mech-fill--lockin',
    },
    {
      key: 'arrears_or_unfunded_promise',
      ruleKey: 'P8-ARREARS-OR-UNFUNDED-PROMISE',
      fillClass: 'pv-p8-mech-fill--arrears',
    },
    {
      key: 'grandfathered_or_honored',
      ruleKey: 'CLEARED',
      fillClass: 'pv-p8-mech-fill--honored',
    },
  ];

  const rows = ordered.map(({ key, ruleKey, fillClass }) => {
    const rawMech = byMechanism?.[key];
    let tasks = 0;
    let leads = 0;
    if (typeof rawMech === 'number') {
      tasks = Math.max(0, rawMech);
    } else if (rawMech && typeof rawMech === 'object') {
      tasks = Math.max(0, Number(rawMech.tasks) || 0);
      leads = Math.max(0, Number(rawMech.leads) || 0);
    }
    if (leads === 0 && bySignal) {
      const sigVal = bySignal[key] ?? bySignal[ruleKey] ?? bySignal[ruleKey.toLowerCase()];
      if (sigVal != null) {
        leads = Math.max(0, Number(sigVal) || 0);
      }
    }
    const meta = formatWalkbackMechanism(key);
    return {
      key,
      label: meta.label,
      shortLabel: meta.shortLabel,
      chipClass: meta.chipClass,
      fillClass,
      isCleared: meta.isCleared,
      tasks,
      leads,
    };
  });

  const totalTasks = rows.reduce((acc, r) => acc + r.tasks, 0);
  const totalLeads = rows.reduce((acc, r) => acc + r.leads, 0);
  const denominator = totalTasks > 0 ? totalTasks : totalLeads;

  const items: P8NormalizedMechanism[] = rows.map((r) => ({
    ...r,
    sharePct: calcPct(totalTasks > 0 ? r.tasks : r.leads, denominator),
  }));

  return { items, totalTasks, totalLeads };
}

export function p8NormalizeCategories(
  byCategory?: VisualSovereignLiesCategory[] | null,
): P8NormalizedCategory[] {
  if (!Array.isArray(byCategory)) return [];
  return byCategory
    .map((c) => {
      const meta = formatSchemeCategory(c.category);
      return {
        category: meta.key,
        label: c.label?.trim() || meta.label,
        shortLabel: meta.shortLabel,
        tasks: Math.max(0, Number(c.tasks) || 0),
        done: Math.max(0, Number(c.done) || 0),
        leads: Math.max(0, Number(c.leads) || 0),
        cleared: Math.max(0, Number(c.cleared) || 0),
        flagged_inr_cr: Math.max(0, Number(c.flagged_inr_cr) || 0),
      };
    })
    .sort((a, b) => b.tasks - a.tasks || a.label.localeCompare(b.label));
}

export function p8NormalizeRetroactiveImpacts(
  byImpact?: Record<string, number> | null,
): {
  items: P8NormalizedImpact[];
  total: number;
} {
  const keys = [
    'retroactive_existing_holders',
    'mid_stream_active_scheme',
    'prospective_discontinuation',
    'eligibility_narrowing',
    'unfulfilled_arrears',
    'grandfathered_or_honored',
  ] as const;

  const counts = keys.map((k) => Math.max(0, Number(byImpact?.[k]) || 0));
  const total = counts.reduce((acc, n) => acc + n, 0);

  const items: P8NormalizedImpact[] = keys.map((k, idx) => {
    const meta = formatRetroactiveImpact(k);
    const count = counts[idx];
    return {
      key: k,
      label: meta.label,
      shortLabel: meta.shortLabel,
      chipClass: meta.chipClass,
      count,
      sharePct: calcPct(count, total),
    };
  });

  return { items, total };
}

export function p8FilterJurisdictions(
  jurisdictions: VisualSovereignLiesJurisdiction[],
  query: string,
  tierFilter: 'all' | P8TierKey = 'all',
): VisualSovereignLiesJurisdiction[] {
  const q = query.trim().toLowerCase();
  return jurisdictions.filter((j) => {
    const lvl = (j.jurisdiction_level || '').trim().toLowerCase();
    if (tierFilter !== 'all' && lvl !== tierFilter) return false;
    if (!q) return true;
    const display = formatJurisdictionLabel(j.jurisdiction, j.state_or_ut, j.jurisdiction_level);
    return (
      j.jurisdiction.toLowerCase().includes(q) ||
      (j.state_or_ut || '').toLowerCase().includes(q) ||
      display.toLowerCase().includes(q) ||
      lvl.includes(q)
    );
  });
}

export function p8FilterSchemes(
  schemes: VisualSovereignLiesScheme[],
  query: string,
  tierFilter: 'all' | P8TierKey = 'all',
  categoryFilter = 'all',
  mechanismFilter = 'all',
  jurisdictionFilter = 'all',
): VisualSovereignLiesScheme[] {
  const q = query.trim().toLowerCase();
  const cat = categoryFilter.trim().toLowerCase();
  const mech = mechanismFilter.trim().toLowerCase();
  const jur = jurisdictionFilter.trim().toLowerCase();

  return schemes.filter((s) => {
    const lvl = (s.jurisdiction_level || '').trim().toLowerCase();
    if (tierFilter !== 'all' && lvl !== tierFilter) return false;
    if (cat && cat !== 'all' && (s.scheme_category || '').trim().toLowerCase() !== cat) {
      return false;
    }
    if (mech && mech !== 'all' && formatWalkbackMechanism(s.walkback_mechanism).key !== mech) {
      return false;
    }
    if (jur && jur !== 'all' && (s.jurisdiction || '').trim().toLowerCase() !== jur) {
      return false;
    }
    if (!q) return true;
    const jurDisplay = formatJurisdictionLabel(s.jurisdiction, s.state_or_ut, s.jurisdiction_level);
    return (
      s.scheme_name.toLowerCase().includes(q) ||
      (s.scheme_slug || '').toLowerCase().includes(q) ||
      s.jurisdiction.toLowerCase().includes(q) ||
      jurDisplay.toLowerCase().includes(q) ||
      formatSchemeCategory(s.scheme_category).label.toLowerCase().includes(q) ||
      formatWalkbackMechanism(s.walkback_mechanism).label.toLowerCase().includes(q)
    );
  });
}

export function p8FilterLeads(
  leads: VisualSovereignLiesLead[],
  tierFilter: 'all' | P8TierKey = 'all',
  categoryFilter = 'all',
  mechanismFilter = 'all',
  jurisdictionFilter = 'all',
): VisualSovereignLiesLead[] {
  const cat = categoryFilter.trim().toLowerCase();
  const mech = mechanismFilter.trim().toLowerCase();
  const jur = jurisdictionFilter.trim().toLowerCase();

  return leads.filter((l) => {
    const lvl = (l.jurisdiction_level || '').trim().toLowerCase();
    if (tierFilter !== 'all' && lvl && lvl !== tierFilter) return false;
    if (cat && cat !== 'all' && (l.scheme_category || '').trim().toLowerCase() !== cat) {
      return false;
    }
    if (
      mech &&
      mech !== 'all' &&
      formatWalkbackMechanism(l.walkback_mechanism || l.signal_category).key !== mech
    ) {
      return false;
    }
    if (jur && jur !== 'all' && (l.jurisdiction || '').trim().toLowerCase() !== jur) {
      return false;
    }
    return true;
  });
}

function p8ShortText(text?: string | null, maxLen = 140): string {
  const clean = (text || '').replace(/\s+/g, ' ').trim();
  if (!clean) return '—';
  return clean.length > maxLen ? `${clean.slice(0, maxLen - 1).trimEnd()}…` : clean;
}

export function SovereignLiesVisualiser({
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
  const p8 = visualSummary?.sovereign_lies;
  const workTag = getWorkTypeTag(undefined, mission.methodology_code);
  const slugPath = encodeURIComponent(mission.slug);

  const [selectedTier, setSelectedTier] = useState<'all' | P8TierKey>('all');
  const [selectedCategory, setSelectedCategory] = useState('all');
  const [selectedMechanism, setSelectedMechanism] = useState('all');
  const [selectedJurisdiction, setSelectedJurisdiction] = useState('all');
  const [jurisdictionQuery, setJurisdictionQuery] = useState('');
  const [showAllJurisdictions, setShowAllJurisdictions] = useState(false);
  const [schemeQuery, setSchemeQuery] = useState('');
  const [showAllSchemes, setShowAllSchemes] = useState(false);

  const hook = p8?.hook?.trim() || P8_DEFAULT_HOOK;
  const totalTasks = p8?.total_tasks ?? mission.total_tasks ?? 0;
  const uniqueSchemesCovered = p8?.unique_schemes_covered ?? p8?.top_schemes?.length ?? 0;
  const jurisdictions = p8?.jurisdictions ?? [];
  const statesAndUtsCovered =
    p8?.states_and_uts_covered ??
    jurisdictions.filter((j) => (j.jurisdiction_level || '').toLowerCase() !== 'union').length;
  const sgbTasksCount = p8?.sgb_tasks_count ?? 0;
  const clearedEmbedded = p8?.cleared_control_tasks_embedded ?? 0;
  const leads = p8?.leads ?? 0;
  const verified = p8?.verified ?? mission.verified_findings_count ?? 0;
  const inReview = p8?.peer_review ?? mission.peer_review_count ?? 0;
  const clearedTasks = p8?.cleared_tasks ?? mission.cleared_tasks_count ?? 0;
  const flaggedTotalCr = p8?.flagged_total_inr_cr ?? 0;

  const topSchemes = p8?.top_schemes ?? [];
  const topLeads = p8?.top_leads ?? [];
  const sgbAnchor = p8?.sgb_anchor_case ?? null;
  const documentsUrl = p8?.documents_url || `/api/missions/${slugPath}/documents.csv`;
  const coverageUrl = p8?.coverage_url || `/api/missions/${slugPath}/coverage.json`;

  const tiers = useMemo(
    () => p8NormalizeTiers(p8?.by_jurisdiction_level),
    [p8?.by_jurisdiction_level],
  );
  const mechanisms = useMemo(
    () => p8NormalizeMechanisms(p8?.by_mechanism, p8?.by_signal),
    [p8?.by_mechanism, p8?.by_signal],
  );
  const categories = useMemo(
    () => p8NormalizeCategories(p8?.by_category),
    [p8?.by_category],
  );
  const impacts = useMemo(
    () => p8NormalizeRetroactiveImpacts(p8?.by_retroactive_impact),
    [p8?.by_retroactive_impact],
  );

  const filteredJurisdictions = useMemo(
    () => p8FilterJurisdictions(jurisdictions, jurisdictionQuery, selectedTier),
    [jurisdictions, jurisdictionQuery, selectedTier],
  );
  const visibleJurisdictions =
    showAllJurisdictions || jurisdictionQuery.trim().length > 0 || selectedTier !== 'all'
      ? filteredJurisdictions
      : filteredJurisdictions.slice(0, P8_INITIAL_JURISDICTIONS);

  const filteredSchemes = useMemo(
    () =>
      p8FilterSchemes(
        topSchemes,
        schemeQuery,
        selectedTier,
        selectedCategory,
        selectedMechanism,
        selectedJurisdiction,
      ),
    [
      topSchemes,
      schemeQuery,
      selectedTier,
      selectedCategory,
      selectedMechanism,
      selectedJurisdiction,
    ],
  );
  const visibleSchemes =
    showAllSchemes ||
    schemeQuery.trim().length > 0 ||
    selectedTier !== 'all' ||
    selectedCategory !== 'all' ||
    selectedMechanism !== 'all' ||
    selectedJurisdiction !== 'all'
      ? filteredSchemes
      : filteredSchemes.slice(0, P8_INITIAL_SCHEMES);

  const filteredLeads = useMemo(
    () =>
      p8FilterLeads(
        topLeads,
        selectedTier,
        selectedCategory,
        selectedMechanism,
        selectedJurisdiction,
      ),
    [topLeads, selectedTier, selectedCategory, selectedMechanism, selectedJurisdiction],
  );

  const hasActiveFilters =
    selectedTier !== 'all' ||
    selectedCategory !== 'all' ||
    selectedMechanism !== 'all' ||
    selectedJurisdiction !== 'all';

  const handleSelectTier = (nextTier: 'all' | P8TierKey) => {
    setSelectedTier(nextTier);
    setSelectedJurisdiction((prev) =>
      p8ReconcileJurisdictionForTier(prev, nextTier, jurisdictions),
    );
  };

  const resetAllFilters = () => {
    setSelectedTier('all');
    setSelectedCategory('all');
    setSelectedMechanism('all');
    setSelectedJurisdiction('all');
  };

  const tierDenominator = tiers.totalTasks || totalTasks;
  const unionPct = calcPct(tiers.union.tasks, tierDenominator);
  const statePct = calcPct(tiers.state.tasks, tierDenominator);
  const utPct = calcPct(tiers.ut.tasks, tierDenominator);

  const anchorMechanism = sgbAnchor?.walkback_mechanism
    ? formatWalkbackMechanism(sgbAnchor.walkback_mechanism)
    : null;
  const anchorImpact = sgbAnchor?.retroactive_impact
    ? formatRetroactiveImpact(sgbAnchor.retroactive_impact)
    : null;
  const anchorElapsed = formatElapsedPromiseSpan(sgbAnchor?.days_from_promise_to_walkback);
  const anchorPrimaryUrl = safeExternalUrl(sgbAnchor?.source_url);
  const anchorSecondaryUrl = safeExternalUrl(sgbAnchor?.secondary_url);
  const anchorPortalUrl = safeExternalUrl(sgbAnchor?.portal_url);

  const tierCards: Array<{
    bucket: P8NormalizedTier;
    pct: number;
    btnClass: string;
    desc: string;
  }> = [
    {
      bucket: tiers.union,
      pct: unionPct,
      btnClass: 'pv-p8-tier-btn--union',
      desc: 'Central acts, RBI bonds, FinMin & Union Ministries',
    },
    {
      bucket: tiers.state,
      pct: statePct,
      btnClass: 'pv-p8-tier-btn--state',
      desc: 'State budgets, GOs, guarantees, subsidies & CAG audits',
    },
    {
      bucket: tiers.ut,
      pct: utPct,
      btnClass: 'pv-p8-tier-btn--ut',
      desc: 'NCT of Delhi, J&K, Puducherry, Chandigarh & UTs',
    },
  ];

  return (
    <section className="card card-pad pv-results" aria-label="Sovereign Lies scheme promise audit results">
      {/* Header + Summary Pills */}
      <div className="pv-results-head">
        <div className="pv-results-title-wrap">
          <span className="kicker">
            <BarChart3 size={12} aria-hidden="true" />
            Live results · {workTag.label}
          </span>
          <h2 className="pv-results-title">Sovereign Lies: Promise vs. Walk-Back Ledger</h2>
          <p className="pv-p6-hook">{hook}</p>
        </div>

        <div className="pv-summary-pills tabular">
          <span className="tag-mono">
            {fmtCount(totalTasks)} {totalTasks === 1 ? 'scheme task' : 'scheme tasks'}
          </span>
          {uniqueSchemesCovered > 0 ? (
            <span className="tag-mono">
              {fmtCount(uniqueSchemesCovered)} {uniqueSchemesCovered === 1 ? 'scheme' : 'schemes'}
            </span>
          ) : null}
          {statesAndUtsCovered > 0 ? (
            <span className="tag-mono">Union + {fmtCount(statesAndUtsCovered)} States &amp; UTs</span>
          ) : null}
          {sgbTasksCount > 0 ? (
            <span className="p8-sgb-chip">
              {fmtCount(sgbTasksCount)} {sgbTasksCount === 1 ? 'SGB task' : 'SGB tasks'}
            </span>
          ) : null}
          <span className="tag-mono">
            {fmtCount(leads)} {leads === 1 ? 'divergence lead' : 'divergence leads'}
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
          {clearedTasks > 0 ? (
            <span className={FINDING_TAGS.cleared.badgeClass}>
              {fmtCount(clearedTasks)} {FINDING_TAGS.cleared.label}
            </span>
          ) : null}
        </div>
      </div>

      {/* Headline KPIs */}
      <div className="pv-p8-overview">
        <div className="pv-p8-kpi-grid">
          <div className="pv-overview-tile pv-p8-kpi--tasks">
            <span className="pv-overview-kicker">Tracked scheme tasks</span>
            <span className="pv-overview-num tabular">{fmtCount(totalTasks)}</span>
            <span className="pv-overview-sub tabular">
              {uniqueSchemesCovered > 0 ? `${fmtCount(uniqueSchemesCovered)} schemes · ` : ''}
              {statesAndUtsCovered > 0
                ? `Union + ${fmtCount(statesAndUtsCovered)} States/UTs`
                : 'Union & State/UT coverage'}
            </span>
          </div>
          <div className="pv-overview-tile pv-p8-kpi--sgb">
            <span className="pv-overview-kicker">Sovereign Gold Bond cohort</span>
            <span className="pv-overview-num tabular">{fmtCount(sgbTasksCount)}</span>
            <span className="pv-overview-sub">
              Flagship anchor · Sec 47(viic) &amp; Finance Act 2026
            </span>
          </div>
          <div className="pv-overview-tile pv-p8-kpi--leads">
            <span className="pv-overview-kicker">Documented leads &amp; cleared</span>
            <span className="pv-overview-num tabular">{fmtCount(leads)}</span>
            <span className="pv-overview-sub tabular">
              {fmtCount(verified)} verified · {fmtCount(inReview)} in review · {fmtCount(clearedTasks)} cleared
              {clearedEmbedded > 0 ? ` (${fmtCount(clearedEmbedded)} honored controls)` : ''}
            </span>
          </div>
          <div className="pv-overview-tile pv-p8-kpi--value">
            <span className="pv-overview-kicker">Published scheme exposure</span>
            <span className="pv-overview-num tabular">
              {flaggedTotalCr > 0 ? formatInrCr(flaggedTotalCr) : 'Documentary'}
            </span>
            <span className="pv-overview-sub">
              Officially published amounts only; statutory changes tracked verbatim
            </span>
          </div>
        </div>

        {/* Flagship Sovereign Gold Bond (SGB) Anchor Spotlight */}
        {sgbAnchor ? (
          <article
            className="pv-p8-sgb-anchor"
            aria-label="Sovereign Gold Bond flagship anchor comparison"
          >
            <div className="pv-p6-anchor-head">
              <div className="pv-p6-anchor-meta">
                <span className="p8-sgb-chip">Flagship Anchor · Sovereign Gold Bond (SGB)</span>
                <span className="p8-tier-chip p8-tier-chip--union">
                  {formatJurisdictionLabel(sgbAnchor.jurisdiction, null, 'union')}
                </span>
                {sgbAnchor.authority ? (
                  <span className="tag-mono">{sgbAnchor.authority}</span>
                ) : null}
                {sgbAnchor.tranche_or_phase ? (
                  <span className="tag-mono">{sgbAnchor.tranche_or_phase}</span>
                ) : null}
                {anchorImpact ? (
                  <span className={anchorImpact.chipClass}>{anchorImpact.label}</span>
                ) : null}
              </div>
              {anchorMechanism ? (
                <span className={anchorMechanism.chipClass}>{anchorMechanism.label}</span>
              ) : null}
            </div>

            <h3 className="pv-p6-anchor-title">{sgbAnchor.scheme_name}</h3>

            {sgbAnchor.affected_group ? (
              <p className="pv-p6-anchor-people">
                <strong>Affected group:</strong> {sgbAnchor.affected_group}
              </p>
            ) : null}

            {(sgbAnchor.promised_metric || sgbAnchor.revised_metric) ? (
              <div className="pv-p8-sgb-delta tabular">
                <span>
                  <strong>Original terms:</strong> {sgbAnchor.promised_metric || 'Documented at launch'}
                </span>
                <span aria-hidden="true">→</span>
                <span>
                  <strong>Revised terms:</strong> {sgbAnchor.revised_metric || 'Amended subsequently'}
                </span>
                {anchorElapsed ? (
                  <span className="tag-mono">{anchorElapsed}</span>
                ) : null}
              </div>
            ) : null}

            <div className="pv-p8-sgb-grid">
              <div className="pv-p8-sgb-col pv-p8-sgb-col--promise">
                <div className="pv-p8-sgb-col-head">
                  <span className="pv-p6-anchor-label" style={{ marginBottom: 0 }}>
                    01 · Original Launch Promise
                  </span>
                  {sgbAnchor.promise_date ? (
                    <span className="pv-p8-sgb-date font-mono tabular">{sgbAnchor.promise_date}</span>
                  ) : null}
                </div>
                <p className="pv-p6-anchor-text">{sgbAnchor.promise_summary}</p>
                {sgbAnchor.promise_document_ref ? (
                  <span className="pv-p8-sgb-ref font-mono">{sgbAnchor.promise_document_ref}</span>
                ) : null}
              </div>

              <div className="pv-p8-sgb-col pv-p8-sgb-col--walkback">
                <div className="pv-p8-sgb-col-head">
                  <span className="pv-p6-anchor-label" style={{ marginBottom: 0 }}>
                    02 · Subsequent Walk-Back
                  </span>
                  {sgbAnchor.walkback_date ? (
                    <span className="pv-p8-sgb-date font-mono tabular">{sgbAnchor.walkback_date}</span>
                  ) : null}
                </div>
                <p className="pv-p6-anchor-text">{sgbAnchor.walkback_summary}</p>
                {sgbAnchor.walkback_document_ref ? (
                  <span className="pv-p8-sgb-ref font-mono">{sgbAnchor.walkback_document_ref}</span>
                ) : null}
              </div>
            </div>

            <div className="pv-p6-anchor-actions">
              {sgbAnchor.finding_id && onSelectFinding ? (
                <button
                  type="button"
                  className="btn btn-secondary btn-xs"
                  onClick={() => onSelectFinding(sgbAnchor.finding_id!)}
                >
                  Inspect SGB dossier
                </button>
              ) : onFilterTasks ? (
                <button
                  type="button"
                  className="btn btn-secondary btn-xs"
                  onClick={() => onFilterTasks('Sovereign Gold Bond')}
                >
                  Find SGB tasks in queue
                </button>
              ) : null}
              {anchorPrimaryUrl ? (
                <a
                  className="inline-link"
                  href={anchorPrimaryUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Launch notification <ExternalLink size={11} aria-hidden="true" className="pv-ub-ext" />
                </a>
              ) : null}
              {anchorSecondaryUrl ? (
                <a
                  className="inline-link"
                  href={anchorSecondaryUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Finance Act / Walk-back record <ExternalLink size={11} aria-hidden="true" className="pv-ub-ext" />
                </a>
              ) : null}
              {anchorPortalUrl &&
              anchorPortalUrl !== anchorPrimaryUrl &&
              anchorPortalUrl !== anchorSecondaryUrl ? (
                <a
                  className="inline-link"
                  href={anchorPortalUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Official portal <ExternalLink size={11} aria-hidden="true" className="pv-ub-ext" />
                </a>
              ) : null}
              {sgbAnchor.source_sha256 ? (
                <span className="tag-mono font-mono" style={{ fontSize: 11 }}>
                  sha256:{sgbAnchor.source_sha256.slice(0, 10)}
                </span>
              ) : null}
            </div>
          </article>
        ) : (
          <div className="pv-empty-box">
            Sovereign Gold Bond (SGB) flagship anchor comparison loads automatically from the hub summary index.
          </div>
        )}

        {/* Interactive Jurisdiction Tier Selector (Union vs States vs UTs) */}
        {tiers.totalTasks > 0 ? (
          <div className="pv-p7-cohort-panel">
            <div className="pv-p7-cohort-head">
              <h3>Jurisdiction tier (Union vs. States &amp; UTs — select to filter explorer)</h3>
              <div className="pv-mini-legend">
                <span>
                  <i className="pv-dot pv-p8-dot--union" aria-hidden="true" /> Union
                </span>
                <span>
                  <i className="pv-dot pv-p8-dot--state" aria-hidden="true" /> States
                </span>
                <span>
                  <i className="pv-dot pv-p8-dot--ut" aria-hidden="true" /> UTs
                </span>
                {selectedTier !== 'all' ? (
                  <button
                    type="button"
                    className="btn btn-ghost btn-xs"
                    onClick={() => handleSelectTier('all')}
                  >
                    <RotateCcw size={11} aria-hidden="true" />
                    <span>All tiers</span>
                  </button>
                ) : null}
              </div>
            </div>

            <div
              className="pv-p8-tier-bar"
              role="img"
              aria-label={`Jurisdiction split across ${fmtCount(tiers.totalTasks)} tasks: ${fmtCount(tiers.union.tasks)} Union (${fmtPct(unionPct)}), ${fmtCount(tiers.state.tasks)} States (${fmtPct(statePct)}), ${fmtCount(tiers.ut.tasks)} Union Territories (${fmtPct(utPct)})`}
            >
              {unionPct > 0 ? (
                <span
                  className="pv-p8-seg pv-p8-seg--union"
                  style={{ width: `${unionPct}%` }}
                  title={`Union of India: ${fmtCount(tiers.union.tasks)} (${fmtPct(unionPct)})`}
                />
              ) : null}
              {statePct > 0 ? (
                <span
                  className="pv-p8-seg pv-p8-seg--state"
                  style={{ width: `${statePct}%` }}
                  title={`States: ${fmtCount(tiers.state.tasks)} (${fmtPct(statePct)})`}
                />
              ) : null}
              {utPct > 0 ? (
                <span
                  className="pv-p8-seg pv-p8-seg--ut"
                  style={{ width: `${utPct}%` }}
                  title={`Union Territories: ${fmtCount(tiers.ut.tasks)} (${fmtPct(utPct)})`}
                />
              ) : null}
            </div>

            <div className="pv-p8-tier-cards" role="group" aria-label="Filter by jurisdiction tier">
              {tierCards.map(({ bucket, pct, btnClass, desc }) => {
                const isSelected = selectedTier === bucket.key;
                return (
                  <button
                    key={bucket.key}
                    type="button"
                    aria-pressed={isSelected}
                    className={`pv-p7-cohort-btn ${btnClass}`}
                    onClick={() =>
                      handleSelectTier(selectedTier === bucket.key ? 'all' : bucket.key)
                    }
                  >
                    <span className="pv-p7-cohort-btn-top">
                      <span className="pv-p7-cohort-name">{bucket.label}</span>
                      <span className="pv-p7-cohort-share tabular">{fmtPct(pct)}</span>
                    </span>
                    <span className="pv-p7-cohort-num tabular">
                      {fmtCount(bucket.tasks)} {bucket.tasks === 1 ? 'task' : 'tasks'}
                    </span>
                    <span className="pv-p7-cohort-sub tabular">
                      {fmtCount(bucket.done)}/{fmtCount(bucket.tasks)} checked ({fmtPct(bucket.pct)})
                      {bucket.leads > 0
                        ? ` · ${fmtCount(bucket.leads)} ${bucket.leads === 1 ? 'lead' : 'leads'}`
                        : ''}
                      {bucket.cleared > 0 ? ` · ${fmtCount(bucket.cleared)} cleared` : ''}
                    </span>
                    <span className="pv-p7-cohort-sub">{desc}</span>
                  </button>
                );
              })}
            </div>
          </div>
        ) : null}

        {/* Interactive Walk-Back Mechanism & Scheme Category Explorer */}
        <div className="pv-p7-cohort-panel">
          <div className="pv-p7-cohort-head">
            <h3>Walk-back mechanisms (7 deterministic categories — select to filter)</h3>
            <span className="pv-chart-note tabular">
              {mechanisms.totalTasks > 0
                ? `${fmtCount(mechanisms.totalTasks)} tasks across 6 walk-back mechanisms + honored controls`
                : 'Counts populate from the hub summary index'}
            </span>
          </div>

          <div className="pv-p8-mech-grid" role="group" aria-label="Filter by walk-back mechanism">
            {mechanisms.items.map((m) => {
              const isSelected = selectedMechanism === m.key;
              return (
                <button
                  key={m.key}
                  type="button"
                  aria-pressed={isSelected}
                  className={`pv-p8-mech-btn${m.isCleared ? ' pv-p8-mech-btn--cleared' : ''}`}
                  onClick={() =>
                    setSelectedMechanism((prev) => (prev === m.key ? 'all' : m.key))
                  }
                >
                  <div className="pv-p7-signal-top">
                    <span className={m.chipClass}>{m.label}</span>
                    <span className="pv-p7-signal-count tabular">
                      <strong>{fmtCount(m.tasks)}</strong> {m.tasks === 1 ? 'task' : 'tasks'}
                      {m.leads > 0
                        ? ` · ${fmtCount(m.leads)} ${m.leads === 1 ? 'lead' : 'leads'}`
                        : ''}
                    </span>
                  </div>
                  <span className="pv-p7-signal-track" aria-hidden="true">
                    <span
                      className={`pv-p7-signal-fill ${m.fillClass}`}
                      style={{ width: `${m.sharePct > 0 ? Math.max(3, m.sharePct) : 0}%` }}
                    />
                  </span>
                </button>
              );
            })}
          </div>

          {/* Scheme Categories Filter Pills */}
          {categories.length > 0 ? (
            <div className="pv-p8-cat-pills" role="group" aria-label="Filter by scheme category">
              <span className="pv-p7-jur-label">Scheme categories:</span>
              <button
                type="button"
                aria-pressed={selectedCategory === 'all'}
                className={`btn btn-xs ${selectedCategory === 'all' ? 'btn-primary' : 'btn-ghost'}`}
                onClick={() => setSelectedCategory('all')}
              >
                All ({fmtCount(categories.length)})
              </button>
              {categories.map((cat) => {
                const active = selectedCategory === cat.category;
                return (
                  <button
                    key={cat.category}
                    type="button"
                    aria-pressed={active}
                    className={`btn btn-xs ${active ? 'btn-primary' : 'btn-ghost'}`}
                    onClick={() =>
                      setSelectedCategory((prev) => (prev === cat.category ? 'all' : cat.category))
                    }
                  >
                    {cat.shortLabel} ({fmtCount(cat.tasks)})
                  </button>
                );
              })}
            </div>
          ) : null}

          {/* Retroactive Impact Distribution Strip */}
          {impacts.total > 0 ? (
            <div className="pv-p8-impact-strip" aria-label="Retroactive impact distribution">
              <span className="pv-p7-jur-label">Impact scope:</span>
              {impacts.items
                .filter((imp) => imp.count > 0)
                .map((imp) => (
                  <span key={imp.key} className={imp.chipClass}>
                    {imp.shortLabel}: <strong className="tabular">{fmtCount(imp.count)}</strong>
                  </span>
                ))}
            </div>
          ) : null}

          {hasActiveFilters ? (
            <div className="pv-p8-active-filters">
              <span>
                Active filters:{' '}
                {[
                  selectedTier !== 'all' ? p8TierLabel(selectedTier) : null,
                  selectedJurisdiction !== 'all'
                    ? formatJurisdictionLabel(selectedJurisdiction)
                    : null,
                  selectedCategory !== 'all' ? p8CategoryLabel(selectedCategory) : null,
                  selectedMechanism !== 'all' ? p8MechanismLabel(selectedMechanism) : null,
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </span>
              <button type="button" className="btn btn-ghost btn-xs" onClick={resetAllFilters}>
                <RotateCcw size={11} aria-hidden="true" />
                <span>Reset all filters</span>
              </button>
            </div>
          ) : null}
        </div>
      </div>

      {/* Two-Column Split: Union + State/UT Breakdown + How to Read This */}
      <div className="pv-results-split">
        <div className="pv-chart-card">
          <div className="pv-chart-subhead">
            <h3>Union, States &amp; UTs ({fmtCount(jurisdictions.length)})</h3>
            <span className="pv-chart-note">
              Click a jurisdiction to filter schemes &amp; leads below
            </span>
          </div>

          {jurisdictions.length > 0 ? (
            <>
              <div className="pv-p6-ministry-controls">
                <div className="pv-p6-search-wrap">
                  <Search size={13} aria-hidden="true" className="pv-p6-search-icon" />
                  <input
                    type="search"
                    className="input-field pv-p6-search"
                    value={jurisdictionQuery}
                    onChange={(e) => setJurisdictionQuery(e.target.value)}
                    placeholder={`Filter ${fmtCount(jurisdictions.length)} jurisdictions (Union, States, UTs)…`}
                    aria-label="Filter Union, State, and UT jurisdictions"
                  />
                </div>
                <div className="pv-p6-filter-pills" role="group" aria-label="Filter jurisdictions by tier">
                  {(['all', 'union', 'state', 'ut'] as const).map((t) => (
                    <button
                      key={t}
                      type="button"
                      aria-pressed={selectedTier === t}
                      className={`btn btn-xs ${selectedTier === t ? 'btn-primary' : 'btn-ghost'}`}
                      onClick={() => handleSelectTier(t)}
                    >
                      {t === 'all' ? 'All' : t === 'union' ? 'Union' : t === 'state' ? 'States' : 'UTs'}
                    </button>
                  ))}
                </div>
              </div>

              {visibleJurisdictions.length > 0 ? (
                <div className="pv-ub-years-wrap">
                  <table className="pv-ub-table pv-ub-years pv-p6-ministries-table">
                    <caption className="pv-ub-caption">
                      Scheme promise-vs-walk-back coverage across Union of India, States, and Union Territories.
                    </caption>
                    <thead>
                      <tr>
                        <th scope="col">Jurisdiction</th>
                        <th scope="col">Tasks &amp; progress</th>
                        <th scope="col" className="num">
                          Leads / Cleared
                        </th>
                        <th scope="col" className="num">
                          Queue
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {visibleJurisdictions.map((j) => {
                        const display = formatJurisdictionLabel(
                          j.jurisdiction,
                          j.state_or_ut,
                          j.jurisdiction_level,
                        );
                        const lvl = (j.jurisdiction_level || 'union').toLowerCase();
                        const total = Math.max(0, j.tasks);
                        const done = Math.max(0, j.done ?? 0);
                        const donePct = calcPct(done, total);
                        const isSelected = selectedJurisdiction === j.jurisdiction;
                        const queueToken = p8JurisdictionFilterQuery(j.jurisdiction, j.state_or_ut);
                        const tierClass =
                          lvl === 'state'
                            ? 'p8-tier-chip--state'
                            : lvl === 'ut'
                              ? 'p8-tier-chip--ut'
                              : 'p8-tier-chip--union';
                        const segClass =
                          lvl === 'state'
                            ? 'pv-p8-seg--state'
                            : lvl === 'ut'
                              ? 'pv-p8-seg--ut'
                              : 'pv-p8-seg--union';

                        return (
                          <tr
                            key={j.jurisdiction}
                            className="pv-p6-ministry-row--interactive"
                            style={isSelected ? { background: 'var(--sunk)' } : undefined}
                            onClick={(e) => {
                              const target = e.target as HTMLElement | null;
                              if (target?.closest('a, button')) return;
                              setSelectedJurisdiction((prev) =>
                                prev === j.jurisdiction ? 'all' : j.jurisdiction,
                              );
                            }}
                          >
                            <th scope="row" className="pv-ub-year pv-p8-jur-name">
                              <button
                                type="button"
                                className="pv-ub-link"
                                aria-pressed={isSelected}
                                onClick={() =>
                                  setSelectedJurisdiction((prev) =>
                                    prev === j.jurisdiction ? 'all' : j.jurisdiction,
                                  )
                                }
                              >
                                {display}
                              </button>
                              <span className="pv-ub-sub">
                                <span className={`p8-tier-chip ${tierClass}`} style={{ fontSize: 10.5, padding: '0 6px' }}>
                                  {lvl === 'union' ? 'Union' : lvl === 'ut' ? 'UT' : 'State'}
                                </span>
                              </span>
                            </th>
                            <td className="pv-ub-years-checked">
                              <span
                                className="pv-ub-bar"
                                role="img"
                                aria-label={`${display}: ${fmtCount(done)} of ${fmtCount(total)} tasks checked`}
                              >
                                {donePct > 0 ? (
                                  <span
                                    className={`pv-ub-bar-seg ${segClass}`}
                                    style={{ width: `${Math.max(donePct, 1.5)}%` }}
                                  />
                                ) : null}
                              </span>
                              <span className="pv-ub-sub tabular">
                                {fmtCount(total)} {total === 1 ? 'task' : 'tasks'} · {fmtCount(done)} checked ({fmtPct(donePct)})
                              </span>
                            </td>
                            <td className="num pv-ub-years-lines" data-label="Leads / Cleared">
                              <span className="pv-ub-cell-val">
                                {fmtCount(j.leads)} {j.leads === 1 ? 'lead' : 'leads'}
                              </span>
                              <span className="pv-ub-sub">
                                {(j.cleared ?? 0) > 0
                                  ? `${fmtCount(j.cleared)} cleared`
                                  : j.flagged_inr_cr && j.flagged_inr_cr > 0
                                    ? formatInrCr(j.flagged_inr_cr)
                                    : 'Documentary'}
                              </span>
                            </td>
                            <td className="num pv-ub-years-below" data-label="Queue">
                              {onFilterTasks ? (
                                <button
                                  type="button"
                                  className="pv-ub-link"
                                  onClick={() => onFilterTasks(queueToken)}
                                  aria-label={`Filter task queue for ${display}`}
                                >
                                  Tasks →
                                </button>
                              ) : (
                                <span className="pv-ub-sub">{fmtCount(total)}</span>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="pv-empty-box">
                  No jurisdictions match &ldquo;{jurisdictionQuery || p8TierLabel(selectedTier)}&rdquo;.
                </div>
              )}

              {jurisdictionQuery.trim().length === 0 &&
              selectedTier === 'all' &&
              filteredJurisdictions.length > P8_INITIAL_JURISDICTIONS ? (
                <div className="pv-list-more">
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    onClick={() => setShowAllJurisdictions((v) => !v)}
                  >
                    {showAllJurisdictions
                      ? `Show top ${P8_INITIAL_JURISDICTIONS} jurisdictions`
                      : `Show all ${fmtCount(filteredJurisdictions.length)} jurisdictions`}
                  </button>
                </div>
              ) : null}
            </>
          ) : (
            <div className="pv-empty-box">
              Union, State, and UT scheme coverage appears here once the hub loads the Sovereign Lies index.
            </div>
          )}
        </div>

        <div className="pv-chart-card pv-ub-guide">
          <div className="pv-chart-subhead">
            <h3>How to read this</h3>
          </div>
          <p>
            Every task downloads and SHA-256-verifies both the <strong>original launch notification,
            statute, or scheme guideline</strong> and the <strong>subsequent amendment, circular,
            Finance Act, or CAG/Parliamentary record</strong>. If any official file fails hash verification
            or lacks the required verbatim evidence tokens, the check fails closed as{' '}
            {FINDING_TAGS.not_enough_data.label}.
          </p>
          <p>
            Schemes whose launch commitments were honored or where existing subscribers were explicitly
            grandfathered intact are published as {FINDING_TAGS.cleared.label} with equal standing
            alongside documented walk-back leads.
          </p>
          <p>
            Every record uses strictly neutral documentary framing under <strong>BNS Sec 356</strong>,
            recording concrete lawful explanations (statutory parliamentary authority, fiscal
            sustainability, arbitrage prevention, or budgetary reprioritisation), and requires three
            independent peer verifications before reaching {FINDING_TAGS.verified.label}.
          </p>
          <div className="pv-ub-links">
            <a className="inline-link" href={documentsUrl}>
              Scheme sources (CSV)
            </a>
            <a className="inline-link" href={coverageUrl}>
              Coverage (JSON)
            </a>
            <a className="inline-link" href={P8_METHOD_URL} target="_blank" rel="noopener noreferrer">
              Method <ExternalLink size={11} aria-hidden="true" className="pv-ub-ext" />
            </a>
          </div>
          {p8?.generated_on ? (
            <p className="pv-chart-note">
              Sovereign Lies index built {p8.generated_on}
              {p8.as_of_date && p8.as_of_date !== p8.generated_on ? ` · as of ${p8.as_of_date}` : ''}.
            </p>
          ) : null}
        </div>
      </div>

      {/* Interactive Tracked Schemes Explorer */}
      <div className="pv-chart-card pv-p7-section-card">
        <div className="pv-chart-subhead">
          <h3>
            Tracked schemes ({fmtCount(filteredSchemes.length)}
            {filteredSchemes.length !== topSchemes.length
              ? ` of ${fmtCount(topSchemes.length)}`
              : ''}
            )
          </h3>
          <span className="pv-chart-note">
            Filter by jurisdiction, category, or mechanism — click any scheme to view its tasks
          </span>
        </div>

        {topSchemes.length > 0 ? (
          <>
            <div className="pv-p6-ministry-controls">
              <div className="pv-p6-search-wrap">
                <Search size={13} aria-hidden="true" className="pv-p6-search-icon" />
                <input
                  type="search"
                  className="input-field pv-p6-search"
                  value={schemeQuery}
                  onChange={(e) => setSchemeQuery(e.target.value)}
                  placeholder={`Search ${fmtCount(topSchemes.length)} schemes (e.g. Sovereign Gold Bond, FAME-II, Ladki Bahin, OPS)…`}
                  aria-label="Search tracked Union and State schemes"
                />
              </div>
              {hasActiveFilters || schemeQuery.trim().length > 0 ? (
                <button
                  type="button"
                  className="btn btn-ghost btn-xs"
                  onClick={() => {
                    resetAllFilters();
                    setSchemeQuery('');
                  }}
                >
                  <RotateCcw size={11} aria-hidden="true" />
                  <span>Reset</span>
                </button>
              ) : null}
            </div>

            {visibleSchemes.length > 0 ? (
              <div className="table-wrap pv-ub-leads-wrap">
                <table className="ledger-table pv-ub-table pv-ub-leads-table">
                  <caption className="pv-ub-caption">
                    Indian Union and State/UT government schemes indexed in Project 8 with launch-promise vs. walk-back classification.
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">Scheme &amp; Jurisdiction</th>
                      <th scope="col">Category</th>
                      <th scope="col">Mechanism &amp; Impact</th>
                      <th scope="col" className="num">
                        Tasks / Leads
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleSchemes.map((s) => {
                      const mech = formatWalkbackMechanism(s.walkback_mechanism);
                      const imp = formatRetroactiveImpact(s.retroactive_impact);
                      const cat = formatSchemeCategory(s.scheme_category);
                      const jur = formatJurisdictionLabel(
                        s.jurisdiction,
                        s.state_or_ut,
                        s.jurisdiction_level,
                      );
                      const filterQ = p8SchemeFilterQuery(s.scheme_name);

                      return (
                        <tr key={`${s.scheme_slug}-${s.jurisdiction}`}>
                          <td className="pv-ub-line pv-p8-scheme-col">
                            {onFilterTasks ? (
                              <button
                                type="button"
                                className="pv-ub-link"
                                onClick={() => onFilterTasks(filterQ)}
                              >
                                {s.scheme_name}
                              </button>
                            ) : (
                              <span>{s.scheme_name}</span>
                            )}
                            <span className="pv-ub-sub">
                              {jur}
                              {s.is_sgb_anchor ? (
                                <>
                                  {' · '}
                                  <span className="p8-sgb-chip" style={{ fontSize: 10.5, padding: '0 6px' }}>
                                    SGB Anchor
                                  </span>
                                </>
                              ) : null}
                            </span>
                          </td>
                          <td data-label="Category">
                            <span className="tag-mono">{cat.label}</span>
                          </td>
                          <td data-label="Mechanism & impact">
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
                              <span className={mech.chipClass}>{mech.label}</span>
                              <span className={imp.chipClass}>{imp.shortLabel}</span>
                            </div>
                          </td>
                          <td className="num tabular" data-label="Tasks / Leads">
                            <span className="pv-ub-cell-val">
                              {fmtCount(s.tasks)} {s.tasks === 1 ? 'task' : 'tasks'}
                            </span>
                            <span className="pv-ub-sub">
                              {fmtCount(s.leads)} {s.leads === 1 ? 'lead' : 'leads'}
                              {(s.cleared ?? 0) > 0 ? ` · ${fmtCount(s.cleared)} cleared` : ''}
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
                No tracked schemes match the current filter combination.
              </div>
            )}

            {schemeQuery.trim().length === 0 &&
            !hasActiveFilters &&
            filteredSchemes.length > P8_INITIAL_SCHEMES ? (
              <div className="pv-list-more">
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => setShowAllSchemes((v) => !v)}
                >
                  {showAllSchemes
                    ? `Show top ${P8_INITIAL_SCHEMES} schemes`
                    : `Show all ${fmtCount(filteredSchemes.length)} schemes`}
                </button>
              </div>
            ) : null}
          </>
        ) : (
          <div className="pv-empty-box">
            Scheme-by-scheme coverage appears here once the hub loads the Sovereign Lies corpus index.
          </div>
        )}
      </div>

      {/* Reported Promise-vs-Walk-Back Leads Table */}
      <div className="pv-ub-leads">
        <div className="pv-chart-subhead">
          <h3>
            Promise-vs-walk-back leads reported so far
            {hasActiveFilters && topLeads.length > 0
              ? ` (${fmtCount(filteredLeads.length)} matching filters)`
              : ''}
          </h3>
          {filteredLeads.length > 0 ? (
            <span className="pv-chart-note">
              Original launch promise vs. subsequent statutory revision with SHA-256 provenance
            </span>
          ) : null}
        </div>
        {filteredLeads.length > 0 ? (
          <div className="table-wrap pv-ub-leads-wrap">
            <table className="ledger-table pv-ub-table pv-ub-leads-table">
              <caption className="pv-ub-caption">
                Verified and in-review scheme promise-vs-walk-back dossiers.
                {onSelectFinding
                  ? ' Open any row to read the full two-column documentary dossier, citations, and lawful benign explanations.'
                  : ''}
              </caption>
              <thead>
                <tr>
                  <th scope="col">Scheme &amp; Authority</th>
                  <th scope="col">Jurisdiction &amp; Timeline</th>
                  <th scope="col">Mechanism &amp; Metric Delta</th>
                  <th scope="col">Promise vs. Walk-Back</th>
                  <th scope="col">Audit Status</th>
                </tr>
              </thead>
              <tbody>
                {filteredLeads.map((lead) => {
                  const tag = getFindingTag(lead.status);
                  const mech = formatWalkbackMechanism(lead.walkback_mechanism || lead.signal_category);
                  const imp = lead.retroactive_impact
                    ? formatRetroactiveImpact(lead.retroactive_impact)
                    : null;
                  const jur = formatJurisdictionLabel(
                    lead.jurisdiction,
                    lead.state_or_ut,
                    lead.jurisdiction_level,
                  );

                  return (
                    <tr key={lead.finding_id}>
                      <td className="pv-ub-line pv-p8-lead-scheme">
                        {onSelectFinding ? (
                          <button
                            type="button"
                            className="pv-ub-link"
                            onClick={() => onSelectFinding(lead.finding_id)}
                          >
                            {lead.scheme_name}
                            {lead.tranche_or_phase ? ` · ${lead.tranche_or_phase}` : ''}
                          </button>
                        ) : (
                          <span>
                            {lead.scheme_name}
                            {lead.tranche_or_phase ? ` · ${lead.tranche_or_phase}` : ''}
                          </span>
                        )}
                        <span className="pv-ub-sub">
                          {lead.is_sgb_anchor ? (
                            <>
                              <span className="p8-sgb-chip" style={{ fontSize: 10.5, padding: '0 6px' }}>
                                SGB Anchor
                              </span>{' '}
                            </>
                          ) : null}
                          {lead.entity_name || lead.title}
                        </span>
                      </td>
                      <td className="tabular pv-ub-lead-year" data-label="Jurisdiction & timeline">
                        <span>{jur}</span>
                        <span className="pv-ub-sub">
                          {lead.promise_date || '—'}
                          {lead.walkback_date ? ` → ${lead.walkback_date}` : ''}
                          {lead.days_from_promise_to_walkback != null
                            ? ` (${fmtCount(lead.days_from_promise_to_walkback)}d)`
                            : ''}
                        </span>
                      </td>
                      <td data-label="Mechanism & delta">
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                          <span className={mech.chipClass}>{mech.shortLabel}</span>
                          {imp?.isRetroactive ? (
                            <span className={imp.chipClass}>{imp.shortLabel}</span>
                          ) : null}
                        </div>
                        {lead.promised_metric || lead.revised_metric ? (
                          <span className="pv-ub-sub tabular">
                            {lead.promised_metric || 'Launch'} → {lead.revised_metric || 'Revised'}
                          </span>
                        ) : null}
                      </td>
                      <td className="pv-p8-lead-compare" data-label="Promise vs. walk-back">
                        <div className="pv-p6-excerpt">
                          <strong>Promise:</strong> {p8ShortText(lead.promise_summary, 110)}
                        </div>
                        <div className="pv-p6-excerpt pv-p6-excerpt--followup">
                          <strong>{mech.isCleared ? 'Status:' : 'Walk-back:'}</strong>{' '}
                          {p8ShortText(lead.walkback_summary, 110)}
                        </div>
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
            {hasActiveFilters && topLeads.length > 0
              ? 'No reported leads match the active explorer filters. Reset filters above to view all leads.'
              : 'Scheme promise-vs-walk-back leads appear here as contributor agents verify tasks. Every entry cites the official launch notification, subsequent amendment, SHA-256 digests, and lawful benign explanations.'}
          </div>
        )}
      </div>
    </section>
  );
}

/* ---------- 3I. P9 Auditing Government Exams: 3-year schedule vs. conduct audit across Union & State/UT exams ---------- */

const P9_METHOD_URL =
  'https://github.com/aiwatchdogsindia/aiwatchdogs.in/blob/main/projects/auditing-government-exams/METHODOLOGY.md';
export const P9_DEFAULT_HOOK =
  'Out of 1,000 Indian government exams scheduled over the past 3 years, how many happened flawlessly — and how many were delayed, postponed, rescheduled, or cancelled after students sat?';
const P9_INITIAL_ANCHORS = 4;
const P9_INITIAL_JURISDICTIONS = 12;
const P9_INITIAL_AUTHORITIES = 12;
const P9_INITIAL_LEADS = 15;

export type P9TierKey = 'union' | 'state' | 'ut';

export type P9DisruptionKey =
  | 'cancelled_after_conduct'
  | 'postponed'
  | 'rescheduled'
  | 'delayed'
  | 'cancelled_before_conduct'
  | 'partial_reexam_or_glitch'
  | 'flawless_conduct';

export type P9JurisdictionSortKey = 'not_flawless' | 'rate' | 'cancelled_after' | 'tasks' | 'name';

export interface P9NormalizedHeadline {
  totalAudited: number;
  notFlawlessCount: number;
  flawlessCount: number;
  notFlawlessRatePct: number;
  flawlessRatePct: number;
  cancelledAfterConduct: number;
  postponed: number;
  rescheduled: number;
  delayed: number;
  cancelledBeforeConduct: number;
  partialReexamOrGlitch: number;
  documentedAffectedCandidatesTotal: number;
}

export interface P9NormalizedDisruption {
  key: P9DisruptionKey;
  label: string;
  shortLabel: string;
  ruleCode: string;
  chipClass: string;
  fillClass: string;
  isCleared: boolean;
  tasks: number;
  done: number;
  leads: number;
  cleared: number;
  sharePct: number;
  documentedAffectedCandidates: number;
}

export interface P9NormalizedTier {
  key: P9TierKey;
  label: string;
  shortLabel: string;
  tasks: number;
  done: number;
  claimed: number;
  open: number;
  pct: number;
  leads: number;
  cleared: number;
  notFlawless: number;
  flawless: number;
  cancelledAfterConduct: number;
}

export interface P9NormalizedYear {
  year: string;
  yearNum: number;
  tasks: number;
  done: number;
  leads: number;
  notFlawless: number;
  flawless: number;
  notFlawlessPct: number;
  flawlessPct: number;
  cancelledAfterConduct: number;
  postponed: number;
  rescheduled: number;
  delayed: number;
  cancelledBeforeConduct: number;
  partialReexamOrGlitch: number;
}

export function p9Plural(n: number, singular: string, plural?: string): string {
  const word = n === 1 ? singular : (plural ?? `${singular}s`);
  return `${fmtCount(n)} ${word}`;
}

export function p9TierLabel(tier: string): string {
  const k = (tier || '').trim().toLowerCase();
  if (k === 'union' || k === 'central') return 'Union of India';
  if (k === 'state' || k === 'states') return 'States';
  if (k === 'ut' || k === 'uts' || k === 'union_territory') return 'Union Territories';
  return tier || 'Jurisdiction';
}

export function p9DisruptionLabel(disruption: string): string {
  if ((disruption || '').trim().toLowerCase() === 'not_flawless') {
    return 'Did Not Happen Flawlessly';
  }
  return formatExamDisruption(disruption).label;
}

export function p9AuthorityTypeLabel(authorityType: string): string {
  return formatExamAuthorityType(authorityType).label;
}

export function p9ExamCategoryLabel(category: string): string {
  return formatExamCategory(category).label;
}

export function p9ReasonLabel(reason: string): string {
  return formatExamDisruptionReason(reason).label;
}

/** Exact jurisdiction prefix token matching P9 task titles without substring collisions (e.g. Goa vs Goal). */
export function p9JurisdictionFilterQuery(
  jurisdiction?: string | null,
  stateOrUt?: string | null,
): string {
  const cleanState = (stateOrUt || '').trim();
  if (cleanState) return `${cleanState} ·`;
  const cleanJur = (jurisdiction || '').trim();
  if (!cleanJur) return '';
  if (/^state:/i.test(cleanJur)) {
    const name = cleanJur.slice(6).trim();
    return name ? `${name} ·` : '';
  }
  if (/^ut:/i.test(cleanJur)) {
    const name = cleanJur.slice(3).trim();
    return name ? `${name} ·` : '';
  }
  return `${cleanJur} ·`;
}

/** Clear active jurisdiction filter if switching to a tier that excludes it. */
export function p9ReconcileJurisdictionForTier(
  selectedJurisdiction: string,
  nextTier: 'all' | P9TierKey,
  jurisdictions: VisualExamJurisdiction[],
): string {
  if (!selectedJurisdiction || selectedJurisdiction === 'all' || nextTier === 'all') {
    return selectedJurisdiction;
  }
  const matched = jurisdictions.find((j) => j.jurisdiction === selectedJurisdiction);
  if (matched) {
    return (matched.jurisdiction_level || '').trim().toLowerCase() === nextTier
      ? selectedJurisdiction
      : 'all';
  }
  const lower = selectedJurisdiction.trim().toLowerCase();
  const inferredTier: P9TierKey = lower.startsWith('state:')
    ? 'state'
    : lower.startsWith('ut:')
      ? 'ut'
      : 'union';
  return inferredTier === nextTier ? selectedJurisdiction : 'all';
}

export function p9NormalizeDisruptions(
  byDisruptionType?: Record<string, VisualExamDisruptionBucket | number> | null,
  headlineCounts?: VisualExamHeadlineCounts | null,
): {
  items: P9NormalizedDisruption[];
  totalTasks: number;
  totalLeads: number;
  notFlawlessTasks: number;
  flawlessTasks: number;
} {
  const ordered: Array<{
    key: P9DisruptionKey;
    fillClass: string;
    headlineFallback: number;
  }> = [
    {
      key: 'cancelled_after_conduct',
      fillClass: 'pv-p9-fill--cancelled-after',
      headlineFallback: Math.max(0, Number(headlineCounts?.cancelled_after_conduct) || 0),
    },
    {
      key: 'postponed',
      fillClass: 'pv-p9-fill--postponed',
      headlineFallback: Math.max(0, Number(headlineCounts?.postponed) || 0),
    },
    {
      key: 'rescheduled',
      fillClass: 'pv-p9-fill--rescheduled',
      headlineFallback: Math.max(0, Number(headlineCounts?.rescheduled) || 0),
    },
    {
      key: 'delayed',
      fillClass: 'pv-p9-fill--delayed',
      headlineFallback: Math.max(0, Number(headlineCounts?.delayed) || 0),
    },
    {
      key: 'cancelled_before_conduct',
      fillClass: 'pv-p9-fill--cancelled-before',
      headlineFallback: Math.max(0, Number(headlineCounts?.cancelled_before_conduct) || 0),
    },
    {
      key: 'partial_reexam_or_glitch',
      fillClass: 'pv-p9-fill--glitch',
      headlineFallback: Math.max(0, Number(headlineCounts?.partial_reexam_or_glitch) || 0),
    },
    {
      key: 'flawless_conduct',
      fillClass: 'pv-p9-fill--flawless',
      headlineFallback: Math.max(0, Number(headlineCounts?.flawless_count) || 0),
    },
  ];

  const rows = ordered.map(({ key, fillClass, headlineFallback }) => {
    const raw = byDisruptionType?.[key];
    let tasks = 0;
    let done = 0;
    let leads = 0;
    let cleared = 0;
    let explicitPct: number | null = null;
    let documentedAffectedCandidates = 0;

    if (typeof raw === 'number') {
      tasks = Math.max(0, raw);
    } else if (raw && typeof raw === 'object') {
      tasks = Math.max(0, Number(raw.tasks) || 0);
      done = Math.max(0, Number(raw.done) || 0);
      leads = Math.max(0, Number(raw.leads) || 0);
      cleared = Math.max(0, Number(raw.cleared) || 0);
      if (raw.pct != null && Number.isFinite(Number(raw.pct))) {
        explicitPct = Math.max(0, Number(raw.pct));
      }
      documentedAffectedCandidates = Math.max(
        0,
        Number(raw.documented_affected_candidates) || 0,
      );
    }

    if (tasks === 0 && headlineFallback > 0) {
      tasks = headlineFallback;
    }

    const meta = formatExamDisruption(key);
    return {
      key,
      label:
        raw && typeof raw === 'object' && raw.label?.trim() ? raw.label.trim() : meta.label,
      shortLabel: meta.shortLabel,
      ruleCode:
        raw && typeof raw === 'object' && raw.rule?.trim() ? raw.rule.trim() : meta.ruleCode,
      chipClass: meta.chipClass,
      fillClass,
      isCleared: meta.isCleared,
      tasks,
      done,
      leads,
      cleared,
      explicitPct,
      documentedAffectedCandidates,
    };
  });

  const totalTasks = rows.reduce((acc, r) => acc + r.tasks, 0);
  const totalLeads = rows.reduce((acc, r) => acc + r.leads, 0);
  const denominator = totalTasks > 0 ? totalTasks : totalLeads;

  const items: P9NormalizedDisruption[] = rows.map((r) => ({
    key: r.key,
    label: r.label,
    shortLabel: r.shortLabel,
    ruleCode: r.ruleCode,
    chipClass: r.chipClass,
    fillClass: r.fillClass,
    isCleared: r.isCleared,
    tasks: r.tasks,
    done: r.done,
    leads: r.leads,
    cleared: r.cleared,
    sharePct:
      r.explicitPct != null && (r.tasks > 0 || r.leads > 0)
        ? r.explicitPct
        : calcPct(totalTasks > 0 ? r.tasks : r.leads, denominator),
    documentedAffectedCandidates: r.documentedAffectedCandidates,
  }));

  const flawlessTasks = items.find((i) => i.key === 'flawless_conduct')?.tasks ?? 0;
  const notFlawlessTasks = Math.max(0, totalTasks - flawlessTasks);

  return { items, totalTasks, totalLeads, notFlawlessTasks, flawlessTasks };
}

export function p9NormalizeHeadline(
  p9?: VisualAuditingGovernmentExams | null,
  fallbackTotalTasks = 0,
): P9NormalizedHeadline {
  const hc = p9?.headline_counts;
  const disruptions = p9NormalizeDisruptions(p9?.by_disruption_type, hc);
  const byKey = new Map(disruptions.items.map((item) => [item.key, item.tasks]));

  const cancelledAfterConduct = Math.max(
    0,
    Number(hc?.cancelled_after_conduct ?? byKey.get('cancelled_after_conduct') ?? 0),
  );
  const postponed = Math.max(0, Number(hc?.postponed ?? byKey.get('postponed') ?? 0));
  const rescheduled = Math.max(0, Number(hc?.rescheduled ?? byKey.get('rescheduled') ?? 0));
  const delayed = Math.max(0, Number(hc?.delayed ?? byKey.get('delayed') ?? 0));
  const cancelledBeforeConduct = Math.max(
    0,
    Number(hc?.cancelled_before_conduct ?? byKey.get('cancelled_before_conduct') ?? 0),
  );
  const partialReexamOrGlitch = Math.max(
    0,
    Number(hc?.partial_reexam_or_glitch ?? byKey.get('partial_reexam_or_glitch') ?? 0),
  );
  const flawlessCount = Math.max(
    0,
    Number(hc?.flawless_count ?? byKey.get('flawless_conduct') ?? 0),
  );

  const summedDisrupted =
    cancelledAfterConduct +
    postponed +
    rescheduled +
    delayed +
    cancelledBeforeConduct +
    partialReexamOrGlitch;
  const notFlawlessCount = Math.max(0, Number(hc?.not_flawless_count ?? summedDisrupted));
  const totalAudited = Math.max(
    0,
    Number(
      hc?.total_audited ??
        (notFlawlessCount + flawlessCount > 0
          ? notFlawlessCount + flawlessCount
          : (p9?.total_tasks ?? fallbackTotalTasks)),
    ),
  );

  const notFlawlessRatePct =
    hc?.not_flawless_rate_pct != null && Number.isFinite(Number(hc.not_flawless_rate_pct))
      ? Math.max(0, Number(hc.not_flawless_rate_pct))
      : calcPct(notFlawlessCount, totalAudited);
  const flawlessRatePct =
    hc?.flawless_rate_pct != null && Number.isFinite(Number(hc.flawless_rate_pct))
      ? Math.max(0, Number(hc.flawless_rate_pct))
      : calcPct(flawlessCount, totalAudited);

  const documentedAffectedCandidatesTotal = Math.max(
    0,
    Number(hc?.documented_affected_candidates_total) || 0,
  );

  return {
    totalAudited,
    notFlawlessCount,
    flawlessCount,
    notFlawlessRatePct,
    flawlessRatePct,
    cancelledAfterConduct,
    postponed,
    rescheduled,
    delayed,
    cancelledBeforeConduct,
    partialReexamOrGlitch,
    documentedAffectedCandidatesTotal,
  };
}

export function p9NormalizeTiers(
  byTier?: Record<string, VisualExamTierCounts | number> | null,
): {
  union: P9NormalizedTier;
  state: P9NormalizedTier;
  ut: P9NormalizedTier;
  totalTasks: number;
  totalDone: number;
  totalLeads: number;
  totalCleared: number;
  totalNotFlawless: number;
  totalFlawless: number;
} {
  const readTier = (key: P9TierKey, shortLabel: string, aliases: string[]): P9NormalizedTier => {
    let raw: VisualExamTierCounts | number | undefined;
    for (const [k, v] of Object.entries(byTier || {})) {
      const norm = k.trim().toLowerCase().replace(/[\s-]+/g, '_');
      if (aliases.includes(norm)) {
        raw = v;
        break;
      }
    }
    if (typeof raw === 'number') {
      const tasks = Math.max(0, raw);
      return {
        key,
        label: p9TierLabel(key),
        shortLabel,
        tasks,
        done: 0,
        claimed: 0,
        open: tasks,
        pct: 0,
        leads: 0,
        cleared: 0,
        notFlawless: 0,
        flawless: 0,
        cancelledAfterConduct: 0,
      };
    }
    const obj = raw && typeof raw === 'object' ? raw : null;
    const tasks = Math.max(0, Number(obj?.tasks) || 0);
    const done = Math.max(0, Number(obj?.done) || 0);
    const claimed = Math.max(0, Number(obj?.claimed) || 0);
    const open =
      obj?.open != null ? Math.max(0, Number(obj.open) || 0) : Math.max(0, tasks - done - claimed);
    const pct =
      obj?.pct != null && Number.isFinite(Number(obj.pct))
        ? Math.max(0, Number(obj.pct))
        : calcPct(done, tasks);
    return {
      key,
      label: p9TierLabel(key),
      shortLabel,
      tasks,
      done,
      claimed,
      open,
      pct,
      leads: Math.max(0, Number(obj?.leads) || 0),
      cleared: Math.max(0, Number(obj?.cleared) || 0),
      notFlawless: Math.max(0, Number(obj?.not_flawless) || 0),
      flawless: Math.max(0, Number(obj?.flawless) || 0),
      cancelledAfterConduct: Math.max(0, Number(obj?.cancelled_after_conduct) || 0),
    };
  };

  const union = readTier('union', 'Union', ['union', 'central', 'union_of_india']);
  const state = readTier('state', 'States', ['state', 'states']);
  const ut = readTier('ut', 'UTs', ['ut', 'uts', 'union_territory', 'union_territories']);

  return {
    union,
    state,
    ut,
    totalTasks: union.tasks + state.tasks + ut.tasks,
    totalDone: union.done + state.done + ut.done,
    totalLeads: union.leads + state.leads + ut.leads,
    totalCleared: union.cleared + state.cleared + ut.cleared,
    totalNotFlawless: union.notFlawless + state.notFlawless + ut.notFlawless,
    totalFlawless: union.flawless + state.flawless + ut.flawless,
  };
}

export function p9NormalizeYears(byYear?: VisualExamYear[] | null): P9NormalizedYear[] {
  if (!Array.isArray(byYear)) return [];
  return byYear
    .map((row) => {
      const yearNum = Number(row.year) || 0;
      const tasks = Math.max(0, Number(row.tasks) || 0);
      const notFlawless = Math.max(0, Number(row.not_flawless) || 0);
      const flawless = Math.max(0, Number(row.flawless) || 0);
      const denom = tasks > 0 ? tasks : notFlawless + flawless;
      return {
        year: String(row.year),
        yearNum,
        tasks: denom,
        done: Math.max(0, Number(row.done) || 0),
        leads: Math.max(0, Number(row.leads) || 0),
        notFlawless,
        flawless,
        notFlawlessPct: calcPct(notFlawless, denom),
        flawlessPct: calcPct(flawless, denom),
        cancelledAfterConduct: Math.max(0, Number(row.cancelled_after_conduct) || 0),
        postponed: Math.max(0, Number(row.postponed) || 0),
        rescheduled: Math.max(0, Number(row.rescheduled) || 0),
        delayed: Math.max(0, Number(row.delayed) || 0),
        cancelledBeforeConduct: Math.max(0, Number(row.cancelled_before_conduct) || 0),
        partialReexamOrGlitch: Math.max(0, Number(row.partial_reexam_or_glitch) || 0),
      };
    })
    .sort((a, b) => a.yearNum - b.yearNum || a.year.localeCompare(b.year));
}

export function p9FilterAndSortJurisdictions(
  jurisdictions: VisualExamJurisdiction[],
  query: string,
  tierFilter: 'all' | P9TierKey = 'all',
  sortBy: P9JurisdictionSortKey = 'not_flawless',
): VisualExamJurisdiction[] {
  const q = query.trim().toLowerCase();
  const filtered = jurisdictions.filter((j) => {
    const lvl = (j.jurisdiction_level || '').trim().toLowerCase();
    if (tierFilter !== 'all' && lvl !== tierFilter) return false;
    if (!q) return true;
    const display = formatJurisdictionLabel(j.jurisdiction, j.state_or_ut, j.jurisdiction_level);
    return (
      j.jurisdiction.toLowerCase().includes(q) ||
      (j.state_or_ut || '').toLowerCase().includes(q) ||
      display.toLowerCase().includes(q) ||
      lvl.includes(q)
    );
  });

  const rateOf = (j: VisualExamJurisdiction) =>
    j.not_flawless_rate_pct != null && Number.isFinite(j.not_flawless_rate_pct)
      ? j.not_flawless_rate_pct
      : calcPct(j.not_flawless, j.tasks);

  return [...filtered].sort((a, b) => {
    if (sortBy === 'rate') {
      return (
        rateOf(b) - rateOf(a) ||
        b.not_flawless - a.not_flawless ||
        b.tasks - a.tasks ||
        a.jurisdiction.localeCompare(b.jurisdiction)
      );
    }
    if (sortBy === 'cancelled_after') {
      return (
        b.cancelled_after_conduct - a.cancelled_after_conduct ||
        b.not_flawless - a.not_flawless ||
        b.tasks - a.tasks ||
        a.jurisdiction.localeCompare(b.jurisdiction)
      );
    }
    if (sortBy === 'tasks') {
      return (
        b.tasks - a.tasks ||
        b.not_flawless - a.not_flawless ||
        a.jurisdiction.localeCompare(b.jurisdiction)
      );
    }
    if (sortBy === 'name') {
      const la = formatJurisdictionLabel(a.jurisdiction, a.state_or_ut, a.jurisdiction_level);
      const lb = formatJurisdictionLabel(b.jurisdiction, b.state_or_ut, b.jurisdiction_level);
      return la.localeCompare(lb);
    }
    return (
      b.not_flawless - a.not_flawless ||
      b.tasks - a.tasks ||
      a.jurisdiction.localeCompare(b.jurisdiction)
    );
  });
}

export function p9FilterAuthorities(
  authorities: VisualExamAuthority[],
  query: string,
  tierFilter: 'all' | P9TierKey = 'all',
  authorityTypeFilter = 'all',
  jurisdictionFilter = 'all',
): VisualExamAuthority[] {
  const q = query.trim().toLowerCase();
  const at = authorityTypeFilter.trim().toLowerCase();
  const jur = jurisdictionFilter.trim().toLowerCase();

  return authorities.filter((a) => {
    const lvl = (a.jurisdiction_level || '').trim().toLowerCase();
    if (tierFilter !== 'all' && lvl !== tierFilter) return false;
    if (at && at !== 'all' && (a.authority_type || '').trim().toLowerCase() !== at) return false;
    if (jur && jur !== 'all' && (a.jurisdiction || '').trim().toLowerCase() !== jur) return false;
    if (!q) return true;
    const jurDisplay = formatJurisdictionLabel(a.jurisdiction, null, a.jurisdiction_level);
    return (
      a.authority.toLowerCase().includes(q) ||
      (a.authority_key || '').toLowerCase().includes(q) ||
      a.jurisdiction.toLowerCase().includes(q) ||
      jurDisplay.toLowerCase().includes(q) ||
      formatExamAuthorityType(a.authority_type).label.toLowerCase().includes(q)
    );
  });
}

export function p9FilterLeads(
  leads: VisualExamLead[],
  query: string,
  disruptionFilter = 'all',
  tierFilter: 'all' | P9TierKey = 'all',
  jurisdictionFilter = 'all',
  authorityTypeFilter = 'all',
  yearFilter = 'all',
): VisualExamLead[] {
  const q = query.trim().toLowerCase();
  const dis = disruptionFilter.trim().toLowerCase();
  const jur = jurisdictionFilter.trim().toLowerCase();
  const at = authorityTypeFilter.trim().toLowerCase();
  const yr = yearFilter.trim();

  return leads.filter((l) => {
    const disruptionKey = formatExamDisruption(l.disruption_type || l.signal_category).key;
    if (dis === 'not_flawless') {
      if (disruptionKey === 'flawless_conduct') return false;
    } else if (dis && dis !== 'all' && disruptionKey !== dis) {
      return false;
    }

    const lvl = (l.jurisdiction_level || '').trim().toLowerCase();
    if (tierFilter !== 'all' && lvl && lvl !== tierFilter) return false;

    if (jur && jur !== 'all' && (l.jurisdiction || '').trim().toLowerCase() !== jur) return false;

    if (at && at !== 'all' && (l.authority_type || '').trim().toLowerCase() !== at) return false;

    if (yr && yr !== 'all' && String(l.exam_year) !== yr) return false;

    if (!q) return true;
    const jurDisplay = formatJurisdictionLabel(l.jurisdiction, l.state_or_ut, l.jurisdiction_level);
    return (
      l.exam_name.toLowerCase().includes(q) ||
      (l.title || '').toLowerCase().includes(q) ||
      (l.authority || '').toLowerCase().includes(q) ||
      (l.cycle_or_stage || '').toLowerCase().includes(q) ||
      (l.case_key || '').toLowerCase().includes(q) ||
      l.jurisdiction.toLowerCase().includes(q) ||
      jurDisplay.toLowerCase().includes(q) ||
      (l.schedule_summary || '').toLowerCase().includes(q) ||
      (l.outcome_summary || '').toLowerCase().includes(q) ||
      (l.schedule_document_ref || '').toLowerCase().includes(q) ||
      (l.outcome_document_ref || '').toLowerCase().includes(q) ||
      formatExamDisruption(l.disruption_type).label.toLowerCase().includes(q) ||
      formatExamDisruptionReason(l.disruption_reason_category).label.toLowerCase().includes(q)
    );
  });
}

export function p9LeadStatusMeta(status?: string | null): {
  label: string;
  badgeClass: string;
  description: string;
} {
  const s = (status || '').trim().toLowerCase();
  if (s === 'corpus_queued' || s === 'queued') {
    return {
      label: 'Queued in corpus',
      badgeClass: TASK_TAGS.open.badgeClass,
      description:
        'Indexed in the SHA-256-pinned P9 exam corpus and open for deterministic agent execution & 3-agent peer verification.',
    };
  }
  const tag = getFindingTag(status);
  return {
    label: tag.label,
    badgeClass: tag.badgeClass,
    description: tag.description,
  };
}

function p9ShortText(text?: string | null, maxLen = 135): string {
  const clean = (text || '').replace(/\s+/g, ' ').trim();
  if (!clean) return '—';
  return clean.length > maxLen ? `${clean.slice(0, maxLen - 1).trimEnd()}…` : clean;
}

export function AuditingGovernmentExamsVisualiser({
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
  const p9 = visualSummary?.auditing_government_exams;
  const workTag = getWorkTypeTag(undefined, mission.methodology_code);
  const slugPath = encodeURIComponent(mission.slug);

  const [selectedDisruption, setSelectedDisruption] = useState<string>('all');
  const [selectedTier, setSelectedTier] = useState<'all' | P9TierKey>('all');
  const [selectedJurisdiction, setSelectedJurisdiction] = useState<string>('all');
  const [selectedAuthorityType, setSelectedAuthorityType] = useState<string>('all');
  const [selectedYear, setSelectedYear] = useState<string>('all');
  const [jurisdictionQuery, setJurisdictionQuery] = useState('');
  const [jurisdictionSort, setJurisdictionSort] = useState<P9JurisdictionSortKey>('not_flawless');
  const [showAllJurisdictions, setShowAllJurisdictions] = useState(false);
  const [authorityQuery, setAuthorityQuery] = useState('');
  const [showAllAuthorities, setShowAllAuthorities] = useState(false);
  const [showAllAnchors, setShowAllAnchors] = useState(false);
  const [ledgerQuery, setLedgerQuery] = useState('');
  const [showAllLeads, setShowAllLeads] = useState(false);

  const hook = p9?.hook?.trim() || P9_DEFAULT_HOOK;
  const totalTasks = p9?.total_tasks ?? mission.total_tasks ?? 0;
  const auditWindowLabel = p9?.audit_window?.label || 'Nov 2023 – Nov 2026 (3 years)';
  const uniqueExamsCovered = p9?.unique_exams_covered ?? 0;
  const uniqueAuthoritiesCovered =
    p9?.unique_authorities_covered ?? p9?.top_authorities?.length ?? 0;
  const uniqueDocumentsCount = p9?.unique_documents_count ?? 0;
  const jurisdictions = p9?.jurisdictions ?? [];
  const statesAndUtsCovered =
    p9?.states_and_uts_covered ??
    jurisdictions.filter((j) => (j.jurisdiction_level || '').toLowerCase() !== 'union').length;

  const publishedLeads = p9?.published_leads ?? 0;
  const verified = p9?.verified ?? mission.verified_findings_count ?? 0;
  const inReview = p9?.peer_review ?? mission.peer_review_count ?? 0;
  const clearedTasks = p9?.cleared_tasks ?? mission.cleared_tasks_count ?? 0;
  const leadsSource = p9?.leads_source ?? (publishedLeads > 0 ? 'live' : 'corpus_fallback');
  const isCorpusFallback = leadsSource === 'corpus_fallback';

  const topAuthorities = p9?.top_authorities ?? [];
  const authorityTypes = p9?.by_authority_type ?? [];
  const activeAuthorityTypes = authorityTypes.filter((at) => at.tasks > 0);
  const examCategories = p9?.by_exam_category ?? [];
  const reasonCategories = p9?.by_reason_category ?? [];
  const anchorCases: VisualExamAnchorCase[] = p9?.anchor_cases ?? [];
  const allLeads = p9?.leads ?? [];
  const dryRun = p9?.dry_run_summary ?? null;

  const documentsUrl = p9?.documents_url || `/api/missions/${slugPath}/documents.csv`;
  const coverageUrl = p9?.coverage_url || `/api/missions/${slugPath}/coverage.json`;

  const headline = useMemo(() => p9NormalizeHeadline(p9, totalTasks), [p9, totalTasks]);
  const disruptions = useMemo(
    () => p9NormalizeDisruptions(p9?.by_disruption_type, p9?.headline_counts),
    [p9?.by_disruption_type, p9?.headline_counts],
  );
  const tiers = useMemo(
    () => p9NormalizeTiers(p9?.by_jurisdiction_level),
    [p9?.by_jurisdiction_level],
  );
  const years = useMemo(() => p9NormalizeYears(p9?.by_year), [p9?.by_year]);

  const filteredJurisdictions = useMemo(
    () =>
      p9FilterAndSortJurisdictions(jurisdictions, jurisdictionQuery, selectedTier, jurisdictionSort),
    [jurisdictions, jurisdictionQuery, selectedTier, jurisdictionSort],
  );
  const visibleJurisdictions =
    showAllJurisdictions || jurisdictionQuery.trim().length > 0 || selectedTier !== 'all'
      ? filteredJurisdictions
      : filteredJurisdictions.slice(0, P9_INITIAL_JURISDICTIONS);

  const filteredAuthorities = useMemo(
    () =>
      p9FilterAuthorities(
        topAuthorities,
        authorityQuery,
        selectedTier,
        selectedAuthorityType,
        selectedJurisdiction,
      ),
    [
      topAuthorities,
      authorityQuery,
      selectedTier,
      selectedAuthorityType,
      selectedJurisdiction,
    ],
  );
  const visibleAuthorities =
    showAllAuthorities ||
    authorityQuery.trim().length > 0 ||
    selectedTier !== 'all' ||
    selectedAuthorityType !== 'all' ||
    selectedJurisdiction !== 'all'
      ? filteredAuthorities
      : filteredAuthorities.slice(0, P9_INITIAL_AUTHORITIES);

  const visibleAnchors = showAllAnchors
    ? anchorCases
    : anchorCases.slice(0, P9_INITIAL_ANCHORS);

  const filteredLeads = useMemo(
    () =>
      p9FilterLeads(
        allLeads,
        ledgerQuery,
        selectedDisruption,
        selectedTier,
        selectedJurisdiction,
        selectedAuthorityType,
        selectedYear,
      ),
    [
      allLeads,
      ledgerQuery,
      selectedDisruption,
      selectedTier,
      selectedJurisdiction,
      selectedAuthorityType,
      selectedYear,
    ],
  );
  const visibleLeads = showAllLeads
    ? filteredLeads
    : filteredLeads.slice(0, P9_INITIAL_LEADS);

  const hasActiveFilters =
    selectedDisruption !== 'all' ||
    selectedTier !== 'all' ||
    selectedJurisdiction !== 'all' ||
    selectedAuthorityType !== 'all' ||
    selectedYear !== 'all' ||
    ledgerQuery.trim().length > 0;

  const handleSelectTier = (nextTier: 'all' | P9TierKey) => {
    setSelectedTier(nextTier);
    setSelectedJurisdiction((prev) =>
      p9ReconcileJurisdictionForTier(prev, nextTier, jurisdictions),
    );
  };

  const resetAllFilters = () => {
    setSelectedDisruption('all');
    setSelectedTier('all');
    setSelectedJurisdiction('all');
    setSelectedAuthorityType('all');
    setSelectedYear('all');
    setLedgerQuery('');
  };

  const tierDenominator = tiers.totalTasks || totalTasks;
  const unionPct = calcPct(tiers.union.tasks, tierDenominator);
  const statePct = calcPct(tiers.state.tasks, tierDenominator);
  const utPct = calcPct(tiers.ut.tasks, tierDenominator);

  const candidatesCompactTotal = formatCandidateCountCompact(
    headline.documentedAffectedCandidatesTotal,
  );

  const calloutCounters: Array<{
    key: P9DisruptionKey;
    label: string;
    sub: string;
    count: number;
    cardClass: string;
  }> = [
    {
      key: 'cancelled_after_conduct',
      label: 'Took Exam, Then Cancelled',
      sub: 'Students sat, then cancelled',
      count: headline.cancelledAfterConduct,
      cardClass: 'pv-p9-counter--cancelled-after',
    },
    {
      key: 'postponed',
      label: 'Postponed',
      sub: 'Deferred before conduct',
      count: headline.postponed,
      cardClass: 'pv-p9-counter--postponed',
    },
    {
      key: 'rescheduled',
      label: 'Rescheduled',
      sub: 'Moved to revised dates',
      count: headline.rescheduled,
      cardClass: 'pv-p9-counter--rescheduled',
    },
    {
      key: 'delayed',
      label: 'Delayed',
      sub: 'Cycle or schedule slip',
      count: headline.delayed,
      cardClass: 'pv-p9-counter--delayed',
    },
    {
      key: 'cancelled_before_conduct',
      label: 'Cancelled Before Conduct',
      sub: 'Withdrawn pre-exam',
      count: headline.cancelledBeforeConduct,
      cardClass: 'pv-p9-counter--cancelled-before',
    },
    {
      key: 'partial_reexam_or_glitch',
      label: 'Glitch / Partial Re-Exam',
      sub: 'CBT glitch or center re-test',
      count: headline.partialReexamOrGlitch,
      cardClass: 'pv-p9-counter--glitch',
    },
    {
      key: 'flawless_conduct',
      label: 'Conducted Flawlessly (Cleared)',
      sub: 'Held on schedule as notified',
      count: headline.flawlessCount,
      cardClass: 'pv-p9-counter--flawless',
    },
  ];

  const tierCards: Array<{
    bucket: P9NormalizedTier;
    pct: number;
    btnClass: string;
    desc: string;
  }> = [
    {
      bucket: tiers.union,
      pct: unionPct,
      btnClass: 'pv-p8-tier-btn--union',
      desc: 'NTA, UPSC, SSC, RRB, NBEMS, IBPS, Defence & IITs',
    },
    {
      bucket: tiers.state,
      pct: statePct,
      btnClass: 'pv-p8-tier-btn--state',
      desc: 'All 28 States — State PSCs, Police, Teacher & Entrance Boards',
    },
    {
      bucket: tiers.ut,
      pct: utPct,
      btnClass: 'pv-p8-tier-btn--ut',
      desc: 'All 8 Union Territories — DSSSB, JKPSC, JKSSB & UT Boards',
    },
  ];

  return (
    <section
      className="card card-pad pv-results"
      aria-label="Auditing Government Exams 3-year reliability scorecard"
    >
      {/* Header + Summary Pills */}
      <div className="pv-results-head">
        <div className="pv-results-title-wrap">
          <span className="kicker">
            <BarChart3 size={12} aria-hidden="true" />
            Live results · {workTag.label} · {auditWindowLabel}
          </span>
          <h2 className="pv-results-title">
            Auditing Government Exams: 3-Year Reliability Scorecard
          </h2>
          <p className="pv-p6-hook">{hook}</p>
        </div>

        <div className="pv-summary-pills tabular">
          <span className="tag-mono">{p9Plural(totalTasks, 'exam task')}</span>
          {uniqueExamsCovered > 0 ? (
            <span className="tag-mono">{p9Plural(uniqueExamsCovered, 'unique exam')}</span>
          ) : null}
          {uniqueAuthoritiesCovered > 0 ? (
            <span className="tag-mono">
              {p9Plural(uniqueAuthoritiesCovered, 'authority', 'authorities')}
            </span>
          ) : null}
          {statesAndUtsCovered > 0 ? (
            <span className="tag-mono">
              Union + {fmtCount(statesAndUtsCovered)} States &amp; UTs
            </span>
          ) : null}
          {uniqueDocumentsCount > 0 ? (
            <span className="tag-mono">{p9Plural(uniqueDocumentsCount, 'pinned document')}</span>
          ) : null}
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
          {clearedTasks > 0 ? (
            <span className={FINDING_TAGS.cleared.badgeClass}>
              {fmtCount(clearedTasks)} {FINDING_TAGS.cleared.label}
            </span>
          ) : null}
        </div>
      </div>

      {/* 1. 3-YEAR EXAM RELIABILITY SCORECARD (HEADLINE HERO) */}
      <div className="pv-p9-scorecard">
        <div className="pv-p9-hero-split">
          <button
            type="button"
            aria-pressed={selectedDisruption === 'not_flawless'}
            className="pv-p9-hero-tile pv-p9-hero-tile--disrupted"
            onClick={() =>
              setSelectedDisruption((prev) =>
                prev === 'not_flawless' ? 'all' : 'not_flawless',
              )
            }
          >
            <div className="pv-p9-hero-top">
              <span className="pv-overview-kicker">Did Not Happen Flawlessly</span>
              <span className="pv-p9-hero-rate pv-p9-hero-rate--disrupted tabular">
                {fmtPct(headline.notFlawlessRatePct)}
              </span>
            </div>
            <div className="pv-p9-hero-num tabular">
              {fmtCount(headline.notFlawlessCount)}
              <span className="pv-p9-hero-denom">
                {' '}
                / {p9Plural(headline.totalAudited, 'exam')}
              </span>
            </div>
            <p className="pv-overview-sub tabular">
              Postponed, rescheduled, delayed, glitch/re-exam, or cancelled (before or after conduct)
              {candidatesCompactTotal
                ? ` · ${candidatesCompactTotal} affected candidates documented in official notices`
                : ''}
            </p>
          </button>

          <button
            type="button"
            aria-pressed={selectedDisruption === 'flawless_conduct'}
            className="pv-p9-hero-tile pv-p9-hero-tile--flawless"
            onClick={() =>
              setSelectedDisruption((prev) =>
                prev === 'flawless_conduct' ? 'all' : 'flawless_conduct',
              )
            }
          >
            <div className="pv-p9-hero-top">
              <span className="pv-overview-kicker">Happened Flawlessly (Cleared)</span>
              <span className="pv-p9-hero-rate pv-p9-hero-rate--flawless tabular">
                {fmtPct(headline.flawlessRatePct)}
              </span>
            </div>
            <div className="pv-p9-hero-num tabular">
              {fmtCount(headline.flawlessCount)}
              <span className="pv-p9-hero-denom">
                {' '}
                / {p9Plural(headline.totalAudited, 'exam')}
              </span>
            </div>
            <p className="pv-overview-sub tabular">
              Verified benchmark cohort conducted on schedule without documented cancellation, postponement, or re-exam
            </p>
          </button>
        </div>

        {/* Full-Width Disrupted vs. Flawless Progress Bar */}
        {headline.totalAudited > 0 ? (
          <div className="pv-p9-reliability-wrap">
            <div
              className="pv-p9-reliability-bar"
              role="img"
              aria-label={`Out of ${fmtCount(headline.totalAudited)} audited government exams: ${fmtCount(headline.notFlawlessCount)} did not happen flawlessly (${fmtPct(headline.notFlawlessRatePct)}) and ${fmtCount(headline.flawlessCount)} happened flawlessly (${fmtPct(headline.flawlessRatePct)}).`}
            >
              {headline.notFlawlessRatePct > 0 ? (
                <span
                  className="pv-p9-rel-seg pv-p9-rel-seg--disrupted"
                  style={{ width: `${Math.min(100, headline.notFlawlessRatePct)}%` }}
                  title={`Did not happen flawlessly: ${fmtCount(headline.notFlawlessCount)} (${fmtPct(headline.notFlawlessRatePct)})`}
                />
              ) : null}
              {headline.flawlessRatePct > 0 ? (
                <span
                  className="pv-p9-rel-seg pv-p9-rel-seg--flawless"
                  style={{ width: `${Math.min(100, headline.flawlessRatePct)}%` }}
                  title={`Happened flawlessly (Cleared): ${fmtCount(headline.flawlessCount)} (${fmtPct(headline.flawlessRatePct)})`}
                />
              ) : null}
            </div>
            <div className="pv-p9-reliability-legend tabular">
              <span>
                <i className="pv-dot pv-p9-dot--disrupted" aria-hidden="true" /> Disrupted:{' '}
                <strong>{fmtCount(headline.notFlawlessCount)}</strong> (
                {fmtPct(headline.notFlawlessRatePct)})
              </span>
              <span>
                <i className="pv-dot pv-p9-dot--flawless" aria-hidden="true" /> Flawless (Cleared):{' '}
                <strong>{fmtCount(headline.flawlessCount)}</strong> (
                {fmtPct(headline.flawlessRatePct)})
              </span>
              {headline.documentedAffectedCandidatesTotal > 0 ? (
                <span className="pv-p9-candidates-note">
                  Documented candidates affected:{' '}
                  <strong>
                    {headline.documentedAffectedCandidatesTotal.toLocaleString('en-IN')}
                  </strong>{' '}
                  (officially cited notices only)
                </span>
              ) : null}
            </div>
          </div>
        ) : null}

        {/* 7 Dedicated First-Class Callout Counters (Click to Filter Ledger) */}
        <div
          className="pv-p9-counter-grid"
          role="group"
          aria-label="Filter exam audit ledger by disruption category"
        >
          {calloutCounters.map((c) => {
            const active = selectedDisruption === c.key;
            const pct = calcPct(c.count, headline.totalAudited);
            return (
              <button
                key={c.key}
                type="button"
                aria-pressed={active}
                className={`pv-p9-counter-btn ${c.cardClass}`}
                onClick={() =>
                  setSelectedDisruption((prev) => (prev === c.key ? 'all' : c.key))
                }
              >
                <span className="pv-p9-counter-label">{c.label}</span>
                <span className="pv-p9-counter-num tabular">{fmtCount(c.count)}</span>
                <span className="pv-p9-counter-sub tabular">
                  {fmtPct(pct)} · {c.sub}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {/* 2. FLAGSHIP ANCHOR CASES STRIP */}
      {anchorCases.length > 0 ? (
        <div className="pv-chart-card pv-p7-section-card">
          <div className="pv-chart-subhead">
            <h3>Flagship Anchor Exam Cases ({fmtCount(anchorCases.length)})</h3>
            <span className="pv-chart-note">
              Major documented post-exam cancellations, last-minute postponements &amp; reschedules with SHA-256-pinned official notices
            </span>
          </div>

          <div className="pv-p9-anchor-grid">
            {visibleAnchors.map((ac) => {
              const disrupt = formatExamDisruption(ac.disruption_type);
              const reason = ac.disruption_reason_category
                ? formatExamDisruptionReason(ac.disruption_reason_category)
                : null;
              const jur = formatJurisdictionLabel(
                ac.jurisdiction,
                ac.state_or_ut,
                ac.jurisdiction_level,
              );
              const delayLabel = formatExamDelaySpan(ac.delay_days);
              const candCompact = formatCandidateCountCompact(ac.affected_candidates_count);
              const primaryUrl = safeExternalUrl(ac.source_url);
              const secondaryUrl = safeExternalUrl(ac.secondary_url);
              const tierCls =
                (ac.jurisdiction_level || 'union').toLowerCase() === 'state'
                  ? 'p8-tier-chip--state'
                  : (ac.jurisdiction_level || 'union').toLowerCase() === 'ut'
                    ? 'p8-tier-chip--ut'
                    : 'p8-tier-chip--union';

              return (
                <article key={ac.case_key} className="pv-p9-anchor-card">
                  <div className="pv-p6-anchor-head">
                    <div className="pv-p6-anchor-meta">
                      <span className={disrupt.chipClass}>{disrupt.shortLabel}</span>
                      {ac.students_took_exam_before_cancellation ? (
                        <span className="p9-sat-chip">Sat, then cancelled</span>
                      ) : null}
                      <span className={`p8-tier-chip ${tierCls}`}>{jur}</span>
                      <span className="tag-mono tabular">{String(ac.exam_year)}</span>
                      {reason && reason.key !== 'none_flawless' ? (
                        <span className={reason.chipClass}>{reason.shortLabel}</span>
                      ) : null}
                    </div>
                  </div>

                  <h4 className="pv-p9-anchor-title">
                    {ac.exam_name}
                    {ac.cycle_or_stage ? ` · ${ac.cycle_or_stage}` : ''}
                  </h4>
                  <p className="pv-p9-anchor-meta-line">
                    <strong>{ac.authority}</strong>
                    {ac.scheduled_date ? (
                      <>
                        {' · '}
                        <span className="font-mono tabular">
                          {ac.scheduled_date}
                          {ac.actual_or_event_date && ac.actual_or_event_date !== ac.scheduled_date
                            ? ` → ${ac.actual_or_event_date}`
                            : ''}
                          {!disrupt.isCleared && delayLabel && (ac.delay_days ?? 0) > 0
                            ? ` (+${delayLabel})`
                            : ''}
                        </span>
                      </>
                    ) : null}
                    {candCompact ? (
                      <>
                        {' · '}
                        <span
                          className="tabular"
                          title={ac.affected_candidates_basis || 'Officially documented candidates'}
                        >
                          {candCompact} candidates
                        </span>
                      </>
                    ) : null}
                  </p>

                  <div className="pv-p9-anchor-compare">
                    <div className="pv-p9-anchor-col pv-p9-anchor-col--schedule">
                      <span className="fc-p8-col-kicker">Original Schedule</span>
                      <p className="fc-p8-col-text">{p9ShortText(ac.schedule_summary, 115)}</p>
                    </div>
                    <div
                      className={`pv-p9-anchor-col ${
                        disrupt.isCleared
                          ? 'pv-p9-anchor-col--flawless'
                          : 'pv-p9-anchor-col--outcome'
                      }`}
                    >
                      <span className="fc-p8-col-kicker">
                        {disrupt.isCleared ? 'Conducted Flawlessly' : 'What Actually Happened'}
                      </span>
                      <p className="fc-p8-col-text">{p9ShortText(ac.outcome_summary, 115)}</p>
                    </div>
                  </div>

                  <div className="pv-p6-anchor-actions">
                    {ac.finding_id && onSelectFinding ? (
                      <button
                        type="button"
                        className="btn btn-secondary btn-xs"
                        onClick={() => onSelectFinding(ac.finding_id!)}
                      >
                        Inspect exam dossier
                      </button>
                    ) : onFilterTasks ? (
                      <button
                        type="button"
                        className="btn btn-secondary btn-xs"
                        onClick={() => onFilterTasks(ac.exam_name)}
                      >
                        Find in task queue
                      </button>
                    ) : null}
                    {primaryUrl ? (
                      <a
                        className="inline-link"
                        href={primaryUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        Official notice{' '}
                        <ExternalLink size={11} aria-hidden="true" className="pv-ub-ext" />
                      </a>
                    ) : null}
                    {secondaryUrl && secondaryUrl !== primaryUrl ? (
                      <a
                        className="inline-link"
                        href={secondaryUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        Secondary notice{' '}
                        <ExternalLink size={11} aria-hidden="true" className="pv-ub-ext" />
                      </a>
                    ) : null}
                  </div>
                </article>
              );
            })}
          </div>

          {anchorCases.length > P9_INITIAL_ANCHORS ? (
            <div className="pv-list-more">
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => setShowAllAnchors((v) => !v)}
              >
                {showAllAnchors
                  ? `Show top ${P9_INITIAL_ANCHORS} anchor cases`
                  : `Show all ${fmtCount(anchorCases.length)} anchor cases`}
              </button>
            </div>
          ) : null}
        </div>
      ) : null}

      {/* 3. INTERACTIVE DISRUPTION DISTRIBUTION + YEAR-OVER-YEAR (2023–2026) */}
      <div className="pv-results-split">
        <div className="pv-chart-card">
          <div className="pv-chart-subhead">
            <h3>Disruption Type Distribution (7 Canonical Categories)</h3>
            <span className="pv-chart-note">Click any bar to filter the exam ledger below</span>
          </div>

          <div className="pv-p8-mech-grid" role="group" aria-label="Disruption type distribution">
            {disruptions.items.map((d) => {
              const isSelected = selectedDisruption === d.key;
              const candCompact = formatCandidateCountCompact(d.documentedAffectedCandidates);
              return (
                <button
                  key={d.key}
                  type="button"
                  aria-pressed={isSelected}
                  className={`pv-p8-mech-btn${d.isCleared ? ' pv-p8-mech-btn--cleared' : ''}`}
                  onClick={() =>
                    setSelectedDisruption((prev) => (prev === d.key ? 'all' : d.key))
                  }
                >
                  <div className="pv-p7-signal-top">
                    <span className={d.chipClass}>{d.label}</span>
                    <span className="pv-p7-signal-count tabular">
                      <strong>{p9Plural(d.tasks, 'exam')}</strong> ({fmtPct(d.sharePct)})
                      {d.leads > 0 ? ` · ${p9Plural(d.leads, 'lead')}` : ''}
                    </span>
                  </div>
                  <span className="pv-p7-signal-track" aria-hidden="true">
                    <span
                      className={`pv-p7-signal-fill ${d.fillClass}`}
                      style={{ width: `${d.sharePct > 0 ? Math.max(3, d.sharePct) : 0}%` }}
                    />
                  </span>
                  {candCompact ? (
                    <span className="pv-ub-sub tabular">
                      {candCompact} documented affected candidates
                    </span>
                  ) : null}
                </button>
              );
            })}
          </div>

          {reasonCategories.length > 0 ? (
            <div className="pv-p8-impact-strip" aria-label="Documented disruption reason categories">
              <span className="pv-p7-jur-label">Documented reasons:</span>
              {reasonCategories
                .filter((rc) => rc.tasks > 0)
                .map((rc) => {
                  const meta = formatExamDisruptionReason(rc.reason_category);
                  return (
                    <span key={rc.reason_category} className={meta.chipClass}>
                      {rc.label || meta.shortLabel}:{' '}
                      <strong className="tabular">{fmtCount(rc.tasks)}</strong>
                    </span>
                  );
                })}
            </div>
          ) : null}

          {examCategories.length > 0 ? (
            <div className="pv-p8-impact-strip" aria-label="Audited exam categories">
              <span className="pv-p7-jur-label">Exam sectors:</span>
              {examCategories
                .filter((ec) => ec.tasks > 0)
                .map((ec) => {
                  const meta = formatExamCategory(ec.exam_category);
                  return (
                    <span key={ec.exam_category} className="tag-mono tabular">
                      {ec.label || meta.shortLabel}: <strong>{fmtCount(ec.tasks)}</strong> (
                      {fmtCount(ec.not_flawless)} disrupted)
                    </span>
                  );
                })}
            </div>
          ) : null}
        </div>

        {/* Year-over-Year Breakdown (2023–2026) */}
        <div className="pv-chart-card">
          <div className="pv-chart-subhead">
            <h3>Year-over-Year Breakdown (2023 – 2026)</h3>
            <span className="pv-chart-note">Click any year to filter the ledger</span>
          </div>

          {years.length > 0 ? (
            <div className="pv-p9-years-list" role="group" aria-label="Filter by exam year">
              {years.map((y) => {
                const active = selectedYear === y.year;
                return (
                  <button
                    key={y.year}
                    type="button"
                    aria-pressed={active}
                    className="pv-p9-year-card"
                    onClick={() =>
                      setSelectedYear((prev) => (prev === y.year ? 'all' : y.year))
                    }
                  >
                    <div className="pv-p9-year-top tabular">
                      <span className="pv-p9-year-label font-mono">{y.year}</span>
                      <span className="pv-p9-year-totals">
                        <strong>{fmtCount(y.notFlawless)}</strong> disrupted ({fmtPct(y.notFlawlessPct)})
                        {' · '}
                        <span style={{ color: 'var(--ep-verified-fg)' }}>
                          {fmtCount(y.flawless)} flawless
                        </span>
                        {' · '}
                        {p9Plural(y.tasks, 'exam')}
                      </span>
                    </div>

                    <div
                      className="pv-p9-year-bar"
                      role="img"
                      aria-label={`${y.year}: ${fmtCount(y.notFlawless)} disrupted (${fmtPct(y.notFlawlessPct)}), ${fmtCount(y.flawless)} flawless (${fmtPct(y.flawlessPct)})`}
                    >
                      {y.notFlawlessPct > 0 ? (
                        <span
                          className="pv-p9-rel-seg pv-p9-rel-seg--disrupted"
                          style={{ width: `${Math.min(100, y.notFlawlessPct)}%` }}
                        />
                      ) : null}
                      {y.flawlessPct > 0 ? (
                        <span
                          className="pv-p9-rel-seg pv-p9-rel-seg--flawless"
                          style={{ width: `${Math.min(100, y.flawlessPct)}%` }}
                        />
                      ) : null}
                    </div>

                    <div className="pv-p9-year-sub tabular">
                      <span>Sat &amp; cancelled: {fmtCount(y.cancelledAfterConduct)}</span>
                      <span>Postponed: {fmtCount(y.postponed)}</span>
                      <span>Rescheduled: {fmtCount(y.rescheduled)}</span>
                      <span>Delayed: {fmtCount(y.delayed)}</span>
                      {y.cancelledBeforeConduct > 0 ? (
                        <span>Pre-cancelled: {fmtCount(y.cancelledBeforeConduct)}</span>
                      ) : null}
                      {y.partialReexamOrGlitch > 0 ? (
                        <span>Glitch/Re-exam: {fmtCount(y.partialReexamOrGlitch)}</span>
                      ) : null}
                    </div>
                  </button>
                );
              })}
            </div>
          ) : (
            <div className="pv-empty-box">
              Year-by-year exam reliability counts (2023–2026) appear here once the hub loads the P9 summary index.
            </div>
          )}
        </div>
      </div>

      {/* 4. UNION vs. STATES vs. UTs COVERAGE TIER TOGGLE */}
      {tiers.totalTasks > 0 ? (
        <div className="pv-p7-cohort-panel">
          <div className="pv-p7-cohort-head">
            <h3>Jurisdiction Tier (Union vs. 28 States vs. 8 UTs — select to filter)</h3>
            <div className="pv-mini-legend">
              <span>
                <i className="pv-dot pv-p8-dot--union" aria-hidden="true" /> Union
              </span>
              <span>
                <i className="pv-dot pv-p8-dot--state" aria-hidden="true" /> States
              </span>
              <span>
                <i className="pv-dot pv-p8-dot--ut" aria-hidden="true" /> UTs
              </span>
              {selectedTier !== 'all' ? (
                <button
                  type="button"
                  className="btn btn-ghost btn-xs"
                  onClick={() => handleSelectTier('all')}
                >
                  <RotateCcw size={11} aria-hidden="true" />
                  <span>All tiers</span>
                </button>
              ) : null}
            </div>
          </div>

          <div
            className="pv-p8-tier-bar"
            role="img"
            aria-label={`Jurisdiction split across ${fmtCount(tiers.totalTasks)} exam tasks: ${fmtCount(tiers.union.tasks)} Union (${fmtPct(unionPct)}), ${fmtCount(tiers.state.tasks)} States (${fmtPct(statePct)}), ${fmtCount(tiers.ut.tasks)} Union Territories (${fmtPct(utPct)})`}
          >
            {unionPct > 0 ? (
              <span
                className="pv-p8-seg pv-p8-seg--union"
                style={{ width: `${unionPct}%` }}
                title={`Union of India: ${p9Plural(tiers.union.tasks, 'exam')} (${fmtPct(unionPct)})`}
              />
            ) : null}
            {statePct > 0 ? (
              <span
                className="pv-p8-seg pv-p8-seg--state"
                style={{ width: `${statePct}%` }}
                title={`States: ${p9Plural(tiers.state.tasks, 'exam')} (${fmtPct(statePct)})`}
              />
            ) : null}
            {utPct > 0 ? (
              <span
                className="pv-p8-seg pv-p8-seg--ut"
                style={{ width: `${utPct}%` }}
                title={`Union Territories: ${p9Plural(tiers.ut.tasks, 'exam')} (${fmtPct(utPct)})`}
              />
            ) : null}
          </div>

          <div className="pv-p8-tier-cards" role="group" aria-label="Filter by jurisdiction tier">
            {tierCards.map(({ bucket, pct, btnClass, desc }) => {
              const isSelected = selectedTier === bucket.key;
              return (
                <button
                  key={bucket.key}
                  type="button"
                  aria-pressed={isSelected}
                  className={`pv-p7-cohort-btn ${btnClass}`}
                  onClick={() =>
                    handleSelectTier(selectedTier === bucket.key ? 'all' : bucket.key)
                  }
                >
                  <span className="pv-p7-cohort-btn-top">
                    <span className="pv-p7-cohort-name">{bucket.label}</span>
                    <span className="pv-p7-cohort-share tabular">{fmtPct(pct)}</span>
                  </span>
                  <span className="pv-p7-cohort-num tabular">
                    {p9Plural(bucket.tasks, 'exam')}
                  </span>
                  <span className="pv-p7-cohort-sub tabular">
                    {fmtCount(bucket.notFlawless)} disrupted · {fmtCount(bucket.flawless)} flawless
                    {bucket.cancelledAfterConduct > 0
                      ? ` · ${fmtCount(bucket.cancelledAfterConduct)} sat & cancelled`
                      : ''}
                  </span>
                  <span className="pv-p7-cohort-sub">{desc}</span>
                </button>
              );
            })}
          </div>
        </div>
      ) : null}

      {/* 5. ALL-INDIA 37-JURISDICTION BREAKDOWN + HOW TO READ THIS */}
      <div className="pv-results-split">
        <div className="pv-chart-card">
          <div className="pv-chart-subhead">
            <h3>
              All-India Jurisdiction Breakdown ({fmtCount(filteredJurisdictions.length)}
              {filteredJurisdictions.length !== jurisdictions.length
                ? ` of ${fmtCount(jurisdictions.length)}`
                : ''}
              )
            </h3>
            <span className="pv-chart-note">
              Union of India + 28 States + 8 UTs — click any row to filter authorities &amp; ledger
            </span>
          </div>

          {jurisdictions.length > 0 ? (
            <>
              <div className="pv-p6-ministry-controls">
                <div className="pv-p6-search-wrap">
                  <Search size={13} aria-hidden="true" className="pv-p6-search-icon" />
                  <input
                    type="search"
                    className="input-field pv-p6-search"
                    value={jurisdictionQuery}
                    onChange={(e) => setJurisdictionQuery(e.target.value)}
                    placeholder={`Search ${fmtCount(jurisdictions.length)} jurisdictions (Union, States, UTs)…`}
                    aria-label="Search Union, State, and UT exam jurisdictions"
                  />
                </div>
                <select
                  className="select-field pv-p9-sort-select"
                  value={jurisdictionSort}
                  onChange={(e) => setJurisdictionSort(e.target.value as P9JurisdictionSortKey)}
                  aria-label="Sort jurisdictions"
                >
                  <option value="not_flawless">Sort: Most disrupted</option>
                  <option value="rate">Sort: Disruption rate %</option>
                  <option value="cancelled_after">Sort: Sat &amp; cancelled</option>
                  <option value="tasks">Sort: Total exams</option>
                  <option value="name">Sort: A–Z</option>
                </select>
                <div className="pv-p6-filter-pills" role="group" aria-label="Filter jurisdictions by tier">
                  {(['all', 'union', 'state', 'ut'] as const).map((t) => (
                    <button
                      key={t}
                      type="button"
                      aria-pressed={selectedTier === t}
                      className={`btn btn-xs ${selectedTier === t ? 'btn-primary' : 'btn-ghost'}`}
                      onClick={() => handleSelectTier(t)}
                    >
                      {t === 'all' ? 'All' : t === 'union' ? 'Union' : t === 'state' ? 'States' : 'UTs'}
                    </button>
                  ))}
                </div>
              </div>

              {visibleJurisdictions.length > 0 ? (
                <div className="pv-ub-years-wrap">
                  <table className="pv-ub-table pv-ub-years pv-p6-ministries-table">
                    <caption className="pv-ub-caption">
                      Government exam reliability across Union of India, 28 States, and 8 Union Territories (Nov 2023 – Nov 2026).
                    </caption>
                    <thead>
                      <tr>
                        <th scope="col">Jurisdiction</th>
                        <th scope="col">Disrupted vs. Flawless</th>
                        <th scope="col" className="num">
                          Breakdown
                        </th>
                        <th scope="col" className="num">
                          Queue
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {visibleJurisdictions.map((j) => {
                        const display = formatJurisdictionLabel(
                          j.jurisdiction,
                          j.state_or_ut,
                          j.jurisdiction_level,
                        );
                        const lvl = (j.jurisdiction_level || 'union').toLowerCase();
                        const total = Math.max(0, j.tasks);
                        const notFlawlessPct =
                          j.not_flawless_rate_pct != null && Number.isFinite(j.not_flawless_rate_pct)
                            ? j.not_flawless_rate_pct
                            : calcPct(j.not_flawless, total);
                        const flawlessPct = calcPct(j.flawless, total);
                        const isSelected = selectedJurisdiction === j.jurisdiction;
                        const queueToken = p9JurisdictionFilterQuery(
                          j.jurisdiction,
                          j.state_or_ut,
                        );
                        const tierClass =
                          lvl === 'state'
                            ? 'p8-tier-chip--state'
                            : lvl === 'ut'
                              ? 'p8-tier-chip--ut'
                              : 'p8-tier-chip--union';

                        return (
                          <tr
                            key={j.jurisdiction}
                            className="pv-p6-ministry-row--interactive"
                            style={isSelected ? { background: 'var(--sunk)' } : undefined}
                            onClick={(e) => {
                              const target = e.target as HTMLElement | null;
                              if (target?.closest('a, button')) return;
                              setSelectedJurisdiction((prev) =>
                                prev === j.jurisdiction ? 'all' : j.jurisdiction,
                              );
                            }}
                          >
                            <th scope="row" className="pv-ub-year pv-p8-jur-name">
                              <button
                                type="button"
                                className="pv-ub-link"
                                aria-pressed={isSelected}
                                onClick={() =>
                                  setSelectedJurisdiction((prev) =>
                                    prev === j.jurisdiction ? 'all' : j.jurisdiction,
                                  )
                                }
                              >
                                {display}
                              </button>
                              <span className="pv-ub-sub">
                                <span
                                  className={`p8-tier-chip ${tierClass}`}
                                  style={{ fontSize: 10.5, padding: '0 6px' }}
                                >
                                  {lvl === 'union' ? 'Union' : lvl === 'ut' ? 'UT' : 'State'}
                                </span>
                                {' · '}
                                {p9Plural(total, 'exam')}
                              </span>
                            </th>
                            <td className="pv-ub-years-checked">
                              <span
                                className="pv-ub-bar pv-p9-dual-bar"
                                role="img"
                                aria-label={`${display}: ${fmtCount(j.not_flawless)} disrupted (${fmtPct(notFlawlessPct)}), ${fmtCount(j.flawless)} flawless (${fmtPct(flawlessPct)})`}
                              >
                                {notFlawlessPct > 0 ? (
                                  <span
                                    className="pv-ub-bar-seg pv-p9-rel-seg--disrupted"
                                    style={{ width: `${Math.min(100, notFlawlessPct)}%` }}
                                  />
                                ) : null}
                                {flawlessPct > 0 ? (
                                  <span
                                    className="pv-ub-bar-seg pv-p9-rel-seg--flawless"
                                    style={{ width: `${Math.min(100, flawlessPct)}%` }}
                                  />
                                ) : null}
                              </span>
                              <span className="pv-ub-sub tabular">
                                <strong>{fmtCount(j.not_flawless)}</strong> disrupted (
                                {fmtPct(notFlawlessPct)}) · {fmtCount(j.flawless)} flawless
                              </span>
                            </td>
                            <td className="num pv-ub-years-lines" data-label="Breakdown">
                              <span className="pv-ub-cell-val tabular">
                                {j.cancelled_after_conduct > 0
                                  ? `${fmtCount(j.cancelled_after_conduct)} sat & cancelled`
                                  : j.not_flawless > 0
                                    ? `${fmtCount(j.not_flawless)} disrupted`
                                    : '0 disrupted'}
                              </span>
                              <span className="pv-ub-sub tabular">
                                {[
                                  j.postponed > 0 ? `${fmtCount(j.postponed)} postp.` : null,
                                  j.rescheduled > 0 ? `${fmtCount(j.rescheduled)} resch.` : null,
                                  j.delayed > 0 ? `${fmtCount(j.delayed)} delayed` : null,
                                  j.cancelled_before_conduct > 0
                                    ? `${fmtCount(j.cancelled_before_conduct)} pre-cancel`
                                    : null,
                                  j.partial_reexam_or_glitch > 0
                                    ? `${fmtCount(j.partial_reexam_or_glitch)} glitch`
                                    : null,
                                ]
                                  .filter(Boolean)
                                  .join(' · ') ||
                                  (j.not_flawless === 0
                                    ? 'No disruptions'
                                    : 'Post-exam cancellation')}
                              </span>
                            </td>
                            <td className="num pv-ub-years-below" data-label="Queue">
                              {onFilterTasks ? (
                                <button
                                  type="button"
                                  className="pv-ub-link"
                                  onClick={() => onFilterTasks(queueToken)}
                                  aria-label={`Filter task queue for ${display}`}
                                >
                                  Tasks →
                                </button>
                              ) : (
                                <span className="pv-ub-sub">{fmtCount(total)}</span>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="pv-empty-box">
                  No jurisdictions match &ldquo;{jurisdictionQuery || p9TierLabel(selectedTier)}&rdquo;.
                </div>
              )}

              {jurisdictionQuery.trim().length === 0 &&
              selectedTier === 'all' &&
              filteredJurisdictions.length > P9_INITIAL_JURISDICTIONS ? (
                <div className="pv-list-more">
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    onClick={() => setShowAllJurisdictions((v) => !v)}
                  >
                    {showAllJurisdictions
                      ? `Show top ${P9_INITIAL_JURISDICTIONS} jurisdictions`
                      : `Show all ${fmtCount(filteredJurisdictions.length)} jurisdictions`}
                  </button>
                </div>
              ) : null}
            </>
          ) : (
            <div className="pv-empty-box">
              All-India Union, State, and UT examination breakdown appears here once the hub loads the P9 index.
            </div>
          )}
        </div>

        <div className="pv-chart-card pv-ub-guide">
          <div className="pv-chart-subhead">
            <h3>How to read this audit</h3>
          </div>
          <p>
            Every task downloads and SHA-256-verifies official examination schedules, calendars, and
            subsequent public notices or parliamentary replies within the <strong>3-year window
            ({auditWindowLabel})</strong>. If any official file fails hash checks or lacks verbatim
            evidence tokens, the deterministic engine fails closed as{' '}
            {FINDING_TAGS.not_enough_data.label}.
          </p>
          <p>
            Examinations confirmed conducted on schedule without cancellation, postponement, or
            partial re-exam are published as {FINDING_TAGS.cleared.label} with equal standing alongside
            documented disruption leads.
          </p>
          <p>
            All dossiers strictly follow <strong>BNS Sec 356</strong> factual neutrality and{' '}
            <strong>DPDP Act 2023</strong> privacy rules — citing the{' '}
            <strong>Public Examinations (Prevention of Unfair Means) Act, 2024</strong> and lawful
            operational explanations (preventive integrity safeguards, Election Model Code of
            Conduct, weather/disaster safety, CBT vendor incidents, or judicial stays). Candidate
            counts are shown only when disclosed in cited official notices.
          </p>
          {dryRun ? (
            <div className="pv-p9-dryrun-box tabular">
              <strong>Deterministic Corpus Verification:</strong>{' '}
              {p9Plural(dryRun.evaluated_tasks, 'task')} evaluated ·{' '}
              {p9Plural(dryRun.flagged_leads, 'disruption lead')} ·{' '}
              {fmtCount(dryRun.cleared_flawless)} flawless cleared ·{' '}
              {fmtCount(dryRun.unevaluable)} unevaluable ·{' '}
              {p9Plural(dryRun.verified_documents, 'verified document')}
            </div>
          ) : null}
          <div className="pv-ub-links">
            <a className="inline-link" href={documentsUrl}>
              Exam sources (CSV)
            </a>
            <a className="inline-link" href={coverageUrl}>
              Coverage (JSON)
            </a>
            <a className="inline-link" href={P9_METHOD_URL} target="_blank" rel="noopener noreferrer">
              Method <ExternalLink size={11} aria-hidden="true" className="pv-ub-ext" />
            </a>
          </div>
          {p9?.generated_on ? (
            <p className="pv-chart-note">
              P9 exam corpus index built {p9.generated_on}
              {p9.as_of_date && p9.as_of_date !== p9.generated_on
                ? ` · as of ${p9.as_of_date}`
                : ''}
              .
            </p>
          ) : null}
        </div>
      </div>

      {/* 6. CONDUCTING AUTHORITY / AGENCY LEADERBOARD */}
      <div className="pv-chart-card pv-p7-section-card">
        <div className="pv-chart-subhead">
          <h3>
            Conducting Authority &amp; Agency Leaderboard ({fmtCount(filteredAuthorities.length)}
            {filteredAuthorities.length !== topAuthorities.length
              ? ` of ${fmtCount(topAuthorities.length)}`
              : ''}
            )
          </h3>
          <span className="pv-chart-note">
            NTA, UPSC, SSC, RRB, NBEMS, State PSCs &amp; Police Boards ranked by non-flawless exams
          </span>
        </div>

        {activeAuthorityTypes.length > 0 ? (
          <div
            className="pv-p8-cat-pills"
            style={{ marginBottom: 12 }}
            role="group"
            aria-label="Filter by conducting authority type"
          >
            <span className="pv-p7-jur-label">Authority type:</span>
            <button
              type="button"
              aria-pressed={selectedAuthorityType === 'all'}
              className={`btn btn-xs ${selectedAuthorityType === 'all' ? 'btn-primary' : 'btn-ghost'}`}
              onClick={() => setSelectedAuthorityType('all')}
            >
              All ({fmtCount(activeAuthorityTypes.length)})
            </button>
            {activeAuthorityTypes.map((at) => {
              const active = selectedAuthorityType === at.authority_type;
              const meta = formatExamAuthorityType(at.authority_type);
              return (
                <button
                  key={at.authority_type}
                  type="button"
                  aria-pressed={active}
                  className={`btn btn-xs ${active ? 'btn-primary' : 'btn-ghost'}`}
                  onClick={() =>
                    setSelectedAuthorityType((prev) =>
                      prev === at.authority_type ? 'all' : at.authority_type,
                    )
                  }
                >
                  {meta.shortLabel} ({fmtCount(at.tasks)})
                </button>
              );
            })}
          </div>
        ) : null}

        {topAuthorities.length > 0 ? (
          <>
            <div className="pv-p6-ministry-controls">
              <div className="pv-p6-search-wrap">
                <Search size={13} aria-hidden="true" className="pv-p6-search-icon" />
                <input
                  type="search"
                  className="input-field pv-p6-search"
                  value={authorityQuery}
                  onChange={(e) => setAuthorityQuery(e.target.value)}
                  placeholder={`Search ${fmtCount(topAuthorities.length)} agencies (e.g. NTA, UPSC, SSC, RRB, UPPSC, BPSC)…`}
                  aria-label="Search conducting authorities and exam boards"
                />
              </div>
              {authorityQuery.trim().length > 0 || selectedAuthorityType !== 'all' ? (
                <button
                  type="button"
                  className="btn btn-ghost btn-xs"
                  onClick={() => {
                    setAuthorityQuery('');
                    setSelectedAuthorityType('all');
                  }}
                >
                  <RotateCcw size={11} aria-hidden="true" />
                  <span>Reset agency filter</span>
                </button>
              ) : null}
            </div>

            {visibleAuthorities.length > 0 ? (
              <div className="table-wrap pv-ub-leads-wrap">
                <table className="ledger-table pv-ub-table pv-ub-leads-table">
                  <caption className="pv-ub-caption">
                    Indian government exam conducting agencies ranked by non-flawless exam count over the past 3 years.
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">Conducting Authority</th>
                      <th scope="col">Jurisdiction &amp; Type</th>
                      <th scope="col" className="num">
                        Disrupted / Total
                      </th>
                      <th scope="col" className="num">
                        Sat &amp; Cancelled
                      </th>
                      <th scope="col" className="num">
                        Postp. / Resch. / Delay
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleAuthorities.map((a) => {
                      const jur = formatJurisdictionLabel(
                        a.jurisdiction,
                        null,
                        a.jurisdiction_level,
                      );
                      const atMeta = formatExamAuthorityType(a.authority_type);
                      const rate =
                        a.not_flawless_rate_pct != null && Number.isFinite(a.not_flawless_rate_pct)
                          ? a.not_flawless_rate_pct
                          : calcPct(a.not_flawless, a.tasks);
                      return (
                        <tr key={`${a.authority_key}-${a.jurisdiction}`}>
                          <td className="pv-ub-line pv-p8-scheme-col">
                            {onFilterTasks ? (
                              <button
                                type="button"
                                className="pv-ub-link"
                                onClick={() => onFilterTasks(a.authority)}
                              >
                                {a.authority}
                              </button>
                            ) : (
                              <span>{a.authority}</span>
                            )}
                            {a.documented_affected_candidates &&
                            a.documented_affected_candidates > 0 ? (
                              <span className="pv-ub-sub tabular">
                                {formatCandidateCountCompact(a.documented_affected_candidates)}{' '}
                                candidates documented
                              </span>
                            ) : null}
                          </td>
                          <td data-label="Jurisdiction & type">
                            <span>{jur}</span>
                            <span className="pv-ub-sub">{atMeta.shortLabel}</span>
                          </td>
                          <td className="num tabular" data-label="Disrupted / Total">
                            <span className="pv-ub-cell-val">
                              <strong>{fmtCount(a.not_flawless)}</strong> / {fmtCount(a.tasks)} (
                              {fmtPct(rate)})
                            </span>
                            <span className="pv-ub-sub">
                              {fmtCount(a.flawless)} flawless
                              {a.leads > 0 ? ` · ${p9Plural(a.leads, 'lead')}` : ''}
                            </span>
                          </td>
                          <td className="num tabular" data-label="Sat & cancelled">
                            <span
                              className={
                                a.cancelled_after_conduct > 0
                                  ? 'p9-disrupt p9-disrupt--cancelled-after'
                                  : 'pv-ub-sub'
                              }
                            >
                              {fmtCount(a.cancelled_after_conduct)}
                            </span>
                          </td>
                          <td className="num tabular" data-label="Postp. / Resch. / Delay">
                            <span className="pv-ub-cell-val">
                              {fmtCount(a.postponed)} / {fmtCount(a.rescheduled)} /{' '}
                              {fmtCount(a.delayed)}
                            </span>
                            {a.cancelled_before_conduct > 0 || a.partial_reexam_or_glitch > 0 ? (
                              <span className="pv-ub-sub">
                                {[
                                  a.cancelled_before_conduct > 0
                                    ? `${fmtCount(a.cancelled_before_conduct)} pre-cancel`
                                    : null,
                                  a.partial_reexam_or_glitch > 0
                                    ? `${fmtCount(a.partial_reexam_or_glitch)} glitch`
                                    : null,
                                ]
                                  .filter(Boolean)
                                  .join(' · ')}
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
                No conducting authorities match the active filter combination.
              </div>
            )}

            {authorityQuery.trim().length === 0 &&
            selectedTier === 'all' &&
            selectedAuthorityType === 'all' &&
            selectedJurisdiction === 'all' &&
            filteredAuthorities.length > P9_INITIAL_AUTHORITIES ? (
              <div className="pv-list-more">
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => setShowAllAuthorities((v) => !v)}
                >
                  {showAllAuthorities
                    ? `Show top ${P9_INITIAL_AUTHORITIES} agencies`
                    : `Show all ${fmtCount(filteredAuthorities.length)} agencies`}
                </button>
              </div>
            ) : null}
          </>
        ) : (
          <div className="pv-empty-box">
            Agency-by-agency exam reliability leaderboard appears here once the hub loads the P9 index.
          </div>
        )}
      </div>

      {/* 7. FILTERABLE EXAM AUDIT LEDGER (LIVE LEADS + CORPUS FALLBACK) */}
      <div className="pv-ub-leads">
        <div className="pv-chart-subhead">
          <h3>
            Exam Audit Ledger ({fmtCount(filteredLeads.length)}
            {filteredLeads.length !== allLeads.length ? ` of ${fmtCount(allLeads.length)}` : ''})
          </h3>
          <span className="pv-chart-note">
            {isCorpusFallback
              ? 'Indexed corpus records (awaiting live swarm execution) — original schedule vs. conduct notice'
              : 'Verified and in-review exam conduct dossiers with SHA-256 provenance'}
          </span>
        </div>

        {isCorpusFallback && allLeads.length > 0 ? (
          <div className="pv-p9-fallback-banner" role="status">
            <strong>Corpus preview ledger:</strong> Live volunteer submissions have not been logged
            for these tasks yet. Showing SHA-256-pinned non-flawless exam records from the indexed
            1,000-exam corpus so citizens can inspect schedules, notices, and sources immediately.
          </div>
        ) : null}

        {/* Multi-Axis Ledger Filter Bar */}
        <div className="pv-p9-ledger-controls">
          <div className="pv-p6-search-wrap">
            <Search size={13} aria-hidden="true" className="pv-p6-search-icon" />
            <input
              type="search"
              className="input-field pv-p6-search"
              value={ledgerQuery}
              onChange={(e) => setLedgerQuery(e.target.value)}
              placeholder="Search exam name, NTA/UPSC/SSC/PSC, State/UT, notice ref…"
              aria-label="Search exam audit ledger"
            />
          </div>

          <select
            className="select-field pv-p9-filter-select"
            value={selectedDisruption}
            onChange={(e) => setSelectedDisruption(e.target.value)}
            aria-label="Filter ledger by disruption type"
          >
            <option value="all">All outcomes</option>
            <option value="not_flawless">Did Not Happen Flawlessly</option>
            <option value="cancelled_after_conduct">Took Exam, Then Cancelled</option>
            <option value="postponed">Postponed</option>
            <option value="rescheduled">Rescheduled</option>
            <option value="delayed">Delayed</option>
            <option value="cancelled_before_conduct">Cancelled Before Conduct</option>
            <option value="partial_reexam_or_glitch">Technical Glitch / Partial Re-Exam</option>
            <option value="flawless_conduct">Conducted Flawlessly (Cleared)</option>
          </select>

          <select
            className="select-field pv-p9-filter-select"
            value={selectedYear}
            onChange={(e) => setSelectedYear(e.target.value)}
            aria-label="Filter ledger by exam year"
          >
            <option value="all">All years (2023–2026)</option>
            {(years.length > 0 ? years.map((y) => y.year) : ['2023', '2024', '2025', '2026']).map(
              (yr) => (
                <option key={yr} value={yr}>
                  {yr}
                </option>
              ),
            )}
          </select>

          <select
            className="select-field pv-p9-filter-select"
            value={selectedAuthorityType}
            onChange={(e) => setSelectedAuthorityType(e.target.value)}
            aria-label="Filter ledger by authority type"
          >
            <option value="all">All authority types</option>
            <option value="central_testing_agency">Central Testing Agency</option>
            <option value="union_psc_or_recruitment">Union PSC / SSC</option>
            <option value="railway_banking_psu">Railways, Banking &amp; PSU</option>
            <option value="defence_medical_technical">Defence, Medical &amp; Tech</option>
            <option value="state_psc">State PSC</option>
            <option value="state_police_or_subordinate">State Police &amp; Subordinate</option>
            <option value="state_teacher_or_entrance">State Teacher &amp; Entrance</option>
            <option value="ut_recruitment_or_board">UT Recruitment Board</option>
          </select>

          {hasActiveFilters ? (
            <button type="button" className="btn btn-ghost btn-xs" onClick={resetAllFilters}>
              <RotateCcw size={11} aria-hidden="true" />
              <span>Reset all</span>
            </button>
          ) : null}
        </div>

        {hasActiveFilters ? (
          <div className="pv-p8-active-filters" style={{ marginBottom: 10 }}>
            <span>
              Active filters:{' '}
              {[
                selectedDisruption !== 'all' ? p9DisruptionLabel(selectedDisruption) : null,
                selectedYear !== 'all' ? `Year ${selectedYear}` : null,
                selectedTier !== 'all' ? p9TierLabel(selectedTier) : null,
                selectedJurisdiction !== 'all'
                  ? formatJurisdictionLabel(selectedJurisdiction)
                  : null,
                selectedAuthorityType !== 'all'
                  ? p9AuthorityTypeLabel(selectedAuthorityType)
                  : null,
                ledgerQuery.trim() ? `“${ledgerQuery.trim()}”` : null,
              ]
                .filter(Boolean)
                .join(' · ')}
            </span>
            <button type="button" className="btn btn-ghost btn-xs" onClick={resetAllFilters}>
              <RotateCcw size={11} aria-hidden="true" />
              <span>Reset filters</span>
            </button>
          </div>
        ) : null}

        {visibleLeads.length > 0 ? (
          <>
            <div className="table-wrap pv-ub-leads-wrap">
              <table className="ledger-table pv-ub-table pv-ub-leads-table">
                <caption className="pv-ub-caption">
                  Government examination conduct ledger comparing original schedule against actual outcome with SHA-256-verified official notices.
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Exam &amp; Authority</th>
                    <th scope="col">Jurisdiction &amp; Timeline</th>
                    <th scope="col">Disruption &amp; Reason</th>
                    <th scope="col">Schedule vs. Outcome</th>
                    <th scope="col">Status &amp; Source</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleLeads.map((lead) => {
                    const disrupt = formatExamDisruption(
                      lead.disruption_type || lead.signal_category,
                    );
                    const reason =
                      lead.disruption_reason_category &&
                      lead.disruption_reason_category !== 'none_flawless'
                        ? formatExamDisruptionReason(lead.disruption_reason_category)
                        : null;
                    const statusMeta = p9LeadStatusMeta(lead.status);
                    const jur = formatJurisdictionLabel(
                      lead.jurisdiction,
                      lead.state_or_ut,
                      lead.jurisdiction_level,
                    );
                    const candCompact = formatCandidateCountCompact(
                      lead.affected_candidates_count,
                    );
                    const leadUrl = safeExternalUrl(lead.source_url);
                    const leadSecUrl = safeExternalUrl(lead.secondary_url);
                    const rowKey = lead.finding_id || lead.task_id || lead.case_key;

                    return (
                      <tr key={String(rowKey)}>
                        <td className="pv-ub-line pv-p8-lead-scheme">
                          {lead.finding_id && onSelectFinding ? (
                            <button
                              type="button"
                              className="pv-ub-link"
                              onClick={() => onSelectFinding(lead.finding_id!)}
                            >
                              {lead.exam_name}
                              {lead.cycle_or_stage ? ` · ${lead.cycle_or_stage}` : ''}
                            </button>
                          ) : onFilterTasks ? (
                            <button
                              type="button"
                              className="pv-ub-link"
                              onClick={() => onFilterTasks(lead.exam_name)}
                            >
                              {lead.exam_name}
                              {lead.cycle_or_stage ? ` · ${lead.cycle_or_stage}` : ''}
                            </button>
                          ) : (
                            <span>
                              {lead.exam_name}
                              {lead.cycle_or_stage ? ` · ${lead.cycle_or_stage}` : ''}
                            </span>
                          )}
                          <span className="pv-ub-sub">
                            {lead.is_anchor_case ? (
                              <>
                                <span
                                  className="p9-anchor-chip"
                                  style={{ fontSize: 10.5, padding: '0 6px' }}
                                >
                                  Anchor
                                </span>{' '}
                              </>
                            ) : null}
                            {lead.authority}
                          </span>
                        </td>
                        <td className="tabular pv-ub-lead-year" data-label="Jurisdiction & timeline">
                          <span>
                            {jur} · <span className="font-mono">{String(lead.exam_year)}</span>
                          </span>
                          <span className="pv-ub-sub">
                            {lead.scheduled_date || '—'}
                            {lead.actual_or_event_date &&
                            lead.actual_or_event_date !== lead.scheduled_date
                              ? ` → ${lead.actual_or_event_date}`
                              : ''}
                            {!disrupt.isCleared && lead.delay_days != null && lead.delay_days > 0
                              ? ` (+${fmtCount(lead.delay_days)}d)`
                              : ''}
                          </span>
                          {candCompact ? (
                            <span
                              className="pv-ub-sub"
                              title={lead.affected_candidates_basis || 'Documented candidates'}
                            >
                              {candCompact} candidates
                            </span>
                          ) : null}
                        </td>
                        <td data-label="Disruption & reason">
                          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                            <span className={disrupt.chipClass}>{disrupt.shortLabel}</span>
                            {lead.students_took_exam_before_cancellation ? (
                              <span className="p9-sat-chip">Sat, then cancelled</span>
                            ) : null}
                            {reason ? (
                              <span className={reason.chipClass}>{reason.shortLabel}</span>
                            ) : null}
                          </div>
                        </td>
                        <td className="pv-p8-lead-compare" data-label="Schedule vs. outcome">
                          <div className="pv-p6-excerpt">
                            <strong>Scheduled:</strong> {p9ShortText(lead.schedule_summary, 105)}
                          </div>
                          <div className="pv-p6-excerpt pv-p6-excerpt--followup">
                            <strong>{disrupt.isCleared ? 'Verified:' : 'Outcome:'}</strong>{' '}
                            {p9ShortText(lead.outcome_summary, 105)}
                          </div>
                        </td>
                        <td className="pv-ub-lead-status" data-label="Status & source">
                          <span className={statusMeta.badgeClass} title={statusMeta.description}>
                            {statusMeta.label}
                          </span>
                          {leadUrl || leadSecUrl ? (
                            <div className="pv-ub-sub" style={{ marginTop: 4 }}>
                              {leadUrl ? (
                                <a
                                  href={leadUrl}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="inline-link"
                                >
                                  Notice <ExternalLink size={10} aria-hidden="true" />
                                </a>
                              ) : null}
                              {leadUrl && leadSecUrl && leadSecUrl !== leadUrl ? ' · ' : null}
                              {leadSecUrl && leadSecUrl !== leadUrl ? (
                                <a
                                  href={leadSecUrl}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="inline-link"
                                >
                                  Notice 2 <ExternalLink size={10} aria-hidden="true" />
                                </a>
                              ) : null}
                            </div>
                          ) : null}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {filteredLeads.length > P9_INITIAL_LEADS ? (
              <div className="pv-list-more">
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => setShowAllLeads((v) => !v)}
                >
                  {showAllLeads
                    ? `Show first ${P9_INITIAL_LEADS} exams`
                    : `Show all ${fmtCount(filteredLeads.length)} matching exams`}
                </button>
              </div>
            ) : null}
          </>
        ) : (
          <div className="pv-empty-box">
            {selectedDisruption === 'flawless_conduct' && headline.flawlessCount > 0
              ? `${p9Plural(headline.flawlessCount, 'audited exam')} in the 3-year corpus passed as Conducted Flawlessly (Cleared). This ledger lists non-flawless disruption notices; reset filters above to view disruption records or browse the full task queue below.`
              : hasActiveFilters && allLeads.length > 0
                ? 'No audited exams match your active filter combination. Reset filters above to view all records.'
                : 'Audited Indian government exam records appear here once the hub loads the P9 corpus or contributor agents submit verified results.'}
          </div>
        )}
      </div>
    </section>
  );
}

/* ---------- 3I. P10 Innocent But Locked Up: post-acquittal custody overruns & contempt compliance ---------- */

const P10_METHOD_URL =
  'https://github.com/aiwatchdogsindia/aiwatchdogs.in/blob/main/projects/innocent-but-locked-up/METHODOLOGY.md';
export const P10_DEFAULT_HOOK =
  'The court declared them acquitted and ordered release. Why did the prison keep them locked up until contempt was filed?';
const P10_INITIAL_STATES = 12;
const P10_INITIAL_COURTS = 12;
const P10_INITIAL_PRISONS = 10;

export type P10PortalCategoryKey =
  | 'court_portal'
  | 'prison_portal'
  | 'police_portal'
  | 'nhrc_shrc'
  | 'nalsa_slsa'
  | 'parliament'
  | 'govt_ministry'
  | 'cag_audit';

export type P10CourtTierKey =
  | 'supreme_court'
  | 'high_court'
  | 'district_court'
  | 'statutory_body';

export type P10ChainStatusKey =
  | 'full_4_stage_chain'
  | 'partial_3_stage'
  | 'institutional_record'
  | 'compliant_control';

export type P10OverrunMechanismKey =
  | 'post_acquittal_contempt_overrun'
  | 'bail_or_release_order_contempt'
  | 'warrant_or_faster_transmission_delay'
  | 'statutory_commission_detention_finding'
  | 'institutional_compliance_gap'
  | 'timely_release_or_compliant';

export interface P10NormalizedPortal {
  portal_category: P10PortalCategoryKey;
  label: string;
  shortLabel: string;
  chipClass: string;
  segClass: string;
  tasks: number;
  done: number;
  claimed: number;
  open: number;
  pct: number;
  leads: number;
  cleared: number;
  sharePct: number;
}

export interface P10NormalizedYear {
  year: number;
  tasks: number;
  done: number;
  claimed: number;
  open: number;
  pct: number;
  leads: number;
  cleared: number;
  full_4_stage: number;
  overrun_days: number;
}

export interface P10NormalizedCourtTier {
  key: P10CourtTierKey;
  label: string;
  shortLabel: string;
  chipClass: string;
  tasks: number;
  done: number;
  leads: number;
  cleared: number;
  sharePct: number;
}

export interface P10NormalizedMechanism {
  key: P10OverrunMechanismKey;
  label: string;
  shortLabel: string;
  chipClass: string;
  fillClass: string;
  isCleared: boolean;
  tasks: number;
  leads: number;
  sharePct: number;
}

export interface P10NormalizedChainStatus {
  key: P10ChainStatusKey;
  label: string;
  shortLabel: string;
  chipClass: string;
  count: number;
  sharePct: number;
}

export interface P10StageStep {
  stage: 1 | 2 | 3 | 4;
  kicker: string;
  title: string;
  verified: boolean;
  date: string | null;
  primary: string;
  secondary: string | null;
  badge: string | null;
  quote: string | null;
}

export function p10PortalLabel(portal: string): string {
  return formatPortalCategory(portal).label;
}

export function p10MechanismLabel(mechanism: string): string {
  return formatCustodyOverrunMechanism(mechanism).label;
}

export function p10CourtTierLabel(tier: string): string {
  return formatCourtTier(tier).label;
}

export function p10ChainStatusLabel(status: string): string {
  return formatChainStatus(status).label;
}

export function p10StateFilterQuery(
  stateUt?: string | null,
  jurisdiction?: string | null,
): string {
  const cleanState = (stateUt || '').trim();
  if (cleanState) return cleanState;
  const cleanJur = (jurisdiction || '').trim();
  if (!cleanJur) return '';
  if (/^state:/i.test(cleanJur)) return cleanJur.slice(6).trim();
  if (/^ut:/i.test(cleanJur)) return cleanJur.slice(3).trim();
  return cleanJur;
}

export function p10NormalizePortals(
  byPortal?:
    | VisualInnocentButLockedUpPortal[]
    | Record<string, VisualInnocentButLockedUpPortal | number>
    | null,
): {
  items: P10NormalizedPortal[];
  totalTasks: number;
  totalLeads: number;
} {
  const ordered: Array<{ key: P10PortalCategoryKey; segClass: string }> = [
    { key: 'court_portal', segClass: 'pv-p10-seg--court' },
    { key: 'prison_portal', segClass: 'pv-p10-seg--prison' },
    { key: 'police_portal', segClass: 'pv-p10-seg--police' },
    { key: 'nhrc_shrc', segClass: 'pv-p10-seg--nhrc' },
    { key: 'nalsa_slsa', segClass: 'pv-p10-seg--nalsa' },
    { key: 'parliament', segClass: 'pv-p10-seg--parliament' },
    { key: 'govt_ministry', segClass: 'pv-p10-seg--govt' },
    { key: 'cag_audit', segClass: 'pv-p10-seg--cag' },
  ];

  const lookup = new Map<string, VisualInnocentButLockedUpPortal | number>();
  if (Array.isArray(byPortal)) {
    for (const item of byPortal) {
      if (item && item.portal_category) {
        lookup.set(item.portal_category.trim().toLowerCase().replace(/[\s-]+/g, '_'), item);
      }
    }
  } else if (byPortal && typeof byPortal === 'object') {
    for (const [k, v] of Object.entries(byPortal)) {
      lookup.set(k.trim().toLowerCase().replace(/[\s-]+/g, '_'), v);
    }
  }

  const rows = ordered.map(({ key, segClass }) => {
    const raw = lookup.get(key);
    const meta = formatPortalCategory(key);
    if (typeof raw === 'number') {
      const tasks = Math.max(0, raw);
      return {
        portal_category: key,
        label: meta.label,
        shortLabel: meta.shortLabel,
        chipClass: meta.chipClass,
        segClass,
        tasks,
        done: 0,
        claimed: 0,
        open: tasks,
        pct: 0,
        leads: 0,
        cleared: 0,
      };
    }
    const obj = raw && typeof raw === 'object' ? raw : null;
    const tasks = Math.max(0, Number(obj?.tasks) || 0);
    const done = Math.max(0, Number(obj?.done) || 0);
    const claimed = Math.max(0, Number(obj?.claimed) || 0);
    const open =
      obj?.open != null ? Math.max(0, Number(obj.open) || 0) : Math.max(0, tasks - done - claimed);
    const pct =
      obj?.pct != null && Number.isFinite(Number(obj.pct))
        ? Math.max(0, Number(obj.pct))
        : calcPct(done, tasks);
    return {
      portal_category: key,
      label: obj?.label?.trim() || meta.label,
      shortLabel: meta.shortLabel,
      chipClass: meta.chipClass,
      segClass,
      tasks,
      done,
      claimed,
      open,
      pct,
      leads: Math.max(0, Number(obj?.leads) || 0),
      cleared: Math.max(0, Number(obj?.cleared) || 0),
    };
  });

  const totalTasks = rows.reduce((acc, r) => acc + r.tasks, 0);
  const totalLeads = rows.reduce((acc, r) => acc + r.leads, 0);
  const denominator = totalTasks > 0 ? totalTasks : totalLeads;

  const items: P10NormalizedPortal[] = rows
    .map((r) => ({
      ...r,
      sharePct: calcPct(totalTasks > 0 ? r.tasks : r.leads, denominator),
    }))
    .sort((a, b) => b.tasks - a.tasks || b.leads - a.leads);

  return { items, totalTasks, totalLeads };
}

export function p10NormalizeYears(
  byYear?: VisualInnocentButLockedUpYear[] | null,
): P10NormalizedYear[] {
  if (!Array.isArray(byYear)) return [];
  return byYear
    .map((y) => {
      const yr = Number(y.year);
      if (!Number.isFinite(yr) || yr < 2000) return null;
      const tasks = Math.max(0, Number(y.tasks) || 0);
      const done = Math.max(0, Number(y.done) || 0);
      const claimed = Math.max(0, Number(y.claimed) || 0);
      const open =
        y.open != null ? Math.max(0, Number(y.open) || 0) : Math.max(0, tasks - done - claimed);
      const pct =
        y.pct != null && Number.isFinite(Number(y.pct))
          ? Math.max(0, Number(y.pct))
          : calcPct(done, tasks);
      return {
        year: yr,
        tasks,
        done,
        claimed,
        open,
        pct,
        leads: Math.max(0, Number(y.leads) || 0),
        cleared: Math.max(0, Number(y.cleared) || 0),
        full_4_stage: Math.max(0, Number(y.full_4_stage) || 0),
        overrun_days: Math.max(0, Number(y.overrun_days) || 0),
      };
    })
    .filter((item): item is P10NormalizedYear => item !== null)
    .sort((a, b) => a.year - b.year);
}

export function p10NormalizeCourtTiers(
  byTier?: Record<string, VisualInnocentButLockedUpCourtTierCounts | number> | null,
): {
  items: P10NormalizedCourtTier[];
  totalTasks: number;
} {
  const keys: P10CourtTierKey[] = [
    'supreme_court',
    'high_court',
    'district_court',
    'statutory_body',
  ];
  const rows = keys.map((key) => {
    const raw = byTier?.[key];
    const meta = formatCourtTier(key);
    if (typeof raw === 'number') {
      return {
        key,
        label: meta.label,
        shortLabel: meta.shortLabel,
        chipClass: meta.chipClass,
        tasks: Math.max(0, raw),
        done: 0,
        leads: 0,
        cleared: 0,
      };
    }
    const obj = raw && typeof raw === 'object' ? raw : null;
    return {
      key,
      label: meta.label,
      shortLabel: meta.shortLabel,
      chipClass: meta.chipClass,
      tasks: Math.max(0, Number(obj?.tasks) || 0),
      done: Math.max(0, Number(obj?.done) || 0),
      leads: Math.max(0, Number(obj?.leads) || 0),
      cleared: Math.max(0, Number(obj?.cleared) || 0),
    };
  });
  const totalTasks = rows.reduce((acc, r) => acc + r.tasks, 0);
  const items: P10NormalizedCourtTier[] = rows.map((r) => ({
    ...r,
    sharePct: calcPct(r.tasks, totalTasks),
  }));
  return { items, totalTasks };
}

export function p10NormalizeMechanisms(
  byMechanism?: Record<string, VisualInnocentButLockedUpMechanismCounts | number> | null,
  bySignal?: Record<string, number> | null,
): {
  items: P10NormalizedMechanism[];
  totalTasks: number;
  totalLeads: number;
} {
  const ordered: Array<{
    key: P10OverrunMechanismKey;
    ruleKey: string;
    fillClass: string;
  }> = [
    {
      key: 'post_acquittal_contempt_overrun',
      ruleKey: 'P10-POST-ACQUITTAL-CONTEMPT-OVERRUN',
      fillClass: 'pv-p10-mech-fill--acquittal-contempt',
    },
    {
      key: 'bail_or_release_order_contempt',
      ruleKey: 'P10-RELEASE-ORDER-CONTEMPT-OVERRUN',
      fillClass: 'pv-p10-mech-fill--release-contempt',
    },
    {
      key: 'warrant_or_faster_transmission_delay',
      ruleKey: 'P10-WARRANT-TRANSMISSION-DELAY',
      fillClass: 'pv-p10-mech-fill--transmission',
    },
    {
      key: 'statutory_commission_detention_finding',
      ruleKey: 'P10-STATUTORY-UNLAWFUL-DETENTION',
      fillClass: 'pv-p10-mech-fill--commission',
    },
    {
      key: 'institutional_compliance_gap',
      ruleKey: 'P10-INSTITUTIONAL-COMPLIANCE-GAP',
      fillClass: 'pv-p10-mech-fill--compliance',
    },
    {
      key: 'timely_release_or_compliant',
      ruleKey: 'CLEARED',
      fillClass: 'pv-p10-mech-fill--compliant',
    },
  ];

  const rows = ordered.map(({ key, ruleKey, fillClass }) => {
    const rawMech = byMechanism?.[key];
    let tasks = 0;
    let leads = 0;
    if (typeof rawMech === 'number') {
      tasks = Math.max(0, rawMech);
    } else if (rawMech && typeof rawMech === 'object') {
      tasks = Math.max(0, Number(rawMech.tasks) || 0);
      leads = Math.max(0, Number(rawMech.leads) || 0);
    }
    if (leads === 0 && bySignal) {
      const sigVal = bySignal[key] ?? bySignal[ruleKey] ?? bySignal[ruleKey.toLowerCase()];
      if (sigVal != null) {
        leads = Math.max(0, Number(sigVal) || 0);
      }
    }
    const meta = formatCustodyOverrunMechanism(key);
    return {
      key,
      label: meta.label,
      shortLabel: meta.shortLabel,
      chipClass: meta.chipClass,
      fillClass,
      isCleared: meta.isCleared,
      tasks,
      leads,
    };
  });

  const totalTasks = rows.reduce((acc, r) => acc + r.tasks, 0);
  const totalLeads = rows.reduce((acc, r) => acc + r.leads, 0);
  const denominator = totalTasks > 0 ? totalTasks : totalLeads;

  const items: P10NormalizedMechanism[] = rows.map((r) => ({
    ...r,
    sharePct: calcPct(totalTasks > 0 ? r.tasks : r.leads, denominator),
  }));

  return { items, totalTasks, totalLeads };
}

export function p10NormalizeChainStatuses(
  byChain?: Record<string, number> | null,
): {
  items: P10NormalizedChainStatus[];
  total: number;
} {
  const keys: P10ChainStatusKey[] = [
    'full_4_stage_chain',
    'partial_3_stage',
    'institutional_record',
    'compliant_control',
  ];
  const counts = keys.map((k) => Math.max(0, Number(byChain?.[k]) || 0));
  const total = counts.reduce((acc, n) => acc + n, 0);
  const items: P10NormalizedChainStatus[] = keys.map((k, idx) => {
    const meta = formatChainStatus(k);
    const count = counts[idx];
    return {
      key: k,
      label: meta.label,
      shortLabel: meta.shortLabel,
      chipClass: meta.chipClass,
      count,
      sharePct: calcPct(count, total),
    };
  });
  return { items, total };
}

export function p10FilterStates(
  states: VisualInnocentButLockedUpState[],
  query: string,
): VisualInnocentButLockedUpState[] {
  const q = query.trim().toLowerCase();
  if (!q) return states;
  return states.filter(
    (s) =>
      (s.state_ut || '').toLowerCase().includes(q) ||
      (s.jurisdiction || '').toLowerCase().includes(q),
  );
}

export function p10FilterCourts(
  courts: VisualInnocentButLockedUpCourt[],
  query: string,
  tierFilter: 'all' | P10CourtTierKey = 'all',
  stateFilter = 'all',
): VisualInnocentButLockedUpCourt[] {
  const q = query.trim().toLowerCase();
  const st = stateFilter.trim().toLowerCase();
  return courts.filter((c) => {
    const tier = (c.court_tier || '').trim().toLowerCase();
    if (tierFilter !== 'all' && tier !== tierFilter) return false;
    if (st && st !== 'all') {
      const courtState = (c.state_ut || '').trim().toLowerCase();
      if (st === 'union of india') {
        if (courtState && courtState !== 'union of india') return false;
      } else if (courtState !== st) {
        return false;
      }
    }
    if (!q) return true;
    return (
      c.court_name.toLowerCase().includes(q) ||
      (c.state_ut || 'Union of India').toLowerCase().includes(q) ||
      formatCourtTier(c.court_tier).label.toLowerCase().includes(q)
    );
  });
}

export function p10FilterLeads(
  leads: VisualInnocentButLockedUpLead[],
  yearFilter: 'all' | number = 'all',
  stateFilter = 'all',
  portalFilter: 'all' | P10PortalCategoryKey = 'all',
  chainFilter: 'all' | P10ChainStatusKey = 'all',
  mechanismFilter: 'all' | P10OverrunMechanismKey = 'all',
  courtTierFilter: 'all' | P10CourtTierKey = 'all',
): VisualInnocentButLockedUpLead[] {
  const st = stateFilter.trim().toLowerCase();
  return leads.filter((l) => {
    if (yearFilter !== 'all' && Number(l.year) !== yearFilter) return false;
    if (st && st !== 'all') {
      const leadState = (l.state_ut || '').trim().toLowerCase();
      const leadJur = p10StateFilterQuery(l.state_ut, l.jurisdiction).toLowerCase();
      if (st === 'union of india') {
        if (
          (leadState && leadState !== 'union of india') ||
          (leadJur && leadJur !== 'union of india')
        ) {
          return false;
        }
      } else if (leadState !== st && leadJur !== st) {
        return false;
      }
    }
    if (
      portalFilter !== 'all' &&
      (l.portal_category || '').trim().toLowerCase().replace(/[\s-]+/g, '_') !== portalFilter
    ) {
      return false;
    }
    if (chainFilter !== 'all') {
      const cStatus = formatChainStatus(l.chain_status, l.stages_verified_count).key;
      if (cStatus !== chainFilter) return false;
    }
    if (
      mechanismFilter !== 'all' &&
      formatCustodyOverrunMechanism(l.overrun_mechanism || l.signal_category).key !== mechanismFilter
    ) {
      return false;
    }
    if (
      courtTierFilter !== 'all' &&
      (l.court_tier || '').trim().toLowerCase().replace(/[\s-]+/g, '_') !== courtTierFilter
    ) {
      return false;
    }
    return true;
  });
}

export function p10BuildFourStageChain(rec: {
  overrun_mechanism?: string | null;
  chain_status?: string | null;
  stages_verified_count?: number | null;
  stage_1_acquittal_verified?: boolean;
  stage_2_continued_detention_verified?: boolean;
  stage_3_contempt_verified?: boolean;
  stage_4_release_verified?: boolean;
  acquittal_order_date?: string | null;
  acquittal_court?: string | null;
  acquittal_case_number?: string | null;
  acquittal_order_type?: string | null;
  acquittal_quote?: string | null;
  prison_name?: string | null;
  custody_authority?: string | null;
  non_release_reason?: string | null;
  overrun_days?: number | null;
  overrun_window?: string | null;
  contempt_case_number?: string | null;
  contempt_court?: string | null;
  contempt_filing_date?: string | null;
  contempt_order_date?: string | null;
  contempt_quote?: string | null;
  release_date?: string | null;
  release_verified?: boolean;
  compensation_inr?: number | null;
  judicial_direction_summary?: string | null;
}): P10StageStep[] {
  const isCompliant =
    formatCustodyOverrunMechanism(rec.overrun_mechanism).isCleared ||
    (rec.chain_status || '').trim().toLowerCase() === 'compliant_control';
  const isFull4Stage =
    !isCompliant &&
    ((rec.chain_status || '').trim().toLowerCase() === 'full_4_stage_chain' ||
      (rec.stages_verified_count != null && rec.stages_verified_count >= 4));

  const s1Verified =
    typeof rec.stage_1_acquittal_verified === 'boolean'
      ? rec.stage_1_acquittal_verified
      : isFull4Stage ||
        Boolean(rec.acquittal_order_date || rec.acquittal_case_number || rec.acquittal_quote);
  const s2Verified =
    typeof rec.stage_2_continued_detention_verified === 'boolean'
      ? rec.stage_2_continued_detention_verified
      : isCompliant && (rec.overrun_days == null || rec.overrun_days === 0)
        ? false
        : isFull4Stage || Boolean(rec.overrun_days != null && rec.overrun_days > 0);
  const s3Verified =
    typeof rec.stage_3_contempt_verified === 'boolean'
      ? rec.stage_3_contempt_verified
      : isFull4Stage ||
        Boolean(
          rec.contempt_case_number ||
            rec.contempt_court ||
            rec.contempt_filing_date ||
            rec.contempt_order_date ||
            rec.contempt_quote,
        );
  const s4Verified =
    typeof rec.stage_4_release_verified === 'boolean'
      ? rec.stage_4_release_verified
      : isFull4Stage || Boolean(rec.release_verified || rec.release_date);

  const overrunBadge =
    rec.overrun_days != null && rec.overrun_days > 0
      ? `+${rec.overrun_days.toLocaleString('en-IN')} ${rec.overrun_days === 1 ? 'day' : 'days'} overrun`
      : rec.overrun_days === 0
        ? '0 days overrun'
        : null;
  const compBadge =
    rec.compensation_inr != null && rec.compensation_inr > 0
      ? `${formatInr(rec.compensation_inr)} compensation`
      : null;

  return [
    {
      stage: 1,
      kicker: 'Stage 1',
      title: rec.acquittal_order_type
        ? formatAcquittalOrderType(rec.acquittal_order_type)
        : 'Acquitted / Release Ordered',
      verified: s1Verified,
      date: rec.acquittal_order_date || null,
      primary:
        [rec.acquittal_court, rec.acquittal_case_number].filter(Boolean).join(' · ') ||
        'Judicial acquittal or release order verified in primary record',
      secondary: null,
      badge: rec.acquittal_order_date ? rec.acquittal_order_date : null,
      quote: rec.acquittal_quote || null,
    },
    {
      stage: 2,
      kicker: 'Stage 2',
      title: 'Held in Prison',
      verified: s2Verified,
      date: rec.overrun_window || null,
      primary:
        [rec.prison_name, rec.custody_authority].filter(Boolean).join(' · ') ||
        'Continued custody after judicial release order',
      secondary: rec.non_release_reason || null,
      badge: overrunBadge,
      quote: null,
    },
    {
      stage: 3,
      kicker: 'Stage 3',
      title: 'Contempt Filed',
      verified: s3Verified,
      date: rec.contempt_order_date || rec.contempt_filing_date || null,
      primary:
        [rec.contempt_court, rec.contempt_case_number].filter(Boolean).join(' · ') ||
        (s3Verified ? 'Contempt / habeas corpus proceedings initiated' : 'No separate contempt docket recorded'),
      secondary:
        rec.contempt_filing_date && rec.contempt_order_date
          ? `Filed ${rec.contempt_filing_date} · Order ${rec.contempt_order_date}`
          : rec.contempt_filing_date
            ? `Filed ${rec.contempt_filing_date}`
            : rec.contempt_order_date
              ? `Order ${rec.contempt_order_date}`
              : null,
      badge: rec.contempt_case_number || null,
      quote: rec.contempt_quote || null,
    },
    {
      stage: 4,
      kicker: 'Stage 4',
      title: 'Released & Directions',
      verified: s4Verified,
      date: rec.release_date || null,
      primary: s4Verified
        ? rec.release_date
          ? `Release verified (${rec.release_date})`
          : 'Release verified in official record'
        : 'Release status pending documentary confirmation',
      secondary: rec.judicial_direction_summary || null,
      badge: compBadge,
      quote: null,
    },
  ];
}

function p10ShortText(text?: string | null, maxLen = 135): string {
  const clean = (text || '').replace(/\s+/g, ' ').trim();
  if (!clean) return '—';
  return clean.length > maxLen ? `${clean.slice(0, maxLen - 1).trimEnd()}…` : clean;
}

export function InnocentButLockedUpVisualiser({
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
  const p10 = visualSummary?.innocent_but_locked_up;
  const workTag = getWorkTypeTag(undefined, mission.methodology_code);
  const slugPath = encodeURIComponent(mission.slug);

  const [selectedYear, setSelectedYear] = useState<'all' | number>('all');
  const [selectedState, setSelectedState] = useState('all');
  const [selectedPortal, setSelectedPortal] = useState<'all' | P10PortalCategoryKey>('all');
  const [selectedChain, setSelectedChain] = useState<'all' | P10ChainStatusKey>('all');
  const [selectedMechanism, setSelectedMechanism] = useState<'all' | P10OverrunMechanismKey>('all');
  const [selectedCourtTier, setSelectedCourtTier] = useState<'all' | P10CourtTierKey>('all');

  const [stateQuery, setStateQuery] = useState('');
  const [showAllStates, setShowAllStates] = useState(false);
  const [courtQuery, setCourtQuery] = useState('');
  const [showAllCourts, setShowAllCourts] = useState(false);

  const hook = p10?.hook?.trim() || P10_DEFAULT_HOOK;
  const totalTasks = p10?.total_tasks ?? mission.total_tasks ?? 0;
  const uniqueDocs = p10?.unique_documents_covered ?? 0;
  const statesCovered = p10?.states_and_uts_covered ?? p10?.states?.length ?? 0;
  const courtsCovered = p10?.courts_covered ?? p10?.top_courts?.length ?? 0;
  const prisonsNamed = p10?.prisons_named ?? p10?.top_prisons?.length ?? 0;
  const full4StageTasks = p10?.full_4_stage_tasks ?? 0;
  const contemptTasks = p10?.contempt_tasks ?? 0;
  const verifiedReleaseTasks = p10?.verified_release_tasks ?? 0;
  const clearedEmbedded = p10?.cleared_control_tasks_embedded ?? 0;
  const totalOverrunDays = p10?.total_overrun_days_documented ?? 0;
  const medianOverrunDays = p10?.median_overrun_days ?? null;
  const maxOverrunDays = p10?.max_overrun_days ?? null;
  const totalCompensationInr = p10?.total_compensation_ordered_inr ?? 0;

  const leads = p10?.leads ?? 0;
  const verified = p10?.verified ?? mission.verified_findings_count ?? 0;
  const inReview = p10?.peer_review ?? mission.peer_review_count ?? 0;
  const clearedTasks = p10?.cleared_tasks ?? mission.cleared_tasks_count ?? 0;

  const states = p10?.states ?? [];
  const topCourts = p10?.top_courts ?? [];
  const topPrisons = p10?.top_prisons ?? [];
  const topLeads = p10?.top_leads ?? [];
  const anchorCase = p10?.anchor_case ?? null;
  const documentsUrl = p10?.documents_url || `/api/missions/${slugPath}/documents.csv`;
  const coverageUrl = p10?.coverage_url || `/api/missions/${slugPath}/coverage.json`;

  const portals = useMemo(
    () => p10NormalizePortals(p10?.by_portal_category),
    [p10?.by_portal_category],
  );
  const years = useMemo(() => p10NormalizeYears(p10?.by_year), [p10?.by_year]);
  const courtTiers = useMemo(
    () => p10NormalizeCourtTiers(p10?.by_court_tier),
    [p10?.by_court_tier],
  );
  const mechanisms = useMemo(
    () => p10NormalizeMechanisms(p10?.by_mechanism, p10?.by_signal),
    [p10?.by_mechanism, p10?.by_signal],
  );
  const chainStatuses = useMemo(
    () => p10NormalizeChainStatuses(p10?.by_chain_status),
    [p10?.by_chain_status],
  );

  const filteredStates = useMemo(
    () => p10FilterStates(states, stateQuery),
    [states, stateQuery],
  );
  const visibleStates =
    showAllStates || stateQuery.trim().length > 0
      ? filteredStates
      : filteredStates.slice(0, P10_INITIAL_STATES);

  const filteredCourts = useMemo(
    () => p10FilterCourts(topCourts, courtQuery, selectedCourtTier, selectedState),
    [topCourts, courtQuery, selectedCourtTier, selectedState],
  );
  const visibleCourts =
    showAllCourts ||
    courtQuery.trim().length > 0 ||
    selectedCourtTier !== 'all' ||
    selectedState !== 'all'
      ? filteredCourts
      : filteredCourts.slice(0, P10_INITIAL_COURTS);

  const filteredLeads = useMemo(
    () =>
      p10FilterLeads(
        topLeads,
        selectedYear,
        selectedState,
        selectedPortal,
        selectedChain,
        selectedMechanism,
        selectedCourtTier,
      ),
    [
      topLeads,
      selectedYear,
      selectedState,
      selectedPortal,
      selectedChain,
      selectedMechanism,
      selectedCourtTier,
    ],
  );

  const hasActiveFilters =
    selectedYear !== 'all' ||
    selectedState !== 'all' ||
    selectedPortal !== 'all' ||
    selectedChain !== 'all' ||
    selectedMechanism !== 'all' ||
    selectedCourtTier !== 'all';

  const resetAllFilters = () => {
    setSelectedYear('all');
    setSelectedState('all');
    setSelectedPortal('all');
    setSelectedChain('all');
    setSelectedMechanism('all');
    setSelectedCourtTier('all');
  };

  const anchorStages = useMemo(
    () => (anchorCase ? p10BuildFourStageChain(anchorCase) : []),
    [anchorCase],
  );
  const anchorMechanism = anchorCase?.overrun_mechanism
    ? formatCustodyOverrunMechanism(anchorCase.overrun_mechanism)
    : null;
  const anchorPortal = anchorCase?.portal_category
    ? formatPortalCategory(anchorCase.portal_category)
    : null;
  const anchorTier = anchorCase?.court_tier ? formatCourtTier(anchorCase.court_tier) : null;
  const anchorChain = anchorCase
    ? formatChainStatus(anchorCase.chain_status, anchorCase.stages_verified_count)
    : null;
  const anchorPrimaryUrl = safeExternalUrl(anchorCase?.source_url);
  const anchorSecondaryUrl = safeExternalUrl(anchorCase?.secondary_url);
  const anchorPortalUrl = safeExternalUrl(anchorCase?.portal_url);

  const maxYearTasks = years.reduce((m, y) => Math.max(m, y.tasks), 0);

  return (
    <section
      className="card card-pad pv-results"
      aria-label="Innocent But Locked Up post-acquittal custody and contempt audit results"
    >
      {/* Header + Summary Pills */}
      <div className="pv-results-head">
        <div className="pv-results-title-wrap">
          <span className="kicker">
            <BarChart3 size={12} aria-hidden="true" />
            Live results · {workTag.label}
          </span>
          <h2 className="pv-results-title">
            Innocent But Locked Up: Post-Acquittal Custody &amp; Contempt Ledger
          </h2>
          <p className="pv-p6-hook">{hook}</p>
        </div>

        <div className="pv-summary-pills tabular">
          <span className="tag-mono">
            {fmtCount(totalTasks)} {totalTasks === 1 ? 'case task' : 'case tasks'}
          </span>
          {full4StageTasks > 0 ? (
            <span className="p10-chain p10-chain--full">
              {fmtCount(full4StageTasks)} 4-stage {full4StageTasks === 1 ? 'chain' : 'chains'}
            </span>
          ) : null}
          {contemptTasks > 0 ? (
            <span className="tag-mono">
              {fmtCount(contemptTasks)} contempt {contemptTasks === 1 ? 'record' : 'records'}
            </span>
          ) : null}
          {verifiedReleaseTasks > 0 ? (
            <span className="tag-mono">
              {fmtCount(verifiedReleaseTasks)} verified {verifiedReleaseTasks === 1 ? 'release' : 'releases'}
            </span>
          ) : null}
          {courtsCovered > 0 ? (
            <span className="tag-mono">
              {fmtCount(courtsCovered)} {courtsCovered === 1 ? 'court' : 'courts'}
            </span>
          ) : null}
          {statesCovered > 0 ? (
            <span className="tag-mono">
              {fmtCount(statesCovered)} {statesCovered === 1 ? 'State / UT' : 'States & UTs'}
            </span>
          ) : null}
          <span className="tag-mono">
            {fmtCount(leads)} {leads === 1 ? 'overrun lead' : 'overrun leads'}
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
          {clearedTasks > 0 ? (
            <span className={FINDING_TAGS.cleared.badgeClass}>
              {fmtCount(clearedTasks)} {FINDING_TAGS.cleared.label}
            </span>
          ) : null}
        </div>
      </div>

      {/* Headline KPIs */}
      <div className="pv-p10-overview">
        <div className="pv-p10-kpi-grid">
          <div className="pv-overview-tile pv-p10-kpi--tasks">
            <span className="pv-overview-kicker">Audited tasks (2021–2026)</span>
            <span className="pv-overview-num tabular">{fmtCount(totalTasks)}</span>
            <span className="pv-overview-sub tabular">
              {uniqueDocs > 0 ? `${fmtCount(uniqueDocs)} official ${uniqueDocs === 1 ? 'doc' : 'docs'} · ` : ''}
              {courtsCovered > 0 ? `${fmtCount(courtsCovered)} ${courtsCovered === 1 ? 'court' : 'courts'} · ` : ''}
              {statesCovered > 0
                ? `${fmtCount(statesCovered)} ${statesCovered === 1 ? 'State/UT' : 'States/UTs'}`
                : 'Supreme Court, High Courts & official portals'}
            </span>
          </div>

          <div className="pv-overview-tile pv-p10-kpi--chain">
            <span className="pv-overview-kicker">4-stage chains &amp; contempt</span>
            <span className="pv-overview-num tabular">{fmtCount(full4StageTasks)}</span>
            <span className="pv-overview-sub tabular">
              {fmtCount(contemptTasks)} contempt filed · {fmtCount(verifiedReleaseTasks)} verified{' '}
              {verifiedReleaseTasks === 1 ? 'release' : 'releases'}
              {prisonsNamed > 0
                ? ` · ${fmtCount(prisonsNamed)} ${prisonsNamed === 1 ? 'prison' : 'prisons'} named`
                : ''}
            </span>
          </div>

          <div className="pv-overview-tile pv-p10-kpi--overrun">
            <span className="pv-overview-kicker">Documented overrun days</span>
            <span className="pv-overview-num tabular">
              {totalOverrunDays > 0 ? fmtCount(totalOverrunDays) : 'Documentary'}
            </span>
            <span className="pv-overview-sub tabular">
              {medianOverrunDays != null && medianOverrunDays > 0
                ? `Median ${medianOverrunDays.toLocaleString('en-IN', { maximumFractionDigits: 1 })}d`
                : 'Verified dates across court orders'}
              {maxOverrunDays != null && maxOverrunDays > 0
                ? ` · Max ${fmtCount(maxOverrunDays)}d`
                : ''}
            </span>
          </div>

          <div className="pv-overview-tile pv-p10-kpi--outcomes">
            <span className="pv-overview-kicker">Leads, cleared &amp; compensation</span>
            <span className="pv-overview-num tabular">
              {totalCompensationInr > 0 ? formatInr(totalCompensationInr) : fmtCount(leads)}
            </span>
            <span className="pv-overview-sub tabular">
              {fmtCount(verified)} verified · {fmtCount(inReview)} in review · {fmtCount(clearedTasks)} cleared
              {clearedEmbedded > 0
                ? ` (${fmtCount(clearedEmbedded)} compliant ${clearedEmbedded === 1 ? 'control' : 'controls'})`
                : ''}
            </span>
          </div>
        </div>

        {/* Flagship 4-Stage Anchor Case Spotlight */}
        {anchorCase ? (
          <article
            className="pv-p10-anchor"
            aria-label="Flagship 4-stage post-acquittal custody overrun anchor case"
          >
            <div className="pv-p6-anchor-head">
              <div className="pv-p6-anchor-meta">
                <span className="p10-anchor-chip">Flagship Anchor · 4-Stage Custody Chain</span>
                {anchorTier ? <span className={anchorTier.chipClass}>{anchorTier.shortLabel}</span> : null}
                {anchorPortal ? (
                  <span className={anchorPortal.chipClass}>{anchorPortal.shortLabel}</span>
                ) : null}
                {anchorCase.state_ut || anchorCase.jurisdiction ? (
                  <span className="tag-mono">
                    {anchorCase.state_ut || formatJurisdictionLabel(anchorCase.jurisdiction)}
                  </span>
                ) : null}
                {anchorCase.year ? (
                  <span className="tag-mono tabular">{String(anchorCase.year)}</span>
                ) : null}
                {anchorChain ? (
                  <span className={anchorChain.chipClass}>{anchorChain.shortLabel}</span>
                ) : null}
              </div>
              {anchorMechanism ? (
                <span className={anchorMechanism.chipClass}>{anchorMechanism.label}</span>
              ) : null}
            </div>

            <h3 className="pv-p6-anchor-title">{anchorCase.case_title}</h3>

            {/* 4-Stage Visual Custody Timeline */}
            <ol
              className="p10-timeline"
              aria-label="4-stage custody overrun timeline from acquittal to release"
            >
              {anchorStages.map((step) => (
                <li
                  key={step.stage}
                  className={`p10-stage p10-stage--s${step.stage}${
                    step.verified ? ' p10-stage--verified' : ' p10-stage--unrecorded'
                  }`}
                >
                  <div className="p10-stage-head">
                    <span className="p10-stage-num font-mono" aria-hidden="true">
                      {step.stage}
                    </span>
                    <span className="p10-stage-title">{step.title}</span>
                  </div>
                  {step.badge ? (
                    <span className={`p10-stage-badge p10-stage-badge--s${step.stage} tabular`}>
                      {step.badge}
                    </span>
                  ) : null}
                  <p className="p10-stage-primary">{step.primary}</p>
                  {step.secondary ? (
                    <p className="p10-stage-sub">{p10ShortText(step.secondary, 130)}</p>
                  ) : null}
                  {step.quote ? (
                    <blockquote className="p10-stage-quote">
                      &ldquo;{p10ShortText(step.quote, 150)}&rdquo;
                    </blockquote>
                  ) : null}
                </li>
              ))}
            </ol>

            <div className="pv-p6-anchor-actions">
              {anchorCase.finding_id && onSelectFinding ? (
                <button
                  type="button"
                  className="btn btn-secondary btn-xs"
                  onClick={() => onSelectFinding(anchorCase.finding_id!)}
                >
                  Inspect anchor dossier
                </button>
              ) : onFilterTasks && anchorCase.task_id ? (
                <button
                  type="button"
                  className="btn btn-secondary btn-xs"
                  onClick={() => onFilterTasks(anchorCase.task_id!)}
                >
                  Find anchor task in queue
                </button>
              ) : null}
              {anchorPrimaryUrl ? (
                <a
                  className="inline-link"
                  href={anchorPrimaryUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Primary official order <ExternalLink size={11} aria-hidden="true" className="pv-ub-ext" />
                </a>
              ) : null}
              {anchorSecondaryUrl ? (
                <a
                  className="inline-link"
                  href={anchorSecondaryUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Contempt / release record{' '}
                  <ExternalLink size={11} aria-hidden="true" className="pv-ub-ext" />
                </a>
              ) : null}
              {anchorPortalUrl &&
              anchorPortalUrl !== anchorPrimaryUrl &&
              anchorPortalUrl !== anchorSecondaryUrl ? (
                <a
                  className="inline-link"
                  href={anchorPortalUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Official portal <ExternalLink size={11} aria-hidden="true" className="pv-ub-ext" />
                </a>
              ) : null}
              {anchorCase.source_sha256 ? (
                <span className="tag-mono font-mono" style={{ fontSize: 11 }}>
                  sha256:{anchorCase.source_sha256.slice(0, 10)}
                </span>
              ) : null}
            </div>
          </article>
        ) : (
          <div className="pv-empty-box">
            Flagship 4-stage post-acquittal custody anchor dossier loads automatically from the hub summary index.
          </div>
        )}

        {/* Interactive Source Portal Category Breakdown (8 Official Portals) */}
        {portals.totalTasks > 0 || portals.totalLeads > 0 ? (
          <div className="pv-p7-cohort-panel">
            <div className="pv-p7-cohort-head">
              <h3>Source portal categories (select any portal to filter explorer)</h3>
              <div className="pv-mini-legend">
                {(() => {
                  const activePortalsCount = portals.items.filter(
                    (p) => p.tasks > 0 || p.leads > 0,
                  ).length;
                  return (
                    <span className="pv-chart-note tabular">
                      {fmtCount(activePortalsCount)} active official portal{' '}
                      {activePortalsCount === 1 ? 'category' : 'categories'}
                    </span>
                  );
                })()}
                {selectedPortal !== 'all' ? (
                  <button
                    type="button"
                    className="btn btn-ghost btn-xs"
                    onClick={() => setSelectedPortal('all')}
                  >
                    <RotateCcw size={11} aria-hidden="true" />
                    <span>All portals</span>
                  </button>
                ) : null}
              </div>
            </div>

            <div
              className="pv-p10-portal-bar"
              role="img"
              aria-label={`Portal distribution across ${fmtCount(portals.totalTasks)} tasks`}
            >
              {portals.items
                .filter((p) => p.sharePct > 0)
                .map((p) => (
                  <span
                    key={p.portal_category}
                    className={`pv-p10-seg ${p.segClass}`}
                    style={{ width: `${p.sharePct}%` }}
                    title={`${p.label}: ${fmtCount(p.tasks)} (${fmtPct(p.sharePct)})`}
                  />
                ))}
            </div>

            <div className="pv-p10-portal-grid" role="group" aria-label="Filter by official source portal category">
              {portals.items.map((p) => {
                const isSelected = selectedPortal === p.portal_category;
                return (
                  <button
                    key={p.portal_category}
                    type="button"
                    aria-pressed={isSelected}
                    className={`pv-p10-portal-btn${isSelected ? ' pv-p10-portal-btn--active' : ''}`}
                    onClick={() =>
                      setSelectedPortal((prev) =>
                        prev === p.portal_category ? 'all' : p.portal_category,
                      )
                    }
                  >
                    <span className="pv-p7-cohort-btn-top">
                      <span className={p.chipClass}>{p.shortLabel}</span>
                      <span className="pv-p7-cohort-share tabular">{fmtPct(p.sharePct)}</span>
                    </span>
                    <span className="pv-p10-portal-num tabular">
                      {fmtCount(p.tasks)} {p.tasks === 1 ? 'task' : 'tasks'}
                    </span>
                    <span className="pv-p7-cohort-sub tabular">
                      {fmtCount(p.done)} checked
                      {p.leads > 0 ? ` · ${fmtCount(p.leads)} ${p.leads === 1 ? 'lead' : 'leads'}` : ''}
                      {p.cleared > 0 ? ` · ${fmtCount(p.cleared)} cleared` : ''}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        ) : null}

        {/* Interactive 5-Year Timeline (2021–2026) + 4-Stage Chain & Mechanisms Filter Deck */}
        <div className="pv-p7-cohort-panel">
          {years.length > 0 ? (
            <div className="pv-p10-year-block">
              <div className="pv-p7-cohort-head">
                <h3>5-year audit window (2021–2026 — select a year to filter)</h3>
                {selectedYear !== 'all' ? (
                  <button
                    type="button"
                    className="btn btn-ghost btn-xs"
                    onClick={() => setSelectedYear('all')}
                  >
                    <RotateCcw size={11} aria-hidden="true" />
                    <span>All years</span>
                  </button>
                ) : (
                  <span className="pv-chart-note">Official orders, releases &amp; audit disclosures</span>
                )}
              </div>

              <div className="pv-p10-year-grid" role="group" aria-label="Filter by year 2021 to 2026">
                {years.map((y) => {
                  const isSelected = selectedYear === y.year;
                  const relPct = maxYearTasks > 0 ? calcPct(y.tasks, maxYearTasks) : 0;
                  return (
                    <button
                      key={y.year}
                      type="button"
                      aria-pressed={isSelected}
                      className={`pv-p10-year-btn${isSelected ? ' pv-p10-year-btn--active' : ''}`}
                      onClick={() => setSelectedYear((prev) => (prev === y.year ? 'all' : y.year))}
                    >
                      <span className="pv-p10-year-top">
                        <strong className="font-mono tabular">{y.year}</strong>
                        <span className="pv-p7-cohort-share tabular">
                          {fmtCount(y.tasks)}
                        </span>
                      </span>
                      <span className="pv-p7-signal-track" aria-hidden="true">
                        <span
                          className="pv-p7-signal-fill pv-p10-year-fill"
                          style={{ width: `${relPct > 0 ? Math.max(4, relPct) : 0}%` }}
                        />
                      </span>
                      <span className="pv-p7-cohort-sub tabular">
                        {y.full_4_stage > 0 ? `${fmtCount(y.full_4_stage)} 4-stage` : `${fmtCount(y.done)} checked`}
                        {y.overrun_days > 0 ? ` · ${fmtCount(y.overrun_days)}d` : ''}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          ) : null}

          <div className="pv-p7-cohort-head" style={{ marginTop: years.length > 0 ? 6 : 0 }}>
            <h3>Custody overrun mechanisms (6 deterministic categories — select to filter)</h3>
            <span className="pv-chart-note tabular">
              {mechanisms.totalTasks > 0
                ? `${fmtCount(mechanisms.totalTasks)} tasks across 5 overrun patterns + timely-release benchmarks`
                : 'Counts populate from the hub summary index'}
            </span>
          </div>

          <div className="pv-p10-mech-grid" role="group" aria-label="Filter by custody overrun mechanism">
            {mechanisms.items.map((m) => {
              const isSelected = selectedMechanism === m.key;
              return (
                <button
                  key={m.key}
                  type="button"
                  aria-pressed={isSelected}
                  className={`pv-p8-mech-btn${m.isCleared ? ' pv-p8-mech-btn--cleared' : ''}`}
                  onClick={() =>
                    setSelectedMechanism((prev) => (prev === m.key ? 'all' : m.key))
                  }
                >
                  <div className="pv-p7-signal-top">
                    <span className={m.chipClass}>{m.label}</span>
                    <span className="pv-p7-signal-count tabular">
                      <strong>{fmtCount(m.tasks)}</strong> {m.tasks === 1 ? 'task' : 'tasks'}
                      {m.leads > 0
                        ? ` · ${fmtCount(m.leads)} ${m.leads === 1 ? 'lead' : 'leads'}`
                        : ''}
                    </span>
                  </div>
                  <span className="pv-p7-signal-track" aria-hidden="true">
                    <span
                      className={`pv-p7-signal-fill ${m.fillClass}`}
                      style={{ width: `${m.sharePct > 0 ? Math.max(3, m.sharePct) : 0}%` }}
                    />
                  </span>
                </button>
              );
            })}
          </div>

          {/* 4-Stage Chain Completeness Filter Pills */}
          {chainStatuses.total > 0 ? (
            <div className="pv-p8-cat-pills" role="group" aria-label="Filter by 4-stage chain completeness">
              <span className="pv-p7-jur-label">4-stage chain completeness:</span>
              <button
                type="button"
                aria-pressed={selectedChain === 'all'}
                className={`btn btn-xs ${selectedChain === 'all' ? 'btn-primary' : 'btn-ghost'}`}
                onClick={() => setSelectedChain('all')}
              >
                All ({fmtCount(chainStatuses.total)})
              </button>
              {chainStatuses.items
                .filter((cs) => cs.count > 0)
                .map((cs) => {
                  const active = selectedChain === cs.key;
                  return (
                    <button
                      key={cs.key}
                      type="button"
                      aria-pressed={active}
                      className={`btn btn-xs ${active ? 'btn-primary' : 'btn-ghost'}`}
                      onClick={() =>
                        setSelectedChain((prev) => (prev === cs.key ? 'all' : cs.key))
                      }
                    >
                      {cs.shortLabel} ({fmtCount(cs.count)})
                    </button>
                  );
                })}
            </div>
          ) : null}

          {hasActiveFilters ? (
            <div className="pv-p8-active-filters">
              <span>
                Active filters:{' '}
                {[
                  selectedYear !== 'all' ? String(selectedYear) : null,
                  selectedState !== 'all' ? selectedState : null,
                  selectedPortal !== 'all' ? p10PortalLabel(selectedPortal) : null,
                  selectedChain !== 'all' ? p10ChainStatusLabel(selectedChain) : null,
                  selectedMechanism !== 'all' ? p10MechanismLabel(selectedMechanism) : null,
                  selectedCourtTier !== 'all' ? p10CourtTierLabel(selectedCourtTier) : null,
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </span>
              <button type="button" className="btn btn-ghost btn-xs" onClick={resetAllFilters}>
                <RotateCcw size={11} aria-hidden="true" />
                <span>Reset all filters</span>
              </button>
            </div>
          ) : null}
        </div>
      </div>

      {/* Two-Column Split: States & UTs Breakdown + How to Read This */}
      <div className="pv-results-split">
        <div className="pv-chart-card">
          <div className="pv-chart-subhead">
            <h3>States &amp; UTs covered ({fmtCount(states.length)})</h3>
            <span className="pv-chart-note">
              Click a State/UT to filter courts &amp; case dossiers below
            </span>
          </div>

          {states.length > 0 ? (
            <>
              <div className="pv-p6-ministry-controls">
                <div className="pv-p6-search-wrap">
                  <Search size={13} aria-hidden="true" className="pv-p6-search-icon" />
                  <input
                    type="search"
                    className="input-field pv-p6-search"
                    value={stateQuery}
                    onChange={(e) => setStateQuery(e.target.value)}
                    placeholder={`Filter ${fmtCount(states.length)} States & UTs (e.g. Uttar Pradesh, Delhi, Maharashtra)…`}
                    aria-label="Filter States and Union Territories"
                  />
                </div>
                {selectedState !== 'all' ? (
                  <button
                    type="button"
                    className="btn btn-ghost btn-xs"
                    onClick={() => setSelectedState('all')}
                  >
                    <RotateCcw size={11} aria-hidden="true" />
                    <span>All States/UTs</span>
                  </button>
                ) : null}
              </div>

              {visibleStates.length > 0 ? (
                <div className="pv-ub-years-wrap">
                  <table className="pv-ub-table pv-ub-years pv-p6-ministries-table">
                    <caption className="pv-ub-caption">
                      Post-acquittal custody overrun and contempt compliance coverage across Indian States and Union Territories.
                    </caption>
                    <thead>
                      <tr>
                        <th scope="col">State / UT</th>
                        <th scope="col">Tasks &amp; progress</th>
                        <th scope="col" className="num">
                          Overruns / Leads
                        </th>
                        <th scope="col" className="num">
                          Queue
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {visibleStates.map((st) => {
                        const display = st.state_ut || formatJurisdictionLabel(st.jurisdiction);
                        const stateKey = st.state_ut || st.jurisdiction || 'Union of India';
                        const total = Math.max(0, st.tasks);
                        const done = Math.max(0, st.done ?? 0);
                        const donePct = calcPct(done, total);
                        const isSelected = selectedState === stateKey;
                        const queueToken = p10StateFilterQuery(st.state_ut, st.jurisdiction);

                        return (
                          <tr
                            key={stateKey}
                            className="pv-p6-ministry-row--interactive"
                            style={isSelected ? { background: 'var(--sunk)' } : undefined}
                            onClick={(e) => {
                              const target = e.target as HTMLElement | null;
                              if (target?.closest('a, button')) return;
                              setSelectedState((prev) => (prev === stateKey ? 'all' : stateKey));
                            }}
                          >
                            <th scope="row" className="pv-ub-year pv-p8-jur-name">
                              <button
                                type="button"
                                className="pv-ub-link"
                                aria-pressed={isSelected}
                                onClick={() =>
                                  setSelectedState((prev) => (prev === stateKey ? 'all' : stateKey))
                                }
                              >
                                {display}
                              </button>
                              {(st.full_4_stage ?? 0) > 0 ? (
                                <span className="pv-ub-sub tabular">
                                  {fmtCount(st.full_4_stage)} 4-stage{' '}
                                  {st.full_4_stage === 1 ? 'case' : 'cases'}
                                </span>
                              ) : null}
                            </th>
                            <td className="pv-ub-years-checked">
                              <span
                                className="pv-ub-bar"
                                role="img"
                                aria-label={`${display}: ${fmtCount(done)} of ${fmtCount(total)} tasks checked`}
                              >
                                {donePct > 0 ? (
                                  <span
                                    className="pv-ub-bar-seg pv-p10-seg--court"
                                    style={{ width: `${Math.max(donePct, 1.5)}%` }}
                                  />
                                ) : null}
                              </span>
                              <span className="pv-ub-sub tabular">
                                {fmtCount(total)} {total === 1 ? 'task' : 'tasks'} · {fmtCount(done)} checked ({fmtPct(donePct)})
                              </span>
                            </td>
                            <td className="num pv-ub-years-lines" data-label="Overruns / Leads">
                              <span className="pv-ub-cell-val">
                                {(st.total_overrun_days ?? 0) > 0
                                  ? `+${fmtCount(st.total_overrun_days)}d`
                                  : `${fmtCount(st.leads)} ${st.leads === 1 ? 'lead' : 'leads'}`}
                              </span>
                              <span className="pv-ub-sub">
                                {(st.compensation_inr ?? 0) > 0
                                  ? formatInr(st.compensation_inr)
                                  : (st.cleared ?? 0) > 0
                                    ? `${fmtCount(st.cleared)} cleared`
                                    : `${fmtCount(st.leads)} ${st.leads === 1 ? 'lead' : 'leads'}`}
                              </span>
                            </td>
                            <td className="num pv-ub-years-below" data-label="Queue">
                              {onFilterTasks ? (
                                <button
                                  type="button"
                                  className="pv-ub-link"
                                  onClick={() => onFilterTasks(queueToken)}
                                  aria-label={`Filter task queue for ${display}`}
                                >
                                  Tasks →
                                </button>
                              ) : (
                                <span className="pv-ub-sub">{fmtCount(total)}</span>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="pv-empty-box">
                  No States or UTs match &ldquo;{stateQuery}&rdquo;.
                </div>
              )}

              {stateQuery.trim().length === 0 && filteredStates.length > P10_INITIAL_STATES ? (
                <div className="pv-list-more">
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    onClick={() => setShowAllStates((v) => !v)}
                  >
                    {showAllStates
                      ? `Show top ${P10_INITIAL_STATES} States & UTs`
                      : `Show all ${fmtCount(filteredStates.length)} States & UTs`}
                  </button>
                </div>
              ) : null}
            </>
          ) : (
            <div className="pv-empty-box">
              State and UT custody coverage appears here once the hub loads the P10 summary index.
            </div>
          )}
        </div>

        <div className="pv-chart-card pv-ub-guide">
          <div className="pv-chart-subhead">
            <h3>How to read this</h3>
          </div>
          <p>
            Every task fetches and SHA-256-verifies official court orders, prison release records,
            police circulars, NHRC/SHRC proceedings, NALSA/SLSA release reports, Digital Sansad
            answers, or CAG audit chapters from <strong>2021–2026</strong>. If a pinned file fails
            byte-digest verification or lacks required verbatim anchors, the check fails closed as{' '}
            {FINDING_TAGS.not_enough_data.label}.
          </p>
          <p>
            Full 4-stage cases document all four verifiable milestones in official primary records:
            <strong> [1] Acquitted / Release Order</strong> → <strong>[2] Held in Prison</strong> →{' '}
            <strong>[3] Contempt Filed</strong> → <strong>[4] Released</strong>. Timely-release
            and FASTER compliance benchmarks without custody overrun are published as{' '}
            {FINDING_TAGS.cleared.label} with equal standing.
          </p>
          <p>
            Every dossier uses strictly factual institutional audit framing under{' '}
            <strong>BNS Sec 356</strong>, scrubs personal identifiers under the{' '}
            <strong>DPDP Act 2023</strong>, records lawful procedural explanations (pending
            warrant verification, other active custody cases, or clerical docket transit), and
            requires three pairwise-independent peer verifications before reaching{' '}
            {FINDING_TAGS.verified.label}.
          </p>
          <div className="pv-ub-links">
            <a className="inline-link" href={documentsUrl}>
              Official sources (CSV)
            </a>
            <a className="inline-link" href={coverageUrl}>
              Coverage (JSON)
            </a>
            <a className="inline-link" href={P10_METHOD_URL} target="_blank" rel="noopener noreferrer">
              Method <ExternalLink size={11} aria-hidden="true" className="pv-ub-ext" />
            </a>
          </div>
          {p10?.generated_on ? (
            <p className="pv-chart-note">
              P10 custody audit index built {p10.generated_on}
              {p10.as_of_date && p10.as_of_date !== p10.generated_on
                ? ` · as of ${p10.as_of_date}`
                : ''}
              .
            </p>
          ) : null}
        </div>
      </div>

      {/* Interactive Courts & Named Prisons Explorer */}
      <div className="pv-chart-card pv-p7-section-card">
        <div className="pv-chart-subhead">
          <h3>
            Courts &amp; statutory bodies covered ({fmtCount(filteredCourts.length)}
            {filteredCourts.length !== topCourts.length
              ? ` of ${fmtCount(topCourts.length)}`
              : ''}
            )
          </h3>
          <span className="pv-chart-note">
            Filter by court tier or State/UT — click any court to view its tasks in the queue
          </span>
        </div>

        {topCourts.length > 0 ? (
          <>
            <div className="pv-p6-ministry-controls">
              <div className="pv-p6-search-wrap">
                <Search size={13} aria-hidden="true" className="pv-p6-search-icon" />
                <input
                  type="search"
                  className="input-field pv-p6-search"
                  value={courtQuery}
                  onChange={(e) => setCourtQuery(e.target.value)}
                  placeholder={`Search ${fmtCount(topCourts.length)} courts & bodies (e.g. Supreme Court, Allahabad, Delhi, NHRC)…`}
                  aria-label="Search courts and statutory bodies"
                />
              </div>
              <div className="pv-p6-filter-pills" role="group" aria-label="Filter courts by tier">
                <button
                  type="button"
                  aria-pressed={selectedCourtTier === 'all'}
                  className={`btn btn-xs ${selectedCourtTier === 'all' ? 'btn-primary' : 'btn-ghost'}`}
                  onClick={() => setSelectedCourtTier('all')}
                >
                  All
                </button>
                {courtTiers.items
                  .filter((t) => t.tasks > 0 || topCourts.some((c) => c.court_tier === t.key))
                  .map((t) => (
                    <button
                      key={t.key}
                      type="button"
                      aria-pressed={selectedCourtTier === t.key}
                      className={`btn btn-xs ${selectedCourtTier === t.key ? 'btn-primary' : 'btn-ghost'}`}
                      onClick={() =>
                        setSelectedCourtTier((prev) => (prev === t.key ? 'all' : t.key))
                      }
                    >
                      {t.shortLabel}
                      {t.tasks > 0 ? ` (${fmtCount(t.tasks)})` : ''}
                    </button>
                  ))}
              </div>
            </div>

            {visibleCourts.length > 0 ? (
              <div className="table-wrap pv-ub-leads-wrap">
                <table className="ledger-table pv-ub-table pv-ub-leads-table">
                  <caption className="pv-ub-caption">
                    Indian courts and statutory bodies indexed in Project P10 (2021–2026).
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">Court / Statutory Body</th>
                      <th scope="col">Tier</th>
                      <th scope="col">State / UT</th>
                      <th scope="col" className="num">
                        4-Stage Chains
                      </th>
                      <th scope="col" className="num">
                        Tasks / Leads
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleCourts.map((c) => {
                      const tier = formatCourtTier(c.court_tier);
                      return (
                        <tr key={`${c.court_name}-${c.state_ut || ''}`}>
                          <td className="pv-ub-line pv-p8-scheme-col">
                            {onFilterTasks ? (
                              <button
                                type="button"
                                className="pv-ub-link"
                                onClick={() => onFilterTasks(c.court_name)}
                              >
                                {c.court_name}
                              </button>
                            ) : (
                              <span>{c.court_name}</span>
                            )}
                          </td>
                          <td data-label="Tier">
                            <span className={tier.chipClass}>{tier.shortLabel}</span>
                          </td>
                          <td data-label="State / UT">{c.state_ut || 'All-India / Union'}</td>
                          <td className="num tabular" data-label="4-Stage Chains">
                            {fmtCount(c.full_4_stage ?? 0)}
                          </td>
                          <td className="num tabular" data-label="Tasks / Leads">
                            <span className="pv-ub-cell-val">
                              {fmtCount(c.tasks)} {c.tasks === 1 ? 'task' : 'tasks'}
                            </span>
                            <span className="pv-ub-sub">
                              {fmtCount(c.leads)} {c.leads === 1 ? 'lead' : 'leads'}
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
                No courts or statutory bodies match the current filter combination.
              </div>
            )}

            {courtQuery.trim().length === 0 &&
            selectedCourtTier === 'all' &&
            selectedState === 'all' &&
            filteredCourts.length > P10_INITIAL_COURTS ? (
              <div className="pv-list-more">
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => setShowAllCourts((v) => !v)}
                >
                  {showAllCourts
                    ? `Show top ${P10_INITIAL_COURTS} courts`
                    : `Show all ${fmtCount(filteredCourts.length)} courts`}
                </button>
              </div>
            ) : null}
          </>
        ) : (
          <div className="pv-empty-box">
            Court-by-court coverage appears here once the hub loads the P10 corpus index.
          </div>
        )}

        {/* Named Prisons / Custody Facilities Strip */}
        {topPrisons.length > 0 ? (
          <div className="pv-p10-prisons-strip" aria-label="Named prisons and custody facilities">
            <span className="pv-p7-jur-label">Named prisons &amp; custody facilities:</span>
            {topPrisons.slice(0, P10_INITIAL_PRISONS).map((pr) => (
              <button
                key={`${pr.prison_name}-${pr.state_ut || ''}`}
                type="button"
                className="btn btn-ghost btn-xs pv-p10-prison-pill"
                onClick={() => onFilterTasks?.(pr.prison_name)}
                disabled={!onFilterTasks}
                title={
                  pr.state_ut
                    ? `${pr.prison_name} (${pr.state_ut}) — click to filter tasks`
                    : `${pr.prison_name} — click to filter tasks`
                }
              >
                <span>{pr.prison_name}</span>
                {pr.state_ut ? <span className="pv-p10-prison-state">{pr.state_ut}</span> : null}
                <span className="tabular font-mono">
                  {fmtCount(pr.tasks)}
                  {(pr.total_overrun_days ?? 0) > 0 ? ` · +${fmtCount(pr.total_overrun_days)}d` : ''}
                </span>
              </button>
            ))}
          </div>
        ) : null}
      </div>

      {/* Reported Custody-Overrun Case Dossiers Table */}
      <div className="pv-ub-leads">
        <div className="pv-chart-subhead">
          <h3>
            Custody-overrun &amp; contempt case dossiers reported so far
            {hasActiveFilters && topLeads.length > 0
              ? ` (${fmtCount(filteredLeads.length)} matching filters)`
              : ''}
          </h3>
          {filteredLeads.length > 0 ? (
            <span className="pv-chart-note">
              4-stage documentary chain: Acquitted → Held in Prison → Contempt Filed → Released
            </span>
          ) : null}
        </div>
        {filteredLeads.length > 0 ? (
          <div className="table-wrap pv-ub-leads-wrap">
            <table className="ledger-table pv-ub-table pv-ub-leads-table">
              <caption className="pv-ub-caption">
                Verified and in-review post-acquittal custody overrun and contempt compliance dossiers.
                {onSelectFinding
                  ? ' Open any row to read the full 4-stage custody timeline, official citations, SHA-256 hashes, and RTI draft.'
                  : ''}
              </caption>
              <thead>
                <tr>
                  <th scope="col">Case &amp; Court / Authority</th>
                  <th scope="col">Year, State &amp; Portal</th>
                  <th scope="col">4-Stage Custody Chain</th>
                  <th scope="col">Mechanism &amp; Outcome</th>
                  <th scope="col">Audit Status</th>
                </tr>
              </thead>
              <tbody>
                {filteredLeads.map((lead) => {
                  const tag = getFindingTag(lead.status);
                  const mech = formatCustodyOverrunMechanism(
                    lead.overrun_mechanism || lead.signal_category,
                  );
                  const portal = formatPortalCategory(lead.portal_category);
                  const chain = formatChainStatus(lead.chain_status, lead.stages_verified_count);
                  const stages = p10BuildFourStageChain(lead);
                  const overrunText = formatOverrunDays(lead.overrun_days);

                  return (
                    <tr key={lead.finding_id}>
                      <td className="pv-ub-line pv-p8-lead-scheme">
                        {onSelectFinding ? (
                          <button
                            type="button"
                            className="pv-ub-link"
                            onClick={() => onSelectFinding(lead.finding_id)}
                          >
                            {lead.case_title || lead.title}
                          </button>
                        ) : (
                          <span>{lead.case_title || lead.title}</span>
                        )}
                        <span className="pv-ub-sub">
                          {lead.is_anchor_case ? (
                            <>
                              <span
                                className="p10-anchor-chip"
                                style={{ fontSize: 10.5, padding: '0 6px' }}
                              >
                                Anchor
                              </span>{' '}
                            </>
                          ) : null}
                          {[lead.acquittal_court || lead.entity_name, lead.prison_name]
                            .filter(Boolean)
                            .join(' · ')}
                        </span>
                      </td>
                      <td className="tabular pv-ub-lead-year" data-label="Year, State & portal">
                        <span>
                          {[
                            lead.year ? String(lead.year) : null,
                            lead.state_ut ||
                              (lead.jurisdiction ? formatJurisdictionLabel(lead.jurisdiction) : null),
                          ]
                            .filter(Boolean)
                            .join(' · ') || '—'}
                        </span>
                        <span className="pv-ub-sub">
                          <span
                            className={portal.chipClass}
                            style={{ fontSize: 10.5, padding: '0 6px' }}
                          >
                            {portal.shortLabel}
                          </span>
                        </span>
                      </td>
                      <td className="pv-p10-lead-chain" data-label="4-Stage custody chain">
                        <div className="p10-mini-chain" aria-label={chain.label}>
                          {stages.map((st) => (
                            <span
                              key={st.stage}
                              className={`p10-mini-node p10-mini-node--s${st.stage}${
                                st.verified ? ' is-done' : ''
                              }`}
                              title={`${st.kicker}: ${st.title}${st.date ? ` (${st.date})` : ''}`}
                            >
                              {st.stage}
                            </span>
                          ))}
                          <span className={chain.chipClass} style={{ fontSize: 10.5, padding: '0 6px' }}>
                            {chain.shortLabel}
                          </span>
                        </div>
                        <span className="pv-ub-sub tabular">
                          {[
                            lead.acquittal_order_date ? `Acq ${lead.acquittal_order_date}` : null,
                            overrunText && lead.overrun_days != null && lead.overrun_days > 0
                              ? `+${overrunText}`
                              : null,
                            lead.release_date ? `Rel ${lead.release_date}` : null,
                          ]
                            .filter(Boolean)
                            .join(' · ') || 'Documented in official record'}
                        </span>
                      </td>
                      <td data-label="Mechanism & outcome">
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                          <span className={mech.chipClass}>{mech.shortLabel}</span>
                          {lead.compensation_inr != null && lead.compensation_inr > 0 ? (
                            <span className="tag-mono tabular">
                              {formatInr(lead.compensation_inr)}
                            </span>
                          ) : null}
                        </div>
                        {lead.judicial_direction_summary || lead.contempt_case_number ? (
                          <span className="pv-ub-sub">
                            {p10ShortText(
                              lead.judicial_direction_summary || lead.contempt_case_number,
                              95,
                            )}
                          </span>
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
        ) : (
          <div className="pv-empty-box">
            {hasActiveFilters && topLeads.length > 0
              ? 'No reported case dossiers match the active explorer filters. Reset filters above to view all dossiers.'
              : 'Post-acquittal custody overrun and contempt dossiers appear here as contributor agents verify tasks. Every record cites official court/portal documents, SHA-256 digests, verbatim excerpts, and lawful procedural context.'}
          </div>
        )}
      </div>
    </section>
  );
}
