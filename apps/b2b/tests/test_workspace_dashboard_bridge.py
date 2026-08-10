"""The web dashboard reaching the workspace endpoints.

Tasks, chat and the calendar are one company's data, but the dashboard signs
in as a ``b2b_user`` while every workspace row references ``b2b_employee(id)``.
``DashboardWorkspaceAuthentication`` is the single place those two identities
are allowed to meet, so what matters here is that it resolves the dashboard
account to the right employee, refuses to touch anything else, and does not
leak into the company endpoints where a ``b2b_user`` id must stay a
``b2b_user`` id.
"""
from unittest.mock import patch

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from rest_framework import exceptions
from rest_framework.test import APIRequestFactory

from apps.b2b.authentication import B2BAuthUser, B2BJWTAuthentication
from apps.b2b.workspace.authentication import (
    DashboardWorkspaceAuthentication,
    WorkspaceUser,
)
from apps.b2b.workspace.views import WorkspaceAPIView, WorkspaceTaskListCreateView

COMPANY_ID = 55
DASHBOARD_USER_ID = 7
EMPLOYEE_ID = 42

factory = APIRequestFactory()

B2B_USER = {
    "id": DASHBOARD_USER_ID,
    "company_id": COMPANY_ID,
    "role": "owner",
    "phone": "+998900000000",
    "first_name": "Aziz",
    "last_name": "Karimov",
}

EMPLOYEE = {
    "id": EMPLOYEE_ID,
    "company_id": COMPANY_ID,
    "role": "owner",
    "full_name": "Aziz Karimov",
    "phone": "+998900000000",
}

MODULE = "apps.b2b.workspace.authentication"


def _request(token_payload: dict):
    """A request carrying a bearer token whose validation is stubbed out —
    the signing itself is simplejwt's business, not this module's."""
    request = factory.get("/api/b2b/workspace/tasks/", HTTP_AUTHORIZATION="Bearer stub")
    request._stub_token = token_payload
    return request


def _authenticate(token_payload: dict, **repo_stubs):
    auth = DashboardWorkspaceAuthentication()
    with patch.object(
        DashboardWorkspaceAuthentication, "get_validated_token", return_value=token_payload
    ), patch(f"{MODULE}.get_b2b_user", return_value=repo_stubs.get("b2b_user", B2B_USER)), patch(
        f"{MODULE}.ensure_workspace_employee",
        return_value=repo_stubs.get("employee", EMPLOYEE),
    ):
        return auth.authenticate(_request(token_payload))


def test_dashboard_token_resolves_to_its_employee_row():
    result = _authenticate({"user_type": "b2b", "sub": str(DASHBOARD_USER_ID)})

    assert result is not None
    user, _token = result
    assert isinstance(user, WorkspaceUser)
    # The employee id, not the dashboard user id: everything the workspace
    # writes (assignees, chat participants, authorship) keys off this.
    assert user.id == EMPLOYEE_ID
    assert user.company_id == COMPANY_ID
    assert user.is_manager is True


def test_mobile_token_is_left_to_the_workspace_authenticator():
    # Returning None (not raising) is what lets DRF fall through the chain;
    # the mobile token is already handled by WorkspaceJWTAuthentication.
    assert _authenticate({"user_type": "b2b_employee", "sub": "42"}) is None


def test_client_token_is_ignored():
    assert _authenticate({"user_type": "client", "sub": "9"}) is None


def test_unknown_dashboard_account_is_rejected():
    with pytest.raises(exceptions.AuthenticationFailed):
        _authenticate({"user_type": "b2b", "sub": "999"}, b2b_user=None)


def test_account_without_a_roster_row_is_rejected():
    # ensure_workspace_employee returning None means the promotion failed —
    # authenticating anyway would hand the views a user with no id.
    with pytest.raises(exceptions.AuthenticationFailed):
        _authenticate({"user_type": "b2b", "sub": str(DASHBOARD_USER_ID)}, employee=None)


def test_non_numeric_subject_is_rejected():
    with pytest.raises(exceptions.AuthenticationFailed):
        _authenticate({"user_type": "b2b", "sub": "not-an-id"})


def test_workspace_views_accept_both_logins():
    assert WorkspaceTaskListCreateView.authentication_classes == (
        WorkspaceAPIView.authentication_classes
    )
    names = [cls.__name__ for cls in WorkspaceTaskListCreateView.authentication_classes]
    assert names == ["WorkspaceJWTAuthentication", "DashboardWorkspaceAuthentication"]


def test_company_endpoints_still_see_a_dashboard_user():
    """The bridge must not be reachable from the default chain.

    If it were, ``/api/b2b/employees/`` would start reading an employee id as
    a dashboard user id — the exact confusion the two token types exist to
    prevent.
    """
    configured = settings.REST_FRAMEWORK.get("DEFAULT_AUTHENTICATION_CLASSES", ())
    assert not any("DashboardWorkspaceAuthentication" in path for path in configured)

    auth = B2BJWTAuthentication()
    request = factory.get("/api/b2b/employees/", HTTP_AUTHORIZATION="Bearer stub")
    with patch.object(
        B2BJWTAuthentication, "get_validated_token",
        return_value={"user_type": "b2b", "sub": str(DASHBOARD_USER_ID)},
    ), patch("apps.b2b.authentication.get_b2b_user", return_value=B2B_USER):
        user, _token = auth.authenticate(request)

    assert isinstance(user, B2BAuthUser)
    assert user.id == DASHBOARD_USER_ID
