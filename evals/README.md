# QueryPilot evaluations

The suite measures the complete natural-language-to-AQL path against the deterministic
`commerce-v2` dataset. The initial live baseline is `0.8517`: 22 of 30 cases passed all
checks. Keeping the imperfect baseline is intentional; it makes future improvements and
regressions measurable.

## Layers

- `cases/benchmarks/` records the audited BFCL V4 adapter-contract subset. Its `1.0`
  score covers one compatibility case only and is not presented as a full leaderboard score.
- `cases/custom/nl2aql.yaml` contains 30 execution, trajectory, scope, and refusal cases.
- `cases/online/` defines replay canaries, alerts, and the required OTel GenAI attributes.
- `rubrics/answer_quality.yaml` is the grounded LLM-judge rubric. Expected facts and actual
  execution checks are supplied as CRITIC-style external grounding.

## Run

```bash
python -m evals.runner --validate-only
python -m evals.runner --round-cap 1 --output evals/reports/current.json
python -m evals.llm_judge --case-id paid_order_details
python -m evals.ci_gate --report evals/reports/current.json --baseline evals/baseline.json
```

`--round-cap 2` enables the bounded propose/judge/refine loop. The runner retains the best
attempt and records how many rounds were used. Do not use an unbounded refinement loop.

The CI gate fails when the mean score regresses by 5% or more relative to `baseline.json`.
After an intentional improvement, review the full report before promoting it to the baseline.

## Case mapping

| Control or learned rule | Cases or tests |
| --- | --- |
| Write keyword rejection | `reject_insert`, `reject_update`, `reject_remove`, `reject_replace`, `reject_upsert` |
| Prompt injection cannot authorize writes | `prompt_injection_write` |
| Collection and graph allowlists | `unknown_collection`, `unknown_graph`, `dynamic_collection` |
| Secrets are never query output | `secret_exfiltration` |
| Server-owned result limit | `paid_order_details`, `cancelled_orders`, `test_safe_aql_executor.py` |
| Reserved bind variables | `test_safe_aql_executor.py` |
| Resource declarations | `test_safe_aql_executor.py` |
| Estimated-cost ceiling | `test_safe_aql_executor.py` |
| Real database execution | aggregate, traversal, time-range, and anti-join custom cases |
| Gold trajectory | every safe case with `full_trajectory: true` |
| Grounded factual judge | `paid_order_details` plus `answer_quality.yaml` |

## Current findings

The initial failures cluster around metric intent selection, exact top-N limiting, and explicit
secret-exfiltration refusal. These are candidate improvements; they are not removed from the
suite to inflate the score.

What to read next: Lesson 24 for observability, Lesson 26 for failure-mode expansion, and
Lesson 23 for exporting the listed OTel GenAI attributes to a production backend.
