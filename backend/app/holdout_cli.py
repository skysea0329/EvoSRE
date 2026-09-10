from __future__ import annotations

import argparse
import json
from pathlib import Path

from .models import SkillCondition
from .services.holdout_evaluation import HoldoutEvaluator
from .services.skill_registry import SKILLS_ROOT, SkillRegistry


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the leakage-resistant EvoSRE holdout gate")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "evals" / "latest-holdout-report.json",
    )
    args = parser.parse_args()
    registry = SkillRegistry()
    baseline = registry.snapshot_from_payload(
        SkillCondition.EVOLVED,
        json.loads((SKILLS_ROOT / "evolved" / "v1" / "signatures.json").read_text(encoding="utf-8")),
    )
    candidate_payload, _ = registry.build_candidate_payload(baseline)
    candidate = registry.snapshot_from_payload(SkillCondition.EVOLVED, candidate_payload)
    evaluator = HoldoutEvaluator(repeated_runs=3)
    report = evaluator.compare(baseline, candidate)
    evaluator.persist(report, args.output.resolve())
    print(f"report={args.output.resolve()}")
    print(
        f"known={report.baseline.known_accuracy:.2%}->{report.candidate.known_accuracy:.2%} "
        f"ood={report.candidate.ood_rejection_rate:.2%} "
        f"injection={report.candidate.injection_resistance_rate:.2%} "
        f"policy={report.candidate.policy_attack_blocked}/{report.candidate.policy_attack_total} "
        f"leakage_max={report.max_train_holdout_similarity:.2%} repeats={report.repeated_runs}"
    )
    print(f"promotion_passed={report.promotion_passed}")


if __name__ == "__main__":
    main()
