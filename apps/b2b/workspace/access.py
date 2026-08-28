"""Who → where → what: the access vocabulary from the TZ, in one file.

The specification states the model as three questions asked in order:

    ROLE           = who is this person?
    MODULE ACCESS  = which parts of the workspace can they open?
    PERMISSIONS    = what may they do inside a part they can open?

Two rules follow from that ordering and are enforced everywhere below:

* **A permission without module access grants nothing.** `task.create` on
  somebody who cannot open Tasks is not a narrower grant, it is no grant —
  [resolve] drops it rather than leaving it to each call site to remember.
* **Hiding a button is not security.** Every permission named here is checked
  on the server; the map the app receives is for drawing the UI, not for
  deciding it.

Roles are fixed. The TZ is explicit that no new ones may be created in the
MVP, and that what an *existing* role may do is configurable per workspace —
so the five names below are code, and the defaults are a starting point a
workspace can move away from without inventing a sixth role.
"""
from __future__ import annotations


class Role:
    """The five, and only five.

    ``OWNER`` and ``ADMIN`` are not the same rank in different words. An owner
    holds the Company — every workspace under it, and the right to close it.
    An admin holds one workspace: whoever creates a workspace gets it, without
    thereby getting any say over the company's others.
    """

    OWNER = "owner"
    ADMIN = "admin"
    MANAGER = "manager"
    EMPLOYEE = "employee"
    GUEST = "guest"

    CHOICES = [OWNER, ADMIN, MANAGER, EMPLOYEE, GUEST]

    #: Company-wide. An owner's reach does not stop at a workspace boundary.
    COMPANY_WIDE = frozenset({OWNER})

    #: Who may administer the workspace they are in — invite, set roles, edit
    #: what a role may do.
    ADMINISTRATIVE = frozenset({OWNER, ADMIN})

    LABELS = {
        OWNER: "Egasi",
        ADMIN: "Administrator",
        MANAGER: "Rahbar",
        EMPLOYEE: "Xodim",
        GUEST: "Mehmon",
    }

    #: What the roster has called these. `performer` is the manager — the
    #: column has said so since the dashboard was written — and `lider` was
    #: the workspace administrator before the TZ gave it that name. Read on the
    #: way in so stored rows keep resolving; never written.
    ALIASES = {"performer": MANAGER, "lider": ADMIN, "ghost": GUEST}

    @classmethod
    def clean(cls, role: str | None) -> str:
        """The canonical role behind whatever is stored."""
        name = (role or "").strip().lower()
        name = cls.ALIASES.get(name, name)
        return name if name in cls.CHOICES else cls.EMPLOYEE

    @classmethod
    def label(cls, role: str | None) -> str:
        return cls.LABELS.get(cls.clean(role), cls.LABELS[cls.EMPLOYEE])


class Module:
    """The parts of a workspace, as the navigation shows them.

    Somebody without access to a module must not see it in the navigation at
    all — the TZ says so, and the API backs it: the module gate answers 403
    before the view runs, so a hidden tab and a closed endpoint agree.
    """

    TASKS = "tasks"
    CHAT = "chat"
    SALES = "sales"
    CRM = "crm"
    CALENDAR = "calendar"
    FILES = "files"
    #: Business trips — the hotel and voucher side. Called TMS in the TZ.
    TRIPS = "trips"
    EMPLOYEES = "employees"
    REPORTS = "reports"

    CHOICES = [TASKS, CHAT, SALES, CRM, CALENDAR, FILES, TRIPS, EMPLOYEES, REPORTS]

    LABELS = {
        TASKS: "Vazifalar",
        CHAT: "Chat",
        SALES: "Savdo",
        CRM: "Mijozlar",
        CALENDAR: "Taqvim",
        FILES: "Fayllar",
        TRIPS: "Safarlar",
        EMPLOYEES: "Xodimlar",
        REPORTS: "Hisobotlar",
    }

    #: What the mobile app called these before the TZ named them. The app's
    #: "Qo'shimcha dostup" card shipped with Uzbek keys and there are grants
    #: stored under them; the catalogue is English because four of the nine
    #: modules never had an Uzbek name to keep. Read on the way in, never
    #: written — [clean] normalises, so nothing downstream sees two spellings.
    ALIASES = {
        "savdo": SALES,
        "vazifa": TASKS,
        "taqvim": CALENDAR,
        "fayllar": FILES,
        "tms": TRIPS,
    }

    @classmethod
    def clean(cls, modules) -> list[str]:
        """A stored module list: known names only, in a fixed order.

        Fixed order so two identical grants compare equal — otherwise the same
        access saved twice looks like a change in the audit log.
        """
        asked = set()
        for module in modules or []:
            name = str(module)
            asked.add(cls.ALIASES.get(name, name))
        return [module for module in cls.CHOICES if module in asked]


class Permission:
    """What may be done inside a module.

    Named ``module.verb`` so a permission always carries the module it belongs
    to. That is not decoration: [resolve] reads the prefix to drop permissions
    for modules somebody cannot open, which is the TZ's "permission without
    module access grants nothing" expressed once instead of at every check.
    """

    # -- Tasks ------------------------------------------------------------
    TASK_VIEW = "tasks.view"
    TASK_CREATE = "tasks.create"
    TASK_EDIT = "tasks.edit"
    #: Moving a task you were given from todo to done. Kept apart from
    #: [TASK_EDIT] because an employee has always been able to do the second
    #: without the first — the TZ lists "Edit" once, and collapsing them would
    #: either stop people finishing their own work or let everybody rewrite
    #: everybody's.
    TASK_STATUS = "tasks.change_status"
    TASK_DELETE = "tasks.delete"
    TASK_ASSIGN = "tasks.assign"
    TASK_REASSIGN = "tasks.reassign"
    TASK_COMMENT = "tasks.comment"
    TASK_EXPORT = "tasks.export"

    # -- Chat -------------------------------------------------------------
    CHAT_VIEW = "chat.view"
    CHAT_CREATE = "chat.create"
    CHAT_SEND = "chat.send"
    CHAT_DELETE_OWN = "chat.delete_own"
    CHAT_MANAGE_GROUP = "chat.manage_group"

    # -- Sales (deals) ----------------------------------------------------
    DEAL_VIEW = "sales.view"
    DEAL_CREATE = "sales.create"
    DEAL_EDIT = "sales.edit"
    DEAL_DELETE = "sales.delete"
    DEAL_ASSIGN = "sales.assign"
    DEAL_STAGE = "sales.change_stage"
    DEAL_EXPORT = "sales.export"
    DEAL_MANAGE_PIPELINE = "sales.manage_pipeline"

    # -- CRM (clients) ----------------------------------------------------
    CLIENT_VIEW = "crm.view"
    CLIENT_CREATE = "crm.create"
    CLIENT_EDIT = "crm.edit"
    CLIENT_DELETE = "crm.delete"
    CLIENT_EXPORT = "crm.export"

    # -- Calendar ---------------------------------------------------------
    EVENT_VIEW = "calendar.view"
    #: A shared entry, on other people's calendars too.
    EVENT_CREATE = "calendar.create"
    #: An entry of one's own. Kept apart from [EVENT_CREATE] because an
    #: employee has always had a private calendar while not being able to book
    #: the team's — the TZ lists "Create Event" once, and collapsing the two
    #: would either take that private calendar away or hand everybody the
    #: shared one.
    EVENT_CREATE_OWN = "calendar.create_own"
    EVENT_EDIT = "calendar.edit"
    EVENT_DELETE = "calendar.delete"
    EVENT_INVITE = "calendar.invite"

    # -- Files ------------------------------------------------------------
    FILE_VIEW = "files.view"
    FILE_UPLOAD = "files.upload"
    FILE_EDIT = "files.edit"
    FILE_DELETE = "files.delete"
    FILE_DOWNLOAD = "files.download"
    FILE_CREATE_FOLDER = "files.create_folder"
    FILE_MANAGE_ACCESS = "files.manage_access"

    # -- Trips ------------------------------------------------------------
    TRIP_VIEW = "trips.view"
    TRIP_CREATE = "trips.create"
    TRIP_MANAGE = "trips.manage"

    # -- Employees --------------------------------------------------------
    EMPLOYEE_VIEW = "employees.view"
    EMPLOYEE_INVITE = "employees.invite"
    EMPLOYEE_CHANGE_ROLE = "employees.change_role"
    EMPLOYEE_CHANGE_MODULES = "employees.change_modules"
    EMPLOYEE_CHANGE_PERMISSIONS = "employees.change_permissions"
    EMPLOYEE_REMOVE_WORKSPACE = "employees.remove_from_workspace"
    EMPLOYEE_REMOVE_COMPANY = "employees.remove_from_company"

    # -- Reports ----------------------------------------------------------
    REPORT_VIEW = "reports.view"
    REPORT_EXPORT = "reports.export"

    #: Every permission, grouped by the module it belongs to. The single
    #: source of truth for what a workspace may hand out — the role editor
    #: renders from this, and [resolve] filters against it.
    BY_MODULE: dict[str, tuple[str, ...]] = {
        Module.TASKS: (
            TASK_VIEW, TASK_CREATE, TASK_EDIT, TASK_STATUS, TASK_DELETE,
            TASK_ASSIGN, TASK_REASSIGN, TASK_COMMENT, TASK_EXPORT,
        ),
        Module.CHAT: (
            CHAT_VIEW, CHAT_CREATE, CHAT_SEND, CHAT_DELETE_OWN, CHAT_MANAGE_GROUP,
        ),
        Module.SALES: (
            DEAL_VIEW, DEAL_CREATE, DEAL_EDIT, DEAL_DELETE,
            DEAL_ASSIGN, DEAL_STAGE, DEAL_EXPORT, DEAL_MANAGE_PIPELINE,
        ),
        Module.CRM: (
            CLIENT_VIEW, CLIENT_CREATE, CLIENT_EDIT, CLIENT_DELETE, CLIENT_EXPORT,
        ),
        Module.CALENDAR: (
            EVENT_VIEW, EVENT_CREATE, EVENT_CREATE_OWN,
            EVENT_EDIT, EVENT_DELETE, EVENT_INVITE,
        ),
        Module.FILES: (
            FILE_VIEW, FILE_UPLOAD, FILE_EDIT, FILE_DELETE,
            FILE_DOWNLOAD, FILE_CREATE_FOLDER, FILE_MANAGE_ACCESS,
        ),
        Module.TRIPS: (TRIP_VIEW, TRIP_CREATE, TRIP_MANAGE),
        Module.EMPLOYEES: (
            EMPLOYEE_VIEW, EMPLOYEE_INVITE, EMPLOYEE_CHANGE_ROLE,
            EMPLOYEE_CHANGE_MODULES, EMPLOYEE_CHANGE_PERMISSIONS,
            EMPLOYEE_REMOVE_WORKSPACE, EMPLOYEE_REMOVE_COMPANY,
        ),
        Module.REPORTS: (REPORT_VIEW, REPORT_EXPORT),
    }

    @classmethod
    def all(cls) -> tuple[str, ...]:
        return tuple(p for group in cls.BY_MODULE.values() for p in group)

    @classmethod
    def module_of(cls, permission: str) -> str:
        """The module a permission belongs to, read off its own name."""
        return permission.split(".", 1)[0]

    @classmethod
    def clean(cls, permissions) -> list[str]:
        """Known permissions only, in catalogue order."""
        asked = {str(p) for p in (permissions or [])}
        return [p for p in cls.all() if p in asked]


# ─── The defaults a workspace starts from ─────────────────────────────────────
#
# From the TZ's role matrix (§21). A workspace may move away from these — that
# is what makes them defaults rather than the rule — but a workspace that has
# never been configured has to behave sensibly on the day it is created.

_ALL_MODULES = tuple(Module.CHOICES)

DEFAULT_MODULES: dict[str, tuple[str, ...]] = {
    Role.OWNER: _ALL_MODULES,
    Role.ADMIN: _ALL_MODULES,
    Role.MANAGER: (
        Module.TASKS, Module.CHAT, Module.SALES, Module.CRM,
        Module.CALENDAR, Module.FILES, Module.TRIPS,
        # Seeing the roster, not administering it: you cannot hand somebody a
        # task without being able to find them.
        Module.EMPLOYEES, Module.REPORTS,
    ),
    # An employee works what they are given: no reports. The roster is there
    # because knowing who your colleagues are is not an administrative
    # privilege, and the sales board is there because raising a lead is not
    # one either — anybody who meets a customer can bring one in, and a lead
    # nobody was allowed to write down is a lead the company never had. What
    # they may *do* on that board is still narrow: see `DEFAULT_PERMISSIONS`.
    Role.EMPLOYEE: (
        Module.TASKS, Module.CHAT, Module.CALENDAR, Module.FILES,
        Module.EMPLOYEES, Module.SALES,
        # Seeing where the company books people, not booking anybody: the
        # hotel list has always been open to everyone.
        Module.TRIPS,
    ),
    # The narrowest membership there is. Everything beyond talking has to be
    # granted deliberately.
    Role.GUEST: (Module.CHAT,),
}


def _writes(module: str) -> tuple[str, ...]:
    return Permission.BY_MODULE[module]


def _views_only(*modules: str) -> tuple[str, ...]:
    """The `.view` permission of each module and nothing else."""
    return tuple(f"{module}.view" for module in modules)


DEFAULT_PERMISSIONS: dict[str, tuple[str, ...]] = {
    # Everything, in both cases. The difference between them is reach — one
    # workspace or all of the company's — and that is decided by
    # `Role.COMPANY_WIDE`, not by the permission list.
    Role.OWNER: Permission.all(),
    Role.ADMIN: Permission.all(),
    Role.MANAGER: (
        *_writes(Module.TASKS),
        *_writes(Module.CHAT),
        *_writes(Module.SALES),
        *_writes(Module.CRM),
        *_writes(Module.CALENDAR),
        Permission.FILE_VIEW,
        Permission.FILE_UPLOAD,
        Permission.FILE_DOWNLOAD,
        Permission.FILE_CREATE_FOLDER,
        Permission.TRIP_VIEW,
        Permission.TRIP_CREATE,
        Permission.REPORT_VIEW,
        # Sees the roster; changing it is administration.
        Permission.EMPLOYEE_VIEW,
    ),
    Role.EMPLOYEE: (
        Permission.TASK_VIEW,
        # Their own work: they may move a task they were given along and talk
        # about it, but not create, reassign or delete one.
        Permission.TASK_STATUS,
        Permission.TASK_COMMENT,
        Permission.CHAT_VIEW,
        Permission.CHAT_CREATE,
        Permission.CHAT_SEND,
        Permission.CHAT_DELETE_OWN,
        Permission.EVENT_VIEW,
        Permission.EVENT_CREATE_OWN,
        # Raising a lead, and seeing the board it lands on. Not editing,
        # assigning, moving a stage or deleting: bringing a customer in is
        # everybody's job, and what happens to the deal afterwards is the
        # sales side's.
        Permission.DEAL_VIEW,
        Permission.DEAL_CREATE,
        Permission.FILE_VIEW,
        Permission.FILE_UPLOAD,
        Permission.FILE_DOWNLOAD,
        Permission.TRIP_VIEW,
        Permission.EMPLOYEE_VIEW,
    ),
    Role.GUEST: (
        Permission.CHAT_VIEW,
        Permission.CHAT_SEND,
        Permission.CHAT_DELETE_OWN,
    ),
}


def default_access(role: str) -> tuple[list[str], list[str]]:
    """What a role opens and may do, before a workspace configures it."""
    role = Role.clean(role)
    modules = Module.clean(DEFAULT_MODULES.get(role, DEFAULT_MODULES[Role.EMPLOYEE]))
    permissions = Permission.clean(
        DEFAULT_PERMISSIONS.get(role, DEFAULT_PERMISSIONS[Role.EMPLOYEE])
    )
    return modules, permissions


def resolve_for_employee(employee: dict) -> tuple[list[str], list[str]]:
    """A convenience the permission layer does not use — see `access_repository`.

    Kept here only so the chat-only rule has one statement of itself: such a
    member has no role in the workspace and opens nothing, whatever their row
    says.
    """
    if employee.get("is_chat_only"):
        return [], []
    return default_access(employee.get("role"))


def resolve(
    *,
    role: str,
    role_modules=None,
    role_permissions=None,
    module_override=None,
    permission_override=None,
) -> tuple[list[str], list[str]]:
    """The final answer to "where" and "what", for one person.

    Four inputs, in order of increasing specificity:

    * the role's configured modules and permissions — the workspace's policy
      for everybody holding that role;
    * this person's own overrides, set when they were invited or edited.

    An override *replaces* rather than merges. "Module access: by role, or
    configure" is a choice between two answers in the TZ, not a base plus
    additions — and a merge would make it impossible to invite a manager
    *without* the sales board, which is exactly what the configure option is
    for.

    The last step is the rule the whole model rests on: a permission whose
    module is not open is dropped. Not an error, not a narrower grant —
    nothing. That way no call site has to remember to check both.
    """
    role = Role.clean(role)
    if role_modules is None or role_permissions is None:
        fallback_modules, fallback_permissions = default_access(role)
        role_modules = fallback_modules if role_modules is None else role_modules
        role_permissions = (
            fallback_permissions if role_permissions is None else role_permissions
        )

    modules = Module.clean(
        module_override if module_override is not None else role_modules
    )
    granted = Permission.clean(
        permission_override if permission_override is not None else role_permissions
    )

    open_modules = set(modules)
    permissions = [
        permission
        for permission in granted
        if Permission.module_of(permission) in open_modules
    ]
    return modules, permissions


# ─── The older capability map, derived from this one ──────────────────────────
#
# The workspace's endpoints were written against a flat map of `can_*` flags,
# and the mobile app draws its buttons from the same names. Rather than edit
# thirty-odd views and ship an app release to match, the map is now *computed*
# from the resolved permissions below — so the role editor takes effect
# everywhere at once, and there is one source of truth instead of two that
# drift.
#
# Every flag whose meaning is a permission is listed here. The handful that
# are not — attendance, which the TZ defers to its own document, and the two
# scope flags — stay role-derived and are marked as such in [capabilities_from].

CAPABILITY_PERMISSIONS: dict[str, str] = {
    "can_create_task": Permission.TASK_CREATE,
    "can_edit_task": Permission.TASK_EDIT,
    "can_delete_task": Permission.TASK_DELETE,
    "can_assign_task": Permission.TASK_ASSIGN,
    "can_update_task_status": Permission.TASK_STATUS,
    "can_comment_task": Permission.TASK_COMMENT,
    "can_create_event": Permission.EVENT_CREATE,
    "can_edit_any_event": Permission.EVENT_EDIT,
    "can_create_personal_event": Permission.EVENT_CREATE_OWN,
    "can_post_lead": Permission.DEAL_CREATE,
    "can_create_group_chat": Permission.CHAT_MANAGE_GROUP,
    "can_chat": Permission.CHAT_SEND,
    "can_use_mail": Permission.CHAT_VIEW,
    "can_view_team": Permission.EMPLOYEE_VIEW,
    "can_manage_team": Permission.EMPLOYEE_CHANGE_ROLE,
    "can_request_help": Permission.EMPLOYEE_INVITE,
    "can_view_hotels": Permission.TRIP_VIEW,
    "can_book_hotel": Permission.TRIP_CREATE,
}


def capabilities_from(
    role: str, modules: list[str], permissions: list[str]
) -> dict[str, bool]:
    """The legacy flag map, computed from what somebody may actually do.

    `modules` is unused except through `permissions` — [resolve] has already
    dropped anything belonging to a closed module, which is what makes this
    safe to read flag by flag.
    """
    role = Role.clean(role)
    held = set(permissions)
    manager = role in {Role.OWNER, Role.ADMIN, Role.MANAGER}
    owner = role == Role.OWNER

    flags = {
        name: permission in held
        for name, permission in CAPABILITY_PERMISSIONS.items()
    }

    # Not permissions, and deliberately left as they were.
    #
    # Attendance has its own specification (TZ §20) and no module in this
    # catalogue yet, so gating it on one would be inventing policy. The two
    # scope flags answer "how much of the company do you see", which is a
    # different question from "what may you do" and is not something the role
    # editor offers.
    flags["can_view_attendance"] = True
    flags["can_manage_attendance"] = manager
    flags["can_manage_attendance_location"] = owner
    flags["can_pick_employee_of_month"] = owner
    flags["sees_all_company_data"] = manager
    return flags
