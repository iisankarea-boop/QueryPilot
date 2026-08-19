import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from querypilot.application.query_agent import QueryAgent
from querypilot.domain.catalog import CatalogRelease
from querypilot.domain.source import SchemaSnapshot, SourceConnection, SourceInfo


class CatalogPublisher(Protocol):
    async def publish(self, release: CatalogRelease) -> object: ...


@dataclass(frozen=True, slots=True)
class PreparedSource:
    info: SourceInfo
    schema: SchemaSnapshot
    release: CatalogRelease
    agent: QueryAgent


class SourceNotFoundError(LookupError):
    pass


class SourceManager:
    def __init__(
        self,
        catalog: CatalogPublisher,
        prepare: Callable[[SourceConnection], Awaitable[PreparedSource]],
        initial: PreparedSource,
    ) -> None:
        self._catalog = catalog
        self._prepare = prepare
        self._sources = {initial.info.source_id: initial}
        self._write_lock = asyncio.Lock()

    async def onboard(self, connection: SourceConnection) -> SourceInfo:
        prepared = await self._prepare(connection)
        if prepared.info.source_id != connection.source_id:
            raise ValueError("prepared source id does not match connection source id")
        await self._catalog.publish(prepared.release)
        async with self._write_lock:
            self._sources[connection.source_id] = prepared
        return prepared.info

    def list_sources(self) -> tuple[SourceInfo, ...]:
        return tuple(
            source.info
            for source in sorted(self._sources.values(), key=lambda item: item.info.source_id)
        )

    def agent_for(self, source_id: str) -> QueryAgent:
        try:
            return self._sources[source_id].agent
        except KeyError as error:
            raise SourceNotFoundError(f"unknown data source: {source_id}") from error
