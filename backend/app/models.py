from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class IncidentStatus(StrEnum):
    QUEUED = "queued"
    INVESTIGATING = "investigating"
    AWAITING_APPROVAL = "awaiting_approval"
    REMEDIATING = "remediating"
    PAUSED = "paused"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"
    FAILED = "failed"


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"


class ScenarioKey(StrEnum):
    DB_POOL_EXHAUSTION = "db_pool_exhaustion"
    PAYMENT_TIMEOUT = "payment_timeout"
    BAD_DEPLOYMENT = "bad_deployment"
    CACHE_OUTAGE = "cache_outage"
    HIGH_CPU = "high_cpu"
    MEMORY_LEAK = "memory_leak"
    KAFKA_LAG = "kafka_lag"
    POD_NOT_READY = "pod_not_ready"
    DNS_FAILURE = "dns_failure"
    CERTIFICATE_EXPIRY = "certificate_expiry"
    DISK_PRESSURE = "disk_pressure"
    RATE_LIMIT_MISCONFIG = "rate_limit_misconfig"


class SkillCondition(StrEnum):
    NONE = "none"
    STATIC = "static"
    EVOLVED = "evolved"


class ObservabilityMode(StrEnum):
    FIXTURE = "fixture"
    OTEL = "otel"


class ActionStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"


class SkillVersionStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    RETIRED = "retired"
    REJECTED = "rejected"


class ScenarioSummary(BaseModel):
    key: ScenarioKey
    name: str
    service: str
    severity: Severity
    description: str
    signal: str


class EvidenceItem(BaseModel):
    id: str
    kind: Literal["metric", "log", "trace", "dependency", "deployment", "runbook"]
    title: str
    summary: str
    source: str
    observed_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class Hypothesis(BaseModel):
    rank: int
    cause: str
    confidence: float = Field(ge=0, le=1)
    rationale: str
    evidence_ids: list[str] = Field(default_factory=list)


class RemediationProposal(BaseModel):
    id: str
    action: str
    service: str
    description: str
    risk: str
    expected_outcome: str
    requires_approval: bool = True
    status: ActionStatus = ActionStatus.PENDING
    executed_at: datetime | None = None


class RecoveryCheck(BaseModel):
    name: str
    before: float | str
    after: float | str
    unit: str = ""
    passed: bool


class AgentNarrative(BaseModel):
    executive_summary: str = Field(min_length=20, max_length=1_000)
    operator_notes: list[str] = Field(min_length=1, max_length=5)


class IncidentRecord(BaseModel):
    id: str
    title: str
    service: str
    scenario: ScenarioKey
    skill_condition: SkillCondition = SkillCondition.EVOLVED
    observability_mode: ObservabilityMode = ObservabilityMode.FIXTURE
    severity: Severity
    status: IncidentStatus
    reporter: str
    created_at: datetime
    updated_at: datetime
    summary: str = ""
    root_cause: str = ""
    evidence: list[EvidenceItem] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    proposal: RemediationProposal | None = None
    recovery_checks: list[RecoveryCheck] = Field(default_factory=list)
    decision_actor: str | None = None
    decision_reason: str | None = None
    decided_at: datetime | None = None
    resolved_at: datetime | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class IncidentEvent(BaseModel):
    id: int
    incident_id: str
    kind: str
    title: str
    detail: str
    created_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class IncidentCreate(BaseModel):
    scenario: ScenarioKey
    skill_condition: SkillCondition = SkillCondition.EVOLVED
    observability_mode: ObservabilityMode = ObservabilityMode.FIXTURE
    reporter: str = Field(default="Demo on-call engineer", min_length=2, max_length=100)


class ActionDecision(BaseModel):
    actor: str = Field(min_length=2, max_length=100)
    reason: str = Field(min_length=8, max_length=2_000)
    idempotency_key: str = Field(min_length=8, max_length=100)


class WorkflowControl(BaseModel):
    actor: str = Field(min_length=2, max_length=100)
    reason: str = Field(min_length=8, max_length=2_000)


class LabState(BaseModel):
    incident_id: str
    scenario: ScenarioKey
    service: str
    fault_active: bool
    generation: int
    release_version: str | None = None
    configuration: dict[str, Any] = Field(default_factory=dict)
    last_action: str | None = None
    updated_at: datetime


class ObservabilityStatus(BaseModel):
    mode: ObservabilityMode
    available: bool
    components: dict[str, bool] = Field(default_factory=dict)
    detail: str = ""


class EvalConditionResult(BaseModel):
    condition: SkillCondition
    total: int
    correct: int
    diagnosis_accuracy: float
    action_accuracy: float
    trace_coverage: float
    unsafe_action_rate: float
    mean_tool_calls: float
    cases: list[dict[str, Any]] = Field(default_factory=list)


class EvalReport(BaseModel):
    id: str
    created_at: datetime
    scenario_count: int
    results: list[EvalConditionResult]
    promoted_skill_version: str | None = None
    notes: list[str] = Field(default_factory=list)


class HoldoutMetrics(BaseModel):
    version: str
    known_total: int
    known_correct: int
    known_accuracy: float
    ood_total: int
    ood_rejected: int
    ood_rejection_rate: float
    injection_total: int
    injection_resisted: int
    injection_resistance_rate: float
    unsafe_action_rate: float
    policy_attack_total: int = 0
    policy_attack_blocked: int = 0
    policy_block_rate: float = 1.0
    legitimate_action_allowed: bool = True
    macro_score: float
    cases: list[dict[str, Any]] = Field(default_factory=list)


class HoldoutReport(BaseModel):
    id: str
    dataset_version: str
    created_at: datetime
    baseline_version: str
    candidate_version: str
    baseline: HoldoutMetrics
    candidate: HoldoutMetrics
    promotion_passed: bool
    gate_failures: list[str] = Field(default_factory=list)
    repeated_runs: int = 1
    leakage_check_passed: bool = True
    max_train_holdout_similarity: float = 0.0


class SkillVersionRecord(BaseModel):
    version: str
    parent_version: str | None = None
    status: SkillVersionStatus
    digest: str
    rules: list[dict[str, Any]]
    source_count: int = 0
    created_at: datetime
    activated_at: datetime | None = None
    evaluation: HoldoutReport | None = None


class SkillLifecycleEvent(BaseModel):
    id: int
    kind: str
    version: str
    previous_version: str | None = None
    actor: str
    reason: str
    created_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class SkillEvolutionRequest(BaseModel):
    actor: str = Field(min_length=2, max_length=100)
    reason: str = Field(min_length=8, max_length=2_000)


class SkillRollbackRequest(SkillEvolutionRequest):
    target_version: str | None = None


class SkillEvolutionResult(BaseModel):
    candidate: SkillVersionRecord
    active: SkillVersionRecord
    report: HoldoutReport
    promoted: bool


class DashboardStats(BaseModel):
    total_incidents: int
    active_incidents: int
    awaiting_approval: int
    critical_incidents: int
    resolved_incidents: int
    average_mttr_seconds: float
