from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from secrets import token_hex

from ..models import (
    EvalConditionResult,
    EvalReport,
    IncidentRecord,
    IncidentStatus,
    SkillCondition,
)
from .diagnostic_agent import DiagnosticAgent
from .scenarios import SCENARIOS


class SkillEvaluator:
    """Runs the same frozen scenario set under three diagnostic policy conditions."""

    def __init__(self, output_path: Path) -> None:
        self.output_path = output_path

    async def run(self, persist: bool = True) -> EvalReport:
        results: list[EvalConditionResult] = []
        for condition in SkillCondition:
            cases: list[dict[str, object]] = []
            diagnosis_correct = 0
            action_correct = 0
            trace_cases = 0
            unsafe_actions = 0
            tool_counts: list[int] = []
            for scenario, definition in SCENARIOS.items():
                now = datetime.now(UTC)
                incident = IncidentRecord(
                    id=f"eval_{token_hex(5)}",
                    title=definition.summary.name,
                    service=definition.summary.service,
                    scenario=scenario,
                    skill_condition=condition,
                    severity=definition.summary.severity,
                    status=IncidentStatus.INVESTIGATING,
                    reporter="frozen-eval-harness",
                    created_at=now,
                    updated_at=now,
                )
                evidence, _hypotheses, _summary, _root_cause, proposal, _provider, inferred, version = (
                    await DiagnosticAgent().investigate(
                        incident, lambda *_args: None, enable_narrative=False
                    )
                )
                is_correct = inferred == scenario
                action_is_correct = proposal.action == definition.action["action"]
                trace_present = any(item.kind == "trace" for item in evidence)
                unsafe = not proposal.requires_approval
                diagnosis_correct += int(is_correct)
                action_correct += int(action_is_correct)
                trace_cases += int(trace_present)
                unsafe_actions += int(unsafe)
                tool_counts.append(len(evidence))
                cases.append(
                    {
                        "scenario": scenario.value,
                        "predicted": inferred.value,
                        "correct": is_correct,
                        "action": proposal.action,
                        "expected_action": definition.action["action"],
                        "action_correct": action_is_correct,
                        "trace_present": trace_present,
                        "requires_approval": proposal.requires_approval,
                        "skill_version": version,
                        "tool_calls": len(evidence),
                    }
                )
            total = len(cases)
            results.append(
                EvalConditionResult(
                    condition=condition,
                    total=total,
                    correct=diagnosis_correct,
                    diagnosis_accuracy=round(diagnosis_correct / total, 4),
                    action_accuracy=round(action_correct / total, 4),
                    trace_coverage=round(trace_cases / total, 4),
                    unsafe_action_rate=round(unsafe_actions / total, 4),
                    mean_tool_calls=round(sum(tool_counts) / total, 2),
                    cases=cases,
                )
            )

        by_condition = {item.condition: item for item in results}
        static_score = by_condition[SkillCondition.STATIC].diagnosis_accuracy
        evolved_score = by_condition[SkillCondition.EVOLVED].diagnosis_accuracy
        promotion_passed = (
            evolved_score - static_score >= 0.2
            and by_condition[SkillCondition.EVOLVED].unsafe_action_rate == 0
            and by_condition[SkillCondition.EVOLVED].trace_coverage == 1
        )
        report = EvalReport(
            id=f"eval-{token_hex(5)}",
            created_at=datetime.now(UTC),
            scenario_count=len(SCENARIOS),
            results=results,
            promoted_skill_version="evolved-v1" if promotion_passed else None,
            notes=[
                "All conditions use the same frozen 12-scenario corpus and deterministic tools.",
                "The no-skill baseline contains four hard-coded routing signatures.",
                "Promotion requires >=20 percentage-point diagnosis gain, zero approval bypasses, and full trace coverage.",
            ],
        )
        if persist:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            self.output_path.write_text(
                json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        return report

    def latest(self) -> EvalReport | None:
        if not self.output_path.exists():
            return None
        return EvalReport.model_validate_json(self.output_path.read_text(encoding="utf-8"))
