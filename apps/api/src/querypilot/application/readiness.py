import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

ReadinessProbe = Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    ready: bool
    dependencies: Mapping[str, str]


class ReadinessChecker:
    def __init__(
        self,
        probes: Mapping[str, ReadinessProbe],
        *,
        timeout_seconds: float,
    ) -> None:
        if not probes:
            raise ValueError("at least one readiness probe is required")
        if timeout_seconds <= 0:
            raise ValueError("readiness timeout must be positive")
        self._probes = dict(probes)
        self._timeout_seconds = timeout_seconds

    async def check(self) -> ReadinessReport:
        results = await asyncio.gather(
            *(self._run_probe(name, probe) for name, probe in self._probes.items())
        )
        dependencies = dict(results)
        return ReadinessReport(
            ready=all(value == "ready" for value in dependencies.values()),
            dependencies=dependencies,
        )

    async def _run_probe(
        self,
        name: str,
        probe: ReadinessProbe,
    ) -> tuple[str, str]:
        try:
            await asyncio.wait_for(probe(), timeout=self._timeout_seconds)
        except Exception:
            return name, "unavailable"
        return name, "ready"
