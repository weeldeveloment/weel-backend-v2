from datetime import timedelta

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import Booking


def _get_cancellation_window(booking: Booking) -> timedelta | None:
    today = timezone.localdate()

    if booking.check_in <= today:
        """The client can't cancel the booking, if check-in is scheduled for today"""
        return None

    if booking.check_in == today + timedelta(days=1):
        """The client can cancel within 1 hours, after 1 hour the client can't"""
        return timedelta(hours=1)

    # on other days, after 6 hours
    return timedelta(hours=6)


def _is_cancellation_expired(booking: Booking) -> bool:
    window = _get_cancellation_window(booking)

    if window is None:
        return True

    return timezone.now() - booking.created_at > window


def get_cancellation_error_message(booking: Booking) -> str:
    window = _get_cancellation_window(booking)
    if window is None:
        return _("This booking can't be cancelled because check-in is today")

    hours = int(window.total_seconds() // 3600)
    if hours == 1:
        return _("You can cancel this booking only within 1 hour after booking")

    return _(
        "You can cancel this booking only within %(hours)s hours after booking"
    ) % {"hours": hours}


def client_can_cancel(booking: Booking):
    if booking.status not in {
        Booking.BookingStatus.PENDING,
        Booking.BookingStatus.CONFIRMED,
    }:
        return False

    if _is_cancellation_expired(booking):
        return False

    return True
