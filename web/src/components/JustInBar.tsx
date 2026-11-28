import { useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import type { FindingItem, JustInItem } from '../types';
import { formatInrCr, formatRelativeTime, isUnverifiedStatus } from '../utils';

interface JustInBarProps {
  justIn?: JustInItem | null;
  recentFindings?: FindingItem[] | null;
  fallbackFinding?: FindingItem | null;
  queuedTasks?: number;
  missionsCount?: number;
  onSelectFinding: (id: string | number) => void;
  onOpenJoin?: () => void;
}

interface TickerEntry {
  id: string | number;
  title: string;
  monetary_exposure_inr_cr: number;
  verified_at: string;
}

const MAX_TICKER_ITEMS = 12;
const MIN_ENTRIES_PER_GROUP = 4;
const MAX_REPEATS_PER_GROUP = 24;
const MIN_GROUP_WIDTH_PX = 2560;
const TARGET_SPEED_PX_PER_SEC = 60;
const MIN_DURATION_SEC = 18;
const MAX_DURATION_SEC = 360;

function isVerifiedFinding(f: FindingItem): boolean {
  return (f.status === 'verified' || f.status === 'rti_filed') && !f.quarantined;
}

function findingToEntry(f: FindingItem): TickerEntry {
  return {
    id: f.id,
    title: f.plain?.head || f.title,
    monetary_exposure_inr_cr:
      f.monetary_exposure_inr != null && Number.isFinite(f.monetary_exposure_inr)
        ? f.monetary_exposure_inr / 1e7
        : 0,
    verified_at: f.verified_at || f.created_at || '',
  };
}

function estimateDurationSec(entries: TickerEntry[], repeatCount: number): number {
  const perCopyChars = entries.reduce((sum, e) => sum + e.title.length + 48, 0);
  const approxPx = perCopyChars * repeatCount * 7.2;
  const raw = approxPx / TARGET_SPEED_PX_PER_SEC;
  return Math.max(MIN_DURATION_SEC, Math.min(MAX_DURATION_SEC, Math.round(raw * 10) / 10));
}

export function JustInBar({
  justIn,
  recentFindings,
  fallbackFinding,
  queuedTasks = 0,
  missionsCount = 8,
  onSelectFinding,
  onOpenJoin,
}: JustInBarProps) {
  const [arriving, setArriving] = useState(false);
  const prevLeadIdRef = useRef<string | number | null>(null);
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const primaryGroupRef = useRef<HTMLDivElement | null>(null);

  const { entries, mode } = useMemo<{
    entries: TickerEntry[];
    mode: 'verified' | 'review' | 'latest' | 'queue';
  }>(() => {
    const verified: TickerEntry[] = [];
    const seen = new Set<string>();

    const pushUnique = (list: TickerEntry[], entry: TickerEntry) => {
      const key = String(entry.id);
      if (seen.has(key) || list.length >= MAX_TICKER_ITEMS) return;
      seen.add(key);
      list.push(entry);
    };

    if (justIn) {
      pushUnique(verified, {
        id: justIn.id,
        title: justIn.title,
        monetary_exposure_inr_cr: justIn.monetary_exposure_inr_cr ?? 0,
        verified_at: justIn.verified_at || '',
      });
    }

    if (recentFindings && recentFindings.length > 0) {
      for (const f of recentFindings) {
        if (isVerifiedFinding(f)) {
          pushUnique(verified, findingToEntry(f));
        }
      }
    }

    if (fallbackFinding && isVerifiedFinding(fallbackFinding)) {
      pushUnique(verified, findingToEntry(fallbackFinding));
    }

    if (verified.length > 0) {
      return { entries: verified, mode: 'verified' };
    }

    // Fallback: no verified findings yet -> show fallbackFinding + latest non-quarantined findings
    const fallbackList: TickerEntry[] = [];
    seen.clear();
    let firstStatus: string | undefined;

    if (fallbackFinding && !fallbackFinding.quarantined) {
      firstStatus = fallbackFinding.status;
      pushUnique(fallbackList, findingToEntry(fallbackFinding));
    }

    if (recentFindings && recentFindings.length > 0) {
      for (const f of recentFindings) {
        if (!f.quarantined) {
          if (!firstStatus) firstStatus = f.status;
          pushUnique(fallbackList, findingToEntry(f));
        }
      }
    }

    if (fallbackList.length > 0) {
      return {
        entries: fallbackList,
        mode: isUnverifiedStatus(firstStatus) ? 'review' : 'latest',
      };
    }

    return { entries: [], mode: 'queue' };
  }, [justIn, recentFindings, fallbackFinding]);

  const baseCount = entries.length > 0 ? entries.length : 1;
  const minRepeats = Math.max(1, Math.ceil(MIN_ENTRIES_PER_GROUP / baseCount));

  const signature = useMemo(
    () =>
      entries.length > 0
        ? `${mode}:${entries.map((e) => e.id).join(',')}`
        : `queue:${queuedTasks}:${missionsCount}`,
    [entries, mode, queuedTasks, missionsCount],
  );

  const [repeatState, setRepeatState] = useState<{ signature: string; extra: number }>({
    signature: '',
    extra: 0,
  });
  const extraRepeats = repeatState.signature === signature ? repeatState.extra : 0;
  const repeatCount = Math.min(MAX_REPEATS_PER_GROUP, minRepeats + extraRepeats);

  const [durationSec, setDurationSec] = useState<number>(() =>
    entries.length > 0 ? estimateDurationSec(entries, minRepeats) : 28,
  );

  const leadId = entries[0]?.id ?? null;

  useEffect(() => {
    if (leadId == null) return;
    if (prevLeadIdRef.current !== null && prevLeadIdRef.current !== leadId) {
      setArriving(true);
      const t = window.setTimeout(() => setArriving(false), 2100);
      prevLeadIdRef.current = leadId;
      return () => window.clearTimeout(t);
    }
    prevLeadIdRef.current = leadId;
  }, [leadId]);

  // Measure one group's rendered width to keep a steady ~60px/s broadcast pace
  // and ensure one group always spans beyond max(viewportWidth, 2560px) (zero blank gap).
  useLayoutEffect(() => {
    const viewport = viewportRef.current;
    const group = primaryGroupRef.current;
    if (!viewport || !group) return;

    const measure = () => {
      const vpWidth = viewport.getBoundingClientRect().width;
      const groupWidth = group.scrollWidth;
      if (groupWidth <= 0) return;

      const targetWidth = Math.max(vpWidth, MIN_GROUP_WIDTH_PX) + 160;
      if (groupWidth < targetWidth && repeatCount < MAX_REPEATS_PER_GROUP) {
        const needed = Math.min(
          MAX_REPEATS_PER_GROUP,
          Math.ceil((targetWidth / groupWidth) * repeatCount),
        );
        if (needed > repeatCount) {
          setRepeatState({ signature, extra: needed - minRepeats });
          return;
        }
      }

      const next = Math.max(
        MIN_DURATION_SEC,
        Math.min(MAX_DURATION_SEC, Math.round((groupWidth / TARGET_SPEED_PX_PER_SEC) * 10) / 10),
      );
      setDurationSec((prev) => (Math.abs(prev - next) >= 1 ? next : prev));
    };

    measure();

    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measure);
    ro.observe(viewport);
    ro.observe(group);
    return () => ro.disconnect();
  }, [signature, repeatCount, minRepeats]);

  if (entries.length === 0 && queuedTasks <= 0) {
    return null;
  }

  const kickerLabel =
    mode === 'verified'
      ? 'JUST VERIFIED'
      : mode === 'review'
        ? 'IN PEER REVIEW'
        : mode === 'latest'
          ? 'LATEST AUDIT'
          : 'OPEN QUEUE';

  const regionLabel =
    mode === 'verified'
      ? 'Just verified audit headlines'
      : mode === 'review'
        ? 'Audit records in peer review'
        : mode === 'latest'
          ? 'Latest public audit records'
          : 'Open audit queue';

  const trackStyle = {
    '--ticker-duration': `${durationSec}s`,
  } as CSSProperties;

  const renderInlineKicker = () => (
    <span className="just-in-kicker">
      <span className="just-in-dot" aria-hidden="true" />
      <span className="just-in-kicker-label">{kickerLabel}</span>
    </span>
  );

  const renderGroupContent = (isClone: boolean) => {
    if (mode === 'queue') {
      const queueHeadline = `${queuedTasks} official dataset${queuedTasks === 1 ? '' : 's'} queued across ${missionsCount} ${missionsCount === 1 ? 'project' : 'projects'}`;
      return Array.from({ length: repeatCount }, (_, repIdx) => {
        const isRepeat = repIdx > 0;
        const hideFromAt = isClone || isRepeat;
        return (
          <span
            className={`just-in-entry${isRepeat ? ' just-in-entry--repeat' : ''}`}
            key={`${isClone ? 'c' : 'p'}-q-${repIdx}`}
            aria-hidden={hideFromAt || undefined}
          >
            {onOpenJoin ? (
              <button
                type="button"
                className="just-in-item"
                tabIndex={hideFromAt ? -1 : 0}
                onClick={onOpenJoin}
              >
                {renderInlineKicker()}
                <span className="just-in-title">{queueHeadline}</span>
                <span className="just-in-meta">3-of-3 quorum active</span>
                <span className="just-in-cta">
                  Claim a task <span aria-hidden="true">→</span>
                </span>
              </button>
            ) : (
              <span className="just-in-item just-in-item--static">
                {renderInlineKicker()}
                <span className="just-in-title">{queueHeadline}</span>
                <span className="just-in-meta">3-of-3 quorum active</span>
              </span>
            )}
            <span className="just-in-sep" aria-hidden="true">
              ◆
            </span>
          </span>
        );
      });
    }

    return Array.from({ length: repeatCount }, (_, repIdx) => {
      const isRepeat = repIdx > 0;
      const hideFromAt = isClone || isRepeat;
      return entries.map((entry) => (
        <span
          className={`just-in-entry${isRepeat ? ' just-in-entry--repeat' : ''}`}
          key={`${isClone ? 'c' : 'p'}-${repIdx}-${entry.id}`}
          aria-hidden={hideFromAt || undefined}
        >
          <button
            type="button"
            className="just-in-item"
            tabIndex={hideFromAt ? -1 : 0}
            onClick={() => onSelectFinding(entry.id)}
          >
            {renderInlineKicker()}
            {entry.monetary_exposure_inr_cr > 0 && (
              <span className="just-in-amount tabular">
                {formatInrCr(entry.monetary_exposure_inr_cr)}
              </span>
            )}
            {entry.verified_at && (
              <span className="just-in-meta">{formatRelativeTime(entry.verified_at)}</span>
            )}
            <span className="just-in-title">{entry.title}</span>
            <span className="just-in-cta">
              Inspect dossier <span aria-hidden="true">→</span>
            </span>
          </button>
          <span className="just-in-sep" aria-hidden="true">
            ◆
          </span>
        </span>
      ));
    });
  };

  return (
    <div
      className={`just-in just-in--${mode}${arriving ? ' arriving' : ''}`}
      role="region"
      aria-label={regionLabel}
    >
      <div className="just-in-viewport" ref={viewportRef}>
        <div className="just-in-track" style={trackStyle}>
          <div className="just-in-group" ref={primaryGroupRef}>
            {renderGroupContent(false)}
          </div>
          <div className="just-in-group just-in-group--clone" aria-hidden="true">
            {renderGroupContent(true)}
          </div>
        </div>
      </div>
    </div>
  );
}
