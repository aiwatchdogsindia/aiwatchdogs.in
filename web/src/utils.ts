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
};

const METHODOLOGY_PROJECT_CODES: Record<string, string> = {
  P1: 'P1',
  W1: 'P2',
  UB1: 'P3',
  OR1: 'P4',
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
      host.endsWith('.cag.org.in')
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
