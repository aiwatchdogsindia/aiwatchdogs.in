import { useEffect, useRef, useState } from 'react';
import { BadgeCheck } from 'lucide-react';

export interface WheelRecord {
  id: string;
  title: string;
  amount?: string | null;
}

interface AuditWheelProps {
  /** Number shown in the hub, already formatted. */
  hubValue: string;
  hubLabel: string;
  /** Recent peer-verified records, cycled in the floating card. */
  records: WheelRecord[];
}

/* Geometry (SVG user units). A 24-spoke wheel: records enter at the rim and
   travel inward through the three checks every lead must pass. */
const SIZE = 520;
const C = SIZE / 2;
const SPOKES = 24;
const R_RIM = 244;
const R_MATH = 202;
const R_QUORUM = 150;
const R_VERIFIED = 98;
const R_HUB = 62;

/* Which spokes carry a record, and when. `drop` records stop at the quorum
   ring: not every lead survives review, and cleared records are published too. */
const PARTICLES: { spoke: number; delay: number; duration: number; drop?: boolean }[] = [
  { spoke: 0, delay: 0, duration: 7.4 },
  { spoke: 2, delay: 2.6, duration: 8.2 },
  { spoke: 3, delay: 5.1, duration: 7.8, drop: true },
  { spoke: 5, delay: 1.2, duration: 8.6 },
  { spoke: 7, delay: 4.0, duration: 7.2 },
  { spoke: 8, delay: 6.4, duration: 8.0 },
  { spoke: 10, delay: 0.6, duration: 7.9, drop: true },
  { spoke: 12, delay: 3.3, duration: 7.5 },
  { spoke: 13, delay: 5.8, duration: 8.4 },
  { spoke: 15, delay: 1.9, duration: 7.7 },
  { spoke: 17, delay: 4.6, duration: 8.1, drop: true },
  { spoke: 18, delay: 7.0, duration: 7.3 },
  { spoke: 20, delay: 2.2, duration: 8.5 },
  { spoke: 22, delay: 5.4, duration: 7.6 },
];

function arcPath(r: number, startDeg: number, endDeg: number) {
  const rad = (d: number) => ((d - 90) * Math.PI) / 180;
  const x1 = C + r * Math.cos(rad(startDeg));
  const y1 = C + r * Math.sin(rad(startDeg));
  const x2 = C + r * Math.cos(rad(endDeg));
  const y2 = C + r * Math.sin(rad(endDeg));
  const large = endDeg - startDeg > 180 ? 1 : 0;
  return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${r} ${r} 0 ${large} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
}

const SPOKE_ANGLES = Array.from({ length: SPOKES }, (_, i) => (360 / SPOKES) * i);

export function AuditWheel({ hubValue, hubLabel, records }: AuditWheelProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [recordIndex, setRecordIndex] = useState(0);

  // Cycle the floating "verified" card through real recent records
  useEffect(() => {
    if (records.length < 2) return;
    const id = window.setInterval(() => {
      if (document.visibilityState === 'hidden') return;
      setRecordIndex((i) => (i + 1) % records.length);
    }, 4200);
    return () => window.clearInterval(id);
  }, [records.length]);

  // Pointer parallax: the wheel and its cards drift a few pixels apart
  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
    if (window.matchMedia?.('(hover: none)').matches) return;

    let frame = 0;
    const onMove = (e: PointerEvent) => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const rect = el.getBoundingClientRect();
        const x = (e.clientX - (rect.left + rect.width / 2)) / window.innerWidth;
        const y = (e.clientY - (rect.top + rect.height / 2)) / window.innerHeight;
        el.style.setProperty('--px', x.toFixed(4));
        el.style.setProperty('--py', y.toFixed(4));
      });
    };
    window.addEventListener('pointermove', onMove, { passive: true });
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('pointermove', onMove);
    };
  }, []);

  const record = records.length > 0 ? records[recordIndex % records.length] : null;

  return (
    <div className="aw" ref={rootRef}>
      <div className="aw-stage">
        <div className="aw-sweep" aria-hidden="true" />

        <svg
          className="aw-svg"
          viewBox={`0 0 ${SIZE} ${SIZE}`}
          role="img"
          aria-label="Diagram: public records pass a deterministic math check, a three-of-three peer quorum and verification before a lead is published"
        >
          <defs>
            <path id="aw-arc-math" d={arcPath(R_MATH + 10, 272, 392)} />
            <path id="aw-arc-quorum" d={arcPath(R_QUORUM + 10, 272, 392)} />
            <path id="aw-arc-verified" d={arcPath(R_VERIFIED + 10, 272, 392)} />
          </defs>

          {/* Rim with one tick per spoke */}
          <circle className="aw-rim" cx={C} cy={C} r={R_RIM} />

          {/* Spokes, ticks and the records travelling along them */}
          <g className="aw-turn">
            {SPOKE_ANGLES.map((a) => (
              <g key={a} transform={`rotate(${a} ${C} ${C})`}>
                <line className="aw-spoke" x1={C} y1={C - R_RIM} x2={C} y2={C - R_HUB} />
                <circle className="aw-tick" cx={C} cy={C - R_RIM} r={2.4} />
              </g>
            ))}
            {PARTICLES.map((p) => (
              <g key={p.spoke} transform={`rotate(${(360 / SPOKES) * p.spoke} ${C} ${C})`}>
                <circle
                  className={`aw-dot ${p.drop ? 'aw-dot--drop' : ''}`}
                  cx={C}
                  cy={C - R_RIM}
                  r={4}
                  style={{ animationDelay: `${p.delay}s`, animationDuration: `${p.duration}s` }}
                />
              </g>
            ))}
          </g>

          {/* Check 1: deterministic math */}
          <circle className="aw-ring aw-ring--math" cx={C} cy={C} r={R_MATH} />
          {/* Check 2: three independent agents, drawn as three arcs */}
          <g className="aw-quorum">
            {[0, 120, 240].map((a) => (
              <path key={a} className="aw-ring aw-ring--quorum" d={arcPath(R_QUORUM, a + 8, a + 112)} />
            ))}
          </g>
          {/* Check 3: verified */}
          <circle className="aw-ring aw-ring--verified" cx={C} cy={C} r={R_VERIFIED} />

          <text className="aw-label">
            <textPath href="#aw-arc-math" startOffset="50%" textAnchor="middle">
              01 · DETERMINISTIC MATH
            </textPath>
          </text>
          <text className="aw-label">
            <textPath href="#aw-arc-quorum" startOffset="50%" textAnchor="middle">
              02 · 3-OF-3 PEER QUORUM
            </textPath>
          </text>
          <text className="aw-label">
            <textPath href="#aw-arc-verified" startOffset="50%" textAnchor="middle">
              03 · VERIFIED
            </textPath>
          </text>

          <circle className="aw-hub" cx={C} cy={C} r={R_HUB} />
          <circle className="aw-hub-ring" cx={C} cy={C} r={R_HUB - 7} />
        </svg>

        <div className="aw-hub-text">
          <span className="aw-hub-value tabular">{hubValue}</span>
          <span className="aw-hub-label">{hubLabel}</span>
        </div>
      </div>

      {record && (
        <div className="aw-card aw-card--record">
          <div className="aw-card-head">
            <BadgeCheck size={15} aria-hidden="true" />
            <span>Peer-verified</span>
          </div>
          {/* key restarts the enter animation whenever the record changes */}
          <p key={record.id} className="aw-card-title">
            {record.title}
          </p>
          {record.amount && (
            <p key={`${record.id}-amount`} className="aw-card-amount tabular">
              {record.amount}
            </p>
          )}
        </div>
      )}

      <div className="aw-card aw-card--quorum">
        <span className="aw-card-kicker">Independent checks</span>
        <div className="aw-quorum-row" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
        <span className="aw-card-foot">3 of 3 must agree</span>
      </div>
    </div>
  );
}
