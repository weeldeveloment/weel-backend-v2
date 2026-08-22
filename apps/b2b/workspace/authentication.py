from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from rest_framework import exceptions
from rest_framework.request import Request
from shared.jwt_authentication import DenylistCheckedJWTAuthentication

from users.tokens import TokenMetadata

from apps.b2b.repository import get_b2b_user
from apps.b2b.workspace.repository import ensure_workspace_employee, get_workspace_employee
from apps.b2b.workspace.roles import capabilities_for, is_manager
from apps.b2b.workspace.tokens import WORKSPACE_USER_TYPE

# The dashboard's own token type. Its subject is a ``b2b_user`` id, which is a
# different table from ``b2b_employee`` — the bridge below is the only place
# the two identities are allowed to meet.
DASHBOARD_USER_TYPE = "b2b"


class WorkspaceUser:
    """The signed-in employee, as seen by permissions and views."""

    is_authenticated = True
    is_active = True

    def __init__(self, employee: dict):
        self._data = employee
        self.id = employee["id"]
        self.employee_id = employee["id"]
        self.company_id = employee["company_id"]
        self.role = employee.get("role") or "employee"
        self.full_name = employee.get("full_name")
        self.phone = employee.get("phone")

    @property
    def is_manager(self) -> bool:
        return is_manager(self.role)

    @property
    def capabilities(self) -> dict[str, bool]:
        return capabilities_for(self.role)

    @property
    def visible_scope(self) -> int | None:
        """Passed to the repository as ``visible_to``: ``None`` means "the
        whole company", an id means "only this person's own work"."""
        return None if self.is_manager else self.id

    @property
    def task_scope(self) -> int | None:
        """The same thing for tasks — and deliberately ``None`` for everyone.

        The board is the company's work, not a private list: an employee opens
        the tasks screen to see what the team is doing and narrows to their own
        with the app's "Menikilar" toggle, which is a client-side filter over
        this response. Writing is still gated by role (``can_edit_task`` and
        friends), so a wider read does not let an employee touch anything they
        could not touch before. Kept as its own property rather than dropping
        the argument at every call site, so the policy has one place to live —
        and so the calendar, which really is private per person, keeps using
        ``visible_scope``.
        """
        return None

    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)


class WorkspaceJWTAuthentication(DenylistCheckedJWTAuthentication):
    """Authenticates the B2B mobile app. Ignores every other token type so it
    can sit alongside the dashboard and client authenticators."""

    def authenticate(self, request: Request):
        header = self.get_header(request)
        if header is None:
            return None

        raw_token = self.get_raw_token(header)
        if raw_token is None:
            return None

        validated_token = self.get_validated_token(raw_token)

        if validated_token.get(TokenMetadata.TOKEN_USER_TYPE) != WORKSPACE_USER_TYPE:
            return None

        subject = validated_token.get(TokenMetadata.TOKEN_SUBJECT)
        try:
            employee_id = int(subject)
        except (TypeError, ValueError):
            raise exceptions.AuthenticationFailed(
                _("Invalid workspace token subject"), code="invalid_workspace_token"
            )

        # Re-read the row instead of trusting the token's claims: an employee
        # deactivated or demoted mid-session must lose access immediately, not
        # when their access token happens to expire.
        employee = get_workspace_employee(employee_id)
        if not employee:
            raise exceptions.AuthenticationFailed(
                _("Employee not found or deactivated"), code="employee_not_found"
            )

        return WorkspaceUser(employee), validated_token


class DashboardWorkspaceAuthentication(DenylistCheckedJWTAuthentication):
    """Lets a **web dashboard** login act on the workspace endpoints.

    Tasks, chat, the calendar and the lead board are the same company's data
    whichever screen it is opened from, but the dashboard signs in as a
    ``b2b_user`` while every workspace row references ``b2b_employee(id)``.
    Rather than asking an owner to log in twice — once for the dashboard, once
    for the workspace — this resolves the dashboard account to the employee
    row that represents it, exactly as the workspace login does via
    ``_resolve_employee``, and hands the views the ``WorkspaceUser`` they
    already know how to reason about. Permissions are then unchanged: the
    role on the employee row decides what this person may do.

    Deliberately *not* in ``DEFAULT_AUTHENTICATION_CLASSES``: a dashboard token
    must keep resolving to ``B2BAuthUser`` everywhere else, or the company
    endpoints would start reading an employee id as a dashboard user id. It is
    attached only to ``WorkspaceAPIView``.
    """

    def authenticate(self, request: Request):
        header = self.get_header(request)
        if header is None:
            return None

        raw_token = self.get_raw_token(header)
        if raw_token is None:
            return None

        validated_token = self.get_validated_token(raw_token)

        if validated_token.get(TokenMetadata.TOKEN_USER_TYPE) != DASHBOARD_USER_TYPE:
            return None

        subject = validated_token.get(TokenMetadata.TOKEN_SUBJECT)
        try:
            user_id = int(subject)
        except (TypeError, ValueError):
            raise exceptions.AuthenticationFailed(
                _("Invalid B2B token subject"), code="invalid_b2b_token"
            )

        b2b_user = get_b2b_user(user_id)
        if not b2b_user:
            raise exceptions.AuthenticationFailed(
                _("B2B user not found"), code="b2b_user_not_found"
            )

        # An owner created by ``create_b2b_owner`` has no roster row until
        # something needs one; the dashboard opening the tasks page is such a
        # moment. Everyone else matches an existing employee by phone.
        employee = ensure_workspace_employee(b2b_user)
        if not employee:
            raise exceptions.AuthenticationFailed(
                _("No workspace employee for this account"), code="employee_not_found"
            )

        return WorkspaceUser(employee), validated_token
