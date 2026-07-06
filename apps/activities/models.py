from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import Decimal
from types import SimpleNamespace

from shared.raw.choices import ChoiceEnum, build_choices
from shared.models import HardDeleteBaseModel


class ActivityCategory(ChoiceEnum):
    EXTREME = "extreme"
    RELAX = "relax"
    WATER = "water"
    LAND = "land"


class ResourceStatus(ChoiceEnum):
    ACTIVE = "active"
    MAINTENANCE = "maintenance"
    BROKEN = "broken"


class ActivityBookingStatus(ChoiceEnum):
    PENDING_PAYMENT = "pending_payment"
    CONFIRMED = "confirmed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


ActivityCategory.choices = build_choices(ActivityCategory)
ResourceStatus.choices = build_choices(ResourceStatus)
ActivityBookingStatus.choices = build_choices(ActivityBookingStatus)

# Applied when a partner doesn't set a buffer explicitly on the activity.
DEFAULT_BUFFER_MINUTES = 10


@dataclass(slots=True)
class Activity(HardDeleteBaseModel):
    id: int | None = None
    partner_user_id: int | None = None
    name: str | None = None
    description: str | None = None
    category: str = ActivityCategory.EXTREME.value
    address: str | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    buffer_minutes: int = DEFAULT_BUFFER_MINUTES
    requires_prepayment: bool = True
    is_active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None

    ActivityCategory = ActivityCategory
    _meta = SimpleNamespace(db_table="activity")


@dataclass(slots=True)
class ActivityImage(HardDeleteBaseModel):
    id: int | None = None
    activity_id: int | None = None
    image_url: str | None = None
    sort_order: int = 0
    _meta = SimpleNamespace(db_table="activity_image")


@dataclass(slots=True)
class ActivityWorkingHours(HardDeleteBaseModel):
    id: int | None = None
    activity_id: int | None = None
    weekday: int | None = None  # 0=Monday ... 6=Sunday (ISO, Python date.weekday())
    is_day_off: bool = False
    opens_at: time | None = None
    closes_at: time | None = None
    _meta = SimpleNamespace(db_table="activity_working_hours")


@dataclass(slots=True)
class Tariff(HardDeleteBaseModel):
    id: int | None = None
    activity_id: int | None = None
    name: str | None = None
    duration_minutes: int | None = None
    price: Decimal | None = None
    min_age: int | None = None
    max_age: int | None = None
    min_weight_kg: int | None = None
    max_weight_kg: int | None = None
    is_active: bool = True
    _meta = SimpleNamespace(db_table="tariff")


@dataclass(slots=True)
class Resource(HardDeleteBaseModel):
    id: int | None = None
    activity_id: int | None = None
    label: str | None = None
    status: str = ResourceStatus.ACTIVE.value

    ResourceStatus = ResourceStatus
    _meta = SimpleNamespace(db_table="resource")


@dataclass(slots=True)
class ActivityBooking(HardDeleteBaseModel):
    id: int | None = None
    activity_id: int | None = None
    tariff_id: int | None = None
    resource_id: int | None = None
    client_user_id: int | None = None
    guest_phone: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    buffer_minutes_snapshot: int = 0
    blocked_until: datetime | None = None
    price_snapshot: Decimal | None = None
    status: str = ActivityBookingStatus.PENDING_PAYMENT.value
    cancellation_reason: str | None = None

    ActivityBookingStatus = ActivityBookingStatus
    _meta = SimpleNamespace(db_table="activity_booking")
