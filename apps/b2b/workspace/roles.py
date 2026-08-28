from __future__ import annotations

from apps.b2b.models import EmployeeRole

# Roles that may run the company: create and assign tasks, put events on the
# shared calendar, open group chats. Everyone else is a plain employee who
# works the tasks they were given.
MANAGER_ROLES = frozenset({
    EmployeeRole.OWNER,
    EmployeeRole.PERFORMER,
    EmployeeRole.LIDER,
})

# Who may ask another workspace to lend them somebody.
#
# Narrower than [MANAGER_ROLES] on purpose, and the one thing that separates a
# lider from a manager. A request hands an outsider a role and a set of modules
# in this workspace for a stretch of time — that is a commitment about who is
# allowed in, which is the owner's or a team lead's call rather than something
# anybody handing out work can do on their own.
REQUEST_ROLES = frozenset({EmployeeRole.OWNER, EmployeeRole.LIDER})


def is_manager(role: str | None) -> bool:
    """Whether somebody runs the workspace: the owner, an administrator
    ("lider"), or a manager ("performer").

    The role is canonicalised first because the same three ranks are stored
    under two vocabularies — the column has said `performer` and `lider` since
    the dashboard was written, and memberships created since the TZ store
    `manager` and `admin`. Comparing the raw string against one of the two
    lists answered False for half the rows it was asked about.
    """
    from apps.b2b.workspace.access import Role

    return Role.clean(role) in {Role.OWNER, Role.ADMIN, Role.MANAGER}


def capabilities_for(role: str | None, modules=None) -> dict[str, bool]:
    """The permission map the mobile app renders its UI from.

    The client must not re-derive these from the role string — new roles or a
    changed policy would then need an app release. It asks the server what the
    signed-in person may do and hides the rest.

    Computed from the permission catalogue rather than from the role string,
    so the workspace's role editor takes effect on these flags too — there is
    one source of truth, and `access.CAPABILITY_PERMISSIONS` is the map
    between the two vocabularies.

    `modules` is a guest's grant, and it only ever subtracts. A permanent
    employee passes `None` and is answered by their role's configuration;
    somebody lent to this workspace to help with the sales board passes the
    modules their secondment named, and everything belonging to a module they
    were not given comes back false.

    Note that this reads the *default* configuration, not the workspace's.
    `WorkspaceUser.capabilities` is what the endpoints actually consult and it
    resolves against the stored role rows — see `access_repository`.
    """

    from apps.b2b.workspace.access import capabilities_from, resolve

    resolved_modules, resolved_permissions = resolve(
        role=role, module_override=list(modules) if modules is not None else None
    )
    return capabilities_from(role, resolved_modules, resolved_permissions)
