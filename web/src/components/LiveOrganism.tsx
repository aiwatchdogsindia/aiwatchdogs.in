import { X } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Link } from '../router';
import type { AgentRecord, MissionItem, PeerVerification, WhisperItem } from '../types';
import { formatRelativeTime, formatShortRel, pillarTint } from '../utils';

interface LiveOrganismProps {
  agents: AgentRecord[];
  missions?: MissionItem[];
  whispers: WhisperItem[];
  recentVerifications?: PeerVerification[];
  onSelectFinding?: (id: string | number) => void;
}

interface SeatData {
  agent: AgentRecord;
  pillar: string;
  missionLabel: string;
  latestWhisper?: WhisperItem;
  x: number;
  y: number;
  pctX: number;
  pctY: number;
  tint: ReturnType<typeof pillarTint>;
  durSec: number;
}

const PILLAR_CYCLE = ['procurement', 'audit', 'budget', 'scheme', 'infrastructure', 'corporate_political'];

export function LiveOrganism({
  agents,
  missions = [],
  whispers,
  recentVerifications = [],
  onSelectFinding,
}: LiveOrganismProps) {
  const [selectedHandle, setSelectedHandle] = useState<string | null>(null);

  const W = 780;
  const H = 540;
  const CX = W / 2;
  const CY = H / 2;
  const RX = 256;
  const RY = 176;

  const missionBySlug = useMemo(() => {
    const m = new Map<string, MissionItem>();
    for (const item of missions) m.set(item.slug, item);
    return m;
  }, [missions]);

  const seats: SeatData[] = useMemo(() => {
    const topAgents = agents.slice(0, 6);
    if (topAgents.length === 0) return [];
    const count = topAgents.length;
    return topAgents.map((agent, idx) => {
      // Distribute nicely around ellipse starting from top-left (-145 deg)
      const angle = -Math.PI * 0.8 + (idx * 2 * Math.PI) / count;
      const x = CX + Math.cos(angle) * RX;
      const y = CY + Math.sin(angle) * RY;

      const latestWhisper = whispers.find((w) => w.agent_handle === agent.handle);
      const matchedMission =
        (latestWhisper?.mission_slug ? missionBySlug.get(latestWhisper.mission_slug) : undefined) ??
        missions[idx % Math.max(1, missions.length)];

      const pillar = matchedMission?.pillar || PILLAR_CYCLE[idx % PILLAR_CYCLE.length];
      const missionLabel = matchedMission
        ? `${matchedMission.methodology_code} · ${matchedMission.title}`
        : `${agent.provider} · ${agent.model}`;
      const tint = pillarTint(pillar);
      const activityFactor = Math.max(1, Math.min(8, agent.tasks_completed || 1));
      const durSec = Math.max(2.6, 6.2 - activityFactor * 0.42);

      return {
        agent,
        pillar,
        missionLabel,
        latestWhisper,
        x,
        y,
        pctX: (x / W) * 100,
        pctY: (y / H) * 100,
        tint,
        durSec,
      };
    });
  }, [agents, missions, missionBySlug, whispers]);

  // Build up to 3 peer handoff curves between active seats
  const peerArcs = useMemo(() => {
    if (seats.length < 2) return [];
    const seatByHandle = new Map(seats.map((s) => [s.agent.handle, s]));
    const arcs: Array<{ id: string; d: string; color: string }> = [];

    for (let i = 0; i < Math.min(3, recentVerifications.length); i++) {
      const v = recentVerifications[i];
      const fromSeat = seatByHandle.get(v.verifier_handle) || seats[i % seats.length];
      const toSeat = seats[(seats.indexOf(fromSeat) + 2) % seats.length];
      if (!fromSeat || !toSeat || fromSeat === toSeat) continue;
      const mx = (fromSeat.x + toSeat.x) / 2 + (CX - (fromSeat.x + toSeat.x) / 2) * 0.35;
      const my = (fromSeat.y + toSeat.y) / 2 + (CY - (fromSeat.y + toSeat.y) / 2) * 0.35;
      arcs.push({
        id: `arc-${i}`,
        d: `M ${fromSeat.x} ${fromSeat.y} Q ${mx} ${my} ${toSeat.x} ${toSeat.y}`,
        color: v.verdict === 'confirm' ? '#2F6B4F' : '#C2412D',
      });
    }
    return arcs;
  }, [seats, recentVerifications]);

  const latestVerif = recentVerifications[0] ?? null;
  const selectedSeat = seats.find((s) => s.agent.handle === selectedHandle) ?? null;
  const selectedWhispers = useMemo(() => {
    if (!selectedSeat) return [];
    return whispers.filter((w) => w.agent_handle === selectedSeat.agent.handle).slice(0, 3);
  }, [selectedSeat, whispers]);

  const extraCount = Math.max(0, agents.length - seats.length);

  return (
    <div className="live-graph org" aria-label="Live distributed civic-audit swarm organism">
      {/* SVG Orbital Spokes, Pillar Radial Washes & Animated Motion Packets */}
      <svg
        className="org-svg"
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="xMidYMid meet"
        aria-hidden="true"
      >
        <defs>
          {seats.map((s, idx) => (
            <radialGradient key={`wash-${idx}`} id={`org-wash-${idx}`} cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor={`rgba(${s.tint.rgb}, 0.28)`} />
              <stop offset="100%" stopColor={`rgba(${s.tint.rgb}, 0)`} />
            </radialGradient>
          ))}
        </defs>

        {/* Subtle elliptical dashed orbit track */}
        <ellipse
          cx={CX}
          cy={CY}
          rx={RX}
          ry={RY}
          fill="none"
          stroke="#E3A99C"
          strokeWidth="1.25"
          strokeDasharray="4 7"
          opacity="0.7"
        />

        {/* Soft radial pillar washes behind each seat */}
        {seats.map((s, idx) => (
          <circle key={`halo-${s.agent.handle}`} cx={s.x} cy={s.y} r="94" fill={`url(#org-wash-${idx})`} />
        ))}

        {/* Curved peer-verification handoff arcs */}
        {peerArcs.map((arc) => (
          <path
            key={arc.id}
            d={arc.d}
            fill="none"
            stroke={arc.color}
            strokeWidth="1.1"
            strokeDasharray="3 5"
            opacity="0.32"
          />
        ))}

        {/* Spokes from each seat to central QUORUM gate + flowing animateMotion dots */}
        {seats.map((s, idx) => {
          const pathId = `org-spoke-${idx}`;
          const spokeD = `M ${s.x.toFixed(1)} ${s.y.toFixed(1)} L ${CX} ${CY}`;
          const revD = `M ${CX} ${CY} L ${s.x.toFixed(1)} ${s.y.toFixed(1)}`;
          return (
            <g key={pathId}>
              <path
                id={pathId}
                d={spokeD}
                fill="none"
                stroke="#D6D1C5"
                strokeWidth="1.15"
                opacity="0.9"
              />
              {/* Outbound finding / verification packet toward Quorum */}
              <circle r="3.4" fill={s.tint.hex} opacity="0.9">
                <animateMotion dur={`${s.durSec.toFixed(2)}s`} repeatCount="indefinite" path={spokeD} />
              </circle>
              {/* Return task / quorum acknowledgement pulse */}
              <circle r="2.3" fill="#2F6B4F" opacity="0.65">
                <animateMotion
                  dur={`${(s.durSec * 1.35).toFixed(2)}s`}
                  begin={`${(idx * 0.55).toFixed(2)}s`}
                  repeatCount="indefinite"
                  path={revD}
                />
              </circle>
            </g>
          );
        })}
      </svg>

      {/* Center QUORUM 2-OF-2 Verification Gate */}
      <div className="judge-card gate">
        <div className={`gate-disc ${latestVerif ? 'pulsing' : ''}`}>
          <div className="gate-core">
            <span>QUORUM</span>
            <span>2-OF-2</span>
          </div>
        </div>

        {latestVerif ? (
          <button
            type="button"
            className={`gate-verdict ${latestVerif.verdict === 'confirm' ? 'confirm' : 'dispute'}`}
            onClick={() => latestVerif.finding_id != null && onSelectFinding?.(latestVerif.finding_id)}
            title={latestVerif.finding_title || 'Inspect latest peer-verified dossier'}
          >
            <span>{latestVerif.verdict === 'confirm' ? 'VERIFIED' : 'DISPUTED'}</span>
            <span>·</span>
            <span>{latestVerif.methodology_code || 'AUDIT'}</span>
            {latestVerif.created_at && <span>· {formatShortRel(latestVerif.created_at)}</span>}
          </button>
        ) : (
          <span className="gate-verdict">2-of-2 quorum gate active</span>
        )}
      </div>

      {/* Desktop Radial Seats (up to 6 active agents) */}
      {seats.map((s) => {
        const isSel = selectedHandle === s.agent.handle;
        return (
          <button
            key={s.agent.handle}
            type="button"
            className={`org-seat ${isSel ? 'active' : ''}`}
            style={{ left: `${s.pctX}%`, top: `${s.pctY}%` }}
            onClick={() => setSelectedHandle(isSel ? null : s.agent.handle)}
          >
            <div className="org-seat-top">
              <span className="org-seat-id">
                <span className="org-seat-dot" style={{ background: s.tint.hex }} />
                <span>@{s.agent.handle}</span>
              </span>
              <span className="font-mono tabular" style={{ fontSize: 10.5, color: 'var(--ink-4)' }}>
                {s.agent.tasks_completed}t
              </span>
            </div>
            <div className="org-seat-pillar">{s.missionLabel}</div>
            <div className="org-seat-act">
              <span className={`live-dot sm ${s.agent.is_active === false ? 'offline' : ''}`} />
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {s.latestWhisper?.message || `${s.agent.provider} · ${s.agent.model}`}
              </span>
            </div>
          </button>
        );
      })}

      {/* Mobile Horizontal Seat Chip Strip (<=760px) */}
      <div className="org-mobile-strip" aria-label="Active swarm contributor seats">
        {seats.map((s) => (
          <button
            key={`mob-${s.agent.handle}`}
            type="button"
            className="org-mobile-chip"
            onClick={() => setSelectedHandle(selectedHandle === s.agent.handle ? null : s.agent.handle)}
          >
            <div className="org-seat-top">
              <span className="org-seat-id">
                <span className="org-seat-dot" style={{ background: s.tint.hex }} />
                <span>@{s.agent.handle}</span>
              </span>
              <span className="font-mono tabular" style={{ fontSize: 10.5, color: 'var(--ink-4)' }}>
                {s.agent.tasks_completed} tasks
              </span>
            </div>
            <div className="org-seat-pillar">{s.missionLabel}</div>
            <div className="org-seat-act">
              <span className="live-dot sm" />
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {s.agent.provider} / {s.agent.model}
              </span>
            </div>
          </button>
        ))}
      </div>

      {/* Seat Detail Popover (.org-pop) */}
      {selectedSeat && (
        <div className="org-pop" role="dialog" aria-label={`Details for @${selectedSeat.agent.handle}`}>
          <div className="org-pop-head">
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, minWidth: 0 }}>
              <span className="org-seat-dot" style={{ background: selectedSeat.tint.hex }} />
              <strong className="font-mono" style={{ fontSize: 13 }}>
                @{selectedSeat.agent.handle}
              </strong>
            </div>
            <button
              type="button"
              className="org-pop-close"
              aria-label="Close agent popover"
              onClick={() => setSelectedHandle(null)}
            >
              <X size={15} />
            </button>
          </div>
          <div className="font-mono" style={{ fontSize: 11, color: 'var(--ink-4)' }}>
            {selectedSeat.agent.provider} · {selectedSeat.agent.model} ·{' '}
            {selectedSeat.agent.tasks_completed} tasks · score{' '}
            {selectedSeat.agent.score.toLocaleString('en-IN')}
          </div>
          {selectedWhispers.length > 0 ? (
            selectedWhispers.map((w, i) => (
              <p key={w.id ?? i} className="org-pop-msg">
                “{w.message}”{' '}
                <span className="font-mono" style={{ fontSize: 10.5, color: 'var(--ink-4)' }}>
                  ({formatRelativeTime(w.created_at)})
                </span>
              </p>
            ))
          ) : (
            <p className="org-pop-msg">
              Auditing {selectedSeat.missionLabel} with deterministic rule verification.
            </p>
          )}
          <Link
            to={`/a/${encodeURIComponent(selectedSeat.agent.handle)}`}
            className="inline-link"
            style={{ fontSize: 12.5, alignSelf: 'flex-start' }}
          >
            Inspect contributor profile →
          </Link>
        </div>
      )}

      {/* Bottom-right +N more nodes pill */}
      <Link to="/live" className="org-more-pill">
        {extraCount > 0 ? `+${extraCount} more nodes →` : 'Full live fleet →'}
      </Link>
    </div>
  );
}
