import { CheckCircle2, CircleDashed, FileText, ShieldAlert, ShieldCheck } from 'lucide-react';
import type { FindingItem } from '../types';
import { codingAgentLabel, formatInr, formatRelativeTime, isUnverifiedStatus, statusMeta } from '../utils';

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

  const hasCorrections =
    Boolean(finding.has_corrections) ||
    Boolean(finding.editorial_status && finding.editorial_status !== 'none') ||
    Boolean(finding.corrections && finding.corrections.length > 0);
  const isQuarantined = Boolean(finding.quarantined || finding.status === 'quarantined');

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
            ) : finding.status === 'disputed' ||
              finding.status === 'retracted_false_positive' ||
              finding.status === 'under_editorial_review' ||
              finding.status === 'corrected' ||
              isQuarantined ? (
              <ShieldAlert size={12} />
            ) : finding.status === 'cleared' || finding.status === 'resolved_explained' ? (
              <CheckCircle2 size={12} />
            ) : (
              <CircleDashed size={12} />
            )}
            <span>{sm.label}</span>
          </span>
          <span className="tag-mono tag-method">{finding.methodology_code}</span>
          {hasCorrections && (
            <span className="badge badge-disputed">
              {finding.corrections && finding.corrections.length > 0
                ? `Correction Log (${finding.corrections.length})`
                : 'Editorial Notice'}
            </span>
          )}
          {!isQuarantined && (finding.has_rti_draft || finding.rti_draft) && (
            <span className="badge badge-rti">RTI Draft Ready</span>
          )}
        </div>

        <div
          className="font-mono tabular"
          style={{
            fontSize: 13,
            fontWeight: 500,
            color: finding.monetary_exposure_inr && !isQuarantined ? 'var(--ink)' : 'var(--ink-4)',
          }}
        >
          {isQuarantined
            ? 'Withheld'
            : finding.monetary_exposure_inr
              ? formatInr(finding.monetary_exposure_inr)
              : finding.amount_label || 'Disclosure Lead'}
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

      {!compact && (
        <p style={{ margin: 0, fontSize: 14, color: 'var(--ink-2)', lineHeight: 1.55 }}>
          {isQuarantined
            ? finding.quarantine_reason ||
              'This record is quarantined pending privacy or legal compliance review. Raw metrics and excerpts are withheld.'
            : finding.summary
              ? finding.summary.length > 220
                ? `${finding.summary.slice(0, 220)}…`
                : finding.summary
              : ''}
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
          <span>{finding.jurisdiction}</span>
          <span className="font-mono">{finding.fiscal_year}</span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <span className="font-mono tabular" style={{ fontSize: 11.5 }}>
            Quorum <strong>{confirmCount}/{required}</strong>
          </span>
          {finding.submitted_coding_agent && (
            <span className="via-agent">
              via {codingAgentLabel(finding.submitted_coding_agent, finding.submitted_coding_agent_model)}
            </span>
          )}
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
