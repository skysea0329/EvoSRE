from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from ..database import Database
from ..models import (
    SkillEvolutionResult,
    SkillVersionRecord,
    SkillVersionStatus,
)
from .holdout_evaluation import HoldoutEvaluator
from .skill_registry import SKILLS_ROOT, SkillRegistry, canonical_digest


class SkillLifecycle:
    """Creates, gates, atomically activates and rolls back immutable skill snapshots."""

    def __init__(
        self,
        database: Database,
        artifact_dir: Path,
        report_path: Path,
        evaluator: HoldoutEvaluator | None = None,
    ) -> None:
        self.database = database
        self.artifact_dir = artifact_dir
        self.report_path = report_path
        self.evaluator = evaluator or HoldoutEvaluator()
        self.registry = SkillRegistry(database)

    def initialize(self) -> SkillVersionRecord:
        static_payload = json.loads(
            (SKILLS_ROOT / "static" / "incident-response" / "signatures.json").read_text(
                encoding="utf-8"
            )
        )
        evolved_payload = json.loads(
            (SKILLS_ROOT / "evolved" / "v1" / "signatures.json").read_text(encoding="utf-8")
        )
        now = datetime.now(UTC)
        self.database.register_skill_version(
            SkillVersionRecord(
                version=static_payload["version"],
                status=SkillVersionStatus.RETIRED,
                digest=canonical_digest(static_payload),
                rules=static_payload["rules"],
                source_count=8,
                created_at=now,
            )
        )
        self.database.register_skill_version(
            SkillVersionRecord(
                version=evolved_payload["version"],
                parent_version=evolved_payload.get("parent"),
                status=SkillVersionStatus.ACTIVE,
                digest=canonical_digest(evolved_payload),
                rules=evolved_payload["rules"],
                source_count=12,
                created_at=now,
                activated_at=now,
            )
        )
        active = self.database.get_active_skill()
        if active is None:
            raise RuntimeError("Skill lifecycle initialization did not produce an active version")
        return active

    def evolve(self, actor: str, reason: str) -> SkillEvolutionResult:
        parent_record = self.database.get_active_skill()
        if parent_record is None:
            parent_record = self.initialize()
        parent = self.registry.snapshot_from_record(parent_record)
        payload, source_count = self.registry.build_candidate_payload(parent)
        snapshot = self.registry.snapshot_from_payload(parent.condition, payload)
        candidate = SkillVersionRecord(
            version=snapshot.version,
            parent_version=parent.version,
            status=SkillVersionStatus.CANDIDATE,
            digest=snapshot.digest,
            rules=snapshot.rules,
            source_count=source_count,
            created_at=datetime.now(UTC),
        )
        self.database.register_skill_version(candidate)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        (self.artifact_dir / f"{candidate.version}.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

        report = self.evaluator.compare(parent, snapshot)
        self.evaluator.persist(report, self.report_path)
        candidate = self.database.record_skill_evaluation(
            candidate.version, report, accepted=report.promotion_passed
        )
        if report.promotion_passed:
            active = self.database.activate_skill(
                candidate.version,
                actor,
                reason,
                kind="promotion",
                payload={
                    "holdout_report_id": report.id,
                    "dataset_version": report.dataset_version,
                    "baseline_macro_score": report.baseline.macro_score,
                    "candidate_macro_score": report.candidate.macro_score,
                },
            )
            promoted = True
        else:
            active = self.database.get_active_skill()
            if active is None:
                raise RuntimeError("Rejected candidate left the registry without an active skill")
            promoted = False
        candidate = self.database.get_skill_version(candidate.version) or candidate
        return SkillEvolutionResult(
            candidate=candidate,
            active=active,
            report=report,
            promoted=promoted,
        )

    def rollback(self, actor: str, reason: str, target_version: str | None = None) -> SkillVersionRecord:
        current = self.database.get_active_skill()
        if current is None:
            raise RuntimeError("No active skill is available")
        target = target_version or current.parent_version
        if not target:
            raise ValueError("The active skill has no parent to roll back to")
        record = self.database.get_skill_version(target)
        if record is None:
            raise LookupError(f"Rollback target {target} does not exist")
        if record.status != SkillVersionStatus.RETIRED:
            raise ValueError("Rollback target must be a previously active retired version")
        return self.database.activate_skill(
            target,
            actor,
            reason,
            kind="rollback",
            payload={"rolled_back_from": current.version},
        )
