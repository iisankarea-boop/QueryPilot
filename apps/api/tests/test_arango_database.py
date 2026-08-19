from types import SimpleNamespace
from typing import Any

import pytest
import querypilot.adapters.arango_database as arango_adapter
from querypilot.adapters.arango_database import ArangoAqlDatabase
from querypilot.domain.models import QueryRejected


class FakeArangoQueryError(Exception):
    error_code = 1540
    error_message = "usage of unknown function 'SUBSTR()' (while parsing)"


class RejectingAql:
    def explain(self, query: str, bind_vars: dict[str, Any]) -> None:
        raise FakeArangoQueryError

    def execute(self, query: str, **kwargs: Any) -> None:
        raise FakeArangoQueryError


class AcceptingAql:
    def explain(self, query: str, bind_vars: dict[str, Any]) -> dict[str, Any]:
        return {
            "estimatedCost": 12345.5,
            "collections": [{"name": "orders"}, {"name": "payments"}],
        }


def _database() -> ArangoAqlDatabase:
    database = object.__new__(ArangoAqlDatabase)
    database._database = SimpleNamespace(aql=RejectingAql())
    database._timeout_seconds = 8.0
    database._memory_limit_bytes = 64 * 1024 * 1024
    return database


def test_explain_error_becomes_repairable_query_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(arango_adapter, "AQLQueryExplainError", FakeArangoQueryError)

    with pytest.raises(QueryRejected) as captured:
        _database()._explain_sync("RETURN SUBSTR('x', 0, 1)", {})

    assert captured.value.code == "aql_explain_rejected"
    assert "unknown function" in str(captured.value)


def test_execute_error_becomes_specific_query_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(arango_adapter, "AQLQueryExecuteError", FakeArangoQueryError)

    with pytest.raises(QueryRejected) as captured:
        _database()._execute_sync("RETURN 1 / 0", {})

    assert captured.value.code == "aql_execution_rejected"
    assert "unknown function" in str(captured.value)


def test_explain_reads_top_level_cost_and_collections() -> None:
    database = object.__new__(ArangoAqlDatabase)
    database._database = SimpleNamespace(aql=AcceptingAql())

    explanation = database._explain_sync("FOR order IN orders RETURN order", {})

    assert explanation == {
        "estimatedCost": 12345.5,
        "collections": ("orders", "payments"),
    }
