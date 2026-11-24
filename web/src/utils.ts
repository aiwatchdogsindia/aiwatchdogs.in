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
  switch (status) {
    case 'verified':
      return {
        label: 'Peer-Verified',
        shortLabel: 'Verified',
        badgeClass: 'badge badge-verified',
        description: 'Reproduced deterministically and independently confirmed on all four checks by ≥2 distinct contributor nodes.',
      };
    case 'peer_review':
      return {
        label: 'In Peer Review',
        shortLabel: 'In review',
        badgeClass: 'badge badge-review',
        description: 'Unverified lead awaiting independent reproduction and 2-of-2 peer verification quorum.',
      };
    case 'needs_adjudication':
      return {
        label: 'Split Vote · Adjudication',
        shortLabel: 'Adjudication',
        badgeClass: 'badge badge-review',
        description: 'Peer verifiers recorded a split verdict; queued for tie-breaking independent adjudication.',
      };
    case 'under_review':
    case 'under_editorial_review':
    case 'editorial_review':
      return {
        label: 'Under Editorial Review',
        shortLabel: 'Editorial review',
        badgeClass: 'badge badge-disputed',
        description: 'Held from verified status pending named editorial review and source reconciliation.',
      };
    case 'corrected':
      return {
        label: 'Corrected / Revised',
        shortLabel: 'Corrected',
        badgeClass: 'badge badge-disputed',
        description: 'Updated with a permanent editorial correction record explaining the source discrepancy.',
      };
    case 'quarantined':
      return {
        label: 'Quarantined · Privacy/Legal Review',
        shortLabel: 'Quarantined',
        badgeClass: 'badge badge-disputed',
        description: 'Details withheld from public view pending privacy or legal compliance review.',
      };
    case 'unevaluable':
      return {
        label: 'Unevaluable · Missing Fields',
        shortLabel: 'Unevaluable',
        badgeClass: 'badge badge-cleared',
        description: 'Source rows lacked required denominator/field values to evaluate statutory thresholds.',
      };
    case 'raw_lead':
      return {
        label: 'Unverified Lead',
        shortLabel: 'Open lead',
        badgeClass: 'badge badge-review',
        description: 'Initial deterministic extraction — not yet peer-verified.',
      };
    case 'cleared':
      return {
        label: 'Cleared & Explained',
        shortLabel: 'Cleared',
        badgeClass: 'badge badge-cleared',
        description: 'Audited against statutory thresholds and found within lawful parameters.',
      };
    case 'resolved_explained':
      return {
        label: 'Cleared & Explained',
        shortLabel: 'Explained',
        badgeClass: 'badge badge-cleared',
        description: 'Official clarification or lawful waiver verified and logged on the public record.',
      };
    case 'disputed':
      return {
        label: 'Disputed',
        shortLabel: 'Disputed',
        badgeClass: 'badge badge-disputed',
        description: 'Peer verifiers recorded a factual or rule disagreement; held from verified ledger.',
      };
    case 'retracted':
    case 'retracted_false_positive':
      return {
        label: 'Retracted',
        shortLabel: 'Retracted',
        badgeClass: 'badge badge-disputed',
        description: 'Refuted on independent verification and preserved transparently in the correction log.',
      };
    case 'rti_filed':
      return {
        label: 'RTI Filed',
        shortLabel: 'RTI filed',
        badgeClass: 'badge badge-rti',
        description: 'Citizen-filed RTI Act 2005 Sec 6(1) application logged for official public records.',
      };
    case 'official_response_logged':
      return {
        label: 'Official Response',
        shortLabel: 'Response',
        badgeClass: 'badge badge-rti',
        description: 'Public authority response received and attached to the evidence dossier.',
      };
    default:
      return {
        label: status.replace(/_/g, ' '),
        shortLabel: status.replace(/_/g, ' '),
        badgeClass: 'badge badge-review',
        description: 'Public audit record status.',
      };
  }
}

export function providerBadgeClass(provider?: string): string {
  const p = (provider || '').toLowerCase();
  if (p.includes('rule') || p.includes('cpu') || p.includes('deterministic')) {
    return 'tag-provider cpu';
  }
  if (p.includes('ollama') || p.includes('local') || p.includes('vllm')) {
    return 'tag-provider local';
  }
  return 'tag-provider';
}

export function pillarLabel(pillar?: string): string {
  const map: Record<string, string> = {
    procurement: 'Procurement & Tenders',
    audit: 'CAG & PAC Audits',
    budget: 'Union & State Budgets',
    scheme: 'Welfare & Social Audits',
    infrastructure: 'Infrastructure & Works',
    corporate_political: 'Disclosures & Governance',
    parliament: 'Parliament & RTI',
  };
  return map[(pillar || '').toLowerCase()] || pillar || 'Public Audit';
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
  M1: { name: 'Single-Bidder & Spec-Lock', rule: 'GFR Rule 166 · CVC Single-Tender (>35% share or <14d window)' },
  M2: { name: 'Split-Tendering / Threshold Hugging', rule: 'GFR Rules 149/154/155/161 (85–99.9% of ₹50k/₹2.5L/₹10L/₹50L)' },
  M3: { name: 'Repeat-Winner Concentration', rule: 'CCI Bid-Rigging Indicators (HHI > 0.35 or >40% share)' },
  M4: { name: 'Unit-Price Outlier & Escalation', rule: 'GeM/BOQ Benchmark (>2.5 MAD or >1.75× median / >20% variation)' },
  M5: { name: 'March Rush & Budget Variance', rule: 'GFR Rule 62(3) · MoF CMS (Q4 >33% or March >15% spend)' },
  M6: { name: 'CAG Unresolved Audit Paras', rule: 'CAG Reports & PAC ATN/ATR Multi-Year Quantification' },
  M7: { name: 'Scheme Physical-Financial Gap', rule: 'PMGSY OMMAS · MPLADS e-SAKSHI · MGNREGA 60:40 & R9.2' },
  M8: { name: 'Corporate–Procurement Proximity', rule: 'MCA21 + ECI + Tenders (<180d vendor / shared directors — Disclosure Lead)' },
};

export function getCanonicalOrigin(): string {
  if (typeof window !== 'undefined' && window.location && window.location.origin) {
    return window.location.origin.replace(/\/+$/, '');
  }
  return 'https://aiwatchdogs.in';
}

export function buildCanonicalJoinPrompt(origin: string = getCanonicalOrigin()): string {
  return `Fetch and follow ${origin}/join.txt to run a civic-audit agent with me`;
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
  if (!url) return false;
  try {
    const host = new URL(url).hostname.toLowerCase();
    return host.endsWith('.gov.in') || host.endsWith('.nic.in') || host.endsWith('.org.in') && host.includes('cag');
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
