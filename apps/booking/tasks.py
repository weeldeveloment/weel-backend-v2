import logging
from datetime import timedelta
from types import SimpleNamespace

from django.utils import timezone

from core.celery import app
from notification.service import NotificationService
from payment.raw_repository import (
    create_charge_transaction_from_latest,
    get_latest_transaction_history_for_booking,
    mark_latest_transaction_dismissed,
)
from payment.services import PlumAPIError, PlumAPIService

from .raw_booking_repository import (
    get_booking_by_guid,
    list_pending_bookings_for_payment_reminders,
    release_calendar_for_booking,
    update_booking_payment_reminder_stage,
    update_booking_status,
)

logger = logging.getLogger(__name__)

PAYMENT_DEADLINE_MINUTES = 30


def _client_ref(user_id: int):
    return SimpleNamespace(id=user_id)


def _partner_ref(user_id: int):
    return SimpleNamespace(id=user_id)


@app.task(
    name="booking.auto_cancel",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def auto_cancel_booking(self, booking_id):
    booking = get_booking_by_guid(str(booking_id))
    if not booking:
        logger.warning(
            "auto_cancel: booking not found", extra={"booking_id": str(booking_id)}
        )
        return
    if booking["status"] != "pending":
        logger.info(
            "auto_cancel: skipped",
            extra={"booking_id": str(booking_id), "status": booking["status"]},
        )
        return

    logger.info("auto_cancel: triggered", extra={"booking_id": str(booking_id)})

    tx = get_latest_transaction_history_for_booking(int(booking["id"]))
    if tx and tx.get("transaction_id") and tx.get("hold_id"):
        plum_service = PlumAPIService()
        try:
            plum_service.dismiss_hold(
                transaction_id=tx["transaction_id"],
                hold_id=tx["hold_id"],
            )
        except PlumAPIError as plum_api_error:
            logger.warning(
                "auto_cancel: dismiss_hold failed",
                extra={
                    "booking_id": str(booking_id),
                    "error": plum_api_error.message,
                    "status_code": plum_api_error.status_code,
                },
            )
    mark_latest_transaction_dismissed(int(booking["id"]))

    release_calendar_for_booking(booking)
    update_booking_status(
        booking_id=int(booking["id"]),
        status="cancelled",
        cancellation_reason="system_timeout",
        set_cancelled=True,
    )

    NotificationService.send_to_client(
        client=_client_ref(int(booking["created_by"])),
        title="Booking cancelled",
        message=(
            f"Your booking for {booking.get('property_title')} was not confirmed in time "
            f"and has been released. The property is available again for these dates. "
            f"Please choose another option if you still wish to book."
        ),
        notification_type="system",
        data={"booking_id": str(booking["guid"])},
    )
    if booking.get("partner_user_id"):
        NotificationService.send_to_partner(
            partner=_partner_ref(int(booking["partner_user_id"])),
            title="Booking auto-cancelled",
            message=(
                f"Booking {booking.get('booking_number')} for {booking.get('property_title')} "
                f"was auto-cancelled because it was not confirmed in time."
            ),
            notification_type="system",
            data={
                "booking_id": str(booking["guid"]),
                "event": "booking_auto_cancelled",
            },
        )

    logger.info(
        "auto_cancel: booking cancelled successfully",
        extra={"booking_id": str(booking_id)},
    )


@app.task(
    name="booking.auto_complete",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def auto_complete_booking(self, booking_id):
    booking = get_booking_by_guid(str(booking_id))
    if not booking:
        logger.warning(
            "auto_complete: booking not found", extra={"booking_id": str(booking_id)}
        )
        return
    if booking["status"] != "confirmed":
        logger.info(
            "auto_complete: skipped",
            extra={"booking_id": str(booking_id), "status": booking["status"]},
        )
        return

    today = timezone.localdate()
    if booking["check_in"] != today:
        logger.info(
            "auto_complete: skipped(not check-in day)",
            extra={"booking_id": str(booking_id), "check_in": str(booking["check_in"])},
        )
        return

    logger.info("auto_complete triggered", extra={"booking_id": str(booking_id)})

    tx = get_latest_transaction_history_for_booking(int(booking["id"]))
    charge_amount = booking.get("booking_charge_amount")
    if tx and tx.get("transaction_id") and tx.get("hold_id") and charge_amount:
        plum_service = PlumAPIService()
        try:
            charge_transaction = plum_service.charge_hold(
                transaction_id=tx["transaction_id"],
                hold_id=tx["hold_id"],
                charge_amount=charge_amount,
            )
            create_charge_transaction_from_latest(
                booking_row=booking,
                transaction_id=(charge_transaction or {}).get("transactionId") or tx.get("transaction_id"),
                hold_id=(charge_transaction or {}).get("holdId") or tx.get("hold_id"),
                amount=(charge_transaction or {}).get("amount") or charge_amount,
                card_id=(charge_transaction or {}).get("cardId") or tx.get("card_id"),
                extra_id=(charge_transaction or {}).get("extraId"),
            )
        except PlumAPIError as plum_api_error:
            logger.warning(
                "auto_complete: charge_hold PlumAPIError (proceeding)",
                extra={
                    "booking_id": str(booking_id),
                    "error": plum_api_error.message,
                    "status_code": plum_api_error.status_code,
                },
            )
        except Exception:
            logger.exception(
                "auto_complete: charge_hold failed unexpectedly",
                extra={"booking_id": str(booking_id)},
            )

    update_booking_status(
        booking_id=int(booking["id"]),
        status="completed",
        set_completed=True,
    )

    NotificationService.send_to_client(
        client=_client_ref(int(booking["created_by"])),
        title="Бронирование завершено🎉",
        message=(
            f"Бронирование объекта «{booking.get('property_title')}» успешно завершено🏠\n"
            f"Ранее захолдированная сумма: {booking.get('booking_hold_amount')} сум\n"
            f"Сумма к оплате по бронированию: {booking.get('booking_charge_amount')} сум\n"
            f"Спасибо, что выбрали нас😊"
        ),
        notification_type="system",
        data={"booking_id": str(booking["guid"])},
    )
    if booking.get("partner_user_id"):
        NotificationService.send_to_partner(
            partner=_partner_ref(int(booking["partner_user_id"])),
            title="Booking completed",
            message=(
                f"Booking {booking.get('booking_number')} for {booking.get('property_title')} "
                f"was completed."
            ),
            notification_type="system",
            data={"booking_id": str(booking["guid"]), "event": "booking_auto_completed"},
        )

    logger.info(
        "auto_complete: booking completed successfully",
        extra={"booking_id": str(booking_id)},
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
    now = timezone.now()
    deadline_delta = timedelta(minutes=PAYMENT_DEADLINE_MINUTES)

    pending_rows = list_pending_bookings_for_payment_reminders()

    for booking in pending_rows:
        deadline = booking["created_at"] + deadline_delta
        if now >= deadline:
            continue
        minutes_left = (deadline - now).total_seconds() / 60.0
        stage = (booking.get("payment_reminder_stage") or "").strip()

        if 24 >= minutes_left > 6 and stage != "24m":
            NotificationService.send_to_client(
                client=_client_ref(int(booking["created_by"])),
                title="Payment reminder",
                message=(
                    f"You have about 24 minutes left to complete payment for "
                    f"{booking.get('property_title')}. Otherwise the property will be released "
                    f"and available for others."
                ),
                notification_type="system",
                data={"booking_id": str(booking["guid"])},
            )
            update_booking_payment_reminder_stage(int(booking["id"]), "24m")
            logger.info(
                "Payment reminder (24m) sent",
                extra={"booking_id": str(booking["guid"])},
            )
        elif 6 >= minutes_left > 1 and stage != "6m":
            NotificationService.send_to_client(
                client=_client_ref(int(booking["created_by"])),
                title="Payment reminder",
                message=(
                    f"You have about 6 minutes left to complete payment for "
                    f"{booking.get('property_title')}. Otherwise the property will be released "
                    f"and available for others."
                ),
                notification_type="system",
                data={"booking_id": str(booking["guid"])},
            )
            update_booking_payment_reminder_stage(int(booking["id"]), "6m")
            logger.info(
                "Payment reminder (6m) sent",
                extra={"booking_id": str(booking["guid"])},
            )
        elif 1 >= minutes_left > 0 and stage != "1m":
            NotificationService.send_to_client(
                client=_client_ref(int(booking["created_by"])),
                title="Payment reminder",
                message=(
                    f"You have about 1 minute left to complete payment for "
                    f"{booking.get('property_title')}. Otherwise the property will be released "
                    f"and available for others."
                ),
                notification_type="system",
                data={"booking_id": str(booking["guid"])},
            )
            update_booking_payment_reminder_stage(int(booking["id"]), "1m")
            logger.info(
                "Payment reminder (1m) sent",
                extra={"booking_id": str(booking["guid"])},
            )


# ─── Hotel Booking Tasks ─────────────────────────────────────────────────────


@app.task(
    name="booking.auto_cancel_hotel",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def auto_cancel_hotel_booking(self, booking_id):
    from apps.hotels.repository import (
        get_hotel_booking_by_id,
        release_hotel_booking_calendar_slots,
        update_hotel_booking_status,
    )

    booking = get_hotel_booking_by_id(int(booking_id))
    if not booking:
        logger.warning(
            "auto_cancel_hotel: booking not found",
            extra={"booking_id": str(booking_id)},
        )
        return
    if booking["status"] != "pending":
        logger.info(
            "auto_cancel_hotel: skipped",
            extra={"booking_id": str(booking_id), "status": booking["status"]},
        )
        return

    logger.info(
        "auto_cancel_hotel: triggered",
        extra={"booking_id": str(booking_id)},
    )

    tx = get_latest_transaction_history_for_booking(int(booking["id"]))
    if tx and tx.get("transaction_id") and tx.get("hold_id"):
        plum_service = PlumAPIService()
        try:
            plum_service.dismiss_hold(
                transaction_id=tx["transaction_id"],
                hold_id=tx["hold_id"],
            )
        except PlumAPIError as plum_api_error:
            logger.warning(
                "auto_cancel_hotel: dismiss_hold failed",
                extra={
                    "booking_id": str(booking_id),
                    "error": plum_api_error.message,
                    "status_code": plum_api_error.status_code,
                },
            )
    mark_latest_transaction_dismissed(int(booking["id"]))

    release_hotel_booking_calendar_slots(
        room_id=int(booking["room_id"]),
        check_in=booking["check_in"],
        check_out=booking["check_out"],
    )
    update_hotel_booking_status(int(booking["id"]), "cancelled")

    NotificationService.send_to_client(
        client=_client_ref(int(booking["created_by"])),
        title="Hotel booking cancelled",
        message=(
            f"Your hotel booking {booking.get('booking_number')} was not confirmed in time "
            f"and has been released. The room is available again for these dates."
        ),
        notification_type="system",
        data={"booking_id": str(booking["id"])},
    )

    logger.info(
        "auto_cancel_hotel: booking cancelled successfully",
        extra={"booking_id": str(booking_id)},
    )
