from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from rest_framework import exceptions
from rest_framework.request import Request
from shared.jwt_authentication import DenylistCheckedJWTAuthentication

from users.tokens import TokenMetadata

from apps.b2b.workspace.repository import get_workspace_employee
from apps.b2b.workspace.roles import capabilities_for, is_manager
from apps.b2b.workspace.tokens import WORKSPACE_USER_TYPE


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
