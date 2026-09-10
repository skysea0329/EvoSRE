from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from app.models import IncidentRecord, IncidentStatus, ObservabilityMode, ScenarioKey, Severity
from app.services.otel_adapter import OtelEndpoints, OtelLabClient, OtelObservabilityAdapter


def incident() -> IncidentRecord:
    now = datetime.now(UTC)
    return IncidentRecord(
        id="otel-incident-1",
        title="Checkout regression",
        service="checkout-api",
        scenario=ScenarioKey.BAD_DEPLOYMENT,
        observability_mode=ObservabilityMode.OTEL,
        severity=Severity.CRITICAL,
        status=IncidentStatus.INVESTIGATING,
        reporter="OTel test",
        created_at=now,
        updated_at=now,
    )


def handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/state":
        return httpx.Response(
            200,
            json={
                "incident_id": "otel-incident-1",
                "release_version": "2.4.0",
                "fault_active": True,
                "generation": 1,
                "last_action": None,
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )
    if path == "/api/v1/query":
        query = request.url.params["query"]
        values = {
            "http_error_rate": 22.1,
            "p95_latency": 780,
            "coupon_checkout_errors": 96.0,
            "cpu_utilization": 41,
        }
        value = next(number for name, number in values.items() if name in query)
        assert 'incident_id="otel-incident-1"' in query
        assert 'release_version="2.4.0"' in query
        return httpx.Response(
            200,
            json={"status": "success", "data": {"result": [{"value": [1, str(value)]}]}},
        )
    if path == "/loki/api/v1/query_range":
        assert "otel-incident-1" in request.url.params["query"]
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "result": [
                        {
                            "values": [
                                ["1", "incident_id=otel-incident-1 TypeError couponPrice null"]
                            ]
                        }
                    ]
                },
            },
        )
    if path == "/api/search":
        assert "otel-incident-1" in request.url.params["q"]
        return httpx.Response(
            200,
            json={
                "traces": [
                    {
                        "traceID": "abc123",
                        "rootServiceName": "checkout-lab",
                        "rootTraceName": "POST /checkout",
                        "durationMs": 780,
                    }
                ]
            },
        )
    if path == "/dependencies":
        return httpx.Response(
            200,
            json={
                "scenario": "bad_deployment", "healthy": False,
                "detail": "TypeError: couponPrice cannot read discount from null",
                "latency_ms": 1.2, "configuration": {"release_version": "2.4.0"},
            },
        )
    if path.endswith("/health") or path in {"/-/ready", "/ready"}:
        return httpx.Response(200, json={"status": "ok"})
    if path.endswith("/admin/faults/bad-deployment/inject"):
        assert request.headers["x-evosre-lab-token"] == "test-token"
        return httpx.Response(200, json={"incident_id": "otel-incident-1", "fault_active": True})
    if path.endswith("/admin/actions/rollback-release"):
        assert request.headers["x-evosre-lab-token"] == "test-token"
        return httpx.Response(
            200,
            json={
                "incident_id": "otel-incident-1",
                "release_version": "2.3.6",
                "fault_active": False,
            },
        )
    raise AssertionError(f"Unexpected request: {request.method} {request.url}")


@pytest.fixture
def endpoints() -> OtelEndpoints:
    return OtelEndpoints(
        lab="http://lab",
        prometheus="http://prometheus",
        loki="http://loki",
        tempo="http://tempo",
        token="test-token",
    )


def test_real_otel_adapter_queries_all_three_backends(endpoints: OtelEndpoints) -> None:
    adapter = OtelObservabilityAdapter(
        endpoints, transport=httpx.MockTransport(handler), retry_attempts=1
    )
    record = incident()
    metrics = adapter.query_metrics(record)
    logs = adapter.search_logs(record)
    traces = adapter.query_traces(record)
    dependencies = adapter.query_dependencies(record)

    assert metrics.payload["http_error_rate"]["value"] == 22.1
    assert metrics.payload["_backend"] == "prometheus"
    assert logs.payload["backend"] == "loki"
    assert "TypeError" in logs.payload["entries"][0]["message"]
    assert traces.payload["backend"] == "tempo"
    assert traces.payload["spans"][0]["trace_id"] == "abc123"
    assert dependencies.payload["lab"]["healthy"] is False
    assert adapter.status().available is True


def test_otel_lab_client_enforces_typed_action(endpoints: OtelEndpoints) -> None:
    client = OtelLabClient(endpoints, transport=httpx.MockTransport(handler))
    assert client.inject("otel-incident-1")["fault_active"] is True
    result = client.execute(incident(), "rollback_release", "approval-001")
    assert result["release_version"] == "2.3.6"
    assert result["fault_active"] is False
    with pytest.raises(ValueError, match="only permits rollback_release"):
        client.execute(incident(), "delete_namespace", "approval-002")


def test_tempo_query_tolerates_async_ingestion_delay(endpoints: OtelEndpoints) -> None:
    attempts = 0

    def delayed_handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.url.path == "/api/search":
            attempts += 1
            if attempts < 3:
                return httpx.Response(200, json={"traces": []})
        return handler(request)

    adapter = OtelObservabilityAdapter(
        endpoints,
        transport=httpx.MockTransport(delayed_handler),
        retry_attempts=3,
        retry_delay=0,
    )

    traces = adapter.query_traces(incident())

    assert attempts == 3
    assert traces.payload["spans"][0]["trace_id"] == "abc123"
