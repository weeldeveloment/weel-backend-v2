from datetime import timedelta
from types import SimpleNamespace

from celery import shared_task

from django.utils import timezone

from .service import NotificationService
from shared.raw.db import execute, fetch_all, table_exists


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
