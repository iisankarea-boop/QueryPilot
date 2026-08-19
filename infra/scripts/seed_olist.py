import csv
import os
import urllib.request
from collections.abc import Callable, Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

from arango import ArangoClient

DATABASE = os.getenv("OLIST_ARANGO_DATABASE", "olist")
READER = os.getenv("ARANGO_USERNAME", "querypilot_reader")
DATA_DIR = Path(os.getenv("OLIST_DATA_DIR", "/data"))
BATCH_SIZE = int(os.getenv("OLIST_BATCH_SIZE", "1000"))
BASE_URL = "https://raw.githubusercontent.com/olist/work-at-olist-data/master/datasets"

DATASETS = {
    "customers": "olist_customers_dataset.csv",
    "orders": "olist_orders_dataset.csv",
    "order_items": "olist_order_items_dataset.csv",
    "payments": "olist_order_payments_dataset.csv",
    "reviews": "olist_order_reviews_dataset.csv",
    "products": "olist_products_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
    "category_translation": "product_category_name_translation.csv",
}

DOCUMENT_COLLECTIONS = (
    "customers",
    "orders",
    "products",
    "sellers",
    "categories",
    "payments",
    "reviews",
)
EDGE_COLLECTIONS = (
    "placed",
    "contains",
    "fulfilled_by",
    "has_payment",
    "has_review",
    "belongs_to",
)

Row = dict[str, str]
DocumentFactory = Callable[[int, Row], dict[str, Any] | None]


def main() -> None:
    if BATCH_SIZE < 100 or BATCH_SIZE > 10_000:
        raise ValueError("OLIST_BATCH_SIZE must be between 100 and 10000")
    paths = download_datasets(DATA_DIR)
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
    counts = _import_all(database, paths)
    _ensure_graph(database)
    system.update_permission(READER, "ro", DATABASE)

    print("Imported Olist Brazilian E-Commerce Dataset into ArangoDB.")
    for collection in (*DOCUMENT_COLLECTIONS, *EDGE_COLLECTIONS):
        print(f"{collection}: {counts[collection]}")


def download_datasets(data_dir: Path) -> dict[str, Path]:
    data_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for name, filename in DATASETS.items():
        destination = data_dir / filename
        if not destination.is_file() or destination.stat().st_size == 0:
            print(f"Downloading {filename}...")
            temporary = destination.with_suffix(destination.suffix + ".part")
            urllib.request.urlretrieve(f"{BASE_URL}/{filename}", temporary)
            temporary.replace(destination)
        paths[name] = destination
    return paths


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


def _import_all(database: Any, paths: Mapping[str, Path]) -> dict[str, int]:
    translations = {
        row["product_category_name"]: row["product_category_name_english"]
        for row in _rows(paths["category_translation"])
    }
    categories = {
        row.get("product_category_name") or "unknown"
        for row in _rows(paths["products"])
    }
    counts: dict[str, int] = {}
    counts["categories"] = _insert_documents(
        database,
        "categories",
        (
            {
                "_key": category,
                "name_pt": category,
                "name_en": translations.get(category),
            }
            for category in sorted(categories)
        ),
    )
    counts["customers"] = _import_csv(
        database,
        "customers",
        paths["customers"],
        _customer_document,
    )
    counts["orders"] = _import_csv(
        database,
        "orders",
        paths["orders"],
        _order_document,
    )
    counts["products"] = _import_csv(
        database,
        "products",
        paths["products"],
        lambda index, row: _product_document(index, row, translations),
    )
    counts["sellers"] = _import_csv(
        database,
        "sellers",
        paths["sellers"],
        _seller_document,
    )
    counts["payments"] = _import_csv(
        database,
        "payments",
        paths["payments"],
        _payment_document,
    )
    counts["reviews"] = _import_csv(
        database,
        "reviews",
        paths["reviews"],
        _review_document,
    )
    counts["placed"] = _import_csv(
        database,
        "placed",
        paths["orders"],
        _placed_edge,
    )
    counts["contains"] = _import_csv(
        database,
        "contains",
        paths["order_items"],
        _contains_edge,
    )
    counts["fulfilled_by"] = _import_csv(
        database,
        "fulfilled_by",
        paths["order_items"],
        _fulfilled_by_edge,
    )
    counts["has_payment"] = _import_csv(
        database,
        "has_payment",
        paths["payments"],
        _has_payment_edge,
    )
    counts["has_review"] = _import_csv(
        database,
        "has_review",
        paths["reviews"],
        _has_review_edge,
    )
    counts["belongs_to"] = _import_csv(
        database,
        "belongs_to",
        paths["products"],
        _belongs_to_edge,
    )
    return counts


def _import_csv(
    database: Any,
    collection: str,
    path: Path,
    factory: DocumentFactory,
) -> int:
    documents = (
        document
        for index, row in enumerate(_rows(path), start=1)
        if (document := factory(index, row)) is not None
    )
    count = _insert_documents(database, collection, documents)
    print(f"Imported {collection}: {count}")
    return count


def _insert_documents(
    database: Any,
    collection: str,
    documents: Iterable[dict[str, Any]],
) -> int:
    target = database.collection(collection)
    batch: list[dict[str, Any]] = []
    count = 0
    for document in documents:
        batch.append(document)
        if len(batch) >= BATCH_SIZE:
            target.insert_many(batch, raise_on_document_error=True, silent=True)
            count += len(batch)
            batch.clear()
    if batch:
        target.insert_many(batch, raise_on_document_error=True, silent=True)
        count += len(batch)
    return count


def _rows(path: Path) -> Iterator[Row]:
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        yield from csv.DictReader(source)


def _customer_document(_: int, row: Row) -> dict[str, Any]:
    return {
        "_key": row["customer_id"],
        "customer_unique_id": row["customer_unique_id"],
        "zip_code_prefix": _integer(row["customer_zip_code_prefix"]),
        "city": row["customer_city"],
        "state": row["customer_state"],
    }


def _order_document(_: int, row: Row) -> dict[str, Any]:
    return {
        "_key": row["order_id"],
        "customer_id": row["customer_id"],
        "status": row["order_status"],
        "purchased_at": _optional(row["order_purchase_timestamp"]),
        "approved_at": _optional(row["order_approved_at"]),
        "delivered_to_carrier_at": _optional(row["order_delivered_carrier_date"]),
        "delivered_to_customer_at": _optional(row["order_delivered_customer_date"]),
        "estimated_delivery_at": _optional(row["order_estimated_delivery_date"]),
    }


def _product_document(
    _: int,
    row: Row,
    translations: Mapping[str, str],
) -> dict[str, Any]:
    category = row.get("product_category_name") or "unknown"
    return {
        "_key": row["product_id"],
        "category_name_pt": category,
        "category_name_en": translations.get(category),
        "name_length": _integer(row["product_name_lenght"]),
        "description_length": _integer(row["product_description_lenght"]),
        "photo_count": _integer(row["product_photos_qty"]),
        "weight_g": _integer(row["product_weight_g"]),
        "length_cm": _integer(row["product_length_cm"]),
        "height_cm": _integer(row["product_height_cm"]),
        "width_cm": _integer(row["product_width_cm"]),
    }


def _seller_document(_: int, row: Row) -> dict[str, Any]:
    return {
        "_key": row["seller_id"],
        "zip_code_prefix": _integer(row["seller_zip_code_prefix"]),
        "city": row["seller_city"],
        "state": row["seller_state"],
    }


def _payment_document(_: int, row: Row) -> dict[str, Any]:
    key = f"{row['order_id']}-{row['payment_sequential']}"
    return {
        "_key": key,
        "order_id": row["order_id"],
        "sequence": _integer(row["payment_sequential"]),
        "type": row["payment_type"],
        "installments": _integer(row["payment_installments"]),
        "value": _number(row["payment_value"]),
    }


def _review_document(index: int, row: Row) -> dict[str, Any]:
    return {
        "_key": f"review-{index:06d}",
        "review_id": row["review_id"],
        "order_id": row["order_id"],
        "score": _integer(row["review_score"]),
        "title": _optional(row["review_comment_title"]),
        "message": _optional(row["review_comment_message"]),
        "created_at": _optional(row["review_creation_date"]),
        "answered_at": _optional(row["review_answer_timestamp"]),
    }


def _placed_edge(_: int, row: Row) -> dict[str, Any]:
    return {
        "_key": row["order_id"],
        "_from": f"customers/{row['customer_id']}",
        "_to": f"orders/{row['order_id']}",
    }


def _contains_edge(_: int, row: Row) -> dict[str, Any]:
    key = f"{row['order_id']}-{row['order_item_id']}"
    return {
        "_key": key,
        "_from": f"orders/{row['order_id']}",
        "_to": f"products/{row['product_id']}",
        "order_item_id": _integer(row["order_item_id"]),
        "seller_id": row["seller_id"],
        "shipping_limit_at": row["shipping_limit_date"],
        "price": _number(row["price"]),
        "freight_value": _number(row["freight_value"]),
    }


def _fulfilled_by_edge(_: int, row: Row) -> dict[str, Any]:
    key = f"{row['order_id']}-{row['order_item_id']}"
    return {
        "_key": key,
        "_from": f"orders/{row['order_id']}",
        "_to": f"sellers/{row['seller_id']}",
        "order_item_id": _integer(row["order_item_id"]),
        "product_id": row["product_id"],
        "price": _number(row["price"]),
        "freight_value": _number(row["freight_value"]),
    }


def _has_payment_edge(_: int, row: Row) -> dict[str, Any]:
    key = f"{row['order_id']}-{row['payment_sequential']}"
    return {
        "_key": key,
        "_from": f"orders/{row['order_id']}",
        "_to": f"payments/{key}",
    }


def _has_review_edge(index: int, row: Row) -> dict[str, Any]:
    key = f"review-{index:06d}"
    return {
        "_key": key,
        "_from": f"orders/{row['order_id']}",
        "_to": f"reviews/{key}",
    }


def _belongs_to_edge(_: int, row: Row) -> dict[str, Any]:
    category = row.get("product_category_name") or "unknown"
    return {
        "_key": row["product_id"],
        "_from": f"products/{row['product_id']}",
        "_to": f"categories/{category}",
    }


def _ensure_graph(database: Any) -> None:
    if database.has_graph("olist_graph"):
        database.delete_graph("olist_graph", drop_collections=False)
    database.create_graph(
        "olist_graph",
        edge_definitions=[
            _edge_definition("placed", "customers", "orders"),
            _edge_definition("contains", "orders", "products"),
            _edge_definition("fulfilled_by", "orders", "sellers"),
            _edge_definition("has_payment", "orders", "payments"),
            _edge_definition("has_review", "orders", "reviews"),
            _edge_definition("belongs_to", "products", "categories"),
        ],
    )


def _edge_definition(edge: str, source: str, target: str) -> dict[str, Any]:
    return {
        "edge_collection": edge,
        "from_vertex_collections": [source],
        "to_vertex_collections": [target],
    }


def _optional(value: str) -> str | None:
    return value or None


def _integer(value: str) -> int | None:
    return int(float(value)) if value else None


def _number(value: str) -> float | None:
    return float(value) if value else None


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value or value == "replace-me":
        raise RuntimeError(f"{name} must be configured")
    return value


if __name__ == "__main__":
    main()
