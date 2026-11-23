import type {
  AgentRecord,
  Citation,
  ContributorProfile,
  FindingItem,
  GraphData,
  HeadData,
  JustInItem,
  LedgerEntry,
  LiveData,
  MissionItem,
  MissionRung,
  PeerVerification,
  PublicSource,
  RecordEvent,
  RtiDraft,
  SponsorItem,
  WhisperItem,
} from '../types';

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { Accept: 'application/json', ...(init?.headers || {}) },
    ...init,
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = body?.detail ? (typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)) : detail;
    } catch {
      // ignore non-json error body
    }
    throw new Error(detail);
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

export function normalizeAgent(raw: Record<string, unknown>): AgentRecord {
  return {
    id: raw.id as string | number | undefined,
    handle: String(raw.handle || 'unknown-node'),
    display_name: raw.display_name ? String(raw.display_name) : undefined,
    provider: String(raw.provider || 'rule-engine'),
    model: String(raw.model || 'deterministic-v1'),
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
    current_task: (raw.current_task || raw.current_task_title || raw.current_task_id || null) as string | null,
  };
}

export function normalizeVerification(raw: Record<string, unknown>): PeerVerification {
  const checks = asRecord(raw.checks ?? raw.checks_json);
  return {
    id: raw.id as string | number | undefined,
    finding_id: raw.finding_id as string | number | undefined,
    verifier_handle: String(raw.verifier_handle || raw.contributor_id || raw.handle || 'peer-node'),
    provider: raw.provider ? String(raw.provider) : undefined,
    model_id: String(raw.model_id || raw.model || 'deterministic-v1'),
    model: raw.model ? String(raw.model) : raw.model_id ? String(raw.model_id) : undefined,
    verdict: String(raw.verdict || 'confirm'),
    checks: {
      provenance_ok: checks.provenance_ok !== undefined ? Boolean(checks.provenance_ok) : true,
      math_reproduced: checks.math_reproduced !== undefined ? Boolean(checks.math_reproduced) : true,
      rule_valid: checks.rule_valid !== undefined ? Boolean(checks.rule_valid) : true,
      legal_pii_ok: checks.legal_pii_ok !== undefined ? Boolean(checks.legal_pii_ok) : true,
    },
    confidence: Number(raw.confidence ?? 0.9),
    notes: raw.notes ? String(raw.notes) : '',
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
    public_authority: String(raw.public_authority || 'Central Public Information Officer'),
    cpio_designation: String(raw.cpio_designation || 'Central Public Information Officer (CPIO)'),
    rti_portal_url: String(raw.rti_portal_url || 'https://rtionline.gov.in/'),
    statutory_sections: asArray<string>(raw.statutory_sections ?? raw.statutory_sections_json),
    subject: String(raw.subject || 'Application under Section 6(1) of the RTI Act, 2005'),
    body_text: String(raw.body_text || ''),
    word_count: Number(raw.word_count ?? String(raw.body_text || '').trim().split(/\s+/).filter(Boolean).length),
    requested_records: asArray<string>(raw.requested_records ?? raw.requested_records_json),
    status: raw.status ? String(raw.status) : 'draft',
    created_at: raw.created_at ? String(raw.created_at) : undefined,
  };
}

export function normalizeFinding(raw: Record<string, unknown>): FindingItem {
  const citations = asArray<Record<string, unknown>>(raw.citations ?? raw.citations_json).map(
    (c): Citation => ({
      artifact_id: c.artifact_id as string | number | undefined,
      source_url: c.source_url ? String(c.source_url) : undefined,
      title: c.title ? String(c.title) : undefined,
      sha256: c.sha256 ? String(c.sha256) : undefined,
      page: (c.page ?? null) as string | number | null,
      para: (c.para ?? c.para_ref ?? null) as string | null,
      row_id: (c.row_id ?? null) as string | number | null,
      excerpt: String(c.excerpt || c.raw_excerpt || ''),
    }),
  );
  const trace = asRecord(raw.agent_trace ?? raw.agent_trace_json);
  const verifications = asArray<Record<string, unknown>>(raw.verifications ?? raw.peer_verifications).map(
    normalizeVerification,
  );
  const rti = normalizeRtiDraft((raw.rti_draft ?? raw.rti) as Record<string, unknown> | null);

  return {
    id: (raw.id ?? '') as string | number,
    mission_id: raw.mission_id as string | number | undefined,
    mission_slug: raw.mission_slug ? String(raw.mission_slug) : undefined,
    mission_title: raw.mission_title ? String(raw.mission_title) : undefined,
    pillar: raw.pillar ? String(raw.pillar) : undefined,
    title: String(raw.title || 'Public Record Anomaly Lead'),
    summary: String(raw.summary || ''),
    methodology_code: String(raw.methodology_code || 'M1'),
    entity_name: String(raw.entity_name || 'Public Authority'),
    jurisdiction: String(raw.jurisdiction || 'Union'),
    fiscal_year: String(raw.fiscal_year || 'FY 2024-25'),
    monetary_exposure_inr:
      raw.monetary_exposure_inr != null ? Number(raw.monetary_exposure_inr) : null,
    severity: String(raw.severity || 'medium'),
    status: String(raw.status || 'peer_review'),
    rule_triggered: String(raw.rule_triggered || ''),
    anomaly_metrics: asRecord(raw.anomaly_metrics ?? raw.anomaly_metrics_json),
    citations,
    benign_explanations: asArray<string>(raw.benign_explanations ?? raw.benign_explanations_json),
    source_url: String(raw.source_url || citations[0]?.source_url || ''),
    artifact_sha256: String(raw.artifact_sha256 || citations[0]?.sha256 || ''),
    artifact_title: raw.artifact_title ? String(raw.artifact_title) : undefined,
    is_demo_sample: raw.is_demo_sample !== undefined ? Boolean(raw.is_demo_sample) : true,
    legal_lint_passed: raw.legal_lint_passed !== undefined ? Boolean(raw.legal_lint_passed) : true,
    submitted_by: raw.submitted_by ? String(raw.submitted_by) : (trace.node_id ? String(trace.node_id) : undefined),
    submitted_model: raw.submitted_model ? String(raw.submitted_model) : (trace.model_id ? String(trace.model_id) : undefined),
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
  return {
    id: raw.id as string | number | undefined,
    slug: String(raw.slug || ''),
    title: String(raw.title || 'Audit Mission'),
    description: raw.description ? String(raw.description) : undefined,
    pillar: String(raw.pillar || 'procurement'),
    methodology_code: String(raw.methodology_code || 'M1'),
    jurisdiction: String(raw.jurisdiction || 'Union'),
    statutory_anchors: asArray<string>(raw.statutory_anchors ?? raw.statutory_anchors_json),
    data_mode: String(raw.data_mode || 'demo_sample'),
    is_demo_sample: raw.is_demo_sample !== undefined ? Boolean(raw.is_demo_sample) : String(raw.data_mode || '') === 'demo_sample',
    status: String(raw.status || 'active'),
    priority: Number(raw.priority ?? 1),
    total_tasks: Number(raw.total_tasks ?? 0),
    completed_tasks: Number(raw.completed_tasks ?? 0),
    queued_tasks: raw.queued_tasks !== undefined ? Number(raw.queued_tasks) : undefined,
    progress_pct: raw.progress_pct !== undefined ? Number(raw.progress_pct) : undefined,
    verified_findings_count: Number(raw.verified_findings_count ?? 0),
    peer_review_count: raw.peer_review_count !== undefined ? Number(raw.peer_review_count) : undefined,
    cleared_count: Number(raw.cleared_count ?? 0),
    monetary_audited_inr_cr: monetaryCr,
    rungs: raw.rungs ? asArray<Record<string, unknown>>(raw.rungs).map(normalizeRung) : undefined,
    sources: raw.sources ? asArray<Record<string, unknown>>(raw.sources).map(normalizeSource) : undefined,
    findings: raw.findings ? asArray<Record<string, unknown>>(raw.findings).map(normalizeFinding) : undefined,
    tasks: raw.tasks
      ? asArray<Record<string, unknown>>(raw.tasks).map((t) => ({
          id: (t.id ?? '') as string | number,
          task_type: String(t.task_type || 'anomaly_scan'),
          title: t.title ? String(t.title) : undefined,
          status: String(t.status || 'queued'),
          claimed_by: (t.claimed_by_handle || t.claimed_by || null) as string | null,
          attempt_count: Number(t.attempt_count ?? 0),
          created_at: t.created_at ? String(t.created_at) : undefined,
        }))
      : undefined,
  };
}

export function normalizeSource(raw: Record<string, unknown>): PublicSource {
  return {
    id: raw.id as string | number | undefined,
    slug: String(raw.slug || ''),
    name: String(raw.name || ''),
    pillar: String(raw.pillar || 'procurement'),
    authority: String(raw.authority || ''),
    base_url: String(raw.base_url || ''),
    ingestion_tier: Number(raw.ingestion_tier ?? 1),
    connector_type: String(raw.connector_type || 'http'),
    robots_policy: raw.robots_policy ? String(raw.robots_policy) : 'strict-polite',
    notes: String(raw.data_contents || raw.format_access || raw.notes || ''),
    last_synced_at: raw.last_synced_at ? String(raw.last_synced_at) : null,
  };
}

export function normalizeWhisper(raw: Record<string, unknown>): WhisperItem {
  return {
    id: raw.id as string | number | undefined,
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

export function normalizeSponsor(raw: Record<string, unknown>): SponsorItem {
  const computeLabel = raw.compute_credits_desc ?? raw.compute_grant;
  return {
    id: raw.id as string | number | undefined,
    name: String(raw.name || 'Anonymous Backer'),
    handle: (raw.handle_or_org || raw.handle || null) as string | null,
    tier: String(raw.tier || 'citizen_backer'),
    contribution_type: String(
      raw.contribution_type || (computeLabel ? 'compute_grant' : 'financial'),
    ),
    amount_inr: Number(raw.amount_inr ?? 0),
    amount_label: computeLabel ? String(computeLabel) : undefined,
    url: (raw.url || raw.website_url || null) as string | null,
    note: (raw.message || raw.note || null) as string | null,
    is_demo_sample:
      raw.is_demo_entry !== undefined
        ? Boolean(raw.is_demo_entry)
        : raw.is_demo !== undefined
          ? Boolean(raw.is_demo)
          : Boolean(raw.is_demo_sample),
    created_at: raw.created_at ? String(raw.created_at) : undefined,
  };
}

export function normalizeLedgerEntry(raw: Record<string, unknown>): LedgerEntry {
  return {
    id: raw.id as string | number | undefined,
    entry_date: String(raw.entry_date || raw.date || ''),
    direction: String(raw.direction || 'inflow'),
    category: String(raw.category || raw.entry_type || 'grant'),
    counterparty: String(raw.counterparty || 'Public Treasury'),
    description: String(raw.description || ''),
    amount_inr: Number(raw.amount_inr ?? 0),
    currency: String(raw.currency || 'INR'),
    compute_credits_desc: (raw.compute_credit_units || raw.compute_credits_desc || null) as string | null,
    receipt_ref: (raw.receipt_ref || null) as string | null,
    is_demo_sample: raw.is_demo_entry !== undefined ? Boolean(raw.is_demo_entry) : Boolean(raw.is_demo_sample),
  };
}

function normalizeJustIn(raw: unknown): JustInItem | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  if (!r.id && !r.title) return null;
  return {
    id: (r.id ?? '') as string | number,
    title: String(r.title || ''),
    methodology_code: String(r.methodology_code || 'M1'),
    entity_name: String(r.entity_name || ''),
    monetary_exposure_inr_cr: Number(r.monetary_exposure_inr_cr ?? 0),
    verified_at: String(r.verified_at || ''),
    is_demo_sample: Boolean(r.is_demo_sample),
  };
}

function normalizeRecordEvent(raw: Record<string, unknown>): RecordEvent {
  return {
    id: String(raw.id || ''),
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
    return {
      generated_at: String(raw.generated_at || new Date().toISOString()),
      started_at: raw.started_at ? String(raw.started_at) : undefined,
      orchestrator_brief: String(
        raw.orchestrator_brief ||
          'Distributed deterministic audit swarm scanning Indian public procurement, CAG audit paragraphs, Union/State fiscal pacing, and welfare schemes under strict neutrality and 2-of-2 independent verification.',
      ),
      independence_pledge: raw.independence_pledge ? String(raw.independence_pledge) : undefined,
      stats: {
        public_spend_audited_inr_cr: Number(stats.public_spend_audited_inr_cr ?? 0),
        tasks_completed: Number(stats.tasks_completed ?? 0),
        tasks_queued: Number(stats.tasks_queued ?? 0),
        agents_active: Number(stats.agents_active ?? 0),
        agents_total: Number(stats.agents_total ?? 0),
        findings_verified: Number(stats.findings_verified ?? 0),
        findings_peer_review: Number(stats.findings_peer_review ?? 0),
        findings_cleared: Number(stats.findings_cleared ?? 0),
        rtis_drafted: Number(stats.rtis_drafted ?? 0),
        sponsors_total_inr: Number(stats.sponsors_total_inr ?? 0),
      },
      disposition: {
        verified_leads: Number(disp.verified_leads ?? stats.findings_verified ?? 0),
        peer_review: Number(disp.peer_review ?? stats.findings_peer_review ?? 0),
        cleared_records: Number(disp.cleared_records ?? stats.findings_cleared ?? 0),
        disputed: Number(disp.disputed ?? 0),
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
      recent_verifications: asArray<Record<string, unknown>>(raw.recent_verifications).map(normalizeVerification),
      whispers: asArray<Record<string, unknown>>(raw.whispers).map(normalizeWhisper),
      record_events: asArray<Record<string, unknown>>(raw.record_events).map(normalizeRecordEvent),
      top_contributors: asArray<Record<string, unknown>>(raw.top_contributors).map(normalizeAgent),
      sponsors_featured: asArray<Record<string, unknown>>(raw.sponsors_featured).map(normalizeSponsor),
      sponsors_recent: asArray<Record<string, unknown>>(raw.sponsors_recent).map(normalizeSponsor),
    };
  },

  async getMissions(): Promise<MissionItem[]> {
    const raw = await fetchJson<unknown>('/api/missions');
    const list = Array.isArray(raw) ? raw : asArray<Record<string, unknown>>(asRecord(raw).missions ?? asRecord(raw).items);
    return list.map((m) => normalizeMission(asRecord(m)));
  },

  async getMissionDetail(slug: string): Promise<MissionItem> {
    const raw = await fetchJson<Record<string, unknown>>(`/api/missions/${encodeURIComponent(slug)}`);
    if (raw.mission && typeof raw.mission === 'object') {
      return normalizeMission({
        ...(raw.mission as Record<string, unknown>),
        sources: raw.sources,
        findings: raw.findings,
        tasks: raw.tasks,
      });
    }
    return normalizeMission(raw);
  },

  async getFindings(params: {
    status?: string;
    pillar?: string;
    methodology?: string;
    search?: string;
  } = {}): Promise<FindingItem[]> {
    const sp = new URLSearchParams();
    if (params.status) sp.set('status', params.status);
    if (params.pillar) sp.set('pillar', params.pillar);
    if (params.methodology) sp.set('methodology', params.methodology);
    if (params.search) sp.set('search', params.search);
    const qs = sp.toString();
    const raw = await fetchJson<unknown>(`/api/findings${qs ? `?${qs}` : ''}`);
    const list = Array.isArray(raw) ? raw : asArray<Record<string, unknown>>(asRecord(raw).findings ?? asRecord(raw).items);
    return list.map((f) => normalizeFinding(asRecord(f)));
  },

  async getFindingDetail(id: string | number): Promise<FindingItem> {
    const raw = await fetchJson<Record<string, unknown>>(`/api/findings/${encodeURIComponent(String(id))}`);
    if (raw.finding && typeof raw.finding === 'object') {
      return normalizeFinding({
        ...(raw.finding as Record<string, unknown>),
        verifications: raw.verifications ?? raw.peer_verifications,
        rti_draft: raw.rti_draft ?? raw.rti,
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

  async getLive(): Promise<LiveData> {
    const raw = await fetchJson<Record<string, unknown>>('/api/live');
    return {
      agents: asArray<Record<string, unknown>>(raw.agents).map(normalizeAgent),
      whispers: asArray<Record<string, unknown>>(raw.whispers).map(normalizeWhisper),
      recent_verifications: asArray<Record<string, unknown>>(raw.recent_verifications).map(normalizeVerification),
      model_breakdown: asArray<Record<string, unknown>>(raw.model_breakdown).map((m) => ({
        provider: String(m.provider || 'rule-engine'),
        model: String(m.model || 'default'),
        agents: Number(m.agents ?? m.agent_count ?? m.count ?? 0),
        tasks: Number(m.tasks ?? m.tasks_completed ?? 0),
        share_pct: m.share_pct !== undefined ? Number(m.share_pct) : undefined,
      })),
    };
  },

  async getContributors(window: 'all' | 'hour' = 'all'): Promise<AgentRecord[]> {
    const raw = await fetchJson<unknown>(`/api/contributors?window=${window}`);
    const list = Array.isArray(raw)
      ? raw
      : asArray<Record<string, unknown>>(asRecord(raw).contributors ?? asRecord(raw).agents ?? asRecord(raw).items);
    return list.map((a) => normalizeAgent(asRecord(a)));
  },

  async getContributorProfile(handle: string): Promise<ContributorProfile> {
    const raw = await fetchJson<Record<string, unknown>>(`/api/contributors/${encodeURIComponent(handle)}`);
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

  async getSponsors(): Promise<{
    sponsors: SponsorItem[];
    total_inr: number;
    independence_pledge?: string;
  }> {
    const raw = await fetchJson<unknown>('/api/sponsors');
    if (Array.isArray(raw)) {
      const sponsors = raw.map((s) => normalizeSponsor(asRecord(s)));
      return {
        sponsors,
        total_inr: sponsors.reduce((acc, s) => acc + s.amount_inr, 0),
      };
    }
    const obj = asRecord(raw);
    const list = asArray<Record<string, unknown>>(
      obj.sponsors ?? obj.items ?? [...asArray(obj.institutional), ...asArray(obj.citizen_backers)],
    );
    const sponsors = list.map((s) => normalizeSponsor(asRecord(s)));
    return {
      sponsors,
      total_inr: Number(obj.total_inr ?? sponsors.reduce((acc, s) => acc + s.amount_inr, 0)),
      independence_pledge: obj.independence_pledge ? String(obj.independence_pledge) : undefined,
    };
  },

  async pledgeSponsor(payload: {
    name: string;
    handle_or_org?: string;
    tier: string;
    amount_inr: number;
    compute_credits_desc?: string;
    url?: string;
    message?: string;
  }): Promise<SponsorItem> {
    const raw = await fetchJson<Record<string, unknown>>('/api/sponsors/pledge', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: payload.name,
        handle: payload.handle_or_org || undefined,
        handle_or_org: payload.handle_or_org || undefined,
        tier: payload.tier,
        amount_inr: payload.amount_inr,
        compute_credits_desc: payload.compute_credits_desc || '',
        url: payload.url || undefined,
        message: payload.message || '',
        note: payload.message || '',
      }),
    });
    return normalizeSponsor((raw.sponsor as Record<string, unknown>) || raw);
  },

  async getLedger(): Promise<{
    entries: LedgerEntry[];
    total_inflow_inr: number;
    total_outflow_inr: number;
    balance_inr: number;
  }> {
    const raw = await fetchJson<unknown>('/api/ledger?format=json');
    const obj = asRecord(raw);
    const list = Array.isArray(raw) ? raw : asArray<Record<string, unknown>>(obj.entries ?? obj.items ?? obj.ledger);
    const entries = list.map((e) => normalizeLedgerEntry(asRecord(e)));
    const inflow = entries
      .filter((e) => e.direction === 'inflow')
      .reduce((acc, e) => acc + e.amount_inr, 0);
    const outflow = entries
      .filter((e) => e.direction === 'outflow')
      .reduce((acc, e) => acc + e.amount_inr, 0);
    const summary = asRecord(obj.summary);
    return {
      entries,
      total_inflow_inr: Number(obj.total_inflow_inr ?? summary.total_inflow_inr ?? inflow),
      total_outflow_inr: Number(obj.total_outflow_inr ?? summary.total_outflow_inr ?? outflow),
      balance_inr: Number(obj.balance_inr ?? summary.balance_inr ?? inflow - outflow),
    };
  },

  async getSources(): Promise<PublicSource[]> {
    const raw = await fetchJson<unknown>('/api/sources');
    const list = Array.isArray(raw) ? raw : asArray<Record<string, unknown>>(asRecord(raw).sources ?? asRecord(raw).items);
    return list.map((s) => normalizeSource(asRecord(s)));
  },

  async getJoinTxt(): Promise<string> {
    const res = await fetch('/join.txt');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.text();
  },
};
