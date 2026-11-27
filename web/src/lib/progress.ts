export const TASK_PROGRESS_WEIGHT = 70;
export const REVIEW_PROGRESS_WEIGHT = 30;
export const DEFAULT_REQUIRED_REVIEWS = 3;

export interface ProjectProgressInput {
  completed: number;
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
}

export interface ProjectProgressResult {
  progressPct: number;
  pctComplete: number;
  taskPctComplete: number;
  taskProgressPct: number;
  reviewProgressPct: number;
  reviewRemainingPct: number;
  taskWeightPct: number;
  reviewWeightPct: number;
  requiredReviews: number;
  perReviewPct: number;
  reviewsRequired: number;
  reviewsDone: number;
  reviewsRemaining: number;
  hasReviewPhase: boolean;
  hasReviewsRemaining: boolean;
  hasIncompleteWork: boolean;
}

function roundTo(value: number, decimals: number): number {
  const factor = 10 ** decimals;
  return Math.round((value + Number.EPSILON) * factor) / factor;
}

/**
 * Compute weighted project completion:
 * - Primary tasks carry 70% weight; independent peer reviews carry 30% weight
 *   (30 / N percent per review: e.g. 10% each for 3 reviews, 6% each for 5 reviews).
 * - Never reaches 100.0% while any task or independent review remains.
 * - Projects with zero review-bearing findings and zero remaining reviews (e.g. website checks)
 *   map primary task completion smoothly from 0% to 100%.
 */
export function computeProjectProgress(input: ProjectProgressInput): ProjectProgressResult {
  const safeTotal = Math.max(0, Math.floor(Number.isFinite(input.total) ? input.total : 0));
  const safeCompleted = Math.max(
    0,
    Math.min(safeTotal, Math.floor(Number.isFinite(input.completed) ? input.completed : 0)),
  );
  const effRequiredReviews = Math.max(
    1,
    Math.floor(
      input.requiredReviews != null && Number.isFinite(input.requiredReviews) && input.requiredReviews > 0
        ? input.requiredReviews
        : DEFAULT_REQUIRED_REVIEWS,
    ),
  );
  const perReviewPct = roundTo(REVIEW_PROGRESS_WEIGHT / effRequiredReviews, 2);

  if (safeTotal <= 0) {
    return {
      progressPct: 0,
      pctComplete: 0,
      taskPctComplete: 0,
      taskProgressPct: 0,
      reviewProgressPct: 0,
      reviewRemainingPct: 0,
      taskWeightPct: TASK_PROGRESS_WEIGHT,
      reviewWeightPct: REVIEW_PROGRESS_WEIGHT,
      requiredReviews: effRequiredReviews,
      perReviewPct,
      reviewsRequired: 0,
      reviewsDone: 0,
      reviewsRemaining: 0,
      hasReviewPhase: false,
      hasReviewsRemaining: false,
      hasIncompleteWork: false,
    };
  }

  const taskRatio = safeCompleted / safeTotal;
  const rawTaskOnlyPct = taskRatio * 100;

  const inReviewCnt = Math.max(0, Math.floor(input.peerReview ?? 0));
  const verifiedCnt = Math.max(0, Math.floor(input.verified ?? 0));
  const openPv = Math.max(0, Math.floor(input.peerChecksOpen ?? 0));
  const totalPv = Math.max(0, Math.floor(input.peerChecksTotal ?? 0));
  const donePv = Math.max(0, Math.floor(input.peerChecksDone ?? 0));
  const explicitRemaining = Math.max(0, Math.floor(input.reviewsRemaining ?? 0));

  let effSlotsRequired = Math.max(0, Math.floor(input.reviewsRequired ?? 0));
  let effSlotsDone = Math.max(
    0,
    Math.floor(
      input.reviewsDone != null
        ? input.reviewsDone
        : effSlotsRequired > 0 && input.reviewsRemaining != null
          ? Math.max(0, effSlotsRequired - explicitRemaining)
          : 0,
    ),
  );

  if (effSlotsRequired === 0 && totalPv > 0) {
    effSlotsRequired = totalPv;
    effSlotsDone = Math.min(effSlotsRequired, donePv);
    if (openPv > 0 && effSlotsDone >= effSlotsRequired) {
      effSlotsDone = Math.max(0, effSlotsRequired - 1);
    }
  } else if (effSlotsRequired === 0 && inReviewCnt > 0) {
    effSlotsRequired = (verifiedCnt + inReviewCnt) * effRequiredReviews;
    effSlotsDone = verifiedCnt * effRequiredReviews;
  }

  effSlotsDone = Math.max(0, Math.min(effSlotsDone, effSlotsRequired));
  let effSlotsRemaining = Math.max(0, effSlotsRequired - effSlotsDone);

  const hasReviewsRemaining =
    effSlotsRemaining > 0 || inReviewCnt > 0 || openPv > 0 || explicitRemaining > 0;
  if (hasReviewsRemaining && effSlotsRemaining === 0 && effSlotsRequired > 0) {
    effSlotsDone = Math.max(0, effSlotsRequired - 1);
    effSlotsRemaining = 1;
  }

  const hasReviewPhase = effSlotsRequired > 0 || hasReviewsRemaining;
  const reviewRatio =
    effSlotsRequired > 0 ? effSlotsDone / effSlotsRequired : hasReviewsRemaining ? 0 : 1;

  const rawTaskPct = hasReviewPhase ? taskRatio * TASK_PROGRESS_WEIGHT : rawTaskOnlyPct;
  const rawReviewPct = hasReviewPhase ? taskRatio * REVIEW_PROGRESS_WEIGHT * reviewRatio : 0;
  const rawReviewRemainingPct = hasReviewPhase
    ? taskRatio * REVIEW_PROGRESS_WEIGHT * (1 - reviewRatio)
    : 0;
  const rawTotalPct = rawTaskPct + rawReviewPct;

  const hasIncompleteWork = safeCompleted < safeTotal || hasReviewsRemaining;

  let progressPct: number;
  let pctComplete: number;
  let taskProgressPct: number;
  let reviewProgressPct: number;
  let reviewRemainingPct: number;

  if (hasIncompleteWork) {
    progressPct = Math.min(roundTo(rawTotalPct, 1), 99.9);
    pctComplete = Math.min(roundTo(rawTotalPct, 2), 99.9);
    taskProgressPct = roundTo(rawTaskPct, 2);
    reviewProgressPct = hasReviewPhase
      ? Math.max(0, roundTo(pctComplete - taskProgressPct, 2))
      : 0;
    if (hasReviewsRemaining && safeCompleted > 0) {
      reviewRemainingPct =
        safeCompleted >= safeTotal
          ? Math.max(0.1, roundTo(100 - pctComplete, 2))
          : Math.max(0.01, roundTo(rawReviewRemainingPct, 2));
    } else {
      reviewRemainingPct = 0;
    }
  } else {
    progressPct = 100;
    pctComplete = 100;
    taskProgressPct = hasReviewPhase ? TASK_PROGRESS_WEIGHT : 100;
    reviewProgressPct = hasReviewPhase ? REVIEW_PROGRESS_WEIGHT : 0;
    reviewRemainingPct = 0;
  }

  return {
    progressPct,
    pctComplete,
    taskPctComplete: roundTo(rawTaskOnlyPct, 2),
    taskProgressPct,
    reviewProgressPct,
    reviewRemainingPct,
    taskWeightPct: TASK_PROGRESS_WEIGHT,
    reviewWeightPct: REVIEW_PROGRESS_WEIGHT,
    requiredReviews: effRequiredReviews,
    perReviewPct,
    reviewsRequired: effSlotsRequired,
    reviewsDone: effSlotsDone,
    reviewsRemaining: effSlotsRemaining,
    hasReviewPhase,
    hasReviewsRemaining,
    hasIncompleteWork,
  };
}

export function formatProgressPct(pct: number, hasIncompleteWork = false): string {
  if (!Number.isFinite(pct)) return '0.0%';
  const clamped = hasIncompleteWork
    ? Math.max(0, Math.min(99.9, pct))
    : Math.max(0, Math.min(100, pct));
  return `${clamped.toFixed(1)}%`;
}

export function formatPerReviewPct(perReviewPct: number): string {
  if (!Number.isFinite(perReviewPct)) return '10%';
  const rounded = roundTo(perReviewPct, 1);
  return Number.isInteger(rounded) ? `${rounded}%` : `${rounded.toFixed(1)}%`;
}
