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
from apps.b2b.workspace import accounts
from apps.b2b.workspace import push_text
from apps.b2b.workspace import repository as repo
from apps.b2b.workspace.joining_repository import JoinStatus

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
        from apps.notification.service import (
            B2B_ANDROID_CHANNEL,
            FCMService,
            b2b_firebase_app,
        )

        FCMService.send_to_tokens(
            tokens=tokens,
            title=title,
            body=body,
            data=data,
            # The workspace app has its own Firebase project; its tokens are
            # not addressable from the consumer one.
            app=b2b_firebase_app(),
            android_channel_id=B2B_ANDROID_CHANNEL,
            deactivate_invalid=repo.clear_employee_fcm_tokens,
            # The feed rows were written just above, so this counts them.
            badge_for=repo.unread_badges_for_tokens,
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


def _push_account(token: str | None, *, title: str, body: str, data: dict[str, str]) -> None:
    """One message to somebody who is not in a workspace yet.

    A separate path from [_push] because the token comes from a different
    table and the dead-token cleanup has to write back to that same table —
    handing Firebase the employee cleanup here would leave a dead account
    token in place and clear an unrelated employee's instead.
    """
    if not token:
        return
    try:
        from apps.notification.service import (
            B2B_ANDROID_CHANNEL,
            FCMService,
            b2b_firebase_app,
        )

        FCMService.send_to_tokens(
            tokens=[token],
            title=title,
            body=body,
            data=data,
            app=b2b_firebase_app(),
            android_channel_id=B2B_ANDROID_CHANNEL,
            deactivate_invalid=accounts.clear_account_fcm_tokens,
        )
    except Exception:  # noqa: BLE001 - nothing else depends on this landing
        logger.exception("Account push failed for %s", data)


@app.task(name="b2b.workspace.notify_join_request_created")
def notify_join_request_created(request_id: int) -> int:
    """Somebody asked to join — tell whoever may decide it.

    Addressed to every employee this workspace's role editor currently lets
    invite (``employees.invite``) — owner and admin by default, and anyone
    else the workspace has granted or withdrawn it from. Without this the
    request sits on the "Join requests" list until somebody happens to open
    it, which for a workspace that never checks is never.
    """
    from apps.b2b.workspace import access_repository as arepo
    from apps.b2b.workspace import joining_repository as jrepo

    ask = jrepo.get_join_request_with_company(request_id)
    if not ask or ask["status"] != JoinStatus.PENDING:
        # Withdrawn or already answered between the request finishing and
        # this running — nothing left to tell anyone about.
        return 0

    recipients = arepo.list_employee_invite_recipients(ask["company_id"])
    if not recipients:
        return 0

    account = accounts.get_account(ask["account_id"]) or {}
    asker_name = " ".join(
        part
        for part in [account.get("first_name"), account.get("last_name")]
        if part
    ).strip() or "Kimdir"

    title = push_text.JOIN_REQUEST_TITLE
    body = push_text.join_request_body(asker_name, ask.get("message"))

    for recipient in recipients:
        create_notification(
            company_id=recipient["company_id"],
            employee_id=recipient["employee_id"],
            kind="join_request",
            title=title,
            body=body,
            payload={"request_id": request_id, "account_id": ask["account_id"]},
        )

    _push(
        recipients,
        title=title,
        body=body,
        data={"type": "join_request", "request_id": str(request_id)},
    )
    return len(recipients)


@app.task(name="b2b.workspace.notify_join_request_decided")
def notify_join_request_decided(request_id: int) -> int:
    """A join request has been answered — tell the person who sent it.

    This is the one notification in the workspace app that cannot go through
    the feed. A feed row is written against an employee, and the asker has no
    employee row: on a refusal they never will, and on an acceptance the row
    is seconds old and the app has not opened the workspace it belongs to. So
    it is a push and nothing else, addressed to the account.

    Nobody else is told. The workspace already knows what it just decided.
    """
    from apps.b2b.workspace import joining_repository as jrepo

    ask = jrepo.get_join_request_with_company(request_id)
    if not ask or ask["status"] == JoinStatus.PENDING:
        # Still open, or gone. Neither is an answer to report.
        return 0

    company_name = ask.get("company_name") or ""
    account = accounts.get_account(ask["account_id"])
    if not account:
        return 0

    accepted = ask["status"] == JoinStatus.ACCEPTED
    if accepted:
        title = push_text.JOIN_ACCEPTED_TITLE
        body = push_text.join_accepted_body(company_name)
    else:
        title = push_text.JOIN_DECLINED_TITLE
        body = push_text.join_declined_body(company_name, ask.get("decline_reason"))

    _push_account(
        account.get("fcm_token"),
        title=title,
        body=body,
        data={
            "type": "join_request",
            "request_id": str(request_id),
            "status": str(ask["status"]),
            "company_id": str(ask["company_id"]),
        },
    )
    return 1


# ─── Jonli qo'ng'iroq ─────────────────────────────────────────────────────────
#
# Everything a call does that reaches another system — FCM, Eskiz, the ring
# timeout — runs here, off the request: the phone that placed the call is
# waiting for its token, and none of these may hold it up or fail it.

#: The Android channel incoming-call pushes are posted to. Its own channel,
#: created by the app alongside `weel_workspace`, so a person can give calls
#: a louder sound than a chat message — or the other way round — from the
#: system's notification settings.
CALLS_ANDROID_CHANNEL = "weel_calls"


def _push_call(
    tokens: list[str],
    *,
    title: str,
    body: str,
    data: dict[str, str],
    ttl_seconds: int | None = None,
    android_data_only: bool = False,
) -> None:
    if not tokens:
        return
    try:
        from apps.notification.service import FCMService, b2b_firebase_app

        FCMService.send_to_tokens(
            tokens=tokens,
            title=title,
            body=body,
            data=data,
            app=b2b_firebase_app(),
            android_channel_id=CALLS_ANDROID_CHANNEL,
            deactivate_invalid=repo.clear_employee_fcm_tokens,
            badge_for=repo.unread_badges_for_tokens,
            ttl_seconds=ttl_seconds,
            android_data_only=android_data_only,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Call push failed for %s", data)


@app.task(name="b2b.workspace.notify_incoming_call")
def notify_incoming_call(
    call_id: int,
    company_id: int,
    fcm_token: str,
    caller_name: str,
    call_type: str,
    thread_id: int | None = None,
    ttl_seconds: int | None = None,
    avatar: str | None = None,
) -> int:
    """"Kiruvchi qo'ng'iroq" to the phone being rung.

    `ttl_seconds` is how long FCM may hold the push before dropping it — the
    ring window, so a phone that comes back online later is not rung for a
    call that has already been written down as missed.

    Data-only on Android: the app's background handler turns it into the
    phone's own full-screen ringing screen (see `NativeCallUi` in the app),
    which a system-drawn banner could never be. iOS without a VoIP token
    still gets the alert.

    No feed row: a ring is not something to read later — if it is not
    answered, [notify_missed_call] writes the row that is.
    """
    from apps.b2b.workspace import calls_repository as calls_repo

    # Checked again here rather than trusted: the worker may be seconds
    # behind, and a call the caller has already given up on must not make a
    # phone ring for nothing.
    call = calls_repo.get_call(call_id, company_id)
    if not call or call["status"] != calls_repo.CallStatus.RINGING:
        return 0
    data = {
        "type": "call",
        "action": "ringing",
        "call_id": str(call_id),
        "call_type": call_type,
        "caller_name": caller_name,
        "caller_id": str(call["initiator_id"]),
    }
    if thread_id:
        data["thread_id"] = str(thread_id)
    if avatar:
        data["avatar"] = avatar
    if ttl_seconds:
        # How long the ringing screen shows before giving up on its own.
        data["ring_ms"] = str(int(ttl_seconds) * 1000)
    _push_call(
        [fcm_token],
        title=push_text.CALL_INCOMING_TITLE,
        body=push_text.call_incoming_body(caller_name, call_type),
        data=data,
        ttl_seconds=ttl_seconds,
        android_data_only=True,
    )
    return 1


@app.task(name="b2b.workspace.notify_incoming_call_voip")
def notify_incoming_call_voip(
    call_id: int,
    company_id: int,
    voip_token: str,
    caller_name: str,
    call_type: str,
    thread_id: int | None = None,
    ttl_seconds: int | None = None,
    avatar: str | None = None,
    fcm_token: str | None = None,
) -> int:
    """The same ring, to an iPhone through PushKit — see `apns_voip`.

    Falls back to the ordinary push when APNs will not take it, so a phone
    with a stale VoIP token is still told something.
    """
    from apps.b2b.workspace import apns_voip
    from apps.b2b.workspace import calls_repository as calls_repo

    call = calls_repo.get_call(call_id, company_id)
    if not call or call["status"] != calls_repo.CallStatus.RINGING:
        return 0
    window = int(ttl_seconds or 60)
    payload: dict[str, str] = {
        "type": "call",
        "action": "ringing",
        "call_id": str(call_id),
        "call_type": call_type,
        "caller_name": caller_name,
        "caller_id": str(call["initiator_id"]),
        "ring_ms": str(window * 1000),
    }
    if thread_id:
        payload["thread_id"] = str(thread_id)
    if avatar:
        payload["avatar"] = avatar
    if apns_voip.send(
        voip_token,
        payload,
        ttl_seconds=window,
        on_dead_token=lambda token: repo.clear_employee_voip_tokens([token]),
    ):
        return 1
    if fcm_token:
        return notify_incoming_call(
            call_id, company_id, fcm_token, caller_name, call_type, thread_id, ttl_seconds, avatar
        )
    return 0


@app.task(name="b2b.workspace.notify_missed_call")
def notify_missed_call(
    call_id: int,
    company_id: int,
    employee_id: int,
    caller_name: str,
    call_type: str,
    thread_id: int | None = None,
) -> int:
    """"Javobsiz qo'ng'iroq" — a feed row and a push that opens the chat, so
    the person finds the missed-call line the thread now carries."""
    employee = repo.get_workspace_employee(employee_id)
    if not employee:
        return 0
    title = push_text.CALL_MISSED_TITLE
    body = push_text.call_missed_body(caller_name, call_type)
    create_notification(
        company_id=employee["company_id"],
        employee_id=employee["id"],
        kind="chat" if thread_id else "call",
        title=title,
        body=body,
        payload={"call_id": call_id, "thread_id": thread_id},
    )
    data = {"type": "call", "action": "missed", "call_id": str(call_id), "call_type": call_type}
    if thread_id:
        data["thread_id"] = str(thread_id)
    _push(
        [{"employee_id": employee["id"], "company_id": employee["company_id"], "fcm_token": employee.get("fcm_token")}],
        title=title,
        body=body,
        data=data,
    )
    return 1


@app.task(name="b2b.workspace.notify_conference_invite")
def notify_conference_invite(
    conference_id: int,
    company_id: int,
    thread_id: int,
    title: str,
    organiser_name: str,
    employee_ids: list[int],
) -> int:
    """"Konferensiya · Aziz Karimov · Haftalik yig'ilish" to everybody
    invited — a feed row and a push that opens the group the invitation card
    is sitting in.

    One task for the whole invitation rather than one per person: a
    company-wide conference is a hundred rows, and a hundred queued tasks to
    write them is a hundred round trips to Redis for a notification that is
    already late by then.
    """
    sent = 0
    push_title = push_text.CONFERENCE_TITLE
    body = push_text.conference_invite_body(organiser_name, title)
    data = {
        "type": "conference",
        "action": "invited",
        "conference_id": str(conference_id),
        "thread_id": str(thread_id),
    }
    recipients = []
    for employee_id in employee_ids:
        employee = repo.get_workspace_employee(employee_id)
        if not employee:
            continue
        create_notification(
            company_id=employee["company_id"],
            employee_id=employee["id"],
            kind="chat",
            title=push_title,
            body=body,
            payload={"conference_id": conference_id, "thread_id": thread_id},
        )
        recipients.append({
            "employee_id": employee["id"],
            "company_id": employee["company_id"],
            "fcm_token": employee.get("fcm_token"),
        })
        sent += 1
    _push(recipients, title=push_title, body=body, data=data)
    return sent


@app.task(name="b2b.workspace.send_call_guest_link")
def send_call_guest_link(call_id: int, company_id: int, phone: str, link: str) -> bool:
    """The browser link to a lead or customer who is not in Weel, by SMS."""
    from apps.b2b.repository import get_company
    from apps.b2b.workspace import calls_repository as calls_repo

    try:
        from apps.users.services import EskizService

        company = get_company(company_id) or {}
        EskizService().send_text_sms(
            phone, push_text.call_guest_sms(company.get("name") or "", link)
        )
    except Exception:  # noqa: BLE001
        logger.exception("Guest link SMS failed for call %s", call_id)
        return False
    calls_repo.mark_guest_link_sent(call_id)
    return True


@app.task(name="b2b.workspace.expire_call")
def expire_call(call_id: int, company_id: int) -> bool:
    """The per-call ring timeout, queued with a countdown when the call is
    placed. A no-op if the call was answered or hung up in the meantime."""
    from apps.b2b.workspace import calls
    from apps.b2b.workspace import calls_repository as calls_repo

    call = calls_repo.get_call(call_id, company_id)
    if not call:
        return False
    return calls.settle(call) is not call


@app.task(name="b2b.workspace.expire_ringing_calls")
def expire_ringing_calls() -> int:
    """Every minute: whatever the countdown tasks missed — a worker that was
    restarted, a queue that was down."""
    from apps.b2b.workspace import calls

    return calls.expire_stale()


@app.task(name="b2b.workspace.end_stale_conferences")
def end_stale_conferences() -> int:
    """Conferences nobody closed. Rarer than a stale call and so on a slower
    beat: the organiser leaving does not end one — the others may carry on —
    so only the clock can shut a room that emptied out at lunchtime."""
    from apps.b2b.workspace import conferences

    return conferences.end_stale()
