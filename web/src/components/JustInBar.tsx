import { useEffect, useRef, useState } from 'react';
import type { FindingItem, JustInItem } from '../types';
import { formatInrCr, formatRelativeTime, isUnverifiedStatus } from '../utils';

interface JustInBarProps {
  justIn?: JustInItem | null;
  fallbackFinding?: FindingItem | null;
  queuedTasks?: number;
  missionsCount?: number;
  onSelectFinding: (id: string | number) => void;
  onOpenJoin?: () => void;
}

export function JustInBar({
  justIn,
  fallbackFinding,
  queuedTasks = 0,
  missionsCount = 8,
  onSelectFinding,
  onOpenJoin,
}: JustInBarProps) {
  const [arriving, setArriving] = useState(false);
  const prevIdRef = useRef<string | number | null>(null);

  const isVerifiedItem =
    Boolean(justIn) ||
    Boolean(
      fallbackFinding &&
        (fallbackFinding.status === 'verified' || fallbackFinding.status === 'rti_filed'),
    );

  const item: JustInItem | null =
    justIn ??
    (fallbackFinding
      ? {
          id: fallbackFinding.id,
          title: fallbackFinding.title,
          methodology_code: fallbackFinding.methodology_code,
          entity_name: fallbackFinding.entity_name,
          monetary_exposure_inr_cr: fallbackFinding.monetary_exposure_inr
            ? fallbackFinding.monetary_exposure_inr / 1e7
            : 0,
          verified_at: fallbackFinding.verified_at || fallbackFinding.created_at || '',
        }
      : null);

  useEffect(() => {
    if (!item) return;
    if (prevIdRef.current !== null && prevIdRef.current !== item.id) {
      setArriving(true);
      const t = window.setTimeout(() => setArriving(false), 2100);
      prevIdRef.current = item.id;
      return () => window.clearTimeout(t);
    }
    prevIdRef.current = item.id;
  }, [item]);

  if (!item) {
    if (queuedTasks <= 0) return null;
    return (
      <div
        className="just-in"
        role="region"
        aria-label="Open audit queue"
      >
        <span className="just-in-kicker">
          <span className="live-dot sm" aria-hidden="true" />
          <span>OPEN AUDIT QUEUE</span>
        </span>

        <span className="just-in-title">
          {queuedTasks} official dataset{queuedTasks === 1 ? '' : 's'} queued across {missionsCount}{' '}
          community {missionsCount === 1 ? 'project' : 'projects'}
        </span>

        <span className="just-in-meta">2-of-2 quorum active</span>

        {onOpenJoin && (
          <button type="button" className="just-in-cta" onClick={onOpenJoin}>
            Claim a task
          </button>
        )}
      </div>
    );
  }

  const kickerLabel = isVerifiedItem
    ? 'JUST VERIFIED'
    : fallbackFinding && isUnverifiedStatus(fallbackFinding.status)
      ? 'IN PEER REVIEW'
      : 'LATEST AUDIT RECORD';

  return (
    <div
      className={`just-in ${arriving ? 'arriving' : ''}`}
      role="region"
      aria-label="Latest public audit record"
    >
      <span className="just-in-kicker">
        <span className="live-dot sm" aria-hidden="true" />
        <span>{kickerLabel}</span>
      </span>

      <span className="just-in-title">{item.title}</span>

      {item.monetary_exposure_inr_cr > 0 && (
        <span className="just-in-meta tabular">
          {formatInrCr(item.monetary_exposure_inr_cr)}
        </span>
      )}

      {item.verified_at && (
        <span className="just-in-meta">{formatRelativeTime(item.verified_at)}</span>
      )}

      <button
        type="button"
        className="just-in-cta"
        onClick={() => onSelectFinding(item.id)}
      >
        Inspect dossier
      </button>
    </div>
  );
}
