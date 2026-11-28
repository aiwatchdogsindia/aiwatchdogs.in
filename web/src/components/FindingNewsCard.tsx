import { getFindingTag } from '../lib/tags';
import type { FindingItem } from '../types';
import { formatRelativeTime, isUnverifiedStatus } from '../utils';

interface FindingNewsCardProps {
  finding: FindingItem;
  related?: FindingItem[];
  onSelect: (id: string | number) => void;
}

function Meta({ finding, source }: { finding: FindingItem; source?: string }) {
  const tag = getFindingTag(finding.status);
  const confirmed = finding.status === 'verified' || finding.status === 'rti_filed';
  const when = finding.verified_at || finding.created_at;
  return (
    <p className="news-card-meta">
      {!confirmed && (
        <span className="news-card-status" title={tag.description}>
          {tag.label}
        </span>
      )}
      {when && <span>{formatRelativeTime(when)}</span>}
      {when && source && <span aria-hidden="true">—</span>}
      {source && <span className="news-card-source">{source}</span>}
    </p>
  );
}

function headOf(f: FindingItem): string {
  return (!f.quarantined && f.status !== 'quarantined' && f.plain?.head) || f.title;
}

function sameAs(f: FindingItem): string {
  const where = [f.entity_name, f.fiscal_year].filter((x) => x && String(x).trim()).join(', ');
  return where ? `Same finding: ${where}` : 'Same finding, another record';
}

function clip(text: string, max: number): string {
  const clean = text.replace(/\s+/g, ' ').trim();
  return clean.length > max ? `${clean.slice(0, max - 1).trimEnd()}…` : clean;
}

export function FindingNewsCard({ finding, related = [], onSelect }: FindingNewsCardProps) {
  const withheld = Boolean(finding.quarantined || finding.status === 'quarantined');
  const plain = withheld ? undefined : finding.plain;
  const pending = isUnverifiedStatus(finding.status);
  const dek = withheld
    ? finding.quarantine_reason || 'This record is withheld while a privacy or legal review is done.'
    : plain?.what || clip(finding.summary || '', 240);
  const source = plain?.project || finding.entity_name;

  return (
    <article className={`news-card${pending ? ' news-card--pending' : ''}`}>
      <button type="button" className="news-card-hit" onClick={() => onSelect(finding.id)} dir={plain?.dir}>
        <h2 className="news-card-head">{headOf(finding)}</h2>
        {dek && <p className="news-card-dek">{dek}</p>}
      </button>
      <Meta finding={finding} source={source} />
      {related.length > 0 && (
        <ul className="news-card-related">
          {related.map((r) => (
            <li key={r.id}>
              <button type="button" className="news-card-related-hit" onClick={() => onSelect(r.id)} dir={r.plain?.dir}>
                {headOf(r) === headOf(finding) ? sameAs(r) : headOf(r)}
              </button>
              <Meta finding={r} />
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}
