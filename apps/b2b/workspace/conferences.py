"""Conferences — a room many people are invited into at once.

A call rings one person and waits thirty seconds for an answer. A conference
does neither: it is *announced*, and whoever wants to be in it presses a
button. That difference is the whole design.

The flow, end to end:

1. Somebody who may create group chats posts ``/conferences/`` with a title
   and who it is for — the whole company, some departments, or a hand-picked
   list. The people are resolved to employee ids, a **group thread** is
   opened for exactly them, a row is written in ``b2b_conference`` with an
   unguessable LiveKit room, and the first message in that thread is the
   invitation: ``📹 <title>`` with ``#conf <id> live`` under it.
2. Everyone invited gets that message the way they get any other — the
   socket draws it in an open room, the push reaches an app that is asleep —
   and pressing **Kirish** posts ``/conferences/<id>/join/``, which hands
   that person their own token for that room.
3. The organiser presses **Tugatish**: the row is closed and the invitation
   card is rewritten in place, so nobody is left holding a button into an
   empty room.

Why the invitation is an ordinary chat message rather than a notification of
its own: the thread is where the people already are, it survives being
missed, and it gives the conference a place to be talked about afterwards.
The group is reused — a second conference for the same people lands in the
same room instead of opening another.

The media server is the one `calls.py` is pointed at, and the token layout is
that module's `sign_token` unchanged: a conference room is a room like any
other, and only the number of people in it differs. Nothing here asks LiveKit
anything either — this backend decides *who may enter which room* and says so
by signing.

Nothing that talks to another system may fail the request. The row is
committed and the organiser is waiting for a token; a push that does not go
out is logged, not raised.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Sequence

from django.conf import settings
from django.utils import timezone

from apps.b2b.workspace import calls
from apps.b2b.workspace import conferences_repository as conf_repo
from apps.b2b.workspace import push_text
from apps.b2b.workspace import realtime
from apps.b2b.workspace import repository as repo
from apps.b2b.workspace.calls import CallError
from apps.b2b.workspace.conferences_repository import ConferenceScope, ConferenceStatus

logger = logging.getLogger(__name__)

#: The marker line under the human one, read by the app to draw the card.
#: Same shape as `calls.CALL_LOG_TAG` and for the same reason — a chat message
#: has one text column, and a machine-readable second line is how a card is
#: carried in it without a parallel table of "special" messages.
CONF_TAG = "#conf"

#: How long a conference nobody closed stays advertised as running. The
#: organiser leaving does not end one — the others may well carry on — so the
#: only thing that can close a forgotten room is the clock.
MAX_DURATION_SECONDS = 14400


def max_duration() -> timedelta:
    return timedelta(
        seconds=int(getattr(settings, "CONFERENCE_MAX_DURATION_SECONDS", MAX_DURATION_SECONDS))
    )


# ─── Reading ──────────────────────────────────────────────────────────────────


def payload(
    conference: dict[str, Any],
    *,
    token: str | None = None,
    token_expires_at=None,
    thread: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One conference as the app reads it.

    The token is only ever on the response to the person it was signed for —
    it never rides a broadcast, exactly as with a call.
    """
    return {
        "id": conference["id"],
        "room_name": conference["room_name"],
        "title": conference["title"],
        "thread_id": conference["thread_id"],
        "message_id": conference.get("message_id"),
        "scope": conference["scope"],
        "status": conference["status"],
        "created_by": conference["created_by"],
        "started_at": conference.get("started_at"),
        "ended_at": conference.get("ended_at"),
        "provider": calls.provider(),
        "server_url": calls.server_url(),
        "token": token,
        "token_expires_at": token_expires_at,
        "thread": thread,
    }


def invite_text(conference: dict[str, Any]) -> str:
    """"📹 Haftalik yig'ilish\\n#conf 12 live" — the human line the chat list
    previews, and the marker the room draws a card from."""
    title = (conference.get("title") or "").strip() or push_text.CONFERENCE_TITLE
    return f"\U0001F4F9 {title}\n{CONF_TAG} {conference['id']} {conference['status']}"


# ─── Creating ─────────────────────────────────────────────────────────────────


def resolve_members(
    *,
    company_id: int,
    scope: str,
    department_ids: Sequence[int] | None,
    employee_ids: Sequence[int] | None,
) -> list[int]:
    """Who the invitation reaches, as employee ids.

    Raises rather than returning an empty list when a scope resolves to
    nobody: "Bo'limlar" with a department that has been emptied is a mistake
    worth reporting, not a conference of one.
    """
    if scope == ConferenceScope.ALL:
        members = conf_repo.company_employee_ids(company_id)
    elif scope == ConferenceScope.DEPARTMENTS:
        if not department_ids:
            raise CallError("Kamida bitta bo’lim tanlang.", status=400)
        members = conf_repo.employee_ids_in_departments(company_id, department_ids)
    elif scope == ConferenceScope.EMPLOYEES:
        if not employee_ids:
            raise CallError("Kamida bitta xodim tanlang.", status=400)
        valid = repo.employee_ids_in_company(company_id, list(employee_ids))
        if len(valid) != len(set(int(i) for i in employee_ids)):
            raise CallError("Bu xodimlarning ba’zisi sizning kompaniyangizda emas.", status=400)
        members = [int(i) for i in valid]
    else:
        raise CallError("Konferensiya kimlar uchun ekani noto’g’ri.", status=400)

    if not members:
        raise CallError("Bu tanlovda hech kim yo’q.", status=400)
    return members


def create(
    user,
    *,
    title: str,
    scope: str,
    department_ids: Sequence[int] | None = None,
    employee_ids: Sequence[int] | None = None,
) -> dict[str, Any]:
    """Open a conference and invite people into it.

    The organiser is always a member, whichever scope was chosen — a
    department they are not in still produces a room they can enter, because
    they are the one who called it.
    """
    calls._require_configured()

    title = (title or "").strip() or push_text.CONFERENCE_TITLE
    members = resolve_members(
        company_id=user.company_id,
        scope=scope,
        department_ids=department_ids,
        employee_ids=employee_ids,
    )
    others = [i for i in members if i != user.id]
    if not others:
        raise CallError("O’zingizdan boshqa hech kim tanlanmadi.", status=400)

    thread = repo.create_thread(
        company_id=user.company_id,
        created_by=user.id,
        member_ids=others,
        group_name=title,
    )
    if not thread:
        raise CallError("Konferensiya xonasi ochilmadi.", status=500)

    conference = conf_repo.create_conference(
        company_id=user.company_id,
        room_name=calls.new_room_name(),
        title=title,
        thread_id=thread["id"],
        scope=scope,
        created_by=user.id,
    )
    if not conference:
        raise CallError("Konferensiya yozilmadi.", status=500)

    message = _announce_invite(conference, user)
    if message:
        conf_repo.set_message(conference["id"], message["id"])
        conference["message_id"] = message["id"]

    _subscribe(thread["id"], [user.id, *others])
    _publish(conference, "started", [user.id, *others])
    _push_invite(conference, user, others)

    token, expires = calls.sign_token(
        room=conference["room_name"],
        user_id=str(user.id),
        name=_display_name(user.id),
        moderator=True,
    )
    return payload(conference, token=token, token_expires_at=expires, thread=thread)


# ─── Joining and ending ───────────────────────────────────────────────────────


def join(conference: dict[str, Any], user) -> dict[str, Any]:
    """This person's own token for the room.

    Membership of the thread is the whole access rule: the invitation went to
    a group, and being in that group is what "invited" means. Somebody added
    to the group later may therefore join a conference already running, which
    is the behaviour the organiser expects when they add a latecomer.
    """
    calls._require_configured()
    if not repo.is_thread_member(conference["thread_id"], user.id):
        raise CallError("Bu konferensiya sizga emas.", status=403)
    if conference["status"] != ConferenceStatus.LIVE:
        raise CallError("Bu konferensiya tugagan.", status=409)

    token, expires = calls.sign_token(
        room=conference["room_name"],
        user_id=str(user.id),
        name=_display_name(user.id),
        moderator=conference["created_by"] == user.id,
    )
    return payload(conference, token=token, token_expires_at=expires)


def end(conference: dict[str, Any], user) -> dict[str, Any] | None:
    """Close it for everybody.

    Only the organiser: the others leave by hanging up, and a conference that
    any attendee could end for the room would be ended by the first person to
    mistake "Tugatish" for "Chiqish".

    `None` when it was already closed — by the sweep, or by this person's
    other device. The room is shut either way, so the view answers with the
    row rather than with a refusal.
    """
    if conference["created_by"] != user.id:
        raise CallError("Konferensiyani faqat uni ochgan odam tugata oladi.", status=403)
    closed = _close(conference)
    return payload(closed) if closed else None


def settle(conference: dict[str, Any]) -> dict[str, Any]:
    """The row as it truly is: one that outlived the maximum duration is
    written down as ended before anybody reads it, so a read path never has
    to trust the sweep having run.

    A **row**, not a payload — every caller of this goes on to load, check or
    serialise it, and handing back the wire shape here would mean the rest of
    the module quietly working on two different kinds of dictionary.
    """
    if conference["status"] != ConferenceStatus.LIVE:
        return conference
    started = conference.get("started_at")
    if started and timezone.now() - started > max_duration():
        return _close(conference) or conference
    return conference


def end_stale() -> int:
    """Every conference older than the maximum duration, closed in one pass.
    Runs from Celery beat beside the calls sweep."""
    count = 0
    for conference in conf_repo.stale_live(timezone.now() - max_duration()):
        if _close(conference):
            count += 1
    return count


def _close(conference: dict[str, Any]) -> dict[str, Any] | None:
    """The closed row, or `None` if it was already closed.

    Conditional in the repository, so two devices pressing "Tugatish" at once
    — or the sweep racing the organiser — announce the ending exactly once.
    """
    closed = conf_repo.finish(conference["id"])
    if not closed:
        return None
    _rewrite_invite(closed)
    _publish(closed, "ended", repo.thread_member_ids(closed["thread_id"]))
    return closed


# ─── The invitation card ──────────────────────────────────────────────────────


def _announce_invite(conference: dict[str, Any], user) -> dict[str, Any] | None:
    """The first message in the new group: the card people press."""
    try:
        message = repo.send_message(conference["thread_id"], user.id, invite_text(conference))
        if not message:
            return None
        from apps.b2b.workspace.views import _message_payload

        realtime.broadcast_message(
            conference["thread_id"], _message_payload(message, viewer_id=user.id)
        )
        return message
    except Exception:  # noqa: BLE001
        logger.exception("Could not announce conference %s", conference["id"])
        return None


def _rewrite_invite(conference: dict[str, Any]) -> None:
    """The same card, now saying the conference is over.

    Rewritten rather than followed by a second message: two cards in a row,
    one of them offering a button into an empty room, is worse than one card
    that tells the truth.
    """
    message_id = conference.get("message_id")
    if not message_id:
        return
    try:
        message = repo.edit_message(
            message_id, conference["thread_id"], invite_text(conference)
        )
        if not message:
            return
        from apps.b2b.workspace.views import _message_payload

        realtime.publish_thread(
            conference["thread_id"],
            realtime.EVENT_EDITED,
            message=_message_payload(message, viewer_id=conference["created_by"]),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Could not rewrite conference card %s", conference["id"])


# ─── Telling people ───────────────────────────────────────────────────────────


def _publish(conference: dict[str, Any], action: str, employee_ids: Sequence[int]) -> None:
    try:
        realtime.publish_employees(
            [int(i) for i in employee_ids],
            realtime.EVENT_CONFERENCE,
            action=action,
            conference=payload(conference),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Could not announce conference %s (%s)", conference["id"], action)


def _subscribe(thread_id: int, employee_ids: Sequence[int]) -> None:
    """Everyone starts listening to the new room now rather than on their
    next reconnect — without this the group is silent for all of them but its
    creator, which looks exactly like the feature being broken."""
    try:
        from apps.b2b.workspace.views import add_to_thread

        add_to_thread([int(i) for i in employee_ids], thread_id)
    except Exception:  # noqa: BLE001
        logger.exception("Could not subscribe members to thread %s", thread_id)


def _push_invite(conference: dict[str, Any], user, employee_ids: Sequence[int]) -> None:
    """A push for the phones that are asleep. The socket has already reached
    the ones that are not, and the app ignores whichever arrives second.

    Off the request: a company-wide conference is a hundred notification rows
    and a Firebase round trip, and the organiser is waiting for a token.
    """
    try:
        from apps.b2b.workspace.tasks import notify_conference_invite

        notify_conference_invite.delay(
            conference["id"],
            conference["company_id"],
            conference["thread_id"],
            conference["title"],
            _display_name(user.id),
            [int(i) for i in employee_ids],
        )
    except Exception:  # noqa: BLE001
        logger.exception("Could not push conference %s", conference["id"])


def _cards(employee_ids: Sequence[int]) -> dict[int, dict[str, Any]]:
    from apps.b2b.workspace import calls_repository as calls_repo

    return calls_repo.employee_cards(list(employee_ids))


def _display_name(employee_id: int) -> str:
    card = _cards([employee_id]).get(employee_id) or {}
    return card.get("full_name") or "Weel"
