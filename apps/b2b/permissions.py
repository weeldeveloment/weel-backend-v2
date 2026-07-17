from __future__ import annotations

from rest_framework.permissions import BasePermission

from apps.b2b.models import B2BUserRole


class IsB2BPerformer(BasePermission):
    """Restricts an endpoint to B2B "executer" (performer) users.

    Owners set limits/policies; executers are the ones who actually pick
    hotels and send employees on business trips.
    """
    message = "Only company executers (performers) can perform this action."

    def has_permission(self, request, view) -> bool:
        user = request.user
        role = user.get("role") if isinstance(user, dict) else getattr(user, "role", None)
        return bool(user and getattr(user, "is_authenticated", False) and role == B2BUserRole.PERFORMER)


class IsB2BOwner(BasePermission):
    """Restricts an endpoint to B2B "owner" users — e.g. reviewing (approving/
    rejecting) budget requests submitted by executers."""
    message = "Only company owners can perform this action."

    def has_permission(self, request, view) -> bool:
        user = request.user
        role = user.get("role") if isinstance(user, dict) else getattr(user, "role", None)
        return bool(user and getattr(user, "is_authenticated", False) and role == B2BUserRole.OWNER)


class IsB2BOwnerOrPerformer(BasePermission):
    """Allows access to either a B2B owner or performer."""
    message = "Only company owners or executers (performers) can perform this action."

    def has_permission(self, request, view) -> bool:
        user = request.user
        role = user.get("role") if isinstance(user, dict) else getattr(user, "role", None)
        return bool(
            user
            and getattr(user, "is_authenticated", False)
            and role in {B2BUserRole.OWNER, B2BUserRole.PERFORMER}
        )
