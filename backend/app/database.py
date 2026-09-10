from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import (
    ActionDecision,
    ActionStatus,
    DashboardStats,
    IncidentEvent,
    IncidentRecord,
    IncidentStatus,
    LabState,
    ScenarioKey,
    Severity,
    SkillLifecycleEvent,
    SkillVersionRecord,
    SkillVersionStatus,
)


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS incidents (
                    id TEXT PRIMARY KEY,
                    scenario TEXT NOT NULL,
                    service TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    resolved_at TEXT,
                    data_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status);
                CREATE INDEX IF NOT EXISTS idx_incidents_created ON incidents(created_at DESC);

                CREATE TABLE IF NOT EXISTS incident_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    incident_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(incident_id) REFERENCES incidents(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_events_incident ON incident_events(incident_id, id);

                CREATE TABLE IF NOT EXISTS lab_states (
                    incident_id TEXT PRIMARY KEY,
                    scenario TEXT NOT NULL,
                    service TEXT NOT NULL,
                    fault_active INTEGER NOT NULL,
                    generation INTEGER NOT NULL,
                    release_version TEXT,
                    configuration_json TEXT NOT NULL DEFAULT '{}',
                    last_action TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(incident_id) REFERENCES incidents(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS skill_versions (
                    version TEXT PRIMARY KEY,
                    parent_version TEXT,
                    status TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    artifact_json TEXT NOT NULL,
                    source_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    activated_at TEXT,
                    evaluation_json TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_skill
                    ON skill_versions(status) WHERE status = 'active';

                CREATE TABLE IF NOT EXISTS skill_lifecycle_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    version TEXT NOT NULL,
                    previous_version TEXT,
                    actor TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_skill_events_created
                    ON skill_lifecycle_events(created_at DESC, id DESC);
                """
            )

    @staticmethod
    def _skill_from_row(row: sqlite3.Row) -> SkillVersionRecord:
        artifact = json.loads(row["artifact_json"])
        return SkillVersionRecord(
            version=row["version"],
            parent_version=row["parent_version"],
            status=SkillVersionStatus(row["status"]),
            digest=row["digest"],
            rules=artifact["rules"],
            source_count=row["source_count"],
            created_at=datetime.fromisoformat(row["created_at"]),
            activated_at=(
                datetime.fromisoformat(row["activated_at"]) if row["activated_at"] else None
            ),
            evaluation=(
                json.loads(row["evaluation_json"]) if row["evaluation_json"] else None
            ),
        )

    def register_skill_version(self, record: SkillVersionRecord) -> SkillVersionRecord:
        artifact = {"version": record.version, "parent": record.parent_version, "rules": record.rules}
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO skill_versions
                (version, parent_version, status, digest, artifact_json, source_count,
                 created_at, activated_at, evaluation_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.version,
                    record.parent_version,
                    record.status.value,
                    record.digest,
                    json.dumps(artifact, ensure_ascii=False),
                    record.source_count,
                    record.created_at.isoformat(),
                    record.activated_at.isoformat() if record.activated_at else None,
                    (
                        json.dumps(record.evaluation.model_dump(mode="json"), ensure_ascii=False)
                        if record.evaluation else None
                    ),
                ),
            )
        return self.get_skill_version(record.version) or record

    def get_skill_version(self, version: str) -> SkillVersionRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM skill_versions WHERE version = ?", (version,)
            ).fetchone()
        return self._skill_from_row(row) if row else None

    def get_active_skill(self) -> SkillVersionRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM skill_versions WHERE status = 'active'"
            ).fetchone()
        return self._skill_from_row(row) if row else None

    def list_skill_versions(self) -> list[SkillVersionRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM skill_versions ORDER BY created_at DESC"
            ).fetchall()
        return [self._skill_from_row(row) for row in rows]

    def record_skill_evaluation(
        self, version: str, evaluation: Any, accepted: bool
    ) -> SkillVersionRecord:
        next_status = SkillVersionStatus.CANDIDATE if accepted else SkillVersionStatus.REJECTED
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """UPDATE skill_versions SET status = ?, evaluation_json = ?
                WHERE version = ? AND status = 'candidate'""",
                (
                    next_status.value,
                    json.dumps(evaluation.model_dump(mode="json"), ensure_ascii=False),
                    version,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"Skill {version} is not an evaluable candidate")
        record = self.get_skill_version(version)
        if record is None:
            raise LookupError(version)
        return record

    def activate_skill(
        self,
        version: str,
        actor: str,
        reason: str,
        kind: str = "promotion",
        payload: dict[str, Any] | None = None,
    ) -> SkillVersionRecord:
        now = datetime.now(UTC)
        with self._lock, self._connect() as connection:
            target = connection.execute(
                "SELECT status FROM skill_versions WHERE version = ?", (version,)
            ).fetchone()
            if target is None:
                raise LookupError(f"Skill {version} not found")
            if target["status"] == SkillVersionStatus.REJECTED.value:
                raise ValueError("Rejected skills cannot be activated")
            current = connection.execute(
                "SELECT version FROM skill_versions WHERE status = 'active'"
            ).fetchone()
            previous = current["version"] if current else None
            if previous == version:
                return self.get_skill_version(version)  # type: ignore[return-value]
            if previous:
                connection.execute(
                    "UPDATE skill_versions SET status = 'retired' WHERE version = ?", (previous,)
                )
            connection.execute(
                """UPDATE skill_versions SET status = 'active', activated_at = ?
                WHERE version = ?""",
                (now.isoformat(), version),
            )
            connection.execute(
                """INSERT INTO skill_lifecycle_events
                (kind, version, previous_version, actor, reason, created_at, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    kind,
                    version,
                    previous,
                    actor,
                    reason,
                    now.isoformat(),
                    json.dumps(payload or {}, ensure_ascii=False),
                ),
            )
        record = self.get_skill_version(version)
        if record is None:
            raise LookupError(version)
        return record

    def list_skill_events(self) -> list[SkillLifecycleEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM skill_lifecycle_events ORDER BY id DESC"
            ).fetchall()
        return [
            SkillLifecycleEvent(
                id=row["id"],
                kind=row["kind"],
                version=row["version"],
                previous_version=row["previous_version"],
                actor=row["actor"],
                reason=row["reason"],
                created_at=datetime.fromisoformat(row["created_at"]),
                payload=json.loads(row["payload_json"]),
            )
            for row in rows
        ]

    def create_incident(self, incident: IncidentRecord) -> IncidentRecord:
        payload = json.dumps(incident.model_dump(mode="json"), ensure_ascii=False)
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO incidents
                (id, scenario, service, severity, status, created_at, updated_at, resolved_at, data_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    incident.id, incident.scenario.value, incident.service, incident.severity.value,
                    incident.status.value, incident.created_at.isoformat(), incident.updated_at.isoformat(),
                    incident.resolved_at.isoformat() if incident.resolved_at else None, payload,
                ),
            )
        return incident

    def get_incident(self, incident_id: str) -> IncidentRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT data_json FROM incidents WHERE id = ?", (incident_id,)
            ).fetchone()
        return IncidentRecord.model_validate_json(row["data_json"]) if row else None

    def list_incidents(self) -> list[IncidentRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT data_json FROM incidents ORDER BY created_at DESC"
            ).fetchall()
        return [IncidentRecord.model_validate_json(row["data_json"]) for row in rows]

    def list_non_terminal_incidents(self) -> list[IncidentRecord]:
        terminal = {
            IncidentStatus.RESOLVED,
            IncidentStatus.DISMISSED,
            IncidentStatus.FAILED,
            IncidentStatus.PAUSED,
            IncidentStatus.AWAITING_APPROVAL,
        }
        return [item for item in self.list_incidents() if item.status not in terminal]

    def update_incident(self, incident_id: str, **fields: Any) -> IncidentRecord:
        with self._lock:
            current = self.get_incident(incident_id)
            if current is None:
                raise LookupError(f"Incident {incident_id} not found")
            updated = current.model_copy(update={**fields, "updated_at": datetime.now(UTC)})
            updated = IncidentRecord.model_validate(updated)
            payload = json.dumps(updated.model_dump(mode="json"), ensure_ascii=False)
            with self._connect() as connection:
                connection.execute(
                    """UPDATE incidents SET scenario = ?, service = ?, severity = ?, status = ?,
                    updated_at = ?, resolved_at = ?, data_json = ? WHERE id = ?""",
                    (
                        updated.scenario.value, updated.service, updated.severity.value,
                        updated.status.value, updated.updated_at.isoformat(),
                        updated.resolved_at.isoformat() if updated.resolved_at else None,
                        payload, incident_id,
                    ),
                )
        return updated

    def add_event(
        self, incident_id: str, kind: str, title: str, detail: str,
        payload: dict[str, Any] | None = None,
    ) -> IncidentEvent:
        created_at = datetime.now(UTC)
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO incident_events
                (incident_id, kind, title, detail, created_at, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (incident_id, kind, title, detail, created_at.isoformat(), json.dumps(payload or {}, ensure_ascii=False)),
            )
            event_id = int(cursor.lastrowid)
        return IncidentEvent(
            id=event_id, incident_id=incident_id, kind=kind, title=title,
            detail=detail, created_at=created_at, payload=payload or {},
        )

    def decide_action(
        self, incident_id: str, decision: ActionDecision, approved: bool
    ) -> tuple[IncidentRecord, bool]:
        """Atomically record one approval decision and make exact retries idempotent."""
        with self._lock:
            current = self.get_incident(incident_id)
            if current is None:
                raise LookupError(f"Incident {incident_id} not found")
            existing_key = current.metadata.get("decision_idempotency_key")
            if existing_key == decision.idempotency_key:
                return current, False
            if current.status != IncidentStatus.AWAITING_APPROVAL or current.proposal is None:
                raise ValueError("No pending remediation is awaiting approval")
            if current.proposal.status != ActionStatus.PENDING:
                raise ValueError("Remediation proposal was already decided")

            proposal_status = ActionStatus.APPROVED if approved else ActionStatus.REJECTED
            next_status = IncidentStatus.REMEDIATING if approved else IncidentStatus.DISMISSED
            proposal = current.proposal.model_copy(update={"status": proposal_status})
            updated = self.update_incident(
                incident_id,
                status=next_status,
                proposal=proposal,
                decision_actor=decision.actor.strip(),
                decision_reason=decision.reason.strip(),
                decided_at=datetime.now(UTC),
                metadata={
                    **current.metadata,
                    "decision_idempotency_key": decision.idempotency_key,
                },
            )
            return updated, True

    def create_lab_state(self, state: LabState) -> LabState:
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO lab_states
                (incident_id, scenario, service, fault_active, generation, release_version,
                 configuration_json, last_action, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    state.incident_id,
                    state.scenario.value,
                    state.service,
                    int(state.fault_active),
                    state.generation,
                    state.release_version,
                    json.dumps(state.configuration, ensure_ascii=False),
                    state.last_action,
                    state.updated_at.isoformat(),
                ),
            )
        return state

    def get_lab_state(self, incident_id: str) -> LabState | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM lab_states WHERE incident_id = ?", (incident_id,)
            ).fetchone()
        if row is None:
            return None
        return LabState(
            incident_id=row["incident_id"],
            scenario=ScenarioKey(row["scenario"]),
            service=row["service"],
            fault_active=bool(row["fault_active"]),
            generation=row["generation"],
            release_version=row["release_version"],
            configuration=json.loads(row["configuration_json"]),
            last_action=row["last_action"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def update_lab_state(self, incident_id: str, **fields: Any) -> LabState:
        with self._lock:
            current = self.get_lab_state(incident_id)
            if current is None:
                raise LookupError(f"Lab state for {incident_id} not found")
            updated = LabState.model_validate(
                current.model_copy(
                    update={**fields, "generation": current.generation + 1, "updated_at": datetime.now(UTC)}
                )
            )
            return self.create_lab_state(updated)

    def list_events(self, incident_id: str, after_id: int = 0) -> list[IncidentEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM incident_events WHERE incident_id = ? AND id > ? ORDER BY id""",
                (incident_id, after_id),
            ).fetchall()
        return [
            IncidentEvent(
                id=row["id"], incident_id=row["incident_id"], kind=row["kind"],
                title=row["title"], detail=row["detail"],
                created_at=datetime.fromisoformat(row["created_at"]),
                payload=json.loads(row["payload_json"]),
            )
            for row in rows
        ]

    def stats(self) -> DashboardStats:
        incidents = self.list_incidents()
        active_statuses = {
            IncidentStatus.QUEUED, IncidentStatus.INVESTIGATING,
            IncidentStatus.AWAITING_APPROVAL, IncidentStatus.REMEDIATING, IncidentStatus.PAUSED,
        }
        resolved = [item for item in incidents if item.status == IncidentStatus.RESOLVED]
        mttr = [
            (item.resolved_at - item.created_at).total_seconds()
            for item in resolved if item.resolved_at is not None
        ]
        return DashboardStats(
            total_incidents=len(incidents),
            active_incidents=sum(item.status in active_statuses for item in incidents),
            awaiting_approval=sum(item.status == IncidentStatus.AWAITING_APPROVAL for item in incidents),
            critical_incidents=sum(
                item.severity == Severity.CRITICAL and item.status in active_statuses for item in incidents
            ),
            resolved_incidents=len(resolved),
            average_mttr_seconds=round(sum(mttr) / len(mttr), 2) if mttr else 0.0,
        )
