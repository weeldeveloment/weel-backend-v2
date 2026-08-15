"""Real-time workspace chat.

One socket per signed-in employee, subscribed to every thread they belong to.
The alternative — a socket per open room — means reconnecting on every tap in
the thread list, and it tells you nothing about rooms you are not looking at,
which is exactly when a new message matters most.

Delivery is always through the channel layer, never straight from the consumer
that received the message. A REST ``POST /chats/<id>/messages/`` and a socket
``send`` therefore reach every other member by the same path, and neither has
to know the other exists. That is what lets the app keep posting over HTTP —
where it gets retries, auth refresh and a real status code — and use the socket
purely for what arrives.
"""
from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import parse_qs

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.layers import get_channel_layer
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

from apps.b2b.workspace import repository as repo
from apps.b2b.workspace.tokens import WORKSPACE_USER_TYPE
from users.tokens import TokenMetadata

logger = logging.getLogger(__name__)

_CLOSE_TOKEN_MISSING = 4401
_CLOSE_TOKEN_INVALID = 4402

# Named the same way on both sides of the wire. The app matches on these
# strings, so they are part of the contract rather than an implementation
# detail of this file.
EVENT_MESSAGE = "message"
EVENT_TYPING = "typing"
EVENT_READ = "read"


def thread_group(thread_id: int) -> str:
    return f"ws.thread.{thread_id}"


def broadcast_message(thread_id: int, payload: dict[str, Any]) -> None:
    """Pushes a message to everyone in the thread.

    Called from the REST view, which is synchronous, so it wraps the async
    channel-layer call. Never raises: a socket that could not be reached is not
    a reason to tell the sender their message failed — it is already in the
    database, and the other members will see it when they next load the room.
    """
    try:
        channel_layer = get_channel_layer()
        if channel_layer is None:
            return
        async_to_sync(channel_layer.group_send)(
            thread_group(thread_id),
            {"type": "thread.event", "event": EVENT_MESSAGE, "message": payload},
        )
    except Exception:  # noqa: BLE001
        logger.exception("Could not broadcast message for thread %s", thread_id)


class WorkspaceChatConsumer(AsyncWebsocketConsumer):
    """``ws://…/ws/b2b/workspace/chat/?token=<access>``.

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

        # Subscribed to every room this employee is in, resolved once at
        # connect. A thread created later is picked up on the next reconnect,
        # which is also when the app refetches the list anyway.
        self.groups_joined = [
            thread_group(t["id"]) for t in await self._threads(self.company_id, self.employee_id)
        ]
        for group in self.groups_joined:
            await self.channel_layer.group_add(group, self.channel_name)

        await self.accept()
        await self._send({"event": "connected", "threads": len(self.groups_joined)})

    async def disconnect(self, close_code):
        for group in getattr(self, "groups_joined", []):
            await self.channel_layer.group_discard(group, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        """Typing and read receipts only.

        Sending a message stays on HTTP. A socket has no status code, no retry
        and no token refresh, so a message posted over it either silently
        vanishes when the connection is stale or has to grow all of that back —
        and the client would still need the HTTP path for when the socket is
        down. Ephemeral signals have none of that problem: losing a "typing"
        costs nothing.
        """
        try:
            data = json.loads(text_data or "{}")
        except (TypeError, ValueError):
            await self._send({"event": "error", "detail": "Invalid JSON payload."})
            return

        event = data.get("event")
        thread_id = data.get("thread_id")
        if event not in (EVENT_TYPING, EVENT_READ) or not isinstance(thread_id, int):
            await self._send({"event": "error", "detail": "Unknown event."})
            return

        if thread_group(thread_id) not in getattr(self, "groups_joined", []):
            await self._send({"event": "error", "detail": "Not a member of this chat."})
            return

        if event == EVENT_READ:
            await self._mark_read(thread_id, self.employee_id)

        await self.channel_layer.group_send(
            thread_group(thread_id),
            {
                "type": "thread.event",
                "event": event,
                "thread_id": thread_id,
                "employee_id": self.employee_id,
            },
        )

    async def thread_event(self, event: dict):
        """Channel-layer fan-out. ``type`` is the routing key and never goes
        out on the wire."""
        await self._send({k: v for k, v in event.items() if k != "type"})

    async def _send(self, payload: dict[str, Any]) -> None:
        await self.send(text_data=json.dumps(payload, default=str))

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
    def _mark_read(self, thread_id: int, employee_id: int) -> None:
        repo.mark_thread_read(thread_id, employee_id)
