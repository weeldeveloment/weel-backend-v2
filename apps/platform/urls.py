from django.urls import path

from .views import (
    PmsMemberDetailView,
    PmsMembersView,
    PmsMeView,
    PmsOrganizationView,
    PmsSendOTPLoginView,
    PmsSendOTPRegisterView,
    PmsTokenRefreshView,
    PmsVerifyOTPLoginView,
    PmsVerifyOTPRegisterView,
)

urlpatterns = [
    path("register/", PmsSendOTPRegisterView.as_view(), name="pms-register"),
    path("register/verify/", PmsVerifyOTPRegisterView.as_view(), name="pms-register-verify"),
    path("login/", PmsSendOTPLoginView.as_view(), name="pms-login"),
    path("login/verify/", PmsVerifyOTPLoginView.as_view(), name="pms-login-verify"),
    path("me/", PmsMeView.as_view(), name="pms-me"),
    path("token/refresh/", PmsTokenRefreshView.as_view(), name="pms-token-refresh"),
    path("organization/", PmsOrganizationView.as_view(), name="pms-organization"),
    path("organization/members/", PmsMembersView.as_view(), name="pms-members"),
    path("organization/members/<int:member_id>/", PmsMemberDetailView.as_view(), name="pms-member-detail"),
]
