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
import { api } from '../api/client';
import { useDialogFocus } from '../hooks/useDialogFocus';
import { Link } from '../router';
import type { FindingItem, RtiDraft, VerificationCheckState } from '../types';
import {
  METHODOLOGY_DESCRIPTIONS,
  copyToClipboard,
  downloadTextFile,
  formatBytes,
  formatDateShort,
  formatInr,
  formatRelativeTime,
  isOfficialGovUrl,
  providerBadgeClass,
  statusMeta,
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
  if (k.includes('editorial')) return { label: 'Editorial Review', isEditorial: true };
  if (k.includes('adjudic')) return { label: 'Adjudication', isEditorial: true };
  return { label: 'Computational Reproduction', isEditorial: false };
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
        setRtiAuthority(data.rti_draft?.public_authority || data.entity_name || '');
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
  }, [findingId]);

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
    setRtiLoading(true);
    try {
      const draft = await api.generateRtiDraft(finding.id, rtiAuthority.trim() || undefined);
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

  const sm = finding ? statusMeta(finding.status) : null;
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
    finding?.reproduction_summary?.required_quorum ?? finding?.required_quorum ?? 2;
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
  const retrievalIsOfficial = isOfficialGovUrl(retrievalUrl);
  const landingIsOfficial = isOfficialGovUrl(prov?.source_landing_page);
  const mirrorUrl = prov?.mirror_url || (!retrievalIsOfficial && retrievalUrl ? retrievalUrl : null);
  const fiscalDisplay =
    finding?.fiscal_year_start &&
    finding?.fiscal_year_end &&
    finding.fiscal_year_start !== finding.fiscal_year_end
      ? `${finding.fiscal_year_start} – ${finding.fiscal_year_end}`
      : finding?.fiscal_year || 'Unrecorded';

  const isStateRti =
    rtiDraft?.jurisdiction_level === 'state' ||
    (Boolean(finding) &&
      !isOfficialGovUrl(rtiDraft?.rti_portal_url) &&
      finding!.jurisdiction.toLowerCase() !== 'union' &&
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
              {sm && <span className={sm.badgeClass}>{sm.label}</span>}
              {finding && <span className="tag-mono tag-method">{finding.methodology_code}</span>}
              {finding?.editorial_status &&
                finding.editorial_status !== 'none' &&
                finding.editorial_status !== finding.status &&
                !(
                  finding.status === 'under_editorial_review' &&
                  finding.editorial_status === 'under_review'
                ) && (
                  <span className="badge badge-disputed">
                    Editorial: {finding.editorial_status.replace(/_/g, ' ')}
                  </span>
                )}
            </div>
            <h2
              id="finding-drawer-title"
              className="font-serif"
              style={{ margin: 0, fontSize: 23, lineHeight: 1.24, fontWeight: 400 }}
            >
              {loading ? 'Loading evidence dossier…' : finding?.title || 'Finding Dossier'}
            </h2>
          </div>

          <button
            type="button"
            className="btn btn-ghost btn-sm"
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
                      <span className="tag-mono" style={{ marginLeft: 'auto' }}>
                        {finding.editorial_status.replace(/_/g, ' ')}
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
                                ? ` · ${c.previous_status} → ${c.new_status}`
                                : ''}
                            </span>
                            <span className="font-mono" style={{ fontSize: 12, color: 'var(--ink-3)' }}>
                              {c.reviewer ? `Reviewed by ${c.reviewer}` : 'Editorial desk'}
                              {c.created_at ? ` · ${formatDateShort(c.created_at)}` : ''}
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
                          {c.source_evidence_url && (
                            <a
                              href={c.source_evidence_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="inline-link"
                              style={{ fontSize: 12.5, display: 'inline-flex', alignItems: 'center', gap: 4, marginTop: 4 }}
                            >
                              <span>Inspect referenced source evidence</span>
                              <ExternalLink size={11} aria-hidden="true" />
                            </a>
                          )}
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
                    <div className="grid-4" style={{ gap: 12 }}>
                      <div>
                        <div className="kicker" style={{ fontSize: 10.5 }}>Public Entity</div>
                        <div style={{ fontSize: 14, marginTop: 3 }}>{finding.entity_name}</div>
                      </div>
                      <div>
                        <div className="kicker" style={{ fontSize: 10.5 }}>Jurisdiction / Period</div>
                        <div style={{ fontSize: 14, marginTop: 3 }}>
                          {finding.jurisdiction} · <span className="font-mono">{fiscalDisplay}</span>
                        </div>
                      </div>
                      <div>
                        <div className="kicker" style={{ fontSize: 10.5 }}>
                          {finding.amount_label ? finding.amount_label.replace(/_/g, ' ') : 'Reported Figure'}
                        </div>
                        <div className="font-mono tabular" style={{ fontSize: 15.5, marginTop: 3 }}>
                          {finding.monetary_exposure_inr != null
                            ? formatInr(finding.monetary_exposure_inr)
                            : 'Disclosure Lead'}
                        </div>
                      </div>
                      <div>
                        <div className="kicker" style={{ fontSize: 10.5 }}>Severity / Quorum</div>
                        <div className="font-mono tabular" style={{ fontSize: 14, marginTop: 3 }}>
                          <span className={`sev-${finding.severity}`}>{finding.severity.toUpperCase()}</span>
                          {' · '}
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

                  {/* Deterministic Metrics Table */}
                  {Object.keys(finding.anomaly_metrics).length > 0 && (
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
                      <div className="table-wrap">
                        <table className="ledger-table">
                          <thead>
                            <tr>
                              <th>Metric</th>
                              <th className="num">Computed Value</th>
                            </tr>
                          </thead>
                          <tbody>
                            {Object.entries(finding.anomaly_metrics).map(([k, v]) => (
                              <tr key={k}>
                                <td style={{ textTransform: 'capitalize' }}>{k.replace(/_/g, ' ')}</td>
                                <td className="num">
                                  {v === null || v === undefined
                                    ? 'Unrecorded'
                                    : typeof v === 'number'
                                      ? k.includes('inr')
                                        ? formatInr(v)
                                        : v.toLocaleString('en-IN', { maximumFractionDigits: 3 })
                                      : typeof v === 'boolean'
                                        ? v
                                          ? 'Yes'
                                          : 'No'
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
                            <a
                              href={prov.source_landing_page}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="provenance-link"
                            >
                              <ExternalLink size={12} aria-hidden="true" />
                              <span>{prov.source_landing_page}</span>
                            </a>
                          </dd>
                        </div>
                      )}

                      {(prov?.mirror_name || mirrorUrl) && (
                        <div className="provenance-row">
                          <dt>Civic Mirror / Catalog</dt>
                          <dd>
                            {prov?.mirror_name && <span>{prov.mirror_name}</span>}
                            {prov?.mirror_name && mirrorUrl && ' · '}
                            {mirrorUrl && (
                              <a
                                href={mirrorUrl}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="provenance-link"
                              >
                                <ExternalLink size={12} aria-hidden="true" />
                                <span>{mirrorUrl}</span>
                              </a>
                            )}
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
                            <a
                              href={retrievalUrl}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="provenance-link"
                            >
                              <ExternalLink size={12} aria-hidden="true" />
                              <span>{retrievalUrl}</span>
                            </a>
                          </dd>
                        </div>
                      )}

                      {prov?.archive_url && (
                        <div className="provenance-row">
                          <dt>Archive Snapshot</dt>
                          <dd>
                            <a
                              href={prov.archive_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="provenance-link"
                            >
                              <ExternalLink size={12} aria-hidden="true" />
                              <span>{prov.archive_url}</span>
                            </a>
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

                      {prov?.secondary_sha256 && (
                        <div className="provenance-row">
                          <dt>Secondary Input SHA-256</dt>
                          <dd>
                            <code className="font-mono" style={{ fontSize: 11.5, wordBreak: 'break-all' }}>
                              {prov.secondary_sha256}
                            </code>
                          </dd>
                        </div>
                      )}

                      <div className="provenance-row">
                        <dt>Byte Size &amp; Fetch Time</dt>
                        <dd className="tabular">
                          <span>{formatBytes(prov?.byte_size)}</span>
                          {' · '}
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
                        {finding.citations.map((cit, i) => (
                          <div
                            key={i}
                            style={{
                              padding: '10px 12px',
                              background: 'var(--bg-paper)',
                              borderLeft: '2px solid var(--border-strong)',
                              fontSize: 13,
                            }}
                          >
                            <div className="font-mono" style={{ fontSize: 12, color: 'var(--ink-3)', marginBottom: 4 }}>
                              {[
                                cit.title,
                                cit.source_locator,
                                cit.page != null ? `Page ${cit.page}` : null,
                                cit.para ? `Para ${cit.para}` : null,
                                cit.row_id != null ? `Row ${cit.row_id}` : null,
                              ]
                                .filter(Boolean)
                                .join(' · ') || 'Source record excerpt'}
                            </div>
                            {cit.excerpt && (
                              <div style={{ color: 'var(--ink-primary)', fontStyle: 'italic' }}>
                                “{cit.excerpt}”
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  {/* Mandatory Benign Explanations Box (BNS Sec 356 Fairness) */}
                  <div
                    style={{
                      background: '#FBF8F1',
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
                      <span>Mandatory Benign / Lawful Explanations (Journalistic Fairness · BNS Sec 356)</span>
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
                          )}{' '}
                          · Computational reproduction confirms bytes &amp; math; editorial review confirms source meaning.
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
                          const verdictUpper = (v.verdict || 'unrecorded').replace(/_/g, ' ').toUpperCase();
                          const verdictClass =
                            v.verdict === 'confirm'
                              ? 'badge badge-verified'
                              : v.verdict === 'unrecorded' || v.verdict === 'needs_review'
                                ? 'badge badge-review'
                                : 'badge badge-disputed';
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
                                  <span className={reviewKind.isEditorial ? 'badge badge-rti' : 'tag-mono'}>
                                    {reviewKind.label}
                                  </span>
                                  <span className={providerBadgeClass(v.provider || v.model_id)}>
                                    {v.provider ? `${v.provider} / ${v.model_id}` : v.model_id}
                                  </span>
                                </div>
                                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                  <span className={verdictClass}>{verdictUpper}</span>
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
                        Awaiting independent peer verification from 2 distinct contributor nodes. Run{' '}
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
                          Lawful Citizen Action · RTI Act 2005 Section 6(1) &amp; Section 2(f)
                        </div>
                        <h3 className="font-serif" style={{ margin: 0, fontSize: 20 }}>
                          Public Records Request Draft
                        </h3>
                      </div>

                      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                        {rtiDraft?.is_preview && (
                          <span className="badge badge-review">Personal Preview (Not Persisted)</span>
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
                          border: '1px solid #E2B8B0',
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

                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
                      <input
                        type="text"
                        className="input-field"
                        style={{ flex: 1, minWidth: 220 }}
                        value={rtiAuthority}
                        onChange={(e) => setRtiAuthority(e.target.value)}
                        placeholder="Target Public Authority / Ministry (optional personal override)"
                        aria-label="Target Public Authority"
                      />
                      <button
                        type="button"
                        className="btn btn-rti"
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
                            <strong>Authority:</strong> {rtiDraft.public_authority} ·{' '}
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
                                  {rtiDraft.rti_portal_url ? (
                                    <>
                                      File via{' '}
                                      <a
                                        href={rtiDraft.rti_portal_url}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        style={{ color: 'var(--ep-rti-fg)', textDecoration: 'underline', fontWeight: 600 }}
                                      >
                                        {rtiDraft.rti_portal_url}
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
                                    href={rtiDraft.rti_portal_url || 'https://rtionline.gov.in/'}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    style={{ color: 'var(--ep-rti-fg)', textDecoration: 'underline', fontWeight: 600 }}
                                  >
                                    {rtiDraft.rti_portal_url || 'https://rtionline.gov.in/'}
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
                  {finding.verified_at ? ` · Peer-Verified ${formatRelativeTime(finding.verified_at)}` : ''}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
