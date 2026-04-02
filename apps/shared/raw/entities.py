from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID


@dataclass(slots=True)
class RowEntity:
    @classmethod
    def from_row(cls, row: dict[str, Any]):
        kwargs = {}
        for f in fields(cls):
            kwargs[f.name] = row.get(f.name)
        return cls(**kwargs)


@dataclass(slots=True)
class RawUser(RowEntity):
    id: int
    role: str
    email: str | None
    phone_number: str | None
    first_name: str | None
    last_name: str | None
    username: str | None
    avatar: str | None
    is_active: bool | None
    is_verified: bool | None
    verified_at: datetime | None
    verified_by_user_id: int | None
    created_at: datetime
    updated_at: datetime
    legacy_admin_id: int | None
    legacy_client_id: int | None
    legacy_partner_id: int | None

    @property
    def guid(self) -> UUID:
        # Reversible synthetic UUID derived from integer user id.
        return UUID(int=self.id)

    @property
    def is_client(self) -> bool:
        return self.role == "client"

    @property
    def is_partner(self) -> bool:
        return self.role == "partner"


@dataclass(slots=True)
class RawUserMap(RowEntity):
    legacy_table: str
    legacy_id: int
    user_id: int


@dataclass(slots=True)
class RawPropertyBase(RowEntity):
    id: int
    legacy_property_id: int | None
    guid: UUID
    created_at: datetime
    updated_at: datetime
    title: str
    title_sort: str
    is_verified: bool | None
    verified_at: datetime | None
    verification_status: str | None
    is_archived: bool | None
    is_recommended: bool | None
    minimum_weekend_day_stay: bool | None
    weekend_only_sunday_inclusive: bool | None
    comment_count: int | None
    price: Decimal | None
    price_per_person: Decimal | None
    price_on_working_days: Decimal | None
    price_on_weekends: Decimal | None
    currency: str | None
    img: str | None
    partner_user_id: int
    verified_by_user_id: int | None
    latitude: Decimal | None
    longitude: Decimal | None
    city: str | None
    country: str | None
    region_id: int | None
    district_id: int | None
    shaharcha_id: int | None
    mahalla_id: int | None
    description_en: str | None
    description_ru: str | None
    description_uz: str | None
    check_in: time | None
    check_out: time | None
    is_allowed_alcohol: bool | None
    is_allowed_corporate: bool | None
    is_allowed_pets: bool | None
    is_quiet_hours: bool | None
    apartment_number: str | None
    home_number: str | None
    entrance_number: str | None
    floor_number: str | None
    pass_code: str | None


@dataclass(slots=True)
class RawApartment(RawPropertyBase):
    pass


@dataclass(slots=True)
class RawCottage(RawPropertyBase):
    pass


@dataclass(slots=True)
class RawPropertyMap(RowEntity):
    legacy_property_id: int
    property_type: str
    target_table: str
    target_id: int


@dataclass(slots=True)
class RawBooking(RowEntity):
    id: int
    legacy_booking_id: int | None
    guid: UUID | None
    created_at: datetime
    updated_at: datetime
    booking_number: str
    check_in: date
    check_out: date
    adults: int | None
    children: int | None
    babies: int | None
    reminder_sent: bool | None
    status: str
    cancellation_reason: str | None
    confirmed_at: datetime | None
    cancelled_at: datetime | None
    completed_at: datetime | None
    payment_reminder_stage: str | None
    client_user_id: int
    property_apartment_id: int | None
    property_cottage_id: int | None


@dataclass(slots=True)
class RawCalendarDate(RowEntity):
    id: int
    legacy_calendar_id: int | None
    guid: UUID | None
    created_at: datetime
    updated_at: datetime
    status: str
    date: date
    property_apartment_id: int | None
    property_cottage_id: int | None


@dataclass(slots=True)
class RawReview(RowEntity):
    id: int
    legacy_review_id: int | None
    guid: UUID
    created_at: datetime
    updated_at: datetime
    rating: Decimal | None
    comment: str | None
    is_hidden: bool | None
    user_id: int
    apartment_id: int | None
    cottage_id: int | None


@dataclass(slots=True)
class RawStory(RowEntity):
    id: int
    legacy_story_id: int | None
    guid: UUID
    created_at: datetime
    updated_at: datetime
    is_verified: bool | None
    verified_at: datetime | None
    expires_at: datetime | None
    views: int | None
    uploaded_at: datetime
    verified_by_user_id: int | None
    property_apartment_id: int | None
    property_cottage_id: int | None


@dataclass(slots=True)
class RawStoryMedia(RowEntity):
    id: int
    legacy_media_id: int | None
    guid: UUID
    created_at: datetime
    updated_at: datetime
    media: str
    media_type: str
    story_id: int


@dataclass(slots=True)
class RawNotification(RowEntity):
    id: int
    legacy_client_notification_id: int | None
    legacy_partner_notification_id: int | None
    guid: UUID
    created_at: datetime
    updated_at: datetime
    title: str | None
    push_message: str | None
    notification_type: str
    status: str
    is_for_every_one: bool
    recipient_user_id: int | None
    recipient_role: str | None


@dataclass(slots=True)
class RawTransactionHistory(RowEntity):
    id: int
    legacy_booking_transaction_id: int | None
    legacy_payment_id: int | None
    booking_id: int
    client_user_id: int
    partner_user_id: int
    amount: Decimal
    currency: str | None
    transaction_id: str | None
    hold_id: str | None
    type: str | None
    status: str | None
    card_id: str | None
    extra_id: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class RawChatConversation(RowEntity):
    id: int
    legacy_conversation_id: int | None
    created_at: datetime
    updated_at: datetime
    admin_user_id: int
    partner_user_id: int


@dataclass(slots=True)
class RawChatMessage(RowEntity):
    id: int
    legacy_message_id: int | None
    content: str
    is_read: bool | None
    created_at: datetime
    updated_at: datetime
    conversation_id: int
    sender_user_id: int
    receiver_user_id: int
    sender_role: str
    receiver_role: str
