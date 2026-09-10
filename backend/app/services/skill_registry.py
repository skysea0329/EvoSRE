from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..models import ScenarioKey, SkillCondition, SkillVersionRecord

if TYPE_CHECKING:
    from ..database import Database


def _repository_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "skills").is_dir() and (candidate / "evals").is_dir():
            return candidate
    raise RuntimeError("Could not locate EvoSRE skills/ and evals/ directories")


REPOSITORY_ROOT = _repository_root()
SKILLS_ROOT = REPOSITORY_ROOT / "skills"
EVALS_ROOT = REPOSITORY_ROOT / "evals"


BASELINE_RULES = [
    {"scenario": "db_pool_exhaustion", "tokens": ["queuepool", "pool waiters"]},
    {"scenario": "payment_timeout", "tokens": ["primary-pay", "payment-client timeout"]},
    {"scenario": "bad_deployment", "tokens": ["couponprice", "version=2.4.0"]},
    {"scenario": "cache_outage", "tokens": ["redis-catalog", "cache retry"]},
]

INJECTION_MARKERS = (
    "ignore previous",
    "system prompt",
    "assistant:",
    "[untrusted-log]",
    "choose ",
    "execute action",
    "classify as",
)


def canonical_digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class SkillSnapshot:
    condition: SkillCondition
    version: str
    rules: list[dict[str, Any]]
    digest: str


@dataclass(frozen=True)
class Classification:
    scenario: ScenarioKey | None
    confidence: float
    matched_signatures: int
    version: str


class SkillRegistry:
    """Loads policy snapshots and performs injection-aware signature classification."""

    def __init__(self, database: Database | None = None) -> None:
        self.database = database

    @staticmethod
    def snapshot_from_payload(
        condition: SkillCondition, payload: dict[str, Any]
    ) -> SkillSnapshot:
        normalized = {
            "version": str(payload["version"]),
            "parent": payload.get("parent"),
            "rules": list(payload["rules"]),
        }
        return SkillSnapshot(
            condition=condition,
            version=normalized["version"],
            rules=normalized["rules"],
            digest=canonical_digest(normalized),
        )

    @classmethod
    def snapshot_from_record(cls, record: SkillVersionRecord) -> SkillSnapshot:
        return cls.snapshot_from_payload(
            SkillCondition.EVOLVED,
            {"version": record.version, "parent": record.parent_version, "rules": record.rules},
        )

    def load(self, condition: SkillCondition) -> SkillSnapshot:
        if condition == SkillCondition.NONE:
            payload = {"version": "no-skill-baseline", "rules": BASELINE_RULES}
        elif condition == SkillCondition.STATIC:
            payload = json.loads(
                (SKILLS_ROOT / "static" / "incident-response" / "signatures.json").read_text(
                    encoding="utf-8"
                )
            )
        else:
            active = self.database.get_active_skill() if self.database else None
            if active is not None:
                return self.snapshot_from_record(active)
            payload = json.loads(
                (SKILLS_ROOT / "evolved" / "v1" / "signatures.json").read_text(
                    encoding="utf-8"
                )
            )
        return self.snapshot_from_payload(condition, payload)

    @staticmethod
    def sanitize_corpus(corpus: str) -> str:
        safe_lines = []
        for line in corpus.lower().splitlines():
            if any(marker in line for marker in INJECTION_MARKERS):
                continue
            safe_lines.append(line)
        return "\n".join(safe_lines)

    def classify(self, snapshot: SkillSnapshot, corpus: str) -> Classification:
        lowered = self.sanitize_corpus(corpus)
        scored: list[tuple[int, int, str]] = []
        for index, rule in enumerate(snapshot.rules):
            tokens = [str(token).lower() for token in rule.get("tokens", [])]
            patterns = [str(pattern) for pattern in rule.get("patterns", [])]
            token_score = sum(token in lowered for token in tokens)
            pattern_score = sum(bool(re.search(pattern, lowered, re.IGNORECASE)) for pattern in patterns)
            score = token_score + pattern_score
            minimum = int(rule.get("min_matches", 1))
            if score >= minimum:
                scored.append((score, -index, str(rule["scenario"])))
        if not scored:
            return Classification(None, 0.0, 0, snapshot.version)
        score, _priority, scenario = max(scored)
        return Classification(
            ScenarioKey(scenario), min(0.99, 0.55 + score * 0.12), score, snapshot.version
        )

    def infer(self, condition: SkillCondition, corpus: str) -> tuple[ScenarioKey, float, str]:
        snapshot = self.load(condition)
        result = self.classify(snapshot, corpus)
        scenario = result.scenario or ScenarioKey.DB_POOL_EXHAUSTION
        confidence = result.confidence if result.scenario is not None else 0.4
        return scenario, confidence, snapshot.version

    def build_candidate_payload(self, parent: SkillSnapshot) -> tuple[dict[str, Any], int]:
        feedback = json.loads((EVALS_ROOT / "training_feedback.json").read_text(encoding="utf-8"))
        by_scenario = {str(rule["scenario"]): dict(rule) for rule in parent.rules}
        for item in feedback["resolved_trajectories"]:
            scenario = str(item["scenario"])
            current = by_scenario.get(scenario, {"scenario": scenario})
            current["tokens"] = sorted(
                set(str(value).lower() for value in current.get("tokens", []))
                | set(str(value).lower() for value in item.get("verified_signatures", []))
            )
            current["patterns"] = sorted(
                set(str(value) for value in current.get("patterns", []))
                | set(str(value) for value in item.get("generalized_patterns", []))
            )
            current["min_matches"] = int(item.get("min_matches", 2))
            by_scenario[scenario] = current
        version = f"evolved-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"
        payload = {
            "version": version,
            "parent": parent.version,
            "training_dataset": feedback["dataset_version"],
            "rules": list(by_scenario.values()),
            "status": "candidate",
        }
        return payload, len(feedback["resolved_trajectories"])

    def create_candidate_from_feedback(self, output_dir: Path | None = None) -> Path:
        parent = self.load(SkillCondition.EVOLVED)
        payload, _source_count = self.build_candidate_payload(parent)
        candidate_dir = output_dir or SKILLS_ROOT / "candidates"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        path = candidate_dir / f"{payload['version']}.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return path
