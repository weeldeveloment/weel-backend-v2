from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from uuid import UUID, uuid4


@dataclass
class BaseModel:
    guid: UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    _meta = SimpleNamespace(db_table="")

    def __post_init__(self):
        if self.guid is None:
            self.guid = uuid4()


class HardDeleteBaseModel(BaseModel):
    pass


@dataclass
class VerifiedByMixin:
    is_verified: bool | None = False
    verified_by_user_id: int | None = None
    verified_at: datetime | None = None
