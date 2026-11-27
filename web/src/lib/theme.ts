import { useCallback, useEffect, useState } from 'react';

export type Theme = 'light' | 'dark';

const STORAGE_KEY = 'aw.theme';
const THEME_EVENT = 'aw:theme';
const META_COLOR: Record<Theme, string> = { light: '#FFFFFF', dark: '#0F0E0B' };

function readStoredTheme(): Theme | null {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return stored === 'light' || stored === 'dark' ? stored : null;
  } catch {
    return null;
  }
}

function systemTheme(): Theme {
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

/** Theme currently applied to the document (set before paint by /theme-init.js). */
export function currentTheme(): Theme {
  if (typeof document === 'undefined') return 'light';
  return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
}

function applyTheme(theme: Theme) {
  const root = document.documentElement;
  // Cross-fade surfaces for one beat instead of snapping (see shell.css)
  if (!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
    root.classList.add('theme-anim');
    window.setTimeout(() => root.classList.remove('theme-anim'), 320);
  }
  root.setAttribute('data-theme', theme);
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', META_COLOR[theme]);
  window.dispatchEvent(new CustomEvent(THEME_EVENT, { detail: theme }));
}

/**
 * Light/dark theme state. Follows the system setting until the visitor picks
 * one; the choice is then remembered in localStorage.
 */
export function useTheme() {
  const [theme, setTheme] = useState<Theme>(currentTheme);

  useEffect(() => {
    const onTheme = () => setTheme(currentTheme());
    window.addEventListener(THEME_EVENT, onTheme);

    const media = window.matchMedia?.('(prefers-color-scheme: dark)');
    const onSystem = () => {
      if (readStoredTheme() == null) applyTheme(systemTheme());
    };
    media?.addEventListener('change', onSystem);

    return () => {
      window.removeEventListener(THEME_EVENT, onTheme);
      media?.removeEventListener('change', onSystem);
    };
  }, []);

  const toggleTheme = useCallback(() => {
    const next: Theme = currentTheme() === 'dark' ? 'light' : 'dark';
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // storage unavailable: the choice lasts for this page view only
    }
    applyTheme(next);
  }, []);

  return { theme, toggleTheme };
}

/**
 * Resolved value of a CSS custom property, for canvas drawing code that
 * cannot read tokens directly.
 */
export function cssToken(name: string, fallback = ''): string {
  if (typeof document === 'undefined') return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}
