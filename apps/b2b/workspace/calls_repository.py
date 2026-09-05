"""The `b2b_call` rows — one per live video/audio call.

Raw SQL like the rest of the workspace: the table is created by
`create_b2b_tables`, and every read and write of it goes through here so the
service in `calls.py` can be tested against a mocked module.

Two things are enforced at this layer rather than in the service:

* A **status change is conditional**. `transition` only moves a row that is
  still in one of the statuses the caller expects, and says so by returning
  the row or `None`. Two phones answering the same call, or a decline racing
  the ring timeout, then settle on whichever write landed first instead of
  both believing they won.
* **"Is anybody on a call"** is one indexed probe over the partial index in
  the schema, because it is asked on every app resume and before every new
  call.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

from django.utils import timezone

from apps.b2b.raw.tables import (
    B2B_CALL_TABLE,
    B2B_EMPLOYEE_TABLE,
)
from shared.raw.db import execute, fetch_all, fetch_one


class CallStatus:
    RINGING = "ringing"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    MISSED = "missed"
    CANCELLED = "cancelled"
    ENDED = "ended"
    FAILED = "failed"

    #: A call still happening — the two states the other side has to be told
    #: about the moment they change.
    LIVE = (RINGING, ACCEPTED)
    #: Nothing more will happen to it.
    FINAL = (DECLINED, MISSED, CANCELLED, ENDED, FAILED)


class CallType:
    AUDIO = "audio"
    VIDEO = "video"
    CHOICES = (AUDIO, VIDEO)


class CallSource:
    CHAT = "chat"
    CRM = "crm"
    SALES = "sales"
    CHOICES = (CHAT, CRM, SALES)


def create_call(
    *,
    company_id: int,
    room_name: str,
    call_type: str,
    source_module: str,
    initiator_id: int,
    target_employee_id: int | None = None,
    target_lead_id: int | None = None,
    target_customer_id: int | None = None,
    thread_id: int | None = None,
) -> dict[str, Any] | None:
    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO {B2B_CALL_TABLE}
            (company_id, room_name, type, source_module, initiator_id,
             target_employee_id, target_lead_id, target_customer_id, thread_id,
             status, started_at, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        [
            company_id, room_name, call_type, source_module, initiator_id,
            target_employee_id, target_lead_id, target_customer_id, thread_id,
            CallStatus.RINGING, now, now, now,
        ],
    )


def get_call(call_id: int, company_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT * FROM {B2B_CALL_TABLE} WHERE id = %s AND company_id = %s",
        [call_id, company_id],
    )


def transition(
    call_id: int,
    *,
    to: str,
    only_from: Sequence[str],
    answered_at: datetime | None = None,
    ended_at: datetime | None = None,
    duration_seconds: int | None = None,
    ended_by: int | None = None,
) -> dict[str, Any] | None:
    """Moves a call to `to` if it is still in one of `only_from`.

    Returns the updated row, or `None` when somebody else got there first —
    which the caller treats as "already settled" rather than as an error.
    """
    sets = ["status = %s", "updated_at = %s"]
    params: list[Any] = [to, timezone.now()]
    if answered_at is not None:
        sets.append("answered_at = %s")
        params.append(answered_at)
    if ended_at is not None:
        sets.append("ended_at = %s")
        params.append(ended_at)
    if duration_seconds is not None:
        sets.append("duration_seconds = %s")
        params.append(duration_seconds)
    if ended_by is not None:
        sets.append("ended_by = %s")
        params.append(ended_by)
    params += [call_id, list(only_from)]
    return fetch_one(
        f"UPDATE {B2B_CALL_TABLE} SET {', '.join(sets)} "
        "WHERE id = %s AND status = __ANY_MARKER__(%s) RETURNING *",
        params,
    )


def mark_guest_link_sent(call_id: int) -> None:
    execute(
        f"UPDATE {B2B_CALL_TABLE} SET guest_link_sent_at = %s, updated_at = %s WHERE id = %s",
        [timezone.now(), timezone.now(), call_id],
    )


def live_call_for(employee_id: int) -> dict[str, Any] | None:
    """The call this person is on, or being rung for, right now.

    Newest first, so if a stale ringing row somehow survived alongside a real
    one the real one wins. The ring timeout in `calls.py` is what stops stale
    rows surviving at all.
    """
    return fetch_one(
        f"""
        SELECT * FROM {B2B_CALL_TABLE}
        WHERE (target_employee_id = %s OR initiator_id = %s)
          AND status = __ANY_MARKER__(%s)
        ORDER BY started_at DESC
        LIMIT 1
        """,
        [employee_id, employee_id, list(CallStatus.LIVE)],
    )


def ringing_for(employee_id: int) -> dict[str, Any] | None:
    """The call ringing *at* this person — what the app asks on resume, in
    case both the socket and the push missed it."""
    return fetch_one(
        f"""
        SELECT * FROM {B2B_CALL_TABLE}
        WHERE target_employee_id = %s AND status = %s
        ORDER BY started_at DESC
        LIMIT 1
        """,
        [employee_id, CallStatus.RINGING],
    )


def stale_ringing(cutoff: datetime) -> list[dict[str, Any]]:
    """Every call still ringing that started before `cutoff`."""
    return fetch_all(
        f"SELECT * FROM {B2B_CALL_TABLE} WHERE status = %s AND started_at < %s "
        "ORDER BY started_at ASC LIMIT 500",
        [CallStatus.RINGING, cutoff],
    )


def stale_accepted(cutoff: datetime) -> list[dict[str, Any]]:
    """Every answered call whose `/end` never came — answered before `cutoff`
    and still open. `answered_at` can be null on a row written by an older
    build, so `started_at` stands in for it."""
    return fetch_all(
        f"SELECT * FROM {B2B_CALL_TABLE} WHERE status = %s "
        "AND COALESCE(answered_at, started_at) < %s "
        "ORDER BY started_at ASC LIMIT 500",
        [CallStatus.ACCEPTED, cutoff],
    )


def list_history(
    company_id: int,
    *,
    thread_id: int | None = None,
    lead_id: int | None = None,
    customer_id: int | None = None,
    employee_id: int | None = None,
    before_id: int | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Calls for one card — a chat, a lead, a customer — or for one person.

    Newest first. Exactly one filter is expected; the view checks that, this
    just applies whatever it was given.
    """
    sql = f"SELECT * FROM {B2B_CALL_TABLE} WHERE company_id = %s"
    params: list[Any] = [company_id]
    if thread_id is not None:
        sql += " AND thread_id = %s"
        params.append(thread_id)
    if lead_id is not None:
        sql += " AND target_lead_id = %s"
        params.append(lead_id)
    if customer_id is not None:
        sql += " AND target_customer_id = %s"
        params.append(customer_id)
    if employee_id is not None:
        sql += " AND (initiator_id = %s OR target_employee_id = %s)"
        params += [employee_id, employee_id]
    if before_id is not None:
        sql += " AND id < %s"
        params.append(before_id)
    sql += " ORDER BY id DESC LIMIT %s"
    params.append(limit)
    return fetch_all(sql, params)


def employee_cards(employee_ids: Sequence[int]) -> dict[int, dict[str, Any]]:
    """Name and picture for the people a call payload names."""
    ids = list({int(i) for i in employee_ids if i})
    if not ids:
        return {}
    rows = fetch_all(
        f"SELECT id, full_name, photo, fcm_token, voip_token, company_id FROM {B2B_EMPLOYEE_TABLE} "
        "WHERE id = __ANY_MARKER__(%s)",
        [ids],
    )
    return {row["id"]: row for row in rows}
