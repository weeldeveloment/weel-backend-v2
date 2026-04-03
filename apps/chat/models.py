from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace

from shared.models import HardDeleteBaseModel


@dataclass(slots=True)
class Message(HardDeleteBaseModel):
    id: int | None = None
    sender_id: int | None = None
    receiver_id: int | None = None
    content: str | None = None
    created_at: datetime | None = None
    _meta = SimpleNamespace(db_table="message")


@dataclass(slots=True)
class Conversation(HardDeleteBaseModel):
    id: int | None = None
    admin_user_id: int | None = None
    partner_id: int | None = None
    _meta = SimpleNamespace(db_table="conversation")


@dataclass(slots=True)
class ChatMessage(HardDeleteBaseModel):
    id: int | None = None
    conversation_id: int | None = None
    sender_user_id: int | None = None
    receiver_user_id: int | None = None
    content: str | None = None
    is_read: bool = False
    _meta = SimpleNamespace(db_table="chat_message")
