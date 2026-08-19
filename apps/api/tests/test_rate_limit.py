from querypilot.transport.rate_limit import SlidingWindowRateLimiter, client_identifier


def test_sliding_window_rejects_only_requests_inside_window() -> None:
    limiter = SlidingWindowRateLimiter(limit=2, window_seconds=10)

    assert limiter.consume("client-a", now=100.0) is None
    assert limiter.consume("client-a", now=101.0) is None
    assert limiter.consume("client-a", now=102.0) == 8
    assert limiter.consume("client-a", now=110.0) is None


def test_rate_limit_isolated_by_client() -> None:
    limiter = SlidingWindowRateLimiter(limit=1, window_seconds=60)

    assert limiter.consume("client-a", now=100.0) is None
    assert limiter.consume("client-b", now=100.0) is None
    assert limiter.consume("client-a", now=101.0) == 59


def test_forwarded_client_is_used_only_for_trusted_proxy() -> None:
    forwarded = "203.0.113.8, 172.20.0.4"

    assert client_identifier("172.20.0.4", forwarded, trust_proxy_headers=True) == "203.0.113.8"
    assert client_identifier("172.20.0.4", forwarded, trust_proxy_headers=False) == "172.20.0.4"
    assert client_identifier(None, None, trust_proxy_headers=True) == "unknown"
