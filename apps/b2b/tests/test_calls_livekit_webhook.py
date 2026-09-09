"""The media server's word on a call — LiveKit's webhook into `calls.py`.

`/end` is the request most likely to be lost, and a row nobody closed used to
stay `accepted` until the next call or the four-hour sweep wrote it down with
whatever duration the clock had reached: the "5 daqiqa" in the history for a
conversation that lasted one. LiveKit sees the same hang-up as the phone
leaving the room, and says so with a signed webhook. What has to hold: only
LiveKit's own signature is believed; an employee leaving an answered call's
room ends it with its true duration and both sides are told; a guest leaving
does not; a room that is not a call's is nobody's business.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
from datetime import timedelta
from unittest.mock import patch

import jwt
import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from apps.b2b.workspace import calls
from apps.b2b.workspace.calls_repository import CallStatus
from apps.b2b.workspace.calls_views import WorkspaceCallLiveKitWebhookView

COMPANY_ID = 55
AZIZ_ID = 7
BEK_ID = 9
KEY = "weel"
SECRET = "lk-s3cret"

LIVEKIT = dict(
    LIVEKIT_URL="wss://call.weel.uz",
    LIVEKIT_API_KEY=KEY,
    LIVEKIT_API_SECRET=SECRET,
    LIVEKIT_TOKEN_TTL_SECONDS=7200,
    CALL_PROVIDER="livekit",
    CALL_GUEST_BASE_URL="https://business.weel.uz",
    CELERY_TASK_ALWAYS_EAGER=True,
)

factory = APIRequestFactory()


def _call(**overrides):
    row = {
        "id": 100,
        "company_id": COMPANY_ID,
        "room_name": "weel-abc",
        "type": "audio",
        "source_module": "chat",
        "initiator_id": AZIZ_ID,
        "target_employee_id": BEK_ID,
        "target_lead_id": None,
        "target_customer_id": None,
        "thread_id": 3,
        "status": CallStatus.ACCEPTED,
        "started_at": timezone.now() - timedelta(seconds=130),
        "answered_at": timezone.now() - timedelta(seconds=120),
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


def _event(kind: str, room: str = "weel-abc", identity: str | None = None) -> dict:
    event = {"event": kind, "id": "evt-1", "createdAt": int(time.time()), "room": {"name": room, "sid": "RM_1"}}
    if identity is not None:
        event["participant"] = {"identity": identity, "sid": "PA_1"}
    return event


def _body(event: dict) -> bytes:
    return json.dumps(event).encode()


def _signed(body: bytes, *, key: str = KEY, secret: str = SECRET, digest_of: bytes | None = None) -> str:
    digest = base64.b64encode(hashlib.sha256(digest_of if digest_of is not None else body).digest()).decode()
    return jwt.encode(
        {"iss": key, "sha256": digest, "iat": int(time.time()), "exp": int(time.time()) + 300},
        secret,
        algorithm="HS256",
    )


@pytest.fixture
def mocks():
    with override_settings(**LIVEKIT), patch("apps.b2b.workspace.calls.calls_repo") as calls_repo, patch(
        "apps.b2b.workspace.calls.repo"
    ) as repo, patch("apps.b2b.workspace.calls.realtime") as realtime, patch(
        "apps.b2b.workspace.tasks.notify_missed_call"
    ) as missed_push:
        calls_repo.CallStatus = CallStatus
        calls_repo.employee_cards.return_value = CARDS
        yield {"calls_repo": calls_repo, "repo": repo, "realtime": realtime, "missed_push": missed_push}


# ─── The signature ────────────────────────────────────────────────────────────


class TestSignature:
    def test_livekits_own_signature_is_believed(self):
        body = _body(_event("participant_left", identity="9"))
        with override_settings(**LIVEKIT):
            event = calls.verify_livekit_webhook(body, _signed(body))
        assert event["event"] == "participant_left"

    def test_a_bearer_prefix_is_tolerated(self):
        body = _body(_event("room_finished"))
        with override_settings(**LIVEKIT):
            assert calls.verify_livekit_webhook(body, "Bearer " + _signed(body)) is not None

    def test_another_key_or_secret_is_not(self):
        body = _body(_event("room_finished"))
        with override_settings(**LIVEKIT):
            assert calls.verify_livekit_webhook(body, _signed(body, secret="wrong")) is None
            assert calls.verify_livekit_webhook(body, _signed(body, key="somebody")) is None

    def test_a_body_that_was_touched_is_not(self):
        original = _body(_event("participant_left", identity="9"))
        tampered = _body(_event("participant_left", identity="7"))
        with override_settings(**LIVEKIT):
            assert calls.verify_livekit_webhook(tampered, _signed(original)) is None

    def test_nothing_without_a_header_or_a_configured_server(self):
        body = _body(_event("room_finished"))
        with override_settings(**LIVEKIT):
            assert calls.verify_livekit_webhook(body, None) is None
            assert calls.verify_livekit_webhook(body, "") is None
        with override_settings(**dict(LIVEKIT, LIVEKIT_API_SECRET="")):
            assert calls.verify_livekit_webhook(body, _signed(body)) is None


# ─── What the events do ───────────────────────────────────────────────────────


class TestParticipantLeft:
    def test_an_employee_leaving_an_answered_call_ends_it_with_its_duration(self, mocks):
        row = _call()
        mocks["calls_repo"].get_call_by_room.return_value = row
        mocks["calls_repo"].transition.side_effect = lambda call_id, **kw: dict(
            row, status=kw["to"], ended_at=kw.get("ended_at"), duration_seconds=kw.get("duration_seconds"), ended_by=kw.get("ended_by")
        )

        result = calls.livekit_event(_event("participant_left", identity=str(BEK_ID)))

        assert result["status"] == CallStatus.ENDED
        kwargs = mocks["calls_repo"].transition.call_args.kwargs
        assert kwargs["to"] == CallStatus.ENDED
        assert kwargs["only_from"] == [CallStatus.ACCEPTED]
        assert kwargs["ended_by"] == BEK_ID
        # Counted from the answer, not from when anybody happened to look.
        assert 118 <= kwargs["duration_seconds"] <= 122
        # Both sides hear the same word.
        publish = mocks["realtime"].publish_employees
        assert publish.call_args.kwargs["action"] == "ended"
        assert sorted(publish.call_args.args[0]) == [AZIZ_ID, BEK_ID]
        # And the chat gets its line.
        mocks["repo"].send_message.assert_called_once()

    def test_the_caller_leaving_is_the_same_hang_up(self, mocks):
        row = _call()
        mocks["calls_repo"].get_call_by_room.return_value = row
        mocks["calls_repo"].transition.return_value = dict(row, status=CallStatus.ENDED)

        result = calls.livekit_event(_event("participant_left", identity=str(AZIZ_ID)))

        assert result["status"] == CallStatus.ENDED
        assert mocks["calls_repo"].transition.call_args.kwargs["ended_by"] == AZIZ_ID

    def test_a_guest_leaving_does_not_end_the_call(self, mocks):
        # The manager's phone waits for a customer who may have refreshed the
        # page, and hangs up itself if they do not come back.
        row = _call(target_employee_id=None, target_lead_id=5, source_module="sales", thread_id=None)
        mocks["calls_repo"].get_call_by_room.return_value = row

        assert calls.livekit_event(_event("participant_left", identity="guest-100")) is None
        mocks["calls_repo"].transition.assert_not_called()

    def test_somebody_the_row_does_not_name_is_ignored(self, mocks):
        mocks["calls_repo"].get_call_by_room.return_value = _call()
        assert calls.livekit_event(_event("participant_left", identity="42")) is None
        mocks["calls_repo"].transition.assert_not_called()

    def test_a_ringing_colleague_call_is_left_alone(self, mocks):
        # Nobody is in that room before the answer; the event is about an
        # earlier attempt, arriving late.
        mocks["calls_repo"].get_call_by_room.return_value = _call(status=CallStatus.RINGING, answered_at=None)
        assert calls.livekit_event(_event("participant_left", identity=str(AZIZ_ID))) is None
        mocks["calls_repo"].transition.assert_not_called()

    def test_a_settled_call_or_a_lost_race_changes_nothing(self, mocks):
        mocks["calls_repo"].get_call_by_room.return_value = _call(status=CallStatus.ENDED, duration_seconds=90)
        assert calls.livekit_event(_event("participant_left", identity=str(BEK_ID))) is None

        # The phone's `/end` landed a moment earlier: the conditional UPDATE
        # moves nothing, and the webhook reports nothing changed.
        row = _call()
        mocks["calls_repo"].get_call_by_room.return_value = row
        mocks["calls_repo"].transition.return_value = None
        mocks["calls_repo"].get_call.return_value = dict(row, status=CallStatus.ENDED)
        assert calls.livekit_event(_event("participant_left", identity=str(BEK_ID))) is None
        mocks["realtime"].publish_employees.assert_not_called()

    def test_a_room_that_is_not_a_calls_is_nobodys_business(self, mocks):
        assert calls.livekit_event(_event("participant_left", room="conf-12", identity="9")) is None
        assert calls.livekit_event(_event("participant_joined", identity="9")) is None
        mocks["calls_repo"].get_call_by_room.assert_not_called()

        mocks["calls_repo"].get_call_by_room.return_value = None
        assert calls.livekit_event(_event("participant_left", room="weel-nope", identity="9")) is None


class TestRoomFinished:
    def test_closes_an_answered_call_nobody_ended(self, mocks):
        row = _call()
        mocks["calls_repo"].get_call_by_room.return_value = row
        mocks["calls_repo"].transition.return_value = dict(row, status=CallStatus.ENDED, duration_seconds=120)

        result = calls.livekit_event(_event("room_finished"))

        assert result["status"] == CallStatus.ENDED
        kwargs = mocks["calls_repo"].transition.call_args.kwargs
        assert kwargs["to"] == CallStatus.ENDED
        # Nobody pressed anything.
        assert "ended_by" not in kwargs
        assert mocks["realtime"].publish_employees.call_args.kwargs["action"] == "ended"

    def test_a_guest_call_whose_room_closed_is_cancelled(self, mocks):
        row = _call(status=CallStatus.RINGING, answered_at=None, target_employee_id=None, target_lead_id=5, thread_id=None)
        mocks["calls_repo"].get_call_by_room.return_value = row
        mocks["calls_repo"].transition.return_value = dict(row, status=CallStatus.CANCELLED)

        result = calls.livekit_event(_event("room_finished"))

        assert result["status"] == CallStatus.CANCELLED
        assert mocks["calls_repo"].transition.call_args.kwargs["ended_by"] == AZIZ_ID

    def test_a_ringing_colleague_call_is_left_to_its_timeout(self, mocks):
        mocks["calls_repo"].get_call_by_room.return_value = _call(status=CallStatus.RINGING, answered_at=None)
        assert calls.livekit_event(_event("room_finished")) is None
        mocks["calls_repo"].transition.assert_not_called()


# ─── The endpoint ─────────────────────────────────────────────────────────────


class TestEndpoint:
    def _post(self, body: bytes, authorization: str | None):
        extra = {"HTTP_AUTHORIZATION": authorization} if authorization else {}
        request = factory.generic(
            "POST",
            "/api/b2b/workspace/calls/livekit-webhook/",
            data=body,
            content_type="application/webhook+json",
            **extra,
        )
        return WorkspaceCallLiveKitWebhookView.as_view()(request)

    def test_a_signed_event_is_handled_without_a_session(self, mocks):
        row = _call()
        mocks["calls_repo"].get_call_by_room.return_value = row
        mocks["calls_repo"].transition.return_value = dict(row, status=CallStatus.ENDED, duration_seconds=120)
        body = _body(_event("participant_left", identity=str(BEK_ID)))

        response = self._post(body, _signed(body))

        assert response.status_code == 200
        assert response.data == {"handled": True}

    def test_an_event_that_changes_nothing_is_still_a_200(self, mocks):
        mocks["calls_repo"].get_call_by_room.return_value = None
        body = _body(_event("participant_left", identity="9"))
        response = self._post(body, _signed(body))
        assert response.status_code == 200
        assert response.data == {"handled": False}

    def test_anything_unsigned_is_refused(self, mocks):
        body = _body(_event("participant_left", identity="9"))
        assert self._post(body, None).status_code == 401
        assert self._post(body, _signed(body, secret="wrong")).status_code == 401
        mocks["calls_repo"].get_call_by_room.assert_not_called()
