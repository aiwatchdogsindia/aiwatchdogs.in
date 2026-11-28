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
    justice: 'Courts & Custody',
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
  'sovereign-lies': 'P8',
  'innocent-but-locked-up': 'P10',
};

const METHODOLOGY_PROJECT_CODES: Record<string, string> = {
  P1: 'P1',
  W1: 'P2',
  UB1: 'P3',
  OR1: 'P4',
  P5: 'P5',
  P6: 'P6',
  P7: 'P7',
  P8: 'P8',
  P10: 'P10',
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
  hex: string;
  rgb: string;
  wash: string;
  label: string;
}

export function pillarTint(pillar?: string): PillarTint {
  const p = (pillar || '').toLowerCase();
  if (p.includes('procure') || p.includes('tender')) {
    return { hex: '#FF9933', rgb: '255, 153, 51', wash: 'rgba(255, 153, 51, 0.14)', label: 'Procurement' };
  }
  if (p.includes('audit') || p.includes('cag')) {
    return { hex: '#1B1A17', rgb: '60, 56, 50', wash: 'rgba(27, 26, 23, 0.10)', label: 'CAG & Compliance' };
  }
  if (p.includes('budget') || p.includes('fiscal')) {
    return { hex: '#138808', rgb: '95, 211, 90', wash: 'rgba(19, 136, 8, 0.12)', label: 'Budgets & Fiscal' };
  }
  if (p.includes('infra') || p.includes('work') || p.includes('road')) {
    return { hex: '#8A4200', rgb: '168, 79, 0', wash: 'rgba(138, 66, 0, 0.12)', label: 'Infrastructure' };
  }
  if (p.includes('scheme') || p.includes('welfare') || p.includes('social')) {
    return { hex: '#E06A10', rgb: '255, 184, 102', wash: 'rgba(224, 106, 16, 0.12)', label: 'Welfare & Schemes' };
  }
  if (p.includes('corp') || p.includes('politic') || p.includes('disclos') || p.includes('gov')) {
    return { hex: '#4FA83E', rgb: '150, 225, 130', wash: 'rgba(79, 168, 62, 0.12)', label: 'Disclosures' };
  }
  if (p.includes('parl')) {
    return { hex: '#A84F00', rgb: '168, 79, 0', wash: 'rgba(168, 79, 0, 0.11)', label: 'Parliament & RTI' };
  }
  if (p.includes('just') || p.includes('court') || p.includes('custody')) {
    return { hex: '#000080', rgb: '0, 0, 128', wash: 'rgba(0, 0, 128, 0.10)', label: 'Courts & Custody' };
  }
  return { hex: '#1B1A17', rgb: '60, 56, 50', wash: 'rgba(27, 26, 23, 0.10)', label: pillarLabel(pillar) };
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
  P8: {
    name: 'Sovereign Lies: launch promises vs. subsequent revisions across Union & State/UT schemes',
    rule: 'Cross-checks SHA-256-pinned official launch notifications against subsequent statutory amendments, circulars, and budget records across Indian Union and State/UT schemes — comparing original tax, payout, exit, subsidy, and eligibility commitments against walk-backs while publishing honored or grandfathered schemes as Cleared.',
  },
  P10: {
    name: 'Innocent But Locked Up: post-acquittal custody overruns & contempt compliance (2021–2026)',
    rule: 'Verifies SHA-256-pinned Supreme Court, High Court, prison, police, NHRC/SHRC, NALSA/SLSA, Parliamentary, and CAG records (2021–2026) across the 4-stage custody chain (acquittal/release order → continued prison detention → contempt/habeas intervention → verified release), while publishing timely-release compliance benchmarks as Cleared.',
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
      host.endsWith('.sansad.in') ||
      host === 'rajyasabha.digital' ||
      host.endsWith('.rajyasabha.digital') ||
      host === 'rbi.org.in' ||
      host.endsWith('.rbi.org.in') ||
      host === 'pfrda.org.in' ||
      host.endsWith('.pfrda.org.in') ||
      host === 'allahabadhighcourt.in' ||
      host.endsWith('.allahabadhighcourt.in') ||
      host === 'dslsa.org' ||
      host.endsWith('.dslsa.org') ||
      host === 'jhalsa.org' ||
      host.endsWith('.jhalsa.org') ||
      host === 'pshrc.net' ||
      host.endsWith('.pshrc.net')
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
    subsequent_walkback: 'Subsequent Walk-Back',
    promise_document: 'Original Promise Record',
    walkback_document: 'Subsequent Revision Record',
    corroborating_record: 'Corroborating Record',
    recorded_status: 'Recorded Status',
    followup_answer: 'Follow-Up Answer',
    acquittal_and_custody_order: 'Acquittal & Custody Order',
    contempt_and_release_outcome: 'Contempt & Release Outcome',
    contempt_order_document: 'Contempt Order Record',
    acquittal_order_document: 'Acquittal Order Record',
    release_or_compliance_record: 'Release / Compliance Record',
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

export type WalkbackMechanismKey =
  | 'retroactive_tax_reversal'
  | 'midstream_benefit_cut'
  | 'premature_discontinuation'
  | 'eligibility_narrowing'
  | 'lockin_or_exit_restriction'
  | 'arrears_or_unfunded_promise'
  | 'grandfathered_or_honored'
  | 'other';

export interface WalkbackMechanismMeta {
  key: WalkbackMechanismKey;
  label: string;
  shortLabel: string;
  chipClass: string;
  isCleared: boolean;
}

export function formatWalkbackMechanism(raw?: string | null): WalkbackMechanismMeta {
  const s = (raw || '').trim().toLowerCase().replace(/[\s-]+/g, '_');
  if (s === 'retroactive_tax_reversal' || s === 'p8_retroactive_tax_reversal') {
    return {
      key: 'retroactive_tax_reversal',
      label: 'Retrospective Tax Reversal',
      shortLabel: 'Tax reversal',
      chipClass: 'p8-mech p8-mech--retro-tax',
      isCleared: false,
    };
  }
  if (
    s === 'midstream_benefit_cut' ||
    s === 'mid_stream_benefit_cut' ||
    s === 'p8_midstream_benefit_cut'
  ) {
    return {
      key: 'midstream_benefit_cut',
      label: 'Mid-Stream Benefit Cut',
      shortLabel: 'Benefit cut',
      chipClass: 'p8-mech p8-mech--cut',
      isCleared: false,
    };
  }
  if (s === 'premature_discontinuation' || s === 'p8_premature_discontinuation') {
    return {
      key: 'premature_discontinuation',
      label: 'Premature Discontinuation',
      shortLabel: 'Discontinued',
      chipClass: 'p8-mech p8-mech--discontinued',
      isCleared: false,
    };
  }
  if (s === 'eligibility_narrowing' || s === 'p8_eligibility_narrowing') {
    return {
      key: 'eligibility_narrowing',
      label: 'Eligibility Narrowing',
      shortLabel: 'Eligibility narrowed',
      chipClass: 'p8-mech p8-mech--eligibility',
      isCleared: false,
    };
  }
  if (
    s === 'lockin_or_exit_restriction' ||
    s === 'lock_in_or_exit_restriction' ||
    s === 'p8_lockin_or_exit_restriction'
  ) {
    return {
      key: 'lockin_or_exit_restriction',
      label: 'Lock-In / Exit Restriction',
      shortLabel: 'Exit restriction',
      chipClass: 'p8-mech p8-mech--lockin',
      isCleared: false,
    };
  }
  if (
    s === 'arrears_or_unfunded_promise' ||
    s === 'unfunded_arrears' ||
    s === 'p8_arrears_or_unfunded_promise'
  ) {
    return {
      key: 'arrears_or_unfunded_promise',
      label: 'Unfunded Arrears',
      shortLabel: 'Unfunded arrears',
      chipClass: 'p8-mech p8-mech--arrears',
      isCleared: false,
    };
  }
  if (
    s === 'grandfathered_or_honored' ||
    s === 'grandfathered_or_honoured' ||
    s === 'honored' ||
    s === 'cleared'
  ) {
    return {
      key: 'grandfathered_or_honored',
      label: 'Grandfathered / Honored',
      shortLabel: 'Honored (Cleared)',
      chipClass: 'p8-mech p8-mech--honored',
      isCleared: true,
    };
  }
  const clean = (raw || '').trim();
  return {
    key: 'other',
    label: clean ? clean.replace(/_/g, ' ') : 'Scheme Terms Revision',
    shortLabel: clean ? clean.replace(/_/g, ' ') : 'Scheme revision',
    chipClass: 'p8-mech p8-mech--other',
    isCleared: false,
  };
}

export interface SchemeCategoryMeta {
  key: string;
  label: string;
  shortLabel: string;
}

const SCHEME_CATEGORY_MAP: Record<string, { label: string; shortLabel: string }> = {
  sovereign_bonds_small_savings: {
    label: 'Sovereign Bonds & Small Savings',
    shortLabel: 'Bonds & Savings',
  },
  pensions_retirement: {
    label: 'Pensions & Retirement',
    shortLabel: 'Pensions',
  },
  tax_exempt_instruments: {
    label: 'Tax-Exempt Instruments',
    shortLabel: 'Tax Instruments',
  },
  cash_transfers_guarantees: {
    label: 'Cash Transfers & Guarantees',
    shortLabel: 'Cash & Guarantees',
  },
  ev_housing_farm_subsidies: {
    label: 'EV, Housing & Farm Subsidies',
    shortLabel: 'EV / Housing / Farm',
  },
  scholarships_education: {
    label: 'Scholarships & Education',
    shortLabel: 'Scholarships',
  },
  industrial_export_incentives: {
    label: 'Industrial & Export Incentives',
    shortLabel: 'Industrial & Export',
  },
  welfare_health_public_services: {
    label: 'Welfare, Health & Public Services',
    shortLabel: 'Welfare & Health',
  },
};

export function formatSchemeCategory(raw?: string | null): SchemeCategoryMeta {
  const s = (raw || '').trim().toLowerCase().replace(/[\s-]+/g, '_');
  if (SCHEME_CATEGORY_MAP[s]) {
    return { key: s, ...SCHEME_CATEGORY_MAP[s] };
  }
  const clean = (raw || '').trim();
  const readable = clean ? clean.replace(/_/g, ' ') : 'Public Scheme';
  return {
    key: s || 'other',
    label: readable,
    shortLabel: readable,
  };
}

export interface RetroactiveImpactMeta {
  key: string;
  label: string;
  shortLabel: string;
  chipClass: string;
  isRetroactive: boolean;
}

export function formatRetroactiveImpact(raw?: string | null): RetroactiveImpactMeta {
  const s = (raw || '').trim().toLowerCase().replace(/[\s-]+/g, '_');
  if (s === 'retroactive_existing_holders') {
    return {
      key: 'retroactive_existing_holders',
      label: 'Retroactive on existing holders',
      shortLabel: 'Retroactive',
      chipClass: 'p8-impact p8-impact--retro',
      isRetroactive: true,
    };
  }
  if (s === 'mid_stream_active_scheme' || s === 'midstream_active_scheme') {
    return {
      key: 'mid_stream_active_scheme',
      label: 'Mid-stream on active scheme',
      shortLabel: 'Mid-stream',
      chipClass: 'p8-impact p8-impact--midstream',
      isRetroactive: false,
    };
  }
  if (s === 'prospective_discontinuation') {
    return {
      key: 'prospective_discontinuation',
      label: 'Prospective discontinuation',
      shortLabel: 'Prospective',
      chipClass: 'p8-impact p8-impact--prospective',
      isRetroactive: false,
    };
  }
  if (s === 'eligibility_narrowing') {
    return {
      key: 'eligibility_narrowing',
      label: 'Eligibility narrowed',
      shortLabel: 'Eligibility narrowed',
      chipClass: 'p8-impact p8-impact--eligibility',
      isRetroactive: false,
    };
  }
  if (s === 'unfulfilled_arrears') {
    return {
      key: 'unfulfilled_arrears',
      label: 'Unfulfilled arrears',
      shortLabel: 'Unfulfilled arrears',
      chipClass: 'p8-impact p8-impact--arrears',
      isRetroactive: false,
    };
  }
  if (s === 'grandfathered_or_honored' || s === 'grandfathered_or_honoured') {
    return {
      key: 'grandfathered_or_honored',
      label: 'Grandfathered / Honored',
      shortLabel: 'Grandfathered',
      chipClass: 'p8-impact p8-impact--honored',
      isRetroactive: false,
    };
  }
  const clean = (raw || '').trim();
  return {
    key: s || 'other',
    label: clean ? clean.replace(/_/g, ' ') : 'Documented revision',
    shortLabel: clean ? clean.replace(/_/g, ' ') : 'Revision',
    chipClass: 'p8-impact p8-impact--other',
    isRetroactive: false,
  };
}

export function formatJurisdictionLabel(
  jurisdiction?: string | null,
  stateOrUt?: string | null,
  level?: string | null,
): string {
  const lvl = (level || '').trim().toLowerCase();
  const s = (stateOrUt || '').trim();
  const j = (jurisdiction || '').trim();
  if (s) {
    if (lvl === 'ut' && !/\(ut\)$/i.test(s)) return `${s} (UT)`;
    return s;
  }
  if (/^state:/i.test(j)) return j.slice(6).trim();
  if (/^ut:/i.test(j)) {
    const utName = j.slice(3).trim();
    return /\(ut\)$/i.test(utName) ? utName : `${utName} (UT)`;
  }
  return j || 'Union of India';
}

export function formatElapsedPromiseSpan(days?: number | null): string | null {
  if (days == null || !Number.isFinite(days) || days < 0) return null;
  const rounded = Math.round(days);
  if (rounded >= 365) {
    const yrs = (rounded / 365.25).toFixed(1);
    return `${rounded.toLocaleString('en-IN')} days (~${yrs} yrs)`;
  }
  return `${rounded.toLocaleString('en-IN')} ${rounded === 1 ? 'day' : 'days'}`;
}

export interface ParsedSovereignLiesMetrics {
  caseKey: string | null;
  schemeName: string | null;
  schemeSlug: string | null;
  trancheOrPhase: string | null;
  jurisdiction: string | null;
  jurisdictionLevel: string | null;
  stateOrUt: string | null;
  authority: string | null;
  authorityKey: string | null;
  schemeCategory: string | null;
  governmentEraOrLaunchYear: string | null;
  launchYear: string | null;
  walkbackYear: string | null;
  promiseDate: string | null;
  promiseSummary: string | null;
  promiseDocumentRef: string | null;
  promiseExcerpt: string | null;
  promiseLocator: string | null;
  promiseTokensMatched: string[];
  walkbackDate: string | null;
  walkbackSummary: string | null;
  walkbackDocumentRef: string | null;
  walkbackExcerpt: string | null;
  walkbackLocator: string | null;
  walkbackTokensMatched: string[];
  daysFromPromiseToWalkback: number | null;
  walkbackMechanism: string | null;
  retroactiveImpact: string | null;
  signalCategory: string | null;
  verdict: string | null;
  triggeredRules: string[];
  promisedMetric: string | null;
  revisedMetric: string | null;
  metricUnit: string | null;
  affectedGroup: string | null;
  estimatedExposureInr: number | null;
  exposureBasis: string | null;
  statutoryAnchor: string | null;
  secondaryRole: string | null;
  isSgbAnchor: boolean;
  isAnchorCase: boolean;
  asOfDate: string | null;
  sourceLocator: string | null;
}

const P8_MECHANISM_SET = new Set([
  'retroactive_tax_reversal',
  'midstream_benefit_cut',
  'premature_discontinuation',
  'eligibility_narrowing',
  'lockin_or_exit_restriction',
  'arrears_or_unfunded_promise',
  'grandfathered_or_honored',
  'p8_retroactive_tax_reversal',
  'p8_midstream_benefit_cut',
  'p8_premature_discontinuation',
  'p8_eligibility_narrowing',
  'p8_lockin_or_exit_restriction',
  'p8_arrears_or_unfunded_promise',
]);

export function extractSovereignLiesMetrics(
  metrics?: Record<string, unknown> | null,
): ParsedSovereignLiesMetrics | null {
  if (!metrics || typeof metrics !== 'object') return null;

  const schemeName = metricStr(metrics, 'scheme_name');
  const walkbackMechanism = metricStr(metrics, 'walkback_mechanism');
  const promiseSummary = metricStr(metrics, 'promise_summary');
  const walkbackSummary = metricStr(metrics, 'walkback_summary');
  const promisedMetric = metricStr(metrics, 'promised_metric');
  const revisedMetric = metricStr(metrics, 'revised_metric');
  const retroactiveImpact = metricStr(metrics, 'retroactive_impact');
  const signalCategory = metricStr(metrics, 'signal_category');

  const normSignal = (signalCategory || '').toLowerCase().replace(/[\s-]+/g, '_');
  const hasP8Signal = Boolean(normSignal && P8_MECHANISM_SET.has(normSignal));

  if (
    !schemeName &&
    !walkbackMechanism &&
    !promiseSummary &&
    !walkbackSummary &&
    !promisedMetric &&
    !revisedMetric &&
    !retroactiveImpact &&
    !hasP8Signal
  ) {
    return null;
  }

  return {
    caseKey: metricStr(metrics, 'case_key'),
    schemeName,
    schemeSlug: metricStr(metrics, 'scheme_slug'),
    trancheOrPhase: metricStr(metrics, 'tranche_or_phase'),
    jurisdiction: metricStr(metrics, 'jurisdiction'),
    jurisdictionLevel: metricStr(metrics, 'jurisdiction_level'),
    stateOrUt: metricStr(metrics, 'state_or_ut'),
    authority: metricStr(metrics, 'authority'),
    authorityKey: metricStr(metrics, 'authority_key'),
    schemeCategory: metricStr(metrics, 'scheme_category'),
    governmentEraOrLaunchYear: metricStr(metrics, 'government_era_or_launch_year'),
    launchYear: metricStr(metrics, 'launch_year'),
    walkbackYear: metricStr(metrics, 'walkback_year'),
    promiseDate: metricStr(metrics, 'promise_date'),
    promiseSummary,
    promiseDocumentRef: metricStr(metrics, 'promise_document_ref'),
    promiseExcerpt: metricStr(metrics, 'promise_excerpt'),
    promiseLocator: metricStr(metrics, 'promise_locator'),
    promiseTokensMatched: metricStrList(metrics, 'promise_tokens_matched'),
    walkbackDate: metricStr(metrics, 'walkback_date'),
    walkbackSummary,
    walkbackDocumentRef: metricStr(metrics, 'walkback_document_ref'),
    walkbackExcerpt: metricStr(metrics, 'walkback_excerpt'),
    walkbackLocator: metricStr(metrics, 'walkback_locator'),
    walkbackTokensMatched: metricStrList(metrics, 'walkback_tokens_matched'),
    daysFromPromiseToWalkback: metricNum(metrics, 'days_from_promise_to_walkback'),
    walkbackMechanism,
    retroactiveImpact,
    signalCategory,
    verdict: metricStr(metrics, 'verdict'),
    triggeredRules: metricStrList(metrics, 'triggered_rules'),
    promisedMetric,
    revisedMetric,
    metricUnit: metricStr(metrics, 'metric_unit'),
    affectedGroup: metricStr(metrics, 'affected_group'),
    estimatedExposureInr: metricNum(metrics, 'estimated_exposure_inr'),
    exposureBasis: metricStr(metrics, 'exposure_basis'),
    statutoryAnchor: metricStr(metrics, 'statutory_anchor'),
    secondaryRole: metricStr(metrics, 'secondary_role'),
    isSgbAnchor: metricBool(metrics, 'is_sgb_anchor'),
    isAnchorCase: metricBool(metrics, 'is_anchor_case'),
    asOfDate: metricStr(metrics, 'as_of_date'),
    sourceLocator: metricStr(metrics, 'source_locator'),
  };
}

export type P10MechanismKey =
  | 'post_acquittal_contempt_overrun'
  | 'bail_or_release_order_contempt'
  | 'warrant_or_faster_transmission_delay'
  | 'statutory_commission_detention_finding'
  | 'institutional_compliance_gap'
  | 'timely_release_or_compliant'
  | 'other';

export interface P10MechanismMeta {
  key: P10MechanismKey;
  label: string;
  shortLabel: string;
  chipClass: string;
  isCleared: boolean;
}

export function formatCustodyOverrunMechanism(raw?: string | null): P10MechanismMeta {
  const s = (raw || '').trim().toLowerCase().replace(/[\s-]+/g, '_');
  if (
    s === 'post_acquittal_contempt_overrun' ||
    s === 'p10_post_acquittal_contempt_overrun'
  ) {
    return {
      key: 'post_acquittal_contempt_overrun',
      label: 'Post-Acquittal Contempt Overrun',
      shortLabel: 'Acquittal + contempt',
      chipClass: 'p10-mech p10-mech--acquittal-contempt',
      isCleared: false,
    };
  }
  if (
    s === 'bail_or_release_order_contempt' ||
    s === 'p10_release_order_contempt_overrun' ||
    s === 'release_order_contempt_overrun'
  ) {
    return {
      key: 'bail_or_release_order_contempt',
      label: 'Release Order Contempt Overrun',
      shortLabel: 'Release contempt',
      chipClass: 'p10-mech p10-mech--release-contempt',
      isCleared: false,
    };
  }
  if (
    s === 'warrant_or_faster_transmission_delay' ||
    s === 'p10_warrant_transmission_delay' ||
    s === 'warrant_transmission_delay'
  ) {
    return {
      key: 'warrant_or_faster_transmission_delay',
      label: 'Warrant / FASTER Transmission Delay',
      shortLabel: 'FASTER / warrant delay',
      chipClass: 'p10-mech p10-mech--transmission',
      isCleared: false,
    };
  }
  if (
    s === 'statutory_commission_detention_finding' ||
    s === 'p10_statutory_unlawful_detention' ||
    s === 'statutory_unlawful_detention'
  ) {
    return {
      key: 'statutory_commission_detention_finding',
      label: 'NHRC / SHRC Detention Finding',
      shortLabel: 'Commission finding',
      chipClass: 'p10-mech p10-mech--commission',
      isCleared: false,
    };
  }
  if (
    s === 'institutional_compliance_gap' ||
    s === 'p10_institutional_compliance_gap'
  ) {
    return {
      key: 'institutional_compliance_gap',
      label: 'Institutional Compliance Gap',
      shortLabel: 'Compliance gap',
      chipClass: 'p10-mech p10-mech--compliance',
      isCleared: false,
    };
  }
  if (
    s === 'timely_release_or_compliant' ||
    s === 'compliant_control' ||
    s === 'cleared'
  ) {
    return {
      key: 'timely_release_or_compliant',
      label: 'Timely Release / Compliant',
      shortLabel: 'Compliant (Cleared)',
      chipClass: 'p10-mech p10-mech--compliant',
      isCleared: true,
    };
  }
  const clean = (raw || '').trim();
  return {
    key: 'other',
    label: clean ? clean.replace(/_/g, ' ') : 'Custody Compliance Record',
    shortLabel: clean ? clean.replace(/_/g, ' ') : 'Custody record',
    chipClass: 'p10-mech p10-mech--other',
    isCleared: false,
  };
}

export interface P10PortalCategoryMeta {
  key: string;
  label: string;
  shortLabel: string;
  chipClass: string;
}

const P10_PORTAL_CATEGORY_MAP: Record<
  string,
  { label: string; shortLabel: string; chipClass: string }
> = {
  court_portal: {
    label: 'Court Portal',
    shortLabel: 'Court Portal',
    chipClass: 'p10-portal p10-portal--court',
  },
  prison_portal: {
    label: 'Prison Portal',
    shortLabel: 'Prison Portal',
    chipClass: 'p10-portal p10-portal--prison',
  },
  police_portal: {
    label: 'Police Portal',
    shortLabel: 'Police Portal',
    chipClass: 'p10-portal p10-portal--police',
  },
  nhrc_shrc: {
    label: 'NHRC / SHRC',
    shortLabel: 'NHRC/SHRC',
    chipClass: 'p10-portal p10-portal--nhrc',
  },
  nalsa_slsa: {
    label: 'NALSA / SLSA',
    shortLabel: 'NALSA/SLSA',
    chipClass: 'p10-portal p10-portal--nalsa',
  },
  parliament: {
    label: 'Parliament (Digital Sansad)',
    shortLabel: 'Parliament',
    chipClass: 'p10-portal p10-portal--parliament',
  },
  govt_ministry: {
    label: 'Govt Ministry (MHA / DoJ / PIB)',
    shortLabel: 'Govt Ministry',
    chipClass: 'p10-portal p10-portal--govt',
  },
  cag_audit: {
    label: 'CAG Audit',
    shortLabel: 'CAG Audit',
    chipClass: 'p10-portal p10-portal--cag',
  },
};

export function formatPortalCategory(raw?: string | null): P10PortalCategoryMeta {
  const s = (raw || '').trim().toLowerCase().replace(/[\s-]+/g, '_');
  if (P10_PORTAL_CATEGORY_MAP[s]) {
    return { key: s, ...P10_PORTAL_CATEGORY_MAP[s] };
  }
  const clean = (raw || '').trim();
  const readable = clean ? clean.replace(/_/g, ' ') : 'Official Portal';
  return {
    key: s || 'other',
    label: readable,
    shortLabel: readable,
    chipClass: 'p10-portal p10-portal--other',
  };
}

export interface P10CourtTierMeta {
  key: string;
  label: string;
  shortLabel: string;
  chipClass: string;
}

export function formatCourtTier(raw?: string | null): P10CourtTierMeta {
  const s = (raw || '').trim().toLowerCase().replace(/[\s-]+/g, '_');
  if (s === 'supreme_court') {
    return {
      key: 'supreme_court',
      label: 'Supreme Court of India',
      shortLabel: 'Supreme Court',
      chipClass: 'p10-tier p10-tier--sc',
    };
  }
  if (s === 'high_court') {
    return {
      key: 'high_court',
      label: 'High Court',
      shortLabel: 'High Court',
      chipClass: 'p10-tier p10-tier--hc',
    };
  }
  if (s === 'district_court') {
    return {
      key: 'district_court',
      label: 'District / Sessions Court',
      shortLabel: 'District Court',
      chipClass: 'p10-tier p10-tier--dc',
    };
  }
  if (s === 'statutory_body') {
    return {
      key: 'statutory_body',
      label: 'Statutory / Executive Body',
      shortLabel: 'Statutory Body',
      chipClass: 'p10-tier p10-tier--stat',
    };
  }
  const clean = (raw || '').trim();
  const readable = clean ? clean.replace(/_/g, ' ') : 'Judicial / Statutory Body';
  return {
    key: s || 'other',
    label: readable,
    shortLabel: readable,
    chipClass: 'p10-tier p10-tier--other',
  };
}

export interface P10ChainStatusMeta {
  key: string;
  label: string;
  shortLabel: string;
  chipClass: string;
}

export function formatChainStatus(
  raw?: string | null,
  stagesCount?: number | null,
): P10ChainStatusMeta {
  const s = (raw || '').trim().toLowerCase().replace(/[\s-]+/g, '_');
  if (s === 'full_4_stage_chain') {
    return {
      key: 'full_4_stage_chain',
      label: 'Full 4-Stage Chain Verified',
      shortLabel: '4/4 stages',
      chipClass: 'p10-chain p10-chain--full',
    };
  }
  if (s === 'partial_3_stage') {
    return {
      key: 'partial_3_stage',
      label: '3-Stage Custody Chain',
      shortLabel: stagesCount != null ? `${stagesCount}/4 stages` : '3/4 stages',
      chipClass: 'p10-chain p10-chain--partial',
    };
  }
  if (s === 'institutional_record') {
    return {
      key: 'institutional_record',
      label: 'Institutional Compliance Record',
      shortLabel: 'Institutional',
      chipClass: 'p10-chain p10-chain--institutional',
    };
  }
  if (s === 'compliant_control') {
    return {
      key: 'compliant_control',
      label: 'Timely Release Benchmark',
      shortLabel: 'Compliant',
      chipClass: 'p10-chain p10-chain--compliant',
    };
  }
  if (stagesCount != null && stagesCount >= 4) {
    return {
      key: 'full_4_stage_chain',
      label: 'Full 4-Stage Chain Verified',
      shortLabel: '4/4 stages',
      chipClass: 'p10-chain p10-chain--full',
    };
  }
  if (stagesCount != null && stagesCount === 3) {
    return {
      key: 'partial_3_stage',
      label: '3-Stage Custody Chain',
      shortLabel: '3/4 stages',
      chipClass: 'p10-chain p10-chain--partial',
    };
  }
  const clean = (raw || '').trim();
  return {
    key: s || 'institutional_record',
    label: clean ? clean.replace(/_/g, ' ') : 'Institutional Record',
    shortLabel: stagesCount != null ? `${stagesCount}/4 stages` : 'Institutional',
    chipClass: 'p10-chain p10-chain--institutional',
  };
}

export function formatAcquittalOrderType(raw?: string | null): string {
  const s = (raw || '').trim().toLowerCase().replace(/[\s-]+/g, '_');
  const map: Record<string, string> = {
    acquittal: 'Acquittal Order',
    discharge: 'Discharge Order',
    detention_quashed: 'Detention Quashed',
    bail_release_order: 'Bail / Release Order',
    conviction_set_aside: 'Conviction Set Aside',
    institutional_compliance_review: 'Institutional Compliance Review',
  };
  if (map[s]) return map[s];
  const clean = (raw || '').trim();
  return clean ? clean.replace(/_/g, ' ') : 'Release Order';
}

export function formatOverrunDays(days?: number | null): string | null {
  if (days == null || !Number.isFinite(days) || days < 0) return null;
  const rounded = Math.round(days);
  if (rounded >= 365) {
    const yrs = (rounded / 365.25).toFixed(1);
    return `${rounded.toLocaleString('en-IN')} days (~${yrs} yrs)`;
  }
  return `${rounded.toLocaleString('en-IN')} ${rounded === 1 ? 'day' : 'days'}`;
}

export interface ParsedInnocentButLockedUpMetrics {
  caseKey: string | null;
  caseTitle: string | null;
  portalCategory: string | null;
  courtTier: string | null;
  stateUt: string | null;
  jurisdiction: string | null;
  year: number | null;
  documentDate: string | null;
  documentRef: string | null;
  overrunMechanism: string | null;
  signalCategory: string | null;
  verdict: string | null;
  triggeredRules: string[];
  stagesVerifiedCount: number | null;
  chainStatus: string | null;
  stage1AcquittalVerified: boolean;
  acquittalOrderDate: string | null;
  acquittalCourt: string | null;
  acquittalCaseNumber: string | null;
  acquittalOrderType: string | null;
  acquittalQuote: string | null;
  stage2ContinuedDetentionVerified: boolean;
  prisonName: string | null;
  custodyAuthority: string | null;
  nonReleaseReason: string | null;
  overrunDays: number | null;
  overrunWindow: string | null;
  stage3ContemptVerified: boolean;
  contemptCaseNumber: string | null;
  contemptCourt: string | null;
  contemptFilingDate: string | null;
  contemptOrderDate: string | null;
  contemptQuote: string | null;
  stage4ReleaseVerified: boolean;
  releaseDate: string | null;
  releaseVerified: boolean;
  compensationInr: number | null;
  judicialDirectionSummary: string | null;
  custodyOrderExcerpt: string | null;
  custodyOrderLocator: string | null;
  custodyOrderTokensMatched: string[];
  contemptOrOutcomeExcerpt: string | null;
  contemptOrOutcomeLocator: string | null;
  contemptOrOutcomeTokensMatched: string[];
  statutoryAnchor: string | null;
  secondaryRole: string | null;
  isAnchorCase: boolean;
  asOfDate: string | null;
  sourceLocator: string | null;
}

const P10_MECHANISM_SET = new Set([
  'post_acquittal_contempt_overrun',
  'bail_or_release_order_contempt',
  'warrant_or_faster_transmission_delay',
  'statutory_commission_detention_finding',
  'institutional_compliance_gap',
  'timely_release_or_compliant',
  'p10_post_acquittal_contempt_overrun',
  'p10_release_order_contempt_overrun',
  'p10_warrant_transmission_delay',
  'p10_statutory_unlawful_detention',
  'p10_institutional_compliance_gap',
]);

export function extractInnocentButLockedUpMetrics(
  metrics?: Record<string, unknown> | null,
): ParsedInnocentButLockedUpMetrics | null {
  if (!metrics || typeof metrics !== 'object') return null;

  const overrunMechanism = metricStr(metrics, 'overrun_mechanism');
  const portalCategory = metricStr(metrics, 'portal_category');
  const acquittalCourt = metricStr(metrics, 'acquittal_court');
  const acquittalCaseNumber = metricStr(metrics, 'acquittal_case_number');
  const contemptCaseNumber = metricStr(metrics, 'contempt_case_number');
  const prisonName = metricStr(metrics, 'prison_name');
  const chainStatus = metricStr(metrics, 'chain_status');
  const signalCategory = metricStr(metrics, 'signal_category');
  const overrunDays = metricNum(metrics, 'overrun_days');

  const normSignal = (signalCategory || '').toLowerCase().replace(/[\s-]+/g, '_');
  const hasP10Signal = Boolean(normSignal && P10_MECHANISM_SET.has(normSignal));

  if (
    !overrunMechanism &&
    !portalCategory &&
    !acquittalCourt &&
    !acquittalCaseNumber &&
    !contemptCaseNumber &&
    !prisonName &&
    !chainStatus &&
    overrunDays === null &&
    !hasP10Signal
  ) {
    return null;
  }

  const stage1 = metricBool(metrics, 'stage_1_acquittal_verified');
  const stage2 = metricBool(metrics, 'stage_2_continued_detention_verified');
  const stage3 = metricBool(metrics, 'stage_3_contempt_verified');
  const releaseVerified = metricBool(metrics, 'release_verified');
  const stage4 =
    metrics.stage_4_release_verified !== undefined
      ? metricBool(metrics, 'stage_4_release_verified')
      : releaseVerified;
  const computedStages = [stage1, stage2, stage3, stage4].filter(Boolean).length;
  const rawStagesCount = metricNum(metrics, 'stages_verified_count');

  return {
    caseKey: metricStr(metrics, 'case_key'),
    caseTitle: metricStr(metrics, 'case_title'),
    portalCategory,
    courtTier: metricStr(metrics, 'court_tier'),
    stateUt: metricStr(metrics, 'state_ut'),
    jurisdiction: metricStr(metrics, 'jurisdiction'),
    year: metricNum(metrics, 'year'),
    documentDate: metricStr(metrics, 'document_date'),
    documentRef: metricStr(metrics, 'document_ref'),
    overrunMechanism,
    signalCategory,
    verdict: metricStr(metrics, 'verdict'),
    triggeredRules: metricStrList(metrics, 'triggered_rules'),
    stagesVerifiedCount: rawStagesCount ?? (computedStages > 0 ? computedStages : null),
    chainStatus,
    stage1AcquittalVerified: stage1,
    acquittalOrderDate: metricStr(metrics, 'acquittal_order_date'),
    acquittalCourt,
    acquittalCaseNumber,
    acquittalOrderType: metricStr(metrics, 'acquittal_order_type'),
    acquittalQuote: metricStr(metrics, 'acquittal_quote'),
    stage2ContinuedDetentionVerified: stage2,
    prisonName,
    custodyAuthority: metricStr(metrics, 'custody_authority'),
    nonReleaseReason: metricStr(metrics, 'non_release_reason'),
    overrunDays,
    overrunWindow: metricStr(metrics, 'overrun_window'),
    stage3ContemptVerified: stage3,
    contemptCaseNumber,
    contemptCourt: metricStr(metrics, 'contempt_court'),
    contemptFilingDate: metricStr(metrics, 'contempt_filing_date'),
    contemptOrderDate: metricStr(metrics, 'contempt_order_date'),
    contemptQuote: metricStr(metrics, 'contempt_quote'),
    stage4ReleaseVerified: stage4,
    releaseDate: metricStr(metrics, 'release_date'),
    releaseVerified: releaseVerified || stage4,
    compensationInr: metricNum(metrics, 'compensation_inr'),
    judicialDirectionSummary: metricStr(metrics, 'judicial_direction_summary'),
    custodyOrderExcerpt: metricStr(metrics, 'custody_order_excerpt'),
    custodyOrderLocator: metricStr(metrics, 'custody_order_locator'),
    custodyOrderTokensMatched: metricStrList(metrics, 'custody_order_tokens_matched'),
    contemptOrOutcomeExcerpt: metricStr(metrics, 'contempt_or_outcome_excerpt'),
    contemptOrOutcomeLocator: metricStr(metrics, 'contempt_or_outcome_locator'),
    contemptOrOutcomeTokensMatched: metricStrList(metrics, 'contempt_or_outcome_tokens_matched'),
    statutoryAnchor: metricStr(metrics, 'statutory_anchor'),
    secondaryRole: metricStr(metrics, 'secondary_role'),
    isAnchorCase: metricBool(metrics, 'is_anchor_case'),
    asOfDate: metricStr(metrics, 'as_of_date'),
    sourceLocator: metricStr(metrics, 'source_locator'),
  };
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
 * For P8 findings, officially published scheme exposure takes precedence when > 0, else shows
 * the walk-back mechanism and elapsed days (never a misleading ₹0 for non-monetary rule changes).
 * Non-P6/P7/P8 findings retain their exact existing behavior.
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

  if (finding.methodology_code === 'P8' && !isQuarantined) {
    const p8Record = extractSovereignLiesMetrics(finding.anomaly_metrics);
    const publishedVal =
      finding.monetary_exposure_inr != null && finding.monetary_exposure_inr > 0
        ? finding.monetary_exposure_inr
        : p8Record?.estimatedExposureInr != null && p8Record.estimatedExposureInr > 0
          ? p8Record.estimatedExposureInr
          : null;
    if (publishedVal != null && publishedVal > 0) {
      return {
        label: 'Published Scheme Exposure',
        value: formatInr(publishedVal),
      };
    }
    const mechKey = p8Record?.walkbackMechanism || p8Record?.signalCategory;
    if (mechKey) {
      const mech = formatWalkbackMechanism(mechKey);
      return {
        label: mech.isCleared ? 'Scheme Status' : 'Walk-Back Mechanism',
        value: `${mech.shortLabel}${
          p8Record?.daysFromPromiseToWalkback != null
            ? ` · ${p8Record.daysFromPromiseToWalkback.toLocaleString('en-IN')}d`
            : ''
        }`,
      };
    }
    return {
      label: 'Scheme Terms Check',
      value: 'Documented',
    };
  }

  if (finding.methodology_code === 'P10' && !isQuarantined) {
    const p10Record = extractInnocentButLockedUpMetrics(finding.anomaly_metrics);
    const compVal =
      p10Record?.compensationInr != null && p10Record.compensationInr > 0
        ? p10Record.compensationInr
        : finding.monetary_exposure_inr != null && finding.monetary_exposure_inr > 0
          ? finding.monetary_exposure_inr
          : null;
    if (p10Record?.overrunDays != null && p10Record.overrunDays > 0) {
      const daysLabel = `+${p10Record.overrunDays.toLocaleString('en-IN')} ${
        p10Record.overrunDays === 1 ? 'day' : 'days'
      }`;
      return {
        label: 'Custody Overrun',
        value: compVal != null ? `${daysLabel} · ${formatInr(compVal)}` : daysLabel,
      };
    }
    if (compVal != null && compVal > 0) {
      return {
        label: 'Ordered Compensation',
        value: formatInr(compVal),
      };
    }
    const mechKey = p10Record?.overrunMechanism || p10Record?.signalCategory;
    if (mechKey) {
      const mech = formatCustodyOverrunMechanism(mechKey);
      const chain = p10Record?.chainStatus || p10Record?.stagesVerifiedCount != null
        ? formatChainStatus(p10Record?.chainStatus, p10Record?.stagesVerifiedCount)
        : null;
      return {
        label: mech.isCleared ? 'Release Compliance' : 'Custody Audit Pattern',
        value:
          mech.isCleared
            ? mech.shortLabel
            : chain
              ? `${mech.shortLabel} · ${chain.shortLabel}`
              : mech.shortLabel,
      };
    }
    return {
      label: 'Custody Compliance Check',
      value: 'Documented',
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
 * For P8 findings, officially published exposure takes precedence when > 0, else shows a
 * compact metric delta or walk-back mechanism + elapsed days.
 * Non-P6/P7/P8 findings remain completely unchanged.
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

  if (finding.methodology_code === 'P8') {
    const p8Record = extractSovereignLiesMetrics(finding.anomaly_metrics);
    const publishedVal =
      finding.monetary_exposure_inr != null && finding.monetary_exposure_inr > 0
        ? finding.monetary_exposure_inr
        : p8Record?.estimatedExposureInr != null && p8Record.estimatedExposureInr > 0
          ? p8Record.estimatedExposureInr
          : null;
    if (publishedVal != null && publishedVal > 0) {
      return { value: formatInr(publishedVal), prominent: true };
    }
    if (p8Record?.promisedMetric && p8Record?.revisedMetric) {
      const compactDelta = `${p8Record.promisedMetric} → ${p8Record.revisedMetric}`;
      if (compactDelta.length <= 36) {
        return { value: compactDelta, prominent: true };
      }
    }
    const mechKey = p8Record?.walkbackMechanism || p8Record?.signalCategory;
    if (mechKey) {
      const mech = formatWalkbackMechanism(mechKey);
      return {
        value: `${mech.shortLabel}${
          p8Record?.daysFromPromiseToWalkback != null
            ? ` · ${p8Record.daysFromPromiseToWalkback.toLocaleString('en-IN')}d`
            : ''
        }`,
        prominent: true,
      };
    }
    return { value: 'Scheme audit lead', prominent: false };
  }

  if (finding.methodology_code === 'P10') {
    const p10Record = extractInnocentButLockedUpMetrics(finding.anomaly_metrics);
    const compVal =
      p10Record?.compensationInr != null && p10Record.compensationInr > 0
        ? p10Record.compensationInr
        : finding.monetary_exposure_inr != null && finding.monetary_exposure_inr > 0
          ? finding.monetary_exposure_inr
          : null;
    if (p10Record?.overrunDays != null && p10Record.overrunDays > 0) {
      const base = `+${p10Record.overrunDays.toLocaleString('en-IN')}d overrun`;
      return {
        value: compVal != null ? `${base} · ${formatInr(compVal)}` : base,
        prominent: true,
      };
    }
    if (compVal != null && compVal > 0) {
      return { value: formatInr(compVal), prominent: true };
    }
    const mechKey = p10Record?.overrunMechanism || p10Record?.signalCategory;
    if (mechKey) {
      const mech = formatCustodyOverrunMechanism(mechKey);
      if (mech.isCleared) {
        return { value: mech.shortLabel, prominent: true };
      }
      const chain = p10Record?.chainStatus || p10Record?.stagesVerifiedCount != null
        ? formatChainStatus(p10Record?.chainStatus, p10Record?.stagesVerifiedCount)
        : null;
      return {
        value: chain ? `${chain.shortLabel} · ${mech.shortLabel}` : mech.shortLabel,
        prominent: true,
      };
    }
    return { value: 'Custody audit lead', prominent: false };
  }

  if (finding.monetary_exposure_inr) {
    return { value: formatInr(finding.monetary_exposure_inr), prominent: true };
  }

  return {
    value: finding.amount_label || 'Disclosure lead',
    prominent: false,
  };
}
