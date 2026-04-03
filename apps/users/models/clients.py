from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from shared.raw.choices import ChoiceEnum, build_choices
from shared.models import HardDeleteBaseModel


@dataclass(slots=True)
class Client(HardDeleteBaseModel):
    id: int | None = None
    phone_number: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    is_active: bool = True
    role: str = "client"

    _meta = SimpleNamespace(db_table="users")


@dataclass(slots=True)
class ClientSession(HardDeleteBaseModel):
    client_id: int | None = None
    _meta = SimpleNamespace(db_table="users_clientsession")


class ClientDeviceType(ChoiceEnum):
    IOS = "ios"
    ANDROID = "android"
    WEB = "web"


ClientDeviceType.choices = build_choices(ClientDeviceType)


@dataclass(slots=True)
class ClientDevice(HardDeleteBaseModel):
    client_id: int | None = None
    device_type: str | None = None
    fcm_token: str | None = None
    is_active: bool = True

    ClientDeviceType = ClientDeviceType
    _meta = SimpleNamespace(db_table="client_devices")
