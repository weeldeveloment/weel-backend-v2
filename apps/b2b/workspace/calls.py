"""Live video/audio calls over a self-hosted Jitsi Meet — the service half.

TZ "Weel B2B — Jonli video/audio qo'ng'iroq (Jitsi Meet integratsiyasi)",
stages 1–2. The flow, end to end:

1. A phone posts ``/calls/``. A row is written in ``b2b_call`` with a room
   name nobody can guess, the caller is handed a JWT for that room, and the
   person being rung is told two ways at once — a ``call`` frame on their
   socket, and a push in case the app is asleep.
2. They accept (``/accept``, and get their own token), decline, or do nothing
   for [ring timeout] seconds, after which the call is written down as missed.
3. Either side hangs up (``/end``). The duration is counted from the moment
   it was answered, and the chat thread gets a system message: "Video
   qo'ng'iroq · 4:12" or "Javobsiz qo'ng'iroq".

Jitsi itself is never asked anything. The media path is between the two
phones and the videobridge; this backend only decides *who may enter which
room*, and says so by signing tokens. A token names one room and expires, so
neither a leaked token nor a guessed room name opens a conversation that is
not yours (TZ §8).

Every state change is a conditional UPDATE — see ``calls_repository
.transition`` — so a phone answering a call the timeout has just marked
missed, or two phones of one person both accepting, settle on one answer.

Nothing here that talks to another system may fail the request: the socket,
the push, the SMS and the chat message are each wrapped, because the call row
is already committed and the phone is waiting for its token.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any
from urllib.parse import quote

import jwt
from django.conf import settings
from django.utils import timezone

from apps.b2b.workspace import push_text
from apps.b2b.workspace import realtime
from apps.b2b.workspace import repository as repo
from apps.b2b.workspace.calls_repository import (
    CallSource,
    CallStatus,
    CallType,
)
from apps.b2b.workspace import calls_repository as calls_repo
from apps.b2b.workspace.storage import photo_url

logger = logging.getLogger(__name__)


class CallError(Exception):
    """A refusal the view turns into a response — the status code rides
    along so the service decides what kind of refusal it is."""

    def __init__(self, detail: str, status: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status = status


# ─── Configuration ────────────────────────────────────────────────────────────


def is_configured() -> bool:
    return bool(settings.JITSI_SERVER_URL and settings.JITSI_APP_SECRET)


def _require_configured() -> None:
    if not is_configured():
        raise CallError(
            "Video qo’ng’iroq serveri hali sozlanmagan. Administratorga murojaat qiling.",
            status=503,
        )


def ring_timeout() -> timedelta:
    return timedelta(seconds=int(getattr(settings, "CALL_RING_TIMEOUT_SECONDS", 30)))


def max_call_duration() -> timedelta:
    return timedelta(seconds=int(getattr(settings, "CALL_MAX_DURATION_SECONDS", 14400)))


# ─── Tokens ───────────────────────────────────────────────────────────────────


def new_room_name() -> str:
    """`weel-<uuid4>`: unique, unguessable, and legal in a URL and in XMPP."""
    return f"weel-{uuid.uuid4().hex}"


def sign_token(
    *,
    room: str,
    user_id: str,
    name: str,
    avatar: str | None = None,
    moderator: bool = False,
    ttl_seconds: int | None = None,
) -> tuple[str, datetime]:
    """A Jitsi JWT for one person in one room.

    The claim layout is the one `docker-jitsi-meet` verifies with
    `AUTH_TYPE=jwt`: `iss` is the app id, `aud` is literally "jitsi", `sub`
    the host (or "*"), `room` the room the token opens, and `context.user`
    is what the other participants see this person as — so the name and
    picture on the tile are the ones Weel knows, not whatever the phone had
    typed into Jitsi last.
    """
    now = int(time.time())
    ttl = int(ttl_seconds or settings.JITSI_TOKEN_TTL_SECONDS)
    expires = now + ttl
    payload = {
        "iss": settings.JITSI_APP_ID,
        "aud": "jitsi",
        "sub": settings.JITSI_JWT_SUB,
        "room": room,
        # Ten seconds of grace for a phone whose clock runs slightly ahead of
        # the server's — a token "not yet valid" is the least explicable
        # failure a caller can meet.
        "nbf": now - 10,
        "iat": now,
        "exp": expires,
        "context": {
            "user": {
                "id": str(user_id),
                "name": name or "Weel",
                "avatar": avatar or "",
                "email": "",
                "moderator": "true" if moderator else "false",
            },
            "features": {
                "recording": "false",
                "livestreaming": "false",
                "screen-sharing": "false",
                "outbound-call": "false",
            },
        },
    }
    token = jwt.encode(payload, settings.JITSI_APP_SECRET, algorithm="HS256")
    if isinstance(token, bytes):  # PyJWT < 2 returned bytes
        token = token.decode()
    return token, datetime.fromtimestamp(expires, tz=dt_timezone.utc)


def guest_link(call: dict[str, Any], display_name: str) -> str:
    """The browser URL a lead or customer joins from — Jitsi's own web UI,
    with a short-lived token in the fragment so it never reaches a proxy
    log as a query string."""
    token, _ = sign_token(
        room=call["room_name"],
        user_id=f"guest-{call['id']}",
        name=display_name,
        ttl_seconds=settings.JITSI_GUEST_LINK_TTL_SECONDS,
    )
    return f"{settings.JITSI_SERVER_URL}/{quote(call['room_name'])}?jwt={token}"


# ─── Payload ──────────────────────────────────────────────────────────────────


def _person(card: dict[str, Any] | None) -> dict[str, Any] | None:
    if not card:
        return None
    return {
        "id": card["id"],
        "name": card.get("full_name") or "",
        "photo": photo_url(card.get("photo")),
    }


def payload(
    call: dict[str, Any],
    *,
    token: str | None = None,
    token_expires_at: datetime | None = None,
    guest_link_url: str | None = None,
    cards: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One call as the app reads it. The token is only present on the
    response to the person it was signed for — it never rides a broadcast."""
    if cards is None:
        cards = calls_repo.employee_cards(
            [call.get("initiator_id"), call.get("target_employee_id")]
        )
    return {
        "id": call["id"],
        "room_name": call["room_name"],
        "type": call["type"],
        "source_module": call["source_module"],
        "status": call["status"],
        "initiator": _person(cards.get(call.get("initiator_id"))),
        "target": _person(cards.get(call.get("target_employee_id"))),
        "thread_id": call.get("thread_id"),
        "lead_id": call.get("target_lead_id"),
        "customer_id": call.get("target_customer_id"),
        "started_at": call.get("started_at"),
        "answered_at": call.get("answered_at"),
        "ended_at": call.get("ended_at"),
        "duration_seconds": call.get("duration_seconds"),
        "server_url": settings.JITSI_SERVER_URL,
        "ring_timeout_seconds": int(ring_timeout().total_seconds()),
        "token": token,
        "token_expires_at": token_expires_at,
        "guest_link": guest_link_url,
        "guest_link_sent_at": call.get("guest_link_sent_at"),
    }


def _participants(call: dict[str, Any]) -> list[int]:
    return [i for i in (call.get("initiator_id"), call.get("target_employee_id")) if i]


def is_participant(call: dict[str, Any], employee_id: int) -> bool:
    return employee_id in _participants(call)


# ─── Starting a call ──────────────────────────────────────────────────────────


def start(
    *,
    user,
    call_type: str,
    source_module: str,
    thread_id: int | None = None,
    target_employee_id: int | None = None,
    lead_id: int | None = None,
    customer_id: int | None = None,
) -> dict[str, Any]:
    """Opens a call and rings the other side. Returns the caller's payload,
    token included."""
    _require_configured()
    if call_type not in CallType.CHOICES:
        raise CallError("type must be audio or video.")
    if source_module not in CallSource.CHOICES:
        raise CallError("source_module must be chat, crm or sales.")

    company_id = user.company_id
    target: dict[str, Any] | None = None
    thread: dict[str, Any] | None = None
    lead: dict[str, Any] | None = None
    customer: dict[str, Any] | None = None

    if source_module == CallSource.CHAT:
        thread, target = _resolve_chat_target(user, thread_id, target_employee_id)
    elif source_module == CallSource.SALES:
        if not lead_id:
            raise CallError("lead_id is required for a sales call.")
        lead = repo.get_lead(lead_id, company_id)
        if not lead:
            raise CallError("Lead not found.", status=404)
    else:
        if not customer_id:
            raise CallError("customer_id is required for a CRM call.")
        customer = repo.get_customer(customer_id, company_id)
        if not customer:
            raise CallError("Customer not found.", status=404)

    # One call at a time, each side. A caller already on a call cannot start
    # another, and a colleague already ringing or talking is "band" rather
    # than being rung twice — the second ring would only replace the first
    # on their screen.
    # The caller's own line is released rather than refused. The app will not
    # place a call while it believes one is running — `CallService.startCall`
    # returns early on `hasLiveCall` — so a live row belonging to the person
    # *making* this request is a row their phone has already forgotten: a
    # hang-up whose `/end` was lost, or an app killed mid-call. Refusing it
    # made the caller "band" to themselves until the row aged out, which is
    # the bug this exists to close. `end` writes it down honestly — cancelled
    # if it was still ringing, ended with its duration if it was answered.
    mine = _live_after_expiry(user.id)
    if mine:
        try:
            end(mine, user)
        except CallError:
            # Somebody settled it between the read and here; either way the
            # line is free now.
            pass

    # The other side is different: their row may be a real conversation with
    # a third person, and no request of ours may hang that up. A stale one of
    # theirs is closed by the maximum-duration sweep instead. A stale row from
    # the call *we* just shared is the same row as `mine` above, so it has
    # already been settled by the time we look.
    if target and _live_after_expiry(target["id"]):
        raise CallError("Bu xodim hozir band.", status=409)

    call = calls_repo.create_call(
        company_id=company_id,
        room_name=new_room_name(),
        call_type=call_type,
        source_module=source_module,
        initiator_id=user.id,
        target_employee_id=target["id"] if target else None,
        target_lead_id=lead["id"] if lead else None,
        target_customer_id=customer["id"] if customer else None,
        thread_id=thread["id"] if thread else None,
    )
    if not call:
        raise CallError("Qo’ng’iroq yaratilmadi.", status=500)

    cards = calls_repo.employee_cards(_participants(call))
    me = cards.get(user.id) or {"id": user.id, "full_name": getattr(user, "full_name", "")}
    token, expires = sign_token(
        room=call["room_name"],
        user_id=user.id,
        name=me.get("full_name") or "",
        avatar=photo_url(me.get("photo")),
        moderator=True,
    )

    link: str | None = None
    if target:
        _ring(call, cards)
    else:
        # An outside person: no app to ring, so they get a browser link. The
        # SMS goes out off the request, and the link is also handed back so
        # the manager can share it any other way — a lead who wrote in on
        # Telegram is not going to look at their SMS inbox.
        link = _send_guest_link(call, lead or customer)

    _schedule_expiry(call)
    return payload(call, token=token, token_expires_at=expires, guest_link_url=link, cards=cards)


def _resolve_chat_target(user, thread_id, target_employee_id):
    """The colleague a chat call is for, and the direct thread it belongs to.

    Given a thread, it has to be a direct one the caller is in — a group call
    is a later stage (TZ §2.2). Given only a person, the direct thread between
    the two is found or opened, so the call log has a room to land in even
    when nobody has typed anything yet.
    """
    company_id = user.company_id
    if thread_id:
        thread = repo.get_thread_for_member(thread_id, company_id, user.id)
        if not thread:
            raise CallError("Chat not found.", status=404)
        if thread.get("group_name"):
            raise CallError("Guruh qo’ng’irog’i hozircha mavjud emas.", status=400)
        others = [i for i in (thread.get("participant_ids") or []) if i != user.id]
        if len(others) != 1:
            raise CallError("Bu chatda qo’ng’iroq qilib bo’lmaydi.", status=400)
        target_employee_id = others[0]
    if not target_employee_id:
        raise CallError("thread_id or target_employee_id is required.")
    if int(target_employee_id) == int(user.id):
        raise CallError("O’zingizga qo’ng’iroq qila olmaysiz.")

    target = repo.get_workspace_employee(int(target_employee_id))
    if not target or target.get("company_id") != company_id:
        raise CallError("Xodim topilmadi.", status=404)

    if not thread_id:
        thread = repo.find_direct_thread(company_id, user.id, target["id"])
        if not thread:
            thread = repo.create_thread(
                company_id=company_id,
                created_by=user.id,
                member_ids=[user.id, target["id"]],
            )
            if thread:
                # The other side's socket has to start listening to the room
                # the call log will land in. Lazy import: `consumers` pulls
                # channels in, and the service is imported by the worker too.
                try:
                    from apps.b2b.workspace.consumers import add_to_thread

                    add_to_thread([user.id, target["id"]], thread["id"])
                except Exception:  # noqa: BLE001
                    logger.exception("Could not subscribe to the new thread")
    return thread, target


def _live_after_expiry(employee_id: int) -> dict[str, Any] | None:
    """Whether this person is on a call — after settling anything that has
    outlived its window, so a colleague who never answered an hour ago, or a
    conversation whose `/end` was lost this morning, is not "band" for the
    rest of the day if the worker missed it."""
    live = calls_repo.live_call_for(employee_id)
    if not live:
        return None
    settled = settle(live)
    return settled if settled["status"] in CallStatus.LIVE else None


# ─── Answering, refusing, hanging up ──────────────────────────────────────────


def accept(call: dict[str, Any], user) -> dict[str, Any]:
    """The person being rung picks up. Returns their payload, token included."""
    _require_configured()
    if call.get("target_employee_id") != user.id:
        raise CallError("Bu qo’ng’iroq sizga emas.", status=403)
    if _ring_expired(call):
        expire(call)
        raise CallError("Qo’ng’iroq vaqti tugagan.", status=409)

    now = timezone.now()
    updated = calls_repo.transition(
        call["id"],
        to=CallStatus.ACCEPTED,
        only_from=[CallStatus.RINGING],
        answered_at=now,
    )
    if not updated:
        # Somebody got there first: the caller hung up, the timeout fired, or
        # this person's other phone answered. Tell them which, not "400".
        current = calls_repo.get_call(call["id"], call["company_id"]) or call
        if current["status"] == CallStatus.ACCEPTED:
            raise CallError("Qo’ng’iroq boshqa qurilmada qabul qilingan.", status=409)
        raise CallError("Qo’ng’iroq allaqachon tugagan.", status=409)

    cards = calls_repo.employee_cards(_participants(updated))
    me = cards.get(user.id) or {}
    token, expires = sign_token(
        room=updated["room_name"],
        user_id=user.id,
        name=me.get("full_name") or getattr(user, "full_name", "") or "",
        avatar=photo_url(me.get("photo")),
    )
    _announce(updated, "accepted", cards)
    return payload(updated, token=token, token_expires_at=expires, cards=cards)


def decline(call: dict[str, Any], user) -> dict[str, Any]:
    if call.get("target_employee_id") != user.id:
        raise CallError("Bu qo’ng’iroq sizga emas.", status=403)
    updated = calls_repo.transition(
        call["id"],
        to=CallStatus.DECLINED,
        only_from=[CallStatus.RINGING],
        ended_at=timezone.now(),
        ended_by=user.id,
    )
    if not updated:
        return payload(calls_repo.get_call(call["id"], call["company_id"]) or call)
    cards = calls_repo.employee_cards(_participants(updated))
    _announce(updated, "declined", cards)
    _log_to_chat(updated)
    return payload(updated, cards=cards)


def end(call: dict[str, Any], user) -> dict[str, Any]:
    """Either side hangs up.

    While still ringing this is the *caller* giving up — written down as
    cancelled, which the other side's chat shows as a missed call. Once
    answered it is the end of the conversation, and the duration is counted.
    """
    if not is_participant(call, user.id):
        raise CallError("Bu qo’ng’iroq sizga emas.", status=403)
    now = timezone.now()

    if call["status"] == CallStatus.RINGING:
        to = CallStatus.CANCELLED if call["initiator_id"] == user.id else CallStatus.DECLINED
        updated = calls_repo.transition(
            call["id"], to=to, only_from=[CallStatus.RINGING], ended_at=now, ended_by=user.id
        )
    else:
        answered = call.get("answered_at") or now
        seconds = max(0, int((now - answered).total_seconds()))
        updated = calls_repo.transition(
            call["id"],
            to=CallStatus.ENDED,
            only_from=[CallStatus.ACCEPTED],
            ended_at=now,
            duration_seconds=seconds,
            ended_by=user.id,
        )

    if not updated:
        # Already over — the other side hung up a moment earlier. That is the
        # answer the phone wanted anyway.
        return payload(calls_repo.get_call(call["id"], call["company_id"]) or call)

    cards = calls_repo.employee_cards(_participants(updated))
    _announce(updated, "ended", cards)
    _log_to_chat(updated)
    if updated["status"] == CallStatus.CANCELLED:
        _push_missed(updated, cards)
    return payload(updated, cards=cards)


def fresh_token(call: dict[str, Any], user) -> dict[str, Any]:
    """A new token for a call still in progress — the phone asks when the one
    it holds is about to expire, or when it has to rejoin after a drop."""
    _require_configured()
    if not is_participant(call, user.id):
        raise CallError("Bu qo’ng’iroq sizga emas.", status=403)
    if call["status"] not in CallStatus.LIVE:
        raise CallError("Qo’ng’iroq tugagan.", status=409)
    cards = calls_repo.employee_cards(_participants(call))
    me = cards.get(user.id) or {}
    token, expires = sign_token(
        room=call["room_name"],
        user_id=user.id,
        name=me.get("full_name") or getattr(user, "full_name", "") or "",
        avatar=photo_url(me.get("photo")),
        moderator=call["initiator_id"] == user.id,
    )
    return payload(call, token=token, token_expires_at=expires, cards=cards)


# ─── The ring timeout ─────────────────────────────────────────────────────────


def _ring_expired(call: dict[str, Any]) -> bool:
    if call.get("status") != CallStatus.RINGING:
        return False
    started = call.get("started_at")
    if started is None:
        return False
    return timezone.now() - started > ring_timeout()


def _talk_expired(call: dict[str, Any]) -> bool:
    """An answered call nobody ever closed.

    `/end` is the only thing that ends a conversation, and it is the request
    most likely to be lost — the network is worst at the moment a call drops,
    and an app killed mid-call never sends it. Left alone the row stays
    `accepted` for ever and both people are "band" for ever with it.
    """
    if call.get("status") != CallStatus.ACCEPTED:
        return False
    answered = call.get("answered_at") or call.get("started_at")
    if answered is None:
        return False
    return timezone.now() - answered > max_call_duration()


def settle(call: dict[str, Any]) -> dict[str, Any]:
    """The row as it truly is: a ring that outlived its window is written
    down as missed, and a conversation that outlived the maximum duration as
    ended, before anybody reads it. Used by every read path, so the Celery
    timeout is a convenience rather than a dependency."""
    if _ring_expired(call):
        return expire(call) or call
    if _talk_expired(call):
        return abandon(call) or call
    return call


def expire(call: dict[str, Any]) -> dict[str, Any] | None:
    """Nobody answered. Missed on both sides, a line in the chat, and a push
    so the person who was rung finds out even though their phone never rang
    loud enough."""
    updated = calls_repo.transition(
        call["id"],
        to=CallStatus.MISSED,
        only_from=[CallStatus.RINGING],
        ended_at=timezone.now(),
    )
    if not updated:
        return None
    cards = calls_repo.employee_cards(_participants(updated))
    _announce(updated, "missed", cards)
    _log_to_chat(updated)
    _push_missed(updated, cards)
    return updated


def abandon(call: dict[str, Any]) -> dict[str, Any] | None:
    """An answered call whose `/end` never arrived, closed by the server.

    Written down as `ended` with the duration it is known to have had, the
    same as a normal hang-up: from the outside nothing distinguishes this
    from a conversation whose last request was lost, because that is what it
    is. `ended_by` stays null — nobody pressed anything.
    """
    answered = call.get("answered_at") or call.get("started_at")
    now = timezone.now()
    seconds = max(0, int((now - answered).total_seconds())) if answered else 0
    updated = calls_repo.transition(
        call["id"],
        to=CallStatus.ENDED,
        only_from=[CallStatus.ACCEPTED],
        ended_at=now,
        duration_seconds=seconds,
    )
    if not updated:
        return None
    cards = calls_repo.employee_cards(_participants(updated))
    _announce(updated, "ended", cards)
    _log_to_chat(updated)
    return updated


def expire_stale() -> int:
    """The safety net behind the per-call timeout — every ring older than the
    window and every conversation older than the maximum duration, settled in
    one pass. Runs from Celery beat."""
    count = 0
    for call in calls_repo.stale_ringing(timezone.now() - ring_timeout()):
        if expire(call):
            count += 1
    for call in calls_repo.stale_accepted(timezone.now() - max_call_duration()):
        if abandon(call):
            count += 1
    return count


def _schedule_expiry(call: dict[str, Any]) -> None:
    """Asks the worker to settle this call once the window has passed.

    Skipped under eager Celery (DEBUG): an eager `countdown` runs *now*, which
    would mark every call missed the moment it was placed. The read paths
    settle expired rings themselves — see [settle] — so nothing is lost.
    """
    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        return
    try:
        from apps.b2b.workspace.tasks import expire_call

        expire_call.apply_async(
            args=[call["id"], call["company_id"]],
            countdown=int(ring_timeout().total_seconds()) + 2,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Could not schedule the ring timeout for call %s", call["id"])


# ─── Telling the other side ───────────────────────────────────────────────────


def _announce(call: dict[str, Any], action: str, cards: dict[int, dict[str, Any]]) -> None:
    """One `call` frame to each participant's socket. Both sides always: the
    caller needs "accepted" to stop showing the ringing screen, and the
    callee's *other* devices need "accepted" to stop ringing."""
    try:
        realtime.publish_employees(
            _participants(call),
            realtime.EVENT_CALL,
            action=action,
            call=payload(call, cards=cards),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Could not announce call %s (%s)", call["id"], action)


def _ring(call: dict[str, Any], cards: dict[int, dict[str, Any]]) -> None:
    """The socket frame and the push, together. The socket reaches an app
    that is open; the push reaches one that is not. Both carry the same
    call id, and the app ignores whichever arrives second."""
    _announce(call, "ringing", cards)

    target = cards.get(call.get("target_employee_id"))
    caller = cards.get(call.get("initiator_id")) or {}
    if not target or not target.get("fcm_token"):
        return
    name = caller.get("full_name") or "Weel"
    try:
        from apps.b2b.workspace.tasks import notify_incoming_call

        notify_incoming_call.delay(
            call["id"],
            call["company_id"],
            target["fcm_token"],
            name,
            call["type"],
            call.get("thread_id"),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Could not queue the incoming-call push for %s", call["id"])


def _push_missed(call: dict[str, Any], cards: dict[int, dict[str, Any]]) -> None:
    """"Javobsiz qo'ng'iroq" to the person who did not pick up — a feed row
    and a push that opens the chat, so it sits next to the missed-call line
    the thread now carries."""
    target = cards.get(call.get("target_employee_id"))
    caller = cards.get(call.get("initiator_id")) or {}
    if not target:
        return
    try:
        from apps.b2b.workspace.tasks import notify_missed_call

        notify_missed_call.delay(
            call["id"],
            call["company_id"],
            target["id"],
            caller.get("full_name") or "Weel",
            call["type"],
            call.get("thread_id"),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Could not queue the missed-call push for %s", call["id"])


# ─── The line in the chat ─────────────────────────────────────────────────────

#: Marker on the machine-readable last line of a call log message. The app
#: parses it back into a localised row; the dashboard and a push show the
#: human line above it. Kept in the message text rather than in a new column
#: so every existing reader of `b2b_chat_message` keeps working.
CALL_LOG_TAG = "#call"


def call_log_text(call: dict[str, Any]) -> str:
    """"📞 Video qo'ng'iroq · 4:12\\n#call video ended 252"."""
    seconds = int(call.get("duration_seconds") or 0)
    status = call["status"]
    label = push_text.call_log_label(call["type"], status, seconds)
    return f"\U0001F4DE {label}\n{CALL_LOG_TAG} {call['type']} {status} {seconds}"


def _log_to_chat(call: dict[str, Any]) -> None:
    """Writes the outcome into the direct thread as a message from the
    caller, and broadcasts it like any other so an open room draws it at
    once. Only chat calls have a thread; a lead's call is its own history."""
    thread_id = call.get("thread_id")
    if not thread_id:
        return
    try:
        message = repo.send_message(thread_id, call["initiator_id"], call_log_text(call))
        if not message:
            return
        from apps.b2b.workspace.views import _message_payload

        realtime.broadcast_message(thread_id, _message_payload(message, viewer_id=call["initiator_id"]))
    except Exception:  # noqa: BLE001
        logger.exception("Could not log call %s to thread %s", call["id"], thread_id)


# ─── Outside people ───────────────────────────────────────────────────────────


def _send_guest_link(call: dict[str, Any], card: dict[str, Any] | None) -> str | None:
    """The browser link for a lead or customer, sent by SMS off the request.
    Returned either way so the manager can share it by hand."""
    if not card:
        return None
    name = card.get("full_name") or card.get("contact_full_name") or "Mijoz"
    link = guest_link(call, name)
    phone = card.get("phone") or card.get("contact_phone")
    if not phone:
        return link
    try:
        from apps.b2b.workspace.tasks import send_call_guest_link

        send_call_guest_link.delay(call["id"], call["company_id"], str(phone), link)
    except Exception:  # noqa: BLE001
        logger.exception("Could not queue the guest link SMS for call %s", call["id"])
    return link
