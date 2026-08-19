import pytest
from querypilot.application.aql_compiler import AqlCompiler
from querypilot.domain.models import QueryPolicy, QueryRejected
from querypilot.domain.query_ir import (
    EdgeJoin,
    FieldRef,
    FilterSpec,
    GroupSpec,
    MetricSpec,
    QuerySource,
    RatioMetric,
    SelectionSpec,
    SortSpec,
    StructuredQueryPlan,
)
from querypilot.domain.source import (
    DiscoveredCollection,
    DiscoveredEdge,
    DiscoveredField,
    DiscoveredGraph,
    SchemaSnapshot,
)


def _collection(
    name: str,
    kind: str,
    fields: dict[str, str],
) -> DiscoveredCollection:
    return DiscoveredCollection(
        name=name,
        kind=kind,  # type: ignore[arg-type]
        fields=tuple(
            DiscoveredField(name=field_name, inferred_type=field_type)
            for field_name, field_type in fields.items()
        ),
        sampled_documents=10,
    )


@pytest.fixture
def olist_schema() -> SchemaSnapshot:
    return SchemaSnapshot(
        source_id="olist",
        database="olist",
        collections=(
            _collection(
                "orders",
                "document",
                {"_key": "string", "_id": "string", "purchased_at": "datetime"},
            ),
            _collection(
                "payments",
                "document",
                {"_key": "string", "_id": "string", "value": "number"},
            ),
            _collection(
                "reviews",
                "document",
                {"_key": "string", "_id": "string", "score": "integer"},
            ),
            _collection(
                "has_payment",
                "edge",
                {
                    "_key": "string",
                    "_id": "string",
                    "_from": "string",
                    "_to": "string",
                },
            ),
        ),
        graphs=(
            DiscoveredGraph(
                name="olist_graph",
                edges=(
                    DiscoveredEdge(
                        collection="has_payment",
                        from_collections=("orders",),
                        to_collections=("payments",),
                    ),
                ),
            ),
        ),
    )


@pytest.fixture
def policy() -> QueryPolicy:
    return QueryPolicy(
        allowed_collections={"orders", "payments", "reviews", "has_payment"},
        allowed_graphs={"olist_graph"},
        result_limit=100,
        max_traversal_depth=3,
        max_estimated_cost=10_000_000,
    )


def test_compiles_review_score_distribution_from_structured_plan(
    olist_schema: SchemaSnapshot,
    policy: QueryPolicy,
) -> None:
    plan = StructuredQueryPlan(
        intent="aggregate",
        root=QuerySource(alias="review", collection="reviews"),
        filters=(
            FilterSpec(
                field=FieldRef(source="review", field="score"),
                operator="is_not_null",
            ),
        ),
        groups=(
            GroupSpec(
                field=FieldRef(source="review", field="score"),
                transform="none",
                output="score",
            ),
        ),
        metrics=(MetricSpec(operation="count_rows", output="review_count"),),
        sort=(SortSpec(output="score", direction="desc"),),
        requirements=("count reviews by score",),
    )

    candidate = AqlCompiler().compile(plan, olist_schema, policy)

    assert candidate.query == (
        "FOR d0 IN reviews\n"
        "  FILTER d0.score != null\n"
        "  COLLECT score = d0.score INTO qp_group = 1\n"
        "  LET review_count = LENGTH(qp_group)\n"
        "  SORT score DESC\n"
        "  RETURN { score: score, review_count: review_count }"
    )
    assert candidate.bind_vars == {}
    assert candidate.referenced_collections == {"reviews"}
    assert candidate.intent == "aggregate"


def test_compiles_monthly_order_count_with_bound_year_filter(
    olist_schema: SchemaSnapshot,
    policy: QueryPolicy,
) -> None:
    plan = StructuredQueryPlan(
        intent="aggregate",
        root=QuerySource(alias="order", collection="orders"),
        filters=(
            FilterSpec(
                field=FieldRef(source="order", field="purchased_at"),
                transform="year",
                operator="eq",
                value=2018,
            ),
        ),
        groups=(
            GroupSpec(
                field=FieldRef(source="order", field="purchased_at"),
                transform="month",
                output="month",
            ),
        ),
        metrics=(MetricSpec(operation="count_rows", output="order_count"),),
        sort=(SortSpec(output="month", direction="asc"),),
        requirements=("count orders by month in 2018",),
    )

    candidate = AqlCompiler().compile(plan, olist_schema, policy)

    assert "d0.purchased_at != null" in candidate.query
    assert "DATE_YEAR(d0.purchased_at) == @value_0" in candidate.query
    assert "COLLECT month = DATE_MONTH(d0.purchased_at)" in candidate.query
    assert "created_at" not in candidate.query
    assert candidate.bind_vars == {"value_0": 2018}


def test_compiles_payment_total_and_average_per_distinct_order(
    olist_schema: SchemaSnapshot,
    policy: QueryPolicy,
) -> None:
    plan = StructuredQueryPlan(
        intent="aggregate",
        root=QuerySource(alias="order", collection="orders"),
        joins=(
            EdgeJoin(
                from_alias="order",
                edge_alias="payment_link",
                edge_collection="has_payment",
                target_alias="payment",
                target_collection="payments",
                direction="outbound",
            ),
        ),
        filters=(
            FilterSpec(
                field=FieldRef(source="order", field="purchased_at"),
                transform="year",
                operator="eq",
                value=2018,
            ),
        ),
        groups=(
            GroupSpec(
                field=FieldRef(source="order", field="purchased_at"),
                transform="month",
                output="month",
            ),
        ),
        metrics=(
            MetricSpec(
                operation="count_distinct",
                field=FieldRef(source="order", field="_key"),
                output="order_count",
            ),
            MetricSpec(
                operation="sum",
                field=FieldRef(source="payment", field="value"),
                output="payment_total",
            ),
        ),
        ratios=(
            RatioMetric(
                numerator="payment_total",
                denominator="order_count",
                output="average_order_value",
            ),
        ),
        sort=(SortSpec(output="month", direction="asc"),),
        requirements=("monthly order count, payment total, and average order value",),
    )

    candidate = AqlCompiler().compile(plan, olist_schema, policy)

    assert "FOR e0 IN has_payment" in candidate.query
    assert "FILTER e0._from == d0._id" in candidate.query
    assert "FOR d1 IN payments" in candidate.query
    assert "FILTER d1._id == e0._to" in candidate.query
    assert "COUNT_DISTINCT(qp_group[*].metric_0)" in candidate.query
    assert "SUM(qp_group[*].metric_1)" in candidate.query
    assert "order_count == 0 ? null : payment_total / order_count" in candidate.query
    assert candidate.referenced_collections == {"orders", "has_payment", "payments"}


def test_rejects_unknown_field_instead_of_compiling_it(
    olist_schema: SchemaSnapshot,
    policy: QueryPolicy,
) -> None:
    plan = StructuredQueryPlan(
        intent="aggregate",
        root=QuerySource(alias="order", collection="orders"),
        groups=(
            GroupSpec(
                field=FieldRef(source="order", field="rating"),
                transform="none",
                output="rating",
            ),
        ),
        metrics=(MetricSpec(operation="count_rows", output="review_count"),),
        requirements=("count reviews by score",),
    )

    with pytest.raises(QueryRejected, match="orders.rating") as error:
        AqlCompiler().compile(plan, olist_schema, policy)

    assert error.value.code == "query_plan_invalid"


def test_rejects_join_with_the_wrong_edge_direction(
    olist_schema: SchemaSnapshot,
    policy: QueryPolicy,
) -> None:
    plan = StructuredQueryPlan(
        intent="detail",
        root=QuerySource(alias="payment", collection="payments"),
        joins=(
            EdgeJoin(
                from_alias="payment",
                edge_alias="payment_link",
                edge_collection="has_payment",
                target_alias="order",
                target_collection="orders",
                direction="outbound",
            ),
        ),
        selections=(
            SelectionSpec(
                field=FieldRef(source="payment", field="_key"),
                output="payment_id",
            ),
        ),
        requirements=("return payments",),
    )

    with pytest.raises(QueryRejected, match="has_payment") as error:
        AqlCompiler().compile(plan, olist_schema, policy)

    assert error.value.code == "query_plan_invalid"


def test_compiles_global_average_with_a_valid_constant_group(
    olist_schema: SchemaSnapshot,
    policy: QueryPolicy,
) -> None:
    plan = StructuredQueryPlan(
        intent="aggregate",
        root=QuerySource(alias="review", collection="reviews"),
        metrics=(
            MetricSpec(
                operation="average",
                field=FieldRef(source="review", field="score"),
                output="average_score",
            ),
        ),
        requirements=("return average review score",),
    )

    candidate = AqlCompiler().compile(plan, olist_schema, policy)

    assert "COLLECT qp_all = true INTO qp_group" in candidate.query
    assert "AVERAGE(qp_group[*].metric_0)" in candidate.query
    assert "qp_all" not in candidate.query.split("RETURN", maxsplit=1)[1]


def test_aggregate_selection_is_rejected_by_compiler_for_replanning(
    olist_schema: SchemaSnapshot,
    policy: QueryPolicy,
) -> None:
    plan = StructuredQueryPlan(
        intent="aggregate",
        root=QuerySource(alias="review", collection="reviews"),
        selections=(
            SelectionSpec(
                field=FieldRef(source="review", field="score"),
                output="score",
            ),
        ),
        groups=(
            GroupSpec(
                field=FieldRef(source="review", field="score"),
                output="score",
            ),
        ),
        metrics=(MetricSpec(operation="count_rows", output="review_count"),),
        requirements=("count reviews by score",),
    )

    with pytest.raises(QueryRejected, match="selections must be empty") as error:
        AqlCompiler().compile(plan, olist_schema, policy)

    assert error.value.code == "query_plan_invalid"
