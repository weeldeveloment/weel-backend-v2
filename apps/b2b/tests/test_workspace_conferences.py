"""Conferences — the rules in `conferences.py`, against a mocked repository.

What has to hold: the three scopes resolve to the right people and refuse
cleanly when they resolve to nobody; a conference opens a group, a room and
an invitation card in one breath; the card is the access rule, so joining is
being in that group and nothing else; only the organiser ends one, and ending
rewrites the card rather than leaving a button into an empty room; and a room
nobody closed is closed by the clock.
"""
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.workspace import conferences
from apps.b2b.workspace.authentication import WorkspaceUser
from apps.b2b.workspace.calls import CallError
from apps.b2b.workspace.conferences_repository import ConferenceScope, ConferenceStatus
from apps.b2b.workspace.conferences_views import (
    WorkspaceConferenceEndView,
    WorkspaceConferenceJoinView,
    WorkspaceConferenceListCreateView,
)

COMPANY_ID = 55
AZIZ_ID = 7
BEK_ID = 9
DILNOZA_ID = 11
THREAD_ID = 42
CONFERENCE_ID = 300

factory = APIRequestFactory()

LIVEKIT = dict(
    LIVEKIT_URL="wss://call.weel.uz",
    LIVEKIT_API_KEY="weel",
    LIVEKIT_API_SECRET="lk-s3cret",
    LIVEKIT_TOKEN_TTL_SECONDS=7200,
    CALL_PROVIDER="",
    CONFERENCE_MAX_DURATION_SECONDS=14400,
    CELERY_TASK_ALWAYS_EAGER=True,
)

UNCONFIGURED = dict(
    LIVEKIT_URL="",
    LIVEKIT_API_KEY="",
    LIVEKIT_API_SECRET="",
    JITSI_SERVER_URL="",
    JITSI_APP_SECRET="",
    CALL_PROVIDER="",
)


def _user(employee_id: int, role: str = "employee") -> WorkspaceUser:
    return WorkspaceUser({
        "id": employee_id,
        "company_id": COMPANY_ID,
        "role": role,
        "full_name": f"Person {employee_id}",
        "phone": "+998900000000",
    })


AZIZ = _user(AZIZ_ID)
BEK = _user(BEK_ID)

CARDS = {
    AZIZ_ID: {"id": AZIZ_ID, "full_name": "Aziz", "fcm_token": "tok-a", "company_id": COMPANY_ID},
    BEK_ID: {"id": BEK_ID, "full_name": "Bek", "fcm_token": "tok-b", "company_id": COMPANY_ID},
    DILNOZA_ID: {"id": DILNOZA_ID, "full_name": "Dilnoza", "fcm_token": None, "company_id": COMPANY_ID},
}


def _conference(**overrides):
    row = {
        "id": CONFERENCE_ID,
        "company_id": COMPANY_ID,
        "room_name": "weel-conf-abc",
        "title": "Haftalik yig’ilish",
        "thread_id": THREAD_ID,
        "message_id": 900,
        "scope": ConferenceScope.ALL,
        "created_by": AZIZ_ID,
        "status": ConferenceStatus.LIVE,
        "started_at": timezone.now(),
        "ended_at": None,
    }
    row.update(overrides)
    return row


@pytest.fixture
def mocks():
    with override_settings(**LIVEKIT), patch(
        "apps.b2b.workspace.conferences.conf_repo"
    ) as conf_repo, patch("apps.b2b.workspace.conferences.repo") as repo, patch(
        "apps.b2b.workspace.conferences.realtime"
    ) as realtime, patch(
        "apps.b2b.workspace.calls_repository.employee_cards"
    ) as cards, patch(
        "apps.b2b.workspace.tasks.notify_conference_invite"
    ) as push, patch("apps.b2b.workspace.views.add_to_thread") as subscribe, patch(
        "apps.b2b.workspace.views._message_payload"
    ) as message_payload:
        conf_repo.ConferenceScope = ConferenceScope
        conf_repo.ConferenceStatus = ConferenceStatus
        conf_repo.company_employee_ids.return_value = [AZIZ_ID, BEK_ID, DILNOZA_ID]
        conf_repo.employee_ids_in_departments.return_value = [BEK_ID, DILNOZA_ID]
        conf_repo.create_conference.side_effect = lambda **kw: _conference(
            room_name=kw["room_name"], title=kw["title"], scope=kw["scope"], message_id=None
        )
        conf_repo.finish.side_effect = lambda cid, **kw: _conference(
            status=ConferenceStatus.ENDED, ended_at=timezone.now()
        )
        repo.create_thread.return_value = {"id": THREAD_ID, "group_name": "Haftalik yig’ilish"}
        repo.send_message.return_value = {"id": 900, "thread_id": THREAD_ID}
        repo.edit_message.return_value = {"id": 900, "thread_id": THREAD_ID}
        repo.employee_ids_in_company.side_effect = lambda company_id, ids: list(ids)
        repo.is_thread_member.return_value = True
        repo.thread_member_ids.return_value = [AZIZ_ID, BEK_ID, DILNOZA_ID]
        cards.return_value = CARDS
        message_payload.return_value = {"id": 900}
        yield {
            "conf_repo": conf_repo,
            "repo": repo,
            "realtime": realtime,
            "push": push,
            "subscribe": subscribe,
        }


# ─── Who a conference reaches ─────────────────────────────────────────────────


class TestScope:
    def test_everyone_is_the_whole_roster(self, mocks):
        members = conferences.resolve_members(
            company_id=COMPANY_ID,
            scope=ConferenceScope.ALL,
            department_ids=None,
            employee_ids=None,
        )
        assert members == [AZIZ_ID, BEK_ID, DILNOZA_ID]

    def test_departments_resolve_to_their_people(self, mocks):
        members = conferences.resolve_members(
            company_id=COMPANY_ID,
            scope=ConferenceScope.DEPARTMENTS,
            department_ids=[3],
            employee_ids=None,
        )
        assert members == [BEK_ID, DILNOZA_ID]
        mocks["conf_repo"].employee_ids_in_departments.assert_called_once_with(COMPANY_ID, [3])

    def test_departments_without_a_department_is_refused(self, mocks):
        with pytest.raises(CallError) as caught:
            conferences.resolve_members(
                company_id=COMPANY_ID,
                scope=ConferenceScope.DEPARTMENTS,
                department_ids=[],
                employee_ids=None,
            )
        assert caught.value.status == 400

    def test_an_emptied_department_is_refused_rather_than_a_conference_of_one(self, mocks):
        mocks["conf_repo"].employee_ids_in_departments.return_value = []
        with pytest.raises(CallError) as caught:
            conferences.resolve_members(
                company_id=COMPANY_ID,
                scope=ConferenceScope.DEPARTMENTS,
                department_ids=[3],
                employee_ids=None,
            )
        assert caught.value.status == 400

    def test_a_foreign_employee_is_refused(self, mocks):
        mocks["repo"].employee_ids_in_company.side_effect = lambda company_id, ids: [BEK_ID]
        with pytest.raises(CallError) as caught:
            conferences.resolve_members(
                company_id=COMPANY_ID,
                scope=ConferenceScope.EMPLOYEES,
                department_ids=None,
                employee_ids=[BEK_ID, 999],
            )
        assert caught.value.status == 400


# ─── Opening one ──────────────────────────────────────────────────────────────


class TestCreate:
    def test_opens_a_group_a_room_and_a_card(self, mocks):
        payload = conferences.create(
            AZIZ, title="Haftalik yig’ilish", scope=ConferenceScope.ALL
        )

        # The group holds everybody but its creator, who is added by
        # `create_thread` itself.
        _, kwargs = mocks["repo"].create_thread.call_args
        assert kwargs["created_by"] == AZIZ_ID
        assert sorted(kwargs["member_ids"]) == [BEK_ID, DILNOZA_ID]
        assert kwargs["group_name"] == "Haftalik yig’ilish"

        # The room is unguessable and belongs to this conference alone.
        assert payload["room_name"].startswith("weel-")
        assert payload["thread_id"] == THREAD_ID
        assert payload["status"] == ConferenceStatus.LIVE

        # The card is the first message in it.
        thread_id, sender_id, text = mocks["repo"].send_message.call_args[0]
        assert thread_id == THREAD_ID
        assert sender_id == AZIZ_ID
        assert text.startswith("\U0001F4F9 Haftalik yig’ilish")
        assert f"{conferences.CONF_TAG} {CONFERENCE_ID} live" in text

    def test_the_organiser_is_handed_a_moderator_token_for_that_room(self, mocks):
        import jwt

        payload = conferences.create(AZIZ, title="Yig’ilish", scope=ConferenceScope.ALL)
        claims = jwt.decode(payload["token"], "lk-s3cret", algorithms=["HS256"])
        assert claims["video"]["room"] == payload["room_name"]
        assert claims["video"]["roomAdmin"] is True
        assert claims["sub"] == str(AZIZ_ID)

    def test_everybody_starts_listening_at_once(self, mocks):
        conferences.create(AZIZ, title="Yig’ilish", scope=ConferenceScope.ALL)
        subscribed, thread_id = mocks["subscribe"].call_args[0]
        assert thread_id == THREAD_ID
        assert sorted(subscribed) == sorted([AZIZ_ID, BEK_ID, DILNOZA_ID])

    def test_a_conference_of_one_is_refused(self, mocks):
        mocks["conf_repo"].company_employee_ids.return_value = [AZIZ_ID]
        with pytest.raises(CallError) as caught:
            conferences.create(AZIZ, title="Yolg’iz", scope=ConferenceScope.ALL)
        assert caught.value.status == 400

    def test_an_untitled_conference_is_still_named(self, mocks):
        payload = conferences.create(AZIZ, title="  ", scope=ConferenceScope.ALL)
        assert payload["title"] == "Konferensiya"

    def test_refuses_when_no_media_server_is_configured(self, mocks):
        with override_settings(**UNCONFIGURED):
            with pytest.raises(CallError) as caught:
                conferences.create(AZIZ, title="Yig’ilish", scope=ConferenceScope.ALL)
        assert caught.value.status == 503


# ─── Joining ──────────────────────────────────────────────────────────────────


class TestJoin:
    def test_a_member_of_the_group_gets_their_own_token(self, mocks):
        import jwt

        payload = conferences.join(_conference(), BEK)
        claims = jwt.decode(payload["token"], "lk-s3cret", algorithms=["HS256"])
        assert claims["sub"] == str(BEK_ID)
        assert claims["video"]["room"] == "weel-conf-abc"
        # Only the organiser runs the room.
        assert claims["video"]["roomAdmin"] is False

    def test_somebody_outside_the_group_is_refused(self, mocks):
        mocks["repo"].is_thread_member.return_value = False
        with pytest.raises(CallError) as caught:
            conferences.join(_conference(), BEK)
        assert caught.value.status == 403

    def test_a_finished_conference_cannot_be_joined(self, mocks):
        with pytest.raises(CallError) as caught:
            conferences.join(_conference(status=ConferenceStatus.ENDED), BEK)
        assert caught.value.status == 409

    def test_a_token_is_never_broadcast(self, mocks):
        conferences.create(AZIZ, title="Yig’ilish", scope=ConferenceScope.ALL)
        _, kwargs = mocks["realtime"].publish_employees.call_args
        assert kwargs["conference"]["token"] is None


# ─── Ending ───────────────────────────────────────────────────────────────────


class TestEnd:
    def test_only_the_organiser_may_end_it(self, mocks):
        with pytest.raises(CallError) as caught:
            conferences.end(_conference(), BEK)
        assert caught.value.status == 403

    def test_ending_rewrites_the_card_in_place(self, mocks):
        conferences.end(_conference(), AZIZ)
        message_id, text = mocks["repo"].edit_message.call_args[0]
        assert message_id == 900
        assert f"{conferences.CONF_TAG} {CONFERENCE_ID} ended" in text
        # One card, not a second message under the first.
        mocks["repo"].send_message.assert_not_called()

    def test_ending_tells_the_whole_room(self, mocks):
        conferences.end(_conference(), AZIZ)
        args, kwargs = mocks["realtime"].publish_employees.call_args
        assert sorted(args[0]) == sorted([AZIZ_ID, BEK_ID, DILNOZA_ID])
        assert kwargs["action"] == "ended"

    def test_a_second_end_changes_nothing(self, mocks):
        mocks["conf_repo"].finish.side_effect = None
        mocks["conf_repo"].finish.return_value = None
        assert conferences.end(_conference(), AZIZ) is None
        mocks["repo"].edit_message.assert_not_called()


# ─── Rooms nobody closed ──────────────────────────────────────────────────────


class TestStale:
    def test_settle_closes_one_past_the_window(self, mocks):
        old = _conference(started_at=timezone.now() - timedelta(hours=5))
        settled = conferences.settle(old)
        assert settled["status"] == ConferenceStatus.ENDED
        mocks["conf_repo"].finish.assert_called_once()

    def test_settle_leaves_one_inside_the_window_alone(self, mocks):
        fresh = _conference(started_at=timezone.now() - timedelta(minutes=5))
        assert conferences.settle(fresh) is fresh
        mocks["conf_repo"].finish.assert_not_called()

    def test_the_sweep_closes_every_forgotten_room(self, mocks):
        mocks["conf_repo"].stale_live.return_value = [_conference(), _conference(id=301)]
        assert conferences.end_stale() == 2


# ─── The endpoints ────────────────────────────────────────────────────────────


class _Capable(WorkspaceUser):
    """A signed-in employee whose capabilities are decided by the test rather
    than by the access catalogue, which needs a database."""

    def __init__(self, employee_id: int, *, can_create_group: bool):
        super().__init__({
            "id": employee_id,
            "company_id": COMPANY_ID,
            "role": "employee",
            "full_name": f"Person {employee_id}",
        })
        self._can_create_group = can_create_group

    @property
    def capabilities(self):
        return {"can_create_group_chat": self._can_create_group}


class TestViews:
    def _post(self, view_class, user, path="/api/b2b/workspace/conferences/", body=None, **kwargs):
        """The view with its module gate stood down.

        `HasModule` answers from the access catalogue, which is a database —
        and what these tests are about is the capability check inside the
        view, not the gate in front of it. Every other rule the view applies
        is left in place.
        """
        request = factory.post(path, body or {}, format="json")
        force_authenticate(request, user=user)
        with patch.object(view_class, "required_module", None):
            return view_class.as_view()(request, **kwargs)

    def test_a_role_that_cannot_open_a_group_cannot_open_a_conference(self, mocks):
        response = self._post(
            WorkspaceConferenceListCreateView,
            _Capable(AZIZ_ID, can_create_group=False),
            body={"scope": ConferenceScope.ALL, "title": "Yig’ilish"},
        )
        assert response.status_code == 403
        mocks["repo"].create_thread.assert_not_called()

    def test_a_role_that_can_opens_one(self, mocks):
        response = self._post(
            WorkspaceConferenceListCreateView,
            _Capable(AZIZ_ID, can_create_group=True),
            body={"scope": ConferenceScope.ALL, "title": "Yig’ilish"},
        )
        assert response.status_code == 201
        assert response.data["thread_id"] == THREAD_ID

    def test_an_unknown_scope_is_a_400(self, mocks):
        response = self._post(
            WorkspaceConferenceListCreateView,
            _Capable(AZIZ_ID, can_create_group=True),
            body={"scope": "everybody-ish"},
        )
        assert response.status_code == 400

    def test_join_answers_404_for_a_conference_of_another_company(self, mocks):
        mocks["conf_repo"].get_conference.return_value = None
        with patch(
            "apps.b2b.workspace.conferences_views.conf_repo", mocks["conf_repo"]
        ):
            response = self._post(
                WorkspaceConferenceJoinView,
                _Capable(BEK_ID, can_create_group=False),
                path=f"/api/b2b/workspace/conferences/{CONFERENCE_ID}/join/",
                conference_id=CONFERENCE_ID,
            )
        assert response.status_code == 404

    def test_end_by_somebody_else_is_403(self, mocks):
        mocks["conf_repo"].get_conference.return_value = _conference()
        with patch(
            "apps.b2b.workspace.conferences_views.conf_repo", mocks["conf_repo"]
        ):
            response = self._post(
                WorkspaceConferenceEndView,
                _Capable(BEK_ID, can_create_group=True),
                path=f"/api/b2b/workspace/conferences/{CONFERENCE_ID}/end/",
                conference_id=CONFERENCE_ID,
            )
        assert response.status_code == 403
