import asyncio

import pytest
from querypilot.application.readiness import ReadinessChecker


@pytest.mark.asyncio
async def test_readiness_reports_each_dependency_without_leaking_errors() -> None:
    async def healthy() -> None:
        return None

    async def broken() -> None:
        raise RuntimeError("postgresql://user:secret@database/internal")

    checker = ReadinessChecker(
        {"arangodb": healthy, "postgres": broken},
        timeout_seconds=0.1,
    )

    report = await checker.check()

    assert report.ready is False
    assert report.dependencies == {"arangodb": "ready", "postgres": "unavailable"}
    assert "secret" not in repr(report)


@pytest.mark.asyncio
async def test_readiness_times_out_slow_dependency() -> None:
    async def slow() -> None:
        await asyncio.sleep(1)

    checker = ReadinessChecker({"milvus": slow}, timeout_seconds=0.01)

    report = await checker.check()

    assert report.dependencies == {"milvus": "unavailable"}
