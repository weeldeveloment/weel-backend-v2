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


class HasModule(BasePermission):
    """Closes a whole section of the workspace to a guest who was not lent it.

    Views declare ``required_module = Module.SALES``. A permanent employee has
    no module grant and passes every one of these; somebody seconded here to
    help with the sales board is stopped at the calendar, the task list and the
    chat unless those were named in the request they accepted.

    Separate from [HasCapability] because the two answer different questions
    and only one of them covers reading. A capability is "may you *do* this" —
    create a task, post a lead — and a guest with no `vazifa` grant already
    fails those. But `GET /tasks/` has no capability behind it at all: every
    role may read the board, so without this the app could hide the tab while
    the endpoint went on answering. Hiding a tab is not access control.
    """

    message = "This part of the workspace was not shared with you."

    def has_permission(self, request, view) -> bool:
        if not isinstance(request.user, WorkspaceUser):
            return False

        # A chat-only member is in one conversation and nowhere else. The TZ
        # is explicit that they get no workspace role and see no other module,
        # and that a file reachable in a chat does not thereby open Files —
        # so every module gate refuses them, chat included. Their access to
        # the conversation itself is decided by chat membership, which is a
        # different question and a different table.
        if request.user.get("is_chat_only"):
            return False

        module = getattr(view, "required_module", None)
        if module is None:
            return True
        modules = request.user.modules
        # None is a permanent employee: their role is the whole story, and a
        # module list is a guest's narrowing rather than a universal gate.
        return modules is None or module in modules


class HasPermission(BasePermission):
    """Checks one named permission from the TZ's catalogue.

    Views declare ``required_permission = Permission.EMPLOYEE_CHANGE_ROLE``.
    Distinct from [HasCapability], which reads the older role-derived map: this
    one reads what the workspace's role editor actually wrote, so a permission
    an administrator withdrew this morning is refused this afternoon without a
    deployment.
    """

    message = "You do not have permission for this action."

    def has_permission(self, request, view) -> bool:
        if not isinstance(request.user, WorkspaceUser):
            return False
        permission = getattr(view, "required_permission", None)
        if permission is None:
            return True
        return request.user.may(permission)
