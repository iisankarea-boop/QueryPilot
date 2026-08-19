from pathlib import Path

from querypilot.application.catalog_manifest import load_catalog_manifest


def test_commerce_manifest_defines_search_evidence_and_query_policy() -> None:
    manifest_path = Path(__file__).parents[3] / "catalog" / "commerce" / "catalog.yaml"

    manifest = load_catalog_manifest(manifest_path)

    assert manifest.release.release_id == "commerce-v2"
    entry_ids = {entry.id for entry in manifest.release.entries}
    assert "commerce.orders.paid_amount" in entry_ids
    assert "commerce.example.paid_order_details" in entry_ids
    assert len(manifest.release.entries) == 50
    assert manifest.allowed_collections == {
        "users",
        "products",
        "orders",
        "categories",
        "placed",
        "contains",
        "belongs_to",
        "viewed",
    }
    assert manifest.allowed_graphs == {"commerce_graph"}
    assert {collection.name for collection in manifest.schema.collections} == (
        manifest.allowed_collections
    )
    contains = next(
        collection
        for collection in manifest.schema.collections
        if collection.name == "contains"
    )
    assert contains.kind == "edge"
    assert {field.name for field in contains.fields} >= {"_id", "_from", "_to", "quantity"}
    relation = next(
        edge
        for graph in manifest.schema.graphs
        for edge in graph.edges
        if edge.collection == "contains"
    )
    assert relation.from_collections == ("orders",)
    assert relation.to_collections == ("products",)
