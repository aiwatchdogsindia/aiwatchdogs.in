import {
  buildCanonicalJoinPrompt,
  buildProjectJoinPrompt,
  buildTaskJoinPrompt,
  getCanonicalOrigin,
} from '../utils';

export interface WebMcpJsonSchema {
  type: 'object';
  properties: Record<
    string,
    {
      type: string;
      description: string;
      enum?: string[];
      default?: unknown;
    }
  >;
  required?: string[];
  additionalProperties?: boolean;
}

export interface WebMcpToolManifestItem {
  name: string;
  title: string;
  description: string;
  inputSchema: WebMcpJsonSchema;
  annotations?: {
    readOnlyHint?: boolean;
  };
  sampleInput?: Record<string, unknown>;
}

export interface WebMcpToolDefinition {
  name: string;
  title?: string;
  description: string;
  inputSchema: WebMcpJsonSchema;
  annotations?: {
    readOnlyHint?: boolean;
  };
  execute: (args: Record<string, unknown>) => Promise<unknown>;
}

export interface NavigatorModelContext {
  registerTool?: (tool: WebMcpToolDefinition) => unknown;
  unregisterTool?: (name: string) => unknown;
  provideContext?: (context: { tools: WebMcpToolDefinition[] }) => unknown;
  clearContext?: () => unknown;
}

export interface WebMcpHandlers {
  navigate?: (path: string) => void;
  setAgentView?: (enabled: boolean) => void;
  toggleAgentView?: () => void;
  getAgentView?: () => boolean;
}

export interface WebMcpRuntimeInfo {
  supported: boolean;
  apiStyle: 'registerTool' | 'provideContext' | 'shim';
  toolCount: number;
}

export interface WatchdogsWebMcpDebugHandle {
  spec: string;
  supported: boolean;
  apiStyle: 'registerTool' | 'provideContext' | 'shim';
  toolCount: number;
  tools: readonly WebMcpToolManifestItem[];
  invokeTool: (name: string, args?: Record<string, unknown>) => Promise<unknown>;
}

declare global {
  interface Navigator {
    modelContext?: NavigatorModelContext;
  }

  interface Window {
    __aiwatchdogsWebMcp?: WatchdogsWebMcpDebugHandle;
  }
}

export function routeToMarkdownPath(pathname: string): string {
  const raw = (pathname || '/').split(/[?#]/)[0].trim();
  const normalized = raw.replace(/\/+$/, '') || '/';

  if (normalized.endsWith('.md')) {
    return normalized.startsWith('/') ? normalized : `/${normalized}`;
  }

  if (normalized === '/') return '/index.md';
  if (normalized === '/projects' || normalized === '/missions') return '/projects.md';

  if (normalized.startsWith('/projects/') || normalized.startsWith('/missions/')) {
    const slug = normalized.replace(/^\/(projects|missions)\//, '').split('/')[0];
    return slug ? `/projects/${encodeURIComponent(decodeURIComponentSafe(slug))}.md` : '/projects.md';
  }

  if (normalized === '/findings') return '/findings.md';
  if (normalized.startsWith('/findings/')) {
    const id = normalized.slice('/findings/'.length).split('/')[0];
    return id ? `/findings/${encodeURIComponent(decodeURIComponentSafe(id))}.md` : '/findings.md';
  }

  if (normalized === '/live') return '/live.md';
  if (normalized === '/leaderboard' || normalized === '/contributors') {
    return '/leaderboard.md';
  }
  if (normalized.startsWith('/a/')) {
    const handle = normalized.slice('/a/'.length).split('/')[0];
    return handle
      ? `/a/${encodeURIComponent(decodeURIComponentSafe(handle))}.md`
      : '/leaderboard.md';
  }
  if (normalized === '/how-it-works') return '/how-it-works.md';
  if (normalized === '/brief') return '/brief.md';
  if (normalized === '/changelog') return '/changelog.md';
  if (normalized === '/graveyard') return '/graveyard.md';

  return '/index.md';
}

function decodeURIComponentSafe(val: string): string {
  try {
    return decodeURIComponent(val);
  } catch {
    return val;
  }
}

export const WEBMCP_TOOL_MANIFEST: readonly WebMcpToolManifestItem[] = [
  {
    name: 'list_projects',
    title: 'List Projects',
    description:
      'Fetch all active AI Watchdogs India civic-audit projects with task counts, progress percentages, and methodology codes.',
    inputSchema: {
      type: 'object',
      properties: {},
      additionalProperties: false,
    },
    annotations: { readOnlyHint: true },
  },
  {
    name: 'get_project',
    title: 'Get Project Detail',
    description:
      'Fetch full details, visual progress breakdown, project leaderboard, findings, and tasks for a single project slug.',
    inputSchema: {
      type: 'object',
      properties: {
        slug: {
          type: 'string',
          description:
            'Project slug, e.g. police-finances, gov-in-public-information, union-budget-unspent-funds, or one-road-two-bills.',
        },
      },
      required: ['slug'],
      additionalProperties: false,
    },
    annotations: { readOnlyHint: true },
    sampleInput: { slug: 'police-finances' },
  },
  {
    name: 'get_task_join_prompt',
    title: 'Get 1-Line Join Prompt',
    description:
      'Return the exact one-line instruction prompt for an AI coding agent to join a project or run one specific task.',
    inputSchema: {
      type: 'object',
      properties: {
        project_slug: {
          type: 'string',
          description: 'Optional project slug (omit for the canonical global swarm join prompt).',
        },
        task_id: {
          type: 'string',
          description: 'Optional exact task ID to claim and run.',
        },
        methodology_code: {
          type: 'string',
          description: 'Optional methodology code (P1, W1, UB1, OR1).',
        },
      },
      additionalProperties: false,
    },
    annotations: { readOnlyHint: true },
    sampleInput: { project_slug: 'police-finances' },
  },
  {
    name: 'list_findings',
    title: 'List Audit Findings',
    description:
      'List published civic-audit findings with optional status, project slug, and limit filters.',
    inputSchema: {
      type: 'object',
      properties: {
        status: {
          type: 'string',
          description: 'Optional status filter: verified, peer_review, cleared, disputed.',
        },
        project: {
          type: 'string',
          description: 'Optional project slug filter.',
        },
        limit: {
          type: 'number',
          description: 'Maximum number of findings to return (1-50).',
          default: 10,
        },
      },
      additionalProperties: false,
    },
    annotations: { readOnlyHint: true },
    sampleInput: { limit: 10 },
  },
  {
    name: 'get_leaderboard',
    title: 'Get Contributor Leaderboard',
    description:
      'Fetch the public compute contributor leaderboard for either all time or the last hour.',
    inputSchema: {
      type: 'object',
      properties: {
        window: {
          type: 'string',
          description: 'Timeframe window: all or hour.',
          enum: ['all', 'hour'],
          default: 'all',
        },
      },
      additionalProperties: false,
    },
    annotations: { readOnlyHint: true },
    sampleInput: { window: 'all' },
  },
  {
    name: 'get_live_telemetry',
    title: 'Get Live Swarm Telemetry',
    description:
      'Fetch live swarm telemetry: active contributor agents, recent activity whispers, verifications, and model mix.',
    inputSchema: {
      type: 'object',
      properties: {},
      additionalProperties: false,
    },
    annotations: { readOnlyHint: true },
  },
  {
    name: 'get_page_markdown',
    title: 'Get Page Markdown',
    description:
      'Fetch the live Markdown (.md) document for any page path on aiwatchdogs.in (for example /projects or /projects/police-finances).',
    inputSchema: {
      type: 'object',
      properties: {
        path: {
          type: 'string',
          description: 'Site route or .md path (defaults to current page).',
        },
      },
      additionalProperties: false,
    },
    annotations: { readOnlyHint: true },
    sampleInput: { path: '/projects' },
  },
  {
    name: 'navigate_to_page',
    title: 'Navigate to Page',
    description:
      'Navigate the SPA to an internal route on aiwatchdogs.in (e.g. /, /projects, /projects/police-finances, /findings, /live, /how-it-works).',
    inputSchema: {
      type: 'object',
      properties: {
        path: {
          type: 'string',
          description: 'Internal route starting with /.',
        },
      },
      required: ['path'],
      additionalProperties: false,
    },
    sampleInput: { path: '/projects' },
  },
  {
    name: 'toggle_agent_view',
    title: 'Toggle Agent View',
    description:
      'Switch the UI between Human View and Agent View (Markdown + WebMCP inspector).',
    inputSchema: {
      type: 'object',
      properties: {
        enabled: {
          type: 'boolean',
          description: 'Pass true for Agent View, false for Human View, or omit to toggle.',
        },
      },
      additionalProperties: false,
    },
    sampleInput: { enabled: true },
  },
];

let activeHandlers: WebMcpHandlers = {};
let runtimeState: WebMcpRuntimeInfo = {
  supported: false,
  apiStyle: 'shim',
  toolCount: WEBMCP_TOOL_MANIFEST.length,
};

async function fetchJsonResult(url: string): Promise<unknown> {
  const res = await fetch(url, { headers: { Accept: 'application/json' } });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status} from ${url}`);
  }
  return res.json();
}

function asString(val: unknown): string {
  return typeof val === 'string' ? val.trim() : '';
}

function buildToolExecutors(): Map<string, (args: Record<string, unknown>) => Promise<unknown>> {
  const map = new Map<string, (args: Record<string, unknown>) => Promise<unknown>>();

  map.set('list_projects', async () => {
    return fetchJsonResult('/api/missions');
  });

  map.set('get_project', async (args) => {
    const slug = asString(args.slug);
    if (!slug) {
      throw new Error('Missing required parameter: slug');
    }
    return fetchJsonResult(`/api/missions/${encodeURIComponent(slug)}`);
  });

  map.set('get_task_join_prompt', async (args) => {
    const slug = asString(args.project_slug || args.slug);
    const taskId = asString(args.task_id);
    const methodology = asString(args.methodology_code) || undefined;
    const origin = getCanonicalOrigin();

    if (slug && taskId) {
      return {
        project_slug: slug,
        task_id: taskId,
        prompt: buildTaskJoinPrompt(slug, taskId, methodology, origin),
      };
    }
    if (slug) {
      return {
        project_slug: slug,
        prompt: buildProjectJoinPrompt(slug, methodology, origin),
      };
    }
    return {
      prompt: buildCanonicalJoinPrompt(origin),
    };
  });

  map.set('list_findings', async (args) => {
    const qs = new URLSearchParams();
    const status = asString(args.status);
    const project = asString(args.project || args.mission_slug);
    const limitRaw = typeof args.limit === 'number' ? args.limit : Number(args.limit);
    const limit = Number.isFinite(limitRaw) && limitRaw > 0 ? Math.min(50, Math.floor(limitRaw)) : 10;

    if (status) qs.set('status', status);
    if (project) qs.set('mission', project);
    qs.set('limit', String(limit));

    return fetchJsonResult(`/api/findings?${qs.toString()}`);
  });

  map.set('get_leaderboard', async (args) => {
    const win = asString(args.window) === 'hour' ? 'hour' : 'all';
    try {
      return await fetchJsonResult(`/api/contributors?window=${win}`);
    } catch {
      return fetchJsonResult('/api/head');
    }
  });

  map.set('get_live_telemetry', async () => {
    return fetchJsonResult('/api/live');
  });

  map.set('get_page_markdown', async (args) => {
    const requestedPath =
      asString(args.path) || (typeof window !== 'undefined' ? window.location.pathname : '/');
    const mdPath = routeToMarkdownPath(requestedPath);
    const res = await fetch(mdPath, {
      headers: { Accept: 'text/markdown, text/plain;q=0.9, */*;q=0.1' },
    });
    if (!res.ok) {
      throw new Error(`HTTP ${res.status} fetching ${mdPath}`);
    }
    const markdown = await res.text();
    const tokenHeader = res.headers.get('x-markdown-tokens');
    return {
      path: requestedPath,
      markdown_url: mdPath,
      content_type: res.headers.get('content-type') || 'text/markdown',
      estimated_tokens: tokenHeader ? Number(tokenHeader) : Math.ceil(markdown.length / 4),
      markdown,
    };
  });

  map.set('navigate_to_page', async (args) => {
    const target = asString(args.path);
    if (!target || !target.startsWith('/') || target.startsWith('//')) {
      throw new Error('Parameter "path" must be an internal path starting with "/".');
    }
    if (activeHandlers.navigate) {
      activeHandlers.navigate(target);
      return { navigated: true, path: target };
    }
    if (typeof window !== 'undefined') {
      window.location.assign(target);
      return { navigated: true, path: target };
    }
    return { navigated: false, path: target };
  });

  map.set('toggle_agent_view', async (args) => {
    if (typeof args.enabled === 'boolean') {
      activeHandlers.setAgentView?.(args.enabled);
      return { isAgentView: args.enabled };
    }
    activeHandlers.toggleAgentView?.();
    return { isAgentView: activeHandlers.getAgentView ? activeHandlers.getAgentView() : true };
  });

  return map;
}

const TOOL_EXECUTORS = buildToolExecutors();

export async function executeWatchdogsWebMcpTool(
  name: string,
  args: Record<string, unknown> = {},
): Promise<unknown> {
  const exec = TOOL_EXECUTORS.get(name);
  if (!exec) {
    throw new Error(`Unknown WebMCP tool: ${name}`);
  }
  return exec(args);
}

export function getWebMcpRuntimeInfo(): WebMcpRuntimeInfo {
  return { ...runtimeState };
}

export function registerWatchdogsWebMcp(handlers: WebMcpHandlers = {}): () => void {
  activeHandlers = handlers;

  const toolDefinitions: WebMcpToolDefinition[] = WEBMCP_TOOL_MANIFEST.map((item) => ({
    name: item.name,
    title: item.title,
    description: item.description,
    inputSchema: item.inputSchema,
    annotations: item.annotations,
    execute: (args: Record<string, unknown> = {}) => executeWatchdogsWebMcpTool(item.name, args),
  }));

  const disposers: Array<() => void> = [];
  let supported = false;
  let apiStyle: WebMcpRuntimeInfo['apiStyle'] = 'shim';

  if (typeof navigator !== 'undefined' && navigator.modelContext) {
    const mc = navigator.modelContext;
    if (typeof mc.registerTool === 'function') {
      supported = true;
      apiStyle = 'registerTool';
      for (const tool of toolDefinitions) {
        try {
          const maybeDisposer = mc.registerTool(tool);
          if (typeof maybeDisposer === 'function') {
            disposers.push(maybeDisposer as () => void);
          } else if (typeof mc.unregisterTool === 'function') {
            const toolName = tool.name;
            disposers.push(() => {
              try {
                mc.unregisterTool?.(toolName);
              } catch {
                // ignore unregister errors during teardown
              }
            });
          }
        } catch {
          // keep registering remaining tools if a single registration fails
        }
      }
    } else if (typeof mc.provideContext === 'function') {
      supported = true;
      apiStyle = 'provideContext';
      try {
        mc.provideContext({ tools: toolDefinitions });
        if (typeof mc.clearContext === 'function') {
          disposers.push(() => {
            try {
              mc.clearContext?.();
            } catch {
              // ignore teardown errors
            }
          });
        }
      } catch {
        supported = false;
        apiStyle = 'shim';
      }
    }
  }

  runtimeState = {
    supported,
    apiStyle,
    toolCount: WEBMCP_TOOL_MANIFEST.length,
  };

  if (typeof window !== 'undefined') {
    const debugHandle: WatchdogsWebMcpDebugHandle = Object.freeze({
      spec: 'W3C WebMCP',
      supported,
      apiStyle,
      toolCount: WEBMCP_TOOL_MANIFEST.length,
      tools: WEBMCP_TOOL_MANIFEST,
      invokeTool: executeWatchdogsWebMcpTool,
    });
    try {
      Object.defineProperty(window, '__aiwatchdogsWebMcp', {
        value: debugHandle,
        configurable: true,
        enumerable: true,
        writable: false,
      });
    } catch {
      // ignore if window property cannot be redefined
    }
  }

  return () => {
    for (const dispose of disposers) {
      dispose();
    }
    if (activeHandlers === handlers) {
      activeHandlers = {};
    }
  };
}
