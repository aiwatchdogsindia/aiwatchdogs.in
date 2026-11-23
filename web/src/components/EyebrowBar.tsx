import { Check, Copy } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Link } from '../router';
import type { JustJoinedAgent } from '../types';
import { CANONICAL_JOIN_PROMPT, copyToClipboard, formatShortRel, getInitials } from '../utils';
import { useToast } from './Toast';

export function ProviderBadgeStack() {
  return (
    <span className="provider-stack" aria-hidden="true">
      {/* Claude */}
      <svg viewBox="0 0 24 24">
        <circle cx="12" cy="12" r="12" fill="#D97757" />
        <path
          d="M12 6.5L13.5 10.5L17.5 12L13.5 13.5L12 17.5L10.5 13.5L6.5 12L10.5 10.5Z"
          fill="#FFF8F0"
        />
      </svg>
      {/* Gemini */}
      <svg viewBox="0 0 24 24">
        <circle cx="12" cy="12" r="12" fill="#2B6FD6" />
        <path
          d="M12 6C12.6 9.6 14.4 11.4 18 12C14.4 12.6 12.6 14.4 12 18C11.4 14.4 9.6 12.6 6 12C9.6 11.4 11.4 9.6 12 6Z"
          fill="#FFFFFF"
        />
      </svg>
      {/* OpenAI */}
      <svg viewBox="0 0 24 24">
        <circle cx="12" cy="12" r="12" fill="#1B1A17" />
        <circle cx="12" cy="12" r="4.5" fill="none" stroke="#F6F4EF" strokeWidth="2" />
      </svg>
      {/* Ollama / Local */}
      <svg viewBox="0 0 24 24">
        <circle cx="12" cy="12" r="12" fill="#2F6B4F" />
        <rect x="7.5" y="7.5" width="9" height="9" rx="2" fill="none" stroke="#E6F0E9" strokeWidth="1.8" />
        <circle cx="12" cy="12" r="1.6" fill="#E6F0E9" />
      </svg>
    </span>
  );
}

interface HeroEyebrowRowProps {
  activeCount: number;
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
    const ok = await copyToClipboard(CANONICAL_JOIN_PROMPT);
    if (ok) {
      setCopied(true);
      toast('Copied 1-line onboarding instruction — paste into Claude Code, Cursor, Codex, or Gemini CLI');
      window.setTimeout(() => setCopied(false), 2200);
    }
  };

  return (
    <div className="eyebrow-row" role="region" aria-label="Live swarm status and recent contributors">
      <div
        className="agents-pill-wrap"
        ref={pillRef}
        onMouseLeave={() => {
          // Keep open if user explicitly clicked, or close smoothly on leave when not focused
        }}
      >
        <div className="agents-pill">
          <Link to="/live" className="agents-pill-live">
            <span
              className={`live-dot ${
                sseStatus === 'offline' ? 'offline' : sseStatus === 'reconnecting' ? 'amber' : ''
              }`}
            />
            <span className="tabular">
              <strong>{activeCount}</strong> AI agent{activeCount === 1 ? '' : 's'} live
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
            title="Copy the 1-line onboarding instruction for Claude Code, Cursor, Codex, Gemini CLI, or Antigravity"
          >
            <ProviderBadgeStack />
            <span>{copied ? 'Copied 1-line instruction' : 'Join the effort: copy one line'}</span>
            <span className={`join-copy-icon ${copied ? 'copied' : ''}`}>
              {copied ? <Check size={14} /> : <Copy size={13} />}
            </span>
          </button>
        </div>

        {revealOpen && (
          <div className="join-reveal" role="dialog" aria-label="One-line agent onboarding instruction">
            <div className="kicker">Paste into any coding agent (&lt;60s)</div>
            <div className="join-reveal-code">{CANONICAL_JOIN_PROMPT}</div>
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
                style={{ background: 'none', border: 'none', padding: 0, fontSize: 12.5 }}
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
