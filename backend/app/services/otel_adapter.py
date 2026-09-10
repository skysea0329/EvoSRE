from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ..models import EvidenceItem, IncidentRecord, ObservabilityMode, ObservabilityStatus
from .scenarios import scenario_for


class ObservabilityUnavailable(RuntimeError):
    """Raised when real telemetry was requested but a backend cannot be queried."""


@dataclass(frozen=True)
class OtelEndpoints:
    lab: str
    prometheus: str
    loki: str
    tempo: str
    token: str
    timeout_seconds: float = 3.0


class OtelLabClient:
    """Typed control client for the isolated instrumented checkout service."""

    def __init__(self, endpoints: OtelEndpoints, transport: httpx.BaseTransport | None = None) -> None:
        self.endpoints = endpoints
        self._transport = transport

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.endpoints.timeout_seconds,
            transport=self._transport,
            headers={"x-evosre-lab-token": self.endpoints.token},
        )

    def inject(self, incident_id: str, scenario: str = "bad_deployment") -> dict[str, Any]:
        with self._client() as client:
            response = client.post(
                f"{self.endpoints.lab}/admin/faults/{scenario.replace('_', '-')}/inject",
                json={"incident_id": incident_id},
            )
            response.raise_for_status()
            return response.json()

    def state(self) -> dict[str, Any]:
        with self._client() as client:
            response = client.get(f"{self.endpoints.lab}/state")
            response.raise_for_status()
            return response.json()

    def execute(self, incident: IncidentRecord, action: str, idempotency_key: str) -> dict[str, Any]:
        expected = scenario_for(incident.scenario).action["action"]
        if action != expected:
            raise ValueError(f"The real OTel lab only permits {expected} for {incident.scenario.value}")
        with self._client() as client:
            response = client.post(
                f"{self.endpoints.lab}/admin/actions/{action.replace('_', '-')}",
                json={
                    "incident_id": incident.id,
                    "action": action,
                    "idempotency_key": idempotency_key,
                },
            )
            response.raise_for_status()
            return response.json()


class OtelObservabilityAdapter:
    """Read-only Prometheus, Loki and Tempo adapter for one incident id."""

    def __init__(
        self,
        endpoints: OtelEndpoints,
        transport: httpx.BaseTransport | None = None,
        retry_attempts: int = 30,
        retry_delay: float = 0.5,
    ) -> None:
        self.endpoints = endpoints
        self._transport = transport
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self.endpoints.timeout_seconds, transport=self._transport)

    @staticmethod
    def _evidence(
        incident: IncidentRecord,
        kind: str,
        title: str,
        summary: str,
        source: str,
        payload: dict[str, Any],
    ) -> EvidenceItem:
        return EvidenceItem(
            id=f"otel_{kind}_{incident.id}_{int(time.time_ns())}",
            kind=kind,
            title=title,
            summary=summary,
            source=source,
            observed_at=datetime.now(UTC),
            payload=payload,
        )

    def _prometheus_value(
        self, metric: str, incident_id: str, release_version: str
    ) -> tuple[float, str]:
        query = f'{metric}{{incident_id="{incident_id}",release_version="{release_version}"}}'
        for attempt in range(self.retry_attempts):
            with self._client() as client:
                response = client.get(
                    f"{self.endpoints.prometheus}/api/v1/query", params={"query": query}
                )
                response.raise_for_status()
                payload = response.json()
            result = payload.get("data", {}).get("result", [])
            if result:
                return float(result[0]["value"][1]), query
            if attempt + 1 < self.retry_attempts:
                time.sleep(self.retry_delay)
        raise ObservabilityUnavailable(f"Prometheus returned no samples for {query}")

    def query_metrics(self, incident: IncidentRecord) -> EvidenceItem:
        values: dict[str, dict[str, Any]] = {}
        queries: list[str] = []
        with self._client() as client:
            state_response = client.get(f"{self.endpoints.lab}/state")
            state_response.raise_for_status()
            release_version = str(state_response.json()["release_version"])
        definition = scenario_for(incident.scenario)
        for name, (_before, unit, threshold) in definition.metrics_before.items():
            metric = f"evosre_checkout_{name}"
            value, query = self._prometheus_value(metric, incident.id, release_version)
            queries.append(query)
            values[name] = {"value": value, "unit": unit, "healthy_threshold": threshold}
        breached = [name for name, item in values.items() if item["value"] > item["healthy_threshold"]]
        return self._evidence(
            incident,
            "metric",
            "Prometheus metrics queried",
            f"{len(breached)} of {len(values)} real OTLP metrics breach the healthy envelope.",
            f"{self.endpoints.prometheus}/api/v1/query",
            {**values, "_queries": queries, "_backend": "prometheus", "_release_version": release_version},
        )

    def query_dependencies(self, incident: IncidentRecord) -> EvidenceItem:
        with self._client() as client:
            response = client.get(f"{self.endpoints.lab}/dependencies")
            response.raise_for_status()
            raw = response.json()
        healthy = bool(raw["healthy"])
        definition = scenario_for(incident.scenario)
        expected = definition.dependencies_after if healthy else definition.dependencies_before
        dependencies = [
            {
                **item,
                "status": item["status"] if not healthy else "healthy",
                "latency_ms": raw["latency_ms"] if index == 0 else item["latency_ms"],
                "detail": raw["detail"] if index == 0 else item["detail"],
            }
            for index, item in enumerate(expected)
        ]
        return self._evidence(
            incident,
            "dependency",
            "Live dependencies probed",
            f"The instrumented lab dependency probe is {'healthy' if healthy else 'failing'}.",
            f"{self.endpoints.lab}/dependencies",
            {"dependencies": dependencies, "lab": raw},
        )

    def search_logs(self, incident: IncidentRecord) -> EvidenceItem:
        end = datetime.now(UTC)
        start = end - timedelta(minutes=10)
        query = f'{{service_name="checkout-lab"}} |= "{incident.id}"'
        entries: list[dict[str, str]] = []
        for attempt in range(self.retry_attempts):
            with self._client() as client:
                response = client.get(
                    f"{self.endpoints.loki}/loki/api/v1/query_range",
                    params={
                        "query": query,
                        "start": str(int(start.timestamp() * 1_000_000_000)),
                        "end": str(int(end.timestamp() * 1_000_000_000)),
                        "limit": "50", "direction": "backward",
                    },
                )
                response.raise_for_status()
                raw = response.json()
            for stream in raw.get("data", {}).get("result", []):
                for timestamp, line in stream.get("values", []):
                    entries.append({"level": "ERROR" if "error" in line.lower() or "timeout" in line.lower() else "INFO", "message": line, "timestamp": timestamp})
            if entries:
                break
            if attempt + 1 < self.retry_attempts:
                time.sleep(self.retry_delay)
        if not entries:
            raise ObservabilityUnavailable(f"Loki returned no logs for incident {incident.id}")
        return self._evidence(
            incident,
            "log",
            "Loki logs queried",
            f"Collected {len(entries)} OTLP log records correlated by incident id.",
            f"{self.endpoints.loki}/loki/api/v1/query_range",
            {"entries": entries, "query": query, "backend": "loki"},
        )

    def query_traces(self, incident: IncidentRecord) -> EvidenceItem:
        end = int(datetime.now(UTC).timestamp())
        start = end - 600
        traceql = (
            f'{{ resource.service.name = "checkout-lab" && span."incident.id" = "{incident.id}" }} '
            "with (most_recent=true)"
        )
        traces: list[dict[str, Any]] = []
        for attempt in range(self.retry_attempts):
            with self._client() as client:
                response = client.get(
                    f"{self.endpoints.tempo}/api/search",
                    params={"q": traceql, "start": start, "end": end, "limit": 20},
                )
                response.raise_for_status()
                raw = response.json()
            traces = raw.get("traces", [])
            if traces:
                break
            if attempt + 1 < self.retry_attempts:
                time.sleep(self.retry_delay)
        if not traces:
            raise ObservabilityUnavailable(f"Tempo returned no traces for incident {incident.id}")
        with self._client() as client:
            state_response = client.get(f"{self.endpoints.lab}/state")
            state_response.raise_for_status()
            fault_active = bool(state_response.json()["fault_active"])
        spans = [
            {
                "trace_id": item.get("traceID"),
                "service": item.get("rootServiceName", "checkout-lab"),
                "operation": item.get("rootTraceName", "POST /checkout"),
                "duration_ms": round(float(item.get("durationMs", 0)), 2),
                "status": "error" if fault_active else "ok",
            }
            for item in traces
        ]
        return self._evidence(
            incident,
            "trace",
            "Tempo traces queried",
            f"Tempo returned {len(traces)} incident-correlated traces via TraceQL.",
            f"{self.endpoints.tempo}/api/search",
            {"traceql": traceql, "spans": spans, "raw": traces, "backend": "tempo"},
        )

    def status(self) -> ObservabilityStatus:
        checks = {
            "lab": (self.endpoints.lab, "/health"),
            "prometheus": (self.endpoints.prometheus, "/-/ready"),
            "loki": (self.endpoints.loki, "/ready"),
            "tempo": (self.endpoints.tempo, "/ready"),
        }
        components: dict[str, bool] = {}
        with self._client() as client:
            for name, (base, path) in checks.items():
                try:
                    components[name] = client.get(f"{base}{path}").is_success
                except httpx.HTTPError:
                    components[name] = False
        available = all(components.values())
        return ObservabilityStatus(
            mode=ObservabilityMode.OTEL,
            available=available,
            components=components,
            detail="All real OTel components are reachable." if available else "Start the otel Compose profile.",
        )
