from collections.abc import AsyncIterator, Mapping
from typing import Any, Protocol, TypedDict, cast

from langgraph.graph import END, START, StateGraph

from querypilot.application.aql_compiler import AqlCompiler
from querypilot.application.safe_aql import SafeAqlExecutor
from querypilot.domain.catalog import CatalogEvidence, CatalogRelease, ContextPack, ContextRequest
from querypilot.domain.models import AqlCandidate, PreparedQuery, QueryPolicy, QueryRejected
from querypilot.domain.query import (
    AnswerSummary,
    AskCommand,
    PlanPrompt,
    QueryResult,
    RunEvent,
    SummaryPrompt,
)
from querypilot.domain.query_ir import StructuredQueryPlan
from querypilot.domain.source import SchemaSnapshot


class CatalogLookup(Protocol):
    async def context_for(self, request: ContextRequest) -> ContextPack: ...


class PlanningModel(Protocol):
    async def plan(self, prompt: PlanPrompt) -> StructuredQueryPlan: ...
    async def summarize(self, prompt: SummaryPrompt) -> AnswerSummary: ...


class QueryState(TypedDict, total=False):
    command: AskCommand
    context: ContextPack
    plan: StructuredQueryPlan
    candidate: AqlCandidate
    prepared: PreparedQuery
    result: QueryResult
    summary: AnswerSummary


class QueryAgent:
    _MAX_PLAN_REPAIRS = 4
    _EVENT_TYPES = {
        "retrieve_context": "context_retrieved",
        "create_plan": "plan_created",
        "compile_query": "query_compiled",
        "prepare_query": "query_prepared",
        "execute_query": "query_executed",
        "summarize": "completed",
    }

    def __init__(
        self,
        *,
        catalog: CatalogLookup,
        model: PlanningModel,
        compiler: AqlCompiler,
        executor: SafeAqlExecutor,
        policy: QueryPolicy,
        schema: SchemaSnapshot,
        catalog_release: CatalogRelease,
    ) -> None:
        self._catalog = catalog
        self._model = model
        self._compiler = compiler
        self._executor = executor
        self._policy = policy
        self._schema = schema
        self._catalog_release = catalog_release
        self._graph = self._build_graph()

    async def stream(self, command: AskCommand) -> AsyncIterator[RunEvent]:
        sequence = 0
        initial: QueryState = {"command": command}
        current_state = cast(QueryState, dict(initial))
        async for update in self._graph.astream(initial, stream_mode="updates"):
            for node_name, values in cast(dict[str, QueryState], update).items():
                current_state.update(values)
                sequence += 1
                yield RunEvent.now(
                    run_id=command.run_id,
                    seq=sequence,
                    type=self._EVENT_TYPES[node_name],
                    payload=_event_payload(node_name, current_state),
                )

    def _build_graph(self) -> Any:
        builder = StateGraph(QueryState)
        builder.add_node("retrieve_context", self._retrieve_context)
        builder.add_node("create_plan", self._create_plan)
        builder.add_node("compile_query", self._compile_query)
        builder.add_node("prepare_query", self._prepare_query)
        builder.add_node("execute_query", self._execute_query)
        builder.add_node("summarize", self._summarize)
        builder.add_edge(START, "retrieve_context")
        builder.add_edge("retrieve_context", "create_plan")
        builder.add_edge("create_plan", "compile_query")
        builder.add_edge("compile_query", "prepare_query")
        builder.add_edge("prepare_query", "execute_query")
        builder.add_edge("execute_query", "summarize")
        builder.add_edge("summarize", END)
        return builder.compile()

    async def _retrieve_context(self, state: QueryState) -> QueryState:
        command = state["command"]
        context = await self._catalog.context_for(
            ContextRequest(
                question=command.question,
                source_id=command.source_id,
                limit=30,
            )
        )
        return {"context": _planning_context(context, self._catalog_release)}

    async def _create_plan(self, state: QueryState) -> QueryState:
        command = state["command"]
        plan = await self._model.plan(
            PlanPrompt(question=command.question, context=state["context"])
        )
        return {"plan": plan}

    async def _compile_query(self, state: QueryState) -> QueryState:
        command = state["command"]
        plan = state["plan"]
        feedback: tuple[str, ...] = ()
        for attempt in range(self._MAX_PLAN_REPAIRS + 1):
            try:
                candidate = self._compiler.compile(plan, self._schema, self._policy)
            except QueryRejected as error:
                if error.code != "query_plan_invalid" or attempt == self._MAX_PLAN_REPAIRS:
                    raise
                feedback = (*feedback, f"{error.code}: {error}")
                rejected_plan = plan
                plan = await self._model.plan(
                    PlanPrompt(
                        question=command.question,
                        context=state["context"],
                        validation_feedback=feedback,
                        rejected_plan=rejected_plan,
                    )
                )
            else:
                return {"plan": plan, "candidate": candidate}
        raise RuntimeError("query plan repair loop exhausted unexpectedly")

    async def _prepare_query(self, state: QueryState) -> QueryState:
        prepared = await self._executor.prepare(state["candidate"], self._policy)
        return {"prepared": prepared}

    async def _execute_query(self, state: QueryState) -> QueryState:
        result = await self._executor.execute(state["prepared"])
        _validate_result_shape(state["plan"], result)
        return {"result": result}

    async def _summarize(self, state: QueryState) -> QueryState:
        command = state["command"]
        summary = await self._model.summarize(
            SummaryPrompt(
                question=command.question,
                plan=state["plan"],
                candidate=state["candidate"],
                result=state["result"],
            )
        )
        return {"summary": summary}


def _event_payload(node_name: str, state: QueryState) -> dict[str, Any]:
    if node_name == "retrieve_context":
        context = state["context"]
        return {
            "release_id": context.release_id,
            "evidence_ids": [evidence.id for evidence in context.evidence],
        }
    if node_name == "create_plan":
        return _plan_payload(state["plan"])
    if node_name == "compile_query":
        candidate = state["candidate"]
        return {
            "query": candidate.query,
            "bind_vars": candidate.bind_vars,
            "plan": _plan_payload(state["plan"]),
        }
    if node_name == "prepare_query":
        prepared = state["prepared"]
        return {"query": prepared.query, "estimated_cost": prepared.estimated_cost}
    if node_name == "execute_query":
        result = state["result"]
        return {"row_count": len(result.rows), "rows": list(result.rows)}
    if node_name == "summarize":
        return {
            "answer": state["summary"].text,
            "row_count": len(state["result"].rows),
        }
    raise ValueError(f"unknown query graph node: {node_name}")


def _plan_payload(plan: StructuredQueryPlan) -> dict[str, Any]:
    return {
        "intent": plan.intent,
        "root_collection": plan.root.collection,
        "joins": [join.edge_collection for join in plan.joins],
        "outputs": _expected_outputs(plan),
    }


def _planning_context(
    context: ContextPack,
    release: CatalogRelease,
) -> ContextPack:
    evidence = [item for item in context.evidence if item.kind != "example"]
    seen_ids = {item.id for item in evidence}
    for entry in release.entries:
        if entry.id in seen_ids or entry.kind == "example":
            continue
        evidence.append(
            CatalogEvidence(
                id=entry.id,
                kind=entry.kind,
                entity=entry.entity,
                content=entry.content,
            )
        )
        seen_ids.add(entry.id)
        if len(evidence) == 250:
            break
    return ContextPack(
        release_id=context.release_id or release.release_id,
        evidence=tuple(evidence),
    )


def _validate_result_shape(plan: StructuredQueryPlan, result: QueryResult) -> None:
    expected = set(_expected_outputs(plan))
    for row_index, row in enumerate(result.rows):
        if not isinstance(row, Mapping):
            raise QueryRejected(
                "result_shape_invalid",
                f"compiled query returned a non-object row at index {row_index}",
            )
        missing = expected - set(row)
        if missing:
            names = ", ".join(sorted(missing))
            raise QueryRejected(
                "result_shape_invalid",
                f"compiled query result is missing outputs: {names}",
            )


def _expected_outputs(plan: StructuredQueryPlan) -> list[str]:
    if plan.intent == "detail":
        return [selection.output for selection in plan.selections]
    outputs = [group.output for group in plan.groups]
    outputs.extend(metric.output for metric in plan.metrics)
    outputs.extend(ratio.output for ratio in plan.ratios)
    return outputs
