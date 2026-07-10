from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

from shared.models import HardDeleteBaseModel


class TripStatus:
    DRAFT = "draft"
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

    CHOICES = [DRAFT, PENDING, ACTIVE, COMPLETED, CANCELLED]


class TripEmployeeStatus:
    INVITED = "invited"
    CONFIRMED = "confirmed"
    CHECKED_IN = "checked_in"
    CHECKED_OUT = "checked_out"
    CANCELLED = "cancelled"

    CHOICES = [INVITED, CONFIRMED, CHECKED_IN, CHECKED_OUT, CANCELLED]


class BudgetRequestStatus:
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"

    CHOICES = [PENDING, APPROVED, REJECTED]


class B2BUserRole:
    OWNER = "owner"
    PERFORMER = "performer"

    CHOICES = [OWNER, PERFORMER]


class EmployeeStatus:
    AVAILABLE = "available"
    ON_TRIP = "on_trip"
    BLOCKED = "blocked"

    CHOICES = [AVAILABLE, ON_TRIP, BLOCKED]


class HotelBookingRequestStatus:
    """Status of a whole multi-room hotel booking request (the group)."""
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"

    CHOICES = [PENDING, CONFIRMED, REJECTED]


@dataclass(slots=True)
class B2BCompany(HardDeleteBaseModel):
    id: int | None = None
    name: str | None = None
    legal_name: str | None = None
    inn: str | None = None
    city: str | None = None
    district: str | None = None
    legal_address: str | None = None
    industry: str | None = None
    employee_count: int | None = None
    is_active: bool = True
    _meta = SimpleNamespace(db_table="b2b_company")


@dataclass(slots=True)
class B2BUser(HardDeleteBaseModel):
    id: int | None = None
    company_id: int | None = None
    phone: str | None = None
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    role: str = B2BUserRole.PERFORMER
    is_active: bool = True
    _meta = SimpleNamespace(db_table="b2b_user")


@dataclass(slots=True)
class B2BUserSession(HardDeleteBaseModel):
    id: int | None = None
    user_id: int | None = None
    token: str | None = None
    expires_at: datetime | None = None
    _meta = SimpleNamespace(db_table="b2b_user_session")


@dataclass(slots=True)
class B2BDepartment(HardDeleteBaseModel):
    id: int | None = None
    company_id: int | None = None
    name: str | None = None
    _meta = SimpleNamespace(db_table="b2b_department")


@dataclass(slots=True)
class B2BEmployee(HardDeleteBaseModel):
    id: int | None = None
    company_id: int | None = None
    department_id: int | None = None
    full_name: str | None = None
    position: str | None = None
    email: str | None = None
    phone: str | None = None
    date_of_birth: date | None = None
    passport_series: str | None = None
    passport_number: str | None = None
    passport_upload: str | None = None
    pinfl: str | None = None
    individual_limit: Decimal | None = None
    status: str = EmployeeStatus.AVAILABLE
    is_active: bool = True
    _meta = SimpleNamespace(db_table="b2b_employee")


@dataclass(slots=True)
class HotelBookingRequest(HardDeleteBaseModel):
    """One executer action: pick a hotel + dates + N rooms + employees.

    Hotels live in per-organization tenant schemas (see
    apps/property/hotel_repository.py), so this row stores a plain
    ``tenant_schema``/``hotel_property_id`` split — there is no cross-schema
    FK to ``pms_property``. The actual per-room ``pms_booking`` rows live in
    that hotel's own schema; this table is the "group" that makes the whole
    multi-room request show up as ONE entry in booking history.
    """
    id: int | None = None
    company_id: int | None = None
    trip_id: int | None = None
    tenant_schema: str | None = None
    hotel_property_id: int | None = None
    hotel_name: str | None = None
    check_in: date | None = None
    check_out: date | None = None
    status: str = HotelBookingRequestStatus.PENDING
    requested_by: int | None = None
    reviewed_at: datetime | None = None
    _meta = SimpleNamespace(db_table="b2b_hotel_booking_request")


@dataclass(slots=True)
class HotelBookingRoom(HardDeleteBaseModel):
    id: int | None = None
    booking_request_id: int | None = None
    room_id: int | None = None
    room_name: str | None = None
    pms_booking_id: int | None = None
    price_per_night: Decimal | None = None
    total_price: Decimal | None = None
    _meta = SimpleNamespace(db_table="b2b_hotel_booking_room")


@dataclass(slots=True)
class HotelBookingRoomEmployee(HardDeleteBaseModel):
    id: int | None = None
    booking_room_id: int | None = None
    employee_id: int | None = None
    _meta = SimpleNamespace(db_table="b2b_hotel_booking_room_employee")


@dataclass(slots=True)
class BusinessTrip(HardDeleteBaseModel):
    id: int | None = None
    company_id: int | None = None
    name: str | None = None
    destination_city: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    budget: Decimal | None = None
    status: str = TripStatus.DRAFT
    created_by: int | None = None
    notes: str | None = None
    _meta = SimpleNamespace(db_table="b2b_business_trip")


@dataclass(slots=True)
class TripEmployee(HardDeleteBaseModel):
    id: int | None = None
    trip_id: int | None = None
    employee_id: int | None = None
    property_id: int | None = None
    room_id: int | None = None
    check_in: date | None = None
    check_out: date | None = None
    pms_booking_id: int | None = None
    status: str = TripEmployeeStatus.INVITED
    _meta = SimpleNamespace(db_table="b2b_trip_employee")


@dataclass(slots=True)
class TravelPolicy(HardDeleteBaseModel):
    id: int | None = None
    company_id: int | None = None
    budget_per_trip: Decimal | None = None
    monthly_budget: Decimal | None = None
    allowed_star_ratings: list = field(default_factory=list)
    allowed_weel_classifications: list = field(default_factory=list)
    blacklisted_properties: list = field(default_factory=list)
    preferred_properties: list = field(default_factory=list)
    _meta = SimpleNamespace(db_table="b2b_travel_policy")


@dataclass(slots=True)
class TravelPolicyRule(HardDeleteBaseModel):
    id: int | None = None
    policy_id: int | None = None
    applies_to: str = "all"
    target_id: int | None = None
    budget_limit: Decimal | None = None
    _meta = SimpleNamespace(db_table="b2b_travel_policy_rule")


@dataclass(slots=True)
class BudgetRequest(HardDeleteBaseModel):
    id: int | None = None
    trip_id: int | None = None
    employee_id: int | None = None
    department_id: int | None = None
    requested_by: int | None = None
    amount: Decimal | None = None
    description: str | None = None
    status: str = BudgetRequestStatus.PENDING
    reviewed_by: int | None = None
    reviewed_at: datetime | None = None
    review_description: str | None = None
    _meta = SimpleNamespace(db_table="b2b_budget_request")


@dataclass(slots=True)
class TravelVoucher(HardDeleteBaseModel):
    id: int | None = None
    trip_id: int | None = None
    voucher_number: str | None = None
    pdf_url: str | None = None
    generated_at: datetime | None = None
    _meta = SimpleNamespace(db_table="b2b_travel_voucher")
