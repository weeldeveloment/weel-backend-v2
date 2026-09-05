"""Live calls (Jitsi) — the rules in `calls.py`, against a mocked repository.

What has to hold: a token opens one room and expires; a call rings the right
person and nobody else; the state machine cannot be pushed twice; a ring
nobody answers becomes a missed call with a line in the chat; and the
endpoints refuse cleanly when Jitsi is not configured.
"""
from datetime import timedelta
from unittest.mock import patch

import jwt
import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.workspace import calls
from apps.b2b.workspace import push_text
from apps.b2b.workspace.authentication import WorkspaceUser
from apps.b2b.workspace.calls_repository import CallStatus
from apps.b2b.workspace.calls_views import (
    WorkspaceCallAcceptView,
    WorkspaceCallIncomingView,
    WorkspaceCallListCreateView,
)

COMPANY_ID = 55
AZIZ_ID = 7
BEK_ID = 9

factory = APIRequestFactory()

JITSI = dict(
    JITSI_SERVER_URL="https://call.weel.uz",
    JITSI_APP_ID="weel",
    JITSI_APP_SECRET="s3cret",
    JITSI_JWT_SUB="*",
    JITSI_TOKEN_TTL_SECONDS=7200,
    JITSI_GUEST_LINK_TTL_SECONDS=1800,
    CALL_RING_TIMEOUT_SECONDS=30,
    CELERY_TASK_ALWAYS_EAGER=True,
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


def _call(**overrides):
    row = {
        "id": 100,
        "company_id": COMPANY_ID,
        "room_name": "weel-abc",
        "type": "video",
        "source_module": "chat",
        "initiator_id": AZIZ_ID,
        "target_employee_id": BEK_ID,
        "target_lead_id": None,
        "target_customer_id": None,
        "thread_id": 3,
        "status": CallStatus.RINGING,
        "started_at": timezone.now(),
        "answered_at": None,
        "ended_at": None,
        "duration_seconds": None,
        "guest_link_sent_at": None,
    }
    row.update(overrides)
    return row


CARDS = {
    AZIZ_ID: {"id": AZIZ_ID, "full_name": "Aziz", "photo": None, "fcm_token": "tok-a", "company_id": COMPANY_ID},
    BEK_ID: {"id": BEK_ID, "full_name": "Bek", "photo": None, "fcm_token": "tok-b", "company_id": COMPANY_ID},
}


@pytest.fixture
def mocks():
    with override_settings(**JITSI), patch("apps.b2b.workspace.calls.calls_repo") as calls_repo, patch(
        "apps.b2b.workspace.calls.repo"
    ) as repo, patch("apps.b2b.workspace.calls.realtime") as realtime, patch(
        "apps.b2b.workspace.tasks.notify_incoming_call"
    ) as ring_push, patch("apps.b2b.workspace.tasks.notify_missed_call") as missed_push, patch(
        "apps.b2b.workspace.tasks.send_call_guest_link"
    ) as sms:
        calls_repo.CallStatus = CallStatus
        calls_repo.employee_cards.return_value = CARDS
        calls_repo.live_call_for.return_value = None
        yield {
            "calls_repo": calls_repo,
            "repo": repo,
            "realtime": realtime,
            "ring_push": ring_push,
            "missed_push": missed_push,
            "sms": sms,
        }


# ─── Tokens ───────────────────────────────────────────────────────────────────


class TestToken:
    def test_names_one_room_and_the_person(self):
        with override_settings(**JITSI):
            token, expires = calls.sign_token(room="weel-abc", user_id=7, name="Aziz", moderator=True)
        claims = jwt.decode(token, "s3cret", algorithms=["HS256"], audience="jitsi")
        assert claims["room"] == "weel-abc"
        assert claims["iss"] == "weel"
        assert claims["context"]["user"]["name"] == "Aziz"
        assert claims["context"]["user"]["moderator"] == "true"
        assert claims["context"]["features"]["recording"] == "false"
        assert (expires - timezone.now()) > timedelta(hours=1, minutes=55)

    def test_a_guest_link_carries_a_short_token(self):
        with override_settings(**JITSI):
            link = calls.guest_link(_call(), "Mijoz")
        assert link.startswith("https://call.weel.uz/weel-abc?jwt=")
        token = link.split("jwt=", 1)[1]
        claims = jwt.decode(token, "s3cret", algorithms=["HS256"], audience="jitsi")
        assert claims["exp"] - claims["iat"] == 1800
        assert claims["context"]["user"]["id"] == "guest-100"

    def test_room_names_are_unguessable_and_distinct(self):
        names = {calls.new_room_name() for _ in range(50)}
        assert len(names) == 50
        assert all(len(n) > 30 and n.startswith("weel-") for n in names)


# ─── Starting ─────────────────────────────────────────────────────────────────


class TestStart:
    def test_rings_the_other_member_of_a_direct_thread(self, mocks):
        mocks["repo"].get_thread_for_member.return_value = {
            "id": 3, "group_name": None, "participant_ids": [BEK_ID],
        }
        mocks["repo"].get_workspace_employee.return_value = {"id": BEK_ID, "company_id": COMPANY_ID}
        mocks["calls_repo"].create_call.return_value = _call()

        result = calls.start(user=AZIZ, call_type="video", source_module="chat", thread_id=3)

        assert result["token"]
        assert result["status"] == "ringing"
        assert result["server_url"] == "https://call.weel.uz"
        kwargs = mocks["calls_repo"].create_call.call_args.kwargs
        assert kwargs["target_employee_id"] == BEK_ID and kwargs["thread_id"] == 3
        # Told two ways: the socket frame to both, the push to the callee.
        publish = mocks["realtime"].publish_employees
        assert publish.call_args.kwargs["action"] == "ringing"
        assert set(publish.call_args.args[0]) == {AZIZ_ID, BEK_ID}
        assert "token" in publish.call_args.kwargs["call"]
        assert publish.call_args.kwargs["call"]["token"] is None
        push = mocks["ring_push"].delay
        assert push.call_args.args[:3] == (100, COMPANY_ID, "tok-b")

    def test_refuses_a_group_thread(self, mocks):
        mocks["repo"].get_thread_for_member.return_value = {
            "id": 3, "group_name": "Sotuv", "participant_ids": [BEK_ID, 11],
        }
        with pytest.raises(calls.CallError) as refused:
            calls.start(user=AZIZ, call_type="video", source_module="chat", thread_id=3)
        assert refused.value.status == 400
        mocks["calls_repo"].create_call.assert_not_called()

    def test_opens_a_direct_thread_when_only_a_person_is_given(self, mocks):
        mocks["repo"].get_workspace_employee.return_value = {"id": BEK_ID, "company_id": COMPANY_ID}
        mocks["repo"].find_direct_thread.return_value = None
        mocks["repo"].create_thread.return_value = {"id": 44}
        mocks["calls_repo"].create_call.return_value = _call(thread_id=44)

        with patch("apps.b2b.workspace.consumers.add_to_thread"):
            calls.start(user=AZIZ, call_type="audio", source_module="chat", target_employee_id=BEK_ID)

        assert mocks["calls_repo"].create_call.call_args.kwargs["thread_id"] == 44

    def test_a_busy_colleague_is_not_rung(self, mocks):
        mocks["repo"].get_workspace_employee.return_value = {"id": BEK_ID, "company_id": COMPANY_ID}
        mocks["repo"].find_direct_thread.return_value = {"id": 3}
        mocks["calls_repo"].live_call_for.side_effect = lambda eid: (
            _call(id=77, status=CallStatus.ACCEPTED) if eid == BEK_ID else None
        )
        with pytest.raises(calls.CallError) as refused:
            calls.start(user=AZIZ, call_type="video", source_module="chat", target_employee_id=BEK_ID)
        assert refused.value.status == 409
        mocks["calls_repo"].create_call.assert_not_called()

    def test_a_stale_ring_does_not_keep_somebody_busy(self, mocks):
        """A ring the worker never settled must not make a colleague 'band'
        for the rest of the day."""
        stale = _call(id=77, started_at=timezone.now() - timedelta(minutes=5))
        mocks["repo"].get_workspace_employee.return_value = {"id": BEK_ID, "company_id": COMPANY_ID}
        mocks["repo"].find_direct_thread.return_value = {"id": 3}
        mocks["calls_repo"].live_call_for.side_effect = lambda eid: stale if eid == BEK_ID else None
        mocks["calls_repo"].transition.return_value = dict(stale, status=CallStatus.MISSED)
        mocks["calls_repo"].create_call.return_value = _call()

        calls.start(user=AZIZ, call_type="video", source_module="chat", target_employee_id=BEK_ID)

        assert mocks["calls_repo"].transition.call_args.kwargs["to"] == CallStatus.MISSED
        mocks["calls_repo"].create_call.assert_called_once()

    def test_my_own_unclosed_call_does_not_make_me_busy_to_myself(self, mocks):
        """The bug that shipped: hang up, `/end` is lost, and every call you
        try to place afterwards answers "band" — for ever, because an
        answered call had no timeout at all.

        The phone will not place a call while it believes one is running, so a
        live row for the *caller* is a row their phone has already forgotten.
        It is closed here rather than refused.
        """
        mine = _call(
            id=88,
            status=CallStatus.ACCEPTED,
            started_at=timezone.now() - timedelta(minutes=4),
            answered_at=timezone.now() - timedelta(minutes=3),
        )
        mocks["repo"].get_workspace_employee.return_value = {"id": BEK_ID, "company_id": COMPANY_ID}
        mocks["repo"].find_direct_thread.return_value = {"id": 3}
        mocks["calls_repo"].live_call_for.side_effect = (
            lambda eid: mine if eid == AZIZ_ID else None
        )
        mocks["calls_repo"].transition.return_value = dict(mine, status=CallStatus.ENDED)
        mocks["calls_repo"].create_call.return_value = _call()

        calls.start(user=AZIZ, call_type="video", source_module="chat", target_employee_id=BEK_ID)

        assert mocks["calls_repo"].transition.call_args.kwargs["to"] == CallStatus.ENDED
        mocks["calls_repo"].create_call.assert_called_once()

    def test_the_other_side_is_still_refused_while_they_are_talking(self, mocks):
        """Releasing my own line must not release anybody else's — theirs may
        be a real conversation with a third person."""
        theirs = _call(
            id=91,
            initiator_id=BEK_ID,
            target_employee_id=404,
            status=CallStatus.ACCEPTED,
            answered_at=timezone.now() - timedelta(minutes=1),
        )
        mocks["repo"].get_workspace_employee.return_value = {"id": BEK_ID, "company_id": COMPANY_ID}
        mocks["repo"].find_direct_thread.return_value = {"id": 3}
        mocks["calls_repo"].live_call_for.side_effect = (
            lambda eid: theirs if eid == BEK_ID else None
        )

        with pytest.raises(calls.CallError) as raised:
            calls.start(
                user=AZIZ, call_type="video", source_module="chat", target_employee_id=BEK_ID
            )

        assert raised.value.status == 409
        mocks["calls_repo"].create_call.assert_not_called()

    def test_a_lead_gets_a_browser_link_by_sms(self, mocks):
        mocks["repo"].get_lead.return_value = {
            "id": 5, "contact_full_name": "Mijoz", "contact_phone": "+998901112233",
        }
        mocks["calls_repo"].create_call.return_value = _call(
            target_employee_id=None, thread_id=None, target_lead_id=5, source_module="sales"
        )
        result = calls.start(user=AZIZ, call_type="video", source_module="sales", lead_id=5)

        assert result["guest_link"].startswith("https://call.weel.uz/weel-abc?jwt=")
        sms = mocks["sms"].delay
        assert sms.call_args.args[2] == "+998901112233"
        mocks["ring_push"].delay.assert_not_called()

    def test_refuses_when_jitsi_is_not_configured(self, mocks):
        with override_settings(JITSI_APP_SECRET=""):
            with pytest.raises(calls.CallError) as refused:
                calls.start(user=AZIZ, call_type="video", source_module="chat", thread_id=3)
        assert refused.value.status == 503


# ─── Answering, refusing, hanging up ──────────────────────────────────────────


class TestAbandonedCalls:
    """An answered call whose `/end` never arrived."""

    def test_settle_closes_a_conversation_past_the_maximum_duration(self, mocks):
        forgotten = _call(
            status=CallStatus.ACCEPTED,
            answered_at=timezone.now() - timedelta(hours=9),
        )
        mocks["calls_repo"].transition.return_value = dict(
            forgotten, status=CallStatus.ENDED
        )

        settled = calls.settle(forgotten)

        assert settled["status"] == CallStatus.ENDED
        kwargs = mocks["calls_repo"].transition.call_args.kwargs
        assert kwargs["to"] == CallStatus.ENDED
        assert kwargs["only_from"] == [CallStatus.ACCEPTED]
        # Counted, not thrown away: the chat line should read the length it had.
        assert kwargs["duration_seconds"] > 0

    def test_settle_leaves_a_conversation_inside_the_window_alone(self, mocks):
        live = _call(
            status=CallStatus.ACCEPTED,
            answered_at=timezone.now() - timedelta(minutes=20),
        )

        assert calls.settle(live)["status"] == CallStatus.ACCEPTED
        mocks["calls_repo"].transition.assert_not_called()

    def test_the_sweep_covers_both_kinds_of_stale_row(self, mocks):
        mocks["calls_repo"].stale_ringing.return_value = [_call(id=1)]
        mocks["calls_repo"].stale_accepted.return_value = [
            _call(id=2, status=CallStatus.ACCEPTED,
                  answered_at=timezone.now() - timedelta(hours=9))
        ]
        mocks["calls_repo"].transition.side_effect = lambda cid, **kw: _call(
            id=cid, status=kw["to"]
        )

        assert calls.expire_stale() == 2
        mocks["calls_repo"].stale_accepted.assert_called_once()


class TestTransitions:
    def test_accept_hands_the_callee_a_token_and_tells_the_caller(self, mocks):
        mocks["calls_repo"].transition.return_value = _call(status=CallStatus.ACCEPTED, answered_at=timezone.now())
        result = calls.accept(_call(), BEK)
        assert result["status"] == "accepted" and result["token"]
        assert mocks["calls_repo"].transition.call_args.kwargs["only_from"] == [CallStatus.RINGING]
        assert mocks["realtime"].publish_employees.call_args.kwargs["action"] == "accepted"

    def test_only_the_person_rung_may_accept(self, mocks):
        with pytest.raises(calls.CallError) as refused:
            calls.accept(_call(), AZIZ)
        assert refused.value.status == 403

    def test_accepting_a_settled_call_is_refused_not_duplicated(self, mocks):
        mocks["calls_repo"].transition.return_value = None
        mocks["calls_repo"].get_call.return_value = _call(status=CallStatus.CANCELLED)
        with pytest.raises(calls.CallError) as refused:
            calls.accept(_call(), BEK)
        assert refused.value.status == 409

    def test_hanging_up_while_ringing_is_a_cancel_and_a_missed_call_for_the_other(self, mocks):
        mocks["calls_repo"].transition.return_value = _call(status=CallStatus.CANCELLED)
        mocks["repo"].send_message.return_value = {"id": 500, "thread_id": 3, "sender_id": AZIZ_ID, "text": "x"}
        with patch("apps.b2b.workspace.views._message_payload", return_value={"id": 500}):
            result = calls.end(_call(), AZIZ)
        assert result["status"] == "cancelled"
        assert mocks["calls_repo"].transition.call_args.kwargs["to"] == CallStatus.CANCELLED
        mocks["missed_push"].delay.assert_called_once()
        text = mocks["repo"].send_message.call_args.args[2]
        assert text.splitlines()[-1] == "#call video cancelled 0"

    def test_hanging_up_an_answered_call_counts_the_duration(self, mocks):
        answered = timezone.now() - timedelta(minutes=4, seconds=12)
        mocks["calls_repo"].transition.return_value = _call(
            status=CallStatus.ENDED, answered_at=answered, duration_seconds=252
        )
        mocks["repo"].send_message.return_value = {"id": 501, "thread_id": 3, "sender_id": AZIZ_ID, "text": "x"}
        with patch("apps.b2b.workspace.views._message_payload", return_value={"id": 501}):
            calls.end(_call(status=CallStatus.ACCEPTED, answered_at=answered), BEK)
        kwargs = mocks["calls_repo"].transition.call_args.kwargs
        assert kwargs["to"] == CallStatus.ENDED and 251 <= kwargs["duration_seconds"] <= 253
        text = mocks["repo"].send_message.call_args.args[2]
        assert text.startswith("\U0001F4DE Video qo’ng’iroq · 4:12")
        assert text.splitlines()[-1] == "#call video ended 252"
        mocks["realtime"].broadcast_message.assert_called_once()
        mocks["missed_push"].delay.assert_not_called()

    def test_a_stranger_may_not_hang_up(self, mocks):
        with pytest.raises(calls.CallError) as refused:
            calls.end(_call(), _user(42))
        assert refused.value.status == 403


# ─── The ring timeout ─────────────────────────────────────────────────────────


class TestTimeout:
    def test_a_ring_past_the_window_becomes_missed_on_read(self, mocks):
        stale = _call(started_at=timezone.now() - timedelta(seconds=45))
        mocks["calls_repo"].transition.return_value = dict(stale, status=CallStatus.MISSED)
        mocks["repo"].send_message.return_value = {"id": 502, "thread_id": 3, "sender_id": AZIZ_ID, "text": "x"}
        with patch("apps.b2b.workspace.views._message_payload", return_value={"id": 502}):
            settled = calls.settle(stale)
        assert settled["status"] == CallStatus.MISSED
        assert mocks["realtime"].publish_employees.call_args.kwargs["action"] == "missed"
        mocks["missed_push"].delay.assert_called_once()

    def test_a_ring_inside_the_window_is_left_alone(self, mocks):
        fresh = _call(started_at=timezone.now() - timedelta(seconds=10))
        assert calls.settle(fresh) is fresh
        mocks["calls_repo"].transition.assert_not_called()

    def test_accepting_after_the_window_is_refused(self, mocks):
        stale = _call(started_at=timezone.now() - timedelta(seconds=45))
        mocks["calls_repo"].transition.return_value = dict(stale, status=CallStatus.MISSED)
        mocks["repo"].send_message.return_value = None
        with pytest.raises(calls.CallError) as refused:
            calls.accept(stale, BEK)
        assert refused.value.status == 409


# ─── The chat line ────────────────────────────────────────────────────────────


class TestCallLogText:
    @pytest.mark.parametrize(
        "status, seconds, label",
        [
            ("ended", 252, "Video qo’ng’iroq · 4:12"),
            ("ended", 3725, "Video qo’ng’iroq · 1:02:05"),
            ("missed", 0, "Javobsiz qo’ng’iroq"),
            ("cancelled", 0, "Javobsiz qo’ng’iroq"),
            ("declined", 0, "Video qo’ng’iroq · rad etildi"),
        ],
    )
    def test_human_line_then_machine_line(self, status, seconds, label):
        text = calls.call_log_text(_call(status=status, duration_seconds=seconds))
        human, machine = text.splitlines()
        assert human == f"\U0001F4DE {label}"
        assert machine == f"#call video {status} {seconds}"

    def test_audio_is_named_as_such(self):
        assert "Audio" in push_text.call_log_label("audio", "ended", 5)


# ─── The endpoints ────────────────────────────────────────────────────────────


class TestViews:
    def test_create_answers_503_when_unconfigured(self):
        request = factory.post("/calls/", {"thread_id": 3}, format="json")
        force_authenticate(request, user=AZIZ)
        with override_settings(**dict(JITSI, JITSI_APP_SECRET="")):
            response = WorkspaceCallListCreateView.as_view()(request)
        assert response.status_code == 503

    def test_create_returns_the_token(self, mocks):
        mocks["repo"].get_thread_for_member.return_value = {"id": 3, "group_name": None, "participant_ids": [BEK_ID]}
        mocks["repo"].get_workspace_employee.return_value = {"id": BEK_ID, "company_id": COMPANY_ID}
        mocks["calls_repo"].create_call.return_value = _call()
        request = factory.post("/calls/", {"thread_id": 3, "type": "audio"}, format="json")
        force_authenticate(request, user=AZIZ)
        response = WorkspaceCallListCreateView.as_view()(request)
        assert response.status_code == 201
        assert response.data["token"] and response.data["room_name"] == "weel-abc"

    def test_accept_by_a_stranger_is_404(self, mocks):
        mocks["calls_repo"].get_call.return_value = _call()
        with patch("apps.b2b.workspace.calls_views.calls_repo") as views_repo:
            views_repo.get_call.return_value = _call()
            request = factory.post("/calls/100/accept/")
            force_authenticate(request, user=_user(42))
            response = WorkspaceCallAcceptView.as_view()(request, call_id=100)
        assert response.status_code == 404

    def test_incoming_is_204_when_nothing_rings(self, mocks):
        with patch("apps.b2b.workspace.calls_views.calls_repo") as views_repo:
            views_repo.ringing_for.return_value = None
            request = factory.get("/calls/incoming/")
            force_authenticate(request, user=BEK)
            response = WorkspaceCallIncomingView.as_view()(request)
        assert response.status_code == 204

    def test_incoming_returns_the_ringing_call(self, mocks):
        with patch("apps.b2b.workspace.calls_views.calls_repo") as views_repo:
            views_repo.ringing_for.return_value = _call()
            views_repo.CallStatus = CallStatus
            request = factory.get("/calls/incoming/")
            force_authenticate(request, user=BEK)
            response = WorkspaceCallIncomingView.as_view()(request)
        assert response.status_code == 200
        assert response.data["id"] == 100 and response.data["token"] is None
