from typing import cast

import pytest
from querypilot.application.query_agent import QueryAgent
from querypilot.application.source_manager import (
    PreparedSource,
    SourceManager,
    SourceNotFoundError,
)
from querypilot.domain.catalog import CatalogEntry, CatalogRelease
from querypilot.domain.source import (
    DiscoveredCollection,
    DiscoveredField,
    SchemaSnapshot,
    SourceConnection,
    SourceInfo,
)


class RecordingCatalog:
    def __init__(self) -> None:
        self.releases: list[CatalogRelease] = []

    async def publish(self, release: CatalogRelease) -> object:
        self.releases.append(release)
        return object()


def prepared_source(source_id: str) -> PreparedSource:
    release = CatalogRelease(
        release_id=f"{source_id}-v1",
        entries=(
            CatalogEntry(
                id=f"{source_id}.orders",
                source_id=source_id,
                kind="collection",
                entity="orders",
                content="orders collection",
            ),
        ),
    )
    return PreparedSource(
        info=SourceInfo(
            source_id=source_id,
            url="http://arangodb:8529",
            database="analytics",
            release_id=release.release_id,
            collection_count=1,
            field_count=0,
            graph_count=0,
        ),
        schema=SchemaSnapshot(
            source_id=source_id,
            database="analytics",
            collections=(
                DiscoveredCollection(
                    name="orders",
                    kind="document",
                    fields=(DiscoveredField(name="_id", inferred_type="string"),),
                    sampled_documents=0,
                ),
            ),
            graphs=(),
        ),
        release=release,
        agent=cast(QueryAgent, object()),
    )


@pytest.mark.asyncio
async def test_onboard_publishes_before_source_becomes_queryable() -> None:
    catalog = RecordingCatalog()

    async def prepare(connection: SourceConnection) -> PreparedSource:
        return prepared_source(connection.source_id)

    initial = prepared_source("commerce")
    manager = SourceManager(catalog, prepare, initial)
    connection = SourceConnection(
        source_id="external",
        url="http://arangodb:8529",
        database="analytics",
        username="reader",
        password="secret",
    )

    info = await manager.onboard(connection)

    assert info.source_id == "external"
    assert [release.release_id for release in catalog.releases] == ["external-v1"]
    assert [source.source_id for source in manager.list_sources()] == ["commerce", "external"]
    assert manager.agent_for("external") is not None


def test_unknown_source_is_rejected() -> None:
    async def prepare(connection: SourceConnection) -> PreparedSource:
        return prepared_source(connection.source_id)

    manager = SourceManager(RecordingCatalog(), prepare, prepared_source("commerce"))

    with pytest.raises(SourceNotFoundError, match="unknown data source"):
        manager.agent_for("missing")


@pytest.mark.parametrize("url", ["arangodb:8529", "ftp://db.example.com", "http://u:p@db:8529"])
def test_source_connection_rejects_unsafe_or_invalid_urls(url: str) -> None:
    with pytest.raises(ValueError):
        SourceConnection(
            source_id="external",
            url=url,
            database="analytics",
            username="reader",
            password="secret",
        )
