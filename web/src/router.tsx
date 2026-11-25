import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { safeExternalUrl } from './utils';

interface RouterContextValue {
  pathname: string;
  search: string;
  hash: string;
  navigate: (to: string, opts?: { replace?: boolean }) => void;
}

const RouterContext = createContext<RouterContextValue>({
  pathname: '/',
  search: '',
  hash: '',
  navigate: () => {},
});

// eslint-disable-next-line no-control-regex
const CONTROL_CHAR_RE = /[\u0000-\u001f\u007f]/;
const SCHEME_PREFIX_RE = /^[a-zA-Z][a-zA-Z0-9+.-]*:/;

function resolveLinkTarget(to: string): { href?: string; isExternal: boolean } {
  if (typeof to !== 'string') return { href: undefined, isExternal: false };
  const trimmed = to.trim();
  if (!trimmed || trimmed.startsWith('//') || CONTROL_CHAR_RE.test(trimmed)) {
    return { href: undefined, isExternal: false };
  }
  if (SCHEME_PREFIX_RE.test(trimmed)) {
    const safeUrl = safeExternalUrl(trimmed);
    return safeUrl ? { href: safeUrl, isExternal: true } : { href: undefined, isExternal: false };
  }
  if (typeof window !== 'undefined' && window.location?.origin) {
    try {
      const parsed = new URL(trimmed, window.location.origin);
      if (parsed.origin !== window.location.origin) {
        return { href: undefined, isExternal: false };
      }
    } catch {
      return { href: undefined, isExternal: false };
    }
  }
  return { href: trimmed, isExternal: false };
}

function mergeNoopenerRel(rel?: string): string {
  const tokens = new Set((rel || '').split(/\s+/).filter(Boolean));
  tokens.add('noopener');
  tokens.add('noreferrer');
  return Array.from(tokens).join(' ');
}

export function RouterProvider({ children }: { children: React.ReactNode }) {
  const [loc, setLoc] = useState(() => ({
    pathname: window.location.pathname || '/',
    search: window.location.search || '',
    hash: window.location.hash || '',
  }));

  useEffect(() => {
    const onPop = () => {
      setLoc({
        pathname: window.location.pathname || '/',
        search: window.location.search || '',
        hash: window.location.hash || '',
      });
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const navigate = useCallback((to: string, opts?: { replace?: boolean }) => {
    let url: URL;
    try {
      url = new URL(to, window.location.origin);
    } catch {
      return;
    }
    if (url.origin !== window.location.origin) {
      return;
    }
    const nextUrl = url.pathname + url.search + url.hash;
    if (opts?.replace) {
      window.history.replaceState({}, '', nextUrl);
    } else {
      window.history.pushState({}, '', nextUrl);
    }
    setLoc({ pathname: url.pathname || '/', search: url.search || '', hash: url.hash || '' });
    if (url.hash) {
      const targetId = url.hash.replace(/^#/, '');
      window.requestAnimationFrame(() => {
        const el = document.getElementById(targetId);
        if (el) {
          el.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
      });
    } else {
      window.scrollTo({ top: 0, behavior: 'instant' as ScrollBehavior });
    }
  }, []);

  return (
    <RouterContext.Provider value={{ pathname: loc.pathname, search: loc.search, hash: loc.hash, navigate }}>
      {children}
    </RouterContext.Provider>
  );
}

export function useRouter() {
  return useContext(RouterContext);
}

interface LinkProps extends React.AnchorHTMLAttributes<HTMLAnchorElement> {
  to: string;
}

export function Link({ to, onClick, rel, children, ...rest }: LinkProps) {
  const { navigate } = useRouter();
  const { href: safeHref, isExternal } = resolveLinkTarget(to);
  const safeRel = rest.target === '_blank' ? mergeNoopenerRel(rel) : rel;

  const handleClick = (e: React.MouseEvent<HTMLAnchorElement>) => {
    if (onClick) onClick(e);
    if (!safeHref) {
      e.preventDefault();
      return;
    }
    if (
      e.defaultPrevented ||
      e.button !== 0 ||
      e.metaKey ||
      e.ctrlKey ||
      e.shiftKey ||
      e.altKey ||
      rest.target === '_blank'
    ) {
      return;
    }
    if (isExternal) return;
    e.preventDefault();
    navigate(safeHref);
  };
  return (
    <a href={safeHref} rel={safeRel} onClick={handleClick} {...rest}>
      {children}
    </a>
  );
}
