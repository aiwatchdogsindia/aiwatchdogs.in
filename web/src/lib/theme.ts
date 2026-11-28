import { useEffect, useState } from 'react';
import { toDark, toDarkTriplet } from './themeColor';

/**
 * Light / dark theme.
 *
 * `public/theme-init.js` sets `<html data-theme>` before first paint (it has to
 * be an external file: the site's CSP allows no inline scripts). This module
 * owns it afterwards. With no saved choice the site follows the system setting.
 */
export type Theme = 'light' | 'dark';
/** What the visitor picked. `system` means no saved choice: follow the device. */
export type ThemePreference = 'system' | Theme;

const STORAGE_KEY = 'aw.theme';
const THEME_COLOR: Record<Theme, string> = { light: '#FFFFFF', dark: '#000000' };
const listeners = new Set<() => void>();

function savedTheme(): Theme | null {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return value === 'light' || value === 'dark' ? value : null;
  } catch {
    return null;
  }
}

function systemTheme(): Theme {
  return typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-color-scheme: dark)').matches
    ? 'dark'
    : 'light';
}

export function getTheme(): Theme {
  if (typeof document === 'undefined') return 'light';
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
}

export function getThemePreference(): ThemePreference {
  if (typeof window === 'undefined') return 'system';
  return savedTheme() ?? 'system';
}

function apply(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute('content', THEME_COLOR[theme]);
  listeners.forEach((notify) => notify());
}

export function setThemePreference(preference: ThemePreference) {
  try {
    if (preference === 'system') window.localStorage.removeItem(STORAGE_KEY);
    else window.localStorage.setItem(STORAGE_KEY, preference);
  } catch {
    /* private mode: the choice lasts for this page only */
  }
  apply(preference === 'system' ? systemTheme() : preference);
}

export function setTheme(theme: Theme) {
  setThemePreference(theme);
}

export function toggleTheme() {
  setTheme(getTheme() === 'dark' ? 'light' : 'dark');
}

if (typeof window !== 'undefined') {
  apply(savedTheme() ?? systemTheme());
  if (typeof window.matchMedia === 'function') {
    window
      .matchMedia('(prefers-color-scheme: dark)')
      .addEventListener('change', () => {
        if (!savedTheme()) apply(systemTheme());
      });
  }
  // Keep other open tabs in step.
  window.addEventListener('storage', (event) => {
    if (event.key === STORAGE_KEY) apply(savedTheme() ?? systemTheme());
  });
}

export function useTheme(): Theme {
  const [theme, setCurrent] = useState<Theme>(getTheme());
  useEffect(() => {
    const sync = () => setCurrent(getTheme());
    sync();
    listeners.add(sync);
    return () => {
      listeners.delete(sync);
    };
  }, []);
  return theme;
}

export function useThemePreference(): ThemePreference {
  const [preference, setCurrent] = useState<ThemePreference>(getThemePreference());
  useEffect(() => {
    const sync = () => setCurrent(getThemePreference());
    sync();
    listeners.add(sync);
    return () => {
      listeners.delete(sync);
    };
  }, []);
  return preference;
}

/**
 * A light-mode colour as it should be drawn right now. For colours CSS can't
 * reach: canvas strokes and fills, and SVG attributes built in JavaScript.
 */
export function themed(color: string, theme: Theme = getTheme()): string {
  return theme === 'dark' ? toDark(color) : color;
}

/** Same, for an `"r, g, b"` triplet used inside `rgba(...)`. */
export function themedTriplet(triplet: string, theme: Theme = getTheme()): string {
  return theme === 'dark' ? toDarkTriplet(triplet) : triplet;
}
