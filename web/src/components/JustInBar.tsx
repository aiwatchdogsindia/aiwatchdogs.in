import { useMemo } from 'react';
import type { FindingItem, JustInItem } from '../types';
import { formatInrCr, formatRelativeTime } from '../utils';
import { useLang } from '../lib/lang';

interface JustInBarProps {
  justIn?: JustInItem | null;
  recentFindings?: FindingItem[] | null;
  fallbackFinding?: FindingItem | null;
  queuedTasks?: number;
  missionsCount?: number;
  onSelectFinding: (id: string | number) => void;
  onOpenJoin?: () => void;
}

interface Entry {
  id: string | number;
  title: string;
  crore: number;
  when: string;
}

const MAX_ITEMS = 3;

function isVerified(f: FindingItem): boolean {
  return (f.status === 'verified' || f.status === 'rti_filed') && !f.quarantined;
}

function toEntry(f: FindingItem): Entry {
  return {
    id: f.id,
    title: f.plain?.head || f.title,
    crore:
      f.monetary_exposure_inr != null && Number.isFinite(f.monetary_exposure_inr)
        ? f.monetary_exposure_inr / 1e7
        : 0,
    when: f.verified_at || f.created_at || '',
  };
}

export function JustInBar({ justIn, recentFindings, fallbackFinding, onSelectFinding }: JustInBarProps) {
  const { t, dir } = useLang();

  const entries = useMemo<Entry[]>(() => {
    const out: Entry[] = [];
    const seen = new Set<string>();
    const add = (e: Entry) => {
      const key = String(e.id);
      if (seen.has(key) || out.length >= MAX_ITEMS) return;
      seen.add(key);
      out.push(e);
    };
    for (const f of recentFindings ?? []) if (isVerified(f)) add(toEntry(f));
    if (justIn) {
      add({
        id: justIn.id,
        title: justIn.title,
        crore: justIn.monetary_exposure_inr_cr ?? 0,
        when: justIn.verified_at || '',
      });
    }
    if (fallbackFinding && isVerified(fallbackFinding)) add(toEntry(fallbackFinding));
    return out;
  }, [justIn, recentFindings, fallbackFinding]);

  return (
    <section className="latest-strip" aria-label={t('recently_confirmed')} dir={dir}>
      <div className="latest-strip-inner">
        <div className="latest-strip-head">
          <span className="latest-strip-kicker">{t('recently_confirmed')}</span>
        </div>
        {entries.length === 0 ? (
          <p className="latest-strip-empty">{t('nothing_confirmed')}</p>
        ) : (
          <ol className="latest-strip-list">
            {entries.map((e) => (
              <li key={String(e.id)}>
                <button type="button" className="latest-strip-item" onClick={() => onSelectFinding(e.id)}>
                  <strong className="latest-strip-title">{e.title}</strong>
                  <span className="latest-strip-meta" dir="ltr">
                    {e.crore > 0 && <span className="tabular">{formatInrCr(e.crore)}</span>}
                    {e.when && <span>{formatRelativeTime(e.when)}</span>}
                  </span>
                </button>
              </li>
            ))}
          </ol>
        )}
      </div>
    </section>
  );
}
