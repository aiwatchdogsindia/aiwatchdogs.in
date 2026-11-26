import { codingAgentLabel } from '../utils';

/*
 * Logos of coding agents, frontier model families, providers, and the
 * deterministic Python rule engine. Files in /public/agents/ come from
 * LobeHub Icons (@lobehub/icons-static-svg v1.106.0, MIT) and Devicon (MIT).
 * Each logo remains a trademark of its owner and is shown only to identify
 * that agent, provider, or model family.
 */
export interface BrandLogo {
  id: string;
  name: string;
  src: string;
  /** Brand family implied by this logo so we never double-up redundant family logos. */
  family: string;
}

const STACK_AGENT_LOGOS: readonly BrandLogo[] = [
  { id: 'claude-code', name: 'Claude Code', src: '/agents/claude-code.svg', family: 'claude' },
  { id: 'antigravity', name: 'Antigravity', src: '/agents/antigravity.svg', family: 'antigravity' },
  { id: 'codex', name: 'Codex', src: '/agents/codex.svg', family: 'openai' },
  { id: 'cursor', name: 'Cursor', src: '/agents/cursor.svg', family: 'cursor' },
  { id: 'hermes-agent', name: 'Hermes Agent', src: '/agents/hermes-agent.svg', family: 'hermes' },
] as const;

interface BrandMatcher {
  match: RegExp;
  logo: BrandLogo;
  /** Pattern stripped from model IDs once this brand logo is already visible. */
  stripPrefix: RegExp;
}

const BRAND_MATCHERS: readonly BrandMatcher[] = [
  {
    match: /\bclaude[\s_-]?code\b|claudecode/i,
    logo: { id: 'claude-code', name: 'Claude Code', src: '/agents/claude-code.svg', family: 'claude' },
    stripPrefix: /^(?:anthropic\/)?(?:claude[\s_-]?code|claudecode|claude)[\s_-]*/i,
  },
  {
    match: /\b(antigravity|agy)\b/i,
    logo: { id: 'antigravity', name: 'Antigravity', src: '/agents/antigravity.svg', family: 'antigravity' },
    stripPrefix: /^(?:google[\s_-]?)?(?:antigravity(?:[\s_-]?cli)?|agy)[\s_-]*/i,
  },
  {
    match: /\bcodex\b/i,
    logo: { id: 'codex', name: 'Codex', src: '/agents/codex.svg', family: 'openai' },
    stripPrefix: /^(?:openai[\s_-]?)?codex(?:[\s_-]?cli)?[\s_-]*/i,
  },
  {
    match: /\bcursor\b/i,
    logo: { id: 'cursor', name: 'Cursor', src: '/agents/cursor.svg', family: 'cursor' },
    stripPrefix: /^cursor(?:[\s_-]?agent)?[\s_-]*/i,
  },
  {
    match: /\bhermes(?:[\s_-]?agent)?\b/i,
    logo: { id: 'hermes-agent', name: 'Hermes Agent', src: '/agents/hermes-agent.svg', family: 'hermes' },
    stripPrefix: /^hermes(?:[\s_-]?agent)?[\s_-]*/i,
  },
  {
    match: /\bgemini(?:[\s_-]?cli)?\b|gemini/i,
    logo: { id: 'gemini', name: 'Gemini', src: '/agents/gemini.svg', family: 'gemini' },
    stripPrefix: /^(?:google\/)?gemini(?:[\s_-]?cli)?[\s_-]*/i,
  },
  {
    match: /\bclaude\b|claude[-_]/i,
    logo: { id: 'claude', name: 'Claude', src: '/agents/claude.svg', family: 'claude' },
    stripPrefix: /^(?:anthropic\/)?claude[\s_-]*/i,
  },
  {
    match: /\banthropic\b/i,
    logo: { id: 'anthropic', name: 'Anthropic', src: '/agents/anthropic.svg', family: 'claude' },
    stripPrefix: /^anthropic[\s_/-]*/i,
  },
  {
    match: /\bopenrouter\b/i,
    logo: { id: 'openrouter', name: 'OpenRouter', src: '/agents/openrouter.svg', family: 'openrouter' },
    stripPrefix: /^openrouter[\s_/-]*/i,
  },
  {
    match: /\b(openai|chatgpt)\b|\bgpt[-_\d]|\bo[134](?:-mini|-preview|-pro)?\b/i,
    logo: { id: 'openai', name: 'OpenAI', src: '/agents/openai.svg', family: 'openai' },
    stripPrefix: /^(?:openai[\s_/-]*|chatgpt[\s_-]*)/i,
  },
  {
    match: /\bdeepseek\b|deepseek/i,
    logo: { id: 'deepseek', name: 'DeepSeek', src: '/agents/deepseek.svg', family: 'deepseek' },
    stripPrefix: /^(?:deepseek\/)?deepseek[\s_-]*/i,
  },
  {
    match: /\b(mistral|mixtral|codestral|pixtral)\b/i,
    logo: { id: 'mistral', name: 'Mistral', src: '/agents/mistral.svg', family: 'mistral' },
    stripPrefix: /^(?:mistralai\/)?mistral[\s_-]*/i,
  },
  {
    match: /\b(grok|xai|x-ai)\b/i,
    logo: { id: 'grok', name: 'Grok', src: '/agents/grok.svg', family: 'grok' },
    stripPrefix: /^(?:x-?ai\/)?(?:grok|x-?ai)[\s_-]*/i,
  },
  {
    match: /\b(llama|meta)\b|llama[-_\d]/i,
    logo: { id: 'meta', name: 'Meta', src: '/agents/meta.svg', family: 'meta' },
    stripPrefix: /^(?:meta-?llama\/)?(?:meta[\s_-]*)?(?:llama)?[\s_-]*/i,
  },
  {
    match: /\bqwen\b|qwen/i,
    logo: { id: 'qwen', name: 'Qwen', src: '/agents/qwen.svg', family: 'qwen' },
    stripPrefix: /^(?:qwen\/)?qwen[\s_-]*/i,
  },
  {
    match: /\b(?:github[\s_-]?)?copilot\b/i,
    logo: { id: 'copilot', name: 'GitHub Copilot', src: '/agents/copilot.svg', family: 'copilot' },
    stripPrefix: /^(?:github[\s_-]?)?copilot[\s_-]*/i,
  },
  {
    match: /\bwindsurf\b/i,
    logo: { id: 'windsurf', name: 'Windsurf', src: '/agents/windsurf.svg', family: 'windsurf' },
    stripPrefix: /^windsurf[\s_-]*/i,
  },
  {
    match: /\bcline\b/i,
    logo: { id: 'cline', name: 'Cline', src: '/agents/cline.svg', family: 'cline' },
    stripPrefix: /^cline[\s_-]*/i,
  },
  {
    match: /\bopencode\b/i,
    logo: { id: 'opencode', name: 'OpenCode', src: '/agents/opencode.svg', family: 'opencode' },
    stripPrefix: /^opencode[\s_-]*/i,
  },
  {
    match: /\b(kimi|moonshot)\b/i,
    logo: { id: 'kimi', name: 'Kimi', src: '/agents/kimi.svg', family: 'kimi' },
    stripPrefix: /^(?:moonshotai\/)?(?:kimi|moonshot)[\s_-]*/i,
  },
  {
    match: /\b(perplexity|sonar)\b/i,
    logo: { id: 'perplexity', name: 'Perplexity', src: '/agents/perplexity.svg', family: 'perplexity' },
    stripPrefix: /^(?:perplexity\/)?perplexity[\s_-]*/i,
  },
  {
    match: /\b(google|vertex)\b/i,
    logo: { id: 'google', name: 'Google', src: '/agents/google.svg', family: 'google' },
    stripPrefix: /^(?:google|vertex)[\s_/-]*/i,
  },
  {
    match: /\b(rule[\s_-]?engine|deterministic|python|cpu)\b|deterministic/i,
    logo: { id: 'python', name: 'Deterministic Python', src: '/agents/python.svg', family: 'python' },
    stripPrefix: /^.*$/, // Python logo alone represents deterministic rule-engine without redundant text
  },
];

function matchBrandEntry(raw?: string | null): BrandMatcher | null {
  const s = (raw || '').trim();
  if (!s) return null;
  for (const entry of BRAND_MATCHERS) {
    if (entry.match.test(s)) {
      return entry;
    }
  }
  return null;
}

export function resolveBrandLogo(raw?: string | null): BrandLogo | null {
  return matchBrandEntry(raw)?.logo ?? null;
}

const UPPERCASE_TOKENS = new Set(['gpt', 'r1', 'v1', 'v2', 'v3', 'v4', 'vl', 'ai', 'cli', 'cpu']);
const LOWERCASE_PREFIX_RE = /^(?:o[134]|4o)$/i;

/**
 * Strips the redundant brand prefix from a model ID (since the brand logo is
 * already displayed) and humanizes hyphens/versions cleanly:
 *   "claude-fable-5-2" -> "Fable 5.2"
 *   "claude-sonnet-4-5" -> "Sonnet 4.5"
 *   "gemini-2.5-pro" -> "2.5 Pro"
 *   "Gemini" -> "" (bare family name -> logo only)
 *   "deterministic-python-v1" -> "" (Python logo only)
 */
export function humanizeModelVariant(rawModel: string, matcher: BrandMatcher | null): string {
  let s = rawModel.trim();
  if (!s) return '';

  if (matcher) {
    s = s.replace(matcher.stripPrefix, '').trim();
  }
  if (!s) return '';

  // Drop trailing 8-digit release date stamps (e.g. -20250514) and generic "-instruct"
  s = s.replace(/[-_\s]+20\d{6}\b/g, '').replace(/[-_\s]+instruct$/i, '').trim();
  if (!s) return '';

  // Keep GPT-4o / GPT-5 compact with its canonical hyphen
  if (/^gpt[-_]/i.test(s)) {
    const rest = s.slice(4);
    const [ver, ...tail] = rest.split(/[-_\s]+/).filter(Boolean);
    const formattedTail = tail.map(formatVariantWord).join(' ');
    return formattedTail ? `GPT-${ver} ${formattedTail}` : `GPT-${ver}`;
  }

  // Join adjacent version numbers with dots (5-2 -> 5.2, 3-5 -> 3.5)
  let prev = '';
  while (prev !== s) {
    prev = s;
    s = s.replace(/(\d+)[-_](\d+)/g, '$1.$2');
  }

  const words = s
    .split(/[-_\s]+/)
    .map((w) => w.trim())
    .filter(Boolean)
    .map(formatVariantWord);

  return words.join(' ');
}

function formatVariantWord(word: string): string {
  const lower = word.toLowerCase();
  if (UPPERCASE_TOKENS.has(lower)) return word.toUpperCase();
  if (LOWERCASE_PREFIX_RE.test(word)) return lower;
  if (/^\d+(?:\.\d+)?[bkmt]$/i.test(word)) return word.toUpperCase();
  if (/^[a-z]/.test(word)) return word.charAt(0).toUpperCase() + word.slice(1);
  return word;
}

export interface AttributionItem {
  logo?: BrandLogo;
  text?: string;
}

/**
 * Resolves codingAgent/codingAgentModel + provider/model into zero-duplicity
 * visual items:
 *   - Claude Code + claude-fable-5-2 -> [Claude Code logo] "Fable 5.2"
 *   - Antigravity + Gemini -> [Antigravity logo] [Gemini logo]
 *   - Antigravity + gemini-2.5-pro -> [Antigravity logo] [Gemini logo] "2.5 Pro"
 *   - Cursor + claude-sonnet-4-5 -> [Cursor logo] [Claude logo] "Sonnet 4.5"
 *   - rule-engine + deterministic-python-v1 -> [Python logo]
 *   - Claude Code (claude-fable-5-2) + rule-engine (deterministic-python-v1)
 *       -> [Claude Code logo] "Fable 5.2"  [Python logo]
 */
export function resolveAttributionItems(opts: {
  codingAgent?: string | null;
  codingAgentModel?: string | null;
  provider?: string | null;
  model?: string | null;
}): { items: AttributionItem[]; isCpuOnly: boolean } {
  const items: AttributionItem[] = [];
  const seenLogoIds = new Set<string>();
  const seenFamilies = new Set<string>();
  const seenTexts = new Set<string>();

  const addLogo = (logo: BrandLogo) => {
    if (seenLogoIds.has(logo.id)) return;
    // If a more specific product logo in the same family is already present
    // (e.g. Claude Code already implies Claude/Anthropic, Codex implies OpenAI),
    // skip the redundant generic family logo!
    if (seenFamilies.has(logo.family)) return;
    seenLogoIds.add(logo.id);
    seenFamilies.add(logo.family);
    items.push({ logo });
  };

  const addText = (text: string) => {
    const clean = text.trim();
    if (!clean) return;
    const key = clean.toLowerCase();
    if (seenTexts.has(key)) return;
    seenTexts.add(key);
    items.push({ text: clean });
  };

  const processValue = (raw?: string | null, isModelField = false) => {
    let trimmed = (raw || '').trim();
    if (!trimmed) return;

    // For OpenRouter-style "vendor/model" IDs (e.g. "google/gemini-2.5-pro",
    // "anthropic/claude-sonnet-4-5", "meta-llama/llama-3.3-70b"), drop the
    // redundant vendor prefix when the model slug itself matches a brand.
    if (isModelField && trimmed.includes('/')) {
      const slashIdx = trimmed.indexOf('/');
      const rest = trimmed.slice(slashIdx + 1).trim();
      if (rest && matchBrandEntry(rest)) {
        trimmed = rest;
      }
    }

    // Split compound values separated by "/", "·", or "," (e.g. "antigravity / Gemini").
    const parts = trimmed
      .split(/\s*(?:\/|·|,)\s*/)
      .map((p) => p.trim())
      .filter(Boolean);

    for (const part of parts) {
      const entry = matchBrandEntry(part);
      if (entry) {
        addLogo(entry.logo);
        if (isModelField || entry.logo.id !== 'python') {
          const variant = humanizeModelVariant(part, entry);
          if (variant) addText(variant);
        }
      } else {
        // Genuinely unrecognized custom name or bare version string (e.g. "Fable 5.2")
        addText(isModelField ? humanizeModelVariant(part, null) : part);
      }
    }
  };

  // Order matters: coding agent first (specific product logo like Claude Code /
  // Antigravity / Codex), then its model variant, then provider & execution model.
  processValue(opts.codingAgent, false);
  processValue(opts.codingAgentModel, true);
  processValue(opts.provider, false);
  processValue(opts.model, true);

  const logoItems = items.filter((i) => i.logo);
  const textItems = items.filter((i) => i.text);
  const isCpuOnly =
    logoItems.length > 0 &&
    logoItems.every((i) => i.logo!.id === 'python') &&
    textItems.length === 0;

  return { items, isCpuOnly };
}

function buildReadableLabel(parts: {
  codingAgent?: string | null;
  codingAgentModel?: string | null;
  provider?: string | null;
  model?: string | null;
}): string {
  const segments: string[] = [];
  const seen = new Set<string>();
  const push = (val?: string | null) => {
    const clean = (val || '').trim();
    if (!clean) return;
    const key = clean.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    segments.push(clean);
  };

  const agentStr = codingAgentLabel(parts.codingAgent, parts.codingAgentModel);
  if (agentStr) {
    push(parts.codingAgent);
    push(parts.codingAgentModel);
  }
  push(parts.provider);
  push(parts.model);

  return segments.join(' / ');
}

export function AgentLogoStack({ size = 24 }: { size?: number }) {
  const label = `Works with ${STACK_AGENT_LOGOS.map((l) => l.name).join(', ')} and other coding agents`;
  return (
    <span className="agent-logo-stack" role="img" aria-label={label} title={label}>
      {STACK_AGENT_LOGOS.map((logo) => (
        <span key={logo.id} className="agent-logo" style={{ width: size, height: size }}>
          <img
            src={logo.src}
            alt=""
            width={Math.round(size * 0.64)}
            height={Math.round(size * 0.64)}
          />
        </span>
      ))}
    </span>
  );
}

/*
 * One contributor's coding agent and optional model variant.
 * Zero duplicity: never repeats the agent/brand name next to its logo, and strips
 * redundant brand prefixes from model IDs:
 *   Claude Code + claude-fable-5-2 -> [Claude Code logo] Fable 5.2
 *   Antigravity + Gemini -> [Antigravity logo] [Gemini logo]
 * Full human-readable attribution is always preserved in `title` and `aria-label`.
 */
export function AgentBadge({
  agent,
  model,
  logoOnly = false,
  size = 16,
}: {
  agent?: string | null;
  model?: string | null;
  logoOnly?: boolean;
  size?: number;
}) {
  const label = codingAgentLabel(agent, model);
  if (!label) return null;

  const { items } = resolveAttributionItems({ codingAgent: agent, codingAgentModel: model });
  if (items.length === 0) return <>{label}</>;

  const visibleItems = logoOnly ? items.filter((i) => i.logo) : items;
  const rendered = visibleItems.length > 0 ? visibleItems : items;

  return (
    <span className="agent-badge" title={label} aria-label={label}>
      {rendered.map((item, idx) =>
        item.logo ? (
          <img
            key={`logo-${item.logo.id}-${idx}`}
            src={item.logo.src}
            alt=""
            width={size}
            height={size}
            className="brand-logo-icon"
          />
        ) : (
          <span key={`txt-${idx}`} className="brand-logo-variant">
            {item.text}
          </span>
        ),
      )}
    </span>
  );
}

/*
 * Unified provider / model / coding-agent badge used across tables, cards,
 * profile headers, compute share bars, and live fleet cards.
 * Never repeats brand names next to their logos and never clips long strings.
 */
export function ProviderModelBadge({
  provider,
  model,
  codingAgent,
  codingAgentModel,
  size = 16,
  inline = false,
  className = '',
}: {
  provider?: string | null;
  model?: string | null;
  codingAgent?: string | null;
  codingAgentModel?: string | null;
  size?: number;
  inline?: boolean;
  className?: string;
}) {
  const fullLabel = buildReadableLabel({ codingAgent, codingAgentModel, provider, model });
  if (!fullLabel) return null;

  const { items, isCpuOnly } = resolveAttributionItems({
    codingAgent,
    codingAgentModel,
    provider,
    model,
  });

  const baseClass = inline
    ? 'brand-logo-inline'
    : `brand-logo-pill${isCpuOnly ? ' cpu' : ''}`;
  const combinedClass = className ? `${baseClass} ${className}` : baseClass;

  return (
    <span className={combinedClass} title={fullLabel} aria-label={fullLabel}>
      {items.map((item, idx) =>
        item.logo ? (
          <img
            key={`logo-${item.logo.id}-${idx}`}
            src={item.logo.src}
            alt=""
            width={size}
            height={size}
            className="brand-logo-icon"
          />
        ) : (
          <span key={`txt-${idx}`} className="brand-logo-variant">
            {item.text}
          </span>
        ),
      )}
    </span>
  );
}
