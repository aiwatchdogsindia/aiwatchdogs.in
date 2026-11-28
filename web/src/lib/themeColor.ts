/**
 * Light -> dark colour mapping.
 *
 * The site is designed on white paper. Dark mode keeps every hue and flips
 * lightness in OKLCH, so each ink/paper pair keeps its contrast:
 *   - white paper becomes near-black, near-black ink becomes ivory;
 *   - pale tints (washes, hairlines) become deep tints of the same hue;
 *   - dark saturated colours (navy, deep green, brick) are lifted until they
 *     read on dark paper;
 *   - saturated mid-tones (saffron, India green marks) already read on both
 *     papers and are left alone.
 *
 * The dark values in `styles/tokens.css` were generated with this same
 * function, so colours drawn from JavaScript (canvas, inline SVG) match the
 * stylesheet.
 */

export interface Rgba {
  r: number;
  g: number;
  b: number;
  a: number;
}

const NAMED: Record<string, string> = { white: '#ffffff', black: '#000000' };

export function parseColor(input: string): Rgba | null {
  let s = input.trim().toLowerCase();
  if (NAMED[s]) s = NAMED[s];
  if (s.startsWith('#')) {
    let h = s.slice(1);
    if (h.length === 3 || h.length === 4) h = [...h].map((c) => c + c).join('');
    if ((h.length !== 6 && h.length !== 8) || /[^0-9a-f]/.test(h)) return null;
    return {
      r: parseInt(h.slice(0, 2), 16),
      g: parseInt(h.slice(2, 4), 16),
      b: parseInt(h.slice(4, 6), 16),
      a: h.length === 8 ? parseInt(h.slice(6, 8), 16) / 255 : 1,
    };
  }
  const m = s.match(/^rgba?\(([^)]*)\)$/);
  if (!m) return null;
  const parts = m[1].split(/[\s,/]+/).filter(Boolean).map(Number);
  if (parts.length < 3 || parts.some((n) => Number.isNaN(n))) return null;
  return { r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1 };
}

const toLinear = (c: number) => {
  const v = c / 255;
  return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
};

const fromLinear = (v: number) =>
  (v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055) * 255;

function rgbToOklch(r: number, g: number, b: number): [number, number, number] {
  const lr = toLinear(r);
  const lg = toLinear(g);
  const lb = toLinear(b);
  const l = Math.cbrt(0.4122214708 * lr + 0.5363325363 * lg + 0.0514459929 * lb);
  const m = Math.cbrt(0.2119034982 * lr + 0.6806995451 * lg + 0.1073969566 * lb);
  const s = Math.cbrt(0.0883024619 * lr + 0.2817188376 * lg + 0.6299787005 * lb);
  const L = 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s;
  const A = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s;
  const B = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s;
  return [L, Math.hypot(A, B), Math.atan2(B, A)];
}

function oklchToRgb(L: number, C: number, H: number): [number, number, number] {
  const A = C * Math.cos(H);
  const B = C * Math.sin(H);
  const l = (L + 0.3963377774 * A + 0.2158037573 * B) ** 3;
  const m = (L - 0.1055613458 * A - 0.0638541728 * B) ** 3;
  const s = (L - 0.0894841775 * A - 1.291485548 * B) ** 3;
  return [
    fromLinear(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
    fromLinear(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
    fromLinear(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s),
  ];
}

const inGamut = (rgb: number[]) => rgb.every((c) => c >= -0.5 && c <= 255.5);
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const smoothstep = (lo: number, hi: number, v: number) => {
  const t = clamp((v - lo) / (hi - lo), 0, 1);
  return t * t * (3 - 2 * t);
};

/** Lightness of dark paper, and the slope that sends near-black ink to ivory. */
const DARK_PAPER_L = 0.185;
const SLOPE = 0.953;

export function darkRgba({ r, g, b, a }: Rgba): Rgba {
  const [L, C, H] = rgbToOklch(r, g, b);
  const flipped = clamp(DARK_PAPER_L + SLOPE * (1 - L), DARK_PAPER_L, 0.97);
  // Saturated mid-tones keep their own lightness unless flipping makes them brighter.
  const keep = smoothstep(0.07, 0.13, C) * (1 - smoothstep(0.8, 0.9, L));
  const targetL = flipped + keep * (Math.max(L, flipped) - flipped);

  let chroma = C;
  let rgb = oklchToRgb(targetL, chroma, H);
  if (!inGamut(rgb)) {
    let lo = 0;
    let hi = C;
    for (let i = 0; i < 18; i += 1) {
      chroma = (lo + hi) / 2;
      if (inGamut(oklchToRgb(targetL, chroma, H))) lo = chroma;
      else hi = chroma;
    }
    rgb = oklchToRgb(targetL, lo, H);
  }
  const [nr, ng, nb] = rgb.map((c) => clamp(Math.round(c), 0, 255));
  return { r: nr, g: ng, b: nb, a };
}

export function formatColor({ r, g, b, a }: Rgba): string {
  if (a >= 1) {
    return `#${[r, g, b].map((c) => c.toString(16).padStart(2, '0')).join('').toUpperCase()}`;
  }
  return `rgba(${r}, ${g}, ${b}, ${Number(a.toFixed(3))})`;
}

const cache = new Map<string, string>();

/** Dark-mode counterpart of a light-mode CSS colour (`#hex`, `rgb()`, `rgba()`). */
export function toDark(color: string): string {
  const hit = cache.get(color);
  if (hit) return hit;
  const parsed = parseColor(color);
  const out = parsed ? formatColor(darkRgba(parsed)) : color;
  if (cache.size > 400) cache.clear(); // animated alphas would otherwise grow it without bound
  cache.set(color, out);
  return out;
}

/** Dark-mode counterpart of an `"r, g, b"` triplet used inside `rgba(...)`. */
export function toDarkTriplet(triplet: string): string {
  const parsed = parseColor(`rgb(${triplet})`);
  if (!parsed) return triplet;
  const { r, g, b } = darkRgba(parsed);
  return `${r}, ${g}, ${b}`;
}
