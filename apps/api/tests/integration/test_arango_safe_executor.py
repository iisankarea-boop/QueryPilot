import os

import pytest
from querypilot.adapters.arango_database import ArangoAqlDatabase
from querypilot.application.safe_aql import SafeAqlExecutor
from querypilot.domain.models import AqlCandidate, QueryPolicy


@pytest.mark.skipif(
    not os.getenv("ARANGO_TEST_URL"),
    reason="ARANGO_TEST_URL is required for the real ArangoDB integration test",
)
@pytest.mark.asyncio
async def test_prepared_query_is_explained_and_executed_by_read_only_arango_user() -> None:
    database = ArangoAqlDatabase(
        url=os.environ["ARANGO_TEST_URL"],
        database=os.getenv("ARANGO_TEST_DATABASE", "commerce"),
        username=os.getenv("ARANGO_TEST_USERNAME", "querypilot_reader"),
        password=os.environ["ARANGO_TEST_PASSWORD"],
    )
    executor = SafeAqlExecutor(database)
    candidate = AqlCandidate(
        query="FOR order IN orders SORT order._key RETURN order._key",
        bind_vars={},
        referenced_collections={"orders"},
        referenced_graphs=set(),
        intent="document",
    )
    policy = QueryPolicy(
        allowed_collections={"orders"},
        allowed_graphs=set(),
        result_limit=2,
        max_traversal_depth=3,
        max_estimated_cost=10_000,
    )

    prepared = await executor.prepare(candidate, policy)
    result = await executor.execute(prepared)

    assert result.rows == ("order-0001", "order-0002")
