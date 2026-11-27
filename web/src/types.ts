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
  last_check_at?: string;
  joined_at?: string;
  tasks_completed: number;
  findings_submitted?: number;
  findings_verified?: number;
  verifications_completed?: number;
  cleared_records?: number;
  score: number;
  hourly_score?: number;
  score_10m?: number;
  score_1h?: number;
  checks_10m?: number;
  checks_1h?: number;
  checks_total?: number;
  /** Length 36, index 0 = 35h ago, index 35 = current hour */
  hourly_points_36h?: number[];
  /** Length 36, index 0 = 35h ago, index 35 = current hour */
  hourly_checks_36h?: number[];
  current_task?: string | null;
}

export interface VisualStatusCounts {
  completed: number;
  claimed: number;
  queued: number;
  waiting: number;
  planned: number;
  total: number;
  pct_complete: number;
  task_pct_complete?: number;
  task_progress_pct?: number;
  review_progress_pct?: number;
  review_remaining_pct?: number;
  task_weight_pct?: number;
  review_weight_pct?: number;
  required_reviews?: number;
  per_review_pct?: number;
  reviews_required?: number;
  reviews_done?: number;
  reviews_remaining?: number;
  has_reviews_remaining?: boolean;
  audits_done: number;
  audits_total: number;
  checks_done: number;
  checks_total: number;
}

export interface VisualBudgetDivergence {
  finding_id: string;
  entity_name: string;
  title: string;
  pct_difference: number | null;
  monetary_exposure_inr_cr: number;
  status: string;
  severity: string;
  fiscal_year: string;
  rule_triggered: string;
}

export interface VisualBudgetAudit {
  verified_count: number;
  peer_review_count: number;
  cleared_count: number;
  disputed_count: number;
  total_exposure_inr_cr: number;
  top_divergences: VisualBudgetDivergence[];
  groups: Array<{ label: string; done: number; total: number; pct: number }>;
}

export type WebsiteVerificationMode =
  | 'automated_http_html'
  | 'agent_inspection'
  | 'internal_governance';

export type WebsiteAutomationCoverage = 'full' | 'partial_public_indicator' | 'none';

export interface VisualWebsiteCheckpoint {
  key: string;
  label: string;
  section?: string;
  framework?: string;
  checkpoint_ids?: string[];
  description?: string;
  observed: number;
  not_observed: number;
  unassessable: number;
  not_applicable?: number;
  observed_pct: number;
  not_observed_pct: number;
  unassessable_pct: number;
  not_applicable_pct?: number;
}

export interface VisualWebsiteMatrixSummary {
  total_checkpoints: number;
  gigw_checkpoints: number;
  rti_checkpoints: number;
  statutory_other_checkpoints: number;
  automated_http_html: number;
  agent_inspection: number;
  internal_governance: number;
  w1_automated_checks_count: number;
}

export interface VisualWebsiteSection {
  id: string;
  name: string;
  short_name: string;
  framework: string;
  total_checkpoints: number;
  automated_count: number;
  agent_count: number;
  internal_count: number;
  w1_check_keys: string[];
  avg_observed_pct: number | null;
}

export interface VisualWebsiteMatrixItem {
  id: string;
  section_id: string;
  section_name: string;
  framework: string;
  source_group?: string;
  serial_no?: string | number | null;
  title: string;
  requirement_text: string;
  conformance_level?: string | null;
  citation?: string | null;
  wcag_reference?: string | null;
  wcag_level?: 'A' | 'AA' | string | null;
  risks_addressed?: string[];
  source_url?: string | null;
  verification_mode: WebsiteVerificationMode;
  automation_coverage: WebsiteAutomationCoverage;
  w1_check_keys: string[];
  public_disclosure_indicator?: string | null;
  caveats?: string | null;
  applicability?: string | null;
  applicable_hosts_assessed?: number | null;
  observed_pct: number | null;
}

export interface VisualWebsiteAudit {
  batches_done: number;
  batches_total: number;
  hosts_observed: number;
  hosts_unassessable: number;
  hosts_total: number;
  total_directory_hosts?: number;
  matrix_version?: string;
  matrix_endpoint?: string;
  matrix_summary?: VisualWebsiteMatrixSummary;
  sections?: VisualWebsiteSection[];
  checkpoints: VisualWebsiteCheckpoint[];
  matrix?: VisualWebsiteMatrixItem[];
  reasons: Record<string, number>;
}

export interface VisualUnionBudgetYear {
  /** Financial year, e.g. "2014-15". */
  year: string;
  tasks: number;
  done: number;
  claimed: number;
  open: number;
  /** done / tasks * 100. */
  pct: number;
  /** Budget lines checked for that year. */
  lines: number;
  /** Of those, lines traced to their actual expenditure. */
  with_actuals: number;
  /** Rs crore, sum of the lines' budget estimates. */
  budget_estimate_cr: number;
  /** Leads published for that year. */
  leads: number;
  /** Rs crore, sum of those leads' shortfalls. */
  below_estimate_cr: number;
  /** 'revised' when actuals are unavailable or could not be matched for that year. */
  compares: 'actual' | 'revised';
  /** Why actuals were not compared ('not_published' | 'unmatched'), or null when compares === 'actual'. */
  actuals_reason?: 'not_published' | 'unmatched' | null;
}

export interface VisualUnionBudgetLead {
  finding_id: string;
  title: string;
  entity_name: string;
  /** e.g. "FY2019-20". */
  fiscal_year: string;
  status: string;
  budget_estimate_cr: number | null;
  revised_estimate_cr: number | null;
  actual_cr: number | null;
  below_estimate_cr: number;
  below_estimate_pct: number | null;
  /** 'actual_vs_budget_estimate' | 'revised_vs_budget_estimate'. */
  compare: string;
  /** Why actuals were not compared ('not_published' | 'unmatched'), or null when actuals were compared. */
  actuals_reason?: 'not_published' | 'unmatched' | null;
}

export interface VisualUnionBudget {
  lines: number;
  lines_with_actuals: number;
  lines_alone: number;
  alone_from_crore: number;
  set_size: number;
  statements: number;
  financial_years: string[];
  generated_on: string;
  leads: number;
  verified: number;
  peer_review: number;
  cleared_tasks: number;
  below_estimate_cr: number;
  /** One entry per financial year, oldest first. */
  years: VisualUnionBudgetYear[];
  /** Up to 10, largest shortfall first. */
  top_leads: VisualUnionBudgetLead[];
  documents_url: string;
  coverage_url: string;
}

export interface VisualOneRoadTwoBillsYear {
  year: string;
  reports: number;
  pages: number;
  blocks: number;
  tasks: number;
  done: number;
  claimed: number;
  open: number;
  pct: number;
  leads: number;
  flagged_inr_cr: number;
}

export interface VisualOneRoadTwoBillsLead {
  finding_id: string;
  title: string;
  entity_name: string;
  fiscal_year: string;
  status: string;
  report_no: string;
  page: number | null;
  para_number: string;
  signal_category: string;
  flagged_inr_cr: number;
}

export interface VisualOneRoadTwoBills {
  reports: number;
  pages: number;
  blocks: number;
  report_years: string[];
  generated_on: string;
  leads: number;
  verified: number;
  peer_review: number;
  cleared_tasks: number;
  flagged_inr_cr: number;
  by_signal: Record<string, number>;
  years: VisualOneRoadTwoBillsYear[];
  top_leads: VisualOneRoadTwoBillsLead[];
  documents_url: string;
  coverage_url: string;
}

export interface VisualOneTeacherFiveGradesYear {
  year: string;
  tasks: number;
  done: number;
  claimed: number;
  open: number;
  pct: number;
  leads: number;
  all_india_single_teacher_schools: number;
  all_india_single_teacher_enrolments: number;
}

export interface VisualOneTeacherFiveGradesState {
  finding_id: string;
  state_name: string;
  academic_year: string;
  status: string;
  signal_category: string;
  single_teacher_schools: number;
  total_schools: number;
  single_teacher_share_pct: number;
  single_teacher_enrolments: number;
  students_per_single_teacher: number;
  gov_primary_schools: number;
  gov_primary_teachers_per_school: number | null;
}

export interface VisualOneTeacherFiveGrades {
  academic_years: string[];
  states_covered: number;
  total_tasks: number;
  generated_on: string;
  national_baseline: {
    total_schools?: number;
    single_teacher_schools?: number;
    single_teacher_enrolments?: number;
    single_teacher_share_pct?: number;
    students_per_single_teacher?: number;
    gov_primary_1_5_schools?: number;
    gov_primary_teachers_per_school?: number;
  };
  leads: number;
  verified: number;
  peer_review: number;
  cleared_tasks: number;
  flagged_students_total: number;
  by_signal: Record<string, number>;
  years: VisualOneTeacherFiveGradesYear[];
  top_states: VisualOneTeacherFiveGradesState[];
  documents_url: string;
  coverage_url: string;
}

export interface VisualParliamentHouseCounts {
  house?: string;
  tasks: number;
  done?: number;
  claimed?: number;
  open?: number;
  pct?: number;
  pending?: number;
  pending_count?: number;
  fully_implemented?: number;
  fulfilled?: number;
  fulfilled_count?: number;
  partly_implemented?: number;
  partly_implemented_count?: number;
  dropped?: number;
  dropped_count?: number;
  leads?: number;
}

export interface VisualParliamentAnchorCase {
  task_id: string;
  finding_id?: string | null;
  assurance_ref: string;
  house: string;
  session: string;
  promise_date: string;
  ministry: string;
  member?: string | null;
  minister?: string | null;
  subject: string;
  recorded_status: string;
  days_elapsed?: number | null;
  original_promise: string;
  followup_answer: string;
  source_url?: string | null;
  secondary_url?: string | null;
}

export interface VisualParliamentMinistry {
  ministry: string;
  ministry_key?: string;
  tasks: number;
  done: number;
  claimed: number;
  open: number;
  pct: number;
  lok_sabha_tasks?: number;
  rajya_sabha_tasks?: number;
  pending_count: number;
  fulfilled_count: number;
  partly_implemented_count?: number;
  dropped_count?: number;
  other_count?: number;
  leads: number;
  verified?: number;
}

export interface VisualParliamentLead {
  finding_id: string;
  title: string;
  ministry: string;
  house: string;
  session: string;
  assurance_ref: string;
  promise_date: string;
  recorded_status: string;
  signal_category: string;
  days_elapsed?: number | null;
  original_promise: string;
  followup_answer: string;
  status: string;
}

export interface VisualParliamentsToDoList {
  hook?: string;
  total_tasks: number;
  ministries_covered: number;
  houses?: string[];
  by_house?: Record<string, VisualParliamentHouseCounts | number>;
  generated_on: string;
  as_of_date?: string;
  by_recorded_status: Record<string, number>;
  leads: number;
  verified: number;
  peer_review: number;
  cleared_tasks: number;
  by_signal: Record<string, number>;
  anchor_case?: VisualParliamentAnchorCase | null;
  ministries: VisualParliamentMinistry[];
  top_leads: VisualParliamentLead[];
  documents_url: string;
  coverage_url: string;
}

export interface ProjectVisualSummary {
  methodology_code: string;
  status_counts: VisualStatusCounts;
  budget_audit?: VisualBudgetAudit;
  website_audit?: VisualWebsiteAudit;
  union_budget?: VisualUnionBudget;
  one_road_two_bills?: VisualOneRoadTwoBills;
  one_teacher_five_grades?: VisualOneTeacherFiveGrades;
  parliaments_to_do_list?: VisualParliamentsToDoList;
}

export interface MissionItem {
  id?: string | number;
  slug: string;
  project_code?: string;
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
  claimed_tasks?: number;
  queued_tasks?: number;
  peer_checks_total?: number;
  peer_checks_done?: number;
  peer_checks_open?: number;
  progress_pct?: number;
  reviews_required?: number;
  reviews_done?: number;
  reviews_remaining?: number;
  required_reviews?: number;
  per_review_pct?: number;
  task_progress_pct?: number;
  review_progress_pct?: number;
  review_remaining_pct?: number;
  task_weight_pct?: number;
  review_weight_pct?: number;
  has_reviews_remaining?: boolean;
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
  /** Number of tasks in the inline `tasks` array. */
  tasks_listed?: number;
  /** True when `tasks` holds only the first slice of a large project's tasks. */
  tasks_truncated?: boolean;
  is_community_project?: boolean;
  posted_by?: ProjectPoster;
  project_sources?: ProjectSource[];
  project_scope?: { scope: string[]; out_of_scope: string[] };
  enlisted_pr_url?: string;
  planned_tasks?: PlannedTask[];
  token_summary?: TokenSummary;
  contributors_all?: AgentRecord[];
  contributors_hour?: AgentRecord[];
  visual_summary?: ProjectVisualSummary;
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
  attempt_count?: number;
  created_at?: string;
  work_type?: string;
  estimated_tokens?: number;
  estimate_basis?: string;
  usage?: TaskUsage;
}

export type ProjectTaskStatusFilter = 'all' | 'open' | 'claimed' | 'completed';
export type ProjectTaskKindFilter = 'all' | 'audit' | 'check';

/** One page of `GET /api/missions/{slug}/tasks`. */
export interface ProjectTaskPage {
  slug: string;
  total: number;
  offset: number;
  limit: number;
  counts: Record<ProjectTaskStatusFilter, number>;
  tasks: MissionTask[];
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
  role?: string;
  source_url?: string;
  /** Primary dataset of the finding; stamped on every citation by the CLI. */
  source_ref?: string;
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

export interface FindingPatternItem {
  id: string;
  label: string;
  fiscal_year: string;
  amount_inr: number;
}

export interface FindingPattern {
  key: string;
  mission_slug: string;
  mission_title: string;
  methodology_code: string;
  group: string;
  group_kind: 'department' | 'jurisdiction';
  count: number;
  total_inr: number;
  fy_from: string;
  fy_to: string;
  jurisdiction_count: number;
  items: FindingPatternItem[];
}

export interface FindingPatterns {
  patterns: FindingPattern[];
  pattern_count: number;
  finding_count: number;
  by_methodology?: Record<string, number>;
  by_jurisdiction?: Record<string, number>;
  total_inr: number;
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
}

export interface LiveData {
  agents: AgentRecord[];
  whispers: WhisperItem[];
  recent_verifications: PeerVerification[];
  model_breakdown: Array<{
    provider: string;
    model: string;
    coding_agent?: string;
    coding_agent_model?: string;
    agents: number;
    tasks: number;
    share_pct?: number;
  }>;
  latest_event_id?: number;
}

export type LiveSwarmNodeStatus = 'working' | 'verifying' | 'recent' | 'idle';

export type LiveSwarmRole = 'auditor' | 'peer_verifier' | 'adjudicator' | string | null;

export interface LiveSwarmNode {
  handle: string;
  display_name?: string;
  status: LiveSwarmNodeStatus;
  role: LiveSwarmRole;
  coding_agent?: string | null;
  coding_agent_model?: string | null;
  provider?: string;
  model?: string;
  current_task_id?: string | null;
  current_task_title?: string | null;
  current_project_slug?: string | null;
  progress_pct?: number | null;
  latest_whisper?: string | null;
  tasks_completed: number;
  verifications_done: number;
  findings_submitted: number;
  last_seen_at?: string;
  created_at?: string;
}

export interface LiveRecentJoin {
  handle: string;
  display_name?: string;
  coding_agent?: string | null;
  coding_agent_model?: string | null;
  created_at: string;
  last_seen_at?: string;
  is_working?: boolean;
}

export interface LiveSwarmWhisper {
  id: string;
  event_type: string;
  handle: string;
  coding_agent?: string | null;
  role?: LiveSwarmRole;
  task_id?: string | null;
  project_slug?: string | null;
  finding_id?: string | null;
  whisper: string;
  created_at: string;
}

export interface LiveSwarmProgress {
  tasks_total: number;
  tasks_completed: number;
  tasks_in_progress: number;
  findings_verified: number;
  findings_in_review: number;
  findings_cleared?: number;
}

export interface LiveSwarmSnapshot {
  generated_at: string;
  latest_event_id?: number;
  live_window_seconds?: number;
  working_now: number;
  active_leases_count: number;
  total_agents: number;
  total_sessions?: number;
  tasks_completed_total?: number;
  tasks_last_hour?: number;
  last_activity_at?: string | null;
  active_nodes: LiveSwarmNode[];
  recent_joins: LiveRecentJoin[];
  live_whispers: LiveSwarmWhisper[];
  progress: LiveSwarmProgress;
}

export interface ContributorProfile {
  agent: AgentRecord;
  submitted_findings: FindingItem[];
  verifications: PeerVerification[];
  recent_whispers: WhisperItem[];
}

export type GraveyardCause =
  | 'false_positive'
  | 'editorial_retraction'
  | 'peer_dispute'
  | 'withdrawn_review';

export interface GraveyardEntry {
  id: string;
  title: string;
  status: string;
  cause: GraveyardCause;
  cause_label: string;
  methodology_code: string;
  entity_name: string;
  jurisdiction: string;
  fiscal_year: string;
  monetary_exposure_inr_cr: number;
  mission_slug: string;
  project_title: string;
  proposed_at: string;
  ended_at: string;
  lived_seconds: number;
  claimed: string;
  cause_detail: string;
  source_evidence_url?: string | null;
  survived: string;
  confirm_count: number;
  dispute_count: number;
  corrections_count: number;
  rti_on_hold: boolean;
}

export interface GraveyardResponse {
  total: number;
  offset: number;
  limit: number;
  has_more: boolean;
  counts_by_cause: Record<string, number>;
  projects: Array<{ slug: string; title: string }>;
  entries: GraveyardEntry[];
}
