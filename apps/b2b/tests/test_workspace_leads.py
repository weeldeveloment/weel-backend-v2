"""The lead board's own rules.

Two things are worth pinning here and neither is visible from the SQL:

  * **Who may do what.** A lead's contact is withheld from the whole company
    once somebody claims it, and moving, commenting on or editing a lead is the
    owner's right (a manager may override). Getting this wrong leaks a customer
    to a competitor sitting in the same workspace.
  * **The stage/status coupling.** The board's three statuses and the funnel's
    six stages are separate columns, and exactly one rule keeps them in step:
    reaching ``won`` or ``lost`` completes the lead. It lives in the repository,
    so the check is that the view goes through it.

Run against mocked repository calls — the rules are in the views, not in the
database, so no database is needed.
"""
from unittest.mock import patch

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.models import LeadStage, LeadStatus
from apps.b2b.workspace.authentication import WorkspaceUser
from apps.b2b.workspace.views import (
    WorkspaceCustomerSearchView,
    WorkspaceLeadAssignView,
    WorkspaceLeadCommentView,
    WorkspaceLeadDetailView,
    WorkspaceLeadListCreateView,
    WorkspaceLeadStageView,
    WorkspaceLeadTasksView,
)

COMPANY_ID = 55
MANAGER_ID = 1
OWNER_ID = 2
BYSTANDER_ID = 3

factory = APIRequestFactory()


def _user(role: str, employee_id: int) -> WorkspaceUser:
    return WorkspaceUser({
        "id": employee_id,
        "company_id": COMPANY_ID,
        "role": role,
        "full_name": "Test Person",
        "phone": "+998900000000",
    })


MANAGER = _user("owner", MANAGER_ID)
OWNER = _user("employee", OWNER_ID)
BYSTANDER = _user("employee", BYSTANDER_ID)


def _call(view_class, request, user, **kwargs):
    force_authenticate(request, user=user)
    return view_class.as_view()(request, **kwargs)


def _lead(**overrides):
    lead = {
        "id": 7,
        "company_id": COMPANY_ID,
        "author_id": MANAGER_ID,
        "company_name": "GlobalTrade Co",
        "contact_full_name": "Aziz Karimov",
        "contact_phone": "+998901234567",
        "contact_position": "Sotuvlar bo’limi boshlig’i",
        "contact_email": "aziz@globaltrade.uz",
        "contact_address": "Toshkent, Amir Temur ko’chasi 15",
        "product_name": "CRM tizimi",
        "quantity": 3,
        "amount": 22_000_000,
        "status": LeadStatus.IN_PROGRESS,
        "stage": LeadStage.PROPOSAL,
        "source": "website",
        "claimed_by_id": OWNER_ID,
        "claimed_at": None,
        "completed_at": None,
        "created_at": None,
    }
    lead.update(overrides)
    return lead


# ─── The contact is only for whoever is working the lead ──────────────────────

@pytest.mark.parametrize(
    "user,expected_visible",
    [(OWNER, True), (MANAGER, True), (BYSTANDER, False)],
)
def test_contact_is_withheld_from_the_rest_of_the_board(user, expected_visible):
    """Everyone sees the row; only the owner and a manager see who to call.

    The whole contact card, not just the phone: an email or a street address
    reaches the customer just as well.
    """
    with (
        patch("apps.b2b.workspace.views.repo.list_leads", return_value=[_lead()]),
        patch("apps.b2b.workspace.views.repo.count_lead_items", return_value={}),
        patch("apps.b2b.workspace.views.repo.count_lead_tasks", return_value={}),
    ):
        response = _call(
            WorkspaceLeadListCreateView, factory.get("/leads/"), user
        )

    assert response.status_code == 200
    row = response.data["results"][0]
    assert row["can_view_details"] is expected_visible
    for field in (
        "contact_full_name",
        "contact_phone",
        "contact_position",
        "contact_email",
        "contact_address",
    ):
        assert (row[field] is not None) is expected_visible, field


def test_a_manager_deletes_a_lead():
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()),
        patch("apps.b2b.workspace.views.repo.delete_lead", return_value=True) as delete,
    ):
        response = _call(
            WorkspaceLeadDetailView, factory.delete("/leads/7/"), MANAGER, lead_id=7
        )

    assert response.status_code == 204
    delete.assert_called_once_with(7, COMPANY_ID)


def test_an_employee_cannot_delete_a_lead():
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()),
        patch("apps.b2b.workspace.views.repo.delete_lead") as delete,
    ):
        response = _call(
            WorkspaceLeadDetailView, factory.delete("/leads/7/"), OWNER, lead_id=7
        )

    assert response.status_code == 403
    delete.assert_not_called()


def test_deleting_an_unknown_lead_is_a_404():
    with patch("apps.b2b.workspace.views.repo.get_lead", return_value=None):
        response = _call(
            WorkspaceLeadDetailView, factory.delete("/leads/7/"), MANAGER, lead_id=7
        )

    assert response.status_code == 404


def test_detail_returns_items_activity_and_tasks_in_one_response():
    """The screen needs four things at once, so one request brings all four."""
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()),
        patch(
            "apps.b2b.workspace.views.repo.list_lead_items",
            return_value=[{"id": 1, "name": "CRM tizimi", "unit": "3 oy", "amount": 9_000_000}],
        ),
        patch(
            "apps.b2b.workspace.views.repo.list_lead_activity",
            return_value=[{"id": 4, "kind": "comment", "text": "Qo’ng’iroq qildim"}],
        ),
        patch("apps.b2b.workspace.views.repo.list_lead_tasks", return_value=[]),
    ):
        response = _call(
            WorkspaceLeadDetailView, factory.get("/leads/7/"), OWNER, lead_id=7
        )

    assert response.status_code == 200
    assert response.data["item_count"] == 1
    assert response.data["items"][0]["amount"] == 9_000_000
    assert response.data["activity"][0]["kind"] == "comment"
    assert response.data["tasks"] == []


# ─── Moving the lead along the funnel ─────────────────────────────────────────

def test_the_owner_moves_the_stage():
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()),
        patch(
            "apps.b2b.workspace.views.repo.set_lead_stage",
            return_value=_lead(stage=LeadStage.NEGOTIATION),
        ) as set_stage,
    ):
        response = _call(
            WorkspaceLeadStageView,
            factory.post("/leads/7/stage/", {"stage": LeadStage.NEGOTIATION}, format="json"),
            OWNER,
            lead_id=7,
        )

    assert response.status_code == 200
    assert response.data["stage"] == LeadStage.NEGOTIATION
    # Through the repository, which is where the "won/lost closes the lead"
    # rule lives — the view must not set the status itself.
    set_stage.assert_called_once()


def test_a_bystander_cannot_move_somebody_elses_lead():
    with patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()):
        response = _call(
            WorkspaceLeadStageView,
            factory.post("/leads/7/stage/", {"stage": LeadStage.WON}, format="json"),
            BYSTANDER,
            lead_id=7,
        )

    assert response.status_code == 403


def test_a_closed_lead_does_not_move_again():
    """Once won or lost, the funnel is done with it — reopening would leave the
    completed_at stamp pointing at a lead that is back in play."""
    closed = _lead(status=LeadStatus.COMPLETED, stage=LeadStage.WON)
    with patch("apps.b2b.workspace.views.repo.get_lead", return_value=closed):
        response = _call(
            WorkspaceLeadStageView,
            factory.post("/leads/7/stage/", {"stage": LeadStage.PROPOSAL}, format="json"),
            OWNER,
            lead_id=7,
        )

    assert response.status_code == 409


def test_an_unknown_stage_is_refused():
    with patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()):
        response = _call(
            WorkspaceLeadStageView,
            factory.post("/leads/7/stage/", {"stage": "almost_there"}, format="json"),
            OWNER,
            lead_id=7,
        )

    assert response.status_code == 400


# ─── Reassigning, commenting, and raising a task ──────────────────────────────

def test_only_a_manager_reassigns_a_lead():
    with patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()):
        response = _call(
            WorkspaceLeadAssignView,
            factory.post("/leads/7/assign/", {"employee_id": BYSTANDER_ID}, format="json"),
            OWNER,
            lead_id=7,
        )

    assert response.status_code == 403


def test_reassigning_checks_the_employee_belongs_to_this_company():
    """An id straight off the wire would otherwise hand the lead to another
    company's staff."""
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()),
        patch(
            "apps.b2b.workspace.views.repo.employee_ids_in_company", return_value=set()
        ),
        patch("apps.b2b.workspace.views.repo.assign_lead") as assign,
    ):
        response = _call(
            WorkspaceLeadAssignView,
            factory.post("/leads/7/assign/", {"employee_id": 9999}, format="json"),
            MANAGER,
            lead_id=7,
        )

    assert response.status_code == 404
    assign.assert_not_called()


def test_a_bystander_cannot_comment_on_a_lead():
    """The history names the customer and the calls, so it is withheld with the
    contact rather than being open to the whole board."""
    with patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()):
        response = _call(
            WorkspaceLeadCommentView,
            factory.post("/leads/7/comments/", {"text": "Nima gap?"}, format="json"),
            BYSTANDER,
            lead_id=7,
        )

    assert response.status_code == 403


def test_a_task_raised_off_a_lead_carries_the_link_and_defaults_to_the_asker():
    """The button exists so the person working the deal can note the next step,
    so it does not go through the manager-only task gate and it assigns itself."""
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()),
        patch(
            "apps.b2b.workspace.views.repo.create_task",
            return_value={"id": 31, "title": "Taklifnomani tayyorlash", "assignee_ids": [OWNER_ID]},
        ) as create_task,
    ):
        response = _call(
            WorkspaceLeadTasksView,
            factory.post(
                "/leads/7/tasks/",
                {"title": "Taklifnomani tayyorlash"},
                format="json",
            ),
            OWNER,
            lead_id=7,
        )

    assert response.status_code == 201
    kwargs = create_task.call_args.kwargs
    assert kwargs["lead_id"] == 7
    assert kwargs["assignee_ids"] == [OWNER_ID]
    # Grouped under the customer's name, so it reads sensibly on the Vazifa tab
    # where the lead is not in sight.
    assert kwargs["project"] == "GlobalTrade Co"


# ─── Closing a deal as lost has to say why ────────────────────────────────────

def test_losing_a_deal_without_a_reason_is_refused():
    """"Yutqazdik" with nothing beside it is a number nobody can act on, so the
    sheet's required dropdown is enforced here and not only in the app."""
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()),
        patch("apps.b2b.workspace.views.repo.set_lead_stage") as set_stage,
    ):
        response = _call(
            WorkspaceLeadStageView,
            factory.post("/leads/7/stage/", {"stage": LeadStage.LOST}, format="json"),
            OWNER,
            lead_id=7,
        )

    assert response.status_code == 400
    # The project's exception handler flattens field errors into `errors`.
    assert "lost_reason" in {e["field"] for e in response.data["errors"]}
    set_stage.assert_not_called()


def test_losing_a_deal_carries_the_reason_and_the_note_through():
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()),
        patch(
            "apps.b2b.workspace.views.repo.set_lead_stage",
            return_value=_lead(stage=LeadStage.LOST, status=LeadStatus.COMPLETED),
        ) as set_stage,
    ):
        response = _call(
            WorkspaceLeadStageView,
            factory.post(
                "/leads/7/stage/",
                {
                    "stage": LeadStage.LOST,
                    "lost_reason": "price",
                    "note": "Raqobatchi 15% arzon taklif qildi",
                },
                format="json",
            ),
            OWNER,
            lead_id=7,
        )

    assert response.status_code == 200
    kwargs = set_stage.call_args.kwargs
    assert kwargs["lost_reason"] == "price"
    assert kwargs["note"] == "Raqobatchi 15% arzon taklif qildi"


def test_a_won_deal_needs_no_reason():
    """Only the losing end is asked to explain itself."""
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()),
        patch(
            "apps.b2b.workspace.views.repo.set_lead_stage",
            return_value=_lead(stage=LeadStage.WON, status=LeadStatus.COMPLETED),
        ) as set_stage,
    ):
        response = _call(
            WorkspaceLeadStageView,
            factory.post("/leads/7/stage/", {"stage": LeadStage.WON}, format="json"),
            OWNER,
            lead_id=7,
        )

    assert response.status_code == 200
    assert set_stage.call_args.kwargs["lost_reason"] is None


def test_the_contract_stage_is_an_ordinary_move_and_does_not_close_the_lead():
    """"Shartnoma tuzish" sits between negotiation and won. It was added after
    the funnel shipped, so the check is that nothing treats it as an ending."""
    assert LeadStage.CONTRACT in LeadStage.CHOICES
    assert LeadStage.CONTRACT not in LeadStage.CLOSED
    assert LeadStage.ORDER.index(LeadStage.CONTRACT) == (
        LeadStage.ORDER.index(LeadStage.NEGOTIATION) + 1
    )


# ─── Creating a lead from the two-step sheet ──────────────────────────────────

def test_an_employee_may_record_their_own_deal_and_holds_it_from_the_start():
    """"Mas'ul menejer: Siz" — the salesperson entering a deal they are already
    working is not posting it to the board, so the manager gate does not apply
    and the lead is theirs on creation rather than sitting there to be claimed.
    """
    with (
        patch("apps.b2b.workspace.views.repo.create_lead", return_value=_lead()) as create,
        patch("apps.b2b.workspace.views.repo.list_employee_fcm_tokens") as tokens,
    ):
        response = _call(
            WorkspaceLeadListCreateView,
            factory.post(
                "/leads/",
                {
                    "contact_full_name": "Aziz Karimov",
                    "contact_phone": "+998 90 123 45 67",
                    "amount": "22000000",
                    "source": "call",
                },
                format="json",
            ),
            BYSTANDER,
        )

    assert response.status_code == 201
    assert create.call_args.kwargs["claim_for_author"] is True
    # A lead its author already holds is not up for grabs, so the board is not
    # told about it.
    tokens.assert_not_called()


def test_an_employee_cannot_post_a_lead_to_the_shared_board():
    """The manager gate is still there — it is about handing work to other
    people, which is the one thing an employee may not do."""
    with patch("apps.b2b.workspace.views.repo.create_lead") as create:
        response = _call(
            WorkspaceLeadListCreateView,
            factory.post(
                "/leads/",
                {
                    "contact_full_name": "Aziz Karimov",
                    "contact_phone": "+998901234567",
                    "assign_to_me": False,
                },
                format="json",
            ),
            BYSTANDER,
        )

    assert response.status_code == 403
    create.assert_not_called()


def test_a_lead_falls_back_to_the_contact_and_the_first_line_it_was_given():
    """The sheet asks for a customer and a price, not for a company name and a
    product — so the board's two columns are derived rather than demanded."""
    with (
        patch("apps.b2b.workspace.views.repo.create_lead", return_value=_lead()) as create,
        patch("apps.b2b.workspace.views.repo.list_employee_fcm_tokens", return_value=[]),
    ):
        response = _call(
            WorkspaceLeadListCreateView,
            factory.post(
                "/leads/",
                {
                    "contact_full_name": "Aziz Karimov",
                    "contact_phone": "+998901234567",
                    "items": [{"name": "CRM tizimi", "amount": "9000000"}],
                },
                format="json",
            ),
            MANAGER,
        )

    assert response.status_code == 201
    kwargs = create.call_args.kwargs
    assert kwargs["company_name"] == "Aziz Karimov"
    assert kwargs["product_name"] == "CRM tizimi"
    assert kwargs["quantity"] == 1


def test_a_lead_against_a_customer_from_another_company_is_not_found():
    """`customer_id` comes off the wire, so it is checked against the caller's
    own directory before anything is written against it."""
    with (
        patch("apps.b2b.workspace.views.repo.get_customer", return_value=None),
        patch("apps.b2b.workspace.views.repo.create_lead") as create,
    ):
        response = _call(
            WorkspaceLeadListCreateView,
            factory.post(
                "/leads/",
                {
                    "customer_id": 999,
                    "contact_full_name": "Aziz Karimov",
                    "contact_phone": "+998901234567",
                },
                format="json",
            ),
            MANAGER,
        )

    assert response.status_code == 404
    create.assert_not_called()


# ─── The customer directory ───────────────────────────────────────────────────

def test_anyone_may_search_the_directory_and_the_query_reaches_the_repository():
    """Step 1 of the sheet exists to stop the same buyer being typed in twice,
    which only works if the whole company can search."""
    with patch(
        "apps.b2b.workspace.views.repo.search_customers",
        return_value=[{
            "id": 3, "full_name": "Aziz Karimov", "phone": "+998901234567",
            "company_name": "GlobalTrade Co", "position": None, "deal_count": 2,
        }],
    ) as search:
        response = _call(
            WorkspaceCustomerSearchView,
            factory.get("/customers/", {"q": "90 123"}),
            BYSTANDER,
        )

    assert response.status_code == 200
    assert search.call_args.kwargs["query"] == "90 123"
    assert response.data["results"][0]["deal_count"] == 2
