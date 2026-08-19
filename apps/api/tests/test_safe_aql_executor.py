from typing import Any

import pytest
from querypilot.application.safe_aql import SafeAqlExecutor
from querypilot.domain.models import AqlCandidate, PreparedQuery, QueryPolicy, QueryRejected


class AcceptingExplainer:
    async def explain(self, query: str, bind_vars: dict[str, Any]) -> dict[str, Any]:
        collections = ["users"] if "FOR user IN users" in query else ["orders"]
        return {"estimatedCost": 2.0, "collections": collections}


@pytest.mark.asyncio
async def test_safe_query_is_prepared_with_a_server_controlled_limit() -> None:
    executor = SafeAqlExecutor(AcceptingExplainer())
    candidate = AqlCandidate(
        query="FOR order IN orders FILTER order.status == @status RETURN order",
        bind_vars={"status": "paid"},
        referenced_collections={"orders"},
        referenced_graphs=set(),
        intent="document",
    )
    policy = QueryPolicy(
        allowed_collections={"orders"},
        allowed_graphs=set(),
        result_limit=50,
        max_traversal_depth=3,
        max_estimated_cost=100.0,
    )

    prepared = await executor.prepare(candidate, policy)

    assert isinstance(prepared, PreparedQuery)
    assert prepared.bind_vars == {"status": "paid", "__qp_limit": 50}
    assert prepared.query == (
        "FOR __qp_row IN (\n"
        "FOR order IN orders FILTER order.status == @status RETURN order\n"
        ")\n"
        "LIMIT @__qp_limit\n"
        "RETURN __qp_row"
    )


@pytest.mark.asyncio
async def test_write_query_is_rejected_before_explain() -> None:
    executor = SafeAqlExecutor(AcceptingExplainer())
    candidate = AqlCandidate(
        query='INSERT {name: "unsafe"} INTO users RETURN NEW',
        bind_vars={},
        referenced_collections={"users"},
        referenced_graphs=set(),
        intent="document",
    )
    policy = QueryPolicy(
        allowed_collections={"users"},
        allowed_graphs=set(),
        result_limit=100,
        max_traversal_depth=3,
        max_estimated_cost=100.0,
    )

    with pytest.raises(QueryRejected, match="write operations are not allowed") as error:
        await executor.prepare(candidate, policy)

    assert error.value.code == "write_operation"


@pytest.mark.asyncio
async def test_database_plan_collections_are_checked_against_policy() -> None:
    executor = SafeAqlExecutor(AcceptingExplainer())
    candidate = AqlCandidate(
        query="FOR user IN users RETURN user",
        bind_vars={},
        referenced_collections={"orders"},
        referenced_graphs=set(),
        intent="document",
    )
    policy = QueryPolicy(
        allowed_collections={"orders"},
        allowed_graphs=set(),
        result_limit=100,
        max_traversal_depth=3,
        max_estimated_cost=100.0,
    )

    with pytest.raises(QueryRejected, match="collection is not allowed") as error:
        await executor.prepare(candidate, policy)

    assert error.value.code == "collection_not_allowed"


@pytest.mark.asyncio
async def test_aggregate_intent_cannot_return_one_row_per_document() -> None:
    executor = SafeAqlExecutor(AcceptingExplainer())
    candidate = AqlCandidate(
        query="FOR order IN orders RETURN 1",
        bind_vars={},
        referenced_collections={"orders"},
        referenced_graphs=set(),
        intent="aggregate",
    )
    policy = QueryPolicy(
        allowed_collections={"orders"},
        allowed_graphs=set(),
        result_limit=100,
        max_traversal_depth=3,
        max_estimated_cost=100.0,
    )

    with pytest.raises(QueryRejected, match="must use an AQL aggregate") as error:
        await executor.prepare(candidate, policy)

    assert error.value.code == "aggregate_required"
