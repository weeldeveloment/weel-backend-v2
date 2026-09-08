"""The PushKit half of a ring: the token the phone registers, the push that
goes to Apple, and the ring choosing between the two roads."""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.test import override_settings

from apps.b2b.workspace import apns_voip


def _p8() -> tuple[str, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return private, public


@pytest.fixture(autouse=True)
def _fresh_client():
    apns_voip.use_client_for_tests(None)
    yield
    apns_voip.use_client_for_tests(None)


def test_unconfigured_by_default_and_never_sends():
    assert apns_voip.is_configured() is False
    assert apns_voip.send("abc", {"x": 1}, ttl_seconds=60) is False


def test_the_provider_token_is_signed_with_the_key_and_names_the_team():
    private, public = _p8()
    with override_settings(APNS_TEAM_ID="TEAM1", APNS_KEY_ID="KEY1", APNS_AUTH_KEY=private):
        token = apns_voip.provider_token(now=1_700_000_000)
    claims = jwt.decode(token, public, algorithms=["ES256"])
    assert claims == {"iss": "TEAM1", "iat": 1_700_000_000}
    assert jwt.get_unverified_header(token)["kid"] == "KEY1"


def test_a_one_line_env_key_is_unfolded():
    private, _ = _p8()
    folded = private.replace("\n", "\\n")
    with override_settings(APNS_TEAM_ID="T", APNS_KEY_ID="K", APNS_AUTH_KEY=folded):
        assert apns_voip._private_key() == private.strip()


def test_send_speaks_voip_to_the_right_gateway():
    private, _ = _p8()
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200)

    apns_voip.use_client_for_tests(httpx.Client(transport=httpx.MockTransport(handler)))
    with override_settings(
        APNS_TEAM_ID="T", APNS_KEY_ID="K", APNS_AUTH_KEY=private,
        APNS_VOIP_TOPIC="uz.weel.weelB2bV2.voip", APNS_USE_SANDBOX=True,
    ):
        ok = apns_voip.send("dev1", {"type": "call", "call_id": "7"}, ttl_seconds=65)
    assert ok is True
    assert seen["url"] == "https://api.sandbox.push.apple.com/3/device/dev1"
    assert seen["headers"]["apns-push-type"] == "voip"
    assert seen["headers"]["apns-topic"] == "uz.weel.weelB2bV2.voip"
    assert seen["headers"]["apns-priority"] == "10"
    assert seen["headers"]["authorization"].startswith("bearer ")
    assert seen["body"] == {"type": "call", "call_id": "7"}


def test_a_dead_token_is_reported_once_and_the_push_counts_as_not_sent():
    private, _ = _p8()
    apns_voip.use_client_for_tests(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(410, json={"reason": "Unregistered"})
            )
        )
    )
    dropped = []
    with override_settings(APNS_TEAM_ID="T", APNS_KEY_ID="K", APNS_AUTH_KEY=private):
        ok = apns_voip.send("dead", {}, ttl_seconds=60, on_dead_token=dropped.append)
    assert ok is False
    assert dropped == ["dead"]


def test_a_token_from_the_other_environment_is_retried_there_and_kept():
    """An Xcode build's token is sandbox, a TestFlight build's is production,
    and both are on real phones at once. APNs answers `BadDeviceToken` to the
    wrong one — which is not a dead token, and deleting it used to stop that
    iPhone ringing for good."""
    private, _ = _p8()
    seen = []

    def handler(request):
        seen.append(str(request.url.host))
        if "sandbox" in str(request.url.host):
            return httpx.Response(400, json={"reason": "BadDeviceToken"})
        return httpx.Response(200)

    apns_voip.use_client_for_tests(httpx.Client(transport=httpx.MockTransport(handler)))
    dropped = []
    with override_settings(
        APNS_TEAM_ID="T", APNS_KEY_ID="K", APNS_AUTH_KEY=private, APNS_USE_SANDBOX=True
    ):
        ok = apns_voip.send("prod", {}, ttl_seconds=60, on_dead_token=dropped.append)

    assert ok is True
    assert dropped == []
    assert seen == ["api.sandbox.push.apple.com", "api.push.apple.com"]


def test_a_token_neither_environment_knows_is_finally_dropped():
    private, _ = _p8()
    apns_voip.use_client_for_tests(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(400, json={"reason": "BadDeviceToken"})
            )
        )
    )
    dropped = []
    with override_settings(APNS_TEAM_ID="T", APNS_KEY_ID="K", APNS_AUTH_KEY=private):
        ok = apns_voip.send("junk", {}, ttl_seconds=60, on_dead_token=dropped.append)
    assert ok is False
    assert dropped == ["junk"]


def test_a_network_failure_is_not_sent_and_does_not_raise():
    private, _ = _p8()

    def handler(request):
        raise httpx.ConnectError("no route")

    apns_voip.use_client_for_tests(httpx.Client(transport=httpx.MockTransport(handler)))
    with override_settings(APNS_TEAM_ID="T", APNS_KEY_ID="K", APNS_AUTH_KEY=private):
        assert apns_voip.send("dev", {}, ttl_seconds=60) is False


class TestRingChoosesTheRoad:
    """`_ring` sends the PushKit push to a phone that has a VoIP token when
    Apple is configured, and the ordinary push otherwise."""

    CALL = {"id": 9, "company_id": 1, "type": "audio", "initiator_id": 1, "target_employee_id": 2, "thread_id": 5}

    def _cards(self, voip):
        return {
            1: {"id": 1, "full_name": "Aziz", "photo": None, "fcm_token": "f1", "voip_token": None},
            2: {"id": 2, "full_name": "Bekzod", "photo": None, "fcm_token": "f2", "voip_token": voip},
        }

    def test_voip_when_configured_and_the_phone_has_a_token(self):
        from apps.b2b.workspace import calls

        with patch("apps.b2b.workspace.calls.realtime"), patch(
            "apps.b2b.workspace.tasks.notify_incoming_call"
        ) as fcm, patch("apps.b2b.workspace.tasks.notify_incoming_call_voip") as voip, override_settings(
            APNS_TEAM_ID="T", APNS_KEY_ID="K", APNS_AUTH_KEY="k"
        ):
            calls._ring(self.CALL, self._cards("v2"))
        assert voip.delay.called and not fcm.delay.called
        args = voip.delay.call_args.args
        assert args[2] == "v2" and args[-1] == "f2"

    def test_fcm_when_apple_is_not_configured(self):
        from apps.b2b.workspace import calls

        with patch("apps.b2b.workspace.calls.realtime"), patch(
            "apps.b2b.workspace.tasks.notify_incoming_call"
        ) as fcm, patch("apps.b2b.workspace.tasks.notify_incoming_call_voip") as voip:
            calls._ring(self.CALL, self._cards("v2"))
        assert fcm.delay.called and not voip.delay.called
        assert fcm.delay.call_args.args[2] == "f2"

    def test_fcm_when_the_phone_has_no_voip_token(self):
        from apps.b2b.workspace import calls

        with patch("apps.b2b.workspace.calls.realtime"), patch(
            "apps.b2b.workspace.tasks.notify_incoming_call"
        ) as fcm, patch("apps.b2b.workspace.tasks.notify_incoming_call_voip") as voip, override_settings(
            APNS_TEAM_ID="T", APNS_KEY_ID="K", APNS_AUTH_KEY="k"
        ):
            calls._ring(self.CALL, self._cards(None))
        assert fcm.delay.called and not voip.delay.called


class TestTheRingIsTakenDown:
    """The caller giving up has to reach the phone that is ringing.

    Android reads the `action: missed` data push in its background isolate
    and dismisses. An iPhone cannot: the screen up there was drawn by CallKit
    out of a PushKit push, and an ordinary data push does not wake a closed
    iPhone at all — so the same news goes out over VoIP as well. Without it
    the caller hangs up and the iPhone rings on for the rest of the window.
    """

    def test_a_missed_call_stops_the_iphone_ringing(self):
        from apps.b2b.workspace import tasks

        employee = {
            "id": 2, "company_id": 7,
            "fcm_token": "f2", "voip_token": "v2",
        }
        with patch.object(tasks.repo, "get_workspace_employee", return_value=employee), \
             patch.object(tasks, "create_notification"), \
             patch.object(tasks, "_push_voip", return_value=True) as voip, \
             patch.object(tasks, "_push"):
            tasks.notify_missed_call(9, 7, 2, "Aziz", "audio", None)

        assert voip.call_args.args[0] == "v2"
        assert voip.call_args.args[1] == {
            "type": "call", "action": "missed", "call_id": "9", "call_type": "audio",
        }

    def test_the_tray_row_still_goes_out_alongside_it(self):
        """The dismiss is not the notification. "Javobsiz qo'ng'iroq" has to
        be readable afterwards by somebody who was not holding the phone."""
        from apps.b2b.workspace import tasks

        employee = {
            "id": 2, "company_id": 7,
            "fcm_token": "f2", "voip_token": "v2",
        }
        with patch.object(tasks.repo, "get_workspace_employee", return_value=employee), \
             patch.object(tasks, "create_notification") as row, \
             patch.object(tasks, "_push_voip", return_value=True), \
             patch.object(tasks, "_push") as push:
            tasks.notify_missed_call(9, 7, 2, "Aziz", "audio", None)

        assert row.called and push.called

    def test_an_android_only_phone_is_not_asked_of_apple(self):
        from apps.b2b.workspace import tasks

        employee = {"id": 2, "company_id": 7, "fcm_token": "f2", "voip_token": None}
        with patch.object(tasks.repo, "get_workspace_employee", return_value=employee), \
             patch.object(tasks, "create_notification"), \
             patch.object(tasks, "_push") as push:
            tasks.notify_missed_call(9, 7, 2, "Aziz", "audio", None)

        assert push.called
