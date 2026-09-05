"""Stock and catalogue behind the sales board — the TZ's "Ombor moduli".

Four things, in the order a shop thinks of them: the products it sells, the
warehouses it keeps them in, how many of each are where, and the ledger that
explains every one of those numbers. The ledger is the source of truth —
``b2b_stock.quantity`` is a cache of it per (product, warehouse), and
[apply_movement] is the only thing that writes either, so the two can never
disagree.

Above the ledger sits the paper: ``inventory_documents`` files a receipt, a
transfer, a count, a write-off, a repricing, a sale or a return as a document
with lines, and confirming it is what calls [apply_movement]. This module is
the catalogue, the warehouses, the suppliers, the settings and the ledger
primitive; the document layer is next door.
"""
from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Sequence

from django.db import transaction
from django.utils import timezone

from shared.raw.db import execute, fetch_all, fetch_one
from apps.b2b.raw.tables import (
    B2B_EMPLOYEE_TABLE,
    B2B_INVENTORY_SETTINGS_TABLE,
    B2B_PRICE_HISTORY_TABLE,
    B2B_PRODUCT_CATEGORY_TABLE,
    B2B_PRODUCT_COMPONENT_TABLE,
    B2B_PRODUCT_TABLE,
    B2B_STOCK_DOCUMENT_ITEM_TABLE,
    B2B_STOCK_DOCUMENT_TABLE,
    B2B_STOCK_MOVEMENT_TABLE,
    B2B_STOCK_TABLE,
    B2B_SUPPLIER_TABLE,
    B2B_WAREHOUSE_TABLE,
    B2B_WORKSPACE_LEAD_ITEM_TABLE,
    B2B_WORKSPACE_LEAD_TABLE,
)
from apps.b2b.workspace.storage import photo_url

logger = logging.getLogger(__name__)


class MovementKind:
    """Which way stock moved, and why."""

    #: Goods arriving — a purchase, a delivery from a supplier.
    RECEIPT = "receipt"
    #: Goods leaving because they were sold. Written by the lead hook and by
    #: the movements sheet when a sale was made outside the funnel.
    SALE = "sale"
    #: Goods leaving without being sold — damaged, expired, lost.
    WRITE_OFF = "write_off"
    #: Goods moving between two of the company's own warehouses.
    TRANSFER = "transfer"
    #: A count that disagreed with the ledger. The row carries the signed
    #: difference, so the ledger still sums to the shelf.
    ADJUSTMENT = "adjustment"
    #: The mirror of a sale — a customer brought it back.
    RETURN = "return"

    CHOICES = [RECEIPT, SALE, WRITE_OFF, TRANSFER, ADJUSTMENT, RETURN]
    #: The kinds that put stock on the shelf.
    INBOUND = {RECEIPT, RETURN}
    #: The kinds that take it off.
    OUTBOUND = {SALE, WRITE_OFF}


class ProductKind:
    """What a catalogue row is."""

    #: Something physical, with a balance on a shelf.
    PRODUCT = "product"
    #: Something done rather than handed over — never holds stock.
    SERVICE = "service"
    #: Several products sold as one line; its components leave the shelf.
    BUNDLE = "bundle"

    CHOICES = [PRODUCT, SERVICE, BUNDLE]


class WriteOffReason:
    DEFECT = "defect"
    LOSS = "loss"
    DAMAGE = "damage"
    INTERNAL_USE = "internal_use"
    OTHER = "other"

    CHOICES = [DEFECT, LOSS, DAMAGE, INTERNAL_USE, OTHER]


class InventoryError(Exception):
    """A movement the ledger refuses — said in words the sheet can print."""

    def __init__(self, message: str, code: str = "invalid", details: Any = None):
        super().__init__(message)
        self.code = code
        self.details = details


DEFAULT_WAREHOUSE_NAME = "Asosiy sklad"


def _q(value: Any) -> Decimal:
    return Decimal(str(value if value is not None else 0))


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


# ─── Settings ─────────────────────────────────────────────────────────────────

def get_settings(company_id: int) -> dict[str, Any]:
    """The company's stock-room switches, with defaults for a company that
    never opened the settings sheet."""
    row = fetch_one(
        f"SELECT * FROM {B2B_INVENTORY_SETTINGS_TABLE} WHERE company_id = %s",
        [company_id],
    )
    return row or {
        "company_id": company_id,
        "allow_backorder": False,
        "base_currency": "UZS",
        "sku_prefix": "P",
        "next_sku": 1,
        "write_off_alert": Decimal("500000"),
    }


def update_settings(company_id: int, **fields: Any) -> dict[str, Any]:
    allowed = {"allow_backorder", "base_currency", "sku_prefix", "write_off_alert"}
    changes = {k: v for k, v in fields.items() if k in allowed and v is not None}
    current = get_settings(company_id)
    merged = {**current, **changes}
    execute(
        f"""
        INSERT INTO {B2B_INVENTORY_SETTINGS_TABLE}
            (company_id, allow_backorder, base_currency, sku_prefix, next_sku, write_off_alert, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (company_id) DO UPDATE SET
            allow_backorder = EXCLUDED.allow_backorder,
            base_currency = EXCLUDED.base_currency,
            sku_prefix = EXCLUDED.sku_prefix,
            write_off_alert = EXCLUDED.write_off_alert,
            updated_at = EXCLUDED.updated_at
        """,
        [
            company_id, bool(merged["allow_backorder"]),
            (merged["base_currency"] or "UZS")[:3].upper(),
            (merged["sku_prefix"] or "P")[:10], int(merged.get("next_sku") or 1),
            _q(merged.get("write_off_alert")), timezone.now(),
        ],
    )
    return get_settings(company_id)


def next_sku(company_id: int) -> str:
    """"P-000012": the prefix from the settings and a counter that only
    goes up. Reserved atomically so two people generating at once do not
    get the same article."""
    settings = get_settings(company_id)
    row = fetch_one(
        f"""
        INSERT INTO {B2B_INVENTORY_SETTINGS_TABLE} (company_id, next_sku, updated_at)
        VALUES (%s, 2, %s)
        ON CONFLICT (company_id) DO UPDATE SET next_sku = {B2B_INVENTORY_SETTINGS_TABLE}.next_sku + 1
        RETURNING next_sku - 1 AS taken
        """,
        [company_id, timezone.now()],
    )
    number = int((row or {}).get("taken") or 1)
    prefix = settings.get("sku_prefix") or "P"
    candidate = f"{prefix}-{number:06d}"
    # A hand-typed article may already sit on the number; step past it.
    while _sku_taken(company_id, "sku", candidate, exclude_id=None):
        number += 1
        candidate = f"{prefix}-{number:06d}"
        execute(
            f"UPDATE {B2B_INVENTORY_SETTINGS_TABLE} SET next_sku = GREATEST(next_sku, %s) "
            "WHERE company_id = %s",
            [number + 1, company_id],
        )
    return candidate


def next_barcode(company_id: int) -> str:
    """An EAN-13 in the 200–299 in-store range, with a valid check digit,
    that no product of this company already carries."""
    for _ in range(50):
        body = "2" + "".join(random.choice("0123456789") for _ in range(11))
        total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(body))
        candidate = body + str((10 - total % 10) % 10)
        if not _sku_taken(company_id, "barcode", candidate, exclude_id=None):
            return candidate
    raise InventoryError("Shtrix-kod generatsiya qilib bo'lmadi.", code="barcode_failed")


# ─── Warehouses ───────────────────────────────────────────────────────────────

def list_warehouses(company_id: int, *, include_inactive: bool = False) -> list[dict[str, Any]]:
    """Every warehouse, with what it holds: distinct products and the total
    value of them at purchase price."""
    return fetch_all(
        f"""
        SELECT w.*,
               COALESCE(s.product_count, 0) AS product_count,
               COALESCE(s.stock_value, 0) AS stock_value,
               COALESCE(s.quantity_total, 0) AS quantity_total
        FROM {B2B_WAREHOUSE_TABLE} w
        LEFT JOIN (
            SELECT st.warehouse_id,
                   COUNT(*) FILTER (WHERE st.quantity <> 0) AS product_count,
                   SUM(st.quantity * p.purchase_price) AS stock_value,
                   SUM(st.quantity) AS quantity_total
            FROM {B2B_STOCK_TABLE} st
            JOIN {B2B_PRODUCT_TABLE} p ON p.id = st.product_id
            GROUP BY st.warehouse_id
        ) s ON s.warehouse_id = w.id
        WHERE w.company_id = %s {"" if include_inactive else "AND w.is_active"}
        ORDER BY w.is_default DESC, w.name, w.id
        """,
        [company_id],
    )


def get_warehouse(warehouse_id: int, company_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT * FROM {B2B_WAREHOUSE_TABLE} WHERE id = %s AND company_id = %s",
        [warehouse_id, company_id],
    )


def create_warehouse(
    company_id: int, *, name: str, address: str | None = None, is_default: bool = False
) -> dict[str, Any] | None:
    now = timezone.now()
    # The first warehouse a company makes is its default whether or not the
    # sheet said so: a won lead has to come out of somewhere.
    has_any = fetch_one(
        f"SELECT 1 AS x FROM {B2B_WAREHOUSE_TABLE} WHERE company_id = %s AND is_active LIMIT 1",
        [company_id],
    )
    is_default = is_default or not has_any
    with transaction.atomic():
        if is_default:
            execute(
                f"UPDATE {B2B_WAREHOUSE_TABLE} SET is_default = FALSE, updated_at = %s "
                "WHERE company_id = %s AND is_default",
                [now, company_id],
            )
        return fetch_one(
            f"""
            INSERT INTO {B2B_WAREHOUSE_TABLE}
                (company_id, name, address, is_default, is_active, created_at, updated_at)
            VALUES (%s, %s, %s, %s, TRUE, %s, %s)
            RETURNING *
            """,
            [company_id, name.strip(), (address or "").strip() or None, is_default, now, now],
        )


def update_warehouse(
    warehouse_id: int, company_id: int, **fields: Any
) -> dict[str, Any] | None:
    allowed = {"name", "address", "is_default", "is_active"}
    changes = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not changes:
        return get_warehouse(warehouse_id, company_id)
    now = timezone.now()
    with transaction.atomic():
        if changes.get("is_default"):
            execute(
                f"UPDATE {B2B_WAREHOUSE_TABLE} SET is_default = FALSE, updated_at = %s "
                "WHERE company_id = %s AND is_default AND id <> %s",
                [now, company_id, warehouse_id],
            )
        assignments = ", ".join(f"{column} = %s" for column in changes)
        return fetch_one(
            f"""
            UPDATE {B2B_WAREHOUSE_TABLE} SET {assignments}, updated_at = %s
            WHERE id = %s AND company_id = %s
            RETURNING *
            """,
            [*changes.values(), now, warehouse_id, company_id],
        )


def delete_warehouse(warehouse_id: int, company_id: int) -> bool:
    """Closes a warehouse. Refused while anything is still on its shelves —
    stock that vanishes with its warehouse is stock nobody wrote off."""
    held = fetch_one(
        f"SELECT COALESCE(SUM(quantity), 0) AS qty FROM {B2B_STOCK_TABLE} WHERE warehouse_id = %s",
        [warehouse_id],
    )
    if held and _q(held["qty"]) != 0:
        raise InventoryError(
            "Skladda hali tovar bor. Avval ko'chiring yoki hisobdan chiqaring.",
            code="warehouse_not_empty",
        )
    return bool(execute(
        f"UPDATE {B2B_WAREHOUSE_TABLE} SET is_active = FALSE, is_default = FALSE, updated_at = %s "
        "WHERE id = %s AND company_id = %s AND is_active",
        [timezone.now(), warehouse_id, company_id],
    ))


def default_warehouse(company_id: int, *, create: bool = True) -> dict[str, Any] | None:
    """The warehouse a sale ships from when the line did not say. Made on
    first use, so a company that never opened the stock screen still gets
    its won deals counted."""
    row = fetch_one(
        f"SELECT * FROM {B2B_WAREHOUSE_TABLE} WHERE company_id = %s AND is_active "
        "ORDER BY is_default DESC, id LIMIT 1",
        [company_id],
    )
    if row or not create:
        return row
    return create_warehouse(company_id, name=DEFAULT_WAREHOUSE_NAME, is_default=True)


# ─── Suppliers ────────────────────────────────────────────────────────────────

def list_suppliers(
    company_id: int, *, q: str | None = None, include_inactive: bool = False
) -> list[dict[str, Any]]:
    """Every supplier, with what has been bought from them: receipts
    confirmed against their name, and the sum of those."""
    where = ["s.company_id = %s"]
    params: list[Any] = [company_id]
    if not include_inactive:
        where.append("s.is_active")
    if q:
        where.append("(s.name ILIKE %s OR s.phone ILIKE %s OR s.email ILIKE %s)")
        like = f"%{q.strip()}%"
        params.extend([like, like, like])
    return fetch_all(
        f"""
        SELECT s.*,
               COALESCE(d.receipt_count, 0) AS receipt_count,
               COALESCE(d.purchases, 0) AS purchases,
               d.last_receipt_at,
               COALESCE(p.product_count, 0) AS product_count
        FROM {B2B_SUPPLIER_TABLE} s
        LEFT JOIN (
            SELECT doc.supplier_id, COUNT(*) AS receipt_count,
                   SUM(t.total) AS purchases, MAX(doc.confirmed_at) AS last_receipt_at
            FROM {B2B_STOCK_DOCUMENT_TABLE} doc
            LEFT JOIN (
                SELECT document_id, SUM(quantity * unit_cost) AS total
                FROM {B2B_STOCK_DOCUMENT_ITEM_TABLE} GROUP BY document_id
            ) t ON t.document_id = doc.id
            WHERE doc.kind = 'receipt' AND doc.status = 'confirmed' AND doc.supplier_id IS NOT NULL
            GROUP BY doc.supplier_id
        ) d ON d.supplier_id = s.id
        LEFT JOIN (
            SELECT supplier_id, COUNT(*) AS product_count FROM {B2B_PRODUCT_TABLE}
            WHERE is_active AND supplier_id IS NOT NULL GROUP BY supplier_id
        ) p ON p.supplier_id = s.id
        WHERE {' AND '.join(where)}
        ORDER BY s.name, s.id
        """,
        params,
    )


def get_supplier(supplier_id: int, company_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT * FROM {B2B_SUPPLIER_TABLE} WHERE id = %s AND company_id = %s",
        [supplier_id, company_id],
    )


def create_supplier(company_id: int, **fields: Any) -> dict[str, Any] | None:
    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO {B2B_SUPPLIER_TABLE}
            (company_id, name, kind, phone, email, requisites, note, is_active, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s)
        RETURNING *
        """,
        [
            company_id, (fields.get("name") or "").strip(),
            fields.get("kind") or "company",
            (fields.get("phone") or "").strip() or None,
            (fields.get("email") or "").strip() or None,
            (fields.get("requisites") or "").strip() or None,
            (fields.get("note") or "").strip() or None,
            now, now,
        ],
    )


def update_supplier(supplier_id: int, company_id: int, **fields: Any) -> dict[str, Any] | None:
    allowed = {"name", "kind", "phone", "email", "requisites", "note", "is_active"}
    changes = {k: v for k, v in fields.items() if k in allowed}
    if not changes:
        return get_supplier(supplier_id, company_id)
    assignments = ", ".join(f"{column} = %s" for column in changes)
    return fetch_one(
        f"""
        UPDATE {B2B_SUPPLIER_TABLE} SET {assignments}, updated_at = %s
        WHERE id = %s AND company_id = %s
        RETURNING *
        """,
        [*changes.values(), timezone.now(), supplier_id, company_id],
    )


def supplier_purchases(supplier_id: int, company_id: int, *, limit: int = 100) -> list[dict[str, Any]]:
    """What was bought from them, receipt by receipt."""
    return fetch_all(
        f"""
        SELECT doc.id, doc.number, doc.status, doc.doc_date, doc.external_number,
               doc.currency, doc.extra_costs, doc.confirmed_at, doc.created_at,
               COALESCE(t.total, 0) AS total, COALESCE(t.line_count, 0) AS line_count,
               COALESCE(t.names, '') AS product_names
        FROM {B2B_STOCK_DOCUMENT_TABLE} doc
        LEFT JOIN (
            SELECT i.document_id, SUM(i.quantity * i.unit_cost) AS total, COUNT(*) AS line_count,
                   STRING_AGG(p.name, ', ' ORDER BY i.position) AS names
            FROM {B2B_STOCK_DOCUMENT_ITEM_TABLE} i
            JOIN {B2B_PRODUCT_TABLE} p ON p.id = i.product_id
            GROUP BY i.document_id
        ) t ON t.document_id = doc.id
        WHERE doc.company_id = %s AND doc.supplier_id = %s AND doc.kind = 'receipt'
          AND doc.status <> 'cancelled'
        ORDER BY doc.created_at DESC
        LIMIT %s
        """,
        [company_id, supplier_id, limit],
    )


def find_or_create_supplier(company_id: int, name: str) -> dict[str, Any] | None:
    name = (name or "").strip()
    if not name:
        return None
    row = fetch_one(
        f"SELECT * FROM {B2B_SUPPLIER_TABLE} WHERE company_id = %s AND LOWER(name) = LOWER(%s) LIMIT 1",
        [company_id, name],
    )
    return row or create_supplier(company_id, name=name)


# ─── Categories ───────────────────────────────────────────────────────────────

def list_categories(company_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT c.*, COALESCE(p.n, 0) AS product_count
        FROM {B2B_PRODUCT_CATEGORY_TABLE} c
        LEFT JOIN (
            SELECT category_id, COUNT(*) AS n FROM {B2B_PRODUCT_TABLE}
            WHERE is_active GROUP BY category_id
        ) p ON p.category_id = c.id
        WHERE c.company_id = %s
        ORDER BY c.position, c.name, c.id
        """,
        [company_id],
    )


def get_category(category_id: int, company_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT * FROM {B2B_PRODUCT_CATEGORY_TABLE} WHERE id = %s AND company_id = %s",
        [category_id, company_id],
    )


def create_category(
    company_id: int, *, name: str, parent_id: int | None = None
) -> dict[str, Any] | None:
    now = timezone.now()
    position = fetch_one(
        f"SELECT COALESCE(MAX(position), -1) + 1 AS next FROM {B2B_PRODUCT_CATEGORY_TABLE} "
        "WHERE company_id = %s",
        [company_id],
    )
    return fetch_one(
        f"""
        INSERT INTO {B2B_PRODUCT_CATEGORY_TABLE}
            (company_id, parent_id, name, position, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        [company_id, parent_id, name.strip(), int((position or {}).get("next") or 0), now, now],
    )


def find_or_create_category(company_id: int, name: str) -> dict[str, Any] | None:
    name = (name or "").strip()
    if not name:
        return None
    row = fetch_one(
        f"SELECT * FROM {B2B_PRODUCT_CATEGORY_TABLE} WHERE company_id = %s AND LOWER(name) = LOWER(%s) LIMIT 1",
        [company_id, name],
    )
    return row or create_category(company_id, name=name)


def update_category(category_id: int, company_id: int, **fields: Any) -> dict[str, Any] | None:
    allowed = {"name", "parent_id", "position"}
    changes = {k: v for k, v in fields.items() if k in allowed}
    if not changes:
        return get_category(category_id, company_id)
    if changes.get("parent_id") == category_id:
        raise InventoryError("Kategoriya o'zining ichida bo'la olmaydi.")
    assignments = ", ".join(f"{column} = %s" for column in changes)
    return fetch_one(
        f"""
        UPDATE {B2B_PRODUCT_CATEGORY_TABLE} SET {assignments}, updated_at = %s
        WHERE id = %s AND company_id = %s
        RETURNING *
        """,
        [*changes.values(), timezone.now(), category_id, company_id],
    )


def delete_category(category_id: int, company_id: int) -> bool:
    """Products keep existing, just uncategorised — the FK is SET NULL."""
    return bool(execute(
        f"DELETE FROM {B2B_PRODUCT_CATEGORY_TABLE} WHERE id = %s AND company_id = %s",
        [category_id, company_id],
    ))


# ─── Products ─────────────────────────────────────────────────────────────────

# `reserved` is derived, never stored: the catalogue lines of every lead still
# being worked (not completed, not lost) that have not yet been sold out of
# stock. Deriving it is what keeps it honest — a lead edited, lost or won
# changes the number without a hook that could be forgotten.
_OPEN_LEAD_LINES = f"""
    SELECT li.product_id, li.qty
    FROM {B2B_WORKSPACE_LEAD_ITEM_TABLE} li
    JOIN {B2B_WORKSPACE_LEAD_TABLE} l ON l.id = li.lead_id
    WHERE li.product_id IS NOT NULL
      AND l.deleted_at IS NULL
      AND l.status <> 'completed'
      AND l.stage NOT IN ('lost', 'archived')
      AND NOT EXISTS (
          SELECT 1 FROM {B2B_STOCK_DOCUMENT_ITEM_TABLE} di
          JOIN {B2B_STOCK_DOCUMENT_TABLE} d ON d.id = di.document_id
          WHERE di.lead_item_id = li.id AND d.status <> 'cancelled'
      )
"""

# A bundle on a lead reserves its parts, in the recipe's amounts; a product
# reserves itself; a service reserves nothing.
_RESERVED_SUBQUERY = f"""
    SELECT x.product_id, SUM(x.qty) AS reserved
    FROM (
        SELECT ol.product_id, ol.qty
        FROM ({_OPEN_LEAD_LINES}) ol
        JOIN {B2B_PRODUCT_TABLE} bp ON bp.id = ol.product_id AND bp.kind = 'product'
        UNION ALL
        SELECT pc.component_id AS product_id, pc.quantity * ol.qty AS qty
        FROM ({_OPEN_LEAD_LINES}) ol
        JOIN {B2B_PRODUCT_COMPONENT_TABLE} pc ON pc.bundle_id = ol.product_id
    ) x
    GROUP BY x.product_id
"""

_PRODUCT_SELECT = f"""
    SELECT p.*,
           c.name AS category_name,
           sup.name AS supplier_name,
           parent.name AS parent_name,
           COALESCE(s.quantity_total, 0) AS stock_total,
           COALESCE(r.reserved, 0) AS reserved,
           COALESCE(s.quantity_total, 0) - COALESCE(r.reserved, 0) AS available,
           COALESCE(s.quantity_total, 0) * p.purchase_price AS stock_value,
           COALESCE(s.stocks, '[]'::json) AS stocks,
           COALESCE(v.variant_count, 0) AS variant_count
    FROM {B2B_PRODUCT_TABLE} p
    LEFT JOIN {B2B_PRODUCT_CATEGORY_TABLE} c ON c.id = p.category_id
    LEFT JOIN {B2B_SUPPLIER_TABLE} sup ON sup.id = p.supplier_id
    LEFT JOIN {B2B_PRODUCT_TABLE} parent ON parent.id = p.parent_id
    LEFT JOIN (
        SELECT st.product_id,
               SUM(st.quantity) AS quantity_total,
               json_agg(json_build_object(
                   'warehouse_id', st.warehouse_id,
                   'warehouse_name', w.name,
                   'quantity', st.quantity
               ) ORDER BY w.is_default DESC, w.name) AS stocks
        FROM {B2B_STOCK_TABLE} st
        JOIN {B2B_WAREHOUSE_TABLE} w ON w.id = st.warehouse_id AND w.is_active
        GROUP BY st.product_id
    ) s ON s.product_id = p.id
    LEFT JOIN ({_RESERVED_SUBQUERY}) r ON r.product_id = p.id
    LEFT JOIN (
        SELECT parent_id, COUNT(*) AS variant_count FROM {B2B_PRODUCT_TABLE}
        WHERE parent_id IS NOT NULL AND is_active GROUP BY parent_id
    ) v ON v.parent_id = p.id
"""


def _shape_product(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return row
    stocks = _json(row.get("stocks")) or []
    row["stocks"] = [{**s, "quantity": _q(s.get("quantity"))} for s in stocks]
    row["stock_total"] = _q(row.get("stock_total"))
    row["reserved"] = _q(row.get("reserved"))
    row["available"] = _q(row.get("available"))
    row["stock_value"] = _q(row.get("stock_value"))
    row["attributes"] = _json(row.get("attributes")) or {}
    row["photo_url"] = photo_url(row.get("photo"))
    row["kind"] = row.get("kind") or ProductKind.PRODUCT
    tracks_stock = row["kind"] == ProductKind.PRODUCT
    row["is_low"] = bool(
        tracks_stock and _q(row.get("min_stock")) > 0
        and row["stock_total"] <= _q(row.get("min_stock"))
    )
    row["is_out"] = bool(tracks_stock and row["available"] <= 0)
    row["stock_status"] = (
        "n/a" if not tracks_stock else "out" if row["is_out"] else "low" if row["is_low"] else "ok"
    )
    return row


def list_products(
    company_id: int,
    *,
    q: str | None = None,
    category_id: int | None = None,
    warehouse_id: int | None = None,
    supplier_id: int | None = None,
    brand: str | None = None,
    kind: str | None = None,
    price_min=None,
    price_max=None,
    status: str | None = None,
    low_stock: bool = False,
    include_inactive: bool = False,
    include_variants: bool = True,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """The catalogue, with every product's stock per warehouse folded in.

    One query with a JSON aggregate rather than a stock query per product:
    the screen lists the whole catalogue and a shop with four hundred SKUs
    would otherwise wait on four hundred round trips.

    ``status`` is the TZ's filter row: ``active``, ``inactive``, ``low``,
    ``zero``, ``archived``. Omitted, the list is the active catalogue.
    """
    where = ["p.company_id = %s"]
    params: list[Any] = [company_id]
    status = status or ("archived" if include_inactive else None)
    if status == "archived":
        where.append("NOT p.is_active")
    elif status == "inactive":
        where.append("NOT p.is_active")
    elif status is None or status in ("active", "low", "zero"):
        where.append("p.is_active")
    if q:
        where.append("(p.name ILIKE %s OR p.sku ILIKE %s OR p.barcode ILIKE %s OR p.brand ILIKE %s)")
        like = f"%{q.strip()}%"
        params.extend([like, like, like, like])
    if category_id:
        where.append("p.category_id = %s")
        params.append(category_id)
    if supplier_id:
        where.append("p.supplier_id = %s")
        params.append(supplier_id)
    if brand:
        where.append("p.brand ILIKE %s")
        params.append(brand.strip())
    if kind:
        where.append("p.kind = %s")
        params.append(kind)
    if price_min is not None:
        where.append("p.sale_price >= %s")
        params.append(_q(price_min))
    if price_max is not None:
        where.append("p.sale_price <= %s")
        params.append(_q(price_max))
    if warehouse_id:
        where.append(
            f"EXISTS (SELECT 1 FROM {B2B_STOCK_TABLE} x "
            "WHERE x.product_id = p.id AND x.warehouse_id = %s AND x.quantity <> 0)"
        )
        params.append(warehouse_id)
    if low_stock or status == "low":
        where.append("p.kind = 'product' AND p.min_stock > 0 AND COALESCE(s.quantity_total, 0) <= p.min_stock")
    if status == "zero":
        where.append("p.kind = 'product' AND COALESCE(s.quantity_total, 0) <= 0")
    if not include_variants:
        where.append("p.parent_id IS NULL")
    rows = fetch_all(
        f"{_PRODUCT_SELECT} WHERE {' AND '.join(where)} ORDER BY p.name, p.id LIMIT %s",
        [*params, limit],
    )
    return [_shape_product(row) for row in rows]


def list_brands(company_id: int) -> list[str]:
    rows = fetch_all(
        f"SELECT DISTINCT brand FROM {B2B_PRODUCT_TABLE} "
        "WHERE company_id = %s AND brand IS NOT NULL AND brand <> '' ORDER BY brand",
        [company_id],
    )
    return [row["brand"] for row in rows]


def get_product(product_id: int, company_id: int) -> dict[str, Any] | None:
    row = _shape_product(fetch_one(
        f"{_PRODUCT_SELECT} WHERE p.id = %s AND p.company_id = %s",
        [product_id, company_id],
    ))
    if row and row["kind"] == ProductKind.BUNDLE:
        row["components"] = list_components(product_id)
    return row


def get_product_raw(product_id: int, company_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT * FROM {B2B_PRODUCT_TABLE} WHERE id = %s AND company_id = %s",
        [product_id, company_id],
    )


def find_product_by_sku(company_id: int, sku: str) -> dict[str, Any] | None:
    if not sku:
        return None
    return fetch_one(
        f"SELECT * FROM {B2B_PRODUCT_TABLE} WHERE company_id = %s AND sku = %s LIMIT 1",
        [company_id, sku.strip()],
    )


def _sku_taken(company_id: int, column: str, value: str | None, *, exclude_id: int | None) -> bool:
    if not value:
        return False
    row = fetch_one(
        f"SELECT id FROM {B2B_PRODUCT_TABLE} WHERE company_id = %s AND {column} = %s "
        f"{'AND id <> %s' if exclude_id else ''} LIMIT 1",
        [company_id, value, *([exclude_id] if exclude_id else [])],
    )
    return bool(row)


def _price_from_markup(purchase, markup) -> Decimal | None:
    """Sale price = purchase price + markup — the card's own arithmetic."""
    if purchase is None or markup is None:
        return None
    return (_q(purchase) * (Decimal(100) + _q(markup)) / Decimal(100)).quantize(Decimal("0.01"))


def create_product(
    company_id: int,
    *,
    name: str,
    author_id: int | None = None,
    kind: str = ProductKind.PRODUCT,
    category_id: int | None = None,
    supplier_id: int | None = None,
    brand: str | None = None,
    sku: str | None = None,
    barcode: str | None = None,
    generate_sku: bool = False,
    generate_barcode: bool = False,
    unit: str = "dona",
    purchase_price=0,
    sale_price=None,
    markup_percent=None,
    wholesale_price=0,
    allow_free_price: bool = False,
    min_stock=0,
    description: str | None = None,
    attributes: dict[str, Any] | None = None,
    parent_id: int | None = None,
    variant_label: str | None = None,
    currency: str | None = None,
    components: Sequence[dict[str, Any]] = (),
    initial_quantity=None,
    initial_warehouse_id: int | None = None,
) -> dict[str, Any]:
    """Files a product, and — if the sheet said how many are already on the
    shelf — books them as an opening receipt so the ledger starts honest."""
    kind = kind if kind in ProductKind.CHOICES else ProductKind.PRODUCT
    sku = (sku or "").strip() or None
    barcode = (barcode or "").strip() or None
    if _sku_taken(company_id, "sku", sku, exclude_id=None):
        raise InventoryError("Bu artikul allaqachon mavjud.", code="sku_taken")
    if _sku_taken(company_id, "barcode", barcode, exclude_id=None):
        raise InventoryError("Bu shtrix-kod allaqachon mavjud.", code="barcode_taken")
    if sale_price is None:
        sale_price = _price_from_markup(purchase_price, markup_percent) or 0
    if parent_id and not get_product_raw(parent_id, company_id):
        raise InventoryError("Asosiy tovar topilmadi.", code="product_not_found")
    now = timezone.now()
    with transaction.atomic():
        if generate_sku and not sku:
            sku = next_sku(company_id)
        if generate_barcode and not barcode:
            barcode = next_barcode(company_id)
        row = fetch_one(
            f"""
            INSERT INTO {B2B_PRODUCT_TABLE}
                (company_id, kind, category_id, supplier_id, brand, name, sku, barcode, unit,
                 purchase_price, sale_price, markup_percent, wholesale_price, allow_free_price,
                 min_stock, description, attributes, parent_id, variant_label, currency,
                 is_active, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb,
                    %s, %s, %s, TRUE, %s, %s)
            RETURNING *
            """,
            [
                company_id, kind, category_id, supplier_id, (brand or "").strip() or None,
                name.strip(), sku, barcode, (unit or "dona").strip(),
                _q(purchase_price), _q(sale_price),
                _q(markup_percent) if markup_percent is not None else None,
                _q(wholesale_price), bool(allow_free_price), _q(min_stock),
                (description or "").strip() or None,
                json.dumps(attributes) if attributes else None,
                parent_id, (variant_label or "").strip() or None,
                (currency or get_settings(company_id).get("base_currency") or "UZS")[:3].upper(),
                now, now,
            ],
        )
        if kind == ProductKind.BUNDLE:
            replace_components(row["id"], company_id, components)
        if kind == ProductKind.PRODUCT and initial_quantity is not None and _q(initial_quantity) > 0:
            from apps.b2b.workspace import inventory_documents as documents

            warehouse = (
                get_warehouse(initial_warehouse_id, company_id)
                if initial_warehouse_id else default_warehouse(company_id)
            )
            if warehouse is None:
                raise InventoryError("Sklad topilmadi.", code="warehouse_not_found")
            doc = documents.create_document(
                company_id,
                kind="receipt",
                author_id=author_id,
                warehouse_id=warehouse["id"],
                supplier_id=supplier_id,
                note="Boshlang'ich qoldiq",
                items=[{"product_id": row["id"], "quantity": initial_quantity, "unit_cost": purchase_price}],
            )
            documents.confirm_document(doc["id"], company_id, actor_id=author_id)
    return get_product(row["id"], company_id) or row


def update_product(
    product_id: int, company_id: int, *, author_id: int | None = None, **fields: Any
) -> dict[str, Any] | None:
    allowed = {
        "name", "kind", "category_id", "supplier_id", "brand", "sku", "barcode", "unit",
        "purchase_price", "sale_price", "markup_percent", "wholesale_price",
        "allow_free_price", "min_stock", "description", "attributes", "is_active",
        "parent_id", "variant_label", "currency", "photo",
    }
    changes = {k: v for k, v in fields.items() if k in allowed}
    components = fields.get("components")
    current = get_product_raw(product_id, company_id)
    if not current:
        return None
    if not changes and components is None:
        return get_product(product_id, company_id)
    for column in ("sku", "barcode"):
        if column in changes:
            changes[column] = (changes[column] or "").strip() or None
            if _sku_taken(company_id, column, changes[column], exclude_id=product_id):
                raise InventoryError(
                    "Bu artikul allaqachon mavjud." if column == "sku"
                    else "Bu shtrix-kod allaqachon mavjud.",
                    code=f"{column}_taken",
                )
    if changes.get("parent_id") == product_id:
        raise InventoryError("Tovar o'zining varianti bo'la olmaydi.")
    # Markup edited, sale price not: the price follows.
    if "markup_percent" in changes and "sale_price" not in changes and changes["markup_percent"] is not None:
        derived = _price_from_markup(
            changes.get("purchase_price", current.get("purchase_price")), changes["markup_percent"]
        )
        if derived is not None:
            changes["sale_price"] = derived
    for column in ("purchase_price", "sale_price", "wholesale_price", "min_stock"):
        if column in changes and changes[column] is not None:
            changes[column] = _q(changes[column])
    if "markup_percent" in changes and changes["markup_percent"] is not None:
        changes["markup_percent"] = _q(changes["markup_percent"])
    if "attributes" in changes:
        changes["attributes"] = json.dumps(changes["attributes"]) if changes["attributes"] else None
    if "currency" in changes and changes["currency"]:
        changes["currency"] = str(changes["currency"])[:3].upper()
    with transaction.atomic():
        for field in ("sale_price", "wholesale_price", "purchase_price"):
            if field in changes and changes[field] is not None and _q(current.get(field)) != changes[field]:
                record_price_change(
                    company_id, product_id, field=field,
                    old_price=current.get(field), new_price=changes[field], author_id=author_id,
                )
        if changes:
            assignments = ", ".join(
                f"{column} = %s::jsonb" if column == "attributes" else f"{column} = %s"
                for column in changes
            )
            execute(
                f"""
                UPDATE {B2B_PRODUCT_TABLE} SET {assignments}, updated_at = %s
                WHERE id = %s AND company_id = %s
                """,
                [*changes.values(), timezone.now(), product_id, company_id],
            )
        if components is not None:
            replace_components(product_id, company_id, components)
    return get_product(product_id, company_id)


def delete_product(product_id: int, company_id: int) -> bool:
    """Archives rather than deletes: the ledger and every won lead's line
    still point at it, and a sale made last month should keep its name."""
    return bool(execute(
        f"UPDATE {B2B_PRODUCT_TABLE} SET is_active = FALSE, updated_at = %s "
        "WHERE id = %s AND company_id = %s AND is_active",
        [timezone.now(), product_id, company_id],
    ))


def list_variants(product_id: int, company_id: int) -> list[dict[str, Any]]:
    rows = fetch_all(
        f"{_PRODUCT_SELECT} WHERE p.parent_id = %s AND p.company_id = %s ORDER BY p.variant_label, p.id",
        [product_id, company_id],
    )
    return [_shape_product(row) for row in rows]


# ─── Bundles ──────────────────────────────────────────────────────────────────

def list_components(bundle_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT c.id, c.component_id, c.quantity, p.name, p.sku, p.unit, p.kind,
               p.sale_price, p.purchase_price
        FROM {B2B_PRODUCT_COMPONENT_TABLE} c
        JOIN {B2B_PRODUCT_TABLE} p ON p.id = c.component_id
        WHERE c.bundle_id = %s
        ORDER BY c.id
        """,
        [bundle_id],
    )


def replace_components(bundle_id: int, company_id: int, components: Sequence[dict[str, Any]]) -> None:
    """A bundle's recipe, swapped whole. A bundle inside a bundle is refused:
    the sale would have to unfold recursively, and nobody has asked for it."""
    execute(f"DELETE FROM {B2B_PRODUCT_COMPONENT_TABLE} WHERE bundle_id = %s", [bundle_id])
    for component in components or ():
        component_id = int(component.get("component_id") or component.get("product_id") or 0)
        if not component_id or component_id == bundle_id:
            continue
        part = get_product_raw(component_id, company_id)
        if not part:
            raise InventoryError("Komplekt tarkibidagi tovar topilmadi.", code="product_not_found")
        if part.get("kind") == ProductKind.BUNDLE:
            raise InventoryError("Komplekt ichida komplekt bo'la olmaydi.", code="nested_bundle")
        qty = _q(component.get("quantity") or 1)
        if qty <= 0:
            continue
        execute(
            f"""
            INSERT INTO {B2B_PRODUCT_COMPONENT_TABLE} (bundle_id, component_id, quantity)
            VALUES (%s, %s, %s)
            ON CONFLICT (bundle_id, component_id) DO UPDATE SET quantity = EXCLUDED.quantity
            """,
            [bundle_id, component_id, qty],
        )


def expand_for_stock(company_id: int, product_id: int, quantity) -> list[tuple[dict[str, Any], Decimal]]:
    """What actually leaves the shelf when `quantity` of `product_id` is
    sold: the product itself, or a bundle's components in their amounts, or
    nothing at all for a service."""
    product = get_product_raw(product_id, company_id)
    if not product:
        raise InventoryError("Tovar topilmadi.", code="product_not_found")
    qty = _q(quantity)
    kind = product.get("kind") or ProductKind.PRODUCT
    if kind == ProductKind.SERVICE:
        return []
    if kind == ProductKind.BUNDLE:
        parts = []
        for component in list_components(product_id):
            if component.get("kind") == ProductKind.SERVICE:
                continue
            part = get_product_raw(int(component["component_id"]), company_id)
            if part:
                parts.append((part, _q(component["quantity"]) * qty))
        return parts
    return [(product, qty)]


# ─── Price history ────────────────────────────────────────────────────────────

def record_price_change(
    company_id: int, product_id: int, *, field: str, old_price, new_price,
    author_id: int | None, document_id: int | None = None,
) -> None:
    execute(
        f"""
        INSERT INTO {B2B_PRICE_HISTORY_TABLE}
            (company_id, product_id, field, old_price, new_price, author_id, document_id, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        [
            company_id, product_id, field,
            _q(old_price) if old_price is not None else None,
            _q(new_price) if new_price is not None else None,
            author_id, document_id, timezone.now(),
        ],
    )


def list_price_history(product_id: int, company_id: int, *, limit: int = 100) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT h.*, e.full_name AS author_name, d.number AS document_number
        FROM {B2B_PRICE_HISTORY_TABLE} h
        LEFT JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = h.author_id
        LEFT JOIN {B2B_STOCK_DOCUMENT_TABLE} d ON d.id = h.document_id
        WHERE h.product_id = %s AND h.company_id = %s
        ORDER BY h.created_at DESC, h.id DESC
        LIMIT %s
        """,
        [product_id, company_id, limit],
    )


# ─── The ledger ───────────────────────────────────────────────────────────────

def _lock_stock(product_id: int, warehouse_id: int) -> Decimal:
    """The row for (product, warehouse), created if missing, locked for the
    rest of the transaction so two movements cannot both read the same
    balance and each subtract from it."""
    execute(
        f"""
        INSERT INTO {B2B_STOCK_TABLE} (product_id, warehouse_id, quantity, updated_at)
        VALUES (%s, %s, 0, %s)
        ON CONFLICT (product_id, warehouse_id) DO NOTHING
        """,
        [product_id, warehouse_id, timezone.now()],
    )
    row = fetch_one(
        f"SELECT quantity FROM {B2B_STOCK_TABLE} "
        "WHERE product_id = %s AND warehouse_id = %s FOR UPDATE",
        [product_id, warehouse_id],
    )
    return _q((row or {}).get("quantity"))


def _set_stock(product_id: int, warehouse_id: int, quantity: Decimal) -> None:
    execute(
        f"UPDATE {B2B_STOCK_TABLE} SET quantity = %s, updated_at = %s "
        "WHERE product_id = %s AND warehouse_id = %s",
        [quantity, timezone.now(), product_id, warehouse_id],
    )


def stock_at(product_id: int, warehouse_id: int) -> Decimal:
    row = fetch_one(
        f"SELECT quantity FROM {B2B_STOCK_TABLE} WHERE product_id = %s AND warehouse_id = %s",
        [product_id, warehouse_id],
    )
    return _q((row or {}).get("quantity"))


def apply_movement(
    company_id: int,
    *,
    kind: str,
    product_id: int,
    warehouse_id: int,
    quantity,
    to_warehouse_id: int | None = None,
    unit_cost=None,
    note: str | None = None,
    author_id: int | None = None,
    lead_id: int | None = None,
    lead_item_id: int | None = None,
    document_id: int | None = None,
    customer_id: int | None = None,
    currency: str | None = None,
    reversal_of: int | None = None,
    allow_negative: bool = False,
) -> dict[str, Any]:
    """Moves stock and writes the row that says so, as one transaction.

    ``quantity`` is what moved. For every kind but ``adjustment`` it is
    positive and ``kind`` gives the direction; for an adjustment it is the
    *counted* quantity on the shelf, and the row stores the signed difference
    from what the ledger believed, so the ledger sums to the count afterwards.

    Taking more than is there is refused with [InventoryError] — except when
    ``allow_negative`` is set, which the storno of a receipt needs: the goods
    it brought in may already have been sold, and the reversal is still the
    truth about that receipt.
    """
    if kind not in MovementKind.CHOICES:
        raise InventoryError("Noma'lum harakat turi.", code="bad_kind")
    product = get_product_raw(product_id, company_id)
    if not product:
        raise InventoryError("Tovar topilmadi.", code="product_not_found")
    if (product.get("kind") or ProductKind.PRODUCT) != ProductKind.PRODUCT:
        raise InventoryError(
            "Xizmat va komplekt fizik qoldiqqa ega emas.", code="no_stock_kind"
        )
    source = get_warehouse(warehouse_id, company_id)
    if not source or not source.get("is_active"):
        raise InventoryError("Sklad topilmadi.", code="warehouse_not_found")

    qty = _q(quantity)
    if kind == MovementKind.TRANSFER:
        if not to_warehouse_id or to_warehouse_id == warehouse_id:
            raise InventoryError("Qabul qiluvchi skladni tanlang.", code="bad_target")
        target = get_warehouse(to_warehouse_id, company_id)
        if not target or not target.get("is_active"):
            raise InventoryError("Qabul qiluvchi sklad topilmadi.", code="warehouse_not_found")
    else:
        to_warehouse_id = None
    if kind != MovementKind.ADJUSTMENT and qty <= 0:
        raise InventoryError("Miqdor noldan katta bo'lishi kerak.", code="bad_quantity")
    if kind == MovementKind.ADJUSTMENT and qty < 0:
        raise InventoryError("Sanoq manfiy bo'la olmaydi.", code="bad_quantity")

    cost_price = _q(product.get("purchase_price"))
    if unit_cost is None:
        unit_cost = (
            _q(product.get("sale_price")) if kind in (MovementKind.SALE, MovementKind.RETURN)
            else cost_price
        )
    unit_cost = _q(unit_cost)
    currency = (currency or product.get("currency") or "UZS")[:3].upper()

    now = timezone.now()
    with transaction.atomic():
        # Locks are taken in id order so two transfers in opposite directions
        # between the same pair cannot deadlock on each other.
        lock_ids = sorted({warehouse_id, *([to_warehouse_id] if to_warehouse_id else [])})
        balances = {wid: _lock_stock(product_id, wid) for wid in lock_ids}
        before = balances[warehouse_id]

        if kind in MovementKind.INBOUND:
            _set_stock(product_id, warehouse_id, before + qty)
            stored_qty = qty
        elif kind in MovementKind.OUTBOUND or kind == MovementKind.TRANSFER:
            if before - qty < 0 and not allow_negative:
                raise InventoryError(
                    f"Skladda yetarli tovar yo'q: {product.get('name')} — "
                    f"{before.normalize()} {product.get('unit') or ''}".strip(),
                    code="insufficient_stock",
                    details=[{
                        "product_id": product_id, "name": product.get("name"),
                        "unit": product.get("unit"), "warehouse_id": warehouse_id,
                        "needed": qty, "available": before, "short": qty - before,
                    }],
                )
            _set_stock(product_id, warehouse_id, before - qty)
            if kind == MovementKind.TRANSFER:
                _set_stock(product_id, to_warehouse_id, balances[to_warehouse_id] + qty)
            stored_qty = qty
        else:  # adjustment: qty is the count, the row keeps the difference
            stored_qty = qty - before
            _set_stock(product_id, warehouse_id, qty)

        row = fetch_one(
            f"""
            INSERT INTO {B2B_STOCK_MOVEMENT_TABLE}
                (company_id, product_id, warehouse_id, to_warehouse_id, kind, quantity,
                 unit_cost, cost_price, note, lead_id, lead_item_id, author_id, created_at,
                 document_id, currency, customer_id, reversal_of)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            [
                company_id, product_id, warehouse_id, to_warehouse_id, kind, stored_qty,
                unit_cost, cost_price, (note or "").strip() or None, lead_id, lead_item_id,
                author_id, now, document_id, currency, customer_id, reversal_of,
            ],
        )
    row["product_name"] = product.get("name")
    row["unit"] = product.get("unit")
    row["warehouse_name"] = source.get("name")
    row["balance_after"] = qty if kind == MovementKind.ADJUSTMENT else (
        before + qty if kind in MovementKind.INBOUND else before - qty
    )
    return row


def list_movements(
    company_id: int,
    *,
    product_id: int | None = None,
    warehouse_id: int | None = None,
    kind: str | None = None,
    category_id: int | None = None,
    supplier_id: int | None = None,
    customer_id: int | None = None,
    author_id: int | None = None,
    document_id: int | None = None,
    q: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    where = ["m.company_id = %s"]
    params: list[Any] = [company_id]
    if product_id:
        where.append("m.product_id = %s")
        params.append(product_id)
    if warehouse_id:
        where.append("(m.warehouse_id = %s OR m.to_warehouse_id = %s)")
        params.extend([warehouse_id, warehouse_id])
    if kind:
        where.append("m.kind = %s")
        params.append(kind)
    if category_id:
        where.append("p.category_id = %s")
        params.append(category_id)
    if supplier_id:
        where.append("d.supplier_id = %s")
        params.append(supplier_id)
    if customer_id:
        where.append("COALESCE(m.customer_id, d.customer_id) = %s")
        params.append(customer_id)
    if author_id:
        where.append("m.author_id = %s")
        params.append(author_id)
    if document_id:
        where.append("m.document_id = %s")
        params.append(document_id)
    if q:
        where.append("(p.name ILIKE %s OR p.sku ILIKE %s OR p.barcode ILIKE %s)")
        like = f"%{q.strip()}%"
        params.extend([like, like, like])
    if date_from:
        where.append("m.created_at >= %s")
        params.append(date_from)
    if date_to:
        where.append("m.created_at < %s")
        params.append(date_to)
    return fetch_all(
        f"""
        SELECT m.*, p.name AS product_name, p.unit, p.sku, p.category_id,
               w.name AS warehouse_name, t.name AS to_warehouse_name,
               e.full_name AS author_name,
               d.number AS document_number, d.kind AS document_kind, d.status AS document_status,
               d.reason AS document_reason,
               s.name AS supplier_name,
               cu.full_name AS customer_name
        FROM {B2B_STOCK_MOVEMENT_TABLE} m
        JOIN {B2B_PRODUCT_TABLE} p ON p.id = m.product_id
        JOIN {B2B_WAREHOUSE_TABLE} w ON w.id = m.warehouse_id
        LEFT JOIN {B2B_WAREHOUSE_TABLE} t ON t.id = m.to_warehouse_id
        LEFT JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = m.author_id
        LEFT JOIN {B2B_STOCK_DOCUMENT_TABLE} d ON d.id = m.document_id
        LEFT JOIN {B2B_SUPPLIER_TABLE} s ON s.id = d.supplier_id
        LEFT JOIN b2b_workspace_customer cu ON cu.id = COALESCE(m.customer_id, d.customer_id)
        WHERE {' AND '.join(where)}
        ORDER BY m.created_at DESC, m.id DESC
        LIMIT %s
        """,
        [*params, limit],
    )


# ─── Turnover ─────────────────────────────────────────────────────────────────

def summary(
    company_id: int, *, date_from: datetime | None = None, date_to: datetime | None = None
) -> dict[str, Any]:
    """What the turnover screen prints: the shelf as it stands, and what
    moved through it in the window.

    Profit is revenue minus the cost price frozen on each sale row — not
    minus today's purchase price — so editing a price does not rewrite last
    month's margin.
    """
    now = timezone.now()
    date_to = date_to or now
    date_from = date_from or (date_to - timedelta(days=30))

    shelf = fetch_one(
        f"""
        SELECT COUNT(DISTINCT p.id) FILTER (WHERE p.is_active) AS product_count,
               COALESCE(SUM(st.quantity), 0) AS quantity_total,
               COALESCE(SUM(st.quantity * p.purchase_price), 0) AS stock_value,
               COALESCE(SUM(st.quantity * p.sale_price), 0) AS retail_value,
               COUNT(DISTINCT p.id) FILTER (
                   WHERE p.is_active AND p.kind = 'product' AND p.min_stock > 0
                     AND COALESCE(tot.quantity_total, 0) <= p.min_stock
               ) AS low_stock_count,
               COUNT(DISTINCT p.id) FILTER (
                   WHERE p.is_active AND p.kind = 'product' AND COALESCE(tot.quantity_total, 0) <= 0
               ) AS out_of_stock_count
        FROM {B2B_PRODUCT_TABLE} p
        LEFT JOIN {B2B_STOCK_TABLE} st ON st.product_id = p.id
        LEFT JOIN (
            SELECT product_id, SUM(quantity) AS quantity_total
            FROM {B2B_STOCK_TABLE} GROUP BY product_id
        ) tot ON tot.product_id = p.id
        WHERE p.company_id = %s
        """,
        [company_id],
    ) or {}

    flows = fetch_one(
        f"""
        SELECT
            COALESCE(SUM(quantity) FILTER (WHERE kind = 'sale'), 0) AS sold_qty,
            COALESCE(SUM(quantity * unit_cost) FILTER (WHERE kind = 'sale'), 0) AS revenue,
            COALESCE(SUM(quantity * cost_price) FILTER (WHERE kind = 'sale'), 0) AS cogs,
            COALESCE(SUM(quantity) FILTER (WHERE kind = 'return'), 0) AS returned_qty,
            COALESCE(SUM(quantity * unit_cost) FILTER (WHERE kind = 'return'), 0) AS returns,
            COALESCE(SUM(quantity * cost_price) FILTER (WHERE kind = 'return'), 0) AS returns_cost,
            COALESCE(SUM(quantity) FILTER (WHERE kind = 'receipt'), 0) AS received_qty,
            COALESCE(SUM(quantity * unit_cost) FILTER (WHERE kind = 'receipt'), 0) AS purchases,
            COALESCE(SUM(quantity) FILTER (WHERE kind = 'write_off'), 0) AS written_off_qty,
            COALESCE(SUM(quantity * cost_price) FILTER (WHERE kind = 'write_off'), 0) AS write_offs,
            COALESCE(SUM(ABS(quantity)) FILTER (WHERE kind = 'adjustment'), 0) AS adjusted_qty,
            COALESCE(SUM(quantity * cost_price) FILTER (WHERE kind = 'adjustment'), 0) AS adjustments,
            COUNT(*) FILTER (WHERE kind = 'adjustment') AS adjustment_count,
            COUNT(*) FILTER (WHERE kind = 'sale') AS sale_count
        FROM {B2B_STOCK_MOVEMENT_TABLE} m
        WHERE m.company_id = %s AND m.created_at >= %s AND m.created_at < %s
          AND m.reversal_of IS NULL
          AND NOT EXISTS (SELECT 1 FROM {B2B_STOCK_MOVEMENT_TABLE} rv WHERE rv.reversal_of = m.id)
        """,
        [company_id, date_from, date_to],
    ) or {}

    # Days are cut in the workspace's own time zone, not UTC's: a sale rung
    # up at 23:30 in Tashkent belongs to that evening, not to the next day's
    # bar on the chart.
    tz_name = timezone.get_current_timezone_name()
    daily = fetch_all(
        f"""
        SELECT DATE(created_at AT TIME ZONE %s) AS day,
               COALESCE(SUM(quantity * unit_cost) FILTER (WHERE kind = 'sale'), 0) AS revenue,
               COALESCE(SUM(quantity * unit_cost) FILTER (WHERE kind = 'receipt'), 0) AS purchases
        FROM {B2B_STOCK_MOVEMENT_TABLE} m
        WHERE m.company_id = %s AND m.created_at >= %s AND m.created_at < %s
          AND m.kind IN ('sale', 'receipt') AND m.reversal_of IS NULL
          AND NOT EXISTS (SELECT 1 FROM {B2B_STOCK_MOVEMENT_TABLE} rv WHERE rv.reversal_of = m.id)
        GROUP BY DATE(created_at AT TIME ZONE %s)
        ORDER BY day
        """,
        [tz_name, company_id, date_from, date_to, tz_name],
    )

    top = fetch_all(
        f"""
        SELECT p.id AS product_id, p.name, p.unit,
               SUM(m.quantity) AS sold_qty,
               SUM(m.quantity * m.unit_cost) AS revenue,
               SUM(m.quantity * (m.unit_cost - m.cost_price)) AS profit
        FROM {B2B_STOCK_MOVEMENT_TABLE} m
        JOIN {B2B_PRODUCT_TABLE} p ON p.id = m.product_id
        WHERE m.company_id = %s AND m.kind = 'sale' AND m.reversal_of IS NULL
          AND NOT EXISTS (SELECT 1 FROM {B2B_STOCK_MOVEMENT_TABLE} rv WHERE rv.reversal_of = m.id)
          AND m.created_at >= %s AND m.created_at < %s
        GROUP BY p.id, p.name, p.unit
        ORDER BY revenue DESC, sold_qty DESC
        LIMIT 5
        """,
        [company_id, date_from, date_to],
    )

    by_supplier = fetch_all(
        f"""
        SELECT s.id AS supplier_id, s.name,
               COUNT(DISTINCT d.id) AS receipt_count,
               COALESCE(SUM(i.quantity * i.unit_cost), 0) AS purchases
        FROM {B2B_STOCK_DOCUMENT_TABLE} d
        JOIN {B2B_SUPPLIER_TABLE} s ON s.id = d.supplier_id
        JOIN {B2B_STOCK_DOCUMENT_ITEM_TABLE} i ON i.document_id = d.id
        WHERE d.company_id = %s AND d.kind = 'receipt' AND d.status = 'confirmed'
          AND d.confirmed_at >= %s AND d.confirmed_at < %s
        GROUP BY s.id, s.name
        ORDER BY purchases DESC
        LIMIT 10
        """,
        [company_id, date_from, date_to],
    )

    write_off_reasons = fetch_all(
        f"""
        SELECT COALESCE(d.reason, 'other') AS reason,
               SUM(m.quantity) AS quantity,
               SUM(m.quantity * m.cost_price) AS value
        FROM {B2B_STOCK_MOVEMENT_TABLE} m
        LEFT JOIN {B2B_STOCK_DOCUMENT_TABLE} d ON d.id = m.document_id
        WHERE m.company_id = %s AND m.kind = 'write_off' AND m.reversal_of IS NULL
          AND NOT EXISTS (SELECT 1 FROM {B2B_STOCK_MOVEMENT_TABLE} rv WHERE rv.reversal_of = m.id)
          AND m.created_at >= %s AND m.created_at < %s
        GROUP BY COALESCE(d.reason, 'other')
        ORDER BY value DESC
        """,
        [company_id, date_from, date_to],
    )

    revenue = _q(flows.get("revenue")) - _q(flows.get("returns"))
    cogs = _q(flows.get("cogs")) - _q(flows.get("returns_cost"))
    stock_value = _q(shelf.get("stock_value"))
    days = max((date_to - date_from).days, 1)
    # Turnover: how many times the shelf's value was sold through in the
    # window, annualised — the ratio buyers compare warehouses by.
    turnover_ratio = (cogs / stock_value * Decimal(365) / Decimal(days)) if stock_value > 0 else Decimal(0)
    return {
        "date_from": date_from,
        "date_to": date_to,
        "product_count": int(shelf.get("product_count") or 0),
        "quantity_total": _q(shelf.get("quantity_total")),
        "stock_value": stock_value,
        "retail_value": _q(shelf.get("retail_value")),
        "low_stock_count": int(shelf.get("low_stock_count") or 0),
        "out_of_stock_count": int(shelf.get("out_of_stock_count") or 0),
        "sale_count": int(flows.get("sale_count") or 0),
        "sold_qty": _q(flows.get("sold_qty")),
        "revenue": revenue,
        "cogs": cogs,
        "profit": revenue - cogs,
        "received_qty": _q(flows.get("received_qty")),
        "purchases": _q(flows.get("purchases")),
        "written_off_qty": _q(flows.get("written_off_qty")),
        "write_offs": _q(flows.get("write_offs")),
        "returned_qty": _q(flows.get("returned_qty")),
        "adjusted_qty": _q(flows.get("adjusted_qty")),
        "adjustments": _q(flows.get("adjustments")),
        "adjustment_count": int(flows.get("adjustment_count") or 0),
        "turnover_ratio": turnover_ratio.quantize(Decimal("0.01")),
        "daily": [
            {"day": row["day"], "revenue": _q(row["revenue"]), "purchases": _q(row["purchases"])}
            for row in daily
        ],
        "top_products": [
            {
                "product_id": row["product_id"],
                "name": row["name"],
                "unit": row["unit"],
                "sold_qty": _q(row["sold_qty"]),
                "revenue": _q(row["revenue"]),
                "profit": _q(row["profit"]),
            }
            for row in top
        ],
        "by_supplier": [
            {
                "supplier_id": row["supplier_id"], "name": row["name"],
                "receipt_count": int(row["receipt_count"] or 0), "purchases": _q(row["purchases"]),
            }
            for row in by_supplier
        ],
        "write_off_reasons": [
            {"reason": row["reason"], "quantity": _q(row["quantity"]), "value": _q(row["value"])}
            for row in write_off_reasons
        ],
    }


# ─── Notifications ────────────────────────────────────────────────────────────

MANAGER_ROLES = ("owner", "admin", "manager", "performer", "lider")


def notify_managers(
    company_id: int, *, title: str, body: str, payload: dict[str, Any] | None = None,
    exclude_employee_id: int | None = None,
) -> int:
    """One in-app notification per person running the workspace. Never
    raises — a notification that could not be written is worth a log line,
    not a failed receipt."""
    from apps.b2b.mail import repository as mail_repo

    try:
        rows = fetch_all(
            f"SELECT id FROM {B2B_EMPLOYEE_TABLE} WHERE company_id = %s AND is_active = TRUE "
            f"AND role = ANY(%s)",
            [company_id, list(MANAGER_ROLES)],
        )
        sent = 0
        for row in rows:
            if exclude_employee_id and row["id"] == exclude_employee_id:
                continue
            mail_repo.create_notification(
                company_id=company_id, employee_id=row["id"], kind="inventory",
                title=title, body=body, payload=payload or {},
            )
            sent += 1
        return sent
    except Exception:  # noqa: BLE001
        logger.exception("Could not write the stock notification %r", title)
        return 0


def alert_low_stock(company_id: int, product_ids: Sequence[int], *, actor_id: int | None = None) -> None:
    """After anything that took stock off: say which products crossed their
    reorder line, and which ran out."""
    if not product_ids:
        return
    rows = fetch_all(
        f"""
        SELECT p.id, p.name, p.unit, p.min_stock, COALESCE(SUM(st.quantity), 0) AS total
        FROM {B2B_PRODUCT_TABLE} p
        LEFT JOIN {B2B_STOCK_TABLE} st ON st.product_id = p.id
        WHERE p.company_id = %s AND p.id = ANY(%s) AND p.kind = 'product'
        GROUP BY p.id
        """,
        [company_id, list({int(i) for i in product_ids})],
    )
    for row in rows:
        total = _q(row["total"])
        if total <= 0:
            notify_managers(
                company_id, title="Tovar tugadi",
                body=f"{row['name']} skladda qolmadi",
                payload={"type": "inventory", "product_id": row["id"], "event": "out"},
                exclude_employee_id=actor_id,
            )
        elif _q(row["min_stock"]) > 0 and total <= _q(row["min_stock"]):
            notify_managers(
                company_id, title="Tovar minimal qoldiqqa yetdi",
                body=f"{row['name']}: {total.normalize()} {row['unit'] or ''} qoldi (min {_q(row['min_stock']).normalize()})",
                payload={"type": "inventory", "product_id": row["id"], "event": "low"},
                exclude_employee_id=actor_id,
            )


# ─── The sales board's side ───────────────────────────────────────────────────

def lead_lines_to_book(lead_id: int) -> list[dict[str, Any]]:
    """The lead's catalogue lines nobody has filed a sale document for."""
    return fetch_all(
        f"""
        SELECT i.* FROM {B2B_WORKSPACE_LEAD_ITEM_TABLE} i
        WHERE i.lead_id = %s AND i.product_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM {B2B_STOCK_DOCUMENT_ITEM_TABLE} di
              JOIN {B2B_STOCK_DOCUMENT_TABLE} d ON d.id = di.document_id
              WHERE di.lead_item_id = i.id AND d.status <> 'cancelled'
          )
        ORDER BY i.position, i.id
        """,
        [lead_id],
    )


def shortages_for_lines(
    company_id: int, lines: Sequence[dict[str, Any]], *, default_warehouse_id: int | None = None
) -> list[dict[str, Any]]:
    """Which lines the shelf cannot cover, product by product — bundles
    unfolded into their parts, services skipped. Needs are summed per
    (product, warehouse) first, so two lines of the same product are
    checked together rather than each passing alone."""
    needs: dict[tuple[int, int], Decimal] = {}
    names: dict[int, dict[str, Any]] = {}
    for line in lines:
        product_id = line.get("product_id")
        if not product_id:
            continue
        warehouse_id = int(line.get("warehouse_id") or default_warehouse_id or 0)
        if not warehouse_id:
            fallback = default_warehouse(company_id)
            warehouse_id = int(fallback["id"]) if fallback else 0
        for part, qty in expand_for_stock(company_id, int(product_id), line.get("qty") or line.get("quantity") or 0):
            key = (int(part["id"]), warehouse_id)
            needs[key] = needs.get(key, Decimal(0)) + qty
            names[int(part["id"])] = part
    shortages = []
    for (product_id, warehouse_id), needed in needs.items():
        available = stock_at(product_id, warehouse_id)
        if available < needed:
            part = names[product_id]
            warehouse = get_warehouse(warehouse_id, company_id) or {}
            shortages.append({
                "product_id": product_id, "name": part.get("name"), "unit": part.get("unit"),
                "warehouse_id": warehouse_id, "warehouse_name": warehouse.get("name"),
                "needed": needed, "available": available, "short": needed - available,
            })
    return shortages


def record_sale_for_lead(lead: dict[str, Any], *, author_id: int | None) -> int:
    """Takes a won lead's catalogue lines off the shelf, as a sale document.

    Called when a lead is completed, moved to ``won``, or recorded as a
    quick sale. Idempotent per line — a line with a live sale document is
    not booked again — so a lead moved to won, reopened, and won again
    sells its stock once. Lines that name no product are somebody's typed
    text and are left alone. Returns how many lines were booked.

    Stock is checked first. Short, and backorders off, the whole thing is
    refused with ``insufficient_stock`` and the list of what is missing —
    the caller turns that into the 409 the sheet prints. Backorders on, the
    short lines are filed as a pending sale document that ships later, and
    the shelf is never taken below zero.
    """
    from apps.b2b.workspace import inventory_documents as documents

    lead_id = lead.get("id")
    company_id = lead.get("company_id")
    if not lead_id or not company_id:
        return 0
    lines = lead_lines_to_book(int(lead_id))
    if not lines:
        return 0
    settings = get_settings(int(company_id))
    fallback = default_warehouse(int(company_id))
    if not fallback:
        return 0
    shortages = shortages_for_lines(int(company_id), lines, default_warehouse_id=fallback["id"])
    short_products = {s["product_id"] for s in shortages}
    if shortages and not settings.get("allow_backorder"):
        raise InventoryError(
            "Skladda yetarli tovar yo'q.", code="insufficient_stock", details=shortages
        )

    ready, pending = [], []
    for line in lines:
        qty = _q(line.get("qty"))
        if qty <= 0:
            continue
        amount = _q(line.get("amount"))
        unit_cost = (amount / qty) if amount > 0 else None
        parts = {p["id"] for p, _ in expand_for_stock(int(company_id), int(line["product_id"]), qty)}
        item = {
            "product_id": int(line["product_id"]), "quantity": qty, "unit_cost": unit_cost,
            "lead_item_id": line["id"], "warehouse_id": line.get("warehouse_id") or fallback["id"],
        }
        (pending if parts & short_products else ready).append(item)

    booked = 0
    for group, status_after in ((ready, "confirmed"), (pending, "pending")):
        if not group:
            continue
        # Lines may name different warehouses; one document per warehouse.
        by_warehouse: dict[int, list[dict[str, Any]]] = {}
        for item in group:
            by_warehouse.setdefault(int(item["warehouse_id"]), []).append(item)
        for warehouse_id, items in by_warehouse.items():
            doc = documents.create_document(
                int(company_id), kind="sale", author_id=author_id, warehouse_id=warehouse_id,
                customer_id=lead.get("customer_id"), lead_id=int(lead_id),
                note=f"Lid #{lead_id}: {lead.get('company_name') or ''}".strip(),
                items=items,
            )
            if status_after == "confirmed":
                documents.confirm_document(doc["id"], int(company_id), actor_id=author_id)
            else:
                documents.mark_pending(doc["id"], int(company_id))
            booked += len(items)
    return booked
