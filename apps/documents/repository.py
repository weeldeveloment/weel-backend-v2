from __future__ import annotations

import json
import logging
import random
from datetime import datetime
from decimal import Decimal
from typing import Any

from django.utils import timezone

from shared.raw.db import execute, fetch_all, fetch_one, table_exists
from apps.documents.raw.tables import DOCUMENT_TABLE, DOCUMENT_RECIPIENT_TABLE

logger = logging.getLogger(__name__)

_tables_ready = False


def _ensure_tables() -> None:
    # The document tables are raw SQL (no Django migrations) and the
    # create_document_tables command is not part of the deploy pipeline,
    # so create them lazily on first use.
    global _tables_ready
    if _tables_ready:
        return
    if not table_exists(DOCUMENT_TABLE):
        execute(f"""
            CREATE TABLE IF NOT EXISTS {DOCUMENT_TABLE} (
                id BIGSERIAL PRIMARY KEY,
                doc_number VARCHAR(50) NOT NULL UNIQUE,
                doc_type VARCHAR(30) NOT NULL DEFAULT 'invoice',
                organization_id BIGINT,
                company_id BIGINT,
                partner_id BIGINT,
                booking_id BIGINT,
                trip_id BIGINT,
                amount NUMERIC(14,2),
                status VARCHAR(20) NOT NULL DEFAULT 'created',
                pdf_url VARCHAR(500),
                notes TEXT,
                created_by BIGINT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
    if not table_exists(DOCUMENT_RECIPIENT_TABLE):
        execute(f"""
            CREATE TABLE IF NOT EXISTS {DOCUMENT_RECIPIENT_TABLE} (
                id BIGSERIAL PRIMARY KEY,
                document_id BIGINT NOT NULL REFERENCES {DOCUMENT_TABLE}(id) ON DELETE CASCADE,
                recipient_type VARCHAR(20) NOT NULL DEFAULT 'client',
                inn VARCHAR(20),
                org_name VARCHAR(300),
                bank_details JSONB DEFAULT '{{}}',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
    _tables_ready = True


def _to_pg_json(v: Any) -> Any:
    if isinstance(v, (list, dict)):
        return json.dumps(v)
    return v


def _generate_doc_number(doc_type: str) -> str:
    prefix_map = {
        "invoice": "INV",
        "agreement": "AGR",
        "additional_agreement": "ADD",
        "certificate": "CERT",
        "reconciliation": "REC",
        "voucher": "VCH",
        "power_of_attorney": "POA",
        "other": "DOC",
    }
    prefix = prefix_map.get(doc_type, "DOC")
    year = datetime.now().strftime("%Y")
    num = random.randint(10000, 99999)
    return f"{prefix}-{year}-{num}"


def create_document(*, doc_type: str, **kwargs: Any) -> dict[str, Any] | None:
    _ensure_tables()
    now = timezone.now()
    doc_number = _generate_doc_number(doc_type)
    cols = ["doc_number", "doc_type", "created_at", "updated_at"]
    vals = [doc_number, doc_type, now, now]

    field_map = {
        "organization_id": int, "company_id": int, "partner_id": int,
        "booking_id": int, "trip_id": int,
        "amount": lambda v: v, "status": str,
        "pdf_url": str, "notes": str, "created_by": int,
    }
    for key, caster in field_map.items():
        if key in kwargs and kwargs[key] is not None:
            cols.append(key)
            vals.append(caster(kwargs[key]))

    placeholders = ", ".join(["%s"] * len(cols))
    return fetch_one(
        f"INSERT INTO {DOCUMENT_TABLE} ({', '.join(cols)}) VALUES ({placeholders}) RETURNING *",
        vals,
    )


def list_documents(
    *,
    organization_id: int | None = None,
    company_id: int | None = None,
    doc_type: str | None = None,
    status: str | None = None,
    trip_id: int | None = None,
    search: str | None = None,
) -> list[dict[str, Any]]:
    _ensure_tables()
    conditions = []
    params: list[Any] = []

    if organization_id:
        conditions.append("organization_id = %s")
        params.append(organization_id)
    if company_id:
        conditions.append("company_id = %s")
        params.append(company_id)
    if doc_type:
        conditions.append("doc_type = %s")
        params.append(doc_type)
    if status:
        conditions.append("status = %s")
        params.append(status)
    if trip_id:
        conditions.append("trip_id = %s")
        params.append(trip_id)
    if search:
        conditions.append("(doc_number ILIKE %s OR notes ILIKE %s)")
        params.extend([f"%{search}%", f"%{search}%"])

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    return fetch_all(
        f"SELECT * FROM {DOCUMENT_TABLE} {where} ORDER BY created_at DESC",
        params,
    )


def get_document(doc_id: int) -> dict[str, Any] | None:
    _ensure_tables()
    return fetch_one(f"SELECT * FROM {DOCUMENT_TABLE} WHERE id = %s", [doc_id])


def update_document_status(doc_id: int, status: str) -> dict[str, Any] | None:
    _ensure_tables()
    return fetch_one(
        f"UPDATE {DOCUMENT_TABLE} SET status = %s, updated_at = %s WHERE id = %s RETURNING *",
        [status, timezone.now(), doc_id],
    )


def update_document_pdf(doc_id: int, pdf_url: str) -> dict[str, Any] | None:
    _ensure_tables()
    return fetch_one(
        f"UPDATE {DOCUMENT_TABLE} SET pdf_url = %s, updated_at = %s WHERE id = %s RETURNING *",
        [pdf_url, timezone.now(), doc_id],
    )


def add_document_recipient(*, document_id: int, recipient_type: str, **kwargs: Any) -> dict[str, Any] | None:
    _ensure_tables()
    now = timezone.now()
    cols = ["document_id", "recipient_type", "created_at", "updated_at"]
    vals = [document_id, recipient_type, now, now]

    if kwargs.get("inn"):
        cols.append("inn")
        vals.append(str(kwargs["inn"]))
    if kwargs.get("org_name"):
        cols.append("org_name")
        vals.append(str(kwargs["org_name"]))
    if kwargs.get("bank_details"):
        cols.append("bank_details")
        vals.append(_to_pg_json(kwargs["bank_details"]))

    placeholders = ", ".join(["%s"] * len(cols))
    return fetch_one(
        f"INSERT INTO {DOCUMENT_RECIPIENT_TABLE} ({', '.join(cols)}) VALUES ({placeholders}) RETURNING *",
        vals,
    )


def get_document_recipients(document_id: int) -> list[dict[str, Any]]:
    _ensure_tables()
    return fetch_all(
        f"SELECT * FROM {DOCUMENT_RECIPIENT_TABLE} WHERE document_id = %s",
        [document_id],
    )
