"""The lead board's own rules.

Two things are worth pinning here and neither is visible from the SQL:

  * **Who may do what.** A lead's contact is withheld from the whole company
    once somebody claims it, and moving, commenting on or editing a lead is the
    claimant's alone — the owner and the managers raise leads, hand them out
    and watch the board. Getting this wrong leaks a customer to a competitor
    sitting in the same workspace.
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

from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.models import (
    LeadKind,
    LeadQuality,
    LeadStage,
    LeadStatus,
    PaymentMethod,
)
from apps.b2b.workspace.authentication import WorkspaceUser
from apps.b2b.workspace.views import (
    WorkspaceCustomerSearchView,
    WorkspaceLeadAssignView,
    WorkspaceLeadCommentView,
    WorkspaceLeadDetailView,
    WorkspaceLeadDueDateView,
    WorkspaceLeadListCreateView,
    WorkspaceLeadQualityView,
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
        "due_date": None,
        "quality": None,
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
    # Who removed it is recorded now: a soft delete keeps the row, and a
    # row that cannot say who deleted it cannot be judged before restoring.
    delete.assert_called_once_with(7, COMPANY_ID, actor_id=MANAGER_ID)


def test_the_claimant_cannot_delete_their_own_lead():
    """TZ §11: deleting a lead is the owner's or the administrator's call —
    working a deal, even claiming and closing it, is not the same right.
    (`OWNER` here is an `employee`-role fixture despite its name — it is the
    lead's claimant, not the workspace's actual owner; see `MANAGER`, which
    despite *its* name is the `owner`-role fixture used above.)"""
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()),
        patch("apps.b2b.workspace.views.repo.delete_lead") as delete,
    ):
        response = _call(
            WorkspaceLeadDetailView, factory.delete("/leads/7/"), OWNER, lead_id=7
        )

    assert response.status_code == 403
    delete.assert_not_called()


def test_a_bystander_cannot_delete_somebody_elses_lead():
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()),
        patch("apps.b2b.workspace.views.repo.delete_lead") as delete,
    ):
        response = _call(
            WorkspaceLeadDetailView, factory.delete("/leads/7/"), BYSTANDER, lead_id=7
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




def test_a_move_can_carry_a_document():
    """The signed contract behind "Yutdik" goes with the move.

    Stored through the same door as every other upload — the quota is one SUM
    over that table — and handed to the repository as an id, because only the
    repository knows which history row the move wrote and that is what the
    file has to hang off.
    """
    contract = SimpleUploadedFile("shartnoma.pdf", b"%PDF-1.4 ...", content_type="application/pdf")
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()),
        patch(
            "apps.b2b.workspace.views.store_upload",
            return_value=({"id": 91, "name": "shartnoma.pdf"}, None),
        ) as upload,
        patch(
            "apps.b2b.workspace.views.repo.set_lead_stage",
            return_value=_lead(stage=LeadStage.WON, status=LeadStatus.COMPLETED),
        ) as set_stage,
    ):
        response = _call(
            WorkspaceLeadStageView,
            factory.post(
                "/leads/7/stage/",
                {"stage": LeadStage.WON, "file": contract},
                format="multipart",
            ),
            OWNER,
            lead_id=7,
        )

    assert response.status_code == 200
    assert upload.call_args.kwargs["kind"] == "lead"
    assert set_stage.call_args.kwargs["attachment_file_id"] == 91


def test_a_move_that_goes_nowhere_stores_nothing():
    """A lead posted to the stage it is already on writes no history row, so a
    file sent with it would be bytes against the quota that no screen can show
    and nobody can delete."""
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()),
        patch("apps.b2b.workspace.views.store_upload") as upload,
        patch("apps.b2b.workspace.views.repo.set_lead_stage") as set_stage,
    ):
        response = _call(
            WorkspaceLeadStageView,
            factory.post(
                "/leads/7/stage/",
                {
                    "stage": LeadStage.PROPOSAL,  # where _lead() already is
                    "file": SimpleUploadedFile("x.pdf", b"..."),
                },
                format="multipart",
            ),
            OWNER,
            lead_id=7,
        )

    assert response.status_code == 200
    upload.assert_not_called()
    set_stage.assert_not_called()


def test_a_document_filed_with_a_move_comes_back_on_the_history_row():
    """The feed shows the file beside the event it belongs to, so the row
    carries it — nested, like a chat attachment, not flattened."""
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()),
        patch("apps.b2b.workspace.views.repo.list_lead_items", return_value=[]),
        patch(
            "apps.b2b.workspace.views.repo.list_lead_activity",
            return_value=[
                {
                    "id": 4,
                    "kind": "stage",
                    "text": "proposal>won",
                    "attachment_id": 91,
                    "attachment_name": "shartnoma.pdf",
                    "attachment_path": "b2b/workspace/55/lead/shartnoma.pdf",
                    "attachment_size": 2048,
                    "attachment_content_type": "application/pdf",
                },
                {"id": 3, "kind": "comment", "text": "Qo’ng’iroq qildim"},
            ],
        ),
        patch("apps.b2b.workspace.views.repo.list_lead_tasks", return_value=[]),
    ):
        response = _call(
            WorkspaceLeadDetailView, factory.get("/leads/7/"), OWNER, lead_id=7
        )

    assert response.status_code == 200
    moved, commented = response.data["activity"]
    assert moved["attachment"]["name"] == "shartnoma.pdf"
    assert moved["attachment"]["size"] == 2048
    assert moved["attachment"]["url"]
    # The flat columns the join produced do not leak into the payload.
    assert "attachment_path" not in moved
    # And a row with nothing filed against it says so rather than leaving the
    # key out, which a client would have to special-case.
    assert commented["attachment"] is None

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


# ─── The deal's deadline ──────────────────────────────────────────────────────

def test_the_owner_sets_a_deadline():
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()),
        patch(
            "apps.b2b.workspace.views.repo.set_lead_due_date",
            return_value=_lead(due_date="2026-09-15T00:00:00Z"),
        ) as set_due,
    ):
        response = _call(
            WorkspaceLeadDueDateView,
            factory.post(
                "/leads/7/due-date/",
                {"due_date": "2026-09-15T00:00:00Z"},
                format="json",
            ),
            OWNER,
            lead_id=7,
        )

    assert response.status_code == 200
    set_due.assert_called_once()
    assert set_due.call_args.kwargs["due_date"] is not None


def test_a_manager_may_set_a_deadline_over_the_owners_head():
    """The one write on a deal that is not the claimant's alone.

    A deadline is as often the manager's call as the salesperson's — it is the
    manager who knows the quarter ends on the 30th.
    """
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()),
        patch(
            "apps.b2b.workspace.views.repo.set_lead_due_date", return_value=_lead()
        ),
    ):
        response = _call(
            WorkspaceLeadDueDateView,
            factory.post(
                "/leads/7/due-date/",
                {"due_date": "2026-09-15T00:00:00Z"},
                format="json",
            ),
            MANAGER,
            lead_id=7,
        )

    assert response.status_code == 200


def test_a_bystander_cannot_set_a_deadline():
    with patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()):
        response = _call(
            WorkspaceLeadDueDateView,
            factory.post(
                "/leads/7/due-date/",
                {"due_date": "2026-09-15T00:00:00Z"},
                format="json",
            ),
            BYSTANDER,
            lead_id=7,
        )

    assert response.status_code == 403


def test_a_null_deadline_clears_it():
    """Explicitly sayable, and not the same as omitting the field."""
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()),
        patch(
            "apps.b2b.workspace.views.repo.set_lead_due_date", return_value=_lead()
        ) as set_due,
    ):
        response = _call(
            WorkspaceLeadDueDateView,
            factory.post("/leads/7/due-date/", {"due_date": None}, format="json"),
            OWNER,
            lead_id=7,
        )

    assert response.status_code == 200
    assert set_due.call_args.kwargs["due_date"] is None


def test_a_deadline_needs_a_date():
    """A bare POST is a malformed request, not a way to clear the date."""
    with patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()):
        response = _call(
            WorkspaceLeadDueDateView,
            factory.post("/leads/7/due-date/", {}, format="json"),
            OWNER,
            lead_id=7,
        )

    assert response.status_code == 400


def test_a_closed_lead_takes_no_deadline():
    """A clock nothing can run down."""
    closed = _lead(status=LeadStatus.COMPLETED, stage=LeadStage.WON)
    with patch("apps.b2b.workspace.views.repo.get_lead", return_value=closed):
        response = _call(
            WorkspaceLeadDueDateView,
            factory.post(
                "/leads/7/due-date/",
                {"due_date": "2026-09-15T00:00:00Z"},
                format="json",
            ),
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


def test_an_archived_deal_needs_no_reason_either():
    """Archive closes the lead the same way won and lost do, but asks
    nothing — there is no verdict to explain, only a deal taken off the
    board."""
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()),
        patch(
            "apps.b2b.workspace.views.repo.set_lead_stage",
            return_value=_lead(
                stage=LeadStage.ARCHIVED, status=LeadStatus.COMPLETED
            ),
        ) as set_stage,
    ):
        response = _call(
            WorkspaceLeadStageView,
            factory.post(
                "/leads/7/stage/", {"stage": LeadStage.ARCHIVED}, format="json"
            ),
            OWNER,
            lead_id=7,
        )

    assert response.status_code == 200
    assert response.data["stage"] == LeadStage.ARCHIVED
    assert response.data["status"] == LeadStatus.COMPLETED
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

def test_a_manager_records_a_deal_they_are_already_working():
    """"Mas'ul menejer: Siz" — a manager entering a deal of their own is not
    posting it to the board, so the lead is theirs on creation rather than
    sitting there to be claimed."""
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
            MANAGER,
        )

    assert response.status_code == 201
    assert create.call_args.kwargs["claim_for_author"] is True
    # A lead its author already holds is not up for grabs, so the board is not
    # told about it.
    tokens.assert_not_called()


@pytest.mark.parametrize("assign_to_me", [True, False])
def test_an_employee_cannot_raise_a_lead_at_all(assign_to_me):
    """Neither form of it: not posting one to the board, and not recording one
    as already theirs. An employee works the deals they are handed."""
    with patch("apps.b2b.workspace.views.repo.create_lead") as create:
        response = _call(
            WorkspaceLeadListCreateView,
            factory.post(
                "/leads/",
                {
                    "contact_full_name": "Aziz Karimov",
                    "contact_phone": "+998901234567",
                    "assign_to_me": assign_to_me,
                },
                format="json",
            ),
            BYSTANDER,
        )

    assert response.status_code == 403
    create.assert_not_called()


# ─── Management watches the board; the claimant works the lead ────────────────

def test_a_manager_does_not_move_a_lead_somebody_else_is_working():
    """The whole point of the rule: an owner or a manager hands the deal out
    and then watches it. Moving it over the claimant's head would put a stage
    in the history that the person running the deal did not choose."""
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()),
        patch("apps.b2b.workspace.views.repo.set_lead_stage") as move,
    ):
        response = _call(
            WorkspaceLeadStageView,
            factory.post("/stage/", {"stage": LeadStage.NEGOTIATION}, format="json"),
            MANAGER,
            lead_id=7,
        )

    assert response.status_code == 403
    move.assert_not_called()


def test_a_manager_does_not_comment_on_somebody_elses_lead():
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()),
        patch("apps.b2b.workspace.views.repo.add_lead_comment") as comment,
    ):
        response = _call(
            WorkspaceLeadCommentView,
            factory.post("/comments/", {"text": "Qo’ng’iroq qildim"}, format="json"),
            MANAGER,
            lead_id=7,
        )

    assert response.status_code == 403
    comment.assert_not_called()


def test_a_manager_who_took_the_lead_himself_works_it_like_anyone_else():
    """The claimant is the claimant, whatever their role."""
    lead = _lead(claimed_by_id=MANAGER_ID)
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=lead),
        patch("apps.b2b.workspace.views.repo.set_lead_stage", return_value=lead) as move,
    ):
        response = _call(
            WorkspaceLeadStageView,
            factory.post("/stage/", {"stage": LeadStage.NEGOTIATION}, format="json"),
            MANAGER,
            lead_id=7,
        )

    assert response.status_code == 200
    assert move.call_args.kwargs["stage"] == LeadStage.NEGOTIATION


def test_the_board_tells_each_viewer_whether_the_lead_is_theirs_to_work():
    """`can_work` is what the app hides its write controls on, so a manager
    reading a colleague's lead must get it false while still seeing the row."""
    lead = _lead()
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=lead),
        patch("apps.b2b.workspace.views.repo.list_lead_items", return_value=[]),
        patch("apps.b2b.workspace.views.repo.list_lead_activity", return_value=[]),
        patch("apps.b2b.workspace.views.repo.list_lead_tasks", return_value=[]),
    ):
        for user, expected in ((OWNER, True), (MANAGER, False), (BYSTANDER, False)):
            response = _call(
                WorkspaceLeadDetailView, factory.get("/leads/7/"), user, lead_id=7
            )
            assert response.status_code == 200
            assert response.data["can_work"] is expected
            assert response.data["can_change_stage"] is expected


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


# ─── Was the enquiry worth working ────────────────────────────────────────────

def test_the_owner_marks_a_lead_good():
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()),
        patch(
            "apps.b2b.workspace.views.repo.set_lead_quality",
            return_value=_lead(quality=LeadQuality.GOOD),
        ) as set_quality,
    ):
        response = _call(
            WorkspaceLeadQualityView,
            factory.post(
                "/leads/7/quality/", {"quality": LeadQuality.GOOD}, format="json"
            ),
            OWNER,
            lead_id=7,
        )

    assert response.status_code == 200
    assert response.data["quality"] == LeadQuality.GOOD
    assert set_quality.call_args.kwargs["quality"] == LeadQuality.GOOD


def test_a_manager_may_mark_a_lead_over_the_owners_head():
    """A lead written off as noise too quickly is the manager's to correct."""
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()),
        patch(
            "apps.b2b.workspace.views.repo.set_lead_quality",
            return_value=_lead(quality=LeadQuality.BAD),
        ),
    ):
        response = _call(
            WorkspaceLeadQualityView,
            factory.post(
                "/leads/7/quality/", {"quality": LeadQuality.BAD}, format="json"
            ),
            MANAGER,
            lead_id=7,
        )

    assert response.status_code == 200


def test_a_bystander_cannot_mark_a_lead():
    with patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()):
        response = _call(
            WorkspaceLeadQualityView,
            factory.post(
                "/leads/7/quality/", {"quality": LeadQuality.BAD}, format="json"
            ),
            BYSTANDER,
            lead_id=7,
        )

    assert response.status_code == 403


def test_a_null_quality_takes_the_mark_off():
    """Sayable, and not what an omitted field does — see the deadline above."""
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()),
        patch(
            "apps.b2b.workspace.views.repo.set_lead_quality", return_value=_lead()
        ) as set_quality,
    ):
        response = _call(
            WorkspaceLeadQualityView,
            factory.post("/leads/7/quality/", {"quality": None}, format="json"),
            OWNER,
            lead_id=7,
        )

    assert response.status_code == 200
    assert set_quality.call_args.kwargs["quality"] is None


def test_a_quality_has_to_be_one_of_the_two():
    with patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()):
        response = _call(
            WorkspaceLeadQualityView,
            factory.post("/leads/7/quality/", {"quality": "maybe"}, format="json"),
            OWNER,
            lead_id=7,
        )

    assert response.status_code == 400


def test_a_bare_post_does_not_clear_the_mark():
    with patch("apps.b2b.workspace.views.repo.get_lead", return_value=_lead()):
        response = _call(
            WorkspaceLeadQualityView,
            factory.post("/leads/7/quality/", {}, format="json"),
            OWNER,
            lead_id=7,
        )

    assert response.status_code == 400


def test_a_closed_lead_can_still_be_marked():
    """Unlike the deadline, deliberately.

    "That enquiry was never real" is a judgement usually made about a deal that
    has already been lost. Refusing it on a closed lead would put the mark out
    of reach on exactly the leads it exists for.
    """
    closed = _lead(status=LeadStatus.COMPLETED, stage=LeadStage.LOST)
    with (
        patch("apps.b2b.workspace.views.repo.get_lead", return_value=closed),
        patch(
            "apps.b2b.workspace.views.repo.set_lead_quality",
            return_value=_lead(quality=LeadQuality.BAD),
        ),
    ):
        response = _call(
            WorkspaceLeadQualityView,
            factory.post(
                "/leads/7/quality/", {"quality": LeadQuality.BAD}, format="json"
            ),
            OWNER,
            lead_id=7,
        )

    assert response.status_code == 200


def test_the_board_can_be_narrowed_to_one_quality():
    with (
        patch("apps.b2b.workspace.views.repo.list_leads", return_value=[]) as listed,
        patch("apps.b2b.workspace.views.repo.count_lead_items", return_value={}),
        patch("apps.b2b.workspace.views.repo.count_lead_tasks", return_value={}),
    ):
        response = _call(
            WorkspaceLeadListCreateView,
            factory.get("/leads/", {"quality": LeadQuality.BAD}),
            MANAGER,
        )

    assert response.status_code == 200
    assert listed.call_args.kwargs["quality"] == LeadQuality.BAD


def test_the_unmarked_filter_asks_for_a_null_rather_than_a_value():
    """`unmarked` is not a quality, so it cannot be a parameter.

    Sent as one it would look for the literal string in a column that holds
    `good`, `bad` or nothing, and quietly answer with an empty board — which is
    the one wrong answer a filter can give that nobody reports as a bug.
    """
    from apps.b2b.workspace import repository

    with patch.object(repository, "fetch_all", return_value=[]) as fetched:
        repository.list_leads(COMPANY_ID, quality=repository.LEAD_QUALITY_UNMARKED)

    sql, params = fetched.call_args.args
    assert "quality IS NULL" in sql
    # The company and the kind the funnel always asks for, and nothing else:
    # `unmarked` itself must not have become a bound parameter.
    assert params == [COMPANY_ID, LeadKind.LEAD]
    assert repository.LEAD_QUALITY_UNMARKED not in params


# ─── Tezkor savdo — a sale recorded after the fact ────────────────────────────

def test_a_quick_sale_is_written_closed_and_kept_off_the_board():
    """The point of the second entry on the "+" menu.

    A sale that has already happened has no step left for anyone to take, so
    it is born won, held by whoever recorded it and completed — which is what
    puts it in the CRM card and every sales total without it ever appearing on
    the funnel as work.
    """
    from apps.b2b.workspace import repository

    inserted = {}

    def _capture(sql, params):
        if "INSERT INTO b2b_workspace_lead" in sql:
            inserted["sql"], inserted["params"] = sql, params
            return {"id": 91}
        return None

    with (
        patch.object(repository, "fetch_one", side_effect=_capture),
        patch.object(repository, "upsert_customer", return_value=None),
        patch.object(repository, "add_lead_activity", return_value={"id": 1}),
        patch.object(repository, "get_lead", return_value=_lead()),
    ):
        repository.create_lead(
            company_id=COMPANY_ID,
            author_id=MANAGER_ID,
            company_name="GlobalTrade Co",
            contact_full_name="Aziz Karimov",
            contact_phone="+998901234567",
            product_name="CRM tizimi",
            quantity=1,
            amount=22_000_000,
            kind=LeadKind.QUICK_SALE,
            payment_method=PaymentMethod.CARD,
        )

    params = inserted["params"]
    assert LeadStatus.COMPLETED in params
    assert LeadStage.WON in params
    assert LeadKind.QUICK_SALE in params
    assert PaymentMethod.CARD in params
    # Held by its author whatever the caller said about assignment: there is
    # nothing on it for a colleague to claim.
    assert MANAGER_ID in params


def test_a_quick_sale_ignores_assign_to_me_and_tells_nobody():
    """A lead posted to the board pushes the whole company. A sale that is
    already done is news to no one, so the notification path stays shut even
    when the sheet asked for the lead to be posted."""
    with (
        patch(
            "apps.b2b.workspace.views.repo.create_lead",
            return_value=_lead(kind=LeadKind.QUICK_SALE),
        ) as create,
        patch("apps.b2b.workspace.views.repo.list_company_recipients") as recipients,
    ):
        response = _call(
            WorkspaceLeadListCreateView,
            factory.post(
                "/leads/",
                {
                    "contact_full_name": "Aziz Karimov",
                    "contact_phone": "+998 90 123 45 67",
                    "amount": "22000000",
                    "kind": LeadKind.QUICK_SALE,
                    "payment_method": PaymentMethod.CASH,
                    "assign_to_me": False,
                },
                format="json",
            ),
            MANAGER,
        )

    assert response.status_code == 201
    assert create.call_args.kwargs["kind"] == LeadKind.QUICK_SALE
    assert create.call_args.kwargs["payment_method"] == PaymentMethod.CASH
    assert create.call_args.kwargs["claim_for_author"] is True
    recipients.assert_not_called()


def test_a_quick_sale_must_say_how_it_was_paid_for():
    """The one figure a sales report cannot reconstruct afterwards."""
    with patch("apps.b2b.workspace.views.repo.create_lead") as create:
        response = _call(
            WorkspaceLeadListCreateView,
            factory.post(
                "/leads/",
                {
                    "contact_full_name": "Aziz Karimov",
                    "contact_phone": "+998901234567",
                    "kind": LeadKind.QUICK_SALE,
                },
                format="json",
            ),
            MANAGER,
        )

    assert response.status_code == 400
    assert "payment_method" in str(response.data)
    create.assert_not_called()


def test_an_ordinary_lead_cannot_carry_a_payment_method():
    """A lead is an intention and has nothing to pay with yet — a sheet that
    sent one anyway must not leave a paid-looking row on the board."""
    with patch(
        "apps.b2b.workspace.views.repo.create_lead", return_value=_lead()
    ) as create:
        response = _call(
            WorkspaceLeadListCreateView,
            factory.post(
                "/leads/",
                {
                    "contact_full_name": "Aziz Karimov",
                    "contact_phone": "+998901234567",
                    "payment_method": PaymentMethod.CASH,
                },
                format="json",
            ),
            MANAGER,
        )

    assert response.status_code == 201
    assert create.call_args.kwargs["kind"] == LeadKind.LEAD
    assert create.call_args.kwargs["payment_method"] is None


def test_the_board_asks_for_leads_and_the_quick_sale_list_for_sales():
    """Two lists cut from one table. The funnel never passes `kind`, so the
    default is the guarantee — a quick sale cannot reach the board through a
    caller that simply forgot about it."""
    from apps.b2b.workspace import repository

    with patch.object(repository, "fetch_all", return_value=[]) as fetched:
        repository.list_leads(COMPANY_ID)
    assert fetched.call_args.args[1] == [COMPANY_ID, LeadKind.LEAD]

    with patch.object(repository, "fetch_all", return_value=[]) as fetched:
        repository.list_leads(COMPANY_ID, kind=LeadKind.QUICK_SALE)
    assert fetched.call_args.args[1] == [COMPANY_ID, LeadKind.QUICK_SALE]

    # And the reader that counts deals rather than working them gets both.
    with patch.object(repository, "fetch_all", return_value=[]) as fetched:
        repository.list_leads(COMPANY_ID, kind=repository.LEAD_KIND_ANY)
    sql, params = fetched.call_args.args
    assert "kind = %s" not in sql
    assert params == [COMPANY_ID]
