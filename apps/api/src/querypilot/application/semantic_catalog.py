from collections.abc import Sequence
from typing import Protocol

from querypilot.domain.catalog import (
    CatalogEntry,
    CatalogEvidence,
    CatalogRelease,
    ContextPack,
    ContextRequest,
    IndexedCatalogEntry,
    PublishReport,
)


class EmbeddingModel(Protocol):
    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...
    async def embed_query(self, text: str) -> list[float]: ...


class CatalogIndex(Protocol):
    async def publish(
        self,
        release_id: str,
        entries: Sequence[IndexedCatalogEntry],
    ) -> None: ...

    async def search(
        self,
        source_id: str,
        vector: Sequence[float],
        limit: int,
    ) -> list[IndexedCatalogEntry]: ...


class SemanticCatalog:
    def __init__(self, embeddings: EmbeddingModel, index: CatalogIndex) -> None:
        self._embeddings = embeddings
        self._index = index

    async def publish(self, release: CatalogRelease) -> PublishReport:
        texts = [_embedding_text(entry) for entry in release.entries]
        vectors = await self._embeddings.embed_documents(texts)
        if len(vectors) != len(release.entries):
            raise ValueError("embedding provider returned an unexpected vector count")

        indexed_entries = [
            IndexedCatalogEntry(
                id=entry.id,
                release_id=release.release_id,
                source_id=entry.source_id,
                kind=entry.kind,
                entity=entry.entity,
                content=entry.content,
                approved=entry.approved,
                embedding=tuple(vector),
            )
            for entry, vector in zip(release.entries, vectors, strict=True)
        ]
        await self._index.publish(release.release_id, indexed_entries)
        return PublishReport(
            release_id=release.release_id,
            published_entries=len(indexed_entries),
        )

    async def context_for(self, request: ContextRequest) -> ContextPack:
        vector = await self._embeddings.embed_query(request.question)
        matches = await self._index.search(request.source_id, vector, request.limit)
        if not matches:
            return ContextPack(release_id="", evidence=())

        return ContextPack(
            release_id=matches[0].release_id,
            evidence=tuple(
                CatalogEvidence(
                    id=match.id,
                    kind=match.kind,
                    entity=match.entity,
                    content=match.content,
                )
                for match in matches
            ),
        )


def _embedding_text(entry: CatalogEntry) -> str:
    # Kept local so the exact indexing representation does not become public interface.
    aliases = ", ".join(entry.aliases)
    return (
        f"kind: {entry.kind}\n"
        f"entity: {entry.entity}\n"
        f"aliases: {aliases}\n"
        f"description: {entry.content}"
    )
