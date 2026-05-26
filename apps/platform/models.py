from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

from shared.models import BaseModel, HardDeleteBaseModel


@dataclass(slots=True)
class Organization(HardDeleteBaseModel):
    id: int | None = None
    name: str | None = None
    slug: str | None = None
    schema_name: str | None = None
    is_active: bool = True
    _meta = SimpleNamespace(db_table="platform_organization")


@dataclass(slots=True)
class PlatformUser(HardDeleteBaseModel):
    id: int | None = None
    email: str | None = None
    phone: str | None = None
    password_hash: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    is_active: bool = True
    _meta = SimpleNamespace(db_table="platform_user")


class MemberRole:
    OWNER = "owner"
    ADMIN = "admin"
    MANAGER = "manager"
    RECEPTIONIST = "receptionist"
    HOUSEKEEPING = "housekeeping"

    CHOICES = [OWNER, ADMIN, MANAGER, RECEPTIONIST, HOUSEKEEPING]


@dataclass(slots=True)
class OrganizationMember(HardDeleteBaseModel):
    id: int | None = None
    organization_id: int | None = None
    user_id: int | None = None
    role: str = MemberRole.MANAGER
    _meta = SimpleNamespace(db_table="platform_organization_member")
