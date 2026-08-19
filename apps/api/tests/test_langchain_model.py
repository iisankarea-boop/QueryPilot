import json

import httpx
import pytest
from pydantic import SecretStr
from querypilot.adapters.langchain_model import LangChainPlanningModel
from querypilot.domain.catalog import CatalogEvidence, ContextPack
from querypilot.domain.models import AqlCandidate
from querypilot.domain.query import PlanPrompt, QueryResult, SummaryPrompt
from querypilot.domain.query_ir import (
    FieldRef,
    GroupSpec,
    MetricSpec,
    QuerySource,
    SortSpec,
    StructuredQueryPlan,
)


def _plan_arguments() -> dict[str, object]:
    return {
        "intent": "aggregate",
        "root": {"alias": "review", "collection": "reviews"},
        "joins": [],
        "filters": [
            {
                "field": {"source": "review", "field": "score"},
                "transform": "none",
                "operator": "is_not_null",
                "value": None,
            }
        ],
        "selections": [],
        "groups": [
            {
                "field": {"source": "review", "field": "score"},
                "transform": "none",
                "output": "score",
            }
        ],
        "metrics": [
            {"operation": "count_rows", "field": None, "output": "review_count"}
        ],
        "ratios": [],
        "sort": [{"output": "score", "direction": "desc"}],
        "limit": 100,
        "assumptions": [],
        "requirements": ["count reviews by score"],
    }


@pytest.mark.asyncio
async def test_planner_uses_only_structured_query_plan_function_calling() -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        tool_names = {tool["function"]["name"] for tool in payload["tools"]}
        assert tool_names == {"StructuredQueryPlan"}
        assert payload.get("enable_thinking") is False
        return _tool_response("StructuredQueryPlan", _plan_arguments())

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle),
        base_url="https://model.test",
    ) as client:
        model = LangChainPlanningModel.for_dashscope(
            model="qwen-test",
            api_key=SecretStr("test-key"),
            base_url="https://model.test/v1",
            timeout=10,
            http_async_client=client,
        )
        plan = await model.plan(
            PlanPrompt(
                question="Count reviews by score",
                context=ContextPack(
                    release_id="olist-v1",
                    evidence=(
                        CatalogEvidence(
                            id="olist.reviews.score",
                            kind="field",
                            entity="reviews.score",
                            content="type=integer",
                        ),
                    ),
                ),
            )
        )

    assert plan.root.collection == "reviews"
    assert plan.groups[0].field == FieldRef(source="review", field="score")
    assert plan.metrics[0].operation == "count_rows"


@pytest.mark.asyncio
async def test_repair_feedback_is_sent_with_rejected_structured_plan() -> None:
    invalid_plan = StructuredQueryPlan(
        intent="aggregate",
        root=QuerySource(alias="order", collection="orders"),
        groups=(
            GroupSpec(
                field=FieldRef(source="order", field="rating"),
                output="score",
            ),
        ),
        metrics=(MetricSpec(operation="count_rows", output="review_count"),),
        sort=(SortSpec(output="score", direction="desc"),),
        requirements=("count reviews by score",),
    )

    async def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        human_message = payload["messages"][-1]["content"]
        assert "query_plan_invalid" in human_message
        assert "orders.rating" in human_message
        assert "rejected_plan" in human_message
        return _tool_response("StructuredQueryPlan", _plan_arguments())

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle),
        base_url="https://model.test",
    ) as client:
        model = LangChainPlanningModel.for_dashscope(
            model="qwen-test",
            api_key=SecretStr("test-key"),
            base_url="https://model.test/v1",
            timeout=10,
            http_async_client=client,
        )
        repaired = await model.plan(
            PlanPrompt(
                question="Count reviews by score",
                context=ContextPack(release_id="olist-v1", evidence=()),
                validation_feedback=(
                    "query_plan_invalid: field is not present in schema: orders.rating",
                ),
                rejected_plan=invalid_plan,
            )
        )

    assert repaired.root.collection == "reviews"


@pytest.mark.asyncio
async def test_planner_retries_a_structurally_invalid_tool_result() -> None:
    calls = 0

    async def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        arguments = _plan_arguments()
        if calls == 1:
            arguments["root"] = {"alias": "invalid-alias", "collection": "reviews"}
        else:
            payload = json.loads(request.content)
            assert "structure_validation_feedback" in payload["messages"][-1]["content"]
        return _tool_response("StructuredQueryPlan", arguments)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle),
        base_url="https://model.test",
    ) as client:
        model = LangChainPlanningModel.for_dashscope(
            model="qwen-test",
            api_key=SecretStr("test-key"),
            base_url="https://model.test/v1",
            timeout=10,
            http_async_client=client,
        )
        plan = await model.plan(
            PlanPrompt(
                question="Count reviews by score",
                context=ContextPack(release_id="olist-v1", evidence=()),
            )
        )

    assert calls == 2
    assert plan.root.alias == "review"


@pytest.mark.asyncio
async def test_summary_is_a_separate_small_structured_call() -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        tool_names = {tool["function"]["name"] for tool in payload["tools"]}
        assert tool_names == {"AnswerSummary"}
        return _tool_response("AnswerSummary", {"text": "5分有3条评论。"})

    plan = StructuredQueryPlan.model_validate(_plan_arguments())
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle),
        base_url="https://model.test",
    ) as client:
        model = LangChainPlanningModel.for_dashscope(
            model="qwen-test",
            api_key=SecretStr("test-key"),
            base_url="https://model.test/v1",
            timeout=10,
            http_async_client=client,
        )
        summary = await model.summarize(
            SummaryPrompt(
                question="Count reviews by score",
                plan=plan,
                candidate=AqlCandidate(
                    query="FOR review IN reviews RETURN review.score",
                    bind_vars={},
                    referenced_collections={"reviews"},
                    referenced_graphs=set(),
                    intent="aggregate",
                ),
                result=QueryResult(rows=({"score": 5, "review_count": 3},)),
            )
        )

    assert summary.text == "5分有3条评论。"


def _tool_response(name: str, arguments: dict[str, object]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1,
            "model": "qwen-test",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-test",
                                "type": "function",
                                "function": {
                                    "name": name,
                                    "arguments": json.dumps(arguments),
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        },
    )
