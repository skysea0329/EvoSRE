from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..database import Database
from ..models import IncidentRecord, LabState
from .scenarios import scenario_for
from .action_policy import ActionPolicy


class FaultLab:
    """Persistent local service state used by tools and typed remediation actions.

    The lab is intentionally small, but actions change a real persisted resource rather
    than only rewriting the incident summary. Metrics and deployment probes read this
    state again after remediation to verify recovery.
    """

    def __init__(self, database: Database) -> None:
        self.database = database

    def inject(self, incident: IncidentRecord) -> LabState:
        definition = scenario_for(incident.scenario)
        deployment = definition.deployment or {}
        state = LabState(
            incident_id=incident.id,
            scenario=incident.scenario,
            service=incident.service,
            fault_active=True,
            generation=1,
            release_version=deployment.get("version"),
            configuration={
                "fault": incident.scenario.value,
                "expected_action": definition.action["action"],
                "injected_by": incident.reporter,
            },
            updated_at=datetime.now(UTC),
        )
        return self.database.create_lab_state(state)

    def require_state(self, incident_id: str) -> LabState:
        state = self.database.get_lab_state(incident_id)
        if state is None:
            raise LookupError(f"Fault lab state for incident {incident_id} does not exist")
        return state

    def metrics(self, incident: IncidentRecord) -> dict[str, tuple[float, str, float]]:
        state = self.require_state(incident.id)
        definition = scenario_for(incident.scenario)
        return definition.metrics_before if state.fault_active else definition.metrics_after

    def deployment(self, incident: IncidentRecord) -> dict[str, Any] | None:
        state = self.require_state(incident.id)
        definition = scenario_for(incident.scenario)
        if definition.deployment is None:
            return None
        deployment = dict(definition.deployment)
        deployment["version"] = state.release_version or deployment["version"]
        deployment["fault_active"] = state.fault_active
        deployment["generation"] = state.generation
        return deployment

    def execute(self, incident: IncidentRecord, action: str) -> tuple[LabState, LabState]:
        state = self.require_state(incident.id)
        ActionPolicy.authorize(incident, action, state.incident_id)
        definition = scenario_for(incident.scenario)
        expected = definition.action["action"]
        if action != expected:
            raise ValueError(f"Action {action!r} is outside this scenario's typed boundary")
        if not state.fault_active and state.last_action == action:
            return state, state

        configuration = {
            **state.configuration,
            "applied_action": action,
            "recovery_mode": definition.action.get("recovery_mode", "bounded"),
        }
        release_version = state.release_version
        if action == "rollback_release" and definition.deployment:
            release_version = definition.deployment.get("previous_version")
        updated = self.database.update_lab_state(
            incident.id,
            fault_active=False,
            release_version=release_version,
            configuration=configuration,
            last_action=action,
        )
        return state, updated
