import { AgentBadge } from './AgentLogos';
import { CheckCircle2, CircleDashed, FileText, ShieldAlert, ShieldCheck } from 'lucide-react';
import { ACTION_TAGS, FINDING_TAGS, getFindingTag, getWorkTypeTag } from '../lib/tags';
import type { FindingItem } from '../types';
import {
  extractInnocentButLockedUpMetrics,
  extractOnlyOneBidMetrics,
  extractSovereignLiesMetrics,
  formatChainStatus,
  formatCourtTier,
  formatCustodyOverrunMechanism,
  formatInr,
  formatJurisdictionLabel,
  formatOnlyOneBidBidsSummary,
  formatOverrunDays,
  formatPortalCategory,
  formatProcurementAmountType,
  formatProcurementSignal,
  formatRelativeTime,
  formatRetroactiveImpact,
  formatSchemeCategory,
  formatSingleBidMechanism,
  formatWalkbackMechanism,
  isUnverifiedStatus,
  resolveFindingCardMetric,
} from '../utils';

function clipCardText(text?: string | null, maxLen = 135): string {
  const clean = (text || '').replace(/\s+/g, ' ').trim();
  if (!clean) return '';
  return clean.length > maxLen ? `${clean.slice(0, maxLen - 1).trimEnd()}…` : clean;
}

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

  const p8Record =
    finding.methodology_code === 'P8' && !isQuarantined
      ? extractSovereignLiesMetrics(finding.anomaly_metrics)
      : null;
  const p8Mechanism = p8Record?.walkbackMechanism || p8Record?.signalCategory
    ? formatWalkbackMechanism(p8Record.walkbackMechanism || p8Record.signalCategory)
    : null;
  const p8Impact = p8Record?.retroactiveImpact
    ? formatRetroactiveImpact(p8Record.retroactiveImpact)
    : null;
  const p8Category = p8Record?.schemeCategory
    ? formatSchemeCategory(p8Record.schemeCategory)
    : null;
  const p8Jurisdiction = p8Record
    ? formatJurisdictionLabel(p8Record.jurisdiction, p8Record.stateOrUt, p8Record.jurisdictionLevel)
    : null;

  const p10Record =
    finding.methodology_code === 'P10' && !isQuarantined
      ? extractInnocentButLockedUpMetrics(finding.anomaly_metrics)
      : null;
  const p10Mechanism =
    p10Record && (p10Record.overrunMechanism || p10Record.signalCategory)
      ? formatCustodyOverrunMechanism(p10Record.overrunMechanism || p10Record.signalCategory)
      : null;
  const p10Portal = p10Record?.portalCategory
    ? formatPortalCategory(p10Record.portalCategory)
    : null;
  const p10Tier = p10Record?.courtTier ? formatCourtTier(p10Record.courtTier) : null;
  const p10Chain = p10Record
    ? formatChainStatus(p10Record.chainStatus, p10Record.stagesVerifiedCount)
    : null;
  const p10OverrunText = p10Record ? formatOverrunDays(p10Record.overrunDays) : null;

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
          {p8Mechanism && (
            <span className={p8Mechanism.chipClass} title={`Walk-back mechanism: ${p8Mechanism.label}`}>
              {p8Mechanism.shortLabel}
            </span>
          )}
          {p8Impact?.isRetroactive && (
            <span className={p8Impact.chipClass} title={p8Impact.label}>
              {p8Impact.shortLabel}
            </span>
          )}
          {p8Record?.isSgbAnchor && (
            <span className="p8-sgb-chip" title="Flagship Sovereign Gold Bond anchor case">
              SGB Anchor
            </span>
          )}
          {p10Mechanism && (
            <span className={p10Mechanism.chipClass} title={`Custody mechanism: ${p10Mechanism.label}`}>
              {p10Mechanism.shortLabel}
            </span>
          )}
          {p10Chain && (
            <span className={p10Chain.chipClass} title={p10Chain.label}>
              {p10Chain.shortLabel}
            </span>
          )}
          {p10Record?.isAnchorCase && (
            <span className="p10-anchor-chip" title="Flagship 4-stage post-acquittal custody anchor case">
              Anchor Case
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

      {p8Record && (
        <div className="fc-p8-strip" aria-label="Original promise versus subsequent revision">
          <div className="fc-p8-meta-row">
            {p8Jurisdiction && <span className="tag-mono">{p8Jurisdiction}</span>}
            {p8Category && <span className="tag-mono">{p8Category.shortLabel}</span>}
            {p8Record.promiseDate && (
              <span className="tag-mono tabular">
                {p8Record.promiseDate}
                {p8Record.walkbackDate ? ` → ${p8Record.walkbackDate}` : ''}
                {p8Record.daysFromPromiseToWalkback != null
                  ? ` (${p8Record.daysFromPromiseToWalkback.toLocaleString('en-IN')}d)`
                  : ''}
              </span>
            )}
            {p8Record.promisedMetric && p8Record.revisedMetric && (
              <span
                className="fc-p8-delta-pill tabular"
                title={`${p8Record.promisedMetric} → ${p8Record.revisedMetric}`}
              >
                {clipCardText(p8Record.promisedMetric, compact ? 28 : 42)} →{' '}
                {clipCardText(p8Record.revisedMetric, compact ? 28 : 42)}
              </span>
            )}
          </div>

          {(p8Record.promiseSummary || p8Record.walkbackSummary) && (
            <div className="fc-p8-compare-grid">
              <div className="fc-p8-col fc-p8-col--promise">
                <span className="fc-p8-col-kicker">Original Promise</span>
                <p className="fc-p8-col-text">
                  {clipCardText(p8Record.promiseSummary, compact ? 95 : 135) ||
                    'Launch promise documented in primary official notice.'}
                </p>
                {p8Record.promiseDocumentRef && !compact && (
                  <span className="fc-p8-col-ref font-mono">{p8Record.promiseDocumentRef}</span>
                )}
              </div>
              <div
                className={`fc-p8-col ${
                  p8Mechanism?.isCleared ? 'fc-p8-col--honored' : 'fc-p8-col--walkback'
                }`}
              >
                <span className="fc-p8-col-kicker">
                  {p8Mechanism?.isCleared ? 'Honored / Current Record' : 'Subsequent Walk-Back'}
                </span>
                <p className="fc-p8-col-text">
                  {clipCardText(p8Record.walkbackSummary, compact ? 95 : 135) ||
                    'Subsequent amendment documented in official revision record.'}
                </p>
                {p8Record.walkbackDocumentRef && !compact && (
                  <span className="fc-p8-col-ref font-mono">{p8Record.walkbackDocumentRef}</span>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {p10Record && (
        <div className="fc-p10-strip" aria-label="4-stage post-acquittal custody chain summary">
          <div className="fc-p8-meta-row">
            {p10Portal && <span className={p10Portal.chipClass}>{p10Portal.shortLabel}</span>}
            {p10Tier && <span className={p10Tier.chipClass}>{p10Tier.shortLabel}</span>}
            {(p10Record.stateUt || p10Record.jurisdiction) && (
              <span className="tag-mono">
                {p10Record.stateUt || formatJurisdictionLabel(p10Record.jurisdiction)}
              </span>
            )}
            {p10Record.year != null && <span className="tag-mono tabular">{String(p10Record.year)}</span>}
            {p10OverrunText && p10Record.overrunDays != null && p10Record.overrunDays > 0 && (
              <span className="p10-overrun-pill tabular">+{p10OverrunText} overrun</span>
            )}
            {p10Record.compensationInr != null && p10Record.compensationInr > 0 && (
              <span className="tag-mono tabular">{formatInr(p10Record.compensationInr)} comp.</span>
            )}
          </div>

          <ol className="fc-p10-stages" aria-label="Four-stage custody timeline">
            <li
              className={`fc-p10-stage fc-p10-stage--s1${
                p10Record.stage1AcquittalVerified || p10Record.acquittalOrderDate || p10Record.acquittalCourt
                  ? ' is-verified'
                  : ''
              }`}
            >
              <span className="fc-p10-stage-kicker">1 · Acquitted</span>
              <span className="fc-p10-stage-val">
                {clipCardText(
                  [p10Record.acquittalOrderDate, p10Record.acquittalCourt || p10Record.acquittalCaseNumber]
                    .filter(Boolean)
                    .join(' · '),
                  compact ? 42 : 68,
                ) || 'Release order'}
              </span>
            </li>
            <li
              className={`fc-p10-stage fc-p10-stage--s2${
                p10Record.stage2ContinuedDetentionVerified || p10Record.prisonName || (p10Record.overrunDays ?? 0) > 0
                  ? ' is-verified'
                  : ''
              }`}
            >
              <span className="fc-p10-stage-kicker">2 · Held in Prison</span>
              <span className="fc-p10-stage-val">
                {clipCardText(
                  [
                    p10Record.prisonName || p10Record.custodyAuthority,
                    p10OverrunText && (p10Record.overrunDays ?? 0) > 0 ? `+${p10OverrunText}` : p10Record.overrunWindow,
                  ]
                    .filter(Boolean)
                    .join(' · '),
                  compact ? 42 : 68,
                ) || 'Detention overrun'}
              </span>
            </li>
            <li
              className={`fc-p10-stage fc-p10-stage--s3${
                p10Record.stage3ContemptVerified || p10Record.contemptCaseNumber || p10Record.contemptCourt
                  ? ' is-verified'
                  : ''
              }`}
            >
              <span className="fc-p10-stage-kicker">3 · Contempt</span>
              <span className="fc-p10-stage-val">
                {clipCardText(
                  [
                    p10Record.contemptCaseNumber || p10Record.contemptCourt,
                    p10Record.contemptFilingDate || p10Record.contemptOrderDate,
                  ]
                    .filter(Boolean)
                    .join(' · '),
                  compact ? 42 : 68,
                ) || (p10Mechanism?.isCleared ? 'Not required' : 'Proceeding logged')}
              </span>
            </li>
            <li
              className={`fc-p10-stage fc-p10-stage--s4${
                p10Record.stage4ReleaseVerified || p10Record.releaseVerified || p10Record.releaseDate
                  ? ' is-verified'
                  : ''
              }`}
            >
              <span className="fc-p10-stage-kicker">4 · Released</span>
              <span className="fc-p10-stage-val">
                {clipCardText(
                  [
                    p10Record.releaseDate || (p10Record.releaseVerified ? 'Release verified' : null),
                    p10Record.compensationInr != null && p10Record.compensationInr > 0
                      ? formatInr(p10Record.compensationInr)
                      : null,
                  ]
                    .filter(Boolean)
                    .join(' · '),
                  compact ? 42 : 68,
                ) || 'Compliance recorded'}
              </span>
            </li>
          </ol>
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
