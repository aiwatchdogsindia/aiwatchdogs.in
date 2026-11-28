import { pick } from '../lib/theme';
import {
  ArrowRight,
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  ExternalLink,
  Radio,
  Sparkles,
  Users,
  X,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from '../router';
import {
  buildCanonicalJoinPrompt,
  codingAgentLabel,
  copyToClipboard,
  formatRelativeTime,
  projectCodeFor,
} from '../utils';
import { ProviderModelBadge, resolveBrandLogo } from './AgentLogos';
import { GitHubAvatar, normalizeHandle } from './GitHubAvatar';
import { useToast } from './Toast';
import '../styles/live-swarm-viewer.css';

/* =========================================================================
   Shared Live Swarm Snapshot Types (aligned with GET /api/swarm/live)
   ========================================================================= */

export type LiveSwarmNodeStatus = 'working' | 'verifying' | 'recent' | 'idle';

export type LiveSwarmRole = 'auditor' | 'peer_verifier' | 'adjudicator' | string | null;

export interface LiveSwarmNode {
  handle: string;
  display_name?: string | null;
  status: LiveSwarmNodeStatus | string;
  role?: LiveSwarmRole;
  coding_agent?: string | null;
  coding_agent_model?: string | null;
  provider?: string | null;
  model?: string | null;
  current_task_id?: string | number | null;
  current_task_title?: string | null;
  current_project_slug?: string | null;
  progress_pct?: number | null;
  latest_whisper?: string | null;
  tasks_completed?: number;
  verifications_done?: number;
  findings_submitted?: number;
  score?: number;
  github_verified?: boolean;
  github_verified_at?: string | null;
  last_seen_at?: string | null;
  created_at?: string | null;
}

export interface LiveRecentJoin {
  handle: string;
  display_name?: string | null;
  coding_agent?: string | null;
  coding_agent_model?: string | null;
  provider?: string | null;
  model?: string | null;
  github_verified?: boolean;
  created_at: string;
  last_seen_at?: string | null;
  is_working?: boolean;
}

export interface LiveSwarmWhisper {
  id?: string | number;
  event_type?: string | null;
  handle: string;
  coding_agent?: string | null;
  coding_agent_model?: string | null;
  role?: LiveSwarmRole;
  task_id?: string | number | null;
  project_slug?: string | null;
  finding_id?: string | number | null;
  whisper: string;
  created_at: string;
}

export interface LiveSwarmProgress {
  tasks_total: number;
  tasks_completed: number;
  tasks_in_progress?: number;
  findings_verified: number;
  findings_in_review: number;
  findings_cleared?: number;
}

export interface LiveSwarmSnapshot {
  generated_at: string;
  latest_event_id?: number;
  live_window_seconds?: number;
  working_now: number;
  active_leases_count?: number;
  total_agents: number;
  total_sessions?: number;
  tasks_completed_total?: number;
  tasks_last_hour?: number;
  last_activity_at?: string | null;
  active_nodes: LiveSwarmNode[];
  recent_joins: LiveRecentJoin[];
  live_whispers: LiveSwarmWhisper[];
  progress?: LiveSwarmProgress | null;
}

export interface LiveSwarmViewerProps {
  /** Live snapshot returned by GET /api/swarm/live (or synthesized from /api/head). */
  snapshot: LiveSwarmSnapshot | null;
  /** Optional SSE connection state for subtle status reflection. */
  sseStatus?: 'connecting' | 'live' | 'reconnecting' | 'offline';
  /** Show loading state before the first snapshot arrives. */
  loading?: boolean;
  /** Custom one-liner copied by the inviting "Your agent here" orbit slot. */
  oneLiner?: string;
  /** Optional callback when an agent card is clicked/selected. */
  onSelectAgent?: (node: LiveSwarmNode) => void;
  /** Optional callback when the user clicks to open the full join instructions modal. */
  onOpenJoin?: () => void;
  /** Optional callback when a settled finding event in the center pill is clicked. */
  onSelectFinding?: (findingId: string | number) => void;
  /** Heading override (defaults to "Live swarm viewer"). */
  title?: string;
  /** Semantic heading level for the viewer title (defaults to "h2"). */
  headingAs?: 'h1' | 'h2';
  /** Hide the outer header when embedded inside a section that supplies its own heading. */
  hideHeader?: boolean;
  /** Optional extra CSS class on the outer container. */
  className?: string;
}

/* =========================================================================
   Dynamic Radial Layout Geometry (Zero-Overlap Single & Concentric 2-Ring)
   ========================================================================= */

export type RadialLayoutDensity = 'normal' | 'compact' | 'dense';

export interface RadialRingSpec {
  ringIndex: 0 | 1;
  count: number;
  rx: number;
  ry: number;
  startAngle: number;
}

export interface OrbitSlotCoords {
  x: number;
  y: number;
  pctX: number;
  pctY: number;
  ringIndex: 0 | 1;
  angle: number;
}

export interface RadialLayoutPlan {
  stageW: number;
  stageH: number;
  hubX: number;
  hubY: number;
  density: RadialLayoutDensity;
  isTwoRing: boolean;
  cardW: number;
  cardH: number;
  cardWidthPct: number;
  haloRadius: number;
  rings: RadialRingSpec[];
  slots: OrbitSlotCoords[];
}

export const MAX_RADIAL_CANVAS_NODES = 16;

function buildRingSlots(
  rings: RadialRingSpec[],
  stageW: number,
  stageH: number,
  hubX: number,
  hubY: number,
): OrbitSlotCoords[] {
  const slots: OrbitSlotCoords[] = [];
  for (const ring of rings) {
    if (ring.count <= 0) continue;
    for (let i = 0; i < ring.count; i++) {
      const angle = ring.startAngle + (i * 2 * Math.PI) / ring.count;
      const x = hubX + ring.rx * Math.cos(angle);
      const y = hubY + ring.ry * Math.sin(angle);
      slots.push({
        x: Math.round(x * 10) / 10,
        y: Math.round(y * 10) / 10,
        pctX: (x / stageW) * 100,
        pctY: (y / stageH) * 100,
        ringIndex: ring.ringIndex,
        angle,
      });
    }
  }
  return slots;
}

/**
 * Compute a zero-overlap radial orbital layout for 0–16 seats:
 * - 1–6 seats:  single balanced elliptical ring on a 920 × 580 stage (`normal` density)
 * - 7–8 seats:  single expanded elliptical ring on a 1080 × 660 stage (`compact` density)
 * - 9–12 seats: concentric 2-ring orbit on an 1180 × 740 stage (`compact` density, staggered spokes)
 * - 13–16 seats: concentric 2-ring orbit on a 1220 × 780–790 stage (`dense` density, staggered spokes)
 */
export function computeRadialLayout(rawSeatCount: number): RadialLayoutPlan {
  const count = Math.max(0, Math.min(MAX_RADIAL_CANVAS_NODES, Math.floor(rawSeatCount)));
  const PI = Math.PI;

  if (count <= 6) {
    const stageW = 920;
    const stageH = 580;
    const hubX = stageW / 2;
    const hubY = stageH / 2;
    const rings: RadialRingSpec[] = [
      { ringIndex: 0, count: Math.max(1, count), rx: 320, ry: 194, startAngle: -PI / 2 },
    ];
    return {
      stageW,
      stageH,
      hubX,
      hubY,
      density: 'normal',
      isTwoRing: false,
      cardW: 212,
      cardH: 106,
      cardWidthPct: 23.2,
      haloRadius: 102,
      rings,
      slots: count > 0 ? buildRingSlots(rings, stageW, stageH, hubX, hubY) : [],
    };
  }

  if (count <= 8) {
    const stageW = 1080;
    const stageH = 624;
    const hubX = stageW / 2;
    const hubY = stageH / 2;
    const rings: RadialRingSpec[] = [
      { ringIndex: 0, count, rx: 416, ry: 218, startAngle: -PI / 2 },
    ];
    return {
      stageW,
      stageH,
      hubX,
      hubY,
      density: 'compact',
      isTwoRing: false,
      cardW: 194,
      cardH: 98,
      cardWidthPct: 18.2,
      haloRadius: 88,
      rings,
      slots: buildRingSlots(rings, stageW, stageH, hubX, hubY),
    };
  }

  if (count <= 12) {
    const stageW = 1180;
    const stageH = 720;
    const hubX = stageW / 2;
    const hubY = stageH / 2;
    const outerCount = 6;
    const innerCount = count - outerCount; // 3, 4, 5, or 6
    const innerSpecByCount: Record<number, { rx: number; ry: number; startAngle: number }> = {
      3: { rx: 252, ry: 164, startAngle: -PI / 2 },
      4: { rx: 236, ry: 158, startAngle: -PI / 2 },
      5: { rx: 238, ry: 166, startAngle: PI / 2 },
      6: { rx: 256, ry: 158, startAngle: -PI / 2 },
    };
    const innerSpec = innerSpecByCount[innerCount] ?? { rx: 250, ry: 160, startAngle: -PI / 2 };
    const outerRy = innerCount === 5 ? 286 : 278;
    const outerRx = innerCount === 5 ? 480 : 478;
    const rings: RadialRingSpec[] = [
      { ringIndex: 0, count: innerCount, ...innerSpec },
      { ringIndex: 1, count: outerCount, rx: outerRx, ry: outerRy, startAngle: -PI / 2 + PI / outerCount },
    ];
    return {
      stageW,
      stageH,
      hubX,
      hubY,
      density: 'compact',
      isTwoRing: true,
      cardW: 180,
      cardH: 88,
      cardWidthPct: 15.4,
      haloRadius: 72,
      rings,
      slots: buildRingSlots(rings, stageW, stageH, hubX, hubY),
    };
  }

  // 13–16 seats: concentric 2-ring dense layout
  const isTopTier = count >= 15;
  const stageW = 1220;
  const stageH = isTopTier ? 790 : 780;
  const hubX = stageW / 2;
  const hubY = stageH / 2;
  const outerCount = isTopTier ? 10 : 8;
  const innerCount = count - outerCount; // 5 or 6
  const innerSpec =
    innerCount === 5
      ? { rx: 250, ry: isTopTier ? 168 : 170, startAngle: PI / 2 }
      : { rx: isTopTier ? 248 : 252, ry: isTopTier ? 160 : 162, startAngle: -PI / 2 };
  const outerRx = isTopTier ? 506 : 502;
  const outerRy = isTopTier ? 312 : 306;
  const rings: RadialRingSpec[] = [
    { ringIndex: 0, count: innerCount, ...innerSpec },
    { ringIndex: 1, count: outerCount, rx: outerRx, ry: outerRy, startAngle: -PI / 2 + PI / outerCount },
  ];
  return {
    stageW,
    stageH,
    hubX,
    hubY,
    density: 'dense',
    isTwoRing: true,
    cardW: isTopTier ? 166 : 168,
    cardH: isTopTier ? 82 : 84,
    cardWidthPct: isTopTier ? 13.7 : 13.9,
    haloRadius: 62,
    rings,
    slots: buildRingSlots(rings, stageW, stageH, hubX, hubY),
  };
}

interface RoleVisual {
  key: 'auditor' | 'peer_verifier' | 'adjudicator' | 'live_ready' | 'idle';
  label: string;
  color: string;
  rgb: string;
  spokeOpacity: number;
}

const ROLE_VISUALS: Record<RoleVisual['key'], RoleVisual> = {
  auditor: {
    key: 'auditor',
    label: 'Auditor',
    get color() {
      return pick('#C85A32','#C9A27E');
    },
    get rgb() {
      return pick('200, 90, 50','201, 162, 126');
    },
    spokeOpacity: 0.78,
  },
  peer_verifier: {
    key: 'peer_verifier',
    label: 'Peer Verifier',
    get color() {
      return pick('#138808','#30D158');
    },
    get rgb() {
      return pick('19, 136, 8','48, 209, 88');
    },
    spokeOpacity: 0.76,
  },
  adjudicator: {
    key: 'adjudicator',
    label: 'Adjudicator',
    get color() {
      return pick('#A18324','#0A84FF');
    },
    get rgb() {
      return pick('161, 131, 36','10, 132, 255');
    },
    spokeOpacity: 0.76,
  },
  live_ready: {
    key: 'live_ready',
    label: 'Live · Ready',
    get color() {
      return pick('#2A8C55','#64D2FF');
    },
    get rgb() {
      return pick('42, 140, 85','100, 210, 255');
    },
    spokeOpacity: 0.62,
  },
  idle: {
    key: 'idle',
    label: 'Contributor',
    get color() {
      return pick('#9E978B','#636366');
    },
    get rgb() {
      return pick('158, 151, 139','99, 99, 102');
    },
    spokeOpacity: 0.44,
  },
};

export const DEFAULT_LIVE_WINDOW_SECONDS = 120;
const LIVE_CLOCK_SKEW_GRACE_MS = 15_000;

/**
 * Resolve the effective live heartbeat window in milliseconds (defaults to 120s).
 */
export function resolveLiveWindowMs(liveWindowSeconds?: number | null): number {
  const sec =
    typeof liveWindowSeconds === 'number' && Number.isFinite(liveWindowSeconds) && liveWindowSeconds > 0
      ? liveWindowSeconds
      : DEFAULT_LIVE_WINDOW_SECONDS;
  return sec * 1000;
}

/**
 * Return true when an ISO timestamp is within `live_window_seconds + 15s` clock-skew grace of `nowMs`.
 */
export function isTimestampFreshlyLive(
  iso: string | null | undefined,
  nowMs: number,
  liveWindowSeconds?: number | null,
): boolean {
  if (!iso) return false;
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return false;
  const maxAgeMs = resolveLiveWindowMs(liveWindowSeconds) + LIVE_CLOCK_SKEW_GRACE_MS;
  return nowMs - ts <= maxAgeMs;
}

function isNodeHoldingLease(node: LiveSwarmNode): boolean {
  const s = (node.status || '').toLowerCase();
  return s === 'working' || s === 'verifying' || s === 'active';
}

function isNodeActivelyWorking(node: LiveSwarmNode): boolean {
  const s = (node.status || '').toLowerCase();
  return s === 'working' || s === 'verifying' || s === 'active' || s === 'recent';
}

/**
 * Return true when a node has an active/live status (`working`, `verifying`, `active`,
 * or heartbeating between leases as `recent`) AND its `last_seen_at` heartbeat is within
 * `live_window_seconds` (+15s clock-skew grace) of `nowMs`.
 * Matches backend `GET /api/swarm/live` `working_now` semantics 100%.
 */
export function isNodeFreshlyLive(
  node: LiveSwarmNode,
  nowMs: number,
  liveWindowSeconds?: number | null,
): boolean {
  if (!isNodeActivelyWorking(node)) return false;
  return isTimestampFreshlyLive(node.last_seen_at, nowMs, liveWindowSeconds);
}

/**
 * Downgrade any node whose heartbeat has aged past `live_window_seconds` to calm standby (`idle`),
 * hiding stale lease labels and frozen progress bars. Fresh `recent` nodes (between leases, `<120s`)
 * remain `recent` so they stay visibly live on the canvas.
 */
export function normalizeNodeLiveState(
  node: LiveSwarmNode,
  nowMs: number,
  liveWindowSeconds?: number | null,
): LiveSwarmNode {
  const s = (node.status || '').toLowerCase();
  const fresh = isTimestampFreshlyLive(node.last_seen_at, nowMs, liveWindowSeconds);
  if (s === 'working' || s === 'verifying' || s === 'active') {
    if (fresh) return node;
    return {
      ...node,
      status: 'idle',
      role: null,
      current_task_id: null,
      current_task_title: null,
      current_project_slug: null,
      progress_pct: null,
    };
  }
  if (s === 'recent' && !fresh) {
    return {
      ...node,
      status: 'idle',
      role: null,
      current_task_id: null,
      current_task_title: null,
      current_project_slug: null,
      progress_pct: null,
    };
  }
  return node;
}

/**
 * Recompute effective live working agent count from freshly-heartbeating nodes
 * (including leased `working`/`verifying`/`active` workers and freshly heartbeating
 * `recent` agents between leases) so all views agree 100% with backend `working_now`
 * when fresh and smoothly decay to 0 once heartbeats age past `live_window_seconds`.
 */
export function computeEffectiveWorkingNow(
  snapshot: LiveSwarmSnapshot | null | undefined,
  nowMs: number,
): number {
  if (!snapshot) return 0;
  const windowSec = snapshot.live_window_seconds ?? DEFAULT_LIVE_WINDOW_SECONDS;
  const rawNodes = snapshot.active_nodes ?? [];
  if (rawNodes.length > 0) {
    const seen = new Set<string>();
    const nodes: LiveSwarmNode[] = [];
    for (const n of rawNodes) {
      const key = normalizeHandle(n.handle).toLowerCase();
      if (!key || seen.has(key)) continue;
      seen.add(key);
      nodes.push(n);
    }
    const freshWorking = nodes.filter((n) => isNodeFreshlyLive(n, nowMs, windowSec)).length;
    const totalAgents = snapshot.total_agents ?? nodes.length;
    if (nodes.length >= totalAgents) {
      return freshWorking;
    }
    if (freshWorking === 0 || !isTimestampFreshlyLive(snapshot.generated_at, nowMs, windowSec)) {
      return freshWorking;
    }
    const rawWorkingInNodes = nodes.filter(isNodeActivelyWorking).length;
    const agedOut = Math.max(0, rawWorkingInNodes - freshWorking);
    const overflow = Math.max(0, (snapshot.working_now ?? 0) - nodes.length);
    return freshWorking + (agedOut === 0 ? overflow : 0);
  }
  if ((snapshot.working_now ?? 0) <= 0) return 0;
  return isTimestampFreshlyLive(snapshot.last_activity_at ?? snapshot.generated_at, nowMs, windowSec)
    ? snapshot.working_now
    : 0;
}

function resolveRoleVisual(node: LiveSwarmNode): RoleVisual {
  const status = (node.status || '').toLowerCase();
  const role = (node.role || '').toLowerCase();

  if (status === 'idle' || status === 'revoked') {
    return ROLE_VISUALS.idle;
  }
  if (status === 'recent') {
    return ROLE_VISUALS.live_ready;
  }
  if (role.includes('adjudic') || role.includes('tie')) {
    return ROLE_VISUALS.adjudicator;
  }
  if (role.includes('verif') || status === 'verifying') {
    return ROLE_VISUALS.peer_verifier;
  }
  if (role.includes('audit') || status === 'working' || status === 'active') {
    return ROLE_VISUALS.auditor;
  }
  return ROLE_VISUALS.idle;
}

const GITHUB_USERNAME_RE = /^[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?$/i;
const NON_GITHUB_FALLBACK_RE = /^civic-/i;
const SYNTHETIC_NON_GITHUB_HANDLES = new Set([
  'hub',
  'system',
  'swarm',
  'swarm-node',
  'unknown-node',
  'peer-node',
  'coordinator',
  'anonymous',
  'guest',
]);

/**
 * Return the canonical GitHub profile URL ONLY for cryptographically verified
 * GitHub usernames, or null for unverified / synthetic / `civic-*` handles.
 */
export function githubProfileUrl(
  rawHandle: string | null | undefined,
  githubVerified?: boolean,
): string | null {
  if (!githubVerified) return null;
  const clean = normalizeHandle(rawHandle || '');
  if (!clean) return null;
  if (NON_GITHUB_FALLBACK_RE.test(clean)) return null;
  if (SYNTHETIC_NON_GITHUB_HANDLES.has(clean.toLowerCase())) return null;
  if (clean.includes('--')) return null;
  if (!GITHUB_USERNAME_RE.test(clean)) return null;
  return `https://github.com/${encodeURIComponent(clean)}`;
}

function statusPriority(node: LiveSwarmNode): number {
  const s = (node.status || '').toLowerCase();
  if (s === 'working') return 0;
  if (s === 'verifying') return 1;
  if (s === 'active') return 2;
  if (s === 'recent') return 3;
  return 4;
}

function parseTimestampMs(iso: string | null | undefined): number {
  if (!iso) return 0;
  const ts = Date.parse(iso);
  return Number.isNaN(ts) ? 0 : ts;
}

/**
 * Deduplicate and order live swarm nodes:
 * 1. Active workers/verifiers ahead of recent/idle nodes.
 * 2. Recency (`last_seen_at DESC`, then `created_at DESC`) ahead of `tasks_completed`
 *    so newly joined contributors appear on the canvas immediately instead of being
 *    hidden behind older idle accounts.
 */
export function sortLiveSwarmNodes(nodes: LiveSwarmNode[]): LiveSwarmNode[] {
  const seen = new Set<string>();
  const deduped: LiveSwarmNode[] = [];
  for (const node of nodes) {
    const key = normalizeHandle(node.handle).toLowerCase();
    if (!key || seen.has(key)) continue;
    seen.add(key);
    deduped.push(node);
  }
  return deduped.sort((a, b) => {
    const diff = statusPriority(a) - statusPriority(b);
    if (diff !== 0) return diff;
    const seenDiff = parseTimestampMs(b.last_seen_at) - parseTimestampMs(a.last_seen_at);
    if (seenDiff !== 0) return seenDiff;
    const createdDiff = parseTimestampMs(b.created_at) - parseTimestampMs(a.created_at);
    if (createdDiff !== 0) return createdDiff;
    const tasksDiff = (b.tasks_completed ?? 0) - (a.tasks_completed ?? 0);
    if (tasksDiff !== 0) return tasksDiff;
    return normalizeHandle(a.handle).localeCompare(normalizeHandle(b.handle));
  });
}

function formatTickingAge(iso: string | null | undefined, nowMs: number): string {
  if (!iso) return 'just now';
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return 'just now';
  const diffSec = Math.max(0, Math.floor((nowMs - ts) / 1000));
  if (diffSec < 60) return `${Math.max(1, diffSec)} s ago`;
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin} min ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr} h ago`;
  const diffDay = Math.floor(diffHr / 24);
  return `${diffDay} d ago`;
}

function formatRoleSubtitle(node: LiveSwarmNode, visual: RoleVisual): {
  roleText: string;
  agentText: string;
  logoSrc: string | null;
} {
  const status = (node.status || '').toLowerCase();
  const rawRole = (node.role || '').toLowerCase();
  const roleText = isNodeHoldingLease(node)
    ? rawRole.includes('adjudic') || rawRole.includes('tie')
      ? 'Adjudicator'
      : rawRole.includes('verif') || status === 'verifying'
        ? 'Peer Verifier'
        : rawRole.includes('audit')
          ? 'Auditor'
          : visual.label
    : status === 'recent'
      ? 'Live · Ready'
      : 'Standby Node';

  const agentStr = codingAgentLabel(node.coding_agent, node.coding_agent_model);
  const fallbackStr =
    node.provider && node.provider !== 'rule-engine'
      ? node.model
        ? `${node.provider} / ${node.model}`
        : node.provider
      : node.provider === 'rule-engine'
        ? 'Deterministic CPU'
        : '';

  const agentText = agentStr || fallbackStr;
  const logo =
    resolveBrandLogo(node.coding_agent) ||
    resolveBrandLogo(node.coding_agent_model) ||
    resolveBrandLogo(node.provider) ||
    resolveBrandLogo(node.model);

  return { roleText, agentText, logoSrc: logo?.src ?? null };
}

function formatLiveActionLine(node: LiveSwarmNode): string {
  const status = (node.status || '').toLowerCase();
  const role = (node.role || '').toLowerCase();
  const verb =
    role.includes('adjudic') || role.includes('tie')
      ? 'adjudicating'
      : status === 'verifying' || role.includes('verif')
        ? 'verifying'
        : 'auditing';

  if (isNodeHoldingLease(node)) {
    if (node.current_task_title) {
      return `${verb} ${node.current_task_title}`;
    }
    if (node.current_task_id != null && String(node.current_task_id).trim() !== '') {
      const taskIdStr = String(node.current_task_id);
      const prefix = node.current_project_slug
        ? `${projectCodeFor({ slug: node.current_project_slug })}-`
        : '';
      const ref = /^[A-Za-z]/.test(taskIdStr) ? taskIdStr : `${prefix}${taskIdStr}`;
      return `${verb} ${ref}`;
    }
  }
  if (node.latest_whisper) {
    return node.latest_whisper;
  }
  if (isNodeHoldingLease(node) && node.current_project_slug) {
    return `${verb} ${node.current_project_slug}`;
  }
  if (status === 'recent') {
    return (node.tasks_completed ?? 0) > 0 ? 'Claiming next task' : 'Ready for next lease';
  }
  return 'Standby · awaiting lease';
}

function formatNodeMetricLine(node: LiveSwarmNode): string {
  const parts: string[] = [];
  const tasks = node.tasks_completed ?? 0;
  const verified = node.verifications_done ?? 0;
  const submitted = node.findings_submitted ?? 0;
  const leased = isNodeHoldingLease(node);
  const status = (node.status || '').toLowerCase();

  if (tasks > 0) {
    parts.push(`${tasks.toLocaleString('en-IN')} task${tasks === 1 ? '' : 's'}`);
  }
  if (node.progress_pct != null && Number.isFinite(node.progress_pct) && leased) {
    parts.push(`${Math.max(0, Math.min(100, Math.round(node.progress_pct)))}%`);
  } else if (verified > 0) {
    parts.push(`${verified.toLocaleString('en-IN')} verified`);
  } else if (submitted > 0) {
    parts.push(`${submitted.toLocaleString('en-IN')} lead${submitted === 1 ? '' : 's'}`);
  }

  if (parts.length === 0) {
    if (leased) return 'active lease';
    if (status === 'recent') return 'live · ready';
    return '0 tasks yet';
  }
  return parts.join(' · ');
}

function formatHubEventPill(
  whispers: LiveSwarmWhisper[],
  progress: LiveSwarmProgress | null | undefined,
  nowMs: number,
): {
  verb: string;
  target: string;
  rel: string | null;
  tone: 'emerald' | 'saffron' | 'neutral';
  findingId?: string | number | null;
} {
  const preferred =
    whispers.find((w) => {
      const et = (w.event_type || '').toLowerCase();
      return (
        et === 'finding_verified' ||
        et === 'verification_recorded' ||
        et === 'cleared' ||
        et === 'scan_cleared' ||
        et === 'website_batch_observed' ||
        et === 'finding_submitted' ||
        et === 'task_claimed'
      );
    }) ?? whispers[0];

  if (!preferred) {
    const open =
      progress && progress.tasks_total > progress.tasks_completed
        ? progress.tasks_total - progress.tasks_completed
        : 0;
    return {
      verb: 'quorum 3-of-3',
      target: open > 0 ? `${open.toLocaleString('en-IN')} tasks open` : 'ready for agents',
      rel: null,
      tone: 'neutral',
    };
  }

  const et = (preferred.event_type || '').toLowerCase();
  let verb = 'updated';
  let tone: 'emerald' | 'saffron' | 'neutral' = 'emerald';
  if (et === 'finding_verified') {
    verb = 'verified';
  } else if (et === 'verification_recorded') {
    verb = 'checked';
  } else if (et === 'cleared' || et === 'scan_cleared' || et === 'website_batch_observed') {
    verb = 'cleared';
  } else if (et === 'finding_submitted') {
    verb = 'flagged';
    tone = 'saffron';
  } else if (et === 'task_claimed') {
    verb = 'claimed';
  } else if (et === 'agent_joined') {
    verb = 'joined';
  } else if (et === 'rti_drafted') {
    verb = 'drafted RTI';
  }

  let target = '';
  if (preferred.task_id != null && String(preferred.task_id).trim() !== '') {
    const rawId = String(preferred.task_id);
    const code = preferred.project_slug ? projectCodeFor({ slug: preferred.project_slug }) : '';
    target = /^[A-Za-z]/.test(rawId) ? rawId : code ? `${code}-${rawId}` : `#${rawId}`;
  } else if (preferred.project_slug) {
    target = projectCodeFor({ slug: preferred.project_slug });
  } else {
    target = `@${normalizeHandle(preferred.handle)}`;
  }

  return {
    verb,
    target,
    rel: formatTickingAge(preferred.created_at, nowMs),
    tone,
    findingId: preferred.finding_id,
  };
}

interface OrbitSeatItem {
  slotIndex: number;
  coords: OrbitSlotCoords;
  kind: 'node' | 'invite' | 'standby';
  node?: LiveSwarmNode;
  visual?: RoleVisual;
  standbyRole?: string;
  standbyLabel?: string;
}

export function LiveSwarmViewer({
  snapshot,
  sseStatus = 'live',
  loading = false,
  oneLiner,
  onSelectAgent,
  onOpenJoin,
  onSelectFinding,
  title = 'Live swarm viewer',
  headingAs = 'h2',
  hideHeader = false,
  className = '',
}: LiveSwarmViewerProps) {
  const HeadingTag = headingAs;
  const toast = useToast();
  const stageRef = useRef<HTMLDivElement | null>(null);

  const [nowMs, setNowMs] = useState(() => Date.now());
  const [selectedHandle, setSelectedHandle] = useState<string | null>(null);
  const [rosterOpen, setRosterOpen] = useState(false);
  const [rosterFilter, setRosterFilter] = useState<'all' | 'working' | 'verifier' | 'recent'>('all');
  const [copiedInvite, setCopiedInvite] = useState(false);

  const [inView, setInView] = useState(true);
  const [tabVisible, setTabVisible] = useState(() =>
    typeof document === 'undefined' ? true : document.visibilityState !== 'hidden',
  );
  const [reducedMotion, setReducedMotion] = useState(() =>
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
      : false,
  );

  // Live 1-second ticker for "updated X s ago" (pauses when tab is hidden)
  useEffect(() => {
    if (!tabVisible) return;
    setNowMs(Date.now());
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [tabVisible]);

  // Observe visibility in viewport to save GPU/CPU when scrolled offscreen
  useEffect(() => {
    const el = stageRef.current;
    if (!el || typeof IntersectionObserver === 'undefined') return;
    const obs = new IntersectionObserver(
      ([entry]) => setInView(Boolean(entry?.isIntersecting)),
      { threshold: 0.02 },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  useEffect(() => {
    const onVis = () => setTabVisible(document.visibilityState !== 'hidden');
    document.addEventListener('visibilitychange', onVis);
    return () => document.removeEventListener('visibilitychange', onVis);
  }, []);

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    const onChange = () => setReducedMotion(mq.matches);
    mq.addEventListener?.('change', onChange);
    return () => mq.removeEventListener?.('change', onChange);
  }, []);

  useEffect(() => {
    if (!selectedHandle) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setSelectedHandle(null);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [selectedHandle]);

  const animateActive = inView && tabVisible && !reducedMotion;
  const joinCommand = oneLiner ?? buildCanonicalJoinPrompt();
  const liveWindowSeconds = snapshot?.live_window_seconds ?? DEFAULT_LIVE_WINDOW_SECONDS;

  // Downgrade stalled nodes whose heartbeats aged past live_window_seconds, then deduplicate and sort:
  // freshly-live workers/verifiers first, then recency (last_seen_at / created_at DESC).
  const sortedNodes = useMemo(
    () =>
      sortLiveSwarmNodes(
        (snapshot?.active_nodes ?? []).map((n) =>
          normalizeNodeLiveState(n, nowMs, liveWindowSeconds),
        ),
      ),
    [snapshot?.active_nodes, nowMs, liveWindowSeconds],
  );

  const { liveNodes, offlineNodes } = useMemo(() => {
    const live: LiveSwarmNode[] = [];
    const offline: LiveSwarmNode[] = [];
    for (const node of sortedNodes) {
      if (isNodeFreshlyLive(node, nowMs, liveWindowSeconds)) {
        live.push(node);
      } else {
        offline.push(node);
      }
    }
    return { liveNodes: live, offlineNodes: offline };
  }, [sortedNodes, nowMs, liveWindowSeconds]);

  const canvasLiveNodes = useMemo(
    () => liveNodes.slice(0, MAX_RADIAL_CANVAS_NODES),
    [liveNodes],
  );
  const extraLiveNodes = useMemo(
    () => liveNodes.slice(MAX_RADIAL_CANVAS_NODES),
    [liveNodes],
  );

  const { layout, orbitItems } = useMemo(() => {
    type SeatSeed = Omit<OrbitSeatItem, 'slotIndex' | 'coords'>;
    const seeds: SeatSeed[] = [];

    if (liveNodes.length === 0) {
      // Graceful standby mode: up to 6 most recent contributors, or invite + 2 standby slots when empty
      if (offlineNodes.length === 0) {
        seeds.push(
          { kind: 'invite' },
          {
            kind: 'standby',
            standbyRole: 'Peer Verifier',
            standbyLabel: 'Independent 3-of-3 check slot',
          },
          {
            kind: 'standby',
            standbyRole: 'Civic Auditor',
            standbyLabel: 'Public budget & .gov.in slot',
          },
        );
      } else {
        const standbySlice = offlineNodes.slice(0, 6);
        for (const node of standbySlice) {
          seeds.push({ kind: 'node', node, visual: resolveRoleVisual(node) });
        }
        if (standbySlice.length < 6) {
          seeds.push({ kind: 'invite' });
        }
      }
    } else if (liveNodes.length <= 5) {
      // 1–5 live nodes: render all live nodes + 1 dashed "Your agent here" invite card (2–6 total seats)
      for (const node of liveNodes) {
        seeds.push({ kind: 'node', node, visual: resolveRoleVisual(node) });
      }
      seeds.push({ kind: 'invite' });
    } else {
      // 6–16 live nodes (or top 16 when 17+ live): showcase ALL live workers around the SWARM hub
      for (const node of canvasLiveNodes) {
        seeds.push({ kind: 'node', node, visual: resolveRoleVisual(node) });
      }
    }

    const plan = computeRadialLayout(seeds.length);
    const items: OrbitSeatItem[] = seeds.map((seed, idx) => ({
      ...seed,
      slotIndex: idx,
      coords: plan.slots[idx] ?? {
        x: plan.hubX,
        y: plan.hubY,
        pctX: 50,
        pctY: 50,
        ringIndex: 0,
        angle: -Math.PI / 2,
      },
    }));

    return { layout: plan, orbitItems: items };
  }, [liveNodes, offlineNodes, canvasLiveNodes]);

  // Mobile grid renders ALL live nodes (never capped at 6 or 16!), plus invite card when < 6
  const mobileItems = useMemo(() => {
    const items: Array<
      | { kind: 'node'; key: string; node: LiveSwarmNode; visual: RoleVisual }
      | { kind: 'invite'; key: string }
    > = [];
    if (liveNodes.length === 0) {
      const standbySlice = offlineNodes.slice(0, 6);
      for (const node of standbySlice) {
        items.push({
          kind: 'node',
          key: `mob-${normalizeHandle(node.handle)}`,
          node,
          visual: resolveRoleVisual(node),
        });
      }
      if (standbySlice.length < 6) {
        items.push({ kind: 'invite', key: 'mob-invite' });
      }
    } else {
      for (const node of liveNodes) {
        items.push({
          kind: 'node',
          key: `mob-${normalizeHandle(node.handle)}`,
          node,
          visual: resolveRoleVisual(node),
        });
      }
      if (liveNodes.length < 6) {
        items.push({ kind: 'invite', key: 'mob-invite' });
      }
    }
    return items;
  }, [liveNodes, offlineNodes]);

  // Subtle curved dotted collaboration arcs between adjacent active peers on the same orbital ring
  const peerArcs = useMemo(() => {
    const realSeats = orbitItems.filter((item) => item.kind === 'node' && item.node);
    if (realSeats.length < 2) return [];
    const arcs: Array<{ id: string; d: string; color: string }> = [];
    const byRing: OrbitSeatItem[][] = [[], []];
    for (const seat of realSeats) {
      byRing[seat.coords.ringIndex]?.push(seat);
    }
    const maxPerRing = layout.isTwoRing ? 1 : 2;

    for (const ringSeats of byRing) {
      if (ringSeats.length < 2) continue;
      let addedForRing = 0;
      for (let i = 0; i < ringSeats.length && addedForRing < maxPerRing && arcs.length < 2; i++) {
        const a = ringSeats[i];
        const b = ringSeats[(i + 1) % ringSeats.length];
        if (!a.node || !b.node) continue;
        const sameProject =
          a.node.current_project_slug &&
          a.node.current_project_slug === b.node.current_project_slug;
        const bothActive = isNodeActivelyWorking(a.node) && isNodeActivelyWorking(b.node);
        if (!sameProject && !bothActive) continue;

        const mx =
          (a.coords.x + b.coords.x) / 2 + (layout.hubX - (a.coords.x + b.coords.x) / 2) * 0.22;
        const my =
          (a.coords.y + b.coords.y) / 2 + (layout.hubY - (a.coords.y + b.coords.y) / 2) * 0.22;
        arcs.push({
          id: `peer-${a.slotIndex}-${b.slotIndex}`,
          d: `M ${a.coords.x} ${a.coords.y} Q ${mx.toFixed(1)} ${my.toFixed(1)} ${b.coords.x} ${b.coords.y}`,
          color: a.visual?.color ?? '#C9A27E',
        });
        addedForRing += 1;
      }
    }
    return arcs;
  }, [orbitItems, layout.hubX, layout.hubY, layout.isTwoRing]);

  const workingNow = useMemo(
    () => computeEffectiveWorkingNow(snapshot, nowMs),
    [snapshot, nowMs],
  );
  const totalKnownNodes = Math.max(sortedNodes.length, snapshot?.total_agents ?? 0);
  const effectiveLiveTotal = Math.max(liveNodes.length, workingNow);
  const extraLiveOverflow = Math.max(0, effectiveLiveTotal - canvasLiveNodes.length);
  const standbyOverflow =
    liveNodes.length > 0
      ? Math.max(0, totalKnownNodes - effectiveLiveTotal)
      : Math.max(0, totalKnownNodes - Math.min(offlineNodes.length, 6));
  const overflowCount = extraLiveOverflow + standbyOverflow;
  const overflowPillLabel = useMemo(() => {
    if (extraLiveOverflow > 0 && standbyOverflow > 0) {
      return `+${extraLiveOverflow.toLocaleString('en-IN')} more live · +${standbyOverflow.toLocaleString('en-IN')} standby`;
    }
    if (extraLiveOverflow > 0) {
      return `+${extraLiveOverflow.toLocaleString('en-IN')} more live`;
    }
    return `+${standbyOverflow.toLocaleString('en-IN')} standby contributor${standbyOverflow === 1 ? '' : 's'}`;
  }, [extraLiveOverflow, standbyOverflow]);
  const unlistedLiveCount = Math.max(0, workingNow - liveNodes.length);
  const hasBetweenLeaseOnCanvas = useMemo(
    () => canvasLiveNodes.some((n) => (n.status || '').toLowerCase() === 'recent'),
    [canvasLiveNodes],
  );
  const activeLeases = workingNow > 0 ? (snapshot?.active_leases_count ?? workingNow) : 0;
  const totalTasksDone = snapshot?.tasks_completed_total ?? snapshot?.progress?.tasks_completed ?? 0;

  const updatedLabel = snapshot?.generated_at
    ? formatTickingAge(snapshot.generated_at, nowMs)
    : loading
      ? 'connecting…'
      : 'just now';

  const hubEvent = useMemo(
    () => formatHubEventPill(snapshot?.live_whispers ?? [], snapshot?.progress, nowMs),
    [snapshot?.live_whispers, snapshot?.progress, nowMs],
  );

  const selectedNode = useMemo(() => {
    if (!selectedHandle) return null;
    return (
      sortedNodes.find(
        (n) => normalizeHandle(n.handle).toLowerCase() === selectedHandle.toLowerCase(),
      ) ?? null
    );
  }, [selectedHandle, sortedNodes]);

  const filteredRosterNodes = useMemo(() => {
    if (rosterFilter === 'all') return sortedNodes;
    if (rosterFilter === 'working') return sortedNodes.filter(isNodeActivelyWorking);
    if (rosterFilter === 'verifier') {
      return sortedNodes.filter(
        (n) =>
          (n.role || '').toLowerCase().includes('verif') ||
          (n.status || '').toLowerCase() === 'verifying',
      );
    }
    return sortedNodes.filter((n) => !isNodeHoldingLease(n));
  }, [sortedNodes, rosterFilter]);

  const handleCopyInvite = async () => {
    const ok = await copyToClipboard(joinCommand);
    if (ok) {
      setCopiedInvite(true);
      toast('Copied 1-line swarm join prompt — paste into your coding agent to join live');
      window.setTimeout(() => setCopiedInvite(false), 2200);
    }
  };

  const handleSelectNode = (node: LiveSwarmNode) => {
    const clean = normalizeHandle(node.handle);
    setSelectedHandle((prev) => (prev?.toLowerCase() === clean.toLowerCase() ? null : clean));
    onSelectAgent?.(node);
  };

  const rightSummaryText = useMemo(() => {
    if (loading && !snapshot) return 'Syncing live swarm telemetry…';
    if (workingNow > 0) {
      const taskPart =
        activeLeases > 0
          ? `${activeLeases.toLocaleString('en-IN')} active task${activeLeases === 1 ? '' : 's'}`
          : `${totalKnownNodes.toLocaleString('en-IN')} contributors`;
      return `${workingNow.toLocaleString('en-IN')} agent${workingNow === 1 ? '' : 's'} working · ${taskPart}`;
    }
    if (totalKnownNodes > 0) {
      return `${totalKnownNodes.toLocaleString('en-IN')} contributor${totalKnownNodes === 1 ? '' : 's'} · ${totalTasksDone.toLocaleString('en-IN')} tasks checked`;
    }
    return 'Swarm open · awaiting live agents';
  }, [loading, snapshot, workingNow, activeLeases, totalKnownNodes, totalTasksDone]);

  const cardAvatarSize = layout.density === 'dense' ? 16 : layout.density === 'compact' ? 18 : 20;

  return (
    <section
      className={`lsv-root${className ? ` ${className}` : ''}`}
      aria-label="Live swarm viewer"
    >
      {!hideHeader && (
        <div className="lsv-header">
          <div className="lsv-header-left">
            <HeadingTag className="lsv-title">{title}</HeadingTag>
            <span className="lsv-freshness" aria-live="polite">
              <span
                className={`lsv-dot ${
                  sseStatus === 'offline'
                    ? 'lsv-dot--offline'
                    : sseStatus === 'reconnecting'
                      ? 'lsv-dot--amber'
                      : 'lsv-dot--live'
                }`}
              />
              <span className="lsv-freshness-text">updated {updatedLabel}</span>
            </span>
          </div>

          <div className="lsv-header-right">
            <span className="lsv-summary-stat tabular">{rightSummaryText}</span>
          </div>
        </div>
      )}

      {/* Interactive Radial Stage */}
      <div
        ref={stageRef}
        className={`lsv-stage is-density-${layout.density}${layout.isTwoRing ? ' is-two-ring' : ''}${
          animateActive ? ' is-animating' : ' is-paused'
        }`}
        data-working-count={workingNow}
        data-live-canvas-count={canvasLiveNodes.length}
        data-ring-count={layout.isTwoRing ? 2 : 1}
        style={{
          ['--lsv-stage-max-w' as string]: `${layout.stageW}px`,
          ['--lsv-stage-ratio' as string]: `${layout.stageW} / ${layout.stageH}`,
          ['--lsv-card-w' as string]: `${layout.cardW}px`,
          ['--lsv-card-w-pct' as string]: `${layout.cardWidthPct}%`,
        }}
      >
        <div className="lsv-canvas-frame">
          {/* Desktop Radial SVG Canvas */}
          <svg
            className="lsv-svg"
            viewBox={`0 0 ${layout.stageW} ${layout.stageH}`}
            preserveAspectRatio="xMidYMid meet"
            aria-hidden="true"
          >
            <defs>
              {orbitItems.map((item) => {
                if (item.kind !== 'node' || !item.visual) return null;
                const leased = item.node ? isNodeHoldingLease(item.node) : false;
                return (
                  <radialGradient
                    key={`aura-${item.slotIndex}`}
                    id={`lsv-aura-${item.slotIndex}`}
                    cx="50%"
                    cy="50%"
                    r="50%"
                  >
                    <stop
                      offset="0%"
                      stopColor={`rgba(${item.visual.rgb}, ${leased ? 0.2 : 0.14})`}
                    />
                    <stop offset="100%" stopColor={`rgba(${item.visual.rgb}, 0)`} />
                  </radialGradient>
                );
              })}
            </defs>

            {/* Concentric dotted orbital path(s) connecting every orbiting seat */}
            {layout.rings.map((ring) => (
              <ellipse
                key={`orbit-ring-${ring.ringIndex}`}
                cx={layout.hubX}
                cy={layout.hubY}
                rx={ring.rx}
                ry={ring.ry}
                fill="none"
                stroke={
                  ring.ringIndex === 0
                    ? 'rgba(198, 134, 114, 0.34)'
                    : 'rgba(198, 134, 114, 0.27)'
                }
                strokeWidth={ring.ringIndex === 0 ? '1.25' : '1.15'}
                strokeDasharray={ring.ringIndex === 0 ? '3 7' : '4 8'}
              />
            ))}

            {/* Soft role-tinted radial auras behind active orbiting agents */}
            {orbitItems.map((item) => {
              if (item.kind !== 'node' || !item.node || !isNodeActivelyWorking(item.node)) {
                return null;
              }
              return (
                <circle
                  key={`halo-${item.slotIndex}`}
                  cx={item.coords.x}
                  cy={item.coords.y}
                  r={layout.haloRadius}
                  fill={`url(#lsv-aura-${item.slotIndex})`}
                />
              );
            })}

            {/* Curved dotted collaboration arcs between peer agents */}
            {peerArcs.map((arc) => (
              <path
                key={arc.id}
                d={arc.d}
                fill="none"
                stroke={arc.color}
                strokeWidth="1.15"
                strokeDasharray="3 6"
                opacity="0.34"
              />
            ))}

            {/* Radial spokes + animated packets between center hub and each orbiting card */}
            {orbitItems.map((item) => {
              const isNode = item.kind === 'node' && item.node != null;
              const live = isNode && isNodeActivelyWorking(item.node!);
              const leased = isNode && isNodeHoldingLease(item.node!);
              const isOuter = item.coords.ringIndex === 1;
              const selected =
                isNode &&
                selectedHandle != null &&
                normalizeHandle(item.node!.handle).toLowerCase() === selectedHandle.toLowerCase();
              const strokeColor = isNode
                ? item.visual?.color ?? pick('#9E978B', '#636366')
                : pick('#C9C2B6', '#3A3A3C');
              const strokeWidth = selected
                ? 2.7
                : leased
                  ? isOuter
                    ? 1.8
                    : 2.15
                  : live
                    ? isOuter
                      ? 1.35
                      : 1.65
                    : 1.2;
              const baseOpacity = isNode ? item.visual?.spokeOpacity ?? 0.5 : 0.45;
              const opacity = selected ? 0.95 : isOuter ? baseOpacity * 0.85 : baseOpacity;
              const spokeFromHub = `M ${layout.hubX} ${layout.hubY} L ${item.coords.x} ${item.coords.y}`;
              const spokeToHub = `M ${item.coords.x} ${item.coords.y} L ${layout.hubX} ${layout.hubY}`;
              const jewelT = isOuter ? 0.72 : 0.5;
              const midX = layout.hubX + (item.coords.x - layout.hubX) * jewelT;
              const midY = layout.hubY + (item.coords.y - layout.hubY) * jewelT;
              const durSec = Math.max(2.4, (isOuter ? 4.4 : 3.9) - (item.slotIndex % 3) * 0.4);

              return (
                <g key={`spoke-${item.slotIndex}`}>
                  <path
                    d={spokeFromHub}
                    fill="none"
                    stroke={strokeColor}
                    strokeWidth={strokeWidth}
                    strokeDasharray={!isNode ? '4 6' : !leased && live ? '5 4' : undefined}
                    strokeLinecap="round"
                    opacity={opacity}
                  />

                  {/* Static midpoint jewel visible when reduced-motion or idle */}
                  {isNode && (
                    <circle
                      cx={midX}
                      cy={midY}
                      r={leased ? 2.5 : live ? 2.2 : 2.0}
                      fill={strokeColor}
                      opacity={live ? 0.85 : 0.55}
                    />
                  )}

                  {/* Animated packets pulsing along live spokes */}
                  {live && animateActive && (
                    <>
                      <circle r={leased ? '3.2' : '2.6'} fill={strokeColor} opacity="0.92">
                        <animateMotion
                          dur={`${durSec.toFixed(2)}s`}
                          begin={`-${(item.slotIndex * 0.55 + 0.35).toFixed(2)}s`}
                          repeatCount="indefinite"
                          path={spokeToHub}
                        />
                      </circle>
                      {leased && (
                        <circle r="2.1" fill={pick('#1FA319', '#30D158')} opacity="0.72">
                          <animateMotion
                            dur={`${(durSec * 1.35).toFixed(2)}s`}
                            begin={`-${(item.slotIndex * 0.75 + 0.9).toFixed(2)}s`}
                            repeatCount="indefinite"
                            path={spokeFromHub}
                          />
                        </circle>
                      )}
                    </>
                  )}
                </g>
              );
            })}
          </svg>

          {/* Center SWARM Hub Disc + Floating Live Settled Event Pill */}
          <div className="lsv-hub" role="region" aria-label="Swarm verification hub">
            <div className={`lsv-hub-rings${workingNow > 0 && animateActive ? ' is-pulsing' : ''}`}>
              <div className="lsv-hub-disc">
                <span className="lsv-hub-word">SWARM</span>
                {workingNow > 0 ? (
                  <span className="lsv-hub-count tabular">{workingNow} live</span>
                ) : (
                  <span className="lsv-hub-count tabular" style={{ color: pick('#A39D92', '#8E8E93') }}>
                    standby
                  </span>
                )}
              </div>
            </div>

            {hubEvent.findingId != null && onSelectFinding ? (
              <button
                type="button"
                className={`lsv-hub-pill lsv-hub-pill--${hubEvent.tone} is-clickable`}
                onClick={() => onSelectFinding(hubEvent.findingId!)}
                title="Inspect latest verified dossier"
              >
                <span className="lsv-hub-pill-verb">{hubEvent.verb}</span>
                <span className="lsv-hub-pill-target">{hubEvent.target}</span>
                {hubEvent.rel && (
                  <>
                    <span className="lsv-hub-pill-sep" aria-hidden="true">·</span>
                    <span className="lsv-hub-pill-time">{hubEvent.rel}</span>
                  </>
                )}
              </button>
            ) : (
              <div className={`lsv-hub-pill lsv-hub-pill--${hubEvent.tone}`}>
                <span className="lsv-hub-pill-verb">{hubEvent.verb}</span>
                <span className="lsv-hub-pill-target">{hubEvent.target}</span>
                {hubEvent.rel && (
                  <>
                    <span className="lsv-hub-pill-sep" aria-hidden="true">·</span>
                    <span className="lsv-hub-pill-time">{hubEvent.rel}</span>
                  </>
                )}
              </div>
            )}
          </div>

          {/* Desktop Orbiting Agent Cards + Inviting Slot */}
          <div className="lsv-orbit-layer">
            {orbitItems.map((item) => {
              if (item.kind === 'invite') {
                return (
                  <button
                    key={`invite-${item.slotIndex}`}
                    type="button"
                    className={`lsv-card lsv-card--invite${copiedInvite ? ' is-copied' : ''}`}
                    style={{ left: `${item.coords.pctX}%`, top: `${item.coords.pctY}%` }}
                    onClick={handleCopyInvite}
                    title={joinCommand}
                  >
                    <div className="lsv-card-head">
                      <span className="lsv-role-dot lsv-role-dot--invite" aria-hidden="true" />
                      <span className="lsv-card-handle">Your agent here</span>
                    </div>
                    <div className="lsv-card-sub">
                      <span>Any coding agent · ₹0 compute</span>
                    </div>
                    <div className="lsv-card-activity lsv-card-activity--cta">
                      {copiedInvite ? (
                        <>
                          <Check size={12} color="var(--green)" />
                          <span>Copied! Paste into your agent</span>
                        </>
                      ) : (
                        <>
                          <Copy size={12} />
                          <span>Copy one line to join live</span>
                        </>
                      )}
                    </div>
                    <div className="lsv-card-stats">
                      <span>Auto-uses your public GitHub handle</span>
                    </div>
                  </button>
                );
              }

              if (item.kind === 'standby') {
                return (
                  <button
                    key={`standby-${item.slotIndex}`}
                    type="button"
                    className="lsv-card lsv-card--standby"
                    style={{ left: `${item.coords.pctX}%`, top: `${item.coords.pctY}%` }}
                    onClick={onOpenJoin ?? handleCopyInvite}
                  >
                    <div className="lsv-card-head">
                      <span className="lsv-role-dot lsv-role-dot--idle" aria-hidden="true" />
                      <span className="lsv-card-handle">open-slot-{item.slotIndex + 1}</span>
                    </div>
                    <div className="lsv-card-sub">
                      <span>{item.standbyRole}</span>
                    </div>
                    <div className="lsv-card-activity">
                      <span className="lsv-dot lsv-dot--offline" aria-hidden="true" />
                      <span className="lsv-card-activity-text">{item.standbyLabel}</span>
                    </div>
                    <div className="lsv-card-stats">
                      <span>Ready to claim</span>
                    </div>
                  </button>
                );
              }

              const node = item.node!;
              const visual = item.visual!;
              const cleanHandle = normalizeHandle(node.handle);
              const live = isNodeActivelyWorking(node);
              const leased = isNodeHoldingLease(node);
              const betweenLeases = live && !leased;
              const selected =
                selectedHandle != null &&
                cleanHandle.toLowerCase() === selectedHandle.toLowerCase();
              const sub = formatRoleSubtitle(node, visual);
              const actionLine = formatLiveActionLine(node);
              const metricLine = formatNodeMetricLine(node);
              const pct =
                leased && node.progress_pct != null && Number.isFinite(node.progress_pct)
                  ? Math.max(0, Math.min(100, Math.round(node.progress_pct)))
                  : null;

              return (
                <button
                  key={`node-${cleanHandle}`}
                  type="button"
                  className={`lsv-card${live ? ' is-working' : ' is-idle'}${
                    betweenLeases ? ' is-live-ready' : ''
                  }${selected ? ' is-selected' : ''}`}
                  data-ring={item.coords.ringIndex}
                  data-node-status={(node.status || '').toLowerCase()}
                  style={{
                    left: `${item.coords.pctX}%`,
                    top: `${item.coords.pctY}%`,
                    ['--lsv-role-color' as string]: visual.color,
                    ['--lsv-role-rgb' as string]: visual.rgb,
                  }}
                  aria-pressed={selected}
                  onClick={() => handleSelectNode(node)}
                >
                  <div className="lsv-card-head">
                    <span
                      className={`lsv-role-dot${live ? ' is-pulsing' : ''}`}
                      style={{ background: visual.color }}
                      aria-hidden="true"
                    />
                    <GitHubAvatar
                      handle={cleanHandle}
                      size={cardAvatarSize}
                      verified={Boolean(node.github_verified)}
                    />
                    <span className="lsv-card-handle" title={`@${cleanHandle}`}>
                      @{cleanHandle}
                    </span>
                  </div>

                  <div
                    className="lsv-card-sub"
                    title={`${sub.roleText}${sub.agentText ? ` · ${sub.agentText}` : ''}`}
                  >
                    {sub.logoSrc && (
                      <img
                        src={sub.logoSrc}
                        alt=""
                        width={12}
                        height={12}
                        className="lsv-card-brand-icon"
                      />
                    )}
                    <span className="lsv-card-sub-text">
                      {sub.roleText}
                      {sub.agentText ? ` · ${sub.agentText}` : ''}
                    </span>
                  </div>

                  <div className="lsv-card-activity" title={actionLine}>
                    <span
                      className={`lsv-dot ${live ? 'lsv-dot--live' : 'lsv-dot--offline'}`}
                      aria-hidden="true"
                    />
                    <span className="lsv-card-activity-text">{actionLine}</span>
                  </div>

                  <div className="lsv-card-stats tabular">
                    <span>{metricLine}</span>
                    {pct != null && (
                      <span className="lsv-card-microbar" aria-hidden="true">
                        <span
                          className="lsv-card-microbar-fill"
                          style={{ width: `${Math.max(8, pct)}%`, background: visual.color }}
                        />
                      </span>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        {/* Mobile / Narrow Viewport Clean Grid Layout (renders ALL liveNodes, never capped at 6) */}
        <div className="lsv-mobile-grid" aria-label="Live swarm agents">
          {mobileItems.map((item) => {
            if (item.kind === 'invite') {
              return (
                <button
                  key={item.key}
                  type="button"
                  className={`lsv-mob-card lsv-mob-card--invite${copiedInvite ? ' is-copied' : ''}`}
                  onClick={handleCopyInvite}
                >
                  <div className="lsv-card-head">
                    <Sparkles size={14} color="var(--accent-deep)" />
                    <span className="lsv-card-handle">Your agent here</span>
                  </div>
                  <div className="lsv-card-sub">
                    {copiedInvite
                      ? 'Copied! Paste into your coding agent'
                      : 'Tap to copy one line & join live'}
                  </div>
                </button>
              );
            }

            const node = item.node;
            const visual = item.visual;
            const cleanHandle = normalizeHandle(node.handle);
            const live = isNodeActivelyWorking(node);
            const selected =
              selectedHandle != null &&
              cleanHandle.toLowerCase() === selectedHandle.toLowerCase();
            const sub = formatRoleSubtitle(node, visual);
            const actionLine = formatLiveActionLine(node);
            const metricLine = formatNodeMetricLine(node);

            return (
              <button
                key={item.key}
                type="button"
                className={`lsv-mob-card${selected ? ' is-selected' : ''}`}
                style={{ ['--lsv-role-color' as string]: visual.color }}
                aria-pressed={selected}
                onClick={() => handleSelectNode(node)}
              >
                <div className="lsv-card-head">
                  <span
                    className={`lsv-role-dot${live ? ' is-pulsing' : ''}`}
                    style={{ background: visual.color }}
                    aria-hidden="true"
                  />
                  <GitHubAvatar
                    handle={cleanHandle}
                    size={22}
                    verified={Boolean(node.github_verified)}
                  />
                  <span className="lsv-card-handle">@{cleanHandle}</span>
                </div>
                <div className="lsv-card-sub">
                  {sub.logoSrc && (
                    <img
                      src={sub.logoSrc}
                      alt=""
                      width={13}
                      height={13}
                      className="lsv-card-brand-icon"
                    />
                  )}
                  <span className="lsv-card-sub-text">
                    {sub.roleText}
                    {sub.agentText ? ` · ${sub.agentText}` : ''}
                  </span>
                </div>
                <div className="lsv-card-activity">
                  <span className={`lsv-dot ${live ? 'lsv-dot--live' : 'lsv-dot--offline'}`} />
                  <span className="lsv-card-activity-text">{actionLine}</span>
                </div>
                <div className="lsv-card-stats tabular">{metricLine}</div>
              </button>
            );
          })}
        </div>

        {/* Role Legend (Bottom-Left on Desktop) */}
        <div className="lsv-legend" aria-hidden="true">
          <span className="lsv-legend-item">
            <span className="lsv-legend-dot" style={{ background: ROLE_VISUALS.auditor.color }} />
            Auditor
          </span>
          <span className="lsv-legend-item">
            <span
              className="lsv-legend-dot"
              style={{ background: ROLE_VISUALS.peer_verifier.color }}
            />
            Verifier
          </span>
          <span className="lsv-legend-item">
            <span
              className="lsv-legend-dot"
              style={{ background: ROLE_VISUALS.adjudicator.color }}
            />
            Adjudicator
          </span>
          {hasBetweenLeaseOnCanvas && (
            <span className="lsv-legend-item">
              <span
                className="lsv-legend-dot"
                style={{ background: ROLE_VISUALS.live_ready.color }}
              />
              Ready
            </span>
          )}
        </div>

        {/* Bottom-Right Overflow Pill (remaining contributors not shown on radial canvas) */}
        {overflowCount > 0 && (
          <button
            type="button"
            className={`lsv-more-pill${rosterOpen ? ' is-open' : ''}`}
            aria-expanded={rosterOpen}
            onClick={() => setRosterOpen((v) => !v)}
          >
            <span className="lsv-more-dots" aria-hidden="true">•••</span>
            <span>{overflowPillLabel}</span>
            {rosterOpen ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
          </button>
        )}
      </div>

      {/* Extreme-Scale Safety: Always-visible strip for live workers #17+ so none are ever hidden */}
      {extraLiveNodes.length > 0 && (
        <div
          className="lsv-live-overflow"
          role="region"
          aria-label={`Also working live (${extraLiveNodes.length} additional active agents)`}
        >
          <div className="lsv-live-overflow-head">
            <span className="lsv-live-overflow-title">
              <span className="lsv-dot lsv-dot--live" aria-hidden="true" />
              <span>
                Also working live (+{extraLiveNodes.length.toLocaleString('en-IN')})
              </span>
            </span>
            {unlistedLiveCount > 0 && (
              <span className="lsv-live-overflow-note tabular">
                +{unlistedLiveCount.toLocaleString('en-IN')} more streaming
              </span>
            )}
          </div>
          <div className="lsv-live-overflow-grid">
            {extraLiveNodes.map((node) => {
              const cleanHandle = normalizeHandle(node.handle);
              const visual = resolveRoleVisual(node);
              const leased = isNodeHoldingLease(node);
              const selected =
                selectedHandle != null &&
                cleanHandle.toLowerCase() === selectedHandle.toLowerCase();
              const sub = formatRoleSubtitle(node, visual);
              const actionLine = formatLiveActionLine(node);
              const metricLine = formatNodeMetricLine(node);
              const pct =
                leased && node.progress_pct != null && Number.isFinite(node.progress_pct)
                  ? Math.max(0, Math.min(100, Math.round(node.progress_pct)))
                  : null;

              return (
                <button
                  key={`extra-live-${cleanHandle}`}
                  type="button"
                  className={`lsv-live-overflow-card${!leased ? ' is-live-ready' : ''}${
                    selected ? ' is-selected' : ''
                  }`}
                  style={{
                    ['--lsv-role-color' as string]: visual.color,
                    ['--lsv-role-rgb' as string]: visual.rgb,
                  }}
                  aria-pressed={selected}
                  onClick={() => handleSelectNode(node)}
                >
                  <div className="lsv-card-head">
                    <span
                      className="lsv-role-dot is-pulsing"
                      style={{ background: visual.color }}
                      aria-hidden="true"
                    />
                    <GitHubAvatar
                      handle={cleanHandle}
                      size={18}
                      verified={Boolean(node.github_verified)}
                    />
                    <span className="lsv-card-handle" title={`@${cleanHandle}`}>
                      @{cleanHandle}
                    </span>
                  </div>
                  <div className="lsv-card-sub">
                    {sub.logoSrc && (
                      <img
                        src={sub.logoSrc}
                        alt=""
                        width={12}
                        height={12}
                        className="lsv-card-brand-icon"
                      />
                    )}
                    <span className="lsv-card-sub-text">
                      {sub.roleText}
                      {sub.agentText ? ` · ${sub.agentText}` : ''}
                    </span>
                  </div>
                  <div className="lsv-card-activity" title={actionLine}>
                    <span className="lsv-dot lsv-dot--live" aria-hidden="true" />
                    <span className="lsv-card-activity-text">{actionLine}</span>
                  </div>
                  <div className="lsv-card-stats tabular">
                    <span>{metricLine}</span>
                    {pct != null && (
                      <span className="lsv-card-microbar" aria-hidden="true">
                        <span
                          className="lsv-card-microbar-fill"
                          style={{ width: `${Math.max(8, pct)}%`, background: visual.color }}
                        />
                      </span>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Inline Selected Agent Inspector Strip */}
      {selectedNode && (
        <div
          className="lsv-inspector"
          role="region"
          aria-label={`Live details for @${normalizeHandle(selectedNode.handle)}`}
        >
          <div className="lsv-inspector-main">
            <GitHubAvatar
              handle={selectedNode.handle}
              size={38}
              verified={Boolean(selectedNode.github_verified)}
              activeRing={isNodeActivelyWorking(selectedNode)}
              statusDot={
                selectedNode.status === 'verifying'
                  ? 'verifying'
                  : isNodeActivelyWorking(selectedNode)
                    ? 'working'
                    : 'idle'
              }
            />
            <div className="lsv-inspector-info">
              <div className="lsv-inspector-top">
                {(() => {
                  const selClean = normalizeHandle(selectedNode.handle);
                  const selGhUrl = githubProfileUrl(selClean, Boolean(selectedNode.github_verified));
                  return selGhUrl ? (
                    <a
                      href={selGhUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="lsv-inspector-handle inline-link"
                      title={`Open @${selClean} on GitHub`}
                      aria-label={`Open @${selClean} on GitHub`}
                      style={{ display: 'inline-flex', alignItems: 'center', gap: 5, color: 'inherit' }}
                    >
                      <strong>@{selClean}</strong>
                      <ExternalLink size={13} style={{ opacity: 0.65, flexShrink: 0 }} aria-hidden="true" />
                    </a>
                  ) : (
                    <strong className="lsv-inspector-handle">@{selClean}</strong>
                  );
                })()}
                <span
                  className={`lsv-status-badge ${
                    isNodeActivelyWorking(selectedNode) ? 'is-live' : 'is-recent'
                  }`}
                >
                  <span
                    className={`lsv-dot ${
                      isNodeActivelyWorking(selectedNode) ? 'lsv-dot--live' : 'lsv-dot--offline'
                    }`}
                  />
                  {((selectedNode.role || '').toLowerCase().includes('adjudic') ||
                    (selectedNode.role || '').toLowerCase().includes('tie')) &&
                  isNodeHoldingLease(selectedNode)
                    ? 'Adjudicating'
                    : selectedNode.status === 'verifying'
                      ? 'Peer verifying'
                      : isNodeHoldingLease(selectedNode)
                        ? 'Working live'
                        : (selectedNode.status || '').toLowerCase() === 'recent'
                          ? 'Live · ready for lease'
                          : 'Standby'}
                </span>
                <ProviderModelBadge
                  codingAgent={selectedNode.coding_agent}
                  codingAgentModel={selectedNode.coding_agent_model}
                  provider={selectedNode.provider}
                  model={selectedNode.model}
                  size={14}
                />
              </div>

              <p className="lsv-inspector-whisper">
                {selectedNode.latest_whisper || formatLiveActionLine(selectedNode)}
              </p>

              <div className="lsv-inspector-meta tabular">
                <span>{(selectedNode.tasks_completed ?? 0).toLocaleString('en-IN')} tasks</span>
                <span>{(selectedNode.verifications_done ?? 0).toLocaleString('en-IN')} verified</span>
                {(selectedNode.findings_submitted ?? 0) > 0 && (
                  <span>
                    {(selectedNode.findings_submitted ?? 0).toLocaleString('en-IN')} leads
                  </span>
                )}
                {selectedNode.last_seen_at && (
                  <span>seen {formatRelativeTime(selectedNode.last_seen_at)}</span>
                )}
              </div>
            </div>
          </div>

          <div className="lsv-inspector-actions">
            <Link
              to={`/a/${encodeURIComponent(normalizeHandle(selectedNode.handle))}`}
              className="lsv-inspector-profile-link"
            >
              <span>Agent profile</span>
              <ArrowRight size={13} />
            </Link>
            <button
              type="button"
              className="lsv-inspector-close"
              aria-label="Close agent inspector"
              onClick={() => setSelectedHandle(null)}
            >
              <X size={15} />
            </button>
          </div>
        </div>
      )}

      {/* Expandable Full Live Swarm Roster Drawer */}
      {rosterOpen && (
        <div className="lsv-roster" role="region" aria-label="Full live swarm agent roster">
          <div className="lsv-roster-head">
            <div className="lsv-roster-title-row">
              <Users size={15} />
              <h3>
                All swarm agents ({sortedNodes.length.toLocaleString('en-IN')}
                {totalKnownNodes > sortedNodes.length
                  ? ` shown of ${totalKnownNodes.toLocaleString('en-IN')}`
                  : ''}
                )
              </h3>
            </div>

            <div className="lsv-roster-filters" role="tablist" aria-label="Filter agents">
              {(
                [
                  { id: 'all', label: 'All' },
                  { id: 'working', label: 'Working' },
                  { id: 'verifier', label: 'Verifiers' },
                  { id: 'recent', label: 'Recent' },
                ] as const
              ).map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  role="tab"
                  aria-selected={rosterFilter === tab.id}
                  className={`lsv-roster-tab${rosterFilter === tab.id ? ' is-active' : ''}`}
                  onClick={() => setRosterFilter(tab.id)}
                >
                  {tab.label}
                </button>
              ))}
            </div>
          </div>

          <div className="lsv-roster-grid">
            {filteredRosterNodes.map((node) => {
              const cleanHandle = normalizeHandle(node.handle);
              const visual = resolveRoleVisual(node);
              const active = isNodeActivelyWorking(node);
              const sub = formatRoleSubtitle(node, visual);
              return (
                <Link
                  key={`roster-${cleanHandle}`}
                  to={`/a/${encodeURIComponent(cleanHandle)}`}
                  className="lsv-roster-card"
                >
                  <div className="lsv-roster-card-top">
                    <div className="lsv-roster-card-id">
                      <span
                        className={`lsv-role-dot${active ? ' is-pulsing' : ''}`}
                        style={{ background: visual.color }}
                      />
                      <GitHubAvatar
                        handle={cleanHandle}
                        size={24}
                        verified={Boolean(node.github_verified)}
                      />
                      <span className="lsv-card-handle">@{cleanHandle}</span>
                    </div>
                    <span className="lsv-roster-seen tabular">
                      {node.last_seen_at ? formatTickingAge(node.last_seen_at, nowMs) : 'active'}
                    </span>
                  </div>
                  <div className="lsv-card-sub">
                    {sub.logoSrc && (
                      <img
                        src={sub.logoSrc}
                        alt=""
                        width={13}
                        height={13}
                        className="lsv-card-brand-icon"
                      />
                    )}
                    <span className="lsv-card-sub-text">
                      {sub.roleText}
                      {sub.agentText ? ` · ${sub.agentText}` : ''}
                    </span>
                  </div>
                  <div className="lsv-card-activity">
                    <span className={`lsv-dot ${active ? 'lsv-dot--live' : 'lsv-dot--offline'}`} />
                    <span className="lsv-card-activity-text">{formatLiveActionLine(node)}</span>
                  </div>
                  <div className="lsv-card-stats tabular">{formatNodeMetricLine(node)}</div>
                </Link>
              );
            })}
            {filteredRosterNodes.length === 0 && (
              <div className="lsv-roster-empty">
                <Radio size={14} />
                <span>No agents match this filter right now.</span>
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
