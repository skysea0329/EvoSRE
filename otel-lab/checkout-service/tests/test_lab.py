from fastapi.testclient import TestClient

from app.main import app


def test_fault_emits_failure_and_typed_rollback_recovers() -> None:
    with TestClient(app) as client:
        unauthorized = client.post(
            "/admin/faults/bad-deployment/inject", json={"incident_id": "lab-test"}
        )
        assert unauthorized.status_code == 403

        headers = {"x-evosre-lab-token": "evosre-local-lab-token"}
        injected = client.post(
            "/admin/faults/bad-deployment/inject",
            headers=headers,
            json={"incident_id": "lab-test"},
        )
        assert injected.status_code == 200
        assert injected.json()["release_version"] == "2.4.0"
        assert client.get("/checkout").status_code == 500

        wrong = client.post(
            "/admin/actions/rollback-release",
            headers=headers,
            json={
                "incident_id": "lab-test",
                "action": "delete_namespace",
                "idempotency_key": "lab-action-001",
            },
        )
        assert wrong.status_code == 409

        payload = {
            "incident_id": "lab-test",
            "action": "rollback_release",
            "idempotency_key": "lab-action-001",
        }
        recovered = client.post(
            "/admin/actions/rollback-release", headers=headers, json=payload
        )
        replay = client.post(
            "/admin/actions/rollback-release", headers=headers, json=payload
        )
        assert recovered.status_code == replay.status_code == 200
        assert recovered.json()["release_version"] == "2.3.6"
        assert recovered.json()["generation"] == replay.json()["generation"]
        assert client.get("/checkout").status_code == 200


def test_all_live_faults_enforce_their_typed_action_and_recover() -> None:
    headers = {"x-evosre-lab-token": "evosre-local-lab-token"}
    scenarios = {
        "db-pool-exhaustion": "increase-database-pool",
        "cache-outage": "enable-cache-bypass",
        "payment-timeout": "failover-payment-provider",
    }
    with TestClient(app) as client:
        for index, (scenario, action_path) in enumerate(scenarios.items()):
            incident_id = f"live-{index}"
            injected = client.post(
                f"/admin/faults/{scenario}/inject", headers=headers,
                json={"incident_id": incident_id},
            )
            assert injected.status_code == 200
            state = injected.json()
            assert state["scenario"] == scenario.replace("-", "_")
            assert state["fault_active"] is True

            action = action_path.replace("-", "_")
            rejected = client.post(
                "/admin/actions/rollback-release", headers=headers,
                json={"incident_id": incident_id, "action": "rollback_release", "idempotency_key": f"wrong-{index}"},
            )
            assert rejected.status_code == 409
            recovered = client.post(
                f"/admin/actions/{action_path}", headers=headers,
                json={"incident_id": incident_id, "action": action, "idempotency_key": f"ok-{index}"},
            )
            assert recovered.status_code == 200
            assert recovered.json()["fault_active"] is False
