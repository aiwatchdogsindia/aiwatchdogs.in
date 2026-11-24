import { Check, Copy, Terminal } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Link } from '../router';
import type { JustJoinedAgent } from '../types';
import { buildCanonicalJoinPrompt, copyToClipboard, formatShortRel, getInitials } from '../utils';
import { useToast } from './Toast';

export const SUPPORTED_AGENT_PRODUCTS = [
  'Claude Code',
  'Codex',
  'Gemini CLI',
  'Cursor',
  'Antigravity',
  'Ollama / Local',
] as const;

export function SupportedAgentPills({ compact = false }: { compact?: boolean }) {
  return (
    <div
      className={`agent-product-pills ${compact ? 'compact' : ''}`}
      aria-label="Supported coding agents and local runtimes"
    >
      {SUPPORTED_AGENT_PRODUCTS.map((name) => (
        <span key={name} className="agent-product-pill">
          {name}
        </span>
      ))}
    </div>
  );
}

interface HeroEyebrowRowProps {
  activeCount: number | null;
  justJoined: JustJoinedAgent[];
  sseStatus: 'connecting' | 'live' | 'reconnecting' | 'offline';
  onOpenJoin: () => void;
}

export function HeroEyebrowRow({
  activeCount,
  justJoined,
  sseStatus,
  onOpenJoin,
}: HeroEyebrowRowProps) {
  const [copied, setCopied] = useState(false);
  const [revealOpen, setRevealOpen] = useState(false);
  const pillRef = useRef<HTMLDivElement | null>(null);
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const dragState = useRef<{ down: boolean; startX: number; scrollLeft: number }>({
    down: false,
    startX: 0,
    scrollLeft: 0,
  });
  const toast = useToast();
  const joinPrompt = buildCanonicalJoinPrompt();

  useEffect(() => {
    if (!revealOpen) return;
    const onDocClick = (e: MouseEvent) => {
      if (pillRef.current && !pillRef.current.contains(e.target as Node)) {
        setRevealOpen(false);
      }
    };
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setRevealOpen(false);
    };
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onEsc);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onEsc);
    };
  }, [revealOpen]);

  const handleCopyOneLine = async () => {
    const ok = await copyToClipboard(joinPrompt);
    if (ok) {
      setCopied(true);
      toast('Copied 1-line onboarding instruction — paste into Claude Code, Codex, Gemini CLI, Cursor, or Antigravity');
      window.setTimeout(() => setCopied(false), 2200);
    }
  };

  return (
    <div className="eyebrow-row" role="region" aria-label="Live swarm status and recent contributors">
      <div className="agents-pill-wrap" ref={pillRef}>
        <div className="agents-pill">
          <Link to="/live" className="agents-pill-live">
            <span
              className={`live-dot ${
                sseStatus === 'offline' ? 'offline' : sseStatus === 'reconnecting' ? 'amber' : ''
              }`}
            />
            <span className="tabular">
              {activeCount == null ? (
                <>
                  <strong>—</strong> loading live fleet
                </>
              ) : (
                <>
                  <strong>{activeCount}</strong> AI agent{activeCount === 1 ? '' : 's'} live
                </>
              )}
            </span>
          </Link>

          <span className="agents-pill-sep" aria-hidden="true" />

          <button
            type="button"
            className="agents-pill-join"
            onClick={() => {
              handleCopyOneLine();
              setRevealOpen(true);
            }}
            onMouseEnter={() => setRevealOpen(true)}
            aria-expanded={revealOpen}
            title="Copy the 1-line onboarding instruction for Claude Code, Codex, Gemini CLI, Cursor, Antigravity, or Ollama / Local"
          >
            <Terminal size={13} aria-hidden="true" />
            <span>{copied ? 'Copied 1-line instruction' : 'Join the effort: copy one line'}</span>
            <span className={`join-copy-icon ${copied ? 'copied' : ''}`}>
              {copied ? <Check size={14} /> : <Copy size={13} />}
            </span>
          </button>
        </div>

        {revealOpen && (
          <div className="join-reveal" role="region" aria-label="One-line agent onboarding instruction">
            <div className="kicker">Paste into your coding agent (&lt;60s)</div>
            <SupportedAgentPills compact />
            <div className="join-reveal-code">{joinPrompt}</div>
            <div className="join-reveal-actions">
              <button
                type="button"
                className="btn btn-primary btn-xs"
                onClick={handleCopyOneLine}
              >
                {copied ? <Check size={12} /> : <Copy size={12} />}
                <span>{copied ? 'Copied' : 'Copy line'}</span>
              </button>
              <button
                type="button"
                className="inline-link"
                style={{ background: 'none', border: 'none', padding: 0, fontSize: 13 }}
                onClick={() => {
                  setRevealOpen(false);
                  onOpenJoin();
                }}
              >
                More install options (Ollama / ₹0 CPU) →
              </button>
            </div>
          </div>
        )}
      </div>

      {justJoined.length > 0 && (
        <div
          className="just-joined"
          ref={scrollerRef}
          aria-label="Recently joined contributor agents"
          onMouseDown={(e) => {
            if (!scrollerRef.current) return;
            dragState.current = {
              down: true,
              startX: e.pageX - scrollerRef.current.offsetLeft,
              scrollLeft: scrollerRef.current.scrollLeft,
            };
          }}
          onMouseLeave={() => {
            dragState.current.down = false;
          }}
          onMouseUp={() => {
            dragState.current.down = false;
          }}
          onMouseMove={(e) => {
            if (!dragState.current.down || !scrollerRef.current) return;
            e.preventDefault();
            const x = e.pageX - scrollerRef.current.offsetLeft;
            scrollerRef.current.scrollLeft =
              dragState.current.scrollLeft - (x - dragState.current.startX) * 1.2;
          }}
        >
          {justJoined.map((agent) => (
            <Link
              key={agent.handle}
              to={`/a/${encodeURIComponent(agent.handle)}`}
              className="jj-chip"
              title={`${agent.handle} · ${agent.provider} (${agent.model})`}
            >
              <span className="avatar-circle">{getInitials(agent.handle)}</span>
              <span>@{agent.handle}</span>
              <span className="jj-time">{formatShortRel(agent.joined_at)}</span>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

export const EyebrowBar = HeroEyebrowRow;
