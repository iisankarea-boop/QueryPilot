from querypilot.application.commerce_seed import build_commerce_seed


def test_commerce_seed_is_repeatable_and_referentially_complete() -> None:
    first = build_commerce_seed()
    second = build_commerce_seed()

    assert first == second
    assert {name: len(rows) for name, rows in first.items()} == {
        "users": 120,
        "categories": 5,
        "products": 160,
        "orders": 1_200,
        "placed": 1_200,
        "contains": 2_400,
        "belongs_to": 160,
        "viewed": 600,
    }

    document_ids = {
        f"{collection}/{row['_key']}"
        for collection in ("users", "categories", "products", "orders")
        for row in first[collection]
    }
    edge_endpoints = {
        endpoint
        for collection in ("placed", "contains", "belongs_to", "viewed")
        for row in first[collection]
        for endpoint in (row["_from"], row["_to"])
    }

    assert edge_endpoints <= document_ids
