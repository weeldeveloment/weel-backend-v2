from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace

from shared.raw.choices import ChoiceEnum, build_choices
from shared.models import HardDeleteBaseModel


class NotificationStatus(ChoiceEnum):
    PENDING = "pending"
    SENT = "sent"
    READ = "read"


class NotificationType(ChoiceEnum):
    BOOKING_CONFIRMED = "booking_confirmed"
    BOOKING_CANCELLED = "booking_cancelled"
    REMINDER = "reminder"
    SYSTEM = "system"
    BOOKING_NEW = "booking_new"
    BOOKING_COMPLETED = "booking_completed"
    BOOKING_NO_SHOW = "booking_no_show"
    PROMO = "promo"
    LEAVE_REVIEW = "leave_review"


NotificationStatus.choices = build_choices(NotificationStatus)
NotificationType.choices = build_choices(NotificationType)


@dataclass(slots=True)
class Notification(HardDeleteBaseModel):
    recipient_user_id: int | None = None
    recipient_role: str | None = None
    title: str | None = None
    push_message: str | None = None
    notification_type: str = NotificationType.SYSTEM.value
    status: str = NotificationStatus.PENDING.value
    is_for_every_one: bool = False

    Status = NotificationStatus
    NotificationType = NotificationType
    _meta = SimpleNamespace(db_table="notification")


@dataclass(slots=True)
class PartnerNotification(HardDeleteBaseModel):
    partner_id: int | None = None
    title: str | None = None
    body: str | None = None
    notification_type: str = NotificationType.SYSTEM.value
    data: dict = field(default_factory=dict)
    is_read: bool = False
    read_at: datetime | None = None

    NotificationType = NotificationType
    _meta = SimpleNamespace(db_table="notification")
