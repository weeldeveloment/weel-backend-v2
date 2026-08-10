"""PMS sign-up, sign-in and token refresh.

Registration creates the personal account and nothing else. It used to also
create the organization and its tenant schema, which forced the operator to
name their hotel at sign-up and then name it again on the property step. These
tests pin the split: what registration must create, what it must not, and —
most importantly — that an account with no organization yet is a *valid*
account. An earlier version deleted such accounts on their next login as
"incomplete", so anyone who closed the tab after receiving their OTP lost it.

The repository layer is mocked throughout: these rules live in the views, and
the real tables are raw SQL that the sqlite test database does not carry.
"""
from unittest.mock import patch

import pytest
from rest_framework.test import APIRequestFactory

from apps.platform.views import (
    PmsSendOTPRegisterView,
    PmsTokenRefreshView,
    PmsVerifyOTPLoginView,
    PmsVerifyOTPRegisterView,
)

factory = APIRequestFactory()

PHONE = "+998901234567"
OTP = "1234"


class FakeUser:
    def __init__(self, user_id=7, first_name="Ismoil", last_name="Ismoilov"):
        self.id = user_id
        self.phone_number = PHONE
        self.first_name = first_name
        self.last_name = last_name
        self.guid = "00000000-0000-0000-0000-000000000000"


def _org(org_id=3, name="Mehmonxona Uz"):
    return {
        "id": org_id,
        "name": name,
        "slug": f"org-{org_id}",
        "schema_name": f"tenant_{org_id}",
        "is_active": True,
    }


def _post(view, payload, path="/platform/register/"):
    return view.as_view()(factory.post(path, payload, format="json"))


# ─── Sending the registration OTP ────────────────────────────────────────────


@pytest.mark.django_db
def test_register_send_otp_does_not_ask_for_an_organization():
    with patch("apps.platform.views.OTPRedisService") as otp, \
         patch("apps.platform.views.send_otp_sms_eskiz"):
        otp.create_otp_with_data.return_value = OTP
        otp.OTP_EXPIRE = 600

        response = _post(
            PmsSendOTPRegisterView,
            {"phone_number": PHONE, "first_name": "Ismoil", "last_name": "Ismoilov"},
        )

    assert response.status_code == 200
    stored = otp.create_otp_with_data.call_args[0][2]
    assert stored["first_name"] == "Ismoil"
    assert stored["last_name"] == "Ismoilov"
    # The field is gone from the serializer, so nothing about an organization
    # may be carried into the OTP payload any more.
    assert "org_name" not in stored


@pytest.mark.django_db
def test_register_send_otp_still_works_when_the_sms_queue_is_down():
    """Redis or Celery being unavailable must not block sign-up."""
    with patch("apps.platform.views.OTPRedisService") as otp, \
         patch("apps.platform.views.send_otp_sms_eskiz") as sms:
        otp.create_otp_with_data.return_value = OTP
        otp.OTP_EXPIRE = 600
        sms.delay.side_effect = RuntimeError("broker down")

        response = _post(PmsSendOTPRegisterView, {"phone_number": PHONE})

    assert response.status_code == 200


# ─── Verifying the registration OTP ──────────────────────────────────────────


def _verify_registration(existing_user=None, created_user=None):
    """Drive the verify endpoint with the OTP check already satisfied."""
    registration_data = {
        "phone_number": PHONE,
        "first_name": "Ismoil",
        "last_name": "Ismoilov",
    }
    with patch("users.services.OTPRedisService") as otp, \
         patch("apps.platform.views.get_active_user_by_phone", return_value=existing_user), \
         patch("apps.platform.views.create_pms_user", return_value=created_user) as create:
        otp.get_registration_data.return_value = registration_data
        otp.get_otp.return_value = OTP
        response = _post(
            PmsVerifyOTPRegisterView,
            {"phone_number": PHONE, "otp_code": OTP},
            path="/platform/register/verify/",
        )
    return response, create


@pytest.mark.django_db
def test_register_verify_creates_the_account_without_an_organization():
    response, create = _verify_registration(created_user=FakeUser())

    assert response.status_code == 201
    assert response.data["organization"] is None
    assert response.data["organizations"] == []
    assert response.data["has_properties"] is False
    assert response.data["access"] and response.data["refresh"]
    create.assert_called_once()


@pytest.mark.django_db
def test_register_verify_does_not_create_a_tenant_schema():
    """The schema is created with the organization, on the next step."""
    with patch("apps.platform.views.create_tenant_schema") as create_schema, \
         patch("apps.platform.views.create_organization") as create_org, \
         patch("apps.platform.views.create_organization_member") as add_member:
        response, _ = _verify_registration(created_user=FakeUser())

    assert response.status_code == 201
    create_schema.assert_not_called()
    create_org.assert_not_called()
    add_member.assert_not_called()


@pytest.mark.django_db
def test_register_verify_issues_a_token_with_no_organization_scope():
    from rest_framework_simplejwt.tokens import UntypedToken

    response, _ = _verify_registration(created_user=FakeUser())

    token = UntypedToken(response.data["access"])
    assert token.get("user_type") == "pms"
    assert token.get("organization_id") is None


@pytest.mark.django_db
def test_register_verify_rejects_a_phone_that_already_has_an_account():
    response, create = _verify_registration(existing_user=FakeUser())

    assert response.status_code == 400
    create.assert_not_called()


@pytest.mark.django_db
def test_register_verify_rejects_a_wrong_otp():
    with patch("users.services.OTPRedisService") as otp, \
         patch("apps.platform.views.create_pms_user") as create:
        otp.get_registration_data.return_value = {"phone_number": PHONE}
        otp.get_otp.return_value = "9999"
        response = _post(
            PmsVerifyOTPRegisterView,
            {"phone_number": PHONE, "otp_code": OTP},
            path="/platform/register/verify/",
        )

    assert response.status_code == 400
    create.assert_not_called()


# ─── Logging in ──────────────────────────────────────────────────────────────


def _verify_login(organizations):
    user = {
        "id": 7,
        "phone_number": PHONE,
        "first_name": "Ismoil",
        "last_name": "Ismoilov",
        "guid": "00000000-0000-0000-0000-000000000000",
    }
    with patch("users.services.OTPRedisService") as otp, \
         patch("users.raw_repository.get_active_user_by_phone", return_value=FakeUser()), \
         patch("apps.platform.views.get_user_organizations", return_value=organizations), \
         patch("apps.platform.views.has_any_properties", return_value=False), \
         patch("apps.platform.serializers.get_organization_by_id", create=True):
        otp.get_otp.return_value = OTP
        response = _post(
            PmsVerifyOTPLoginView,
            {"phone_number": PHONE, "otp_code": OTP},
            path="/platform/login/verify/",
        )
    return response, user


@pytest.mark.django_db
def test_login_without_an_organization_signs_in_instead_of_deleting_the_account():
    """The regression this suite exists for.

    Registering and then closing the browser leaves an account with no
    organization. That state used to be treated as a corrupt half-registration:
    the login endpoint hard-deleted the account and answered 410 "please
    register again". It is now the normal state between step 1 and step 2.
    """
    with patch("apps.platform.views.delete_orphaned_pms_user", create=True) as delete:
        response, _ = _verify_login(organizations=[])

    assert response.status_code == 200
    assert response.data["organization"] is None
    assert response.data["organizations"] == []
    assert response.data["access"]
    delete.assert_not_called()


@pytest.mark.django_db
def test_login_with_an_organization_scopes_the_token_to_it():
    from rest_framework_simplejwt.tokens import UntypedToken

    response, _ = _verify_login(organizations=[_org(org_id=3)])

    assert response.status_code == 200
    assert response.data["organization"]["id"] == 3
    assert UntypedToken(response.data["access"]).get("organization_id") == 3


# ─── Refreshing tokens ───────────────────────────────────────────────────────


def _refresh(organization_id):
    from apps.platform.views import _create_pms_tokens

    tokens = _create_pms_tokens(
        {"id": 7, "phone_number": PHONE}, organization_id=organization_id
    )
    # Revocation runs for real: CustomRefreshToken.blacklist() writes to the
    # cache denylist, which is locmem under the test settings.
    with patch("apps.platform.views.get_user_by_id", return_value=FakeUser()):
        return _post(
            PmsTokenRefreshView,
            {"refresh": tokens["refresh"]},
            path="/platform/token/refresh/",
        )


@pytest.mark.django_db
def test_refresh_works_for_an_account_that_has_no_organization_yet():
    """Without this the token expires mid-onboarding and the user is bounced
    back to the login screen before they can create their organization."""
    response = _refresh(organization_id=None)

    assert response.status_code == 200
    assert response.data["access"] and response.data["refresh"]


@pytest.mark.django_db
def test_refresh_keeps_the_organization_scope():
    from rest_framework_simplejwt.tokens import UntypedToken

    response = _refresh(organization_id=3)

    assert response.status_code == 200
    assert UntypedToken(response.data["access"]).get("organization_id") == 3


@pytest.mark.django_db
def test_refresh_rejects_a_token_from_another_audience():
    from rest_framework_simplejwt.tokens import RefreshToken

    token = RefreshToken()
    token["user_type"] = "client"
    token["sub"] = "7"

    response = _post(
        PmsTokenRefreshView,
        {"refresh": str(token)},
        path="/platform/token/refresh/",
    )

    assert response.status_code == 401


@pytest.mark.django_db
def test_refresh_requires_a_token():
    response = _post(PmsTokenRefreshView, {}, path="/platform/token/refresh/")
    assert response.status_code == 400
