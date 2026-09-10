from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import IncidentRecord, IncidentStatus, SkillCondition
from app.services.scenarios import scenario_for


@pytest.fixture
def client(tmp_path: Path):
    app = create_app(
        Settings(database_path=tmp_path / "evosre.db", investigation_step_delay_ms=0)
    )
    with TestClient(app) as test_client:
        yield test_client


def wait_for_status(
    client: TestClient, incident_id: str, statuses: set[str], attempts: int = 200
) -> dict:
    for _ in range(attempts):
        response = client.get(f"/api/incidents/{incident_id}")
        assert response.status_code == 200
        incident = response.json()
        if incident["status"] in statuses:
            return incident
        time.sleep(0.01)
    raise AssertionError(f"Incident {incident_id} did not reach {statuses}")


def decision_payload(key: str = "decision-0001") -> dict[str, str]:
    return {
        "actor": "Li Ming",
        "reason": "The bounded remediation is approved after evidence review.",
        "idempotency_key": key,
    }


def test_health_and_scenario_catalog(client: TestClient) -> None:
    health = client.get("/api/health").json()
    assert health == {"status": "ok", "service": "EvoSRE", "environment": "development"}
    scenarios = client.get("/api/scenarios").json()
    assert len(scenarios) == 12
    assert {item["key"] for item in scenarios} == {
        "db_pool_exhaustion", "payment_timeout", "bad_deployment", "cache_outage",
        "high_cpu", "memory_leak", "kafka_lag", "pod_not_ready", "dns_failure",
        "certificate_expiry", "disk_pressure", "rate_limit_misconfig",
    }


@pytest.mark.parametrize(
    ("scenario", "expected_action"),
    [
        ("db_pool_exhaustion", "increase_database_pool"),
        ("payment_timeout", "failover_payment_provider"),
        ("bad_deployment", "rollback_release"),
        ("cache_outage", "enable_cache_bypass"),
        ("high_cpu", "disable_exhaustive_ranking"),
        ("memory_leak", "disable_template_cache"),
        ("kafka_lag", "route_poison_event_to_dlq"),
        ("pod_not_ready", "patch_readiness_probe"),
        ("dns_failure", "update_shipping_hostname"),
        ("certificate_expiry", "rotate_workload_certificate"),
        ("disk_pressure", "prune_debug_traces"),
        ("rate_limit_misconfig", "restore_rate_limit_precedence"),
    ],
)
def test_agent_diagnoses_all_reproducible_scenarios(
    client: TestClient, scenario: str, expected_action: str
) -> None:
    created = client.post(
        "/api/incidents",
        json={"scenario": scenario, "skill_condition": "evolved", "reporter": "Test engineer"},
    )
    assert created.status_code == 202
    incident = wait_for_status(client, created.json()["id"], {"awaiting_approval", "failed"})
    assert incident["status"] == "awaiting_approval", incident.get("error")
    assert incident["proposal"]["action"] == expected_action
    assert incident["metadata"]["predicted_scenario"] == scenario
    assert incident["metadata"]["skill_version"] == "evolved-v1"
    kinds = {item["kind"] for item in incident["evidence"]}
    assert {"metric", "log", "trace", "dependency", "runbook"} <= kinds


def test_real_rollback_is_approved_idempotently_and_verified(client: TestClient) -> None:
    created = client.post("/api/incidents/demo")
    incident_id = created.json()["id"]
    incident = wait_for_status(client, incident_id, {"awaiting_approval"})
    assert incident["proposal"]["requires_approval"] is True
    before = client.get(f"/api/incidents/{incident_id}/lab").json()
    assert before["fault_active"] is True
    assert before["release_version"] == "2.4.0"

    approved = client.post(
        f"/api/incidents/{incident_id}/action/approve", json=decision_payload()
    )
    assert approved.status_code == 200
    replay = client.post(
        f"/api/incidents/{incident_id}/action/approve", json=decision_payload()
    )
    assert replay.status_code == 200

    resolved = wait_for_status(client, incident_id, {"resolved", "failed"})
    assert resolved["status"] == "resolved", resolved.get("error")
    assert resolved["proposal"]["status"] == "executed"
    assert resolved["metadata"]["recovery_verified"] is True
    assert all(item["passed"] for item in resolved["recovery_checks"])
    after = client.get(f"/api/incidents/{incident_id}/lab").json()
    assert after["fault_active"] is False
    assert after["release_version"] == "2.3.6"
    assert after["generation"] == 2
    events = client.get(f"/api/incidents/{incident_id}/events").json()
    assert sum(event["kind"] == "approval" for event in events) == 1
    recovery = next(event for event in events if event["kind"] == "recovery")
    assert recovery["payload"]["lab_before"]["release_version"] == "2.4.0"
    assert recovery["payload"]["lab_after"]["release_version"] == "2.3.6"


def test_rejected_action_is_audited_without_mutating_fault(client: TestClient) -> None:
    created = client.post(
        "/api/incidents", json={"scenario": "payment_timeout", "reporter": "NOC"}
    )
    incident_id = created.json()["id"]
    wait_for_status(client, incident_id, {"awaiting_approval"})
    rejected = client.post(
        f"/api/incidents/{incident_id}/action/reject",
        json={
            "actor": "Duty manager",
            "reason": "Keep primary routing while the provider investigates.",
            "idempotency_key": "reject-0001",
        },
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "dismissed"
    lab = client.get(f"/api/incidents/{incident_id}/lab").json()
    assert lab["fault_active"] is True
    assert lab["generation"] == 1


def test_pause_and_resume_from_persisted_tool_checkpoint(tmp_path: Path) -> None:
    app = create_app(
        Settings(database_path=tmp_path / "pause.db", investigation_step_delay_ms=60)
    )
    with TestClient(app) as client:
        created = client.post(
            "/api/incidents", json={"scenario": "memory_leak", "reporter": "NOC"}
        ).json()
        for _ in range(100):
            checkpoint = client.get(f"/api/incidents/{created['id']}").json()
            if checkpoint["evidence"]:
                break
            time.sleep(0.01)
        assert checkpoint["evidence"]
        paused = client.post(
            f"/api/incidents/{created['id']}/pause",
            json={"actor": "NOC", "reason": "Pause while the incident owner joins the bridge."},
        )
        assert paused.status_code == 200
        time.sleep(0.12)
        checkpoint = client.get(f"/api/incidents/{created['id']}").json()
        assert checkpoint["status"] == "paused"
        assert 1 <= len(checkpoint["evidence"]) < 6
        resumed = client.post(
            f"/api/incidents/{created['id']}/resume",
            json={"actor": "NOC", "reason": "Incident owner reviewed the persisted evidence."},
        )
        assert resumed.status_code == 200
        finished = wait_for_status(client, created["id"], {"awaiting_approval", "failed"})
        assert finished["status"] == "awaiting_approval", finished.get("error")
        assert len({item["kind"] for item in finished["evidence"]}) == len(finished["evidence"])


def test_startup_recovers_a_queued_incident(tmp_path: Path) -> None:
    settings = Settings(database_path=tmp_path / "recovery.db", investigation_step_delay_ms=0)
    app = create_app(settings)
    definition = scenario_for("dns_failure")
    now = datetime.now(UTC)
    record = IncidentRecord(
        id="startup-recovery",
        title=definition.summary.name,
        service=definition.summary.service,
        scenario="dns_failure",
        skill_condition=SkillCondition.EVOLVED,
        severity=definition.summary.severity,
        status=IncidentStatus.QUEUED,
        reporter="restart-test",
        created_at=now,
        updated_at=now,
    )
    app.state.database.create_incident(record)
    app.state.fault_lab.inject(record)
    with TestClient(app) as client:
        recovered = wait_for_status(client, record.id, {"awaiting_approval", "failed"})
        assert recovered["status"] == "awaiting_approval", recovered.get("error")
        assert recovered["metadata"]["predicted_scenario"] == "dns_failure"


def test_skill_comparison_eval_and_promotion_gate(client: TestClient) -> None:
    response = client.post("/api/evals/run")
    assert response.status_code == 200
    report = response.json()
    assert report["scenario_count"] == 12
    scores = {item["condition"]: item for item in report["results"]}
    assert scores["none"]["diagnosis_accuracy"] == pytest.approx(4 / 12, abs=0.0001)
    assert scores["static"]["diagnosis_accuracy"] == pytest.approx(8 / 12, abs=0.0001)
    assert scores["evolved"]["diagnosis_accuracy"] == 1.0
    assert scores["evolved"]["action_accuracy"] == 1.0
    assert scores["evolved"]["trace_coverage"] == 1.0
    assert scores["evolved"]["unsafe_action_rate"] == 0.0
    assert report["promoted_skill_version"] == "evolved-v1"
    assert client.get("/api/evals/latest").json()["id"] == report["id"]
    evolution = client.post(
        "/api/skills/evolve",
        json={
            "actor": "Evaluation owner",
            "reason": "Promote only after the frozen holdout gates pass.",
        },
    )
    assert evolution.status_code == 200
    outcome = evolution.json()
    assert outcome["promoted"] is True
    assert outcome["report"]["baseline"]["known_accuracy"] == pytest.approx(0.5833)
    assert outcome["report"]["candidate"]["known_accuracy"] == 1.0
    assert outcome["report"]["candidate"]["ood_rejection_rate"] == 1.0
    assert outcome["report"]["candidate"]["injection_resistance_rate"] == 1.0
    assert outcome["report"]["candidate"]["policy_attack_total"] == 140
    assert outcome["report"]["candidate"]["policy_attack_blocked"] == 140
    assert outcome["report"]["candidate"]["policy_block_rate"] == 1.0
    assert outcome["report"]["candidate"]["legitimate_action_allowed"] is True
    assert outcome["report"]["leakage_check_passed"] is True
    assert outcome["report"]["max_train_holdout_similarity"] < 0.65
    assert outcome["report"]["repeated_runs"] == 3
    assert client.get("/api/skills/active").json()["version"] == outcome["candidate"]["version"]
    assert len(client.get("/api/skills/versions").json()) == 3

    rollback = client.post(
        "/api/skills/rollback",
        json={
            "actor": "Evaluation owner",
            "reason": "Run the audited rollback drill after promotion validation.",
        },
    )
    assert rollback.status_code == 200
    assert rollback.json()["version"] == "evolved-v1"
    events = client.get("/api/skills/events").json()
    assert [event["kind"] for event in events[:2]] == ["rollback", "promotion"]


def test_dashboard_stats_track_active_incidents(client: TestClient) -> None:
    created = client.post("/api/incidents/demo").json()
    wait_for_status(client, created["id"], {"awaiting_approval"})
    stats = client.get("/api/stats").json()
    assert stats["total_incidents"] == 1
    assert stats["active_incidents"] == 1
    assert stats["critical_incidents"] == 1
    assert stats["awaiting_approval"] == 1
