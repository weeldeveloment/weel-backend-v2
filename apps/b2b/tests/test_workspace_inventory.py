"""The stock room — the rules the SQL does not show.

  * **Who may do what.** Everyone in the sales module reads the catalogue;
    receipts, transfers and counts need ``stock_manage``; writing off and
    repricing are narrower still; and an owner passes everything. Costs are
    blanked for a reader without ``stock_view_cost``.
  * **The ledger's arithmetic.** A receipt adds, a sale subtracts, a transfer
    does both, a count stores the difference, and taking more than is there
    is refused — except for a storno, which is the truth about a document.
  * **A sale checks the shelf first.** Completing a lead, moving it to
    ``won`` and recording a quick sale all ask the warehouse before closing;
    short, they answer 409 with what is missing. A free price needs the
    product's switch and the seller's right. A repeated submission with the
    same key lands on the first row.

Views run against a mocked repository; the ledger's arithmetic is checked
by patching the row-level helpers it is built on. The document flows
(confirm, send/receive, storno, backorders, import) are exercised against a
real database by the smoke script that accompanied the change and are not
repeated here without one.
"""
from decimal import Decimal
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.models import LeadKind, LeadStage, LeadStatus
from apps.b2b.workspace import inventory_documents as documents
from apps.b2b.workspace import inventory_repository as inventory
from apps.b2b.workspace.authentication import WorkspaceUser
from apps.b2b.workspace.inventory_repository import InventoryError, MovementKind
from apps.b2b.workspace.inventory_views import (
    WorkspaceDocumentListCreateView,
    WorkspaceInventorySummaryView,
    WorkspaceMovementListCreateView,
    WorkspaceProductDetailView,
    WorkspaceProductListCreateView,
    WorkspaceWarehouseDetailView,
    WorkspaceWarehouseListCreateView,
)
from apps.b2b.workspace.views import (
    WorkspaceLeadCompleteView,
    WorkspaceLeadListCreateView,
    WorkspaceLeadStageView,
)

COMPANY_ID = 55
OWNER_ID = 1
MANAGER_ID = 2
EMPLOYEE_ID = 3

factory = APIRequestFactory()


def _user(role: str, employee_id: int) -> WorkspaceUser:
    return WorkspaceUser({
        "id": employee_id,
        "company_id": COMPANY_ID,
        "role": role,
        "full_name": "Test Person",
        "phone": "+998900000000",
    })


OWNER = _user("owner", OWNER_ID)
MANAGER = _user("manager", MANAGER_ID)
EMPLOYEE = _user("employee", EMPLOYEE_ID)


def _call(view_class, request, user, **kwargs):
    force_authenticate(request, user=user)
    return view_class.as_view()(request, **kwargs)


def _product(**overrides):
    row = {
        "id": 10,
        "company_id": COMPANY_ID,
        "kind": "product",
        "name": "Sement M400",
        "unit": "qop",
        "purchase_price": Decimal("40000"),
        "sale_price": Decimal("52000"),
        "allow_free_price": False,
        "currency": "UZS",
        "is_active": True,
        "stock_total": Decimal("20"),
        "reserved": Decimal("0"),
        "available": Decimal("20"),
        "stock_value": Decimal("800000"),
        "stocks": [],
    }
    row.update(overrides)
    return row


def _warehouse(**overrides):
    row = {"id": 3, "company_id": COMPANY_ID, "name": "Asosiy", "is_active": True, "is_default": True}
    row.update(overrides)
    return row


# ─── Who may do what ──────────────────────────────────────────────────────────

def test_any_sales_member_reads_the_catalogue_without_costs():
    with (
        patch("apps.b2b.workspace.inventory_views.inventory.list_products", return_value=[_product()]),
        patch("apps.b2b.workspace.inventory_views.inventory.list_brands", return_value=[]),
    ):
        response = _call(WorkspaceProductListCreateView, factory.get("/inventory/products/"), EMPLOYEE)
    assert response.status_code == 200
    row = response.data["results"][0]
    assert row["name"] == "Sement M400"
    assert row["sale_price"] == Decimal("52000")
    # Purchase prices are the owner's and the manager's, not everyone's.
    assert row["purchase_price"] is None
    assert row["stock_value"] is None


def test_a_manager_sees_costs():
    with (
        patch("apps.b2b.workspace.inventory_views.inventory.list_products", return_value=[_product()]),
        patch("apps.b2b.workspace.inventory_views.inventory.list_brands", return_value=[]),
    ):
        response = _call(WorkspaceProductListCreateView, factory.get("/inventory/products/"), MANAGER)
    assert response.data["results"][0]["purchase_price"] == Decimal("40000")


@pytest.mark.parametrize(
    "view_class,path,body",
    [
        (WorkspaceProductListCreateView, "/inventory/products/", {"name": "Sement"}),
        (WorkspaceWarehouseListCreateView, "/inventory/warehouses/", {"name": "Filial"}),
        (WorkspaceMovementListCreateView, "/inventory/movements/",
         {"kind": "receipt", "product_id": 10, "quantity": "5"}),
    ],
)
def test_an_employee_cannot_run_the_stock_room(view_class, path, body):
    with patch("apps.b2b.workspace.inventory_views.inventory.create_product") as create:
        response = _call(view_class, factory.post(path, body, format="json"), EMPLOYEE)
    assert response.status_code == 403
    assert response.data["permission"] == "sales.stock_manage"
    create.assert_not_called()


def test_a_manager_receives_but_does_not_write_off_by_default():
    """The TZ's warehouse manager: receipts and transfers are theirs,
    writing off only when handed the right."""
    with patch("apps.b2b.workspace.inventory_views.documents.create_document") as create:
        write_off = _call(
            WorkspaceDocumentListCreateView,
            factory.post("/inventory/documents/", {
                "kind": "write_off", "warehouse_id": 3, "reason": "defect",
                "items": [{"product_id": 10, "quantity": "1"}],
            }, format="json"),
            MANAGER,
        )
    assert write_off.status_code == 403
    assert write_off.data["permission"] == "sales.stock_write_off"
    create.assert_not_called()

    with patch(
        "apps.b2b.workspace.inventory_views.documents.create_document",
        return_value={"id": 5, "kind": "receipt", "status": "draft", "number": "KR-000005", "line_count": 1},
    ) as create:
        receipt = _call(
            WorkspaceDocumentListCreateView,
            factory.post("/inventory/documents/", {
                "kind": "receipt", "warehouse_id": 3, "items": [{"product_id": 10, "quantity": "5"}],
            }, format="json"),
            MANAGER,
        )
    assert receipt.status_code == 201
    assert create.call_args.kwargs["author_id"] == MANAGER_ID


def test_an_owner_writes_off_and_confirms_in_one_request():
    with (
        patch(
            "apps.b2b.workspace.inventory_views.documents.create_document",
            return_value={"id": 6, "kind": "write_off", "status": "draft", "number": "HC-000006", "line_count": 1},
        ),
        patch(
            "apps.b2b.workspace.inventory_views.documents.confirm_document",
            return_value={"id": 6, "kind": "write_off", "status": "confirmed", "number": "HC-000006", "line_count": 1},
        ) as confirm,
    ):
        response = _call(
            WorkspaceDocumentListCreateView,
            factory.post("/inventory/documents/", {
                "kind": "write_off", "warehouse_id": 3, "reason": "defect", "confirm": True,
                "items": [{"product_id": 10, "quantity": "1"}],
            }, format="json"),
            OWNER,
        )
    assert response.status_code == 201
    assert response.data["status"] == "confirmed"
    confirm.assert_called_once_with(6, COMPANY_ID, actor_id=OWNER_ID)


def test_repricing_from_the_card_needs_the_reprice_right():
    """A manager may edit the card, but a new sale price on it is a
    repricing in all but name."""
    with (
        patch("apps.b2b.workspace.inventory_views.inventory.get_product_raw", return_value=_product()),
        patch("apps.b2b.workspace.inventory_views.inventory.update_product", return_value=_product(name="X")) as update,
    ):
        renamed = _call(
            WorkspaceProductDetailView,
            factory.patch("/inventory/products/10/", {"name": "X"}, format="json"), MANAGER, product_id=10,
        )
        assert renamed.status_code == 200
        repriced = _call(
            WorkspaceProductDetailView,
            factory.patch("/inventory/products/10/", {"sale_price": "60000"}, format="json"), MANAGER, product_id=10,
        )
    assert repriced.status_code == 403
    assert repriced.data["permission"] == "sales.stock_reprice"
    assert update.call_count == 1


def test_a_duplicate_article_is_a_conflict_not_a_crash():
    with patch(
        "apps.b2b.workspace.inventory_views.inventory.create_product",
        side_effect=InventoryError("taken", code="sku_taken"),
    ):
        response = _call(
            WorkspaceProductListCreateView,
            factory.post("/inventory/products/", {"name": "X", "sku": "A-1"}, format="json"),
            OWNER,
        )
    assert response.status_code == 409
    assert response.data["code"] == "sku_taken"


def test_a_warehouse_with_stock_cannot_be_closed():
    with patch(
        "apps.b2b.workspace.inventory_views.inventory.delete_warehouse",
        side_effect=InventoryError("not empty", code="warehouse_not_empty"),
    ):
        response = _call(
            WorkspaceWarehouseDetailView, factory.delete("/inventory/warehouses/3/"),
            OWNER, warehouse_id=3,
        )
    assert response.status_code == 409


def test_a_one_line_movement_becomes_a_confirmed_document():
    with (
        patch(
            "apps.b2b.workspace.inventory_views.documents.create_document",
            return_value={"id": 7, "kind": "inventory", "status": "draft"},
        ) as create,
        patch(
            "apps.b2b.workspace.inventory_views.documents.confirm_document",
            return_value={"id": 7, "kind": "inventory", "status": "confirmed"},
        ) as confirm,
    ):
        response = _call(
            WorkspaceMovementListCreateView,
            factory.post("/inventory/movements/", {"kind": "adjustment", "product_id": 10, "quantity": "9"}, format="json"),
            MANAGER,
        )
    assert response.status_code == 201
    assert create.call_args.kwargs["kind"] == "inventory"
    assert create.call_args.kwargs["items"] == [{"product_id": 10, "unit_cost": None, "counted_quantity": Decimal("9")}]
    confirm.assert_called_once()


def test_a_shortage_on_confirm_is_a_409_with_the_list():
    shortage = [{"product_id": 10, "name": "Sement M400", "needed": 5, "available": 2, "short": 3}]
    with (
        patch("apps.b2b.workspace.inventory_views.documents.create_document", return_value={"id": 8, "kind": "write_off", "status": "draft"}),
        patch(
            "apps.b2b.workspace.inventory_views.documents.confirm_document",
            side_effect=InventoryError("short", code="insufficient_stock", details=shortage),
        ),
    ):
        response = _call(
            WorkspaceMovementListCreateView,
            factory.post("/inventory/movements/", {"kind": "write_off", "product_id": 10, "warehouse_id": 3, "quantity": "5"}, format="json"),
            OWNER,
        )
    assert response.status_code == 409
    assert response.data["code"] == "insufficient_stock"
    assert response.data["shortages"] == shortage


def test_the_summary_hides_profit_from_a_reader_without_cost_rights():
    summary = {
        "date_from": None, "date_to": None, "product_count": 1, "quantity_total": 1, "stock_value": 1,
        "retail_value": 1, "low_stock_count": 0, "out_of_stock_count": 0, "sale_count": 1, "sold_qty": 1,
        "revenue": 10, "cogs": 6, "profit": 4, "received_qty": 0, "purchases": 0, "written_off_qty": 0,
        "write_offs": 0, "returned_qty": 0, "adjusted_qty": 0, "adjustments": 0, "adjustment_count": 0,
        "turnover_ratio": 1, "daily": [], "top_products": [{"product_id": 10, "name": "x", "unit": "d", "sold_qty": 1, "revenue": 10, "profit": 4}],
        "by_supplier": [], "write_off_reasons": [],
    }
    with patch("apps.b2b.workspace.inventory_views.inventory.summary", return_value=dict(summary)):
        response = _call(WorkspaceInventorySummaryView, factory.get("/inventory/summary/"), EMPLOYEE)
    assert response.status_code == 200
    assert response.data["revenue"] == 10
    assert response.data["profit"] is None and response.data["cogs"] is None
    assert response.data["top_products"][0]["profit"] is None


# ─── The ledger's arithmetic ──────────────────────────────────────────────────

class _Ledger:
    """Stands in for the row-level helpers `apply_movement` writes through,
    so the arithmetic can be checked without a database."""

    def __init__(self, balances: dict[tuple[int, int], Decimal]):
        self.balances = dict(balances)
        self.rows: list[list] = []

    def lock(self, product_id, warehouse_id):
        return self.balances.get((product_id, warehouse_id), Decimal(0))

    def set(self, product_id, warehouse_id, quantity):
        self.balances[(product_id, warehouse_id)] = quantity

    def insert(self, sql, params=None):
        self.rows.append(params)
        keys = (
            "company_id", "product_id", "warehouse_id", "to_warehouse_id", "kind",
            "quantity", "unit_cost", "cost_price", "note", "lead_id", "lead_item_id",
            "author_id", "created_at", "document_id", "currency", "customer_id", "reversal_of",
        )
        return {"id": len(self.rows), **dict(zip(keys, params))}


@pytest.fixture
def ledger():
    book = _Ledger({(10, 3): Decimal("12"), (10, 4): Decimal("0")})

    def warehouse(warehouse_id, company_id):
        return _warehouse(id=warehouse_id, is_default=warehouse_id == 3)

    with (
        patch.object(inventory, "_lock_stock", side_effect=book.lock),
        patch.object(inventory, "_set_stock", side_effect=book.set),
        patch.object(inventory, "get_product_raw", return_value=_product()),
        patch.object(inventory, "fetch_one", side_effect=book.insert),
        patch.object(inventory, "get_warehouse", side_effect=warehouse),
        patch.object(inventory, "execute", return_value=1),
        patch.object(inventory.transaction, "atomic"),
    ):
        yield book


def test_a_receipt_adds_at_the_purchase_price(ledger):
    row = inventory.apply_movement(
        COMPANY_ID, kind=MovementKind.RECEIPT, product_id=10, warehouse_id=3, quantity="8"
    )
    assert ledger.balances[(10, 3)] == Decimal("20")
    assert row["unit_cost"] == Decimal("40000")
    assert row["cost_price"] == Decimal("40000")
    assert row["balance_after"] == Decimal("20")


def test_a_sale_subtracts_at_the_sale_price_and_freezes_the_cost(ledger):
    row = inventory.apply_movement(
        COMPANY_ID, kind=MovementKind.SALE, product_id=10, warehouse_id=3, quantity="2", document_id=9
    )
    assert ledger.balances[(10, 3)] == Decimal("10")
    assert row["unit_cost"] == Decimal("52000")
    assert row["cost_price"] == Decimal("40000")
    assert row["document_id"] == 9
    assert row["currency"] == "UZS"


def test_a_transfer_moves_between_the_two(ledger):
    inventory.apply_movement(
        COMPANY_ID, kind=MovementKind.TRANSFER, product_id=10,
        warehouse_id=3, to_warehouse_id=4, quantity="5",
    )
    assert ledger.balances[(10, 3)] == Decimal("7")
    assert ledger.balances[(10, 4)] == Decimal("5")


def test_a_transfer_needs_a_different_target(ledger):
    with pytest.raises(InventoryError) as refusal:
        inventory.apply_movement(
            COMPANY_ID, kind=MovementKind.TRANSFER, product_id=10,
            warehouse_id=3, to_warehouse_id=3, quantity="5",
        )
    assert refusal.value.code == "bad_target"


def test_a_count_stores_the_difference(ledger):
    row = inventory.apply_movement(
        COMPANY_ID, kind=MovementKind.ADJUSTMENT, product_id=10, warehouse_id=3, quantity="9"
    )
    assert ledger.balances[(10, 3)] == Decimal("9")
    assert row["quantity"] == Decimal("-3")


def test_the_shelf_cannot_go_below_zero_by_hand(ledger):
    with pytest.raises(InventoryError) as refusal:
        inventory.apply_movement(
            COMPANY_ID, kind=MovementKind.WRITE_OFF, product_id=10, warehouse_id=3, quantity="13"
        )
    assert refusal.value.code == "insufficient_stock"
    assert refusal.value.details[0]["short"] == Decimal("1")
    assert ledger.balances[(10, 3)] == Decimal("12")


def test_a_storno_may_overdraw_the_shelf(ledger):
    inventory.apply_movement(
        COMPANY_ID, kind=MovementKind.WRITE_OFF, product_id=10, warehouse_id=3,
        quantity="13", allow_negative=True, reversal_of=1,
    )
    assert ledger.balances[(10, 3)] == Decimal("-1")


def test_a_service_never_touches_the_shelf(ledger):
    with patch.object(inventory, "get_product_raw", return_value=_product(kind="service")):
        with pytest.raises(InventoryError) as refusal:
            inventory.apply_movement(
                COMPANY_ID, kind=MovementKind.RECEIPT, product_id=10, warehouse_id=3, quantity="1"
            )
    assert refusal.value.code == "no_stock_kind"


def test_a_bundle_unfolds_into_its_parts_and_a_service_into_nothing():
    bundle = _product(id=20, kind="bundle", name="To'plam")
    brick = _product(id=10, name="G'isht")
    with (
        patch.object(inventory, "get_product_raw", side_effect=lambda pid, cid: {20: bundle, 10: brick, 30: _product(id=30, kind="service")}[pid]),
        patch.object(inventory, "list_components", return_value=[
            {"component_id": 10, "quantity": Decimal("3"), "kind": "product"},
            {"component_id": 30, "quantity": Decimal("1"), "kind": "service"},
        ]),
    ):
        parts = inventory.expand_for_stock(COMPANY_ID, 20, 2)
        assert [(p["id"], q) for p, q in parts] == [(10, Decimal("6"))]
        assert inventory.expand_for_stock(COMPANY_ID, 30, 5) == []


def test_the_sale_price_follows_the_markup():
    assert inventory._price_from_markup(1000, 50) == Decimal("1500.00")
    assert inventory._price_from_markup(None, 50) is None


def test_document_numbers_carry_their_kind():
    assert documents._number("receipt", 12) == "KR-000012"
    assert documents._number("write_off", 3) == "HC-000003"


def test_the_legs_of_each_document_kind():
    doc = {"kind": "transfer", "warehouse_id": 3, "to_warehouse_id": 4}
    assert documents._legs(doc, Decimal(5)) == [(3, Decimal(-5)), (4, Decimal(5))]
    assert documents._legs({"kind": "receipt", "warehouse_id": 3}, Decimal(5)) == [(3, Decimal(5))]
    assert documents._legs({"kind": "sale", "warehouse_id": 3}, Decimal(5)) == [(3, Decimal(-5))]
    assert documents._legs({"kind": "revaluation"}, Decimal(5)) == []


# ─── A sale asks the shelf first ──────────────────────────────────────────────

def _lead(**overrides):
    lead = {
        "id": 7,
        "company_id": COMPANY_ID,
        "author_id": MANAGER_ID,
        "company_name": "GlobalTrade Co",
        "contact_full_name": "Aziz Karimov",
        "contact_phone": "+998901234567",
        "product_name": "Sement M400",
        "quantity": 3,
        "amount": 156_000,
        "status": LeadStatus.IN_PROGRESS,
        "stage": LeadStage.PROPOSAL,
        "claimed_by_id": EMPLOYEE_ID,
        "created_at": None,
        "claimed_at": None,
        "completed_at": None,
        "due_date": None,
        "quality": None,
        "kind": LeadKind.LEAD,
    }
    lead.update(overrides)
    return lead


SHORT = [{"product_id": 10, "name": "Sement M400", "unit": "qop", "warehouse_id": 3, "needed": 3, "available": 1, "short": 2}]


@contextmanager
def _stock(shortages, allow_backorder=False):
    """The shelf as the lead views see it: one catalogue line, and what the
    warehouse says about covering it."""
    with (
        patch("apps.b2b.workspace.views.inventory.lead_lines_to_book", return_value=[{"id": 1, "product_id": 10, "qty": 3}]),
        patch("apps.b2b.workspace.views.inventory.get_settings", return_value={"allow_backorder": allow_backorder}),
        patch("apps.b2b.workspace.views.inventory.default_warehouse", return_value=_warehouse()),
        patch("apps.b2b.workspace.views.inventory.shortages_for_lines", return_value=shortages),
    ):
        yield


@contextmanager
def _sale_env(product, shortages=()):
    with (
        patch("apps.b2b.workspace.views.inventory.get_product_raw", return_value=product),
        patch("apps.b2b.workspace.views.inventory.get_settings", return_value={"allow_backorder": False}),
        patch("apps.b2b.workspace.views.inventory.default_warehouse", return_value=_warehouse()),
        patch("apps.b2b.workspace.views.inventory.shortages_for_lines", return_value=list(shortages)),
    ):
        yield


def test_completing_a_short_lead_is_refused_and_the_lead_stays_open():
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()),
        patch("apps.b2b.workspace.views.repo.complete_lead") as complete,
        _stock(SHORT),
    ):
        response = _call(WorkspaceLeadCompleteView, factory.post("/leads/7/complete/"), EMPLOYEE, lead_id=7)
    assert response.status_code == 409
    assert response.data["code"] == "insufficient_stock"
    assert response.data["shortages"] == SHORT
    complete.assert_not_called()


def test_completing_a_covered_lead_books_its_lines():
    won = _lead(status=LeadStatus.COMPLETED, stage=LeadStage.WON)
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()),
        patch("apps.b2b.workspace.views.repo.complete_lead", return_value=won),
        patch("apps.b2b.workspace.views.inventory.record_sale_for_lead", return_value=1) as book,
        _stock([]),
    ):
        response = _call(WorkspaceLeadCompleteView, factory.post("/leads/7/complete/"), EMPLOYEE, lead_id=7)
    assert response.status_code == 200
    book.assert_called_once_with(won, author_id=EMPLOYEE_ID)


def test_backorders_on_let_a_short_lead_close():
    won = _lead(status=LeadStatus.COMPLETED, stage=LeadStage.WON)
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()),
        patch("apps.b2b.workspace.views.repo.complete_lead", return_value=won),
        patch("apps.b2b.workspace.views.inventory.record_sale_for_lead", return_value=1),
        _stock(SHORT, allow_backorder=True),
    ):
        response = _call(WorkspaceLeadCompleteView, factory.post("/leads/7/complete/"), EMPLOYEE, lead_id=7)
    assert response.status_code == 200


def test_moving_to_won_asks_the_shelf_and_other_stages_do_not():
    for stage, expected in ((LeadStage.NEGOTIATION, 200), (LeadStage.WON, 409)):
        with (
            patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()),
            patch("apps.b2b.workspace.views.repo.set_lead_stage", return_value=_lead(stage=stage)),
            patch("apps.b2b.workspace.views.inventory.record_sale_for_lead", return_value=1),
            _stock(SHORT),
        ):
            response = _call(
                WorkspaceLeadStageView,
                factory.post("/leads/7/stage/", {"stage": stage}, format="json"), EMPLOYEE, lead_id=7,
            )
        assert response.status_code == expected, stage


def _quick_sale(amount="156000", key=None):
    body = {
        "contact_full_name": "Aziz Karimov",
        "contact_phone": "+998901234567",
        "kind": "quick_sale",
        "payment_method": "cash",
        "items": [{"name": "Sement M400", "product_id": 10, "qty": "3", "amount": amount}],
    }
    if key:
        body["idempotency_key"] = key
    return factory.post("/leads/", body, format="json")


def test_a_quick_sale_at_list_price_is_booked():
    sale = _lead(kind=LeadKind.QUICK_SALE, status=LeadStatus.COMPLETED, stage=LeadStage.WON, claimed_by_id=MANAGER_ID)
    with (
        patch("apps.b2b.workspace.views.inventory.get_product_raw", return_value=_product()),
        patch("apps.b2b.workspace.views.inventory.get_settings", return_value={"allow_backorder": False}),
        patch("apps.b2b.workspace.views.inventory.default_warehouse", return_value=_warehouse()),
        patch("apps.b2b.workspace.views.inventory.shortages_for_lines", return_value=[]),
        patch("apps.b2b.workspace.views.repo.find_lead_by_external_id", return_value=None),
        patch("apps.b2b.workspace.views.repo.create_lead", return_value=sale) as create,
        patch("apps.b2b.workspace.views.repo.list_company_recipients", return_value=[]),
        patch("apps.b2b.workspace.views.inventory.record_sale_for_lead", return_value=1) as book,
    ):
        response = _call(WorkspaceLeadListCreateView, _quick_sale(key="abc-1"), MANAGER)
    assert response.status_code == 201, response.data
    book.assert_called_once()
    assert create.call_args.kwargs["external_id"] == "abc-1"
    line = create.call_args.kwargs["items"][0]
    assert line["product_id"] == 10 and line["qty"] == Decimal("3")


def test_a_short_quick_sale_is_refused_before_anything_is_written():
    with (
        patch("apps.b2b.workspace.views.inventory.get_product_raw", return_value=_product()),
        patch("apps.b2b.workspace.views.inventory.get_settings", return_value={"allow_backorder": False}),
        patch("apps.b2b.workspace.views.inventory.default_warehouse", return_value=_warehouse()),
        patch("apps.b2b.workspace.views.inventory.shortages_for_lines", return_value=SHORT),
        patch("apps.b2b.workspace.views.repo.create_lead") as create,
    ):
        response = _call(WorkspaceLeadListCreateView, _quick_sale(), MANAGER)
    assert response.status_code == 409
    assert response.data["shortages"] == SHORT
    create.assert_not_called()


def test_a_free_price_needs_the_switch_and_the_right():
    """156 000 for three is the list price; 150 000 is not."""
    with patch("apps.b2b.workspace.views.repo.create_lead") as create:
        # Switch off on the product: nobody may.
        with _sale_env(_product()):
            response = _call(WorkspaceLeadListCreateView, _quick_sale(amount="150000"), MANAGER)
        assert response.status_code == 403 and response.data["code"] == "free_price_off"
        # Switch on, a seller whose role editor withheld the right: refused.
        with (
            _sale_env(_product(allow_free_price=True)),
            patch("apps.b2b.workspace.inventory_views.may", return_value=False),
        ):
            response = _call(WorkspaceLeadListCreateView, _quick_sale(amount="150000"), MANAGER)
        assert response.status_code == 403 and response.data["code"] == "free_price_denied"
        create.assert_not_called()
    # Switch on, a manager (who holds stock_free_price by default): allowed.
    sale = _lead(kind=LeadKind.QUICK_SALE, status=LeadStatus.COMPLETED, stage=LeadStage.WON, claimed_by_id=MANAGER_ID)
    with (
        patch("apps.b2b.workspace.views.inventory.get_product_raw", return_value=_product(allow_free_price=True)),
        patch("apps.b2b.workspace.views.inventory.get_settings", return_value={"allow_backorder": False}),
        patch("apps.b2b.workspace.views.inventory.default_warehouse", return_value=_warehouse()),
        patch("apps.b2b.workspace.views.inventory.shortages_for_lines", return_value=[]),
        patch("apps.b2b.workspace.views.repo.create_lead", return_value=sale),
        patch("apps.b2b.workspace.views.repo.list_company_recipients", return_value=[]),
        patch("apps.b2b.workspace.views.inventory.record_sale_for_lead", return_value=1),
    ):
        response = _call(WorkspaceLeadListCreateView, _quick_sale(amount="150000"), MANAGER)
    assert response.status_code == 201, response.data


def test_the_same_key_twice_lands_on_the_first_sale():
    sale = _lead(kind=LeadKind.QUICK_SALE, status=LeadStatus.COMPLETED, stage=LeadStage.WON, claimed_by_id=MANAGER_ID)
    with (
        patch("apps.b2b.workspace.views.inventory.get_product_raw", return_value=_product()),
        patch("apps.b2b.workspace.views.inventory.get_settings", return_value={"allow_backorder": False}),
        patch("apps.b2b.workspace.views.inventory.default_warehouse", return_value=_warehouse()),
        patch("apps.b2b.workspace.views.inventory.shortages_for_lines", return_value=[]),
        patch("apps.b2b.workspace.views.repo.find_lead_by_external_id", return_value=sale),
        patch("apps.b2b.workspace.views.repo.create_lead") as create,
        patch("apps.b2b.workspace.views.inventory.record_sale_for_lead") as book,
    ):
        response = _call(WorkspaceLeadListCreateView, _quick_sale(key="abc-1"), MANAGER)
    assert response.status_code == 200
    assert response.data["id"] == 7
    create.assert_not_called()
    book.assert_not_called()


def test_a_stock_failure_after_the_check_never_breaks_a_won_deal():
    won = _lead(status=LeadStatus.COMPLETED, stage=LeadStage.WON)
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()),
        patch("apps.b2b.workspace.views.repo.complete_lead", return_value=won),
        patch("apps.b2b.workspace.views.inventory.record_sale_for_lead", side_effect=RuntimeError("database away")),
        _stock([]),
    ):
        response = _call(WorkspaceLeadCompleteView, factory.post("/leads/7/complete/"), EMPLOYEE, lead_id=7)
    assert response.status_code == 200


def test_record_sale_files_ready_and_pending_lines_apart():
    """Backorders on: the covered line is a confirmed sale document, the
    short one a pending sale document that ships later. Neither is booked
    twice — the second call finds no lines left."""
    lines = [
        {"id": 1, "product_id": 10, "qty": Decimal("3"), "amount": Decimal("156000"), "warehouse_id": 3},
        {"id": 2, "product_id": 11, "qty": Decimal("1"), "amount": Decimal("9000"), "warehouse_id": 3},
    ]
    created: list[dict] = []
    confirmed: list[int] = []
    pending: list[int] = []

    def create(company_id, **kwargs):
        created.append(kwargs)
        return {"id": len(created)}

    with (
        patch.object(inventory, "lead_lines_to_book", side_effect=[lines, []]),
        patch.object(inventory, "get_settings", return_value={"allow_backorder": True}),
        patch.object(inventory, "default_warehouse", return_value=_warehouse()),
        patch.object(inventory, "shortages_for_lines", return_value=[{"product_id": 11, "short": 1}]),
        patch.object(inventory, "expand_for_stock", side_effect=lambda cid, pid, qty: [(_product(id=pid), Decimal(qty))]),
        patch("apps.b2b.workspace.inventory_documents.create_document", side_effect=create),
        patch("apps.b2b.workspace.inventory_documents.confirm_document", side_effect=lambda did, cid, actor_id: confirmed.append(did)),
        patch("apps.b2b.workspace.inventory_documents.mark_pending", side_effect=lambda did, cid: pending.append(did)),
    ):
        assert inventory.record_sale_for_lead(_lead(), author_id=EMPLOYEE_ID) == 2
        assert inventory.record_sale_for_lead(_lead(), author_id=EMPLOYEE_ID) == 0
    assert len(created) == 2
    ready, waiting = created
    assert ready["items"][0]["lead_item_id"] == 1 and ready["items"][0]["unit_cost"] == Decimal("52000")
    assert waiting["items"][0]["lead_item_id"] == 2
    assert confirmed == [1] and pending == [2]


def test_record_sale_refuses_a_shortage_when_backorders_are_off():
    with (
        patch.object(inventory, "lead_lines_to_book", return_value=[{"id": 1, "product_id": 10, "qty": Decimal("3"), "amount": Decimal("1")}]),
        patch.object(inventory, "get_settings", return_value={"allow_backorder": False}),
        patch.object(inventory, "default_warehouse", return_value=_warehouse()),
        patch.object(inventory, "shortages_for_lines", return_value=SHORT),
    ):
        with pytest.raises(InventoryError) as refusal:
            inventory.record_sale_for_lead(_lead(), author_id=EMPLOYEE_ID)
    assert refusal.value.code == "insufficient_stock"
    assert refusal.value.details == SHORT


# ─── Periods ──────────────────────────────────────────────────────────────────

def test_a_bare_to_date_means_the_whole_day():
    """``?to=2026-09-05`` includes everything booked on the 5th. Django's
    `parse_datetime` reads a bare date as midnight — the start of that day —
    which quietly dropped the current day from every period."""
    from apps.b2b.workspace.inventory_views import _datetime_param

    request = factory.get("/inventory/movements/", {"from": "2026-09-01", "to": "2026-09-05"})
    request.query_params = request.GET
    start = _datetime_param(request, "from")
    end = _datetime_param(request, "to", end=True)
    assert (start.year, start.month, start.day, start.hour) == (2026, 9, 1, 0)
    assert (end.year, end.month, end.day, end.hour) == (2026, 9, 6, 0)
    request = factory.get("/x/", {"to": "2026-09-05T13:30:00"})
    request.query_params = request.GET
    exact = _datetime_param(request, "to", end=True)
    assert (exact.day, exact.hour, exact.minute) == (5, 13, 30)
