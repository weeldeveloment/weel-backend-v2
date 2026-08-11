from __future__ import annotations

from apps.b2b.models import EmployeeRole

# Roles that may run the company: create and assign tasks, put events on the
# shared calendar, open group chats. Everyone else is a plain employee who
# works the tasks they were given.
MANAGER_ROLES = frozenset({EmployeeRole.OWNER, EmployeeRole.PERFORMER})


def is_manager(role: str | None) -> bool:
    return role in MANAGER_ROLES


def capabilities_for(role: str | None) -> dict[str, bool]:
    """The permission map the mobile app renders its UI from.

    The client must not re-derive these from the role string — new roles or a
    changed policy would then need an app release. It asks the server what the
    signed-in person may do and hides the rest.
    """
    manager = is_manager(role)
    owner = role == EmployeeRole.OWNER

    return {
        # Tasks
        "can_create_task": manager,
        "can_edit_task": manager,
        "can_delete_task": manager,
        "can_assign_task": manager,
        # Anyone can move a task they were assigned along its workflow.
        "can_update_task_status": True,
        "can_comment_task": True,
        # Calendar
        "can_create_event": manager,
        "can_edit_any_event": manager,
        # An employee still gets a private calendar of their own.
        "can_create_personal_event": True,
        # Chat
        "can_create_group_chat": manager,
        "can_chat": True,
        # Team & hotels
        "can_view_team": True,
        "can_manage_team": owner,
        # Owner picks who gets the badge; everyone can see who won it.
        "can_pick_employee_of_month": owner,
        "can_view_hotels": True,
        "can_book_hotel": manager,
        # Corporate mail. Having a mailbox at all is a separate question the
        # server answers per-employee (`/mail/me/`) — these flags only say what
        # someone with one is allowed to do with it.
        "can_use_mail": True,
        # Everyone with a mailbox may write to the outside world — that is the
        # point of giving an employee a company address. Abuse is held back by
        # the per-mailbox send limit rather than by role, so that a compromised
        # account cannot burn the sending IP's reputation.
        "can_send_external_mail": True,
        "can_manage_mail_domain": owner,
        "can_manage_mailboxes": owner,
        # Whether this person sees the whole company or only their own work.
        "sees_all_company_data": manager,
    }
