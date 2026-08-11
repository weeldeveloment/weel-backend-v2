"""Who can see whose mail.

A connected account is somebody's *personal* inbox — often their private Gmail
— living inside a work app. That makes the access rules here stricter than
anywhere else in this codebase: not "can this company see it" but "can this
person see it", with no administrative override, not even for the owner.

So the cases worth pinning down are the refusals: a colleague's thread, a
colleague's attachment, and an owner who is still just another employee where
mail is concerned.

The repository is mocked throughout — these rules live in the views and in the
SQL joins, not in business logic, so no database is involved.
"""
from unittest.mock import patch

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.mail import providers
from apps.b2b.mail.views import (
    MailAccountDetailView,
    MailAccountListCreateView,
    MailAttachmentDownloadView,
    MailSendView,
    MailThreadListView,
    MailThreadMessagesView,
)
from apps.b2b.workspace.authentication import WorkspaceUser
from apps.b2b.workspace.roles import capabilities_for

COMPANY_ID = 55
OWNER_ID = 1
EMPLOYEE_ID = 2
ACCOUNT_ID = 7

factory = APIRequestFactory()


def _user(role: str, employee_id: int, company_id: int = COMPANY_ID) -> WorkspaceUser:
    return WorkspaceUser({
        "id": employee_id,
        "company_id": company_id,
        "role": role,
        "full_name": "Test Person",
        "phone": "+998900000000",
    })


OWNER = _user("owner", OWNER_ID)
EMPLOYEE = _user("employee", EMPLOYEE_ID)


def _account(**overrides):
    account = {
        "id": ACCOUNT_ID,
        "company_id": COMPANY_ID,
        "employee_id": EMPLOYEE_ID,
        "address": "aziz@gmail.com",
        "display_name": "Aziz Karimov",
        "employee_name": "Aziz Karimov",
        "provider": "gmail",
        "auth_type": "app_password",
        "secret_enc": "enc",
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "is_active": True,
        "last_seen_uid": 0,
        "last_sync_at": None,
        "sync_error": None,
    }
    account.update(overrides)
    return account


def _call(view_class, request, user, **kwargs):
    force_authenticate(request, user=user)
    return view_class.as_view()(request, **kwargs)


def _enabled():
    return patch.object(settings, "B2B_MAIL_ENABLED", True, create=True)


# ─── Capability map ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("role", ["owner", "performer", "employee"])
def test_mail_is_open_to_every_role(role):
    # The inbox belongs to the person, not the company, so there is no role
    # that should be prevented from connecting one.
    assert capabilities_for(role)["can_use_mail"] is True


@pytest.mark.parametrize("role", ["owner", "performer", "employee"])
def test_there_is_no_administrative_mail_capability(role):
    """Guards against re-introducing an owner-level override.

    Mail is the one workspace feature an owner must *not* be able to
    administer: doing so would mean reading an employee's private
    correspondence.
    """
    caps = capabilities_for(role)
    for flag in caps:
        assert flag in ("can_use_mail",) or "mail" not in flag, (
            f"{flag} would give a role power over somebody else's inbox"
        )


# ─── Feature flag and empty state ─────────────────────────────────────────────

def test_endpoints_report_503_when_mail_is_disabled():
    with patch.object(settings, "B2B_MAIL_ENABLED", False, create=True):
        response = _call(MailAccountListCreateView, factory.get("/mail/accounts/"), EMPLOYEE)
    assert response.status_code == 503


def test_a_person_with_no_account_gets_an_empty_list_not_an_error():
    # The apps render a "connect your inbox" prompt from this, so it must not
    # arrive as a failure.
    with _enabled(), patch("apps.b2b.mail.views.repo.list_accounts", return_value=[]):
        response = _call(MailThreadListView, factory.get("/mail/threads/"), EMPLOYEE)
    assert response.status_code == 200
    assert response.data["results"] == []
    assert response.data["code"] == "no_account"


# ─── Privacy between colleagues ───────────────────────────────────────────────

def test_a_colleagues_thread_is_not_found():
    """The core privacy guarantee in one test.

    ``get_thread_for_employee`` joins through the account's owner, so a thread
    id belonging to somebody else matches nothing. If this ever returns 200,
    the feature is leaking private mail between colleagues.
    """
    with _enabled(), \
         patch("apps.b2b.mail.views.repo.get_thread_for_employee", return_value=None) as lookup:
        response = _call(
            MailThreadMessagesView,
            factory.get("/mail/threads/999/messages/"),
            EMPLOYEE,
            thread_id=999,
        )

    assert response.status_code == 404
    # Scoped by the signed-in employee, never by company.
    lookup.assert_called_once_with(999, EMPLOYEE_ID)


def test_an_owner_has_no_special_access_to_a_thread():
    with _enabled(), \
         patch("apps.b2b.mail.views.repo.get_thread_for_employee", return_value=None) as lookup:
        response = _call(
            MailThreadMessagesView,
            factory.get("/mail/threads/42/messages/"),
            OWNER,
            thread_id=42,
        )

    assert response.status_code == 404
    # The owner is looked up by their own id like anybody else — there is no
    # company-wide branch to fall back on.
    lookup.assert_called_once_with(42, OWNER_ID)


def test_an_attachment_is_scoped_to_its_owner():
    with _enabled(), \
         patch("apps.b2b.mail.views.repo.get_attachment_for_employee",
               return_value=None) as lookup:
        response = _call(
            MailAttachmentDownloadView,
            factory.get("/mail/attachments/12/"),
            EMPLOYEE,
            attachment_id=12,
        )

    assert response.status_code == 404
    lookup.assert_called_once_with(12, EMPLOYEE_ID)


def test_another_persons_account_cannot_be_renamed_or_disconnected():
    with _enabled(), patch("apps.b2b.mail.views.repo.get_account", return_value=None) as lookup:
        patched = _call(
            MailAccountDetailView,
            factory.patch("/mail/accounts/7/", {"display_name": "x"}, format="json"),
            EMPLOYEE,
            account_id=7,
        )
        deleted = _call(
            MailAccountDetailView,
            factory.delete("/mail/accounts/7/"),
            EMPLOYEE,
            account_id=7,
        )

    assert patched.status_code == 404
    assert deleted.status_code == 404
    assert lookup.call_args_list == [((7, EMPLOYEE_ID),), ((7, EMPLOYEE_ID),)]


# ─── Connecting ───────────────────────────────────────────────────────────────

def test_an_account_is_verified_before_it_is_stored():
    """A credential that cannot log in must never be saved.

    Otherwise the account sits in the list looking connected and silently
    never syncs, which reads as "the app is broken" rather than "the password
    is wrong".
    """
    from cryptography.fernet import Fernet

    from apps.b2b.mail.connection import MailAuthError

    with _enabled(), \
         patch.object(settings, "B2B_MAIL_SECRET_KEY",
                      Fernet.generate_key().decode(), create=True), \
         patch("apps.b2b.mail.views.repo.find_account", return_value=None), \
         patch("apps.b2b.mail.views.verify", side_effect=MailAuthError("bad password")), \
         patch("apps.b2b.mail.views.repo.create_account") as create:
        response = _call(
            MailAccountListCreateView,
            factory.post("/mail/accounts/",
                         {"address": "aziz@gmail.com", "password": "wrong"},
                         format="json"),
            EMPLOYEE,
        )

    assert response.status_code == 400
    assert not create.called
    # Gmail's app-password requirement is explained rather than left to guess.
    assert response.data["help_url"]


def test_the_same_inbox_cannot_be_connected_twice():
    with _enabled(), \
         patch("apps.b2b.mail.views.repo.find_account", return_value=_account()):
        response = _call(
            MailAccountListCreateView,
            factory.post("/mail/accounts/",
                         {"address": "aziz@gmail.com", "password": "abcd efgh ijkl mnop"},
                         format="json"),
            EMPLOYEE,
        )
    assert response.status_code == 400


def test_app_password_spaces_are_stripped():
    """Google shows app passwords in groups of four and people paste them so.

    The spaces are presentation, not part of the secret — leaving them in makes
    a correct password fail.
    """
    from apps.b2b.mail.serializers import MailAccountConnectSerializer

    serializer = MailAccountConnectSerializer(data={
        "address": "aziz@gmail.com",
        "password": "abcd efgh ijkl mnop",
    })
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["password"] == "abcdefghijklmnop"


# ─── Provider guessing ────────────────────────────────────────────────────────

@pytest.mark.parametrize("address, host", [
    ("aziz@gmail.com", "imap.gmail.com"),
    ("aziz@googlemail.com", "imap.gmail.com"),
    ("aziz@yandex.ru", "imap.yandex.ru"),
    ("aziz@mail.ru", "imap.mail.ru"),
    ("aziz@outlook.com", "outlook.office365.com"),
    ("aziz@hotmail.com", "outlook.office365.com"),
])
def test_known_providers_fill_in_their_own_servers(address, host):
    assert providers.for_address(address).imap_host == host


def test_an_unknown_domain_falls_back_to_the_usual_convention():
    settings_for = providers.for_address("aziz@kompaniya.uz")
    assert settings_for.imap_host == "imap.kompaniya.uz"
    assert settings_for.smtp_host == "smtp.kompaniya.uz"
    assert settings_for.requires_app_password is False


def test_gmail_is_flagged_as_needing_an_app_password():
    assert providers.for_address("aziz@gmail.com").requires_app_password is True
    assert providers.for_address("aziz@gmail.com").help_url


# ─── Sending ──────────────────────────────────────────────────────────────────

def test_sending_stops_at_the_daily_limit():
    with _enabled(), \
         patch("apps.b2b.mail.views.repo.list_accounts", return_value=[_account()]), \
         patch("apps.b2b.mail.views.repo.count_sent_today", return_value=999):
        response = _call(
            MailSendView,
            factory.post("/mail/messages/",
                         {"to": ["mijoz@gmail.com"], "body_text": "salom"},
                         format="json"),
            EMPLOYEE,
        )
    assert response.status_code == 429


def test_sending_from_a_broken_account_asks_for_a_reconnect():
    # Distinct from a generic error: the apps branch on this code to show the
    # reconnect sheet instead of a failure toast.
    with _enabled(), \
         patch("apps.b2b.mail.views.repo.list_accounts",
               return_value=[_account(is_active=False, sync_error="revoked")]):
        response = _call(
            MailSendView,
            factory.post("/mail/messages/",
                         {"to": ["mijoz@gmail.com"], "body_text": "salom"},
                         format="json"),
            EMPLOYEE,
        )
    assert response.status_code == 409
    assert response.data["code"] == "reconnect"


def test_sending_uses_a_named_account_only_if_the_caller_owns_it():
    with _enabled(), patch("apps.b2b.mail.views.repo.get_account", return_value=None) as lookup:
        response = _call(
            MailSendView,
            factory.post("/mail/messages/",
                         {"account_id": 999, "to": ["mijoz@gmail.com"], "body_text": "salom"},
                         format="json"),
            EMPLOYEE,
        )
    assert response.status_code == 404
    lookup.assert_called_once_with(999, EMPLOYEE_ID)
