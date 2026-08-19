from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from querypilot.domain.catalog import CatalogEntry, CatalogKind, CatalogRelease
from querypilot.domain.source import (
    DiscoveredCollection,
    DiscoveredEdge,
    DiscoveredField,
    DiscoveredGraph,
    SchemaSnapshot,
)


@dataclass(frozen=True, slots=True)
class CatalogManifest:
    database: str
    release: CatalogRelease
    schema: SchemaSnapshot
    allowed_collections: set[str]
    allowed_graphs: set[str]


def load_catalog_manifest(path: Path) -> CatalogManifest:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("catalog manifest root must be a mapping")

    release_id = _required_string(raw, "release_id")
    source_id = _required_string(raw, "source_id")
    database = _required_string(raw, "database")
    entries: list[CatalogEntry] = []
    allowed_collections: set[str] = set()
    discovered_collections: list[DiscoveredCollection] = []

    catalog_groups: tuple[tuple[str, CatalogKind], ...] = (
        ("collections", "collection"),
        ("edge_collections", "collection"),
    )
    for kind_key, catalog_kind in catalog_groups:
        for collection_name, definition in _mapping(raw.get(kind_key, {}), kind_key).items():
            collection = _mapping(definition, f"{kind_key}.{collection_name}")
            allowed_collections.add(collection_name)
            collection_kind: Literal["document", "edge"] = (
                "edge" if kind_key == "edge_collections" else "document"
            )
            entries.append(
                CatalogEntry(
                    id=f"{source_id}.{collection_name}",
                    source_id=source_id,
                    kind=catalog_kind,
                    entity=collection_name,
                    content=_required_string(collection, "description"),
                    aliases=_string_tuple(collection.get("aliases", ())),
                )
            )
            discovered_fields: list[DiscoveredField] = []
            for field_name, field_definition in _mapping(
                collection.get("fields", {}), f"{kind_key}.{collection_name}.fields"
            ).items():
                field = _mapping(field_definition, f"{collection_name}.{field_name}")
                field_type = str(field.get("type", "unknown"))
                description = _required_string(field, "description")
                discovered_fields.append(
                    DiscoveredField(name=field_name, inferred_type=field_type)
                )
                entries.append(
                    CatalogEntry(
                        id=f"{source_id}.{collection_name}.{field_name}",
                        source_id=source_id,
                        kind="field",
                        entity=f"{collection_name}.{field_name}",
                        content=f"type={field_type}; {description}",
                        aliases=_string_tuple(field.get("aliases", ())),
                    )
                )
            required_fields = {"_key": "string", "_id": "string"}
            if collection_kind == "edge":
                required_fields.update({"_from": "string", "_to": "string"})
            known_fields = {field.name for field in discovered_fields}
            discovered_fields.extend(
                DiscoveredField(name=name, inferred_type=field_type)
                for name, field_type in required_fields.items()
                if name not in known_fields
            )
            discovered_collections.append(
                DiscoveredCollection(
                    name=collection_name,
                    kind=collection_kind,
                    fields=tuple(sorted(discovered_fields, key=lambda item: item.name)),
                    sampled_documents=0,
                )
            )

    allowed_graphs: set[str] = set()
    discovered_graphs: list[DiscoveredGraph] = []
    for graph_name, graph_definition in _mapping(raw.get("graphs", {}), "graphs").items():
        graph = _mapping(graph_definition, f"graphs.{graph_name}")
        allowed_graphs.add(graph_name)
        discovered_edges: list[DiscoveredEdge] = []
        for edge_name, edge_definition in _mapping(
            graph.get("edges", {}), f"graphs.{graph_name}.edges"
        ).items():
            edge = _mapping(edge_definition, f"graphs.{graph_name}.edges.{edge_name}")
            from_collection = _required_string(edge, "from")
            to_collection = _required_string(edge, "to")
            discovered_edges.append(
                DiscoveredEdge(
                    collection=edge_name,
                    from_collections=(from_collection,),
                    to_collections=(to_collection,),
                )
            )
            entries.append(
                CatalogEntry(
                    id=f"{source_id}.graph.{graph_name}.{edge_name}",
                    source_id=source_id,
                    kind="edge",
                    entity=f"{graph_name}.{edge_name}",
                    content=(
                        f"edge collection {edge_name}: "
                        f"{from_collection} -> {to_collection}"
                    ),
                )
            )
        discovered_graphs.append(
            DiscoveredGraph(
                name=graph_name,
                edges=tuple(discovered_edges),
            )
        )

    for metric_name, metric_definition in _mapping(raw.get("metrics", {}), "metrics").items():
        metric = _mapping(metric_definition, f"metrics.{metric_name}")
        entries.append(
            CatalogEntry(
                id=f"{source_id}.metric.{metric_name}",
                source_id=source_id,
                kind="metric",
                entity=metric_name,
                content=(
                    f"{_required_string(metric, 'description')}; "
                    f"expression={_required_string(metric, 'expression')}"
                ),
                aliases=_string_tuple(metric.get("aliases", ())),
            )
        )

    for example in raw.get("examples", []):
        item = _mapping(example, "examples[]")
        example_id = _required_string(item, "id")
        entries.append(
            CatalogEntry(
                id=f"{source_id}.example.{example_id}",
                source_id=source_id,
                kind="example",
                entity=example_id,
                content=(
                    f"question: {_required_string(item, 'question')}\n"
                    f"aql: {_required_string(item, 'aql')}"
                ),
                approved=bool(item.get("approved", False)),
            )
        )

    return CatalogManifest(
        database=database,
        release=CatalogRelease(release_id=release_id, entries=tuple(entries)),
        schema=SchemaSnapshot(
            source_id=source_id,
            database=database,
            collections=tuple(discovered_collections),
            graphs=tuple(discovered_graphs),
        ),
        allowed_collections=allowed_collections,
        allowed_graphs=allowed_graphs,
    )


def _mapping(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{location} must be a string-keyed mapping")
    return value


def _required_string(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
        raise ValueError("aliases must be a list of strings")
    return tuple(value)
