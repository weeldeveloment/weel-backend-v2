from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

from shared.raw.choices import ChoiceEnum, build_choices
from shared.models import HardDeleteBaseModel


class BookingStatus(ChoiceEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class BookingCancellationReason(ChoiceEnum):
    USER_CANCELLED = "user_cancelled"
    PARTNER_CANCELLED = "partner_cancelled"
    USER_NO_SHOW = "user_no_show"


class CalendarStatus(ChoiceEnum):
    AVAILABLE = "available"
    BOOKED = "booked"
    BLOCKED = "blocked"
    HELD = "held"


BookingStatus.choices = build_choices(BookingStatus)
BookingCancellationReason.choices = build_choices(BookingCancellationReason)
CalendarStatus.choices = build_choices(CalendarStatus)


@dataclass(slots=True)
class Booking(HardDeleteBaseModel):
    id: int | None = None
    client_user_id: int | None = None
    booking_number: str | None = None
    check_in: date | None = None
    check_out: date | None = None
    status: str = BookingStatus.PENDING.value
    cancellation_reason: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    BookingStatus = BookingStatus
    BookingCancellationReason = BookingCancellationReason
    _meta = SimpleNamespace(db_table="booking")


@dataclass(slots=True)
class BookingPrice(HardDeleteBaseModel):
    booking_id: int | None = None
    subtotal: Decimal | None = None
    hold_amount: Decimal | None = None
    charge_amount: Decimal | None = None
    _meta = SimpleNamespace(db_table="booking_price")


@dataclass(slots=True)
class BookingTransaction(HardDeleteBaseModel):
    booking_id: int | None = None
    plum_transaction_id: int | None = None
    _meta = SimpleNamespace(db_table="booking_transaction")


@dataclass(slots=True)
class CalendarDate(HardDeleteBaseModel):
    id: int | None = None
    date: date | None = None
    status: str = CalendarStatus.AVAILABLE.value

    CalendarStatus = CalendarStatus
    _meta = SimpleNamespace(db_table="calendar_date")
