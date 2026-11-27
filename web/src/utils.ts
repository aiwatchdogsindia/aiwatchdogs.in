import { getFindingTag, getTaskTag, getWorkTypeTag } from './lib/tags';
import type { EpistemicStatus } from './types';

export function formatInrCr(cr: number | null | undefined): string {
  if (cr == null || Number.isNaN(cr)) return '—';
  if (cr >= 1000) return `₹${cr.toLocaleString('en-IN', { maximumFractionDigits: 0 })} Cr`;
  if (cr >= 10) return `₹${cr.toLocaleString('en-IN', { maximumFractionDigits: 1 })} Cr`;
  return `₹${cr.toLocaleString('en-IN', { maximumFractionDigits: 2 })} Cr`;
}

export function formatInr(amount: number | null | undefined): string {
  if (amount == null || Number.isNaN(amount)) return '—';
  const abs = Math.abs(amount);
  const sign = amount < 0 ? '-' : '';
  if (abs >= 1e7) {
    return `${sign}₹${(abs / 1e7).toLocaleString('en-IN', { maximumFractionDigits: 2 })} Cr`;
  }
  if (abs >= 1e5) {
    return `${sign}₹${(abs / 1e5).toLocaleString('en-IN', { maximumFractionDigits: 2 })} L`;
  }
  return `${sign}₹${abs.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
}

export function formatInrExact(amount: number | null | undefined): string {
  if (amount == null || Number.isNaN(amount)) return '—';
  return `₹${Math.round(amount).toLocaleString('en-IN')}`;
}

export function formatRelativeTime(iso?: string | null): string {
  if (!iso) return 'just now';
  const parsed = Date.parse(iso);
  if (Number.isNaN(parsed)) return iso;
  const diffSec = Math.max(0, Math.floor((Date.now() - parsed) / 1000));
  if (diffSec < 15) return 'just now';
  if (diffSec < 60) return `${diffSec}s ago`;
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin} min ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr} h ago`;
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay < 30) return `${diffDay} d ago`;
  return new Date(parsed).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
}

export function formatShortRel(iso?: string | null): string {
  if (!iso) return 'now';
  const parsed = Date.parse(iso);
  if (Number.isNaN(parsed)) return 'now';
  const diffSec = Math.max(0, Math.floor((Date.now() - parsed) / 1000));
  if (diffSec < 60) return `${Math.max(1, diffSec)} s`;
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin} min`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr} h`;
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay < 30) return `${diffDay} d`;
  return new Date(parsed).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
}

export function formatDateShort(iso?: string | null): string {
  if (!iso) return '2026';
  const parsed = Date.parse(iso);
  if (Number.isNaN(parsed)) return iso;
  return new Date(parsed).toLocaleDateString('en-IN', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  });
}

export function formatDateTimeIst(iso?: string | null): string {
  if (!iso) return '';
  const parsed = Date.parse(iso);
  if (Number.isNaN(parsed)) return iso;
  const datePart = new Intl.DateTimeFormat('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    timeZone: 'Asia/Kolkata',
  }).format(parsed);
  const timePart = new Intl.DateTimeFormat('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'Asia/Kolkata',
  }).format(parsed);
  return `${datePart}, ${timePart} IST`;
}

export function getInitials(text?: string | null): string {
  if (!text) return 'AW';
  const cleaned = text.replace(/^@/, '').trim();
  const parts = cleaned.split(/[-_.\s]+/).filter(Boolean);
  if (parts.length >= 2) {
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }
  return cleaned.slice(0, 2).toUpperCase() || 'AW';
}

export function isUnverifiedStatus(status?: EpistemicStatus): boolean {
  const s = (status || '').toLowerCase();
  return (
    s === 'peer_review' ||
    s === 'raw_lead' ||
    s === 'unverified' ||
    s === 'needs_adjudication' ||
    s === 'under_review' ||
    s === 'under_editorial_review' ||
    s === 'editorial_review'
  );
}

export function isVerifiedStatus(status?: EpistemicStatus): boolean {
  const s = (status || '').toLowerCase();
  return s === 'verified' || s === 'rti_filed' || s === 'official_response_logged';
}

export function isClearedStatus(status?: EpistemicStatus): boolean {
  const s = (status || '').toLowerCase();
  return s === 'cleared' || s === 'resolved_explained';
}

export function statusMeta(status: EpistemicStatus): {
  label: string;
  shortLabel: string;
  badgeClass: string;
  description: string;
} {
  const tag = getFindingTag(status);
  return {
    label: tag.label,
    shortLabel: tag.shortLabel,
    badgeClass: tag.badgeClass,
    description: tag.description,
  };
}

export function providerBadgeClass(provider?: string): string {
  const p = (provider || '').toLowerCase();
  if (p.includes('rule') || p.includes('cpu') || p.includes('deterministic')) {
    return 'tag-provider cpu';
  }
  return 'tag-provider';
}

export function pillarLabel(pillar?: string): string {
  const map: Record<string, string> = {
    procurement: 'Procurement & Tenders',
    audit: 'CAG & PAC Audits',
    budget: 'Union & State Budgets',
    disclosure: 'Public Disclosure',
    scheme: 'Welfare & Social Audits',
    infrastructure: 'Infrastructure & Works',
    corporate_political: 'Disclosures & Governance',
    parliament: 'Parliament & RTI',
  };
  return map[(pillar || '').toLowerCase()] || pillar || 'Public Audit';
}

const SLUG_PROJECT_CODES: Record<string, string> = {
  'police-finances': 'P1',
  'gov-in-public-information': 'P2',
  'union-budget-unspent-funds': 'P3',
  'one-road-two-bills': 'P4',
  'one-teacher-five-grades': 'P5',
  'parliaments-to-do-list': 'P6',
  'only-one-bid': 'P7',
};

const METHODOLOGY_PROJECT_CODES: Record<string, string> = {
  P1: 'P1',
  W1: 'P2',
  UB1: 'P3',
  OR1: 'P4',
  P5: 'P5',
  P6: 'P6',
  P7: 'P7',
};

/**
 * Returns the canonical sequential project code (P1, P2, P3, ...) for UI display,
 * preferring the hub API's `project_code` and falling back cleanly by slug / methodology.
 */
export function projectCodeFor(
  mission?: {
    project_code?: string | null;
    slug?: string | null;
    methodology_code?: string | null;
  } | null,
): string {
  const explicit = (mission?.project_code || '').trim().toUpperCase();
  if (explicit) return explicit;
  const slug = (mission?.slug || '').trim().toLowerCase();
  if (slug && SLUG_PROJECT_CODES[slug]) return SLUG_PROJECT_CODES[slug];
  const method = (mission?.methodology_code || '').trim().toUpperCase();
  if (method && METHODOLOGY_PROJECT_CODES[method]) return METHODOLOGY_PROJECT_CODES[method];
  return method || 'P1';
}

export interface PillarTint {
  /** CSS colour for inline styles: a design token, so it follows the theme. */
  hex: string;
  rgb: string;
  wash: string;
  label: string;
}

export function pillarTint(pillar?: string): PillarTint {
  const p = (pillar || '').toLowerCase();
  if (p.includes('procure') || p.includes('tender')) {
    return { hex: 'var(--secondary)', rgb: '255, 153, 51', wash: 'rgba(255, 153, 51, 0.14)', label: 'Procurement' };
  }
  if (p.includes('audit') || p.includes('cag')) {
    return { hex: 'var(--primary)', rgb: '60, 56, 50', wash: 'rgba(27, 26, 23, 0.10)', label: 'CAG & Compliance' };
  }
  if (p.includes('budget') || p.includes('fiscal')) {
    return { hex: 'var(--tertiary)', rgb: '95, 211, 90', wash: 'rgba(19, 136, 8, 0.12)', label: 'Budgets & Fiscal' };
  }
  if (p.includes('infra') || p.includes('work') || p.includes('road')) {
    return { hex: 'var(--secondary-ink)', rgb: '168, 79, 0', wash: 'rgba(138, 66, 0, 0.12)', label: 'Infrastructure' };
  }
  if (p.includes('scheme') || p.includes('welfare') || p.includes('social')) {
    return { hex: 'var(--secondary-hover)', rgb: '255, 184, 102', wash: 'rgba(224, 106, 16, 0.12)', label: 'Welfare & Schemes' };
  }
  if (p.includes('corp') || p.includes('politic') || p.includes('disclos') || p.includes('gov')) {
    return { hex: 'var(--tertiary-ink)', rgb: '150, 225, 130', wash: 'rgba(79, 168, 62, 0.12)', label: 'Disclosures' };
  }
  if (p.includes('parl')) {
    return { hex: 'var(--secondary-ink)', rgb: '168, 79, 0', wash: 'rgba(168, 79, 0, 0.11)', label: 'Parliament & RTI' };
  }
  return { hex: 'var(--ink-3)', rgb: '60, 56, 50', wash: 'rgba(27, 26, 23, 0.10)', label: pillarLabel(pillar) };
}

export const METHODOLOGY_DESCRIPTIONS: Record<string, { name: string; rule: string }> = {
  P1: { name: 'Police Budget-Head Variance', rule: 'MH 2055 / 4055, Actuals vs BE vs RE from official budget documents (cited page and line)' },
  UB1: {
    name: 'Union Budget lines: budget estimate vs revised estimate vs actual',
    rule: 'Reads a numbered line of the Notes on Demands for Grants from two checksummed Union Budget PDFs and reports the part of its budget estimate that was not spent, when that is 10% or more and at least Rs 1 crore.',
  },
  OR1: {
    name: 'One Road, Two Bills: Union duplicate billing & dual-agency claims',
    rule: 'Reads non-overlapping section windows of checksummed Union CAG Audit Report PDFs (past 2 years) for documented duplicate billing, double payments, two receipts, or irregular dual-agency claims while excluding legitimate cost-sharing.',
  },
  P5: {
    name: 'One Teacher, Five Grades: single-teacher & primary staffing concentration',
    rule: 'Cross-checks official MoE UDISE+ national reports (Tables 2.2, 3.3, 4.3, 5.2) across 2023-24 to 2025-26 to map single-teacher concentration (>=10%), pupil overcrowding (>30:1 RTE norm), and Government Primary (I-V) staffing below 2 teachers/school.',
  },
  P6: {
    name: "Parliament's To-Do List: ministerial assurances & follow-up answers",
    rule: 'Verifies formal ministerial assurances across all 56 Union Ministries from Digital Sansad (18th Lok Sabha & Rajya Sabha), citing the original promise, recorded status, and what the follow-up implementation statement actually answered.',
  },
  P7: {
    name: 'Only One Bid: single-bidder tenders & repeat-winner patterns (2016–2026)',
    rule: 'Audits official Indian public procurement records (2016–2026) for non-exempt single-bidder awards, repeat-winner concentration, and compressed bid windows against GFR 2017 Rules 166/173(xx), CVC guidelines, and State procurement rules, separating target tender value from cohort totals.',
  },
};

const ALLOWED_PRODUCTION_ORIGINS = new Set([
  'https://aiwatchdogs.in',
  'https://www.aiwatchdogs.in',
]);

// eslint-disable-next-line no-control-regex
const CONTROL_CHAR_RE = /[\u0000-\u001f\u007f]/;

export function safeExternalUrl(raw?: string | null): string | null {
  if (!raw || typeof raw !== 'string') return null;
  const trimmed = raw.trim();
  if (!trimmed || CONTROL_CHAR_RE.test(trimmed)) return null;
  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') return null;
    if (parsed.username || parsed.password) return null;
    if (!parsed.hostname) return null;
    return parsed.href;
  } catch {
    return null;
  }
}

export function safeDecodeURIComponent(raw: string): string {
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

export function getCanonicalOrigin(): string {
  if (typeof window !== 'undefined' && window.location && window.location.origin) {
    const origin = window.location.origin.replace(/\/+$/, '');
    if (ALLOWED_PRODUCTION_ORIGINS.has(origin)) {
      return origin;
    }
    if (import.meta.env.DEV) {
      try {
        const parsed = new URL(origin);
        if (
          (parsed.protocol === 'http:' || parsed.protocol === 'https:') &&
          (parsed.hostname === 'localhost' || parsed.hostname === '127.0.0.1')
        ) {
          return origin;
        }
      } catch {
        // fall through to canonical production origin
      }
    }
  }
  return 'https://aiwatchdogs.in';
}

export function buildCanonicalJoinPrompt(origin: string = getCanonicalOrigin()): string {
  const base = origin.replace(/\/+$/, '');
  return `Fetch and follow ${base}/join.txt and start a civic-audit agent now`;
}

export function buildProjectJoinPrompt(
  slug: string,
  _methodologyCode?: string,
  origin: string = getCanonicalOrigin(),
): string {
  const base = origin.replace(/\/+$/, '');
  return `Fetch and follow ${base}/join.txt and start auditing project ${slug} now`;
}

export function buildTaskJoinPrompt(
  slug: string,
  taskId: string,
  _methodologyCode?: string,
  origin: string = getCanonicalOrigin(),
): string {
  const base = origin.replace(/\/+$/, '');
  return `Fetch and follow ${base}/join.txt and run task ${taskId} on ${slug} now`;
}

export const CANONICAL_JOIN_PROMPT = buildCanonicalJoinPrompt();

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes) || bytes <= 0) return 'Unrecorded';
  if (bytes >= 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(2)} MB (${bytes.toLocaleString('en-IN')} bytes)`;
  }
  if (bytes >= 1024) {
    return `${(bytes / 1024).toFixed(1)} KB (${bytes.toLocaleString('en-IN')} bytes)`;
  }
  return `${bytes.toLocaleString('en-IN')} bytes`;
}

export function isOfficialGovUrl(url?: string | null): boolean {
  if (!url || typeof url !== 'string') return false;
  const trimmed = url.trim();
  if (!trimmed || CONTROL_CHAR_RE.test(trimmed)) return false;
  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol !== 'https:') return false;
    if (parsed.username || parsed.password) return false;
    const host = parsed.hostname.toLowerCase();
    return (
      host === 'gov.in' ||
      host.endsWith('.gov.in') ||
      host === 'nic.in' ||
      host.endsWith('.nic.in') ||
      host === 'cag.org.in' ||
      host.endsWith('.cag.org.in') ||
      host === 'sansad.in' ||
      host.endsWith('.sansad.in')
    );
  } catch {
    return false;
  }
}

export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // fallback below
  }
  try {
    const el = document.createElement('textarea');
    el.value = text;
    el.style.position = 'fixed';
    el.style.opacity = '0';
    document.body.appendChild(el);
    el.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(el);
    return ok;
  } catch {
    return false;
  }
}

export function downloadTextFile(filename: string, content: string, mime = 'text/plain;charset=utf-8'): void {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/** "via Antigravity · Gemini 4 Neon" — the AI coding agent that produced a piece of work. */
export function codingAgentLabel(agent?: string | null, model?: string | null): string {
  const a = (agent || '').trim();
  const m = (model || '').trim();
  if (!a) return '';
  return m ? `${a} (${m})` : a;
}

/** 1234567 -> "1.2M". Undefined stays undefined so callers can say "not reported". */
export function formatTokens(n: number): string {
  for (const [size, unit] of [[1e12, 'T'], [1e9, 'B'], [1e6, 'M'], [1e3, 'K']] as const) {
    if (n >= size) return `${Number((n / size).toFixed(1))}${unit}`;
  }
  return String(n);
}

export function formatSeconds(s: number): string {
  if (s < 1) return 'under 1s';
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

export function workTypeLabel(type?: string, methodologyCode?: string): string {
  return getWorkTypeTag(type, methodologyCode).label;
}

/** Plain words for a task's state on the project page. */
export function taskStatusLabel(status: string, waiting?: boolean): string {
  return getTaskTag(status, waiting).label;
}

export type AssuranceStatusKey = 'pending' | 'fulfilled' | 'partly' | 'dropped' | 'other';

export interface AssuranceStatusMeta {
  key: AssuranceStatusKey;
  label: string;
  chipClass: string;
}

export function formatAssuranceStatus(raw?: string | null): AssuranceStatusMeta {
  const s = (raw || '').trim().toLowerCase().replace(/[\s-]+/g, '_');
  if (s === 'pending' || s === 'overdue_pending') {
    return { key: 'pending', label: 'Pending', chipClass: 'p6-status p6-status--pending' };
  }
  if (s === 'fully_implemented' || s === 'fimp' || s === 'fulfilled' || s === 'implemented') {
    return { key: 'fulfilled', label: 'Fully implemented', chipClass: 'p6-status p6-status--fulfilled' };
  }
  if (s === 'partly_implemented' || s === 'partially_implemented' || s === 'partly_fulfilled') {
    return { key: 'partly', label: 'Partly implemented', chipClass: 'p6-status p6-status--partly' };
  }
  if (s === 'dropped') {
    return { key: 'dropped', label: 'Dropped', chipClass: 'p6-status p6-status--dropped' };
  }
  const clean = (raw || '').trim();
  return {
    key: 'other',
    label: clean ? clean.replace(/_/g, ' ') : 'Unrecorded',
    chipClass: 'p6-status p6-status--other',
  };
}

export interface ParsedAssuranceMetrics {
  ministry: string | null;
  house: string | null;
  session: string | null;
  assuranceRef: string | null;
  promiseDate: string | null;
  recordedStatus: string | null;
  originalPromise: string | null;
  followupAnswer: string | null;
  followupDate: string | null;
  daysElapsed: number | null;
  fulfillmentDays: number | null;
  extensionsCount: number | null;
  extensionUpto: string | null;
  impRecordNo: string | null;
  member: string | null;
  minister: string | null;
  remarks: string | null;
  signalCategory: string | null;
}

function metricStr(m: Record<string, unknown>, ...keys: string[]): string | null {
  for (const k of keys) {
    const v = m[k];
    if (typeof v === 'string' && v.trim()) return v.trim();
    if (typeof v === 'number' && Number.isFinite(v)) return String(v);
  }
  return null;
}

function metricNum(m: Record<string, unknown>, ...keys: string[]): number | null {
  for (const k of keys) {
    const v = m[k];
    if (typeof v === 'number' && Number.isFinite(v)) return v;
    if (typeof v === 'string' && v.trim() !== '') {
      const n = Number(v);
      if (Number.isFinite(n)) return n;
    }
  }
  return null;
}

function metricStrList(m: Record<string, unknown>, ...keys: string[]): string[] {
  for (const k of keys) {
    const v = m[k];
    if (Array.isArray(v)) {
      return v
        .map((item) => (typeof item === 'string' || typeof item === 'number' ? String(item).trim() : ''))
        .filter(Boolean);
    }
    if (typeof v === 'string' && v.trim()) {
      const trimmed = v.trim();
      if (trimmed.startsWith('[')) {
        try {
          const parsed = JSON.parse(trimmed);
          if (Array.isArray(parsed)) {
            return parsed
              .map((item) => (typeof item === 'string' || typeof item === 'number' ? String(item).trim() : ''))
              .filter(Boolean);
          }
        } catch {
          // fall through to comma split
        }
      }
      return trimmed
        .split(/[,;]+/)
        .map((s) => s.trim())
        .filter(Boolean);
    }
  }
  return [];
}

function metricBool(m: Record<string, unknown>, ...keys: string[]): boolean {
  for (const k of keys) {
    const v = m[k];
    if (typeof v === 'boolean') return v;
    if (typeof v === 'number') return v !== 0;
    if (typeof v === 'string') {
      const lower = v.trim().toLowerCase();
      if (lower === 'true' || lower === '1' || lower === 'yes') return true;
      if (lower === 'false' || lower === '0' || lower === 'no') return false;
    }
  }
  return false;
}

export function extractAssuranceMetrics(
  metrics?: Record<string, unknown> | null,
): ParsedAssuranceMetrics | null {
  if (!metrics || typeof metrics !== 'object') return null;
  const originalPromise = metricStr(metrics, 'original_promise');
  const recordedStatus = metricStr(metrics, 'recorded_status', 'raw_status');
  const followupAnswer = metricStr(metrics, 'followup_answer');
  if (!originalPromise && !recordedStatus && !followupAnswer) return null;

  return {
    ministry: metricStr(metrics, 'ministry'),
    house: metricStr(metrics, 'house'),
    session: metricStr(metrics, 'session'),
    assuranceRef: metricStr(metrics, 'assurance_ref', 'oassu_no'),
    promiseDate: metricStr(metrics, 'promise_date'),
    recordedStatus,
    originalPromise,
    followupAnswer,
    followupDate: metricStr(metrics, 'followup_date'),
    daysElapsed: metricNum(metrics, 'days_elapsed'),
    fulfillmentDays: metricNum(metrics, 'fulfillment_days'),
    extensionsCount: metricNum(metrics, 'extensions_count', 'ext_no'),
    extensionUpto: metricStr(metrics, 'extension_upto', 'ext_upto'),
    impRecordNo: metricStr(metrics, 'imp_record_no', 'impRecordNo'),
    member: metricStr(metrics, 'member'),
    minister: metricStr(metrics, 'minister'),
    remarks: metricStr(metrics, 'remarks'),
    signalCategory: metricStr(metrics, 'signal_category'),
  };
}

export type ProcurementSignalKey =
  | 'single_bidder_repeat_winner'
  | 'unexplained_single_bidder'
  | 'repeat_winner_concentration'
  | 'short_window_or_process_gap'
  | 'other';

export interface ProcurementSignalMeta {
  key: ProcurementSignalKey;
  label: string;
  shortLabel: string;
  chipClass: string;
}

export function formatProcurementSignal(raw?: string | null): ProcurementSignalMeta {
  const s = (raw || '').trim().toLowerCase().replace(/[\s-]+/g, '_');
  if (s === 'single_bidder_repeat_winner') {
    return {
      key: 'single_bidder_repeat_winner',
      label: 'Single Bid + Repeat Winner',
      shortLabel: 'Single bid + repeat',
      chipClass: 'p7-signal p7-signal--combined',
    };
  }
  if (s === 'unexplained_single_bidder') {
    return {
      key: 'unexplained_single_bidder',
      label: 'Single-Bidder Tender',
      shortLabel: 'Single bidder',
      chipClass: 'p7-signal p7-signal--single',
    };
  }
  if (s === 'repeat_winner_concentration') {
    return {
      key: 'repeat_winner_concentration',
      label: 'Repeat-Winner Concentration',
      shortLabel: 'Repeat winner',
      chipClass: 'p7-signal p7-signal--repeat',
    };
  }
  if (s === 'short_window_or_process_gap') {
    return {
      key: 'short_window_or_process_gap',
      label: 'Compressed Window / Process Gap',
      shortLabel: 'Process gap',
      chipClass: 'p7-signal p7-signal--process',
    };
  }
  const clean = (raw || '').trim();
  return {
    key: 'other',
    label: clean ? clean.replace(/_/g, ' ') : 'Procurement Pattern Lead',
    shortLabel: clean ? clean.replace(/_/g, ' ') : 'Procurement lead',
    chipClass: 'p7-signal p7-signal--other',
  };
}

export function formatSingleBidMechanism(raw?: string | null): string | null {
  const s = (raw || '').trim().toLowerCase().replace(/[\s-]+/g, '_');
  if (
    !s ||
    s === 'none' ||
    s === 'not_applicable' ||
    s === 'multiple_bids' ||
    s === 'multi_bid' ||
    s === 'unrecorded' ||
    s === 'unknown' ||
    s === 'n_a'
  ) {
    return null;
  }
  if (s === 'single_bid_received') return 'Only 1 bid received';
  if (
    s === 'single_qualified_after_rejection' ||
    s === 'single_qualified_after_tech_rejection'
  ) {
    return '1 qualified after technical screening';
  }
  if (s === 'single_tender_enquiry') return 'Single tender enquiry';
  return (raw || '').trim().replace(/_/g, ' ');
}

export function resolveCitationHref(
  cit: {
    role?: string | null;
    source_url?: string | null;
    source_ref?: string | null;
    source_landing_page?: string | null;
    sha256?: string | null;
  },
  artifactSha?: string,
): string | null {
  const role = (cit.role || '').trim().toLowerCase().replace(/[\s-]+/g, '_');
  if (role === 'statutory_benchmark') {
    return safeExternalUrl(cit.source_landing_page || cit.source_url || cit.source_ref);
  }
  const ownDocument =
    cit.source_url && cit.sha256 && artifactSha && cit.sha256 !== artifactSha
      ? cit.source_url
      : null;
  return safeExternalUrl(ownDocument || cit.source_ref || cit.source_url);
}

export function formatProcurementAmountType(raw?: string | null): string {
  const s = (raw || '').trim().toLowerCase().replace(/[\s-]+/g, '_');
  if (s === 'awarded_contract_values' || s === 'award_value' || s === 'awarded_value') {
    return 'Awarded contract value';
  }
  if (s === 'tender_estimates' || s === 'tender_estimate' || s === 'estimated_value') {
    return 'Tender estimate';
  }
  return 'Target tender value';
}

export function formatCitationRoleLabel(role?: string | null): string | null {
  const r = (role || '').trim().toLowerCase().replace(/[\s-]+/g, '_');
  if (!r) return null;
  const map: Record<string, string> = {
    primary_tender: 'Primary Tender Record',
    repeat_winner_cohort: 'Repeat-Winner Cohort',
    statutory_benchmark: 'Statutory Benchmark',
    original_promise: 'Original Promise',
    recorded_status: 'Recorded Status',
    followup_answer: 'Follow-Up Answer',
  };
  if (map[r]) return map[r];
  const readable = r.replace(/_/g, ' ');
  return readable.charAt(0).toUpperCase() + readable.slice(1);
}

export interface ParsedOnlyOneBidMetrics {
  caseKey: string | null;
  auditMode: string | null;
  verdict: string | null;
  signalCategory: string | null;
  triggeredRules: string[];
  portal: string | null;
  authority: string | null;
  authorityKey: string | null;
  jurisdiction: string | null;
  jurisdictionLevel: string | null;
  tenderId: string | null;
  ocid: string | null;
  tenderTitle: string | null;
  itemCategory: string | null;
  procurementMethod: string | null;
  exemptionStatus: string | null;
  bidsReceived: number | null;
  technicallyQualifiedBids: number | null;
  effectiveBids: number | null;
  singleBidMechanism: string | null;
  bidWindowDays: number | null;
  publishDate: string | null;
  closeDate: string | null;
  awardDate: string | null;
  fiscalYear: string | null;
  fiscalYearStart: string | null;
  fiscalYearEnd: string | null;
  amountType: string | null;
  awardValueInr: number | null;
  tenderEstimateInr: number | null;
  /** Target tender's own value only (award value, else tender estimate) — NEVER the cohort total. */
  targetTenderValueInr: number | null;
  winnerName: string | null;
  normalizedWinnerKey: string | null;
  winnerTotalWins: number | null;
  winnerSingleBidWins: number | null;
  /** Separate multi-award cohort total for context only — never summed into project exposure. */
  winnerCohortTotalInr: number | null;
  winnerShareByCountPct: number | null;
  winnerShareByValuePct: number | null;
  consecutiveWinStreak: number | null;
  authorityCohortTenders: number | null;
  authoritySingleBidRatePct: number | null;
  relatedTenderIds: string[];
  governingInstrument: string | null;
  sourceLocator: string | null;
  isAnchorCase: boolean;
}

const P7_SIGNAL_SET = new Set([
  'single_bidder_repeat_winner',
  'unexplained_single_bidder',
  'repeat_winner_concentration',
  'short_window_or_process_gap',
]);

export function extractOnlyOneBidMetrics(
  metrics?: Record<string, unknown> | null,
): ParsedOnlyOneBidMetrics | null {
  if (!metrics || typeof metrics !== 'object') return null;

  const tenderId = metricStr(metrics, 'tender_id');
  const ocid = metricStr(metrics, 'ocid');
  const winnerName = metricStr(metrics, 'winner_name');
  const singleBidMechanism = metricStr(metrics, 'single_bid_mechanism');
  const signalCategory = metricStr(metrics, 'signal_category');
  const bidsReceived = metricNum(metrics, 'bids_received');
  const technicallyQualifiedBids = metricNum(metrics, 'technically_qualified_bids');
  const effectiveBids = metricNum(metrics, 'effective_bids');
  const winnerTotalWins = metricNum(metrics, 'winner_total_wins');

  const hasP7Signal = Boolean(signalCategory && P7_SIGNAL_SET.has(signalCategory.toLowerCase()));
  if (
    !tenderId &&
    !ocid &&
    !winnerName &&
    !singleBidMechanism &&
    bidsReceived === null &&
    technicallyQualifiedBids === null &&
    effectiveBids === null &&
    winnerTotalWins === null &&
    !hasP7Signal
  ) {
    return null;
  }

  const awardValueInr = metricNum(metrics, 'award_value_inr');
  const tenderEstimateInr = metricNum(metrics, 'tender_estimate_inr');
  const targetTenderValueInr =
    awardValueInr != null && awardValueInr > 0
      ? awardValueInr
      : tenderEstimateInr != null && tenderEstimateInr > 0
        ? tenderEstimateInr
        : null;

  return {
    caseKey: metricStr(metrics, 'case_key'),
    auditMode: metricStr(metrics, 'audit_mode'),
    verdict: metricStr(metrics, 'verdict'),
    signalCategory,
    triggeredRules: metricStrList(metrics, 'triggered_rules'),
    portal: metricStr(metrics, 'portal'),
    authority: metricStr(metrics, 'authority'),
    authorityKey: metricStr(metrics, 'authority_key'),
    jurisdiction: metricStr(metrics, 'jurisdiction'),
    jurisdictionLevel: metricStr(metrics, 'jurisdiction_level'),
    tenderId,
    ocid,
    tenderTitle: metricStr(metrics, 'tender_title'),
    itemCategory: metricStr(metrics, 'item_category'),
    procurementMethod: metricStr(metrics, 'procurement_method'),
    exemptionStatus: metricStr(metrics, 'exemption_status'),
    bidsReceived,
    technicallyQualifiedBids,
    effectiveBids,
    singleBidMechanism,
    bidWindowDays: metricNum(metrics, 'bid_window_days'),
    publishDate: metricStr(metrics, 'publish_date'),
    closeDate: metricStr(metrics, 'close_date'),
    awardDate: metricStr(metrics, 'award_date'),
    fiscalYear: metricStr(metrics, 'fiscal_year'),
    fiscalYearStart: metricStr(metrics, 'fiscal_year_start'),
    fiscalYearEnd: metricStr(metrics, 'fiscal_year_end'),
    amountType: metricStr(metrics, 'amount_type'),
    awardValueInr,
    tenderEstimateInr,
    targetTenderValueInr,
    winnerName,
    normalizedWinnerKey: metricStr(metrics, 'normalized_winner_key'),
    winnerTotalWins,
    winnerSingleBidWins: metricNum(metrics, 'winner_single_bid_wins'),
    winnerCohortTotalInr: metricNum(metrics, 'winner_cohort_total_inr'),
    winnerShareByCountPct: metricNum(metrics, 'winner_share_by_count_pct'),
    winnerShareByValuePct: metricNum(metrics, 'winner_share_by_value_pct'),
    consecutiveWinStreak: metricNum(metrics, 'consecutive_win_streak'),
    authorityCohortTenders: metricNum(metrics, 'authority_cohort_tenders'),
    authoritySingleBidRatePct: metricNum(metrics, 'authority_single_bid_rate_pct'),
    relatedTenderIds: metricStrList(metrics, 'related_tender_ids'),
    governingInstrument: metricStr(metrics, 'governing_instrument'),
    sourceLocator: metricStr(metrics, 'source_locator'),
    isAnchorCase: metricBool(metrics, 'is_anchor_case'),
  };
}

export function formatOnlyOneBidBidsSummary(rec: ParsedOnlyOneBidMetrics): string | null {
  const recCount = rec.bidsReceived;
  const qualCount = rec.technicallyQualifiedBids;
  const effCount = rec.effectiveBids;

  if (recCount != null && qualCount != null && recCount !== qualCount) {
    return `${recCount.toLocaleString('en-IN')} received → ${qualCount.toLocaleString('en-IN')} qualified`;
  }
  if (recCount != null) {
    return `${recCount.toLocaleString('en-IN')} ${recCount === 1 ? 'bid' : 'bids'} received`;
  }
  if (qualCount != null) {
    return `${qualCount.toLocaleString('en-IN')} qualified ${qualCount === 1 ? 'bid' : 'bids'}`;
  }
  if (effCount != null) {
    return `${effCount.toLocaleString('en-IN')} effective ${effCount === 1 ? 'bid' : 'bids'}`;
  }
  return null;
}

function formatOnlyOneBidSignalSummary(rec: ParsedOnlyOneBidMetrics): string | null {
  const parts: string[] = [];
  if (rec.effectiveBids != null) {
    parts.push(`${rec.effectiveBids.toLocaleString('en-IN')} ${rec.effectiveBids === 1 ? 'bid' : 'bids'}`);
  } else if (rec.bidsReceived != null) {
    parts.push(`${rec.bidsReceived.toLocaleString('en-IN')} ${rec.bidsReceived === 1 ? 'bid' : 'bids'}`);
  }
  if (rec.winnerTotalWins != null && rec.winnerTotalWins >= 2) {
    parts.push(`${rec.winnerTotalWins.toLocaleString('en-IN')} wins`);
  }
  if (rec.winnerShareByCountPct != null && rec.winnerShareByCountPct > 0 && parts.length < 2) {
    parts.push(`${rec.winnerShareByCountPct.toFixed(0)}% share`);
  }
  return parts.length > 0 ? parts.join(' · ') : null;
}

export interface FindingDrawerMetric {
  label: string;
  value: string;
}

export interface FindingCardMetric {
  value: string;
  prominent: boolean;
}

type FindingMetricSource = {
  methodology_code: string;
  monetary_exposure_inr?: number | null;
  amount_label?: string | null;
  anomaly_metrics?: Record<string, unknown> | null;
  quarantined?: boolean;
  status?: string;
};

/**
 * Resolve the headline metric shown in FindingDrawer's metadata strip.
 * For P6 findings, recorded assurance status takes precedence over `monetary_exposure_inr`
 * (which is stored as 0 for non-monetary parliamentary assurances).
 * For P7 findings, target tender value (never the repeat-winner cohort total) is labeled
 * with its exact amount type (`Awarded contract value` / `Tender estimate`), or falls back
 * to a factual competition readout when value is unrecorded.
 * Non-P6/P7 findings retain their exact existing behavior.
 */
export function resolveFindingDrawerMetric(
  finding: FindingMetricSource,
): FindingDrawerMetric {
  const isQuarantined = Boolean(finding.quarantined || finding.status === 'quarantined');
  const p6Record =
    finding.methodology_code === 'P6' && !isQuarantined
      ? extractAssuranceMetrics(finding.anomaly_metrics)
      : null;
  const p6Status = p6Record?.recordedStatus
    ? formatAssuranceStatus(p6Record.recordedStatus)
    : null;

  if (p6Status) {
    return {
      label: 'Recorded Status',
      value: `${p6Status.label}${
        p6Record?.daysElapsed != null
          ? ` · ${p6Record.daysElapsed.toLocaleString('en-IN')}d`
          : ''
      }`,
    };
  }

  if (finding.methodology_code === 'P7' && !isQuarantined) {
    const p7Record = extractOnlyOneBidMetrics(finding.anomaly_metrics);
    const targetVal =
      finding.monetary_exposure_inr != null && finding.monetary_exposure_inr > 0
        ? finding.monetary_exposure_inr
        : p7Record?.targetTenderValueInr ?? null;
    if (targetVal != null && targetVal > 0) {
      return {
        label: formatProcurementAmountType(p7Record?.amountType ?? finding.amount_label),
        value: formatInr(targetVal),
      };
    }
    const signalSummary = p7Record ? formatOnlyOneBidSignalSummary(p7Record) : null;
    return {
      label: signalSummary ? 'Competition Pattern' : 'Target Tender Value',
      value: signalSummary ?? 'Unrecorded',
    };
  }

  return {
    label: finding.amount_label
      ? finding.amount_label.replace(/_/g, ' ')
      : 'Reported Figure',
    value:
      finding.monetary_exposure_inr != null
        ? formatInr(finding.monetary_exposure_inr)
        : 'Disclosure Lead',
  };
}

/**
 * Resolve the top-right headline readout shown on FindingCard.
 * For P6 findings, recorded assurance status takes precedence over `monetary_exposure_inr`.
 * For P7 findings, target tender value (never cohort total) takes precedence when > 0,
 * falling back to a crisp competition readout (e.g. "1 bid · 6 wins") when unrecorded.
 * Non-P6/P7 findings remain completely unchanged.
 */
export function resolveFindingCardMetric(
  finding: FindingMetricSource,
  withheldLabel = 'Withheld',
): FindingCardMetric {
  const isQuarantined = Boolean(finding.quarantined || finding.status === 'quarantined');
  if (isQuarantined) {
    return { value: withheldLabel, prominent: false };
  }

  const p6Record =
    finding.methodology_code === 'P6'
      ? extractAssuranceMetrics(finding.anomaly_metrics)
      : null;
  const p6RightLabel = p6Record?.recordedStatus
    ? `${formatAssuranceStatus(p6Record.recordedStatus).label}${
        p6Record.daysElapsed != null
          ? ` · ${p6Record.daysElapsed.toLocaleString('en-IN')}d`
          : ''
      }`
    : null;

  if (p6RightLabel) {
    return { value: p6RightLabel, prominent: true };
  }

  if (finding.methodology_code === 'P7') {
    const p7Record = extractOnlyOneBidMetrics(finding.anomaly_metrics);
    const targetVal =
      finding.monetary_exposure_inr != null && finding.monetary_exposure_inr > 0
        ? finding.monetary_exposure_inr
        : p7Record?.targetTenderValueInr ?? null;
    if (targetVal != null && targetVal > 0) {
      return { value: formatInr(targetVal), prominent: true };
    }
    const signalSummary = p7Record ? formatOnlyOneBidSignalSummary(p7Record) : null;
    if (signalSummary) {
      return { value: signalSummary, prominent: true };
    }
    return { value: 'Procurement lead', prominent: false };
  }

  if (finding.monetary_exposure_inr) {
    return { value: formatInr(finding.monetary_exposure_inr), prominent: true };
  }

  return {
    value: finding.amount_label || 'Disclosure lead',
    prominent: false,
  };
}
