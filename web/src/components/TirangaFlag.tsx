import { useEffect, useId, useState } from 'react';

interface TirangaFlagProps {
  /** Rendered width in px; the flag keeps its 3:2 proportion. */
  width?: number;
  className?: string;
  /** Set false for a still flag (the wave is also off under reduced motion). */
  wave?: boolean;
}

/* Flag geometry in SVG user units: 3:2, three equal bands, and a 24-spoke
   Ashoka Chakra whose diameter is three quarters of the white band. */
const W = 60;
const H = 40;
const BAND = H / 3;
const CHAKRA_R = (BAND * 0.75) / 2;
const SPOKES = Array.from({ length: 24 }, (_, i) => i * 15);

/** One horizontal band whose top and bottom edges ripple by `a` units. */
function band(y0: number, y1: number, a: number) {
  return (
    `M0 ${y0} C${W * 0.25} ${y0 - a} ${W * 0.75} ${y0 + a} ${W} ${y0} ` +
    `L${W} ${y1} C${W * 0.75} ${y1 + a} ${W * 0.25} ${y1 - a} 0 ${y1} Z`
  );
}

const AMP = 1.5;
const DUR = '5.2s';
const SPLINES = '0.45 0 0.55 1;0.45 0 0.55 1';

export function TirangaFlag({ width = 36, className = '', wave = true }: TirangaFlagProps) {
  const clipId = useId();
  const [animate, setAnimate] = useState(false);

  // Only wave when the visitor has not asked for reduced motion
  useEffect(() => {
    if (!wave) return;
    const query = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    if (!query) {
      setAnimate(true);
      return;
    }
    const sync = () => setAnimate(!query.matches);
    sync();
    query.addEventListener('change', sync);
    return () => query.removeEventListener('change', sync);
  }, [wave]);

  const bands = [
    { y0: -2, y1: BAND, fill: 'var(--flag-saffron)' },
    { y0: BAND, y1: BAND * 2, fill: 'var(--flag-white)' },
    { y0: BAND * 2, y1: H + 2, fill: 'var(--flag-green)' },
  ];

  return (
    <svg
      className={`tiranga ${className}`}
      width={width}
      height={(width * H) / W}
      viewBox={`0 0 ${W} ${H}`}
      role="img"
      aria-label="Flag of India"
    >
      <defs>
        <clipPath id={clipId}>
          <rect width={W} height={H} rx={3.5} />
        </clipPath>
      </defs>

      <g clipPath={`url(#${clipId})`}>
        {bands.map((b) => (
          <path key={b.fill} d={band(b.y0, b.y1, animate ? AMP : 0)} fill={b.fill}>
            {animate && (
              <animate
                attributeName="d"
                dur={DUR}
                repeatCount="indefinite"
                calcMode="spline"
                keySplines={SPLINES}
                values={`${band(b.y0, b.y1, AMP)};${band(b.y0, b.y1, -AMP)};${band(b.y0, b.y1, AMP)}`}
              />
            )}
          </path>
        ))}

        {/* Ashoka Chakra */}
        <g className="tiranga-chakra" fill="none" stroke="var(--flag-navy)">
          <circle cx={W / 2} cy={H / 2} r={CHAKRA_R} strokeWidth={0.9} />
          {SPOKES.map((angle) => (
            <line
              key={angle}
              x1={W / 2}
              y1={H / 2}
              x2={W / 2}
              y2={H / 2 - CHAKRA_R}
              strokeWidth={0.42}
              transform={`rotate(${angle} ${W / 2} ${H / 2})`}
            />
          ))}
          <circle cx={W / 2} cy={H / 2} r={0.9} fill="var(--flag-navy)" stroke="none" />
        </g>

        {/* Soft sheen that travels with the ripple */}
        {animate && <rect className="tiranga-sheen" x={-W} y={0} width={W} height={H} />}
      </g>

      <rect className="tiranga-edge" x={0.4} y={0.4} width={W - 0.8} height={H - 0.8} rx={3.2} />
    </svg>
  );
}
