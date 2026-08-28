"""The one place the workspace pushes to open sockets from.

Every screen in the app used to find out about a change the same way: by being
pulled down. That is fine for a list somebody is looking at and wrong for
everything else — a task assigned to you, a lead somebody else claimed, a join
request answered while you waited on it. So the write paths say what happened
here, and the consumer fans it out.

Three audiences, and the difference between them is who is allowed to know:

* **thread**   — everyone in one chat room. Messages, typing, read receipts.
* **employee** — one person, wherever they are signed in. Something addressed
  to them: a task they were given, their join request being answered, their
  role changing under them.
* **company**  — everyone in the workspace. Shared state: the calendar, the
  sales board, attendance, who is online.

Nothing here raises. A socket that could not be reached is never a reason to
fail the request that changed the data — the change is already committed, and
the client will see it on its next load. Real-time is an improvement on
polling, not a replacement for it, and the moment it is allowed to break a
write it becomes the least reliable part of the system.
"""
from __future__ import annotations

import logging
from typing import Any

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)

#: The channel-layer routing key. Maps to `WorkspaceConsumer.workspace_event`;
#: it never goes out on the wire.
_ENVELOPE = "workspace.event"


def thread_group(thread_id: int) -> str:
    return f"ws.thread.{thread_id}"


def employee_group(employee_id: int) -> str:
    return f"ws.employee.{employee_id}"


def company_group(company_id: int) -> str:
    return f"ws.company.{company_id}"


def _publish(group: str, event: str, payload: dict[str, Any]) -> None:
    try:
        layer = get_channel_layer()
        if layer is None:
            return
        async_to_sync(layer.group_send)(
            group, {"type": _ENVELOPE, "event": event, **payload}
        )
    except Exception:  # noqa: BLE001
        logger.exception("Could not publish %s to %s", event, group)


def publish_thread(thread_id: int, event: str, **payload: Any) -> None:
    """Everyone in one room.

    ``thread_id`` is always put on the wire: a client holds one socket for
    every room it is in, so a frame that does not say which room it belongs to
    cannot be placed.
    """
    _publish(thread_group(thread_id), event, {"thread_id": thread_id, **payload})


def publish_employee(employee_id: int, event: str, **payload: Any) -> None:
    _publish(employee_group(employee_id), event, payload)


def publish_company(company_id: int, event: str, **payload: Any) -> None:
    _publish(company_group(company_id), event, payload)


def publish_employees(employee_ids, event: str, **payload: Any) -> None:
    """The same event to several people individually.

    Used where the audience is a list rather than a room — a task's assignees,
    an event's participants — and is narrower than the whole company.
    """
    for employee_id in {int(i) for i in employee_ids if i is not None}:
        _publish(employee_group(employee_id), event, payload)


# ─── The event vocabulary ─────────────────────────────────────────────────────
#
# These strings are the contract with the app, which matches on them, so they
# belong in one list rather than being spelled out at each call site.

EVENT_MESSAGE = "message"
EVENT_TYPING = "typing"
EVENT_READ = "read"
EVENT_DELETED = "deleted"
EVENT_EDITED = "edited"
EVENT_REACTION = "reaction"
EVENT_PINNED = "pinned"

EVENT_THREAD = "thread"
EVENT_PRESENCE = "presence"

EVENT_TASK = "task"
EVENT_LEAD = "lead"
EVENT_CALENDAR = "calendar"
EVENT_ATTENDANCE = "attendance"
EVENT_JOIN_REQUEST = "join_request"
EVENT_REQUEST = "request"
EVENT_ACCESS = "access"
EVENT_FILE = "file"
EVENT_TEAM = "team"


# ─── Chat, kept as named calls ────────────────────────────────────────────────
#
# The chat views have said `broadcast_message(...)` since before there was a
# bus, and the names read better at the call site than a generic publish with
# two string arguments.


def broadcast_message(thread_id: int, payload: dict[str, Any]) -> None:
    """A new message, to everyone in the room."""
    publish_thread(thread_id, EVENT_MESSAGE, message=payload)


def broadcast_edit(thread_id: int, payload: dict[str, Any]) -> None:
    """A message whose text changed, so open threads swap it in place."""
    publish_thread(thread_id, EVENT_EDITED, message=payload)


def broadcast_deletion(thread_id: int, message_id: int) -> None:
    """A message that is gone, so open threads drop the bubble rather than
    showing it until the next refetch."""
    publish_thread(thread_id, EVENT_DELETED, message_id=message_id)
