import logging
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from rest_framework.exceptions import ValidationError

from .models import (
    Sanatorium,
    SanatoriumRoom,
    SanatoriumRoomPrice,
    RoomCalendarDate,
    PackageType,
    Treatment,
    MedicalSpecialization,
    SanatoriumBooking,
    SanatoriumBookingPrice,
)

logger = logging.getLogger("..sanatorium")

SERVICE_FEE_PERCENTAGE = 20


def calculate_booking_price(room: SanatoriumRoom, package_type: PackageType) -> dict:
    """Calculate total and hold amounts for a ..sanatorium booking.

    Payment flow from Figma:
      1. 20% hold upfront
      2. After check-in, 10% returned
      3. Commission is 10%, guest pays remaining 90% to owner
    """
    room_price = SanatoriumRoomPrice.objects.filter(
        room=room, package_type=package_type
    ).first()

    if not room_price:
        raise ValidationError(
            _("Price is not set for this room and package combination")
        )

    subtotal = room_price.price
    hold_percentage = Decimal("0.20")
    hold_amount = (subtotal * hold_percentage).quantize(Decimal("0.01"))
    service_fee = (subtotal * Decimal("0.10")).quantize(Decimal("0.01"))
    charge_amount = subtotal - hold_amount

    return {
        "subtotal": subtotal,
        "hold_amount": hold_amount,
        "charge_amount": charge_amount,
        "service_fee": service_fee,
        "service_fee_percentage": SERVICE_FEE_PERCENTAGE,
        "currency": room_price.currency,
    }


def check_room_availability(
    room: SanatoriumRoom,
    check_in,
    package_type: PackageType,
) -> bool:
    """Check if the room is available for the date range."""
    check_out = check_in + timedelta(days=package_type.duration_days)

    booked_or_blocked = RoomCalendarDate.objects.filter(
        room=room,
        date__gte=check_in,
        date__lt=check_out,
        status__in=[
            RoomCalendarDate.CalendarStatus.BOOKED,
            RoomCalendarDate.CalendarStatus.BLOCKED,
        ],
    ).exists()

    return not booked_or_blocked


def mark_room_dates_booked(room: SanatoriumRoom, check_in, check_out):
    """Mark calendar dates as booked for the given range."""
    dates_to_book = []
    current = check_in
    while current < check_out:
        dates_to_book.append(
            RoomCalendarDate(
                room=room,
                date=current,
                status=RoomCalendarDate.CalendarStatus.BOOKED,
            )
        )
        current += timedelta(days=1)

    RoomCalendarDate.objects.bulk_create(
        dates_to_book,
        update_conflicts=True,
        update_fields=["status"],
        unique_fields=["room", "date"],
    )


def release_room_dates(room: SanatoriumRoom, check_in, check_out):
    """Release calendar dates back to available."""
    RoomCalendarDate.objects.filter(
        room=room,
        date__gte=check_in,
        date__lt=check_out,
        status=RoomCalendarDate.CalendarStatus.BOOKED,
    ).update(status=RoomCalendarDate.CalendarStatus.AVAILABLE)


@transaction.atomic
def create_sanatorium_booking(
    client,
    sanatorium: Sanatorium,
    room: SanatoriumRoom,
    package_type: PackageType,
    check_in,
    treatment=None,
    specialization=None,
    card_id=None,
) -> SanatoriumBooking:
    """Full booking creation with price calculation and calendar update."""
    check_out = check_in + timedelta(days=package_type.duration_days)

    if not check_room_availability(room, check_in, package_type):
        raise ValidationError(
            _("This room is not available for the selected dates")
        )

    today = timezone.localdate()
    if check_in < today:
        raise ValidationError(_("Check-in date cannot be in the past"))

    max_advance = today + timedelta(days=30)
    if check_in > max_advance:
        raise ValidationError(
            _("Advance booking is available up to 1 month ahead")
        )

    price_data = calculate_booking_price(room, package_type)

    booking = SanatoriumBooking.objects.create(
        client=client,
        sanatorium=sanatorium,
        room=room,
        treatment=treatment,
        specialization=specialization,
        package_type=package_type,
        check_in=check_in,
        check_out=check_out,
    )

    SanatoriumBookingPrice.objects.create(
        booking=booking,
        subtotal=price_data["subtotal"],
        hold_amount=price_data["hold_amount"],
        charge_amount=price_data["charge_amount"],
        service_fee=price_data["service_fee"],
        service_fee_percentage=price_data["service_fee_percentage"],
    )

    mark_room_dates_booked(room, check_in, check_out)

    logger.info(
        "Sanatorium booking created: %s for room %s (%s → %s)",
        booking.booking_number,
        room.guid,
        check_in,
        check_out,
    )

    return booking
