import type {
  AgentRecord,
  Citation,
  ContributorProfile,
  FindingCorrection,
  FindingItem,
  FindingProvenance,
  GraphData,
  GraveyardResponse,
  HeadData,
  JustInItem,
  LiveData,
  LiveRecentJoin,
  LiveSwarmNode,
  LiveSwarmNodeStatus,
  LiveSwarmSnapshot,
  LiveSwarmWhisper,
  MissionItem,
  MissionRung,
  MissionTask,
  PaginatedFindings,
  PeerVerification,
  ProjectTaskKindFilter,
  ProjectTaskPage,
  ProjectTaskStatusFilter,
  ProjectVisualSummary,
  PublicSource,
  RecordEvent,
  RtiDraft,
  VerificationCheckState,
  WhisperItem,
  TaskUsage,
} from '../types';

const DEFAULT_TIMEOUT_MS = 15000;

const SENSITIVE_ERROR_PATTERN =
  /DATABASE_URL|POSTGRES_URL|PGPASSWORD|SECRET|TOKEN|Traceback|File\s+"|psycopg|sqlalchemy|sqlite3|ECONNREFUSED|127\.0\.0\.1|localhost:\d+/i;

function sanitizeErrorMessage(status: number | null, rawDetail: string): string {
  if (import.meta.env.DEV) {
    return rawDetail;
  }
  if (status === null) {
    return 'Unable to reach the service right now. Please try again shortly.';
  }
  if (status >= 500) {
    return `Service temporarily unavailable (HTTP ${status}). Please try again shortly.`;
  }
  if (status === 429) {
    return 'Too many requests. Please wait a moment and try again.';
  }
  const cleaned = rawDetail.replace(/[\r\n]+/g, ' ').trim();
  if (!cleaned || cleaned.length > 240 || SENSITIVE_ERROR_PATTERN.test(cleaned)) {
    return `Request could not be completed (HTTP ${status}).`;
  }
  return cleaned;
}

async function fetchWithTimeout(
  url: string,
  init?: RequestInit & { timeoutMs?: number },
): Promise<Response> {
  const { timeoutMs = DEFAULT_TIMEOUT_MS, signal: externalSignal, ...fetchInit } = init ?? {};
  const controller = new AbortController();
  let timedOut = false;

  const onExternalAbort = () => controller.abort();
  if (externalSignal) {
    if (externalSignal.aborted) {
      controller.abort();
    } else {
      externalSignal.addEventListener('abort', onExternalAbort, { once: true });
    }
  }

  const timeoutId = window.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  try {
    return await fetch(url, {
      ...fetchInit,
      signal: controller.signal,
    });
  } catch (err) {
    if (externalSignal?.aborted) {
      const abortErr = new Error('Aborted');
      abortErr.name = 'AbortError';
      throw abortErr;
    }
    if (timedOut) {
      throw new Error(
        import.meta.env.DEV
          ? `Request timed out after ${timeoutMs}ms`
          : 'Request timed out. Please try again.',
      );
    }
    const rawMsg = err instanceof Error ? err.message : 'Network error';
    throw new Error(sanitizeErrorMessage(null, rawMsg));
  } finally {
    window.clearTimeout(timeoutId);
    if (externalSignal) {
      externalSignal.removeEventListener('abort', onExternalAbort);
    }
  }
}

async function fetchJson<T>(
  url: string,
  init?: RequestInit & { timeoutMs?: number },
): Promise<T> {
  const res = await fetchWithTimeout(url, {
    ...init,
    headers: { Accept: 'application/json', ...(init?.headers || {}) },
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    if (import.meta.env.DEV || res.status < 500) {
      try {
        const body = await res.json();
        detail = body?.detail
          ? typeof body.detail === 'string'
            ? body.detail
            : JSON.stringify(body.detail)
          : detail;
      } catch {
        // ignore non-json error body
      }
    }
    throw new Error(sanitizeErrorMessage(res.status, detail));
  }
  return res.json() as Promise<T>;
}

function asArray<T>(val: unknown): T[] {
  if (Array.isArray(val)) return val as T[];
  if (typeof val === 'string') {
    try {
      const parsed = JSON.parse(val);
      return Array.isArray(parsed) ? (parsed as T[]) : [];
    } catch {
      return [];
    }
  }
  return [];
}

function asRecord(val: unknown): Record<string, unknown> {
  if (val && typeof val === 'object' && !Array.isArray(val)) return val as Record<string, unknown>;
  if (typeof val === 'string') {
    try {
      const parsed = JSON.parse(val);
      return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
        ? (parsed as Record<string, unknown>)
        : {};
    } catch {
      return {};
    }
  }
  return {};
}

function optNum(val: unknown): number | null {
  if (val === null || val === undefined || val === '') return null;
  const n = Number(val);
  return Number.isFinite(n) ? n : null;
}

function optStr(val: unknown): string | null {
  if (val === null || val === undefined) return null;
  if (typeof val === 'object') return null;
  const s = String(val).trim();
  return s.length > 0 ? s : null;
}

function posNum(...vals: unknown[]): number | null {
  for (const v of vals) {
    const n = optNum(v);
    if (n != null && n > 0) return n;
  }
  return null;
}

function formatLocator(val: unknown): string | null {
  if (val === null || val === undefined) return null;
  if (typeof val === 'string') {
    const trimmed = val.trim();
    if (!trimmed || trimmed === '{}') return null;
    if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
      try {
        const parsed = JSON.parse(trimmed);
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
          return formatLocator(parsed);
        }
      } catch {
        // fall through to raw string
      }
    }
    return trimmed;
  }
  if (typeof val === 'object' && !Array.isArray(val)) {
    const entries = Object.entries(val as Record<string, unknown>).filter(
      ([, v]) => v !== null && v !== undefined && String(v).trim() !== '',
    );
    if (entries.length === 0) return null;
    return entries
      .map(([k, v]) => `${k.replace(/_/g, ' ')}: ${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
      .join(', ');
  }
  const s = String(val).trim();
  return s.length > 0 ? s : null;
}

function toCheckState(val: unknown): VerificationCheckState {
  if (val === true) return 'pass';
  if (val === false) return 'fail';
  if (typeof val === 'string') {
    const lower = val.trim().toLowerCase();
    if (lower === 'pass' || lower === 'passed' || lower === 'true' || lower === 'ok') return 'pass';
    if (lower === 'fail' || lower === 'failed' || lower === 'false') return 'fail';
  }
  return 'unrecorded';
}

export function normalizeAgent(raw: Record<string, unknown>): AgentRecord {
  const rawPts36h = asArray<unknown>(raw.hourly_points_36h);
  const rawChk36h = asArray<unknown>(raw.hourly_checks_36h);
  return {
    id: raw.id as string | number | undefined,
    handle: String(raw.handle || 'unknown-node'),
    display_name: raw.display_name ? String(raw.display_name) : undefined,
    provider: String(raw.provider || 'rule-engine'),
    model: String(raw.model || 'deterministic-v1'),
    coding_agent: optStr(raw.coding_agent) ?? undefined,
    coding_agent_model: optStr(raw.coding_agent_model) ?? undefined,
    mode: raw.mode ? String(raw.mode) : 'auto',
    region: raw.region ? String(raw.region) : 'IN',
    status: raw.status ? String(raw.status) : 'active',
    is_active: raw.is_active !== undefined ? Boolean(raw.is_active) : raw.status === 'active',
    heartbeat_age_seconds:
      raw.heartbeat_age_seconds !== undefined ? Number(raw.heartbeat_age_seconds) : undefined,
    last_heartbeat: String(raw.last_heartbeat_at || raw.last_heartbeat || raw.joined_at || ''),
    joined_at: String(raw.joined_at || ''),
    tasks_completed: Number(raw.tasks_completed ?? 0),
    findings_submitted: Number(raw.findings_submitted ?? 0),
    findings_verified: Number(raw.findings_verified ?? 0),
    verifications_completed: Number(raw.verifications_done ?? raw.verifications_completed ?? 0),
    cleared_records: Number(raw.cleared_submitted ?? raw.cleared_records ?? 0),
    score: Number(raw.score ?? raw.compute_score ?? raw.hourly_score ?? 0),
    hourly_score: optNum(raw.hourly_score) ?? undefined,
    score_10m: optNum(raw.score_10m) ?? undefined,
    score_1h: optNum(raw.score_1h) ?? optNum(raw.hourly_score) ?? undefined,
    checks_10m: optNum(raw.checks_10m) ?? undefined,
    checks_1h: optNum(raw.checks_1h) ?? undefined,
    checks_total: optNum(raw.checks_total) ?? optNum(raw.tasks_completed) ?? undefined,
    hourly_points_36h:
      rawPts36h.length > 0 ? rawPts36h.map((v) => Math.max(0, Number(v) || 0)) : undefined,
    hourly_checks_36h:
      rawChk36h.length > 0 ? rawChk36h.map((v) => Math.max(0, Number(v) || 0)) : undefined,
    current_task: (raw.current_task || raw.current_task_title || raw.current_task_id || null) as string | null,
  };
}

export function normalizeVerification(raw: Record<string, unknown>): PeerVerification {
  const checks = asRecord(raw.checks ?? raw.checks_json);
  const rawVerdict = optStr(raw.verdict);
  const confidence = optNum(raw.confidence);
  const rawKind = optStr(raw.review_type ?? raw.review_kind ?? raw.verification_kind ?? raw.kind);

  return {
    id: raw.id as string | number | undefined,
    finding_id: raw.finding_id as string | number | undefined,
    verifier_handle: String(raw.verifier_handle || raw.contributor_id || raw.handle || 'peer-node'),
    coding_agent: optStr(raw.coding_agent) ?? undefined,
    coding_agent_model: optStr(raw.coding_agent_model) ?? undefined,
    provider: raw.provider ? String(raw.provider) : undefined,
    model_id: String(raw.model_id || raw.model || 'deterministic-v1'),
    model: raw.model ? String(raw.model) : raw.model_id ? String(raw.model_id) : undefined,
    verdict: rawVerdict ?? 'unrecorded',
    review_kind: rawKind ?? 'computational_reproduction',
    checks: {
      provenance_ok: toCheckState(checks.bytes_digest ?? checks.provenance_ok),
      math_reproduced: toCheckState(checks.deterministic_reproduction ?? checks.math_reproduced),
      rule_valid: toCheckState(checks.metric_tolerance ?? checks.rule_valid),
      legal_pii_ok: toCheckState(checks.neutrality_privacy ?? checks.legal_pii_ok),
    },
    confidence,
    notes: raw.notes ? String(raw.notes) : '',
    source_locator: optStr(raw.source_locator),
    engine_version: optStr(raw.engine_version),
    ruleset_version: optStr(raw.ruleset_version),
    created_at: raw.created_at ? String(raw.created_at) : undefined,
    finding_title: raw.finding_title ? String(raw.finding_title) : undefined,
    methodology_code: raw.methodology_code ? String(raw.methodology_code) : undefined,
    finding_status: raw.finding_status ? String(raw.finding_status) : undefined,
  };
}

export function normalizeRtiDraft(raw: Record<string, unknown> | null | undefined): RtiDraft | null {
  if (!raw || typeof raw !== 'object') return null;
  if (!raw.body_text && !raw.subject) return null;
  return {
    id: raw.id as string | number | undefined,
    finding_id: raw.finding_id as string | number | undefined,
    public_authority: String(raw.public_authority || 'Public Information Officer'),
    cpio_designation: String(raw.cpio_designation || 'Public Information Officer (PIO / CPIO)'),
    rti_portal_url: String(raw.rti_portal_url || ''),
    jurisdiction: optStr(raw.jurisdiction) ?? undefined,
    jurisdiction_level: optStr(raw.jurisdiction_level) ?? undefined,
    fee_note: optStr(raw.fee_note ?? raw.fee_rules ?? raw.fee_instruction) ?? undefined,
    filing_channel: optStr(raw.filing_channel) ?? undefined,
    statutory_sections: asArray<string>(raw.statutory_sections ?? raw.statutory_sections_json),
    subject: String(raw.subject || 'Application under Section 6(1) of the RTI Act, 2005'),
    body_text: String(raw.body_text || ''),
    word_count: Number(
      raw.word_count ?? String(raw.body_text || '').trim().split(/\s+/).filter(Boolean).length,
    ),
    requested_records: asArray<string>(raw.requested_records ?? raw.requested_records_json),
    status: raw.status ? String(raw.status) : 'draft',
    editorial_warning: optStr(raw.editorial_notice ?? raw.editorial_warning ?? raw.correction_notice),
    is_preview: raw.is_preview !== undefined ? Boolean(raw.is_preview) : undefined,
    persisted: raw.persisted !== undefined ? Boolean(raw.persisted) : undefined,
    version: optNum(raw.version) ?? undefined,
    created_at: raw.created_at ? String(raw.created_at) : undefined,
  };
}

function normalizeCorrection(raw: Record<string, unknown>): FindingCorrection {
  return {
    id: raw.id as string | number | undefined,
    created_at: optStr(raw.created_at ?? raw.recorded_at ?? raw.timestamp) ?? undefined,
    reviewer: optStr(raw.reviewed_by ?? raw.reviewer ?? raw.editor ?? raw.actor ?? raw.recorded_by) ?? undefined,
    correction_type: optStr(raw.correction_type) ?? undefined,
    editorial_status: optStr(raw.editorial_status ?? raw.disposition) ?? undefined,
    previous_status: optStr(raw.previous_status ?? raw.from_status) ?? undefined,
    new_status: optStr(raw.new_status ?? raw.to_status) ?? undefined,
    summary: String(raw.summary || raw.reason || raw.note || raw.description || 'Editorial review notice recorded.'),
    source_discrepancy: optStr(raw.details ?? raw.source_discrepancy ?? raw.discrepancy) ?? undefined,
    source_evidence_url: optStr(raw.source_evidence_url) ?? undefined,
    rti_notice: optStr(raw.rti_notice ?? raw.rti_impact) ?? undefined,
  };
}

function normalizeProvenance(raw: Record<string, unknown>, citations: Citation[]): FindingProvenance {
  const prov = asRecord(raw.provenance);
  const art = asRecord(raw.artifact ?? raw.artifact_provenance);
  const trace = asRecord(raw.agent_trace ?? raw.agent_trace_json);
  const firstCit = citations[0];
  return {
    official_publisher: optStr(
      prov.official_publisher ??
        art.official_publisher ??
        raw.official_publisher ??
        firstCit?.official_publisher,
    ),
    source_landing_page: optStr(
      prov.source_landing_page ??
        art.source_landing_page ??
        raw.source_landing_page ??
        firstCit?.source_landing_page,
    ),
    mirror_name: optStr(
      prov.mirror_name ?? art.mirror_name ?? raw.mirror_name ?? firstCit?.mirror_name,
    ),
    mirror_url: optStr(
      prov.mirror_catalog_url ??
        prov.mirror_url ??
        art.mirror_catalog_url ??
        art.mirror_url ??
        raw.mirror_catalog_url ??
        raw.mirror_url,
    ),
    retrieval_url: optStr(
      prov.retrieval_url ??
        prov.canonical_url ??
        prov.artifact_url ??
        art.retrieval_url ??
        art.canonical_url ??
        art.artifact_url ??
        raw.retrieval_url ??
        raw.artifact_url ??
        firstCit?.retrieval_url ??
        raw.source_url ??
        raw.canonical_url ??
        firstCit?.source_url,
    ),
    archive_url: optStr(prov.archive_url ?? art.archive_url ?? raw.archive_url),
    observed_sha256: optStr(
      prov.sha256 ??
        prov.observed_sha256 ??
        art.sha256 ??
        art.observed_sha256 ??
        raw.observed_sha256 ??
        raw.artifact_sha256 ??
        firstCit?.sha256,
    ),
    secondary_url: optStr(prov.secondary_url ?? art.secondary_url ?? raw.secondary_url),
    secondary_sha256: optStr(
      prov.secondary_sha256 ?? art.secondary_sha256 ?? raw.secondary_sha256 ?? trace.secondary_sha256,
    ),
    byte_size: posNum(
      prov.byte_size,
      prov.size_bytes,
      art.byte_size,
      art.size_bytes,
      raw.artifact_byte_size,
      raw.byte_size,
    ),
    fetched_at: optStr(
      prov.fetched_at ?? art.fetched_at ?? raw.artifact_fetched_at ?? raw.fetched_at,
    ),
    source_locator:
      formatLocator(prov.source_locator) ??
      formatLocator(art.source_locator) ??
      formatLocator(raw.source_locator) ??
      formatLocator(firstCit?.source_locator),
    engine_version: optStr(
      prov.engine_version ?? art.engine_version ?? raw.engine_version ?? trace.engine_version,
    ),
    ruleset_version: optStr(
      prov.ruleset_version ?? art.ruleset_version ?? raw.ruleset_version ?? trace.ruleset_version,
    ),
    as_of_date: optStr(
      prov.as_of_date ?? art.as_of_date ?? raw.as_of_date ?? trace.as_of_date,
    ),
  };
}

export function normalizeFinding(raw: Record<string, unknown>): FindingItem {
  const citations = asArray<Record<string, unknown>>(raw.citations ?? raw.citations_json).map(
    (c): Citation => ({
      artifact_id: c.artifact_id as string | number | undefined,
      source_url: c.source_url ? String(c.source_url) : undefined,
      retrieval_url: c.retrieval_url ? String(c.retrieval_url) : undefined,
      official_publisher: c.official_publisher ? String(c.official_publisher) : undefined,
      source_landing_page: c.source_landing_page ? String(c.source_landing_page) : undefined,
      mirror_name: c.mirror_name ? String(c.mirror_name) : undefined,
      title: c.title ? String(c.title) : undefined,
      sha256: c.sha256 ? String(c.sha256) : undefined,
      page: (c.page ?? null) as string | number | null,
      para: (c.para ?? c.para_ref ?? null) as string | null,
      row_id: (c.row_id ?? null) as string | number | null,
      source_locator: formatLocator(c.source_locator),
      excerpt: String(c.excerpt || c.raw_excerpt || ''),
    }),
  );
  const trace = asRecord(raw.agent_trace ?? raw.agent_trace_json);
  const verifications = asArray<Record<string, unknown>>(raw.verifications ?? raw.peer_verifications).map(
    normalizeVerification,
  );
  const corrections = asArray<Record<string, unknown>>(
    raw.corrections ?? raw.finding_corrections ?? raw.correction_history,
  ).map(normalizeCorrection);
  const rti = normalizeRtiDraft((raw.rti_draft ?? raw.rti) as Record<string, unknown> | null);
  const provenance = normalizeProvenance(raw, citations);
  const statusStr = String(raw.status || 'peer_review');
  const quarantined = Boolean(raw.quarantined || statusStr === 'quarantined');

  const rawEditorial = optStr(raw.editorial_status);
  const editorialStatus = rawEditorial && rawEditorial !== 'none' ? rawEditorial : null;
  const repro = asRecord(raw.reproduction_summary);
  const reproSummary =
    repro.computational_confirm_count !== undefined || repro.editorial_review_count !== undefined
      ? {
          computational_confirm_count: Number(repro.computational_confirm_count ?? 0),
          editorial_review_count: Number(repro.editorial_review_count ?? 0),
          required_quorum: Number(repro.required_quorum ?? raw.required_quorum ?? 2),
        }
      : null;

  return {
    id: (raw.id ?? '') as string | number,
    mission_id: raw.mission_id as string | number | undefined,
    mission_slug: raw.mission_slug ? String(raw.mission_slug) : undefined,
    mission_title: raw.mission_title ? String(raw.mission_title) : undefined,
    pillar: raw.pillar ? String(raw.pillar) : undefined,
    title: String(raw.title || 'Public Record Audit Lead'),
    summary: String(raw.summary || ''),
    editorial_interpretation: optStr(raw.editorial_interpretation),
    methodology_code: String(raw.methodology_code || 'P1'),
    entity_name: String(raw.entity_name || 'Public Authority'),
    jurisdiction: String(raw.jurisdiction || 'Union'),
    fiscal_year: String(raw.fiscal_year || 'Unrecorded'),
    fiscal_year_start: optStr(raw.fiscal_year_start),
    fiscal_year_end: optStr(raw.fiscal_year_end),
    monetary_exposure_inr:
      raw.monetary_exposure_inr != null ? Number(raw.monetary_exposure_inr) : null,
    amount_label: optStr(raw.amount_label ?? raw.amount_type),
    severity: String(raw.severity || 'medium'),
    status: statusStr,
    editorial_status: editorialStatus,
    quarantined,
    quarantine_reason: optStr(raw.quarantine_notice ?? raw.quarantine_reason),
    correction_count: optNum(raw.correction_count) ?? corrections.length,
    has_corrections: Boolean(raw.has_corrections || corrections.length > 0 || editorialStatus),
    corrections,
    reproduction_summary: reproSummary,
    rule_triggered: String(raw.rule_triggered || ''),
    anomaly_metrics: quarantined ? {} : asRecord(raw.anomaly_metrics ?? raw.anomaly_metrics_json),
    citations: quarantined ? [] : citations,
    benign_explanations: asArray<string>(raw.benign_explanations ?? raw.benign_explanations_json),
    source_url: String(provenance.retrieval_url || raw.source_url || raw.canonical_url || citations[0]?.source_url || ''),
    artifact_sha256: String(provenance.observed_sha256 || raw.artifact_sha256 || citations[0]?.sha256 || ''),
    artifact_title: raw.artifact_title ? String(raw.artifact_title) : undefined,
    provenance,
    is_demo_sample: Boolean(raw.is_demo_sample),
    legal_lint_passed: raw.legal_lint_passed !== undefined ? Boolean(raw.legal_lint_passed) : undefined,
    submitted_coding_agent: optStr(raw.submitted_coding_agent) ?? undefined,
    submitted_coding_agent_model: optStr(raw.submitted_coding_agent_model) ?? undefined,
    submitted_by: raw.submitted_by
      ? String(raw.submitted_by)
      : trace.node_id
        ? String(trace.node_id)
        : undefined,
    submitted_model: raw.submitted_model
      ? String(raw.submitted_model)
      : trace.model_id
        ? String(trace.model_id)
        : undefined,
    verifier_script_hash: String(raw.verifier_script_hash || trace.verifier_script_hash || trace.script_hash || ''),
    verification_score: Number(raw.verification_score ?? 0),
    confirm_count: Number(raw.confirm_count ?? verifications.filter((v) => v.verdict === 'confirm').length),
    required_quorum: Number(raw.required_quorum ?? 2),
    verifications,
    has_rti_draft: Boolean(raw.has_rti_draft || rti),
    rti_draft: rti,
    created_at: raw.created_at ? String(raw.created_at) : undefined,
    verified_at: raw.verified_at ? String(raw.verified_at) : null,
  };
}

export function normalizeRung(raw: Record<string, unknown>): MissionRung {
  return {
    id: (raw.id ?? '') as string | number,
    state: String(raw.state || 'queued'),
    label: String(raw.label || ''),
    kind: String(raw.kind || 'task'),
    finding_id: (raw.finding_id ?? undefined) as string | number | undefined,
  };
}

export function normalizeMission(raw: Record<string, unknown>): MissionItem {
  const monetaryInr = Number(raw.monetary_audited_inr ?? 0);
  const monetaryCr =
    raw.monetary_audited_inr_cr !== undefined
      ? Number(raw.monetary_audited_inr_cr)
      : monetaryInr > 0
        ? monetaryInr / 1e7
        : undefined;
  const workloadCr = optNum(raw.scan_workload_inr_crore ?? raw.scan_workload_inr_cr) ?? monetaryCr;
  const uniqueCr = optNum(raw.unique_coverage_inr_crore ?? raw.unique_coverage_inr_cr);
  const slug = String(raw.slug || '');
  const methodologyCode = String(raw.methodology_code || 'P1');
  const explicitProjectCode = optStr(raw.project_code);
  const fallbackProjectCode =
    slug === 'police-finances'
      ? 'P1'
      : slug === 'gov-in-public-information' || methodologyCode.toUpperCase() === 'W1'
        ? 'P2'
        : slug === 'union-budget-unspent-funds' || methodologyCode.toUpperCase() === 'UB1'
          ? 'P3'
          : slug === 'one-road-two-bills' || methodologyCode.toUpperCase() === 'OR1'
            ? 'P4'
            : slug === 'one-teacher-five-grades' || methodologyCode.toUpperCase() === 'OT1'
              ? 'P5'
              : methodologyCode;
  return {
    id: raw.id as string | number | undefined,
    slug,
    project_code: explicitProjectCode || fallbackProjectCode,
    title: String(raw.title || 'Audit project'),
    description: raw.description ? String(raw.description) : undefined,
    pillar: String(raw.pillar || 'budget'),
    methodology_code: methodologyCode,
    jurisdiction: String(raw.jurisdiction || 'Union'),
    statutory_anchors: asArray<string>(raw.statutory_anchors ?? raw.statutory_anchors_json),
    data_mode: String(raw.data_mode || 'live_public'),
    is_demo_sample: Boolean(raw.is_demo_sample),
    status: String(raw.status || 'active'),
    priority: Number(raw.priority ?? 1),
    total_tasks: Number(raw.total_tasks ?? 0),
    completed_tasks: Number(raw.completed_tasks ?? 0),
    claimed_tasks: raw.claimed_tasks !== undefined ? Number(raw.claimed_tasks) : undefined,
    queued_tasks: raw.queued_tasks !== undefined ? Number(raw.queued_tasks) : undefined,
    peer_checks_total: optNum(raw.peer_checks_total) ?? undefined,
    peer_checks_done: optNum(raw.peer_checks_done) ?? undefined,
    peer_checks_open: optNum(raw.peer_checks_open) ?? undefined,
    progress_pct: raw.progress_pct !== undefined ? Number(raw.progress_pct) : undefined,
    verified_findings_count: Number(raw.verified_findings_count ?? 0),
    peer_review_count: raw.peer_review_count !== undefined ? Number(raw.peer_review_count) : undefined,
    cleared_count: Number(raw.cleared_findings_count ?? raw.cleared_count ?? 0),
    cleared_tasks_count: optNum(raw.cleared_scans_count ?? raw.cleared_tasks_count) ?? undefined,
    unevaluable_groups_count: optNum(raw.unevaluable_count ?? raw.unevaluable_groups_count) ?? undefined,
    monetary_audited_inr_cr: monetaryCr,
    scan_workload_inr_cr: workloadCr,
    unique_coverage_inr_cr: uniqueCr,
    amount_unit_note: optStr(
      raw.coverage_note ??
        raw.financial_quantity_label ??
        raw.amount_unit_note ??
        raw.coverage_unit_note ??
        raw.amount_basis,
    ),
    is_community_project: Boolean(raw.is_community_project),
    posted_by: (() => {
      const p = asRecord(raw.posted_by);
      const name = String(p.name || '').trim();
      return name ? { name, handle: String(p.handle || ''), url: String(p.url || '') } : undefined;
    })(),
    project_sources: asArray<Record<string, unknown>>(raw.project_sources)
      .map((s) => ({ name: String(s.name || ''), publisher: s.publisher ? String(s.publisher) : undefined, url: String(s.url || '') }))
      .filter((s) => s.name && s.url),
    project_scope: (() => {
      const sc = asRecord(raw.project_scope);
      return { scope: asArray<string>(sc.scope), out_of_scope: asArray<string>(sc.out_of_scope) };
    })(),
    enlisted_pr_url: optStr(raw.enlisted_pr_url) ?? undefined,
    planned_tasks: asArray<Record<string, unknown>>(raw.planned_tasks).map((p) => ({
      title: String(p.title || ''),
      type: String(p.type || ''),
      estimated_tokens: Number(p.estimated_tokens ?? 0),
      estimate_basis: optStr(p.estimate_basis) ?? undefined,
    })),
    token_summary: raw.token_summary
      ? (() => {
          const t = asRecord(raw.token_summary);
          return {
            estimated_tokens: optNum(t.estimated_tokens) ?? undefined,
            remaining_tokens: optNum(t.remaining_tokens) ?? undefined,
            tokens_used: optNum(t.tokens_used) ?? undefined,
            reported_tasks: Number(t.reported_tasks ?? 0),
            completed_tasks: Number(t.completed_tasks ?? 0),
          };
        })()
      : undefined,
    rungs: raw.rungs ? asArray<Record<string, unknown>>(raw.rungs).map(normalizeRung) : undefined,
    sources: raw.sources ? asArray<Record<string, unknown>>(raw.sources).map(normalizeSource) : undefined,
    findings: raw.findings ? asArray<Record<string, unknown>>(raw.findings).map(normalizeFinding) : undefined,
    tasks: raw.tasks ? asArray<Record<string, unknown>>(raw.tasks).map(normalizeTask) : undefined,
    tasks_listed: optNum(raw.tasks_listed) ?? undefined,
    tasks_truncated: raw.tasks_truncated !== undefined ? Boolean(raw.tasks_truncated) : undefined,
    contributors_all: raw.contributors_all
      ? asArray<Record<string, unknown>>(raw.contributors_all).map(normalizeAgent)
      : undefined,
    contributors_hour: raw.contributors_hour
      ? asArray<Record<string, unknown>>(raw.contributors_hour).map(normalizeAgent)
      : undefined,
    visual_summary:
      raw.visual_summary && typeof raw.visual_summary === 'object' && !Array.isArray(raw.visual_summary)
        ? (raw.visual_summary as ProjectVisualSummary)
        : undefined,
  };
}

function normalizeTask(t: Record<string, unknown>): MissionTask {
  return {
    id: (t.id ?? '') as string | number,
    retrieval_url: optStr(t.retrieval_url) ?? undefined,
    task_type: String(t.task_type || 'anomaly_scan'),
    role: optStr(t.role) ?? (String(t.task_type) === 'peer_verify' ? 'peer_verifier' : 'auditor'),
    title: t.title ? String(t.title) : undefined,
    status: String(t.status || 'queued'),
    claimed_by: (t.claimed_by_handle || t.claimed_by || null) as string | null,
    claimed_by_handle: (t.claimed_by_handle || null) as string | null,
    attempt_count: Number(t.attempt_count ?? 0),
    created_at: t.created_at ? String(t.created_at) : undefined,
    work_type: optStr(t.work_type) ?? undefined,
    estimated_tokens: optNum(t.estimated_tokens) ?? undefined,
    estimate_basis: optStr(t.estimate_basis) ?? undefined,
    usage: t.usage ? normalizeUsage(asRecord(t.usage)) : undefined,
  };
}

function normalizeUsage(u: Record<string, unknown>): TaskUsage {
  const num = (k: string) => optNum(u[k]) ?? undefined;
  return {
    tokens_used: num('tokens_used'),
    agent_tokens: num('agent_tokens'),
    check_tokens: num('check_tokens'),
    iterations: num('iterations'),
    tool_calls: num('tool_calls'),
    agent_seconds: num('agent_seconds'),
    hub_seconds: num('hub_seconds'),
    split_across: num('split_across'),
  };
}

export function normalizeSource(raw: Record<string, unknown>): PublicSource {
  return {
    id: raw.id as string | number | undefined,
    slug: String(raw.slug || ''),
    name: String(raw.name || ''),
    pillar: String(raw.pillar || 'budget'),
    authority: String(raw.authority || ''),
    official_publisher: optStr(raw.official_publisher) ?? undefined,
    source_landing_page: optStr(raw.source_landing_page) ?? undefined,
    mirror_name: optStr(raw.mirror_name) ?? undefined,
    base_url: String(raw.base_url || raw.retrieval_url || ''),
    ingestion_tier: Number(raw.ingestion_tier ?? 1),
    connector_type: String(raw.connector_type || 'http'),
    robots_policy: raw.robots_policy ? String(raw.robots_policy) : 'strict-polite',
    notes: String(raw.data_contents || raw.format_access || raw.notes || ''),
    last_synced_at: raw.last_synced_at ? String(raw.last_synced_at) : null,
  };
}

export function normalizeWhisper(raw: Record<string, unknown>): WhisperItem {
  const numId = optNum(raw.event_id ?? raw.seq_id ?? (typeof raw.id === 'number' ? raw.id : null));
  return {
    id: raw.id as string | number | undefined,
    event_id: numId ?? undefined,
    agent_handle: String(raw.agent_handle || raw.handle || raw.agent || 'swarm-node'),
    provider: raw.provider ? String(raw.provider) : undefined,
    model: raw.model ? String(raw.model) : undefined,
    event_type: raw.event_type ? String(raw.event_type) : String(raw.type || 'whisper'),
    message: String(raw.message || raw.text || raw.whisper || ''),
    mission_slug: (raw.mission_slug || null) as string | null,
    finding_id: (raw.finding_id || null) as string | number | null,
    created_at: String(raw.created_at || raw.timestamp || new Date().toISOString()),
  };
}

function normalizeJustIn(raw: unknown): JustInItem | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  if (!r.id && !r.title) return null;
  return {
    id: (r.id ?? '') as string | number,
    title: String(r.title || ''),
    methodology_code: String(r.methodology_code || 'P1'),
    entity_name: String(r.entity_name || ''),
    monetary_exposure_inr_cr: Number(r.monetary_exposure_inr_cr ?? 0),
    verified_at: String(r.verified_at || ''),
    is_demo_sample: Boolean(r.is_demo_sample),
  };
}

function normalizeRecordEvent(raw: Record<string, unknown>): RecordEvent {
  return {
    id: String(raw.id || ''),
    seq_id: optNum(raw.seq_id ?? raw.event_id) ?? undefined,
    ts: String(raw.ts || ''),
    kind: String(raw.kind || 'peer_review'),
    tag: String(raw.tag || 'Record'),
    title: String(raw.title || ''),
    actor: raw.actor ? String(raw.actor) : null,
    methodology_code: raw.methodology_code ? String(raw.methodology_code) : undefined,
    finding_id: (raw.finding_id ?? null) as string | number | null,
    monetary_exposure_inr_cr:
      raw.monetary_exposure_inr_cr !== undefined ? Number(raw.monetary_exposure_inr_cr) : undefined,
    is_demo_sample: Boolean(raw.is_demo_sample),
  };
}

export const api = {
  async getHead(): Promise<HeadData> {
    const raw = await fetchJson<Record<string, unknown>>('/api/head');
    const stats = asRecord(raw.stats);
    const disp = asRecord(raw.disposition);

    const uniqueCoverage = optNum(
      stats.unique_coverage_inr_cr ?? stats.unique_coverage_inr_crore,
    );
    const scanWorkload =
      optNum(stats.scan_workload_inr_cr ?? stats.scan_workload_inr_crore) ??
      optNum(stats.public_spend_audited_inr_cr);

    return {
      generated_at: String(raw.generated_at || new Date().toISOString()),
      started_at: raw.started_at ? String(raw.started_at) : undefined,
      latest_event_id: optNum(raw.latest_event_id ?? raw.last_event_id) ?? undefined,
      orchestrator_brief: String(
        raw.orchestrator_brief ||
          'Distributed deterministic audit swarm checking published Indian government budgets and spending records under strict neutrality and 2-of-2 independent verification.',
      ),
      stats: {
        public_spend_audited_inr_cr: uniqueCoverage,
        unique_coverage_inr_cr: uniqueCoverage,
        unique_coverage_withheld: Boolean(stats.unique_coverage_withheld || uniqueCoverage == null),
        scan_workload_inr_cr: scanWorkload,
        coverage_unit_note: optStr(stats.coverage_unit_note),
        coverage_withheld_reason: optStr(stats.unique_coverage_reason ?? stats.coverage_withheld_reason),
        coverage_by_quantity_type: asArray<Record<string, unknown>>(stats.coverage_by_quantity_type).map(
          (b) => ({
            quantity_type: String(b.quantity_type || ''),
            label: String(b.label || ''),
            unique_datasets: Number(b.unique_datasets ?? 0),
            deduplicated_inr: Number(b.deduplicated_inr ?? 0),
            deduplicated_inr_cr: Number(b.deduplicated_inr_cr ?? 0),
            scan_workload_inr_cr: Number(b.scan_workload_inr_cr ?? 0),
            missions: asArray<string>(b.missions),
          }),
        ),
        tasks_completed: Number(stats.tasks_completed ?? 0),
        tasks_queued: Number(stats.tasks_queued ?? 0),
        agents_active: Number(stats.agents_active ?? 0),
        agents_total: Number(stats.agents_total ?? 0),
        findings_verified: Number(stats.findings_verified ?? 0),
        findings_peer_review: Number(stats.findings_peer_review ?? 0),
        findings_cleared: Number(stats.findings_cleared ?? 0),
        cleared_tasks_count: optNum(stats.cleared_scans ?? stats.cleared_tasks_count ?? disp.cleared_scans) ?? undefined,
        unevaluable_groups_count:
          optNum(stats.unevaluable_groups ?? stats.unevaluable_groups_count ?? disp.unevaluable_groups) ?? undefined,
        findings_disputed: optNum(stats.findings_disputed ?? disp.disputed) ?? undefined,
        findings_under_review: optNum(stats.findings_under_review ?? disp.under_review) ?? undefined,
        findings_retracted: optNum(stats.findings_retracted ?? disp.retracted) ?? undefined,
        findings_corrected: optNum(stats.findings_corrected ?? disp.corrected) ?? undefined,
        findings_quarantined: optNum(stats.findings_quarantined ?? disp.quarantined) ?? undefined,
        rtis_drafted: Number(stats.rtis_drafted ?? 0),
      },
      disposition: {
        verified_leads: Number(disp.verified_leads ?? stats.findings_verified ?? 0),
        peer_review: Number(disp.peer_review ?? stats.findings_peer_review ?? 0),
        cleared_records: Number(disp.cleared_records ?? stats.findings_cleared ?? 0),
        cleared_tasks: optNum(disp.cleared_scans ?? disp.cleared_tasks ?? stats.cleared_scans) ?? undefined,
        unevaluable_groups:
          optNum(disp.unevaluable_groups ?? stats.unevaluable_groups) ?? undefined,
        disputed: Number(disp.disputed ?? stats.findings_disputed ?? 0),
        under_editorial_review: optNum(disp.under_review ?? disp.under_editorial_review ?? stats.findings_under_review) ?? undefined,
        corrected: optNum(disp.corrected ?? stats.findings_corrected) ?? undefined,
        quarantined: optNum(disp.quarantined ?? stats.findings_quarantined) ?? undefined,
      },
      just_in: normalizeJustIn(raw.just_in),
      just_joined: asArray<Record<string, unknown>>(raw.just_joined).map((j) => ({
        handle: String(j.handle || ''),
        display_name: j.display_name ? String(j.display_name) : undefined,
        provider: String(j.provider || ''),
        model: String(j.model || ''),
        joined_at: String(j.joined_at || ''),
      })),
      active_agents: asArray<Record<string, unknown>>(raw.active_agents).map(normalizeAgent),
      missions: asArray<Record<string, unknown>>(raw.missions).map(normalizeMission),
      recent_findings: asArray<Record<string, unknown>>(raw.recent_findings).map(normalizeFinding),
      recent_verifications: asArray<Record<string, unknown>>(raw.recent_verifications).map(
        normalizeVerification,
      ),
      whispers: asArray<Record<string, unknown>>(raw.whispers).map(normalizeWhisper),
      record_events: asArray<Record<string, unknown>>(raw.record_events).map(normalizeRecordEvent),
      top_contributors: asArray<Record<string, unknown>>(raw.top_contributors).map(normalizeAgent),
    };
  },

  async getMissions(): Promise<MissionItem[]> {
    const raw = await fetchJson<unknown>('/api/missions');
    const list = Array.isArray(raw)
      ? raw
      : asArray<Record<string, unknown>>(asRecord(raw).missions ?? asRecord(raw).items);
    return list.map((m) => normalizeMission(asRecord(m)));
  },

  async getMissionDetail(slug: string): Promise<MissionItem> {
    const raw = await fetchJson<Record<string, unknown>>(`/api/missions/${encodeURIComponent(slug)}`);
    if (raw.mission && typeof raw.mission === 'object') {
      const m = raw.mission as Record<string, unknown>;
      return normalizeMission({
        ...m,
        sources: raw.sources ?? m.sources,
        findings: raw.findings ?? m.findings,
        tasks: raw.tasks ?? m.tasks,
        tasks_listed: raw.tasks_listed ?? m.tasks_listed,
        tasks_truncated: raw.tasks_truncated ?? m.tasks_truncated,
        contributors_all: raw.contributors_all ?? m.contributors_all,
        contributors_hour: raw.contributors_hour ?? m.contributors_hour,
        visual_summary: raw.visual_summary ?? m.visual_summary,
      });
    }
    return normalizeMission(raw);
  },

  /** One server-side page of a project's tasks (`limit` is capped at 100 by the hub). */
  async getProjectTasks(
    slug: string,
    params: {
      status?: ProjectTaskStatusFilter;
      q?: string;
      kind?: ProjectTaskKindFilter;
      offset?: number;
      limit?: number;
    } = {},
    signal?: AbortSignal,
  ): Promise<ProjectTaskPage> {
    const sp = new URLSearchParams();
    sp.set('status', params.status ?? 'all');
    const q = (params.q ?? '').trim();
    if (q) sp.set('q', q);
    sp.set('kind', params.kind ?? 'all');
    sp.set('offset', String(Math.max(0, params.offset ?? 0)));
    sp.set('limit', String(Math.min(100, Math.max(1, params.limit ?? 25))));
    const raw = await fetchJson<Record<string, unknown>>(
      `/api/missions/${encodeURIComponent(slug)}/tasks?${sp.toString()}`,
      { signal },
    );
    const tasks = asArray<Record<string, unknown>>(raw.tasks).map(normalizeTask);
    const c = asRecord(raw.counts);
    const total = Number(raw.total ?? tasks.length);
    return {
      slug: String(raw.slug || slug),
      total,
      offset: Number(raw.offset ?? params.offset ?? 0),
      limit: Number(raw.limit ?? params.limit ?? 25),
      counts: {
        all: Number(c.all ?? total),
        open: Number(c.open ?? 0),
        claimed: Number(c.claimed ?? 0),
        completed: Number(c.completed ?? 0),
      },
      tasks,
    };
  },

  async getFindings(
    params: {
      status?: string;
      pillar?: string;
      methodology?: string;
      search?: string;
      limit?: number;
      offset?: number;
    } = {},
  ): Promise<PaginatedFindings> {
    const sp = new URLSearchParams();
    if (params.status) sp.set('status', params.status);
    if (params.pillar) sp.set('pillar', params.pillar);
    if (params.methodology) sp.set('methodology', params.methodology);
    if (params.search) sp.set('search', params.search);
    if (params.limit != null) sp.set('limit', String(params.limit));
    if (params.offset != null) sp.set('offset', String(params.offset));
    const qs = sp.toString();
    const raw = await fetchJson<unknown>(`/api/findings${qs ? `?${qs}` : ''}`);
    if (Array.isArray(raw)) {
      const items = raw.map((f) => normalizeFinding(asRecord(f)));
      const offset = params.offset ?? 0;
      const limit = params.limit ?? items.length;
      return { items, total: items.length, limit, offset, has_more: false };
    }
    const obj = asRecord(raw);
    const items = asArray<Record<string, unknown>>(obj.findings ?? obj.items).map((f) =>
      normalizeFinding(asRecord(f)),
    );
    const limit = optNum(obj.limit) ?? params.limit ?? Math.max(items.length, 24);
    const offset = optNum(obj.offset) ?? params.offset ?? 0;
    const total = optNum(obj.total) ?? offset + items.length;
    const hasMore =
      obj.has_more !== undefined ? Boolean(obj.has_more) : offset + items.length < total;
    return { items, total, limit, offset, has_more: hasMore };
  },

  async getFindingDetail(id: string | number): Promise<FindingItem> {
    const raw = await fetchJson<Record<string, unknown>>(
      `/api/findings/${encodeURIComponent(String(id))}`,
    );
    if (raw.finding && typeof raw.finding === 'object') {
      const f = raw.finding as Record<string, unknown>;
      return normalizeFinding({
        ...f,
        verifications:
          raw.verifications ?? raw.peer_verifications ?? f.verifications ?? f.peer_verifications,
        rti_draft: raw.rti_draft ?? raw.rti ?? f.rti_draft ?? f.rti,
        corrections:
          raw.corrections ?? raw.finding_corrections ?? f.corrections ?? f.finding_corrections,
        provenance: raw.provenance ?? f.provenance,
        artifact: raw.artifact ?? f.artifact,
      });
    }
    return normalizeFinding(raw);
  },

  async generateRtiDraft(id: string | number, publicAuthority?: string): Promise<RtiDraft> {
    const raw = await fetchJson<Record<string, unknown>>(
      `/api/findings/${encodeURIComponent(String(id))}/rti-draft`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(publicAuthority ? { public_authority: publicAuthority } : {}),
      },
    );
    const draft = normalizeRtiDraft((raw.rti_draft ?? raw) as Record<string, unknown>);
    if (!draft) throw new Error('Invalid RTI draft response from hub.');
    return draft;
  },

  async getGraph(): Promise<GraphData> {
    const raw = await fetchJson<Record<string, unknown>>('/api/graph');
    return {
      nodes: asArray<GraphData['nodes'][number]>(raw.nodes),
      edges: asArray<GraphData['edges'][number]>(raw.edges),
    };
  },

  async getEvents(sinceId?: number): Promise<{ events: WhisperItem[]; latest_event_id?: number }> {
    const qs = sinceId != null && sinceId > 0 ? `?since_id=${sinceId}` : '';
    const raw = await fetchJson<Record<string, unknown>>(`/api/events${qs}`);
    const list = asArray<Record<string, unknown>>(raw.events ?? raw.items ?? raw.whispers);
    const events = list.map(normalizeWhisper);
    const latest =
      optNum(raw.last_event_id ?? raw.latest_event_id) ??
      events.reduce<number | undefined>((max, e) => {
        if (e.event_id == null) return max;
        return max == null ? e.event_id : Math.max(max, e.event_id);
      }, undefined);
    return { events, latest_event_id: latest };
  },

  async getLive(): Promise<LiveData> {
    const raw = await fetchJson<Record<string, unknown>>('/api/live');
    return {
      agents: asArray<Record<string, unknown>>(raw.agents).map(normalizeAgent),
      whispers: asArray<Record<string, unknown>>(raw.whispers).map(normalizeWhisper),
      recent_verifications: asArray<Record<string, unknown>>(raw.recent_verifications).map(
        normalizeVerification,
      ),
      model_breakdown: asArray<Record<string, unknown>>(raw.model_breakdown).map((m) => ({
        provider: String(m.provider || 'rule-engine'),
        model: String(m.model || 'default'),
        agents: Number(m.agents ?? m.agent_count ?? m.count ?? 0),
        tasks: Number(m.tasks ?? m.tasks_completed ?? 0),
        share_pct: m.share_pct !== undefined ? Number(m.share_pct) : undefined,
      })),
      latest_event_id: optNum(raw.latest_event_id) ?? undefined,
    };
  },

  async getSwarmLive(options?: { signal?: AbortSignal }): Promise<LiveSwarmSnapshot> {
    const raw = await fetchJson<Record<string, unknown>>('/api/swarm/live', {
      signal: options?.signal,
    });
    const normalizeStatus = (val: unknown): LiveSwarmNodeStatus => {
      const s = String(val || '').toLowerCase();
      if (s === 'working' || s === 'verifying' || s === 'recent' || s === 'idle') return s;
      return 'idle';
    };
    const activeNodes: LiveSwarmNode[] = asArray<Record<string, unknown>>(raw.active_nodes).map(
      (n) => ({
        handle: String(n.handle || 'swarm-node'),
        display_name: optStr(n.display_name) ?? String(n.handle || 'swarm-node'),
        status: normalizeStatus(n.status),
        role: optStr(n.role),
        coding_agent: optStr(n.coding_agent),
        coding_agent_model: optStr(n.coding_agent_model),
        provider: optStr(n.provider) ?? 'rule-engine',
        model: optStr(n.model) ?? 'deterministic',
        current_task_id: optStr(n.current_task_id),
        current_task_title: optStr(n.current_task_title),
        current_project_slug: optStr(n.current_project_slug),
        progress_pct: optNum(n.progress_pct),
        latest_whisper: optStr(n.latest_whisper),
        tasks_completed: Number(n.tasks_completed ?? 0),
        verifications_done: Number(n.verifications_done ?? 0),
        findings_submitted: Number(n.findings_submitted ?? 0),
        last_seen_at: optStr(n.last_seen_at) ?? undefined,
        created_at: optStr(n.created_at) ?? undefined,
      }),
    );
    const recentJoins: LiveRecentJoin[] = asArray<Record<string, unknown>>(raw.recent_joins).map(
      (j) => ({
        handle: String(j.handle || ''),
        display_name: optStr(j.display_name) ?? String(j.handle || ''),
        coding_agent: optStr(j.coding_agent),
        coding_agent_model: optStr(j.coding_agent_model),
        created_at: String(j.created_at || raw.generated_at || new Date().toISOString()),
        last_seen_at: optStr(j.last_seen_at) ?? undefined,
        is_working: Boolean(j.is_working),
      }),
    ).filter((j) => j.handle.length > 0);
    const liveWhispers: LiveSwarmWhisper[] = asArray<Record<string, unknown>>(raw.live_whispers).map(
      (w, idx) => ({
        id: String(w.id ?? `w-${idx}`),
        event_type: String(w.event_type || 'whisper'),
        handle: String(w.handle || 'swarm-node'),
        coding_agent: optStr(w.coding_agent),
        role: optStr(w.role),
        task_id: optStr(w.task_id),
        project_slug: optStr(w.project_slug),
        finding_id: optStr(w.finding_id),
        whisper: String(w.whisper || ''),
        created_at: String(w.created_at || raw.generated_at || new Date().toISOString()),
      }),
    ).filter((w) => w.whisper.length > 0);
    const p = asRecord(raw.progress);
    return {
      generated_at: String(raw.generated_at || new Date().toISOString()),
      latest_event_id: optNum(raw.latest_event_id) ?? undefined,
      live_window_seconds: optNum(raw.live_window_seconds) ?? 900,
      working_now: Number(raw.working_now ?? 0),
      active_leases_count: Number(raw.active_leases_count ?? 0),
      total_agents: Number(raw.total_agents ?? activeNodes.length),
      total_sessions: optNum(raw.total_sessions) ?? Number(raw.tasks_completed_total ?? 0),
      tasks_completed_total: Number(raw.tasks_completed_total ?? p.tasks_completed ?? 0),
      tasks_last_hour: Number(raw.tasks_last_hour ?? 0),
      last_activity_at: optStr(raw.last_activity_at),
      active_nodes: activeNodes,
      recent_joins: recentJoins,
      live_whispers: liveWhispers,
      progress: {
        tasks_total: Number(p.tasks_total ?? 0),
        tasks_completed: Number(p.tasks_completed ?? 0),
        tasks_in_progress: Number(p.tasks_in_progress ?? 0),
        findings_verified: Number(p.findings_verified ?? 0),
        findings_in_review: Number(p.findings_in_review ?? 0),
      },
    };
  },

  async getContributors(window: 'all' | 'hour' = 'all'): Promise<AgentRecord[]> {
    const raw = await fetchJson<unknown>(`/api/contributors?window=${window}`);
    const list = Array.isArray(raw)
      ? raw
      : asArray<Record<string, unknown>>(
          asRecord(raw).contributors ?? asRecord(raw).agents ?? asRecord(raw).items,
        );
    return list.map((a) => normalizeAgent(asRecord(a)));
  },

  async getContributorProfile(handle: string): Promise<ContributorProfile> {
    const raw = await fetchJson<Record<string, unknown>>(
      `/api/contributors/${encodeURIComponent(handle)}`,
    );
    const agentObj = raw.agent ? asRecord(raw.agent) : raw;
    return {
      agent: normalizeAgent(agentObj),
      submitted_findings: asArray<Record<string, unknown>>(
        raw.submitted_findings ?? raw.findings,
      ).map(normalizeFinding),
      verifications: asArray<Record<string, unknown>>(
        raw.verifications ?? raw.peer_verifications,
      ).map(normalizeVerification),
      recent_whispers: asArray<Record<string, unknown>>(raw.recent_whispers ?? raw.whispers).map(
        normalizeWhisper,
      ),
    };
  },

  async getSources(): Promise<PublicSource[]> {
    const raw = await fetchJson<unknown>('/api/sources');
    const list = Array.isArray(raw)
      ? raw
      : asArray<Record<string, unknown>>(asRecord(raw).sources ?? asRecord(raw).items);
    return list.map((s) => normalizeSource(asRecord(s)));
  },

  async getGraveyard(
    params: {
      cause?: string;
      project?: string;
      limit?: number;
      offset?: number;
    } = {},
    options?: { signal?: AbortSignal },
  ): Promise<GraveyardResponse> {
    const sp = new URLSearchParams();
    if (params.cause) sp.set('cause', params.cause);
    if (params.project) sp.set('project', params.project);
    if (params.limit != null) sp.set('limit', String(params.limit));
    if (params.offset != null) sp.set('offset', String(params.offset));
    const qs = sp.toString();
    const raw = await fetchJson<Record<string, unknown>>(
      `/api/graveyard${qs ? `?${qs}` : ''}`,
      { signal: options?.signal },
    );
    const entries = asArray<GraveyardResponse['entries'][number]>(raw.entries);
    return {
      total: Number(raw.total ?? entries.length),
      offset: Number(raw.offset ?? params.offset ?? 0),
      limit: Number(raw.limit ?? params.limit ?? 24),
      has_more: Boolean(raw.has_more),
      counts_by_cause: (raw.counts_by_cause as Record<string, number>) || {},
      projects: asArray<{ slug: string; title: string }>(raw.projects),
      entries,
    };
  },

  async getJoinTxt(): Promise<string> {
    const res = await fetchWithTimeout('/join.txt');
    if (!res.ok) throw new Error(sanitizeErrorMessage(res.status, `HTTP ${res.status}`));
    return res.text();
  },
};

/**
 * Derive a best-effort LiveSwarmSnapshot from /api/head + live SSE whispers
 * while /api/swarm/live is loading or temporarily unavailable.
 */
export function deriveLiveSwarmSnapshot(
  head: HeadData | null,
  liveWhispers: WhisperItem[] = [],
): LiveSwarmSnapshot | null {
  if (!head) return null;

  const mergedWhispers = liveWhispers.length > 0 ? liveWhispers : head.whispers ?? [];
  const latestByHandle = new Map<string, WhisperItem>();
  for (const w of mergedWhispers) {
    if (w.agent_handle && !latestByHandle.has(w.agent_handle)) {
      latestByHandle.set(w.agent_handle, w);
    }
  }

  const activeSet = new Set<string>();
  const seen = new Set<string>();
  const activeNodes: LiveSwarmNode[] = [];

  for (const a of head.active_agents ?? []) {
    if (!a.handle || seen.has(a.handle)) continue;
    seen.add(a.handle);
    activeSet.add(a.handle);
    const w = latestByHandle.get(a.handle);
    activeNodes.push({
      handle: a.handle,
      display_name: a.display_name || a.handle,
      status: a.mode === 'verify' ? 'verifying' : 'working',
      role: a.mode === 'verify' ? 'peer_verifier' : 'auditor',
      coding_agent: a.coding_agent ?? null,
      coding_agent_model: a.coding_agent_model ?? null,
      provider: a.provider,
      model: a.model,
      current_project_slug: w?.mission_slug ?? null,
      latest_whisper: w?.message ?? null,
      tasks_completed: a.tasks_completed ?? 0,
      verifications_done: a.verifications_completed ?? 0,
      findings_submitted: a.findings_verified ?? 0,
      last_seen_at: a.last_heartbeat ?? head.generated_at,
    });
  }

  for (const a of head.top_contributors ?? []) {
    if (!a.handle || seen.has(a.handle)) continue;
    seen.add(a.handle);
    const w = latestByHandle.get(a.handle);
    activeNodes.push({
      handle: a.handle,
      display_name: a.display_name || a.handle,
      status: 'recent',
      role: null,
      coding_agent: a.coding_agent ?? null,
      coding_agent_model: a.coding_agent_model ?? null,
      provider: a.provider,
      model: a.model,
      current_project_slug: w?.mission_slug ?? null,
      latest_whisper: w?.message ?? null,
      tasks_completed: a.tasks_completed ?? 0,
      verifications_done: a.verifications_completed ?? 0,
      findings_submitted: a.findings_verified ?? 0,
      last_seen_at: a.last_heartbeat ?? head.generated_at,
    });
  }

  const recentJoins: LiveRecentJoin[] = (head.just_joined ?? []).map((j) => ({
    handle: j.handle,
    display_name: j.handle,
    created_at: j.joined_at || head.generated_at,
    last_seen_at: j.joined_at || head.generated_at,
    is_working: activeSet.has(j.handle),
  }));

  const liveSwarmWhispers: LiveSwarmWhisper[] = mergedWhispers.slice(0, 20).map((w, idx) => ({
    id: String(w.event_id ?? w.id ?? `head-w-${idx}`),
    event_type: w.event_type || 'whisper',
    handle: w.agent_handle || 'swarm-node',
    project_slug: w.mission_slug ?? null,
    finding_id: w.finding_id != null ? String(w.finding_id) : null,
    whisper: w.message,
    created_at: w.created_at || head.generated_at,
  }));

  const stats = head.stats;
  const tasksCompleted = stats?.tasks_completed ?? 0;
  const tasksQueued = stats?.tasks_queued ?? 0;

  return {
    generated_at: head.generated_at || new Date().toISOString(),
    latest_event_id: head.latest_event_id,
    live_window_seconds: 900,
    working_now: stats?.agents_active ?? activeSet.size,
    active_leases_count: activeSet.size,
    total_agents: stats?.agents_total ?? activeNodes.length,
    total_sessions: tasksCompleted,
    tasks_completed_total: tasksCompleted,
    tasks_last_hour: 0,
    last_activity_at: liveSwarmWhispers[0]?.created_at ?? head.generated_at,
    active_nodes: activeNodes,
    recent_joins: recentJoins,
    live_whispers: liveSwarmWhispers,
    progress: {
      tasks_total: tasksCompleted + tasksQueued + activeSet.size,
      tasks_completed: tasksCompleted,
      tasks_in_progress: activeSet.size,
      findings_verified: stats?.findings_verified ?? 0,
      findings_in_review: stats?.findings_peer_review ?? 0,
    },
  };
}

/**
 * Prepend fresher SSE whispers onto an already-loaded LiveSwarmSnapshot
 * so live agent cards react instantaneously without waiting for the next fetch.
 */
export function mergeSseWhispersIntoSnapshot(
  snapshot: LiveSwarmSnapshot,
  liveWhispers: WhisperItem[],
): LiveSwarmSnapshot {
  if (liveWhispers.length === 0) return snapshot;
  const cursor = snapshot.latest_event_id ?? 0;
  const existingIds = new Set(snapshot.live_whispers.map((w) => String(w.id)));
  const incoming: LiveSwarmWhisper[] = [];

  for (const w of liveWhispers) {
    if (w.event_id != null && cursor > 0 && w.event_id <= cursor) continue;
    const id = String(w.event_id ?? w.id ?? '');
    if (id && existingIds.has(id)) continue;
    if (!w.message) continue;
    incoming.push({
      id: id || `sse-${w.created_at}-${incoming.length}`,
      event_type: w.event_type || 'whisper',
      handle: w.agent_handle || 'swarm-node',
      project_slug: w.mission_slug ?? null,
      finding_id: w.finding_id != null ? String(w.finding_id) : null,
      whisper: w.message,
      created_at: w.created_at || new Date().toISOString(),
    });
  }

  if (incoming.length === 0) return snapshot;
  return {
    ...snapshot,
    generated_at: incoming[0]?.created_at || snapshot.generated_at,
    live_whispers: [...incoming, ...snapshot.live_whispers].slice(0, 20),
  };
}
