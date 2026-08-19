import json
import re
from dataclasses import dataclass

from querypilot.domain.models import (
    AqlCandidate,
    QueryIntent,
    QueryPolicy,
    QueryRejected,
)
from querypilot.domain.query_ir import (
    FieldRef,
    FilterSpec,
    StructuredQueryPlan,
    Transform,
)
from querypilot.domain.source import DiscoveredEdge, SchemaSnapshot

_AQL_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_NUMERIC_TYPES = frozenset({"integer", "number"})


@dataclass(frozen=True, slots=True)
class _BoundSource:
    collection: str
    variable: str


@dataclass(frozen=True, slots=True)
class _Relation:
    graph: str
    edge: DiscoveredEdge


class AqlCompiler:
    def compile(
        self,
        plan: StructuredQueryPlan,
        schema: SchemaSnapshot,
        policy: QueryPolicy,
    ) -> AqlCandidate:
        _validate_plan_shape(plan)
        index = _SchemaIndex(schema)
        index.require_collection(plan.root.collection, policy)
        if len(plan.joins) > policy.max_traversal_depth:
            raise _invalid_plan(
                f"query uses {len(plan.joins)} joins; maximum is {policy.max_traversal_depth}"
            )

        aliases: dict[str, _BoundSource] = {
            plan.root.alias: _BoundSource(plan.root.collection, "d0")
        }
        referenced_collections = {plan.root.collection}
        referenced_graphs: set[str] = set()
        lines = [f"FOR d0 IN {_collection_name(plan.root.collection)}"]

        document_index = 1
        for join_index, join in enumerate(plan.joins):
            if join.edge_alias in aliases or join.target_alias in aliases:
                raise _invalid_plan("query aliases must be unique")
            source = aliases.get(join.from_alias)
            if source is None:
                raise _invalid_plan(f"join references unknown source alias: {join.from_alias}")
            index.require_collection(join.edge_collection, policy)
            index.require_collection(join.target_collection, policy)
            relation = index.require_relation(
                edge_collection=join.edge_collection,
                source_collection=source.collection,
                target_collection=join.target_collection,
                direction=join.direction,
                policy=policy,
            )
            edge_variable = f"e{join_index}"
            target_variable = f"d{document_index}"
            document_index += 1
            aliases[join.edge_alias] = _BoundSource(join.edge_collection, edge_variable)
            aliases[join.target_alias] = _BoundSource(
                join.target_collection,
                target_variable,
            )
            referenced_collections.update(
                {join.edge_collection, join.target_collection}
            )
            referenced_graphs.add(relation.graph)
            if join.direction == "outbound":
                source_endpoint, target_endpoint = "_from", "_to"
            else:
                source_endpoint, target_endpoint = "_to", "_from"
            lines.extend(
                (
                    f"  FOR {edge_variable} IN {_collection_name(join.edge_collection)}",
                    f"    FILTER {edge_variable}.{source_endpoint} == {source.variable}._id",
                    f"    FOR {target_variable} IN {_collection_name(join.target_collection)}",
                    f"      FILTER {target_variable}._id == {edge_variable}.{target_endpoint}",
                )
            )

        bind_vars: dict[str, object] = {}
        guard_expressions = _required_guards(plan, aliases, index)
        for expression in guard_expressions:
            lines.append(f"  FILTER {expression} != null")
        for filter_spec in plan.filters:
            lines.append(
                "  FILTER "
                + self._render_filter(filter_spec, aliases, index, bind_vars)
            )

        intent: QueryIntent
        if plan.intent == "aggregate":
            self._compile_aggregate(plan, aliases, index, policy, lines)
            intent = "aggregate"
        else:
            self._compile_detail(plan, aliases, index, policy, lines)
            intent = "document"

        return AqlCandidate(
            query="\n".join(lines),
            bind_vars=bind_vars,
            referenced_collections=referenced_collections,
            referenced_graphs=referenced_graphs,
            intent=intent,
        )

    def _render_filter(
        self,
        filter_spec: FilterSpec,
        aliases: dict[str, _BoundSource],
        index: "_SchemaIndex",
        bind_vars: dict[str, object],
    ) -> str:
        expression = _field_expression(filter_spec.field, aliases, index)
        expression = _transform(expression, filter_spec.transform)
        if filter_spec.operator == "is_null":
            return f"{expression} == null"
        if filter_spec.operator == "is_not_null":
            return f"{expression} != null"

        bind_name = f"value_{len(bind_vars)}"
        bind_vars[bind_name] = filter_spec.value
        operator = {
            "eq": "==",
            "ne": "!=",
            "gt": ">",
            "gte": ">=",
            "lt": "<",
            "lte": "<=",
            "in": "IN",
        }[filter_spec.operator]
        return f"{expression} {operator} @{bind_name}"

    def _compile_aggregate(
        self,
        plan: StructuredQueryPlan,
        aliases: dict[str, _BoundSource],
        index: "_SchemaIndex",
        policy: QueryPolicy,
        lines: list[str],
    ) -> None:
        group_assignments = [
            f"{group.output} = "
            f"{_transform(_field_expression(group.field, aliases, index), group.transform)}"
            for group in plan.groups
        ]
        metric_fields = [metric for metric in plan.metrics if metric.field is not None]
        metric_slots = {
            metric.output: f"metric_{slot}"
            for slot, metric in enumerate(metric_fields)
        }
        if metric_fields:
            projection = ", ".join(
                f"{metric_slots[metric.output]}: "
                f"{_field_expression(metric.field, aliases, index)}"
                for metric in metric_fields
                if metric.field is not None
            )
            group_projection = f"{{ {projection} }}"
        else:
            group_projection = "1"

        collect = "  COLLECT"
        if group_assignments:
            collect += " " + ", ".join(group_assignments)
        else:
            collect += " qp_all = true"
        collect += f" INTO qp_group = {group_projection}"
        lines.append(collect)

        output_names = [group.output for group in plan.groups]
        for metric in plan.metrics:
            if metric.operation == "count_rows":
                calculation = "LENGTH(qp_group)"
            else:
                assert metric.field is not None
                field_type = index.field_type(
                    aliases[metric.field.source].collection,
                    metric.field.field,
                )
                if metric.operation in {"sum", "average"} and field_type not in _NUMERIC_TYPES:
                    raise _invalid_plan(
                        f"{metric.operation} requires a numeric field: "
                        f"{aliases[metric.field.source].collection}.{metric.field.field}"
                    )
                values = f"qp_group[*].{metric_slots[metric.output]}"
                function = {
                    "count_distinct": "COUNT_DISTINCT",
                    "sum": "SUM",
                    "average": "AVERAGE",
                    "min": "MIN",
                    "max": "MAX",
                }[metric.operation]
                calculation = f"{function}({values})"
            lines.append(f"  LET {metric.output} = {calculation}")
            output_names.append(metric.output)

        available = {metric.output for metric in plan.metrics}
        for ratio in plan.ratios:
            if ratio.numerator not in available or ratio.denominator not in available:
                raise _invalid_plan(f"ratio references unavailable metrics: {ratio.output}")
            lines.append(
                f"  LET {ratio.output} = {ratio.denominator} == 0 "
                f"? null : {ratio.numerator} / {ratio.denominator}"
            )
            available.add(ratio.output)
            output_names.append(ratio.output)

        for sort in plan.sort:
            lines.append(f"  SORT {sort.output} {sort.direction.upper()}")
        if plan.limit < policy.result_limit:
            lines.append(f"  LIMIT {plan.limit}")
        lines.append("  RETURN " + _return_object(output_names))

    def _compile_detail(
        self,
        plan: StructuredQueryPlan,
        aliases: dict[str, _BoundSource],
        index: "_SchemaIndex",
        policy: QueryPolicy,
        lines: list[str],
    ) -> None:
        selections = {
            selection.output: _field_expression(selection.field, aliases, index)
            for selection in plan.selections
        }
        for sort in plan.sort:
            lines.append(f"  SORT {selections[sort.output]} {sort.direction.upper()}")
        if plan.limit < policy.result_limit:
            lines.append(f"  LIMIT {plan.limit}")
        returned = ", ".join(
            f"{output}: {expression}" for output, expression in selections.items()
        )
        lines.append(f"  RETURN {{ {returned} }}")


class _SchemaIndex:
    def __init__(self, schema: SchemaSnapshot) -> None:
        self._collections = {
            collection.name: {
                field.name: field.inferred_type for field in collection.fields
            }
            for collection in schema.collections
        }
        self._relations = tuple(
            _Relation(graph=graph.name, edge=edge)
            for graph in schema.graphs
            for edge in graph.edges
        )

    def require_collection(self, collection: str, policy: QueryPolicy) -> None:
        if collection not in self._collections:
            raise _invalid_plan(f"collection is not present in schema: {collection}")
        if collection not in policy.allowed_collections:
            raise QueryRejected(
                "collection_not_allowed",
                f"collection is not allowed: {collection}",
            )

    def field_type(self, collection: str, field: str) -> str:
        fields = self._collections.get(collection, {})
        try:
            return fields[field]
        except KeyError as error:
            raise _invalid_plan(
                f"field is not present in schema: {collection}.{field}"
            ) from error

    def require_relation(
        self,
        *,
        edge_collection: str,
        source_collection: str,
        target_collection: str,
        direction: str,
        policy: QueryPolicy,
    ) -> _Relation:
        for relation in self._relations:
            if relation.edge.collection != edge_collection:
                continue
            if relation.graph not in policy.allowed_graphs:
                continue
            if direction == "outbound":
                valid = (
                    source_collection in relation.edge.from_collections
                    and target_collection in relation.edge.to_collections
                )
            else:
                valid = (
                    source_collection in relation.edge.to_collections
                    and target_collection in relation.edge.from_collections
                )
            if valid:
                return relation
        raise _invalid_plan(
            f"edge {edge_collection} does not connect {source_collection} to "
            f"{target_collection} in the requested {direction} direction"
        )


def _required_guards(
    plan: StructuredQueryPlan,
    aliases: dict[str, _BoundSource],
    index: _SchemaIndex,
) -> tuple[str, ...]:
    expressions: list[str] = []
    transformed_fields = [
        item.field for item in plan.filters if item.transform != "none"
    ]
    transformed_fields.extend(
        item.field for item in plan.groups if item.transform != "none"
    )
    for field in transformed_fields:
        expression = _field_expression(field, aliases, index)
        field_type = index.field_type(aliases[field.source].collection, field.field)
        if field_type != "datetime":
            raise _invalid_plan(
                f"date transform requires a datetime field: "
                f"{aliases[field.source].collection}.{field.field}"
            )
        if expression not in expressions:
            expressions.append(expression)
    return tuple(expressions)


def _validate_plan_shape(plan: StructuredQueryPlan) -> None:
    for filter_spec in plan.filters:
        if filter_spec.operator in {"is_null", "is_not_null"}:
            if filter_spec.value is not None or filter_spec.transform != "none":
                raise _invalid_plan(
                    f"{filter_spec.operator} accepts neither a value nor a transform"
                )
        elif filter_spec.operator == "in":
            if not isinstance(filter_spec.value, (list, tuple)) or not filter_spec.value:
                raise _invalid_plan("in requires a non-empty list value")
        elif filter_spec.value is None:
            raise _invalid_plan(f"{filter_spec.operator} requires a value")

    for metric in plan.metrics:
        if metric.operation == "count_rows" and metric.field is not None:
            raise _invalid_plan("count_rows does not accept a field")
        if metric.operation != "count_rows" and metric.field is None:
            raise _invalid_plan(f"{metric.operation} requires a field")

    if plan.intent == "detail":
        if not plan.selections:
            raise _invalid_plan("detail queries require at least one selection")
        if plan.groups or plan.metrics or plan.ratios:
            raise _invalid_plan("detail queries cannot contain aggregate outputs")
        outputs = [selection.output for selection in plan.selections]
    else:
        if not plan.metrics:
            raise _invalid_plan("aggregate queries require at least one metric")
        if plan.selections:
            raise _invalid_plan("aggregate query selections must be empty; use groups")
        outputs = [group.output for group in plan.groups]
        outputs.extend(metric.output for metric in plan.metrics)
        available_metrics = {metric.output for metric in plan.metrics}
        for ratio in plan.ratios:
            if ratio.numerator not in available_metrics:
                raise _invalid_plan(f"unknown ratio numerator: {ratio.numerator}")
            if ratio.denominator not in available_metrics:
                raise _invalid_plan(f"unknown ratio denominator: {ratio.denominator}")
            available_metrics.add(ratio.output)
            outputs.append(ratio.output)

    if len(outputs) != len(set(outputs)):
        raise _invalid_plan("query output names must be unique")
    unknown_sort_outputs = {item.output for item in plan.sort} - set(outputs)
    if unknown_sort_outputs:
        names = ", ".join(sorted(unknown_sort_outputs))
        raise _invalid_plan(f"sort references unknown outputs: {names}")


def _field_expression(
    field: FieldRef,
    aliases: dict[str, _BoundSource],
    index: _SchemaIndex,
) -> str:
    source = aliases.get(field.source)
    if source is None:
        raise _invalid_plan(f"field references unknown source alias: {field.source}")
    index.field_type(source.collection, field.field)
    expression = source.variable
    for segment in field.field.split("."):
        if _AQL_NAME.fullmatch(segment):
            expression += f".{segment}"
        else:
            expression += f"[{json.dumps(segment, ensure_ascii=False)}]"
    return expression


def _transform(expression: str, transform: Transform) -> str:
    return {
        "none": expression,
        "year": f"DATE_YEAR({expression})",
        "month": f"DATE_MONTH({expression})",
        "day": f"DATE_FORMAT({expression}, \"%yyyy-%mm-%dd\")",
        "year_month": f"DATE_FORMAT({expression}, \"%yyyy-%mm\")",
    }[transform]


def _collection_name(name: str) -> str:
    if _AQL_NAME.fullmatch(name):
        return name
    if "`" in name:
        raise _invalid_plan(f"collection name cannot be rendered safely: {name}")
    return f"`{name}`"


def _return_object(outputs: list[str]) -> str:
    fields = ", ".join(f"{output}: {output}" for output in outputs)
    return f"{{ {fields} }}"


def _invalid_plan(message: str) -> QueryRejected:
    return QueryRejected("query_plan_invalid", message)
