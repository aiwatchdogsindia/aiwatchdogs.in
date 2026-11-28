/**
 * Light -> dark colour mapping.
 *
 * The site is designed on white paper. Dark mode keeps every hue and flips
 * lightness in OKLCH, so each ink/paper pair keeps its contrast:
 *   - white paper becomes true black, near-black ink becomes near-white, and
 *     the warm greys in between become neutral greys;
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

/**
 * Light lightness -> dark lightness (OKLCH L), as a curve through fixed points.
 * White paper lands on true black and the greys step up from it the way a
 * neutral black UI does: #0A0A0A surfaces, #1F1F1F hairlines, #A1A1A1
 * secondary text, #EDEDED ink.
 */
const CURVE: readonly (readonly [number, number])[] = [
  [0, 1],
  [0.217, 0.945], // ink
  [0.44, 0.71], // secondary text
  [0.68, 0.5], // faint text
  [0.85, 0.31], // strong hairline
  [0.915, 0.24], // hairline
  [0.967, 0.145], // sunk surface
  [1, 0], // paper
];

function flipLightness(L: number): number {
  for (let i = 1; i < CURVE.length; i += 1) {
    const [x0, y0] = CURVE[i - 1];
    const [x1, y1] = CURVE[i];
    if (L <= x1) return y0 + ((L - x0) / (x1 - x0)) * (y1 - y0);
  }
  return 0;
}

export function darkRgba({ r, g, b, a }: Rgba): Rgba {
  const [L, sourceC, H] = rgbToOklch(r, g, b);
  // The light palette's greys are warm (a faint yellow cast); on black that
  // warmth reads as brown, so they go fully neutral. Real tints keep their hue.
  const hue = ((H * 180) / Math.PI + 360) % 360;
  const warmGreyBand = hue > 55 && hue < 115;
  const tint = warmGreyBand
    ? smoothstep(0.022, 0.036, sourceC)
    : smoothstep(0.006, 0.014, sourceC);
  // Pale washes sit very close to black once flipped: lift them a little and
  // deepen the colour so a tinted chip still reads as tinted.
  const paleWash = L > 0.85 ? tint : 0;
  const C = sourceC * tint * (1 + 1.2 * paleWash);
  const flipped = Math.max(flipLightness(clamp(L, 0, 1)), paleWash * 0.21);
  // Saturated mid-tones keep their own lightness unless flipping makes them brighter.
  const keep = smoothstep(0.07, 0.13, sourceC) * (1 - smoothstep(0.8, 0.9, L));
  const targetL = flipped + keep * (Math.max(L, flipped) - flipped);

  let chroma = C;
  let rgb = oklchToRgb(targetL, chroma, H);
  if (!inGamut(rgb)) {
    let lo = 0;
    let hi = chroma;
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
