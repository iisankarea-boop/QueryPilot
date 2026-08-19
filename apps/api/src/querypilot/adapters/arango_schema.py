import asyncio
from collections.abc import Mapping, Sequence
from typing import Any

from arango.client import ArangoClient
from arango.cursor import Cursor

from querypilot.domain.source import SourceConnection


class ArangoSchemaReader:
    def __init__(self, connection: SourceConnection, *, timeout_seconds: float) -> None:
        client = ArangoClient(
            hosts=connection.url,
            request_timeout=timeout_seconds + 2,
        )
        self._database = client.db(
            connection.database,
            username=connection.username,
            password=connection.password.get_secret_value(),
        )

    async def collections(self) -> Sequence[Mapping[str, Any]]:
        return await asyncio.to_thread(self._collections_sync)

    async def sample_documents(
        self,
        collection: str,
        limit: int,
    ) -> Sequence[Mapping[str, Any]]:
        return await asyncio.to_thread(self._sample_documents_sync, collection, limit)

    async def graphs(self) -> Sequence[Mapping[str, Any]]:
        return await asyncio.to_thread(self._graphs_sync)

    def _collections_sync(self) -> Sequence[Mapping[str, Any]]:
        collections = self._database.collections()
        if not isinstance(collections, list):
            raise RuntimeError("ArangoDB returned an unexpected collections response")
        return collections

    def _sample_documents_sync(
        self,
        collection: str,
        limit: int,
    ) -> Sequence[Mapping[str, Any]]:
        bind_vars: dict[str, Any] = {"@collection": collection, "limit": limit}
        cursor = self._database.aql.execute(
            "FOR document IN @@collection LIMIT @limit RETURN document",
            bind_vars=bind_vars,
            batch_size=limit,
            max_runtime=10,  # type: ignore[arg-type]
            fail_on_warning=True,
        )
        if not isinstance(cursor, Cursor):
            raise RuntimeError("ArangoDB returned an unexpected cursor type")
        documents = list(cursor)
        if not all(isinstance(document, dict) for document in documents):
            raise RuntimeError("ArangoDB returned a non-document sample")
        return documents

    def _graphs_sync(self) -> Sequence[Mapping[str, Any]]:
        graphs = self._database.graphs()
        if not isinstance(graphs, list):
            raise RuntimeError("ArangoDB returned an unexpected graphs response")
        return graphs
