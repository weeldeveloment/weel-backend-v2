from datetime import timedelta
from types import SimpleNamespace

from celery import shared_task

from django.utils import timezone

from .service import NotificationService
from shared.raw.db import execute, fetch_all, table_exists

REVIEW_REMINDER_WINDOW_DAYS = 30


@shared_task
def send_booking_reminders():
    if not table_exists("booking"):
        return

    today = timezone.localdate()
    tomorrow = today + timedelta(days=1)

    bookings = fetch_all(
        """
        SELECT
            b.id,
            b.guid,
            b.client_user_id,
            COALESCE(a.title, c.title) AS property_title
        FROM public.booking b
        LEFT JOIN public.apartment a ON a.id = b.property_apartment_id
        LEFT JOIN public.cottage c ON c.id = b.property_cottage_id
        WHERE b.check_in = %s
          AND COALESCE(b.reminder_sent, FALSE) = FALSE
          AND b.status = 'confirmed'
        """,
        [tomorrow],
    )

    for booking in bookings:
        client = SimpleNamespace(id=booking["client_user_id"])
        NotificationService.send_to_client(
            client=client,
            title="Напоминание о предстоящем бронирование⏰",
            message=(
                f"Напоминаем, что заселение по вашему бронированию "
                f"в {booking['property_title']} состоится затвра"
                f"Желаем приятного пребывания!"
            ),
            notification_type="reminder",
            data={"booking_id": str(booking["guid"])},
        )

        execute(
            """
            UPDATE public.booking
            SET reminder_sent = TRUE
            WHERE id = %s
            """,
            [booking["id"]],
        )


def _review_table() -> str | None:
    for candidate in ("review", "property_review"):
        if table_exists(candidate):
            return f"public.{candidate}"
    return None


@shared_task
def send_review_reminders():
    """
    Runs daily. Sends a push notification to clients who completed a booking
    but have not yet left a review for the property. Stops after REVIEW_REMINDER_WINDOW_DAYS.
    """
    if not table_exists("booking"):
        return

    review_table = _review_table()
    if review_table is None:
        return

    today = timezone.localdate()
    window_start = today - timedelta(days=REVIEW_REMINDER_WINDOW_DAYS)

    bookings = fetch_all(
        f"""
        SELECT
            b.id,
            b.guid,
            b.client_user_id,
            b.property_apartment_id,
            b.property_cottage_id,
            COALESCE(a.guid, c.guid) AS property_guid,
            COALESCE(a.title, c.title) AS property_title
        FROM public.booking b
        LEFT JOIN public.apartment a ON a.id = b.property_apartment_id
        LEFT JOIN public.cottage c ON c.id = b.property_cottage_id
        WHERE b.status = 'completed'
          AND b.check_out < %s
          AND b.check_out >= %s
          AND NOT EXISTS (
              SELECT 1 FROM {review_table} r
              WHERE r.user_id = b.client_user_id
                AND (
                    (b.property_apartment_id IS NOT NULL AND r.apartment_id = b.property_apartment_id)
                    OR
                    (b.property_cottage_id IS NOT NULL AND r.cottage_id = b.property_cottage_id)
                )
          )
        """,
        [today, window_start],
    )

    for booking in bookings:
        client = SimpleNamespace(id=booking["client_user_id"])
        NotificationService.send_to_client(
            client=client,
            title="Оставьте отзыв о вашем пребывании ⭐",
            message=(
                f"Как вам было в «{booking['property_title']}»? "
                f"Поделитесь впечатлениями и поставьте оценку — "
                f"это поможет другим путешественникам сделать правильный выбор!"
            ),
            notification_type="leave_review",
            data={
                "booking_id": str(booking["guid"]),
                "property_id": str(booking["property_guid"]),
                "event": "review_reminder",
            },
        )
