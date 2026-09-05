"""Call Center Desk endpoints — `/api/admin-auth/ccd/…`.

The desk reads all of its B2B data from here, so the contract these views expose is the
one another service depends on. What is pinned: only admins get through, the query
parameters are actually honoured (a search box that silently ignores its input is worse
than none), a bad `limit` cannot be used to pull the whole table, and a missing row is a
404 rather than a null body.

The repository is stubbed throughout — `test_ccd_repository.py` covers the SQL itself
against a real database.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from rest_framework.test import APIRequestFactory, force_authenticate

from apps.admin_auth.ccd_views import (
    CcdApprovalsView,
    CcdAuditView,
    CcdCallsView,
    CcdCompaniesView,
    CcdCompanyDetailView,
    CcdEmployeeActiveView,
    CcdEmployeesView,
    CcdWorkspaceActiveView,
    CcdWorkspacesView,
)

factory = APIRequestFactory()

ADMIN = SimpleNamespace(id=1, role="admin", is_active=True, is_authenticated=True)
OPERATOR = SimpleNamespace(id=2, role="employee", is_active=True, is_authenticated=True)

REPO = "apps.admin_auth.ccd_views.repo"


def _get(view, url="/", user=ADMIN, **kwargs):
    request = factory.get(url, kwargs)
    if user is not None:
        force_authenticate(request, user=user)
    return request


# ─── access ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "view, url",
    [
        (CcdCompaniesView, "/ccd/companies/"),
        (CcdWorkspacesView, "/ccd/workspaces/"),
        (CcdEmployeesView, "/ccd/employees/"),
        (CcdCallsView, "/ccd/calls/"),
        (CcdAuditView, "/ccd/audit/"),
        (CcdApprovalsView, "/ccd/approvals/"),
    ],
)
def test_non_admin_is_refused(view, url):
    """A workspace employee's token must not reach the cross-company desk data."""
    response = view.as_view()(_get(view, url, user=OPERATOR))
    assert response.status_code == 403


@pytest.mark.parametrize(
    "view, url",
    [
        (CcdCompaniesView, "/ccd/companies/"),
        (CcdEmployeesView, "/ccd/employees/"),
    ],
)
def test_anonymous_is_refused(view, url):
    response = view.as_view()(_get(view, url, user=None))
    assert response.status_code in (401, 403)


# ─── companies ────────────────────────────────────────────────────────────────

def test_companies_pass_the_search_through():
    with patch(REPO) as repo:
        repo.list_companies.return_value = [{"id": 1, "name": "Weel Demo"}]
        response = CcdCompaniesView.as_view()(_get(CcdCompaniesView, "/ccd/companies/", q="  weel  "))
    assert response.status_code == 200
    assert response.data == [{"id": 1, "name": "Weel Demo"}]
    repo.list_companies.assert_called_once_with(search="weel")


def test_blank_search_is_no_search():
    """An empty box must list everything, not match the empty string."""
    with patch(REPO) as repo:
        repo.list_companies.return_value = []
        CcdCompaniesView.as_view()(_get(CcdCompaniesView, "/ccd/companies/", q="   "))
    repo.list_companies.assert_called_once_with(search=None)


def test_company_detail_404s_for_an_unknown_id():
    with patch(REPO) as repo:
        repo.get_company.return_value = None
        response = CcdCompanyDetailView.as_view()(_get(CcdCompanyDetailView), company_id=999)
    assert response.status_code == 404
    assert "detail" in response.data


def test_company_detail_returns_the_row():
    with patch(REPO) as repo:
        repo.get_company.return_value = {"id": 7, "name": "Toshkent", "users": 3}
        response = CcdCompanyDetailView.as_view()(_get(CcdCompanyDetailView), company_id=7)
    assert response.status_code == 200 and response.data["id"] == 7


# ─── workspaces and people ────────────────────────────────────────────────────

def test_workspaces_scope_to_one_company():
    with patch(REPO) as repo:
        repo.list_workspaces.return_value = []
        CcdWorkspacesView.as_view()(_get(CcdWorkspacesView, "/ccd/workspaces/", company_id="4"))
    repo.list_workspaces.assert_called_once_with(org_id=4)


def test_a_junk_company_id_lists_everything_rather_than_erroring():
    with patch(REPO) as repo:
        repo.list_workspaces.return_value = []
        response = CcdWorkspacesView.as_view()(_get(CcdWorkspacesView, "/ccd/workspaces/", company_id="abc"))
    assert response.status_code == 200
    repo.list_workspaces.assert_called_once_with(org_id=None)


def test_employees_honour_both_filters():
    with patch(REPO) as repo:
        repo.list_employees.return_value = []
        CcdEmployeesView.as_view()(_get(CcdEmployeesView, "/ccd/employees/", q="abbos", company_id="1"))
    repo.list_employees.assert_called_once_with(search="abbos", org_id=1)


# ─── limits ───────────────────────────────────────────────────────────────────

def test_calls_use_the_default_limit():
    with patch(REPO) as repo:
        repo.list_calls.return_value = []
        CcdCallsView.as_view()(_get(CcdCallsView, "/ccd/calls/"))
    repo.list_calls.assert_called_once_with(limit=200)


# The audit view's own default is 300; 0 and junk fall back to it, not to the calls default.
@pytest.mark.parametrize("asked, used", [("50", 50), ("100000", 1000), ("0", 300), ("-5", 1), ("abc", 300)])
def test_the_limit_is_clamped(asked, used):
    """`limit` is caller-supplied, so it must never become "select everything"."""
    with patch(REPO) as repo:
        repo.list_audit.return_value = []
        CcdAuditView.as_view()(_get(CcdAuditView, "/ccd/audit/", limit=asked))
    repo.list_audit.assert_called_once_with(limit=used)


# ─── desk actions ─────────────────────────────────────────────────────────────

def _post(view, body, user=ADMIN):
    request = factory.post("/", body, format="json")
    force_authenticate(request, user=user)
    return request


def test_blocking_a_person_passes_the_flag_through():
    with patch(REPO) as repo:
        repo.set_employee_active.return_value = {"id": 26, "full_name": "Abbos", "is_active": False}
        response = CcdEmployeeActiveView.as_view()(_post(CcdEmployeeActiveView, {"active": False}), employee_id=26)
    assert response.status_code == 200 and response.data["is_active"] is False
    repo.set_employee_active.assert_called_once_with(26, active=False)


def test_blocking_an_unknown_person_is_a_404():
    with patch(REPO) as repo:
        repo.set_employee_active.return_value = None
        response = CcdEmployeeActiveView.as_view()(_post(CcdEmployeeActiveView, {"active": False}), employee_id=999)
    assert response.status_code == 404


def test_the_active_flag_is_required():
    """Without it the view would have to guess, and guessing here blocks somebody."""
    with patch(REPO) as repo:
        response = CcdEmployeeActiveView.as_view()(_post(CcdEmployeeActiveView, {}), employee_id=26)
    assert response.status_code == 400
    repo.set_employee_active.assert_not_called()


def test_freezing_a_workspace():
    with patch(REPO) as repo:
        repo.set_workspace_active.return_value = {"id": 3, "name": "Bosh ofis", "is_active": False}
        response = CcdWorkspaceActiveView.as_view()(_post(CcdWorkspaceActiveView, {"active": False}), workspace_id=3)
    assert response.status_code == 200
    repo.set_workspace_active.assert_called_once_with(3, active=False)


def test_a_non_admin_cannot_block_anyone():
    with patch(REPO) as repo:
        response = CcdEmployeeActiveView.as_view()(
            _post(CcdEmployeeActiveView, {"active": False}, user=OPERATOR), employee_id=26
        )
    assert response.status_code == 403
    repo.set_employee_active.assert_not_called()
