from collections.abc import Mapping
from typing import Any, Protocol

from querypilot.domain.models import AqlCandidate, PreparedQuery, QueryPolicy, QueryRejected
from querypilot.domain.query import QueryResult


class AqlDatabase(Protocol):
    async def explain(self, query: str, bind_vars: dict[str, Any]) -> Mapping[str, Any]: ...
    async def execute(self, query: str, bind_vars: dict[str, Any]) -> QueryResult: ...


class SafeAqlExecutor:
    _WRITE_KEYWORDS = frozenset({"INSERT", "UPDATE", "REPLACE", "REMOVE", "UPSERT"})
    _RESERVED_BIND_VAR_PREFIX = "__qp_"

    def __init__(self, database: AqlDatabase) -> None:
        self._database = database

    async def prepare(
        self,
        candidate: AqlCandidate,
        policy: QueryPolicy,
    ) -> PreparedQuery:
        self.validate(candidate, policy)

        wrapped_query = (
            f"FOR __qp_row IN (\n{candidate.query.strip()}\n)\nLIMIT @__qp_limit\nRETURN __qp_row"
        )
        bind_vars = {**candidate.bind_vars, "__qp_limit": policy.result_limit}
        explanation = await self._database.explain(wrapped_query, bind_vars)
        actual_collections = {
            name
            for name in explanation.get("collections", ())
            if isinstance(name, str) and name
        }
        disallowed_collections = actual_collections - policy.allowed_collections
        if disallowed_collections:
            names = ", ".join(sorted(disallowed_collections))
            raise QueryRejected("collection_not_allowed", f"collection is not allowed: {names}")
        estimated_cost = float(explanation.get("estimatedCost", 0.0))
        if estimated_cost > policy.max_estimated_cost:
            raise QueryRejected(
                "estimated_cost_exceeded",
                f"query plan cost {estimated_cost:g} exceeds the configured limit "
                f"{policy.max_estimated_cost:g}; replace correlated collection scans "
                "with indexed lookups or catalog edge collections",
            )

        return PreparedQuery(
            query=wrapped_query,
            bind_vars=bind_vars,
            estimated_cost=estimated_cost,
            collections=frozenset(actual_collections),
        )

    def validate(self, candidate: AqlCandidate, policy: QueryPolicy) -> None:
        tokens = _identifier_tokens(candidate.query)
        if forbidden := self._WRITE_KEYWORDS.intersection(tokens):
            keyword = sorted(forbidden)[0]
            raise QueryRejected(
                "write_operation",
                f"write operations are not allowed ({keyword})",
            )

        if candidate.intent == "aggregate" and not _contains_aggregate(tokens):
            raise QueryRejected(
                "aggregate_required",
                "aggregate intent must use an AQL aggregate operation",
            )

        if any(name.startswith(self._RESERVED_BIND_VAR_PREFIX) for name in candidate.bind_vars):
            raise QueryRejected("reserved_bind_var", "reserved bind variable prefix is not allowed")

    async def execute(self, query: PreparedQuery) -> QueryResult:
        return await self._database.execute(query.query, query.bind_vars)


def _identifier_tokens(query: str) -> set[str]:
    tokens: set[str] = set()
    current: list[str] = []
    quote: str | None = None
    escaped = False

    for character in query:
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue

        if character in {'"', "'", "`"}:
            if current:
                tokens.add("".join(current).upper())
                current.clear()
            quote = character
        elif character.isalnum() or character == "_":
            current.append(character)
        elif current:
            tokens.add("".join(current).upper())
            current.clear()

    if current:
        tokens.add("".join(current).upper())
    return tokens


def _contains_aggregate(tokens: set[str]) -> bool:
    aggregate_tokens = {
        "AVERAGE",
        "AVG",
        "COLLECT",
        "COUNT",
        "COUNT_DISTINCT",
        "COUNT_UNIQUE",
        "LENGTH",
        "MAX",
        "MIN",
        "SUM",
    }
    return bool(tokens.intersection(aggregate_tokens))
