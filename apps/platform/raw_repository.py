from __future__ import annotations

import hashlib
import secrets
from datetime import datetime
from typing import Any
from uuid import uuid4

from psycopg2.extensions import quote_ident

from django.db import connection
from django.utils import timezone

from shared.raw.db import execute, fetch_all, fetch_one, table_exists
from shared.raw.tables import USER_TABLE
from apps.platform.raw.tables import (
    ORGANIZATION_TABLE,
    PLATFORM_USER_TABLE,
    ORGANIZATION_MEMBER_TABLE,
)

_org_schema_cache: dict[int, dict[str, Any] | None] = {}


def invalidate_org_schema_cache(organization_id: int) -> None:
    _org_schema_cache.pop(organization_id, None)


def _table(name: str) -> str:
    if table_exists(name):
        return name
    return name


def create_tenant_schema(schema_name: str) -> None:
    from psycopg2.extensions import quote_ident

    with connection.cursor() as cursor:
        safe_name = quote_ident(schema_name, cursor.cursor)
        cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {safe_name}")
        cursor.execute(f"SET search_path TO {safe_name}, public")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pms_property (
                id BIGSERIAL PRIMARY KEY,
                organization_id INTEGER NOT NULL,
                partner_user_id INTEGER,
                name VARCHAR(200) NOT NULL,
                description_uz TEXT,
                description_ru TEXT,
                description_en TEXT,
                address TEXT,
                full_address TEXT,
                city VARCHAR(100),
                country VARCHAR(3) DEFAULT 'UZ',
                latitude NUMERIC(17,14),
                longitude NUMERIC(17,14),
                star_rating INTEGER,
                weel_classification VARCHAR(20),
                themes TEXT[] DEFAULT '{}',
                amenities TEXT[] DEFAULT '{}',
                legal_info JSONB DEFAULT '{}',
                check_in_time TIME,
                check_out_time TIME,
                cancellation_policy VARCHAR(50),
                quiet_hours BOOLEAN DEFAULT TRUE,
                alcohol_allowed BOOLEAN DEFAULT TRUE,
                pets_allowed BOOLEAN DEFAULT FALSE,
                timezone VARCHAR(50) DEFAULT 'Asia/Tashkent',
                photos TEXT[] DEFAULT '{}',
                is_active BOOLEAN DEFAULT TRUE,
                is_testing BOOLEAN NOT NULL DEFAULT FALSE,
                is_verified BOOLEAN NOT NULL DEFAULT FALSE,
                is_archived BOOLEAN NOT NULL DEFAULT FALSE,
                is_recommended BOOLEAN NOT NULL DEFAULT FALSE,
                verification_status VARCHAR(20) DEFAULT 'waiting',
                guid UUID NOT NULL DEFAULT gen_random_uuid(),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pms_property_image (
                id BIGSERIAL PRIMARY KEY,
                property_id BIGINT NOT NULL REFERENCES pms_property(id) ON DELETE CASCADE,
                image_url VARCHAR(500) NOT NULL,
                "order" INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pms_room_type (
                id BIGSERIAL PRIMARY KEY,
                property_id BIGINT NOT NULL REFERENCES pms_property(id) ON DELETE CASCADE,
                preset VARCHAR(20),
                custom_name VARCHAR(100),
                name VARCHAR(100) NOT NULL,
                description TEXT,
                base_rate NUMERIC(12,2),
                capacity INTEGER DEFAULT 2,
                amenities TEXT[] DEFAULT '{}',
                photos TEXT[] DEFAULT '{}',
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pms_room (
                id BIGSERIAL PRIMARY KEY,
                property_id BIGINT NOT NULL REFERENCES pms_property(id) ON DELETE CASCADE,
                room_type_id BIGINT REFERENCES pms_room_type(id) ON DELETE SET NULL,
                room_type_name VARCHAR(100),
                room_type_preset VARCHAR(20),
                room_number VARCHAR(20) NOT NULL,
                display_name VARCHAR(200),
                floor INTEGER DEFAULT 1,
                area NUMERIC(8,2),
                bedroom_count INTEGER DEFAULT 1,
                beds JSONB DEFAULT '[]',
                amenities TEXT[] DEFAULT '{}',
                photos TEXT[] DEFAULT '{}',
                condition VARCHAR(20) DEFAULT 'clean',
                availability VARCHAR(20) DEFAULT 'available',
                capacity INTEGER DEFAULT 2,
                meal_plan VARCHAR(3) DEFAULT 'BB',
                base_price NUMERIC(10,2),
                currency VARCHAR(3) NOT NULL DEFAULT 'UZS',
                cover_photo_index INTEGER DEFAULT 0,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(property_id, room_number)
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pms_calendar_slot (
                id BIGSERIAL PRIMARY KEY,
                room_id BIGINT NOT NULL REFERENCES pms_room(id) ON DELETE CASCADE,
                date DATE NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'available',
                hold_expires_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(room_id, date)
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pms_guest (
                id BIGSERIAL PRIMARY KEY,
                first_name VARCHAR(100) NOT NULL,
                last_name VARCHAR(100),
                email VARCHAR(254),
                phone VARCHAR(32),
                id_document JSONB DEFAULT '{}',
                preferences JSONB DEFAULT '{}',
                is_vip BOOLEAN DEFAULT FALSE,
                is_blacklisted BOOLEAN DEFAULT FALSE,
                notes TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pms_booking (
                id BIGSERIAL PRIMARY KEY,
                property_id BIGINT NOT NULL REFERENCES pms_property(id) ON DELETE CASCADE,
                room_id BIGINT NOT NULL REFERENCES pms_room(id) ON DELETE RESTRICT,
                guest_id BIGINT REFERENCES pms_guest(id) ON DELETE SET NULL,
                booking_number VARCHAR(20) NOT NULL UNIQUE,
                check_in DATE NOT NULL,
                check_out DATE NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'new',
                source VARCHAR(20) NOT NULL DEFAULT 'direct',
                meal_plan VARCHAR(3) NOT NULL DEFAULT 'RO',
                adult_count INTEGER DEFAULT 1,
                child_count INTEGER DEFAULT 0,
                rate NUMERIC(10,2),
                currency VARCHAR(3) DEFAULT 'USD',
                payment_status VARCHAR(20) DEFAULT 'pending',
                total_cost NUMERIC(10,2),
                hold_amount NUMERIC(10,2),
                confirmed_at TIMESTAMPTZ,
                confirmation_deadline TIMESTAMPTZ,
                b2b_company_id BIGINT,
                voucher_number VARCHAR(50),
                notes TEXT,
                created_by BIGINT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pms_booking_history (
                id BIGSERIAL PRIMARY KEY,
                booking_id BIGINT NOT NULL REFERENCES pms_booking(id) ON DELETE CASCADE,
                action VARCHAR(50) NOT NULL,
                previous_value JSONB DEFAULT '{}',
                new_value JSONB DEFAULT '{}',
                user_id BIGINT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pms_review (
                id BIGSERIAL PRIMARY KEY,
                property_id BIGINT NOT NULL REFERENCES pms_property(id) ON DELETE CASCADE,
                booking_id BIGINT REFERENCES pms_booking(id) ON DELETE SET NULL,
                guest_name VARCHAR(200) NOT NULL,
                rating NUMERIC(2,1) NOT NULL,
                categories JSONB DEFAULT '{}',
                text TEXT,
                hotel_response TEXT,
                response_date TIMESTAMPTZ,
                is_complained BOOLEAN DEFAULT FALSE,
                complaint_reason TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pms_property_image_property_id ON pms_property_image (property_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pms_room_property_id ON pms_room (property_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pms_booking_property_id ON pms_booking (property_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pms_booking_room_id ON pms_booking (room_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pms_booking_guest_id ON pms_booking (guest_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pms_booking_created_by ON pms_booking (created_by)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pms_booking_history_booking_id ON pms_booking_history (booking_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pms_review_property_id ON pms_review (property_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pms_review_booking_id ON pms_review (booking_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pms_calendar_slot_status_expires ON pms_calendar_slot (status, hold_expires_at)")

        cursor.execute("SET search_path TO public")


def delete_orphaned_pms_user(user_id: int) -> None:
    execute(
        f"DELETE FROM {USER_TABLE} WHERE id = %s AND role = 'pms'",
        [user_id],
    )


def drop_tenant_schema(schema_name: str) -> None:
    with connection.cursor() as cursor:
        safe_name = quote_ident(schema_name, cursor.cursor)
        cursor.execute(f"DROP SCHEMA IF EXISTS {safe_name} CASCADE")


def hard_delete_organization(org_id: int) -> bool:
    result = execute(
        f"""
        DELETE FROM public.{_table(ORGANIZATION_TABLE)}
        WHERE id = %s
        """,
        [org_id],
    ) > 0
    if result:
        invalidate_org_schema_cache(org_id)
    return result


def user_has_org_membership(user_id: int) -> bool:
    row = fetch_one(
        f"""
        SELECT EXISTS (
            SELECT 1
            FROM public.{_table(ORGANIZATION_MEMBER_TABLE)}
            WHERE user_id = %s
        ) AS exists_flag
        """,
        [user_id],
    )
    return bool(row and row["exists_flag"])


def create_organization(
    *,
    name: str,
    slug: str,
    schema_name: str,
) -> dict[str, Any] | None:
    now = timezone.now()
    result = fetch_one(
        f"""
        INSERT INTO public.{_table(ORGANIZATION_TABLE)}
            (guid, name, slug, schema_name, is_active, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        [uuid4(), name, slug, schema_name, True, now, now],
    )
    if result:
        invalidate_org_schema_cache(result["id"])
    return result


def get_organization_by_slug(slug: str) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        SELECT * FROM public.{_table(ORGANIZATION_TABLE)}
        WHERE slug = %s AND is_active = TRUE
        LIMIT 1
        """,
        [slug],
    )


def get_organization_by_id(org_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        SELECT * FROM public.{_table(ORGANIZATION_TABLE)}
        WHERE id = %s AND is_active = TRUE
        LIMIT 1
        """,
        [org_id],
    )


def get_organization_by_schema(schema_name: str) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        SELECT * FROM public.{_table(ORGANIZATION_TABLE)}
        WHERE schema_name = %s AND is_active = TRUE
        LIMIT 1
        """,
        [schema_name],
    )


def list_organizations() -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT * FROM public.{_table(ORGANIZATION_TABLE)}
        WHERE is_active = TRUE
        ORDER BY created_at DESC
        """
    )


def update_organization(org_id: int, **kwargs: Any) -> dict[str, Any] | None:
    if not kwargs:
        return get_organization_by_id(org_id)

    sets = ", ".join(f"{k} = %s" for k in kwargs)
    values = list(kwargs.values())
    values.append(timezone.now())
    values.append(org_id)

    result = fetch_one(
        f"""
        UPDATE public.{_table(ORGANIZATION_TABLE)}
        SET {sets}, updated_at = %s
        WHERE id = %s
        RETURNING *
        """,
        values,
    )
    if result:
        invalidate_org_schema_cache(org_id)
    return result


def deactivate_organization(org_id: int) -> bool:
    result = execute(
        f"""
        UPDATE public.{_table(ORGANIZATION_TABLE)}
        SET is_active = FALSE, updated_at = %s
        WHERE id = %s
        """,
        [timezone.now(), org_id],
    ) > 0
    if result:
        invalidate_org_schema_cache(org_id)
    return result


# ─── Platform Users ──────────────────────────────────────────────────────────


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}${h}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt, h = stored.split("$", 1)
    except ValueError:
        return False
    return hashlib.sha256((salt + password).encode()).hexdigest() == h


def create_platform_user(
    *,
    email: str,
    password: str,
    first_name: str | None = None,
    last_name: str | None = None,
    phone: str | None = None,
) -> dict[str, Any] | None:
    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO public.{_table(PLATFORM_USER_TABLE)}
            (email, phone, password_hash, first_name, last_name, is_active, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        [email, phone, _hash_password(password), first_name, last_name, True, now, now],
    )


def get_platform_user_by_email(email: str) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        SELECT * FROM public.{_table(PLATFORM_USER_TABLE)}
        WHERE email = %s AND is_active = TRUE
        LIMIT 1
        """,
        [email],
    )


def get_platform_user_by_phone(phone: str) -> dict[str, Any] | None:
    candidates = [phone.strip()]
    raw = phone.strip()
    if raw.startswith("+"):
        candidates.append(raw[1:])
    else:
        candidates.append(f"+{raw}")
    placeholders = ",".join(["%s"] * len(candidates))
    return fetch_one(
        f"""
        SELECT * FROM public.{_table(PLATFORM_USER_TABLE)}
        WHERE phone IN ({placeholders}) AND is_active = TRUE
        LIMIT 1
        """,
        candidates,
    )


def get_platform_user_by_id(user_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        SELECT * FROM public.{_table(PLATFORM_USER_TABLE)}
        WHERE id = %s AND is_active = TRUE
        LIMIT 1
        """,
        [user_id],
    )


def verify_platform_user_password(email: str, password: str) -> dict[str, Any] | None:
    user = get_platform_user_by_email(email)
    if not user or not user.get("password_hash"):
        return None
    if not _verify_password(password, user["password_hash"]):
        return None
    return user


def update_platform_user(user_id: int, **kwargs: Any) -> dict[str, Any] | None:
    if "password" in kwargs:
        kwargs["password_hash"] = _hash_password(kwargs.pop("password"))

    if not kwargs:
        return get_platform_user_by_id(user_id)

    sets = ", ".join(f"{k} = %s" for k in kwargs)
    values = list(kwargs.values())
    values.append(timezone.now())
    values.append(user_id)

    return fetch_one(
        f"""
        UPDATE public.{_table(PLATFORM_USER_TABLE)}
        SET {sets}, updated_at = %s
        WHERE id = %s
        RETURNING *
        """,
        values,
    )


# ─── Organization Members ────────────────────────────────────────────────────


def create_organization_member(
    *,
    organization_id: int,
    user_id: int,
    role: str = "manager",
) -> dict[str, Any] | None:
    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO public.{_table(ORGANIZATION_MEMBER_TABLE)}
            (guid, organization_id, user_id, role, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        [uuid4(), organization_id, user_id, role, now, now],
    )


def get_organization_member(org_id: int, user_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        SELECT m.*, u.email, u.first_name, u.last_name, u.phone_number as phone
        FROM public.{_table(ORGANIZATION_MEMBER_TABLE)} m
        JOIN public.{USER_TABLE} u ON u.id = m.user_id
        WHERE m.organization_id = %s AND m.user_id = %s
        LIMIT 1
        """,
        [org_id, user_id],
    )


def list_organization_members(org_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT m.*, u.email, u.first_name, u.last_name, u.phone_number as phone
        FROM public.{_table(ORGANIZATION_MEMBER_TABLE)} m
        JOIN public.{USER_TABLE} u ON u.id = m.user_id
        WHERE m.organization_id = %s
        ORDER BY m.role ASC, u.first_name ASC
        """,
        [org_id],
    )


def get_user_organizations(user_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT o.*, m.role as member_role
        FROM public.{_table(ORGANIZATION_MEMBER_TABLE)} m
        JOIN public.{_table(ORGANIZATION_TABLE)} o ON o.id = m.organization_id
        WHERE m.user_id = %s AND o.is_active = TRUE
        ORDER BY o.name ASC
        """,
        [user_id],
    )


def remove_organization_member(org_id: int, user_id: int) -> bool:
    return execute(
        f"""
        DELETE FROM public.{_table(ORGANIZATION_MEMBER_TABLE)}
        WHERE organization_id = %s AND user_id = %s
        """,
        [org_id, user_id],
    ) > 0


def update_member_role(org_id: int, user_id: int, role: str) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        UPDATE public.{_table(ORGANIZATION_MEMBER_TABLE)}
        SET role = %s, updated_at = %s
        WHERE organization_id = %s AND user_id = %s
        RETURNING *
        """,
        [role, timezone.now(), org_id, user_id],
    )
