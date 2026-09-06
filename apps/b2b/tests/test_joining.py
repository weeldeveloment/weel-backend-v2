"""Registration, and the three ways into a workspace.

The TZ's registration produces an account that belongs to nothing, and that is
the state most of these are about: what somebody can do holding only an
account session, and what each of the three doors — a link, a request, a chat
invite — actually hands over.

The link is the one with teeth. It is a bearer credential, so the checks are
that it expires, that it can be withdrawn, that it cannot be used twice, and
that what it grants is what it said rather than whatever the role happens to
open today.
"""
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.workspace.access import Module, Permission, Role
from apps.b2b.workspace.accounts import suggest_username
from apps.b2b.workspace.authentication import WorkspaceAccount, WorkspaceUser
from apps.b2b.workspace.joining_repository import JoinStatus, invite_problem
from apps.b2b.workspace.joining_views import (
    AccountDeviceTokenView,
    AccountJoinRequestView,
    AccountMeView,
    AccountOpenWorkspaceView,
    AccountOrgWorkspacesView,
    InvitePreviewView,
    JoinCodeView,
    WorkspaceInviteListCreateView,
    WorkspaceJoinRequestDecideView,
    WorkspaceJoinRequestListView,
    WorkspaceSearchView,
)

COMPANY = 10
factory = APIRequestFactory()


def _account(**overrides) -> WorkspaceAccount:
    data = {
        "id": 1,
        "phone": "+998905554433",
        "username": "nodir",
        "first_name": "Nodir",
        "last_name": "Qodirov",
    }
    data.update(overrides)
    return WorkspaceAccount(data)


def _admin() -> WorkspaceUser:
    return WorkspaceUser({
        "id": 1,
        "company_id": COMPANY,
        "role": Role.ADMIN,
        "full_name": "Admin Aliyev",
    })


def _call(view_class, request, user, **kwargs):
    force_authenticate(request, user=user)
    return view_class.as_view()(request, **kwargs)


def _granting(*permissions):
    return patch(
        "apps.b2b.workspace.access_repository.access_for_employee",
        return_value=(Module.CHOICES, list(permissions)),
    )


def _invite(**overrides):
    invite = {
        "id": 5,
        "company_id": COMPANY,
        "company_name": "Toshkent filiali",
        "token": "a-long-unguessable-token",
        "role": Role.MANAGER,
        "modules": [Module.CHAT, Module.TASKS],
        "permissions": None,
        "expires_at": timezone.now() + timedelta(days=7),
        "revoked_at": None,
        "accepted_at": None,
        "created_by_name": "Admin Aliyev",
    }
    invite.update(overrides)
    return invite


# ─── Registration ─────────────────────────────────────────────────────────────

def test_an_account_with_no_name_has_not_finished_registering():
    """The TZ's flow is phone → OTP → name → username. Somebody who stopped
    after the OTP is resumed rather than dropped into an empty app."""
    assert _account(first_name=None, username=None).has_profile is False
    assert _account(username=None).has_profile is False
    assert _account().has_profile is True


@pytest.mark.parametrize(
    "bad",
    ["ab", "Nodir Qodirov", "nodir!", "1nodir", "nodir-q"],
    ids=["too-short", "spaces", "punctuation", "leading-digit", "hyphen"],
)
def test_a_username_has_to_be_typeable(bad):
    response = _call(
        AccountMeView,
        factory.put("/account/me/", {"first_name": "Nodir", "username": bad},
                    format="json"),
        _account(),
    )

    assert response.status_code == 400


def test_the_at_sign_is_dropped_because_it_is_not_part_of_the_handle():
    with patch(
        "apps.b2b.workspace.joining_views.accounts.username_taken", return_value=False
    ), patch(
        "apps.b2b.workspace.joining_views.accounts.update_account",
        return_value={"id": 1, "phone": "+998905554433", "username": "nodir",
                      "first_name": "Nodir"},
    ) as write, patch(
        "apps.b2b.workspace.joining_views.accounts.list_memberships", return_value=[]
    ):
        response = _call(
            AccountMeView,
            factory.put("/account/me/", {"first_name": "Nodir", "username": "@Nodir"},
                        format="json"),
            _account(),
        )

    assert response.status_code == 200
    assert write.call_args.kwargs["username"] == "nodir"


def test_a_username_somebody_else_holds_is_refused():
    with patch(
        "apps.b2b.workspace.joining_views.accounts.username_taken", return_value=True
    ):
        response = _call(
            AccountMeView,
            factory.put("/account/me/", {"first_name": "Nodir", "username": "aziz"},
                        format="json"),
            _account(),
        )

    assert response.status_code == 409


def test_a_suggested_username_avoids_the_ones_already_taken():
    """A blank field with a uniqueness rule is where registrations stop."""
    taken = {"nodir", "nodir1"}
    with patch(
        "apps.b2b.workspace.accounts.username_taken",
        side_effect=lambda name, **_: name in taken,
    ):
        assert suggest_username("Nodir", "Qodirov", "+998905554433") == "nodir2"


def test_a_suggestion_falls_back_to_the_number_when_there_is_no_usable_name():
    with patch("apps.b2b.workspace.accounts.username_taken", return_value=False):
        assert suggest_username(None, None, "+998 90 555 44 33") == "user554433"


# ─── Invite links ─────────────────────────────────────────────────────────────

def test_a_link_is_usable_until_it_is_not():
    assert invite_problem(_invite()) is None
    assert invite_problem(None) == "not_found"
    assert invite_problem(_invite(revoked_at=timezone.now())) == "revoked"
    assert invite_problem(_invite(accepted_at=timezone.now())) == "used"
    assert (
        invite_problem(_invite(expires_at=timezone.now() - timedelta(minutes=1)))
        == "expired"
    )


def test_an_owner_cannot_be_invited_by_link():
    """A company has one owner and it is not handed out on a link."""
    with _granting(Permission.EMPLOYEE_INVITE):
        response = _call(
            WorkspaceInviteListCreateView,
            factory.post("/invites/", {"role": "owner"}, format="json"),
            _admin(),
        )

    assert response.status_code == 400


def test_inviting_needs_the_permission_to_invite():
    with _granting(Permission.TASK_VIEW):
        response = _call(
            WorkspaceInviteListCreateView,
            factory.post("/invites/", {"role": "employee"}, format="json"),
            _admin(),
        )

    assert response.status_code == 403


def test_a_link_records_what_it_grants_rather_than_deciding_later():
    """What the link said is what the person gets. Reading the role's modules
    at acceptance time would silently change old links every time the role
    editor was saved."""
    with _granting(Permission.EMPLOYEE_INVITE), patch(
        "apps.b2b.workspace.joining_views.jrepo.create_invite", return_value=_invite()
    ) as create, patch("apps.b2b.workspace.joining_views.arepo.record_audit"):
        response = _call(
            WorkspaceInviteListCreateView,
            factory.post(
                "/invites/",
                {"role": "manager", "modules": ["chat", "vazifa"], "days": 3},
                format="json",
            ),
            _admin(),
        )

    assert response.status_code == 201
    assert create.call_args.kwargs["modules"] == ["chat", "vazifa"]
    assert create.call_args.kwargs["days"] == 3


def test_a_link_with_no_modules_means_by_role():
    with _granting(Permission.EMPLOYEE_INVITE), patch(
        "apps.b2b.workspace.joining_views.jrepo.create_invite", return_value=_invite()
    ) as create, patch("apps.b2b.workspace.joining_views.arepo.record_audit"):
        _call(
            WorkspaceInviteListCreateView,
            factory.post("/invites/", {"role": "employee"}, format="json"),
            _admin(),
        )

    # None, not [] — one is "by role", the other is access to nothing.
    assert create.call_args.kwargs["modules"] is None


# ─── Accepting one ────────────────────────────────────────────────────────────

def test_a_preview_says_what_by_role_actually_opens():
    """Somebody deciding whether to accept should be told what they are
    accepting, and "by role" tells them nothing."""
    with patch(
        "apps.b2b.workspace.joining_views.jrepo.get_invite_by_token",
        return_value=_invite(modules=None),
    ), patch(
        "apps.b2b.workspace.joining_views.arepo.role_access",
        return_value=([Module.TASKS, Module.CHAT], []),
    ):
        response = _call(
            InvitePreviewView, factory.get("/account/invites/x/"), _account(), token="x"
        )

    assert response.status_code == 200
    assert response.data["modules"] == [Module.TASKS, Module.CHAT]


@pytest.mark.parametrize(
    "invite, expected",
    [
        (None, 404),
        (_invite(revoked_at=timezone.now()), 409),
        (_invite(expires_at=timezone.now() - timedelta(minutes=1)), 409),
        (_invite(accepted_at=timezone.now()), 409),
    ],
    ids=["missing", "revoked", "expired", "used"],
)
def test_a_link_that_cannot_be_used_is_refused(invite, expected):
    with patch(
        "apps.b2b.workspace.joining_views.jrepo.get_invite_by_token", return_value=invite
    ), patch(
        "apps.b2b.workspace.joining_views.accounts.create_membership"
    ) as join:
        response = _call(
            InvitePreviewView,
            factory.post("/account/invites/x/"),
            _account(),
            token="x",
        )

    assert response.status_code == expected
    join.assert_not_called()


def test_somebody_without_a_name_finishes_registering_first():
    with patch(
        "apps.b2b.workspace.joining_views.jrepo.get_invite_by_token",
        return_value=_invite(),
    ), patch("apps.b2b.workspace.joining_views.accounts.create_membership") as join:
        response = _call(
            InvitePreviewView,
            factory.post("/account/invites/x/"),
            _account(username=None),
            token="x",
        )

    assert response.status_code == 409
    assert response.data["problem"] == "no_profile"
    join.assert_not_called()


def test_the_link_is_claimed_before_anybody_is_put_on_the_roster():
    """Two taps a moment apart would otherwise both pass and add the person
    twice."""
    with patch(
        "apps.b2b.workspace.joining_views.jrepo.get_invite_by_token",
        return_value=_invite(),
    ), patch(
        "apps.b2b.workspace.joining_views.accounts.employee_in_company",
        return_value=None,
    ), patch(
        "apps.b2b.workspace.joining_views.jrepo.mark_invite_accepted", return_value=0
    ), patch(
        "apps.b2b.workspace.joining_views.accounts.create_membership"
    ) as join:
        response = _call(
            InvitePreviewView,
            factory.post("/account/invites/x/"),
            _account(),
            token="x",
        )

    assert response.status_code == 409
    join.assert_not_called()


def test_accepting_grants_exactly_what_the_link_said():
    with patch(
        "apps.b2b.workspace.joining_views.jrepo.get_invite_by_token",
        return_value=_invite(),
    ), patch(
        "apps.b2b.workspace.joining_views.accounts.employee_in_company",
        return_value=None,
    ), patch(
        "apps.b2b.workspace.joining_views.jrepo.mark_invite_accepted", return_value=1
    ), patch(
        "apps.b2b.workspace.joining_views.accounts.create_membership",
        return_value={"id": 42, "company_id": COMPANY, "role": Role.MANAGER},
    ) as join, patch(
        "apps.b2b.workspace.joining_views.arepo.record_audit"
    ), patch(
        "apps.b2b.workspace.joining_views.create_workspace_tokens",
        return_value={"access": "a", "refresh": "r"},
    ):
        response = _call(
            InvitePreviewView,
            factory.post("/account/invites/x/"),
            _account(),
            token="x",
        )

    assert response.status_code == 201
    assert join.call_args.kwargs["role"] == Role.MANAGER
    assert join.call_args.kwargs["modules"] == [Module.CHAT, Module.TASKS]
    # And they are signed into the workspace they just joined, rather than
    # being sent back to a login screen.
    assert response.data["employee_id"] == 42


def test_somebody_already_in_the_workspace_is_not_added_twice():
    with patch(
        "apps.b2b.workspace.joining_views.jrepo.get_invite_by_token",
        return_value=_invite(),
    ), patch(
        "apps.b2b.workspace.joining_views.accounts.employee_in_company",
        return_value={"id": 9, "is_chat_only": False},
    ), patch(
        "apps.b2b.workspace.joining_views.accounts.create_membership"
    ) as join:
        response = _call(
            InvitePreviewView,
            factory.post("/account/invites/x/"),
            _account(),
            token="x",
        )

    assert response.status_code == 409
    join.assert_not_called()


# ─── Finding a workspace to ask ───────────────────────────────────────────────

def test_a_search_too_short_to_narrow_anything_returns_nothing():
    """One letter matches most of the table, so it is refused before the query
    runs — this endpoint may not become a directory of every workspace."""
    from apps.b2b.workspace.joining_repository import search_companies

    with patch("apps.b2b.workspace.joining_repository.fetch_all") as fetch:
        assert search_companies("w", account_id=1) == []
        assert search_companies("", account_id=1) == []
        assert search_companies("@a", account_id=1) == []

    fetch.assert_not_called()


def test_a_search_carries_only_what_the_card_shows():
    row = {
        "id": 7,
        "name": "Weel HQ",
        "slug": "weel_hq",
        "icon": "chart",
        "org_name": "Weel Tech",
        "member_count": 12,
        # Anything the query happens to select beyond the card is not a reason
        # to hand it out.
        "owner_phone": "+998901112233",
    }
    with patch(
        "apps.b2b.workspace.joining_views.jrepo.search_companies", return_value=[row]
    ):
        response = _call(
            WorkspaceSearchView,
            factory.get("/account/workspaces/search/", {"q": "weel"}),
            _account(),
        )

    assert response.status_code == 200
    assert response.data["results"] == [{
        "id": 7,
        "name": "Weel HQ",
        "slug": "weel_hq",
        "icon": "chart",
        "org_name": "Weel Tech",
        "member_count": 12,
    }]


def test_a_search_is_scoped_to_the_account_asking():
    """So workspaces they are already on can be left out of the answer."""
    with patch(
        "apps.b2b.workspace.joining_views.jrepo.search_companies", return_value=[]
    ) as search:
        _call(
            WorkspaceSearchView,
            factory.get("/account/workspaces/search/", {"q": "weel"}),
            _account(id=42),
        )

    search.assert_called_once_with("weel", account_id=42)


# ─── Asking to join ───────────────────────────────────────────────────────────

def test_a_workspace_nobody_can_find_cannot_be_asked():
    with patch(
        "apps.b2b.workspace.joining_views.jrepo.find_company_by_slug", return_value=None
    ):
        response = _call(
            AccountJoinRequestView,
            factory.post("/account/join-requests/", {"slug": "yoq"}, format="json"),
            _account(),
        )

    assert response.status_code == 404


def test_asking_to_join_tells_whoever_may_decide_it():
    """Owner, admin, or anyone else the workspace lets invite must hear about
    a request as it comes in — not only once somebody happens to open the
    list. See `notify_join_request_created`."""
    with patch(
        "apps.b2b.workspace.joining_views.jrepo.find_company_by_slug",
        return_value={"id": COMPANY, "name": "Weel"},
    ), patch(
        "apps.b2b.workspace.joining_views.accounts.employee_in_company",
        return_value=None,
    ), patch(
        "apps.b2b.workspace.joining_views.jrepo.create_join_request",
        return_value={"id": 5},
    ), patch(
        "apps.b2b.workspace.joining_views._queue_join_request_created"
    ) as queued:
        response = _call(
            AccountJoinRequestView,
            factory.post("/account/join-requests/", {"slug": "weel"}, format="json"),
            _account(),
        )

    assert response.status_code == 201
    queued.assert_called_once_with(5)


def test_a_broker_that_is_down_does_not_lose_the_ask():
    """The request is already stored; the push is the fast path, not the
    only one — same reasoning as the decision's own broker guard."""
    from apps.b2b.workspace.joining_views import _queue_join_request_created

    with patch(
        "apps.b2b.workspace.tasks.notify_join_request_created"
    ) as task:
        task.delay.side_effect = RuntimeError("broker down")
        _queue_join_request_created(5)  # must not raise


def test_what_the_asker_wants_is_a_request_and_not_a_grant():
    """The TZ is explicit: choosing modules yourself is a request for that
    access and requires confirmation. The decision reads the workspace's own
    answer, never `wanted_modules`."""
    with patch(
        "apps.b2b.workspace.joining_views.jrepo.get_join_request",
        return_value={
            "id": 3,
            "company_id": COMPANY,
            "account_id": 1,
            "wanted_modules": Module.CHOICES,
        },
    ), _granting(Permission.EMPLOYEE_INVITE), patch(
        "apps.b2b.workspace.joining_views.jrepo.close_join_request", return_value=1
    ) as close, patch(
        "apps.b2b.workspace.joining_views.accounts.get_account",
        return_value={"id": 1, "phone": "+998905554433"},
    ), patch(
        "apps.b2b.workspace.joining_views.accounts.create_membership",
        return_value={"id": 42},
    ) as join, patch(
        "apps.b2b.workspace.joining_views.arepo.record_audit"
    ):
        response = _call(
            WorkspaceJoinRequestDecideView,
            factory.post(
                "/join-requests/3/accept/",
                {"role": "employee", "modules": ["chat"]},
                format="json",
            ),
            _admin(),
            request_id=3,
            action="accept",
        )

    assert response.status_code == 200
    assert close.call_args.kwargs["granted_modules"] == ["chat"]
    # Not the nine they asked for.
    assert join.call_args.kwargs["modules"] == ["chat"]


def test_a_request_from_another_workspace_is_not_answerable_here():
    with patch(
        "apps.b2b.workspace.joining_views.jrepo.get_join_request",
        return_value={"id": 3, "company_id": 999, "account_id": 1},
    ), _granting(Permission.EMPLOYEE_INVITE):
        response = _call(
            WorkspaceJoinRequestDecideView,
            factory.post("/join-requests/3/decline/", {}, format="json"),
            _admin(),
            request_id=3,
            action="decline",
        )

    assert response.status_code == 404


def test_answering_a_request_twice_is_refused():
    with patch(
        "apps.b2b.workspace.joining_views.jrepo.get_join_request",
        return_value={"id": 3, "company_id": COMPANY, "account_id": 1},
    ), _granting(Permission.EMPLOYEE_INVITE), patch(
        "apps.b2b.workspace.joining_views.jrepo.close_join_request", return_value=0
    ), patch(
        "apps.b2b.workspace.joining_views.accounts.create_membership"
    ) as join:
        response = _call(
            WorkspaceJoinRequestDecideView,
            factory.post("/join-requests/3/accept/", {}, format="json"),
            _admin(),
            request_id=3,
            action="accept",
        )

    assert response.status_code == 409
    join.assert_not_called()


# ─── Choosing a workspace ─────────────────────────────────────────────────────

def test_a_workspace_you_do_not_belong_to_cannot_be_opened():
    with patch(
        "apps.b2b.workspace.joining_views.accounts.list_memberships",
        return_value=[{"employee_id": 7}],
    ):
        response = _call(
            AccountOpenWorkspaceView,
            factory.post("/account/workspaces/999/open/"),
            _account(),
            employee_id=999,
        )

    assert response.status_code == 403


def test_opening_one_you_do_belong_to_hands_back_a_workspace_session():
    with patch(
        "apps.b2b.workspace.joining_views.accounts.list_memberships",
        return_value=[{"employee_id": 7}],
    ), patch(
        "apps.b2b.workspace.repository.get_workspace_employee",
        return_value={"id": 7, "company_id": COMPANY},
    ), patch(
        "apps.b2b.workspace.joining_views.create_workspace_tokens",
        return_value={"access": "a", "refresh": "r"},
    ):
        response = _call(
            AccountOpenWorkspaceView,
            factory.post("/account/workspaces/7/open/"),
            _account(),
            employee_id=7,
        )

    assert response.status_code == 200
    assert response.data["company_id"] == COMPANY


def test_an_orgs_workspaces_are_closed_to_somebody_outside_it():
    with patch(
        "apps.b2b.workspace.joining_views.accounts.org_ids_for_account",
        return_value=[1, 2],
    ):
        response = _call(
            AccountOrgWorkspacesView,
            factory.get("/account/orgs/9/workspaces/"),
            _account(),
            org_id=9,
        )

    assert response.status_code == 403


def test_an_orgs_workspaces_list_the_seat_you_hold_in_each():
    with patch(
        "apps.b2b.workspace.joining_views.accounts.org_ids_for_account",
        return_value=[1],
    ), patch(
        "apps.b2b.workspace.joining_views.accounts.list_org_workspaces",
        return_value=[
            {
                "id": 10,
                "name": "Sotuv bo’limi",
                "slug": "sotuv",
                "description": None,
                "icon": "chart",
                "member_count": 12,
                "admin_name": "Aziz Karimov",
                "has_pending_request": False,
            },
            {
                "id": 11,
                "name": "HR bo’limi",
                "slug": "hr",
                "description": None,
                "icon": "people",
                "member_count": 5,
                "admin_name": "Dilnoza Rahimova",
                "has_pending_request": True,
            },
        ],
    ), patch(
        "apps.b2b.workspace.joining_views.accounts.list_memberships",
        # Only a member of the first of the two.
        return_value=[{"company_id": 10, "employee_id": 7}],
    ):
        response = _call(
            AccountOrgWorkspacesView,
            factory.get("/account/orgs/1/workspaces/"),
            _account(),
            org_id=1,
        )

    assert response.status_code == 200
    results = {row["id"]: row for row in response.data["results"]}
    assert results[10]["employee_id"] == 7
    assert results[10]["member_count"] == 12
    assert results[11]["employee_id"] is None
    # And what can be done about the one there is no seat on: the slug a join
    # request names, and whether one is already waiting. Without these the
    # screen can list a room, refuse to open it, and offer nothing else.
    assert results[11]["slug"] == "hr"
    assert results[11]["has_pending_request"] is True
    assert results[10]["has_pending_request"] is False


def test_an_account_session_cannot_reach_a_workspace_endpoint():
    """The two token types must never be interchangeable: an account id read
    as an employee id is a different table with the same integers."""
    from apps.b2b.workspace.permissions import IsWorkspaceUser

    assert IsWorkspaceUser().has_permission(
        type("R", (), {"user": _account()})(), None
    ) is False


def test_the_status_names_are_the_ones_stored():
    assert JoinStatus.CHOICES == ["pending", "accepted", "declined"]


# ─── Hearing back ─────────────────────────────────────────────────────────────

def test_the_asker_can_read_their_own_outbox():
    """Somebody who has asked and heard nothing cannot otherwise tell a request
    that was never sent from one nobody has answered."""
    rows = [
        {
            "id": 5,
            "company_id": 10,
            "company_name": "Weel HQ",
            "company_slug": "weel_hq",
            "org_name": "Weel Tech",
            "status": JoinStatus.ACCEPTED,
            "decline_reason": None,
            "granted_role": Role.MANAGER,
            "created_at": None,
            "decided_at": None,
        }
    ]
    with patch(
        "apps.b2b.workspace.joining_views.jrepo.list_account_join_requests",
        return_value=rows,
    ) as listed:
        response = _call(
            AccountJoinRequestView,
            factory.get("/account/join-requests/"),
            _account(id=7),
        )

    listed.assert_called_once_with(7)
    assert response.status_code == 200
    row = response.data["results"][0]
    assert row["status"] == JoinStatus.ACCEPTED
    # "You are in" without saying as what is half an answer.
    assert row["granted_role_label"] == Role.label(Role.MANAGER)


def test_a_pending_request_names_no_role_it_has_not_been_given():
    rows = [{
        "id": 5,
        "company_id": 10,
        "company_name": "Weel HQ",
        "company_slug": "weel_hq",
        "org_name": None,
        "status": JoinStatus.PENDING,
        "decline_reason": None,
        "granted_role": None,
        "created_at": None,
        "decided_at": None,
    }]
    with patch(
        "apps.b2b.workspace.joining_views.jrepo.list_account_join_requests",
        return_value=rows,
    ):
        response = _call(
            AccountJoinRequestView, factory.get("/account/join-requests/"), _account()
        )

    assert response.data["results"][0]["granted_role_label"] is None


def test_this_phone_is_addressable_before_it_belongs_anywhere():
    """The roster's token cannot reach somebody waiting on a request — they
    are not on a roster yet, which is the whole point of the wait."""
    with patch(
        "apps.b2b.workspace.joining_views.accounts.set_account_fcm_token"
    ) as store:
        response = _call(
            AccountDeviceTokenView,
            factory.post("/account/device-token/", {"fcm_token": " abc "}, format="json"),
            _account(id=7),
        )

    assert response.status_code == 200
    store.assert_called_once_with(7, "abc")


def test_signing_out_clears_the_account_token_rather_than_storing_blank():
    with patch(
        "apps.b2b.workspace.joining_views.accounts.set_account_fcm_token"
    ) as store:
        _call(
            AccountDeviceTokenView,
            factory.post("/account/device-token/", {"fcm_token": ""}, format="json"),
            _account(id=7),
        )

    store.assert_called_once_with(7, None)


def test_answering_a_request_tells_the_person_who_sent_it():
    ask = {"id": 3, "company_id": COMPANY, "account_id": 9, "status": JoinStatus.PENDING}
    with (
        _granting(Permission.EMPLOYEE_INVITE),
        patch("apps.b2b.workspace.joining_views.jrepo.get_join_request", return_value=ask),
        patch(
            "apps.b2b.workspace.joining_views.jrepo.close_join_request", return_value=1
        ),
        patch("apps.b2b.workspace.joining_views._queue_join_decision") as queued,
    ):
        response = _call(
            WorkspaceJoinRequestDecideView,
            factory.post("/join-requests/3/decline/", {}, format="json"),
            _admin(),
            request_id=3,
            action="decline",
        )

    assert response.status_code == 200
    queued.assert_called_once_with(3)


def test_a_broker_that_is_down_does_not_unanswer_the_request():
    """The decision is already stored; the push is the fast path, not the only
    one."""
    from apps.b2b.workspace.joining_views import _queue_join_decision

    with patch(
        "apps.b2b.workspace.tasks.notify_join_request_decided"
    ) as task:
        task.delay.side_effect = RuntimeError("broker down")
        _queue_join_decision(3)  # must not raise


def test_a_request_still_open_is_not_announced_as_answered():
    from apps.b2b.workspace.tasks import notify_join_request_decided

    with patch(
        "apps.b2b.workspace.joining_repository.get_join_request_with_company",
        return_value={"id": 3, "status": JoinStatus.PENDING},
    ):
        assert notify_join_request_decided(3) == 0


def test_a_new_request_reaches_everyone_who_may_decide_it():
    """The other half of the round trip: creating the request must reach the
    same audience `HasPermission` gates the decision on — not just owner and
    admin by name, but whoever the workspace's own role editor currently
    grants `employees.invite`."""
    from apps.b2b.workspace.tasks import notify_join_request_created

    ask = {
        "id": 3,
        "company_id": COMPANY,
        "account_id": 9,
        "status": JoinStatus.PENDING,
        "message": "Iltimos",
        "company_name": "Weel",
    }
    recipients = [
        {"employee_id": 1, "company_id": COMPANY, "fcm_token": "tok-owner"},
        {"employee_id": 2, "company_id": COMPANY, "fcm_token": "tok-admin"},
    ]
    with patch(
        "apps.b2b.workspace.joining_repository.get_join_request_with_company",
        return_value=ask,
    ), patch(
        "apps.b2b.workspace.access_repository.list_employee_invite_recipients",
        return_value=recipients,
    ) as list_recipients, patch(
        "apps.b2b.workspace.accounts.get_account",
        return_value={"id": 9, "first_name": "Nodir", "last_name": "Qodirov"},
    ), patch("apps.b2b.workspace.tasks.create_notification") as create_row, patch(
        "apps.notification.service.b2b_firebase_app", return_value=object()
    ), patch(
        "apps.notification.service.FCMService.send_to_tokens"
    ) as send:
        sent = notify_join_request_created(3)

    list_recipients.assert_called_once_with(COMPANY)
    assert sent == 2
    assert create_row.call_count == 2
    assert send.call_args.kwargs["tokens"] == ["tok-owner", "tok-admin"]


def test_a_request_already_answered_is_not_announced_as_new():
    from apps.b2b.workspace.tasks import notify_join_request_created

    with patch(
        "apps.b2b.workspace.joining_repository.get_join_request_with_company",
        return_value={"id": 3, "status": JoinStatus.ACCEPTED},
    ):
        assert notify_join_request_created(3) == 0


# ─── One field, two things it can hold ────────────────────────────────────────

def _resolve(code: str, account=None):
    return _call(
        JoinCodeView,
        factory.get("/account/join-code/", {"code": code}),
        account or _account(),
    )


def test_a_link_resolves_to_the_offer_it_carries():
    with patch(
        "apps.b2b.workspace.joining_views.jrepo.get_invite_by_token",
        return_value=_invite(),
    ):
        response = _resolve("https://weel.uz/invite/abc123")

    assert response.status_code == 200
    assert response.data["kind"] == "invite"
    assert response.data["invite"]["is_usable"] is True


def test_a_dead_link_still_resolves_as_a_link_and_says_why():
    """Answering 404 would send somebody looking for a company code that does
    not exist, instead of telling them their link has expired."""
    with patch(
        "apps.b2b.workspace.joining_views.jrepo.get_invite_by_token",
        return_value=_invite(expires_at=timezone.now() - timedelta(days=1)),
    ):
        response = _resolve("abc123")

    assert response.status_code == 200
    assert response.data["kind"] == "invite"
    assert response.data["invite"]["is_usable"] is False
    assert response.data["invite"]["problem"] == "expired"


def test_a_company_code_resolves_to_the_rooms_inside_it():
    rooms = [{
        "id": 3,
        "name": "Dizayn jamoasi",
        "slug": "dizayn",
        "icon": None,
        "member_count": 4,
        "is_member": False,
        "has_pending_request": True,
    }]
    with (
        patch(
            "apps.b2b.workspace.joining_views.jrepo.get_invite_by_token",
            return_value=None,
        ),
        patch(
            "apps.b2b.workspace.joining_views.accounts.find_org_by_join_code",
            return_value={"id": 1, "name": "Weel Tech", "join_code": "W-8932"},
        ),
        patch(
            "apps.b2b.workspace.joining_views.accounts.org_workspaces_for_joining",
            return_value=rooms,
        ),
    ):
        response = _resolve("W-8932")

    assert response.status_code == 200
    assert response.data["kind"] == "company"
    assert response.data["company"]["name"] == "Weel Tech"
    room = response.data["workspaces"][0]
    # Marked rather than hidden: somebody who has already asked should be told
    # so, not shown the same button and answered with a 409.
    assert room["has_pending_request"] is True
    assert room["is_member"] is False


def test_a_string_that_is_neither_says_only_that():
    """Guessing at five characters must not learn that a code exists but its
    company is closed."""
    with (
        patch(
            "apps.b2b.workspace.joining_views.jrepo.get_invite_by_token",
            return_value=None,
        ),
        patch(
            "apps.b2b.workspace.joining_views.accounts.find_org_by_join_code",
            return_value=None,
        ),
    ):
        response = _resolve("nonsense")

    assert response.status_code == 404


def test_a_code_is_the_same_code_however_it_was_typed():
    from apps.b2b.workspace.accounts import normalise_join_code

    # Pasted whole, lower case, spaced, and with the prefix left off.
    for typed in ["W-8932", "w8932", "w-89 32", "8932", "weel.app/join/W-8932"]:
        assert normalise_join_code(typed) == "W-8932"


def test_an_empty_code_is_refused_before_anything_is_looked_up():
    with patch(
        "apps.b2b.workspace.joining_views.jrepo.get_invite_by_token"
    ) as invite:
        response = _resolve("   ")

    assert response.status_code == 400
    invite.assert_not_called()


# ─── Deleting an account frees the number ────────────────────────────────────
#
# The bug: delete the account, register again with the same phone, and the
# first code entered lands you back in the old workspace with the old role.
# `delete_account` had cleared only the `b2b_account` row and the roster rows
# it was linked to by `account_id`; a legacy `b2b_user` login for the same
# number, and any roster row not carrying the link, kept the phone and were
# what `_resolve_employee` rebuilt a seat from on the next sign-in.


def _delete_account_statements(phone: str):
    from apps.b2b.workspace import accounts

    with (
        patch.object(
            accounts, "get_account", return_value={"id": 5, "phone": phone}
        ),
        patch.object(accounts, "companies_closed_by_deleting", return_value=[]),
        patch.object(accounts, "execute", return_value=0) as execute,
    ):
        accounts.delete_account(5)

    return [
        (" ".join(call.args[0].split()), list(call.args[1]) if len(call.args) > 1 else [])
        for call in execute.call_args_list
    ]


def test_deleting_an_account_revokes_the_legacy_login_for_that_number():
    statements = _delete_account_statements("+998 90 555 44 33")

    user_update = next(
        (sql, params)
        for sql, params in statements
        if "UPDATE b2b_user" in sql and "is_active = FALSE" in sql
    )
    # Matched the way sign-in matches — the last nine digits, trailing LIKE.
    assert "%905554433" in user_update[1]
    # And the number is released rather than left on the row.
    assert "phone = 'deleted-' || id" in user_update[0]


def test_deleting_an_account_drops_the_legacy_sessions_for_that_number():
    statements = _delete_account_statements("+998905554433")

    assert any(
        "DELETE FROM b2b_user_session" in sql and "%905554433" in params
        for sql, params in statements
    )


def test_deleting_an_account_anonymises_roster_rows_by_number_not_just_link():
    statements = _delete_account_statements("+998905554433")

    roster = next(
        (sql, params)
        for sql, params in statements
        if "UPDATE b2b_employee" in sql
    )
    # Still keyed on the link, but now also on the number, so a secondment or
    # an unlinked row is caught too.
    assert "account_id = %s" in roster[0]
    assert "LIKE %s" in roster[0]
    assert "%905554433" in roster[1]


def test_deleting_an_account_with_no_phone_touches_no_identity_tables():
    statements = _delete_account_statements("")

    assert not any("b2b_user" in sql for sql, _ in statements)
    # The account row itself is still removed.
    assert any("DELETE FROM b2b_account" in sql for sql, _ in statements)


def _manager() -> WorkspaceUser:
    return WorkspaceUser({
        "id": 4,
        "company_id": COMPANY,
        "role": Role.MANAGER,
        "full_name": "Rahbar Rahimov",
    })


def _employee() -> WorkspaceUser:
    return WorkspaceUser({
        "id": 5,
        "company_id": COMPANY,
        "role": Role.EMPLOYEE,
        "full_name": "Xodim Xolmatov",
    })


def test_a_manager_answers_the_door_only_when_permitted():
    """TZ v2 §11 "Принимать заявки на вступление: Руководитель — в своей
    рабочей среде, при разрешении". Off by default; the role editor handing
    a manager `employees.invite` turns it on — the same permission as
    inviting, which the matrix answers identically on every row."""
    with patch(
        "apps.b2b.workspace.joining_views.jrepo.get_join_request",
        return_value={"id": 3, "company_id": COMPANY, "account_id": 9},
    ):
        response = _call(
            WorkspaceJoinRequestDecideView,
            factory.post(
                "/join-requests/3/accept/", {"role": "employee"}, format="json"
            ),
            _manager(),
            request_id=3,
            action="accept",
        )
    assert response.status_code == 403

    with _granting(Permission.EMPLOYEE_INVITE), patch(

        "apps.b2b.workspace.joining_views.jrepo.get_join_request",
        return_value={"id": 3, "company_id": COMPANY, "account_id": 9},
    ), patch(
        "apps.b2b.workspace.joining_views.jrepo.close_join_request",
        return_value=True,
    ), patch(
        "apps.b2b.workspace.joining_views.accounts.get_account",
        return_value=_account(),
    ), patch(
        "apps.b2b.workspace.joining_views.accounts.create_membership",
        return_value={"id": 42},
    ), patch(
        "apps.b2b.workspace.joining_views.arepo.record_audit"
    ):
        response = _call(
            WorkspaceJoinRequestDecideView,
            factory.post(
                "/join-requests/3/accept/", {"role": "employee"}, format="json"
            ),
            _manager(),
            request_id=3,
            action="accept",
        )

    assert response.status_code == 200


def test_a_plain_employee_may_not_let_anybody_in():
    response = _call(
        WorkspaceJoinRequestDecideView,
        factory.post("/join-requests/3/accept/", {"role": "owner"}, format="json"),
        _employee(),
        request_id=3,
        action="accept",
    )
    assert response.status_code == 403


def test_a_plain_employee_cannot_even_see_who_is_asking():
    # The list is the other half of the same door. Leaving it open would show
    # every employee the phone number of everyone who has applied.
    response = _call(
        WorkspaceJoinRequestListView, factory.get("/join-requests/"), _employee()
    )
    assert response.status_code == 403


# ─── TZ v2 §5.2: accepting as asked, and only downwards ──────────────────────

def _accepting(user, body, ask=None):
    with patch(
        "apps.b2b.workspace.joining_views.jrepo.get_join_request",
        return_value=ask or {
            "id": 3,
            "company_id": COMPANY,
            "account_id": 1,
            "wanted_modules": ["tasks", "chat"],
        },
    ), patch(
        "apps.b2b.workspace.joining_views.jrepo.close_join_request", return_value=1
    ) as close, patch(
        "apps.b2b.workspace.joining_views.accounts.get_account",
        return_value={"id": 1, "phone": "+998905554433"},
    ), patch(
        "apps.b2b.workspace.joining_views.accounts.create_membership",
        return_value={"id": 42},
    ) as join, patch(
        "apps.b2b.workspace.joining_views.arepo.record_audit"
    ), patch("apps.b2b.workspace.joining_views._queue_join_decision"):
        response = _call(
            WorkspaceJoinRequestDecideView,
            factory.post("/join-requests/3/accept/", body, format="json"),
            user,
            request_id=3,
            action="accept",
        )
    return response, close, join


def test_accepting_as_asked_grants_what_was_asked():
    """§5.2's first answer: "принять запрос без изменений". Sending no module
    list is that answer, and the modules on the request are what is granted."""
    response, close, join = _accepting(_admin(), {"role": "employee"})

    assert response.status_code == 200
    assert close.call_args.kwargs["granted_modules"] == ["tasks", "chat"]
    assert join.call_args.kwargs["modules"] == ["tasks", "chat"]


def test_an_explicit_null_means_by_role_not_as_asked():
    response, close, join = _accepting(_admin(), {"role": "employee", "modules": None})

    assert response.status_code == 200
    assert close.call_args.kwargs["granted_modules"] is None
    assert join.call_args.kwargs["modules"] is None


@pytest.mark.parametrize(
    "user, role, allowed",
    [
        (_admin, "manager", True),
        (_admin, "admin", False),
        (_manager, "employee", True),
        (_manager, "guest", True),
        (_manager, "manager", False),
    ],
)
def test_the_assigned_role_may_not_reach_the_acceptors(user, role, allowed):
    """§5.2: "назначаемая роль не может превышать уровень роли пользователя,
    который принимает заявку" — read, as §11's role rows read it, as strictly
    below."""
    with _granting(Permission.EMPLOYEE_INVITE):
        response, close, _ = _accepting(user(), {"role": role})

    assert (response.status_code == 200) is allowed, response.data
    assert close.called is allowed


def test_the_acceptor_cannot_open_a_module_they_do_not_hold():
    with patch(
        "apps.b2b.workspace.access_repository.access_for_employee",
        return_value=([Module.CHAT, Module.EMPLOYEES], [Permission.EMPLOYEE_INVITE]),
    ):
        response, close, _ = _accepting(
            _manager(), {"role": "employee", "modules": ["chat", "sales"]}
        )

    assert response.status_code == 403
    assert response.data["modules"] == ["sales"]
    close.assert_not_called()


# ─── TZ v2 §11: opening a workspace inside a company ─────────────────────────

def _opening(account, org_id=7):
    from apps.b2b.workspace.joining_views import AccountWorkspacesView

    with patch(
        "apps.b2b.workspace.joining_views.accounts.org_ids_for_account",
        return_value=[org_id],
    ), patch(
        "apps.b2b.workspace.joining_views.accounts.create_workspace", return_value=None
    ) as create:

        response = _call(
            AccountWorkspacesView,
            factory.post(
                "/account/workspaces/", {"name": "Marketing", "org_id": org_id}, format="json"
            ),
            account,
        )
    return response, create


def _seat(role, employee_id=11, org_id=7, is_guest=False):
    return {"employee_id": employee_id, "role": role, "org_id": org_id,
            "company_id": 3, "is_guest": is_guest}


@pytest.mark.parametrize(
    "role, permission_access, allowed",
    [
        ("owner", None, True),
        ("admin", None, True),
        ("manager", None, False),
        ("manager", [Permission.WORKSPACE_CREATE, Permission.EMPLOYEE_VIEW], True),
        ("employee", None, False),
        ("guest", [Permission.WORKSPACE_CREATE], False),
    ],
)
def test_who_may_open_a_workspace_in_the_company(role, permission_access, allowed):
    account = _account(first_name="Nodir", last_name="Qodirov")
    with patch(
        "apps.b2b.workspace.joining_views.accounts.list_memberships",
        return_value=[_seat(role)],
    ), patch(
        "apps.b2b.workspace.joining_views.repo.get_workspace_employee",
        return_value={"id": 11, "company_id": 3, "role": role,
                      "module_access": None, "permission_access": permission_access},
    ):
        response, create = _opening(account)

    if allowed:
        # Past the gate; the stubbed repository answers nothing, which the
        # view reports as 400 rather than 403.
        assert response.status_code == 400, response.data
        create.assert_called_once()
    else:
        assert response.status_code == 403, response.data
        create.assert_not_called()


def test_a_seat_in_another_company_does_not_open_this_one():
    account = _account(first_name="Nodir", last_name="Qodirov")
    with patch(
        "apps.b2b.workspace.joining_views.accounts.list_memberships",
        return_value=[_seat("owner", org_id=99)],
    ):
        response, create = _opening(account, org_id=7)

    assert response.status_code == 403
    create.assert_not_called()


# ─── TZ v2 §2/§3: the company's owner is on every one of its workspaces ──────

def _creating_in_org(creator, owners):
    """Run `accounts.create_workspace` for an existing org with the database
    stubbed: the company row insert answers a dict, memberships are recorded
    rather than written."""
    from apps.b2b.workspace import accounts as accts

    made = []

    def _membership(*, account, company_id, role, **_):
        made.append((account["id"], role))
        return {"id": 100 + account["id"], "company_id": company_id, "role": role}

    with patch.object(accts, "org_owner_accounts", return_value=owners), patch.object(
        accts, "fetch_one", return_value={"id": 77, "name": "Marketing", "org_id": 7}
    ), patch.object(accts, "free_workspace_slug", return_value="marketing"), patch.object(
        accts, "create_membership", side_effect=_membership
    ):
        created = accts.create_workspace(account=creator, name="Marketing", org_id=7)
    return created, made


def test_an_owner_opening_another_workspace_owns_it_too():
    owner = {"id": 1, "phone": "+998900000001"}
    created, made = _creating_in_org(owner, owners=[owner])

    assert created["role"] == Role.OWNER
    assert made == [(1, Role.OWNER)]


def test_a_workspace_an_admin_opens_still_has_the_owner_on_it():
    """Otherwise nobody in it could ever approve its deletion (§4) or stand
    above its admin — a room with no owner is one the company cannot close."""
    admin = {"id": 2, "phone": "+998900000002"}
    owner = {"id": 1, "phone": "+998900000001"}
    created, made = _creating_in_org(admin, owners=[owner])

    assert created["role"] == Role.ADMIN
    assert made == [(2, Role.ADMIN), (1, Role.OWNER)]
