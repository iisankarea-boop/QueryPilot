from infra.scripts.seed_olist import (
    _contains_edge,
    _customer_document,
    _fulfilled_by_edge,
    _has_payment_edge,
    _payment_document,
    _review_document,
)


def test_customer_record_keeps_cross_order_identity_separate() -> None:
    document = _customer_document(
        1,
        {
            "customer_id": "customer-record",
            "customer_unique_id": "stable-buyer",
            "customer_zip_code_prefix": "13000",
            "customer_city": "campinas",
            "customer_state": "SP",
        },
    )

    assert document["_key"] == "customer-record"
    assert document["customer_unique_id"] == "stable-buyer"


def test_order_item_edges_keep_product_and_seller_context() -> None:
    row = {
        "order_id": "order-1",
        "order_item_id": "2",
        "product_id": "product-1",
        "seller_id": "seller-1",
        "shipping_limit_date": "2018-01-01 10:00:00",
        "price": "99.90",
        "freight_value": "12.50",
    }

    contains = _contains_edge(1, row)
    fulfilled = _fulfilled_by_edge(1, row)

    assert contains["_key"] == fulfilled["_key"] == "order-1-2"
    assert contains["_from"] == "orders/order-1"
    assert contains["_to"] == "products/product-1"
    assert fulfilled["_to"] == "sellers/seller-1"
    assert fulfilled["product_id"] == "product-1"


def test_payment_document_and_edge_share_a_stable_composite_key() -> None:
    row = {
        "order_id": "order-1",
        "payment_sequential": "2",
        "payment_type": "credit_card",
        "payment_installments": "3",
        "payment_value": "120.25",
    }

    payment = _payment_document(1, row)
    edge = _has_payment_edge(1, row)

    assert payment["_key"] == "order-1-2"
    assert edge["_to"] == "payments/order-1-2"
    assert payment["value"] == 120.25


def test_review_document_uses_row_identity_when_source_ids_repeat() -> None:
    row = {
        "review_id": "source-review",
        "order_id": "order-1",
        "review_score": "5",
        "review_comment_title": "",
        "review_comment_message": "great",
        "review_creation_date": "2018-01-01 00:00:00",
        "review_answer_timestamp": "2018-01-02 00:00:00",
    }

    first = _review_document(1, row)
    second = _review_document(2, row)

    assert first["_key"] != second["_key"]
    assert first["review_id"] == second["review_id"] == "source-review"
