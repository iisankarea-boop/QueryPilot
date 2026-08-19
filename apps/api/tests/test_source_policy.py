import pytest
from querypilot.application.source_policy import SourceHostPolicy


def test_development_policy_allows_hosts_when_allowlist_is_empty() -> None:
    policy = SourceHostPolicy.from_csv("", require_allowlist=False)

    policy.enforce("http://host.docker.internal:8529")


def test_production_policy_requires_exact_allowed_host() -> None:
    policy = SourceHostPolicy.from_csv(
        "arangodb, db.example.com",
        require_allowlist=True,
    )

    policy.enforce("http://arangodb:8529")
    policy.enforce("https://DB.EXAMPLE.COM:8529")
    with pytest.raises(ValueError, match="allowlist"):
        policy.enforce("http://169.254.169.254/latest/meta-data")
    with pytest.raises(ValueError, match="allowlist"):
        policy.enforce("http://sub.db.example.com:8529")


def test_production_policy_with_empty_allowlist_rejects_onboarding() -> None:
    policy = SourceHostPolicy.from_csv("", require_allowlist=True)

    with pytest.raises(ValueError, match="allowlist"):
        policy.enforce("http://arangodb:8529")
