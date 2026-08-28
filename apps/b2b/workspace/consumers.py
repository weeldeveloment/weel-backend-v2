"""The workspace's live connection.

One socket per signed-in employee, and it carries everything — not just chat.
The alternative, a socket per screen, means a handshake on every tab and tells
you nothing about the screens you are not looking at, which is exactly when a
change matters most: a task you were given while reading the calendar, a lead
somebody else claimed, your join request finally answered.

So the connection subscribes to three kinds of group:

* every **thread** the employee belongs to — messages, typing, read receipts;
* their own **employee** group — things addressed to them personally;
* their workspace's **company** group — shared state everyone sees, and who is
  online.

Delivery is always through the channel layer, never straight from the consumer
that received something. A REST ``POST /chats/<id>/messages/`` and a socket
frame therefore reach every other member by the same path, and neither has to
know the other exists. That is what lets the app keep *posting* over HTTP —
where it gets retries, auth refresh and a real status code — and use the socket
purely for what arrives.
"""
from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

from apps.b2b.workspace import presence
from apps.b2b.workspace import realtime
from apps.b2b.workspace import repository as repo
from apps.b2b.workspace.realtime import (  # noqa: F401  (imported for re-export)
    broadcast_deletion,
    broadcast_edit,
    broadcast_message,
)
from apps.b2b.workspace.tokens import WORKSPACE_USER_TYPE
from users.tokens import TokenMetadata

logger = logging.getLogger(__name__)

_CLOSE_TOKEN_MISSING = 4401
_CLOSE_TOKEN_INVALID = 4402

# Named the same way on both sides of the wire. The app matches on these
# strings, so they are part of the contract rather than an implementation
# detail of this file.
EVENT_MESSAGE = realtime.EVENT_MESSAGE
EVENT_TYPING = realtime.EVENT_TYPING
EVENT_READ = realtime.EVENT_READ
EVENT_DELETED = realtime.EVENT_DELETED

#: What a client may send. Everything else the socket carries goes one way.
#:
#: Sending a message stays on HTTP. A socket has no status code, no retry and
#: no token refresh, so a message posted over it either silently vanishes when
#: the connection is stale or has to grow all of that back — and the client
#: would still need the HTTP path for when the socket is down. Ephemeral
#: signals have none of that problem: losing a "typing" costs nothing.
_INBOUND_THREAD_EVENTS = {EVENT_TYPING, EVENT_READ}
_EVENT_PING = "ping"

thread_group = realtime.thread_group


class WorkspaceConsumer(AsyncWebsocketConsumer):
    """``ws://…/ws/b2b/workspace/chat/?token=<access>``.

    The path still says "chat" because shipped apps connect to it; what it
    carries has outgrown the name.

    The token goes in the query string because browsers cannot set headers on
    a WebSocket handshake. It is the same short-lived access token the REST API
    takes, and it is verified here rather than trusted — the socket is a second
    front door and gets the same lock.
    """

    async def connect(self):
        params = parse_qs(self.scope["query_string"].decode())
        token = (params.get("token") or [None])[0]
        if not token:
            await self.close(code=_CLOSE_TOKEN_MISSING)
            return

        claims = await self._verify(token)
        if not claims:
            await self.close(code=_CLOSE_TOKEN_INVALID)
            return

        self.employee_id = claims["employee_id"]
        self.company_id = claims["company_id"]

        # Every room this employee is in, resolved once at connect. A thread
        # created later is joined without a reconnect — see [add_to_thread],
        # which the chat views reach through the employee group below.
        self.thread_groups = {
            thread_group(t["id"])
            for t in await self._threads(self.company_id, self.employee_id)
        }
        self.own_groups = {
            realtime.employee_group(self.employee_id),
            realtime.company_group(self.company_id),
        }
        for group in self.thread_groups | self.own_groups:
            await self.channel_layer.group_add(group, self.channel_name)

        await self.accept()

        # Presence is announced after `accept`, never before: a failed
        # handshake that had already told the company somebody was online
        # would leave a green dot with nothing behind it.
        became_online = await self._mark_online()
        if became_online:
            await self._announce_presence(True)

        await self._send({
            "event": "connected",
            "threads": len(self.thread_groups),
            "heartbeat_seconds": presence.HEARTBEAT_SECONDS,
            # The roster's current state, so the app does not need a second
            # request to draw its first green dots.
            "online": sorted(await self._online_now()),
        })

    async def disconnect(self, close_code):
        for group in getattr(self, "thread_groups", set()) | getattr(self, "own_groups", set()):
            await self.channel_layer.group_discard(group, self.channel_name)

        if getattr(self, "employee_id", None) is None:
            return
        if await self._mark_offline():
            await self._announce_presence(False)

    async def receive(self, text_data=None, bytes_data=None):
        try:
            data = json.loads(text_data or "{}")
        except (TypeError, ValueError):
            await self._send({"event": "error", "detail": "Invalid JSON payload."})
            return

        event = data.get("event")

        # The heartbeat. Without one, a presence entry expires while somebody
        # is sitting on a screen not typing, and they blink offline to
        # everybody else while their socket is perfectly healthy.
        if event == _EVENT_PING:
            await self._touch()
            await self._send({"event": "pong"})
            return

        thread_id = data.get("thread_id")
        if event not in _INBOUND_THREAD_EVENTS or not isinstance(thread_id, int):
            await self._send({"event": "error", "detail": "Unknown event."})
            return

        if thread_group(thread_id) not in getattr(self, "thread_groups", set()):
            await self._send({"event": "error", "detail": "Not a member of this chat."})
            return

        payload: dict[str, Any] = {
            "thread_id": thread_id,
            "employee_id": self.employee_id,
        }
        if event == EVENT_READ:
            read_at = await self._mark_read(thread_id, self.employee_id)
            # What the other side needs to tick its own bubbles: everything it
            # sent up to this moment has been seen. Sending the timestamp
            # rather than a message id means a client that was mid-scroll does
            # not have to guess which bubbles it covers.
            payload["read_at"] = read_at
            payload["last_message_id"] = await self._last_message_id(thread_id)

        await self.channel_layer.group_send(
            thread_group(thread_id),
            {"type": "workspace.event", "event": event, **payload},
        )

    # ─── Channel-layer handlers ──────────────────────────────────────────────

    async def workspace_event(self, event: dict):
        """Fan-out from [realtime]. ``type`` is the routing key and never goes
        out on the wire."""
        await self._send({k: v for k, v in event.items() if k != "type"})

    async def thread_event(self, event: dict):
        """The envelope this consumer used before there was a bus.

        Kept because a deploy is not atomic: a worker still running the old
        code can put one of these on the layer after this one has started, and
        dropping it would lose a message rather than a frame nobody needed.
        """
        await self.workspace_event(event)

    async def thread_joined(self, event: dict):
        """Subscribes this connection to a room opened after it connected.

        Sent to the employee group by whoever created the thread. Without it a
        group chat you were added to stays silent until the app reconnects,
        which on a phone that is already awake may be a very long time.
        """
        thread_id = event.get("thread_id")
        if not isinstance(thread_id, int):
            return
        group = thread_group(thread_id)
        if group in self.thread_groups:
            return
        self.thread_groups.add(group)
        await self.channel_layer.group_add(group, self.channel_name)
        await self._send({"event": realtime.EVENT_THREAD, "action": "joined", "thread_id": thread_id})

    async def thread_left(self, event: dict):
        """The other half: somebody removed from a room, or leaving it."""
        thread_id = event.get("thread_id")
        if not isinstance(thread_id, int):
            return
        group = thread_group(thread_id)
        if group not in self.thread_groups:
            return
        self.thread_groups.discard(group)
        await self.channel_layer.group_discard(group, self.channel_name)
        await self._send({"event": realtime.EVENT_THREAD, "action": "left", "thread_id": thread_id})

    # ─── Helpers ─────────────────────────────────────────────────────────────

    async def _send(self, payload: dict[str, Any]) -> None:
        await self.send(text_data=json.dumps(payload, default=str))

    async def _announce_presence(self, online: bool) -> None:
        await self.channel_layer.group_send(
            realtime.company_group(self.company_id),
            {
                "type": "workspace.event",
                "event": realtime.EVENT_PRESENCE,
                "employee_id": self.employee_id,
                "online": online,
            },
        )

    @database_sync_to_async
    def _verify(self, token: str) -> dict[str, Any] | None:
        try:
            access = AccessToken(token)
        except TokenError:
            return None

        # A dashboard token carries a b2b_user id in the same claim an employee
        # token uses. Accepting either would resolve to whichever employee
        # happens to share that primary key.
        if access.get(TokenMetadata.TOKEN_USER_TYPE) != WORKSPACE_USER_TYPE:
            return None

        try:
            employee_id = int(access[TokenMetadata.TOKEN_SUBJECT])
        except (KeyError, TypeError, ValueError):
            return None

        employee = repo.get_workspace_employee(employee_id)
        if not employee:
            return None
        return {"employee_id": employee_id, "company_id": employee["company_id"]}

    @database_sync_to_async
    def _threads(self, company_id: int, employee_id: int) -> list[dict[str, Any]]:
        return repo.list_threads(company_id, employee_id)

    @database_sync_to_async
    def _mark_read(self, thread_id: int, employee_id: int) -> str | None:
        return repo.mark_thread_read(thread_id, employee_id)

    @database_sync_to_async
    def _last_message_id(self, thread_id: int) -> int | None:
        return repo.last_message_id(thread_id)

    @database_sync_to_async
    def _mark_online(self) -> bool:
        return presence.mark_online(self.employee_id)

    @database_sync_to_async
    def _mark_offline(self) -> bool:
        return presence.mark_offline(self.employee_id)

    @database_sync_to_async
    def _touch(self) -> None:
        presence.touch(self.employee_id)

    @database_sync_to_async
    def _online_now(self) -> set[int]:
        return presence.online_ids(repo.company_employee_ids(self.company_id))


#: The name the routing table and older imports use.
WorkspaceChatConsumer = WorkspaceConsumer


def add_to_thread(employee_ids, thread_id: int) -> None:
    """Tells whoever is connected to start listening to a room.

    Called after members are added, so a group chat opened while everybody is
    already online is live for all of them immediately.
    """
    _fanout(employee_ids, "thread.joined", thread_id)


def remove_from_thread(employee_ids, thread_id: int) -> None:
    """The other half of [add_to_thread]."""
    _fanout(employee_ids, "thread.left", thread_id)


def _fanout(employee_ids, envelope: str, thread_id: int) -> None:
    from asgiref.sync import async_to_sync
    from channels.layers import get_channel_layer

    try:
        layer = get_channel_layer()
        if layer is None:
            return
        for employee_id in {int(i) for i in employee_ids if i is not None}:
            async_to_sync(layer.group_send)(
                realtime.employee_group(employee_id),
                {"type": envelope, "thread_id": thread_id},
            )
    except Exception:  # noqa: BLE001
        logger.exception("Could not update thread membership for %s", thread_id)
