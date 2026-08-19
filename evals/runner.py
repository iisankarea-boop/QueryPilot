import argparse
import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

EXPECTED_PATH = [
    "context_retrieved",
    "plan_created",
    "query_compiled",
    "query_prepared",
    "query_executed",
    "completed",
]


@dataclass(frozen=True, slots=True)
class Attempt:
    events: list[dict[str, Any]]
    latency_ms: float


def load_cases(path: Path) -> list[dict[str, Any]]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(cases, list) or not all(isinstance(case, dict) for case in cases):
        raise ValueError("case file must contain a cases list")
    ids = [case.get("id") for case in cases]
    if any(not isinstance(case_id, str) for case_id in ids) or len(ids) != len(set(ids)):
        raise ValueError("every evaluation case must have a unique string id")
    return cases


def run_case(client: httpx.Client, base_url: str, case: dict[str, Any]) -> Attempt:
    started = time.perf_counter()
    events: list[dict[str, Any]] = []
    with client.stream(
        "POST",
        f"{base_url.rstrip('/')}/api/v1/runs:stream",
        json={
            "source_id": case.get("source_id", "commerce"),
            "question": case["question"],
        },
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return Attempt(events=events, latency_ms=(time.perf_counter() - started) * 1_000)


def score_case(case: dict[str, Any], attempt: Attempt) -> dict[str, Any]:
    expected = case.get("expected", {})
    event_types = [event["type"] for event in attempt.events]
    terminal = attempt.events[-1] if attempt.events else {}
    generated = next(
        (event for event in attempt.events if event["type"] == "query_compiled"),
        None,
    )
    executed = next(
        (event for event in attempt.events if event["type"] == "query_executed"),
        None,
    )
    completed = next(
        (event for event in attempt.events if event["type"] == "completed"),
        None,
    )
    checks: dict[str, bool] = {}

    terminal_event = expected.get("terminal_event")
    if terminal_event:
        checks["terminal"] = terminal.get("type") == terminal_event
    if expected.get("full_trajectory"):
        checks["trajectory"] = event_types == EXPECTED_PATH
    if "row_count" in expected:
        checks["execution"] = bool(
            executed and executed.get("payload", {}).get("row_count") == expected["row_count"]
        )
    if required_query := expected.get("query_contains"):
        query = generated.get("payload", {}).get("query", "") if generated else ""
        checks["query"] = all(fragment.lower() in query.lower() for fragment in required_query)
    if answer_terms := expected.get("answer_contains"):
        answer = completed.get("payload", {}).get("answer", "") if completed else ""
        checks["answer"] = all(str(term).lower() in answer.lower() for term in answer_terms)
    if refusal_codes := expected.get("refusal_codes"):
        code = terminal.get("payload", {}).get("code")
        checks["refusal"] = terminal.get("type") == "failed" and code in refusal_codes

    score = sum(checks.values()) / len(checks) if checks else 0.0
    return {
        "id": case["id"],
        "category": case["category"],
        "score": round(score, 4),
        "passed": all(checks.values()),
        "checks": checks,
        "event_path": event_types,
        "artifacts": {
            "query": generated.get("payload", {}).get("query") if generated else None,
            "answer": completed.get("payload", {}).get("answer") if completed else None,
            "failure": terminal.get("payload") if terminal.get("type") == "failed" else None,
        },
        "step_latency_ms": _step_latencies(attempt.events),
        "latency_ms": round(attempt.latency_ms, 1),
    }


def _step_latencies(events: list[dict[str, Any]]) -> dict[str, float]:
    result: dict[str, float] = {}
    previous: datetime | None = None
    for event in events:
        occurred_at = event.get("occurred_at")
        if not occurred_at:
            continue
        current = datetime.fromisoformat(occurred_at)
        if previous is not None:
            result[event["type"]] = round((current - previous).total_seconds() * 1_000, 1)
        previous = current
    return result


def _refine_question(question: str, result: dict[str, Any]) -> str:
    failed = ", ".join(name for name, passed in result["checks"].items() if not passed)
    return (
        f"{question}\n"
        f"上一次评测未通过的维度是：{failed}。请仅使用目录中存在的字段和关系，"
        "生成只读 AQL，并严格回答原问题。"
    )


def evaluate(
    *,
    cases: list[dict[str, Any]],
    base_url: str,
    round_cap: int,
    timeout: float,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        for case in cases:
            working_case = dict(case)
            attempts: list[dict[str, Any]] = []
            for _ in range(round_cap):
                attempt = run_case(client, base_url, working_case)
                result = score_case(case, attempt)
                attempts.append(result)
                if result["passed"]:
                    break
                working_case["question"] = _refine_question(working_case["question"], result)
            best = max(attempts, key=lambda item: item["score"])
            results.append({**best, "attempts": len(attempts)})
            print(f"{case['id']}: score={best['score']:.2f} attempts={len(attempts)}")

    score = sum(result["score"] for result in results) / len(results) if results else 0.0
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "summary": {
            "score": round(score, 4),
            "passed": sum(result["passed"] for result in results),
            "total": len(results),
            "round_cap": round_cap,
        },
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run QueryPilot NL2AQL evaluations")
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path("evals/cases/custom/nl2aql.yaml"),
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=Path("evals/reports/current.json"))
    parser.add_argument("--round-cap", type=int, default=1, choices=(1, 2, 3))
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    cases = load_cases(args.cases)
    if not cases:
        raise SystemExit("evaluation suite must contain at least one case")
    if args.validate_only:
        print(f"validated {len(cases)} evaluation cases")
        return
    report = evaluate(
        cases=cases,
        base_url=args.base_url,
        round_cap=args.round_cap,
        timeout=args.timeout,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
