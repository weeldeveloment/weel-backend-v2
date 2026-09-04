"""The paper behind the ledger.

A document is an operation as a person filed it: a receipt from a supplier,
a transfer between two warehouses, a count, a write-off, a repricing, a sale,
a return. It has lines and a status, and the status is the whole story:

    draft      — filed, moves nothing, still editable
    sent       — a transfer that left its source and is on the road
    pending    — a sale waiting for stock (backorders on)
    confirmed  — applied to the ledger; the movements point back here
    cancelled  — undone: a confirmed one by writing the reverse movements

Two rules from the TZ hold everything together. Stock changes only through
a confirmed document — the primitive ``apply_movement`` is called from
[confirm_document] and [cancel_document] and nowhere else in this layer. And
a confirmed document is never deleted or edited: cancelling it writes new
rows that undo it and keeps the old ones, so the ledger still explains
every number it ever showed.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Sequence

from django.db import transaction
from django.utils import timezone

from shared.raw.db import execute, fetch_all, fetch_one
from apps.b2b.raw.tables import (
    B2B_EMPLOYEE_TABLE,
    B2B_PRODUCT_TABLE,
    B2B_STOCK_DOCUMENT_ITEM_TABLE,
    B2B_STOCK_DOCUMENT_TABLE,
    B2B_STOCK_MOVEMENT_TABLE,
    B2B_SUPPLIER_TABLE,
    B2B_WAREHOUSE_TABLE,
)
from apps.b2b.workspace import inventory_repository as inventory
from apps.b2b.workspace.inventory_repository import (
    InventoryError,
    MovementKind,
    ProductKind,
    WriteOffReason,
    _q,
)

logger = logging.getLogger(__name__)


class DocumentKind:
    RECEIPT = "receipt"
    TRANSFER = "transfer"
    INVENTORY = "inventory"
    WRITE_OFF = "write_off"
    REVALUATION = "revaluation"
    SALE = "sale"
    RETURN = "return"

    CHOICES = [RECEIPT, TRANSFER, INVENTORY, WRITE_OFF, REVALUATION, SALE, RETURN]

    #: The letters on the number: KR-000012 is a receipt.
    PREFIX = {
        RECEIPT: "KR", TRANSFER: "TR", INVENTORY: "IN", WRITE_OFF: "HC",
        REVALUATION: "QB", SALE: "SV", RETURN: "QT",
    }
    LABELS = {
        RECEIPT: "Kirim", TRANSFER: "Ko'chirish", INVENTORY: "Inventarizatsiya",
        WRITE_OFF: "Hisobdan chiqarish", REVALUATION: "Qayta baholash",
        SALE: "Savdo", RETURN: "Qaytarish",
    }


class DocumentStatus:
    DRAFT = "draft"
    SENT = "sent"
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"

    CHOICES = [DRAFT, SENT, PENDING, CONFIRMED, CANCELLED]


def _number(kind: str, document_id: int) -> str:
    return f"{DocumentKind.PREFIX.get(kind, 'DC')}-{document_id:06d}"


# ─── Reading ──────────────────────────────────────────────────────────────────

_DOC_SELECT = f"""
    SELECT d.*,
           w.name AS warehouse_name, t.name AS to_warehouse_name,
           s.name AS supplier_name, cu.full_name AS customer_name,
           a.full_name AS author_name, c.full_name AS confirmed_by_name,
           x.full_name AS cancelled_by_name, r.number AS reversal_of_number,
           COALESCE(i.line_count, 0) AS line_count,
           COALESCE(i.total, 0) AS total,
           COALESCE(i.quantity_total, 0) AS quantity_total
    FROM {B2B_STOCK_DOCUMENT_TABLE} d
    LEFT JOIN {B2B_WAREHOUSE_TABLE} w ON w.id = d.warehouse_id
    LEFT JOIN {B2B_WAREHOUSE_TABLE} t ON t.id = d.to_warehouse_id
    LEFT JOIN {B2B_SUPPLIER_TABLE} s ON s.id = d.supplier_id
    LEFT JOIN b2b_workspace_customer cu ON cu.id = d.customer_id
    LEFT JOIN {B2B_EMPLOYEE_TABLE} a ON a.id = d.author_id
    LEFT JOIN {B2B_EMPLOYEE_TABLE} c ON c.id = d.confirmed_by
    LEFT JOIN {B2B_EMPLOYEE_TABLE} x ON x.id = d.cancelled_by
    LEFT JOIN {B2B_STOCK_DOCUMENT_TABLE} r ON r.id = d.reversal_of
    LEFT JOIN (
        SELECT document_id, COUNT(*) AS line_count,
               SUM(quantity * unit_cost) AS total,
               SUM(quantity) AS quantity_total
        FROM {B2B_STOCK_DOCUMENT_ITEM_TABLE} GROUP BY document_id
    ) i ON i.document_id = d.id
"""


def _shape(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return row
    for key in ("total", "quantity_total", "extra_costs"):
        row[key] = _q(row.get(key))
    row["kind_label"] = DocumentKind.LABELS.get(row.get("kind"), row.get("kind"))
    return row


def list_documents(
    company_id: int,
    *,
    kind: str | None = None,
    status: str | None = None,
    warehouse_id: int | None = None,
    supplier_id: int | None = None,
    customer_id: int | None = None,
    lead_id: int | None = None,
    q: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    where = ["d.company_id = %s"]
    params: list[Any] = [company_id]
    if kind:
        where.append("d.kind = %s")
        params.append(kind)
    if status:
        where.append("d.status = %s")
        params.append(status)
    if warehouse_id:
        where.append("(d.warehouse_id = %s OR d.to_warehouse_id = %s)")
        params.extend([warehouse_id, warehouse_id])
    if supplier_id:
        where.append("d.supplier_id = %s")
        params.append(supplier_id)
    if customer_id:
        where.append("d.customer_id = %s")
        params.append(customer_id)
    if lead_id:
        where.append("d.lead_id = %s")
        params.append(lead_id)
    if q:
        where.append("(d.number ILIKE %s OR d.external_number ILIKE %s OR d.note ILIKE %s)")
        like = f"%{q.strip()}%"
        params.extend([like, like, like])
    if date_from:
        where.append("d.created_at >= %s")
        params.append(date_from)
    if date_to:
        where.append("d.created_at < %s")
        params.append(date_to)
    rows = fetch_all(
        f"{_DOC_SELECT} WHERE {' AND '.join(where)} ORDER BY d.created_at DESC, d.id DESC LIMIT %s",
        [*params, limit],
    )
    return [_shape(row) for row in rows]


def get_document(document_id: int, company_id: int) -> dict[str, Any] | None:
    row = _shape(fetch_one(
        f"{_DOC_SELECT} WHERE d.id = %s AND d.company_id = %s", [document_id, company_id]
    ))
    if row:
        row["items"] = list_items(document_id)
    return row


def get_document_raw(document_id: int, company_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT * FROM {B2B_STOCK_DOCUMENT_TABLE} WHERE id = %s AND company_id = %s",
        [document_id, company_id],
    )


def find_by_idempotency_key(company_id: int, key: str) -> dict[str, Any] | None:
    if not key:
        return None
    row = fetch_one(
        f"SELECT id FROM {B2B_STOCK_DOCUMENT_TABLE} WHERE company_id = %s AND idempotency_key = %s",
        [company_id, key],
    )
    return get_document(row["id"], company_id) if row else None


def list_items(document_id: int) -> list[dict[str, Any]]:
    rows = fetch_all(
        f"""
        SELECT i.*, p.name AS product_name, p.sku, p.unit, p.kind AS product_kind,
               p.sale_price AS current_sale_price, p.wholesale_price AS current_wholesale_price,
               p.purchase_price AS current_purchase_price
        FROM {B2B_STOCK_DOCUMENT_ITEM_TABLE} i
        JOIN {B2B_PRODUCT_TABLE} p ON p.id = i.product_id
        WHERE i.document_id = %s
        ORDER BY i.position, i.id
        """,
        [document_id],
    )
    for row in rows:
        row["quantity"] = _q(row.get("quantity"))
        row["unit_cost"] = _q(row.get("unit_cost"))
        if row.get("counted_quantity") is not None and row.get("system_quantity") is not None:
            row["difference"] = _q(row["counted_quantity"]) - _q(row["system_quantity"])
    return rows


# ─── Writing ──────────────────────────────────────────────────────────────────

def _clean_items(company_id: int, kind: str, items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned = []
    for raw in items or ():
        product_id = raw.get("product_id")
        if not product_id:
            continue
        product = inventory.get_product_raw(int(product_id), company_id)
        if not product:
            raise InventoryError("Tovar topilmadi.", code="product_not_found")
        product_kind = product.get("kind") or ProductKind.PRODUCT
        if kind in (DocumentKind.RECEIPT, DocumentKind.TRANSFER, DocumentKind.INVENTORY,
                    DocumentKind.WRITE_OFF) and product_kind != ProductKind.PRODUCT:
            raise InventoryError(
                f"{product.get('name')}: xizmat va komplekt qoldiqqa ega emas.", code="no_stock_kind"
            )
        qty = _q(raw.get("quantity") if raw.get("quantity") is not None else raw.get("qty"))
        unit_cost = raw.get("unit_cost")
        if unit_cost is None:
            unit_cost = (
                product.get("sale_price") if kind in (DocumentKind.SALE, DocumentKind.RETURN)
                else product.get("purchase_price")
            )
        item = {
            "product_id": int(product_id),
            "quantity": qty,
            "unit_cost": _q(unit_cost),
            "system_quantity": None,
            "counted_quantity": None,
            "old_price": None,
            "new_price": None,
            "old_wholesale": None,
            "new_wholesale": None,
            "lead_item_id": raw.get("lead_item_id"),
        }
        if kind == DocumentKind.INVENTORY:
            counted = raw.get("counted_quantity")
            if counted is None:
                counted = raw.get("quantity")
            if counted is None:
                raise InventoryError("Sanalgan miqdorni kiriting.", code="bad_quantity")
            item["counted_quantity"] = _q(counted)
            item["quantity"] = _q(counted)
        elif kind == DocumentKind.REVALUATION:
            item["old_price"] = _q(product.get("sale_price"))
            item["old_wholesale"] = _q(product.get("wholesale_price"))
            item["new_price"] = _q(raw["new_price"]) if raw.get("new_price") is not None else None
            item["new_wholesale"] = (
                _q(raw["new_wholesale"]) if raw.get("new_wholesale") is not None else None
            )
            if item["new_price"] is None and item["new_wholesale"] is None:
                raise InventoryError("Yangi narxni kiriting.", code="bad_price")
            item["quantity"] = Decimal(0)
        elif qty <= 0:
            raise InventoryError(
                f"{product.get('name')}: miqdor noldan katta bo'lishi kerak.", code="bad_quantity"
            )
        cleaned.append(item)
    if not cleaned:
        raise InventoryError("Hujjatda kamida bitta qator bo'lishi kerak.", code="no_lines")
    return cleaned


def create_document(
    company_id: int,
    *,
    kind: str,
    author_id: int | None,
    items: Sequence[dict[str, Any]],
    warehouse_id: int | None = None,
    to_warehouse_id: int | None = None,
    supplier_id: int | None = None,
    customer_id: int | None = None,
    lead_id: int | None = None,
    currency: str | None = None,
    extra_costs=0,
    reason: str | None = None,
    note: str | None = None,
    doc_date: date | None = None,
    external_number: str | None = None,
    file_id: int | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Files a draft. Nothing moves until [confirm_document]."""
    if kind not in DocumentKind.CHOICES:
        raise InventoryError("Noma'lum hujjat turi.", code="bad_kind")
    if idempotency_key:
        existing = find_by_idempotency_key(company_id, idempotency_key)
        if existing:
            return existing
    if kind != DocumentKind.REVALUATION:
        if not warehouse_id:
            fallback = inventory.default_warehouse(company_id)
            warehouse_id = fallback["id"] if fallback else None
        warehouse = inventory.get_warehouse(warehouse_id, company_id) if warehouse_id else None
        if not warehouse or not warehouse.get("is_active"):
            raise InventoryError("Sklad topilmadi.", code="warehouse_not_found")
    if kind == DocumentKind.TRANSFER:
        if not to_warehouse_id or to_warehouse_id == warehouse_id:
            raise InventoryError("Qabul qiluvchi skladni tanlang.", code="bad_target")
        target = inventory.get_warehouse(to_warehouse_id, company_id)
        if not target or not target.get("is_active"):
            raise InventoryError("Qabul qiluvchi sklad topilmadi.", code="warehouse_not_found")
    else:
        to_warehouse_id = None
    if kind == DocumentKind.WRITE_OFF:
        reason = reason if reason in WriteOffReason.CHOICES else WriteOffReason.OTHER
    elif kind != DocumentKind.RETURN:
        reason = None
    if supplier_id and not inventory.get_supplier(supplier_id, company_id):
        raise InventoryError("Yetkazib beruvchi topilmadi.", code="supplier_not_found")
    lines = _clean_items(company_id, kind, items)
    currency = (currency or inventory.get_settings(company_id).get("base_currency") or "UZS")[:3].upper()
    now = timezone.now()
    with transaction.atomic():
        row = fetch_one(
            f"""
            INSERT INTO {B2B_STOCK_DOCUMENT_TABLE}
                (company_id, kind, status, warehouse_id, to_warehouse_id, supplier_id, customer_id,
                 lead_id, currency, extra_costs, reason, note, doc_date, external_number, file_id,
                 idempotency_key, author_id, created_at, updated_at)
            VALUES (%s, %s, 'draft', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (company_id, idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
            RETURNING *
            """,
            [
                company_id, kind, warehouse_id, to_warehouse_id, supplier_id, customer_id,
                lead_id, currency, _q(extra_costs), reason, (note or "").strip() or None,
                doc_date or now.date(), (external_number or "").strip() or None, file_id,
                idempotency_key or None, author_id, now, now,
            ],
        )
        if row is None:
            # The same submission landed twice; the first one won.
            return find_by_idempotency_key(company_id, idempotency_key or "") or {}
        execute(
            f"UPDATE {B2B_STOCK_DOCUMENT_TABLE} SET number = %s WHERE id = %s",
            [_number(kind, row["id"]), row["id"]],
        )
        _write_items(row["id"], lines)
    return get_document(row["id"], company_id)


def _write_items(document_id: int, lines: Sequence[dict[str, Any]]) -> None:
    execute(f"DELETE FROM {B2B_STOCK_DOCUMENT_ITEM_TABLE} WHERE document_id = %s", [document_id])
    for position, item in enumerate(lines):
        execute(
            f"""
            INSERT INTO {B2B_STOCK_DOCUMENT_ITEM_TABLE}
                (document_id, product_id, quantity, system_quantity, counted_quantity, unit_cost,
                 old_price, new_price, old_wholesale, new_wholesale, position, lead_item_id, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                document_id, item["product_id"], item["quantity"], item.get("system_quantity"),
                item.get("counted_quantity"), item["unit_cost"], item.get("old_price"),
                item.get("new_price"), item.get("old_wholesale"), item.get("new_wholesale"),
                position, item.get("lead_item_id"), timezone.now(),
            ],
        )


def update_document(
    document_id: int, company_id: int, *, items: Sequence[dict[str, Any]] | None = None, **fields: Any
) -> dict[str, Any] | None:
    """Edits a draft. Anything past draft is history and is not touched."""
    current = get_document_raw(document_id, company_id)
    if not current:
        return None
    if current["status"] != DocumentStatus.DRAFT:
        raise InventoryError("Faqat qoralama hujjatni tahrirlash mumkin.", code="not_draft")
    allowed = {
        "warehouse_id", "to_warehouse_id", "supplier_id", "customer_id", "currency",
        "extra_costs", "reason", "note", "doc_date", "external_number", "file_id",
    }
    changes = {k: v for k, v in fields.items() if k in allowed}
    if "extra_costs" in changes:
        changes["extra_costs"] = _q(changes["extra_costs"])
    with transaction.atomic():
        if changes:
            assignments = ", ".join(f"{column} = %s" for column in changes)
            execute(
                f"UPDATE {B2B_STOCK_DOCUMENT_TABLE} SET {assignments}, updated_at = %s WHERE id = %s",
                [*changes.values(), timezone.now(), document_id],
            )
        if items is not None:
            _write_items(document_id, _clean_items(company_id, current["kind"], items))
    return get_document(document_id, company_id)


def delete_draft(document_id: int, company_id: int) -> bool:
    """A draft that was never confirmed moved nothing and may go."""
    return bool(execute(
        f"DELETE FROM {B2B_STOCK_DOCUMENT_TABLE} WHERE id = %s AND company_id = %s AND status = 'draft'",
        [document_id, company_id],
    ))


def mark_pending(document_id: int, company_id: int) -> None:
    execute(
        f"UPDATE {B2B_STOCK_DOCUMENT_TABLE} SET status = 'pending', updated_at = %s "
        "WHERE id = %s AND company_id = %s AND status = 'draft'",
        [timezone.now(), document_id, company_id],
    )


def _set_status(document_id: int, status: str, **stamps: Any) -> None:
    assignments = ", ".join(f"{column} = %s" for column in stamps)
    execute(
        f"UPDATE {B2B_STOCK_DOCUMENT_TABLE} SET status = %s, updated_at = %s"
        f"{', ' + assignments if assignments else ''} WHERE id = %s",
        [status, timezone.now(), *stamps.values(), document_id],
    )


def preview_confirm(document_id: int, company_id: int) -> list[dict[str, Any]]:
    """What confirming would do to the shelf, line by line: the balance now
    and the balance after. The confirmation sheet prints this so a person
    sees the count they are about to commit to."""
    doc = get_document(document_id, company_id)
    if not doc:
        return []
    rows = []
    for item in doc["items"]:
        for part, qty in inventory.expand_for_stock(company_id, int(item["product_id"]), item["quantity"]):
            for warehouse_id, sign in _legs(doc, qty):
                before = inventory.stock_at(int(part["id"]), warehouse_id)
                if doc["kind"] == DocumentKind.INVENTORY:
                    after = _q(item.get("counted_quantity"))
                else:
                    after = before + sign
                warehouse = inventory.get_warehouse(warehouse_id, company_id) or {}
                rows.append({
                    "product_id": part["id"], "name": part.get("name"), "unit": part.get("unit"),
                    "warehouse_id": warehouse_id, "warehouse_name": warehouse.get("name"),
                    "before": before, "after": after, "change": after - before,
                })
    return rows


def _legs(doc: dict[str, Any], qty: Decimal) -> list[tuple[int, Decimal]]:
    """Which warehouses a document's line touches, and by how much."""
    kind = doc["kind"]
    if kind in (DocumentKind.RECEIPT, DocumentKind.RETURN):
        return [(int(doc["warehouse_id"]), qty)]
    if kind in (DocumentKind.SALE, DocumentKind.WRITE_OFF):
        return [(int(doc["warehouse_id"]), -qty)]
    if kind == DocumentKind.TRANSFER:
        return [(int(doc["warehouse_id"]), -qty), (int(doc["to_warehouse_id"]), qty)]
    if kind == DocumentKind.INVENTORY:
        return [(int(doc["warehouse_id"]), Decimal(0))]
    return []


def confirm_document(document_id: int, company_id: int, *, actor_id: int | None) -> dict[str, Any]:
    """Applies a document to the ledger.

    One transaction: every line moves or none does, and a shortage on the
    third line leaves the first two untouched. A transfer confirmed straight
    from draft is sent and received in one step — the "on the road" state is
    for the case where two different people do the two halves.
    """
    doc = get_document(document_id, company_id)
    if not doc:
        raise InventoryError("Hujjat topilmadi.", code="document_not_found")
    if doc["status"] in (DocumentStatus.CONFIRMED, DocumentStatus.CANCELLED):
        raise InventoryError("Hujjat allaqachon yakunlangan.", code="already_done")
    # A transfer already on the road has moved its stock; what is left is
    # the arrival, which is its own step.
    if doc["status"] == DocumentStatus.SENT:
        return receive_transfer(document_id, company_id, actor_id=actor_id)
    kind = doc["kind"]
    now = timezone.now()
    touched: list[int] = []
    with transaction.atomic():
        # Re-locked inside the transaction: the status seen a moment ago may
        # already be stale if two people pressed confirm together.
        locked = fetch_one(
            f"SELECT status FROM {B2B_STOCK_DOCUMENT_TABLE} WHERE id = %s FOR UPDATE", [document_id]
        )
        if not locked or locked["status"] in (DocumentStatus.CONFIRMED, DocumentStatus.CANCELLED):
            raise InventoryError("Hujjat allaqachon yakunlangan.", code="already_done")

        if kind == DocumentKind.REVALUATION:
            for item in doc["items"]:
                product = inventory.get_product_raw(int(item["product_id"]), company_id)
                if not product:
                    continue
                changes: dict[str, Any] = {}
                if item.get("new_price") is not None:
                    changes["sale_price"] = _q(item["new_price"])
                if item.get("new_wholesale") is not None:
                    changes["wholesale_price"] = _q(item["new_wholesale"])
                for field, value in changes.items():
                    if _q(product.get(field)) != value:
                        inventory.record_price_change(
                            company_id, int(item["product_id"]), field=field,
                            old_price=product.get(field), new_price=value,
                            author_id=actor_id, document_id=document_id,
                        )
                if changes:
                    assignments = ", ".join(f"{c} = %s" for c in changes)
                    execute(
                        f"UPDATE {B2B_PRODUCT_TABLE} SET {assignments}, updated_at = %s WHERE id = %s",
                        [*changes.values(), now, item["product_id"]],
                    )
                # The old price is frozen on the line the moment it applies.
                execute(
                    f"UPDATE {B2B_STOCK_DOCUMENT_ITEM_TABLE} SET old_price = %s, old_wholesale = %s WHERE id = %s",
                    [_q(product.get("sale_price")), _q(product.get("wholesale_price")), item["id"]],
                )
        else:
            shortages: list[dict[str, Any]] = []
            if kind in (DocumentKind.SALE, DocumentKind.WRITE_OFF, DocumentKind.TRANSFER):
                shortages = inventory.shortages_for_lines(
                    company_id,
                    [{"product_id": i["product_id"], "qty": i["quantity"], "warehouse_id": doc["warehouse_id"]}
                     for i in doc["items"]],
                    default_warehouse_id=doc["warehouse_id"],
                )
                if shortages:
                    raise InventoryError(
                        "Skladda yetarli tovar yo'q.", code="insufficient_stock", details=shortages
                    )
            for item in doc["items"]:
                parts = inventory.expand_for_stock(company_id, int(item["product_id"]), item["quantity"])
                # A bundle's price is spread over its parts by their purchase
                # price, so the ledger's revenue per part adds up to the line.
                total_cost = sum((_q(p.get("purchase_price")) * q for p, q in parts), Decimal(0))
                for part, qty in parts:
                    if kind == DocumentKind.INVENTORY:
                        before = inventory.stock_at(int(part["id"]), int(doc["warehouse_id"]))
                        execute(
                            f"UPDATE {B2B_STOCK_DOCUMENT_ITEM_TABLE} SET system_quantity = %s WHERE id = %s",
                            [before, item["id"]],
                        )
                        if before == _q(item.get("counted_quantity")):
                            continue
                        movement_kind, moved = MovementKind.ADJUSTMENT, _q(item.get("counted_quantity"))
                    else:
                        movement_kind = {
                            DocumentKind.RECEIPT: MovementKind.RECEIPT,
                            DocumentKind.SALE: MovementKind.SALE,
                            DocumentKind.WRITE_OFF: MovementKind.WRITE_OFF,
                            DocumentKind.TRANSFER: MovementKind.TRANSFER,
                            DocumentKind.RETURN: MovementKind.RETURN,
                        }[kind]
                        moved = qty
                    line_total = item["unit_cost"] * item["quantity"]
                    if len(parts) == 1:
                        unit_cost = item["unit_cost"]
                    elif total_cost > 0:
                        share = _q(part.get("purchase_price")) * qty / total_cost
                        unit_cost = (line_total * share / qty) if qty else item["unit_cost"]
                    else:
                        unit_cost = (line_total / len(parts) / qty) if qty else item["unit_cost"]
                    inventory.apply_movement(
                        company_id, kind=movement_kind, product_id=int(part["id"]),
                        warehouse_id=int(doc["warehouse_id"]), to_warehouse_id=doc.get("to_warehouse_id"),
                        quantity=moved, unit_cost=unit_cost, note=doc.get("note"),
                        author_id=actor_id, lead_id=doc.get("lead_id"),
                        lead_item_id=item.get("lead_item_id"), document_id=document_id,
                        customer_id=doc.get("customer_id"), currency=doc.get("currency"),
                    )
                    touched.append(int(part["id"]))
            # The receipt sets the product's purchase price to what was just
            # paid: the next sale's margin should be measured against it.
            if kind == DocumentKind.RECEIPT:
                for item in doc["items"]:
                    product = inventory.get_product_raw(int(item["product_id"]), company_id)
                    if product and _q(product.get("purchase_price")) != item["unit_cost"] and item["unit_cost"] > 0:
                        inventory.record_price_change(
                            company_id, int(item["product_id"]), field="purchase_price",
                            old_price=product.get("purchase_price"), new_price=item["unit_cost"],
                            author_id=actor_id, document_id=document_id,
                        )
                        execute(
                            f"UPDATE {B2B_PRODUCT_TABLE} SET purchase_price = %s, updated_at = %s WHERE id = %s",
                            [item["unit_cost"], now, item["product_id"]],
                        )
        stamps = {"confirmed_by": actor_id, "confirmed_at": now}
        if kind == DocumentKind.TRANSFER:
            stamps.update({"received_by": actor_id, "received_at": now})
            if not doc.get("sent_at"):
                stamps["sent_at"] = now
        _set_status(document_id, DocumentStatus.CONFIRMED, **stamps)
    _after_confirm(doc, touched, actor_id=actor_id)
    return get_document(document_id, company_id) or doc


def _after_confirm(doc: dict[str, Any], touched: Sequence[int], *, actor_id: int | None) -> None:
    """The notifications the TZ lists, once the ledger is written."""
    try:
        company_id = int(doc["company_id"])
        kind = doc["kind"]
        if kind in (DocumentKind.SALE, DocumentKind.WRITE_OFF, DocumentKind.TRANSFER, DocumentKind.INVENTORY):
            inventory.alert_low_stock(company_id, touched, actor_id=actor_id)
        if kind == DocumentKind.TRANSFER:
            inventory.notify_managers(
                company_id, title="Transfer qabul qilindi",
                body=f"{doc.get('number')}: {doc.get('warehouse_name')} → {doc.get('to_warehouse_name')}",
                payload={"type": "inventory", "document_id": doc["id"], "event": "transfer_received"},
                exclude_employee_id=actor_id,
            )
        if kind == DocumentKind.INVENTORY:
            fresh = get_document(int(doc["id"]), company_id) or doc
            diffs = [i for i in fresh.get("items", []) if _q(i.get("difference")) != 0]
            if diffs:
                inventory.notify_managers(
                    company_id, title="Inventarizatsiya tafovut bilan yakunlandi",
                    body=f"{doc.get('number')}: {len(diffs)} ta tovarda farq bor",
                    payload={"type": "inventory", "document_id": doc["id"], "event": "inventory_diff"},
                    exclude_employee_id=actor_id,
                )
        if kind == DocumentKind.WRITE_OFF:
            settings = inventory.get_settings(company_id)
            value = sum((_q(i["quantity"]) * _q(i.get("current_purchase_price")) for i in doc.get("items", [])), Decimal(0))
            if value >= _q(settings.get("write_off_alert")):
                inventory.notify_managers(
                    company_id, title="Katta hisobdan chiqarish",
                    body=f"{doc.get('number')}: {value.quantize(Decimal('1'))} {doc.get('currency') or ''}",
                    payload={"type": "inventory", "document_id": doc["id"], "event": "big_write_off"},
                    exclude_employee_id=actor_id,
                )
    except Exception:  # noqa: BLE001 - the document is confirmed either way
        logger.exception("Could not send the notifications for document %s", doc.get("id"))


def send_transfer(document_id: int, company_id: int, *, actor_id: int | None) -> dict[str, Any]:
    """The first half of a transfer: stock leaves the source now and sits
    "on the road" until somebody at the other end receives it. Written as
    a write-off-shaped movement out of the source only; [confirm_document]
    on a sent transfer books the arrival."""
    doc = get_document(document_id, company_id)
    if not doc or doc["kind"] != DocumentKind.TRANSFER:
        raise InventoryError("Hujjat topilmadi.", code="document_not_found")
    if doc["status"] != DocumentStatus.DRAFT:
        raise InventoryError("Faqat qoralama transfer jo'natiladi.", code="not_draft")
    shortages = inventory.shortages_for_lines(
        company_id,
        [{"product_id": i["product_id"], "qty": i["quantity"], "warehouse_id": doc["warehouse_id"]}
         for i in doc["items"]],
        default_warehouse_id=doc["warehouse_id"],
    )
    if shortages:
        raise InventoryError("Skladda yetarli tovar yo'q.", code="insufficient_stock", details=shortages)
    now = timezone.now()
    touched = []
    with transaction.atomic():
        for item in doc["items"]:
            inventory.apply_movement(
                company_id, kind=MovementKind.TRANSFER, product_id=int(item["product_id"]),
                warehouse_id=int(doc["warehouse_id"]), to_warehouse_id=int(doc["to_warehouse_id"]),
                quantity=item["quantity"], unit_cost=item["unit_cost"], note=doc.get("note"),
                author_id=actor_id, document_id=document_id, currency=doc.get("currency"),
            )
            touched.append(int(item["product_id"]))
        _set_status(document_id, DocumentStatus.SENT, sent_at=now)
    inventory.alert_low_stock(company_id, touched, actor_id=actor_id)
    inventory.notify_managers(
        company_id, title="Transfer jo'natildi",
        body=f"{doc.get('number')}: {doc.get('warehouse_name')} → {doc.get('to_warehouse_name')}",
        payload={"type": "inventory", "document_id": document_id, "event": "transfer_sent"},
        exclude_employee_id=actor_id,
    )
    return get_document(document_id, company_id) or doc


def receive_transfer(document_id: int, company_id: int, *, actor_id: int | None) -> dict[str, Any]:
    """The second half: the goods arrived. The movement was written at send
    time (a transfer is one row with both warehouses on it), so receiving is
    the status and the stamp — the destination's balance already rose when
    it was sent, which is what a transfer of one row means."""
    doc = get_document_raw(document_id, company_id)
    if not doc or doc["kind"] != DocumentKind.TRANSFER:
        raise InventoryError("Hujjat topilmadi.", code="document_not_found")
    if doc["status"] != DocumentStatus.SENT:
        raise InventoryError("Bu transfer jo'natilmagan.", code="not_sent")
    now = timezone.now()
    _set_status(document_id, DocumentStatus.CONFIRMED, received_by=actor_id, received_at=now,
                confirmed_by=actor_id, confirmed_at=now)
    full = get_document(document_id, company_id)
    inventory.notify_managers(
        company_id, title="Transfer qabul qilindi",
        body=f"{full.get('number')}: {full.get('warehouse_name')} → {full.get('to_warehouse_name')}",
        payload={"type": "inventory", "document_id": document_id, "event": "transfer_received"},
        exclude_employee_id=actor_id,
    )
    return full


def cancel_document(
    document_id: int, company_id: int, *, actor_id: int | None, reason: str
) -> dict[str, Any]:
    """Undoes a document — the TZ's storno.

    A draft or a pending sale simply becomes cancelled. A confirmed (or sent)
    one keeps every movement it wrote and gains their mirror image, each new
    row pointing at the one it reverses; a repricing puts the old prices
    back and records that too. The reason is mandatory: a reversal nobody
    explained is a number nobody can audit.
    """
    reason = (reason or "").strip()
    if not reason:
        raise InventoryError("Bekor qilish sababini kiriting.", code="reason_required")
    doc = get_document(document_id, company_id)
    if not doc:
        raise InventoryError("Hujjat topilmadi.", code="document_not_found")
    if doc["status"] == DocumentStatus.CANCELLED:
        raise InventoryError("Hujjat allaqachon bekor qilingan.", code="already_done")
    now = timezone.now()
    touched: list[int] = []
    with transaction.atomic():
        locked = fetch_one(
            f"SELECT status FROM {B2B_STOCK_DOCUMENT_TABLE} WHERE id = %s FOR UPDATE", [document_id]
        )
        if not locked or locked["status"] == DocumentStatus.CANCELLED:
            raise InventoryError("Hujjat allaqachon bekor qilingan.", code="already_done")
        if locked["status"] in (DocumentStatus.CONFIRMED, DocumentStatus.SENT):
            if doc["kind"] == DocumentKind.REVALUATION:
                for item in doc["items"]:
                    product = inventory.get_product_raw(int(item["product_id"]), company_id)
                    if not product:
                        continue
                    restore: dict[str, Any] = {}
                    if item.get("new_price") is not None and item.get("old_price") is not None:
                        restore["sale_price"] = _q(item["old_price"])
                    if item.get("new_wholesale") is not None and item.get("old_wholesale") is not None:
                        restore["wholesale_price"] = _q(item["old_wholesale"])
                    for field, value in restore.items():
                        inventory.record_price_change(
                            company_id, int(item["product_id"]), field=field,
                            old_price=product.get(field), new_price=value,
                            author_id=actor_id, document_id=document_id,
                        )
                    if restore:
                        assignments = ", ".join(f"{c} = %s" for c in restore)
                        execute(
                            f"UPDATE {B2B_PRODUCT_TABLE} SET {assignments}, updated_at = %s WHERE id = %s",
                            [*restore.values(), now, item["product_id"]],
                        )
            else:
                movements = fetch_all(
                    f"SELECT * FROM {B2B_STOCK_MOVEMENT_TABLE} WHERE document_id = %s AND reversal_of IS NULL "
                    "ORDER BY id",
                    [document_id],
                )
                for movement in movements:
                    _reverse_movement(company_id, movement, actor_id=actor_id, reason=reason)
                    touched.append(int(movement["product_id"]))
        _set_status(
            document_id, DocumentStatus.CANCELLED,
            cancelled_by=actor_id, cancelled_at=now, cancel_reason=reason,
        )
    inventory.alert_low_stock(company_id, touched, actor_id=actor_id)
    return get_document(document_id, company_id) or doc


def _reverse_movement(company_id: int, movement: dict[str, Any], *, actor_id: int | None, reason: str) -> None:
    """The mirror of one ledger row. Allowed to take a warehouse below zero:
    cancelling a receipt whose goods were already sold is still the truth
    about that receipt, and a shelf that goes red is how the shortfall is
    found rather than hidden."""
    kind = movement["kind"]
    qty = _q(movement["quantity"])
    note = f"Storno {movement.get('document_id') or ''}: {reason}".strip()
    common = dict(
        company_id=company_id, product_id=int(movement["product_id"]),
        unit_cost=movement.get("unit_cost"), note=note, author_id=actor_id,
        document_id=movement.get("document_id"), currency=movement.get("currency"),
        reversal_of=movement["id"], allow_negative=True,
    )
    if kind == MovementKind.RECEIPT:
        inventory.apply_movement(kind=MovementKind.WRITE_OFF, warehouse_id=int(movement["warehouse_id"]), quantity=qty, **common)
    elif kind == MovementKind.RETURN:
        inventory.apply_movement(kind=MovementKind.SALE, warehouse_id=int(movement["warehouse_id"]), quantity=qty, **common)
    elif kind == MovementKind.SALE:
        inventory.apply_movement(kind=MovementKind.RETURN, warehouse_id=int(movement["warehouse_id"]), quantity=qty, **common)
    elif kind == MovementKind.WRITE_OFF:
        inventory.apply_movement(kind=MovementKind.RECEIPT, warehouse_id=int(movement["warehouse_id"]), quantity=qty, **common)
    elif kind == MovementKind.TRANSFER:
        inventory.apply_movement(
            kind=MovementKind.TRANSFER, warehouse_id=int(movement["to_warehouse_id"]),
            to_warehouse_id=int(movement["warehouse_id"]), quantity=qty, **common,
        )
    elif kind == MovementKind.ADJUSTMENT:
        # The row holds the signed difference; the count that undoes it is
        # the current balance minus that difference.
        current = inventory.stock_at(int(movement["product_id"]), int(movement["warehouse_id"]))
        inventory.apply_movement(
            kind=MovementKind.ADJUSTMENT, warehouse_id=int(movement["warehouse_id"]),
            quantity=max(current - qty, Decimal(0)), **common,
        )


def list_pending_sales(company_id: int) -> list[dict[str, Any]]:
    """Sales waiting for stock — the backorders a receipt should ship."""
    return list_documents(company_id, kind=DocumentKind.SALE, status=DocumentStatus.PENDING, limit=500)
