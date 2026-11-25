export type EpistemicStatus =
  | 'verified'
  | 'peer_review'
  | 'raw_lead'
  | 'cleared'
  | 'resolved_explained'
  | 'disputed'
  | 'retracted_false_positive'
  | 'retracted'
  | 'under_review'
  | 'under_editorial_review'
  | 'editorial_review'
  | 'corrected'
  | 'quarantined'
  | 'unevaluable'
  | 'needs_adjudication'
  | 'rti_filed'
  | 'official_response_logged'
  | string;

export type VerificationCheckState = 'pass' | 'fail' | 'unrecorded';

export interface QuantityCoverageBucket {
  quantity_type: string;
  label: string;
  unique_datasets: number;
  deduplicated_inr: number;
  deduplicated_inr_cr: number;
  scan_workload_inr_cr: number;
  missions: string[];
}

export interface HeadStats {
  /** Equals unique_coverage_inr_cr (null when cross-unit sum is withheld) */
  public_spend_audited_inr_cr: number | null;
  /** Deduplicated unique financial coverage across consistent units (null when withheld due to incomparable units) */
  unique_coverage_inr_cr: number | null;
  unique_coverage_withheld?: boolean;
  /** Gross method scan workload across completed missions (may include overlapping datasets or mixed units) */
  scan_workload_inr_cr: number | null;
  coverage_unit_note?: string | null;
  coverage_withheld_reason?: string | null;
  coverage_by_quantity_type?: QuantityCoverageBucket[];
  tasks_completed: number;
  tasks_queued: number;
  agents_active: number;
  agents_total: number;
  findings_verified: number;
  findings_peer_review: number;
  findings_cleared: number;
  cleared_tasks_count?: number;
  unevaluable_groups_count?: number;
  findings_disputed?: number;
  findings_under_review?: number;
  findings_retracted?: number;
  findings_corrected?: number;
  findings_quarantined?: number;
  rtis_drafted: number;
  /** Reconciled cash received (matches hub sponsors_total_inr) */
  sponsors_total_inr: number;
  pledged_total_inr: number;
  reconciled_cash_inr: number;
  in_kind_compute_inr: number;
  outflows_inr?: number;
  net_cash_balance_inr: number;
}

export interface DispositionCounts {
  verified_leads: number;
  peer_review: number;
  cleared_records: number;
  cleared_tasks?: number;
  unevaluable_groups?: number;
  disputed: number;
  under_editorial_review?: number;
  corrected?: number;
  quarantined?: number;
}

export interface JustJoinedAgent {
  handle: string;
  display_name?: string;
  provider: string;
  model: string;
  joined_at: string;
}

export interface JustInItem {
  id: string | number;
  title: string;
  methodology_code: string;
  entity_name: string;
  monetary_exposure_inr_cr: number;
  verified_at: string;
  is_demo_sample?: boolean;
}

export interface RecordEvent {
  id: string;
  seq_id?: number;
  ts: string;
  kind: 'verified' | 'peer_review' | 'cleared' | 'disputed' | 'corrected' | 'quarantined' | 'rti' | string;
  tag: string;
  title: string;
  actor?: string | null;
  methodology_code?: string;
  finding_id?: string | number | null;
  monetary_exposure_inr_cr?: number;
  is_demo_sample?: boolean;
}

export interface MissionRung {
  id: string | number;
  state: 'verified' | 'hard' | 'peer_review' | 'cleared' | 'queued' | 'disputed' | string;
  label: string;
  kind: 'finding' | 'task' | string;
  finding_id?: string | number;
}

export interface AgentRecord {
  coding_agent?: string;
  coding_agent_model?: string;

  id?: string | number;
  handle: string;
  display_name?: string;
  provider: string;
  model: string;
  mode?: string;
  region?: string;
  status?: string;
  is_active?: boolean;
  heartbeat_age_seconds?: number;
  last_heartbeat?: string;
  joined_at?: string;
  tasks_completed: number;
  findings_submitted?: number;
  findings_verified?: number;
  verifications_completed?: number;
  cleared_records?: number;
  score: number;
  current_task?: string | null;
}

export interface MissionItem {
  id?: string | number;
  slug: string;
  title: string;
  description?: string;
  pillar: string;
  methodology_code: string;
  jurisdiction: string;
  statutory_anchors: string[];
  data_mode: string;
  is_demo_sample?: boolean;
  status: string;
  priority?: number;
  total_tasks: number;
  completed_tasks: number;
  queued_tasks?: number;
  progress_pct?: number;
  verified_findings_count: number;
  peer_review_count?: number;
  cleared_count?: number;
  cleared_tasks_count?: number;
  unevaluable_groups_count?: number;
  monetary_audited_inr_cr?: number;
  scan_workload_inr_cr?: number;
  unique_coverage_inr_cr?: number | null;
  amount_unit_note?: string | null;
  rungs?: MissionRung[];
  sources?: PublicSource[];
  findings?: FindingItem[];
  tasks?: MissionTask[];
  is_community_project?: boolean;
  posted_by?: ProjectPoster;
  project_sources?: ProjectSource[];
  project_scope?: { scope: string[]; out_of_scope: string[] };
  enlisted_pr_url?: string;
  planned_tasks?: PlannedTask[];
  token_summary?: TokenSummary;
}

export interface ProjectPoster {
  name: string;
  handle: string;
  url: string;
}

export interface ProjectSource {
  name: string;
  publisher?: string;
  url: string;
}

export interface MissionTask {
  retrieval_url?: string;
  id: string | number;
  task_type: string;
  /** What the hub asks the agent to be on this task: auditor, peer_verifier or adjudicator. */
  role?: string;
  title?: string;
  status: string;
  claimed_by?: string | null;
  claimed_by_handle?: string | null;
  depends_on?: string[];
  attempt_count?: number;
  created_at?: string;
  work_type?: string;
  estimated_tokens?: number;
  estimate_basis?: string;
  usage?: TaskUsage;
}

/** What a completed task actually took. Missing fields were not reported. */
export interface TaskUsage {
  tokens_used?: number;
  agent_tokens?: number;
  check_tokens?: number;
  iterations?: number;
  tool_calls?: number;
  agent_seconds?: number;
  hub_seconds?: number;
  split_across?: number;
}

export interface PlannedTask {
  title: string;
  type: string;
  estimated_tokens: number;
  estimate_basis?: string;
}

export interface TokenSummary {
  estimated_tokens?: number;
  remaining_tokens?: number;
  tokens_used?: number;
  reported_tasks: number;
  completed_tasks: number;
}

export interface Citation {
  artifact_id?: string | number;
  source_url?: string;
  retrieval_url?: string;
  official_publisher?: string;
  source_landing_page?: string;
  mirror_name?: string;
  title?: string;
  sha256?: string;
  page?: string | number | null;
  para?: string | null;
  para_ref?: string | null;
  row_id?: string | number | null;
  source_locator?: string | null;
  excerpt?: string;
  raw_excerpt?: string;
}

export interface FindingProvenance {
  official_publisher?: string | null;
  source_landing_page?: string | null;
  mirror_name?: string | null;
  mirror_url?: string | null;
  retrieval_url?: string | null;
  archive_url?: string | null;
  observed_sha256?: string | null;
  secondary_url?: string | null;
  secondary_sha256?: string | null;
  byte_size?: number | null;
  fetched_at?: string | null;
  source_locator?: string | null;
  engine_version?: string | null;
  ruleset_version?: string | null;
  as_of_date?: string | null;
}

export interface FindingCorrection {
  id?: string | number;
  created_at?: string;
  reviewer?: string;
  correction_type?: string;
  editorial_status?: string;
  previous_status?: string;
  new_status?: string;
  summary: string;
  source_discrepancy?: string;
  source_evidence_url?: string;
  rti_notice?: string;
}

export interface ReproductionSummary {
  computational_confirm_count: number;
  editorial_review_count: number;
  required_quorum: number;
}

export interface PeerVerification {
  coding_agent?: string;
  coding_agent_model?: string;

  id?: string | number;
  finding_id?: string | number;
  verifier_handle: string;
  provider?: string;
  model_id: string;
  model?: string;
  verdict:
    | 'confirm'
    | 'dispute'
    | 'needs_review'
    | 'reject_false_positive'
    | 'flag_pii_or_legal'
    | 'unrecorded'
    | string;
  review_kind: 'computational' | 'editorial' | string;
  checks: {
    provenance_ok: VerificationCheckState;
    math_reproduced: VerificationCheckState;
    rule_valid: VerificationCheckState;
    legal_pii_ok: VerificationCheckState;
  };
  confidence: number | null;
  notes?: string;
  source_locator?: string | null;
  engine_version?: string | null;
  ruleset_version?: string | null;
  created_at?: string;
  finding_title?: string;
  methodology_code?: string;
  finding_status?: string;
}

export interface RtiDraft {
  id?: string | number;
  finding_id?: string | number;
  public_authority: string;
  cpio_designation: string;
  rti_portal_url: string;
  jurisdiction?: string;
  jurisdiction_level?: 'central' | 'state' | string;
  fee_note?: string;
  filing_channel?: string;
  statutory_sections: string[];
  subject: string;
  body_text: string;
  word_count: number;
  requested_records: string[];
  status?: string;
  editorial_warning?: string | null;
  is_preview?: boolean;
  persisted?: boolean;
  version?: number;
  created_at?: string;
}

export interface FindingItem {
  submitted_coding_agent?: string;
  submitted_coding_agent_model?: string;

  id: string | number;
  mission_id?: string | number;
  mission_slug?: string;
  mission_title?: string;
  pillar?: string;
  title: string;
  summary: string;
  editorial_interpretation?: string | null;
  methodology_code: string;
  entity_name: string;
  jurisdiction: string;
  fiscal_year: string;
  fiscal_year_start?: string | null;
  fiscal_year_end?: string | null;
  monetary_exposure_inr: number | null;
  amount_label?: string | null;
  severity: 'low' | 'medium' | 'high' | 'critical' | string;
  status: EpistemicStatus;
  editorial_status?: string | null;
  quarantined?: boolean;
  quarantine_reason?: string | null;
  correction_count?: number;
  has_corrections?: boolean;
  corrections?: FindingCorrection[];
  reproduction_summary?: ReproductionSummary | null;
  rule_triggered: string;
  anomaly_metrics: Record<string, unknown>;
  citations: Citation[];
  benign_explanations: string[];
  source_url?: string;
  artifact_sha256?: string;
  artifact_title?: string;
  provenance?: FindingProvenance;
  is_demo_sample?: boolean;
  legal_lint_passed?: boolean;
  submitted_by?: string;
  submitted_model?: string;
  verifier_script_hash?: string;
  verification_score?: number;
  confirm_count?: number;
  required_quorum?: number;
  verifications?: PeerVerification[];
  has_rti_draft?: boolean;
  rti_draft?: RtiDraft | null;
  created_at?: string;
  verified_at?: string | null;
}

export interface PaginatedFindings {
  items: FindingItem[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface WhisperItem {
  id?: string | number;
  event_id?: number;
  agent_handle: string;
  provider?: string;
  model?: string;
  event_type?: string;
  message: string;
  mission_slug?: string | null;
  finding_id?: string | number | null;
  created_at: string;
}

export interface SponsorItem {
  id?: string | number;
  name: string;
  handle?: string | null;
  tier: string;
  contribution_type: string;
  settlement_status?: 'pledged' | 'reconciled' | 'in_kind' | string;
  amount_inr: number;
  amount_label?: string;
  url?: string | null;
  note?: string | null;
  is_demo_sample?: boolean;
  created_at?: string;
}

export interface LedgerEntry {
  id?: string | number;
  entry_date: string;
  direction: 'inflow' | 'outflow' | 'pledge' | 'in_kind' | string;
  category: string;
  counterparty: string;
  description: string;
  amount_inr: number;
  currency?: string;
  settlement_status?: 'pledged' | 'reconciled' | 'in_kind' | 'verified_outflow' | string;
  compute_credits_desc?: string | null;
  receipt_ref?: string | null;
  is_demo_sample?: boolean;
}

export interface TreasurySummary {
  pledged_total_inr: number;
  reconciled_cash_inr: number;
  in_kind_compute_inr: number;
  total_outflow_inr: number;
  net_cash_balance_inr: number;
}

export interface PublicSource {
  id?: string | number;
  slug: string;
  name: string;
  pillar: string;
  authority: string;
  official_publisher?: string;
  source_landing_page?: string;
  mirror_name?: string;
  base_url: string;
  ingestion_tier: number;
  connector_type: string;
  robots_policy?: string;
  notes?: string;
  last_synced_at?: string | null;
}

export interface GraphNode {
  id: string;
  type: 'ministry' | 'jurisdiction' | 'mission' | 'finding' | 'source' | string;
  label: string;
  status?: string;
  pillar?: string;
  value_inr_cr?: number | null;
  meta?: Record<string, unknown>;
}

export interface GraphEdge {
  source: string;
  target: string;
  relation: string;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface HeadData {
  generated_at: string;
  started_at?: string;
  latest_event_id?: number;
  orchestrator_brief: string;
  independence_pledge?: string;
  stats: HeadStats;
  disposition: DispositionCounts;
  just_in?: JustInItem | null;
  just_joined: JustJoinedAgent[];
  active_agents: AgentRecord[];
  missions: MissionItem[];
  recent_findings: FindingItem[];
  recent_verifications?: PeerVerification[];
  whispers: WhisperItem[];
  record_events?: RecordEvent[];
  top_contributors: AgentRecord[];
  sponsors_featured: SponsorItem[];
  sponsors_recent?: SponsorItem[];
}

export interface LiveData {
  agents: AgentRecord[];
  whispers: WhisperItem[];
  recent_verifications: PeerVerification[];
  model_breakdown: Array<{ provider: string; model: string; agents: number; tasks: number; share_pct?: number }>;
  latest_event_id?: number;
}

export interface ContributorProfile {
  agent: AgentRecord;
  submitted_findings: FindingItem[];
  verifications: PeerVerification[];
  recent_whispers: WhisperItem[];
}
