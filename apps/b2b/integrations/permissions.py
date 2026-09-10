"""Who may plug an outside service into the workspace, and who may unplug it.

Two rules, because the two acts are not the same size:

* **Connecting Meta** — everybody on the roster but a guest (the owner's call,
  2026-09-11). Whoever runs the company's ads may well be an ordinary
  employee, and a connection only ever *adds* the pages that person signs in
  with: the callback never replaces another account's pages and never drops
  them (see `public_views.MetaOAuthCallbackView`). The same people may look at
  the connection and ask it to fetch recent leads.

* **Everything that takes away** — unplugging Meta, pausing a page — and the
  AI assistants' keys belong to the owner, the administrator ("lider") and the
  manager ("rahbar"), plus whoever made the Meta connection in the first
  place: an employee who connected the company's pages by mistake can undo it
  without asking, but cannot pull the plug on the owner's.

Enforced here *and* reported to the app — `can_manage_integrations` opens the
screen, `can_disconnect` on Meta's row draws the button — so the screen and
the endpoints agree. Hiding a button is not the control; this is.
"""
from __future__ import annotations

from typing import Any

from rest_framework.permissions import BasePermission

from apps.b2b.workspace.access import Role
from apps.b2b.workspace.authentication import WorkspaceUser


def may_manage_integrations(role: str | None) -> bool:
    """The AI keys, and taking a Meta connection away from somebody else."""
    return Role.clean(role) in Role.INTEGRATION_ROLES


def may_connect_meta(role: str | None) -> bool:
    return Role.clean(role) in Role.META_ROLES


def may_unplug_meta(user, integration: dict[str, Any] | None) -> bool:
    """Disconnect, or pause a page: the managing roles, or the person who
    connected it."""
    if may_manage_integrations(getattr(user, "role", None)):
        return True
    return bool(
        integration
        and integration.get("connected_by_id")
        and integration["connected_by_id"] == getattr(user, "id", None)
        and may_connect_meta(getattr(user, "role", None))
    )


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


class CanConnectMeta(BasePermission):
    message = "A guest cannot connect Meta."

    def has_permission(self, request, view) -> bool:
        return (
            isinstance(request.user, WorkspaceUser)
            and may_connect_meta(getattr(request.user, "role", None))
        )
