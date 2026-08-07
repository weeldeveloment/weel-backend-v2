from __future__ import annotations

from rest_framework.permissions import BasePermission

from apps.b2b.workspace.authentication import WorkspaceUser


class IsWorkspaceUser(BasePermission):
    """Any employee signed into the mobile workspace."""

    message = "This endpoint requires a B2B workspace (mobile) login."

    def has_permission(self, request, view) -> bool:
        return isinstance(request.user, WorkspaceUser)


class IsWorkspaceManager(BasePermission):
    """Owner or performer — the roles allowed to hand out work.

    Plain employees are stopped here rather than inside each view, so a new
    manager-only endpoint cannot forget the check.
    """

    message = "Only company owners and managers can perform this action."

    def has_permission(self, request, view) -> bool:
        return isinstance(request.user, WorkspaceUser) and request.user.is_manager


class HasCapability(BasePermission):
    """Checks one named capability from ``roles.capabilities_for``.

    Views declare ``required_capability = "can_create_task"`` so the rule the
    API enforces and the flag the app hides its button on are the same value.
    """

    message = "Your role does not allow this action."

    def has_permission(self, request, view) -> bool:
        if not isinstance(request.user, WorkspaceUser):
            return False
        capability = getattr(view, "required_capability", None)
        if capability is None:
            return True
        return bool(request.user.capabilities.get(capability))
