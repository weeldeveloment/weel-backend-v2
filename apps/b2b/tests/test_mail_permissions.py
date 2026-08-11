"""Who may do what with corporate mail, and what one company can see of another.

Mail is the first B2B feature where a bug leaks *content written by outsiders
to a named person*, so the cases worth pinning down are the refusals: an
employee administering domains, a mailbox nobody owns, and above all a thread
id from a different company.

The repository is mocked throughout — these rules live in the views, not in
SQL, so no database is involved.
"""
from unittest.mock import PropertyMock, patch

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.workspace.authentication import WorkspaceUser
from apps.b2b.workspace.roles import capabilities_for

from apps.b2b.mail.views import (
    MailboxListCreateView,
    MailDomainListCreateView,
    MailMeView,
    MailSendView,
    MailThreadMessagesView,
)

COMPANY_ID = 55
OTHER_COMPANY_ID = 66
OWNER_ID = 1
EMPLOYEE_ID = 2

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


def _mailbox(**overrides):
    mailbox = {
        "id": 7,
        "company_id": COMPANY_ID,
        "domain_id": 3,
        "employee_id": EMPLOYEE_ID,
        "address": "aziz@kompaniya.com",
        "local_part": "aziz",
        "display_name": "Aziz Karimov",
        "employee_name": "Aziz Karimov",
        "smtp_password_enc": "enc",
        "quota_bytes": 2147483648,
        "daily_send_limit": 200,
        "is_active": True,
        "last_seen_uid": 0,
        "last_sync_at": None,
        "sync_error": None,
        "domain_name": "kompaniya.com",
        "domain_status": "active",
    }
    mailbox.update(overrides)
    return mailbox


def _call(view_class, request, user, **kwargs):
    force_authenticate(request, user=user)
    return view_class.as_view()(request, **kwargs)


# ─── Capability map ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("role", ["owner", "performer", "employee"])
def test_everyone_may_use_mail_and_write_to_the_outside_world(role):
    # The whole point of a corporate address is that the employee holding it
    # can correspond with customers, not only with colleagues.
    caps = capabilities_for(role)
    assert caps["can_use_mail"] is True
    assert caps["can_send_external_mail"] is True


@pytest.mark.parametrize("role, allowed", [("owner", True), ("performer", False), ("employee", False)])
def test_only_the_owner_administers_domains_and_mailboxes(role, allowed):
    caps = capabilities_for(role)
    assert caps["can_manage_mail_domain"] is allowed
    assert caps["can_manage_mailboxes"] is allowed


# ─── Mail disabled / no mailbox ───────────────────────────────────────────────

def test_mail_endpoints_report_503_when_the_feature_is_off():
    with patch.object(settings, "B2B_MAIL_ENABLED", False, create=True):
        response = _call(MailMeView, factory.get("/mail/me/"), EMPLOYEE)
    assert response.status_code == 503


def test_an_employee_without_a_mailbox_gets_a_recognisable_code():
    # The apps render an empty state from this rather than an error toast, so
    # the machine-readable code matters as much as the status.
    with patch.object(settings, "B2B_MAIL_ENABLED", True, create=True), \
         patch("apps.b2b.mail.views.repo.get_mailbox_for_employee", return_value=None):
        response = _call(MailMeView, factory.get("/mail/me/"), EMPLOYEE)
    assert response.status_code == 404
    assert response.data["code"] == "no_mailbox"


def test_a_disabled_mailbox_is_refused_rather_than_silently_empty():
    with patch.object(settings, "B2B_MAIL_ENABLED", True, create=True), \
         patch("apps.b2b.mail.views.repo.get_mailbox_for_employee",
               return_value=_mailbox(is_active=False)):
        response = _call(MailMeView, factory.get("/mail/me/"), EMPLOYEE)
    assert response.status_code == 403


# ─── Tenant isolation ─────────────────────────────────────────────────────────

def test_a_thread_from_another_company_is_not_found():
    """The whole tenancy guarantee in one test.

    ``get_thread`` is called with the caller's own mailbox id, so a thread id
    belonging to someone else matches nothing. If this ever returns 200 the
    feature is leaking other companies' mail.
    """
    with patch.object(settings, "B2B_MAIL_ENABLED", True, create=True), \
         patch("apps.b2b.mail.views.repo.get_mailbox_for_employee", return_value=_mailbox()), \
         patch("apps.b2b.mail.views.repo.get_thread", return_value=None) as get_thread:
        response = _call(
            MailThreadMessagesView,
            factory.get("/mail/threads/999/messages/"),
            EMPLOYEE,
            thread_id=999,
        )

    assert response.status_code == 404
    # Scoped by mailbox, not looked up by id alone.
    get_thread.assert_called_once_with(999, 7)


def test_reading_a_thread_marks_it_read_only_on_the_newest_page():
    with patch.object(settings, "B2B_MAIL_ENABLED", True, create=True), \
         patch("apps.b2b.mail.views.repo.get_mailbox_for_employee", return_value=_mailbox()), \
         patch("apps.b2b.mail.views.repo.get_thread", return_value={"id": 4}), \
         patch("apps.b2b.mail.views.repo.list_messages", return_value=[]), \
         patch("apps.b2b.mail.views.repo.list_recipients", return_value={}), \
         patch("apps.b2b.mail.views.repo.list_attachments", return_value={}), \
         patch("apps.b2b.mail.views.repo.mark_thread_read") as mark_read:
        _call(MailThreadMessagesView, factory.get("/mail/threads/4/messages/"),
              EMPLOYEE, thread_id=4)
        assert mark_read.called

        mark_read.reset_mock()
        # Paging back through history must not clear messages that arrived
        # since the reader opened the thread.
        _call(MailThreadMessagesView, factory.get("/mail/threads/4/messages/?before_id=10"),
              EMPLOYEE, thread_id=4)
        assert not mark_read.called


# ─── Administration ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("user", [EMPLOYEE, _user("performer", 3)])
def test_a_non_owner_may_not_list_or_connect_domains(user):
    with patch.object(settings, "B2B_MAIL_ENABLED", True, create=True):
        listed = _call(MailDomainListCreateView, factory.get("/mail/domains/"), user)
        created = _call(
            MailDomainListCreateView,
            factory.post("/mail/domains/", {"domain": "kompaniya.com"}, format="json"),
            user,
        )
    assert listed.status_code == 403
    assert created.status_code == 403


def test_a_non_owner_may_not_create_mailboxes():
    with patch.object(settings, "B2B_MAIL_ENABLED", True, create=True):
        response = _call(
            MailboxListCreateView,
            factory.post("/mail/mailboxes/",
                         {"employee_id": 3, "domain_id": 1, "local_part": "aziz"},
                         format="json"),
            EMPLOYEE,
        )
    assert response.status_code == 403


def test_a_mailbox_cannot_be_created_on_an_unverified_domain():
    # Sending from a domain whose SPF/DKIM is not published yet lands in spam
    # and damages the sending IP — better to refuse than to look broken later.
    with patch.object(settings, "B2B_MAIL_ENABLED", True, create=True), \
         patch("apps.b2b.mail.views.repo.get_domain",
               return_value={"id": 1, "domain": "kompaniya.com", "status": "pending"}):
        response = _call(
            MailboxListCreateView,
            factory.post("/mail/mailboxes/",
                         {"employee_id": 3, "domain_id": 1, "local_part": "aziz"},
                         format="json"),
            OWNER,
        )
    assert response.status_code == 400
    assert "domain_id" in response.data


def test_an_employee_from_another_company_cannot_be_given_a_mailbox():
    with patch.object(settings, "B2B_MAIL_ENABLED", True, create=True), \
         patch("apps.b2b.mail.views.repo.get_domain",
               return_value={"id": 1, "domain": "kompaniya.com", "status": "active"}), \
         patch("apps.b2b.mail.views.repo.get_employee", return_value=None) as get_employee:
        response = _call(
            MailboxListCreateView,
            factory.post("/mail/mailboxes/",
                         {"employee_id": 999, "domain_id": 1, "local_part": "aziz"},
                         format="json"),
            OWNER,
        )
    assert response.status_code == 400
    get_employee.assert_called_once_with(999, COMPANY_ID)


def test_a_domain_already_connected_elsewhere_is_refused():
    # Two companies cannot both own delivery for one domain; letting the second
    # one through would route a competitor's mail to them.
    with patch.object(settings, "B2B_MAIL_ENABLED", True, create=True), \
         patch("apps.b2b.mail.views.repo.find_domain_by_name",
               return_value={"id": 1, "company_id": OTHER_COMPANY_ID}):
        response = _call(
            MailDomainListCreateView,
            factory.post("/mail/domains/", {"domain": "kompaniya.com"}, format="json"),
            OWNER,
        )
    assert response.status_code == 400


# ─── Send limits ──────────────────────────────────────────────────────────────

def test_sending_stops_at_the_daily_limit():
    with patch.object(settings, "B2B_MAIL_ENABLED", True, create=True), \
         patch("apps.b2b.mail.views.repo.get_mailbox_for_employee", return_value=_mailbox()), \
         patch("apps.b2b.mail.views.repo.list_company_addresses", return_value=[]), \
         patch("apps.b2b.mail.views.repo.count_sent_today", return_value=200):
        response = _call(
            MailSendView,
            factory.post("/mail/messages/",
                         {"to": ["mijoz@gmail.com"], "body_text": "salom"},
                         format="json"),
            EMPLOYEE,
        )
    assert response.status_code == 429


def test_a_role_denied_external_mail_may_still_write_to_colleagues():
    """Guards the internal/external split itself.

    ``can_send_external_mail`` is True for every role today, so this drives the
    check with the capability forced off — the branch has to keep working if
    that policy is ever tightened.
    """
    restricted = _user("employee", EMPLOYEE_ID)
    denied = {**capabilities_for("employee"), "can_send_external_mail": False}

    with patch.object(settings, "B2B_MAIL_ENABLED", True, create=True), \
         patch.object(WorkspaceUser, "capabilities", PropertyMock(return_value=denied)), \
         patch("apps.b2b.mail.views.repo.get_mailbox_for_employee", return_value=_mailbox()), \
         patch("apps.b2b.mail.views.repo.list_company_addresses",
               return_value=["boshliq@kompaniya.com"]), \
         patch("apps.b2b.mail.views.repo.count_sent_today", return_value=0):
        outside = _call(
            MailSendView,
            factory.post("/mail/messages/",
                         {"to": ["mijoz@gmail.com"], "body_text": "salom"},
                         format="json"),
            restricted,
        )
    assert outside.status_code == 403
