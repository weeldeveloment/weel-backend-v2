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
    WorkspaceEmployeeAccessView,
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
    modules, permissions = default_access(Role.GUEST)

    assert modules == [Module.CHAT]
    assert all(p.startswith("chat.") for p in permissions)


def test_no_role_defaults_to_a_permission_outside_its_own_modules():
    """Otherwise the defaults would ship a grant that silently does nothing."""
    for role in Role.CHOICES:
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
        "can_manage_team", "can_pick_employee_of_month", "can_post_lead",
        "can_request_help", "can_update_task_status", "can_use_mail",
        "can_view_attendance", "can_view_hotels", "can_view_team",
        "sees_all_company_data",
    },
    # The roster calls this one `performer`; the app calls it "Manager".
    "performer": {
        "can_assign_task", "can_book_hotel", "can_chat", "can_comment_task",
        "can_create_event", "can_create_group_chat", "can_create_personal_event",
        "can_create_task", "can_delete_task", "can_edit_any_event",
        "can_edit_task", "can_manage_attendance", "can_post_lead",
        "can_update_task_status", "can_use_mail", "can_view_attendance",
        "can_view_hotels", "can_view_team", "sees_all_company_data",
    },
    "employee": {
        "can_chat", "can_comment_task", "can_create_personal_event",
        "can_update_task_status", "can_use_mail", "can_view_attendance",
        "can_view_hotels", "can_view_team",
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
    assert manager["can_manage_team"] is False
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
        "sees_all_company_data",
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
