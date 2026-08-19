from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Alias = str
Transform = Literal["none", "year", "month", "day", "year_month"]


class FieldRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: Alias = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=64)
    field: str = Field(min_length=1, max_length=256)


class QuerySource(BaseModel):
    model_config = ConfigDict(frozen=True)

    alias: Alias = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=64)
    collection: str = Field(min_length=1, max_length=256)


class EdgeJoin(BaseModel):
    model_config = ConfigDict(frozen=True)

    from_alias: Alias = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=64)
    edge_alias: Alias = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=64)
    edge_collection: str = Field(min_length=1, max_length=256)
    target_alias: Alias = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=64)
    target_collection: str = Field(min_length=1, max_length=256)
    direction: Literal["outbound", "inbound"]


class FilterSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    field: FieldRef
    transform: Transform = "none"
    operator: Literal[
        "eq",
        "ne",
        "gt",
        "gte",
        "lt",
        "lte",
        "in",
        "is_null",
        "is_not_null",
    ]
    value: Any = None

class SelectionSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    field: FieldRef
    output: Alias = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=64)


class GroupSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    field: FieldRef
    transform: Transform = "none"
    output: Alias = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=64)


class MetricSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    operation: Literal["count_rows", "count_distinct", "sum", "average", "min", "max"]
    field: FieldRef | None = None
    output: Alias = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=64)

class RatioMetric(BaseModel):
    model_config = ConfigDict(frozen=True)

    numerator: Alias = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=64)
    denominator: Alias = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=64)
    output: Alias = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=64)


class SortSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    output: Alias = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=64)
    direction: Literal["asc", "desc"] = "asc"


class StructuredQueryPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    intent: Literal["detail", "aggregate"]
    root: QuerySource
    joins: tuple[EdgeJoin, ...] = ()
    filters: tuple[FilterSpec, ...] = ()
    selections: tuple[SelectionSpec, ...] = ()
    groups: tuple[GroupSpec, ...] = ()
    metrics: tuple[MetricSpec, ...] = ()
    ratios: tuple[RatioMetric, ...] = ()
    sort: tuple[SortSpec, ...] = ()
    limit: int = Field(default=100, ge=1, le=200)
    assumptions: tuple[str, ...] = ()
    requirements: tuple[str, ...] = Field(min_length=1, max_length=20)
