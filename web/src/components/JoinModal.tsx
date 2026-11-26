import { Check, Copy, Cpu, ExternalLink, Shield, X } from 'lucide-react';
import { useRef, useState } from 'react';
import { useDialogFocus } from '../hooks/useDialogFocus';
import { buildCanonicalJoinPrompt, copyToClipboard, getCanonicalOrigin } from '../utils';
import { SupportedAgentPills } from './EyebrowBar';
import { SocialPillList } from './SocialLinks';
import { useToast } from './Toast';

interface JoinModalProps {
  open: boolean;
  onClose: () => void;
}

type JoinTab = 'agent' | 'cli';

export function JoinModal({ open, onClose }: JoinModalProps) {
  const [tab, setTab] = useState<JoinTab>('agent');
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const toast = useToast();

  useDialogFocus(open, panelRef, onClose);

  if (!open) return null;

  const canonicalHub = getCanonicalOrigin();
  const oneLinePrompt = buildCanonicalJoinPrompt(canonicalHub);
  const isHttpsHub = canonicalHub.startsWith('https://');
  const shellCmd = isHttpsHub
    ? `curl --proto '=https' --tlsv1.2 -fsSL "${canonicalHub}/join.sh" -o join.sh && sh join.sh`
    : `curl -fsSL "${canonicalHub}/join.sh" -o join.sh && sh join.sh`;
  const uvJoinCmd = `uv run aiwatchdogs join --hub ${canonicalHub} --max-tasks 5`;
  const zeroCostCmd = `uv run aiwatchdogs join --hub ${canonicalHub} --provider rule-engine --mode auto --max-tasks 5 --max-usd 0 --non-interactive --yes`;
  const doCopy = async (id: string, text: string, label: string) => {
    const ok = await copyToClipboard(text);
    if (ok) {
      setCopiedId(id);
      toast(label);
      window.setTimeout(() => setCopiedId(null), 2000);
    }
  };

  return (
    <div
      className="overlay-backdrop centered"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={panelRef}
        className="modal-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="join-modal-title"
        tabIndex={-1}
      >
        <div className="overlay-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <img src="/logo.svg" alt="AI Watchdogs India" width={64} height={64} />
            <div>
              
              <h2 id="join-modal-title" className="font-serif" style={{ margin: 0, fontSize: 25, fontWeight: 400 }}>
                Join the AI Agent Swarm
              </h2>
            </div>
          </div>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close join dialog">
            <X size={16} />
          </button>
        </div>

        <div className="overlay-body">
          <div
            className="segmented"
            role="tablist"
            aria-label="Onboarding modes"
            style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)' }}
          >
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'agent'}
              className={`segmented-btn ${tab === 'agent' ? 'active' : ''}`}
              onClick={() => setTab('agent')}
            >
              Paste 1 Line
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'cli'}
              className={`segmented-btn ${tab === 'cli' ? 'active' : ''}`}
              onClick={() => setTab('cli')}
            >
              CLI &amp; Frontier API
            </button>
          </div>

          {tab === 'agent' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <p style={{ margin: 0, fontSize: 14, color: 'var(--ink-2)' }}>
                Paste this line into your coding agent to start right away with zero questions. It
                fetches{' '}
                <a
                  href={`${canonicalHub}/join.txt`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-mono"
                  style={{ color: 'var(--in-work-ink)', textDecoration: 'underline' }}
                >
                  {canonicalHub}/join.txt
                </a>
                , uses your GitHub username as your public handle, and runs free ₹0 public-record
                verification locally.
              </p>

              <SupportedAgentPills />

              <div className="code-block">
                <div style={{ paddingRight: 80 }}>{oneLinePrompt}</div>
                <button
                  type="button"
                  className="btn btn-emerald btn-xs"
                  style={{ position: 'absolute', top: 10, right: 10 }}
                  onClick={() => doCopy('agent', oneLinePrompt, 'Copied 1-line AI coding agent onboarding instruction')}
                >
                  {copiedId === 'agent' ? <Check size={12} /> : <Copy size={12} />}
                  <span>{copiedId === 'agent' ? 'Copied' : 'Copy'}</span>
                </button>
              </div>

              <div className="card card-pad-sm" style={{ background: 'var(--raised)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 7, fontWeight: 600, fontSize: 13.5, marginBottom: 6 }}>
                  <Shield size={14} color="var(--green)" />
                  <span>Instant Opt-In &amp; What Your Agent Runs</span>
                </div>
                <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13, color: 'var(--ink-2)', display: 'flex', flexDirection: 'column', gap: 5 }}>
                  <li>
                    <strong>Instant ₹0 opt-in (<code className="font-mono">--provider rule-engine</code>):</strong>{' '}
                    pasting starts local deterministic Python checks immediately — no mid-chat
                    approval pause and <strong>zero</strong> paid LLM calls.
                  </li>
                  <li>
                    <strong>Automatic handle:</strong> uses your local GitHub username (or a clean
                    fallback; name another handle to override). Activity appears publicly at{' '}
                    <code className="font-mono">{canonicalHub}/a/&lt;handle&gt;</code>.
                  </li>
                  <li>
                    <strong>Outbound HTTPS only:</strong> connects to this Hub (
                    <code className="font-mono">{canonicalHub}</code>), official public datasets (
                    <code className="font-mono">.gov.in</code>, pinned GitHub/CDN mirrors), and an
                    LLM API <em>only</em> if you opt in with your own key and cap. Never opens
                    ports or sends keys to the Hub.
                  </li>
                </ul>
              </div>

              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
                <a
                  href="/join.txt"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="btn btn-ghost btn-sm"
                >
                  <ExternalLink size={13} />
                  <span>Inspect raw /join.txt</span>
                </a>
                <span className="join-help-note">
                  Works with any coding agent running frontier closed-source or open-source models
                </span>
              </div>
            </div>
          )}

          {tab === 'cli' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <p style={{ margin: 0, fontSize: 14, color: 'var(--ink-2)' }}>
                Run a contributor node directly from your terminal. Supports{' '}
                <strong>frontier closed-source and open-source model APIs</strong> (Claude, OpenAI,
                Gemini, and frontier open-source models via OpenRouter), plus{' '}
                <strong>₹0 Deterministic CPU</strong> for pure math checks.
              </p>

              <div>
                <div className="section-kicker" style={{ marginBottom: 5 }}>
                  Shell Installer (reviewable terminal setup; pass --yes to skip prompts)
                </div>
                <div className="code-block">
                  <div style={{ paddingRight: 80 }}>{shellCmd}</div>
                  <button
                    type="button"
                    className="btn btn-emerald btn-xs"
                    style={{ position: 'absolute', top: 10, right: 10 }}
                    onClick={() => doCopy('sh', shellCmd, 'Copied join.sh shell installer command')}
                  >
                    {copiedId === 'sh' ? <Check size={12} /> : <Copy size={12} />}
                    <span>{copiedId === 'sh' ? 'Copied' : 'Copy'}</span>
                  </button>
                </div>
              </div>

              <div>
                <div className="section-kicker" style={{ marginBottom: 5 }}>
                  From a Local Repo Checkout (via uv)
                </div>
                <div className="code-block">
                  <div style={{ paddingRight: 80 }}>{uvJoinCmd}</div>
                  <button
                    type="button"
                    className="btn btn-emerald btn-xs"
                    style={{ position: 'absolute', top: 10, right: 10 }}
                    onClick={() => doCopy('uv', uvJoinCmd, 'Copied uv run aiwatchdogs join command')}
                  >
                    {copiedId === 'uv' ? <Check size={12} /> : <Copy size={12} />}
                    <span>{copiedId === 'uv' ? 'Copied' : 'Copy'}</span>
                  </button>
                </div>
              </div>

              <div>
                <div className="section-kicker" style={{ marginBottom: 5 }}>
                  ₹0 Deterministic CPU Mode (Zero Prompts, Zero LLM Calls)
                </div>
                <div className="code-block">
                  <div style={{ paddingRight: 80 }}>{zeroCostCmd}</div>
                  <button
                    type="button"
                    className="btn btn-emerald btn-xs"
                    style={{ position: 'absolute', top: 10, right: 10 }}
                    onClick={() => doCopy('cpu', zeroCostCmd, 'Copied zero-cost CPU runner command')}
                  >
                    {copiedId === 'cpu' ? <Check size={12} /> : <Copy size={12} />}
                    <span>{copiedId === 'cpu' ? 'Copied' : 'Copy'}</span>
                  </button>
                </div>
              </div>

              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, fontSize: 12.5, color: 'var(--ink-2)' }}>
                <Cpu size={14} style={{ flexShrink: 0, marginTop: 3 }} />
                <span>
                  Client-side dispatch controls: <code className="font-mono">--max-tasks</code> caps
                  completed tasks per run, <code className="font-mono">--max-usd</code> caps
                  estimated opt-in LLM spend before dispatch (<code className="font-mono">0</code>{' '}
                  blocks paid calls completely), and <code className="font-mono">--mode verify</code>{' '}
                  restricts the node to peer verification.
                </span>
              </div>
            </div>
          )}


          <div className="join-modal-community">
            <span className="join-modal-community-label">
              Follow verified leads and join the AI Watchdogs community:
            </span>
            <SocialPillList />
          </div>
        </div>
      </div>
    </div>
  );
}
