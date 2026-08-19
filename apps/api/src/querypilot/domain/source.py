from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class SourceConnection(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    url: str = Field(min_length=1, max_length=2_048)
    database: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=128)
    password: SecretStr
    sample_size: int = Field(default=50, ge=1, le=200)

    @field_validator("url")
    @classmethod
    def url_is_an_http_endpoint_without_credentials(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("url must be an http or https endpoint")
        if parsed.username or parsed.password:
            raise ValueError("url must not contain credentials")
        return value.rstrip("/")


class DiscoveredField(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=256)
    inferred_type: str = Field(min_length=1, max_length=128)


class DiscoveredCollection(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=256)
    kind: Literal["document", "edge"]
    fields: tuple[DiscoveredField, ...]
    sampled_documents: int = Field(ge=0)


class DiscoveredEdge(BaseModel):
    model_config = ConfigDict(frozen=True)

    collection: str = Field(min_length=1, max_length=256)
    from_collections: tuple[str, ...]
    to_collections: tuple[str, ...]


class DiscoveredGraph(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=256)
    edges: tuple[DiscoveredEdge, ...]


class SchemaSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str = Field(min_length=1, max_length=128)
    database: str = Field(min_length=1, max_length=128)
    collections: tuple[DiscoveredCollection, ...]
    graphs: tuple[DiscoveredGraph, ...]


class SourceInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str
    url: str
    database: str
    release_id: str
    collection_count: int = Field(ge=0)
    field_count: int = Field(ge=0)
    graph_count: int = Field(ge=0)
    status: Literal["ready"] = "ready"
