import uuid
import logging
from datetime import timedelta

from django.utils import timezone

from core.celery import app

from .models import Booking
from .services import BookingService

logger = logging.getLogger(__name__)

PAYMENT_DEADLINE_MINUTES = 30


@app.task(
    name="booking.auto_cancel",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def auto_cancel_booking(self, booking_id: uuid.UUID):
    booking = (
        Booking.objects.select_related("client", "property")
        .filter(guid=booking_id)
        .first()
    )
    if not booking:
        logger.warning(
            "auto_cancel: booking not found", extra={"booking_id": booking_id}
        )
        return
    if booking.status != Booking.BookingStatus.PENDING:
        logger.info(
            "auto_cancel: skipped",
            extra={"booking_id": booking_id, "status": booking.status},
        )
        return

    logger.info("auto_cancel: triggered", extra={"booking_id": booking_id})

    booking_service = BookingService(client=booking.client, property=booking.property)
    booking_service.system_cancel_booking(booking)
    logger.info(
        "auto_cancel: booking cancelled successfully", extra={"booking_id": booking_id}
    )


@app.task(
    name="booking.auto_complete",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def auto_complete_booking(self, booking_id: uuid.UUID):
    booking = (
        Booking.objects.select_related("client", "property")
        .filter(guid=booking_id)
        .first()
    )
    if not booking:
        logger.warning(
            "auto_complete: booking not found", extra={"booking_id": booking_id}
        )
        return
    if booking.status != Booking.BookingStatus.CONFIRMED:
        logger.info(
            "auto_complete: skipped",
            extra={"booking_id": booking_id, "status": booking.status},
        )
        return

    today = timezone.localdate()
    if booking.check_in != today:
        logger.info(
            "auto_complete: skipped(not check-in day)",
            extra={"booking_id": booking_id, "check_in": str(booking.check_in)},
        )
        return

    logger.info("auto_complete triggered", extra={"booking_id": booking_id})

    booking_service = BookingService(client=booking.client, property=booking.property)
    booking_service.system_complete_booking(booking)
    logger.info(
        "auto_complete: booking completed successfully",
        extra={"booking_id": booking_id},
    )


@app.task(
    name="booking.tasks.send_pending_booking_payment_reminders",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def send_pending_booking_payment_reminders(self):
    """
    Sends reminders to clients with PENDING bookings: time left to complete payment.
    If they do not pay in time, the property will be released and available for others.
    Sends at ~24 min, ~6 min, ~1 min left (each stage once per booking).
    """
    from notification.models import Notification
    from notification.service import NotificationService

    now = timezone.now()
    deadline_delta = timedelta(minutes=PAYMENT_DEADLINE_MINUTES)

    pending = (
        Booking.objects.filter(status=Booking.BookingStatus.PENDING)
        .select_related("client", "property")
        .order_by("created_at")
    )

    for booking in pending:
        deadline = booking.created_at + deadline_delta
        if now >= deadline:
            continue
        minutes_left = (deadline - now).total_seconds() / 60.0
        stage = (booking.payment_reminder_stage or "").strip()

        if 24 >= minutes_left > 6 and stage != "24m":
            NotificationService.send_to_client(
                client=booking.client,
                title="Payment reminder",
                message=(
                    f"You have about 24 minutes left to complete payment for "
                    f"{booking.property.title}. Otherwise the property will be released "
                    f"and available for others."
                ),
                notification_type=Notification.NotificationType.SYSTEM,
                data={"booking_id": str(booking.guid)},
            )
            booking.payment_reminder_stage = "24m"
            booking.save(update_fields=["payment_reminder_stage"])
            logger.info(
                "Payment reminder (24m) sent",
                extra={"booking_id": str(booking.guid)},
            )
        elif 6 >= minutes_left > 1 and stage != "6m":
            NotificationService.send_to_client(
                client=booking.client,
                title="Payment reminder",
                message=(
                    f"You have about 6 minutes left to complete payment for "
                    f"{booking.property.title}. Otherwise the property will be released "
                    f"and available for others."
                ),
                notification_type=Notification.NotificationType.SYSTEM,
                data={"booking_id": str(booking.guid)},
            )
            booking.payment_reminder_stage = "6m"
            booking.save(update_fields=["payment_reminder_stage"])
            logger.info(
                "Payment reminder (6m) sent",
                extra={"booking_id": str(booking.guid)},
            )
        elif 1 >= minutes_left > 0 and stage != "1m":
            NotificationService.send_to_client(
                client=booking.client,
                title="Payment reminder",
                message=(
                    f"You have about 1 minute left to complete payment for "
                    f"{booking.property.title}. Otherwise the property will be released "
                    f"and available for others."
                ),
                notification_type=Notification.NotificationType.SYSTEM,
                data={"booking_id": str(booking.guid)},
            )
            booking.payment_reminder_stage = "1m"
            booking.save(update_fields=["payment_reminder_stage"])
            logger.info(
                "Payment reminder (1m) sent",
                extra={"booking_id": str(booking.guid)},
            )
