import uuid
import logging

from django.utils import timezone

from core.celery import app

from .models import SanatoriumBooking
from .services import release_room_dates

logger = logging.getLogger(".sanatorium")


@app.task(
    name=".sanatorium.auto_cancel_booking",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def auto_cancel_sanatorium_booking(self, booking_id: str):
    """Auto-cancel a pending .sanatorium booking after timeout."""
    booking = (
        SanatoriumBooking.objects.select_related("room", "sanatorium")
        .filter(guid=booking_id)
        .first()
    )
    if not booking:
        logger.warning("auto_cancel: booking not found", extra={"booking_id": booking_id})
        return

    if booking.status != SanatoriumBooking.BookingStatus.PENDING:
        logger.info(
            "auto_cancel: skipped (status=%s)",
            booking.status,
            extra={"booking_id": booking_id},
        )
        return

    booking.status = SanatoriumBooking.BookingStatus.CANCELLED
    booking.cancellation_reason = SanatoriumBooking.BookingCancellationReason.SYSTEM_TIMEOUT
    booking.cancelled_at = timezone.now()
    booking.save(update_fields=["status", "cancellation_reason", "cancelled_at"])

    release_room_dates(booking.room, booking.check_in, booking.check_out)
    logger.info("auto_cancel: booking cancelled", extra={"booking_id": booking_id})


@app.task(
    name=".sanatorium.auto_complete_booking",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def auto_complete_sanatorium_booking(self, booking_id: str):
    """Auto-complete a confirmed booking on check-out day."""
    booking = (
        SanatoriumBooking.objects.select_related("room", "sanatorium")
        .filter(guid=booking_id)
        .first()
    )
    if not booking:
        logger.warning("auto_complete: booking not found", extra={"booking_id": booking_id})
        return

    if booking.status != SanatoriumBooking.BookingStatus.CONFIRMED:
        logger.info(
            "auto_complete: skipped (status=%s)",
            booking.status,
            extra={"booking_id": booking_id},
        )
        return

    today = timezone.localdate()
    if today < booking.check_out:
        logger.info(
            "auto_complete: skipped (not check-out day yet)",
            extra={"booking_id": booking_id, "check_out": str(booking.check_out)},
        )
        return

    booking.status = SanatoriumBooking.BookingStatus.COMPLETED
    booking.completed_at = timezone.now()
    booking.save(update_fields=["status", "completed_at"])
    logger.info("auto_complete: booking completed", extra={"booking_id": booking_id})


@app.task(name=".sanatorium.send_check_in_reminder")
def send_check_in_reminder():
    """Send reminder notifications 1 day before check-in for confirmed bookings."""
    from notification.models import Notification
    from notification.service import NotificationService

    tomorrow = timezone.localdate() + timezone.timedelta(days=1)
    bookings = SanatoriumBooking.objects.filter(
        status=SanatoriumBooking.BookingStatus.CONFIRMED,
        check_in=tomorrow,
        reminder_sent=False,
    ).select_related("client", "sanatorium")

    for booking in bookings:
        try:
            NotificationService.send_to_client(
                client=booking.client,
                title="Eslatma",
                message=f"Ertaga {booking.sanatorium.title} ga tashrif buyurasiz!",
                notification_type=Notification.NotificationType.REMINDER,
                data={"type": "sanatorium_reminder", "booking_id": str(booking.guid)},
            )
            booking.reminder_sent = True
            booking.save(update_fields=["reminder_sent"])
        except Exception as e:
            logger.error(
                "Failed to send reminder for booking %s: %s",
                booking.booking_number,
                e,
            )
