from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

from shared.models import BaseModel, HardDeleteBaseModel


class PropertyTheme:
    BEACH = "beach"
    SKI = "ski"
    CITY = "city"
    COUNTRYSIDE = "countryside"
    LAKEFRONT = "lakefront"
    MOUNTAIN = "mountain"
    BOUTIQUE = "boutique"
    BUSINESS = "business"
    HISTORIC = "historic"
    NATURE = "nature"
    SPA = "spa"
    FAMILY = "family"

    CHOICES = [BEACH, SKI, CITY, COUNTRYSIDE, LAKEFRONT, MOUNTAIN, BOUTIQUE, BUSINESS, HISTORIC, NATURE, SPA, FAMILY]


class WeelClassification:
    STANDARD = "standard"
    ESSENTIAL = "essential"
    COMFORT = "comfort"
    COMFORT_PLUS = "comfort_plus"
    BUSINESS = "business"
    PREMIUM = "premium"
    SIGNATURE = "signature"

    CHOICES = [STANDARD, ESSENTIAL, COMFORT, COMFORT_PLUS, BUSINESS, PREMIUM, SIGNATURE]


class RoomTypePreset:
    STANDARD = "standard"
    SUPERIOR = "superior"
    DELUXE = "deluxe"
    SUITE = "suite"
    STUDIO = "studio"
    APARTMENT = "apartment"
    FAMILY = "family"
    DORMITORY = "dormitory"
    CUSTOM = "custom"

    CHOICES = [STANDARD, SUPERIOR, DELUXE, SUITE, STUDIO, APARTMENT, FAMILY, DORMITORY, CUSTOM]


@dataclass(slots=True)
class RoomType(HardDeleteBaseModel):
    id: int | None = None
    property_id: int | None = None
    preset: str | None = None
    custom_name: str | None = None
    name: str | None = None
    description: str | None = None
    base_rate: Decimal | None = None
    capacity: int = 2
    amenities: list = field(default_factory=list)
    photos: list = field(default_factory=list)
    is_active: bool = True
    _meta = SimpleNamespace(db_table="pms_room_type")


class BedType:
    SINGLE = "single"
    TWIN = "twin"
    DOUBLE = "double"
    KING = "king"
    SOFA_BED = "sofa_bed"
    BUNK_BED = "bunk_bed"
    KIDS_BED = "kids_bed"
    EXTRA_BED = "extra_bed"

    CHOICES = [SINGLE, TWIN, DOUBLE, KING, SOFA_BED, BUNK_BED, KIDS_BED, EXTRA_BED]


class BookingStatus:
    NEW = "new"
    CONFIRMED = "confirmed"
    CHECKED_IN = "checked_in"
    CHECKED_OUT = "checked_out"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"

    CHOICES = [NEW, CONFIRMED, CHECKED_IN, CHECKED_OUT, CANCELLED, NO_SHOW]


class CalendarStatus:
    AVAILABLE = "available"
    BLOCKED = "blocked"
    OCCUPIED = "occupied"
    HELD = "held"

    CHOICES = [AVAILABLE, BLOCKED, OCCUPIED, HELD]


class RoomCondition:
    CLEAN = "clean"
    DIRTY = "dirty"
    INSPECTION = "inspection"
    MAINTENANCE = "maintenance"

    CHOICES = [CLEAN, DIRTY, INSPECTION, MAINTENANCE]


class RoomAvailability:
    AVAILABLE = "available"
    OCCUPIED = "occupied"
    BLOCKED = "blocked"

    CHOICES = [AVAILABLE, OCCUPIED, BLOCKED]


class MealPlan:
    RO = "RO"
    BB = "BB"
    HB = "HB"
    FB = "FB"
    AI = "AI"
    UAI = "UAI"

    CHOICES = [RO, BB, HB, FB, AI, UAI]


class BookingSource:
    DIRECT = "direct"
    OTA = "ota"
    B2B = "b2b"
    WALK_IN = "walk_in"

    CHOICES = [DIRECT, OTA, B2B, WALK_IN]


class PaymentStatus:
    PENDING = "pending"
    PAID = "paid"
    PARTIAL = "partial"
    REFUNDED = "refunded"

    CHOICES = [PENDING, PAID, PARTIAL, REFUNDED]


@dataclass(slots=True)
class Property(HardDeleteBaseModel):
    id: int | None = None
    organization_id: int | None = None
    partner_user_id: int | None = None
    name: str | None = None
    description_uz: str | None = None
    description_ru: str | None = None
    description_en: str | None = None
    address: str | None = None
    full_address: str | None = None
    city: str | None = None
    country: str | None = "UZ"
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    star_rating: int | None = None
    weel_classification: str | None = None
    themes: list = field(default_factory=list)
    amenities: list = field(default_factory=list)
    legal_info: dict = field(default_factory=dict)
    check_in_time: time | None = None
    check_out_time: time | None = None
    cancellation_policy: str | None = None
    quiet_hours: bool = True
    alcohol_allowed: bool = True
    pets_allowed: bool = False
    timezone: str = "Asia/Tashkent"
    photos: list = field(default_factory=list)
    is_active: bool = True
    is_testing: bool = False
    is_verified: bool = False
    verification_status: str = "waiting"
    _meta = SimpleNamespace(db_table="pms_property")


@dataclass(slots=True)
class PropertyImage(HardDeleteBaseModel):
    id: int | None = None
    property_id: int | None = None
    image_url: str | None = None
    order: int = 0
    _meta = SimpleNamespace(db_table="pms_property_image")


@dataclass(slots=True)
class Room(HardDeleteBaseModel):
    id: int | None = None
    property_id: int | None = None
    room_type_id: int | None = None
    room_type_name: str | None = None
    room_type_preset: str | None = None
    room_number: str | None = None
    display_name: str | None = None
    floor: int = 1
    area: Decimal | None = None
    bedroom_count: int = 1
    beds: list = field(default_factory=list)
    amenities: list = field(default_factory=list)
    photos: list = field(default_factory=list)
    condition: str = RoomCondition.CLEAN
    availability: str = RoomAvailability.AVAILABLE
    capacity: int = 2
    meal_plan: str = MealPlan.BB
    is_active: bool = True
    _meta = SimpleNamespace(db_table="pms_room")


@dataclass(slots=True)
class CalendarSlot(HardDeleteBaseModel):
    id: int | None = None
    room_id: int | None = None
    date: date | None = None
    status: str = CalendarStatus.AVAILABLE
    hold_expires_at: datetime | None = None
    _meta = SimpleNamespace(db_table="pms_calendar_slot")


@dataclass(slots=True)
class Guest(HardDeleteBaseModel):
    id: int | None = None
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None
    id_document: dict = field(default_factory=dict)
    preferences: dict = field(default_factory=dict)
    is_vip: bool = False
    is_blacklisted: bool = False
    notes: str | None = None
    _meta = SimpleNamespace(db_table="pms_guest")


@dataclass(slots=True)
class Booking(HardDeleteBaseModel):
    id: int | None = None
    property_id: int | None = None
    room_id: int | None = None
    guest_id: int | None = None
    booking_number: str | None = None
    check_in: date | None = None
    check_out: date | None = None
    status: str = BookingStatus.NEW
    source: str = BookingSource.DIRECT
    meal_plan: str = MealPlan.RO
    adult_count: int = 1
    child_count: int = 0
    rate: Decimal | None = None
    currency: str = "USD"
    payment_status: str = PaymentStatus.PENDING
    total_cost: Decimal | None = None
    hold_amount: Decimal | None = None
    confirmed_at: datetime | None = None
    confirmation_deadline: datetime | None = None
    b2b_company_id: int | None = None
    voucher_number: str | None = None
    notes: str | None = None
    created_by: int | None = None
    external_provider: str | None = None
    external_reservation_id: str | None = None
    external_room_id: str | None = None
    external_payload_ref: dict = field(default_factory=dict)
    imported_at: datetime | None = None
    last_synced_at: datetime | None = None
    _meta = SimpleNamespace(db_table="pms_booking")


@dataclass(slots=True)
class BookingHistory(HardDeleteBaseModel):
    id: int | None = None
    booking_id: int | None = None
    action: str | None = None
    previous_value: dict = field(default_factory=dict)
    new_value: dict = field(default_factory=dict)
    user_id: int | None = None
    _meta = SimpleNamespace(db_table="pms_booking_history")


@dataclass(slots=True)
class Review(HardDeleteBaseModel):
    id: int | None = None
    property_id: int | None = None
    booking_id: int | None = None
    guest_name: str | None = None
    rating: Decimal | None = None
    categories: dict = field(default_factory=dict)
    text: str | None = None
    hotel_response: str | None = None
    response_date: datetime | None = None
    is_complained: bool = False
    complaint_reason: str | None = None
    _meta = SimpleNamespace(db_table="pms_review")
