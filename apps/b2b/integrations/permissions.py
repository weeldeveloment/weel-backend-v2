"""Who may plug an outside service into the workspace.

The owner and the workspace administrator — "lider" in the column, which
[Role.clean] canonicalises to `admin`. Not a manager: connecting Meta points
somebody else's advertising at this company's sales board and hands us a token
to their Facebook account, which is a decision about the company rather than
about the work being handed out. Same two roles that may ask another
workspace for people (`roles.REQUEST_ROLES`), for the same reason.

Enforced here *and* reported to the app as `can_manage_integrations` so the
row on the profile screen and the endpoint agree — see
`access.capabilities_from`. Hiding the row is not the control; this is.
"""
from __future__ import annotations

from rest_framework.permissions import BasePermission

from apps.b2b.workspace.access import Role
from apps.b2b.workspace.authentication import WorkspaceUser


def may_manage_integrations(role: str | None) -> bool:
    return Role.clean(role) in Role.ADMINISTRATIVE


class CanManageIntegrations(BasePermission):
    message = "Only the workspace owner or an administrator can manage integrations."

    def has_permission(self, request, view) -> bool:
        return (
            isinstance(request.user, WorkspaceUser)
            and may_manage_integrations(getattr(request.user, "role", None))
        )
