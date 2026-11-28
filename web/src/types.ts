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
  github_verified?: boolean;
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
  github_verified?: boolean;
  github_verified_at?: string | null;
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

export interface VisualOnlyOneBidCohortCounts {
  cohort?: string;
  label?: string;
  tasks: number;
  done: number;
  claimed: number;
  open: number;
  pct: number;
  leads?: number;
  flagged_inr_cr?: number;
}

export interface VisualOnlyOneBidYear {
  /** Calendar or financial year label, e.g. "2016" or "2016-17". */
  year: string;
  fiscal_year?: string;
  tasks: number;
  done: number;
  claimed: number;
  open: number;
  pct: number;
  single_bidder_tasks: number;
  repeat_winner_tasks: number;
  both_tasks: number;
  leads: number;
  flagged_inr_cr: number;
}

export interface VisualOnlyOneBidJurisdiction {
  jurisdiction: string;
  jurisdiction_level?: 'central' | 'state' | string;
  portal?: string;
  tasks: number;
  done?: number;
  claimed?: number;
  open?: number;
  pct?: number;
  single_bidder_tasks?: number;
  repeat_winner_tasks?: number;
  both_tasks?: number;
  authorities?: number;
  leads?: number;
  flagged_inr_cr?: number;
}

export interface VisualOnlyOneBidAuthority {
  authority: string;
  authority_key?: string;
  jurisdiction?: string;
  jurisdiction_level?: 'central' | 'state' | string;
  portal?: string;
  tasks: number;
  done?: number;
  claimed?: number;
  open?: number;
  pct?: number;
  single_bidder_tasks?: number;
  repeat_winner_tasks?: number;
  both_tasks?: number;
  single_bid_rate_pct?: number | null;
  distinct_repeat_winners?: number;
  leads: number;
  verified?: number;
  flagged_inr_cr?: number;
  total_value_inr_cr?: number;
}

export interface VisualOnlyOneBidRepeatWinner {
  winner_name: string;
  normalized_winner_key?: string;
  authority: string;
  authority_key?: string;
  jurisdiction?: string;
  portal?: string;
  win_count?: number;
  total_wins?: number;
  winner_total_wins?: number;
  single_bid_wins?: number;
  winner_single_bid_wins?: number;
  total_value_inr_cr?: number | null;
  cohort_total_inr_cr?: number | null;
  cohort_total_inr?: number | null;
  winner_cohort_total_inr?: number | null;
  share_by_count_pct?: number | null;
  winner_share_by_count_pct?: number | null;
  share_by_value_pct?: number | null;
  winner_share_by_value_pct?: number | null;
  consecutive_win_streak?: number | null;
  tasks?: number;
  leads?: number;
  year_range?: string;
  sample_tender_ids?: string[];
  related_tender_ids?: string[];
  finding_id?: string | null;
}

export interface VisualOnlyOneBidAnchorCase {
  task_id?: string;
  case_key?: string;
  finding_id?: string | null;
  portal?: string;
  authority: string;
  jurisdiction?: string;
  jurisdiction_level?: 'central' | 'state' | string;
  tender_id?: string;
  ocid?: string;
  tender_title?: string;
  title?: string;
  fiscal_year?: string;
  audit_mode?: string;
  signal_category?: string;
  procurement_method?: string;
  bids_received?: number | null;
  technically_qualified_bids?: number | null;
  effective_bids?: number | null;
  single_bid_mechanism?: string | null;
  bid_window_days?: number | null;
  winner_name?: string;
  winner_total_wins?: number | null;
  winner_single_bid_wins?: number | null;
  winner_share_by_count_pct?: number | null;
  winner_share_by_value_pct?: number | null;
  consecutive_win_streak?: number | null;
  amount_type?: string;
  award_value_inr?: number | null;
  tender_estimate_inr?: number | null;
  target_value_inr_cr?: number | null;
  winner_cohort_total_inr?: number | null;
  winner_cohort_total_inr_cr?: number | null;
  governing_instrument?: string;
  summary?: string;
  source_url?: string | null;
  secondary_url?: string | null;
  record_url?: string | null;
}

export interface VisualOnlyOneBidLead {
  finding_id: string;
  title: string;
  authority?: string;
  entity_name?: string;
  jurisdiction?: string;
  portal?: string;
  fiscal_year?: string;
  tender_id?: string;
  ocid?: string;
  audit_mode?: string;
  signal_category: string;
  verdict?: string;
  bids_received?: number | null;
  technically_qualified_bids?: number | null;
  effective_bids?: number | null;
  single_bid_mechanism?: string | null;
  bid_window_days?: number | null;
  winner_name?: string;
  winner_total_wins?: number | null;
  winner_single_bid_wins?: number | null;
  winner_share_by_count_pct?: number | null;
  consecutive_win_streak?: number | null;
  amount_type?: string;
  target_value_inr?: number | null;
  target_value_inr_cr?: number | null;
  flagged_inr_cr?: number | null;
  monetary_exposure_inr?: number | null;
  winner_cohort_total_inr?: number | null;
  winner_cohort_total_inr_cr?: number | null;
  status: string;
}

export interface VisualOnlyOneBid {
  hook?: string;
  generated_on?: string;
  as_of_date?: string;
  total_tasks: number;
  year_range?: string | string[];
  portals_covered?: number;
  authorities_covered?: number;
  distinct_repeat_winners?: number;
  leads: number;
  verified: number;
  peer_review: number;
  cleared_tasks: number;
  flagged_total_inr_cr: number;
  by_cohort?: Record<string, VisualOnlyOneBidCohortCounts | number>;
  by_signal?: Record<string, number>;
  years?: VisualOnlyOneBidYear[];
  jurisdictions?: VisualOnlyOneBidJurisdiction[];
  top_authorities?: VisualOnlyOneBidAuthority[];
  top_repeat_winners?: VisualOnlyOneBidRepeatWinner[];
  anchor_case?: VisualOnlyOneBidAnchorCase | null;
  top_leads?: VisualOnlyOneBidLead[];
  documents_url?: string;
  coverage_url?: string;
}

export type SovereignLiesJurisdictionLevel = 'union' | 'state' | 'ut';

export type SovereignLiesSchemeCategory =
  | 'sovereign_bonds_small_savings'
  | 'pensions_retirement'
  | 'tax_exempt_instruments'
  | 'cash_transfers_guarantees'
  | 'ev_housing_farm_subsidies'
  | 'scholarships_education'
  | 'industrial_export_incentives'
  | 'welfare_health_public_services';

export type SovereignLiesWalkbackMechanism =
  | 'retroactive_tax_reversal'
  | 'midstream_benefit_cut'
  | 'premature_discontinuation'
  | 'eligibility_narrowing'
  | 'lockin_or_exit_restriction'
  | 'arrears_or_unfunded_promise'
  | 'grandfathered_or_honored';

export type SovereignLiesRetroactiveImpact =
  | 'retroactive_existing_holders'
  | 'mid_stream_active_scheme'
  | 'prospective_discontinuation'
  | 'eligibility_narrowing'
  | 'unfulfilled_arrears'
  | 'grandfathered_or_honored';

export interface VisualSovereignLiesTierCounts {
  tasks: number;
  done: number;
  claimed: number;
  open: number;
  pct: number;
  leads?: number;
  cleared?: number;
  flagged_inr_cr?: number;
}

export interface VisualSovereignLiesMechanismCounts {
  tasks: number;
  leads: number;
  done?: number;
  cleared?: number;
}

export interface VisualSovereignLiesCategory {
  category: SovereignLiesSchemeCategory | string;
  label?: string;
  tasks: number;
  done?: number;
  leads: number;
  cleared?: number;
  flagged_inr_cr?: number;
}

export interface VisualSovereignLiesJurisdiction {
  jurisdiction: string;
  jurisdiction_level: SovereignLiesJurisdictionLevel | string;
  state_or_ut?: string | null;
  tasks: number;
  done?: number;
  claimed?: number;
  open?: number;
  pct?: number;
  leads: number;
  cleared?: number;
  flagged_inr_cr?: number;
}

export interface VisualSovereignLiesScheme {
  scheme_name: string;
  scheme_slug: string;
  jurisdiction: string;
  jurisdiction_level: SovereignLiesJurisdictionLevel | string;
  state_or_ut?: string | null;
  scheme_category: SovereignLiesSchemeCategory | string;
  walkback_mechanism: SovereignLiesWalkbackMechanism | string;
  retroactive_impact: SovereignLiesRetroactiveImpact | string;
  tasks: number;
  done?: number;
  leads: number;
  cleared?: number;
  is_sgb_anchor?: boolean;
}

export interface VisualSovereignLiesSgbAnchor {
  task_id?: string;
  finding_id?: string | null;
  scheme_name: string;
  tranche_or_phase?: string;
  jurisdiction: string;
  authority: string;
  promise_date?: string;
  promise_summary: string;
  promise_document_ref?: string;
  walkback_date?: string | null;
  walkback_summary: string;
  walkback_document_ref?: string;
  walkback_mechanism?: SovereignLiesWalkbackMechanism | string;
  retroactive_impact?: SovereignLiesRetroactiveImpact | string;
  promised_metric?: string;
  revised_metric?: string;
  metric_unit?: string;
  affected_group?: string;
  days_from_promise_to_walkback?: number | null;
  source_url?: string | null;
  secondary_url?: string | null;
  portal_url?: string | null;
  source_sha256?: string | null;
  secondary_sha256?: string | null;
}

export interface VisualSovereignLiesLead {
  finding_id: string;
  title: string;
  scheme_name: string;
  scheme_slug?: string;
  tranche_or_phase?: string;
  entity_name?: string;
  jurisdiction: string;
  jurisdiction_level?: SovereignLiesJurisdictionLevel | string;
  state_or_ut?: string | null;
  scheme_category?: SovereignLiesSchemeCategory | string;
  walkback_mechanism: SovereignLiesWalkbackMechanism | string;
  retroactive_impact?: SovereignLiesRetroactiveImpact | string;
  signal_category: string;
  promise_date?: string;
  walkback_date?: string | null;
  days_from_promise_to_walkback?: number | null;
  promised_metric?: string;
  revised_metric?: string;
  metric_unit?: string;
  promise_summary?: string;
  walkback_summary?: string;
  affected_group?: string;
  is_sgb_anchor?: boolean;
  monetary_exposure_inr?: number | null;
  flagged_inr_cr?: number | null;
  severity?: string;
  status: string;
}

export interface VisualSovereignLies {
  hook?: string;
  generated_on?: string;
  as_of_date?: string;
  total_tasks: number;
  unique_schemes_covered?: number;
  states_and_uts_covered?: number;
  sgb_tasks_count?: number;
  cleared_control_tasks_embedded?: number;
  leads: number;
  verified: number;
  peer_review: number;
  cleared_tasks: number;
  flagged_total_inr_cr: number;
  by_jurisdiction_level?: Record<string, VisualSovereignLiesTierCounts | number>;
  by_mechanism?: Record<string, VisualSovereignLiesMechanismCounts | number>;
  by_signal?: Record<string, number>;
  by_retroactive_impact?: Record<string, number>;
  by_category?: VisualSovereignLiesCategory[];
  jurisdictions?: VisualSovereignLiesJurisdiction[];
  top_schemes?: VisualSovereignLiesScheme[];
  sgb_anchor_case?: VisualSovereignLiesSgbAnchor | null;
  top_leads?: VisualSovereignLiesLead[];
  documents_url?: string;
  coverage_url?: string;
}

export type ExamJurisdictionLevel = 'union' | 'state' | 'ut';

export type ExamDisruptionType =
  | 'cancelled_after_conduct'
  | 'postponed'
  | 'rescheduled'
  | 'delayed'
  | 'cancelled_before_conduct'
  | 'partial_reexam_or_glitch'
  | 'flawless_conduct';

export type ExamAuthorityType =
  | 'central_testing_agency'
  | 'union_psc_or_recruitment'
  | 'railway_banking_psu'
  | 'defence_medical_technical'
  | 'state_psc'
  | 'state_police_or_subordinate'
  | 'state_teacher_or_entrance'
  | 'ut_recruitment_or_board';

export type ExamCategory =
  | 'civil_services_and_psc'
  | 'police_and_defence'
  | 'teaching_and_faculty'
  | 'medical_and_health'
  | 'engineering_and_higher_ed'
  | 'banking_railways_ssc'
  | 'clerical_and_subordinate'
  | 'board_and_other_public';

export type ExamDisruptionReasonCategory =
  | 'paper_integrity_compromise'
  | 'administrative_or_logistical'
  | 'technical_or_cbt_glitch'
  | 'election_or_calendar_clash'
  | 'weather_or_disaster'
  | 'judicial_or_policy_review'
  | 'none_flawless';

export interface VisualExamAuditWindow {
  start_date: string;
  end_date: string;
  years: number[];
  label: string;
}

export interface VisualExamHeadlineCounts {
  total_audited: number;
  not_flawless_count: number;
  flawless_count: number;
  not_flawless_rate_pct: number;
  flawless_rate_pct: number;
  cancelled_after_conduct: number;
  postponed: number;
  rescheduled: number;
  delayed: number;
  cancelled_before_conduct: number;
  partial_reexam_or_glitch: number;
  documented_affected_candidates_total: number;
}

export interface VisualExamDisruptionBucket {
  disruption_type: ExamDisruptionType | string;
  label?: string;
  rule?: string | null;
  tasks: number;
  done?: number;
  leads: number;
  cleared?: number;
  pct?: number;
  documented_affected_candidates?: number;
}

export interface VisualExamTierCounts {
  tasks: number;
  done: number;
  claimed: number;
  open: number;
  pct: number;
  leads?: number;
  cleared?: number;
  not_flawless?: number;
  flawless?: number;
  cancelled_after_conduct?: number;
}

export interface VisualExamAuthorityType {
  authority_type: ExamAuthorityType | string;
  label?: string;
  tasks: number;
  done?: number;
  leads: number;
  not_flawless: number;
  flawless: number;
  cancelled_after_conduct: number;
}

export interface VisualExamCategory {
  exam_category: ExamCategory | string;
  label?: string;
  tasks: number;
  done?: number;
  leads: number;
  not_flawless: number;
  flawless: number;
  cancelled_after_conduct: number;
}

export interface VisualExamYear {
  year: number | string;
  tasks: number;
  done?: number;
  leads?: number;
  not_flawless: number;
  flawless: number;
  cancelled_after_conduct: number;
  postponed: number;
  rescheduled: number;
  delayed: number;
  cancelled_before_conduct: number;
  partial_reexam_or_glitch: number;
}

export interface VisualExamReasonCategory {
  reason_category: ExamDisruptionReasonCategory | string;
  label?: string;
  tasks: number;
  leads: number;
}

export interface VisualExamJurisdiction {
  jurisdiction: string;
  jurisdiction_level: ExamJurisdictionLevel | string;
  state_or_ut?: string | null;
  tasks: number;
  done?: number;
  leads: number;
  cleared?: number;
  not_flawless: number;
  flawless: number;
  cancelled_after_conduct: number;
  postponed: number;
  rescheduled: number;
  delayed: number;
  cancelled_before_conduct: number;
  partial_reexam_or_glitch: number;
  not_flawless_rate_pct?: number;
  documented_affected_candidates?: number;
}

export interface VisualExamAuthority {
  authority: string;
  authority_key: string;
  authority_type: ExamAuthorityType | string;
  jurisdiction: string;
  jurisdiction_level: ExamJurisdictionLevel | string;
  tasks: number;
  done?: number;
  leads: number;
  not_flawless: number;
  flawless: number;
  cancelled_after_conduct: number;
  postponed: number;
  rescheduled: number;
  delayed: number;
  cancelled_before_conduct: number;
  partial_reexam_or_glitch: number;
  not_flawless_rate_pct?: number;
  documented_affected_candidates?: number;
}

export interface VisualExamAnchorCase {
  task_id?: string;
  case_key: string;
  finding_id?: string | null;
  exam_name: string;
  exam_slug?: string;
  cycle_or_stage?: string;
  exam_year: number;
  jurisdiction: string;
  jurisdiction_level: ExamJurisdictionLevel | string;
  state_or_ut?: string | null;
  authority: string;
  authority_key?: string;
  authority_type?: ExamAuthorityType | string;
  exam_category?: ExamCategory | string;
  scheduled_date?: string;
  actual_or_event_date?: string;
  delay_days?: number | null;
  disruption_type: ExamDisruptionType | string;
  disruption_reason_category?: ExamDisruptionReasonCategory | string;
  students_took_exam_before_cancellation?: boolean;
  schedule_summary: string;
  schedule_document_ref?: string;
  outcome_summary: string;
  outcome_document_ref?: string;
  affected_candidates_count?: number | null;
  affected_candidates_basis?: string;
  statutory_anchor?: string;
  source_url?: string | null;
  secondary_url?: string | null;
  portal_url?: string | null;
  severity?: string;
}

export interface VisualExamLead {
  finding_id?: string | null;
  task_id?: string;
  case_key: string;
  title: string;
  exam_name: string;
  exam_slug?: string;
  cycle_or_stage?: string;
  exam_year: number;
  jurisdiction: string;
  jurisdiction_level?: ExamJurisdictionLevel | string;
  state_or_ut?: string | null;
  authority: string;
  authority_key?: string;
  authority_type?: ExamAuthorityType | string;
  exam_category?: ExamCategory | string;
  scheduled_date?: string;
  actual_or_event_date?: string;
  delay_days?: number | null;
  disruption_type: ExamDisruptionType | string;
  disruption_reason_category?: ExamDisruptionReasonCategory | string;
  signal_category?: string;
  rule_triggered?: string;
  students_took_exam_before_cancellation?: boolean;
  schedule_summary?: string;
  schedule_document_ref?: string;
  outcome_summary?: string;
  outcome_document_ref?: string;
  affected_candidates_count?: number | null;
  affected_candidates_basis?: string;
  statutory_anchor?: string;
  source_url?: string | null;
  secondary_url?: string | null;
  portal_url?: string | null;
  is_anchor_case?: boolean;
  severity?: string;
  status: string;
}

export interface VisualExamDryRunSummary {
  status: string;
  evaluated_tasks: number;
  flagged_leads: number;
  cleared_flawless: number;
  unevaluable: number;
  verified_documents: number;
  as_of_date?: string;
}

export interface VisualAuditingGovernmentExams {
  project_slug?: string;
  project_code?: string;
  methodology_code?: string;
  hook?: string;
  generated_on?: string;
  as_of_date?: string;
  audit_window?: VisualExamAuditWindow;
  total_tasks: number;
  completed_tasks?: number;
  published_leads?: number;
  verified: number;
  peer_review: number;
  cleared_tasks: number;
  unevaluable_tasks?: number;
  unique_exams_covered?: number;
  unique_authorities_covered?: number;
  unique_documents_count?: number;
  states_and_uts_covered?: number;
  leads_source?: 'live' | 'corpus_fallback' | string;
  documents_url?: string;
  coverage_url?: string;
  headline_counts?: VisualExamHeadlineCounts;
  by_disruption_type?: Record<string, VisualExamDisruptionBucket | number>;
  by_jurisdiction_level?: Record<string, VisualExamTierCounts | number>;
  by_authority_type?: VisualExamAuthorityType[];
  by_exam_category?: VisualExamCategory[];
  by_year?: VisualExamYear[];
  by_reason_category?: VisualExamReasonCategory[];
  jurisdictions?: VisualExamJurisdiction[];
  top_authorities?: VisualExamAuthority[];
  anchor_cases?: VisualExamAnchorCase[];
  leads?: VisualExamLead[];
  dry_run_summary?: VisualExamDryRunSummary | null;
}

export type InnocentButLockedUpPortalCategory =
  | 'court_portal'
  | 'prison_portal'
  | 'police_portal'
  | 'nhrc_shrc'
  | 'nalsa_slsa'
  | 'parliament'
  | 'govt_ministry'
  | 'cag_audit';

export type InnocentButLockedUpCourtTier =
  | 'supreme_court'
  | 'high_court'
  | 'district_court'
  | 'statutory_body';

export type InnocentButLockedUpMechanism =
  | 'post_acquittal_contempt_overrun'
  | 'bail_or_release_order_contempt'
  | 'warrant_or_faster_transmission_delay'
  | 'statutory_commission_detention_finding'
  | 'institutional_compliance_gap'
  | 'timely_release_or_compliant';

export type InnocentButLockedUpChainStatus =
  | 'full_4_stage_chain'
  | 'partial_3_stage'
  | 'institutional_record'
  | 'compliant_control';

export interface VisualInnocentButLockedUpPortal {
  portal_category: InnocentButLockedUpPortalCategory | string;
  label?: string;
  tasks: number;
  done?: number;
  claimed?: number;
  open?: number;
  pct?: number;
  leads?: number;
  cleared?: number;
}

export interface VisualInnocentButLockedUpYear {
  year: number | string;
  tasks: number;
  done?: number;
  claimed?: number;
  open?: number;
  pct?: number;
  leads?: number;
  cleared?: number;
  full_4_stage?: number;
  overrun_days?: number;
}

export interface VisualInnocentButLockedUpCourtTierCounts {
  tasks: number;
  done?: number;
  leads?: number;
  cleared?: number;
}

export interface VisualInnocentButLockedUpMechanismCounts {
  tasks: number;
  leads: number;
  done?: number;
  cleared?: number;
}

export interface VisualInnocentButLockedUpState {
  state_ut?: string | null;
  jurisdiction?: string | null;
  tasks: number;
  done?: number;
  leads: number;
  cleared?: number;
  full_4_stage?: number;
  total_overrun_days?: number;
  compensation_inr?: number;
}

export interface VisualInnocentButLockedUpCourt {
  court_name: string;
  court_tier: InnocentButLockedUpCourtTier | string;
  state_ut?: string | null;
  tasks: number;
  leads: number;
  full_4_stage?: number;
}

export interface VisualInnocentButLockedUpPrison {
  prison_name: string;
  state_ut?: string | null;
  tasks: number;
  leads: number;
  total_overrun_days?: number;
}

export interface VisualInnocentButLockedUpAnchor {
  task_id?: string;
  finding_id?: string | null;
  case_title: string;
  portal_category: InnocentButLockedUpPortalCategory | string;
  court_tier?: InnocentButLockedUpCourtTier | string;
  state_ut?: string | null;
  jurisdiction?: string | null;
  year?: number | string | null;
  document_ref?: string | null;
  overrun_mechanism?: InnocentButLockedUpMechanism | string;
  stages_verified_count?: number | null;
  chain_status?: InnocentButLockedUpChainStatus | string;
  acquittal_order_date?: string | null;
  acquittal_court?: string | null;
  acquittal_case_number?: string | null;
  acquittal_order_type?: string | null;
  acquittal_quote?: string | null;
  prison_name?: string | null;
  custody_authority?: string | null;
  non_release_reason?: string | null;
  overrun_days?: number | null;
  overrun_window?: string | null;
  contempt_case_number?: string | null;
  contempt_court?: string | null;
  contempt_filing_date?: string | null;
  contempt_order_date?: string | null;
  contempt_quote?: string | null;
  release_date?: string | null;
  release_verified?: boolean;
  compensation_inr?: number | null;
  judicial_direction_summary?: string | null;
  source_url?: string | null;
  secondary_url?: string | null;
  portal_url?: string | null;
  source_sha256?: string | null;
  secondary_sha256?: string | null;
}

export interface VisualInnocentButLockedUpLead {
  finding_id: string;
  title: string;
  case_key?: string;
  case_title: string;
  entity_name?: string;
  portal_category: InnocentButLockedUpPortalCategory | string;
  court_tier?: InnocentButLockedUpCourtTier | string;
  state_ut?: string | null;
  jurisdiction?: string | null;
  year?: number | string | null;
  document_ref?: string | null;
  overrun_mechanism: InnocentButLockedUpMechanism | string;
  signal_category: string;
  stages_verified_count?: number | null;
  chain_status?: InnocentButLockedUpChainStatus | string;
  acquittal_order_date?: string | null;
  acquittal_court?: string | null;
  acquittal_case_number?: string | null;
  acquittal_order_type?: string | null;
  acquittal_quote?: string | null;
  prison_name?: string | null;
  custody_authority?: string | null;
  non_release_reason?: string | null;
  overrun_days?: number | null;
  overrun_window?: string | null;
  contempt_case_number?: string | null;
  contempt_court?: string | null;
  contempt_filing_date?: string | null;
  contempt_order_date?: string | null;
  contempt_quote?: string | null;
  release_date?: string | null;
  release_verified?: boolean;
  compensation_inr?: number | null;
  judicial_direction_summary?: string | null;
  is_anchor_case?: boolean;
  severity?: string;
  status: string;
}

export interface VisualInnocentButLockedUp {
  hook?: string;
  generated_on?: string;
  as_of_date?: string;
  year_range?: [number, number] | number[] | string;
  total_tasks: number;
  unique_documents_covered?: number;
  states_and_uts_covered?: number;
  courts_covered?: number;
  prisons_named?: number;
  full_4_stage_tasks?: number;
  contempt_tasks?: number;
  verified_release_tasks?: number;
  cleared_control_tasks_embedded?: number;
  total_overrun_days_documented?: number;
  median_overrun_days?: number | null;
  max_overrun_days?: number | null;
  total_compensation_ordered_inr?: number;
  leads: number;
  verified: number;
  peer_review: number;
  cleared_tasks: number;
  flagged_compensation_inr_cr?: number;
  by_portal_category?:
    | VisualInnocentButLockedUpPortal[]
    | Record<string, VisualInnocentButLockedUpPortal | number>;
  by_year?: VisualInnocentButLockedUpYear[];
  by_court_tier?: Record<string, VisualInnocentButLockedUpCourtTierCounts | number>;
  by_mechanism?: Record<string, VisualInnocentButLockedUpMechanismCounts | number>;
  by_signal?: Record<string, number>;
  by_chain_status?: Record<string, number>;
  states?: VisualInnocentButLockedUpState[];
  top_courts?: VisualInnocentButLockedUpCourt[];
  top_prisons?: VisualInnocentButLockedUpPrison[];
  anchor_case?: VisualInnocentButLockedUpAnchor | null;
  top_leads?: VisualInnocentButLockedUpLead[];
  documents_url?: string;
  coverage_url?: string;
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
  only_one_bid?: VisualOnlyOneBid;
  sovereign_lies?: VisualSovereignLies;
  auditing_government_exams?: VisualAuditingGovernmentExams;
  innocent_but_locked_up?: VisualInnocentButLockedUp;
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
  created_at?: string;
  posted_by?: ProjectPoster;
  plain_answer?: string;
  plain_answer_finding?: string | null;
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
  methodology_code?: string;
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
  plain?: PlainCard | null;
}

export interface PlainCard {
  lang: string;
  dir: 'ltr' | 'rtl' | string;
  kind: string;
  source: 'template' | 'model' | string;
  model?: string;
  head: string;
  what: string;
  why: string;
  ask: string;
  project: string;
  status: string;
  not: string;
  speak: string;
}

export interface PlainLanguage {
  code: string;
  name: string;
  native: string;
  dir: string;
  bcp47: string;
  beta?: boolean;
}

export interface NewsStory {
  finding_id: string;
  head: string;
  what: string;
  why: string;
  ask: string;
  status: string;
  project: string;
  kind: string;
  source: string;
  amount: string;
  source_url: string;
}

export interface NewsProjectState {
  slug: string;
  name: string;
  lines: string[];
  finding_id: string | null;
  source_url: string;
  project_url: string;
  prompt: string;
}

export interface NewsSection {
  id: string;
  title: string;
  stories?: NewsStory[];
  lines?: { text: string; finding_id?: string }[];
  projects?: NewsProjectState[];
  big?: string;
  heading?: string;
  text?: string;
  note?: string;
  prompt?: string;
  finding_id?: string;
}

export interface NewsEdition {
  date: string;
  state: 'live' | 'final' | string;
  lang: string;
  dir: string;
  bcp47: string;
  title: string;
  tagline: string;
  edition_label: string;
  lede: string;
  lede_model: string;
  intro: string;
  reading_minutes: number;
  reading_label: string;
  sections: NewsSection[];
  footer: string;
  fingerprint: string;
  fingerprint_label: string;
  speak: string;
  share_text: string;
  url: string;
  labels: Record<string, string>;
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
  github_verified?: boolean;
  github_verified_at?: string | null;
  last_seen_at?: string;
  created_at?: string;
}

export interface LiveRecentJoin {
  handle: string;
  display_name?: string;
  coding_agent?: string | null;
  coding_agent_model?: string | null;
  github_verified?: boolean;
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
