"""A sale and the return that undoes it, against a live PostgreSQL database.

The unit tests of the return endpoint mock the warehouse away, and that is how
returns shipped broken: `b2b_stock_document_item.lead_item_id` (and the same
column on `b2b_stock_movement`) carried a UNIQUE index, so the return line —
which names the very lead line the sale line named, that being how "what is
left to return" is counted — was refused by the database, and the customer
saw a 500. Only the real schema can catch that, so this runs against one:

    WEEL_INTEGRATION_DB=1 \\
    DJANGO_SETTINGS_MODULE=core.settings \\
    DB_NAME=weel_test DB_HOST=127.0.0.1 \\
    pytest apps/b2b/tests/test_lead_return_db.py

Point DB_NAME at a throwaway database — never at the one serving traffic.
"""
from __future__ import annotations

import os
from decimal import Decimal

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("WEEL_INTEGRATION_DB") != "1",
        reason=(
            "Needs a live PostgreSQL database with the raw-SQL schema. "
            "Set WEEL_INTEGRATION_DB=1 and point DB_NAME at a throwaway database."
        ),
    ),
    pytest.mark.django_db(transaction=True),
]


@pytest.fixture
def shop(django_db_setup, django_db_blocker):
    """A company with one seller, one warehouse and five units of one
    product on the shelf."""
    from shared.raw.db import execute, fetch_one
    from apps.b2b.workspace import inventory_repository as inventory

    with django_db_blocker.unblock():
        execute(
            "INSERT INTO b2b_company (name, is_active, created_at, updated_at) "
            "VALUES ('Return Test Co', TRUE, NOW(), NOW())"
        )
        company = fetch_one(
            "SELECT id FROM b2b_company WHERE name = 'Return Test Co' ORDER BY id DESC LIMIT 1"
        )
        execute(
            "INSERT INTO b2b_employee (company_id, full_name, role, is_active, created_at, updated_at) "
            "VALUES (%s, 'Sotuvchi Sardor', 'employee', TRUE, NOW(), NOW())",
            [company["id"]],
        )
        seller = fetch_one(
            "SELECT id FROM b2b_employee WHERE company_id = %s ORDER BY id DESC LIMIT 1",
            [company["id"]],
        )
        warehouse = inventory.default_warehouse(company["id"], create=True)
        product = inventory.create_product(
            company["id"],
            name="Interaktiv panel",
            author_id=seller["id"],
            unit="dona",
            purchase_price=Decimal("1000000"),
            sale_price=Decimal("1500000"),
            initial_quantity=Decimal("5"),
            initial_warehouse_id=warehouse["id"],
        )
        yield {
            "company_id": company["id"],
            "seller_id": seller["id"],
            "warehouse_id": warehouse["id"],
            "product_id": product["id"],
        }


def _on_shelf(product_id: int, warehouse_id: int) -> Decimal:
    from shared.raw.db import fetch_one

    row = fetch_one(
        "SELECT quantity FROM b2b_stock WHERE product_id = %s AND warehouse_id = %s",
        [product_id, warehouse_id],
    )
    return Decimal(str(row["quantity"])) if row else Decimal(0)


def _quick_sale(shop, qty: str):
    from apps.b2b.models import LeadKind
    from apps.b2b.workspace import inventory_repository as inventory
    from apps.b2b.workspace import repository as repo

    lead = repo.create_lead(
        company_id=shop["company_id"],
        author_id=shop["seller_id"],
        company_name="Polvon",
        contact_full_name="Polvon aka",
        contact_phone="+998901112233",
        product_name="Interaktiv panel",
        quantity=qty,
        kind=LeadKind.QUICK_SALE,
        items=[{
            "name": "Interaktiv panel", "unit": "dona",
            "amount": Decimal("1500000") * Decimal(qty),
            "product_id": shop["product_id"], "qty": Decimal(qty),
            "warehouse_id": shop["warehouse_id"],
        }],
    )
    # The view books the sale after filing the deal; here it is done by hand
    # so the test stays on the two repository calls it is about.
    if not lead.get("items"):
        lead = repo.get_lead(lead["id"], shop["company_id"])
    inventory.record_sale_for_lead(lead, author_id=shop["seller_id"])
    return repo.get_lead(lead["id"], shop["company_id"])


def test_a_sold_line_comes_back_one_unit_at_a_time(shop):
    from apps.b2b.workspace import inventory_repository as inventory

    lead = _quick_sale(shop, "3")
    assert _on_shelf(shop["product_id"], shop["warehouse_id"]) == Decimal("2")
    (line,) = inventory.lead_return_lines(lead["id"])
    assert Decimal(str(line["returned"])) == 0

    # The first return is the one the unique index refused: the return
    # document's line names the lead line the sale's line already named.
    filed = inventory.record_return_for_lead(
        lead, lines=[{"lead_item_id": line["id"], "qty": "1"}],
        author_id=shop["seller_id"], note="Rangi noto'g'ri",
    )
    assert len(filed) == 1 and filed[0]["kind"] == "return" and filed[0]["status"] == "confirmed"
    assert _on_shelf(shop["product_id"], shop["warehouse_id"]) == Decimal("3")

    # A second one on the same line — two return lines now share the
    # lead_item_id, on top of the sale's.
    inventory.record_return_for_lead(
        lead, lines=[{"lead_item_id": line["id"], "qty": "1"}],
        author_id=shop["seller_id"], note="Ikkinchisi ham",
    )
    (line,) = inventory.lead_return_lines(lead["id"])
    assert Decimal(str(line["returned"])) == Decimal("2")
    assert _on_shelf(shop["product_id"], shop["warehouse_id"]) == Decimal("4")

    # And the sale is still booked once: nothing left to book on this deal.
    assert inventory.lead_lines_to_book(lead["id"]) == []


def test_more_than_is_left_is_refused_and_moves_nothing(shop):
    from apps.b2b.workspace import inventory_repository as inventory

    lead = _quick_sale(shop, "2")
    (line,) = inventory.lead_return_lines(lead["id"])
    with pytest.raises(inventory.InventoryError) as refusal:
        inventory.record_return_for_lead(
            lead, lines=[{"lead_item_id": line["id"], "qty": "3"}],
            author_id=shop["seller_id"], note="Ko'p",
        )
    assert refusal.value.code == "too_much"
    assert _on_shelf(shop["product_id"], shop["warehouse_id"]) == Decimal("3")


def test_the_lead_item_indexes_are_not_unique(shop):
    """The schema command replaces the unique indexes a database created
    before 2026-09-09 still carries; a fresh one must not get them back."""
    from shared.raw.db import fetch_all

    rows = fetch_all(
        "SELECT indexname, indexdef FROM pg_indexes WHERE indexname IN "
        "('b2b_stock_movement_lead_item_idx', 'b2b_stock_document_item_lead_item_idx')"
    )
    assert len(rows) == 2
    assert all("UNIQUE" not in row["indexdef"] for row in rows)
