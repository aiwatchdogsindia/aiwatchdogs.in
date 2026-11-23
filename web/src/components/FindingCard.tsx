import { CheckCircle2, CircleDashed, FileText, ShieldAlert, ShieldCheck } from 'lucide-react';
import type { FindingItem } from '../types';
import { formatInr, formatRelativeTime, isUnverifiedStatus, statusMeta } from '../utils';

interface FindingCardProps {
  finding: FindingItem;
  onSelect: (id: string | number) => void;
  compact?: boolean;
}

export function FindingCard({ finding, onSelect, compact = false }: FindingCardProps) {
  const sm = statusMeta(finding.status);
  const unverified = isUnverifiedStatus(finding.status);
  const confirmCount =
    finding.confirm_count ?? finding.verifications?.filter((v) => v.verdict === 'confirm').length ?? 0;
  const required = finding.required_quorum ?? 2;

  return (
    <article
      className={`finding-card status-${finding.status}`}
      onClick={() => onSelect(finding.id)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onSelect(finding.id);
        }
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 8,
          flexWrap: 'wrap',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          <span className={sm.badgeClass} title={sm.description}>
            {finding.status === 'verified' ? (
              <ShieldCheck size={12} />
            ) : finding.status === 'disputed' || finding.status === 'retracted_false_positive' ? (
              <ShieldAlert size={12} />
            ) : finding.status === 'cleared' || finding.status === 'resolved_explained' ? (
              <CheckCircle2 size={12} />
            ) : (
              <CircleDashed size={12} />
            )}
            <span>{sm.label}</span>
          </span>
          <span className="tag-mono tag-method">{finding.methodology_code}</span>
          {(finding.has_rti_draft || finding.rti_draft) && (
            <span className="badge badge-rti">RTI Draft Ready</span>
          )}
        </div>

        <div
          className="font-mono tabular"
          style={{
            fontSize: 13,
            fontWeight: 500,
            color: finding.monetary_exposure_inr ? 'var(--ink)' : 'var(--ink-4)',
          }}
        >
          {finding.monetary_exposure_inr ? formatInr(finding.monetary_exposure_inr) : 'Disclosure Lead'}
        </div>
      </div>

      <h3
        className="font-serif"
        style={{
          margin: 0,
          fontSize: compact ? 18 : 21,
          fontWeight: 400,
          lineHeight: 1.28,
          fontStyle: unverified ? 'italic' : 'normal',
          color: unverified ? 'var(--ink-2)' : 'var(--ink)',
        }}
      >
        {finding.title}
      </h3>

      {!compact && finding.summary && (
        <p style={{ margin: 0, fontSize: 13.5, color: 'var(--ink-2)', lineHeight: 1.55 }}>
          {finding.summary.length > 220 ? `${finding.summary.slice(0, 220)}…` : finding.summary}
        </p>
      )}

      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 10,
          flexWrap: 'wrap',
          paddingTop: 8,
          borderTop: '1px solid var(--rule)',
          fontSize: 12,
          color: 'var(--ink-3)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ color: 'var(--ink-2)', fontWeight: 500 }}>{finding.entity_name}</span>
          <span style={{ color: 'var(--ink-5)' }}>·</span>
          <span>{finding.jurisdiction}</span>
          <span style={{ color: 'var(--ink-5)' }}>·</span>
          <span className="font-mono">{finding.fiscal_year}</span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <span className="font-mono tabular" style={{ fontSize: 11.5 }}>
            Quorum <strong>{confirmCount}/{required}</strong>
          </span>
          {finding.artifact_sha256 && (
            <span className="font-mono" style={{ fontSize: 11, color: 'var(--ink-4)' }}>
              sha256:{finding.artifact_sha256.slice(0, 8)}
            </span>
          )}
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 4,
              color: 'var(--ink)',
              fontWeight: 500,
            }}
          >
            <FileText size={12} />
            <span>Dossier</span>
          </span>
          {finding.created_at && (
            <span className="font-mono" style={{ fontSize: 11, color: 'var(--ink-4)' }}>
              {formatRelativeTime(finding.created_at)}
            </span>
          )}
        </div>
      </div>
    </article>
  );
}
