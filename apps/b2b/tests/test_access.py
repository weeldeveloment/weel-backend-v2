"""Who → where → what.

The TZ states the access model as three questions asked in order, and two
rules that fall out of the ordering. Both are the kind of thing that is
obviously true until somebody adds an endpoint, so they are pinned here:

  * a permission whose module is closed grants nothing — not less, nothing;
  * an individual grant replaces the role's, and never creates a new role.

Plus the boundaries around editing access at all: an owner cannot be narrowed
or demoted through the employee screen, and reading the audit is itself
administrative.
"""
from unittest.mock import patch

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.workspace.access import (
    DEFAULT_MODULES,
    DEFAULT_PERMISSIONS,
    Module,
    Permission,
    Role,
    default_access,
    resolve,
)
from apps.b2b.workspace.access_views import (
    WorkspaceAccessCatalogueView,
    WorkspaceAuditView,
    WorkspaceDeleteRequestDecideView,
    WorkspaceDeleteRequestView,
    WorkspaceEmployeeAccessView,
    WorkspaceEmployeeRemoveView,
    WorkspaceRoleDetailView,
    WorkspaceRoleListView,
)
from apps.b2b.workspace.authentication import WorkspaceUser

COMPANY = 10
factory = APIRequestFactory()


def _user(role: str, employee_id: int = 1, **extra) -> WorkspaceUser:
    return WorkspaceUser({
        "id": employee_id,
        "company_id": COMPANY,
        "role": role,
        "full_name": "Test Person",
        **extra,
    })


def _call(view_class, request, user, **kwargs):
    force_authenticate(request, user=user)
    return view_class.as_view()(request, **kwargs)


def _granting(*permissions):
    """Answer this user's access without a database.

    `HasPermission` runs before the view body, so even a test about a 404 has
    to get past the gate first.
    """
    return patch(
        "apps.b2b.workspace.access_repository.access_for_employee",
        return_value=(Module.CHOICES, list(permissions)),
    )


# ─── The catalogue ────────────────────────────────────────────────────────────

def test_there_are_exactly_five_roles():
    """The TZ forbids creating new ones in the MVP, so this is a fence."""
    assert Role.CHOICES == ["owner", "admin", "manager", "employee", "guest"]


def test_every_permission_belongs_to_a_module_that_exists():
    for module, permissions in Permission.BY_MODULE.items():
        assert module in Module.CHOICES
        for permission in permissions:
            assert Permission.module_of(permission) == module, permission


def test_every_module_has_at_least_a_view_permission():
    """A module you can open but do nothing in is a tab that does nothing."""
    for module in Module.CHOICES:
        assert f"{module}.view" in Permission.BY_MODULE[module], module


def test_the_roles_the_roster_already_stores_still_resolve():
    # `performer` has meant "manager" since the dashboard was written, and
    # `lider` was the workspace administrator before the TZ named it.
    assert Role.clean("performer") == Role.MANAGER
    assert Role.clean("lider") == Role.ADMIN
    assert Role.clean("ghost") == Role.GUEST
    # Anything unrecognised is the narrowest thing that is still a member.
    assert Role.clean("wizard") == Role.EMPLOYEE
    assert Role.clean(None) == Role.EMPLOYEE


def test_writing_a_role_lands_in_the_vocabulary_the_roster_column_reads():
    """`to_storage` is the only door back to `b2b_employee.role` — a write
    through `Role.clean` alone would store `"admin"`/`"manager"` into a
    column `views.py` and `secondment.py` still compare against
    `"lider"`/`"performer"` directly, and those rows would stop matching
    either vocabulary's checks."""
    from apps.b2b.models import EmployeeRole
    from apps.b2b.workspace.roles import to_storage

    assert to_storage(Role.ADMIN) == EmployeeRole.LIDER
    assert to_storage(Role.MANAGER) == EmployeeRole.PERFORMER
    assert to_storage(Role.OWNER) == EmployeeRole.OWNER
    assert to_storage(Role.EMPLOYEE) == EmployeeRole.EMPLOYEE
    assert to_storage(Role.GUEST) == EmployeeRole.GUEST

    # And the round trip: whatever is written is exactly what `Role.clean`
    # already knows how to read back, for every one of the five roles.
    for role in Role.CHOICES:
        assert Role.clean(to_storage(role)) == role


def test_the_apps_old_module_names_still_resolve():
    assert Module.clean(["savdo", "vazifa", "taqvim", "fayllar"]) == [
        Module.TASKS,
        Module.SALES,
        Module.CALENDAR,
        Module.FILES,
    ]


def test_a_module_list_is_stored_in_a_fixed_order():
    """Two identical grants must compare equal, or saving the same access
    twice reads as a change in the audit log."""
    assert Module.clean(["chat", "tasks"]) == Module.clean(["tasks", "chat"])


# ─── The rule the whole model rests on ────────────────────────────────────────

def test_a_permission_without_its_module_grants_nothing():
    modules, permissions = resolve(
        role=Role.MANAGER,
        role_modules=[Module.TASKS],
        role_permissions=[Permission.TASK_CREATE, Permission.DEAL_CREATE],
    )

    assert modules == [Module.TASKS]
    # Not "narrowed" and not an error — absent.
    assert permissions == [Permission.TASK_CREATE]


def test_an_override_replaces_the_role_rather_than_adding_to_it():
    """"Module access: by role, or configure" is a choice between two answers.
    A merge would make it impossible to invite a manager *without* the sales
    board, which is the whole reason the configure option exists."""
    modules, _ = resolve(
        role=Role.MANAGER,
        role_modules=[Module.TASKS, Module.SALES],
        role_permissions=[],
        module_override=[Module.CHAT],
    )

    assert modules == [Module.CHAT]


def test_no_override_means_by_role():
    modules, _ = resolve(
        role=Role.MANAGER,
        role_modules=[Module.TASKS],
        role_permissions=[],
        module_override=None,
    )

    assert modules == [Module.TASKS]


def test_an_empty_override_is_not_the_same_as_none():
    """One is access to nothing; the other is access to whatever the role
    says. Collapsing them would make "no modules" impossible to express."""
    modules, _ = resolve(
        role=Role.MANAGER,
        role_modules=[Module.TASKS],
        role_permissions=[],
        module_override=[],
    )

    assert modules == []


# ─── The defaults ─────────────────────────────────────────────────────────────

def test_an_owner_and_an_admin_may_do_everything():
    for role in (Role.OWNER, Role.ADMIN):
        modules, permissions = default_access(role)
        assert modules == Module.CHOICES
        assert set(permissions) == set(Permission.all()), role


def test_the_difference_between_an_owner_and_an_admin_is_reach_not_permissions():
    """An owner holds the Company; an admin holds one workspace. Their
    permission lists are identical and `COMPANY_WIDE` is what separates them."""
    assert DEFAULT_PERMISSIONS[Role.OWNER] == DEFAULT_PERMISSIONS[Role.ADMIN]
    assert Role.OWNER in Role.COMPANY_WIDE
    assert Role.ADMIN not in Role.COMPANY_WIDE


def test_an_employee_keeps_a_private_calendar_without_booking_the_team_s():
    _, permissions = default_access(Role.EMPLOYEE)

    assert Permission.EVENT_CREATE_OWN in permissions
    assert Permission.EVENT_CREATE not in permissions


def test_an_employee_cannot_administer_the_roster():
    _, permissions = default_access(Role.EMPLOYEE)

    assert Permission.EMPLOYEE_VIEW in permissions
    assert Permission.EMPLOYEE_CHANGE_ROLE not in permissions
    assert Permission.EMPLOYEE_INVITE not in permissions


def test_a_guest_starts_with_conversation_and_nothing_else():
    """The default guest opens chat and nothing else, and what they *resolve*
    to is three chat permissions — the catalogue lists more, but every one
    of them belongs to a module a guest has not been given."""
    modules, permissions = default_access(Role.GUEST)
    assert modules == [Module.CHAT]

    modules, permissions = resolve(role=Role.GUEST)
    assert modules == [Module.CHAT]
    assert permissions and all(p.startswith("chat.") for p in permissions)


def test_a_guest_lent_a_module_may_create_its_records():
    """TZ v2 §6: creating a record is open to anybody who can open the
    module, "including a Guest". Opening the sales board or the task list to
    a guest is the whole grant — no second one for the create button."""
    modules, permissions = resolve(
        role=Role.GUEST, module_override=[Module.CHAT, Module.SALES, Module.TASKS]
    )

    assert modules == [Module.TASKS, Module.CHAT, Module.SALES]
    assert Permission.DEAL_CREATE in permissions
    assert Permission.TASK_CREATE in permissions
    # Their own work and no more: nothing is edited, deleted or handed out.
    assert Permission.DEAL_EDIT not in permissions
    assert Permission.DEAL_DELETE not in permissions
    assert Permission.TASK_DELETE not in permissions
    assert Permission.TASK_ASSIGN not in permissions


def test_an_employee_creates_records_but_does_not_run_them():
    """TZ v2 §6 and §11 "Создавать записи: Да" for the employee — a task, a
    lead, a quick sale. Editing, assigning and deleting stay above them."""
    _, permissions = resolve(role=Role.EMPLOYEE)

    assert Permission.TASK_CREATE in permissions
    assert Permission.DEAL_CREATE in permissions
    for above in (
        Permission.TASK_EDIT, Permission.TASK_ASSIGN, Permission.TASK_DELETE,
        Permission.DEAL_EDIT, Permission.DEAL_ASSIGN, Permission.DEAL_DELETE,
    ):
        assert above not in permissions, above



def test_no_role_defaults_to_a_permission_outside_its_own_modules():
    """Otherwise the defaults would ship a grant that silently does nothing.

    The guest is the one exception, and on purpose: their default modules
    are chat alone, but TZ v2 §6 wants a guest who *is* lent a module to
    create its records — so the catalogue lists what a guest may do in a
    module they are given, and [resolve] drops it until they are.
    """
    for role in Role.CHOICES:
        if role == Role.GUEST:
            continue
        modules, permissions = default_access(role)
        for permission in permissions:
            assert Permission.module_of(permission) in modules, (role, permission)



# ─── The role editor ──────────────────────────────────────────────────────────

def test_the_catalogue_is_the_same_for_everybody():
    """It is the vocabulary, not the policy — the app renders the editor from
    it so a permission added on the server needs no app release."""
    response = _call(
        WorkspaceAccessCatalogueView, factory.get("/access/catalogue/"),
        _user(Role.EMPLOYEE),
    )

    assert response.status_code == 200
    assert len(response.data["roles"]) == 5
    assert {m["code"] for m in response.data["modules"]} == set(Module.CHOICES)
    # Every permission is labelled — an editor cannot draw a checkbox with no
    # words next to it.
    for module in response.data["modules"]:
        for permission in module["permissions"]:
            assert permission["label"], permission["code"]


def test_the_role_list_says_which_roles_a_workspace_has_configured():
    with patch(
        "apps.b2b.workspace.access_views.arepo.list_role_config",
        return_value={Role.MANAGER: {"modules": [], "permissions": []}},
    ), patch(
        "apps.b2b.workspace.access_views.arepo.role_access",
        return_value=([Module.TASKS], [Permission.TASK_VIEW]),
    ):
        response = _call(
            WorkspaceRoleListView, factory.get("/access/roles/"), _user(Role.OWNER)
        )

    by_code = {row["code"]: row for row in response.data["results"]}
    assert by_code[Role.MANAGER]["is_customised"] is True
    assert by_code[Role.EMPLOYEE]["is_customised"] is False


def test_the_owners_access_cannot_be_narrowed():
    """No amount of undo makes locking an owner out of their own company safe."""
    with _granting(Permission.EMPLOYEE_CHANGE_PERMISSIONS), patch(
        "apps.b2b.workspace.access_views.arepo.set_role_access"
    ) as write:
        response = _call(
            WorkspaceRoleDetailView,
            factory.put("/access/roles/owner/", {"modules": [], "permissions": []},
                        format="json"),
            _user(Role.OWNER),
            code=Role.OWNER,
        )

    assert response.status_code == 403
    write.assert_not_called()


def test_editing_a_role_needs_the_permission_to_edit_permissions():
    with patch(
        "apps.b2b.workspace.access_repository.access_for_employee",
        return_value=([Module.TASKS], [Permission.TASK_VIEW]),
    ), patch("apps.b2b.workspace.access_views.arepo.set_role_access") as write:
        response = _call(
            WorkspaceRoleDetailView,
            factory.put("/access/roles/manager/", {"modules": [], "permissions": []},
                        format="json"),
            _user(Role.EMPLOYEE),
            code=Role.MANAGER,
        )

    assert response.status_code == 403
    write.assert_not_called()


def test_a_role_that_does_not_exist_is_not_created_by_editing_it():
    with _granting(Permission.EMPLOYEE_CHANGE_PERMISSIONS):
        response = _call(
            WorkspaceRoleDetailView,
            factory.put("/access/roles/wizard/", {"modules": [], "permissions": []},
                        format="json"),
            _user(Role.OWNER),
            code="wizard",
        )

    assert response.status_code == 404


# ─── One person's access ──────────────────────────────────────────────────────

def _employee(role=Role.EMPLOYEE, employee_id=5, **extra):
    return {"id": employee_id, "company_id": COMPANY, "role": role, **extra}


def test_somebody_from_another_workspace_cannot_be_read_through_this():
    with patch(
        "apps.b2b.workspace.access_views.repo.get_workspace_employee",
        return_value=_employee(company_id=999),
    ):
        response = _call(
            WorkspaceEmployeeAccessView,
            factory.get("/employees/5/access/"),
            _user(Role.OWNER),
            employee_id=5,
        )

    assert response.status_code == 404


def test_the_owners_role_cannot_be_changed_on_the_employee_screen():
    """Handing over the company is a transfer of ownership, not an edit."""
    with patch(
        "apps.b2b.workspace.access_views.repo.get_workspace_employee",
        return_value=_employee(role=Role.OWNER),
    ), patch(
        "apps.b2b.workspace.access_repository.access_for_employee",
        return_value=(Module.CHOICES, list(Permission.all())),
    ), patch("apps.b2b.workspace.access_views.arepo.set_employee_role") as write:
        response = _call(
            WorkspaceEmployeeAccessView,
            factory.put("/employees/5/access/", {"role": "employee"}, format="json"),
            _user(Role.OWNER),
            employee_id=5,
        )

    assert response.status_code == 403
    write.assert_not_called()


def test_changing_modules_does_not_reset_permissions():
    """Each field is applied on its own; a payload carrying one must not
    silently clear what somebody else set last week."""
    with patch(
        "apps.b2b.workspace.access_views.repo.get_workspace_employee",
        return_value=_employee(),
    ), patch(
        "apps.b2b.workspace.access_repository.access_for_employee",
        return_value=(Module.CHOICES, list(Permission.all())),
    ), patch("apps.b2b.workspace.access_views.arepo.set_employee_access") as write:
        _call(
            WorkspaceEmployeeAccessView,
            factory.put("/employees/5/access/", {"modules": ["tasks"]}, format="json"),
            _user(Role.OWNER),
            employee_id=5,
        )

    from apps.b2b.workspace.access_repository import KEEP

    assert write.call_args.kwargs["modules"] == ["tasks"]
    assert write.call_args.kwargs["permissions"] is KEEP


def test_null_clears_an_override_rather_than_emptying_it():
    with patch(
        "apps.b2b.workspace.access_views.repo.get_workspace_employee",
        return_value=_employee(),
    ), patch(
        "apps.b2b.workspace.access_repository.access_for_employee",
        return_value=(Module.CHOICES, list(Permission.all())),
    ), patch("apps.b2b.workspace.access_views.arepo.set_employee_access") as write:
        _call(
            WorkspaceEmployeeAccessView,
            factory.put("/employees/5/access/", {"modules": None}, format="json"),
            _user(Role.OWNER),
            employee_id=5,
        )

    # None, not KEEP and not []: "by role".
    assert write.call_args.kwargs["modules"] is None


# ─── Audit ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("role", [Role.MANAGER, Role.EMPLOYEE, Role.GUEST])
def test_reading_the_audit_is_administrative(role):
    """It names who changed whose access and when."""
    response = _call(
        WorkspaceAuditView, factory.get("/audit/"), _user(role)
    )

    assert response.status_code == 403


@pytest.mark.parametrize("role", [Role.OWNER, Role.ADMIN])
def test_an_owner_or_admin_may_read_it(role):
    with patch(
        "apps.b2b.workspace.access_views.arepo.list_audit", return_value=[]
    ):
        response = _call(WorkspaceAuditView, factory.get("/audit/"), _user(role))

    assert response.status_code == 200


def test_a_failed_audit_write_does_not_undo_the_change_it_records():
    """A role change that half happened is worse than one nobody logged."""
    from apps.b2b.workspace.access_repository import record_audit

    with patch(
        "apps.b2b.workspace.access_repository.execute",
        side_effect=RuntimeError("db is gone"),
    ):
        record_audit(COMPANY, actor_employee_id=1, action="role.access_changed")


# ─── Delete is not destroy ────────────────────────────────────────────────────

def test_the_trash_is_not_readable_by_somebody_who_cannot_delete():
    """The TZ says an ordinary user does not see deleted objects at all — a
    bin anybody can read is a way to see the deal somebody removed today."""
    from apps.b2b.workspace.access_views import WorkspaceTrashView

    with _granting(Permission.TASK_VIEW, Permission.DEAL_VIEW):
        response = _call(WorkspaceTrashView, factory.get("/trash/"), _user(Role.EMPLOYEE))

    assert response.status_code == 403


def test_each_half_of_the_trash_is_gated_on_its_own():
    """Somebody who may delete tasks and not deals sees the tasks and not the
    deals, rather than all or nothing."""
    from apps.b2b.workspace.access_views import WorkspaceTrashView

    with _granting(Permission.TASK_DELETE), patch(
        "apps.b2b.workspace.access_views.repo.list_deleted_tasks",
        return_value=[{"id": 1}],
    ), patch(
        "apps.b2b.workspace.access_views.repo.list_deleted_leads",
        return_value=[{"id": 2}],
    ):
        response = _call(WorkspaceTrashView, factory.get("/trash/"), _user(Role.MANAGER))

    assert response.status_code == 200
    assert response.data["tasks"] == [{"id": 1}]
    assert response.data["leads"] == []


def test_restoring_takes_the_same_authority_as_deleting():
    """Otherwise somebody who cannot remove a deal could put back one that was
    removed deliberately."""
    from apps.b2b.workspace.access_views import WorkspaceRestoreView

    with _granting(Permission.TASK_DELETE), patch(
        "apps.b2b.workspace.access_views.repo.restore_lead"
    ) as restore:
        response = _call(
            WorkspaceRestoreView,
            factory.post("/trash/leads/7/restore/"),
            _user(Role.MANAGER),
            kind="leads",
            object_id=7,
        )

    assert response.status_code == 403
    restore.assert_not_called()


def test_restoring_something_that_was_never_deleted_is_not_a_success():
    from apps.b2b.workspace.access_views import WorkspaceRestoreView

    with _granting(Permission.TASK_DELETE), patch(
        "apps.b2b.workspace.access_views.repo.restore_task", return_value=False
    ):
        response = _call(
            WorkspaceRestoreView,
            factory.post("/trash/tasks/7/restore/"),
            _user(Role.MANAGER),
            kind="tasks",
            object_id=7,
        )

    assert response.status_code == 404


def test_only_tasks_and_leads_can_be_restored():
    """Soft delete is required for those two in the MVP; everything else in
    the schema still deletes outright, and pretending otherwise would offer a
    restore that silently does nothing."""
    from apps.b2b.workspace.access_views import WorkspaceRestoreView

    with _granting(*Permission.all()):
        response = _call(
            WorkspaceRestoreView,
            factory.post("/trash/chats/7/restore/"),
            _user(Role.OWNER),
            kind="chats",
            object_id=7,
        )

    assert response.status_code == 404


def test_purging_takes_the_same_authority_as_deleting():
    """Same reasoning as restoring, with more at stake: this one does not come
    back."""
    from apps.b2b.workspace.access_views import WorkspacePurgeView

    with _granting(Permission.TASK_DELETE), patch(
        "apps.b2b.workspace.access_views.repo.purge_lead"
    ) as purge:
        response = _call(
            WorkspacePurgeView,
            factory.delete("/trash/leads/7/"),
            _user(Role.MANAGER),
            kind="leads",
            object_id=7,
        )

    assert response.status_code == 403
    purge.assert_not_called()


def test_purging_something_that_is_not_in_the_bin_is_a_404():
    """The repository only ever deletes rows already carrying a `deleted_at`,
    so a live task reached through this endpoint answers exactly as one that
    does not exist — which is what keeps a stray id from destroying live work.
    """
    from apps.b2b.workspace.access_views import WorkspacePurgeView

    with _granting(Permission.TASK_DELETE), patch(
        "apps.b2b.workspace.access_views.repo.purge_task", return_value=False
    ):
        response = _call(
            WorkspacePurgeView,
            factory.delete("/trash/tasks/7/"),
            _user(Role.MANAGER),
            kind="tasks",
            object_id=7,
        )

    assert response.status_code == 404


def test_purging_leaves_its_only_trace_in_the_audit_log():
    from apps.b2b.workspace.access_views import WorkspacePurgeView

    with _granting(Permission.TASK_DELETE), patch(
        "apps.b2b.workspace.access_views.repo.purge_task", return_value=True
    ), patch("apps.b2b.workspace.access_views.arepo.record_audit") as audit:
        response = _call(
            WorkspacePurgeView,
            factory.delete("/trash/tasks/7/"),
            _user(Role.MANAGER),
            kind="tasks",
            object_id=7,
        )

    assert response.status_code == 204
    assert audit.call_args.kwargs["action"] == "task.purged"
    assert audit.call_args.kwargs["target_id"] == 7


def test_only_tasks_and_leads_can_be_purged():
    from apps.b2b.workspace.access_views import WorkspacePurgeView

    with _granting(*Permission.all()):
        response = _call(
            WorkspacePurgeView,
            factory.delete("/trash/chats/7/"),
            _user(Role.OWNER),
            kind="chats",
            object_id=7,
        )

    assert response.status_code == 404


# ─── Chat-only members ────────────────────────────────────────────────────────

def test_a_chat_only_member_opens_nothing_at_all():
    """They are in one conversation and are not a member of the workspace —
    not even of Chat, whose module gate they also fail. Which conversation
    they can read is chat membership's business, not this."""
    from apps.b2b.workspace.access_repository import access_for_employee

    modules, permissions = access_for_employee({
        "id": 9,
        "company_id": COMPANY,
        "role": Role.GUEST,
        "is_chat_only": True,
    })

    assert modules == []
    assert permissions == []


def test_a_chat_only_member_is_refused_by_every_module_gate():
    from apps.b2b.workspace.permissions import HasModule
    from apps.b2b.workspace.views import WorkspaceThreadListCreateView

    user = _user(Role.GUEST, is_chat_only=True)
    view = WorkspaceThreadListCreateView()

    assert HasModule().has_permission(
        type("R", (), {"user": user})(), view
    ) is False


# ─── The older flag map, now derived from this one ────────────────────────────
#
# The workspace's endpoints and the mobile app both read a flat map of `can_*`
# flags. It is computed from the permission catalogue rather than from the role
# string, so the role editor takes effect on those endpoints too — and these
# pin the result against what each role could do before that change, because a
# migration that quietly widened somebody's access would be the worst possible
# outcome.

#: What each role's flags were before the two were merged. Written out rather
#: than computed, so a change to the catalogue that moves one of these has to
#: be an explicit edit here.
LEGACY_FLAGS = {
    "owner": {
        "can_assign_task", "can_book_hotel", "can_chat", "can_comment_task",
        "can_create_event", "can_create_group_chat", "can_create_personal_event",
        "can_create_task", "can_delete_task", "can_edit_any_event",
        "can_edit_task", "can_manage_attendance", "can_manage_attendance_location",
        # Plugging an outside service into the funnel — Meta's lead ads. Held
        # by the owner, the administrator and the manager: connecting one
        # commits the whole company's board to somebody's advertising account
        # and hands the server a token to their Facebook, which is why it stops
        # above the ordinary employee. See `apps/b2b/integrations`.
        "can_manage_integrations",
        "can_manage_team", "can_pick_employee_of_month", "can_post_lead",
        "can_request_help", "can_update_task_status", "can_use_mail",
        "can_view_attendance", "can_view_hotels", "can_view_team",
        "sees_all_company_data",
        # TZ v2 §11 "Создавать рабочую среду": the owner and the
        # administrator, unconditionally.
        "can_create_workspace",
    },

    # The roster calls this one `performer`; the app calls it "Manager".
    # No `can_delete_task` — TZ §11 gives that to the owner and the
    # administrator only.
    "performer": {
        "can_assign_task", "can_book_hotel", "can_chat", "can_comment_task",
        "can_create_event", "can_create_group_chat", "can_create_personal_event",
        "can_create_task", "can_edit_any_event",
        "can_edit_task", "can_manage_attendance",
        # A deliberate widening, like `can_post_lead` below. The manager runs
        # the funnel every Meta lead lands in, and a source only the owner
        # could reconnect is one that stays broken until they are asked.
        "can_manage_integrations",
        # TZ v2 §11: a manager sets an employee's or a guest's role in their
        # own workspace, so the roster screen opens to them. What they may
        # set it *to* is bounded by `Role.assignable`, not by this flag.
        "can_manage_team",
        "can_post_lead",
        "can_update_task_status", "can_use_mail", "can_view_attendance",
        "can_view_hotels", "can_view_team", "sees_all_company_data",
    },

    # `can_post_lead` is a deliberate widening, not a drift: raising a lead is
    # now everybody's job — anybody who meets a customer can bring one in, and
    # a lead nobody was allowed to write down is a lead the company never had.
    # What happens to the deal afterwards is still the sales side's, which is
    # why no other `sales.*` flag appears here.
    # `can_create_task` joined `can_post_lead` under TZ v2 §6: creating a
    # record is open to anybody who can open the module. Running the board —
    # editing, assigning, deleting — is still not.
    "employee": {
        "can_chat", "can_comment_task", "can_create_personal_event",
        "can_create_task",
        "can_post_lead", "can_update_task_status", "can_use_mail",
        "can_view_attendance", "can_view_hotels", "can_view_team",
    },
}



@pytest.mark.parametrize("role", sorted(LEGACY_FLAGS))
def test_the_derived_flags_match_what_the_role_could_always_do(role):
    from apps.b2b.workspace.roles import capabilities_for

    granted = {name for name, allowed in capabilities_for(role).items() if allowed}

    assert granted == LEGACY_FLAGS[role]


def test_an_admin_gains_roster_administration_and_that_is_deliberate():
    """`lider` used to be a manager with one extra power. Under the TZ it is
    the workspace's administrator, so it gains what an administrator has."""
    from apps.b2b.workspace.roles import capabilities_for

    admin = capabilities_for("lider")
    manager = capabilities_for("performer")

    assert admin["can_manage_team"] is True
    # The manager has it too now (TZ v2 §11: "employee/guest in their own
    # workspace"); what tells the two apart is `Role.assignable`.
    assert manager["can_manage_team"] is True
    # TZ §10: picking the employee of the month is the owner's or the

    # administrator's call, not the manager's.
    assert admin["can_pick_employee_of_month"] is True
    assert manager["can_pick_employee_of_month"] is False
    # Everything the manager could do, the admin still can.
    for flag, allowed in manager.items():
        if allowed:
            assert admin[flag] is True, flag


def test_every_flag_the_app_reads_is_still_answered():
    """A flag that stopped being produced would read as False in the app and
    hide a button that should be there — the quietest possible regression."""
    from apps.b2b.workspace.access import CAPABILITY_PERMISSIONS
    from apps.b2b.workspace.roles import capabilities_for

    produced = set(capabilities_for(Role.EMPLOYEE))
    expected = set(CAPABILITY_PERMISSIONS) | {
        "can_view_attendance",
        "can_manage_attendance",
        "can_manage_attendance_location",
        "can_pick_employee_of_month",
        # Role-derived like the four above rather than a permission: the TZ's
        # modules are parts of a workspace, and connecting an outside service
        # is a company-level commitment with no module to hang off.
        "can_manage_integrations",
        "sees_all_company_data",
        # Role-and-permission derived — see `access.may_create_workspace`.
        "can_create_workspace",
    }

    assert produced == expected



def test_narrowing_a_role_narrows_the_flags_it_produces():
    """The whole point of the merge: the role editor now reaches the endpoints
    that were written against the old map."""
    from apps.b2b.workspace.access import capabilities_from

    modules, permissions = resolve(
        role=Role.MANAGER,
        role_modules=[Module.TASKS],
        role_permissions=[Permission.TASK_VIEW],
    )
    flags = capabilities_from(Role.MANAGER, modules, permissions)

    assert flags["can_create_task"] is False
    assert flags["can_post_lead"] is False
    # And the scope flags are untouched, because they are not permissions.
    assert flags["sees_all_company_data"] is True


def test_moving_a_task_along_is_not_editing_it():
    """An employee has always been able to finish their own work without being
    able to rewrite anybody's. One permission for both would break one or the
    other."""
    _, employee = default_access(Role.EMPLOYEE)

    assert Permission.TASK_STATUS in employee
    assert Permission.TASK_EDIT not in employee


# ─── Removing a member (TZ §11's two "delete a member" rows) ──────────────────

def test_outranks_orders_the_five_roles():
    from apps.b2b.workspace.access_repository import outranks

    assert outranks(Role.OWNER, Role.ADMIN)
    assert outranks(Role.ADMIN, Role.MANAGER)
    assert outranks(Role.MANAGER, Role.EMPLOYEE)
    assert outranks(Role.EMPLOYEE, Role.GUEST)
    assert not outranks(Role.MANAGER, Role.MANAGER)
    assert not outranks(Role.EMPLOYEE, Role.MANAGER)
    # Legacy storage values resolve the same way, since rank reads through
    # `Role.clean`.
    assert outranks("lider", "performer")


def test_the_owner_cannot_be_removed():
    with patch(
        "apps.b2b.workspace.access_views.repo.get_workspace_employee",
        return_value=_employee(role=Role.OWNER),
    ):
        response = _call(
            WorkspaceEmployeeRemoveView,
            factory.post("/employees/5/remove/", {}, format="json"),
            _user(Role.ADMIN),
            employee_id=5,
        )

    assert response.status_code == 403


def test_a_manager_cannot_remove_an_admin():
    """Manager outranks nobody an admin does — the TZ's "only lower roles"."""
    with patch(
        "apps.b2b.workspace.access_views.repo.get_workspace_employee",
        return_value=_employee(role=Role.ADMIN),
    ), _granting(Permission.EMPLOYEE_REMOVE_WORKSPACE):
        response = _call(
            WorkspaceEmployeeRemoveView,
            factory.post("/employees/5/remove/", {}, format="json"),
            _user(Role.MANAGER),
            employee_id=5,
        )

    assert response.status_code == 403


def test_an_admin_may_remove_an_employee_from_the_workspace():
    with patch(
        "apps.b2b.workspace.access_views.repo.get_workspace_employee",
        return_value=_employee(role=Role.EMPLOYEE),
    ), _granting(Permission.EMPLOYEE_REMOVE_WORKSPACE), patch(
        "apps.b2b.workspace.access_views.arepo.remove_employee"
    ) as write:
        response = _call(
            WorkspaceEmployeeRemoveView,
            factory.post("/employees/5/remove/", {}, format="json"),
            _user(Role.ADMIN),
            employee_id=5,
        )

    assert response.status_code == 204
    write.assert_called_once()
    assert write.call_args.kwargs["scope"] == "workspace"


def test_removing_from_the_company_needs_the_wider_permission():
    """Holding `EMPLOYEE_REMOVE_WORKSPACE` alone does not reach `scope=company`."""
    with patch(
        "apps.b2b.workspace.access_views.repo.get_workspace_employee",
        return_value=_employee(role=Role.EMPLOYEE),
    ), _granting(Permission.EMPLOYEE_REMOVE_WORKSPACE):
        response = _call(
            WorkspaceEmployeeRemoveView,
            factory.post("/employees/5/remove/", {"scope": "company"}, format="json"),
            _user(Role.ADMIN),
            employee_id=5,
        )

    assert response.status_code == 403


# ─── Deleting a workspace (TZ §4) ──────────────────────────────────────────────

def test_only_a_manager_or_above_may_ask_to_delete_the_workspace():
    response = _call(
        WorkspaceDeleteRequestView,
        factory.post("/delete-requests/", {}, format="json"),
        _user(Role.EMPLOYEE),
    )
    assert response.status_code == 403


def test_an_admin_may_ask_to_delete_the_workspace():
    with patch(
        "apps.b2b.workspace.access_views.arepo.request_workspace_deletion"
    ) as write:
        write.return_value = {"id": 1, "status": "pending"}
        response = _call(
            WorkspaceDeleteRequestView,
            factory.post("/delete-requests/", {"reason": "no longer needed"}, format="json"),
            _user(Role.ADMIN),
        )

    assert response.status_code == 201
    write.assert_called_once()


def test_only_the_owner_may_decide_a_workspace_deletion_request():
    response = _call(
        WorkspaceDeleteRequestDecideView,
        factory.post("/delete-requests/1/approve/"),
        _user(Role.ADMIN),
        request_id=1,
        action="approve",
    )
    assert response.status_code == 403


def test_the_archive_shows_completed_and_deleted_separately():
    """TZ §7: "История и архив" is two named states, not one merged list."""
    from apps.b2b.workspace.access_views import WorkspaceArchiveView

    with patch(
        "apps.b2b.workspace.access_views.repo.list_tasks", return_value=[{"id": 1}]
    ) as list_tasks, patch(
        "apps.b2b.workspace.access_views.repo.list_leads", return_value=[{"id": 2}]
    ) as list_leads, patch(
        "apps.b2b.workspace.access_views.repo.list_deleted_tasks", return_value=[{"id": 3}]
    ), patch(
        "apps.b2b.workspace.access_views.repo.list_deleted_leads", return_value=[{"id": 4}]
    ):
        response = _call(
            WorkspaceArchiveView, factory.get("/archive/"), _user(Role.OWNER)
        )

    assert response.status_code == 200
    assert response.data["completed"]["tasks"] == [{"id": 1}]
    assert response.data["completed"]["leads"] == [{"id": 2}]
    assert response.data["deleted"]["tasks"] == [{"id": 3}]
    assert response.data["deleted"]["leads"] == [{"id": 4}]
    assert response.data["can_restore_tasks"] is True
    list_tasks.assert_called_once_with(COMPANY, status="done")
    list_leads.assert_called_once()


def test_the_archive_shows_an_employee_their_own_slice():
    """TZ v2 §11: below the manager, "History and archive" is read "within
    permitted access" — the modules open to them, and their own records."""
    from apps.b2b.workspace.access_views import WorkspaceArchiveView

    me, other = 1, 2
    with patch(
        "apps.b2b.workspace.access_views.repo.list_tasks",
        return_value=[
            {"id": 1, "author_id": other, "assignee_ids": [me]},
            {"id": 5, "author_id": other, "assignee_ids": [other]},
        ],
    ), patch(
        "apps.b2b.workspace.access_views.repo.list_leads",
        return_value=[{"id": 2, "claimed_by_id": me}, {"id": 6, "claimed_by_id": other}],
    ), patch(
        "apps.b2b.workspace.access_views.repo.list_deleted_tasks",
        return_value=[{"id": 3, "author_id": me, "assignee_ids": []}],
    ), patch(
        "apps.b2b.workspace.access_views.repo.list_deleted_leads",
        return_value=[{"id": 4, "claimed_by_id": other, "author_id": other}],
    ):
        response = _call(
            WorkspaceArchiveView, factory.get("/archive/"), _user(Role.EMPLOYEE, me)
        )

    assert response.status_code == 200
    assert [t["id"] for t in response.data["completed"]["tasks"]] == [1]
    assert [l["id"] for l in response.data["completed"]["leads"]] == [2]
    assert [t["id"] for t in response.data["deleted"]["tasks"]] == [3]
    assert response.data["deleted"]["leads"] == []
    # Seeing a deleted record is not the right to bring it back (§11).
    assert response.data["can_restore_tasks"] is False
    assert response.data["can_restore_leads"] is False


def test_the_archive_hides_a_module_that_is_closed_to_the_viewer():
    from apps.b2b.workspace.access_views import WorkspaceArchiveView

    with patch(
        "apps.b2b.workspace.access_views.repo.list_tasks", return_value=[{"id": 1}]
    ), patch(
        "apps.b2b.workspace.access_views.repo.list_leads", return_value=[{"id": 2}]
    ) as list_leads, patch(
        "apps.b2b.workspace.access_views.repo.list_deleted_tasks", return_value=[]
    ), patch(
        "apps.b2b.workspace.access_views.repo.list_deleted_leads", return_value=[]
    ), patch(
        "apps.b2b.workspace.access_repository.access_for_employee",
        return_value=([Module.TASKS], [Permission.TASK_VIEW, Permission.DEAL_VIEW]),
    ):

        response = _call(
            WorkspaceArchiveView, factory.get("/archive/"), _user(Role.ADMIN)
        )

    assert response.status_code == 200
    assert response.data["completed"]["tasks"] == [{"id": 1}]
    assert response.data["completed"]["leads"] == []
    list_leads.assert_not_called()



def test_a_manager_cannot_edit_a_completed_task():
    """TZ §8: once a task is done, only the owner or an administrator may
    still touch it, even somebody who could edit it a minute before it
    closed."""
    from apps.b2b.workspace.views import WorkspaceTaskDetailView

    with patch(
        "apps.b2b.workspace.views.repo.get_task",
        return_value={"id": 9, "company_id": COMPANY, "author_id": 1, "status": "done", "assignee_ids": []},
    ), _granting(*Permission.all()):
        response = _call(
            WorkspaceTaskDetailView,
            factory.patch("/tasks/9/", {"title": "New title"}, format="json"),
            _user(Role.MANAGER),
            task_id=9,
        )

    assert response.status_code == 403


def test_an_admin_may_edit_a_completed_task():
    from apps.b2b.workspace.views import WorkspaceTaskDetailView

    with patch(
        "apps.b2b.workspace.views.repo.get_task",
        return_value={"id": 9, "company_id": COMPANY, "author_id": 1, "status": "done", "assignee_ids": []},
    ), patch(
        "apps.b2b.workspace.views.repo.update_task",
        return_value={"id": 9, "company_id": COMPANY, "author_id": 1, "status": "done", "assignee_ids": [], "title": "New title"},
    ), _granting(*Permission.all()):
        response = _call(
            WorkspaceTaskDetailView,
            factory.patch("/tasks/9/", {"title": "New title"}, format="json"),
            _user(Role.ADMIN),
            task_id=9,
        )

    assert response.status_code == 200


def test_the_owner_approving_a_deletion_marks_the_workspace_deleted():
    with patch(
        "apps.b2b.workspace.access_views.arepo.decide_workspace_deletion"
    ) as write:
        write.return_value = {"id": 1, "status": "approved"}
        response = _call(
            WorkspaceDeleteRequestDecideView,
            factory.post("/delete-requests/1/approve/"),
            _user(Role.OWNER),
            request_id=1,
            action="approve",
        )

    assert response.status_code == 200
    assert write.call_args.kwargs["approve"] is True


# ─── TZ v2 §5.2, §11, §12: nobody hands out more than they hold ───────────────

def _editing(target_role, actor_role, body, actor_access=None):
    """PUT the employee editor as `actor_role` against a `target_role` row."""
    access = actor_access or (Module.CHOICES, list(Permission.all()))
    with patch(
        "apps.b2b.workspace.access_views.repo.get_workspace_employee",
        return_value=_employee(role=target_role),
    ), patch(
        "apps.b2b.workspace.access_repository.access_for_employee",
        return_value=access,
    ), patch(
        "apps.b2b.workspace.access_views.arepo.set_employee_role"
    ) as set_role, patch(
        "apps.b2b.workspace.access_views.arepo.set_employee_access"
    ) as set_access:
        response = _call(
            WorkspaceEmployeeAccessView,
            factory.put("/employees/5/access/", body, format="json"),
            _user(actor_role),
            employee_id=5,
        )
    return response, set_role, set_access


@pytest.mark.parametrize(
    "actor, target, new_role, allowed",
    [
        # The owner assigns anything but ownership itself.
        (Role.OWNER, Role.MANAGER, Role.ADMIN, True),
        (Role.OWNER, Role.EMPLOYEE, Role.OWNER, False),
        # An administrator: "below the administrator's level".
        (Role.ADMIN, Role.EMPLOYEE, Role.MANAGER, True),
        (Role.ADMIN, Role.EMPLOYEE, Role.ADMIN, False),
        (Role.ADMIN, Role.ADMIN, Role.EMPLOYEE, False),
        # A manager: "employee/guest in their own workspace".
        (Role.MANAGER, Role.EMPLOYEE, Role.GUEST, True),
        (Role.MANAGER, Role.GUEST, Role.EMPLOYEE, True),
        (Role.MANAGER, Role.EMPLOYEE, Role.MANAGER, False),
        (Role.MANAGER, Role.MANAGER, Role.EMPLOYEE, False),
    ],
)
def test_a_role_is_assigned_only_downwards(actor, target, new_role, allowed):
    response, set_role, _ = _editing(target, actor, {"role": new_role})

    assert (response.status_code == 200) is allowed, response.data
    assert set_role.called is allowed


def test_access_is_granted_only_within_ones_own():
    """§12: an administrator narrowed to the task list cannot open the sales
    board to somebody else — and the refusal names what was over the line."""
    narrowed = ([Module.TASKS], [Permission.TASK_VIEW, Permission.EMPLOYEE_CHANGE_MODULES,
                                  Permission.EMPLOYEE_CHANGE_PERMISSIONS])
    # `resolve` would have dropped the employees.* permissions for a closed
    # module; the editor is what is under test here, so they are handed in
    # already resolved.
    narrowed = ([Module.TASKS, Module.EMPLOYEES], narrowed[1])

    response, _, set_access = _editing(
        Role.EMPLOYEE, Role.ADMIN, {"modules": ["tasks", "sales"]}, actor_access=narrowed
    )
    assert response.status_code == 403
    assert response.data["modules"] == ["sales"]
    set_access.assert_not_called()

    response, _, set_access = _editing(
        Role.EMPLOYEE, Role.ADMIN,
        {"permissions": [Permission.TASK_VIEW, Permission.TASK_DELETE]},
        actor_access=narrowed,
    )
    assert response.status_code == 403
    assert response.data["permissions"] == [Permission.TASK_DELETE]
    set_access.assert_not_called()

    # Within what they hold, it goes through.
    response, _, set_access = _editing(
        Role.EMPLOYEE, Role.ADMIN, {"modules": ["tasks"]}, actor_access=narrowed
    )
    assert response.status_code == 200
    set_access.assert_called_once()


def test_the_owner_is_never_narrowed_by_their_own_access():
    """The owner holds the company; §12's rule is about everybody else."""
    response, _, set_access = _editing(
        Role.ADMIN, Role.OWNER, {"modules": Module.CHOICES},
        actor_access=(
            [Module.TASKS, Module.EMPLOYEES],
            [Permission.TASK_VIEW, Permission.EMPLOYEE_CHANGE_MODULES],
        ),
    )

    assert response.status_code == 200
    set_access.assert_called_once()


def test_access_is_edited_only_downwards():
    """A manager may not widen a manager — themselves included."""
    response, _, set_access = _editing(Role.MANAGER, Role.MANAGER, {"modules": ["tasks"]})
    assert response.status_code == 403
    set_access.assert_not_called()


def test_the_role_editor_reaches_only_the_roles_below_you():
    """An administrator configures managers, employees and guests. Editing
    the administrator role itself would let one administrator narrow — or
    widen — every other."""
    with patch(
        "apps.b2b.workspace.access_repository.access_for_employee",
        return_value=(Module.CHOICES, list(Permission.all())),
    ), patch("apps.b2b.workspace.access_views.arepo.set_role_access") as write:
        refused = _call(
            WorkspaceRoleDetailView,
            factory.put(
                "/access/roles/admin/", {"modules": ["chat"], "permissions": []}, format="json"
            ),
            _user(Role.ADMIN),
            code=Role.ADMIN,
        )
        write.return_value = {"modules": ["chat"], "permissions": []}
        allowed = _call(
            WorkspaceRoleDetailView,
            factory.put(
                "/access/roles/manager/", {"modules": ["chat"], "permissions": []}, format="json"
            ),
            _user(Role.ADMIN),
            code=Role.MANAGER,
        )

    assert refused.status_code == 403
    assert allowed.status_code == 200
    write.assert_called_once()


def test_the_role_editor_grants_only_what_the_editor_holds():
    with patch(
        "apps.b2b.workspace.access_repository.access_for_employee",
        return_value=(
            [Module.TASKS, Module.EMPLOYEES],
            [Permission.TASK_VIEW, Permission.EMPLOYEE_CHANGE_PERMISSIONS],
        ),
    ), patch("apps.b2b.workspace.access_views.arepo.set_role_access") as write:
        response = _call(
            WorkspaceRoleDetailView,
            factory.put(
                "/access/roles/employee/",
                {"modules": ["tasks", "sales"], "permissions": [Permission.DEAL_CREATE]},
                format="json",
            ),
            _user(Role.ADMIN),
            code=Role.EMPLOYEE,
        )

    assert response.status_code == 403
    assert response.data["modules"] == ["sales"]
    assert response.data["permissions"] == [Permission.DEAL_CREATE]
    write.assert_not_called()


def test_the_rank_table_reads_as_the_tz_writes_it():
    assert Role.outranks(Role.OWNER, Role.ADMIN)
    assert Role.outranks(Role.ADMIN, Role.MANAGER)
    assert Role.outranks(Role.MANAGER, Role.EMPLOYEE)
    assert Role.outranks(Role.EMPLOYEE, Role.GUEST)
    assert not Role.outranks(Role.ADMIN, Role.ADMIN)
    # The roster's older spellings rank the same.
    assert Role.outranks("lider", "performer")
    assert Role.assignable(Role.OWNER, Role.ADMIN)
    assert not Role.assignable(Role.OWNER, Role.OWNER)
    assert not Role.assignable(Role.ADMIN, Role.ADMIN)


# ─── TZ v2 §11: creating a workspace ─────────────────────────────────────────

@pytest.mark.parametrize(
    "role, permissions, allowed",
    [
        (Role.OWNER, [], True),
        (Role.ADMIN, [], True),
        (Role.MANAGER, [], False),
        (Role.MANAGER, [Permission.WORKSPACE_CREATE], True),
        (Role.EMPLOYEE, [], False),
        (Role.EMPLOYEE, [Permission.WORKSPACE_CREATE], True),
        # "Гость — Нет", whatever they were handed.
        (Role.GUEST, [Permission.WORKSPACE_CREATE], False),
    ],
)
def test_who_may_open_a_workspace(role, permissions, allowed):
    from apps.b2b.workspace.access import may_create_workspace

    assert may_create_workspace(role, permissions) is allowed
