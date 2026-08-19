import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal, Protocol

from querypilot.domain.catalog import CatalogEntry, CatalogRelease
from querypilot.domain.source import (
    DiscoveredCollection,
    DiscoveredEdge,
    DiscoveredField,
    DiscoveredGraph,
    SchemaSnapshot,
)


class SchemaReader(Protocol):
    async def collections(self) -> Sequence[Mapping[str, Any]]: ...

    async def sample_documents(
        self,
        collection: str,
        limit: int,
    ) -> Sequence[Mapping[str, Any]]: ...

    async def graphs(self) -> Sequence[Mapping[str, Any]]: ...


class SchemaDiscovery:
    def __init__(self, reader: SchemaReader) -> None:
        self._reader = reader

    async def inspect(
        self,
        *,
        source_id: str,
        database: str,
        sample_size: int,
    ) -> SchemaSnapshot:
        raw_collections = await self._reader.collections()
        collection_metadata = sorted(
            (
                item
                for item in raw_collections
                if not bool(item.get("system", False)) and isinstance(item.get("name"), str)
            ),
            key=lambda item: str(item["name"]),
        )
        if not collection_metadata:
            raise ValueError("ArangoDB database has no accessible non-system collections")

        collections: list[DiscoveredCollection] = []
        for metadata in collection_metadata:
            name = str(metadata["name"])
            kind: Literal["document", "edge"] = (
                "edge" if metadata.get("type") == "edge" else "document"
            )
            documents = await self._reader.sample_documents(name, sample_size)
            collections.append(
                DiscoveredCollection(
                    name=name,
                    kind=kind,
                    fields=_discover_fields(documents, kind),
                    sampled_documents=len(documents),
                )
            )

        allowed_collections = {collection.name for collection in collections}
        graphs = _discover_graphs(await self._reader.graphs(), allowed_collections)
        return SchemaSnapshot(
            source_id=source_id,
            database=database,
            collections=tuple(collections),
            graphs=graphs,
        )


def catalog_release_from_schema(snapshot: SchemaSnapshot) -> CatalogRelease:
    entries: list[CatalogEntry] = []
    for collection in snapshot.collections:
        collection_label = "边集合" if collection.kind == "edge" else "文档集合"
        entries.append(
            CatalogEntry(
                id=f"{snapshot.source_id}.{collection.name}",
                source_id=snapshot.source_id,
                kind="collection",
                entity=collection.name,
                content=(
                    f"ArangoDB {collection_label}；Schema 由 {collection.sampled_documents} "
                    "条样本文档自动推断"
                ),
                aliases=_entity_aliases(collection.name),
            )
        )
        for field in collection.fields:
            entries.append(
                CatalogEntry(
                    id=f"{snapshot.source_id}.{collection.name}.{field.name}",
                    source_id=snapshot.source_id,
                    kind="field",
                    entity=f"{collection.name}.{field.name}",
                    content=f"type={field.inferred_type}; 由 ArangoDB 样本文档自动推断",
                )
            )
        entries.append(
            CatalogEntry(
                id=f"{snapshot.source_id}.example.count_{collection.name}",
                source_id=snapshot.source_id,
                kind="example",
                entity=f"count_{collection.name}",
                content=(
                    f"question: 统计 {collection.name} 集合的文档总数\n"
                    f"aql: FOR document IN {collection.name}\n"
                    "  COLLECT WITH COUNT INTO document_count\n"
                    "  RETURN { document_count }"
                ),
            )
        )

    for graph in snapshot.graphs:
        for edge in graph.edges:
            entries.append(
                CatalogEntry(
                    id=f"{snapshot.source_id}.graph.{graph.name}.{edge.collection}",
                    source_id=snapshot.source_id,
                    kind="edge",
                    entity=f"{graph.name}.{edge.collection}",
                    content=(
                        f"edge collection {edge.collection}: "
                        f"{', '.join(edge.from_collections)} -> "
                        f"{', '.join(edge.to_collections)}"
                    ),
                )
            )
            if not edge.from_collections or not edge.to_collections:
                continue
            source = edge.from_collections[0]
            target = edge.to_collections[0]
            entries.append(
                CatalogEntry(
                    id=(
                        f"{snapshot.source_id}.example.{graph.name}."
                        f"join_{source}_to_{target}_via_{edge.collection}"
                    ),
                    source_id=snapshot.source_id,
                    kind="example",
                    entity=f"join_{source}_to_{target}_via_{edge.collection}",
                    content=(
                        f"question: join {source} documents to related {target} documents\n"
                        f"aql: FOR source IN {source}\n"
                        f"  FOR relation IN {edge.collection}\n"
                        "    FILTER relation._from == source._id\n"
                        f"    FOR target IN {target}\n"
                        "      FILTER target._id == relation._to\n"
                        "      RETURN { source, relation, target }"
                    ),
                    aliases=(
                        f"join {source} to {target}",
                        f"{source} {target} relationship",
                        *_entity_aliases(source),
                        *_entity_aliases(target),
                    ),
                )
            )
            entries.append(
                CatalogEntry(
                    id=(
                        f"{snapshot.source_id}.pattern.{graph.name}."
                        f"aggregate_{target}_per_{source}_via_{edge.collection}"
                    ),
                    source_id=snapshot.source_id,
                    kind="example",
                    entity=f"aggregate_{target}_per_{source}_via_{edge.collection}",
                    content=(
                        "pattern: aggregate related target values at source grain, then group "
                        "sources. Replace angle-bracket fields with exact catalog fields.\n"
                        f"FOR source IN {source}\n"
                        "  LET source_total = SUM(\n"
                        f"    FOR relation IN {edge.collection}\n"
                        "      FILTER relation._from == source._id\n"
                        f"      FOR target IN {target}\n"
                        "        FILTER target._id == relation._to\n"
                        "        RETURN target.<numeric_target_field>\n"
                        "  )\n"
                        "  LET source_group = source.<grouping_field>\n"
                        "  COLLECT group_key = source_group INTO grouped_sources\n"
                        "  LET source_count = LENGTH(grouped_sources)\n"
                        "  LET total_value = SUM(grouped_sources[*].source_total)\n"
                        "  RETURN { group_key, source_count, total_value, "
                        "average_per_source: total_value / source_count }"
                    ),
                    aliases=(
                        f"aggregate {target} per {source}",
                        "two stage relationship aggregation",
                        "average value per source",
                    ),
                )
            )
            entries.append(
                CatalogEntry(
                    id=(
                        f"{snapshot.source_id}.example.{graph.name}."
                        f"top_{target}_by_{edge.collection}"
                    ),
                    source_id=snapshot.source_id,
                    kind="example",
                    entity=f"top_{target}_by_{edge.collection}",
                    content=(
                        f"question: 哪个 {target} 在 {source} 到 {target} 的 "
                        f"{edge.collection} 关系中出现次数最多；按关系数量排名\n"
                        f"aql: FOR relation IN {edge.collection}\n"
                        "  COLLECT target_id = relation._to WITH COUNT INTO relation_count\n"
                        "  SORT relation_count DESC\n"
                        "  LIMIT 20\n"
                        f"  FOR target IN {target}\n"
                        "    FILTER target._id == target_id\n"
                        "    RETURN { target_id: target._key, relation_count }"
                    ),
                    aliases=(
                        f"top {target}",
                        f"most frequent {target}",
                        "出现次数最多",
                        "关系频次排行",
                        *_entity_aliases(target),
                        *_relationship_aliases(edge.collection, source, target),
                    ),
                )
            )

    fingerprint_payload = {
        "schema": snapshot.model_dump(mode="json"),
        "entries": [entry.model_dump(mode="json") for entry in entries],
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()[:12]
    return CatalogRelease(
        release_id=f"{snapshot.source_id[:100]}-{fingerprint}",
        entries=tuple(entries),
    )


def _discover_fields(
    documents: Sequence[Mapping[str, Any]],
    collection_kind: str,
) -> tuple[DiscoveredField, ...]:
    observed: dict[str, set[str]] = {}
    for document in documents:
        _observe_mapping(document, observed)

    required = {"_key": {"string"}, "_id": {"string"}}
    if collection_kind == "edge":
        required.update({"_from": {"string"}, "_to": {"string"}})
    for field_name, field_types in required.items():
        observed.setdefault(field_name, set()).update(field_types)
    observed.pop("_rev", None)

    return tuple(
        DiscoveredField(name=name, inferred_type=_merged_type(types))
        for name, types in sorted(observed.items())
    )


def _observe_mapping(
    value: Mapping[str, Any],
    observed: dict[str, set[str]],
    *,
    prefix: str = "",
    depth: int = 0,
) -> None:
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        field_name = f"{prefix}.{key}" if prefix else key
        observed.setdefault(field_name, set()).add(_value_type(item))
        if isinstance(item, dict) and depth < 2:
            _observe_mapping(item, observed, prefix=field_name, depth=depth + 1)


def _value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "datetime" if _is_iso_datetime(value) else "string"
    if isinstance(value, list):
        item_types = {_value_type(item) for item in value}
        return f"array<{_merged_type(item_types) if item_types else 'unknown'}>"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def _is_iso_datetime(value: str) -> bool:
    if "T" not in value and " " not in value:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _merged_type(types: set[str]) -> str:
    concrete = types - {"null"}
    if not concrete:
        return "null"
    if concrete <= {"integer", "number"}:
        return "number" if "number" in concrete else "integer"
    if len(concrete) == 1:
        return next(iter(concrete))
    return "mixed<" + "|".join(sorted(concrete)) + ">"


def _discover_graphs(
    raw_graphs: Sequence[Mapping[str, Any]],
    allowed_collections: set[str],
) -> tuple[DiscoveredGraph, ...]:
    graphs: list[DiscoveredGraph] = []
    for raw_graph in sorted(raw_graphs, key=lambda item: str(item.get("name", ""))):
        graph_name = raw_graph.get("name")
        if not isinstance(graph_name, str) or not graph_name:
            continue
        edges: list[DiscoveredEdge] = []
        raw_edges = raw_graph.get("edge_definitions", ())
        if not isinstance(raw_edges, list):
            continue
        for raw_edge in raw_edges:
            if not isinstance(raw_edge, dict):
                continue
            collection = raw_edge.get("edge_collection")
            if not isinstance(collection, str) or collection not in allowed_collections:
                continue
            from_collections = _allowed_names(
                raw_edge.get("from_vertex_collections"), allowed_collections
            )
            to_collections = _allowed_names(
                raw_edge.get("to_vertex_collections"), allowed_collections
            )
            edges.append(
                DiscoveredEdge(
                    collection=collection,
                    from_collections=from_collections,
                    to_collections=to_collections,
                )
            )
        graphs.append(
            DiscoveredGraph(
                name=graph_name,
                edges=tuple(sorted(edges, key=lambda edge: edge.collection)),
            )
        )
    return tuple(graphs)


def _allowed_names(value: Any, allowed_collections: set[str]) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        sorted(
            name
            for name in value
            if isinstance(name, str) and name in allowed_collections
        )
    )


def _entity_aliases(entity: str) -> tuple[str, ...]:
    aliases = {
        "categories": ("分类", "商品分类"),
        "customers": ("客户", "买家"),
        "orders": ("订单",),
        "payments": ("支付", "付款"),
        "products": ("商品", "产品"),
        "reviews": ("评论", "评价"),
        "sellers": ("卖家", "商家"),
    }
    return aliases.get(entity.lower(), ())


def _relationship_aliases(edge: str, source: str, target: str) -> tuple[str, ...]:
    if edge.lower() == "contains" and source.lower() == "orders" and target.lower() == "products":
        return ("最畅销商品", "销量最高商品", "商品销量排行")
    return ()
