from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .services.evaluation import SkillEvaluator


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the frozen EvoSRE skill benchmark")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "evals" / "latest-report.json",
    )
    args = parser.parse_args()
    report = asyncio.run(SkillEvaluator(args.output.resolve()).run())
    print(f"report={args.output.resolve()}")
    for result in report.results:
        print(
            f"{result.condition.value}: diagnosis={result.correct}/{result.total} "
            f"action={result.action_accuracy:.2%} trace={result.trace_coverage:.2%} "
            f"unsafe={result.unsafe_action_rate:.2%}"
        )
    print(f"promoted={report.promoted_skill_version or 'none'}")


if __name__ == "__main__":
    main()
