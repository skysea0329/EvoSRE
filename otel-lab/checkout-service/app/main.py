from __future__ import annotations

import asyncio
import logging
import os
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

import asyncpg
import httpx
import redis.asyncio as redis
from fastapi import FastAPI, Header, HTTPException
from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import Observation
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode
from pydantic import BaseModel


SERVICE_NAME = "checkout-lab"
OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").rstrip("/")
LAB_TOKEN = os.getenv("EVOSRE_LAB_TOKEN", "evosre-local-lab-token")
POSTGRES_DSN = os.getenv("POSTGRES_DSN", "")
REDIS_URL = os.getenv("REDIS_URL", "")
TOXIPROXY_URL = os.getenv("TOXIPROXY_URL", "").rstrip("/")
PAYMENT_PRIMARY_URL = os.getenv("PAYMENT_PRIMARY_URL", "")
PAYMENT_BACKUP_URL = os.getenv("PAYMENT_BACKUP_URL", "")

LIVE_SCENARIOS = {
    "bad_deployment": "rollback_release",
    "db_pool_exhaustion": "increase_database_pool",
    "cache_outage": "enable_cache_bypass",
    "payment_timeout": "failover_payment_provider",
}

METRICS = {
    "bad_deployment": {
        "http_error_rate": (22.1, 0.2), "p95_latency": (780, 205),
        "coupon_checkout_errors": (96.0, 0.1), "cpu_utilization": (41, 37),
    },
    "db_pool_exhaustion": {
        "http_error_rate": (18.4, 0.3), "p95_latency": (4820, 226),
        "db_pool_waiters": (87, 0), "db_pool_utilization": (100, 42),
    },
    "cache_outage": {
        "http_error_rate": (4.8, 0.4), "p95_latency": (2140, 390),
        "cache_error_rate": (100, 0), "database_read_multiplier": (6.4, 1.3),
    },
    "payment_timeout": {
        "http_error_rate": (12.7, 0.5), "p95_latency": (6480, 344),
        "payment_p95_latency": (6220, 310), "worker_saturation": (94, 38),
    },
}


@dataclass(frozen=True)
class RuntimeState:
    incident_id: str = "idle"
    scenario: str = "bad_deployment"
    release_version: str = "2.3.6"
    fault_active: bool = False
    generation: int = 0
    last_action: str | None = None
    last_idempotency_key: str | None = None
    last_trace_id: str | None = None
    configuration: dict[str, Any] = field(default_factory=dict)
    updated_at: datetime = datetime.min.replace(tzinfo=UTC)


class StateView(BaseModel):
    incident_id: str
    scenario: str
    release_version: str
    fault_active: bool
    generation: int
    last_action: str | None
    last_trace_id: str | None
    configuration: dict[str, Any]
    updated_at: datetime


class InjectRequest(BaseModel):
    incident_id: str


class ActionRequest(BaseModel):
    incident_id: str
    action: str
    idempotency_key: str


class CheckoutLab:
    def __init__(self) -> None:
        self._state = RuntimeState()
        self._lock = threading.RLock()

    def state(self) -> RuntimeState:
        with self._lock:
            return self._state

    def inject(self, incident_id: str, scenario: str, configuration: dict[str, Any]) -> RuntimeState:
        if scenario not in LIVE_SCENARIOS:
            raise ValueError(f"Unsupported live scenario: {scenario}")
        with self._lock:
            self._state = RuntimeState(
                incident_id=incident_id,
                scenario=scenario,
                release_version="2.4.0" if scenario == "bad_deployment" else "2.3.6",
                fault_active=True,
                generation=self._state.generation + 1,
                configuration=configuration,
                updated_at=datetime.now(UTC),
            )
            return self._state

    def remediate(self, request: ActionRequest, configuration: dict[str, Any]) -> RuntimeState:
        with self._lock:
            current = self._state
            if request.incident_id != current.incident_id:
                raise ValueError("Incident does not own the active lab fault")
            expected = LIVE_SCENARIOS[current.scenario]
            if request.action != expected:
                raise ValueError(f"Only {expected} is allowed for {current.scenario}")
            if not current.fault_active and current.last_idempotency_key == request.idempotency_key:
                return current
            self._state = replace(
                current,
                release_version="2.3.6",
                fault_active=False,
                generation=current.generation + 1,
                last_action=request.action,
                last_idempotency_key=request.idempotency_key,
                configuration=configuration,
                updated_at=datetime.now(UTC),
            )
            return self._state

    def record_trace(self, trace_id: str) -> RuntimeState:
        with self._lock:
            self._state = replace(self._state, last_trace_id=trace_id)
            return self._state


class LiveDependencies:
    def __init__(self) -> None:
        self.pool: asyncpg.Pool | None = None
        self.held_connections: list[asyncpg.Connection] = []
        self.operation_lock = asyncio.Lock()

    async def start(self) -> None:
        if POSTGRES_DSN:
            self.pool = await asyncpg.create_pool(POSTGRES_DSN, min_size=2, max_size=2)
            async with self.pool.acquire() as connection:
                await connection.execute("CREATE TABLE IF NOT EXISTS lab_probe (id integer primary key)")
        if TOXIPROXY_URL:
            for attempt in range(20):
                try:
                    await self._ensure_proxy("redis-catalog", "0.0.0.0:6380", "redis:6379")
                    await self._ensure_proxy("primary-pay", "0.0.0.0:8666", "payment-provider:8020")
                    break
                except httpx.HTTPError:
                    if attempt == 19:
                        raise
                    await asyncio.sleep(0.5)
            await self.reset_proxies()

    async def stop(self) -> None:
        await self.release_pool()
        if self.pool:
            await self.pool.close()
            self.pool = None

    async def _ensure_proxy(self, name: str, listen: str, upstream: str) -> None:
        async with httpx.AsyncClient(timeout=2) as client:
            response = await client.post(
                f"{TOXIPROXY_URL}/proxies",
                json={"name": name, "listen": listen, "upstream": upstream, "enabled": True},
            )
            if response.status_code not in {201, 409}:
                response.raise_for_status()

    async def _proxy_enabled(self, name: str, enabled: bool) -> None:
        if not TOXIPROXY_URL:
            return
        async with httpx.AsyncClient(timeout=2) as client:
            response = await client.post(
                f"{TOXIPROXY_URL}/proxies/{name}", json={"enabled": enabled}
            )
            response.raise_for_status()

    async def _delete_toxic(self) -> None:
        if not TOXIPROXY_URL:
            return
        async with httpx.AsyncClient(timeout=2) as client:
            response = await client.delete(
                f"{TOXIPROXY_URL}/proxies/primary-pay/toxics/evosre-latency"
            )
            if response.status_code not in {204, 404}:
                response.raise_for_status()

    async def reset_proxies(self) -> None:
        await self._proxy_enabled("redis-catalog", True)
        await self._proxy_enabled("primary-pay", True)
        await self._delete_toxic()

    async def inject(self, scenario: str) -> dict[str, Any]:
        async with self.operation_lock:
            return await self._inject(scenario)

    async def _inject(self, scenario: str) -> dict[str, Any]:
        await self.release_pool()
        await self.reset_proxies()
        if scenario == "db_pool_exhaustion" and self.pool:
            self.held_connections = [await self.pool.acquire() for _ in range(2)]
            return {"postgres_pool_size": 2, "held_connections": 2}
        if scenario == "cache_outage":
            await self._proxy_enabled("redis-catalog", False)
            return {"redis_proxy_enabled": False, "cache_bypass": False}
        if scenario == "payment_timeout" and TOXIPROXY_URL:
            async with httpx.AsyncClient(timeout=2) as client:
                response = await client.post(
                    f"{TOXIPROXY_URL}/proxies/primary-pay/toxics",
                    json={
                        "name": "evosre-latency", "type": "latency", "stream": "downstream",
                        "toxicity": 1.0, "attributes": {"latency": 1500, "jitter": 0},
                    },
                )
                response.raise_for_status()
            return {"payment_route": "primary", "primary_latency_injected_ms": 1500}
        return {"release_version": "2.4.0" if scenario == "bad_deployment" else "2.3.6"}

    async def remediate(self, scenario: str) -> dict[str, Any]:
        async with self.operation_lock:
            return await self._remediate(scenario)

    async def _remediate(self, scenario: str) -> dict[str, Any]:
        if scenario == "db_pool_exhaustion":
            await self.release_pool()
            if self.pool:
                await self.pool.close()
                self.pool = await asyncpg.create_pool(POSTGRES_DSN, min_size=2, max_size=6)
            return {"postgres_pool_size": 6, "held_connections": 0}
        if scenario == "cache_outage":
            return {"redis_proxy_enabled": False, "cache_bypass": True}
        if scenario == "payment_timeout":
            return {"payment_route": "backup", "primary_circuit": "open"}
        return {"release_version": "2.3.6"}

    async def release_pool(self) -> None:
        if self.pool:
            for connection in self.held_connections:
                await self.pool.release(connection)
        self.held_connections = []

    async def probe(self, state: RuntimeState) -> tuple[bool, str, float]:
        async with self.operation_lock:
            return await self._probe(state)

    async def _probe(self, state: RuntimeState) -> tuple[bool, str, float]:
        started = asyncio.get_running_loop().time()
        try:
            if state.scenario == "bad_deployment" and state.fault_active:
                raise TypeError("couponPrice cannot read discount from null")
            if state.scenario == "db_pool_exhaustion" and self.pool:
                async with asyncio.timeout(0.15):
                    async with self.pool.acquire() as connection:
                        await connection.fetchval("SELECT 1")
            elif state.scenario == "cache_outage" and not state.configuration.get("cache_bypass"):
                if REDIS_URL:
                    client = redis.from_url(REDIS_URL, socket_connect_timeout=0.2)
                    try:
                        await client.ping()
                    finally:
                        await client.aclose()
            elif state.scenario == "payment_timeout":
                route = state.configuration.get("payment_route", "primary")
                url = PAYMENT_BACKUP_URL if route == "backup" else PAYMENT_PRIMARY_URL
                if url:
                    async with httpx.AsyncClient(timeout=0.35) as client:
                        response = await client.post(url)
                        response.raise_for_status()
            duration = (asyncio.get_running_loop().time() - started) * 1000
            return True, "dependency probe succeeded", duration
        except Exception as exc:
            duration = (asyncio.get_running_loop().time() - started) * 1000
            return False, f"{type(exc).__name__}: {exc}", duration


lab = CheckoutLab()
dependencies = LiveDependencies()


def configure_telemetry():
    resource = Resource.create(
        {"service.name": SERVICE_NAME, "service.namespace": "evosre", "deployment.environment": "local-lab"}
    )
    if OTLP_ENDPOINT:
        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{OTLP_ENDPOINT}/v1/traces"))
        )
        trace.set_tracer_provider(tracer_provider)
        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=f"{OTLP_ENDPOINT}/v1/metrics"), export_interval_millis=1_000
        )
        metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[metric_reader]))
        logger_provider = LoggerProvider(resource=resource)
        logger_provider.add_log_record_processor(
            BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{OTLP_ENDPOINT}/v1/logs"))
        )
        set_logger_provider(logger_provider)
        logging.getLogger().addHandler(LoggingHandler(logger_provider=logger_provider))
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    return trace.get_tracer(SERVICE_NAME), metrics.get_meter(SERVICE_NAME)


tracer, meter = configure_telemetry()
request_counter = meter.create_counter("evosre.checkout.requests", unit="1")


def metric_observation(name: str) -> list[Observation]:
    state = lab.state()
    pair = METRICS.get(state.scenario, {}).get(name)
    if pair is None:
        return []
    value = pair[0] if state.fault_active else pair[1]
    return [Observation(value, {"incident_id": state.incident_id, "scenario": state.scenario, "release_version": state.release_version})]


for metric_name in sorted({name for values in METRICS.values() for name in values}):
    meter.create_observable_gauge(
        f"evosre.checkout.{metric_name}",
        callbacks=[lambda _options, name=metric_name: metric_observation(name)],
    )


async def emit_probe(source: str = "traffic-generator") -> dict[str, object]:
    state = lab.state()
    attributes = {
        "incident.id": state.incident_id, "incident.scenario": state.scenario,
        "deployment.version": state.release_version, "traffic.source": source,
    }
    with tracer.start_as_current_span("POST /checkout", attributes=attributes) as span:
        trace_id = f"{span.get_span_context().trace_id:032x}"
        with tracer.start_as_current_span(f"probe {state.scenario}"):
            healthy, detail, measured_ms = await dependencies.probe(state)
        request_counter.add(1, {"incident_id": state.incident_id, "scenario": state.scenario, "outcome": "ok" if healthy else "error", "release_version": state.release_version})
        span.set_attribute("checkout.duration_ms", measured_ms)
        span.set_attribute("fault.active", state.fault_active)
        if healthy:
            logging.getLogger("checkout").info(
                "incident_id=%s scenario=%s dependency probe recovered version=%s",
                state.incident_id, state.scenario, state.release_version,
            )
        else:
            messages = {
                "bad_deployment": "TypeError couponPrice null discount release marker version=2.4.0",
                "db_pool_exhaustion": "QueuePool timeout pool waiters=87 acquisition exceeded 3000ms",
                "cache_outage": "redis-catalog connection refused cache retry 3/3 bypass disabled",
                "payment_timeout": "payment-client timeout provider=primary-pay circuit breaker disabled",
            }
            message = f"incident_id={state.incident_id} {messages[state.scenario]} detail={detail}"
            logging.getLogger("checkout").error(message)
            span.record_exception(RuntimeError(detail))
            span.set_status(Status(StatusCode.ERROR, state.scenario))
        lab.record_trace(trace_id)
        return {
            "status": 200 if healthy else 500,
            "duration_ms": round(measured_ms, 2),
            "detail": detail,
            "trace_id": trace_id,
        }


async def traffic_loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        if lab.state().incident_id != "idle":
            await emit_probe()
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.25)
        except TimeoutError:
            continue


@asynccontextmanager
async def lifespan(_: FastAPI):
    await dependencies.start()
    stop = asyncio.Event()
    task = asyncio.create_task(traffic_loop(stop), name="checkout-otel-traffic")
    yield
    stop.set()
    await task
    await dependencies.stop()


app = FastAPI(title="EvoSRE multi-fault checkout lab", version="2.0.0", lifespan=lifespan)


def view(state: RuntimeState) -> StateView:
    payload = state.__dict__.copy()
    payload.pop("last_idempotency_key")
    return StateView(**payload)


def require_token(token: str) -> None:
    if token != LAB_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid lab control token")


@app.get("/health")
async def health() -> dict[str, object]:
    return {"status": "ok", "service": SERVICE_NAME, "telemetry": "otlp" if OTLP_ENDPOINT else "disabled", "live_scenarios": sorted(LIVE_SCENARIOS)}


@app.get("/state", response_model=StateView)
async def state() -> StateView:
    return view(lab.state())


@app.get("/dependencies")
async def dependency_state() -> dict[str, object]:
    current = lab.state()
    healthy, detail, latency = await dependencies.probe(current)
    return {"scenario": current.scenario, "healthy": healthy, "detail": detail, "latency_ms": round(latency, 2), "configuration": current.configuration}


@app.get("/checkout")
async def checkout() -> dict[str, object]:
    result = await emit_probe("api-request")
    if result["status"] == 500:
        raise HTTPException(status_code=500, detail=str(result["detail"]))
    return result


@app.post("/admin/faults/{scenario}/inject", response_model=StateView)
async def inject_fault(scenario: str, payload: InjectRequest, x_evosre_lab_token: str = Header(default="")) -> StateView:
    require_token(x_evosre_lab_token)
    scenario = scenario.replace("-", "_")
    if scenario not in LIVE_SCENARIOS:
        raise HTTPException(status_code=404, detail="Unsupported live scenario")
    configuration = await dependencies.inject(scenario)
    current = lab.inject(payload.incident_id, scenario, configuration)
    await emit_probe("fault-injector")
    return view(lab.state())


@app.post("/admin/actions/{action}", response_model=StateView)
async def execute_action(action: str, payload: ActionRequest, x_evosre_lab_token: str = Header(default="")) -> StateView:
    require_token(x_evosre_lab_token)
    action = action.replace("-", "_")
    current = lab.state()
    if not current.fault_active and current.last_idempotency_key == payload.idempotency_key:
        return view(current)
    if payload.action != action:
        raise HTTPException(status_code=409, detail="Path action and payload action differ")
    try:
        expected = LIVE_SCENARIOS[current.scenario]
        if action != expected:
            raise ValueError(f"Only {expected} is allowed for {current.scenario}")
        configuration = await dependencies.remediate(current.scenario)
        updated = lab.remediate(payload, configuration)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await emit_probe("remediation-verifier")
    return view(lab.state())
