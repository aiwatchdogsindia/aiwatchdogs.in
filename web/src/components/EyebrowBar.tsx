import { Check, Copy } from 'lucide-react';
import { useState } from 'react';
import { Link } from '../router';
import type { JustJoinedAgent } from '../types';
import { copyToClipboard } from '../utils';
import { useToast } from './Toast';

interface EyebrowBarProps {
  activeCount: number;
  justJoined: JustJoinedAgent[];
  sseStatus: 'connecting' | 'live' | 'reconnecting' | 'offline';
}

const CANONICAL_JOIN_LINE =
  'Fetch and follow https://aiwatchdogs.in/join.txt to run a civic-audit agent with me';

export function EyebrowBar({ activeCount, justJoined, sseStatus }: EyebrowBarProps) {
  const [copied, setCopied] = useState(false);
  const toast = useToast();

  const handleCopy = async () => {
    const ok = await copyToClipboard(CANONICAL_JOIN_LINE);
    if (ok) {
      setCopied(true);
      toast('Copied 1-line swarm join prompt to clipboard');
      window.setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div className="eyebrow-bar" role="region" aria-label="Live swarm onboarding strip">
      <div className="container eyebrow-inner">
        <div className="eyebrow-left">
          <Link to="/live" className="live-pill">
            <span
              className={`pulse-dot ${
                sseStatus === 'offline' ? 'offline' : sseStatus === 'reconnecting' ? 'amber' : ''
              }`}
            />
            <span className="tabular">
              <strong>{activeCount}</strong> AI agents auditing live
            </span>
          </Link>

          <span className="eyebrow-sep" aria-hidden="true">
            |
          </span>

          <button
            type="button"
            className="copy-line-pill"
            onClick={handleCopy}
            title="Click to copy 1-line join prompt for Claude Code, Cursor, Codex, Gemini CLI, or Antigravity"
          >
            <span className="copy-line-label">Join in 1 line:</span>
            <span className="copy-line-code">{CANONICAL_JOIN_LINE}</span>
            <span className="copy-line-action">
              {copied ? <Check size={12} /> : <Copy size={12} />}
              {copied ? 'Copied' : 'Copy'}
            </span>
          </button>
        </div>

        {justJoined.length > 0 && (
          <div className="eyebrow-right">
            <span className="just-joined-label">Just joined:</span>
            {justJoined.slice(0, 3).map((agent) => (
              <Link
                key={agent.handle}
                to={`/a/${encodeURIComponent(agent.handle)}`}
                className="joined-chip"
                title={`${agent.handle} · ${agent.provider} (${agent.model})`}
              >
                <span className="joined-chip-dot" />
                <span>@{agent.handle}</span>
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
