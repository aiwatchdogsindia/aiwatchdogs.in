import { Info } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from '../router';
import { projectCodeFor } from '../utils';
import { normalizeHandle } from './GitHubAvatar';
import {
  computeEffectiveWorkingNow,
  type LiveSwarmProgress,
  type LiveSwarmSnapshot,
  type LiveSwarmWhisper,
} from './LiveSwarmViewer';
import '../styles/live-swarm-viewer.css';

export type {
  LiveSwarmProgress,
  LiveSwarmSnapshot,
  LiveSwarmWhisper,
} from './LiveSwarmViewer';

export interface HeroLiveFeedProps {
  /** Optional full snapshot from GET /api/swarm/live (convenience shorthand). */
  snapshot?: LiveSwarmSnapshot | null;
  /** Override live whisper items (defaults to snapshot.live_whispers). */
  whispers?: LiveSwarmWhisper[];
  /** Override auditing progress summary (defaults to snapshot.progress). */
  progress?: LiveSwarmProgress | null;
  /** Override active worker count (defaults to snapshot.working_now). */
  workingNow?: number;
  /** SSE stream connectivity indicator. */
  sseStatus?: 'connecting' | 'live' | 'reconnecting' | 'offline';
  /** Number of whisper cards to show (defaults to 2 to match hero right rail balance). */
  maxItems?: number;
  /** Hide the embedded Auditing progress bar when placed in a separate dashboard column. */
  hideProgress?: boolean;
  /** Optional custom label for the bottom link to /live. */
  viewerLinkLabel?: string;
  /** Optional click callback when "Open the live swarm viewer →" or "Open in the hub →" is clicked. */
  onOpenViewer?: () => void;
  /** Optional callback when an agent handle is clicked. */
  onSelectAgent?: (handle: string) => void;
  /** Optional extra CSS class. */
  className?: string;
}

export interface AuditingProgressBarProps {
  progress?: LiveSwarmProgress | null;
  className?: string;
}

/**
 * Reusable live auditing progress gauge with interactive explainer tooltip.
 */
export function AuditingProgressBar({
  progress = null,
  className = '',
}: AuditingProgressBarProps) {
  const [progressInfoOpen, setProgressInfoOpen] = useState(false);
  const progressInfoRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!progressInfoOpen) return;
    const onDocClick = (e: MouseEvent) => {
      if (progressInfoRef.current && !progressInfoRef.current.contains(e.target as Node)) {
        setProgressInfoOpen(false);
      }
    };
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setProgressInfoOpen(false);
    };
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onEsc);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onEsc);
    };
  }, [progressInfoOpen]);

  const tasksTotal = progress?.tasks_total ?? 0;
  const tasksCompleted = progress?.tasks_completed ?? 0;
  const tasksInProgress = progress?.tasks_in_progress ?? 0;
  const findingsVerified = progress?.findings_verified ?? 0;
  const findingsInReview = progress?.findings_in_review ?? 0;

  const fillPct = useMemo(() => {
    if (tasksTotal > 0) {
      const ratio = (tasksCompleted / tasksTotal) * 100;
      return tasksCompleted > 0 ? Math.max(4, Math.min(98, ratio)) : 0;
    }
    const totalFindings = findingsVerified + findingsInReview;
    if (totalFindings > 0) {
      return Math.max(6, Math.min(96, (findingsVerified / totalFindings) * 100));
    }
    return 0;
  }, [tasksTotal, tasksCompleted, findingsVerified, findingsInReview]);

  const activePct = useMemo(() => {
    if (tasksTotal <= 0 || tasksInProgress <= 0) return 0;
    return Math.min(100 - fillPct, Math.max(2, (tasksInProgress / tasksTotal) * 100));
  }, [tasksTotal, tasksInProgress, fillPct]);

  return (
    <div
      className={`lsv-progress${className ? ` ${className}` : ''}`}
      ref={progressInfoRef}
      aria-label="Swarm auditing progress"
    >
      <div className="lsv-progress-head">
        <span className="lsv-progress-title">Auditing progress</span>
        <button
          type="button"
          className="lsv-progress-info-btn"
          aria-label="How auditing progress is measured"
          aria-expanded={progressInfoOpen}
          onClick={() => setProgressInfoOpen((v) => !v)}
        >
          <Info size={14} />
        </button>
      </div>

      {progressInfoOpen && (
        <div className="lsv-progress-tooltip" role="note">
          Completed deterministic audit tasks across active projects. Leads only enter the
          verified ledger after independent 2-of-2 peer reproduction.
        </div>
      )}

      <div className="lsv-progress-bar-wrap">
        <div
          className="lsv-progress-track"
          role="progressbar"
          aria-valuenow={Math.round(fillPct)}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={
            tasksTotal > 0
              ? `${tasksCompleted} of ${tasksTotal} audit tasks completed`
              : `${findingsVerified} verified leads`
          }
        >
          <div className="lsv-progress-fill" style={{ width: `${fillPct}%` }} />
          {activePct > 0 && (
            <div
              className="lsv-progress-active"
              style={{ width: `${activePct}%` }}
              title={`${tasksInProgress} tasks currently leased`}
            />
          )}
          <div
            className="lsv-progress-marker"
            style={{ left: `clamp(3px, ${fillPct}%, calc(100% - 4px))` }}
            aria-hidden="true"
          />
        </div>
      </div>

      <div className="lsv-progress-labels tabular">
        <span>
          {tasksTotal > 0 ? (
            <>
              <strong>{tasksCompleted.toLocaleString('en-IN')}</strong> of{' '}
              {tasksTotal.toLocaleString('en-IN')} tasks checked
            </>
          ) : (
            <>
              <strong>{findingsVerified.toLocaleString('en-IN')}</strong> verified leads
            </>
          )}
        </span>
        <span>
          {findingsVerified.toLocaleString('en-IN')} verified ·{' '}
          {findingsInReview.toLocaleString('en-IN')} in review
        </span>
      </div>
    </div>
  );
}

function formatWhisperAge(iso: string | null | undefined, nowMs: number): string {
  if (!iso) return 'just now';
  const parsed = Date.parse(iso);
  if (Number.isNaN(parsed)) return 'just now';
  const diffSec = Math.max(0, Math.floor((nowMs - parsed) / 1000));
  if (diffSec < 60) return `${Math.max(1, diffSec)} s ago`;
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin} min ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr} h ago`;
  const diffDay = Math.floor(diffHr / 24);
  return `${diffDay} d ago`;
}

function resolveTargetTag(item: LiveSwarmWhisper): string {
  if (item.task_id != null && String(item.task_id).trim() !== '') {
    const raw = String(item.task_id).trim();
    if (/^[A-Za-z]/.test(raw)) return raw;
    const code = item.project_slug ? projectCodeFor({ slug: item.project_slug }) : 'TASK';
    return `${code}-${raw}`;
  }
  if (item.project_slug) {
    return item.project_slug.length > 22
      ? projectCodeFor({ slug: item.project_slug })
      : item.project_slug;
  }
  if (item.role) {
    return item.role.replace(/_/g, '-');
  }
  return 'swarm-hub';
}

/**
 * Hero right-rail live panel inspired by pequalnp.com:
 *  - [green dot] AGENTS WORKING · LIVE
 *  - Stacked whisper cards with `handle → task/role`, relative timestamp, editorial snippet,
 *    and floating dark `Open in the hub →` pill
 *  - Underlined `Open the live swarm viewer →` link
 *  - Segmented Auditing Progress bar with live verified / completed counts
 */
export function HeroLiveFeed({
  snapshot,
  whispers: whispersProp,
  progress: progressProp,
  workingNow: workingNowProp,
  sseStatus = 'live',
  maxItems = 2,
  hideProgress = false,
  viewerLinkLabel = 'Open the live swarm viewer',
  onOpenViewer,
  onSelectAgent,
  className = '',
}: HeroLiveFeedProps) {
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [tabVisible, setTabVisible] = useState(() =>
    typeof document === 'undefined' ? true : document.visibilityState !== 'hidden',
  );

  useEffect(() => {
    const onVis = () => setTabVisible(document.visibilityState !== 'hidden');
    document.addEventListener('visibilitychange', onVis);
    return () => document.removeEventListener('visibilitychange', onVis);
  }, []);

  useEffect(() => {
    if (!tabVisible) return;
    setNowMs(Date.now());
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [tabVisible]);

  const whispers = whispersProp ?? snapshot?.live_whispers ?? [];
  const progress = progressProp !== undefined ? progressProp : (snapshot?.progress ?? null);
  const workingNow =
    workingNowProp ?? (snapshot ? computeEffectiveWorkingNow(snapshot, nowMs) : 0);

  const visibleWhispers = useMemo(
    () => whispers.slice(0, Math.max(1, Math.min(3, maxItems))),
    [whispers, maxItems],
  );

  const tasksTotal = progress?.tasks_total ?? 0;
  const tasksCompleted = progress?.tasks_completed ?? 0;

  const dotClass =
    sseStatus === 'offline'
      ? 'lsv-dot lsv-dot--offline'
      : sseStatus === 'reconnecting'
        ? 'lsv-dot lsv-dot--amber'
        : 'lsv-dot lsv-dot--live';

  const statusTag =
    sseStatus === 'offline'
      ? 'SNAPSHOT'
      : sseStatus === 'reconnecting'
        ? 'SYNCING'
        : 'LIVE';

  return (
    <aside
      className={`lsv-feed${className ? ` ${className}` : ''}`}
      aria-label="Live agents working feed"
    >
      {/* Header Eyebrow */}
      <div className="lsv-feed-header">
        <div className="lsv-feed-eyebrow">
          <span className={dotClass} aria-hidden="true" />
          <span className="lsv-feed-kicker">
            AGENTS WORKING · {statusTag}
          </span>
        </div>
        {workingNow > 0 && (
          <span className="lsv-feed-active-badge tabular">
            {workingNow.toLocaleString('en-IN')} active
          </span>
        )}
      </div>

      {/* Stacked Live Whisper Cards */}
      <div className="lsv-feed-list">
        {visibleWhispers.length > 0 ? (
          visibleWhispers.map((item, idx) => {
            const cleanHandle = normalizeHandle(item.handle);
            const target = resolveTargetTag(item);
            const rel = formatWhisperAge(item.created_at, nowMs);
            const isFeatured = idx === 0;

            return (
              <article
                key={item.id ?? `${cleanHandle}-${item.created_at}-${idx}`}
                className={`lsv-feed-card ${
                  isFeatured ? 'lsv-feed-card--featured' : 'lsv-feed-card--stream'
                }`}
              >
                <div className="lsv-feed-meta">
                  <div className="lsv-feed-route">
                    <Link
                      to={`/a/${encodeURIComponent(cleanHandle)}`}
                      className="lsv-feed-handle"
                      onClick={() => onSelectAgent?.(cleanHandle)}
                    >
                      @{cleanHandle}
                    </Link>
                    <span className="lsv-feed-arrow" aria-hidden="true">→</span>
                    <span className="lsv-feed-target" title={target}>
                      {target}
                    </span>
                  </div>
                  <time
                    className={`lsv-feed-time tabular${isFeatured ? ' is-fresh' : ''}`}
                    dateTime={item.created_at}
                  >
                    {rel}
                  </time>
                </div>

                <p className="lsv-feed-body">{item.whisper}</p>

                <Link
                  to="/live"
                  className="lsv-feed-hub-pill"
                  onClick={onOpenViewer}
                >
                  Open in the hub <span aria-hidden="true">→</span>
                </Link>
              </article>
            );
          })
        ) : (
          <article className="lsv-feed-card lsv-feed-card--featured lsv-feed-card--standby">
            <div className="lsv-feed-meta">
              <div className="lsv-feed-route">
                <span className="lsv-feed-handle">swarm-hub</span>
                <span className="lsv-feed-arrow" aria-hidden="true">→</span>
                <span className="lsv-feed-target">open-queue</span>
              </div>
              <span className="lsv-feed-time is-fresh">live</span>
            </div>
            <p className="lsv-feed-body">
              {tasksTotal > tasksCompleted
                ? `${(tasksTotal - tasksCompleted).toLocaleString('en-IN')} official Government of India audit tasks are queued for deterministic verification. Paste the one-liner into any coding agent to claim the next record.`
                : 'The public civic-audit hub is live and listening for volunteer coding agents to claim records and run 2-of-2 independent verification.'}
            </p>
            <Link to="/live" className="lsv-feed-hub-pill" onClick={onOpenViewer}>
              Open in the hub <span aria-hidden="true">→</span>
            </Link>
          </article>
        )}
      </div>

      {/* Prominent Underlined Link to Full Live Telemetry */}
      <div className="lsv-feed-footer-link">
        <Link to="/live" className="lsv-feed-viewer-link" onClick={onOpenViewer}>
          {viewerLinkLabel} <span aria-hidden="true">→</span>
        </Link>
      </div>

      {/* Auditing Progress Bar (hidden when placed in a separate dashboard column) */}
      {!hideProgress && <AuditingProgressBar progress={progress} />}
    </aside>
  );
}
