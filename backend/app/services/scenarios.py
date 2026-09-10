from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from ..models import ScenarioKey, ScenarioSummary, Severity


@dataclass(frozen=True)
class ScenarioDefinition:
    summary: ScenarioSummary
    root_cause: str
    metrics_before: dict[str, tuple[float, str, float]]
    metrics_after: dict[str, tuple[float, str, float]]
    logs: list[dict[str, str]]
    dependencies_before: list[dict[str, Any]]
    dependencies_after: list[dict[str, Any]]
    deployment: dict[str, Any] | None
    runbook: list[str]
    action: dict[str, str]
    trace_spans: list[dict[str, Any]] = field(default_factory=list)


SCENARIOS: dict[ScenarioKey, ScenarioDefinition] = {
    ScenarioKey.DB_POOL_EXHAUSTION: ScenarioDefinition(
        summary=ScenarioSummary(
            key=ScenarioKey.DB_POOL_EXHAUSTION,
            name="Checkout database pool exhaustion",
            service="checkout-api",
            severity=Severity.CRITICAL,
            description="Checkout requests stall while PostgreSQL pool waiters increase.",
            signal="5xx 18.4% · p95 4.8s",
        ),
        root_cause="A traffic burst exhausted checkout-api's 20-connection PostgreSQL pool; requests timed out while database health remained available.",
        metrics_before={
            "http_error_rate": (18.4, "%", 1.0), "p95_latency": (4_820, "ms", 500),
            "db_pool_waiters": (87, "requests", 3), "db_pool_utilization": (100, "%", 80),
        },
        metrics_after={
            "http_error_rate": (0.3, "%", 1.0), "p95_latency": (226, "ms", 500),
            "db_pool_waiters": (0, "requests", 3), "db_pool_utilization": (42, "%", 80),
        },
        logs=[
            {"level": "ERROR", "message": "sqlalchemy.exc.TimeoutError: QueuePool limit of size 20 overflow 0 reached"},
            {"level": "WARN", "message": "checkout request waited 3000ms for a database connection"},
            {"level": "INFO", "message": "traffic rate increased from 110 rps to 390 rps"},
        ],
        dependencies_before=[{"name": "postgres-orders", "status": "degraded", "latency_ms": 32, "error_rate": 0.0, "detail": "Reachable; 87 client-side pool waiters"}],
        dependencies_after=[{"name": "postgres-orders", "status": "healthy", "latency_ms": 28, "error_rate": 0.0, "detail": "Pool utilization normalized"}],
        deployment=None,
        runbook=["Confirm database server health before changing the client pool.", "Increase the checkout pool from 20 to 60 for the current traffic envelope.", "Verify pool waiters, p95 latency and error rate for two windows."],
        action={"action": "increase_database_pool", "description": "Increase checkout-api PostgreSQL pool from 20 to 60 connections.", "risk": "Medium — raises database concurrency; bounded below the database connection budget.", "expected": "Pool waiters return to zero and checkout p95 latency falls below 500 ms."},
    ),
    ScenarioKey.PAYMENT_TIMEOUT: ScenarioDefinition(
        summary=ScenarioSummary(
            key=ScenarioKey.PAYMENT_TIMEOUT,
            name="Payment provider timeout cascade",
            service="checkout-api",
            severity=Severity.HIGH,
            description="A slow payment dependency holds checkout workers until requests time out.",
            signal="payment p95 6.2s · checkout errors 12.7%",
        ),
        root_cause="The primary payment provider exceeded the 3-second timeout and the checkout circuit breaker was disabled, causing a timeout cascade.",
        metrics_before={"http_error_rate": (12.7, "%", 1.0), "p95_latency": (6_480, "ms", 500), "payment_p95_latency": (6_220, "ms", 800), "worker_saturation": (94, "%", 75)},
        metrics_after={"http_error_rate": (0.5, "%", 1.0), "p95_latency": (344, "ms", 500), "payment_p95_latency": (310, "ms", 800), "worker_saturation": (38, "%", 75)},
        logs=[
            {"level": "ERROR", "message": "payment-client timeout after 3000ms provider=primary-pay"},
            {"level": "WARN", "message": "circuit breaker primary-pay is configured disabled"},
            {"level": "ERROR", "message": "checkout request canceled while waiting for payment authorization"},
        ],
        dependencies_before=[{"name": "primary-pay", "status": "unhealthy", "latency_ms": 6220, "error_rate": 31.0, "detail": "Timeout threshold exceeded"}, {"name": "backup-pay", "status": "healthy", "latency_ms": 310, "error_rate": 0.2, "detail": "Standby route available"}],
        dependencies_after=[{"name": "primary-pay", "status": "isolated", "latency_ms": 0, "error_rate": 0.0, "detail": "Circuit opened"}, {"name": "backup-pay", "status": "healthy", "latency_ms": 310, "error_rate": 0.2, "detail": "Receiving checkout traffic"}],
        deployment=None,
        runbook=["Check the payment provider and backup route independently.", "Open the primary provider circuit when p95 exceeds three seconds.", "Route new authorizations to backup-pay and verify success rate."],
        action={"action": "failover_payment_provider", "description": "Open the primary-pay circuit and route authorizations to backup-pay.", "risk": "Medium — changes payment routing; no transaction data is modified.", "expected": "Payment latency falls below 800 ms and checkout errors below 1%."},
    ),
    ScenarioKey.BAD_DEPLOYMENT: ScenarioDefinition(
        summary=ScenarioSummary(
            key=ScenarioKey.BAD_DEPLOYMENT,
            name="Checkout regression after deployment",
            service="checkout-api",
            severity=Severity.CRITICAL,
            description="A fresh checkout release raises 5xx errors on coupon orders.",
            signal="v2.4.0 · 5xx 22.1%",
        ),
        root_cause="checkout-api v2.4.0 introduced a null coupon dereference; errors began immediately after deployment and affect coupon orders.",
        metrics_before={"http_error_rate": (22.1, "%", 1.0), "p95_latency": (780, "ms", 500), "coupon_checkout_errors": (96.0, "%", 1.0), "cpu_utilization": (41, "%", 80)},
        metrics_after={"http_error_rate": (0.2, "%", 1.0), "p95_latency": (205, "ms", 500), "coupon_checkout_errors": (0.1, "%", 1.0), "cpu_utilization": (37, "%", 80)},
        logs=[
            {"level": "ERROR", "message": "TypeError: Cannot read properties of null (reading 'discount') at couponPrice"},
            {"level": "INFO", "message": "release marker checkout-api version=2.4.0 sha=8f31ad2"},
            {"level": "ERROR", "message": "POST /checkout 500 segment=coupon_order"},
        ],
        dependencies_before=[{"name": "postgres-orders", "status": "healthy", "latency_ms": 29, "error_rate": 0.0, "detail": "No dependency anomaly"}, {"name": "primary-pay", "status": "healthy", "latency_ms": 280, "error_rate": 0.1, "detail": "No dependency anomaly"}],
        dependencies_after=[{"name": "postgres-orders", "status": "healthy", "latency_ms": 27, "error_rate": 0.0, "detail": "Healthy"}, {"name": "primary-pay", "status": "healthy", "latency_ms": 276, "error_rate": 0.1, "detail": "Healthy"}],
        deployment={"version": "2.4.0", "previous_version": "2.3.6", "sha": "8f31ad2", "deployer": "release-bot", "age_minutes": 8, "change": "coupon pricing refactor"},
        runbook=["Correlate the first error timestamp with release markers.", "If a fresh release causes a segment-wide regression, roll back before debugging forward.", "Verify coupon and non-coupon checkout probes after rollback."],
        action={"action": "rollback_release", "description": "Roll back checkout-api from v2.4.0 to the last healthy release v2.3.6.", "risk": "Low — known healthy artifact; new v2.4.0 changes are temporarily removed.", "expected": "Coupon checkout errors and global 5xx rate return below 1%."},
    ),
    ScenarioKey.CACHE_OUTAGE: ScenarioDefinition(
        summary=ScenarioSummary(
            key=ScenarioKey.CACHE_OUTAGE,
            name="Catalog Redis cache outage",
            service="catalog-api",
            severity=Severity.HIGH,
            description="Redis connection failures amplify database reads and catalog latency.",
            signal="cache errors 100% · DB reads +640%",
        ),
        root_cause="The catalog Redis node stopped accepting connections and cache bypass was disabled, causing every product lookup to retry before falling through to PostgreSQL.",
        metrics_before={"http_error_rate": (4.8, "%", 1.0), "p95_latency": (2_140, "ms", 500), "cache_error_rate": (100, "%", 1.0), "database_read_multiplier": (6.4, "x", 1.5)},
        metrics_after={"http_error_rate": (0.4, "%", 1.0), "p95_latency": (390, "ms", 500), "cache_error_rate": (0, "%", 1.0), "database_read_multiplier": (1.3, "x", 1.5)},
        logs=[
            {"level": "ERROR", "message": "redis.exceptions.ConnectionError: connection refused redis-catalog:6379"},
            {"level": "WARN", "message": "cache retry 3/3 failed; bypass mode is disabled"},
            {"level": "WARN", "message": "catalog database read volume exceeded normal envelope"},
        ],
        dependencies_before=[{"name": "redis-catalog", "status": "unhealthy", "latency_ms": 0, "error_rate": 100.0, "detail": "Connection refused"}, {"name": "postgres-catalog", "status": "degraded", "latency_ms": 180, "error_rate": 0.2, "detail": "Read load 6.4x baseline"}],
        dependencies_after=[{"name": "redis-catalog", "status": "isolated", "latency_ms": 0, "error_rate": 100.0, "detail": "Bypassed pending repair"}, {"name": "postgres-catalog", "status": "healthy", "latency_ms": 72, "error_rate": 0.1, "detail": "Protected by bounded bypass"}],
        deployment=None,
        runbook=["Confirm Redis reachability and database headroom.", "Enable bounded cache bypass to stop retry amplification.", "Keep Redis isolated until the node is repaired, then warm cache before rejoining."],
        action={"action": "enable_cache_bypass", "description": "Enable bounded cache bypass and stop Redis retry amplification.", "risk": "Medium — increases direct reads, protected by request coalescing and rate limits.", "expected": "Catalog p95 falls below 500 ms while database read load remains below 1.5x."},
    ),
    ScenarioKey.HIGH_CPU: ScenarioDefinition(
        summary=ScenarioSummary(
            key=ScenarioKey.HIGH_CPU, name="Recommendation service CPU saturation",
            service="recommendation-api", severity=Severity.HIGH,
            description="A runaway ranking fallback saturates CPU and delays recommendations.",
            signal="CPU 99% · recommendation p95 3.9s",
        ),
        root_cause="A feature-flagged exhaustive ranking fallback created a tight CPU loop in recommendation-api.",
        metrics_before={"http_error_rate": (7.2, "%", 1.0), "p95_latency": (3_940, "ms", 500), "cpu_utilization": (99, "%", 80), "run_queue": (18, "threads", 4)},
        metrics_after={"http_error_rate": (0.2, "%", 1.0), "p95_latency": (184, "ms", 500), "cpu_utilization": (34, "%", 80), "run_queue": (2, "threads", 4)},
        logs=[
            {"level": "WARN", "message": "ranking fallback exhaustive_scan enabled feature=recommendation-v2"},
            {"level": "ERROR", "message": "event loop delay 2870ms cpu throttle active"},
            {"level": "INFO", "message": "profile top frame=rank_all_candidates samples=91%"},
        ],
        dependencies_before=[{"name": "product-catalog", "status": "healthy", "latency_ms": 42, "error_rate": 0.1, "detail": "Dependency healthy; saturation is local"}],
        dependencies_after=[{"name": "product-catalog", "status": "healthy", "latency_ms": 39, "error_rate": 0.1, "detail": "Healthy"}],
        deployment=None,
        runbook=["Confirm CPU is local rather than downstream wait time.", "Disable exhaustive ranking fallback.", "Verify CPU, event loop delay and recommendation latency."],
        action={"action": "disable_exhaustive_ranking", "description": "Disable the exhaustive ranking fallback feature flag.", "risk": "Low — reverts to the bounded stable ranking path.", "expected": "CPU falls below 80% and p95 below 500 ms."},
        trace_spans=[
            {"service": "frontend", "operation": "GET /recommendations", "duration_ms": 3970, "status": "error"},
            {"service": "recommendation-api", "operation": "rank_all_candidates", "duration_ms": 3810, "status": "error", "attributes": {"feature.recommendation-v2": True}},
        ],
    ),
    ScenarioKey.MEMORY_LEAK: ScenarioDefinition(
        summary=ScenarioSummary(
            key=ScenarioKey.MEMORY_LEAK, name="Email worker memory leak",
            service="email-worker", severity=Severity.HIGH,
            description="An unbounded template cache grows until the worker is repeatedly OOM-killed.",
            signal="RSS 1.9GiB · 4 OOM restarts",
        ),
        root_cause="email-worker retained rendered templates in an unbounded cache introduced by template-cache-v3.",
        metrics_before={"http_error_rate": (5.1, "%", 1.0), "p95_latency": (1_420, "ms", 500), "memory_rss": (1940, "MiB", 768), "restart_count": (4, "restarts", 0)},
        metrics_after={"http_error_rate": (0.1, "%", 1.0), "p95_latency": (210, "ms", 500), "memory_rss": (312, "MiB", 768), "restart_count": (0, "restarts", 0)},
        logs=[
            {"level": "ERROR", "message": "OOMKilled container=email-worker limit=2Gi"},
            {"level": "WARN", "message": "template_cache entries=184221 eviction=disabled feature=template-cache-v3"},
            {"level": "INFO", "message": "heap retained_by=RenderedTemplateCache 1.41GiB"},
        ],
        dependencies_before=[{"name": "smtp-relay", "status": "healthy", "latency_ms": 63, "error_rate": 0.0, "detail": "Relay healthy"}],
        dependencies_after=[{"name": "smtp-relay", "status": "healthy", "latency_ms": 61, "error_rate": 0.0, "detail": "Healthy"}],
        deployment={"version": "3.7.1", "previous_version": "3.7.0", "sha": "b319c4e", "deployer": "release-bot", "age_minutes": 46, "change": "template cache v3"},
        runbook=["Confirm restart reason and retained heap owner.", "Disable the unbounded cache and restart one worker.", "Verify RSS remains below 768MiB for two windows."],
        action={"action": "disable_template_cache", "description": "Disable template-cache-v3 and roll the affected worker.", "risk": "Medium — temporarily increases template render CPU.", "expected": "RSS stabilizes below 768MiB with no OOM restarts."},
        trace_spans=[
            {"service": "notification-api", "operation": "enqueue_email", "duration_ms": 72, "status": "ok"},
            {"service": "email-worker", "operation": "render_template", "duration_ms": 1380, "status": "error", "attributes": {"cache.entries": 184221}},
        ],
    ),
    ScenarioKey.KAFKA_LAG: ScenarioDefinition(
        summary=ScenarioSummary(
            key=ScenarioKey.KAFKA_LAG, name="Order events Kafka consumer lag",
            service="order-consumer", severity=Severity.CRITICAL,
            description="A poisoned event retry loop blocks a partition and grows consumer lag.",
            signal="consumer lag 128k · oldest event 19m",
        ),
        root_cause="A malformed order event retried indefinitely on partition 7 because dead-letter routing was disabled.",
        metrics_before={"http_error_rate": (0.2, "%", 1.0), "p95_latency": (220, "ms", 500), "consumer_lag": (128400, "messages", 1000), "oldest_event_age": (1140, "s", 60)},
        metrics_after={"http_error_rate": (0.2, "%", 1.0), "p95_latency": (215, "ms", 500), "consumer_lag": (420, "messages", 1000), "oldest_event_age": (18, "s", 60)},
        logs=[
            {"level": "ERROR", "message": "deserialization failed partition=7 offset=884201 schema=order.v4"},
            {"level": "WARN", "message": "retry attempt=982 dlq_enabled=false partition blocked"},
            {"level": "INFO", "message": "partitions 0-6 and 8-11 healthy"},
        ],
        dependencies_before=[{"name": "kafka-orders", "status": "degraded", "latency_ms": 8, "error_rate": 0.0, "detail": "Partition 7 blocked by poison event"}],
        dependencies_after=[{"name": "kafka-orders", "status": "healthy", "latency_ms": 7, "error_rate": 0.0, "detail": "DLQ route active; partitions draining"}],
        deployment=None,
        runbook=["Identify the lagging partition and poison offset.", "Enable DLQ routing before skipping the poison event.", "Verify lag and oldest-event age converge."],
        action={"action": "route_poison_event_to_dlq", "description": "Enable DLQ routing and quarantine the malformed partition-7 event.", "risk": "Medium — quarantines one event for later operator review.", "expected": "Consumer lag falls below 1,000 and oldest event below 60 seconds."},
        trace_spans=[
            {"service": "order-consumer", "operation": "consume order.v4", "duration_ms": 30000, "status": "error", "attributes": {"messaging.kafka.partition": 7, "retry.count": 982}},
        ],
    ),
    ScenarioKey.POD_NOT_READY: ScenarioDefinition(
        summary=ScenarioSummary(
            key=ScenarioKey.POD_NOT_READY, name="Cart service readiness failure",
            service="cart-api", severity=Severity.HIGH,
            description="A stale readiness path removes all cart pods from service.",
            signal="Ready 0/3 · cart 503 38%",
        ),
        root_cause="The cart deployment probed the removed /healthz endpoint, so healthy containers never became Ready.",
        metrics_before={"http_error_rate": (38.0, "%", 1.0), "p95_latency": (910, "ms", 500), "unavailable_pods": (3, "pods", 0), "readiness_failures": (126, "checks", 0)},
        metrics_after={"http_error_rate": (0.3, "%", 1.0), "p95_latency": (190, "ms", 500), "unavailable_pods": (0, "pods", 0), "readiness_failures": (0, "checks", 0)},
        logs=[
            {"level": "WARN", "message": "Readiness probe failed status=404 path=/healthz pod=cart-api-7d9"},
            {"level": "INFO", "message": "container listening health endpoint=/readyz"},
            {"level": "ERROR", "message": "service cart-api has no ready endpoints"},
        ],
        dependencies_before=[{"name": "redis-cart", "status": "healthy", "latency_ms": 5, "error_rate": 0.0, "detail": "Dependency healthy"}],
        dependencies_after=[{"name": "redis-cart", "status": "healthy", "latency_ms": 5, "error_rate": 0.0, "detail": "Healthy"}],
        deployment={"version": "1.9.0", "previous_version": "1.8.4", "sha": "0a44f9c", "deployer": "helm-controller", "age_minutes": 13, "change": "health endpoint rename"},
        runbook=["Compare probe path with the container's advertised endpoint.", "Patch readiness path to /readyz.", "Wait for all replicas before verifying traffic."],
        action={"action": "patch_readiness_probe", "description": "Patch the cart readiness probe from /healthz to /readyz.", "risk": "Low — updates only the health check path.", "expected": "All three pods become Ready and 503 rate returns below 1%."},
        trace_spans=[
            {"service": "frontend", "operation": "POST /cart", "duration_ms": 905, "status": "error", "attributes": {"http.status_code": 503}},
            {"service": "cart-api", "operation": "readiness /healthz", "duration_ms": 2, "status": "error", "attributes": {"http.status_code": 404}},
        ],
    ),
    ScenarioKey.DNS_FAILURE: ScenarioDefinition(
        summary=ScenarioSummary(
            key=ScenarioKey.DNS_FAILURE, name="Shipping dependency DNS failure",
            service="checkout-api", severity=Severity.HIGH,
            description="Checkout cannot resolve the shipping service after a service rename.",
            signal="DNS NXDOMAIN 100% · checkout 5xx 14%",
        ),
        root_cause="checkout-api still resolved shipping-v1 after the Kubernetes Service was renamed to shipping-api.",
        metrics_before={"http_error_rate": (14.1, "%", 1.0), "p95_latency": (1_240, "ms", 500), "dns_error_rate": (100, "%", 1.0), "shipping_error_rate": (100, "%", 1.0)},
        metrics_after={"http_error_rate": (0.4, "%", 1.0), "p95_latency": (242, "ms", 500), "dns_error_rate": (0, "%", 1.0), "shipping_error_rate": (0.2, "%", 1.0)},
        logs=[
            {"level": "ERROR", "message": "getaddrinfo ENOTFOUND shipping-v1 namespace=shop"},
            {"level": "INFO", "message": "service registry endpoint=shipping-api.shop.svc.cluster.local"},
            {"level": "WARN", "message": "shipping quote unavailable; checkout aborted"},
        ],
        dependencies_before=[{"name": "shipping-v1", "status": "unhealthy", "latency_ms": 0, "error_rate": 100.0, "detail": "NXDOMAIN"}, {"name": "shipping-api", "status": "healthy", "latency_ms": 46, "error_rate": 0.1, "detail": "Current service name"}],
        dependencies_after=[{"name": "shipping-api", "status": "healthy", "latency_ms": 44, "error_rate": 0.1, "detail": "Resolved via cluster DNS"}],
        deployment=None,
        runbook=["Compare failing hostname with service discovery.", "Update the bounded shipping endpoint configuration.", "Verify DNS and shipping quote probes."],
        action={"action": "update_shipping_hostname", "description": "Update checkout shipping host from shipping-v1 to shipping-api.", "risk": "Low — switches to the discovered healthy service record.", "expected": "DNS errors fall to zero and shipping success exceeds 99%."},
        trace_spans=[
            {"service": "checkout-api", "operation": "resolve shipping-v1", "duration_ms": 1004, "status": "error", "attributes": {"error.type": "NXDOMAIN"}},
        ],
    ),
    ScenarioKey.CERTIFICATE_EXPIRY: ScenarioDefinition(
        summary=ScenarioSummary(
            key=ScenarioKey.CERTIFICATE_EXPIRY, name="Inventory mTLS certificate expiry",
            service="inventory-api", severity=Severity.CRITICAL,
            description="Inventory calls fail TLS verification after a workload certificate expires.",
            signal="TLS failures 100% · inventory unavailable",
        ),
        root_cause="inventory-api presented an expired workload certificate because certificate rotation was paused.",
        metrics_before={"http_error_rate": (31.0, "%", 1.0), "p95_latency": (860, "ms", 500), "tls_handshake_failures": (440, "checks", 0), "certificate_expiry_overdue": (1, "days", 0)},
        metrics_after={"http_error_rate": (0.2, "%", 1.0), "p95_latency": (176, "ms", 500), "tls_handshake_failures": (0, "checks", 0), "certificate_expiry_overdue": (0, "days", 0)},
        logs=[
            {"level": "ERROR", "message": "x509: certificate has expired subject=spiffe://shop/inventory-api"},
            {"level": "WARN", "message": "cert-manager rotation paused annotation=maintenance"},
            {"level": "INFO", "message": "issuer shop-workload-ca healthy"},
        ],
        dependencies_before=[{"name": "shop-workload-ca", "status": "healthy", "latency_ms": 18, "error_rate": 0.0, "detail": "Issuer ready; leaf is expired"}],
        dependencies_after=[{"name": "shop-workload-ca", "status": "healthy", "latency_ms": 17, "error_rate": 0.0, "detail": "New leaf issued"}],
        deployment=None,
        runbook=["Confirm leaf expiry and issuer health.", "Resume rotation and issue a new workload certificate.", "Verify SPIFFE identity and TLS handshakes."],
        action={"action": "rotate_workload_certificate", "description": "Resume rotation and issue a new inventory workload certificate.", "risk": "Medium — rotates a live workload identity under the same issuer.", "expected": "TLS failures return to zero with at least 7 days validity."},
        trace_spans=[
            {"service": "checkout-api", "operation": "inventory-api/ListStock", "duration_ms": 842, "status": "error", "attributes": {"error.type": "x509_expired"}},
        ],
    ),
    ScenarioKey.DISK_PRESSURE: ScenarioDefinition(
        summary=ScenarioSummary(
            key=ScenarioKey.DISK_PRESSURE, name="Search node disk pressure",
            service="search-api", severity=Severity.HIGH,
            description="Unbounded debug traces fill the search node volume and block index writes.",
            signal="disk 97% · index writes rejected",
        ),
        root_cause="Verbose query tracing wrote unbounded files to the search data volume until the node crossed its flood-stage watermark.",
        metrics_before={"http_error_rate": (9.8, "%", 1.0), "p95_latency": (2_240, "ms", 500), "disk_utilization": (97, "%", 85), "index_write_rejections": (318, "writes", 0)},
        metrics_after={"http_error_rate": (0.3, "%", 1.0), "p95_latency": (260, "ms", 500), "disk_utilization": (61, "%", 85), "index_write_rejections": (0, "writes", 0)},
        logs=[
            {"level": "ERROR", "message": "flood stage disk watermark exceeded index read_only_allow_delete=true"},
            {"level": "WARN", "message": "debug query trace directory size=420Gi retention=unbounded"},
            {"level": "INFO", "message": "snapshot repository healthy latest=12m"},
        ],
        dependencies_before=[{"name": "search-volume", "status": "degraded", "latency_ms": 31, "error_rate": 18.0, "detail": "97% used; flood-stage active"}],
        dependencies_after=[{"name": "search-volume", "status": "healthy", "latency_ms": 12, "error_rate": 0.0, "detail": "61% used; writes restored"}],
        deployment=None,
        runbook=["Identify the largest safe-to-delete artifact class.", "Disable verbose tracing and prune expired debug traces.", "Clear read-only block and verify disk/write health."],
        action={"action": "prune_debug_traces", "description": "Disable verbose query tracing and prune expired debug trace files.", "risk": "Medium — removes only generated debug artifacts covered by retention policy.", "expected": "Disk falls below 85% and index writes resume."},
        trace_spans=[
            {"service": "search-api", "operation": "index product", "duration_ms": 2190, "status": "error", "attributes": {"disk.watermark": "flood_stage"}},
        ],
    ),
    ScenarioKey.RATE_LIMIT_MISCONFIG: ScenarioDefinition(
        summary=ScenarioSummary(
            key=ScenarioKey.RATE_LIMIT_MISCONFIG, name="Frontend rate-limit regression",
            service="api-gateway", severity=Severity.MEDIUM,
            description="A policy rollout applies the internal crawler limit to authenticated users.",
            signal="429 rate 42% · backend healthy",
        ),
        root_cause="api-gateway policy v18 matched authenticated users to the 5 RPM crawler bucket due to rule ordering.",
        metrics_before={"http_error_rate": (42.0, "%", 1.0), "p95_latency": (86, "ms", 500), "rate_limited_requests": (18400, "requests", 100), "backend_error_rate": (0.1, "%", 1.0)},
        metrics_after={"http_error_rate": (0.4, "%", 1.0), "p95_latency": (92, "ms", 500), "rate_limited_requests": (54, "requests", 100), "backend_error_rate": (0.1, "%", 1.0)},
        logs=[
            {"level": "WARN", "message": "rate_limit denied bucket=crawler-5rpm identity=authenticated rule=policy-v18:12"},
            {"level": "INFO", "message": "backend upstream success_rate=99.9%"},
            {"level": "INFO", "message": "policy v18 deployed 6 minutes before 429 spike"},
        ],
        dependencies_before=[{"name": "catalog-api", "status": "healthy", "latency_ms": 73, "error_rate": 0.1, "detail": "Upstream healthy"}],
        dependencies_after=[{"name": "catalog-api", "status": "healthy", "latency_ms": 76, "error_rate": 0.1, "detail": "Healthy"}],
        deployment={"version": "policy-v18", "previous_version": "policy-v17", "sha": "pol18a2", "deployer": "gateway-controller", "age_minutes": 6, "change": "crawler bucket ordering"},
        runbook=["Separate gateway denials from upstream failures.", "Restore authenticated-user rule precedence.", "Verify 429 rate and crawler protection independently."],
        action={"action": "restore_rate_limit_precedence", "description": "Restore authenticated-user precedence while retaining crawler limits.", "risk": "Low — reorders two validated policy rules.", "expected": "User 429 rate returns below 1% while crawler limits remain active."},
        trace_spans=[
            {"service": "api-gateway", "operation": "rate_limit evaluate", "duration_ms": 2, "status": "error", "attributes": {"http.status_code": 429, "policy.rule": "v18:12"}},
        ],
    ),
}


def list_scenarios() -> list[ScenarioSummary]:
    return [definition.summary for definition in SCENARIOS.values()]


def scenario_for(key: ScenarioKey) -> ScenarioDefinition:
    return deepcopy(SCENARIOS[key])
