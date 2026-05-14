from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from decimal import Decimal
from types import SimpleNamespace

from shared.raw.choices import ChoiceEnum, build_choices
from shared.models import BaseModel, HardDeleteBaseModel, VerifiedByMixin


class VerificationStatus(ChoiceEnum):
    WAITING = "waiting"
    ACCEPTED = "accepted"
    CANCELLED = "cancelled"


VerificationStatus.choices = build_choices(VerificationStatus)


@dataclass(slots=True)
class PropertyType(BaseModel):
    id: int | None = None
    title_en: str | None = None
    title_ru: str | None = None
    title_uz: str | None = None
    _meta = SimpleNamespace(db_table="property_type")


@dataclass(slots=True)
class PropertyService(BaseModel):
    id: int | None = None
    property_type_id: int | None = None
    title_en: str | None = None
    title_ru: str | None = None
    title_uz: str | None = None
    _meta = SimpleNamespace(db_table="property_service")


@dataclass(slots=True)
class Property(HardDeleteBaseModel, VerifiedByMixin):
    id: int | None = None
    title: str | None = None
    partner_user_id: int | None = None
    verification_status: str = VerificationStatus.WAITING.value
    is_archived: bool = False
    is_recommended: bool = False
    currency: str | None = None
    img: str | None = None
    price: Decimal | None = None
    property_services: list = field(default_factory=list)
    categories: list = field(default_factory=list)
    property_type: PropertyType | None = None
    check_in: time | None = None
    check_out: time | None = None

    _meta = SimpleNamespace(db_table="property")


@dataclass(slots=True)
class PropertyPrice(HardDeleteBaseModel):
    id: int | None = None
    property_id: int | None = None
    month_from: object | None = None
    month_to: object | None = None
    price_per_person: Decimal | None = None
    price_on_working_days: Decimal | None = None
    price_on_weekends: Decimal | None = None
    _meta = SimpleNamespace(db_table="property_price")


@dataclass(slots=True)
class PropertyDetail(HardDeleteBaseModel):
    id: int | None = None
    property_id: int | None = None
    _meta = SimpleNamespace(db_table="property_detail")


@dataclass(slots=True)
class PropertyLocation(HardDeleteBaseModel):
    id: int | None = None
    property_id: int | None = None
    _meta = SimpleNamespace(db_table="property_location")


@dataclass(slots=True)
class PropertyImage(HardDeleteBaseModel):
    id: int | None = None
    property_id: int | None = None
    _meta = SimpleNamespace(db_table="property_image")

