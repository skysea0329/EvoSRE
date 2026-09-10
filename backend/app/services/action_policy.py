from __future__ import annotations

from ..models import ActionStatus, IncidentRecord, IncidentStatus
from .scenarios import scenario_for


class ActionPolicyViolation(ValueError):
    """Raised when an action cannot cross the deterministic mutation boundary."""


class ActionPolicy:
    """Model-independent authorization policy evaluated immediately before mutation."""

    @staticmethod
    def authorize(incident: IncidentRecord, action: str, resource_owner_id: str) -> None:
        proposal = incident.proposal
        expected = scenario_for(incident.scenario).action["action"]
        if resource_owner_id != incident.id:
            raise ActionPolicyViolation("The incident does not own the target resource")
        if proposal is None:
            raise ActionPolicyViolation("No remediation proposal exists")
        if action != expected or proposal.action != expected:
            raise ActionPolicyViolation("The action is outside the scenario allowlist")
        if not proposal.requires_approval:
            raise ActionPolicyViolation("Dangerous actions cannot disable approval")
        if incident.status != IncidentStatus.REMEDIATING:
            raise ActionPolicyViolation("The incident is not in the remediating state")
        if proposal.status != ActionStatus.APPROVED:
            raise ActionPolicyViolation("The proposal has not been approved")
        if not incident.decision_actor or not incident.decision_reason:
            raise ActionPolicyViolation("An attributable approval decision is required")
