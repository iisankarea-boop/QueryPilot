import os
from typing import Any

from arango import ArangoClient
from querypilot.application.commerce_seed import build_commerce_seed

DATABASE = os.getenv("ARANGO_DATABASE", "commerce")
READER = os.getenv("ARANGO_USERNAME", "querypilot_reader")


def main() -> None:
    root_password = _required_env("ARANGO_ROOT_PASSWORD")
    reader_password = _required_env("ARANGO_PASSWORD")
    client = ArangoClient(hosts=os.getenv("ARANGO_URL", "http://arangodb:8529"))
    system = client.db("_system", username="root", password=root_password)

    if not system.has_database(DATABASE):
        system.create_database(DATABASE)
    if system.has_user(READER):
        system.update_user(READER, password=reader_password, active=True)
    else:
        system.create_user(READER, password=reader_password, active=True)
    system.update_permission(READER, "ro", DATABASE)

    database = client.db(DATABASE, username="root", password=root_password)
    _ensure_collections(database)
    _replace_seed_data(database, build_commerce_seed())
    _ensure_graph(database)
    print("Seeded deterministic commerce data and configured the read-only QueryPilot user.")


def _ensure_collections(database: Any) -> None:
    for name in ("users", "products", "orders", "categories"):
        if not database.has_collection(name):
            database.create_collection(name)
    for name in ("placed", "contains", "belongs_to", "viewed"):
        if not database.has_collection(name):
            database.create_collection(name, edge=True)


def _replace_seed_data(database: Any, documents: dict[str, list[dict[str, Any]]]) -> None:
    reset_order = (
        "placed",
        "contains",
        "belongs_to",
        "viewed",
        "users",
        "products",
        "orders",
        "categories",
    )
    for collection_name in reset_order:
        database.collection(collection_name).truncate()
    for collection_name, rows in documents.items():
        database.collection(collection_name).insert_many(
            rows,
            raise_on_document_error=True,
        )


def _ensure_graph(database: Any) -> None:
    if database.has_graph("commerce_graph"):
        return
    database.create_graph(
        "commerce_graph",
        edge_definitions=[
            {
                "edge_collection": "placed",
                "from_vertex_collections": ["users"],
                "to_vertex_collections": ["orders"],
            },
            {
                "edge_collection": "contains",
                "from_vertex_collections": ["orders"],
                "to_vertex_collections": ["products"],
            },
            {
                "edge_collection": "belongs_to",
                "from_vertex_collections": ["products"],
                "to_vertex_collections": ["categories"],
            },
            {
                "edge_collection": "viewed",
                "from_vertex_collections": ["users"],
                "to_vertex_collections": ["products"],
            },
        ],
    )


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value or value == "replace-me":
        raise RuntimeError(f"{name} must be configured")
    return value


if __name__ == "__main__":
    main()
