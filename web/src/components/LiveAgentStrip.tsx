import { Check, Copy, Info } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Link } from '../router';
import { buildCanonicalJoinPrompt, copyToClipboard } from '../utils';
import { AgentLogoStack } from './AgentLogos';
import { GitHubAvatar, normalizeHandle } from './GitHubAvatar';
import type { LiveRecentJoin, LiveSwarmSnapshot } from './LiveSwarmViewer';
import { useToast } from './Toast';
import '../styles/live-swarm-viewer.css';

export type { LiveRecentJoin, LiveSwarmSnapshot } from './LiveSwarmViewer';

export interface LiveAgentStripProps {
  /** Optional full snapshot from GET /api/swarm/live (convenience shorthand). */
  snapshot?: LiveSwarmSnapshot | null;
  /** Number of agents currently working (overrides snapshot.working_now). */
  workingNow?: number | null;
  /** Active task leases count for the info tooltip. */
  activeLeasesCount?: number;
  /** Total registered contributor agents for the info tooltip. */
  totalAgents?: number;
  /** Recently joined contributors for the horizontal ticker (overrides snapshot.recent_joins). */
  recentJoins?: LiveRecentJoin[];
  /** Live SSE stream status. */
  sseStatus?: 'connecting' | 'live' | 'reconnecting' | 'offline';
  /** Custom one-line copy prompt (defaults to buildCanonicalJoinPrompt()). */
  oneLiner?: string;
  /** Loading state before first snapshot arrives. */
  loading?: boolean;
  /** Callback fired after the one-liner is successfully copied. */
  onCopy?: (prompt: string) => void;
  /** Optional callback to open the full Join modal / CLI instructions. */
  onOpenJoin?: () => void;
  /** Optional extra CSS class. */
  className?: string;
}

function formatJoinedRel(iso?: string | null): string {
  if (!iso) return 'just now';
  const parsed = Date.parse(iso);
  if (Number.isNaN(parsed)) return 'just now';
  const diffSec = Math.max(0, Math.floor((Date.now() - parsed) / 1000));
  if (diffSec < 60) return 'just now';
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin} min`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr} h`;
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay < 30) return `${diffDay} d`;
  return new Date(parsed).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
}

/**
 * Hero top live strip inspired by pequalnp.com:
 *   [● N working on it now ⓘ | [agent logos] Join the effort: copy one line ⧉]  [@handle joined 4 min] ...
 */
export function LiveAgentStrip({
  snapshot,
  workingNow: workingNowProp,
  activeLeasesCount: leasesProp,
  totalAgents: totalAgentsProp,
  recentJoins: joinsProp,
  sseStatus = 'live',
  oneLiner,
  loading = false,
  onCopy,
  onOpenJoin,
  className = '',
}: LiveAgentStripProps) {
  const toast = useToast();
  const [copied, setCopied] = useState(false);
  const [infoOpen, setInfoOpen] = useState(false);

  const pillWrapRef = useRef<HTMLDivElement | null>(null);
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<{ down: boolean; startX: number; scrollLeft: number; moved: boolean }>({
    down: false,
    startX: 0,
    scrollLeft: 0,
    moved: false,
  });

  const workingNow =
    workingNowProp !== undefined ? workingNowProp : (snapshot?.working_now ?? null);
  const activeLeases = leasesProp ?? snapshot?.active_leases_count ?? workingNow ?? 0;
  const totalAgents = totalAgentsProp ?? snapshot?.total_agents ?? 0;
  const recentJoins = joinsProp ?? snapshot?.recent_joins ?? [];
  const promptText = oneLiner ?? buildCanonicalJoinPrompt();

  useEffect(() => {
    if (!infoOpen) return;
    const onDocClick = (e: MouseEvent) => {
      if (pillWrapRef.current && !pillWrapRef.current.contains(e.target as Node)) {
        setInfoOpen(false);
      }
    };
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setInfoOpen(false);
    };
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onEsc);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onEsc);
    };
  }, [infoOpen]);

  const handleCopy = async () => {
    const ok = await copyToClipboard(promptText);
    if (ok) {
      setCopied(true);
      onCopy?.(promptText);
      toast('Copied 1-line prompt — paste into Claude Code, Cursor, Codex, Antigravity, or Gemini CLI');
      window.setTimeout(() => setCopied(false), 2200);
    }
  };

  const dotClass =
    sseStatus === 'offline'
      ? 'lsv-dot lsv-dot--offline'
      : sseStatus === 'reconnecting'
        ? 'lsv-dot lsv-dot--amber'
        : 'lsv-dot lsv-dot--live';

  return (
    <div
      className={`lsv-strip${className ? ` ${className}` : ''}`}
      role="region"
      aria-label="Live swarm agents and recent contributors"
    >
      {/* Left Floating Control Pill */}
      <div className="lsv-strip-pill-wrap" ref={pillWrapRef}>
        <div className="lsv-strip-pill">
          <div className="lsv-strip-live-group">
            <Link to="/live" className="lsv-strip-live-link">
              <span className={dotClass} aria-hidden="true" />
              <span className="lsv-strip-live-text tabular">
                {loading && workingNow == null ? (
                  'Swarm connecting…'
                ) : workingNow != null && workingNow > 0 ? (
                  <>
                    <strong>{workingNow.toLocaleString('en-IN')}</strong> working on it now
                  </>
                ) : (
                  'Swarm live · ready for agents'
                )}
              </span>
            </Link>

            <button
              type="button"
              className="lsv-strip-info-btn"
              aria-label="About live swarm count"
              aria-expanded={infoOpen}
              onClick={() => setInfoOpen((v) => !v)}
            >
              <Info size={14} />
            </button>
          </div>

          <span className="lsv-strip-sep" aria-hidden="true" />

          <button
            type="button"
            className={`lsv-strip-copy-btn${copied ? ' is-copied' : ''}`}
            onClick={handleCopy}
            title={promptText}
          >
            <AgentLogoStack size={20} />
            <span className="lsv-strip-copy-label">
              {copied ? 'Copied! Paste into your agent' : 'Join the effort: copy one line'}
            </span>
            <span className="lsv-strip-copy-icon" aria-hidden="true">
              {copied ? <Check size={14} color="var(--green)" /> : <Copy size={14} />}
            </span>
          </button>
        </div>

        {/* Floating Info / One-Liner Popover (zero layout shift) */}
        {infoOpen && (
          <div className="lsv-strip-popover" role="dialog" aria-label="Live swarm details">
            <div className="lsv-strip-popover-title">Live civic-audit swarm</div>
            <p className="lsv-strip-popover-body">
              {workingNow && workingNow > 0
                ? `${workingNow.toLocaleString('en-IN')} agent${workingNow === 1 ? '' : 's'} active in the live window (${activeLeases.toLocaleString('en-IN')} active task lease${activeLeases === 1 ? '' : 's'}), out of ${Math.max(totalAgents, workingNow).toLocaleString('en-IN')} total contributors.`
                : `${totalAgents > 0 ? `${totalAgents.toLocaleString('en-IN')} contributors registered. ` : ''}The hub is live and issuing free ₹0 public-record verification tasks on demand.`}
            </p>
            <div className="lsv-strip-popover-code">{promptText}</div>
            <div className="lsv-strip-popover-actions">
              <button type="button" className="btn btn-primary btn-xs" onClick={handleCopy}>
                {copied ? <Check size={12} /> : <Copy size={12} />}
                <span>{copied ? 'Copied' : 'Copy one line'}</span>
              </button>
              <div className="lsv-strip-popover-links">
                <Link to="/live" className="inline-link" style={{ fontSize: 12.5 }}>
                  Open swarm viewer →
                </Link>
                {onOpenJoin && (
                  <button
                    type="button"
                    className="lsv-text-btn"
                    onClick={() => {
                      setInfoOpen(false);
                      onOpenJoin();
                    }}
                  >
                    CLI &amp; API options
                  </button>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Right Scrollable Ticker of @github_handle Joined Pills */}
      <div
        className="lsv-strip-joins"
        ref={scrollerRef}
        aria-label="Recently joined contributors"
        onMouseDown={(e) => {
          if (!scrollerRef.current) return;
          dragRef.current = {
            down: true,
            startX: e.pageX - scrollerRef.current.offsetLeft,
            scrollLeft: scrollerRef.current.scrollLeft,
            moved: false,
          };
        }}
        onMouseLeave={() => {
          dragRef.current.down = false;
        }}
        onMouseUp={() => {
          dragRef.current.down = false;
        }}
        onMouseMove={(e) => {
          if (!dragRef.current.down || !scrollerRef.current) return;
          const x = e.pageX - scrollerRef.current.offsetLeft;
          const delta = x - dragRef.current.startX;
          if (Math.abs(delta) > 4) dragRef.current.moved = true;
          e.preventDefault();
          scrollerRef.current.scrollLeft = dragRef.current.scrollLeft - delta * 1.15;
        }}
      >
        {recentJoins.length > 0 ? (
          recentJoins.map((join) => {
            const cleanHandle = normalizeHandle(join.handle);
            const rel = formatJoinedRel(join.created_at);
            return (
              <Link
                key={`${cleanHandle}-${join.created_at}`}
                to={`/a/${encodeURIComponent(cleanHandle)}`}
                className={`lsv-join-chip${join.is_working ? ' is-working' : ''}`}
                title={`@${cleanHandle}${join.coding_agent ? ` via ${join.coding_agent}` : ''} · joined ${rel}`}
                onClick={(e) => {
                  if (dragRef.current.moved) {
                    e.preventDefault();
                    dragRef.current.moved = false;
                  }
                }}
              >
                <GitHubAvatar
                  handle={cleanHandle}
                  size={22}
                  statusDot={join.is_working ? 'working' : null}
                />
                <span className="lsv-join-handle">@{cleanHandle}</span>
                <span className="lsv-join-time">
                  {rel === 'just now' ? 'joined just now' : `joined ${rel}`}
                </span>
              </Link>
            );
          })
        ) : (
          <button
            type="button"
            className="lsv-join-chip lsv-join-chip--invite"
            onClick={handleCopy}
            title={promptText}
          >
            <span className="lsv-dot lsv-dot--live" aria-hidden="true" />
            <span className="lsv-join-handle">Your @github handle here</span>
            <span className="lsv-join-time">paste 1 line to join</span>
          </button>
        )}
      </div>
    </div>
  );
}
