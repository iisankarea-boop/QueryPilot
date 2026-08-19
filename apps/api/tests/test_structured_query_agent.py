from collections.abc import AsyncIterator
from typing import Any

import pytest
from querypilot.application.aql_compiler import AqlCompiler
from querypilot.application.query_agent import QueryAgent
from querypilot.application.safe_aql import SafeAqlExecutor
from querypilot.domain.catalog import (
    CatalogEntry,
    CatalogEvidence,
    CatalogRelease,
    ContextPack,
    ContextRequest,
)
from querypilot.domain.models import QueryPolicy
from querypilot.domain.query import (
    AnswerSummary,
    AskCommand,
    PlanPrompt,
    QueryResult,
    SummaryPrompt,
)
from querypilot.domain.query_ir import (
    FieldRef,
    FilterSpec,
    GroupSpec,
    MetricSpec,
    QuerySource,
    SortSpec,
    StructuredQueryPlan,
)
from querypilot.domain.source import (
    DiscoveredCollection,
    DiscoveredField,
    SchemaSnapshot,
)


class ReviewCatalog:
    async def context_for(self, request: ContextRequest) -> ContextPack:
        assert request.source_id == "olist"
        return ContextPack(
            release_id="olist-v1",
            evidence=(
                CatalogEvidence(
                    id="olist.reviews.score",
                    kind="field",
                    entity="reviews.score",
                    content="type=integer",
                ),
            ),
        )


class RepairingStructuredModel:
    def __init__(self) -> None:
        self.plan_calls = 0

    async def plan(self, prompt: PlanPrompt) -> StructuredQueryPlan:
        self.plan_calls += 1
        if self.plan_calls == 1:
            source = QuerySource(alias="order", collection="orders")
            field = FieldRef(source="order", field="rating")
        else:
            assert prompt.validation_feedback == (
                "query_plan_invalid: field is not present in schema: orders.rating",
            )
            assert prompt.rejected_plan is not None
            source = QuerySource(alias="review", collection="reviews")
            field = FieldRef(source="review", field="score")
        return StructuredQueryPlan(
            intent="aggregate",
            root=source,
            filters=(FilterSpec(field=field, operator="is_not_null"),),
            groups=(GroupSpec(field=field, transform="none", output="score"),),
            metrics=(MetricSpec(operation="count_rows", output="review_count"),),
            sort=(SortSpec(output="score", direction="desc"),),
            requirements=("count reviews by score",),
        )

    async def summarize(self, prompt: SummaryPrompt) -> AnswerSummary:
        assert prompt.result.rows == (
            {"score": 5, "review_count": 3},
            {"score": 4, "review_count": 1},
        )
        return AnswerSummary(text="5分有3条评论，4分有1条评论。")


class ReviewDatabase:
    async def explain(self, query: str, bind_vars: dict[str, Any]) -> dict[str, Any]:
        assert "reviews" in query
        assert "rating" not in query
        return {"estimatedCost": 3.0, "collections": ("reviews",)}

    async def execute(self, query: str, bind_vars: dict[str, Any]) -> QueryResult:
        return QueryResult(
            rows=(
                {"score": 5, "review_count": 3},
                {"score": 4, "review_count": 1},
            )
        )


@pytest.mark.asyncio
async def test_agent_replans_invalid_ir_and_never_asks_model_for_aql() -> None:
    schema = SchemaSnapshot(
        source_id="olist",
        database="olist",
        collections=(
            DiscoveredCollection(
                name="orders",
                kind="document",
                fields=(
                    DiscoveredField(name="_id", inferred_type="string"),
                    DiscoveredField(name="purchased_at", inferred_type="datetime"),
                ),
                sampled_documents=10,
            ),
            DiscoveredCollection(
                name="reviews",
                kind="document",
                fields=(
                    DiscoveredField(name="_id", inferred_type="string"),
                    DiscoveredField(name="score", inferred_type="integer"),
                ),
                sampled_documents=10,
            ),
        ),
        graphs=(),
    )
    release = CatalogRelease(
        release_id="olist-v1",
        entries=(
            CatalogEntry(
                id="olist.orders.purchased_at",
                source_id="olist",
                kind="field",
                entity="orders.purchased_at",
                content="type=datetime",
            ),
            CatalogEntry(
                id="olist.reviews.score",
                source_id="olist",
                kind="field",
                entity="reviews.score",
                content="type=integer",
            ),
        ),
    )
    model = RepairingStructuredModel()
    agent = QueryAgent(
        catalog=ReviewCatalog(),
        model=model,
        compiler=AqlCompiler(),
        executor=SafeAqlExecutor(ReviewDatabase()),
        policy=QueryPolicy(
            allowed_collections={"orders", "reviews"},
            allowed_graphs=set(),
            result_limit=100,
            max_traversal_depth=3,
            max_estimated_cost=100,
        ),
        schema=schema,
        catalog_release=release,
    )
    command = AskCommand(
        run_id="run-review-count",
        thread_id="thread-review-count",
        source_id="olist",
        question="各评分等级分别有多少条评论？按照评分从高到低排列",
    )

    events = [event async for event in _stream(agent, command)]

    assert model.plan_calls == 2
    assert [event.type for event in events] == [
        "context_retrieved",
        "plan_created",
        "query_compiled",
        "query_prepared",
        "query_executed",
        "completed",
    ]
    assert "reviews" in events[2].payload["query"]
    assert "orders.rating" not in events[2].payload["query"]
    assert events[-1].payload["answer"] == "5分有3条评论，4分有1条评论。"


async def _stream(agent: QueryAgent, command: AskCommand) -> AsyncIterator[Any]:
    async for event in agent.stream(command):
        yield event
