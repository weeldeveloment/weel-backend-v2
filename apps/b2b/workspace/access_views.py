"""Managing who may do what: the role editor, per-person access, and the audit.

The TZ's rule that these endpoints exist to serve is short and absolute:
hiding a button is not a security mechanism. Everything here writes what the
server will actually check, and the catalogue endpoint exists so the app can
render an editor for it without shipping its own copy of the list — a copy
that would drift the first time a permission was added.
"""
from __future__ import annotations

from django.utils.translation import gettext as _
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.b2b.models import LeadStatus
from apps.b2b.workspace import access_repository as arepo
from apps.b2b.workspace import repository as repo
from apps.b2b.workspace.access import Module, Permission, Role
from apps.b2b.workspace.permissions import IsWorkspaceManager, IsWorkspaceUser
from apps.b2b.workspace.views import WORKSPACE_TAG, WorkspaceAPIView


class RoleAccessSerializer(serializers.Serializer):
    modules = serializers.ListField(
        child=serializers.CharField(), allow_empty=True
    )
    permissions = serializers.ListField(
        child=serializers.CharField(), allow_empty=True
    )


class EmployeeAccessSerializer(serializers.Serializer):
    """One person's standing, as the employee editor saves it.

    All three fields are optional and each is applied on its own: changing
    somebody's role without touching their overrides is the ordinary edit, and
    a payload that carried every field would silently reset the other two.

    `modules` and `permissions` accept null explicitly — that is "by role",
    which is how an override is removed rather than emptied. An empty list and
    "no override" are different answers: one is access to nothing, the other
    is access to whatever the role says.
    """

    role = serializers.ChoiceField(choices=Role.CHOICES, required=False)
    modules = serializers.ListField(
        child=serializers.CharField(), required=False, allow_null=True
    )
    permissions = serializers.ListField(
        child=serializers.CharField(), required=False, allow_null=True
    )


class WorkspaceAccessCatalogueView(WorkspaceAPIView):
    """GET /api/b2b/workspace/access/catalogue/ — every role, module and
    permission this build knows about, with the labels to draw them.

    Read-only and the same for every workspace: it is the vocabulary, not the
    policy. The app renders the role editor from this rather than from its own
    hard-coded list, so a permission added on the server appears in the editor
    without an app release.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG, operation_summary="Roles, modules and permissions"
    )
    def get(self, request):
        return Response({
            "roles": [
                {"code": code, "label": Role.label(code)} for code in Role.CHOICES
            ],
            "modules": [
                {
                    "code": module,
                    "label": Module.LABELS[module],
                    "permissions": [
                        {"code": permission, "label": _permission_label(permission)}
                        for permission in Permission.BY_MODULE[module]
                    ],
                }
                for module in Module.CHOICES
            ],
        })


def _permission_label(permission: str) -> str:
    """The verb, in Uzbek. Derived from the permission's own name rather than
    listed separately, so a new permission cannot be added without one."""
    verb = permission.split(".", 1)[1]
    return _VERB_LABELS.get(verb, verb.replace("_", " ").capitalize())


_VERB_LABELS = {
    "view": "Ko’rish",
    "create": "Yaratish",
    "create_own": "O’zi uchun yaratish",
    "edit": "Tahrirlash",
    "delete": "O’chirish",
    "assign": "Biriktirish",
    "reassign": "Qayta biriktirish",
    "comment": "Izoh qoldirish",
    "export": "Eksport",
    "send": "Xabar yuborish",
    "delete_own": "O’z xabarini o’chirish",
    "manage_group": "Guruhni boshqarish",
    "change_stage": "Bosqichni o’zgartirish",
    "manage_pipeline": "Voronkani boshqarish",
    "invite": "Taklif qilish",
    "upload": "Yuklash",
    "download": "Yuklab olish",
    "create_folder": "Papka yaratish",
    "manage_access": "Kirishni boshqarish",
    "manage": "Boshqarish",
    "change_role": "Rolni o’zgartirish",
    "change_modules": "Modullarni o’zgartirish",
    "change_permissions": "Huquqlarni o’zgartirish",
    "remove_from_workspace": "Ish joyidan chiqarish",
    "remove_from_company": "Kompaniyadan chiqarish",
}


class WorkspaceRoleListView(WorkspaceAPIView):
    """GET /api/b2b/workspace/access/roles/ — what each role may do here."""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG, operation_summary="This workspace's role configuration"
    )
    def get(self, request):
        configured = arepo.list_role_config(request.user.company_id)
        results = []
        for code in Role.CHOICES:
            modules, permissions = arepo.role_access(request.user.company_id, code)
            results.append({
                "code": code,
                "label": Role.label(code),
                "modules": modules,
                "permissions": permissions,
                # Whether this workspace has moved away from the defaults.
                # Worth saying: an administrator looking at the editor should
                # be able to tell policy from what merely came with the box.
                "is_customised": code in configured,
            })
        return Response({"results": results})


class WorkspaceRoleDetailView(WorkspaceAPIView):
    """PUT /api/b2b/workspace/access/roles/<code>/ — change what a role may do.

    Roles themselves cannot be created or removed — the TZ forbids it for the
    MVP — so there is no POST and no DELETE here. What a role *is* stays; what
    it may do is this workspace's to decide.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]
    required_permission = Permission.EMPLOYEE_CHANGE_PERMISSIONS

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Set a role's modules and permissions",
        request_body=RoleAccessSerializer,
    )
    def put(self, request, code: str):
        if Role.clean(code) != code or code not in Role.CHOICES:
            return Response(
                {"detail": _("No such role.")}, status=status.HTTP_404_NOT_FOUND
            )
        # An owner's reach is what the Company is. Letting it be edited would
        # allow an administrator to lock the owner out of their own company,
        # which no amount of undo makes safe.
        if code == Role.OWNER:
            return Response(
                {"detail": _("The owner's access cannot be narrowed.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = RoleAccessSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        saved = arepo.set_role_access(
            request.user.company_id,
            code,
            modules=serializer.validated_data["modules"],
            permissions=serializer.validated_data["permissions"],
            actor_employee_id=request.user.id,
        )
        return Response({"code": code, "label": Role.label(code), **saved})


class WorkspaceEmployeeAccessView(WorkspaceAPIView):
    """GET/PUT /api/b2b/workspace/employees/<id>/access/ — one person's standing."""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG, operation_summary="What one employee may do"
    )
    def get(self, request, employee_id: int):
        employee = self._load(request, employee_id)
        if not employee:
            return Response(
                {"detail": _("Employee not found.")}, status=status.HTTP_404_NOT_FOUND
            )
        modules, permissions = arepo.access_for_employee(employee)
        return Response({
            "employee_id": employee["id"],
            "role": Role.clean(employee.get("role")),
            "role_label": Role.label(employee.get("role")),
            "modules": modules,
            "permissions": permissions,
            # Null means "by role". The editor needs the difference to draw
            # its "По роли / Настроить" toggle in the right position.
            "module_override": employee.get("module_access"),
            "permission_override": employee.get("permission_access"),
        })

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Set one employee's role and access",
        request_body=EmployeeAccessSerializer,
    )
    def put(self, request, employee_id: int):
        employee = self._load(request, employee_id)
        if not employee:
            return Response(
                {"detail": _("Employee not found.")}, status=status.HTTP_404_NOT_FOUND
            )

        serializer = EmployeeAccessSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if "role" in data:
            if not request.user.may(Permission.EMPLOYEE_CHANGE_ROLE):
                return Response(
                    {"detail": _("You may not change roles.")},
                    status=status.HTTP_403_FORBIDDEN,
                )
            if Role.clean(employee.get("role")) == Role.OWNER:
                # The owner is the company. Handing that over is a transfer of
                # ownership, not an edit on the employee screen.
                return Response(
                    {"detail": _("The owner's role cannot be changed here.")},
                    status=status.HTTP_403_FORBIDDEN,
                )
            arepo.set_employee_role(
                employee_id,
                data["role"],
                company_id=request.user.company_id,
                actor_employee_id=request.user.id,
            )

        touches_access = "modules" in data or "permissions" in data
        if touches_access:
            needed = (
                Permission.EMPLOYEE_CHANGE_MODULES
                if "modules" in data
                else Permission.EMPLOYEE_CHANGE_PERMISSIONS
            )
            if not request.user.may(needed):
                return Response(
                    {"detail": _("You may not change access.")},
                    status=status.HTTP_403_FORBIDDEN,
                )
            arepo.set_employee_access(
                employee_id,
                modules=data.get("modules") if "modules" in data else arepo.KEEP,
                permissions=(
                    data.get("permissions") if "permissions" in data else arepo.KEEP
                ),
                company_id=request.user.company_id,
                actor_employee_id=request.user.id,
            )

        return self.get(request, employee_id)

    def _load(self, request, employee_id: int):
        employee = repo.get_workspace_employee(employee_id)
        # Scoped to the caller's own workspace. Without this an id from
        # another company would be readable — and writable — through here.
        if not employee or employee["company_id"] != request.user.company_id:
            return None
        return employee


class EmployeeRemoveSerializer(serializers.Serializer):
    #: The TZ's two separate rights-matrix rows: ending somebody's standing
    #: in this one workspace, or in every workspace under the org.
    scope = serializers.ChoiceField(choices=["workspace", "company"], default="workspace")


class WorkspaceEmployeeRemoveView(WorkspaceAPIView):
    """POST /api/b2b/workspace/employees/<id>/remove/ — end a member's
    standing. Deactivates rather than deletes: their tasks, leads and history
    keep the name that was on them.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Remove a member",
        request_body=EmployeeRemoveSerializer,
    )
    def post(self, request, employee_id: int):
        target = repo.get_workspace_employee(employee_id)
        if not target or target["company_id"] != request.user.company_id:
            return Response(
                {"detail": _("Employee not found.")}, status=status.HTTP_404_NOT_FOUND
            )
        if Role.clean(target.get("role")) == Role.OWNER:
            return Response(
                {"detail": _("The owner cannot be removed.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = EmployeeRemoveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        scope = serializer.validated_data["scope"]

        needed = (
            Permission.EMPLOYEE_REMOVE_COMPANY
            if scope == "company"
            else Permission.EMPLOYEE_REMOVE_WORKSPACE
        )
        if not request.user.may(needed):
            return Response(
                {"detail": _("You may not remove this member.")},
                status=status.HTTP_403_FORBIDDEN,
            )
        # "Only a lower role" — the TZ's own qualifier on this row. An owner's
        # rank already outranks the whole roster, so this never narrows them.
        if not arepo.outranks(request.user.role, target.get("role")):
            return Response(
                {"detail": _("You may only remove somebody ranked below you.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        arepo.remove_employee(
            employee_id,
            company_id=request.user.company_id,
            scope=scope,
            actor_employee_id=request.user.id,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class OwnershipRequestSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(
        choices=[arepo.OwnershipRequestKind.TRANSFER, arepo.OwnershipRequestKind.CLOSE]
    )
    target_employee_id = serializers.IntegerField(required=False, allow_null=True)
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class WorkspaceOwnershipRequestView(WorkspaceAPIView):
    """GET/POST /api/b2b/workspace/company/ownership-requests/ — handing the
    company over, or closing it, neither of which this endpoint ever does
    itself.

    Owner only, and on their own company only: an admin or a manager runs a
    workspace, not the Company it belongs to, and the whole point of routing
    this through `admin_auth` is that nobody inside the workspace — owner
    included — can make either thing happen by themselves. See the note on
    `Role.OWNER` in `access.py`.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    def _require_owner(self, request):
        if Role.clean(request.user.role) != Role.OWNER:
            return Response(
                {"detail": _("Only the owner may do this.")},
                status=status.HTTP_403_FORBIDDEN,
            )
        return None

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="This workspace's ownership/closure requests",
    )
    def get(self, request):
        refused = self._require_owner(request)
        if refused:
            return refused
        return Response({
            "results": arepo.list_own_ownership_requests(request.user.company_id)
        })

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Ask to transfer or close the company",
        request_body=OwnershipRequestSerializer,
    )
    def post(self, request):
        refused = self._require_owner(request)
        if refused:
            return refused

        serializer = OwnershipRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            created = arepo.create_ownership_request(
                company_id=request.user.company_id,
                requested_by=request.user.id,
                kind=data["kind"],
                target_employee_id=data.get("target_employee_id"),
                reason=data.get("reason", ""),
            )
        except arepo.OwnershipRequestError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(created, status=status.HTTP_201_CREATED)


class WorkspaceDeleteRequestSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class WorkspaceDeleteRequestView(WorkspaceAPIView):
    """GET/POST /api/b2b/workspace/delete-requests/ — TZ §4: asking to delete
    *this one workspace*, as opposed to [WorkspaceOwnershipRequestView]'s
    company-wide close, which only WEEL's own desk can grant. A leader (or
    anybody `IsWorkspaceManager` lets through) may ask; only this workspace's
    own owner may grant it — see [WorkspaceDeleteRequestDecideView].
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser, IsWorkspaceManager]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG, operation_summary="This workspace's deletion requests"
    )
    def get(self, request):
        return Response({
            "results": arepo.list_workspace_delete_requests(request.user.company_id)
        })

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Ask to delete this workspace",
        request_body=WorkspaceDeleteRequestSerializer,
    )
    def post(self, request):
        serializer = WorkspaceDeleteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            created = arepo.request_workspace_deletion(
                company_id=request.user.company_id,
                requested_by=request.user.id,
                reason=serializer.validated_data.get("reason", ""),
            )
        except arepo.OwnershipRequestError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(created, status=status.HTTP_201_CREATED)


class WorkspaceDeleteRequestDecideView(WorkspaceAPIView):
    """POST /api/b2b/workspace/delete-requests/<id>/<approve|reject>/

    Owner only, and only on this same workspace's own pending request — the
    TZ's "Владелец получает запрос... принимает или отклоняет". Approving
    marks this workspace `is_active = FALSE`; the org above it and its other
    workspaces are untouched.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG, operation_summary="Decide a workspace deletion request"
    )
    def post(self, request, request_id: int, action: str):
        if Role.clean(request.user.role) != Role.OWNER:
            return Response(
                {"detail": _("Only the owner may decide this.")},
                status=status.HTTP_403_FORBIDDEN,
            )
        if action not in ("approve", "reject"):
            return Response(
                {"detail": _("Unknown action.")}, status=status.HTTP_400_BAD_REQUEST
            )
        updated = arepo.decide_workspace_deletion(
            request_id,
            company_id=request.user.company_id,
            approve=action == "approve",
            reviewer_employee_id=request.user.id,
        )
        if not updated:
            return Response(
                {"detail": _("This request has already been answered.")},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(updated)


class WorkspaceAuditView(WorkspaceAPIView):
    """GET /api/b2b/workspace/audit/ — role changes, access changes, deletions."""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="The workspace's audit log",
        manual_parameters=[
            openapi.Parameter("limit", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
        ],
    )
    def get(self, request):
        # Reading the audit is itself administrative: it names who changed
        # whose access and when.
        if Role.clean(request.user.role) not in Role.ADMINISTRATIVE:
            return Response(
                {"detail": _("Only an owner or administrator may read the audit.")},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            limit = min(int(request.query_params.get("limit", 100)), 500)
        except (TypeError, ValueError):
            limit = 100
        return Response(
            {"results": arepo.list_audit(request.user.company_id, limit=limit)}
        )


class WorkspaceTrashView(WorkspaceAPIView):
    """GET /api/b2b/workspace/trash/ — what has been deleted and can come back.

    Behind a permission of its own rather than shown to everybody: the TZ says
    an ordinary user does not see deleted objects at all, and a bin that
    anybody can read is a way to see the deal somebody removed this morning.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG, operation_summary="Deleted tasks and deals"
    )
    def get(self, request):
        may_tasks = request.user.may(Permission.TASK_DELETE)
        may_leads = request.user.may(Permission.DEAL_DELETE)
        if not (may_tasks or may_leads):
            return Response(
                {"detail": _("You may not see deleted objects.")},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response({
            # Each half is gated on its own: somebody who may delete tasks and
            # not deals sees the tasks and not the deals.
            "tasks": repo.list_deleted_tasks(request.user.company_id) if may_tasks else [],
            "leads": repo.list_deleted_leads(request.user.company_id) if may_leads else [],
        })


class WorkspaceRestoreView(WorkspaceAPIView):
    """POST /api/b2b/workspace/trash/<kind>/<id>/restore/ — put one back."""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Restore a deleted object")
    def post(self, request, kind: str, object_id: int):
        if kind not in {"tasks", "leads"}:
            return Response(
                {"detail": _("Not something that can be restored.")},
                status=status.HTTP_404_NOT_FOUND,
            )
        needed = (
            Permission.TASK_DELETE if kind == "tasks" else Permission.DEAL_DELETE
        )
        # Restoring is the same authority as deleting. Splitting them would
        # mean somebody who cannot remove a deal could put back one that was
        # removed deliberately.
        if not request.user.may(needed):
            return Response(
                {"detail": _("You may not restore this.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        restore = repo.restore_task if kind == "tasks" else repo.restore_lead
        if not restore(object_id, request.user.company_id):
            return Response(
                {"detail": _("Nothing to restore.")}, status=status.HTTP_404_NOT_FOUND
            )
        arepo.record_audit(
            request.user.company_id,
            actor_employee_id=request.user.id,
            action=f"{kind[:-1]}.restored",
            target_type=kind[:-1],
            target_id=object_id,
        )
        return Response({"restored": True})


class WorkspacePurgeView(WorkspaceAPIView):
    """DELETE /api/b2b/workspace/trash/<kind>/<id>/ — destroy one for good.

    The other half of a bin. Without it the only way out of the trash was back
    into the working set, so something deleted by mistake and something deleted
    on purpose sat in the same list for the life of the company — which is what
    the screen's "Butunlay o'chirish" is for.

    Gated on the same permission as deleting and restoring, and — in the
    repository — on the row already being in the bin. Nothing live can be
    reached through this endpoint: an id that was never deleted answers 404
    exactly as an id that never existed.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG, operation_summary="Permanently delete a binned object"
    )
    def delete(self, request, kind: str, object_id: int):
        if kind not in {"tasks", "leads"}:
            return Response(
                {"detail": _("Not something that can be deleted.")},
                status=status.HTTP_404_NOT_FOUND,
            )
        needed = (
            Permission.TASK_DELETE if kind == "tasks" else Permission.DEAL_DELETE
        )
        if not request.user.may(needed):
            return Response(
                {"detail": _("You may not delete this.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        purge = repo.purge_task if kind == "tasks" else repo.purge_lead
        if not purge(object_id, request.user.company_id):
            return Response(
                {"detail": _("Nothing to delete.")}, status=status.HTTP_404_NOT_FOUND
            )
        # The audit log is the only trace a permanent deletion leaves anywhere,
        # so it is written even though the row it names is already gone.
        arepo.record_audit(
            request.user.company_id,
            actor_employee_id=request.user.id,
            action=f"{kind[:-1]}.purged",
            target_type=kind[:-1],
            target_id=object_id,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkspaceArchiveView(WorkspaceAPIView):
    """GET /api/b2b/workspace/archive/ — "История и архив": every completed
    or deleted task, lead and quick sale, read only.

    A different door onto rows [WorkspaceTrashView] reads only the deleted
    half of, open to the whole company rather than gated on the authority to
    delete: the point is that nobody — owner, lead, or anyone else — can make
    a deal or a task vanish from what the company can see, only from the
    working list. There is no restore or purge here on purpose; those stay
    behind the delete permission on the trash screen, because undoing or
    finishing a removal is a different authority from being able to see that
    it happened.

    The TZ names two states this section shows side by side rather than one:
    finished work still on the board (``completed``) and work somebody took
    off it (``deleted``) — different facts, so the response keeps them apart
    rather than merging into one list a screen would have to re-split.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG, operation_summary="Completed and deleted tasks, leads and quick sales"
    )
    def get(self, request):
        company_id = request.user.company_id
        return Response({
            "completed": {
                "tasks": repo.list_tasks(company_id, status="done"),
                "leads": repo.list_leads(company_id, status=LeadStatus.COMPLETED, kind=repo.LEAD_KIND_ANY),
            },
            "deleted": {
                "tasks": repo.list_deleted_tasks(company_id),
                "leads": repo.list_deleted_leads(company_id),
            },
            # Kept for callers still reading the pre-TZ shape — the same rows
            # as `deleted` above, flat.
            "tasks": repo.list_deleted_tasks(company_id),
            "leads": repo.list_deleted_leads(company_id),
        })
