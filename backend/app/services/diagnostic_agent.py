from __future__ import annotations

import asyncio
from collections.abc import Callable
from secrets import token_hex

from ..models import EvidenceItem, Hypothesis, IncidentRecord, RemediationProposal, ScenarioKey
from .fault_lab import FaultLab
from .narrative import synthesize_narrative
from .otel_adapter import OtelObservabilityAdapter
from .scenarios import scenario_for
from .skill_registry import SkillRegistry
from .tools import DiagnosticToolbox


ToolObserver = Callable[[str, str, EvidenceItem], None]
PauseProbe = Callable[[], bool]


class WorkflowPaused(Exception):
    """Raised at safe tool boundaries so a workflow can be resumed durably."""


class DiagnosticAgent:
    """Evidence-first incident agent with versioned diagnostic skill policies."""

    def __init__(
        self,
        step_delay_ms: int = 0,
        model: str = "gpt-5.6-luna",
        skill_registry: SkillRegistry | None = None,
    ) -> None:
        self.step_delay = step_delay_ms / 1_000
        self.model = model
        self.skills = skill_registry or SkillRegistry()

    async def _call(
        self,
        name: str,
        purpose: str,
        operation: Callable[[], EvidenceItem | None],
        observer: ToolObserver,
        should_pause: PauseProbe,
    ) -> EvidenceItem | None:
        if should_pause():
            raise WorkflowPaused
        if self.step_delay:
            await asyncio.sleep(self.step_delay)
        if should_pause():
            raise WorkflowPaused
        result = await asyncio.to_thread(operation)
        if result is not None:
            observer(name, purpose, result)
        return result

    async def investigate(
        self,
        incident: IncidentRecord,
        observer: ToolObserver,
        lab: FaultLab | None = None,
        observability: OtelObservabilityAdapter | None = None,
        should_pause: PauseProbe | None = None,
        enable_narrative: bool = True,
    ) -> tuple[
        list[EvidenceItem], list[Hypothesis], str, str, RemediationProposal, str, ScenarioKey, str
    ]:
        pause_probe = should_pause or (lambda: False)
        toolbox = DiagnosticToolbox(incident, lab, observability)
        evidence = list(incident.evidence)

        async def collect(
            kind: str,
            name: str,
            purpose: str,
            operation: Callable[[], EvidenceItem | None],
        ) -> EvidenceItem | None:
            existing = next((item for item in evidence if item.kind == kind), None)
            if existing is not None:
                return existing
            item = await self._call(name, purpose, operation, observer, pause_probe)
            if item is not None:
                evidence.append(item)
            return item

        await collect(
            "metric", "query_metrics", "Establish the failing service's symptom envelope.",
            toolbox.query_metrics,
        )
        await collect(
            "log", "search_logs", "Find errors correlated with the alert window.",
            toolbox.search_logs,
        )
        await collect(
            "trace", "query_traces", "Locate latency and error propagation across service spans.",
            toolbox.query_traces,
        )
        await collect(
            "dependency", "check_dependencies", "Separate service faults from dependency faults.",
            toolbox.check_dependencies,
        )
        await collect(
            "deployment", "inspect_deployments", "Correlate onset with configuration or release changes.",
            toolbox.inspect_deployments,
        )
        await collect(
            "runbook", "read_runbook", "Ground remediation in an operator-owned procedure.",
            toolbox.read_runbook,
        )

        corpus = " ".join(f"{item.summary} {item.payload}" for item in evidence)
        inferred, confidence, skill_version = self.skills.infer(incident.skill_condition, corpus)
        definition = scenario_for(inferred)
        evidence_ids = [item.id for item in evidence]
        hypotheses = self._hypotheses(definition.root_cause, confidence, evidence_ids)
        action = definition.action
        proposal = RemediationProposal(
            id=f"act_{token_hex(5)}",
            action=action["action"],
            service=incident.service,
            description=action["description"],
            risk=action["risk"],
            expected_outcome=action["expected"],
        )
        deterministic_summary = (
            f"EvoSRE correlated {len(evidence)} evidence groups across metrics, logs, traces, "
            f"dependencies, deployments and runbooks. Skill {skill_version} ranks the leading "
            f"hypothesis at {round(confidence * 100)}% confidence."
        )
        narrative_provider = "deterministic"
        summary = deterministic_summary
        if enable_narrative:
            narrative, narrative_provider = await synthesize_narrative(
                evidence, hypotheses, definition.root_cause, proposal, self.model
            )
            if narrative:
                summary = narrative.executive_summary
        return (
            evidence, hypotheses, summary, definition.root_cause, proposal,
            narrative_provider, inferred, skill_version,
        )

    @staticmethod
    def _hypotheses(
        root_cause: str, confidence: float, evidence_ids: list[str]
    ) -> list[Hypothesis]:
        candidates = [
            (root_cause, confidence, "Metric, log and trace signatures match this failure mode."),
            (
                "Upstream dependency degradation", max(0.08, 1 - confidence - 0.08),
                "Dependency evidence is retained as an alternative explanation.",
            ),
            (
                "Recent application or configuration change", max(0.05, 1 - confidence - 0.16),
                "Deployment correlation is checked before a mutation is proposed.",
            ),
        ]
        return [
            Hypothesis(
                rank=index,
                cause=cause,
                confidence=round(candidate_confidence, 2),
                rationale=rationale,
                evidence_ids=evidence_ids,
            )
            for index, (cause, candidate_confidence, rationale) in enumerate(candidates, start=1)
        ]
