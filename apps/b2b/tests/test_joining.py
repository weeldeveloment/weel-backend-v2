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
    AccountJoinRequestView,
    AccountMeView,
    AccountOpenWorkspaceView,
    InvitePreviewView,
    WorkspaceInviteListCreateView,
    WorkspaceJoinRequestDecideView,
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


def test_an_account_session_cannot_reach_a_workspace_endpoint():
    """The two token types must never be interchangeable: an account id read
    as an employee id is a different table with the same integers."""
    from apps.b2b.workspace.permissions import IsWorkspaceUser

    assert IsWorkspaceUser().has_permission(
        type("R", (), {"user": _account()})(), None
    ) is False


def test_the_status_names_are_the_ones_stored():
    assert JoinStatus.CHOICES == ["pending", "accepted", "declined"]
