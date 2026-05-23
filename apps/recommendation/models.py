from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace

from shared.models import BaseModel


@dataclass(slots=True)
class RecommendationGraph(BaseModel):
    id: int | None = None
    client_id: int | None = None
    predicate: str | None = None
    object: str | None = None
    weight: float = 1.0
    _meta = SimpleNamespace(db_table="recommendation_graph")


@dataclass(slots=True)
class ClientEmbedding(BaseModel):
    client_id: int | None = None
    embedding: list[float] | None = None
    _meta = SimpleNamespace(db_table="client_embeddings")


@dataclass(slots=True)
class PropertyEmbedding(BaseModel):
    property_guid: str | None = None
    property_kind: str | None = None
    embedding: list[float] | None = None
    _meta = SimpleNamespace(db_table="property_embeddings")


EMBEDDING_DIM = 64
