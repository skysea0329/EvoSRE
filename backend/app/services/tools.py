from __future__ import annotations

from datetime import UTC, datetime
from secrets import token_hex
from typing import Any

from ..models import EvidenceItem, IncidentRecord
from .fault_lab import FaultLab
from .otel_adapter import OtelObservabilityAdapter
from .scenarios import scenario_for


class DiagnosticToolbox:
    """Read-only observability tools over an incident-scoped reproducible snapshot."""

    def __init__(
        self,
        incident: IncidentRecord,
        lab: FaultLab | None = None,
        observability: OtelObservabilityAdapter | None = None,
    ) -> None:
        self.incident = incident
        self.definition = scenario_for(incident.scenario)
        self.lab = lab
        self.observability = observability

    def _evidence(
        self, kind: str, title: str, summary: str, source: str, payload: dict[str, Any]
    ) -> EvidenceItem:
        return EvidenceItem(
            id=f"ev_{token_hex(5)}", kind=kind, title=title, summary=summary,
            source=source, observed_at=datetime.now(UTC), payload=payload,
        )

    def query_metrics(self) -> EvidenceItem:
        if self.observability is not None:
            return self.observability.query_metrics(self.incident)
        metrics = self.lab.metrics(self.incident) if self.lab else self.definition.metrics_before
        breached = [name for name, (value, _unit, threshold) in metrics.items() if value > threshold]
        payload = {
            name: {"value": value, "unit": unit, "healthy_threshold": threshold}
            for name, (value, unit, threshold) in metrics.items()
        }
        return self._evidence(
            "metric", "Service metrics queried",
            f"{len(breached)} of {len(metrics)} signals breach the healthy envelope: {', '.join(breached)}.",
            f"metrics://{self.incident.service}?window=15m", payload,
        )

    def search_logs(self) -> EvidenceItem:
        if self.observability is not None:
            return self.observability.search_logs(self.incident)
        logs = self.definition.logs
        error_count = sum(item["level"] == "ERROR" for item in logs)
        return self._evidence(
            "log", "Correlated logs searched",
            f"Collected {len(logs)} representative log events, including {error_count} errors.",
            f"logs://{self.incident.service}?window=15m", {"entries": logs},
        )

    def query_traces(self) -> EvidenceItem:
        if self.observability is not None:
            return self.observability.query_traces(self.incident)
        spans = self.definition.trace_spans or [
            {
                "service": self.incident.service,
                "operation": "request handler",
                "duration_ms": self.definition.metrics_before.get("p95_latency", (0, "ms", 0))[0],
                "status": "error",
                "attributes": {"scenario": self.incident.scenario.value},
            }
        ]
        failed = sum(span.get("status") == "error" for span in spans)
        return self._evidence(
            "trace",
            "Distributed traces queried",
            f"Collected {len(spans)} correlated spans; {failed} record an error status.",
            f"traces://{self.incident.service}?window=15m",
            {"trace_id": f"trace-{self.incident.id}", "spans": spans},
        )

    def check_dependencies(self) -> EvidenceItem:
        if self.observability is not None:
            return self.observability.query_dependencies(self.incident)
        dependencies = self.definition.dependencies_before
        unhealthy = [item["name"] for item in dependencies if item["status"] != "healthy"]
        summary = (
            f"Non-healthy dependencies: {', '.join(unhealthy)}."
            if unhealthy else "All observed dependencies are healthy; an external dependency failure is unlikely."
        )
        return self._evidence(
            "dependency", "Dependency health checked", summary,
            f"topology://{self.incident.service}/dependencies", {"dependencies": dependencies},
        )

    def inspect_deployments(self) -> EvidenceItem | None:
        deployment = self.lab.deployment(self.incident) if self.lab else self.definition.deployment
        if deployment is None:
            return None
        return self._evidence(
            "deployment", "Recent deployments inspected",
            f"Release {deployment['version']} was deployed {deployment['age_minutes']} minutes before diagnosis.",
            f"deployments://{self.incident.service}", deployment,
        )

    def read_runbook(self) -> EvidenceItem:
        return self._evidence(
            "runbook", "Service runbook retrieved",
            f"Loaded {len(self.definition.runbook)} operator checks for {self.incident.service}.",
            f"runbook://{self.incident.service}/incident-response",
            {"steps": self.definition.runbook},
        )

    def query_recovery_metrics(self) -> EvidenceItem:
        if self.observability is not None:
            return self.observability.query_metrics(self.incident)
        metrics = self.lab.metrics(self.incident) if self.lab else self.definition.metrics_after
        payload = {
            name: {"value": value, "unit": unit, "healthy_threshold": threshold}
            for name, (value, unit, threshold) in metrics.items()
        }
        return self._evidence(
            "metric", "Recovery metrics queried",
            "Post-action probes collected for recovery verification.",
            f"metrics://{self.incident.service}?window=post-action", payload,
        )
