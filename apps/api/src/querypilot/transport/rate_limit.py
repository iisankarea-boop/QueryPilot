import ipaddress
import math
import time
from collections import defaultdict, deque


class SlidingWindowRateLimiter:
    def __init__(self, *, limit: int, window_seconds: int) -> None:
        if limit < 1 or window_seconds < 1:
            raise ValueError("rate limit and window must be positive")
        self._limit = limit
        self._window_seconds = window_seconds
        self._requests: dict[str, deque[float]] = defaultdict(deque)

    def consume(self, key: str, *, now: float | None = None) -> int | None:
        current = time.monotonic() if now is None else now
        cutoff = current - self._window_seconds
        requests = self._requests[key]
        while requests and requests[0] <= cutoff:
            requests.popleft()
        if len(requests) >= self._limit:
            return max(1, math.ceil(requests[0] + self._window_seconds - current))
        requests.append(current)
        return None


def client_identifier(
    direct_host: str | None,
    forwarded_for: str | None,
    *,
    trust_proxy_headers: bool,
) -> str:
    if trust_proxy_headers and forwarded_for:
        candidate = forwarded_for.split(",", maxsplit=1)[0].strip()
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            pass
    return direct_host or "unknown"
