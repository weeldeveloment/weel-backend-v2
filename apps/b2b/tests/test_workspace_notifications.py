"""Who gets told about a task or a calendar entry, and when.

Two separate questions live here:

  * **Who.** A push is addressed to the people a change is *about* — the
    assignees of a task, the participants of an event — and never to the
    person who made the change or to people who already had the thing. Getting
    this wrong is what turns a notification into noise somebody switches off.
  * **When, for a reminder.** The 30/10/0-minute warnings are decided by the
    events themselves on a pass that runs every minute, so the checks are that
    the pass catches up over a restart without ever sending twice, and that
    moving an event makes its reminders due again.

Run against mocked repository calls: the rules are in the view and the task,
not in the database.
"""
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.workspace import push_text
from apps.b2b.workspace.authentication import WorkspaceUser
from apps.b2b.workspace.tasks import (
    EVENT_REMINDER_OFFSETS,
    notify_task_assigned,
    send_event_reminders,
)
from apps.b2b.workspace.views import (
    WorkspaceEventDetailView,
    WorkspaceEventListCreateView,
    WorkspaceLeadListCreateView,
    WorkspaceTaskDetailView,
    WorkspaceTaskListCreateView,
)

COMPANY_ID = 55
MANAGER_ID = 1
ALIYA_ID = 2
BEK_ID = 3

factory = APIRequestFactory()


def _user(role: str, employee_id: int) -> WorkspaceUser:
    return WorkspaceUser({
        "id": employee_id,
        "company_id": COMPANY_ID,
        "role": role,
        "full_name": "Test Person",
        "phone": "+998900000000",
    })


MANAGER = _user("owner", MANAGER_ID)


def _call(view_class, request, user, **kwargs):
    force_authenticate(request, user=user)
    return view_class.as_view()(request, **kwargs)


def _task(**overrides):
    task = {
        "id": 12,
        "company_id": COMPANY_ID,
        "author_id": MANAGER_ID,
        "title": "Shartnomani tayyorlash",
        "description": "",
        "status": "todo",
        "priority": "medium",
        "project": None,
        "due_date": None,
        "assignee_ids": [ALIYA_ID],
        "subtasks": [],
        "created_at": None,
    }
    task.update(overrides)
    return task


def _event(**overrides):
    event = {
        "id": 31,
        "company_id": COMPANY_ID,
        "author_id": MANAGER_ID,
        "title": "Haftalik yig’ilish",
        "event_type": "meeting",
        "starts_at": timezone.now() + timedelta(minutes=30),
        "ends_at": timezone.now() + timedelta(minutes=90),
        "all_day": False,
        "location": "Katta zal",
        "notes": None,
        "participant_ids": [ALIYA_ID],
    }
    event.update(overrides)
    return event


def _recipient(employee_id: int, token: str | None = "tok"):
    return {"employee_id": employee_id, "company_id": COMPANY_ID, "fcm_token": token}


# ─── What a notification says ─────────────────────────────────────────────────

def test_every_push_title_is_uzbek():
    """These used to go through `gettext`, which in a Celery worker resolves
    against `LANGUAGE_CODE = "en"` — and the strings were not in the Uzbek
    catalogue anyway, so a phone was shown "New lead"."""
    assert push_text.LEAD_TITLE == "Yangi lead"
    assert push_text.TASK_TITLE == "Yangi vazifa"
    assert push_text.EVENT_TITLE == "Yangi tadbir"


def test_a_reminder_says_how_long_is_left_until_there_is_none():
    assert push_text.event_reminder_title(30) == "30 daqiqadan keyin"
    assert push_text.event_reminder_title(10) == "10 daqiqadan keyin"
    # Not "0 daqiqadan keyin".
    assert push_text.event_reminder_title(0) == "Tadbir boshlanmoqda"


def test_a_new_lead_is_announced_as_one():
    """`assign_to_me: false` is what leaves the lead on the board — a lead its
    author keeps is not news to anyone else, and is deliberately not pushed."""
    with (
        patch("apps.b2b.workspace.views.repo.create_lead", return_value={"id": 7}),
        patch(
            "apps.b2b.workspace.views.repo.list_company_recipients",
            return_value=[_recipient(ALIYA_ID), _recipient(BEK_ID, token=None)],
        ),
        patch("apps.b2b.workspace.views.mail_repo.create_notification") as feed,
        patch("apps.b2b.workspace.views._lead_payload", return_value={}),
        patch("apps.notification.service.FCMService.send_to_tokens") as send,
        patch("apps.notification.service.b2b_firebase_app", return_value=object()),
    ):
        response = _call(
            WorkspaceLeadListCreateView,
            factory.post(
                "/leads/",
                {
                    "contact_full_name": "Aziz Karimov",
                    "contact_phone": "+998 90 123 45 67",
                    "company_name": "GlobalTrade",
                    "product_name": "CRM",
                    "quantity": 3,
                    "source": "call",
                    "assign_to_me": False,
                },
                format="json",
            ),
            MANAGER,
        )

    assert response.status_code == 201
    assert send.call_args.kwargs["title"] == "Yangi lead"
    assert send.call_args.kwargs["data"]["type"] == "lead"

    # Bek has no token, so he is not in the push — but the lead is still
    # waiting on the board for him, so he is in the notification list.
    assert send.call_args.kwargs["tokens"] == ["tok"]
    assert [call.kwargs["employee_id"] for call in feed.call_args_list] == [
        ALIYA_ID,
        BEK_ID,
    ]
    assert feed.call_args.kwargs["kind"] == "lead"
    assert feed.call_args.kwargs["payload"] == {"lead_id": 7}


# ─── A task somebody was given ────────────────────────────────────────────────

def test_creating_a_task_notifies_the_people_it_was_given_to():
    with (
        patch("apps.b2b.workspace.views.repo.create_task", return_value=_task()),
        patch("apps.b2b.workspace.views._validated_employee_ids", return_value=[ALIYA_ID]),
        patch("apps.b2b.workspace.tasks.notify_task_assigned.delay") as queued,
    ):
        response = _call(
            WorkspaceTaskListCreateView,
            factory.post(
                "/tasks/",
                {"title": "Shartnomani tayyorlash", "assignee_ids": [ALIYA_ID]},
                format="json",
            ),
            MANAGER,
        )

    assert response.status_code == 201
    queued.assert_called_once_with(12, MANAGER_ID, COMPANY_ID, None)


def test_a_broker_that_is_down_does_not_fail_the_create():
    """The task is already stored. Losing the push is not worth a 500."""
    with (
        patch("apps.b2b.workspace.views.repo.create_task", return_value=_task()),
        patch("apps.b2b.workspace.views._validated_employee_ids", return_value=[ALIYA_ID]),
        patch(
            "apps.b2b.workspace.tasks.notify_task_assigned.delay",
            side_effect=RuntimeError("redis is gone"),
        ),
    ):
        response = _call(
            WorkspaceTaskListCreateView,
            factory.post("/tasks/", {"title": "X", "assignee_ids": [ALIYA_ID]}, format="json"),
            MANAGER,
        )

    assert response.status_code == 201


def test_adding_someone_to_a_task_notifies_only_them():
    """Bek joins a task Aliya has had since Monday. Pushing it at Aliya again
    reads as a second task, so the notification is narrowed to the newcomer."""
    with (
        patch("apps.b2b.workspace.views.repo.get_task", return_value=_task()),
        patch("apps.b2b.workspace.views._validated_employee_ids",
              return_value=[ALIYA_ID, BEK_ID]),
        patch("apps.b2b.workspace.views.repo.set_task_assignees"),
        patch("apps.b2b.workspace.views.repo.replace_subtasks"),
        patch("apps.b2b.workspace.views.repo.update_task", return_value=_task()),
        patch("apps.b2b.workspace.tasks.notify_task_assigned.delay") as queued,
    ):
        response = _call(
            WorkspaceTaskDetailView,
            factory.patch("/tasks/12/", {"assignee_ids": [ALIYA_ID, BEK_ID]}, format="json"),
            MANAGER,
            task_id=12,
        )

    assert response.status_code == 200
    queued.assert_called_once_with(12, MANAGER_ID, COMPANY_ID, [BEK_ID])


def test_an_edit_that_changes_no_assignee_notifies_nobody():
    with (
        patch("apps.b2b.workspace.views.repo.get_task", return_value=_task()),
        patch("apps.b2b.workspace.views.repo.update_task", return_value=_task()),
        patch("apps.b2b.workspace.tasks.notify_task_assigned.delay") as queued,
    ):
        response = _call(
            WorkspaceTaskDetailView,
            factory.patch("/tasks/12/", {"title": "Yangi nom"}, format="json"),
            MANAGER,
            task_id=12,
        )

    assert response.status_code == 200
    queued.assert_not_called()


def test_the_person_who_assigned_the_task_is_not_pushed_about_it():
    """A manager who puts themselves on a task is looking at it already."""
    with (
        patch("apps.b2b.workspace.tasks.repo.get_task", return_value=_task()),
        patch(
            "apps.b2b.workspace.tasks.repo.list_task_assignee_recipients",
            return_value=[_recipient(ALIYA_ID)],
        ) as recipients,
        patch("apps.b2b.workspace.tasks.create_notification"),
        patch("apps.b2b.workspace.tasks._push"),
    ):
        notify_task_assigned(12, MANAGER_ID, COMPANY_ID)

    assert recipients.call_args.kwargs["exclude_employee_id"] == MANAGER_ID


def test_a_task_deleted_before_the_push_runs_is_dropped_quietly():
    with (
        patch("apps.b2b.workspace.tasks.repo.get_task", return_value=None),
        patch("apps.b2b.workspace.tasks._push") as push,
    ):
        assert notify_task_assigned(12, MANAGER_ID, COMPANY_ID) == 0

    push.assert_not_called()


# ─── Calendar invitations ─────────────────────────────────────────────────────

def test_creating_an_event_notifies_the_participants():
    with (
        patch("apps.b2b.workspace.views.repo.create_event", return_value=_event()),
        patch("apps.b2b.workspace.views._validated_employee_ids", return_value=[ALIYA_ID]),
        patch("apps.b2b.workspace.tasks.notify_event_created.delay") as queued,
    ):
        starts = timezone.now() + timedelta(hours=2)
        response = _call(
            WorkspaceEventListCreateView,
            factory.post(
                "/events/",
                {
                    "title": "Haftalik yig’ilish",
                    "starts_at": starts.isoformat(),
                    "ends_at": (starts + timedelta(hours=1)).isoformat(),
                    "participant_ids": [ALIYA_ID],
                },
                format="json",
            ),
            MANAGER,
        )

    assert response.status_code == 201
    queued.assert_called_once_with(31, MANAGER_ID, COMPANY_ID, None)


def test_moving_an_event_makes_its_reminders_due_again():
    """The 30-minute row is claimed against the old time. Left in place, a
    meeting pushed from 10:00 to 16:00 warns nobody at all."""
    moved = timezone.now() + timedelta(hours=6)
    with (
        patch("apps.b2b.workspace.views.repo.get_event", return_value=_event()),
        patch("apps.b2b.workspace.views.repo.update_event", return_value=_event()),
        patch("apps.b2b.workspace.views.repo.clear_event_reminders") as cleared,
        patch("apps.b2b.workspace.tasks.notify_event_created.delay"),
    ):
        response = _call(
            WorkspaceEventDetailView,
            factory.patch("/events/31/", {"starts_at": moved.isoformat()}, format="json"),
            MANAGER,
            event_id=31,
        )

    assert response.status_code == 200
    cleared.assert_called_once_with(31)


def test_an_edit_that_leaves_the_time_alone_keeps_the_reminders_claimed():
    with (
        patch("apps.b2b.workspace.views.repo.get_event", return_value=_event()),
        patch("apps.b2b.workspace.views.repo.update_event", return_value=_event()),
        patch("apps.b2b.workspace.views.repo.clear_event_reminders") as cleared,
    ):
        response = _call(
            WorkspaceEventDetailView,
            factory.patch("/events/31/", {"location": "Kichik zal"}, format="json"),
            MANAGER,
            event_id=31,
        )

    assert response.status_code == 200
    cleared.assert_not_called()


# ─── The 30 / 10 / 0-minute reminders ─────────────────────────────────────────

def test_the_three_reminders_are_thirty_ten_and_at_the_time():
    assert EVENT_REMINDER_OFFSETS == (30, 10, 0)


def test_a_reminder_already_sent_is_not_sent_again():
    """What makes the catch-up window safe: the pass looks back over several
    minutes, and the claim is what stops the second look re-sending."""
    with (
        patch(
            "apps.b2b.workspace.tasks.repo.list_events_starting_between",
            return_value=[_event()],
        ),
        patch("apps.b2b.workspace.tasks.repo.claim_event_reminder", return_value=False),
        patch("apps.b2b.workspace.tasks._push") as push,
    ):
        assert send_event_reminders() == 0

    push.assert_not_called()


def test_each_offset_is_claimed_separately():
    """One event, three reminders — so the claim is per offset, not per event,
    or the 10-minute warning would be swallowed by the 30-minute one."""
    with (
        patch(
            "apps.b2b.workspace.tasks.repo.list_events_starting_between",
            return_value=[_event()],
        ),
        patch("apps.b2b.workspace.tasks.repo.claim_event_reminder", return_value=True) as claim,
        patch(
            "apps.b2b.workspace.tasks.repo.list_event_participant_recipients",
            return_value=[_recipient(ALIYA_ID)],
        ),
        patch("apps.b2b.workspace.tasks.create_notification"),
        patch("apps.b2b.workspace.tasks._push"),
    ):
        assert send_event_reminders() == 3

    assert [call.args[1] for call in claim.call_args_list] == [30, 10, 0]


def test_a_reminder_goes_to_everyone_invited_including_the_author():
    """Unlike an invitation, a reminder is not about who did something — the
    person who booked the meeting wants telling that it is starting too."""
    with (
        patch(
            "apps.b2b.workspace.tasks.repo.list_events_starting_between",
            return_value=[_event()],
        ),
        patch("apps.b2b.workspace.tasks.repo.claim_event_reminder", return_value=True),
        patch(
            "apps.b2b.workspace.tasks.repo.list_event_participant_recipients",
            return_value=[_recipient(ALIYA_ID)],
        ) as recipients,
        patch("apps.b2b.workspace.tasks.create_notification"),
        patch("apps.b2b.workspace.tasks._push"),
    ):
        send_event_reminders()

    for call in recipients.call_args_list:
        assert "exclude_employee_id" not in call.kwargs


def test_the_reminder_push_carries_the_event_and_how_long_is_left():
    with (
        patch(
            "apps.b2b.workspace.tasks.repo.list_events_starting_between",
            side_effect=[[_event()], [], []],
        ),
        patch("apps.b2b.workspace.tasks.repo.claim_event_reminder", return_value=True),
        patch(
            "apps.b2b.workspace.tasks.repo.list_event_participant_recipients",
            return_value=[_recipient(ALIYA_ID)],
        ),
        patch("apps.b2b.workspace.tasks.create_notification"),
        patch("apps.b2b.workspace.tasks._push") as push,
    ):
        send_event_reminders()

    data = push.call_args.kwargs["data"]
    assert data["type"] == "event"
    assert data["event_id"] == "31"
    assert data["minutes_before"] == "30"


def test_an_event_nobody_is_on_costs_no_push():
    with (
        patch(
            "apps.b2b.workspace.tasks.repo.list_events_starting_between",
            return_value=[_event()],
        ),
        patch("apps.b2b.workspace.tasks.repo.claim_event_reminder", return_value=True),
        patch(
            "apps.b2b.workspace.tasks.repo.list_event_participant_recipients",
            return_value=[],
        ),
        patch("apps.b2b.workspace.tasks._push") as push,
    ):
        assert send_event_reminders() == 0

    push.assert_not_called()
