from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

from ..config import Settings
from ..database import Database
from ..models import ActionStatus, IncidentStatus, ObservabilityMode, RecoveryCheck
from .diagnostic_agent import DiagnosticAgent, WorkflowPaused
from .action_policy import ActionPolicy
from .fault_lab import FaultLab
from .otel_adapter import OtelEndpoints, OtelLabClient, OtelObservabilityAdapter
from .scenarios import scenario_for
from .skill_registry import SkillRegistry
from .tools import DiagnosticToolbox


class IncidentRunner:
    """Single-worker durable harness with safe-boundary pause and startup recovery."""

    def __init__(
        self,
        database: Database,
        settings: Settings,
        skill_registry: SkillRegistry | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.lab = FaultLab(database)
        self.skill_registry = skill_registry or SkillRegistry(database)
        endpoints = OtelEndpoints(
            lab=settings.otel_lab_url,
            prometheus=settings.prometheus_url,
            loki=settings.loki_url,
            tempo=settings.tempo_url,
            token=settings.otel_lab_token,
            timeout_seconds=settings.observability_timeout_seconds,
        )
        self.otel = OtelObservabilityAdapter(endpoints)
        self.otel_lab = OtelLabClient(endpoints)
        self.queue: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._pause_requested: set[str] = set()

    async def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run(), name="evosre-incident-worker")
            for incident in self.database.list_non_terminal_incidents():
                operation = "remediate" if incident.status == IncidentStatus.REMEDIATING else "investigate"
                await self.enqueue(operation, incident.id)

    async def stop(self) -> None:
        if self._worker is not None:
            await self.queue.put(None)
            await self._worker
            self._worker = None

    async def enqueue(self, operation: str, incident_id: str) -> None:
        await self.queue.put((operation, incident_id))

    def pause(self, incident_id: str):
        record = self.database.get_incident(incident_id)
        if record is None:
            raise KeyError(incident_id)
        if record.status not in {
            IncidentStatus.QUEUED, IncidentStatus.INVESTIGATING, IncidentStatus.REMEDIATING
        }:
            raise ValueError(f"Cannot pause incident in {record.status.value}")
        self._pause_requested.add(incident_id)
        operation = "remediate" if record.status == IncidentStatus.REMEDIATING else "investigate"
        updated = self.database.update_incident(
            incident_id,
            status=IncidentStatus.PAUSED,
            metadata={**record.metadata, "resume_operation": operation, "paused_from": record.status.value},
        )
        self.database.add_event(
            incident_id, "control", "Workflow paused",
            "The harness checkpointed progress at a safe tool boundary.",
            {"resume_operation": operation},
        )
        return updated

    async def resume(self, incident_id: str):
        record = self.database.get_incident(incident_id)
        if record is None:
            raise KeyError(incident_id)
        if record.status != IncidentStatus.PAUSED:
            raise ValueError(f"Cannot resume incident in {record.status.value}")
        operation = str(record.metadata.get("resume_operation", "investigate"))
        self._pause_requested.discard(incident_id)
        next_status = IncidentStatus.REMEDIATING if operation == "remediate" else IncidentStatus.QUEUED
        updated = self.database.update_incident(incident_id, status=next_status)
        self.database.add_event(
            incident_id, "control", "Workflow resumed",
            "The harness will continue from persisted evidence and state.",
            {"operation": operation},
        )
        await self.enqueue(operation, incident_id)
        return updated

    async def _run(self) -> None:
        while True:
            job = await self.queue.get()
            try:
                if job is None:
                    return
                operation, incident_id = job
                if operation == "investigate":
                    await self.investigate(incident_id)
                elif operation == "remediate":
                    await self.remediate(incident_id)
            finally:
                self.queue.task_done()

    def _is_paused(self, incident_id: str) -> bool:
        record = self.database.get_incident(incident_id)
        return incident_id in self._pause_requested or (
            record is not None and record.status == IncidentStatus.PAUSED
        )

    async def investigate(self, incident_id: str) -> None:
        record = self.database.get_incident(incident_id)
        if record is None or self._is_paused(incident_id):
            return
        started = time.perf_counter()
        try:
            self.database.update_incident(incident_id, status=IncidentStatus.INVESTIGATING, error=None)
            self.database.add_event(
                incident_id, "agent", "Investigation started",
                "EvoSRE is selecting observability tools and collecting scoped evidence.",
            )

            def observe(tool_name: str, purpose: str, evidence) -> None:
                latest = self.database.get_incident(incident_id)
                if latest is not None and all(item.id != evidence.id for item in latest.evidence):
                    completed = list(latest.metadata.get("completed_tools", []))
                    self.database.update_incident(
                        incident_id,
                        evidence=[*latest.evidence, evidence],
                        metadata={**latest.metadata, "completed_tools": [*completed, tool_name]},
                    )
                self.database.add_event(
                    incident_id, "tool", f"Tool · {tool_name}", purpose,
                    {"evidence_id": evidence.id, "kind": evidence.kind,
                     "summary": evidence.summary, "source": evidence.source},
                )

            latest = self.database.get_incident(incident_id) or record
            agent = DiagnosticAgent(
                self.settings.investigation_step_delay_ms,
                self.settings.openai_model,
                self.skill_registry,
            )
            result = await agent.investigate(
                latest,
                observe,
                lab=self.lab,
                observability=(
                    self.otel if latest.observability_mode == ObservabilityMode.OTEL else None
                ),
                should_pause=lambda: self._is_paused(incident_id),
            )
            (
                evidence, hypotheses, summary, root_cause, proposal,
                narrative_provider, inferred, skill_version,
            ) = result
            if self._is_paused(incident_id):
                return
            duration = round(time.perf_counter() - started, 3)
            latest = self.database.get_incident(incident_id) or record
            updated = self.database.update_incident(
                incident_id,
                status=IncidentStatus.AWAITING_APPROVAL,
                evidence=evidence,
                hypotheses=hypotheses,
                summary=summary,
                root_cause=root_cause,
                proposal=proposal,
                metadata={
                    **latest.metadata,
                    "investigation_duration_seconds": duration,
                    "tool_call_count": len(evidence),
                    "fault_active": True,
                    "narrative_provider": narrative_provider,
                    "predicted_scenario": inferred.value,
                    "skill_version": skill_version,
                },
            )
            self.database.add_event(
                incident_id, "diagnosis", "Diagnosis ready for operator review", root_cause,
                {"confidence": hypotheses[0].confidence, "proposal_id": proposal.id,
                 "status": updated.status.value, "duration_seconds": duration,
                 "skill_version": skill_version},
            )
        except WorkflowPaused:
            return
        except Exception as exc:
            self.database.update_incident(
                incident_id, status=IncidentStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}",
            )
            self.database.add_event(
                incident_id, "error", "Investigation failed", str(exc),
                {"error_type": type(exc).__name__},
            )

    async def remediate(self, incident_id: str) -> None:
        record = self.database.get_incident(incident_id)
        if record is None or record.proposal is None or self._is_paused(incident_id):
            return
        try:
            self.database.add_event(
                incident_id, "action", f"Executing · {record.proposal.action}",
                record.proposal.description,
                {"proposal_id": record.proposal.id, "actor": record.decision_actor},
            )
            if self.settings.investigation_step_delay_ms:
                await asyncio.sleep(self.settings.investigation_step_delay_ms / 1_000)
            if self._is_paused(incident_id):
                return

            definition = scenario_for(record.scenario)
            local_state = self.lab.require_state(record.id)
            ActionPolicy.authorize(record, record.proposal.action, local_state.incident_id)
            if record.observability_mode == ObservabilityMode.OTEL:
                remote_before = await asyncio.to_thread(self.otel_lab.state)
                remote_after = await asyncio.to_thread(
                    self.otel_lab.execute,
                    record,
                    record.proposal.action,
                    record.proposal.id,
                )
                before_state, after_state = self.lab.execute(record, record.proposal.action)
                execution_before: dict = before_state.model_dump(mode="json")
                execution_after: dict = after_state.model_dump(mode="json")
                execution_before["otel_lab"] = remote_before
                execution_after["otel_lab"] = remote_after
                await asyncio.sleep(1.25)
                recovery_evidence = await asyncio.to_thread(self.otel.query_metrics, record)
            else:
                remote_after = None
                before_state, after_state = self.lab.execute(record, record.proposal.action)
                execution_before = before_state.model_dump(mode="json")
                execution_after = after_state.model_dump(mode="json")
                recovery_evidence = DiagnosticToolbox(record, self.lab).query_recovery_metrics()
            checks: list[RecoveryCheck] = []
            for name, (expected_after, unit, threshold) in definition.metrics_after.items():
                before_value = definition.metrics_before[name][0]
                after_value = (
                    recovery_evidence.payload[name]["value"]
                    if record.observability_mode == ObservabilityMode.OTEL
                    else expected_after
                )
                checks.append(
                    RecoveryCheck(
                        name=name, before=before_value, after=after_value,
                        unit=unit, passed=after_value <= threshold,
                    )
                )

            proposal = record.proposal.model_copy(
                update={"status": ActionStatus.EXECUTED, "executed_at": datetime.now(UTC)}
            )
            remote_healthy = remote_after is None or not bool(remote_after["fault_active"])
            all_passed = (
                all(check.passed for check in checks)
                and not after_state.fault_active
                and remote_healthy
            )
            resolved_at = datetime.now(UTC) if all_passed else None
            final_status = IncidentStatus.RESOLVED if all_passed else IncidentStatus.FAILED
            latest = self.database.get_incident(incident_id) or record
            self.database.update_incident(
                incident_id,
                status=final_status,
                proposal=proposal,
                evidence=[*latest.evidence, recovery_evidence],
                recovery_checks=checks,
                resolved_at=resolved_at,
                metadata={
                    **latest.metadata,
                    "fault_active": after_state.fault_active,
                    "recovery_verified": all_passed,
                    "remediation_action": proposal.action,
                    "lab_generation": after_state.generation,
                    "live_release_version": after_state.release_version,
                },
                error=None if all_passed else "Recovery verification failed",
            )
            self.database.add_event(
                incident_id, "recovery",
                "Recovery verified" if all_passed else "Recovery verification failed",
                f"{sum(check.passed for check in checks)}/{len(checks)} health checks passed.",
                {"checks": [check.model_dump(mode="json") for check in checks],
                 "status": final_status.value,
                 "lab_before": execution_before,
                 "lab_after": execution_after},
            )
        except Exception as exc:
            self.database.update_incident(
                incident_id, status=IncidentStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}",
            )
            self.database.add_event(
                incident_id, "error", "Remediation failed", str(exc),
                {"error_type": type(exc).__name__},
            )
