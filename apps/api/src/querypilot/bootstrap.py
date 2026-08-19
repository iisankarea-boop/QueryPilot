from dataclasses import dataclass

from querypilot.adapters.arango_database import ArangoAqlDatabase
from querypilot.adapters.arango_schema import ArangoSchemaReader
from querypilot.adapters.embeddings import LangChainEmbeddingAdapter
from querypilot.adapters.langchain_model import LangChainPlanningModel
from querypilot.adapters.milvus_catalog import MilvusCatalogIndex
from querypilot.application.aql_compiler import AqlCompiler
from querypilot.application.catalog_manifest import CatalogManifest, load_catalog_manifest
from querypilot.application.query_agent import QueryAgent
from querypilot.application.safe_aql import SafeAqlExecutor
from querypilot.application.schema_discovery import SchemaDiscovery, catalog_release_from_schema
from querypilot.application.semantic_catalog import SemanticCatalog
from querypilot.application.source_manager import PreparedSource, SourceManager
from querypilot.application.source_policy import SourceHostPolicy
from querypilot.config import Settings
from querypilot.domain.catalog import CatalogRelease
from querypilot.domain.models import QueryPolicy
from querypilot.domain.source import SchemaSnapshot, SourceConnection, SourceInfo


@dataclass(frozen=True, slots=True)
class AppContainer:
    agent: QueryAgent
    catalog: SemanticCatalog
    manifest: CatalogManifest
    sources: SourceManager


def build_container(settings: Settings) -> AppContainer:
    manifest = load_catalog_manifest(settings.catalog_path)
    planning_model = LangChainPlanningModel.for_dashscope(
        model=settings.model_name,
        api_key=settings.model_api_key,
        base_url=settings.model_base_url,
        timeout=settings.run_timeout_seconds,
        max_retries=2,
    )
    embeddings = LangChainEmbeddingAdapter.for_openai_compatible_api(
        model=settings.embedding_model_name,
        api_key=settings.model_api_key,
        base_url=settings.model_base_url,
    )
    catalog = SemanticCatalog(
        embeddings,
        MilvusCatalogIndex(
            uri=settings.milvus_uri,
            alias=settings.milvus_collection,
            token=settings.milvus_token.get_secret_value(),
        ),
    )
    source_host_policy = SourceHostPolicy.from_csv(
        settings.source_allowed_hosts,
        require_allowlist=settings.app_env.lower() == "production",
    )
    default_agent = _build_query_agent(
        settings=settings,
        catalog=catalog,
        planning_model=planning_model,
        url=settings.arango_url,
        database=settings.arango_database,
        username=settings.arango_username,
        password=settings.arango_password.get_secret_value(),
        allowed_collections=manifest.allowed_collections,
        allowed_graphs=manifest.allowed_graphs,
        schema=manifest.schema,
        schema_release=manifest.release,
    )
    initial_source = PreparedSource(
        info=SourceInfo(
            source_id=manifest.release.entries[0].source_id,
            url=settings.arango_url,
            database=settings.arango_database,
            release_id=manifest.release.release_id,
            collection_count=len(manifest.allowed_collections),
            field_count=sum(entry.kind == "field" for entry in manifest.release.entries),
            graph_count=len(manifest.allowed_graphs),
        ),
        schema=manifest.schema,
        release=manifest.release,
        agent=default_agent,
    )

    async def prepare_source(connection: SourceConnection) -> PreparedSource:
        source_host_policy.enforce(connection.url)
        snapshot = await SchemaDiscovery(
            ArangoSchemaReader(connection, timeout_seconds=settings.query_timeout_seconds)
        ).inspect(
            source_id=connection.source_id,
            database=connection.database,
            sample_size=connection.sample_size,
        )
        release = catalog_release_from_schema(snapshot)
        agent = _build_query_agent(
            settings=settings,
            catalog=catalog,
            planning_model=planning_model,
            url=connection.url,
            database=connection.database,
            username=connection.username,
            password=connection.password.get_secret_value(),
            allowed_collections={item.name for item in snapshot.collections},
            allowed_graphs={item.name for item in snapshot.graphs},
            schema=snapshot,
            schema_release=release,
        )
        return PreparedSource(
            info=SourceInfo(
                source_id=connection.source_id,
                url=connection.url,
                database=connection.database,
                release_id=release.release_id,
                collection_count=len(snapshot.collections),
                field_count=sum(len(item.fields) for item in snapshot.collections),
                graph_count=len(snapshot.graphs),
            ),
            schema=snapshot,
            release=release,
            agent=agent,
        )

    sources = SourceManager(catalog, prepare_source, initial_source)
    return AppContainer(
        agent=default_agent,
        catalog=catalog,
        manifest=manifest,
        sources=sources,
    )


def _build_query_agent(
    *,
    settings: Settings,
    catalog: SemanticCatalog,
    planning_model: LangChainPlanningModel,
    url: str,
    database: str,
    username: str,
    password: str,
    allowed_collections: set[str],
    allowed_graphs: set[str],
    schema: SchemaSnapshot,
    schema_release: CatalogRelease,
) -> QueryAgent:
    database_adapter = ArangoAqlDatabase(
        url=url,
        database=database,
        username=username,
        password=password,
        timeout_seconds=settings.query_timeout_seconds,
    )
    return QueryAgent(
        catalog=catalog,
        model=planning_model,
        compiler=AqlCompiler(),
        executor=SafeAqlExecutor(database_adapter),
        policy=QueryPolicy(
            allowed_collections=allowed_collections,
            allowed_graphs=allowed_graphs,
            result_limit=settings.result_limit_default,
            max_traversal_depth=3,
            max_estimated_cost=settings.max_estimated_cost,
        ),
        schema=schema,
        catalog_release=schema_release,
    )
