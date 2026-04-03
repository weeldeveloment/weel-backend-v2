from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from shared.raw.choices import ChoiceEnum, build_choices
from shared.models import HardDeleteBaseModel, VerifiedByMixin


class DocumentType(ChoiceEnum):
    PASSPORT = "passport"
    ID_CARD = "id_card"
    SELFIE = "selfie"


class PartnerDeviceType(ChoiceEnum):
    IOS = "ios"
    ANDROID = "android"
    WEB = "web"


DocumentType.choices = build_choices(DocumentType)
PartnerDeviceType.choices = build_choices(PartnerDeviceType)


@dataclass(slots=True)
class Partner(HardDeleteBaseModel, VerifiedByMixin):
    id: int | None = None
    phone_number: str | None = None
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    is_active: bool = True
    role: str = "partner"

    _meta = SimpleNamespace(db_table="users")


@dataclass(slots=True)
class PartnerTelegramUser(HardDeleteBaseModel):
    partner_id: int | None = None
    telegram_user_id: int | None = None
    is_active: bool = True
    _meta = SimpleNamespace(db_table="users_partnertelegramuser")


@dataclass(slots=True)
class PartnerSession(HardDeleteBaseModel):
    partner_id: int | None = None
    _meta = SimpleNamespace(db_table="users_partnersession")


@dataclass(slots=True)
class PartnerDevice(HardDeleteBaseModel):
    partner_id: int | None = None
    device_type: str | None = None
    fcm_token: str | None = None
    is_active: bool = True

    PartnerDeviceType = PartnerDeviceType
    _meta = SimpleNamespace(db_table="partner_devices")


@dataclass(slots=True)
class PartnerDocument(HardDeleteBaseModel):
    partner_id: int | None = None
    document_type: str | None = None
    file: str | None = None

    DocumentType = DocumentType
    _meta = SimpleNamespace(db_table="partner_documents")
