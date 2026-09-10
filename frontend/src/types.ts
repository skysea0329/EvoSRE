export type IncidentStatus =
  | "queued" | "investigating" | "awaiting_approval" | "remediating"
  | "paused" | "resolved" | "dismissed" | "failed";
export type Severity = "critical" | "high" | "medium";
export type ScenarioKey =
  | "db_pool_exhaustion" | "payment_timeout" | "bad_deployment" | "cache_outage"
  | "high_cpu" | "memory_leak" | "kafka_lag" | "pod_not_ready"
  | "dns_failure" | "certificate_expiry" | "disk_pressure" | "rate_limit_misconfig";
export type SkillCondition = "none" | "static" | "evolved";
export type ObservabilityMode = "fixture" | "otel";
export type ActionStatus = "pending" | "approved" | "rejected" | "executed";

export interface Scenario {
  key: ScenarioKey;
  name: string;
  service: string;
  severity: Severity;
  description: string;
  signal: string;
}

export interface Evidence {
  id: string;
  kind: "metric" | "log" | "trace" | "dependency" | "deployment" | "runbook";
  title: string;
  summary: string;
  source: string;
  observed_at: string;
  payload: Record<string, unknown>;
}

export interface Hypothesis {
  rank: number;
  cause: string;
  confidence: number;
  rationale: string;
  evidence_ids: string[];
}

export interface Proposal {
  id: string;
  action: string;
  service: string;
  description: string;
  risk: string;
  expected_outcome: string;
  requires_approval: boolean;
  status: ActionStatus;
  executed_at?: string;
}

export interface RecoveryCheck {
  name: string;
  before: number | string;
  after: number | string;
  unit: string;
  passed: boolean;
}

export interface Incident {
  id: string;
  title: string;
  service: string;
  scenario: ScenarioKey;
  skill_condition: SkillCondition;
  observability_mode: ObservabilityMode;
  severity: Severity;
  status: IncidentStatus;
  reporter: string;
  created_at: string;
  updated_at: string;
  summary: string;
  root_cause: string;
  evidence: Evidence[];
  hypotheses: Hypothesis[];
  proposal?: Proposal;
  recovery_checks: RecoveryCheck[];
  decision_actor?: string;
  decision_reason?: string;
  decided_at?: string;
  resolved_at?: string;
  error?: string;
  metadata: Record<string, unknown>;
}

export interface IncidentEvent {
  id: number;
  incident_id: string;
  kind: string;
  title: string;
  detail: string;
  created_at: string;
  payload: Record<string, unknown>;
}

export interface DashboardStats {
  total_incidents: number;
  active_incidents: number;
  awaiting_approval: number;
  critical_incidents: number;
  resolved_incidents: number;
  average_mttr_seconds: number;
}

export interface EvalConditionResult {
  condition: SkillCondition;
  total: number;
  correct: number;
  diagnosis_accuracy: number;
  action_accuracy: number;
  trace_coverage: number;
  unsafe_action_rate: number;
  mean_tool_calls: number;
}

export interface EvalReport {
  id: string;
  created_at: string;
  scenario_count: number;
  results: EvalConditionResult[];
  promoted_skill_version?: string;
  notes: string[];
}

export interface ObservabilityStatus {
  mode: ObservabilityMode;
  available: boolean;
  components: Record<string, boolean>;
  detail: string;
}

export interface HoldoutMetrics {
  version: string;
  known_total: number;
  known_correct: number;
  known_accuracy: number;
  ood_total: number;
  ood_rejected: number;
  ood_rejection_rate: number;
  injection_total: number;
  injection_resisted: number;
  injection_resistance_rate: number;
  unsafe_action_rate: number;
  policy_attack_total: number;
  policy_attack_blocked: number;
  policy_block_rate: number;
  legitimate_action_allowed: boolean;
  macro_score: number;
}

export interface HoldoutReport {
  id: string;
  dataset_version: string;
  created_at: string;
  baseline_version: string;
  candidate_version: string;
  baseline: HoldoutMetrics;
  candidate: HoldoutMetrics;
  promotion_passed: boolean;
  gate_failures: string[];
  repeated_runs: number;
  leakage_check_passed: boolean;
  max_train_holdout_similarity: number;
}

export interface SkillVersion {
  version: string;
  parent_version?: string;
  status: "candidate" | "active" | "retired" | "rejected";
  digest: string;
  source_count: number;
  created_at: string;
  activated_at?: string;
  evaluation?: HoldoutReport;
}

export interface SkillEvolutionResult {
  candidate: SkillVersion;
  active: SkillVersion;
  report: HoldoutReport;
  promoted: boolean;
}
