"""Push and feed notifications for workspace tasks and the calendar.

Everything here runs off the request. A push costs a call to Firebase, which
is somebody else's network — holding a manager's "create task" response open
for it would make the app feel slow for the one person who is not waiting for
the notification.

The chat and mail senders live in `apps/b2b/mail/tasks.py` for historical
reasons: that is where the B2B feed and the FCM plumbing were first written.
New senders go here instead, next to the repository they read from.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from core.celery import app
from django.utils import timezone

from apps.b2b.mail.repository import create_notification
from apps.b2b.workspace import push_text
from apps.b2b.workspace import repository as repo

logger = logging.getLogger(__name__)


# How far ahead of an event each reminder goes out. `0` is the event starting
# now — the same push, said in the present tense.
EVENT_REMINDER_OFFSETS = (30, 10, 0)

# How far back a reminder pass looks. The task runs every minute, so one
# minute would be enough if nothing ever went wrong; a worker that was
# restarted, redeployed or simply busy would drop that minute's reminders on
# the floor and nobody would ever know. Anything still unsent within this
# window is caught by the next pass instead — `claim_event_reminder` is what
# keeps that from sending the same reminder twice.
REMINDER_CATCHUP = timedelta(minutes=5)


def _push(recipients, *, title: str, body: str, data: dict[str, str]) -> None:
    """Send one message to whichever of `recipients` has a token.

    Imported inside the call rather than at module scope: `apps.notification`
    pulls in `firebase_admin`, and this module is imported by the worker at
    startup whether or not it ever sends anything.
    """
    tokens = [r["fcm_token"] for r in recipients if r.get("fcm_token")]
    if not tokens:
        return
    try:
        from apps.notification.service import FCMService, b2b_firebase_app

        FCMService.send_to_tokens(
            tokens=tokens,
            title=title,
            body=body,
            data=data,
            # The workspace app has its own Firebase project; its tokens are
            # not addressable from the consumer one.
            app=b2b_firebase_app(),
            deactivate_invalid=repo.clear_employee_fcm_tokens,
        )
    except Exception:  # noqa: BLE001 - the row is already in the feed
        logger.exception("Push failed for %s", data)


@app.task(name="b2b.workspace.notify_task_assigned")
def notify_task_assigned(
    task_id: int,
    actor_id: int,
    company_id: int,
    employee_ids: list[int] | None = None,
) -> int:
    """Somebody was put on a task — by its creation, or by being added later.

    Addressed to whoever is on the task now, minus the person who did it. A
    manager assigning themselves along with two colleagues gets the feed row
    and no push: they are looking at the task they just typed.

    `employee_ids` narrows it to the people an edit has just added, so the
    ones who already had the task are not told about it twice.
    """
    task = repo.get_task(task_id, company_id)
    if not task:
        # Deleted between the request finishing and this running.
        return 0

    recipients = repo.list_task_assignee_recipients(
        task_id, exclude_employee_id=actor_id, only_employee_ids=employee_ids
    )
    if not recipients:
        return 0

    title = push_text.TASK_TITLE
    body = task["title"]

    for recipient in recipients:
        create_notification(
            company_id=recipient["company_id"],
            employee_id=recipient["employee_id"],
            kind="task",
            title=title,
            body=body,
            payload={"task_id": task_id},
        )

    _push(
        recipients,
        title=title,
        body=body,
        data={"type": "task", "task_id": str(task_id)},
    )
    return len(recipients)


@app.task(name="b2b.workspace.notify_event_created")
def notify_event_created(
    event_id: int,
    actor_id: int,
    company_id: int,
    employee_ids: list[int] | None = None,
) -> int:
    """An entry was put on somebody's calendar by somebody else.

    Not sent for the private entries an employee makes for themselves: there
    the only participant is the author, who is excluded, and the whole thing
    comes to nothing without a special case for it.

    `employee_ids` narrows it to people an edit has just invited.
    """
    event = repo.get_event(event_id, company_id)
    if not event:
        return 0

    recipients = repo.list_event_participant_recipients(
        event_id, exclude_employee_id=actor_id, only_employee_ids=employee_ids
    )
    if not recipients:
        return 0

    title = push_text.EVENT_TITLE
    body = _event_body(event)

    for recipient in recipients:
        create_notification(
            company_id=recipient["company_id"],
            employee_id=recipient["employee_id"],
            kind="event",
            title=title,
            body=body,
            payload={"event_id": event_id},
        )

    _push(
        recipients,
        title=title,
        body=body,
        data={"type": "event", "event_id": str(event_id)},
    )
    return len(recipients)


@app.task(name="b2b.workspace.send_event_reminders")
def send_event_reminders() -> int:
    """The 30-minute, 10-minute and starting-now warnings.

    Runs every minute over every company. What it sends is decided by the
    events themselves rather than by anything scheduled per event: a meeting
    that is created eleven minutes before it starts simply misses the
    30-minute reminder and gets the other two, and one that is moved has its
    claims cleared so the reminders are due again at the new time.
    """
    now = timezone.now()
    sent = 0

    for minutes in EVENT_REMINDER_OFFSETS:
        # Everything whose start is `minutes` away, give or take the catch-up
        # window. The window opens in the past — an event 29 minutes out is
        # already overdue for its 30-minute warning.
        target = now + timedelta(minutes=minutes)
        events = repo.list_events_starting_between(target - REMINDER_CATCHUP, target)

        for event in events:
            if not repo.claim_event_reminder(event["id"], minutes):
                continue

            recipients = repo.list_event_participant_recipients(event["id"])
            if not recipients:
                continue

            title = push_text.event_reminder_title(minutes)
            body = _event_body(event)

            for recipient in recipients:
                create_notification(
                    company_id=recipient["company_id"],
                    employee_id=recipient["employee_id"],
                    kind="event",
                    title=title,
                    body=body,
                    payload={"event_id": event["id"], "minutes_before": minutes},
                )

            _push(
                recipients,
                title=title,
                body=body,
                data={
                    "type": "event",
                    "event_id": str(event["id"]),
                    "minutes_before": str(minutes),
                },
            )
            sent += 1

    return sent


def _event_body(event: dict) -> str:
    """What an event reads as in a notification: when, and where if known."""
    starts_at = event.get("starts_at")
    when = timezone.localtime(starts_at).strftime("%H:%M") if starts_at else ""
    parts = [part for part in (event.get("title"), when, event.get("location")) if part]
    return " · ".join(parts)


@app.task(name="b2b.workspace.notify_secondment_request")
def notify_secondment_request(request_id: int, event: str) -> int:
    """One of the three moments in a request's life that somebody is waiting on.

    `sent` goes to the person being asked; `accepted` and `declined` go back to
    whoever asked. Nobody is told about a cancellation: the person it was
    withdrawn from never acted on it, and "never mind" is not worth a buzz.
    """
    from apps.b2b.workspace import secondment_repository as srepo

    ask = srepo.get_request(request_id)
    if not ask:
        return 0

    if event == "sent":
        recipient_id = ask["to_employee_id"]
        title = push_text.SECONDMENT_TITLE
        body = _secondment_body(ask)
    elif event in {"accepted", "declined"}:
        recipient_id = ask["from_employee_id"]
        accepted = event == "accepted"
        title = (
            push_text.SECONDMENT_ACCEPTED_TITLE
            if accepted
            else push_text.SECONDMENT_DECLINED_TITLE
        )
        # A refusal carries its reason. It is the entire value of declining
        # rather than ignoring, and burying it in the app is how it goes
        # unread — see `SecondmentDeclineSerializer`.
        body = (ask.get("decline_reason") or "").strip() if not accepted else ""
        if not body:
            body = _secondment_body(ask)
    else:
        return 0

    employee = repo.get_workspace_employee(recipient_id)
    if not employee:
        return 0

    recipients = [{
        "employee_id": employee["id"],
        "company_id": employee["company_id"],
        "fcm_token": employee.get("fcm_token"),
    }]
    create_notification(
        company_id=employee["company_id"],
        employee_id=employee["id"],
        kind="request",
        title=title,
        body=body,
        payload={"request_id": request_id, "event": event},
    )
    _push(
        recipients,
        title=title,
        body=body,
        data={"type": "request", "request_id": str(request_id), "event": event},
    )
    return 1


def _secondment_body(ask: dict) -> str:
    """What the request reads as: who is asking, and what they wrote."""
    message = (ask.get("message") or "").strip()
    if message:
        return message[:200]
    company = ask.get("company_name")
    return f"{company} ish jarayoniga taklif qilmoqda" if company else "Yangi so’rov"


@app.task(name="b2b.workspace.expire_secondments")
def expire_secondments() -> int:
    """Close the secondments whose end has passed.

    Not the security boundary — that is `resolve_membership`, which checks the
    window on every single request and does not care whether this has run. What
    this does is keep the roster honest: an ended guest should stop appearing
    in assignee pickers and chat member lists, and only deactivating the row
    achieves that.
    """
    from apps.b2b.workspace import secondment_repository as srepo

    expired = srepo.list_expired_memberships()
    for membership in expired:
        try:
            srepo.end_membership(membership["id"])
        except Exception:  # noqa: BLE001 - one bad row must not stop the sweep
            logger.exception("Could not end membership %s", membership["id"])
    if expired:
        logger.info("Secondments ended: count=%s", len(expired))
    return len(expired)
