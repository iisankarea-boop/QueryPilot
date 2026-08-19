import asyncio
import hashlib
import re
import time
from collections.abc import Sequence
from typing import Any

from pymilvus import DataType, MilvusClient
from pymilvus.exceptions import MilvusException

from querypilot.domain.catalog import IndexedCatalogEntry


class MilvusCatalogIndex:
    def __init__(
        self,
        uri: str,
        alias: str = "semantic_catalog_active",
        token: str = "",
    ) -> None:
        self._client = MilvusClient(uri=uri, token=token or None)
        self._alias = alias

    async def publish(
        self,
        release_id: str,
        entries: Sequence[IndexedCatalogEntry],
    ) -> None:
        if not entries:
            raise ValueError("cannot publish an empty catalog release")
        await asyncio.to_thread(self._publish_sync, release_id, entries)

    async def search(
        self,
        source_id: str,
        vector: Sequence[float],
        limit: int,
    ) -> list[IndexedCatalogEntry]:
        return await asyncio.to_thread(self._search_sync, source_id, vector, limit)

    def _publish_sync(
        self,
        release_id: str,
        entries: Sequence[IndexedCatalogEntry],
    ) -> None:
        dimension = len(entries[0].embedding)
        if dimension == 0 or any(len(entry.embedding) != dimension for entry in entries):
            raise ValueError("all catalog embeddings must have one non-zero dimension")

        collection_name = _collection_name(release_id, entries)
        if not self._client.has_collection(collection_name):
            schema = self._client.create_schema(auto_id=False, enable_dynamic_field=False)
            schema.add_field(
                field_name="id",
                datatype=DataType.VARCHAR,
                is_primary=True,
                max_length=256,
            )
            schema.add_field(field_name="release_id", datatype=DataType.VARCHAR, max_length=128)
            schema.add_field(field_name="source_id", datatype=DataType.VARCHAR, max_length=128)
            schema.add_field(field_name="kind", datatype=DataType.VARCHAR, max_length=32)
            schema.add_field(field_name="entity", datatype=DataType.VARCHAR, max_length=256)
            schema.add_field(field_name="content", datatype=DataType.VARCHAR, max_length=8192)
            schema.add_field(field_name="approved", datatype=DataType.BOOL)
            schema.add_field(
                field_name="embedding",
                datatype=DataType.FLOAT_VECTOR,
                dim=dimension,
            )
            indexes = self._client.prepare_index_params()
            indexes.add_index(
                field_name="embedding",
                index_type="AUTOINDEX",
                metric_type="COSINE",
            )
            self._client.create_collection(
                collection_name=collection_name,
                schema=schema,
                index_params=indexes,
            )
            self._client.insert(
                collection_name=collection_name,
                data=[_to_row(entry) for entry in entries],
            )
            self._client.flush(collection_name)
            self._client.load_collection(collection_name)

        self._await_searchable(collection_name, entries[0])

        for source_id in sorted({entry.source_id for entry in entries}):
            self._point_alias(_source_alias(self._alias, source_id), collection_name)

        # Keep the original alias as a backwards-compatible default. Per-source
        # aliases are authoritative once a source has been published by this version.
        if not self._alias_exists(self._alias):
            self._client.create_alias(collection_name=collection_name, alias=self._alias)

    def _search_sync(
        self,
        source_id: str,
        vector: Sequence[float],
        limit: int,
    ) -> list[IndexedCatalogEntry]:
        source_alias = _source_alias(self._alias, source_id)
        collection_name = source_alias if self._alias_exists(source_alias) else self._alias
        return self._search_collection(collection_name, source_id, vector, limit)

    def _alias_exists(self, alias: str) -> bool:
        try:
            self._client.describe_alias(alias)
        except MilvusException:
            return False
        return True

    def _point_alias(self, alias: str, collection_name: str) -> None:
        if self._alias_exists(alias):
            self._client.alter_alias(collection_name=collection_name, alias=alias)
        else:
            self._client.create_alias(collection_name=collection_name, alias=alias)

    def _search_collection(
        self,
        collection_name: str,
        source_id: str,
        vector: Sequence[float],
        limit: int,
    ) -> list[IndexedCatalogEntry]:
        escaped_source = source_id.replace("\\", "\\\\").replace('"', '\\"')
        results = self._client.search(
            collection_name=collection_name,
            data=[list(vector)],
            filter=f'source_id == "{escaped_source}" and approved == true',
            limit=limit,
            output_fields=[
                "release_id",
                "source_id",
                "kind",
                "entity",
                "content",
                "approved",
            ],
            search_params={"metric_type": "COSINE"},
        )
        if not results:
            return []
        return [_from_hit(hit) for hit in results[0]]

    def _await_searchable(
        self,
        collection_name: str,
        expected: IndexedCatalogEntry,
    ) -> None:
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            matches = self._search_collection(
                collection_name,
                expected.source_id,
                expected.embedding,
                limit=1,
            )
            if matches and matches[0].id == expected.id:
                return
            time.sleep(0.25)
        raise TimeoutError("Milvus catalog release did not become searchable")


def _collection_name(
    release_id: str,
    entries: Sequence[IndexedCatalogEntry],
) -> str:
    safe_release = re.sub(r"[^a-zA-Z0-9_]", "_", release_id)[:32].strip("_") or "release"
    fingerprint = hashlib.sha256()
    fingerprint.update(release_id.encode())
    for entry in entries:
        fingerprint.update(repr(entry).encode())
    return f"semantic_catalog_{safe_release}_{fingerprint.hexdigest()[:12]}"


def _source_alias(base_alias: str, source_id: str) -> str:
    safe_source = re.sub(r"[^a-zA-Z0-9_]", "_", source_id)[:48].strip("_") or "source"
    fingerprint = hashlib.sha256(source_id.encode()).hexdigest()[:8]
    return f"{base_alias}_{safe_source}_{fingerprint}"


def _to_row(entry: IndexedCatalogEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "release_id": entry.release_id,
        "source_id": entry.source_id,
        "kind": entry.kind,
        "entity": entry.entity,
        "content": entry.content,
        "approved": entry.approved,
        "embedding": list(entry.embedding),
    }


def _from_hit(hit: dict[str, Any]) -> IndexedCatalogEntry:
    entity = hit.get("entity", hit)
    return IndexedCatalogEntry(
        id=str(hit["id"]),
        release_id=str(entity["release_id"]),
        source_id=str(entity["source_id"]),
        kind=entity["kind"],
        entity=str(entity["entity"]),
        content=str(entity["content"]),
        approved=bool(entity["approved"]),
        embedding=tuple(float(value) for value in entity.get("embedding", ())),
    )
