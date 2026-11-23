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
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay < 30) return `${diffDay}d ago`;
  return new Date(parsed).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
}

export function statusMeta(status: EpistemicStatus): {
  label: string;
  badgeClass: string;
  description: string;
} {
  switch (status) {
    case 'verified':
      return {
        label: 'Peer-Verified',
        badgeClass: 'badge badge-verified',
        description: 'Reproduced deterministically and independently confirmed by ≥2 distinct contributor nodes.',
      };
    case 'peer_review':
      return {
        label: 'In Peer Review',
        badgeClass: 'badge badge-review',
        description: 'Unverified lead awaiting independent reproduction and 2-of-2 peer verification quorum.',
      };
    case 'raw_lead':
      return {
        label: 'Unverified Lead',
        badgeClass: 'badge badge-review',
        description: 'Initial deterministic extraction — not yet peer-verified.',
      };
    case 'cleared':
      return {
        label: 'Cleared',
        badgeClass: 'badge badge-cleared',
        description: 'Audited against statutory thresholds and found within lawful parameters.',
      };
    case 'resolved_explained':
      return {
        label: 'Resolved / Explained',
        badgeClass: 'badge badge-cleared',
        description: 'Official clarification or lawful waiver verified and logged on the public record.',
      };
    case 'disputed':
      return {
        label: 'Disputed',
        badgeClass: 'badge badge-disputed',
        description: 'Peer verifiers recorded a factual or rule disagreement; held from verified ledger.',
      };
    case 'retracted_false_positive':
      return {
        label: 'Retracted (False Positive)',
        badgeClass: 'badge badge-disputed',
        description: 'Refuted on independent verification and preserved transparently in the correction log.',
      };
    case 'rti_filed':
      return {
        label: 'RTI Filed',
        badgeClass: 'badge badge-rti',
        description: 'Citizen-filed RTI Act 2005 Sec 6(1) application logged for official public records.',
      };
    case 'official_response_logged':
      return {
        label: 'Official Response Logged',
        badgeClass: 'badge badge-rti',
        description: 'Public authority response received and attached to the evidence dossier.',
      };
    default:
      return {
        label: status.replace(/_/g, ' '),
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
    procurement: 'Public Procurement',
    audit: 'CAG & PAC Audits',
    budget: 'Union & State Budgets',
    scheme: 'Welfare & Infrastructure',
    corporate_political: 'Corporate & Disclosure',
    parliament: 'Parliament & RTI',
  };
  return map[(pillar || '').toLowerCase()] || pillar || 'Public Audit';
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
