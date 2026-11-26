import type { ReactNode } from 'react';
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import {
  WEBMCP_TOOL_MANIFEST,
  getWebMcpRuntimeInfo,
  registerWatchdogsWebMcp,
  routeToMarkdownPath,
} from '../lib/webmcp';
import { useRouter } from '../router';

export { routeToMarkdownPath };

export interface AgentViewContextValue {
  isAgentView: boolean;
  setAgentView: (v: boolean) => void;
  toggleAgentView: () => void;
  currentMarkdownPath: string;
  webMcpToolCount: number;
  webMcpNative: boolean;
}

interface InternalAgentViewContextValue extends AgentViewContextValue {
  registerSlot: () => () => void;
}

const AgentViewContext = createContext<InternalAgentViewContextValue>({
  isAgentView: false,
  setAgentView: () => {},
  toggleAgentView: () => {},
  currentMarkdownPath: '/index.md',
  webMcpToolCount: WEBMCP_TOOL_MANIFEST.length,
  webMcpNative: false,
  registerSlot: () => () => {},
});

function isAgentQuery(search: string): boolean {
  try {
    return new URLSearchParams(search).get('view') === 'agent';
  } catch {
    return false;
  }
}

export function useAgentView(): AgentViewContextValue {
  const ctx = useContext(AgentViewContext);
  return {
    isAgentView: ctx.isAgentView,
    setAgentView: ctx.setAgentView,
    toggleAgentView: ctx.toggleAgentView,
    currentMarkdownPath: ctx.currentMarkdownPath,
    webMcpToolCount: ctx.webMcpToolCount,
    webMcpNative: ctx.webMcpNative,
  };
}

export function AgentViewProvider({ children }: { children: ReactNode }) {
  const { pathname, search, hash, navigate } = useRouter();
  const [isAgentView, setIsAgentViewState] = useState<boolean>(() => isAgentQuery(search));
  const [webMcpInfo, setWebMcpInfo] = useState(() => getWebMcpRuntimeInfo());

  const isAgentViewRef = useRef(isAgentView);
  isAgentViewRef.current = isAgentView;

  const locationRef = useRef({ pathname, search, hash });
  locationRef.current = { pathname, search, hash };

  // Keep state synced with URL query (?view=agent) on back/forward or route navigation
  useEffect(() => {
    setIsAgentViewState(isAgentQuery(search));
  }, [search]);

  const setAgentView = useCallback(
    (enabled: boolean) => {
      setIsAgentViewState(enabled);
      const { pathname: curPath, search: curSearch, hash: curHash } = locationRef.current;
      const params = new URLSearchParams(curSearch);
      if (enabled) {
        params.set('view', 'agent');
      } else {
        params.delete('view');
      }
      const nextSearch = params.toString();
      const nextUrl = `${curPath || '/'}${nextSearch ? `?${nextSearch}` : ''}${curHash || ''}`;
      navigate(nextUrl, { replace: false });
    },
    [navigate],
  );

  const toggleAgentView = useCallback(() => {
    setAgentView(!isAgentViewRef.current);
  }, [setAgentView]);

  const registerSlot = useCallback(() => {
    return () => {};
  }, []);

  // Register W3C WebMCP tools silently once per provider lifecycle
  useEffect(() => {
    const cleanup = registerWatchdogsWebMcp({
      navigate: (path) => {
        const currentAgent = isAgentViewRef.current;
        if (currentAgent && !path.includes('view=')) {
          const sep = path.includes('?') ? '&' : '?';
          navigate(`${path}${sep}view=agent`);
        } else {
          navigate(path);
        }
      },
      setAgentView,
      toggleAgentView,
      getAgentView: () => isAgentViewRef.current,
    });
    setWebMcpInfo(getWebMcpRuntimeInfo());
    return cleanup;
  }, [navigate, setAgentView, toggleAgentView]);

  const currentMarkdownPath = useMemo(() => routeToMarkdownPath(pathname), [pathname]);

  const ctxValue = useMemo<InternalAgentViewContextValue>(
    () => ({
      isAgentView,
      setAgentView,
      toggleAgentView,
      currentMarkdownPath,
      webMcpToolCount: webMcpInfo.toolCount,
      webMcpNative: webMcpInfo.supported,
      registerSlot,
    }),
    [isAgentView, setAgentView, toggleAgentView, currentMarkdownPath, webMcpInfo, registerSlot],
  );

  return (
    <AgentViewContext.Provider value={ctxValue}>
      {children}
    </AgentViewContext.Provider>
  );
}

/**
 * Optional slot wrapper (kept for compatibility; AppShell renders AgentViewPanel
 * full-bleed with zero site chrome when ?view=agent is active).
 */
export function AgentViewSlot({ children }: { children: ReactNode }) {
  const { isAgentView, registerSlot } = useContext(AgentViewContext);

  useEffect(() => {
    return registerSlot();
  }, [registerSlot]);

  if (isAgentView) {
    return <AgentViewPanel />;
  }
  return <>{children}</>;
}

/**
 * Monochrome Vercel-style terminal/agent-eyes SVG glyph.
 */
function AgentGlyph({ size = 17 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 20 20"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      className="av-glyph"
    >
      <rect
        x="2"
        y="3"
        width="16"
        height="14"
        rx="3.2"
        stroke="currentColor"
        strokeWidth="1.5"
      />
      <path d="M2.5 7.2H17.5" stroke="currentColor" strokeWidth="1.2" opacity="0.65" />
      <circle cx="4.8" cy="5.1" r="0.7" fill="currentColor" />
      <circle cx="7.1" cy="5.1" r="0.7" fill="currentColor" />
      <circle cx="9.4" cy="5.1" r="0.7" fill="currentColor" />
      <path
        d="M5.7 13.1L7.4 11.1L9.1 13.1"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M10.9 13.1L12.6 11.1L14.3 13.1"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/**
 * Vercel-style "View as Agent" control: opens the current page's pure
 * `Content-Type: text/markdown; charset=utf-8` `.md` URL directly in a new tab
 * (identical to vercel.com/blog/introducing-geist-serif.md).
 */
export function AgentModeToggle({
  variant = 'header',
}: {
  variant?: 'header' | 'footer' | 'compact';
}) {
  const { currentMarkdownPath } = useAgentView();
  const label = 'View as Agent';

  return (
    <span className={`av-toggle-wrap av-toggle-wrap--${variant}`}>
      <a
        href={currentMarkdownPath}
        target="_blank"
        rel="noopener noreferrer"
        className={`av-toggle av-toggle--${variant}`}
        aria-label={`${label} (${currentMarkdownPath})`}
      >
        <AgentGlyph size={variant === 'compact' ? 15 : 17} />
        {variant === 'compact' ? (
          <span className="av-toggle-inline-label">{label}</span>
        ) : null}
      </a>
      {variant !== 'compact' ? (
        <span className={`av-toggle-tip av-toggle-tip--${variant}`} role="tooltip">
          {label}
        </span>
      ) : null}
    </span>
  );
}

/**
 * Full-bleed raw plain-text Markdown surface for `?view=agent` / WebMCP
 * `toggle_agent_view`: mirrors native browser rendering of `text/markdown`
 * (zero header, zero footer, zero toolbar, zero pills, zero card border).
 */
export function AgentViewPanel() {
  const { setAgentView, currentMarkdownPath } = useContext(AgentViewContext);
  const [markdown, setMarkdown] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setAgentView(false);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [setAgentView]);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    setLoading(true);
    setError(null);

    fetch(currentMarkdownPath, {
      signal: controller.signal,
      headers: { Accept: 'text/markdown, text/plain;q=0.9, */*;q=0.1' },
    })
      .then(async (res) => {
        if (!res.ok) {
          throw new Error(`HTTP ${res.status} loading ${currentMarkdownPath}`);
        }
        const ct = (res.headers.get('content-type') || '').toLowerCase();
        const text = await res.text();
        if (
          ct.includes('text/html') &&
          text.trimStart().toLowerCase().startsWith('<!doctype html')
        ) {
          throw new Error(
            `Markdown endpoint ${currentMarkdownPath} is unavailable on this static dev server.`,
          );
        }
        if (cancelled) return;
        setMarkdown(text);
      })
      .catch((err) => {
        if (cancelled || controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : 'Unable to load Markdown document.');
        setMarkdown('');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [currentMarkdownPath]);

  return (
    <div className="av-raw-shell" aria-label={`Raw Markdown view of ${currentMarkdownPath}`}>
      <button
        type="button"
        className="av-raw-exit"
        onClick={() => setAgentView(false)}
        title="Return to Human view (Esc)"
      >
        View as Human
      </button>
      <pre className="av-raw-md">
        {loading ? `Loading ${currentMarkdownPath}…` : error ? `Error: ${error}` : markdown}
      </pre>
    </div>
  );
}
