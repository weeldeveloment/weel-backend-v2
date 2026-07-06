from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .hold_service import fetch_held_intervals_for_resources
from .raw_repository import (
    fetch_active_resources_for_activity,
    fetch_activity,
    fetch_busy_intervals_for_resources,
    fetch_tariff,
    fetch_working_hours,
)

DEFAULT_SLOT_STEP_MINUTES = 15


@dataclass(slots=True)
class TimeSlot:
    start: datetime
    end: datetime  # start + tariff.duration_minutes (buffer excluded — this is what the client sees)
    available_resource_ids: list[int]


def compute_available_slots(
    *,
    activity_id: int,
    tariff_id: int,
    target_date: date,
    slot_step_minutes: int = DEFAULT_SLOT_STEP_MINUTES,
) -> list[TimeSlot]:
    """
    For a given activity/tariff/date, return every candidate start time (stepped
    by `slot_step_minutes`) for which at least one resource is free.

    Core algorithm:
    1. Look up the activity's working hours for that weekday; a day off means no slots.
    2. duration + activity.buffer_minutes = the span that must be free on a resource.
    3. Collect busy intervals per resource from two sources: confirmed/pending_payment
       DB bookings, and Redis "soft hold" intervals from in-progress checkouts
       (see hold_service.py) — both count as unavailable so two users don't
       race to the same slot during the payment step.
    4. Slide a window across working hours at `slot_step_minutes` resolution; a
       candidate start is offered if [start, start + duration + buffer) doesn't
       overlap any busy interval on at least one resource.
    """
    activity = fetch_activity(activity_id)
    if activity is None:
        return []

    tariff = fetch_tariff(tariff_id)
    if tariff is None or tariff["activity_id"] != activity["id"]:
        return []

    weekday = target_date.weekday()
    working_hours = fetch_working_hours(activity_id=activity_id, weekday=weekday)
    if working_hours is None or working_hours["is_day_off"] or not working_hours["opens_at"] or not working_hours["closes_at"]:
        return []

    duration = timedelta(minutes=tariff["duration_minutes"])
    buffer_ = timedelta(minutes=activity["buffer_minutes"])
    blocked_span = duration + buffer_

    day_start = datetime.combine(target_date, working_hours["opens_at"])
    day_end = datetime.combine(target_date, working_hours["closes_at"])

    resources = fetch_active_resources_for_activity(activity_id)
    resource_ids = [r["id"] for r in resources]
    if not resource_ids:
        return []

    db_busy = fetch_busy_intervals_for_resources(resource_ids=resource_ids, from_dt=day_start, to_dt=day_end)
    held_busy = fetch_held_intervals_for_resources(resource_ids=resource_ids, from_dt=day_start, to_dt=day_end)

    busy: dict[int, list[tuple[datetime, datetime]]] = {}
    for rid in resource_ids:
        busy[rid] = sorted(db_busy.get(rid, []) + held_busy.get(rid, []))

    def _is_free(rid: int, start: datetime, end: datetime) -> bool:
        return all(end <= b_start or start >= b_end for b_start, b_end in busy[rid])

    step = timedelta(minutes=slot_step_minutes)
    slots: list[TimeSlot] = []

    candidate = day_start
    while candidate + duration <= day_end:
        blocked_end = candidate + blocked_span
        free_resource_ids = [rid for rid in resource_ids if _is_free(rid, candidate, blocked_end)]
        if free_resource_ids:
            slots.append(TimeSlot(start=candidate, end=candidate + duration, available_resource_ids=free_resource_ids))
        candidate += step

    return slots
