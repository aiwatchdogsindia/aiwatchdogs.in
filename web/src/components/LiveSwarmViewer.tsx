import {
  ArrowRight,
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
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
  /** Hide the outer header when embedded inside a section that supplies its own heading. */
  hideHeader?: boolean;
  /** Optional extra CSS class on the outer container. */
  className?: string;
}

/* =========================================================================
   Geometry & Visual Constants (920 × 580 hexagonal radial stage)
   ========================================================================= */

const STAGE_W = 920;
const STAGE_H = 580;
const HUB_X = STAGE_W / 2;
const HUB_Y = STAGE_H / 2;
const ORBIT_RX = 292;
const ORBIT_RY = 192;

interface OrbitSlotCoords {
  x: number;
  y: number;
  pctX: number;
  pctY: number;
}

/**
 * Six balanced hexagonal positions matching the gallery symmetry:
 * Top, Upper-Right, Lower-Right, Bottom, Lower-Left, Upper-Left.
 */
const ORBIT_SLOTS: readonly OrbitSlotCoords[] = [
  { x: 460, y: 98, pctX: (460 / STAGE_W) * 100, pctY: (98 / STAGE_H) * 100 },
  { x: 752, y: 200, pctX: (752 / STAGE_W) * 100, pctY: (200 / STAGE_H) * 100 },
  { x: 752, y: 384, pctX: (752 / STAGE_W) * 100, pctY: (384 / STAGE_H) * 100 },
  { x: 460, y: 482, pctX: (460 / STAGE_W) * 100, pctY: (482 / STAGE_H) * 100 },
  { x: 168, y: 384, pctX: (168 / STAGE_W) * 100, pctY: (384 / STAGE_H) * 100 },
  { x: 168, y: 200, pctX: (168 / STAGE_W) * 100, pctY: (200 / STAGE_H) * 100 },
];

interface RoleVisual {
  key: 'auditor' | 'peer_verifier' | 'adjudicator' | 'idle';
  label: string;
  color: string;
  rgb: string;
  spokeOpacity: number;
}

const ROLE_VISUALS: Record<RoleVisual['key'], RoleVisual> = {
  auditor: {
    key: 'auditor',
    label: 'Auditor',
    color: '#C85A32',
    rgb: '200, 90, 50',
    spokeOpacity: 0.78,
  },
  peer_verifier: {
    key: 'peer_verifier',
    label: 'Peer Verifier',
    color: '#138808',
    rgb: '19, 136, 8',
    spokeOpacity: 0.76,
  },
  adjudicator: {
    key: 'adjudicator',
    label: 'Adjudicator',
    color: '#A18324',
    rgb: '161, 131, 36',
    spokeOpacity: 0.76,
  },
  idle: {
    key: 'idle',
    label: 'Contributor',
    color: '#9E978B',
    rgb: '158, 151, 139',
    spokeOpacity: 0.48,
  },
};

function isNodeActivelyWorking(node: LiveSwarmNode): boolean {
  const s = (node.status || '').toLowerCase();
  return s === 'working' || s === 'verifying' || s === 'active';
}

function resolveRoleVisual(node: LiveSwarmNode): RoleVisual {
  const status = (node.status || '').toLowerCase();
  const role = (node.role || '').toLowerCase();

  if (status === 'idle' || status === 'recent') {
    return ROLE_VISUALS.idle;
  }
  if (role.includes('verif') || status === 'verifying') {
    return ROLE_VISUALS.peer_verifier;
  }
  if (role.includes('adjudic') || role.includes('tie')) {
    return ROLE_VISUALS.adjudicator;
  }
  if (role.includes('audit') || status === 'working' || status === 'active') {
    return ROLE_VISUALS.auditor;
  }
  return ROLE_VISUALS.idle;
}

function statusPriority(node: LiveSwarmNode): number {
  const s = (node.status || '').toLowerCase();
  if (s === 'working') return 0;
  if (s === 'verifying') return 1;
  if (s === 'active') return 2;
  if (s === 'recent') return 3;
  return 4;
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
  const rawRole = (node.role || '').toLowerCase();
  const roleText =
    rawRole.includes('verif')
      ? 'Peer Verifier'
      : rawRole.includes('adjudic')
        ? 'Adjudicator'
        : rawRole.includes('audit')
          ? 'Auditor'
          : isNodeActivelyWorking(node)
            ? visual.label
            : node.status === 'recent'
              ? 'Recent Auditor'
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
    status === 'verifying' || role.includes('verif')
      ? 'verifying'
      : role.includes('adjudic')
        ? 'adjudicating'
        : 'auditing';

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
  if (node.latest_whisper) {
    return node.latest_whisper;
  }
  if (node.current_project_slug) {
    return `${verb} ${node.current_project_slug}`;
  }
  return status === 'recent' ? 'recently checked records' : 'ready for next lease';
}

function formatNodeMetricLine(node: LiveSwarmNode): string {
  const parts: string[] = [];
  const tasks = node.tasks_completed ?? 0;
  const verified = node.verifications_done ?? 0;
  const submitted = node.findings_submitted ?? 0;

  if (tasks > 0) {
    parts.push(`${tasks.toLocaleString('en-IN')} task${tasks === 1 ? '' : 's'}`);
  }
  if (node.progress_pct != null && Number.isFinite(node.progress_pct) && isNodeActivelyWorking(node)) {
    parts.push(`${Math.max(0, Math.min(100, Math.round(node.progress_pct)))}%`);
  } else if (verified > 0) {
    parts.push(`${verified.toLocaleString('en-IN')} verified`);
  } else if (submitted > 0) {
    parts.push(`${submitted.toLocaleString('en-IN')} lead${submitted === 1 ? '' : 's'}`);
  }

  if (parts.length === 0) {
    return isNodeActivelyWorking(node) ? 'active lease' : '0 tasks yet';
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
        et === 'scan_cleared' ||
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
      verb: 'quorum 2-of-2',
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
  } else if (et === 'scan_cleared' || et === 'website_batch_observed') {
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
  hideHeader = false,
  className = '',
}: LiveSwarmViewerProps) {
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

  // Deduplicate and sort nodes: active workers first, then verifiers, then recent/idle
  const sortedNodes = useMemo(() => {
    const raw = snapshot?.active_nodes ?? [];
    const seen = new Set<string>();
    const deduped: LiveSwarmNode[] = [];
    for (const node of raw) {
      const key = normalizeHandle(node.handle).toLowerCase();
      if (!key || seen.has(key)) continue;
      seen.add(key);
      deduped.push(node);
    }
    return deduped.sort((a, b) => {
      const diff = statusPriority(a) - statusPriority(b);
      if (diff !== 0) return diff;
      return (b.tasks_completed ?? 0) - (a.tasks_completed ?? 0);
    });
  }, [snapshot?.active_nodes]);

  const orbitItems: OrbitSeatItem[] = useMemo(() => {
    const realNodes = sortedNodes.slice(0, 6);
    const items: OrbitSeatItem[] = realNodes.map((node, idx) => ({
      slotIndex: idx,
      coords: ORBIT_SLOTS[idx],
      kind: 'node',
      node,
      visual: resolveRoleVisual(node),
    }));

    if (realNodes.length === 0) {
      // Never look dead or empty: render inviting "Your agent here" + 2 standby role slots
      items.push(
        {
          slotIndex: 0,
          coords: ORBIT_SLOTS[0],
          kind: 'invite',
        },
        {
          slotIndex: 2,
          coords: ORBIT_SLOTS[2],
          kind: 'standby',
          standbyRole: 'Peer Verifier',
          standbyLabel: 'Independent 2-of-2 check slot',
        },
        {
          slotIndex: 4,
          coords: ORBIT_SLOTS[4],
          kind: 'standby',
          standbyRole: 'Civic Auditor',
          standbyLabel: 'Public budget & .gov.in slot',
        },
      );
    } else if (realNodes.length < 6) {
      // Add one crisp inviting dashed card in the next orbit slot
      items.push({
        slotIndex: realNodes.length,
        coords: ORBIT_SLOTS[realNodes.length],
        kind: 'invite',
      });
    }

    return items;
  }, [sortedNodes]);

  // Subtle curved dotted collaboration arcs between active peers (e.g. Auditor <-> Verifier)
  const peerArcs = useMemo(() => {
    const realSeats = orbitItems.filter((item) => item.kind === 'node' && item.node);
    if (realSeats.length < 2) return [];
    const arcs: Array<{ id: string; d: string; color: string }> = [];

    for (let i = 0; i < realSeats.length && arcs.length < 2; i++) {
      const a = realSeats[i];
      const b = realSeats[(i + 1) % realSeats.length];
      if (!a.node || !b.node) continue;
      const sameProject =
        a.node.current_project_slug &&
        a.node.current_project_slug === b.node.current_project_slug;
      const bothActive = isNodeActivelyWorking(a.node) && isNodeActivelyWorking(b.node);
      if (!sameProject && !bothActive) continue;

      const mx = (a.coords.x + b.coords.x) / 2 + (HUB_X - (a.coords.x + b.coords.x) / 2) * 0.24;
      const my = (a.coords.y + b.coords.y) / 2 + (HUB_Y - (a.coords.y + b.coords.y) / 2) * 0.24;
      arcs.push({
        id: `peer-${a.slotIndex}-${b.slotIndex}`,
        d: `M ${a.coords.x} ${a.coords.y} Q ${mx.toFixed(1)} ${my.toFixed(1)} ${b.coords.x} ${b.coords.y}`,
        color: a.visual?.color ?? '#C85A32',
      });
    }
    return arcs;
  }, [orbitItems]);

  const displayedRealCount = Math.min(sortedNodes.length, 6);
  const totalKnownNodes = Math.max(sortedNodes.length, snapshot?.total_agents ?? 0);
  const overflowCount = Math.max(0, totalKnownNodes - displayedRealCount);
  const workingNow = snapshot?.working_now ?? sortedNodes.filter(isNodeActivelyWorking).length;
  const activeLeases = snapshot?.active_leases_count ?? workingNow;
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
    return sortedNodes.filter((n) => !isNodeActivelyWorking(n));
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

  return (
    <section
      className={`lsv-root${className ? ` ${className}` : ''}`}
      aria-label="Live swarm viewer"
    >
      {!hideHeader && (
        <div className="lsv-header">
          <div className="lsv-header-left">
            <h2 className="lsv-title">{title}</h2>
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
        className={`lsv-stage${animateActive ? ' is-animating' : ' is-paused'}`}
        data-working-count={workingNow}
      >
        <div className="lsv-canvas-frame">
          {/* Desktop Radial SVG Canvas */}
          <svg
            className="lsv-svg"
            viewBox={`0 0 ${STAGE_W} ${STAGE_H}`}
            preserveAspectRatio="xMidYMid meet"
            aria-hidden="true"
          >
          <defs>
            {orbitItems.map((item) => {
              if (item.kind !== 'node' || !item.visual) return null;
              return (
                <radialGradient
                  key={`aura-${item.slotIndex}`}
                  id={`lsv-aura-${item.slotIndex}`}
                  cx="50%"
                  cy="50%"
                  r="50%"
                >
                  <stop offset="0%" stopColor={`rgba(${item.visual.rgb}, 0.22)`} />
                  <stop offset="100%" stopColor={`rgba(${item.visual.rgb}, 0)`} />
                </radialGradient>
              );
            })}
          </defs>

          {/* Outer dotted orbital path connecting all 6 positions */}
          <ellipse
            cx={HUB_X}
            cy={HUB_Y}
            rx={ORBIT_RX}
            ry={ORBIT_RY}
            fill="none"
            stroke="rgba(198, 134, 114, 0.34)"
            strokeWidth="1.25"
            strokeDasharray="3 7"
          />

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
                r="102"
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
            const active = isNode && isNodeActivelyWorking(item.node!);
            const selected =
              isNode &&
              selectedHandle != null &&
              normalizeHandle(item.node!.handle).toLowerCase() === selectedHandle.toLowerCase();
            const strokeColor = isNode
              ? item.visual?.color ?? '#9E978B'
              : '#C9C2B6';
            const strokeWidth = selected ? 2.8 : active ? 2.15 : 1.2;
            const opacity = selected ? 0.95 : isNode ? item.visual?.spokeOpacity ?? 0.5 : 0.45;
            const spokeFromHub = `M ${HUB_X} ${HUB_Y} L ${item.coords.x} ${item.coords.y}`;
            const spokeToHub = `M ${item.coords.x} ${item.coords.y} L ${HUB_X} ${HUB_Y}`;
            const midX = (HUB_X + item.coords.x) / 2;
            const midY = (HUB_Y + item.coords.y) / 2;
            const durSec = Math.max(2.4, 4.1 - (item.slotIndex % 3) * 0.45);

            return (
              <g key={`spoke-${item.slotIndex}`}>
                <path
                  d={spokeFromHub}
                  fill="none"
                  stroke={strokeColor}
                  strokeWidth={strokeWidth}
                  strokeDasharray={isNode ? undefined : '4 6'}
                  strokeLinecap="round"
                  opacity={opacity}
                />

                {/* Static midpoint jewel visible when reduced-motion or idle */}
                {isNode && (
                  <circle
                    cx={midX}
                    cy={midY}
                    r={active ? 2.6 : 2.1}
                    fill={strokeColor}
                    opacity={active ? 0.85 : 0.55}
                  />
                )}

                {/* Animated packets pulsing along actively working spokes */}
                {active && animateActive && (
                  <>
                    <circle r="3.4" fill={strokeColor} opacity="0.95">
                      <animateMotion
                        dur={`${durSec.toFixed(2)}s`}
                        begin={`${(item.slotIndex * 0.45).toFixed(2)}s`}
                        repeatCount="indefinite"
                        path={spokeToHub}
                      />
                    </circle>
                    <circle r="2.2" fill="#1FA319" opacity="0.72">
                      <animateMotion
                        dur={`${(durSec * 1.35).toFixed(2)}s`}
                        begin={`${(item.slotIndex * 0.65 + 0.9).toFixed(2)}s`}
                        repeatCount="indefinite"
                        path={spokeFromHub}
                      />
                    </circle>
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
              {workingNow > 0 && (
                <span className="lsv-hub-count tabular">{workingNow} live</span>
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
                    <span>Instant opt-in · zero prompts</span>
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
            const active = isNodeActivelyWorking(node);
            const selected =
              selectedHandle != null &&
              cleanHandle.toLowerCase() === selectedHandle.toLowerCase();
            const sub = formatRoleSubtitle(node, visual);
            const actionLine = formatLiveActionLine(node);
            const metricLine = formatNodeMetricLine(node);
            const pct =
              node.progress_pct != null && Number.isFinite(node.progress_pct)
                ? Math.max(0, Math.min(100, Math.round(node.progress_pct)))
                : null;

            return (
              <button
                key={`node-${cleanHandle}`}
                type="button"
                className={`lsv-card${active ? ' is-working' : ' is-idle'}${
                  selected ? ' is-selected' : ''
                }`}
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
                    className={`lsv-role-dot${active ? ' is-pulsing' : ''}`}
                    style={{ background: visual.color }}
                    aria-hidden="true"
                  />
                  <GitHubAvatar handle={cleanHandle} size={20} />
                  <span className="lsv-card-handle" title={`@${cleanHandle}`}>
                    @{cleanHandle}
                  </span>
                </div>

                <div className="lsv-card-sub" title={`${sub.roleText}${sub.agentText ? ` · ${sub.agentText}` : ''}`}>
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

                <div className="lsv-card-activity" title={actionLine}>
                  <span
                    className={`lsv-dot ${active ? 'lsv-dot--live' : 'lsv-dot--offline'}`}
                    aria-hidden="true"
                  />
                  <span className="lsv-card-activity-text">{actionLine}</span>
                </div>

                <div className="lsv-card-stats tabular">
                  <span>{metricLine}</span>
                  {pct != null && active && (
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

        {/* Mobile / Narrow Viewport Clean Grid Layout */}
        <div className="lsv-mobile-grid" aria-label="Live swarm agents">
          {orbitItems.map((item) => {
            if (item.kind === 'invite') {
              return (
                <button
                  key={`mob-invite-${item.slotIndex}`}
                  type="button"
                  className={`lsv-mob-card lsv-mob-card--invite${copiedInvite ? ' is-copied' : ''}`}
                  onClick={handleCopyInvite}
                >
                  <div className="lsv-card-head">
                    <Sparkles size={14} color="var(--accent-deep)" />
                    <span className="lsv-card-handle">Your agent here</span>
                  </div>
                  <div className="lsv-card-sub">
                    {copiedInvite ? 'Copied! Paste into your coding agent' : 'Tap to copy one line & join live'}
                  </div>
                </button>
              );
            }
            if (item.kind === 'standby') return null;

            const node = item.node!;
            const visual = item.visual!;
            const cleanHandle = normalizeHandle(node.handle);
            const active = isNodeActivelyWorking(node);
            const selected =
              selectedHandle != null &&
              cleanHandle.toLowerCase() === selectedHandle.toLowerCase();
            const sub = formatRoleSubtitle(node, visual);
            const actionLine = formatLiveActionLine(node);
            const metricLine = formatNodeMetricLine(node);

            return (
              <button
                key={`mob-${cleanHandle}`}
                type="button"
                className={`lsv-mob-card${selected ? ' is-selected' : ''}`}
                style={{ ['--lsv-role-color' as string]: visual.color }}
                aria-pressed={selected}
                onClick={() => handleSelectNode(node)}
              >
                <div className="lsv-card-head">
                  <span
                    className={`lsv-role-dot${active ? ' is-pulsing' : ''}`}
                    style={{ background: visual.color }}
                    aria-hidden="true"
                  />
                  <GitHubAvatar handle={cleanHandle} size={22} />
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
                  <span className={`lsv-dot ${active ? 'lsv-dot--live' : 'lsv-dot--offline'}`} />
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
            <span className="lsv-legend-dot" style={{ background: ROLE_VISUALS.peer_verifier.color }} />
            Verifier
          </span>
          <span className="lsv-legend-item">
            <span className="lsv-legend-dot" style={{ background: ROLE_VISUALS.adjudicator.color }} />
            Adjudicator
          </span>
        </div>

        {/* Bottom-Right Overflow Pill ("... +N more agents") */}
        {overflowCount > 0 && (
          <button
            type="button"
            className={`lsv-more-pill${rosterOpen ? ' is-open' : ''}`}
            aria-expanded={rosterOpen}
            onClick={() => setRosterOpen((v) => !v)}
          >
            <span className="lsv-more-dots" aria-hidden="true">•••</span>
            <span>
              +{overflowCount.toLocaleString('en-IN')} more agent{overflowCount === 1 ? '' : 's'}
            </span>
            {rosterOpen ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
          </button>
        )}
      </div>

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
              activeRing={isNodeActivelyWorking(selectedNode)}
              statusDot={
                isNodeActivelyWorking(selectedNode)
                  ? (selectedNode.status as 'working' | 'verifying')
                  : 'recent'
              }
            />
            <div className="lsv-inspector-info">
              <div className="lsv-inspector-top">
                <strong className="lsv-inspector-handle">
                  @{normalizeHandle(selectedNode.handle)}
                </strong>
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
                  {selectedNode.status === 'verifying'
                    ? 'Peer verifying'
                    : isNodeActivelyWorking(selectedNode)
                      ? 'Working live'
                      : 'Recently active'}
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
                      <GitHubAvatar handle={cleanHandle} size={24} />
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
