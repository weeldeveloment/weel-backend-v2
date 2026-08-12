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
        # Mail in the chat section. Everyone, every role: the account belongs
        # to the person, not to the company — they connect an inbox they
        # already own, and nobody else can see it, including the owner. There
        # is deliberately no manager-level flag here to gate it with.
        "can_use_mail": True,
        # Whether this person sees the whole company or only their own work.
        "sees_all_company_data": manager,
    }
