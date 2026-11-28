export type ThemeName = 'light' | 'dark';

const KEY = 'aw.theme';
const EVENT = 'aw-theme';

const DARK_COLORS: Record<string, string> = {
  '#FFFFFF': '#000000',
  '#FFF': '#000000',
  '#F4F4F4': '#1C1C1E',
  '#F7F5F0': '#1C1C1E',
  '#EDEDED': '#2C2C2E',
  '#E6E3DC': '#2C2C2E',
  '#D3CFC5': '#3A3A3C',
  '#1B1A17': '#F5F5F7',
  '#3F3B34': '#E5E5EA',
  '#575249': '#AEAEB2',
  '#635E54': '#8E8E93',
  '#9E988B': '#636366',
  '#FF9933': '#FF9F0A',
  '#FFB866': '#FFB340',
  '#F28A1E': '#FF9F0A',
  '#A84F00': '#FF9F0A',
  '#B85C00': '#FF9F0A',
  '#8A4200': '#FF9F0A',
  '#138808': '#30D158',
  '#0F7306': '#30D158',
  '#1FA319': '#30D158',
  '#000080': '#0A84FF',
  '#C2412D': '#FF453A',
  '#A2321F': '#FF453A',
  '#8A1C0C': '#FF6961',
  '#962917': '#FF6961',
};

export function readTheme(): ThemeName {
  try {
    return window.localStorage.getItem(KEY) === 'dark' ? 'dark' : 'light';
  } catch {
    return 'light';
  }
}

export function setThemeAttribute(theme: ThemeName) {
  const root = document.documentElement;
  root.dataset.theme = theme;
  root.style.colorScheme = theme;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', theme === 'dark' ? '#000000' : '#FFFFFF');
}

export function applyTheme(theme: ThemeName) {
  setThemeAttribute(theme);
  window.dispatchEvent(new CustomEvent(EVENT, { detail: theme }));
  try {
    window.localStorage.setItem(KEY, theme);
  } catch {
    return;
  }
}

export function onThemeChange(handler: (theme: ThemeName) => void): () => void {
  const listener = (e: Event) => handler((e as CustomEvent<ThemeName>).detail);
  window.addEventListener(EVENT, listener);
  return () => window.removeEventListener(EVENT, listener);
}

export function isDark(): boolean {
  return typeof document !== 'undefined' && document.documentElement.dataset.theme === 'dark';
}

export function pick<T>(light: T, dark: T): T {
  return isDark() ? dark : light;
}

function invertHex(hex: string): string {
  const h = hex.replace('#', '');
  const full = h.length === 3 ? h.split('').map((c) => c + c).join('') : h;
  const r = parseInt(full.slice(0, 2), 16) / 255;
  const g = parseInt(full.slice(2, 4), 16) / 255;
  const b = parseInt(full.slice(4, 6), 16) / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  let hue = 0;
  let sat = 0;
  const light = (max + min) / 2;
  if (max !== min) {
    const d = max - min;
    sat = light > 0.5 ? d / (2 - max - min) : d / (max + min);
    if (max === r) hue = (g - b) / d + (g < b ? 6 : 0);
    else if (max === g) hue = (b - r) / d + 2;
    else hue = (r - g) / d + 4;
    hue /= 6;
  }
  const l2 = 0.06 + (1 - light) * 0.86;
  const s2 = l2 < 0.22 ? 0 : sat * 0.6;
  const toRgb = (p: number, q: number, t: number) => {
    let x = t;
    if (x < 0) x += 1;
    if (x > 1) x -= 1;
    if (x < 1 / 6) return p + (q - p) * 6 * x;
    if (x < 1 / 2) return q;
    if (x < 2 / 3) return p + (q - p) * (2 / 3 - x) * 6;
    return p;
  };
  let rr = l2;
  let gg = l2;
  let bb = l2;
  if (s2 !== 0) {
    const q = l2 < 0.5 ? l2 * (1 + s2) : l2 + s2 - l2 * s2;
    const p = 2 * l2 - q;
    rr = toRgb(p, q, hue + 1 / 3);
    gg = toRgb(p, q, hue);
    bb = toRgb(p, q, hue - 1 / 3);
  }
  const out = (v: number) => Math.round(v * 255).toString(16).padStart(2, '0').toUpperCase();
  return `#${out(rr)}${out(gg)}${out(bb)}`;
}

function darkHex(hex: string): string {
  const key = hex.toUpperCase();
  return DARK_COLORS[key] ?? invertHex(key);
}

export function themed(color: string): string {
  if (!isDark()) return color;
  const trimmed = color.trim();
  if (/^#[0-9a-f]{3}([0-9a-f]{3})?$/i.test(trimmed)) return darkHex(trimmed);
  const rgba = trimmed.match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)$/i);
  if (rgba) {
    const hex = `#${[rgba[1], rgba[2], rgba[3]].map((v) => Number(v).toString(16).padStart(2, '0')).join('')}`;
    const mapped = darkHex(hex);
    const rgb = [1, 3, 5].map((i) => parseInt(mapped.slice(i, i + 2), 16)).join(', ');
    return rgba[4] !== undefined ? `rgba(${rgb}, ${rgba[4]})` : `rgb(${rgb})`;
  }
  return color;
}

export function themedRgb(rgb: string): string {
  if (!isDark()) return rgb;
  const parts = rgb.split(',').map((v) => Number(v.trim()));
  if (parts.length !== 3 || parts.some((v) => Number.isNaN(v))) return rgb;
  const mapped = darkHex(`#${parts.map((v) => v.toString(16).padStart(2, '0')).join('')}`);
  return [1, 3, 5].map((i) => parseInt(mapped.slice(i, i + 2), 16)).join(', ');
}

export function themedCss(value: string): string {
  if (!isDark()) return value;
  return value.replace(/#[0-9a-f]{6}\b|#[0-9a-f]{3}\b|rgba?\([^)]*\)/gi, (m) => themed(m));
}
