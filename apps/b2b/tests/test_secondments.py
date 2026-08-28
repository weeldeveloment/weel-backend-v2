"""Lending somebody to another workspace.

This is the one feature in the schema that crosses the `company_id` boundary
on purpose, so the checks here are mostly about where it stops:

  * you may only ask people who share an owner with you;
  * only management may ask at all;
  * only the person asked may answer, and only once;
  * a guest's reach is their role *narrowed* by the modules they were granted,
    never widened;
  * a secondment that has run out is refused on the next request, without
    waiting for the sweep that tidies the row away.

Run against mocked repository calls — the rules are in the views and in
`secondment.py`, not in the database.
"""
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from django.utils import timezone
from rest_framework import exceptions
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.models import EmployeeRole
from apps.b2b.workspace.authentication import WorkspaceUser, resolve_membership
from apps.b2b.workspace.roles import capabilities_for
from apps.b2b.workspace.secondment import Membership, Module, RequestRole, RequestStatus
from apps.b2b.workspace.secondment_views import (
    WorkspaceOrgPeopleView,
    WorkspaceRequestListCreateView,
    WorkspaceRequestRespondView,
    WorkspaceSwitchView,
)

HOME_COMPANY = 10
HOST_COMPANY = 20
LIDER_ID = 1
AZIZ_ID = 2
AZIZ_GUEST_ID = 99

factory = APIRequestFactory()


def _user(role: str, employee_id: int, company_id: int, membership=None) -> WorkspaceUser:
    return WorkspaceUser(
        {
            "id": employee_id,
            "company_id": company_id,
            "role": role,
            "full_name": "Test Person",
            "phone": "+998900000000",
        },
        membership,
    )


LIDER = _user(EmployeeRole.LIDER, LIDER_ID, HOST_COMPANY)
AZIZ = _user(EmployeeRole.EMPLOYEE, AZIZ_ID, HOME_COMPANY)


def _call(view_class, request, user, **kwargs):
    force_authenticate(request, user=user)
    return view_class.as_view()(request, **kwargs)


def _person(employee_id=AZIZ_ID, company_id=HOME_COMPANY):
    return {
        "id": employee_id,
        "full_name": "Aziz Karimov",
        "username": "aziz",
        "position": "Sotuv menejeri",
        "phone": "+998901234567",
        "photo": None,
        "role": EmployeeRole.EMPLOYEE,
        "company_id": company_id,
        "company_name": "Toshkent filiali",
    }


def _ask(**overrides):
    ask = {
        "id": 7,
        "company_id": HOST_COMPANY,
        "from_employee_id": LIDER_ID,
        "to_employee_id": AZIZ_ID,
        "message": "Bizda leadlar ko’p, yordam kerak",
        "role": RequestRole.MANAGER,
        "modules": [Module.CHAT, Module.SALES],
        "starts_at": None,
        "ends_at": timezone.now() + timedelta(days=14),
        "status": RequestStatus.PENDING,
        "decline_reason": None,
        "responded_at": None,
        "created_at": timezone.now(),
        "company_name": "Samarqand filiali",
    }
    ask.update(overrides)
    return ask


# ─── Who may ask, and whom ────────────────────────────────────────────────────

def test_an_employee_cannot_ask_another_workspace_for_help():
    """The same bar as posting a lead: this lets an outsider in and hands them
    a role, which is not an employee's call to make."""
    response = _call(
        WorkspaceRequestListCreateView,
        factory.post("/requests/", {"to_employee_id": AZIZ_ID, "role": "manager"}, format="json"),
        AZIZ,
    )
    assert response.status_code == 403


def test_the_picker_only_reaches_the_org_and_never_its_own_roster():
    with patch(
        "apps.b2b.workspace.secondment_views.srepo.org_id_for_company", return_value=5
    ), patch(
        "apps.b2b.workspace.secondment_views.srepo.search_org_people",
        return_value=[_person()],
    ) as search:
        response = _call(
            WorkspaceOrgPeopleView, factory.get("/org/people/?search=aziz"), LIDER
        )

    assert response.status_code == 200
    assert search.call_args.args[0] == 5
    # Its own people are already on `/team/`; this screen exists to reach past
    # them, and offering them here would let a workspace second its own staff.
    assert search.call_args.kwargs["exclude_company_id"] == HOST_COMPANY


def test_somebody_outside_the_org_cannot_be_asked():
    with patch(
        "apps.b2b.workspace.secondment_views.srepo.org_id_for_company", return_value=5
    ), patch(
        "apps.b2b.workspace.secondment_views.srepo.search_org_people", return_value=[]
    ):
        response = _call(
            WorkspaceRequestListCreateView,
            factory.post(
                "/requests/",
                {"to_employee_id": 4242, "role": "manager"},
                format="json",
            ),
            LIDER,
        )

    assert response.status_code == 404


def test_asking_twice_returns_the_request_that_already_exists():
    """A button that felt slow is tapped twice, and the second tap means the
    same as the first — an error would be the app's problem, not the user's."""
    with patch(
        "apps.b2b.workspace.secondment_views.srepo.org_id_for_company", return_value=5
    ), patch(
        "apps.b2b.workspace.secondment_views.srepo.search_org_people",
        return_value=[_person()],
    ), patch(
        "apps.b2b.workspace.secondment_views.srepo.pending_request_between",
        return_value=_ask(),
    ), patch(
        "apps.b2b.workspace.secondment_views.srepo.create_request"
    ) as create:
        response = _call(
            WorkspaceRequestListCreateView,
            factory.post(
                "/requests/",
                {"to_employee_id": AZIZ_ID, "role": "manager"},
                format="json",
            ),
            LIDER,
        )

    assert response.status_code == 200
    create.assert_not_called()


def test_a_request_carries_its_modules_and_its_end_date():
    ends = timezone.now() + timedelta(days=30)
    with patch(
        "apps.b2b.workspace.secondment_views.srepo.org_id_for_company", return_value=5
    ), patch(
        "apps.b2b.workspace.secondment_views.srepo.search_org_people",
        return_value=[_person()],
    ), patch(
        "apps.b2b.workspace.secondment_views.srepo.pending_request_between",
        return_value=None,
    ), patch(
        "apps.b2b.workspace.secondment_views.srepo.create_request", return_value=_ask()
    ) as create, patch(
        "apps.b2b.workspace.secondment_views._queue"
    ):
        response = _call(
            WorkspaceRequestListCreateView,
            factory.post(
                "/requests/",
                {
                    "to_employee_id": AZIZ_ID,
                    "role": "manager",
                    "message": "Bizda leadlar ko’p",
                    "modules": ["chat", "savdo", "fayllar"],
                    "ends_at": ends.isoformat(),
                },
                format="json",
            ),
            LIDER,
        )

    assert response.status_code == 201
    assert create.call_args.kwargs["ends_at"] is not None
    # Every module the screen offers reaches storage, files included — see
    # `test_files_can_be_granted_through_a_secondment`.
    assert Module.clean(create.call_args.kwargs["modules"]) == [
        "chat",
        "savdo",
        "fayllar",
    ]


def test_an_end_date_in_the_past_is_refused():
    with patch(
        "apps.b2b.workspace.secondment_views.srepo.org_id_for_company", return_value=5
    ), patch(
        "apps.b2b.workspace.secondment_views.srepo.search_org_people",
        return_value=[_person()],
    ):
        response = _call(
            WorkspaceRequestListCreateView,
            factory.post(
                "/requests/",
                {
                    "to_employee_id": AZIZ_ID,
                    "role": "manager",
                    "ends_at": (timezone.now() - timedelta(days=1)).isoformat(),
                },
                format="json",
            ),
            LIDER,
        )

    assert response.status_code == 400


# ─── Answering ────────────────────────────────────────────────────────────────

def test_only_the_person_asked_may_accept():
    stranger = _user(EmployeeRole.EMPLOYEE, 555, HOME_COMPANY)
    with patch(
        "apps.b2b.workspace.secondment_views.srepo.get_request", return_value=_ask()
    ):
        response = _call(
            WorkspaceRequestRespondView,
            factory.post("/requests/7/accept/"),
            stranger,
            request_id=7,
            action="accept",
        )

    assert response.status_code == 403


def test_accepting_twice_is_refused_by_the_claim_and_creates_nothing():
    """`close_request` only matches a pending row, so the loser of the race
    learns it lost *before* any guest row exists."""
    with patch(
        "apps.b2b.workspace.secondment_views.srepo.get_request", return_value=_ask()
    ), patch(
        "apps.b2b.workspace.secondment_views.srepo.close_request", return_value=0
    ), patch(
        "apps.b2b.workspace.secondment_views.srepo.create_guest_employee"
    ) as guest:
        response = _call(
            WorkspaceRequestRespondView,
            factory.post("/requests/7/accept/"),
            AZIZ,
            request_id=7,
            action="accept",
        )

    assert response.status_code == 409
    guest.assert_not_called()


def test_accepting_creates_a_guest_row_and_a_membership():
    with patch(
        "apps.b2b.workspace.secondment_views.srepo.get_request", return_value=_ask()
    ), patch(
        "apps.b2b.workspace.secondment_views.srepo.close_request", return_value=1
    ), patch(
        "apps.b2b.workspace.secondment_views.repo.get_workspace_employee",
        return_value={"id": AZIZ_ID, "company_id": HOME_COMPANY, "full_name": "Aziz"},
    ), patch(
        "apps.b2b.workspace.secondment_views.srepo.create_guest_employee",
        return_value={"id": AZIZ_GUEST_ID},
    ) as guest, patch(
        "apps.b2b.workspace.secondment_views.srepo.create_membership"
    ) as membership, patch(
        "apps.b2b.workspace.secondment_views._queue"
    ):
        response = _call(
            WorkspaceRequestRespondView,
            factory.post("/requests/7/accept/"),
            AZIZ,
            request_id=7,
            action="accept",
        )

    assert response.status_code == 200
    # The guest row is created in the workspace that asked, not the one that
    # lent — that is what makes every existing company-scoped query work.
    assert guest.call_args.kwargs["company_id"] == HOST_COMPANY
    assert membership.call_args.kwargs["employee_id"] == AZIZ_GUEST_ID
    assert membership.call_args.kwargs["home_employee_id"] == AZIZ_ID
    # No start named means it begins now: they said yes and the workspace
    # asking is short-handed today.
    assert membership.call_args.kwargs["starts_at"] is not None


def test_declining_requires_a_reason():
    """Declining rather than ignoring is only worth anything if the workspace
    that asked learns something."""
    with patch(
        "apps.b2b.workspace.secondment_views.srepo.get_request", return_value=_ask()
    ):
        response = _call(
            WorkspaceRequestRespondView,
            factory.post("/requests/7/decline/", {"reason": "   "}, format="json"),
            AZIZ,
            request_id=7,
            action="decline",
        )

    assert response.status_code == 400


def test_a_decline_stores_the_reason():
    with patch(
        "apps.b2b.workspace.secondment_views.srepo.get_request", return_value=_ask()
    ), patch(
        "apps.b2b.workspace.secondment_views.srepo.close_request", return_value=1
    ) as close, patch(
        "apps.b2b.workspace.secondment_views._queue"
    ):
        _call(
            WorkspaceRequestRespondView,
            factory.post(
                "/requests/7/decline/",
                {"reason": "Ta’tildaman, keyingi hafta"},
                format="json",
            ),
            AZIZ,
            request_id=7,
            action="decline",
        )

    assert close.call_args.kwargs["status"] == RequestStatus.DECLINED
    assert close.call_args.kwargs["decline_reason"] == "Ta’tildaman, keyingi hafta"


def test_only_the_workspace_that_sent_it_may_withdraw_it():
    with patch(
        "apps.b2b.workspace.secondment_views.srepo.get_request", return_value=_ask()
    ):
        response = _call(
            WorkspaceRequestRespondView,
            factory.post("/requests/7/cancel/"),
            AZIZ,
            request_id=7,
            action="cancel",
        )

    assert response.status_code == 403


# ─── What a guest may reach ───────────────────────────────────────────────────

def test_modules_narrow_a_role_and_never_widen_it():
    granted = capabilities_for(EmployeeRole.PERFORMER, [Module.SALES])

    # Kept: the sales board is what they were lent for.
    assert granted["can_post_lead"] is True
    # Withheld: the same manager role would carry these, and the secondment
    # did not include them.
    assert granted["can_create_task"] is False
    assert granted["can_chat"] is False
    assert granted["can_create_event"] is False


def test_a_grant_cannot_hand_out_what_the_role_never_had():
    granted = capabilities_for(EmployeeRole.EMPLOYEE, [Module.SALES, Module.TASKS])

    # An employee cannot post a lead, and being lent the sales board does not
    # promote them.
    assert granted["can_post_lead"] is False
    assert granted["can_create_task"] is False
    # What an employee could always do stays.
    assert granted["can_update_task_status"] is True


def test_a_permanent_employee_is_answered_by_their_role_alone():
    assert capabilities_for(EmployeeRole.PERFORMER) == capabilities_for(
        EmployeeRole.PERFORMER, None
    )


def test_files_can_be_granted_through_a_secondment():
    """It used to be stripped here, on the grounds that files are shared per
    folder rather than per person. But every file view declares
    `required_module = Module.FILES`, so this list is what decides whether the
    Fayllar tab opens at all — dropping it lent somebody the files and then
    answered 403 on every one of those endpoints."""
    assert Module.clean(["chat", "fayllar"]) == ["chat", "fayllar"]


def test_modules_are_stored_in_a_fixed_order():
    """Two identical grants should compare equal however they were typed."""
    assert Module.clean(["taqvim", "chat"]) == Module.clean(["chat", "taqvim"])


# ─── The window ───────────────────────────────────────────────────────────────

def _membership_row(**overrides):
    row = {
        "employee_id": AZIZ_GUEST_ID,
        "company_id": HOST_COMPANY,
        "home_employee_id": AZIZ_ID,
        "role": RequestRole.MANAGER,
        "modules": [Module.SALES],
        "starts_at": timezone.now() - timedelta(days=1),
        "ends_at": timezone.now() + timedelta(days=1),
        "is_active": True,
    }
    row.update(overrides)
    return row


def test_a_secondment_inside_its_window_is_live():
    assert Membership.from_row(_membership_row()).is_live is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"ends_at": timezone.now() - timedelta(minutes=1)},
        {"starts_at": timezone.now() + timedelta(days=1)},
        {"is_active": False},
    ],
    ids=["ended", "not-started", "closed"],
)
def test_a_secondment_outside_its_window_is_not(overrides):
    assert Membership.from_row(_membership_row(**overrides)).is_live is False


def test_an_expired_guest_is_refused_on_the_next_request():
    """The token is still cryptographically valid and the hourly sweep may not
    have run. The window is what decides, on every request."""
    with patch(
        "apps.b2b.workspace.secondment_repository.membership_for_employee",
        return_value=_membership_row(ends_at=timezone.now() - timedelta(minutes=1)),
    ):
        with pytest.raises(exceptions.AuthenticationFailed):
            resolve_membership({"id": AZIZ_GUEST_ID, "is_guest": True})


def test_a_guest_row_with_no_secondment_behind_it_is_refused():
    with patch(
        "apps.b2b.workspace.secondment_repository.membership_for_employee",
        return_value=None,
    ):
        with pytest.raises(exceptions.AuthenticationFailed):
            resolve_membership({"id": AZIZ_GUEST_ID, "is_guest": True})


def test_a_permanent_employee_needs_no_lookup_at_all():
    """The ordinary case, on every single authenticated request."""
    with patch(
        "apps.b2b.workspace.secondment_repository.membership_for_employee"
    ) as lookup:
        assert resolve_membership({"id": AZIZ_ID, "is_guest": False}) is None

    lookup.assert_not_called()


# ─── Switching between workspaces ─────────────────────────────────────────────

def test_switching_to_a_workspace_you_were_never_lent_to_is_refused():
    with patch(
        "apps.b2b.workspace.secondment_views.srepo.list_memberships_for_person",
        return_value=[],
    ):
        response = _call(
            WorkspaceSwitchView,
            factory.post("/switch/", {"employee_id": 4242}, format="json"),
            AZIZ,
        )

    assert response.status_code == 403


def test_the_switcher_lists_home_first_and_then_the_secondments():
    with patch(
        "apps.b2b.workspace.secondment_views.repo.get_workspace_employee",
        return_value={"id": AZIZ_ID, "company_id": HOME_COMPANY, "role": "employee"},
    ), patch(
        "apps.b2b.workspace.secondment_views.get_company",
        return_value={"name": "Toshkent filiali"},
    ), patch(
        "apps.b2b.workspace.secondment_views.srepo.list_memberships_for_person",
        return_value=[
            {
                "employee_id": AZIZ_GUEST_ID,
                "company_id": HOST_COMPANY,
                "company_name": "Samarqand filiali",
                "role": RequestRole.MANAGER,
                "modules": [Module.SALES],
                "ends_at": None,
            }
        ],
    ):
        response = _call(WorkspaceSwitchView, factory.get("/switch/"), AZIZ)

    results = response.data["results"]
    assert results[0]["is_home"] is True
    assert results[0]["modules"] is None
    assert results[1]["company_id"] == HOST_COMPANY
    assert results[1]["modules"] == [Module.SALES]


# ─── The gate on reading, not just on writing ─────────────────────────────────

def _guest(modules):
    """Somebody lent to HOST_COMPANY with a named set of modules."""
    membership = Membership.from_row(_membership_row(modules=modules))
    return _user(EmployeeRole.PERFORMER, AZIZ_GUEST_ID, HOST_COMPANY, membership)


def test_a_guest_without_the_module_cannot_even_read_the_board():
    """`GET /tasks/` has no capability behind it — every role may read the
    board — so without the module gate the app could hide the tab while the
    endpoint went on answering."""
    from apps.b2b.workspace.views import WorkspaceTaskListCreateView

    response = _call(
        WorkspaceTaskListCreateView,
        factory.get("/tasks/"),
        _guest([Module.SALES]),
    )

    assert response.status_code == 403


def test_a_guest_with_the_module_gets_through():
    from apps.b2b.workspace.views import WorkspaceTaskListCreateView

    with patch(
        "apps.b2b.workspace.views.repo.list_tasks", return_value=[]
    ), patch(
        "apps.b2b.workspace.views.repo.task_counters", return_value={}
    ):
        response = _call(
            WorkspaceTaskListCreateView,
            factory.get("/tasks/"),
            _guest([Module.TASKS]),
        )

    assert response.status_code == 200


def test_a_guest_lent_the_files_module_may_open_it():
    """The half that was unreachable: the switch is what this gate reads, and
    while `Module.clean` dropped `fayllar` no guest could ever pass it."""
    from apps.b2b.workspace.views import WorkspaceFileListCreateView

    denied = _call(
        WorkspaceFileListCreateView,
        factory.get("/files/"),
        _guest([Module.TASKS]),
    )
    assert denied.status_code == 403

    with patch(
        "apps.b2b.workspace.views.repo.list_files", return_value=[]
    ):
        allowed = _call(
            WorkspaceFileListCreateView,
            factory.get("/files/"),
            _guest([Module.FILES]),
        )

    assert allowed.status_code == 200


def test_a_permanent_employee_passes_every_module_gate():
    """They have no grant to be narrowed by — their role is the whole story."""
    from apps.b2b.workspace.views import WorkspaceTaskListCreateView

    with patch(
        "apps.b2b.workspace.views.repo.list_tasks", return_value=[]
    ), patch(
        "apps.b2b.workspace.views.repo.task_counters", return_value={}
    ):
        response = _call(
            WorkspaceTaskListCreateView, factory.get("/tasks/"), AZIZ
        )

    assert response.status_code == 200


def test_every_module_section_is_actually_gated():
    """A new view in one of these sections that forgets `required_module` is
    a guest quietly reading something nobody shared with them."""
    from apps.b2b.workspace import views as workspace_views

    expected = {
        "WorkspaceLeadListCreateView": Module.SALES,
        "WorkspaceTaskListCreateView": Module.TASKS,
        "WorkspaceEventListCreateView": Module.CALENDAR,
        "WorkspaceThreadListCreateView": Module.CHAT,
        "WorkspaceFileListCreateView": Module.FILES,
    }
    for name, module in expected.items():
        assert getattr(workspace_views, name).required_module == module, name


def test_me_reports_the_narrowed_permissions_a_guest_actually_has():
    """The app builds its tabs from this. Reporting the un-narrowed map would
    draw screens that 403 the moment they are opened."""
    from apps.b2b.workspace.views import WorkspaceMeView

    guest = _guest([Module.SALES])
    with patch(
        "apps.b2b.workspace.views.repo.get_workspace_employee",
        return_value={
            "id": AZIZ_GUEST_ID,
            "company_id": HOST_COMPANY,
            "role": EmployeeRole.PERFORMER,
            "full_name": "Aziz Karimov",
        },
    ), patch(
        "apps.b2b.workspace.views.get_company", return_value={"name": "Samarqand"}
    ), patch(
        "apps.b2b.workspace.views.repo.completed_tasks_this_month", return_value=0
    ), patch(
        "apps.b2b.workspace.access_repository.access_for_employee",
        return_value=([Module.SALES], []),
    ):
        response = _call(WorkspaceMeView, factory.get("/me/"), guest)

    assert response.data["is_guest"] is True
    assert response.data["modules"] == [Module.SALES]
    assert response.data["permissions"]["can_post_lead"] is True
    assert response.data["permissions"]["can_create_task"] is False


# ─── Lider and manager are not the same thing ─────────────────────────────────

def test_only_an_owner_or_a_lider_may_ask_another_workspace():
    """The one thing that separates the two. A request hands an outsider a
    role and a set of modules for a stretch of time — a commitment about who
    is allowed in, rather than something anybody handing out work can make."""
    assert capabilities_for(EmployeeRole.OWNER)["can_request_help"] is True
    assert capabilities_for(EmployeeRole.LIDER)["can_request_help"] is True

    assert capabilities_for(EmployeeRole.PERFORMER)["can_request_help"] is False
    assert capabilities_for(EmployeeRole.EMPLOYEE)["can_request_help"] is False


def test_a_manager_still_hands_out_work():
    """Separating them must not have demoted the manager. The roster calls
    this role `performer`; the app calls it "Manager"."""
    manager = capabilities_for(EmployeeRole.PERFORMER)

    assert manager["can_create_task"] is True
    assert manager["can_post_lead"] is True
    assert manager["can_create_event"] is True
    assert manager["sees_all_company_data"] is True


def test_a_lider_can_do_everything_a_manager_can():
    lider = capabilities_for(EmployeeRole.LIDER)
    manager = capabilities_for(EmployeeRole.PERFORMER)

    for capability, allowed in manager.items():
        if allowed:
            assert lider[capability] is True, capability


def test_a_manager_cannot_send_even_though_they_manage():
    manager = _user(EmployeeRole.PERFORMER, 5, HOST_COMPANY)

    response = _call(
        WorkspaceRequestListCreateView,
        factory.post(
            "/requests/", {"to_employee_id": AZIZ_ID, "role": "manager"}, format="json"
        ),
        manager,
    )

    assert response.status_code == 403


def test_the_roles_a_request_offers_each_map_to_their_own():
    """`lider` and `manager` landing on the same roster role would make the
    picker's four chips three."""
    assert RequestRole.to_employee_role("lider") == EmployeeRole.LIDER
    assert RequestRole.to_employee_role("manager") == EmployeeRole.PERFORMER
    assert RequestRole.to_employee_role("employee") == EmployeeRole.EMPLOYEE


def test_a_guest_manager_does_not_use_up_the_workspace_s_own_manager_slot():
    """A workspace has one `performer` — the employee who also holds its
    dashboard login. A guest lent here as "Manager" is working the board, not
    holding the web login, so the uniqueness check has to skip them or the
    workspace cannot hire a manager of its own until the secondment ends."""
    from apps.b2b.views import _is_permanent_performer

    own = {"id": 1, "role": EmployeeRole.PERFORMER, "is_guest": False}
    lent = {"id": 2, "role": EmployeeRole.PERFORMER, "is_guest": True}

    assert _is_permanent_performer(own) is True
    assert _is_permanent_performer(lent) is False


def test_a_ghost_is_an_employee_who_is_simply_not_listed():
    assert RequestRole.to_employee_role("ghost") == EmployeeRole.EMPLOYEE
    assert RequestRole.is_hidden("ghost") is True
    assert RequestRole.is_hidden("manager") is False


# ─── The handle somebody picks for themselves ─────────────────────────────────

def _set_username(user, value):
    from apps.b2b.workspace.views import WorkspaceUsernameView

    return _call(
        WorkspaceUsernameView,
        factory.put("/me/username/", {"username": value}, format="json"),
        user,
    )


@pytest.mark.parametrize(
    "typed, stored",
    [
        ("aziz", "aziz"),
        # "@" is how a handle is written, not part of it.
        ("@aziz", "aziz"),
        ("  Aziz_99  ", "aziz_99"),
    ],
)
def test_a_handle_is_stored_the_way_it_is_searched_for(typed, stored):
    with patch(
        "apps.b2b.workspace.views.repo.username_taken", return_value=False
    ), patch(
        "apps.b2b.workspace.views.repo.set_employee_username",
        return_value={"id": AZIZ_ID, "company_id": HOME_COMPANY, "role": "employee"},
    ) as write, patch(
        "apps.b2b.workspace.views.get_company", return_value={}
    ), patch(
        "apps.b2b.workspace.views.repo.completed_tasks_this_month", return_value=0
    ), patch(
        "apps.b2b.workspace.access_repository.access_for_employee",
        return_value=([], []),
    ):
        response = _set_username(AZIZ, typed)

    assert response.status_code == 200
    assert write.call_args.args[1] == stored


@pytest.mark.parametrize(
    "bad",
    ["ab", "aziz karimov", "aziz!", "1aziz", "Aziz-K"],
    ids=["too-short", "spaces", "punctuation", "leading-digit", "hyphen"],
)
def test_a_handle_has_to_be_typeable(bad):
    """It is a name people type into a search box to find you."""
    response = _set_username(AZIZ, bad)
    assert response.status_code == 400


def test_a_blank_handle_gives_it_up():
    with patch(
        "apps.b2b.workspace.views.repo.set_employee_username",
        return_value={"id": AZIZ_ID, "company_id": HOME_COMPANY, "role": "employee"},
    ) as write, patch(
        "apps.b2b.workspace.views.get_company", return_value={}
    ), patch(
        "apps.b2b.workspace.views.repo.completed_tasks_this_month", return_value=0
    ), patch(
        "apps.b2b.workspace.access_repository.access_for_employee",
        return_value=([], []),
    ):
        response = _set_username(AZIZ, "")

    assert response.status_code == 200
    # None, not "": the column is nullable so the partial unique index skips
    # the many rows that have no handle at all.
    assert write.call_args.args[1] is None


def test_a_handle_somebody_here_already_uses_is_refused():
    with patch("apps.b2b.workspace.views.repo.username_taken", return_value=True):
        response = _set_username(AZIZ, "aziz")

    assert response.status_code == 409


def test_losing_the_race_for_a_handle_is_refused_too():
    """Two people typing "@aziz" in the same second both pass the check; the
    unique index is what actually decides."""
    with patch(
        "apps.b2b.workspace.views.repo.username_taken", return_value=False
    ), patch(
        "apps.b2b.workspace.views.repo.set_employee_username", return_value=None
    ):
        response = _set_username(AZIZ, "aziz")

    assert response.status_code == 409


# ─── Correcting your own entry ────────────────────────────────────────────────

def _edit_profile(user, body):
    from apps.b2b.workspace.views import WorkspaceProfileView

    return _call(
        WorkspaceProfileView,
        factory.put("/me/profile/", body, format="json"),
        user,
    )


@contextmanager
def _profile_write(saved=None):
    """Patches the write and the payload this endpoint builds around it."""
    row = {
        "id": AZIZ_ID,
        "company_id": HOME_COMPANY,
        "role": "employee",
        **(saved or {}),
    }
    with patch(
        "apps.b2b.workspace.views.repo.set_own_profile", return_value=row
    ) as write, patch(
        "apps.b2b.workspace.views.accounts.update_account"
    ) as account, patch(
        "apps.b2b.workspace.views.get_company", return_value={}
    ), patch(
        "apps.b2b.workspace.views.repo.completed_tasks_this_month", return_value=0
    ), patch(
        "apps.b2b.workspace.access_repository.access_for_employee",
        return_value=([], []),
    ):
        yield write, account


def test_a_name_is_stored_the_way_a_roster_writes_it():
    """Surname first — the order every list in the app is sorted by."""
    with _profile_write() as (write, _):
        response = _edit_profile(
            AZIZ, {"first_name": "Aziz", "last_name": "Karimov"}
        )

    assert response.status_code == 200
    assert write.call_args.kwargs["full_name"] == "Karimov Aziz"


def test_a_name_survives_having_no_surname():
    with _profile_write() as (write, _):
        _edit_profile(AZIZ, {"first_name": "Aziz"})

    assert write.call_args.kwargs["full_name"] == "Aziz"


def test_a_blank_email_is_stored_as_nothing_rather_than_as_blank():
    """An address somebody has stopped using should be removable."""
    with _profile_write() as (write, _):
        _edit_profile(
            AZIZ, {"first_name": "Aziz", "last_name": "Karimov", "email": ""}
        )

    assert write.call_args.kwargs["email"] is None


def test_a_name_cannot_be_emptied():
    """Every list in the app draws this. There is no blank to fall back to."""
    response = _edit_profile(AZIZ, {"first_name": "", "last_name": "Karimov"})
    assert response.status_code == 400


def test_the_account_learns_the_split_this_screen_is_the_only_one_to_know():
    """The next workspace they join is seeded from the account, not from here."""
    with _profile_write(saved={"account_id": 77}) as (_, account):
        _edit_profile(AZIZ, {"first_name": "Aziz", "last_name": "Karimov"})

    assert account.call_args.args[0] == 77
    assert account.call_args.kwargs == {
        "first_name": "Aziz",
        "last_name": "Karimov",
    }


def test_a_roster_row_with_no_account_behind_it_is_still_editable():
    """Imported from a spreadsheet and never registered. Still a person."""
    with _profile_write(saved={"account_id": None}) as (write, account):
        response = _edit_profile(AZIZ, {"first_name": "Aziz"})

    assert response.status_code == 200
    assert write.called
    account.assert_not_called()


@pytest.mark.parametrize("field", ["position", "role", "phone", "department_id"])
def test_what_the_workspace_owns_cannot_be_set_from_here(field):
    """The job title is the workspace's answer, not the employee's."""
    with _profile_write() as (write, _):
        _edit_profile(
            AZIZ,
            {"first_name": "Aziz", "last_name": "Karimov", field: "smuggled"},
        )

    assert field not in write.call_args.kwargs


@pytest.mark.parametrize(
    "written",
    ["Karimov Aziz", "Aziz", "Karimov Aziz Baxtiyorovich"],
    ids=["two-parts", "one-part", "three-parts"],
)
def test_a_written_name_survives_the_round_trip_through_the_form(written):
    """The form opens by splitting it and saves by joining it back.

    A patronymic is not a field here and guessing at one would lose it, so the
    surname is the first word and the rest travels together.
    """
    from apps.b2b.workspace.accounts import full_name_from, split_full_name

    first, last = split_full_name(written)
    assert full_name_from(first, last) == written
