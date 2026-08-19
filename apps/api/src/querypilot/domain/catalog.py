from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CatalogKind = Literal["collection", "field", "edge", "metric", "example"]


class CatalogEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1, max_length=256)
    source_id: str = Field(min_length=1, max_length=128)
    kind: CatalogKind
    entity: str = Field(min_length=1, max_length=256)
    content: str = Field(min_length=1, max_length=8_192)
    aliases: tuple[str, ...] = ()
    approved: bool = True


class CatalogRelease(BaseModel):
    model_config = ConfigDict(frozen=True)

    release_id: str = Field(min_length=1, max_length=128)
    entries: tuple[CatalogEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def ids_are_unique(self) -> "CatalogRelease":
        ids = [entry.id for entry in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("catalog entry ids must be unique")
        return self


class ContextRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    question: str = Field(min_length=1, max_length=4_000)
    source_id: str = Field(min_length=1, max_length=128)
    limit: int = Field(default=8, ge=1, le=30)


@dataclass(frozen=True, slots=True)
class IndexedCatalogEntry:
    id: str
    release_id: str
    source_id: str
    kind: CatalogKind
    entity: str
    content: str
    approved: bool
    embedding: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class CatalogEvidence:
    id: str
    kind: CatalogKind
    entity: str
    content: str


@dataclass(frozen=True, slots=True)
class ContextPack:
    release_id: str
    evidence: tuple[CatalogEvidence, ...]


@dataclass(frozen=True, slots=True)
class PublishReport:
    release_id: str
    published_entries: int
