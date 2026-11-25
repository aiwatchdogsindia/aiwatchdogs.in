import {
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  ExternalLink,
  FileText,
  Play,
  RefreshCw,
  Terminal,
  Wrench,
} from 'lucide-react';
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
  executeWatchdogsWebMcpTool,
  getWebMcpRuntimeInfo,
  registerWatchdogsWebMcp,
  routeToMarkdownPath,
  type WebMcpToolManifestItem,
} from '../lib/webmcp';
import { useRouter } from '../router';
import { copyToClipboard, getCanonicalOrigin } from '../utils';
import { useToast } from './Toast';

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
  apiStyle: 'registerTool' | 'provideContext' | 'shim';
}

const AgentViewContext = createContext<InternalAgentViewContextValue>({
  isAgentView: false,
  setAgentView: () => {},
  toggleAgentView: () => {},
  currentMarkdownPath: '/index.md',
  webMcpToolCount: WEBMCP_TOOL_MANIFEST.length,
  webMcpNative: false,
  registerSlot: () => () => {},
  apiStyle: 'shim',
});

const QUICK_MD_ROUTES: Array<{ label: string; route: string; mdPath: string }> = [
  { label: 'Home', route: '/', mdPath: '/index.md' },
  { label: 'Projects', route: '/projects', mdPath: '/projects.md' },
  { label: 'Findings', route: '/findings', mdPath: '/findings.md' },
  { label: 'Live', route: '/live', mdPath: '/live.md' },
  { label: 'Leaderboard', route: '/contributors', mdPath: '/leaderboard.md' },
  { label: 'How it works', route: '/how-it-works', mdPath: '/how-it-works.md' },
];

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
  const [slotCount, setSlotCount] = useState(0);
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
    setSlotCount((c) => c + 1);
    return () => setSlotCount((c) => Math.max(0, c - 1));
  }, []);

  // Register W3C WebMCP tools once per provider lifecycle
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
      apiStyle: webMcpInfo.apiStyle,
    }),
    [isAgentView, setAgentView, toggleAgentView, currentMarkdownPath, webMcpInfo, registerSlot],
  );

  return (
    <AgentViewContext.Provider value={ctxValue}>
      {children}
      {isAgentView && slotCount === 0 ? <AgentViewPanel isOverlay /> : null}
    </AgentViewContext.Provider>
  );
}

/**
 * Optional inline slot inside `<main>` so Agent View replaces page content cleanly
 * while keeping `<Header>` and `<Footer>` (and their toggles) visible and clickable.
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

export function AgentModeToggle({
  variant = 'header',
}: {
  variant?: 'header' | 'footer' | 'compact';
}) {
  const { isAgentView, toggleAgentView } = useAgentView();
  const label = isAgentView ? 'View as Human' : 'View as Agent';

  return (
    <span className={`av-toggle-wrap av-toggle-wrap--${variant}`}>
      <button
        type="button"
        className={`av-toggle av-toggle--${variant}${isAgentView ? ' is-active' : ''}`}
        aria-pressed={isAgentView}
        aria-label={label}
        onClick={toggleAgentView}
      >
        <AgentGlyph size={variant === 'compact' ? 15 : 17} />
        {variant === 'compact' ? (
          <span className="av-toggle-inline-label">{label}</span>
        ) : null}
      </button>
      {variant !== 'compact' ? (
        <span className={`av-toggle-tip av-toggle-tip--${variant}`} role="tooltip">
          {label}
        </span>
      ) : null}
    </span>
  );
}

export function AgentViewPanel({ isOverlay = false }: { isOverlay?: boolean }) {
  const {
    setAgentView,
    currentMarkdownPath,
    webMcpToolCount,
    webMcpNative,
    apiStyle,
  } = useContext(AgentViewContext);
  const { pathname, navigate } = useRouter();
  const toast = useToast();

  const [markdown, setMarkdown] = useState<string>('');
  const [tokenCount, setTokenCount] = useState<number | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadNonce, setReloadNonce] = useState(0);

  const [copiedMd, setCopiedMd] = useState(false);
  const [copiedMcp, setCopiedMcp] = useState(false);
  const [toolsOpen, setToolsOpen] = useState(false);

  const timersRef = useRef<number[]>([]);

  useEffect(() => {
    return () => {
      for (const id of timersRef.current) window.clearTimeout(id);
    };
  }, []);

  useEffect(() => {
    if (!isOverlay) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setAgentView(false);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [isOverlay, setAgentView]);

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
          throw new Error(`Markdown endpoint returned HTTP ${res.status}`);
        }
        const ct = (res.headers.get('content-type') || '').toLowerCase();
        const text = await res.text();
        if (ct.includes('text/html') && text.trimStart().toLowerCase().startsWith('<!doctype html')) {
          throw new Error(
            'Markdown endpoint is unavailable on this static dev server. Connect to the live hub to fetch live .md output.',
          );
        }
        if (cancelled) return;
        const headerTokens = res.headers.get('x-markdown-tokens');
        const parsedTokens = headerTokens ? Number(headerTokens) : NaN;
        setMarkdown(text);
        setTokenCount(Number.isFinite(parsedTokens) ? parsedTokens : Math.ceil(text.length / 4));
      })
      .catch((err) => {
        if (cancelled || controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : 'Unable to load Markdown document.');
        setMarkdown('');
        setTokenCount(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [currentMarkdownPath, reloadNonce]);

  const origin = getCanonicalOrigin();
  const fullMarkdownUrl = `${origin}${currentMarkdownPath}`;
  const fullMcpUrl = `${origin}/mcp`;

  const handleCopyMarkdown = async () => {
    if (!markdown) return;
    const ok = await copyToClipboard(markdown);
    if (ok) {
      setCopiedMd(true);
      toast(`Copied ${currentMarkdownPath} to clipboard`);
      const id = window.setTimeout(() => setCopiedMd(false), 2000);
      timersRef.current.push(id);
    }
  };

  const handleCopyMcp = async () => {
    const ok = await copyToClipboard(fullMcpUrl);
    if (ok) {
      setCopiedMcp(true);
      toast(`Copied MCP endpoint: ${fullMcpUrl}`);
      const id = window.setTimeout(() => setCopiedMcp(false), 2000);
      timersRef.current.push(id);
    }
  };

  const handleJumpRoute = (route: string) => {
    navigate(`${route}?view=agent`);
  };

  return (
    <section
      className={`av-shell${isOverlay ? ' av-shell--overlay' : ''}`}
      aria-label="Agent View — Markdown and WebMCP"
    >
      <div className="av-bar">
        <div className="av-bar-inner">
          <div className="av-bar-left">
            <span className="av-badge">
              <span className="live-dot sm" aria-hidden="true" />
              <AgentGlyph size={14} />
              <span>AGENT VIEW</span>
            </span>

            <code className="av-url-pill" title={fullMarkdownUrl}>
              {fullMarkdownUrl}
            </code>

            {tokenCount != null ? (
              <span className="av-meta-pill tabular">~{tokenCount.toLocaleString('en-IN')} tokens</span>
            ) : null}
          </div>

          <div className="av-bar-actions">
            <button
              type="button"
              className={`av-btn${copiedMd ? ' is-success' : ''}`}
              onClick={() => void handleCopyMarkdown()}
              disabled={!markdown || loading}
            >
              {copiedMd ? <Check size={13} /> : <Copy size={13} />}
              <span>{copiedMd ? 'Copied' : 'Copy Markdown'}</span>
            </button>

            <a
              href={currentMarkdownPath}
              target="_blank"
              rel="noopener noreferrer"
              className="av-btn"
            >
              <ExternalLink size={13} />
              <span>Open raw .md</span>
            </a>

            <a
              href="/llms.txt"
              target="_blank"
              rel="noopener noreferrer"
              className="av-btn"
            >
              <FileText size={13} />
              <span>/llms.txt</span>
            </a>

            <button
              type="button"
              className={`av-btn av-btn--mono${copiedMcp ? ' is-success' : ''}`}
              onClick={() => void handleCopyMcp()}
              title={`Copy MCP server URL: ${fullMcpUrl}`}
            >
              {copiedMcp ? <Check size={13} /> : <Terminal size={13} />}
              <span>{copiedMcp ? 'Copied /mcp' : '/mcp'}</span>
            </button>

            <button
              type="button"
              className={`av-btn${toolsOpen ? ' is-active' : ''}`}
              aria-expanded={toolsOpen}
              onClick={() => setToolsOpen((v) => !v)}
            >
              <Wrench size={13} />
              <span className="tabular">WebMCP ({webMcpToolCount} tools)</span>
              {toolsOpen ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
            </button>

            <button
              type="button"
              className="av-btn av-btn--human"
              onClick={() => setAgentView(false)}
            >
              <span>View as Human</span>
            </button>
          </div>
        </div>
      </div>

      <div className="av-body container">
        {/* Quick sibling Markdown switcher */}
        <nav className="av-quick-links" aria-label="Markdown pages">
          <span className="av-quick-label">Markdown pages:</span>
          {QUICK_MD_ROUTES.map((item) => {
            const active =
              routeToMarkdownPath(pathname) === item.mdPath;
            return (
              <button
                key={item.mdPath}
                type="button"
                className={`av-quick-btn${active ? ' is-active' : ''}`}
                onClick={() => handleJumpRoute(item.route)}
              >
                {item.mdPath}
              </button>
            );
          })}
          <a
            href="/.well-known/mcp.json"
            target="_blank"
            rel="noopener noreferrer"
            className="av-quick-btn av-quick-link"
          >
            /.well-known/mcp.json
          </a>
        </nav>

        {/* Expandable WebMCP Tool Inspector */}
        {toolsOpen ? (
          <div className="av-webmcp-drawer" role="region" aria-label="WebMCP Tool Inspector">
            <div className="av-webmcp-head">
              <div>
                <div className="kicker">W3C WebMCP · navigator.modelContext</div>
                <h2 className="av-webmcp-title">
                  Registered browser tools ({webMcpToolCount})
                </h2>
              </div>
              <span className={`av-status-pill${webMcpNative ? ' is-native' : ''}`}>
                {webMcpNative
                  ? `Native navigator.modelContext (${apiStyle})`
                  : 'WebMCP shim ready (window.__aiwatchdogsWebMcp)'}
              </span>
            </div>

            <div className="av-webmcp-grid">
              {WEBMCP_TOOL_MANIFEST.map((tool) => (
                <WebMcpToolCard key={tool.name} tool={tool} />
              ))}
            </div>
          </div>
        ) : null}

        {/* Live Markdown Surface */}
        <article className="av-doc">
          <div className="av-doc-head">
            <span className="av-doc-path">{currentMarkdownPath}</span>
            <div className="av-doc-meta">
              <span>text/markdown · UTF-8</span>
              <button
                type="button"
                className="av-refresh-btn"
                onClick={() => setReloadNonce((n) => n + 1)}
                disabled={loading}
              >
                <RefreshCw size={12} />
                <span>Refresh</span>
              </button>
            </div>
          </div>

          {loading ? (
            <div className="av-state-box">Fetching live Markdown from {currentMarkdownPath}…</div>
          ) : error ? (
            <div className="av-state-box av-state-box--error" role="alert">
              <p style={{ margin: '0 0 10px' }}>{error}</p>
              <div className="av-state-actions">
                <button
                  type="button"
                  className="av-btn"
                  onClick={() => setReloadNonce((n) => n + 1)}
                >
                  <RefreshCw size={13} />
                  <span>Retry</span>
                </button>
                <a
                  href="/llms.txt"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="av-btn"
                >
                  Open /llms.txt
                </a>
              </div>
            </div>
          ) : (
            <pre className="av-md-pre">
              <code>{markdown}</code>
            </pre>
          )}
        </article>
      </div>
    </section>
  );
}

function WebMcpToolCard({ tool }: { tool: WebMcpToolManifestItem }) {
  const hasParams = Object.keys(tool.inputSchema.properties).length > 0;
  const [argsText, setArgsText] = useState<string>(() =>
    tool.sampleInput ? JSON.stringify(tool.sampleInput) : '{}',
  );
  const [running, setRunning] = useState(false);
  const [output, setOutput] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [schemaOpen, setSchemaOpen] = useState(false);

  const handleRun = async () => {
    setRunning(true);
    setRunError(null);
    try {
      let parsed: Record<string, unknown> = {};
      if (hasParams && argsText.trim()) {
        const val = JSON.parse(argsText);
        if (!val || typeof val !== 'object' || Array.isArray(val)) {
          throw new Error('Tool input must be a JSON object.');
        }
        parsed = val as Record<string, unknown>;
      }
      const res = await executeWatchdogsWebMcpTool(tool.name, parsed);
      const formatted = JSON.stringify(res, null, 2);
      setOutput(
        formatted.length > 2600 ? `${formatted.slice(0, 2600)}\n… (truncated preview)` : formatted,
      );
    } catch (err) {
      setRunError(err instanceof Error ? err.message : 'Tool execution failed');
      setOutput(null);
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="av-tool-card">
      <div className="av-tool-top">
        <code className="av-tool-name">{tool.name}</code>
        {tool.annotations?.readOnlyHint ? (
          <span className="av-tool-tag">read-only</span>
        ) : (
          <span className="av-tool-tag av-tool-tag--action">ui-action</span>
        )}
      </div>

      <p className="av-tool-desc">{tool.description}</p>

      {hasParams ? (
        <div className="av-tool-args">
          <label className="av-tool-args-label" htmlFor={`webmcp-arg-${tool.name}`}>
            Input JSON
          </label>
          <input
            id={`webmcp-arg-${tool.name}`}
            type="text"
            className="av-tool-input"
            value={argsText}
            onChange={(e) => setArgsText(e.target.value)}
            spellCheck={false}
          />
        </div>
      ) : null}

      <div className="av-tool-actions">
        <button
          type="button"
          className="av-btn av-btn--run"
          disabled={running}
          onClick={() => void handleRun()}
        >
          <Play size={12} />
          <span>{running ? 'Running…' : 'Run tool'}</span>
        </button>

        <button
          type="button"
          className="av-schema-toggle"
          onClick={() => setSchemaOpen((v) => !v)}
        >
          {schemaOpen ? 'Hide schema' : 'Schema'}
        </button>
      </div>

      {schemaOpen ? (
        <pre className="av-tool-schema">
          <code>{JSON.stringify(tool.inputSchema, null, 2)}</code>
        </pre>
      ) : null}

      {runError ? (
        <div className="av-tool-error" role="alert">
          {runError}
        </div>
      ) : null}

      {output ? (
        <pre className="av-tool-output">
          <code>{output}</code>
        </pre>
      ) : null}
    </div>
  );
}
