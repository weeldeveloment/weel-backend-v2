from __future__ import annotations

from rest_framework.permissions import BasePermission

from apps.b2b.workspace.access import Module, Permission
from apps.b2b.workspace.authentication import WorkspaceUser


class CanCreateTrip(BasePermission):
    """TZ §9 — who may start a business trip.

    Only gates a workspace employee. A traveller booking their own stay is
    not a `WorkspaceUser` and is waved through unchanged — the TZ's Trips
    permission governs a company's roster, not individual customers, and
    this app serves both. Owner and admin hold every permission already;
    manager gets `trips.create` by default; employee does not (though a
    workspace may grant it); guest never opens the module at all, so
    `opens` alone stops them before `may` is even asked.
    """

    message = "Your role does not allow creating a business trip."

    def has_permission(self, request, view) -> bool:
        user = request.user
        if not isinstance(user, WorkspaceUser):
            return True
        return user.opens(Module.TRIPS) and user.may(Permission.TRIP_CREATE)
