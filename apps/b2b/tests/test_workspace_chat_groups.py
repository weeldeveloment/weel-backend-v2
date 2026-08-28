"""The group chat's own screen: its name, its picture and who is in it.

Every one of these endpoints writes to a room that other people are reading,
so the checks worth pinning down are the ones about *who may write*: a plain
member must not be able to rename the room or throw somebody out, an admin must
not be removable by someone who is not one, and the room must never be left
without an admin — a group nobody can administer cannot be repaired from inside
the app at all.
"""
from unittest.mock import patch

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.workspace.authentication import WorkspaceUser
from apps.b2b.workspace.views import (
    WorkspaceGroupMemberView,
    WorkspaceGroupMembersView,
    WorkspaceGroupView,
)

factory = APIRequestFactory()


class _User(WorkspaceUser):
    """A signed-in employee whose capability map is stated rather than resolved.

    The real one reads the workspace's access catalogue out of the database;
    these tests are about the group rules, and the only flag any of them turns
    on is `can_manage_team`.
    """

    def __init__(self, employee: dict, *, can_manage_team: bool = False):
        super().__init__(employee)
        self._caps = {"can_manage_team": can_manage_team}

    @property
    def capabilities(self) -> dict:
        return self._caps


def _user(employee_id: int, *, role: str = "employee", can_manage_team: bool = False):
    return _User(
        {
            "id": employee_id,
            "company_id": 55,
            "role": role,
            "full_name": f"Xodim {employee_id}",
            "phone": f"+99890000000{employee_id}",
        },
        can_manage_team=can_manage_team,
    )


ADMIN = _user(7)
MEMBER = _user(8)
# Runs the company but is only an ordinary member of this particular room.
MANAGER = _user(9, role="manager", can_manage_team=True)

GROUP = {
    "id": 3,
    "company_id": 55,
    "group_name": "Dizayn jamoa",
    "created_by": 7,
    "photo": None,
    "created_at": "2026-08-01T09:00:00Z",
}
DIRECT = {**GROUP, "group_name": None}


def _membership(employee_id: int, role: str = "member") -> dict:
    return {"id": employee_id, "thread_id": 3, "employee_id": employee_id, "role": role}


def _roster(*roles: tuple[int, str]) -> list[dict]:
    return [
        {
            "id": employee_id,
            "full_name": f"Xodim {employee_id}",
            "role": "employee",
            "member_role": member_role,
        }
        for employee_id, member_role in roles
    ]


class TestGroupDetail:
    def test_it_reports_the_members_and_what_the_caller_may_do(self):
        request = factory.get("/chats/3/group/")
        force_authenticate(request, user=ADMIN)

        with patch("apps.b2b.workspace.views.repo") as repo, patch(
            "apps.b2b.workspace.views.presence"
        ) as presence:
            repo.get_thread.return_value = GROUP
            repo.thread_member.return_value = _membership(7, "admin")
            repo.list_thread_members.return_value = _roster((7, "admin"), (8, "member"))
            presence.online_ids.return_value = {7}
            presence.last_seen.return_value = {}
            response = WorkspaceGroupView.as_view()(request, thread_id=3)

        assert response.status_code == 200
        assert response.data["member_count"] == 2
        assert response.data["my_role"] == "admin"
        assert response.data["can_manage"] is True

    def test_a_plain_member_is_told_they_may_not_manage_it(self):
        # The flag is what the app draws its buttons from, so it has to agree
        # with what the write endpoints would actually allow.
        request = factory.get("/chats/3/group/")
        force_authenticate(request, user=MEMBER)

        with patch("apps.b2b.workspace.views.repo") as repo, patch(
            "apps.b2b.workspace.views.presence"
        ) as presence:
            repo.get_thread.return_value = GROUP
            repo.thread_member.return_value = _membership(8)
            repo.list_thread_members.return_value = _roster((7, "admin"), (8, "member"))
            presence.online_ids.return_value = set()
            presence.last_seen.return_value = {}
            response = WorkspaceGroupView.as_view()(request, thread_id=3)

        assert response.data["can_manage"] is False

    def test_a_direct_chat_has_no_group_screen(self):
        request = factory.get("/chats/3/group/")
        force_authenticate(request, user=ADMIN)

        with patch("apps.b2b.workspace.views.repo") as repo:
            repo.get_thread.return_value = DIRECT
            repo.thread_member.return_value = _membership(7)
            response = WorkspaceGroupView.as_view()(request, thread_id=3)

        assert response.status_code == 400

    def test_somebody_outside_the_room_is_told_it_does_not_exist(self):
        # Not 403: a refusal would confirm that a room with this id exists and
        # who it belongs to, which is exactly what an outsider must not learn.
        request = factory.get("/chats/3/group/")
        force_authenticate(request, user=MEMBER)

        with patch("apps.b2b.workspace.views.repo") as repo:
            repo.get_thread.return_value = GROUP
            repo.thread_member.return_value = None
            response = WorkspaceGroupView.as_view()(request, thread_id=3)

        assert response.status_code == 404


class TestRename:
    def test_an_admin_renames_the_group(self):
        request = factory.patch("/chats/3/group/", {"group_name": "Yangi nom"}, format="json")
        force_authenticate(request, user=ADMIN)

        with patch("apps.b2b.workspace.views.repo") as repo, patch(
            "apps.b2b.workspace.views.presence"
        ) as presence, patch("apps.b2b.workspace.views.realtime") as realtime:
            repo.get_thread.return_value = GROUP
            repo.thread_member.return_value = _membership(7, "admin")
            repo.update_thread.return_value = {**GROUP, "group_name": "Yangi nom"}
            repo.list_thread_members.return_value = _roster((7, "admin"))
            presence.online_ids.return_value = set()
            presence.last_seen.return_value = {}
            response = WorkspaceGroupView.as_view()(request, thread_id=3)

        assert response.status_code == 200
        assert repo.update_thread.call_args.kwargs["group_name"] == "Yangi nom"
        # The room hears about it, so a member with the thread open sees the
        # new name without reopening it.
        assert realtime.publish_thread.called

    def test_a_member_cannot_rename_it(self):
        request = factory.patch("/chats/3/group/", {"group_name": "Yangi nom"}, format="json")
        force_authenticate(request, user=MEMBER)

        with patch("apps.b2b.workspace.views.repo") as repo:
            repo.get_thread.return_value = GROUP
            repo.thread_member.return_value = _membership(8)
            response = WorkspaceGroupView.as_view()(request, thread_id=3)

        assert response.status_code == 403
        assert not repo.update_thread.called

    def test_a_manager_may_manage_a_room_they_only_belong_to(self):
        # Otherwise a group becomes unmaintainable the day its admins leave
        # the company — which is when somebody most needs to get into it.
        request = factory.patch("/chats/3/group/", {"group_name": "Yangi nom"}, format="json")
        force_authenticate(request, user=MANAGER)

        with patch("apps.b2b.workspace.views.repo") as repo, patch(
            "apps.b2b.workspace.views.presence"
        ) as presence, patch("apps.b2b.workspace.views.realtime"):
            repo.get_thread.return_value = GROUP
            repo.thread_member.return_value = _membership(9)
            repo.update_thread.return_value = {**GROUP, "group_name": "Yangi nom"}
            repo.list_thread_members.return_value = _roster((7, "admin"), (9, "member"))
            presence.online_ids.return_value = set()
            presence.last_seen.return_value = {}
            response = WorkspaceGroupView.as_view()(request, thread_id=3)

        assert response.status_code == 200

    def test_an_empty_name_is_refused(self):
        request = factory.patch("/chats/3/group/", {"group_name": "   "}, format="json")
        force_authenticate(request, user=ADMIN)

        with patch("apps.b2b.workspace.views.repo") as repo:
            repo.get_thread.return_value = GROUP
            repo.thread_member.return_value = _membership(7, "admin")
            response = WorkspaceGroupView.as_view()(request, thread_id=3)

        assert response.status_code == 400


class TestMembers:
    def test_an_admin_adds_people_and_they_start_listening(self):
        request = factory.post("/chats/3/members/", {"member_ids": [11, 12]}, format="json")
        force_authenticate(request, user=ADMIN)

        with patch("apps.b2b.workspace.views.repo") as repo, patch(
            "apps.b2b.workspace.views.presence"
        ) as presence, patch("apps.b2b.workspace.views.realtime"), patch(
            "apps.b2b.workspace.views.add_to_thread"
        ) as add_to_thread:
            repo.get_thread.return_value = GROUP
            repo.thread_member.return_value = _membership(7, "admin")
            repo.employee_ids_in_company.return_value = {11, 12}
            repo.list_thread_members.return_value = _roster(
                (7, "admin"), (11, "member"), (12, "member")
            )
            presence.online_ids.return_value = set()
            presence.last_seen.return_value = {}
            response = WorkspaceGroupMembersView.as_view()(request, thread_id=3)

        assert response.status_code == 200
        assert [c.args[1] for c in repo.add_thread_member.call_args_list] == [11, 12]
        # Without this the new members hear nothing from the room until they
        # reconnect, which reads as the group being broken for them.
        add_to_thread.assert_called_once_with([11, 12], 3)

    def test_somebody_from_another_company_cannot_be_added(self):
        request = factory.post("/chats/3/members/", {"member_ids": [999]}, format="json")
        force_authenticate(request, user=ADMIN)

        with patch("apps.b2b.workspace.views.repo") as repo:
            repo.get_thread.return_value = GROUP
            repo.thread_member.return_value = _membership(7, "admin")
            repo.employee_ids_in_company.return_value = set()
            response = WorkspaceGroupMembersView.as_view()(request, thread_id=3)

        assert response.status_code == 400
        assert not repo.add_thread_member.called

    def test_a_member_cannot_add_anyone(self):
        request = factory.post("/chats/3/members/", {"member_ids": [11]}, format="json")
        force_authenticate(request, user=MEMBER)

        with patch("apps.b2b.workspace.views.repo") as repo:
            repo.get_thread.return_value = GROUP
            repo.thread_member.return_value = _membership(8)
            response = WorkspaceGroupMembersView.as_view()(request, thread_id=3)

        assert response.status_code == 403


class TestRoles:
    def test_an_admin_promotes_a_member(self):
        request = factory.patch("/chats/3/members/8/", {"role": "admin"}, format="json")
        force_authenticate(request, user=ADMIN)

        with patch("apps.b2b.workspace.views.repo") as repo, patch(
            "apps.b2b.workspace.views.presence"
        ) as presence, patch("apps.b2b.workspace.views.realtime"):
            repo.get_thread.return_value = GROUP
            repo.thread_member.side_effect = [_membership(7, "admin"), _membership(8), _membership(7, "admin")]
            repo.list_thread_members.return_value = _roster((7, "admin"), (8, "admin"))
            presence.online_ids.return_value = set()
            presence.last_seen.return_value = {}
            response = WorkspaceGroupMemberView.as_view()(request, thread_id=3, employee_id=8)

        assert response.status_code == 200
        repo.set_thread_member_role.assert_called_once_with(3, 8, "admin")

    def test_the_last_admin_cannot_step_down(self):
        # A group with no admin cannot be renamed, added to or repaired from
        # inside the app.
        request = factory.patch("/chats/3/members/7/", {"role": "member"}, format="json")
        force_authenticate(request, user=ADMIN)

        with patch("apps.b2b.workspace.views.repo") as repo:
            repo.get_thread.return_value = GROUP
            repo.thread_member.side_effect = [_membership(7, "admin"), _membership(7, "admin")]
            repo.thread_admin_ids.return_value = [7]
            response = WorkspaceGroupMemberView.as_view()(request, thread_id=3, employee_id=7)

        assert response.status_code == 409
        assert not repo.set_thread_member_role.called

    def test_one_of_two_admins_may_step_down(self):
        request = factory.patch("/chats/3/members/7/", {"role": "member"}, format="json")
        force_authenticate(request, user=ADMIN)

        with patch("apps.b2b.workspace.views.repo") as repo, patch(
            "apps.b2b.workspace.views.presence"
        ) as presence, patch("apps.b2b.workspace.views.realtime"):
            repo.get_thread.return_value = GROUP
            repo.thread_member.side_effect = [
                _membership(7, "admin"),
                _membership(7, "admin"),
                _membership(7),
            ]
            repo.thread_admin_ids.return_value = [7, 8]
            repo.list_thread_members.return_value = _roster((8, "admin"), (7, "member"))
            presence.online_ids.return_value = set()
            presence.last_seen.return_value = {}
            response = WorkspaceGroupMemberView.as_view()(request, thread_id=3, employee_id=7)

        assert response.status_code == 200
        assert response.data["my_role"] == "member"


class TestRemoval:
    def test_an_admin_removes_a_member(self):
        request = factory.delete("/chats/3/members/8/")
        force_authenticate(request, user=ADMIN)

        with patch("apps.b2b.workspace.views.repo") as repo, patch(
            "apps.b2b.workspace.views.presence"
        ) as presence, patch("apps.b2b.workspace.views.realtime"), patch(
            "apps.b2b.workspace.views.remove_from_thread"
        ) as remove_from_thread:
            repo.get_thread.return_value = GROUP
            repo.thread_member.side_effect = [_membership(7, "admin"), _membership(8)]
            repo.thread_admin_ids.return_value = [7]
            repo.list_thread_members.return_value = _roster((7, "admin"))
            presence.online_ids.return_value = set()
            presence.last_seen.return_value = {}
            response = WorkspaceGroupMemberView.as_view()(request, thread_id=3, employee_id=8)

        assert response.status_code == 200
        repo.remove_thread_member.assert_called_once_with(3, 8)
        remove_from_thread.assert_called_once_with([8], 3)

    def test_a_manager_who_is_not_an_admin_cannot_remove_an_admin(self):
        # An admin is not above another admin, and running the company is not
        # the same as running this room.
        request = factory.delete("/chats/3/members/7/")
        force_authenticate(request, user=MANAGER)

        with patch("apps.b2b.workspace.views.repo") as repo:
            repo.get_thread.return_value = GROUP
            repo.thread_member.side_effect = [_membership(9), _membership(7, "admin")]
            response = WorkspaceGroupMemberView.as_view()(request, thread_id=3, employee_id=7)

        assert response.status_code == 403
        assert not repo.remove_thread_member.called

    def test_anyone_may_leave_without_being_an_admin(self):
        # Nobody can be held in a conversation.
        request = factory.delete("/chats/3/members/8/")
        force_authenticate(request, user=MEMBER)

        with patch("apps.b2b.workspace.views.repo") as repo, patch(
            "apps.b2b.workspace.views.realtime"
        ), patch("apps.b2b.workspace.views.remove_from_thread"):
            repo.get_thread.return_value = GROUP
            repo.thread_member.side_effect = [_membership(8), _membership(8)]
            response = WorkspaceGroupMemberView.as_view()(request, thread_id=3, employee_id=8)

        assert response.status_code == 204
        repo.remove_thread_member.assert_called_once_with(3, 8)

    def test_the_last_admin_leaving_hands_the_room_on(self):
        request = factory.delete("/chats/3/members/7/")
        force_authenticate(request, user=ADMIN)

        with patch("apps.b2b.workspace.views.repo") as repo, patch(
            "apps.b2b.workspace.views.realtime"
        ), patch("apps.b2b.workspace.views.remove_from_thread"):
            repo.get_thread.return_value = GROUP
            repo.thread_member.side_effect = [
                _membership(7, "admin"),
                _membership(7, "admin"),
            ]
            repo.thread_admin_ids.return_value = []
            response = WorkspaceGroupMemberView.as_view()(request, thread_id=3, employee_id=7)

        assert response.status_code == 204
        repo.promote_longest_standing_member.assert_called_once_with(3)
