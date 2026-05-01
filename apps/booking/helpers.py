from datetime import timedelta

from django.utils import timezone
from django.utils.translation import gettext_lazy as _


CLIENT_CANCELLABLE_STATUSES = {"pending", "confirmed"}


def _get_cancellation_window(_booking) -> timedelta:
    # Within this duration after `created_at`, the client may cancel; after that, they may not.
    return timedelta(minutes=30)


def _is_cancellation_expired(booking) -> bool:
    window = _get_cancellation_window(booking)
    return timezone.now() - booking.created_at > window


def get_cancellation_error_message(booking) -> str:
    window = _get_cancellation_window(booking)
    minutes = int(window.total_seconds() // 60)
    return _(
        "You can cancel this booking only within %(minutes)s minutes after booking"
    ) % {"minutes": minutes}


def client_can_cancel(booking):
    if str(getattr(booking, "status", "")).lower() not in CLIENT_CANCELLABLE_STATUSES:
        return False

    if _is_cancellation_expired(booking):
        return False

    return True
