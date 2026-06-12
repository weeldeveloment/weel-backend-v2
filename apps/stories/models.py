from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace

from shared.raw.choices import ChoiceEnum, build_choices
from shared.models import HardDeleteBaseModel, VerifiedByMixin


class MediaType(ChoiceEnum):
    IMAGE = "image"
    VIDEO = "video"


MediaType.choices = build_choices(MediaType)


@dataclass(slots=True)
class Story(HardDeleteBaseModel, VerifiedByMixin):
    id: int | None = None
    property_id: int | None = None
    expires_at: datetime | None = None
    views: int = 0
    _meta = SimpleNamespace(db_table="story")


@dataclass(slots=True)
class StoryMedia(HardDeleteBaseModel):
    id: int | None = None
    story_id: int | None = None
    media: str | None = None
    media_type: str = MediaType.IMAGE.value

    MediaType = MediaType
    _meta = SimpleNamespace(db_table="story_media")


@dataclass(slots=True)
class StoryView(HardDeleteBaseModel):
    id: int | None = None
    story_id: int | None = None
    client_id: int | None = None
    _meta = SimpleNamespace(db_table="story_view")


@dataclass(slots=True)
class Banner(BaseModel):
    id: int | None = None
    html_source: str = ""
    image: str = ""
    _meta = SimpleNamespace(db_table="banners")
