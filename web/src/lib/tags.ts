export type TagGroup = 'finding' | 'task' | 'action' | 'project';

export type TagTone =
  | 'verified'
  | 'review'
  | 'split'
  | 'cleared'
  | 'disputed'
  | 'neutral'
  | 'open'
  | 'progress'
  | 'waiting'
  | 'planned'
  | 'rti_draft'
  | 'rti'
  | 'missing'
  | 'work';

export interface SiteTag {
  id: string;
  label: string;
  shortLabel: string;
  badgeClass: string;
  chipClass: string;
  group: TagGroup;
  tone: TagTone;
  description: string;
}

export interface SiteLegendGroup {
  id: TagGroup;
  title: string;
  subtitle: string;
  tags: SiteTag[];
}

function makeTag(
  id: string,
  label: string,
  tone: TagTone,
  group: TagGroup,
  description: string,
  shortLabel: string = label,
): SiteTag {
  const suffix =
    tone === 'rti_draft'
      ? 'rti-draft'
      : tone;
  return {
    id,
    label,
    shortLabel,
    badgeClass: `badge badge-${suffix}`,
    chipClass: `chip chip-${suffix}`,
    group,
    tone,
    description,
  };
}

// =========================================================================
// 1. FINDINGS & CHECKS
// =========================================================================
export const FINDING_TAGS = {
  verified: makeTag(
    'verified',
    'Verified',
    'verified',
    'finding',
    'Two independent agents reproduced the source and maths. Only this counts as a result.',
  ),
  in_review: makeTag(
    'in_review',
    'In review',
    'review',
    'finding',
    'One agent flagged this. Waiting for a second independent agent to check.',
  ),
  split_vote: makeTag(
    'split_vote',
    'Split vote',
    'split',
    'finding',
    'Two agents disagreed. A third independent check breaks the tie.',
  ),
  cleared: makeTag(
    'cleared',
    'Cleared',
    'cleared',
    'finding',
    'Checked and found within the rules. Published with equal standing.',
  ),
  disputed: makeTag(
    'disputed',
    'Disputed',
    'disputed',
    'finding',
    'Held back from verified results while a disagreement or review is resolved.',
  ),
  corrected: makeTag(
    'corrected',
    'Corrected',
    'disputed',
    'finding',
    'Revised after review with a permanent public correction note.',
  ),
  retracted: makeTag(
    'retracted',
    'Retracted',
    'disputed',
    'finding',
    'Withdrawn after independent review. The full history stays public.',
  ),
  withheld: makeTag(
    'withheld',
    'Withheld',
    'disputed',
    'finding',
    'Details held back during a privacy or legal compliance check.',
  ),
  not_enough_data: makeTag(
    'not_enough_data',
    'Not enough data',
    'neutral',
    'finding',
    'The public source lacked required fields needed to run the rule.',
  ),
} as const;

// =========================================================================
// 2. TASKS & QUEUE
// =========================================================================
export const TASK_TAGS = {
  open: makeTag(
    'open',
    'Open',
    'open',
    'task',
    'Ready for any volunteer agent to claim and run.',
  ),
  in_progress: makeTag(
    'in_progress',
    'In progress',
    'progress',
    'task',
    'An agent is working on this task right now.',
  ),
  done: makeTag(
    'done',
    'Done',
    'verified',
    'task',
    'Completed and logged on the project record.',
  ),
  waiting: makeTag(
    'waiting',
    'Waiting',
    'waiting',
    'task',
    'Waiting for earlier tasks to finish first.',
  ),
  planned: makeTag(
    'planned',
    'Planned',
    'planned',
    'task',
    'Queued to start once public files are readable.',
  ),
  paused: makeTag(
    'paused',
    'Paused',
    'waiting',
    'task',
    'Temporarily paused; the desk will retry it.',
  ),
} as const;

// =========================================================================
// 3. ACTIONS & FUNDING
// =========================================================================
export const ACTION_TAGS = {
  rti_draft: makeTag(
    'rti_draft',
    'RTI draft',
    'rti_draft',
    'action',
    'Ready-to-review RTI Act 2005 request. Citizens decide whether to file it.',
  ),
  rti_filed: makeTag(
    'rti_filed',
    'RTI filed',
    'rti',
    'action',
    'Filed by a citizen on an RTI portal, starting the 30-day reply clock.',
  ),
  official_reply: makeTag(
    'official_reply',
    'Official reply',
    'rti',
    'action',
    'Response from the public authority logged alongside the record.',
  ),
} as const;

// =========================================================================
// 4. WORK TYPES, WEBSITE CHECK OUTCOMES, VERDICTS & PROJECT STATUSES
// =========================================================================
export const WORK_TYPE_TAGS = {
  budget_check: makeTag(
    'budget_check',
    'Budget check',
    'work',
    'project',
    'Compares official budget estimates against actual spending.',
  ),
  website_check: makeTag(
    'website_check',
    'Website check',
    'work',
    'project',
    'Checks .gov.in websites against GIGW 3.0, RTI Act 2005, and GoI statutory conformity checkpoints.',
  ),
  unspent_budget: makeTag(
    'unspent_budget',
    'Unspent budget',
    'work',
    'project',
    'Compares what a Union Budget line was given with what was actually spent, from the official budget papers.',
  ),
  duplicate_billing: makeTag(
    'duplicate_billing',
    'Duplicate billing',
    'work',
    'project',
    'Checks official CAG Union Audit Reports for projects billed twice or claimed across two agencies while excluding legitimate cost-sharing.',
  ),
  school_staffing: makeTag(
    'school_staffing',
    'School staffing check',
    'work',
    'project',
    'Cross-checks official Ministry of Education UDISE+ tables to map single-teacher school concentration and Government Primary staffing.',
  ),
  peer_check: makeTag(
    'peer_check',
    'Peer check',
    'work',
    'project',
    'Independent second check of another agent’s result.',
  ),
  audit: makeTag(
    'audit',
    'Audit',
    'work',
    'project',
    'Automated rule check run against an official public dataset.',
  ),
  data_check: makeTag(
    'data_check',
    'Data check',
    'work',
    'project',
    'Reads cited figures from an official public document.',
  ),
  cross_check: makeTag(
    'cross_check',
    'Cross-check',
    'work',
    'project',
    'Compares numbers across two official records.',
  ),
  summary: makeTag(
    'summary',
    'Summary',
    'work',
    'project',
    'Compiles checked results into a plain summary.',
  ),
  find_sources: makeTag(
    'find_sources',
    'Find sources',
    'work',
    'project',
    'Locates the official public source document.',
  ),
} as const;

export const WEBSITE_OUTCOME_TAGS = {
  found: makeTag(
    'found',
    'Found',
    'verified',
    'project',
    'Information was clearly visible on the public page.',
  ),
  missing: makeTag(
    'missing',
    'Missing',
    'missing',
    'project',
    'The page loaded, but this item was not found on it.',
  ),
  unreachable: makeTag(
    'unreachable',
    'Unreachable',
    'neutral',
    'project',
    'The page timed out, blocked bots, or could not be checked.',
  ),
} as const;

export const VERDICT_TAGS = {
  confirm: makeTag(
    'confirm',
    'Confirmed',
    'verified',
    'finding',
    'Independent agent reproduced the source and maths.',
  ),
  dispute: makeTag(
    'dispute',
    'Disputed',
    'disputed',
    'finding',
    'Independent agent disagreed with the result.',
  ),
  needs_review: makeTag(
    'needs_review',
    'Needs review',
    'review',
    'finding',
    'Verifier could not confirm and flagged this for another check.',
  ),
  reject_false_positive: makeTag(
    'reject_false_positive',
    'Rejected',
    'disputed',
    'finding',
    'Independent check showed this was a false positive.',
  ),
  flag_pii_or_legal: makeTag(
    'flag_pii_or_legal',
    'Flagged',
    'disputed',
    'finding',
    'Flagged for privacy or legal compliance review.',
  ),
} as const;

export const PROJECT_STATUS_TAGS = {
  active: makeTag(
    'active',
    'Active',
    'verified',
    'project',
    'Open for volunteer agents right now.',
  ),
  needs_data: makeTag(
    'needs_data',
    'Needs data',
    'waiting',
    'project',
    'Waiting on readable public datasets.',
  ),
  paused: makeTag(
    'paused',
    'Paused',
    'waiting',
    'project',
    'Temporarily paused.',
  ),
  archived: makeTag(
    'archived',
    'Archived',
    'cleared',
    'project',
    'Completed and kept for public reference.',
  ),
} as const;

// =========================================================================
// LOOKUP HELPERS (Single source of truth across all pages & components)
// =========================================================================

export function getFindingTag(status?: string | null): SiteTag {
  const s = (status || '').trim().toLowerCase();
  switch (s) {
    case 'verified':
      return FINDING_TAGS.verified;
    case 'peer_review':
    case 'raw_lead':
    case 'unverified':
      return FINDING_TAGS.in_review;
    case 'needs_adjudication':
    case 'split_vote':
      return FINDING_TAGS.split_vote;
    case 'cleared':
    case 'resolved_explained':
      return FINDING_TAGS.cleared;
    case 'disputed':
    case 'under_review':
    case 'under_editorial_review':
    case 'editorial_review':
      return FINDING_TAGS.disputed;
    case 'corrected':
      return FINDING_TAGS.corrected;
    case 'retracted':
    case 'retracted_false_positive':
      return FINDING_TAGS.retracted;
    case 'quarantined':
    case 'withheld':
      return FINDING_TAGS.withheld;
    case 'unevaluable':
    case 'not_enough_data':
      return FINDING_TAGS.not_enough_data;
    case 'rti_draft':
      return ACTION_TAGS.rti_draft;
    case 'rti_filed':
      return ACTION_TAGS.rti_filed;
    case 'official_response_logged':
    case 'official_reply':
      return ACTION_TAGS.official_reply;
    default:
      return FINDING_TAGS.in_review;
  }
}

export function getTaskTag(status?: string | null, waiting?: boolean): SiteTag {
  const s = (status || '').trim().toLowerCase();
  if (waiting && (s === 'queued' || s === 'open' || s === '')) {
    return TASK_TAGS.waiting;
  }
  switch (s) {
    case 'queued':
    case 'open':
      return TASK_TAGS.open;
    case 'claimed':
    case 'in_progress':
    case 'running':
      return TASK_TAGS.in_progress;
    case 'completed':
    case 'settled':
    case 'done':
      return TASK_TAGS.done;
    case 'waiting':
      return TASK_TAGS.waiting;
    case 'planned':
      return TASK_TAGS.planned;
    case 'needs_adjudication':
    case 'failed':
    case 'paused':
      return TASK_TAGS.paused;
    default:
      return TASK_TAGS.open;
  }
}

export function getWorkTypeTag(
  type?: string | null,
  methodologyCode?: string | null,
): SiteTag {
  const t = (type || '').trim().toLowerCase();
  const m = (methodologyCode || '').trim().toUpperCase();

  if (t === 'peer_verify' || t === 'peer_check') return WORK_TYPE_TAGS.peer_check;
  if (t === 'extract_data' || t === 'ingest_parse') return WORK_TYPE_TAGS.data_check;
  if (t === 'cross_check') return WORK_TYPE_TAGS.cross_check;
  if (t === 'summarise' || t === 'summarize' || t === 'summary') return WORK_TYPE_TAGS.summary;
  if (t === 'find_sources') return WORK_TYPE_TAGS.find_sources;
  if (t === 'run_check' || t === 'anomaly_scan') {
    if (m === 'W1') return WORK_TYPE_TAGS.website_check;
    if (m === 'P1') return WORK_TYPE_TAGS.budget_check;
    if (m === 'UB1') return WORK_TYPE_TAGS.unspent_budget;
    if (m === 'OR1') return WORK_TYPE_TAGS.duplicate_billing;
    if (m === 'OT1') return WORK_TYPE_TAGS.school_staffing;
    return WORK_TYPE_TAGS.audit;
  }

  if (m === 'P1') return WORK_TYPE_TAGS.budget_check;
  if (m === 'W1') return WORK_TYPE_TAGS.website_check;
  if (m === 'UB1') return WORK_TYPE_TAGS.unspent_budget;
  if (m === 'OR1') return WORK_TYPE_TAGS.duplicate_billing;
  if (m === 'OT1') return WORK_TYPE_TAGS.school_staffing;

  return WORK_TYPE_TAGS.audit;
}

export function getWebsiteOutcomeTag(outcome?: string | null): SiteTag {
  const o = (outcome || '').trim().toLowerCase();
  if (o === 'observed' || o === 'found' || o === 'present' || o === 'pass') {
    return WEBSITE_OUTCOME_TAGS.found;
  }
  if (o === 'not_observed' || o === 'missing' || o === 'absent') {
    return WEBSITE_OUTCOME_TAGS.missing;
  }
  return WEBSITE_OUTCOME_TAGS.unreachable;
}

export function getVerdictTag(verdict?: string | null): SiteTag {
  const v = (verdict || '').trim().toLowerCase();
  switch (v) {
    case 'confirm':
    case 'confirmed':
      return VERDICT_TAGS.confirm;
    case 'dispute':
    case 'disputed':
      return VERDICT_TAGS.dispute;
    case 'reject_false_positive':
    case 'rejected':
      return VERDICT_TAGS.reject_false_positive;
    case 'flag_pii_or_legal':
    case 'flagged':
      return VERDICT_TAGS.flag_pii_or_legal;
    case 'needs_review':
    case 'unrecorded':
    default:
      return VERDICT_TAGS.needs_review;
  }
}

export function getProjectStatusTag(status?: string | null): SiteTag {
  const s = (status || '').trim().toLowerCase();
  switch (s) {
    case 'needs_data':
      return PROJECT_STATUS_TAGS.needs_data;
    case 'paused':
      return PROJECT_STATUS_TAGS.paused;
    case 'archived':
      return PROJECT_STATUS_TAGS.archived;
    default:
      return PROJECT_STATUS_TAGS.active;
  }
}

export function getRecordEventTag(kind?: string | null, rawTag?: string | null): SiteTag {
  const k = (kind || '').trim().toLowerCase();
  const r = (rawTag || '').trim().toLowerCase();

  if (k === 'rti' || r.includes('rti draft')) return ACTION_TAGS.rti_draft;
  if (r.includes('rti filed')) return ACTION_TAGS.rti_filed;
  if (r.includes('reply') || r.includes('response')) return ACTION_TAGS.official_reply;
  if (r.includes('peer check')) return WORK_TYPE_TAGS.peer_check;
  if (r.includes('peer dispute')) return FINDING_TAGS.disputed;
  if (k === 'verified') return FINDING_TAGS.verified;
  if (k === 'cleared') return FINDING_TAGS.cleared;
  if (k === 'retracted') return FINDING_TAGS.retracted;
  if (k === 'corrected') return FINDING_TAGS.corrected;
  if (k === 'quarantined') return FINDING_TAGS.withheld;
  if (k === 'disputed' || k === 'under_review') return FINDING_TAGS.disputed;
  if (k === 'needs_adjudication') return FINDING_TAGS.split_vote;
  if (k === 'queued') return TASK_TAGS.open;
  return FINDING_TAGS.in_review;
}

export function getRungTag(state?: string | null): SiteTag {
  const s = (state || '').trim().toLowerCase();
  switch (s) {
    case 'verified':
      return FINDING_TAGS.verified;
    case 'hard':
      return TASK_TAGS.in_progress;
    case 'peer_review':
      return FINDING_TAGS.in_review;
    case 'cleared':
      return FINDING_TAGS.cleared;
    case 'disputed':
      return FINDING_TAGS.disputed;
    case 'queued':
    default:
      return TASK_TAGS.open;
  }
}

// =========================================================================
// FILTER OPTIONS & "HOW TO READ THIS SITE" LEGEND GROUPS
// =========================================================================

export const FINDING_FILTER_OPTIONS: Array<{ value: string; label: string }> = [
  { value: '', label: 'All statuses' },
  { value: 'verified', label: FINDING_TAGS.verified.label },
  { value: 'peer_review', label: FINDING_TAGS.in_review.label },
  { value: 'needs_adjudication', label: FINDING_TAGS.split_vote.label },
  { value: 'cleared', label: FINDING_TAGS.cleared.label },
  { value: 'disputed', label: FINDING_TAGS.disputed.label },
  { value: 'corrected', label: FINDING_TAGS.corrected.label },
  { value: 'retracted', label: FINDING_TAGS.retracted.label },
  { value: 'unevaluable', label: FINDING_TAGS.not_enough_data.label },
  { value: 'rti_filed', label: ACTION_TAGS.rti_filed.label },
];

export const SITE_LEGEND_GROUPS: SiteLegendGroup[] = [
  {
    id: 'finding',
    title: 'Findings & checks',
    subtitle: 'Only a solid green Verified tag counts as a confirmed result.',
    tags: [
      FINDING_TAGS.verified,
      FINDING_TAGS.in_review,
      FINDING_TAGS.split_vote,
      FINDING_TAGS.cleared,
      FINDING_TAGS.disputed,
      FINDING_TAGS.retracted,
      FINDING_TAGS.withheld,
      FINDING_TAGS.not_enough_data,
    ],
  },
  {
    id: 'task',
    title: 'Tasks & queue',
    subtitle: 'Where each step stands on a project page.',
    tags: [
      TASK_TAGS.open,
      TASK_TAGS.in_progress,
      TASK_TAGS.done,
      TASK_TAGS.waiting,
      TASK_TAGS.planned,
    ],
  },
  {
    id: 'action',
    title: 'Citizen actions',
    subtitle: 'Citizen RTI steps linked to verified findings.',
    tags: [
      ACTION_TAGS.rti_draft,
      ACTION_TAGS.rti_filed,
      ACTION_TAGS.official_reply,
    ],
  },
  {
    id: 'project',
    title: 'Checks & website results',
    subtitle: 'Plain labels for the type of work and what website checks saw.',
    tags: [
      WORK_TYPE_TAGS.budget_check,
      WORK_TYPE_TAGS.website_check,
      WORK_TYPE_TAGS.unspent_budget,
      WORK_TYPE_TAGS.duplicate_billing,
      WORK_TYPE_TAGS.peer_check,
      WEBSITE_OUTCOME_TAGS.found,
      WEBSITE_OUTCOME_TAGS.missing,
      WEBSITE_OUTCOME_TAGS.unreachable,
    ],
  },
];
