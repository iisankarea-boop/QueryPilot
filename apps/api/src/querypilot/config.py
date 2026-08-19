from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    log_level: str = "INFO"
    trust_proxy_headers: bool = False
    rate_limit_requests: int = Field(default=20, ge=1, le=1_000)
    rate_limit_window_seconds: int = Field(default=60, ge=1, le=3_600)
    readiness_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    source_allowed_hosts: str = ""

    model_api_key: SecretStr
    model_base_url: str
    model_name: str = "qwen-plus"
    embedding_model_name: str = "text-embedding-v4"

    arango_url: str = "http://localhost:8529"
    arango_database: str = "commerce"
    arango_username: str = "querypilot_reader"
    arango_password: SecretStr

    milvus_uri: str = "http://localhost:19530"
    milvus_token: SecretStr = SecretStr("")
    milvus_collection: str = "semantic_catalog_active"

    postgres_dsn: SecretStr
    catalog_path: Path = Path("catalog/commerce/catalog.yaml")
    query_timeout_seconds: float = Field(default=8.0, gt=0, le=60)
    run_timeout_seconds: float = Field(default=45.0, gt=0, le=300)
    result_limit_default: int = Field(default=100, ge=1, le=200)
    result_limit_max: int = Field(default=200, ge=1, le=1_000)
    max_estimated_cost: float = Field(default=10_000_000.0, gt=0)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
