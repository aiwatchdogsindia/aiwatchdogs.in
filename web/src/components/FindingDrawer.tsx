import {
  AlertTriangle,
  Check,
  CheckCircle2,
  Copy,
  Download,
  ExternalLink,
  FileText,
  History,
  Lock,
  MinusCircle,
  Scale,
  ShieldCheck,
  X,
  XCircle,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useLang } from '../lib/lang';
import { PlainBlock } from './PlainBlock';
import { AgentBadge, ProviderModelBadge } from './AgentLogos';
import { api } from '../api/client';
import { useDialogFocus } from '../hooks/useDialogFocus';
import { getFindingTag, getVerdictTag, getWorkTypeTag } from '../lib/tags';
import { Link } from '../router';
import type { Citation, FindingItem, RtiDraft, VerificationCheckState } from '../types';
import {
  METHODOLOGY_DESCRIPTIONS,
  copyToClipboard,
  downloadTextFile,
  extractAssuranceMetrics,
  extractAuditingGovernmentExamsMetrics,
  extractInnocentButLockedUpMetrics,
  extractOnlyOneBidMetrics,
  extractSovereignLiesMetrics,
  formatAcquittalOrderType,
  formatAssuranceStatus,
  formatBytes,
  formatCandidateCountCompact,
  formatChainStatus,
  formatCitationRoleLabel,
  formatCourtTier,
  formatCustodyOverrunMechanism,
  formatDateShort,
  formatElapsedPromiseSpan,
  formatExamAuthorityType,
  formatExamCategory,
  formatExamDelaySpan,
  formatExamDisruption,
  formatExamDisruptionReason,
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
  isOfficialGovUrl,
  resolveCitationHref,
  resolveFindingDrawerMetric,
  safeExternalUrl,
} from '../utils';
import { useToast } from './Toast';

interface FindingDrawerProps {
  findingId: string | number | null;
  onClose: () => void;
  onRtiCreated?: () => void;
}

function renderCheckBadge(label: string, state: VerificationCheckState) {
  const isPass = state === 'pass';
  const isFail = state === 'fail';
  const color = isPass
    ? 'var(--ep-verified-fg)'
    : isFail
      ? 'var(--ep-disputed-fg)'
      : 'var(--ink-3)';
  const text = isPass ? 'Pass' : isFail ? 'Fail' : 'Unrecorded';
  return (
    <span
      key={label}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        color,
      }}
      title={`${label}: ${text}`}
    >
      {isPass ? (
        <CheckCircle2 size={12} aria-hidden="true" />
      ) : isFail ? (
        <XCircle size={12} aria-hidden="true" />
      ) : (
        <MinusCircle size={12} aria-hidden="true" />
      )}
      <span>
        {label}: <strong>{text}</strong>
      </span>
    </span>
  );
}

function formatReviewKindLabel(kind?: string): { label: string; isEditorial: boolean } {
  const k = (kind || 'computational_reproduction').toLowerCase();
  if (k.includes('editorial')) return { label: 'Editorial check', isEditorial: true };
  if (k.includes('adjudic')) return { label: 'Tie-breaker', isEditorial: true };
  return { label: 'Peer check', isEditorial: false };
}

/**
 * The document a citation's line was read from. Statutory benchmarks prefer their official
 * landing/source link over a raw dataset file in `source_ref`; two-document methods cite their
 * own `source_url` when `sha256` differs from the finding's artifact.
 */
function citationHref(cit: Citation, artifactSha?: string): string | null {
  return resolveCitationHref(cit, artifactSha);
}

export function FindingDrawer({ findingId, onClose, onRtiCreated }: FindingDrawerProps) {
  const [finding, setFinding] = useState<FindingItem | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rtiDraft, setRtiDraft] = useState<RtiDraft | null>(null);
  const [rtiLoading, setRtiLoading] = useState(false);
  const [rtiAuthority, setRtiAuthority] = useState('');
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const toast = useToast();
  const { lang } = useLang();

  useDialogFocus(findingId != null, panelRef, onClose);

  useEffect(() => {
    if (findingId == null) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setFinding(null);
    setRtiDraft(null);
    api
      .getFindingDetail(findingId)
      .then((data) => {
        if (cancelled) return;
        setFinding(data);
        setRtiDraft(data.rti_draft ?? null);
        setRtiAuthority((data.rti_draft?.public_authority || data.entity_name || '').slice(0, 160));
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load dossier');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [findingId, lang]);

  if (findingId == null) return null;

  const handleCopy = async (key: string, text: string, label: string) => {
    const ok = await copyToClipboard(text);
    if (ok) {
      setCopiedKey(key);
      toast(label);
      window.setTimeout(() => setCopiedKey(null), 2000);
    }
  };

  const handleGenerateRti = async () => {
    if (!finding) return;
    const trimmedAuthority = rtiAuthority.trim();
    if (trimmedAuthority.length > 160) {
      toast('Target Public Authority must be 160 characters or fewer.');
      return;
    }
    setRtiLoading(true);
    try {
      const draft = await api.generateRtiDraft(finding.id, trimmedAuthority || undefined);
      setRtiDraft(draft);
      toast(
        draft.is_preview
          ? `Generated personal RTI preview (${draft.word_count} words — not persisted)`
          : `Generated RTI Act 2005 Sec 6(1) draft (${draft.word_count} words)`,
      );
      if (!draft.is_preview) onRtiCreated?.();
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Could not generate RTI draft');
    } finally {
      setRtiLoading(false);
    }
  };

  const handleDownloadRti = () => {
    if (!rtiDraft || !finding) return;
    const isState = rtiDraft.jurisdiction_level === 'state' || (!rtiDraft.rti_portal_url && finding.jurisdiction.toLowerCase() !== 'union');
    const portalLine = rtiDraft.rti_portal_url
      ? `Filing Portal: ${rtiDraft.rti_portal_url}`
      : isState
        ? `Filing Channel: ${rtiDraft.filing_channel || `${finding.jurisdiction} State RTI Portal or direct post to State PIO`}`
        : 'Filing Portal: https://rtionline.gov.in/';
    const feeLine =
      rtiDraft.fee_note ||
      (isState
        ? 'Check applicable State RTI Rules for prescribed application fee & postal/online payment mode (BPL exempt under Sec 7(5)).'
        : 'Review carefully and file personally via https://rtionline.gov.in/ (₹10 fee; BPL exempt under Sec 7(5)).');

    const content = [
      `APPLICATION UNDER SECTION 6(1) OF THE RIGHT TO INFORMATION ACT, 2005`,
      `Public Authority: ${rtiDraft.public_authority}`,
      `PIO / CPIO: ${rtiDraft.cpio_designation}`,
      portalLine,
      `Statutory Sections: ${rtiDraft.statutory_sections.join(', ')}`,
      `Subject: ${rtiDraft.subject}`,
      `Word Count: ${rtiDraft.word_count} / 500`,
      `Finding Reference: ${finding.id} (${finding.methodology_code})`,
      rtiDraft.editorial_warning ? `Editorial Notice: ${rtiDraft.editorial_warning}` : '',
      `--------------------------------------------------------------------`,
      '',
      rtiDraft.body_text,
      '',
      `--------------------------------------------------------------------`,
      `Note: Generated by AI Watchdogs India (aiwatchdogs.in) as a citizen draft.`,
      feeLine,
    ]
      .filter((line, idx, arr) => line !== '' || (idx > 0 && arr[idx - 1] !== ''))
      .join('\n');
    downloadTextFile(`rti-draft-${finding.id}.txt`, content);
    toast('Downloaded RTI draft (.txt)');
  };

  const sm = finding ? getFindingTag(finding.status) : null;
  const workTag = finding ? getWorkTypeTag(undefined, finding.methodology_code) : null;
  const confirmCount =
    finding?.reproduction_summary?.computational_confirm_count ??
    finding?.confirm_count ??
    finding?.verifications?.filter((v) => v.verdict === 'confirm').length ??
    0;
  const editorialReviewCount =
    finding?.reproduction_summary?.editorial_review_count ??
    finding?.verifications?.filter((v) => formatReviewKindLabel(v.review_kind).isEditorial).length ??
    0;
  const requiredQuorum =
    finding?.reproduction_summary?.required_quorum ?? finding?.required_quorum ?? 3;
  const methodMeta = finding ? METHODOLOGY_DESCRIPTIONS[finding.methodology_code] : null;
  const isQuarantined = Boolean(finding?.quarantined || finding?.status === 'quarantined');
  const hasEditorialNotice = Boolean(
    finding &&
      (finding.has_corrections ||
        (finding.editorial_status && finding.editorial_status !== 'none') ||
        (finding.corrections && finding.corrections.length > 0) ||
        finding.status === 'under_review' ||
        finding.status === 'under_editorial_review' ||
        finding.status === 'corrected' ||
        finding.status === 'retracted' ||
        finding.status === 'retracted_false_positive'),
  );

  const prov = finding?.provenance;
  const retrievalUrl = prov?.retrieval_url || finding?.source_url || '';
  const safeLandingUrl = safeExternalUrl(prov?.source_landing_page);
  const safeRetrievalUrl = safeExternalUrl(retrievalUrl);
  const safeArchiveUrl = safeExternalUrl(prov?.archive_url);
  const retrievalIsOfficial = isOfficialGovUrl(safeRetrievalUrl);
  const landingIsOfficial = isOfficialGovUrl(safeLandingUrl);
  const rawMirrorUrl = prov?.mirror_url || (!retrievalIsOfficial && retrievalUrl ? retrievalUrl : null);
  const safeMirrorUrl = safeExternalUrl(rawMirrorUrl);
  const safeRtiPortalUrl = safeExternalUrl(rtiDraft?.rti_portal_url);
  const fiscalDisplay =
    finding?.fiscal_year_start &&
    finding?.fiscal_year_end &&
    finding.fiscal_year_start !== finding.fiscal_year_end
      ? `${finding.fiscal_year_start} – ${finding.fiscal_year_end}`
      : finding?.fiscal_year || 'Unrecorded';

  const p6Record =
    finding?.methodology_code === 'P6' && !isQuarantined
      ? extractAssuranceMetrics(finding.anomaly_metrics)
      : null;
  const p6Status = p6Record?.recordedStatus
    ? formatAssuranceStatus(p6Record.recordedStatus)
    : null;
  const p7Record =
    finding?.methodology_code === 'P7' && !isQuarantined
      ? extractOnlyOneBidMetrics(finding.anomaly_metrics)
      : null;
  const p7Signal = p7Record?.signalCategory
    ? formatProcurementSignal(p7Record.signalCategory)
    : null;
  const p7BidsText = p7Record ? formatOnlyOneBidBidsSummary(p7Record) : null;
  const p7MechText = p7Record ? formatSingleBidMechanism(p7Record.singleBidMechanism) : null;
  const p7TargetVal =
    finding && finding.monetary_exposure_inr != null && finding.monetary_exposure_inr > 0
      ? finding.monetary_exposure_inr
      : p7Record?.targetTenderValueInr ?? null;

  const p8Record =
    finding?.methodology_code === 'P8' && !isQuarantined
      ? extractSovereignLiesMetrics(finding.anomaly_metrics)
      : null;
  const p8Mechanism =
    p8Record && (p8Record.walkbackMechanism || p8Record.signalCategory)
      ? formatWalkbackMechanism(p8Record.walkbackMechanism || p8Record.signalCategory)
      : null;
  const p8Impact = p8Record?.retroactiveImpact
    ? formatRetroactiveImpact(p8Record.retroactiveImpact)
    : null;
  const p8Category = p8Record?.schemeCategory
    ? formatSchemeCategory(p8Record.schemeCategory)
    : null;
  const p8Jurisdiction = p8Record
    ? formatJurisdictionLabel(
        p8Record.jurisdiction || finding?.jurisdiction,
        p8Record.stateOrUt,
        p8Record.jurisdictionLevel,
      )
    : null;
  const p8ElapsedLabel = p8Record
    ? formatElapsedPromiseSpan(p8Record.daysFromPromiseToWalkback)
    : null;
  const p8PublishedExposure =
    finding && finding.monetary_exposure_inr != null && finding.monetary_exposure_inr > 0
      ? finding.monetary_exposure_inr
      : p8Record?.estimatedExposureInr != null && p8Record.estimatedExposureInr > 0
        ? p8Record.estimatedExposureInr
        : null;
  const p8PromiseCit = finding?.citations.find((c) => {
    const r = (c.role || '').toLowerCase();
    return r === 'original_promise' || r === 'promise_document';
  });
  const p8WalkbackCit = finding?.citations.find((c) => {
    const r = (c.role || '').toLowerCase();
    return r === 'subsequent_walkback' || r === 'walkback_document';
  });
  const p8PromiseHref = p8PromiseCit
    ? citationHref(p8PromiseCit, finding?.artifact_sha256)
    : safeRetrievalUrl;
  const p8WalkbackHref = p8WalkbackCit
    ? citationHref(p8WalkbackCit, finding?.artifact_sha256)
    : safeExternalUrl(prov?.secondary_url) || safeRetrievalUrl;
  const p8PromiseSha = p8PromiseCit?.sha256 || prov?.observed_sha256 || null;
  const p8WalkbackSha = p8WalkbackCit?.sha256 || prov?.secondary_sha256 || prov?.observed_sha256 || null;

  const p9Record =
    finding?.methodology_code === 'P9' && !isQuarantined
      ? extractAuditingGovernmentExamsMetrics(finding.anomaly_metrics)
      : null;
  const p9Disruption =
    p9Record && (p9Record.disruptionType || p9Record.signalCategory)
      ? formatExamDisruption(p9Record.disruptionType || p9Record.signalCategory)
      : null;
  const p9Reason = p9Record?.disruptionReasonCategory
    ? formatExamDisruptionReason(p9Record.disruptionReasonCategory)
    : null;
  const p9AuthorityType = p9Record?.authorityType
    ? formatExamAuthorityType(p9Record.authorityType)
    : null;
  const p9Category = p9Record?.examCategory ? formatExamCategory(p9Record.examCategory) : null;
  const p9Jurisdiction = p9Record
    ? formatJurisdictionLabel(
        p9Record.jurisdiction || finding?.jurisdiction,
        p9Record.stateOrUt,
        p9Record.jurisdictionLevel,
      )
    : null;
  const p9DelayLabel = p9Record ? formatExamDelaySpan(p9Record.delayDays) : null;
  const p9CandidatesCompact = p9Record
    ? formatCandidateCountCompact(p9Record.affectedCandidatesCount)
    : null;
  const p9ScheduleCit = finding?.citations.find((c) => {
    const r = (c.role || '').toLowerCase();
    return r === 'original_schedule' || r === 'schedule_document';
  });
  const p9OutcomeCit = finding?.citations.find((c) => {
    const r = (c.role || '').toLowerCase();
    return r === 'conduct_outcome' || r === 'outcome_document';
  });
  const p9ScheduleHref = p9ScheduleCit
    ? citationHref(p9ScheduleCit, finding?.artifact_sha256)
    : safeRetrievalUrl;
  const p9OutcomeHref = p9OutcomeCit
    ? citationHref(p9OutcomeCit, finding?.artifact_sha256)
    : safeExternalUrl(prov?.secondary_url) || safeRetrievalUrl;
  const p9ScheduleSha = p9ScheduleCit?.sha256 || prov?.observed_sha256 || null;
  const p9OutcomeSha = p9OutcomeCit?.sha256 || prov?.secondary_sha256 || prov?.observed_sha256 || null;


  const p10Record =
    finding?.methodology_code === 'P10' && !isQuarantined
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
  const p10OverrunLabel = p10Record ? formatOverrunDays(p10Record.overrunDays) : null;
  const p10Compensation =
    p10Record?.compensationInr != null && p10Record.compensationInr > 0
      ? p10Record.compensationInr
      : finding && finding.monetary_exposure_inr != null && finding.monetary_exposure_inr > 0
        ? finding.monetary_exposure_inr
        : null;
  const p10OrderCit = finding?.citations.find((c) => {
    const r = (c.role || '').toLowerCase();
    return r === 'acquittal_and_custody_order' || r === 'acquittal_order_document';
  });
  const p10ContemptCit = finding?.citations.find((c) => {
    const r = (c.role || '').toLowerCase();
    return (
      r === 'contempt_and_release_outcome' ||
      r === 'contempt_order_document' ||
      r === 'release_or_compliance_record'
    );
  });
  const p10OrderHref = p10OrderCit
    ? citationHref(p10OrderCit, finding?.artifact_sha256)
    : safeRetrievalUrl;
  const p10ContemptHref = p10ContemptCit
    ? citationHref(p10ContemptCit, finding?.artifact_sha256)
    : safeExternalUrl(prov?.secondary_url) || safeRetrievalUrl;
  const p10OrderSha = p10OrderCit?.sha256 || prov?.observed_sha256 || null;
  const p10ContemptSha = p10ContemptCit?.sha256 || prov?.secondary_sha256 || prov?.observed_sha256 || null;

  const drawerMetric = finding ? resolveFindingDrawerMetric(finding) : null;
  const displayMetricEntries = finding
    ? Object.entries(finding.anomaly_metrics).filter(
        ([k]) =>
          !(
            (p6Record && (k === 'original_promise' || k === 'followup_answer')) ||
            (p8Record &&
              (k === 'promise_summary' ||
                k === 'walkback_summary' ||
                k === 'promise_excerpt' ||
                k === 'walkback_excerpt')) ||
            (p9Record &&
              (k === 'schedule_summary' ||
                k === 'outcome_summary' ||
                k === 'schedule_excerpt' ||
                k === 'outcome_excerpt')) ||
            (p10Record &&
              (k === 'acquittal_quote' ||
                k === 'contempt_quote' ||
                k === 'custody_order_excerpt' ||
                k === 'contempt_or_outcome_excerpt'))
          ),
      )
    : [];

  const isStateRti =
    rtiDraft?.jurisdiction_level === 'state' ||
    (Boolean(finding) &&
      !isOfficialGovUrl(safeRtiPortalUrl) &&
      finding!.jurisdiction.toLowerCase() !== 'union' &&
      finding!.jurisdiction.toLowerCase() !== 'union of india' &&
      finding!.jurisdiction.toLowerCase() !== 'all-india' &&
      finding!.jurisdiction.toLowerCase() !== 'central');

  return (
    <div
      className="overlay-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={panelRef}
        className="drawer-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="finding-drawer-title"
        tabIndex={-1}
      >
        <div className="overlay-header">
          <div style={{ minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, flexWrap: 'wrap', marginBottom: 6 }}>
              <span className="font-mono" style={{ fontSize: 12, color: 'var(--ink-3)' }}>
                EVIDENCE DOSSIER #{String(findingId)}
              </span>
              {sm && (
                <span className={sm.badgeClass} title={sm.description}>
                  {sm.label}
                </span>
              )}
              {finding && workTag && (
                <span
                  className={workTag.badgeClass}
                  title={`${finding.methodology_code}: ${workTag.description}`}
                >
                  {workTag.label}
                </span>
              )}
              {finding?.editorial_status &&
                finding.editorial_status !== 'none' &&
                getFindingTag(finding.editorial_status).id !== sm?.id && (
                  <span
                    className={getFindingTag(finding.editorial_status).badgeClass}
                    title={getFindingTag(finding.editorial_status).description}
                  >
                    {getFindingTag(finding.editorial_status).label}
                  </span>
                )}
            </div>
            <h2
              id="finding-drawer-title"
              className="font-serif drawer-title"
            >
              {loading ? 'Loading evidence dossier…' : finding?.plain?.head || finding?.title || 'Finding Dossier'}
            </h2>
            {finding?.plain && <p className="fc-tech-title">{finding.title}</p>}
          </div>

          <button
            type="button"
            className="btn btn-ghost btn-sm overlay-close-btn"
            onClick={onClose}
            aria-label="Close dossier"
          >
            <X size={16} />
          </button>
        </div>

        <div className="overlay-body">
          {loading && (
            <div className="state-banner">Loading deterministic metrics, provenance hashes, and peer quorum…</div>
          )}

          {error && (
            <div className="state-banner" style={{ borderColor: 'var(--ep-disputed-bd)', color: 'var(--ep-disputed-fg)' }}>
              Could not load finding dossier: {error}
            </div>
          )}

          {finding && (
            <>
              {finding.plain && !isQuarantined && <PlainBlock plain={finding.plain} sourceUrl={finding.source_url} />}

              {/* Quarantine Notice Shell (Privacy / Legal Compliance Hold) */}
              {isQuarantined && (
                <div className="dossier-quarantine" role="alert">
                  <div className="dossier-notice-head">
                    <Lock size={15} aria-hidden="true" />
                    <span>Record Quarantined Pending Privacy / Legal Review</span>
                  </div>
                  <p style={{ margin: '6px 0 0', fontSize: 13.5, lineHeight: 1.55, color: 'var(--ink-2)' }}>
                    {finding.quarantine_reason ||
                      'This public dossier has been placed in quarantine following a privacy or legal-compliance review flag. Raw metrics, agent traces, pinpoint excerpts, and RTI drafts are withheld from public view while human reviewers inspect the record.'}
                  </p>
                </div>
              )}

              {/* Prominent Editorial Correction & Review History Timeline */}
              {hasEditorialNotice && (
                <section className="dossier-corrections" aria-label="Correction and editorial review history">
                  <div className="dossier-notice-head">
                    <History size={15} aria-hidden="true" />
                    <span>Correction &amp; Editorial Review History</span>
                    {finding.editorial_status && finding.editorial_status !== 'none' && (
                      <span
                        className={getFindingTag(finding.editorial_status).badgeClass}
                        style={{ marginLeft: 'auto' }}
                      >
                        {getFindingTag(finding.editorial_status).label}
                      </span>
                    )}
                  </div>
                  <p style={{ margin: '6px 0 10px', fontSize: 13, lineHeight: 1.55, color: 'var(--ink-2)' }}>
                    This permanent URL (<code className="font-mono">/findings?finding={String(finding.id)}</code>)
                    is preserved for public accountability. Corrections and status revisions are
                    logged transparently below rather than silently deleted.
                  </p>

                  {finding.corrections && finding.corrections.length > 0 ? (
                    <ol className="correction-timeline">
                      {finding.corrections.map((c, idx) => (
                        <li key={c.id ?? idx} className="correction-entry">
                          <div className="correction-entry-meta">
                            <span>
                              <strong>{c.correction_type ? c.correction_type.replace(/_/g, ' ') : 'Editorial review'}</strong>
                              {c.previous_status && c.new_status
                                ? `, ${getFindingTag(c.previous_status).label} to ${getFindingTag(c.new_status).label}`
                                : ''}
                            </span>
                            <span className="font-mono" style={{ fontSize: 12, color: 'var(--ink-3)' }}>
                              {c.reviewer ? `Reviewed by ${c.reviewer}` : 'Editorial desk'}
                              {c.created_at ? `, ${formatDateShort(c.created_at)}` : ''}
                            </span>
                          </div>
                          <p style={{ margin: '4px 0 0', fontSize: 13.5, lineHeight: 1.55, color: 'var(--ink)' }}>
                            {c.summary}
                          </p>
                          {c.source_discrepancy && (
                            <p style={{ margin: '4px 0 0', fontSize: 13, lineHeight: 1.5, color: 'var(--ink-2)' }}>
                              {c.source_discrepancy}
                            </p>
                          )}
                          {(() => {
                            const safeEvidenceUrl = safeExternalUrl(c.source_evidence_url);
                            return safeEvidenceUrl ? (
                              <a
                                href={safeEvidenceUrl}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="inline-link"
                                style={{ fontSize: 12.5, display: 'inline-flex', alignItems: 'center', gap: 4, marginTop: 4 }}
                              >
                                <span>Inspect referenced source evidence</span>
                                <ExternalLink size={11} aria-hidden="true" />
                              </a>
                            ) : null;
                          })()}
                          {c.rti_notice && (
                            <div style={{ marginTop: 5, fontSize: 12.5, color: 'var(--red)' }}>
                              <strong>RTI Draft Impact:</strong> {c.rti_notice}
                            </div>
                          )}
                        </li>
                      ))}
                    </ol>
                  ) : (
                    <div style={{ fontSize: 13, color: 'var(--ink-2)' }}>
                      Current disposition: <strong>{sm?.label}</strong>. Source reconciliation is
                      under active review.
                    </div>
                  )}
                </section>
              )}

              {!isQuarantined && (
                <>
                  {/* Key Metadata Strip */}
                  <div className="card card-pad-sm">
                    <div className="grid-4 drawer-meta-grid" style={{ gap: 12 }}>
                      <div>
                        <div className="kicker" style={{ fontSize: 10.5 }}>Public Entity</div>
                        <div style={{ fontSize: 14, marginTop: 3 }}>{finding.entity_name}</div>
                      </div>
                      <div>
                        <div className="kicker" style={{ fontSize: 10.5 }}>Jurisdiction / Period</div>
                        <div style={{ fontSize: 14, marginTop: 3 }}>
                          {finding.jurisdiction}, <span className="font-mono">{fiscalDisplay}</span>
                        </div>
                      </div>
                      <div>
                        <div className="kicker" style={{ fontSize: 10.5 }}>
                          {drawerMetric?.label ?? 'Reported Figure'}
                        </div>
                        <div className="font-mono tabular" style={{ fontSize: 15.5, marginTop: 3 }}>
                          {drawerMetric?.value ?? 'Disclosure Lead'}
                        </div>
                      </div>
                      <div>
                        <div className="kicker" style={{ fontSize: 10.5 }}>Severity / Quorum</div>
                        <div className="font-mono tabular" style={{ fontSize: 14, marginTop: 3 }}>
                          <span className={`sev-${finding.severity}`}>{finding.severity.toUpperCase()}</span>
                          {', '}
                          <strong>{confirmCount}/{requiredQuorum}</strong>
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* Neutral Editorial Summary */}
                  <div className="card card-pad">
                    <div className="section-kicker">Factual Public-Record Summary</div>
                    <p style={{ margin: '6px 0 10px', fontSize: 14.5, lineHeight: 1.6, color: 'var(--ink-2)' }}>
                      {finding.summary}
                    </p>
                    {finding.editorial_interpretation && (
                      <div
                        style={{
                          margin: '8px 0 10px',
                          padding: '9px 12px',
                          background: 'var(--warm)',
                          borderLeft: '2px solid var(--rule-strong)',
                          fontSize: 13.5,
                          color: 'var(--ink-2)',
                        }}
                      >
                        <div className="kicker" style={{ fontSize: 10, marginBottom: 3 }}>
                          Editorial Interpretation (Separate from Computed Fields)
                        </div>
                        {finding.editorial_interpretation}
                      </div>
                    )}
                    <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', fontSize: 12.5, color: 'var(--ink-3)' }}>
                      {methodMeta && (
                        <span>
                          <strong>Rule:</strong> {methodMeta.name} ({methodMeta.rule})
                        </span>
                      )}
                      {finding.rule_triggered && (
                        <span
                          className="tag-mono"
                          style={{ whiteSpace: 'normal', overflowWrap: 'anywhere', lineHeight: 1.35 }}
                        >
                          Triggered: {finding.rule_triggered}
                        </span>
                      )}
                    </div>
                  </div>

                  {/* P6 Structured 3-Part Assurance Record (Original Promise -> Recorded Status -> Follow-Up Answer) */}
                  {p6Record && (
                    <section className="card card-pad drawer-p6-card" aria-label="Parliamentary assurance record">
                      <div className="drawer-p6-head">
                        <div>
                          <div className="section-kicker" style={{ marginBottom: 4 }}>
                            Parliamentary Assurance Record · Promise → Status → Follow-Up
                          </div>
                          <div className="drawer-p6-meta">
                            {p6Record.house && <span className="tag-mono">{p6Record.house}</span>}
                            {p6Record.session && <span className="tag-mono">Session {p6Record.session}</span>}
                            {p6Record.assuranceRef && (
                              <span className="tag-mono font-mono">{p6Record.assuranceRef}</span>
                            )}
                            {p6Record.promiseDate && (
                              <span className="tag-mono tabular">Promised {p6Record.promiseDate}</span>
                            )}
                            {p6Record.daysElapsed != null && (
                              <span className="tag-mono tabular">
                                {p6Record.daysElapsed.toLocaleString('en-IN')} days elapsed
                              </span>
                            )}
                          </div>
                        </div>
                        {p6Status && <span className={p6Status.chipClass}>{p6Status.label}</span>}
                      </div>

                      {(p6Record.member || p6Record.minister) && (
                        <div className="drawer-p6-people">
                          {p6Record.member && (
                            <span>
                              <strong>MP:</strong> {p6Record.member}
                            </span>
                          )}
                          {p6Record.minister && (
                            <span>
                              <strong>Minister:</strong> {p6Record.minister}
                            </span>
                          )}
                        </div>
                      )}

                      <div className="drawer-p6-steps">
                        <div className="drawer-p6-step">
                          <div className="drawer-p6-step-kicker">01 · Original Promise</div>
                          <p className="drawer-p6-step-body drawer-p6-quote">
                            {p6Record.originalPromise
                              ? `“${p6Record.originalPromise}”`
                              : 'Verbatim promise text unrecorded in primary API payload.'}
                          </p>
                        </div>

                        <div className="drawer-p6-step">
                          <div className="drawer-p6-step-kicker">02 · Recorded Status</div>
                          <div className="drawer-p6-status-row">
                            {p6Status && <span className={p6Status.chipClass}>{p6Status.label}</span>}
                            {p6Record.daysElapsed != null && (
                              <span className="tabular drawer-p6-status-detail">
                                {p6Record.daysElapsed.toLocaleString('en-IN')} days since assurance
                              </span>
                            )}
                            {p6Record.fulfillmentDays != null && (
                              <span className="tabular drawer-p6-status-detail">
                                Fulfilled in {p6Record.fulfillmentDays.toLocaleString('en-IN')} days
                              </span>
                            )}
                          </div>
                          {(p6Record.extensionsCount != null && p6Record.extensionsCount > 0) ||
                          p6Record.extensionUpto ||
                          p6Record.remarks ? (
                            <p className="drawer-p6-step-sub">
                              {[
                                p6Record.extensionsCount != null && p6Record.extensionsCount > 0
                                  ? `${p6Record.extensionsCount} extension${p6Record.extensionsCount === 1 ? '' : 's'} sought`
                                  : null,
                                p6Record.extensionUpto ? `Extended up to ${p6Record.extensionUpto}` : null,
                                p6Record.remarks ? `Remarks: ${p6Record.remarks}` : null,
                              ]
                                .filter(Boolean)
                                .join(' · ')}
                            </p>
                          ) : null}
                        </div>

                        <div className="drawer-p6-step">
                          <div className="drawer-p6-step-kicker">03 · What the Follow-Up Actually Answered</div>
                          <p className="drawer-p6-step-body">
                            {p6Record.followupAnswer ||
                              'No Implementation Statement or follow-up answer is recorded on Digital Sansad.'}
                          </p>
                          {(p6Record.impRecordNo || p6Record.followupDate) && (
                            <p className="drawer-p6-step-sub font-mono tabular">
                              {[
                                p6Record.impRecordNo ? `Implementation Ref: ${p6Record.impRecordNo}` : null,
                                p6Record.followupDate ? `Date: ${p6Record.followupDate}` : null,
                              ]
                                .filter(Boolean)
                                .join(' · ')}
                            </p>
                          )}
                        </div>
                      </div>
                    </section>
                  )}

                  {/* P7 Structured Procurement Record (Bid Competition -> Repeat-Winner Pattern -> Separated Values & Statutory Benchmark) */}
                  {p7Record && (
                    <section className="card card-pad drawer-p7-card" aria-label="Procurement tender dossier">
                      <div className="drawer-p6-head">
                        <div>
                          <div className="section-kicker" style={{ marginBottom: 4 }}>
                            Procurement Record · Competition &amp; Award Dossier
                          </div>
                          <div className="drawer-p6-meta">
                            {p7Record.portal && <span className="tag-mono">{p7Record.portal}</span>}
                            {p7Record.tenderId && (
                              <span className="tag-mono font-mono">Tender {p7Record.tenderId}</span>
                            )}
                            {p7Record.ocid && p7Record.ocid !== p7Record.tenderId && (
                              <span className="tag-mono font-mono">OCID {p7Record.ocid}</span>
                            )}
                            {p7Record.fiscalYear && (
                              <span className="tag-mono tabular">{p7Record.fiscalYear}</span>
                            )}
                            {p7Record.procurementMethod && (
                              <span className="tag-mono">{p7Record.procurementMethod}</span>
                            )}
                            {p7Record.exemptionStatus && (
                              <span className="tag-mono">
                                Exemption: {p7Record.exemptionStatus.replace(/_/g, ' ')}
                              </span>
                            )}
                            {p7Record.isAnchorCase && <span className="tag-mono">Featured case</span>}
                          </div>
                        </div>
                        {p7Signal && <span className={p7Signal.chipClass}>{p7Signal.label}</span>}
                      </div>

                      {p7Record.tenderTitle && p7Record.tenderTitle !== finding.title && (
                        <h3 className="drawer-p7-title">{p7Record.tenderTitle}</h3>
                      )}

                      {(p7Record.authority || p7Record.itemCategory) && (
                        <div className="drawer-p6-people">
                          {p7Record.authority && (
                            <span>
                              <strong>Procuring authority:</strong> {p7Record.authority}
                            </span>
                          )}
                          {p7Record.itemCategory && (
                            <span>
                              <strong>Category:</strong> {p7Record.itemCategory}
                            </span>
                          )}
                        </div>
                      )}

                      <div className="drawer-p7-grid">
                        <div className="drawer-p6-step">
                          <div className="drawer-p6-step-kicker">01 · Bid Competition &amp; Process</div>
                          <dl className="drawer-p7-stat-list tabular">
                            <div className="drawer-p7-stat-row">
                              <dt>Bids received</dt>
                              <dd>
                                {p7Record.bidsReceived != null
                                  ? p7Record.bidsReceived.toLocaleString('en-IN')
                                  : 'Unrecorded'}
                              </dd>
                            </div>
                            <div className="drawer-p7-stat-row">
                              <dt>Technically qualified</dt>
                              <dd>
                                {p7Record.technicallyQualifiedBids != null
                                  ? p7Record.technicallyQualifiedBids.toLocaleString('en-IN')
                                  : 'Unrecorded'}
                              </dd>
                            </div>
                            {p7Record.effectiveBids != null && (
                              <div className="drawer-p7-stat-row">
                                <dt>Effective competing bids</dt>
                                <dd>
                                  <strong>{p7Record.effectiveBids.toLocaleString('en-IN')}</strong>
                                </dd>
                              </div>
                            )}
                            {p7MechText && (
                              <div className="drawer-p7-stat-row">
                                <dt>Single-bid mechanism</dt>
                                <dd>{p7MechText}</dd>
                              </div>
                            )}
                            {p7Record.bidWindowDays != null && (
                              <div className="drawer-p7-stat-row">
                                <dt>Bid window</dt>
                                <dd>{p7Record.bidWindowDays.toLocaleString('en-IN')} days</dd>
                              </div>
                            )}
                            {(p7Record.publishDate || p7Record.closeDate || p7Record.awardDate) && (
                              <div className="drawer-p7-stat-row">
                                <dt>Timeline</dt>
                                <dd>
                                  {[
                                    p7Record.publishDate ? `Pub ${p7Record.publishDate}` : null,
                                    p7Record.closeDate ? `Close ${p7Record.closeDate}` : null,
                                    p7Record.awardDate ? `Award ${p7Record.awardDate}` : null,
                                  ]
                                    .filter(Boolean)
                                    .join(' · ')}
                                </dd>
                              </div>
                            )}
                            {p7Record.authoritySingleBidRatePct != null && (
                              <div className="drawer-p7-stat-row">
                                <dt>Authority single-bid rate</dt>
                                <dd>
                                  {p7Record.authoritySingleBidRatePct.toFixed(1)}%
                                  {p7Record.authorityCohortTenders != null
                                    ? ` (${p7Record.authorityCohortTenders.toLocaleString('en-IN')} tenders)`
                                    : ''}
                                </dd>
                              </div>
                            )}
                          </dl>
                        </div>

                        <div className="drawer-p6-step">
                          <div className="drawer-p6-step-kicker">02 · Repeat-Winner Concentration</div>
                          <dl className="drawer-p7-stat-list tabular">
                            <div className="drawer-p7-stat-row">
                              <dt>Recorded awardee</dt>
                              <dd>
                                <strong>{p7Record.winnerName || 'Unrecorded'}</strong>
                              </dd>
                            </div>
                            <div className="drawer-p7-stat-row">
                              <dt>Total wins at authority</dt>
                              <dd>
                                {p7Record.winnerTotalWins != null
                                  ? p7Record.winnerTotalWins.toLocaleString('en-IN')
                                  : 'Unrecorded'}
                              </dd>
                            </div>
                            <div className="drawer-p7-stat-row">
                              <dt>Single-bid wins</dt>
                              <dd>
                                {p7Record.winnerSingleBidWins != null
                                  ? p7Record.winnerSingleBidWins.toLocaleString('en-IN')
                                  : 'Unrecorded'}
                              </dd>
                            </div>
                            {p7Record.winnerShareByCountPct != null && (
                              <div className="drawer-p7-stat-row">
                                <dt>Authority share (by count)</dt>
                                <dd>{p7Record.winnerShareByCountPct.toFixed(1)}%</dd>
                              </div>
                            )}
                            {p7Record.winnerShareByValuePct != null && (
                              <div className="drawer-p7-stat-row">
                                <dt>Authority share (by value)</dt>
                                <dd>{p7Record.winnerShareByValuePct.toFixed(1)}%</dd>
                              </div>
                            )}
                            {p7Record.consecutiveWinStreak != null && p7Record.consecutiveWinStreak >= 2 && (
                              <div className="drawer-p7-stat-row">
                                <dt>Consecutive win streak</dt>
                                <dd>{p7Record.consecutiveWinStreak.toLocaleString('en-IN')} awards</dd>
                              </div>
                            )}
                          </dl>
                          {p7Record.relatedTenderIds.length > 0 && (
                            <div className="drawer-p7-chips" aria-label="Corroborating tender IDs">
                              {p7Record.relatedTenderIds.slice(0, 10).map((rid) => (
                                <span key={rid} className="tag-mono font-mono">
                                  {rid}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>

                        <div className="drawer-p6-step drawer-p7-grid-full">
                          <div className="drawer-p6-step-kicker">
                            03 · Financial Figures (Separated) &amp; Statutory Benchmark
                          </div>
                          <div className="drawer-p7-values-split tabular">
                            <div className="drawer-p7-value-box drawer-p7-value-box--target">
                              <div className="drawer-p7-value-kicker">
                                Target tender · {formatProcurementAmountType(p7Record.amountType ?? finding.amount_label)}
                              </div>
                              <div className="drawer-p7-value-num">
                                {p7TargetVal != null ? formatInr(p7TargetVal) : 'Unrecorded'}
                              </div>
                              <p className="drawer-p7-value-note">
                                Counted once in finding exposure
                                {p7Record.awardValueInr != null &&
                                p7Record.tenderEstimateInr != null &&
                                p7Record.awardValueInr > 0 &&
                                p7Record.tenderEstimateInr > 0
                                  ? ` (Award: ${formatInr(p7Record.awardValueInr)} · Estimate: ${formatInr(p7Record.tenderEstimateInr)})`
                                  : ''}
                                {p7BidsText ? ` · ${p7BidsText}` : ''}.
                              </p>
                            </div>

                            <div className="drawer-p7-value-box drawer-p7-value-box--cohort">
                              <div className="drawer-p7-value-kicker">
                                Repeat-winner cohort total (context only)
                              </div>
                              <div className="drawer-p7-value-num">
                                {p7Record.winnerCohortTotalInr != null && p7Record.winnerCohortTotalInr > 0
                                  ? formatInr(p7Record.winnerCohortTotalInr)
                                  : 'Not applicable'}
                              </div>
                              <p className="drawer-p7-value-note">
                                {p7Record.winnerCohortTotalInr != null && p7Record.winnerCohortTotalInr > 0
                                  ? `Aggregate across ${
                                      p7Record.winnerTotalWins != null
                                        ? `${p7Record.winnerTotalWins.toLocaleString('en-IN')} awards`
                                        : 'multi-award cohort'
                                    }. Shown separately so project totals never double-count overlapping tenders.`
                                  : 'No multi-award cohort total recorded beyond this individual tender.'}
                              </p>
                            </div>
                          </div>

                          {(p7Record.governingInstrument || p7Record.triggeredRules.length > 0) && (
                            <div style={{ marginTop: 10 }}>
                              {p7Record.governingInstrument && (
                                <p className="drawer-p6-step-sub" style={{ marginTop: 0 }}>
                                  <strong>Statutory benchmark:</strong> {p7Record.governingInstrument}
                                  {p7Record.jurisdictionLevel === 'state'
                                    ? ' (State-level procurement authority; Central GFR cited only as comparative reference).'
                                    : ''}
                                </p>
                              )}
                              {p7Record.triggeredRules.length > 0 && (
                                <div className="drawer-p7-chips">
                                  {p7Record.triggeredRules.map((rule) => (
                                    <span key={rule} className="tag-mono font-mono">
                                      {rule}
                                    </span>
                                  ))}
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      </div>
                    </section>
                  )}

                  {/* P8 Structured Sovereign Scheme Dossier (Original Launch Promise vs. Subsequent Walk-Back) */}
                  {p8Record && (
                    <section
                      className={`card card-pad drawer-p8-card${
                        p8Mechanism?.isCleared ? ' drawer-p8-card--honored' : ''
                      }`}
                      aria-label="Sovereign scheme promise versus revision dossier"
                    >
                      <div className="drawer-p6-head">
                        <div>
                          <div className="section-kicker" style={{ marginBottom: 4 }}>
                            Sovereign Scheme Dossier · Original Promise vs. Subsequent Revision
                          </div>
                          <div className="drawer-p6-meta">
                            {p8Record.isSgbAnchor && (
                              <span className="p8-sgb-chip">SGB Flagship Anchor</span>
                            )}
                            {p8Jurisdiction && (
                              <span
                                className={`p8-tier-chip p8-tier-chip--${
                                  (p8Record.jurisdictionLevel || 'union').toLowerCase() === 'state'
                                    ? 'state'
                                    : (p8Record.jurisdictionLevel || 'union').toLowerCase() === 'ut'
                                      ? 'ut'
                                      : 'union'
                                }`}
                              >
                                {p8Jurisdiction}
                              </span>
                            )}
                            {p8Category && <span className="tag-mono">{p8Category.label}</span>}
                            {p8Record.trancheOrPhase && (
                              <span className="tag-mono">{p8Record.trancheOrPhase}</span>
                            )}
                            {p8Record.governmentEraOrLaunchYear && (
                              <span className="tag-mono tabular">
                                Launch: {p8Record.governmentEraOrLaunchYear}
                              </span>
                            )}
                            {p8Impact && (
                              <span className={p8Impact.chipClass}>{p8Impact.label}</span>
                            )}
                          </div>
                        </div>
                        {p8Mechanism && (
                          <span className={p8Mechanism.chipClass}>{p8Mechanism.label}</span>
                        )}
                      </div>

                      {p8Record.schemeName && (
                        <h3 className="drawer-p8-title">{p8Record.schemeName}</h3>
                      )}

                      {(p8Record.authority || p8Record.affectedGroup) && (
                        <div className="drawer-p6-people">
                          {p8Record.authority && (
                            <span>
                              <strong>Issuing authority:</strong> {p8Record.authority}
                            </span>
                          )}
                          {p8Record.affectedGroup && (
                            <span>
                              <strong>Affected group:</strong> {p8Record.affectedGroup}
                            </span>
                          )}
                        </div>
                      )}

                      {/* Quantitative Metric Delta Strip */}
                      {(p8Record.promisedMetric || p8Record.revisedMetric || p8PublishedExposure != null) && (
                        <div
                          className={`drawer-p8-delta-bar tabular${
                            p8PublishedExposure != null ? ' drawer-p8-delta-bar--with-exposure' : ''
                          }`}
                        >
                          <div className="drawer-p8-delta-box drawer-p8-delta-box--promise">
                            <span className="drawer-p8-delta-kicker">
                              Promised Terms{p8Record.promiseDate ? ` · ${p8Record.promiseDate}` : ''}
                            </span>
                            <span className="drawer-p8-delta-val">
                              {p8Record.promisedMetric || 'Documented launch terms'}
                            </span>
                            {p8Record.metricUnit && (
                              <span className="drawer-p8-delta-sub">Unit: {p8Record.metricUnit}</span>
                            )}
                          </div>

                          <div className="drawer-p8-delta-arrow" aria-hidden="true">
                            <span>→</span>
                            {p8ElapsedLabel && (
                              <span className="drawer-p8-delta-span">{p8ElapsedLabel}</span>
                            )}
                          </div>

                          <div
                            className={`drawer-p8-delta-box ${
                              p8Mechanism?.isCleared
                                ? 'drawer-p8-delta-box--honored'
                                : 'drawer-p8-delta-box--walkback'
                            }`}
                          >
                            <span className="drawer-p8-delta-kicker">
                              {p8Mechanism?.isCleared ? 'Current / Honored Terms' : 'Revised / Walk-Back Terms'}
                              {p8Record.walkbackDate ? ` · ${p8Record.walkbackDate}` : ''}
                            </span>
                            <span className="drawer-p8-delta-val">
                              {p8Record.revisedMetric || 'Documented subsequent revision'}
                            </span>
                            {p8ElapsedLabel && (
                              <span className="drawer-p8-delta-sub">Elapsed: {p8ElapsedLabel}</span>
                            )}
                          </div>

                          {p8PublishedExposure != null && (
                            <div className="drawer-p8-delta-box drawer-p8-delta-box--exposure">
                              <span className="drawer-p8-delta-kicker">Published Exposure</span>
                              <span className="drawer-p8-delta-val">
                                {formatInr(p8PublishedExposure)}
                              </span>
                              {p8Record.exposureBasis && (
                                <span className="drawer-p8-delta-sub">{p8Record.exposureBasis}</span>
                              )}
                            </div>
                          )}
                        </div>
                      )}

                      {/* Two-Column Documentary Comparison: Original Promise vs Subsequent Walk-Back */}
                      <div className="drawer-p8-compare-grid">
                        <div className="drawer-p8-doc-col drawer-p8-doc-col--promise">
                          <div className="drawer-p8-doc-head">
                            <div className="drawer-p6-step-kicker" style={{ marginBottom: 0 }}>
                              01 · Original Launch Promise
                            </div>
                            {p8Record.promiseDate && (
                              <span className="tag-mono tabular">{p8Record.promiseDate}</span>
                            )}
                          </div>
                          {p8Record.promiseDocumentRef && (
                            <div className="drawer-p8-doc-ref font-mono">
                              {p8Record.promiseDocumentRef}
                            </div>
                          )}
                          <p className="drawer-p6-step-body">
                            {p8Record.promiseSummary ||
                              'Original launch commitment verified in pinned official record.'}
                          </p>
                          {(p8Record.promiseExcerpt || p8PromiseCit?.excerpt) && (
                            <blockquote className="drawer-p8-excerpt">
                              &ldquo;{p8Record.promiseExcerpt || p8PromiseCit?.excerpt}&rdquo;
                            </blockquote>
                          )}
                          {p8Record.promiseLocator && (
                            <div className="drawer-p8-doc-ref font-mono">
                              Locator: {p8Record.promiseLocator}
                            </div>
                          )}
                          {p8Record.promiseTokensMatched.length > 0 && (
                            <div className="drawer-p8-tokens" aria-label="Verified launch evidence tokens">
                              {p8Record.promiseTokensMatched.slice(0, 6).map((tok) => (
                                <span key={tok} className="tag-mono font-mono">
                                  ✓ {tok}
                                </span>
                              ))}
                            </div>
                          )}
                          {(p8PromiseHref || p8PromiseSha) && (
                            <div className="drawer-p8-source-bar">
                              {p8PromiseHref && (
                                <a
                                  href={p8PromiseHref}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="provenance-link"
                                >
                                  <ExternalLink size={11} aria-hidden="true" />
                                  <span>Primary official source</span>
                                </a>
                              )}
                              {p8PromiseSha && (
                                <span className="tag-mono font-mono" style={{ fontSize: 11 }}>
                                  sha256:{p8PromiseSha.slice(0, 12)}
                                </span>
                              )}
                            </div>
                          )}
                        </div>

                        <div
                          className={`drawer-p8-doc-col ${
                            p8Mechanism?.isCleared
                              ? 'drawer-p8-doc-col--honored'
                              : 'drawer-p8-doc-col--walkback'
                          }`}
                        >
                          <div className="drawer-p8-doc-head">
                            <div className="drawer-p6-step-kicker" style={{ marginBottom: 0 }}>
                              {p8Mechanism?.isCleared
                                ? '02 · Honored / Grandfathered Record'
                                : '02 · Subsequent Walk-Back / Amendment'}
                            </div>
                            {p8Record.walkbackDate && (
                              <span className="tag-mono tabular">{p8Record.walkbackDate}</span>
                            )}
                          </div>
                          {p8Record.walkbackDocumentRef && (
                            <div className="drawer-p8-doc-ref font-mono">
                              {p8Record.walkbackDocumentRef}
                            </div>
                          )}
                          <p className="drawer-p6-step-body">
                            {p8Record.walkbackSummary ||
                              'Subsequent revision verified in pinned official record.'}
                          </p>
                          {(p8Record.walkbackExcerpt || p8WalkbackCit?.excerpt) && (
                            <blockquote className="drawer-p8-excerpt">
                              &ldquo;{p8Record.walkbackExcerpt || p8WalkbackCit?.excerpt}&rdquo;
                            </blockquote>
                          )}
                          {p8Record.walkbackLocator && (
                            <div className="drawer-p8-doc-ref font-mono">
                              Locator: {p8Record.walkbackLocator}
                            </div>
                          )}
                          {p8Record.walkbackTokensMatched.length > 0 && (
                            <div className="drawer-p8-tokens" aria-label="Verified revision evidence tokens">
                              {p8Record.walkbackTokensMatched.slice(0, 6).map((tok) => (
                                <span key={tok} className="tag-mono font-mono">
                                  ✓ {tok}
                                </span>
                              ))}
                            </div>
                          )}
                          {(p8WalkbackHref || p8WalkbackSha) && (
                            <div className="drawer-p8-source-bar">
                              {p8WalkbackHref && (
                                <a
                                  href={p8WalkbackHref}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="provenance-link"
                                >
                                  <ExternalLink size={11} aria-hidden="true" />
                                  <span>
                                    {p8Mechanism?.isCleared
                                      ? 'Verification source'
                                      : 'Amendment / walk-back source'}
                                  </span>
                                </a>
                              )}
                              {p8WalkbackSha && (
                                <span className="tag-mono font-mono" style={{ fontSize: 11 }}>
                                  sha256:{p8WalkbackSha.slice(0, 12)}
                                </span>
                              )}
                            </div>
                          )}
                        </div>
                      </div>

                      {(p8Record.statutoryAnchor || p8Record.triggeredRules.length > 0) && (
                        <div style={{ marginTop: 10 }}>
                          {p8Record.statutoryAnchor && (
                            <p className="drawer-p6-step-sub" style={{ marginTop: 0 }}>
                              <strong>Statutory / notification anchor:</strong> {p8Record.statutoryAnchor}
                            </p>
                          )}
                          {p8Record.triggeredRules.length > 0 && (
                            <div className="drawer-p7-chips">
                              {p8Record.triggeredRules.map((rule) => (
                                <span key={rule} className="tag-mono font-mono">
                                  {rule}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                      )}
                    </section>
                  )}

                  {/* P9 Structured Exam Audit Dossier (Original Schedule vs. What Actually Happened) */}
                  {p9Record && (
                    <section
                      className={`card card-pad drawer-p9-card${
                        p9Disruption?.isCleared ? ' drawer-p9-card--flawless' : ''
                      }`}
                      aria-label="Government exam schedule versus conduct outcome dossier"
                    >
                      <div className="drawer-p6-head">
                        <div>
                          <div className="section-kicker" style={{ marginBottom: 4 }}>
                            Exam Audit Dossier · Original Schedule vs. What Actually Happened
                          </div>
                          <div className="drawer-p6-meta">
                            {p9Record.isAnchorCase && (
                              <span className="p9-anchor-chip">Flagship Anchor Case</span>
                            )}
                            {p9Record.studentsTookExamBeforeCancellation && (
                              <span className="p9-sat-chip">Students sat before cancellation</span>
                            )}
                            {p9Jurisdiction && (
                              <span
                                className={`p8-tier-chip p8-tier-chip--${
                                  (p9Record.jurisdictionLevel || 'union').toLowerCase() === 'state'
                                    ? 'state'
                                    : (p9Record.jurisdictionLevel || 'union').toLowerCase() === 'ut'
                                      ? 'ut'
                                      : 'union'
                                }`}
                              >
                                {p9Jurisdiction}
                              </span>
                            )}
                            {p9Record.examYear != null && (
                              <span className="tag-mono tabular">{String(p9Record.examYear)}</span>
                            )}
                            {p9Category && <span className="tag-mono">{p9Category.label}</span>}
                            {p9AuthorityType && (
                              <span className="tag-mono">{p9AuthorityType.shortLabel}</span>
                            )}
                            {p9Record.cycleOrStage && (
                              <span className="tag-mono">{p9Record.cycleOrStage}</span>
                            )}
                            {p9Reason && (
                              <span className={p9Reason.chipClass}>{p9Reason.label}</span>
                            )}
                          </div>
                        </div>
                        {p9Disruption && (
                          <span className={p9Disruption.chipClass}>{p9Disruption.label}</span>
                        )}
                      </div>

                      {p9Record.examName && (
                        <h3 className="drawer-p8-title">
                          {p9Record.examName}
                          {p9Record.cycleOrStage ? ` — ${p9Record.cycleOrStage}` : ''}
                        </h3>
                      )}

                      {(p9Record.authority || p9Record.affectedCandidatesBasis) && (
                        <div className="drawer-p6-people">
                          {p9Record.authority && (
                            <span>
                              <strong>Conducting authority:</strong> {p9Record.authority}
                            </span>
                          )}
                          <span>
                            <strong>Affected candidates:</strong>{' '}
                            {p9Record.affectedCandidatesCount != null &&
                            p9Record.affectedCandidatesCount > 0 ? (
                              <>
                                <strong className="tabular">
                                  {p9Record.affectedCandidatesCount.toLocaleString('en-IN')}
                                </strong>
                                {p9CandidatesCompact ? ` (${p9CandidatesCompact})` : ''}
                                {p9Record.affectedCandidatesBasis
                                  ? ` — ${p9Record.affectedCandidatesBasis}`
                                  : ''}
                              </>
                            ) : (
                              p9Record.affectedCandidatesBasis ||
                              'Not disclosed in cited official notice'
                            )}
                          </span>
                        </div>
                      )}

                      {/* Exam Schedule -> Actual Event Timeline Strip */}
                      <div
                        className={`drawer-p8-delta-bar tabular${
                          p9Record.affectedCandidatesCount != null &&
                          p9Record.affectedCandidatesCount > 0
                            ? ' drawer-p8-delta-bar--with-exposure'
                            : ''
                        }`}
                      >
                        <div className="drawer-p8-delta-box drawer-p8-delta-box--promise">
                          <span className="drawer-p8-delta-kicker">Original Scheduled Date</span>
                          <span className="drawer-p8-delta-val font-mono">
                            {p9Record.scheduledDate || 'Unrecorded'}
                          </span>
                          {p9Record.examYear != null && (
                            <span className="drawer-p8-delta-sub">
                              Exam year {String(p9Record.examYear)}
                            </span>
                          )}
                        </div>

                        <div className="drawer-p8-delta-arrow" aria-hidden="true">
                          <span>→</span>
                          <span className="drawer-p8-delta-span">
                            {p9Disruption?.isCleared
                              ? 'On schedule'
                              : p9DelayLabel
                                ? `+${p9DelayLabel}`
                                : 'Timeline shift'}
                          </span>
                        </div>

                        <div
                          className={`drawer-p8-delta-box ${
                            p9Disruption?.isCleared
                              ? 'drawer-p8-delta-box--honored'
                              : 'drawer-p8-delta-box--walkback'
                          }`}
                        >
                          <span className="drawer-p8-delta-kicker">
                            {p9Disruption?.isCleared
                              ? 'Verified Conduct Date'
                              : 'Actual / Disruption Event Date'}
                          </span>
                          <span className="drawer-p8-delta-val font-mono">
                            {p9Record.actualOrEventDate || 'Unrecorded'}
                          </span>
                          <span className="drawer-p8-delta-sub">
                            Students sat before cancellation:{' '}
                            <strong>
                              {p9Record.studentsTookExamBeforeCancellation ? 'Yes' : 'No'}
                            </strong>
                          </span>
                        </div>

                        {p9Record.affectedCandidatesCount != null &&
                          p9Record.affectedCandidatesCount > 0 && (
                            <div className="drawer-p8-delta-box drawer-p8-delta-box--exposure">
                              <span className="drawer-p8-delta-kicker">Documented Candidates</span>
                              <span className="drawer-p8-delta-val">
                                {p9Record.affectedCandidatesCount.toLocaleString('en-IN')}
                              </span>
                              {p9Record.affectedCandidatesBasis && (
                                <span className="drawer-p8-delta-sub">
                                  {p9Record.affectedCandidatesBasis}
                                </span>
                              )}
                            </div>
                          )}
                      </div>

                      {/* Two-Column Documentary Comparison: Original Schedule vs What Actually Happened */}
                      <div className="drawer-p8-compare-grid">
                        <div className="drawer-p8-doc-col drawer-p8-doc-col--promise">
                          <div className="drawer-p8-doc-head">
                            <div className="drawer-p6-step-kicker" style={{ marginBottom: 0 }}>
                              01 · Original Schedule
                            </div>
                            {p9Record.scheduledDate && (
                              <span className="tag-mono tabular">{p9Record.scheduledDate}</span>
                            )}
                          </div>
                          {p9Record.scheduleDocumentRef && (
                            <div className="drawer-p8-doc-ref font-mono">
                              {p9Record.scheduleDocumentRef}
                            </div>
                          )}
                          <p className="drawer-p6-step-body">
                            {p9Record.scheduleSummary ||
                              'Original exam schedule verified in pinned official record.'}
                          </p>
                          {(p9Record.scheduleExcerpt || p9ScheduleCit?.excerpt) && (
                            <blockquote className="drawer-p8-excerpt">
                              &ldquo;{p9Record.scheduleExcerpt || p9ScheduleCit?.excerpt}&rdquo;
                            </blockquote>
                          )}
                          {p9Record.scheduleLocator && (
                            <div className="drawer-p8-doc-ref font-mono">
                              Locator: {p9Record.scheduleLocator}
                            </div>
                          )}
                          {p9Record.scheduleTokensMatched.length > 0 && (
                            <div
                              className="drawer-p8-tokens"
                              aria-label="Verified schedule evidence tokens"
                            >
                              {p9Record.scheduleTokensMatched.slice(0, 6).map((tok) => (
                                <span key={tok} className="tag-mono font-mono">
                                  ✓ {tok}
                                </span>
                              ))}
                            </div>
                          )}
                          {(p9ScheduleHref || p9ScheduleSha) && (
                            <div className="drawer-p8-source-bar">
                              {p9ScheduleHref && (
                                <a
                                  href={p9ScheduleHref}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="provenance-link"
                                >
                                  <ExternalLink size={11} aria-hidden="true" />
                                  <span>Primary schedule document</span>
                                </a>
                              )}
                              {p9ScheduleSha && (
                                <span className="tag-mono font-mono" style={{ fontSize: 11 }}>
                                  sha256:{p9ScheduleSha.slice(0, 12)}
                                </span>
                              )}
                            </div>
                          )}
                        </div>

                        <div
                          className={`drawer-p8-doc-col ${
                            p9Disruption?.isCleared
                              ? 'drawer-p8-doc-col--honored'
                              : 'drawer-p8-doc-col--walkback'
                          }`}
                        >
                          <div className="drawer-p8-doc-head">
                            <div className="drawer-p6-step-kicker" style={{ marginBottom: 0 }}>
                              {p9Disruption?.isCleared
                                ? '02 · Conducted Flawlessly (Cleared)'
                                : '02 · What Actually Happened'}
                            </div>
                            {p9Record.actualOrEventDate && (
                              <span className="tag-mono tabular">{p9Record.actualOrEventDate}</span>
                            )}
                          </div>
                          {p9Record.outcomeDocumentRef && (
                            <div className="drawer-p8-doc-ref font-mono">
                              {p9Record.outcomeDocumentRef}
                            </div>
                          )}
                          <p className="drawer-p6-step-body">
                            {p9Record.outcomeSummary ||
                              'Conduct outcome verified in pinned official record.'}
                          </p>
                          {(p9Record.outcomeExcerpt || p9OutcomeCit?.excerpt) && (
                            <blockquote className="drawer-p8-excerpt">
                              &ldquo;{p9Record.outcomeExcerpt || p9OutcomeCit?.excerpt}&rdquo;
                            </blockquote>
                          )}
                          {p9Record.outcomeLocator && (
                            <div className="drawer-p8-doc-ref font-mono">
                              Locator: {p9Record.outcomeLocator}
                            </div>
                          )}
                          {p9Record.outcomeTokensMatched.length > 0 && (
                            <div
                              className="drawer-p8-tokens"
                              aria-label="Verified conduct outcome evidence tokens"
                            >
                              {p9Record.outcomeTokensMatched.slice(0, 6).map((tok) => (
                                <span key={tok} className="tag-mono font-mono">
                                  ✓ {tok}
                                </span>
                              ))}
                            </div>
                          )}
                          {(p9OutcomeHref || p9OutcomeSha) && (
                            <div className="drawer-p8-source-bar">
                              {p9OutcomeHref && (
                                <a
                                  href={p9OutcomeHref}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="provenance-link"
                                >
                                  <ExternalLink size={11} aria-hidden="true" />
                                  <span>
                                    {p9Disruption?.isCleared
                                      ? 'Conduct verification source'
                                      : 'Official disruption notice'}
                                  </span>
                                </a>
                              )}
                              {p9OutcomeSha && (
                                <span className="tag-mono font-mono" style={{ fontSize: 11 }}>
                                  sha256:{p9OutcomeSha.slice(0, 12)}
                                </span>
                              )}
                            </div>
                          )}
                        </div>
                      </div>

                      {(p9Record.statutoryAnchor || p9Record.triggeredRules.length > 0) && (
                        <div style={{ marginTop: 10 }}>
                          {p9Record.statutoryAnchor && (
                            <p className="drawer-p6-step-sub" style={{ marginTop: 0 }}>
                              <strong>Statutory / examination framework anchor:</strong>{' '}
                              {p9Record.statutoryAnchor}
                            </p>
                          )}
                          {p9Record.triggeredRules.length > 0 && (
                            <div className="drawer-p7-chips">
                              {p9Record.triggeredRules.map((rule) => (
                                <span key={rule} className="tag-mono font-mono">
                                  {rule}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                      )}
                    </section>
                  )}

                  {/* P10 Structured 4-Stage Post-Acquittal Custody Overrun & Contempt Dossier */}
                  {p10Record && (
                    <section
                      className={`card card-pad drawer-p10-card${
                        p10Mechanism?.isCleared ? ' drawer-p10-card--compliant' : ''
                      }`}
                      aria-label="Four-stage post-acquittal custody overrun and contempt dossier"
                    >
                      <div className="drawer-p6-head">
                        <div>
                          <div className="section-kicker" style={{ marginBottom: 4 }}>
                            Custody Overrun Dossier · 4-Stage Acquittal → Prison → Contempt → Release Chain
                          </div>
                          <div className="drawer-p6-meta">
                            {p10Record.isAnchorCase && (
                              <span className="p10-anchor-chip">Flagship Anchor Case</span>
                            )}
                            {p10Tier && <span className={p10Tier.chipClass}>{p10Tier.label}</span>}
                            {p10Portal && <span className={p10Portal.chipClass}>{p10Portal.label}</span>}
                            {(p10Record.stateUt || p10Record.jurisdiction) && (
                              <span className="tag-mono">
                                {p10Record.stateUt || formatJurisdictionLabel(p10Record.jurisdiction)}
                              </span>
                            )}
                            {p10Record.year != null && (
                              <span className="tag-mono tabular">{String(p10Record.year)}</span>
                            )}
                            {p10Chain && (
                              <span className={p10Chain.chipClass}>{p10Chain.label}</span>
                            )}
                          </div>
                        </div>
                        {p10Mechanism && (
                          <span className={p10Mechanism.chipClass}>{p10Mechanism.label}</span>
                        )}
                      </div>

                      {p10Record.caseTitle && (
                        <h3 className="drawer-p8-title">{p10Record.caseTitle}</h3>
                      )}

                      {(p10Record.documentRef || p10Record.custodyAuthority || p10Record.prisonName) && (
                        <div className="drawer-p6-people">
                          {p10Record.prisonName && (
                            <span>
                              <strong>Prison / Facility:</strong> {p10Record.prisonName}
                            </span>
                          )}
                          {p10Record.custodyAuthority && (
                            <span>
                              <strong>Custody authority:</strong> {p10Record.custodyAuthority}
                            </span>
                          )}
                          {p10Record.documentRef && (
                            <span className="font-mono">
                              <strong>Ref:</strong> {p10Record.documentRef}
                            </span>
                          )}
                        </div>
                      )}

                      {/* Key Overrun & Outcome Strip */}
                      <div
                        className={`drawer-p10-metrics-bar tabular${
                          p10Compensation != null ? ' drawer-p10-metrics-bar--with-comp' : ''
                        }`}
                      >
                        <div className="drawer-p8-delta-box drawer-p10-box--s1">
                          <span className="drawer-p8-delta-kicker">
                            {p10Record.acquittalOrderType
                              ? formatAcquittalOrderType(p10Record.acquittalOrderType)
                              : 'Acquittal / Release Order'}
                          </span>
                          <span className="drawer-p8-delta-val">
                            {p10Record.acquittalOrderDate || p10Record.documentDate || 'Recorded'}
                          </span>
                          {p10Record.acquittalCaseNumber && (
                            <span className="drawer-p8-delta-sub font-mono">
                              {p10Record.acquittalCaseNumber}
                            </span>
                          )}
                        </div>

                        <div
                          className={`drawer-p8-delta-box ${
                            p10Mechanism?.isCleared ? 'drawer-p8-delta-box--honored' : 'drawer-p10-box--s2'
                          }`}
                        >
                          <span className="drawer-p8-delta-kicker">Custody Overrun</span>
                          <span className="drawer-p8-delta-val">
                            {p10OverrunLabel && (p10Record.overrunDays ?? 0) > 0
                              ? `+${p10OverrunLabel}`
                              : p10Mechanism?.isCleared
                                ? 'Timely (0 days)'
                                : p10Record.overrunWindow || 'Documented delay'}
                          </span>
                          {p10Record.overrunWindow && (p10Record.overrunDays ?? 0) > 0 && (
                            <span className="drawer-p8-delta-sub">{p10Record.overrunWindow}</span>
                          )}
                        </div>

                        <div className="drawer-p8-delta-box drawer-p10-box--s4">
                          <span className="drawer-p8-delta-kicker">Release Outcome</span>
                          <span className="drawer-p8-delta-val">
                            {p10Record.releaseDate
                              ? p10Record.releaseDate
                              : p10Record.releaseVerified
                                ? 'Verified Released'
                                : 'Status Logged'}
                          </span>
                          {p10Record.contemptCaseNumber && (
                            <span className="drawer-p8-delta-sub font-mono">
                              Contempt: {p10Record.contemptCaseNumber}
                            </span>
                          )}
                        </div>

                        {p10Compensation != null && (
                          <div className="drawer-p8-delta-box drawer-p8-delta-box--exposure">
                            <span className="drawer-p8-delta-kicker">Compensation Ordered</span>
                            <span className="drawer-p8-delta-val">{formatInr(p10Compensation)}</span>
                            <span className="drawer-p8-delta-sub">Court / Commission direction</span>
                          </div>
                        )}
                      </div>

                      {/* Full 4-Stage Visual Custody Timeline */}
                      <ol
                        className="p10-timeline p10-timeline--drawer"
                        aria-label="Full 4-stage custody overrun timeline"
                      >
                        <li
                          className={`p10-stage p10-stage--s1${
                            p10Record.stage1AcquittalVerified
                              ? ' p10-stage--verified'
                              : ' p10-stage--unrecorded'
                          }`}
                        >
                          <div className="p10-stage-head">
                            <span className="p10-stage-num font-mono" aria-hidden="true">1</span>
                            <span className="p10-stage-title">
                              {p10Record.acquittalOrderType
                                ? formatAcquittalOrderType(p10Record.acquittalOrderType)
                                : 'Acquitted / Release Order'}
                            </span>
                          </div>
                          {p10Record.acquittalOrderDate && (
                            <span className="p10-stage-badge p10-stage-badge--s1 tabular">
                              {p10Record.acquittalOrderDate}
                            </span>
                          )}
                          <p className="p10-stage-primary">
                            {[p10Record.acquittalCourt, p10Record.acquittalCaseNumber]
                              .filter(Boolean)
                              .join(' · ') || 'Judicial release or acquittal order verified in official record.'}
                          </p>
                          {p10Record.acquittalQuote && (
                            <blockquote className="p10-stage-quote">
                              &ldquo;{p10Record.acquittalQuote}&rdquo;
                            </blockquote>
                          )}
                        </li>

                        <li
                          className={`p10-stage p10-stage--s2${
                            p10Record.stage2ContinuedDetentionVerified
                              ? ' p10-stage--verified'
                              : ' p10-stage--unrecorded'
                          }`}
                        >
                          <div className="p10-stage-head">
                            <span className="p10-stage-num font-mono" aria-hidden="true">2</span>
                            <span className="p10-stage-title">Held in Prison</span>
                          </div>
                          {p10OverrunLabel && (p10Record.overrunDays ?? 0) > 0 && (
                            <span className="p10-stage-badge p10-stage-badge--s2 tabular">
                              +{p10OverrunLabel} overrun
                            </span>
                          )}
                          <p className="p10-stage-primary">
                            {[p10Record.prisonName, p10Record.custodyAuthority]
                              .filter(Boolean)
                              .join(' · ') ||
                              (p10Mechanism?.isCleared
                                ? 'Timely release verified without post-acquittal custody overrun.'
                                : 'Continued custody following judicial release order.')}
                          </p>
                          {p10Record.nonReleaseReason && (
                            <p className="p10-stage-sub">
                              <strong>Recorded reason:</strong> {p10Record.nonReleaseReason}
                            </p>
                          )}
                        </li>

                        <li
                          className={`p10-stage p10-stage--s3${
                            p10Record.stage3ContemptVerified
                              ? ' p10-stage--verified'
                              : ' p10-stage--unrecorded'
                          }`}
                        >
                          <div className="p10-stage-head">
                            <span className="p10-stage-num font-mono" aria-hidden="true">3</span>
                            <span className="p10-stage-title">Contempt Filed</span>
                          </div>
                          {(p10Record.contemptOrderDate || p10Record.contemptFilingDate) && (
                            <span className="p10-stage-badge p10-stage-badge--s3 tabular">
                              {p10Record.contemptFilingDate && p10Record.contemptOrderDate
                                ? `${p10Record.contemptFilingDate} → ${p10Record.contemptOrderDate}`
                                : p10Record.contemptOrderDate || p10Record.contemptFilingDate}
                            </span>
                          )}
                          <p className="p10-stage-primary">
                            {[p10Record.contemptCourt, p10Record.contemptCaseNumber]
                              .filter(Boolean)
                              .join(' · ') ||
                              (p10Mechanism?.isCleared
                                ? 'No contempt proceedings required (timely release).'
                                : 'Contempt / habeas corpus compliance action recorded.')}
                          </p>
                          {p10Record.contemptQuote && (
                            <blockquote className="p10-stage-quote">
                              &ldquo;{p10Record.contemptQuote}&rdquo;
                            </blockquote>
                          )}
                        </li>

                        <li
                          className={`p10-stage p10-stage--s4${
                            p10Record.stage4ReleaseVerified
                              ? ' p10-stage--verified'
                              : ' p10-stage--unrecorded'
                          }`}
                        >
                          <div className="p10-stage-head">
                            <span className="p10-stage-num font-mono" aria-hidden="true">4</span>
                            <span className="p10-stage-title">Released &amp; Directions</span>
                          </div>
                          {p10Record.releaseDate && (
                            <span className="p10-stage-badge p10-stage-badge--s4 tabular">
                              Released {p10Record.releaseDate}
                            </span>
                          )}
                          <p className="p10-stage-primary">
                            {p10Record.releaseVerified
                              ? 'Release verified in official primary record.'
                              : 'Release status logged from primary institutional record.'}
                            {p10Compensation != null ? ` Compensation ordered: ${formatInr(p10Compensation)}.` : ''}
                          </p>
                          {p10Record.judicialDirectionSummary && (
                            <p className="p10-stage-sub">{p10Record.judicialDirectionSummary}</p>
                          )}
                        </li>
                      </ol>

                      {/* Two-Column Verbatim Documentary Anchors + Official Links & SHA-256 Badges */}
                      <div className="drawer-p8-compare-grid" style={{ marginTop: 12 }}>
                        <div className="drawer-p8-doc-col drawer-p10-doc-col--order">
                          <div className="drawer-p8-doc-head">
                            <div className="drawer-p6-step-kicker" style={{ marginBottom: 0 }}>
                              Primary Anchor · Acquittal &amp; Custody Order
                            </div>
                            {p10Record.acquittalOrderDate && (
                              <span className="tag-mono tabular">{p10Record.acquittalOrderDate}</span>
                            )}
                          </div>
                          {(p10Record.custodyOrderExcerpt || p10OrderCit?.excerpt || p10Record.acquittalQuote) && (
                            <blockquote className="drawer-p8-excerpt">
                              &ldquo;
                              {p10Record.custodyOrderExcerpt ||
                                p10OrderCit?.excerpt ||
                                p10Record.acquittalQuote}
                              &rdquo;
                            </blockquote>
                          )}
                          {p10Record.custodyOrderLocator && (
                            <div className="drawer-p8-doc-ref font-mono">
                              Locator: {p10Record.custodyOrderLocator}
                            </div>
                          )}
                          {p10Record.custodyOrderTokensMatched.length > 0 && (
                            <div className="drawer-p8-tokens" aria-label="Verified custody order evidence tokens">
                              {p10Record.custodyOrderTokensMatched.slice(0, 6).map((tok) => (
                                <span key={tok} className="tag-mono font-mono">
                                  ✓ {tok}
                                </span>
                              ))}
                            </div>
                          )}
                          {(p10OrderHref || p10OrderSha) && (
                            <div className="drawer-p8-source-bar">
                              {p10OrderHref && (
                                <a
                                  href={p10OrderHref}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="provenance-link"
                                >
                                  <ExternalLink size={11} aria-hidden="true" />
                                  <span>Official primary document</span>
                                </a>
                              )}
                              {p10OrderSha && (
                                <span className="tag-mono font-mono" style={{ fontSize: 11 }}>
                                  sha256:{p10OrderSha.slice(0, 12)}
                                </span>
                              )}
                            </div>
                          )}
                        </div>

                        <div
                          className={`drawer-p8-doc-col ${
                            p10Mechanism?.isCleared
                              ? 'drawer-p8-doc-col--honored'
                              : 'drawer-p10-doc-col--contempt'
                          }`}
                        >
                          <div className="drawer-p8-doc-head">
                            <div className="drawer-p6-step-kicker" style={{ marginBottom: 0 }}>
                              {p10Mechanism?.isCleared
                                ? 'Outcome Anchor · Compliance Verification'
                                : 'Outcome Anchor · Contempt & Release Record'}
                            </div>
                            {(p10Record.releaseDate || p10Record.contemptOrderDate) && (
                              <span className="tag-mono tabular">
                                {p10Record.releaseDate || p10Record.contemptOrderDate}
                              </span>
                            )}
                          </div>
                          {(p10Record.contemptOrOutcomeExcerpt ||
                            p10ContemptCit?.excerpt ||
                            p10Record.contemptQuote) && (
                            <blockquote className="drawer-p8-excerpt">
                              &ldquo;
                              {p10Record.contemptOrOutcomeExcerpt ||
                                p10ContemptCit?.excerpt ||
                                p10Record.contemptQuote}
                              &rdquo;
                            </blockquote>
                          )}
                          {p10Record.contemptOrOutcomeLocator && (
                            <div className="drawer-p8-doc-ref font-mono">
                              Locator: {p10Record.contemptOrOutcomeLocator}
                            </div>
                          )}
                          {p10Record.contemptOrOutcomeTokensMatched.length > 0 && (
                            <div className="drawer-p8-tokens" aria-label="Verified contempt and release tokens">
                              {p10Record.contemptOrOutcomeTokensMatched.slice(0, 6).map((tok) => (
                                <span key={tok} className="tag-mono font-mono">
                                  ✓ {tok}
                                </span>
                              ))}
                            </div>
                          )}
                          {(p10ContemptHref || p10ContemptSha) && (
                            <div className="drawer-p8-source-bar">
                              {p10ContemptHref && (
                                <a
                                  href={p10ContemptHref}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="provenance-link"
                                >
                                  <ExternalLink size={11} aria-hidden="true" />
                                  <span>
                                    {p10Mechanism?.isCleared
                                      ? 'Compliance source'
                                      : 'Contempt / release source'}
                                  </span>
                                </a>
                              )}
                              {p10ContemptSha && (
                                <span className="tag-mono font-mono" style={{ fontSize: 11 }}>
                                  sha256:{p10ContemptSha.slice(0, 12)}
                                </span>
                              )}
                            </div>
                          )}
                        </div>
                      </div>

                      {(p10Record.statutoryAnchor || p10Record.triggeredRules.length > 0) && (
                        <div style={{ marginTop: 10 }}>
                          {p10Record.statutoryAnchor && (
                            <p className="drawer-p6-step-sub" style={{ marginTop: 0 }}>
                              <strong>Constitutional &amp; statutory anchor:</strong>{' '}
                              {p10Record.statutoryAnchor}
                            </p>
                          )}
                          {p10Record.triggeredRules.length > 0 && (
                            <div className="drawer-p7-chips">
                              {p10Record.triggeredRules.map((rule) => (
                                <span key={rule} className="tag-mono font-mono">
                                  {rule}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                      )}
                    </section>
                  )}

                  {/* Deterministic Metrics Table */}
                  {displayMetricEntries.length > 0 && (
                    <div>
                      <div
                        style={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          alignItems: 'center',
                          marginBottom: 8,
                          gap: 10,
                          flexWrap: 'wrap',
                        }}
                      >
                        <div className="section-kicker" style={{ marginBottom: 0 }}>
                          Deterministic Computed Metrics (Reproducible)
                        </div>
                        {finding.verifier_script_hash && (
                          <span className="font-mono" style={{ fontSize: 12, color: 'var(--ink-3)' }}>
                            engine_sha256: <code className="font-mono">{finding.verifier_script_hash.slice(0, 12)}</code>
                          </span>
                        )}
                      </div>
                      <div className="table-wrap drawer-metrics-table">
                        <table className="ledger-table">
                          <thead>
                            <tr>
                              <th>Metric</th>
                              <th className="num">Computed Value</th>
                            </tr>
                          </thead>
                          <tbody>
                            {displayMetricEntries.map(([k, v]) => (
                              <tr key={k}>
                                <td style={{ textTransform: 'capitalize' }}>{k.replace(/_/g, ' ')}</td>
                                <td className="num">
                                  {v === null || v === undefined
                                    ? 'Unrecorded'
                                    : typeof v === 'number'
                                      ? k.includes('inr')
                                        ? formatInr(v)
                                        : k.endsWith('_pct')
                                          ? `${v.toLocaleString('en-IN', { maximumFractionDigits: 2 })}%`
                                          : k === 'year' || k.endsWith('_year')
                                            ? String(v)
                                            : v.toLocaleString('en-IN', { maximumFractionDigits: 3 })
                                      : typeof v === 'boolean'
                                        ? v
                                          ? 'Yes'
                                          : 'No'
                                        : Array.isArray(v)
                                          ? v.length > 0
                                            ? v.map((item) => String(item)).join(', ')
                                            : 'None'
                                          : typeof v === 'object'
                                            ? JSON.stringify(v)
                                            : String(v)}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}

                  {/* Honest Source Provenance & Verification Manifest */}
                  <div className="card card-pad">
                    <div className="section-kicker">Source Provenance &amp; Verification Manifest</div>
                    <dl className="provenance-list">
                      {prov?.official_publisher && (
                        <div className="provenance-row">
                          <dt>Official Publisher</dt>
                          <dd>{prov.official_publisher}</dd>
                        </div>
                      )}

                      {prov?.source_landing_page && (
                        <div className="provenance-row">
                          <dt>{landingIsOfficial ? 'Official Source Landing Page' : 'Source Landing Page'}</dt>
                          <dd>
                            {safeLandingUrl ? (
                              <a
                                href={safeLandingUrl}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="provenance-link"
                              >
                                <ExternalLink size={12} aria-hidden="true" />
                                <span>{safeLandingUrl}</span>
                              </a>
                            ) : (
                              <span>{prov.source_landing_page}</span>
                            )}
                          </dd>
                        </div>
                      )}

                      {(prov?.mirror_name || rawMirrorUrl) && (
                        <div className="provenance-row">
                          <dt>Civic Mirror / Catalog</dt>
                          <dd>
                            {prov?.mirror_name && <span>{prov.mirror_name}</span>}
                            {prov?.mirror_name && rawMirrorUrl && ', '}
                            {safeMirrorUrl ? (
                              <a
                                href={safeMirrorUrl}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="provenance-link"
                              >
                                <ExternalLink size={12} aria-hidden="true" />
                                <span>{safeMirrorUrl}</span>
                              </a>
                            ) : rawMirrorUrl ? (
                              <span>{rawMirrorUrl}</span>
                            ) : null}
                          </dd>
                        </div>
                      )}

                      {retrievalUrl && (
                        <div className="provenance-row">
                          <dt>
                            {retrievalIsOfficial
                              ? 'Immutable Official Retrieval URL'
                              : 'Immutable Retrieval URL (Mirror / Snapshot)'}
                          </dt>
                          <dd>
                            {safeRetrievalUrl ? (
                              <a
                                href={safeRetrievalUrl}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="provenance-link"
                              >
                                <ExternalLink size={12} aria-hidden="true" />
                                <span>{safeRetrievalUrl}</span>
                              </a>
                            ) : (
                              <span>{retrievalUrl}</span>
                            )}
                          </dd>
                        </div>
                      )}

                      {prov?.archive_url && (
                        <div className="provenance-row">
                          <dt>Archive Snapshot</dt>
                          <dd>
                            {safeArchiveUrl ? (
                              <a
                                href={safeArchiveUrl}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="provenance-link"
                              >
                                <ExternalLink size={12} aria-hidden="true" />
                                <span>{safeArchiveUrl}</span>
                              </a>
                            ) : (
                              <span>{prov.archive_url}</span>
                            )}
                          </dd>
                        </div>
                      )}

                      <div className="provenance-row">
                        <dt>Observed SHA-256 Digest</dt>
                        <dd>
                          {prov?.observed_sha256 ? (
                            <div className="provenance-hash-box">
                              <code className="font-mono" style={{ fontSize: 11.5, wordBreak: 'break-all' }}>
                                {prov.observed_sha256}
                              </code>
                              <button
                                type="button"
                                className="btn btn-ghost btn-xs"
                                onClick={() =>
                                  handleCopy('sha', prov.observed_sha256 || '', 'Copied observed SHA-256 digest')
                                }
                              >
                                {copiedKey === 'sha' ? <Check size={12} /> : <Copy size={12} />}
                                <span>{copiedKey === 'sha' ? 'Copied' : 'Copy'}</span>
                              </button>
                            </div>
                          ) : (
                            <span style={{ color: 'var(--ink-3)' }}>Unrecorded</span>
                          )}
                        </dd>
                      </div>

                      {prov?.secondary_url && (() => {
                        const safeSecUrl = safeExternalUrl(prov.secondary_url);
                        const secOfficial = isOfficialGovUrl(safeSecUrl);
                        return (
                          <div className="provenance-row">
                            <dt>
                              {secOfficial
                                ? 'Secondary Official Retrieval URL'
                                : 'Secondary Retrieval URL'}
                            </dt>
                            <dd>
                              {safeSecUrl ? (
                                <a
                                  href={safeSecUrl}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="provenance-link"
                                >
                                  <ExternalLink size={12} aria-hidden="true" />
                                  <span>{safeSecUrl}</span>
                                </a>
                              ) : (
                                <span>{prov.secondary_url}</span>
                              )}
                            </dd>
                          </div>
                        );
                      })()}

                      {prov?.secondary_sha256 && (
                        <div className="provenance-row">
                          <dt>Secondary Input SHA-256</dt>
                          <dd>
                            <div className="provenance-hash-box">
                              <code className="font-mono" style={{ fontSize: 11.5, wordBreak: 'break-all' }}>
                                {prov.secondary_sha256}
                              </code>
                              <button
                                type="button"
                                className="btn btn-ghost btn-xs"
                                onClick={() =>
                                  handleCopy(
                                    'sec-sha',
                                    prov.secondary_sha256 || '',
                                    'Copied secondary SHA-256 digest',
                                  )
                                }
                              >
                                {copiedKey === 'sec-sha' ? <Check size={12} /> : <Copy size={12} />}
                                <span>{copiedKey === 'sec-sha' ? 'Copied' : 'Copy'}</span>
                              </button>
                            </div>
                          </dd>
                        </div>
                      )}

                      <div className="provenance-row">
                        <dt>Byte Size &amp; Fetch Time</dt>
                        <dd className="tabular">
                          <span>{formatBytes(prov?.byte_size)}</span>
                          {', '}
                          <span>
                            {prov?.fetched_at ? formatDateShort(prov.fetched_at) : 'Fetch time unrecorded'}
                          </span>
                        </dd>
                      </div>

                      {(prov?.source_locator || prov?.engine_version || prov?.ruleset_version || prov?.as_of_date) && (
                        <div className="provenance-row">
                          <dt>Manifest &amp; Locator</dt>
                          <dd style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                            {prov.source_locator && (
                              <span
                                className="tag-mono"
                                style={{ whiteSpace: 'normal', overflowWrap: 'anywhere', lineHeight: 1.35 }}
                              >
                                Locator: {prov.source_locator}
                              </span>
                            )}
                            {prov.engine_version && <span className="tag-mono">Engine: {prov.engine_version}</span>}
                            {prov.ruleset_version && <span className="tag-mono">Ruleset: {prov.ruleset_version}</span>}
                            {prov.as_of_date && <span className="tag-mono">As-of: {prov.as_of_date}</span>}
                          </dd>
                        </div>
                      )}
                    </dl>

                    {finding.citations.length > 0 && (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 14 }}>
                        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink-2)' }}>
                          Pinpoint Citations ({finding.citations.length})
                        </div>
                        {finding.citations.map((cit, i) => {
                          const citHref = citationHref(cit, finding.artifact_sha256);
                          const roleLabel = formatCitationRoleLabel(cit.role);
                          return (
                            <div
                              key={i}
                              style={{
                                padding: '10px 12px',
                                background: 'var(--bg-paper)',
                                borderLeft: '2px solid var(--border-strong)',
                                fontSize: 13,
                              }}
                            >
                              <div className="drawer-citation-head">
                                {roleLabel && (
                                  <span className="drawer-citation-role">{roleLabel}</span>
                                )}
                                <span className="font-mono" style={{ fontSize: 12, color: 'var(--ink-3)' }}>
                                  {[
                                    cit.title,
                                    cit.source_locator,
                                    cit.page != null ? `Page ${cit.page}` : null,
                                    cit.para ? `Para ${cit.para}` : null,
                                    cit.row_id != null ? `Row ${cit.row_id}` : null,
                                  ]
                                    .filter(Boolean)
                                    .join(', ') || 'Source record excerpt'}
                                </span>
                                {cit.sha256 && (
                                  <span className="tag-mono font-mono" style={{ fontSize: 11 }}>
                                    sha256:{cit.sha256.slice(0, 10)}
                                  </span>
                                )}
                              </div>
                              {cit.excerpt && (
                                <div style={{ color: 'var(--ink-primary)', fontStyle: 'italic' }}>
                                  “{cit.excerpt}”
                                </div>
                              )}
                              {citHref && (
                                <a
                                  href={citHref}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="provenance-link"
                                  style={{ marginTop: 4, wordBreak: 'break-all' }}
                                >
                                  <ExternalLink size={12} aria-hidden="true" />
                                  <span>{citHref}</span>
                                </a>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>

                  {/* Mandatory Benign Explanations Box (BNS Sec 356 Fairness) */}
                  <div
                    style={{
                      background: 'var(--paper)',
                      border: '1px solid var(--ep-review-bd)',
                      borderLeft: '3px solid var(--ep-review-fg)',
                      borderRadius: 4,
                      padding: '15px 18px',
                    }}
                  >
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 7,
                        fontSize: 13,
                        fontWeight: 600,
                        color: 'var(--ep-review-fg)',
                        marginBottom: 8,
                      }}
                    >
                      <Scale size={14} />
                      <span>Mandatory Benign / Lawful Explanations (Journalistic Fairness, BNS Sec 356)</span>
                    </div>
                    <p style={{ margin: '0 0 8px', fontSize: 13, color: 'var(--ink-3)' }}>
                      Statistical divergence in public records is an audit lead — never an allegation of
                      wrongdoing. Before drawing conclusions, verify these plausible lawful explanations:
                    </p>
                    <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13.5, color: 'var(--ink-2)', display: 'flex', flexDirection: 'column', gap: 5 }}>
                      {(finding.benign_explanations.length > 0
                        ? finding.benign_explanations
                        : ['Lawful statutory exception or competent authority waiver may exist on the official file.']
                      ).map((exp, idx) => (
                        <li key={idx}>{exp}</li>
                      ))}
                    </ul>
                  </div>

                  {/* Independent Peer Verification Quorum Tracker */}
                  <div className="card card-pad">
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        gap: 10,
                        marginBottom: 12,
                        flexWrap: 'wrap',
                      }}
                    >
                      <div>
                        <div className="section-kicker" style={{ marginBottom: 2 }}>
                          Independent Verification &amp; Review Record
                        </div>
                        <div style={{ fontSize: 13.5, color: 'var(--ink-3)' }}>
                          Submitted by{' '}
                          {finding.submitted_by ? (
                            <Link
                              to={`/a/${encodeURIComponent(finding.submitted_by)}`}
                              onClick={onClose}
                              className="font-mono"
                              style={{ color: 'var(--ep-contrib-fg)', fontWeight: 600 }}
                            >
                              @{finding.submitted_by}
                            </Link>
                          ) : (
                            'swarm node'
                          )}
                          {finding.submitted_coding_agent ? (
                            <span className="via-agent">
                              {' '}via <AgentBadge agent={finding.submitted_coding_agent} model={finding.submitted_coding_agent_model} />
                            </span>
                          ) : null}{' '}
                          Computational reproduction confirms bytes &amp; math; editorial review confirms source meaning.
                        </div>
                      </div>
                      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                        <span className={confirmCount >= requiredQuorum ? 'badge badge-verified' : 'badge badge-review'}>
                          <ShieldCheck size={12} />
                          <span>
                            Computational: {confirmCount}/{requiredQuorum}
                          </span>
                        </span>
                        <span className={editorialReviewCount > 0 ? 'badge badge-rti' : 'badge badge-cleared'}>
                          Editorial reviews: {editorialReviewCount}
                        </span>
                      </div>
                    </div>

                    {finding.verifications && finding.verifications.length > 0 ? (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                        {finding.verifications.map((v, i) => {
                          const reviewKind = formatReviewKindLabel(v.review_kind);
                          const verdictTag = getVerdictTag(v.verdict);
                          return (
                            <div
                              key={v.id ?? i}
                              style={{
                                padding: '12px 14px',
                                background: 'var(--bg-paper)',
                                border: '1px solid var(--border-hairline)',
                                borderRadius: 4,
                              }}
                            >
                              <div
                                style={{
                                  display: 'flex',
                                  justifyContent: 'space-between',
                                  alignItems: 'center',
                                  gap: 8,
                                  flexWrap: 'wrap',
                                  marginBottom: 8,
                                }}
                              >
                                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                                  <Link
                                    to={`/a/${encodeURIComponent(v.verifier_handle)}`}
                                    onClick={onClose}
                                    className="font-mono"
                                    style={{ fontSize: 13, fontWeight: 600, color: 'var(--ep-contrib-fg)' }}
                                  >
                                    @{v.verifier_handle}
                                  </Link>
                                  <span className={reviewKind.isEditorial ? 'badge badge-rti' : 'badge badge-work'}>
                                    {reviewKind.label}
                                  </span>
                                  <ProviderModelBadge
                                    provider={v.provider}
                                    model={v.model_id}
                                    codingAgent={v.coding_agent}
                                    codingAgentModel={v.coding_agent_model}
                                  />
                                </div>
                                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                  <span className={verdictTag.badgeClass} title={verdictTag.description}>
                                    {verdictTag.label}
                                  </span>
                                  <span className="font-mono tabular" style={{ fontSize: 12, color: 'var(--ink-3)' }}>
                                    {v.confidence != null
                                      ? `${Math.round(v.confidence * 100)}% conf`
                                      : 'Confidence: Unrecorded'}
                                  </span>
                                </div>
                              </div>

                              <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', fontSize: 12, marginBottom: v.notes ? 6 : 0 }}>
                                {renderCheckBadge('Bytes Digest', v.checks.provenance_ok)}
                                {renderCheckBadge('Deterministic Reproduction', v.checks.math_reproduced)}
                                {renderCheckBadge('Metric Tolerance', v.checks.rule_valid)}
                                {renderCheckBadge('Neutrality & PII', v.checks.legal_pii_ok)}
                              </div>

                              {v.notes && (
                                <p style={{ margin: '6px 0 0', fontSize: 13, color: 'var(--ink-2)' }}>
                                  {v.notes}
                                </p>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    ) : (
                      <div className="state-banner" style={{ padding: 16 }}>
                        Awaiting independent peer verification from {requiredQuorum} pairwise-distinct contributor nodes (distinct harnesses and models from the author and each other). Run{' '}
                        <code className="font-mono">aiwatchdogs join --mode verify</code> to verify this lead!
                      </div>
                    )}
                  </div>

                  {/* 1-Click RTI Act 2005 Sec 6(1) Draft Generator & Viewer */}
                  <div className="card card-pad" style={{ borderColor: 'var(--ep-rti-bd)' }}>
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'flex-start',
                        justifyContent: 'space-between',
                        gap: 12,
                        flexWrap: 'wrap',
                        marginBottom: 12,
                      }}
                    >
                      <div>
                        <div className="section-kicker" style={{ color: 'var(--ep-rti-fg)', marginBottom: 3 }}>
                          Lawful Citizen Action, RTI Act 2005 Section 6(1) &amp; Section 2(f)
                        </div>
                        <h3 className="font-serif" style={{ margin: 0, fontSize: 20 }}>
                          Public Records Request Draft
                        </h3>
                      </div>

                      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                        {rtiDraft?.is_preview && (
                          <span className="badge badge-review">Preview only</span>
                        )}
                        {rtiDraft?.version != null && (
                          <span className="tag-mono">v{rtiDraft.version}</span>
                        )}
                        {rtiDraft && (
                          <span
                            className={`font-mono tabular ${
                              rtiDraft.word_count <= 500 ? 'badge badge-verified' : 'badge badge-disputed'
                            }`}
                          >
                            {rtiDraft.word_count} / 500 words
                          </span>
                        )}
                      </div>
                    </div>

                    {(hasEditorialNotice || rtiDraft?.editorial_warning) && (
                      <div
                        style={{
                          marginBottom: 12,
                          padding: '10px 12px',
                          background: 'var(--red-bg)',
                          border: '1px solid var(--ep-disputed-bd)',
                          borderRadius: 6,
                          fontSize: 13,
                          color: 'var(--red)',
                        }}
                      >
                        <strong>Editorial Caution:</strong>{' '}
                        {rtiDraft?.editorial_warning ||
                          'This dossier carries an active editorial review or correction notice. Read the Correction History above before filing any RTI based on this record.'}
                      </div>
                    )}

                    <div className="drawer-rti-form">
                      <input
                        type="text"
                        className="input-field drawer-rti-input"
                        value={rtiAuthority}
                        onChange={(e) => setRtiAuthority(e.target.value)}
                        placeholder="Target Public Authority / Ministry (optional personal override)"
                        aria-label="Target Public Authority"
                        maxLength={160}
                      />
                      <button
                        type="button"
                        className="btn btn-rti drawer-rti-submit"
                        onClick={handleGenerateRti}
                        disabled={rtiLoading}
                      >
                        <FileText size={14} />
                        <span>
                          {rtiLoading
                            ? 'Drafting…'
                            : rtiDraft
                              ? 'Preview / Update Draft'
                              : 'Generate RTI Act 2005 Draft'}
                        </span>
                      </button>
                    </div>

                    {rtiDraft ? (
                      <>
                        <div
                          style={{
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'center',
                            gap: 10,
                            flexWrap: 'wrap',
                            marginBottom: 10,
                            fontSize: 13,
                          }}
                        >
                          <div>
                            <strong>Authority:</strong> {rtiDraft.public_authority},{' '}
                            <strong>PIO/CPIO:</strong> {rtiDraft.cpio_designation}
                          </div>
                          <div style={{ display: 'flex', gap: 7 }}>
                            <button
                              type="button"
                              className="btn btn-secondary btn-xs"
                              onClick={() =>
                                handleCopy('rti', rtiDraft.body_text, 'Copied RTI Act 2005 Sec 6(1) text')
                              }
                            >
                              {copiedKey === 'rti' ? <Check size={12} /> : <Copy size={12} />}
                              <span>{copiedKey === 'rti' ? 'Copied' : 'Copy Text'}</span>
                            </button>
                            <button
                              type="button"
                              className="btn btn-secondary btn-xs"
                              onClick={handleDownloadRti}
                            >
                              <Download size={12} />
                              <span>Download .txt</span>
                            </button>
                          </div>
                        </div>

                        <div className="rti-paper">{rtiDraft.body_text}</div>

                        <div
                          style={{
                            marginTop: 12,
                            padding: '12px 14px',
                            background: 'var(--ep-rti-bg)',
                            border: '1px solid var(--ep-rti-bd)',
                            borderRadius: 4,
                            fontSize: 13,
                            color: 'var(--ink-2)',
                          }}
                        >
                          <div style={{ fontWeight: 600, color: 'var(--ep-rti-fg)', marginBottom: 4 }}>
                            Human-in-the-Loop Filing Guide ({isStateRti ? `${finding.jurisdiction} State Jurisdiction` : 'Central / Union Jurisdiction'})
                          </div>
                          <ol style={{ margin: 0, paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 4 }}>
                            <li>
                              AI agents never auto-file legal requests. Review every line above and verify
                              it seeks existing official records under Section 2(f).
                            </li>
                            <li>
                              {isStateRti ? (
                                <>
                                  <strong>State Public Authority:</strong> Do <em>not</em> submit State
                                  authority RTIs on Central <code className="font-mono">rtionline.gov.in</code>{' '}
                                  (applications for State authorities are returned without refund).{' '}
                                  {safeRtiPortalUrl ? (
                                    <>
                                      File via{' '}
                                      <a
                                        href={safeRtiPortalUrl}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        style={{ color: 'var(--ep-rti-fg)', textDecoration: 'underline', fontWeight: 600 }}
                                      >
                                        {safeRtiPortalUrl}
                                      </a>
                                      .
                                    </>
                                  ) : (
                                    <span>
                                      {rtiDraft.filing_channel ||
                                        `File via the official ${finding.jurisdiction} State RTI Portal or by Registered/Speed Post to the designated State Public Information Officer (SPIO).`}
                                    </span>
                                  )}
                                </>
                              ) : (
                                <>
                                  Open{' '}
                                  <a
                                    href={safeRtiPortalUrl || 'https://rtionline.gov.in/'}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    style={{ color: 'var(--ep-rti-fg)', textDecoration: 'underline', fontWeight: 600 }}
                                  >
                                    {safeRtiPortalUrl || 'https://rtionline.gov.in/'}
                                  </a>
                                  , select the Public Authority, and paste the text (strictly ≤500 words per
                                  Central RTI Rules 2012 Rule 3).
                                </>
                              )}
                            </li>
                            <li>
                              {rtiDraft.fee_note ||
                                (isStateRti
                                  ? 'Pay the fee prescribed under the respective State RTI Rules (BPL applicants exempt under Section 7(5)). Statutory SPIO response deadline is 30 days.'
                                  : 'Pay the statutory ₹10 fee (exempt for BPL applicants under Section 7(5)). Statutory CPIO response deadline is 30 days (First Appeal under Section 19(1) if unanswered).')}
                            </li>
                          </ol>
                        </div>
                      </>
                    ) : (
                      <div
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: 8,
                          fontSize: 13,
                          color: 'var(--ink-3)',
                        }}
                      >
                        <AlertTriangle size={14} />
                        <span>
                          Click <strong>Generate RTI Act 2005 Draft</strong> to preview a ≤500-word Sec 6(1)
                          records request citing this finding’s exact public artifact and statutory anchor.
                        </span>
                      </div>
                    )}
                  </div>
                </>
              )}

              {finding.created_at && (
                <div className="font-mono" style={{ fontSize: 12, color: 'var(--ink-3)', textAlign: 'right' }}>
                  Logged {formatRelativeTime(finding.created_at)}
                  {finding.verified_at ? `, Verified ${formatRelativeTime(finding.verified_at)}` : ''}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
