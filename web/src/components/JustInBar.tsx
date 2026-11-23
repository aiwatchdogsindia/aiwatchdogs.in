import { useEffect, useRef, useState } from 'react';
import type { FindingItem, JustInItem } from '../types';
import { formatInrCr, formatRelativeTime } from '../utils';

interface JustInBarProps {
  justIn?: JustInItem | null;
  fallbackFinding?: FindingItem | null;
  onSelectFinding: (id: string | number) => void;
}

export function JustInBar({ justIn, fallbackFinding, onSelectFinding }: JustInBarProps) {
  const [arriving, setArriving] = useState(false);
  const prevIdRef = useRef<string | number | null>(null);

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
          is_demo_sample: fallbackFinding.is_demo_sample,
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

  if (!item) return null;

  return (
    <div
      className={`just-in ${arriving ? 'arriving' : ''}`}
      role="region"
      aria-label="Latest peer-verified audit record"
    >
      <span className="just-in-kicker">
        <span className="live-dot sm" aria-hidden="true" />
        <span>JUST VERIFIED</span>
      </span>

      <span className="just-in-title">{item.title}</span>

      {item.monetary_exposure_inr_cr > 0 && (
        <span className="just-in-meta tabular">
          {formatInrCr(item.monetary_exposure_inr_cr)}
        </span>
      )}

      {item.is_demo_sample && <span className="just-in-demo">SAMPLE DEMO</span>}

      {item.verified_at && (
        <span className="just-in-meta">{formatRelativeTime(item.verified_at)}</span>
      )}

      <button
        type="button"
        className="just-in-cta"
        onClick={() => onSelectFinding(item.id)}
      >
        Inspect dossier →
      </button>
    </div>
  );
}
