from datetime import UTC, datetime, timedelta
from random import Random
from typing import Any

SeedDocuments = dict[str, list[dict[str, Any]]]

_REGIONS = ("华东", "华南", "华北", "西南", "华中", "东北")
_CATEGORY_NAMES = ("手机数码", "电脑办公", "家用电器", "运动户外", "增值服务")
_PRODUCT_PREFIXES = ("智能手机", "轻薄电脑", "智能家电", "运动装备", "保障服务")


def build_commerce_seed(seed: int = 20_260_819) -> SeedDocuments:
    random = Random(seed)
    users = _users()
    categories = _categories()
    products = _products(random)
    orders: list[dict[str, Any]] = []
    placed: list[dict[str, Any]] = []
    contains: list[dict[str, Any]] = []

    order_start = datetime(2026, 1, 1, tzinfo=UTC)
    for index in range(1, 1_201):
        order_key = f"order-{index:04d}"
        user_key = f"user-{((index - 1) % len(users)) + 1:03d}"
        first_product = random.randrange(1, len(products) + 1)
        second_product = ((first_product + random.randrange(1, len(products))) % len(products)) + 1
        product_indexes = (first_product, second_product)
        quantities = (1 + index % 2, 1)
        amount = sum(
            products[item - 1]["price"] * quantity
            for item, quantity in zip(product_indexes, quantities, strict=True)
        )
        status_slot = index % 10
        status = "paid" if status_slot < 7 else "cancelled" if status_slot < 9 else "refunded"
        paid_amount = amount if status == "paid" else 0
        created_at = order_start + timedelta(hours=index * 3)

        orders.append(
            {
                "_key": order_key,
                "customer_id": user_key,
                "status": status,
                "paid_amount": paid_amount,
                "created_at": created_at.isoformat().replace("+00:00", "Z"),
            }
        )
        placed.append(
            {
                "_key": f"placed-{index:04d}",
                "_from": f"users/{user_key}",
                "_to": f"orders/{order_key}",
            }
        )
        for slot, (product_index, quantity) in enumerate(
            zip(product_indexes, quantities, strict=True),
            start=1,
        ):
            contains.append(
                {
                    "_key": f"contains-{index:04d}-{slot}",
                    "_from": f"orders/{order_key}",
                    "_to": f"products/product-{product_index:03d}",
                    "quantity": quantity,
                }
            )

    belongs_to = [
        {
            "_key": f"belongs-{index:03d}",
            "_from": f"products/product-{index:03d}",
            "_to": f"categories/category-{((index - 1) % len(categories)) + 1:02d}",
        }
        for index in range(1, len(products) + 1)
    ]
    viewed = [
        {
            "_key": f"viewed-{user_index:03d}-{slot}",
            "_from": f"users/user-{user_index:03d}",
            "_to": f"products/product-{((user_index * 7 + slot * 13) % len(products)) + 1:03d}",
            "viewed_at": (datetime(2026, 7, 1, tzinfo=UTC) + timedelta(hours=user_index * 5 + slot))
            .isoformat()
            .replace("+00:00", "Z"),
        }
        for user_index in range(1, len(users) + 1)
        for slot in range(1, 6)
    ]
    return {
        "users": users,
        "categories": categories,
        "products": products,
        "orders": orders,
        "placed": placed,
        "contains": contains,
        "belongs_to": belongs_to,
        "viewed": viewed,
    }


def _users() -> list[dict[str, Any]]:
    registered_at = datetime(2025, 1, 1, tzinfo=UTC)
    return [
        {
            "_key": f"user-{index:03d}",
            "name": f"客户{index:03d}",
            "region": _REGIONS[(index - 1) % len(_REGIONS)],
            "created_at": (registered_at + timedelta(days=index * 2))
            .isoformat()
            .replace("+00:00", "Z"),
        }
        for index in range(1, 121)
    ]


def _categories() -> list[dict[str, Any]]:
    return [
        {"_key": f"category-{index:02d}", "name": name}
        for index, name in enumerate(_CATEGORY_NAMES, start=1)
    ]


def _products(random: Random) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    for index in range(1, 161):
        category_index = (index - 1) % len(_PRODUCT_PREFIXES)
        base_price = (category_index + 1) * 200
        products.append(
            {
                "_key": f"product-{index:03d}",
                "name": f"{_PRODUCT_PREFIXES[category_index]} {index:03d}",
                "price": base_price + random.randrange(50, 5_000),
                "active": index % 17 != 0,
            }
        )
    return products
