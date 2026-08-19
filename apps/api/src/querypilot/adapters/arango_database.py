import asyncio
from collections.abc import Mapping
from typing import Any

from arango.client import ArangoClient
from arango.cursor import Cursor
from arango.exceptions import AQLQueryExecuteError, AQLQueryExplainError

from querypilot.domain.models import QueryRejected
from querypilot.domain.query import QueryResult


class ArangoAqlDatabase:
    def __init__(
        self,
        url: str,
        database: str,
        username: str,
        password: str,
        *,
        timeout_seconds: float = 8.0,
        memory_limit_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        client = ArangoClient(hosts=url, request_timeout=timeout_seconds + 2)
        self._database = client.db(database, username=username, password=password)
        self._timeout_seconds = timeout_seconds
        self._memory_limit_bytes = memory_limit_bytes

    async def explain(self, query: str, bind_vars: dict[str, Any]) -> Mapping[str, Any]:
        return await asyncio.to_thread(self._explain_sync, query, bind_vars)

    async def execute(self, query: str, bind_vars: dict[str, Any]) -> QueryResult:
        return await asyncio.to_thread(self._execute_sync, query, bind_vars)

    def _explain_sync(self, query: str, bind_vars: dict[str, Any]) -> Mapping[str, Any]:
        try:
            explanation = self._database.aql.explain(query, bind_vars=bind_vars)
        except AQLQueryExplainError as error:
            raise _query_rejected("aql_explain_rejected", error) from error
        if not isinstance(explanation, dict):
            raise RuntimeError("ArangoDB returned an unexpected EXPLAIN response")
        return {
            "estimatedCost": float(explanation.get("estimatedCost", 0.0)),
            "collections": tuple(
                collection.get("name", "")
                for collection in explanation.get("collections", ())
                if isinstance(collection, dict)
            ),
        }

    def _execute_sync(self, query: str, bind_vars: dict[str, Any]) -> QueryResult:
        try:
            cursor = self._database.aql.execute(
                query,
                bind_vars=bind_vars,
                batch_size=200,
                memory_limit=self._memory_limit_bytes,
                max_runtime=max(1, int(self._timeout_seconds)),  # type: ignore[arg-type]
                fail_on_warning=True,
            )
        except AQLQueryExecuteError as error:
            raise _query_rejected("aql_execution_rejected", error) from error
        if not isinstance(cursor, Cursor):
            raise RuntimeError("ArangoDB returned an unexpected cursor type")
        return QueryResult(rows=tuple(cursor))


def _query_rejected(code: str, error: AQLQueryExplainError | AQLQueryExecuteError) -> QueryRejected:
    message = " ".join((error.error_message or "ArangoDB rejected the AQL query").split())
    return QueryRejected(
        code,
        f"ArangoDB rejected AQL ({error.error_code}): {message[:500]}",
    )
