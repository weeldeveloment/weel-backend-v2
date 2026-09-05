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

from django.utils.translation import get_language


def request_lang() -> str:
    """The reader's language for labels: ``ru`` when the request came in
    Russian (``X-Language`` / ``Accept-Language``, activated by the locale
    middleware), otherwise ``uz`` — the mobile app's default."""
    code = (get_language() or "uz").split("-")[0].lower()
    return "ru" if code == "ru" else "uz"


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

    #: Who may plug an outside service into the workspace — the owner, the
    #: administrator ("lider") and the manager ("rahbar"). Wider than
    #: [ADMINISTRATIVE] because connecting Meta is a decision about where the
    #: funnel's leads come from, and the manager is who answers for the funnel;
    #: still not the whole roster, because it hands us a token to the company's
    #: Facebook account. See `apps/b2b/integrations/permissions.py`.
    INTEGRATION_ROLES = frozenset({OWNER, ADMIN, MANAGER})

    LABELS = {
        OWNER: "Egasi",
        ADMIN: "Administrator",
        MANAGER: "Rahbar",
        EMPLOYEE: "Xodim",
        GUEST: "Mehmon",
    }

    LABELS_RU = {
        OWNER: "Владелец",
        ADMIN: "Администратор",
        MANAGER: "Руководитель",
        EMPLOYEE: "Сотрудник",
        GUEST: "Гость",
    }

    #: What the roster has called these. `performer` is the manager — the
    #: column has said so since the dashboard was written — and `lider` was
    #: the workspace administrator before the TZ gave it that name. Read on the
    #: way in so stored rows keep resolving; never written.
    ALIASES = {"performer": MANAGER, "lider": ADMIN, "ghost": GUEST}

    #: Who outranks whom. TZ v2 §5.2 and §12 state one rule for every act
    #: that hands somebody standing — accepting a join request, changing a
    #: role, editing what a role may do: nobody hands out more than they
    #: hold. The matrix (§11) spells the same rule out row by row — an admin
    #: assigns "below the admin's level", a manager assigns "employee/guest"
    #: — and this is the number those rows compare.
    RANK = {OWNER: 4, ADMIN: 3, MANAGER: 2, EMPLOYEE: 1, GUEST: 0}

    @classmethod
    def rank(cls, role: str | None) -> int:
        return cls.RANK[cls.clean(role)]

    @classmethod
    def outranks(cls, actor: str | None, target: str | None) -> bool:
        """Strictly above: an admin does not outrank an admin."""
        return cls.rank(actor) > cls.rank(target)

    @classmethod
    def assignable(cls, actor: str | None, role: str | None) -> bool:
        """Whether somebody holding `actor` may hand out `role`.

        Strictly below their own — an admin makes managers, employees and
        guests, never another admin — and nobody makes an owner: that is a
        transfer of the company, which only WEEL's own desk performs (§2).
        """
        return cls.clean(role) != cls.OWNER and cls.outranks(actor, role)

    @classmethod
    def clean(cls, role: str | None) -> str:
        """The canonical role behind whatever is stored."""

        name = (role or "").strip().lower()
        name = cls.ALIASES.get(name, name)
        return name if name in cls.CHOICES else cls.EMPLOYEE

    @classmethod
    def label(cls, role: str | None, lang: str | None = None) -> str:
        labels = cls.LABELS_RU if (lang or request_lang()) == "ru" else cls.LABELS
        return labels.get(cls.clean(role), labels[cls.EMPLOYEE])


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

    LABELS_RU = {
        TASKS: "Задачи",
        CHAT: "Чат",
        SALES: "Продажи",
        CRM: "Клиенты",
        CALENDAR: "Календарь",
        FILES: "Файлы",
        TRIPS: "Командировки",
        EMPLOYEES: "Сотрудники",
        REPORTS: "Отчёты",
    }

    @classmethod
    def label(cls, module: str, lang: str | None = None) -> str:
        labels = cls.LABELS_RU if (lang or request_lang()) == "ru" else cls.LABELS
        return labels.get(module, cls.LABELS.get(module, module))

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
    #: The stock room behind the board — the "Ombor" the sales screen opens.
    #: Inside the sales module rather than a module of its own because the
    #: TZ puts it there: a button beside CRM on the sales page, not a tab in
    #: the navigation. The verbs follow the TZ's four roles: viewing the
    #: catalogue and the sellable balance; running receipts, transfers and
    #: counts; writing stock off; repricing; selling at a free price; seeing
    #: what things cost; and importing or exporting the catalogue.
    STOCK_VIEW = "sales.stock_view"
    STOCK_MANAGE = "sales.stock_manage"
    STOCK_WRITE_OFF = "sales.stock_write_off"
    STOCK_REPRICE = "sales.stock_reprice"
    STOCK_FREE_PRICE = "sales.stock_free_price"
    STOCK_VIEW_COST = "sales.stock_view_cost"
    STOCK_IMPORT = "sales.stock_import"

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
    #: Opening a new workspace under this company. TZ v2 §11: the owner and
    #: the administrator may; a manager or an employee only if handed this;
    #: a guest never, whatever they were handed — see `Role.assignable`.
    WORKSPACE_CREATE = "employees.create_workspace"


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
            STOCK_VIEW, STOCK_MANAGE, STOCK_WRITE_OFF, STOCK_REPRICE,
            STOCK_FREE_PRICE, STOCK_VIEW_COST, STOCK_IMPORT,
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
            WORKSPACE_CREATE,
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
        # Every task permission except deleting one — the TZ's rights matrix
        # (§11) gives that to the owner and the administrator only, so it is
        # left out here rather than granted and then hidden behind a second
        # check. Everything else a manager runs the board with stays.
        *(p for p in _writes(Module.TASKS) if p != Permission.TASK_DELETE),
        *_writes(Module.CHAT),
        # Same story for the sales board: deleting a lead is the owner's or
        # the administrator's call, not the manager's. In the stock room the
        # TZ draws the same line twice more: writing stock off and repricing
        # are the owner's and the administrator's, and a warehouse manager
        # gets them only when handed them.
        *(p for p in _writes(Module.SALES) if p not in (
            Permission.DEAL_DELETE, Permission.STOCK_WRITE_OFF, Permission.STOCK_REPRICE,
        )),
        *_writes(Module.CRM),
        *_writes(Module.CALENDAR),
        Permission.FILE_VIEW,
        Permission.FILE_UPLOAD,
        Permission.FILE_DOWNLOAD,
        Permission.FILE_CREATE_FOLDER,
        Permission.TRIP_VIEW,
        Permission.TRIP_CREATE,
        Permission.REPORT_VIEW,
        Permission.EMPLOYEE_VIEW,
        # TZ v2 §11: a manager runs the people in their own workspace — sets
        # an employee or a guest's role (never higher: `Role.assignable`),
        # narrows or widens their modules within the manager's own, and
        # shows somebody the door of this workspace. Not the company's door,
        # not inviting outsiders, and not answering join requests: those
        # rows say "при разрешении", so they stay off until the role editor
        # hands them over.
        Permission.EMPLOYEE_CHANGE_ROLE,
        Permission.EMPLOYEE_CHANGE_MODULES,
        Permission.EMPLOYEE_REMOVE_WORKSPACE,
    ),
    Role.EMPLOYEE: (
        Permission.TASK_VIEW,
        # TZ v2 §6: creating a record — a task, a lead, a quick sale — is
        # open to anybody who can open the module. Creating one is not the
        # right to edit or delete it afterwards: an employee moves their own
        # task along and talks about it, but does not reassign or delete it.
        Permission.TASK_CREATE,
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
        # The sales manager of the TZ: sees the catalogue and what can be
        # sold, picks products into a quick sale. Not the purchase prices,
        # and not a free price — both are handed over, never assumed.
        Permission.STOCK_VIEW,
        Permission.FILE_VIEW,
        Permission.FILE_UPLOAD,
        Permission.FILE_DOWNLOAD,
        Permission.TRIP_VIEW,
        Permission.EMPLOYEE_VIEW,
    ),
    # A guest's default *modules* are chat and nothing else, and [resolve]
    # drops every permission below whose module is closed — so on the day
    # they arrive this list is exactly three chat permissions. It is longer
    # than that on purpose: TZ v2 §6 says anybody with access to a module
    # may create its records, "including a Guest", so a guest lent the sales
    # board or the task list starts raising leads and tasks on it the moment
    # the module is opened, with no second grant to remember. Reading and
    # creating, and their own work: never editing, deleting, assigning or
    # administering anything.
    Role.GUEST: (
        Permission.TASK_VIEW,
        Permission.TASK_CREATE,
        Permission.TASK_STATUS,
        Permission.TASK_COMMENT,
        Permission.CHAT_VIEW,
        Permission.CHAT_SEND,
        Permission.CHAT_DELETE_OWN,
        Permission.DEAL_VIEW,
        Permission.DEAL_CREATE,
        Permission.STOCK_VIEW,
        Permission.CLIENT_VIEW,
        Permission.CLIENT_CREATE,
        Permission.EVENT_VIEW,
        Permission.EVENT_CREATE_OWN,
        Permission.FILE_VIEW,
        Permission.FILE_UPLOAD,
        Permission.FILE_DOWNLOAD,
        Permission.TRIP_VIEW,
        Permission.EMPLOYEE_VIEW,
        Permission.REPORT_VIEW,
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
    # «Hisobotlar» — whether the report screen opens at all, and whether its
    # figures may leave the phone as a file. An employee has neither by
    # default (TZ v2: no reports module); the role editor can grant both.
    "can_view_reports": Permission.REPORT_VIEW,
    "can_export_reports": Permission.REPORT_EXPORT,
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
    # Plugging an outside service into the funnel. Not a permission in the
    # catalogue: the TZ's modules are parts of the workspace and this is a
    # company-level commitment — a token to the company's Facebook account,
    # and every lead that account produces landing on this board. The owner,
    # the administrator ("lider") and the manager ("rahbar"), who is the one
    # answering for the funnel those leads land in — but no further down the
    # roster. See `apps/b2b/integrations/permissions.py`.
    flags["can_manage_integrations"] = role in Role.INTEGRATION_ROLES

    flags["can_view_attendance"] = True
    flags["can_manage_attendance"] = manager
    flags["can_manage_attendance_location"] = owner
    # TZ §10: the owner or the administrator ("lider") — not the manager, who
    # may only nominate if that feature is ever added, and never decides.
    flags["can_pick_employee_of_month"] = role in Role.ADMINISTRATIVE
    flags["sees_all_company_data"] = manager
    # TZ v2 §11 "Создавать рабочую среду": the owner and the administrator
    # unconditionally, a manager or an employee by permission, a guest never.
    flags["can_create_workspace"] = may_create_workspace(role, permissions)
    return flags


def may_create_workspace(role: str | None, permissions) -> bool:
    """TZ v2 §11's "create a workspace" row, for one roster row."""
    role = Role.clean(role)
    if role == Role.GUEST:
        return False
    return role in Role.ADMINISTRATIVE or Permission.WORKSPACE_CREATE in set(permissions)

