import { AgentBadge } from './AgentLogos';
import { CheckCircle2, CircleDashed, FileText, ShieldAlert, ShieldCheck } from 'lucide-react';
import { ACTION_TAGS, FINDING_TAGS, getFindingTag, getWorkTypeTag } from '../lib/tags';
import type { FindingItem } from '../types';
import {
  extractOnlyOneBidMetrics,
  formatInr,
  formatOnlyOneBidBidsSummary,
  formatProcurementAmountType,
  formatProcurementSignal,
  formatRelativeTime,
  formatSingleBidMechanism,
  isUnverifiedStatus,
  resolveFindingCardMetric,
} from '../utils';

interface FindingCardProps {
  finding: FindingItem;
  onSelect: (id: string | number) => void;
  compact?: boolean;
}

export function FindingCard({ finding, onSelect, compact = false }: FindingCardProps) {
  const sm = getFindingTag(finding.status);
  const workTag = getWorkTypeTag(undefined, finding.methodology_code);
  const unverified = isUnverifiedStatus(finding.status);
  const confirmCount =
    finding.confirm_count ?? finding.verifications?.filter((v) => v.verdict === 'confirm').length ?? 0;
  const required = finding.required_quorum ?? 3;

  const hasCorrections =
    Boolean(finding.has_corrections) ||
    Boolean(finding.editorial_status && finding.editorial_status !== 'none') ||
    Boolean(finding.corrections && finding.corrections.length > 0);
  const isQuarantined = Boolean(finding.quarantined || finding.status === 'quarantined');
  const cardMetric = resolveFindingCardMetric(finding, FINDING_TAGS.withheld.label);
  const p7Record =
    finding.methodology_code === 'P7' && !isQuarantined
      ? extractOnlyOneBidMetrics(finding.anomaly_metrics)
      : null;
  const p7Signal = p7Record?.signalCategory
    ? formatProcurementSignal(p7Record.signalCategory)
    : null;
  const p7BidsText = p7Record ? formatOnlyOneBidBidsSummary(p7Record) : null;
  const p7MechText = p7Record ? formatSingleBidMechanism(p7Record.singleBidMechanism) : null;
  const p7TargetVal =
    finding.monetary_exposure_inr != null && finding.monetary_exposure_inr > 0
      ? finding.monetary_exposure_inr
      : p7Record?.targetTenderValueInr ?? null;

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
            {sm.id === 'verified' ? (
              <ShieldCheck size={12} />
            ) : sm.tone === 'disputed' || isQuarantined ? (
              <ShieldAlert size={12} />
            ) : sm.id === 'cleared' ? (
              <CheckCircle2 size={12} />
            ) : (
              <CircleDashed size={12} />
            )}
            <span>{sm.label}</span>
          </span>
          <span className={workTag.badgeClass} title={`${finding.methodology_code}: ${workTag.description}`}>
            {workTag.label}
          </span>
          {p7Signal && (
            <span className={p7Signal.chipClass} title={`Procurement signal: ${p7Signal.label}`}>
              {p7Signal.shortLabel}
            </span>
          )}
          {hasCorrections && sm.id !== 'corrected' && (
            <span className={FINDING_TAGS.corrected.badgeClass} title={FINDING_TAGS.corrected.description}>
              {finding.corrections && finding.corrections.length > 0
                ? `${FINDING_TAGS.corrected.label} (${finding.corrections.length})`
                : FINDING_TAGS.corrected.label}
            </span>
          )}
          {!isQuarantined && (finding.has_rti_draft || finding.rti_draft) && (
            <span className={ACTION_TAGS.rti_draft.badgeClass} title={ACTION_TAGS.rti_draft.description}>
              {ACTION_TAGS.rti_draft.label}
            </span>
          )}
        </div>

        <div
          className="font-mono tabular"
          style={{
            fontSize: 13,
            fontWeight: 500,
            color: cardMetric.prominent ? 'var(--ink)' : 'var(--ink-4)',
          }}
        >
          {cardMetric.value}
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
          overflowWrap: 'break-word',
        }}
      >
        {finding.title}
      </h3>

      {!compact && (
        <p style={{ margin: 0, fontSize: 14, color: 'var(--ink-2)', lineHeight: 1.55, overflowWrap: 'break-word' }}>
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

      {p7Record && (
        <div className="fc-p7-strip" aria-label="Tender competition facts">
          <div className="fc-p7-meta-row">
            {p7Record.portal && <span className="tag-mono">{p7Record.portal}</span>}
            {(p7Record.tenderId || p7Record.ocid) && (
              <span className="tag-mono font-mono">
                {p7Record.tenderId || p7Record.ocid}
              </span>
            )}
            {p7BidsText && <span className="tag-mono tabular">{p7BidsText}</span>}
            {p7MechText && !compact && <span className="tag-mono">{p7MechText}</span>}
            {p7Record.bidWindowDays != null && !compact && (
              <span className="tag-mono tabular">{p7Record.bidWindowDays}d window</span>
            )}
          </div>

          <div className="fc-p7-facts-grid tabular">
            {p7Record.winnerName && (
              <div className="fc-p7-fact">
                <span className="fc-p7-fact-label">Awardee &amp; repeat pattern</span>
                <span className="fc-p7-fact-val">
                  <strong>{p7Record.winnerName}</strong>
                  {p7Record.winnerTotalWins != null && p7Record.winnerTotalWins > 0
                    ? ` · ${p7Record.winnerTotalWins.toLocaleString('en-IN')} ${
                        p7Record.winnerTotalWins === 1 ? 'win' : 'wins'
                      }`
                    : ''}
                  {p7Record.winnerSingleBidWins != null && p7Record.winnerSingleBidWins > 0
                    ? ` (${p7Record.winnerSingleBidWins.toLocaleString('en-IN')} single-bid)`
                    : ''}
                  {p7Record.winnerShareByCountPct != null && p7Record.winnerShareByCountPct > 0
                    ? ` · ${p7Record.winnerShareByCountPct.toFixed(1)}% share`
                    : ''}
                  {p7Record.consecutiveWinStreak != null && p7Record.consecutiveWinStreak >= 2
                    ? ` · ${p7Record.consecutiveWinStreak}-win streak`
                    : ''}
                </span>
              </div>
            )}

            {(p7TargetVal != null ||
              (p7Record.winnerCohortTotalInr != null && p7Record.winnerCohortTotalInr > 0)) && (
              <div className="fc-p7-fact">
                <span className="fc-p7-fact-label">Values (separated — never double-counted)</span>
                <span className="fc-p7-fact-val">
                  <span>
                    {formatProcurementAmountType(p7Record.amountType ?? finding.amount_label)}:{' '}
                    <strong>{p7TargetVal != null ? formatInr(p7TargetVal) : 'Unrecorded'}</strong>
                  </span>
                  {p7Record.winnerCohortTotalInr != null &&
                  p7Record.winnerCohortTotalInr > 0 &&
                  (p7Record.winnerTotalWins ?? 0) >= 2 ? (
                    <span className="fc-p7-cohort-sep">
                      {' · '}Cohort total ({p7Record.winnerTotalWins?.toLocaleString('en-IN')} awards):{' '}
                      {formatInr(p7Record.winnerCohortTotalInr)}
                    </span>
                  ) : null}
                </span>
              </div>
            )}
          </div>
        </div>
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
              via <AgentBadge agent={finding.submitted_coding_agent} model={finding.submitted_coding_agent_model} />
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
