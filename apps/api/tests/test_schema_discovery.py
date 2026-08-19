from collections.abc import Mapping, Sequence
from typing import Any

import pytest
from querypilot.application.schema_discovery import (
    SchemaDiscovery,
    catalog_release_from_schema,
)


class FakeSchemaReader:
    async def collections(self) -> Sequence[Mapping[str, Any]]:
        return (
            {"name": "_system", "system": True, "type": "document"},
            {"name": "orders", "system": False, "type": "document"},
            {"name": "products", "system": False, "type": "document"},
            {"name": "contains", "system": False, "type": "edge"},
        )

    async def sample_documents(
        self,
        collection: str,
        limit: int,
    ) -> Sequence[Mapping[str, Any]]:
        assert limit == 25
        if collection == "orders":
            return (
                {
                    "_key": "order-1",
                    "amount": 10,
                    "created_at": "2026-08-19T10:00:00Z",
                    "purchased_at": "2018-01-02 12:34:56",
                    "shipping": {"city": "杭州"},
                },
                {"_key": "order-2", "amount": 10.5, "shipping": {"city": "上海"}},
            )
        if collection == "products":
            return ({"_key": "product-1", "category": "books"},)
        return ({"_from": "orders/order-1", "_to": "products/product-1", "quantity": 2},)

    async def graphs(self) -> Sequence[Mapping[str, Any]]:
        return (
            {
                "name": "commerce_graph",
                "edge_definitions": [
                    {
                        "edge_collection": "contains",
                        "from_vertex_collections": ["orders"],
                        "to_vertex_collections": ["products"],
                    }
                ],
            },
        )


@pytest.mark.asyncio
async def test_schema_discovery_infers_fields_and_graph_edges_without_row_values() -> None:
    snapshot = await SchemaDiscovery(FakeSchemaReader()).inspect(
        source_id="external",
        database="analytics",
        sample_size=25,
    )

    assert [collection.name for collection in snapshot.collections] == [
        "contains",
        "orders",
        "products",
    ]
    orders = next(collection for collection in snapshot.collections if collection.name == "orders")
    fields = {field.name: field.inferred_type for field in orders.fields}
    assert fields["amount"] == "number"
    assert fields["created_at"] == "datetime"
    assert fields["purchased_at"] == "datetime"
    assert fields["shipping.city"] == "string"
    assert fields["_id"] == "string"
    assert snapshot.graphs[0].edges[0].from_collections == ("orders",)
    assert snapshot.graphs[0].edges[0].to_collections == ("products",)

    release = catalog_release_from_schema(snapshot)

    assert release.release_id.startswith("external-")
    assert {entry.source_id for entry in release.entries} == {"external"}
    assert "杭州" not in "\n".join(entry.content for entry in release.entries)
    assert any(entry.entity == "orders.shipping.city" for entry in release.entries)
    assert any(entry.entity == "top_products_by_contains" for entry in release.entries)
    assert any(
        entry.entity == "join_orders_to_products_via_contains"
        for entry in release.entries
    )
    assert any(
        entry.entity == "aggregate_products_per_orders_via_contains"
        for entry in release.entries
    )
