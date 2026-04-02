from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    ClientLogoutView,
    UserTokenRefreshView,
    ClientProfileView,
    ClientUpdateProfileView,
    ClientVerifyOTPLoginView,
    ClientSendOTPLoginView,
    ClientRegisterVerifyView,
    ClientSendOTPRegisterView,
    ClientResendOTPRegisterView,
    ClientResendOTPLoginView,
    PartnerOTPRegisterView,
    PartnerRegisterVerifyView,
    PartnerSendOTPLoginView,
    PartnerLoginVerifyView,
    # PartnerPasswordLoginView,
    PartnerLogoutView,
    PartnerProfileView,
    PartnerResendOTPLoginView,
    PartnerResendOTPRegisterView,
    ClientCardViewSet,
    PartnerPassportUploadView,
    PartnerUpdateView,
    OwnAccountView,
)

app_name = "users"

router = DefaultRouter()
router.register("client/cards", ClientCardViewSet, basename="client-cards")

urlpatterns = [
    # client
    path(
        "client/register/", ClientSendOTPRegisterView.as_view(), name="client_register"
    ),
    path(
        "client/register/resend/",
        ClientResendOTPRegisterView.as_view(),
        name="client_register_resend",
    ),
    path(
        "client/register/verify/",
        ClientRegisterVerifyView.as_view(),
        name="client_register_verify",
    ),
    path("client/login/", ClientSendOTPLoginView.as_view(), name="client_login"),
    path(
        "client/login/verify/", ClientVerifyOTPLoginView.as_view(), name="client_login"
    ),
    path(
        "client/login/resend/",
        ClientResendOTPLoginView.as_view(),
        name="client_login_resend",
    ),
    path("client/logout/", ClientLogoutView.as_view(), name="client_logout"),
    path("client/profile/", ClientProfileView.as_view(), name="client_profile"),
    path(
        "client/profile/update/",
        ClientUpdateProfileView.as_view(),
        name="client_profile_update",
    ),
    # partner
    path(
        "partner/register/", PartnerOTPRegisterView.as_view(), name="partner_register"
    ),
    path(
        "partner/register/resend/",
        PartnerResendOTPRegisterView.as_view(),
        name="partner_register_resend",
    ),
    path(
        "partner/register/verify/",
        PartnerRegisterVerifyView.as_view(),
        name="partner_register",
    ),
    path("partner/login/", PartnerSendOTPLoginView.as_view(), name="partner_login"),
    path(
        "partner/login/resend/",
        PartnerResendOTPLoginView.as_view(),
        name="partner_login_resend",
    ),
    path(
        "partner/login/verify/",
        PartnerLoginVerifyView.as_view(),
        name="partner_login_verify",
    ),
    path(
        "partner/documents/passport/",
        PartnerPassportUploadView.as_view(),
        name="partner_passport_upload",
    ),
    # path(
    #     "partner/login/password",
    #     PartnerPasswordLoginView.as_view(),
    #     name="partner_login_password",
    # ),
    path("partner/logout/", PartnerLogoutView.as_view(), name="partner_logout"),
    path("partner/profile/", PartnerProfileView.as_view(), name="partner_profile"),
    path(
        "partner/profile/update/",
        PartnerUpdateView.as_view(),
        name="partner_profile_update",
    ),
    path("refresh/", UserTokenRefreshView.as_view(), name="token_refresh"),
    path("account/", OwnAccountView.as_view(), name="own_account"),
    path("", include(router.urls)),
]
