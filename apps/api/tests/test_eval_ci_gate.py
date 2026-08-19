from evals.ci_gate import regression_exceeds_threshold


def test_ci_gate_fails_at_five_percent_regression() -> None:
    assert not regression_exceeds_threshold(current_score=0.96, baseline_score=1.0)
    assert regression_exceeds_threshold(current_score=0.95, baseline_score=1.0)


def test_ci_gate_handles_an_empty_baseline() -> None:
    assert not regression_exceeds_threshold(current_score=0.0, baseline_score=0.0)
