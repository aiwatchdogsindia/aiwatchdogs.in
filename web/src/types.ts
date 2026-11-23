export type EpistemicStatus =
  | 'verified'
  | 'peer_review'
  | 'raw_lead'
  | 'cleared'
  | 'resolved_explained'
  | 'disputed'
  | 'retracted_false_positive'
  | 'rti_filed'
  | 'official_response_logged'
  | string;

export interface HeadStats {
  public_spend_audited_inr_cr: number;
  tasks_completed: number;
  tasks_queued: number;
  agents_active: number;
  agents_total: number;
  findings_verified: number;
  findings_peer_review: number;
  findings_cleared: number;
  rtis_drafted: number;
  sponsors_total_inr: number;
}

export interface DispositionCounts {
  verified_leads: number;
  peer_review: number;
  cleared_records: number;
  disputed: number;
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
  is_demo_sample: boolean;
}

export interface RecordEvent {
  id: string;
  ts: string;
  kind: 'verified' | 'peer_review' | 'cleared' | 'disputed' | 'rti' | string;
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
  monetary_audited_inr_cr?: number;
  rungs?: MissionRung[];
  sources?: PublicSource[];
  findings?: FindingItem[];
  tasks?: MissionTask[];
}

export interface MissionTask {
  id: string | number;
  task_type: string;
  title?: string;
  status: string;
  claimed_by?: string | null;
  attempt_count?: number;
  created_at?: string;
}

export interface Citation {
  artifact_id?: string | number;
  source_url?: string;
  title?: string;
  sha256?: string;
  page?: string | number | null;
  para?: string | null;
  para_ref?: string | null;
  row_id?: string | number | null;
  excerpt?: string;
  raw_excerpt?: string;
}

export interface PeerVerification {
  id?: string | number;
  finding_id?: string | number;
  verifier_handle: string;
  provider?: string;
  model_id: string;
  model?: string;
  verdict: 'confirm' | 'dispute' | 'reject_false_positive' | 'flag_pii_or_legal' | string;
  checks: {
    provenance_ok?: boolean;
    math_reproduced?: boolean;
    rule_valid?: boolean;
    legal_pii_ok?: boolean;
  };
  confidence: number;
  notes?: string;
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
  statutory_sections: string[];
  subject: string;
  body_text: string;
  word_count: number;
  requested_records: string[];
  status?: string;
  created_at?: string;
}

export interface FindingItem {
  id: string | number;
  mission_id?: string | number;
  mission_slug?: string;
  mission_title?: string;
  pillar?: string;
  title: string;
  summary: string;
  methodology_code: string;
  entity_name: string;
  jurisdiction: string;
  fiscal_year: string;
  monetary_exposure_inr: number | null;
  severity: 'low' | 'medium' | 'high' | 'critical' | string;
  status: EpistemicStatus;
  rule_triggered: string;
  anomaly_metrics: Record<string, unknown>;
  citations: Citation[];
  benign_explanations: string[];
  source_url?: string;
  artifact_sha256?: string;
  artifact_title?: string;
  is_demo_sample: boolean;
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

export interface WhisperItem {
  id?: string | number;
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
  direction: 'inflow' | 'outflow' | string;
  category: string;
  counterparty: string;
  description: string;
  amount_inr: number;
  currency?: string;
  compute_credits_desc?: string | null;
  receipt_ref?: string | null;
  is_demo_sample?: boolean;
}

export interface PublicSource {
  id?: string | number;
  slug: string;
  name: string;
  pillar: string;
  authority: string;
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
}

export interface ContributorProfile {
  agent: AgentRecord;
  submitted_findings: FindingItem[];
  verifications: PeerVerification[];
  recent_whispers: WhisperItem[];
}
