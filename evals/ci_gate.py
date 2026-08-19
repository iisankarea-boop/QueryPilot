import argparse
import json
from pathlib import Path
from typing import Any


def regression_exceeds_threshold(
    *,
    current_score: float,
    baseline_score: float,
    threshold: float = 0.05,
) -> bool:
    if baseline_score <= 0:
        return False
    regression = (baseline_score - current_score) / baseline_score
    return regression + 1e-12 >= threshold


def _score(report: dict[str, Any]) -> float:
    summary = report.get("summary")
    if not isinstance(summary, dict) or not isinstance(summary.get("score"), int | float):
        raise ValueError("evaluation report must contain numeric summary.score")
    return float(summary["score"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Fail on QueryPilot evaluation regression")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=0.05)
    args = parser.parse_args()

    current_score = _score(json.loads(args.report.read_text(encoding="utf-8")))
    baseline_score = _score(json.loads(args.baseline.read_text(encoding="utf-8")))
    regression = 0.0 if baseline_score <= 0 else (baseline_score - current_score) / baseline_score
    print(
        json.dumps(
            {
                "baseline_score": baseline_score,
                "current_score": current_score,
                "regression": regression,
                "threshold": args.threshold,
            }
        )
    )
    if regression_exceeds_threshold(
        current_score=current_score,
        baseline_score=baseline_score,
        threshold=args.threshold,
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
