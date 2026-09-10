from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


LIVE_SCENARIOS = {
    "bad_deployment": "rollback_release",
    "db_pool_exhaustion": "increase_database_pool",
    "cache_outage": "enable_cache_bypass",
    "payment_timeout": "failover_payment_provider",
}


def request(base_url: str, path: str, body: dict[str, Any] | None = None) -> Any:
    payload = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}", data=payload,
        headers={"content-type": "application/json"},
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"{req.method} {path} failed: {exc.code} {detail}") from exc


def wait_for(base_url: str, path: str, predicate, timeout: float = 90) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            last = request(base_url, path)
            if predicate(last):
                return last
        except (OSError, RuntimeError):
            pass
        time.sleep(0.5)
    raise TimeoutError(f"Timed out waiting for {path}; last={last}")


def run(base_url: str) -> dict[str, Any]:
    started = time.monotonic()
    status = wait_for(
        base_url, "/api/observability/status",
        lambda value: value.get("available") is True,
    )
    results: list[dict[str, Any]] = []
    for scenario, expected_action in LIVE_SCENARIOS.items():
        created = request(
            base_url,
            "/api/incidents",
            {
                "scenario": scenario, "skill_condition": "evolved",
                "observability_mode": "otel", "reporter": "CI live acceptance",
            },
        )
        incident_id = created["id"]
        diagnosed = wait_for(
            base_url, f"/api/incidents/{incident_id}",
            lambda value: value.get("status") in {"awaiting_approval", "failed"},
        )
        if diagnosed["status"] == "failed":
            raise AssertionError(f"{scenario} diagnosis failed: {diagnosed.get('error')}")
        if diagnosed["proposal"]["action"] != expected_action:
            raise AssertionError(f"{scenario} proposed {diagnosed['proposal']['action']}")
        sources = {item["kind"]: item["source"] for item in diagnosed["evidence"]}
        required = {"metric": "/api/v1/query", "log": "/loki/api/v1/query_range", "trace": "/api/search", "dependency": "/dependencies"}
        for kind, marker in required.items():
            if marker not in sources.get(kind, ""):
                raise AssertionError(f"{scenario} lacks live {kind} evidence: {sources}")
        request(
            base_url,
            f"/api/incidents/{incident_id}/action/approve",
            {
                "actor": "CI operator",
                "reason": "Live evidence, action scope and rollback boundary verified by acceptance test.",
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        resolved = wait_for(
            base_url, f"/api/incidents/{incident_id}",
            lambda value: value.get("status") in {"resolved", "failed"},
        )
        if resolved["status"] != "resolved":
            raise AssertionError(f"{scenario} remediation failed: {resolved.get('error')}")
        if not resolved["recovery_checks"] or not all(item["passed"] for item in resolved["recovery_checks"]):
            raise AssertionError(f"{scenario} recovery checks did not pass")
        events = request(base_url, f"/api/incidents/{incident_id}/events")
        results.append(
            {
                "scenario": scenario,
                "incident_id": incident_id,
                "action": expected_action,
                "evidence_kinds": sorted(sources),
                "recovery_checks": len(resolved["recovery_checks"]),
                "audit_events": len(events),
                "passed": True,
            }
        )
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "observability_components": status["components"],
        "scenario_count": len(results),
        "duration_seconds": round(time.monotonic() - started, 2),
        "passed": all(item["passed"] for item in results),
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Exercise the complete EvoSRE live OTel remediation loop")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=Path("evals/latest-live-acceptance.json"))
    args = parser.parse_args()
    report = run(args.base_url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
