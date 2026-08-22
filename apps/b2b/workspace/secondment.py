"""Lending a person to another workspace: the vocabulary, in one place.

The product's shape, said plainly:

* An **org** is what the owner signs up as. It holds one or more workspaces.
* A **workspace** is a `b2b_company` row — its own leads, tasks, roster, chat.
  Two workspaces in the same org share nothing operational: ten leads in one
  and fifty in the other are ten and fifty, never sixty.
* A **secondment** is somebody from one workspace agreeing to work in another
  for a while. A lider or owner asks ("we are drowning in leads"); the person
  accepts, or declines with a reason.

What acceptance actually does is create an ordinary employee row for them in
the host workspace, flagged `is_guest` and pointed back at the row they were
hired into. That is the whole trick: every query in this schema scopes by
`company_id`, so from the moment the row exists the guest can be assigned a
lead, added to a chat and put on the calendar with nothing anywhere having to
learn what a secondment is.

Two things are then true that need guarding, and both live here:

* the guest's standing is **time-boxed** — see [Membership.is_live];
* their reach is **narrower than their role** — a secondment grants named
  modules, and `savdo` not being among them has to mean the sales board is
  closed to them, not merely hidden by the app.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.utils import timezone

from apps.b2b.models import EmployeeRole


class RequestStatus:
    """Where an ask has got to."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    # Withdrawn by the workspace that sent it, before it was answered.
    CANCELLED = "cancelled"

    CHOICES = [PENDING, ACCEPTED, DECLINED, CANCELLED]
    #: The ones that are over. A request in any of these is history.
    CLOSED = frozenset({ACCEPTED, DECLINED, CANCELLED})


class RequestRole:
    """What standing the person is being offered.

    The same four the app's picker offers. `manager` is the roster's
    `performer` under the name the app uses for it; `lider` is a rank above,
    and the two are deliberately not the same thing — a lider may ask another
    workspace for help, a manager may not.
    """

    LIDER = "lider"
    MANAGER = "manager"
    EMPLOYEE = "employee"
    #: In the workspace, off every list — for someone who needs access without
    #: appearing as staff.
    GHOST = "ghost"

    CHOICES = [LIDER, MANAGER, EMPLOYEE, GHOST]

    _TO_EMPLOYEE_ROLE = {
        LIDER: EmployeeRole.LIDER,
        MANAGER: EmployeeRole.PERFORMER,
        EMPLOYEE: EmployeeRole.EMPLOYEE,
        # In the workspace, off every list. Their standing is an employee's;
        # what makes them a ghost is `is_hidden` on the row.
        GHOST: EmployeeRole.EMPLOYEE,
    }

    @classmethod
    def to_employee_role(cls, role: str | None) -> str:
        """The roster role a guest with this standing is created as.

        Never `owner`: accepting an invitation cannot make somebody the owner
        of a workspace they were lent to, whatever the request said.
        """
        return cls._TO_EMPLOYEE_ROLE.get(role or "", EmployeeRole.EMPLOYEE)

    @classmethod
    def is_hidden(cls, role: str | None) -> bool:
        return role == cls.GHOST


class Module:
    """A part of the workspace a secondment can open on its own.

    These are the switches on the app's "Qo'shimcha dostup" card. A permanent
    employee has no module list at all — their role is the whole story. A
    guest has one, and it can only ever *narrow* what their role would allow.
    """

    CHAT = "chat"
    SALES = "savdo"
    TASKS = "vazifa"
    CALENDAR = "taqvim"
    FILES = "fayllar"

    CHOICES = [CHAT, SALES, TASKS, CALENDAR, FILES]

    #: Files are shared per folder rather than per person, so there is nothing
    #: for a per-secondment grant to switch — the app draws the row disabled
    #: for the same reason. Listed so a stored value is still understood.
    GRANTABLE = frozenset({CHAT, SALES, TASKS, CALENDAR})

    @classmethod
    def clean(cls, modules) -> list[str]:
        """The stored form of what a request asked for: known, grantable, and
        in a fixed order so two identical grants compare equal."""
        asked = {str(module) for module in (modules or [])}
        return [module for module in cls.CHOICES if module in asked and module in cls.GRANTABLE]


#: Which capabilities each module governs. A capability not named here is not
#: a module's to withhold — `can_chat` is, `can_view_attendance` is not, because
#: the roll call is how a workspace knows who is around today and a guest who
#: cannot see it is a guest nobody can plan around.
MODULE_CAPABILITIES: dict[str, tuple[str, ...]] = {
    Module.CHAT: ("can_chat", "can_create_group_chat", "can_use_mail"),
    Module.SALES: ("can_post_lead",),
    Module.TASKS: (
        "can_create_task",
        "can_edit_task",
        "can_delete_task",
        "can_assign_task",
        "can_update_task_status",
        "can_comment_task",
    ),
    Module.CALENDAR: (
        "can_create_event",
        "can_edit_any_event",
        "can_create_personal_event",
    ),
}


@dataclass(frozen=True)
class Membership:
    """A live secondment, as the permission layer reads it."""

    employee_id: int
    company_id: int
    home_employee_id: int
    role: str
    modules: tuple[str, ...]
    starts_at: datetime | None
    ends_at: datetime | None
    is_active: bool

    @classmethod
    def from_row(cls, row: dict) -> "Membership":
        return cls(
            employee_id=row["employee_id"],
            company_id=row["company_id"],
            home_employee_id=row["home_employee_id"],
            role=row.get("role") or RequestRole.EMPLOYEE,
            modules=tuple(row.get("modules") or []),
            starts_at=row.get("starts_at"),
            ends_at=row.get("ends_at"),
            is_active=bool(row.get("is_active", True)),
        )

    @property
    def is_live(self) -> bool:
        """Whether this secondment is in force *right now*.

        Checked on every request rather than only by the nightly sweep. A
        token minted at 09:00 for a secondment that ends at 17:00 is still a
        valid token at 18:00, and the sweep may not have run — so the window
        has to be the thing that decides, not the flag the sweep sets.
        """
        if not self.is_active:
            return False
        now = timezone.now()
        if self.starts_at and now < self.starts_at:
            return False
        if self.ends_at and now > self.ends_at:
            return False
        return True

    def allows(self, module: str) -> bool:
        return module in self.modules
