from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

QueryIntent = Literal["document", "aggregate", "traversal"]


class AqlCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: str = Field(min_length=1, max_length=10_000)
    bind_vars: dict[str, Any]
    referenced_collections: set[str]
    referenced_graphs: set[str]
    intent: QueryIntent


class QueryPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    allowed_collections: set[str]
    allowed_graphs: set[str]
    result_limit: int = Field(ge=1, le=200)
    max_traversal_depth: int = Field(ge=1, le=10)
    max_estimated_cost: float = Field(gt=0)


@dataclass(frozen=True, slots=True)
class PreparedQuery:
    query: str
    bind_vars: dict[str, Any]
    estimated_cost: float
    collections: frozenset[str]


class QueryRejected(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
