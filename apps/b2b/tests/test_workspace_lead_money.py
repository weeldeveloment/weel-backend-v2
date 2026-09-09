"""What a deal owes, and what comes back off it.

Two features share this file because they share a subject — a sale that is
not finished. One half is money: a deal joins the debts screen by an act
(sold on credit, or a payment recorded against it) and never by the absence
of information, which is the whole reason `debt_tracked` exists beside
`paid_amount`. The other half is goods: a return is filed as the warehouse
module's own document, so the stock, the product ledger and "Amallar" all
learn about it without any of them being told about deals.

Mocked repository calls, like the rest of the lead suite: the rules under
test are in the views, not in the database.
"""
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.models import LeadKind, LeadStage, LeadStatus
from apps.b2b.workspace.authentication import WorkspaceUser
from apps.b2b.workspace.inventory_repository import InventoryError
from apps.b2b.workspace.views import (
    WorkspaceCrmDebtsView,
    WorkspaceLeadDebtView,
    WorkspaceLeadPaymentsView,
    WorkspaceLeadReturnView,
)

COMPANY_ID = 55
MANAGER_ID = 1
SELLER_ID = 2

factory = APIRequestFactory()


def _user(role: str, employee_id: int) -> WorkspaceUser:
    return WorkspaceUser({
        "id": employee_id,
        "company_id": COMPANY_ID,
        "role": role,
        "full_name": "Test Person",
        "phone": "+998900000000",
    })


OWNER = _user("owner", MANAGER_ID)
SELLER = _user("employee", SELLER_ID)


def _call(view_class, request, user, **kwargs):
    force_authenticate(request, user=user)
    return view_class.as_view()(request, **kwargs)


def _sale(**overrides):
    """A quick sale — born completed, and the commonest thing to be owed for."""
    lead = {
        "id": 7,
        "company_id": COMPANY_ID,
        "author_id": SELLER_ID,
        "company_name": "GlobalTrade Co",
        "contact_full_name": "Aziz Karimov",
        "contact_phone": "+998901234567",
        "product_name": "Stul",
        "quantity": 3,
        "amount": Decimal("10000000"),
        "paid_amount": Decimal("0"),
        "debt_tracked": False,
        "status": LeadStatus.COMPLETED,
        "stage": LeadStage.WON,
        "kind": LeadKind.QUICK_SALE,
        "source": "manual",
        "claimed_by_id": SELLER_ID,
        "customer_id": 12,
    }
    lead.update(overrides)
    return lead


# ─── A deal says nothing about money until somebody counts it ─────────────────

def test_an_untracked_deal_reports_no_debt():
    """Every deal filed before the ledger existed is one of these, and the
    card has to be able to say "not my business" rather than "owes it all"."""
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=_sale()),
        patch("apps.b2b.workspace.views.repo.set_lead_debt_tracked"),
    ):
        response = _call(
            WorkspaceLeadDebtView,
            factory.post("/leads/7/debt/", {"tracked": False}, format="json"),
            OWNER,
            lead_id=7,
        )
    assert response.status_code == 200
    assert response.data["debt"] is None


def test_a_tracked_deal_reports_what_is_left():
    lead = _sale(debt_tracked=True, paid_amount=Decimal("4000000"))
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=lead),
        patch("apps.b2b.workspace.views.repo.set_lead_debt_tracked"),
    ):
        response = _call(
            WorkspaceLeadDebtView,
            factory.post("/leads/7/debt/", {"tracked": True}, format="json"),
            OWNER,
            lead_id=7,
        )
    assert response.status_code == 200
    assert response.data["debt"] == Decimal("6000000")


def test_an_overpaid_deal_owes_nothing_rather_than_a_negative():
    lead = _sale(debt_tracked=True, paid_amount=Decimal("12000000"))
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=lead),
        patch("apps.b2b.workspace.views.repo.set_lead_debt_tracked"),
    ):
        response = _call(
            WorkspaceLeadDebtView,
            factory.post("/leads/7/debt/", {"tracked": True}, format="json"),
            OWNER,
            lead_id=7,
        )
    assert response.data["debt"] == Decimal("0")


# ─── Recording a payment ──────────────────────────────────────────────────────

def test_a_payment_is_recorded_and_written_to_the_history():
    payment = {"id": 3, "lead_id": 7, "amount": Decimal("4000000")}
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=_sale()),
        patch("apps.b2b.workspace.views.repo.add_lead_payment", return_value=payment) as add,
        patch("apps.b2b.workspace.views.repo.add_lead_activity") as activity,
        patch("apps.b2b.workspace.views.record_audit"),
    ):
        response = _call(
            WorkspaceLeadPaymentsView,
            factory.post(
                "/leads/7/payments/",
                {"amount": "4000000", "method": "cash"},
                format="json",
            ),
            OWNER,
            lead_id=7,
        )

    assert response.status_code == 201
    assert add.call_args.kwargs["amount"] == Decimal("4000000")
    # The deal's own screen shows money in the same feed as the calls and the
    # stage moves; a separate log nobody opens is not a record.
    assert activity.call_args.kwargs["kind"] == "payment"


def test_a_salesperson_does_not_record_payments():
    """TZ v2 §8: a completed deal is management's, and money is the part of it
    the debts screen is read from."""
    with patch("apps.b2b.workspace.views.repo.get_lead", return_value=_sale()):
        response = _call(
            WorkspaceLeadPaymentsView,
            factory.post("/leads/7/payments/", {"amount": "1000"}, format="json"),
            SELLER,
            lead_id=7,
        )
    assert response.status_code == 403


def test_a_payment_of_zero_is_allowed():
    """"Sotildi, puli keyin" with a receipt for nothing — the deal starts
    being counted at its full amount."""
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=_sale()),
        patch("apps.b2b.workspace.views.repo.add_lead_payment", return_value={"id": 1}),
        patch("apps.b2b.workspace.views.repo.add_lead_activity"),
        patch("apps.b2b.workspace.views.record_audit"),
    ):
        response = _call(
            WorkspaceLeadPaymentsView,
            factory.post("/leads/7/payments/", {"amount": "0"}, format="json"),
            OWNER,
            lead_id=7,
        )
    assert response.status_code == 201


# ─── Sending goods back ───────────────────────────────────────────────────────

def _returnable():
    return [{
        "id": 31,
        "lead_id": 7,
        "name": "Stul",
        "unit": "dona",
        "product_id": 900,
        "product_name": "Ofis stuli",
        "product_unit": "dona",
        "warehouse_id": 4,
        "qty": Decimal("3"),
        "amount": Decimal("9000000"),
        "returned": Decimal("1"),
    }]


def test_the_sheet_is_told_what_is_left_to_return():
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=_sale()),
        patch(
            "apps.b2b.workspace.views.inventory.lead_return_lines",
            return_value=_returnable(),
        ),
    ):
        response = _call(
            WorkspaceLeadReturnView, factory.get("/leads/7/return/"), OWNER, lead_id=7
        )

    assert response.status_code == 200
    row = response.data["results"][0]
    assert row["qty"] == Decimal("3")
    assert row["returned"] == Decimal("1")
    # Two of the three chairs are still out — that is what the stepper caps at.
    assert row["left"] == Decimal("2")


def test_a_return_files_a_document_and_a_history_row():
    filed = [{"id": 88, "number": "QT-000088"}]
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=_sale()),
        patch(
            "apps.b2b.workspace.views.inventory.record_return_for_lead",
            return_value=filed,
        ) as book,
        patch("apps.b2b.workspace.views.repo.add_lead_activity") as activity,
        patch("apps.b2b.workspace.views.record_audit"),
    ):
        response = _call(
            WorkspaceLeadReturnView,
            factory.post(
                "/leads/7/return/",
                {
                    "lines": [{"lead_item_id": 31, "qty": "2"}],
                    "note": "Rangi noto'g'ri",
                },
                format="json",
            ),
            OWNER,
            lead_id=7,
        )

    assert response.status_code == 201
    assert response.data["results"] == filed
    assert book.call_args.kwargs["lines"][0]["lead_item_id"] == 31
    assert book.call_args.kwargs["note"] == "Rangi noto'g'ri"
    # The row carries the reason first and the paperwork after it, so the
    # history answers "why did these come back" and still points at the
    # document that moved them.
    assert activity.call_args.kwargs["kind"] == "returned"
    assert activity.call_args.kwargs["text"] == "Rangi noto'g'ri · QT-000088"


def test_a_return_without_a_reason_is_refused():
    """The note is the point of the row. A return with no reason files a
    document nobody can read six months later, so the field is required and
    a blank one is not a reason."""
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=_sale()),
        patch("apps.b2b.workspace.views.inventory.record_return_for_lead") as book,
    ):
        response = _call(
            WorkspaceLeadReturnView,
            factory.post(
                "/leads/7/return/",
                {"lines": [{"lead_item_id": 31, "qty": "2"}], "note": "   "},
                format="json",
            ),
            OWNER,
            lead_id=7,
        )

    assert response.status_code == 400
    assert response.data["errors"][0]["field"] == "note"
    book.assert_not_called()


def test_returning_more_than_was_sold_is_refused():
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=_sale()),
        patch(
            "apps.b2b.workspace.views.inventory.record_return_for_lead",
            side_effect=InventoryError("Ko'p", code="too_much"),
        ),
        patch("apps.b2b.workspace.views.repo.add_lead_activity") as activity,
    ):
        response = _call(
            WorkspaceLeadReturnView,
            factory.post(
                "/leads/7/return/",
                {
                    "lines": [{"lead_item_id": 31, "qty": "9"}],
                    "note": "Hammasi qaytdi",
                },
                format="json",
            ),
            OWNER,
            lead_id=7,
        )

    assert response.status_code == 409
    assert response.data["code"] == "too_much"
    # Nothing moved, so nothing is written to the deal's history either.
    activity.assert_not_called()


def test_a_deal_that_has_sold_nothing_cannot_be_returned():
    """An open lead is an intention: no goods have left, so none can come
    back, and the sheet must not offer a button that would file an empty
    document."""
    open_lead = _sale(
        kind=LeadKind.LEAD, stage=LeadStage.NEGOTIATION, status=LeadStatus.IN_PROGRESS
    )
    with patch("apps.b2b.workspace.views.repo.get_lead", return_value=open_lead):
        response = _call(
            WorkspaceLeadReturnView, factory.get("/leads/7/return/"), OWNER, lead_id=7
        )
    assert response.status_code == 409


def test_the_salesperson_may_return_their_own_sale():
    """A deliberate hole in TZ v2 §8, asked for on 2026-09-09: the customer
    walks back to the person they bought from, and that person was being sent
    to find an administrator to undo a sale they made themselves."""
    with patch(
        "apps.b2b.workspace.views.repo.get_lead", return_value=_sale()
    ), patch(
        "apps.b2b.workspace.views.inventory.lead_return_lines",
        return_value=_returnable(),
    ):
        response = _call(
            WorkspaceLeadReturnView, factory.get("/leads/7/return/"), SELLER, lead_id=7
        )
    assert response.status_code == 200


def test_a_salesperson_does_not_return_somebody_else_s_sale():
    """The hole above is the claimant's alone, not "anybody in sales": a
    colleague's deal is still none of their business."""
    other = _user("employee", SELLER_ID + 1)
    with patch("apps.b2b.workspace.views.repo.get_lead", return_value=_sale()):
        response = _call(
            WorkspaceLeadReturnView, factory.get("/leads/7/return/"), other, lead_id=7
        )
    assert response.status_code == 403


# ─── The debts screen ─────────────────────────────────────────────────────────

def test_debts_are_grouped_per_customer():
    rows = [{
        "customer_id": 12, "full_name": "Aziz Karimov", "company_name": "GlobalTrade Co",
        "phone": "+998901234567", "deal_count": 2, "total": Decimal("18000000"),
        "paid": Decimal("4000000"), "debt": Decimal("14000000"),
        "last_payment_at": None, "oldest_at": None,
    }]
    with patch("apps.b2b.workspace.views.repo.customer_debts", return_value=rows):
        response = _call(WorkspaceCrmDebtsView, factory.get("/crm/debts/"), OWNER)

    assert response.status_code == 200
    assert response.data["results"][0]["debt"] == Decimal("14000000")


def test_one_customer_s_debts_come_back_as_their_deals():
    """Tapping a row on the debts screen opens the deals behind the figure,
    not a second summary of it."""
    with patch(
        "apps.b2b.workspace.views.repo.list_debtor_leads",
        return_value=[_sale(debt_tracked=True, paid_amount=Decimal("4000000"))],
    ) as listed:
        response = _call(
            WorkspaceCrmDebtsView, factory.get("/crm/debts/?customer_id=12"), OWNER
        )

    assert response.status_code == 200
    assert listed.call_args.kwargs["customer_id"] == 12
    assert response.data["results"][0]["debt"] == Decimal("6000000")
