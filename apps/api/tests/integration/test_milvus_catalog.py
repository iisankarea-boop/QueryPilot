import os

import pytest
from querypilot.adapters.milvus_catalog import MilvusCatalogIndex
from querypilot.domain.catalog import IndexedCatalogEntry


@pytest.mark.skipif(
    not os.getenv("MILVUS_TEST_URI"),
    reason="MILVUS_TEST_URI is required for the real Milvus integration test",
)
@pytest.mark.asyncio
async def test_release_can_be_published_and_searched_in_real_milvus() -> None:
    index = MilvusCatalogIndex(
        uri=os.environ["MILVUS_TEST_URI"],
        alias="semantic_catalog_integration_test",
    )
    entry = IndexedCatalogEntry(
        id="integration.orders.paid_amount",
        release_id="integration-v1",
        source_id="integration",
        kind="field",
        entity="orders.paid_amount",
        content="订单成交额",
        approved=True,
        embedding=(1.0, 0.0, 0.0, 0.0),
    )

    await index.publish("integration-v1", [entry])
    matches = await index.search("integration", [1.0, 0.0, 0.0, 0.0], limit=1)

    assert matches[0].id == entry.id
    assert matches[0].release_id == "integration-v1"
