from __future__ import annotations

import logging

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema

from apps.b2b.repository import get_b2b_user_by_phone
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

from apps.b2b.tokens import create_b2b_tokens, rotate_b2b_tokens
from users.tokens import CustomRefreshToken
from users.models.logs import SmsPurpose
from users.services import EskizService, OTPRedisService
from users.tasks import send_otp_sms_eskiz

logger = logging.getLogger(__name__)


class B2BLoginSendOTPSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=20)

    def validate_phone(self, value: str) -> str:
        value = value.replace(" ", "").strip()
        if not value.startswith("+"):
            value = "+" + value
        return value


class B2BLoginVerifySerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=20)
    otp = serializers.CharField(min_length=4, max_length=6)

    def validate_phone(self, value: str) -> str:
        value = value.replace(" ", "").strip()
        if not value.startswith("+"):
            value = "+" + value
        return value

    def validate(self, attrs):
        phone = attrs["phone"]
        otp = attrs["otp"]

        b2b_user = get_b2b_user_by_phone(phone)
        if not b2b_user:
            raise serializers.ValidationError({"phone": _("B2B user not found.")})

        ok, msg = OTPRedisService.verify_otp(phone, otp, SmsPurpose.B2B_LOGIN)
        if not ok:
            raise serializers.ValidationError({"otp": msg})

        OTPRedisService.consume_otp(phone, SmsPurpose.B2B_LOGIN)
        attrs["b2b_user"] = b2b_user
        return attrs


class B2BLoginSendOTPView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        tags=["B2B Auth"],
        operation_summary="Send OTP to B2B owner/manager phone",
        request_body=B2BLoginSendOTPSerializer,
        responses={
            200: openapi.Response(
                description="OTP sent successfully",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "detail": openapi.Schema(type=openapi.TYPE_STRING),
                        "phone": openapi.Schema(type=openapi.TYPE_STRING),
                        "expires_in": openapi.Schema(type=openapi.TYPE_STRING),
                    },
                ),
            ),
            404: openapi.Response(description="B2B user not found"),
            429: openapi.Response(description="Too many requests — wait before retrying"),
        },
    )
    def post(self, request):
        serializer = B2BLoginSendOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        phone = serializer.validated_data["phone"]

        b2b_user = get_b2b_user_by_phone(phone)
        if not b2b_user:
            if OTPRedisService.is_test_phone_for_purpose(phone, SmsPurpose.B2B_LOGIN):
                return Response(
                    {"detail": _("OTP sent successfully (test mode)"), "phone": phone},
                    status=status.HTTP_200_OK,
                )
            return Response(
                {"detail": _("B2B user not found.")},
                status=status.HTTP_404_NOT_FOUND,
            )

        if OTPRedisService.is_test_phone_for_purpose(phone, SmsPurpose.B2B_LOGIN):
            return Response(
                {
                    "detail": _("OTP sent successfully"),
                    "phone": phone,
                    "expires_in": f"{OTPRedisService.OTP_EXPIRE} seconds",
                }
            )

        try:
            if not OTPRedisService.can_resend(phone, SmsPurpose.B2B_LOGIN):
                return Response(
                    {"detail": _("Please wait before requesting a new OTP.")},
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )

            otp_code = OTPRedisService.create_otp(phone, SmsPurpose.B2B_LOGIN)
            OTPRedisService.mark_resend(phone, SmsPurpose.B2B_LOGIN)
        except Exception:
            logger.exception("B2B login OTP cache is unavailable.")
            return Response(
                {"detail": _("OTP service is temporarily unavailable.")},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if not EskizService.is_configured():
            logger.warning(
                "Eskiz is not configured; skipping real SMS for %s. OTP: %s", phone, otp_code
            )
            if settings.DEBUG:
                # No SMS provider to deliver the code in dev — hand it back
                # directly instead of leaving the user stuck with no way in.
                return Response(
                    {
                        "detail": _("OTP sent successfully (Eskiz not configured, dev mode)"),
                        "phone": phone,
                        "otp": otp_code,
                        "expires_in": f"{OTPRedisService.OTP_EXPIRE} seconds",
                    }
                )
        else:
            try:
                send_otp_sms_eskiz.delay(phone, SmsPurpose.B2B_LOGIN, otp_code)
            except Exception:
                logger.warning("Failed to queue SMS task (Redis/Celery unavailable), OTP: %s", otp_code)

        return Response(
            {
                "detail": _("OTP sent successfully"),
                "phone": phone,
                "expires_in": f"{OTPRedisService.OTP_EXPIRE} seconds",
            }
        )


class B2BLoginVerifyView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        tags=["B2B Auth"],
        operation_summary="Verify OTP and get B2B access tokens",
        request_body=B2BLoginVerifySerializer,
        responses={
            200: openapi.Response(
                description="Login successful",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "access": openapi.Schema(type=openapi.TYPE_STRING),
                        "refresh": openapi.Schema(type=openapi.TYPE_STRING),
                        "detail": openapi.Schema(type=openapi.TYPE_STRING),
                        "user": openapi.Schema(
                            type=openapi.TYPE_OBJECT,
                            properties={
                                "id": openapi.Schema(type=openapi.TYPE_INTEGER),
                                "company_id": openapi.Schema(type=openapi.TYPE_INTEGER),
                                "phone": openapi.Schema(type=openapi.TYPE_STRING),
                                "first_name": openapi.Schema(type=openapi.TYPE_STRING),
                                "last_name": openapi.Schema(type=openapi.TYPE_STRING),
                                "role": openapi.Schema(type=openapi.TYPE_STRING),
                            },
                        ),
                    },
                ),
            ),
            400: openapi.Response(description="Invalid OTP or phone"),
        },
    )
    def post(self, request):
        serializer = B2BLoginVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        b2b_user = serializer.validated_data["b2b_user"]
        tokens = create_b2b_tokens(b2b_user)

        return Response(
            {
                "access": tokens["access"],
                "refresh": tokens["refresh"],
                "user": {
                    "id": b2b_user["id"],
                    "company_id": b2b_user["company_id"],
                    "phone": b2b_user["phone"],
                    "first_name": b2b_user.get("first_name"),
                    "last_name": b2b_user.get("last_name"),
                    "role": b2b_user.get("role"),
                },
                "detail": _("Login successful"),
            }
        )


class B2BRefreshSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class B2BTokenRefreshView(APIView):
    """POST /api/b2b/auth/token/refresh/

    Login has always returned a refresh token, but there was no endpoint to
    redeem it — so B2B sessions (dashboard included) died the moment the
    access token expired and dumped the user back on the login screen.
    """

    permission_classes = [AllowAny]
    throttle_scope = "token_refresh"

    @swagger_auto_schema(
        tags=["B2B Auth"],
        operation_summary="Exchange a B2B refresh token for a new token pair",
        request_body=B2BRefreshSerializer,
        responses={
            200: openapi.Response(
                description="New token pair",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "access": openapi.Schema(type=openapi.TYPE_STRING),
                        "refresh": openapi.Schema(type=openapi.TYPE_STRING),
                    },
                ),
            ),
            401: openapi.Response(description="Invalid or expired refresh token"),
        },
    )
    def post(self, request):
        serializer = B2BRefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            tokens = rotate_b2b_tokens(serializer.validated_data["refresh"])
        except (TokenError, InvalidToken):
            return Response(
                {"detail": _("Invalid or expired refresh token")},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        return Response({"access": tokens["access"], "refresh": tokens["refresh"]})


class B2BLogoutView(APIView):
    """POST /api/b2b/auth/logout/ — revoke the presented refresh token."""

    permission_classes = [AllowAny]

    @swagger_auto_schema(
        tags=["B2B Auth"],
        operation_summary="Log out and revoke the refresh token",
        request_body=B2BRefreshSerializer,
        responses={200: openapi.Response(description="Logged out")},
    )
    def post(self, request):
        serializer = B2BRefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            CustomRefreshToken(serializer.validated_data["refresh"]).blacklist()
        except TokenError:
            return Response(
                {"detail": _("Invalid token")}, status=status.HTTP_400_BAD_REQUEST
            )

        return Response({"detail": _("Successfully logged out")})
