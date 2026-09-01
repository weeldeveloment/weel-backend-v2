"""Who may plug an outside service into the workspace.

The owner, the workspace administrator — "lider" in the column, which
[Role.clean] canonicalises to `admin` — and the manager ("rahbar"). The
manager is in because the thing being connected is the sales funnel's supply:
every Meta lead lands on the board they run, and a funnel whose source can
only be changed by asking the owner is one that stays broken all week. It
stops there. Connecting hands us a token to the company's Facebook account,
which is not a decision to leave with the roster at large.

Enforced here *and* reported to the app as `can_manage_integrations` so the
row on the profile screen and the endpoint agree — see
`access.capabilities_from`. Hiding the row is not the control; this is.
"""
from __future__ import annotations

from rest_framework.permissions import BasePermission

from apps.b2b.workspace.access import Role
from apps.b2b.workspace.authentication import WorkspaceUser


def may_manage_integrations(role: str | None) -> bool:
    return Role.clean(role) in Role.INTEGRATION_ROLES


class CanManageIntegrations(BasePermission):
    message = (
        "Only the workspace owner, an administrator or a manager "
        "can manage integrations."
    )

    def has_permission(self, request, view) -> bool:
        return (
            isinstance(request.user, WorkspaceUser)
            and may_manage_integrations(getattr(request.user, "role", None))
        )
