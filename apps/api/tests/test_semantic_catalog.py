from collections.abc import Sequence

import pytest
from querypilot.application.semantic_catalog import SemanticCatalog
from querypilot.domain.catalog import (
    CatalogEntry,
    CatalogRelease,
    ContextRequest,
    IndexedCatalogEntry,
)


class KeywordEmbedding:
    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    @staticmethod
    def _embed(text: str) -> list[float]:
        return [
            float("成交额" in text or "paid_amount" in text),
            float("用户名" in text or "name" in text),
        ]


class InMemoryCatalogIndex:
    def __init__(self) -> None:
        self.release_id: str | None = None
        self.entries: list[IndexedCatalogEntry] = []

    async def publish(
        self,
        release_id: str,
        entries: Sequence[IndexedCatalogEntry],
    ) -> None:
        self.release_id = release_id
        self.entries = list(entries)

    async def search(
        self,
        source_id: str,
        vector: Sequence[float],
        limit: int,
    ) -> list[IndexedCatalogEntry]:
        matches = [entry for entry in self.entries if entry.source_id == source_id]
        return sorted(
            matches,
            key=lambda entry: sum(a * b for a, b in zip(entry.embedding, vector, strict=True)),
            reverse=True,
        )[:limit]


@pytest.mark.asyncio
async def test_published_catalog_returns_relevant_evidence_for_the_selected_source() -> None:
    index = InMemoryCatalogIndex()
    catalog = SemanticCatalog(KeywordEmbedding(), index)
    release = CatalogRelease(
        release_id="commerce-v1",
        entries=(
            CatalogEntry(
                id="commerce.orders.paid_amount",
                source_id="commerce",
                kind="field",
                entity="orders.paid_amount",
                content="订单实付金额，单位为人民币元",
                aliases=("成交额", "GMV"),
            ),
            CatalogEntry(
                id="commerce.users.name",
                source_id="commerce",
                kind="field",
                entity="users.name",
                content="用户显示名称",
                aliases=("用户名",),
            ),
            CatalogEntry(
                id="support.tickets.amount",
                source_id="support",
                kind="field",
                entity="tickets.amount",
                content="工单金额",
                aliases=("成交额",),
            ),
        ),
    )

    report = await catalog.publish(release)
    context = await catalog.context_for(
        ContextRequest(question="按成交额排序", source_id="commerce", limit=1)
    )

    assert report.published_entries == 3
    assert context.release_id == "commerce-v1"
    assert [evidence.id for evidence in context.evidence] == ["commerce.orders.paid_amount"]
    assert context.evidence[0].entity == "orders.paid_amount"
