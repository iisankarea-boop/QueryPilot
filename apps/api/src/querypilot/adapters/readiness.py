import asyncio

from arango.client import ArangoClient
from pymilvus import MilvusClient

from querypilot.application.readiness import ReadinessChecker
from querypilot.config import Settings


def build_readiness_checker(settings: Settings) -> ReadinessChecker:
    async def arangodb() -> None:
        await asyncio.to_thread(_check_arangodb, settings)

    async def milvus() -> None:
        await asyncio.to_thread(_check_milvus, settings)

    async def postgres() -> None:
        await asyncio.to_thread(_check_postgres, settings)

    return ReadinessChecker(
        {
            "arangodb": arangodb,
            "milvus": milvus,
            "postgres": postgres,
        },
        timeout_seconds=settings.readiness_timeout_seconds,
    )


def _check_arangodb(settings: Settings) -> None:
    client = ArangoClient(
        hosts=settings.arango_url,
        request_timeout=settings.readiness_timeout_seconds,
    )
    database = client.db(
        settings.arango_database,
        username=settings.arango_username,
        password=settings.arango_password.get_secret_value(),
    )
    database.version()


def _check_milvus(settings: Settings) -> None:
    client = MilvusClient(
        uri=settings.milvus_uri,
        token=settings.milvus_token.get_secret_value() or None,
        timeout=settings.readiness_timeout_seconds,
    )
    try:
        client.list_collections()
    finally:
        client.close()


def _check_postgres(settings: Settings) -> None:
    import psycopg

    with psycopg.connect(
        settings.postgres_dsn.get_secret_value(),
        connect_timeout=max(1, int(settings.readiness_timeout_seconds)),
    ) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
