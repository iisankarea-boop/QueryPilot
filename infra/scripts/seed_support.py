import os
from typing import Any

from arango import ArangoClient
from querypilot.application.support_seed import (
    DOCUMENT_COLLECTIONS,
    EDGE_COLLECTIONS,
    build_support_seed,
)

DATABASE = os.getenv("SUPPORT_ARANGO_DATABASE", "support_lab")
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
    system.update_permission(READER, "rw", DATABASE)

    database = client.db(DATABASE, username="root", password=root_password)
    _ensure_collections(database)
    documents = build_support_seed()
    for collection_name in (*DOCUMENT_COLLECTIONS, *EDGE_COLLECTIONS):
        database.collection(collection_name).insert_many(
            documents[collection_name],
            raise_on_document_error=True,
            silent=True,
        )
    _ensure_graph(database)
    system.update_permission(READER, "ro", DATABASE)

    print(f"Seeded {DATABASE} customer support graph.")
    for collection_name in (*DOCUMENT_COLLECTIONS, *EDGE_COLLECTIONS):
        print(f"{collection_name}: {len(documents[collection_name])}")


def _ensure_collections(database: Any) -> None:
    for name in DOCUMENT_COLLECTIONS:
        if not database.has_collection(name):
            database.create_collection(name)
        else:
            database.collection(name).truncate()
    for name in EDGE_COLLECTIONS:
        if not database.has_collection(name):
            database.create_collection(name, edge=True)
        else:
            database.collection(name).truncate()


def _ensure_graph(database: Any) -> None:
    if database.has_graph("support_graph"):
        database.delete_graph("support_graph", drop_collections=False)
    database.create_graph(
        "support_graph",
        edge_definitions=[
            _edge_definition("raised_by", "companies", "cases"),
            _edge_definition("owned_by", "cases", "specialists"),
            _edge_definition("classified_as", "cases", "topics"),
            _edge_definition("has_event", "cases", "case_events"),
        ],
    )


def _edge_definition(edge: str, source: str, target: str) -> dict[str, Any]:
    return {
        "edge_collection": edge,
        "from_vertex_collections": [source],
        "to_vertex_collections": [target],
    }


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value or value == "replace-me":
        raise RuntimeError(f"{name} must be configured")
    return value


if __name__ == "__main__":
    main()
