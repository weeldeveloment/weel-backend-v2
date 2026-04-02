import logging

from rest_framework.views import APIView
from rest_framework import status, viewsets
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework.permissions import AllowAny

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema

from django.conf import settings
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)

from .bin_lookup import get_brand_for_card
from .serializers import (
    UserPhoneNumberSerializer,
    ClientOTPLoginVerifySerializer,
    ClientRegisterSerializer,
    ClientOTPRegistrationVerifySerializer,
    TokenRefreshSerializer,
    ClientProfileSerializer,
    PartnerOTPRegisterSerializer,
    PartnerOTPRegisterVerifySerializer,
    PartnerOTPLoginSerializer,
    # PartnerPasswordLoginSerializer,
    PartnerProfileSerializer,
    PartnerPassportUploadSerializer,
    ResendOTPSerializer,
)

from .models.logs import SmsPurpose
from .services import (
    OTPRedisService,
    TelegramBindingService,
    ClientDeviceService,
    PartnerDeviceService,
    get_telegram_user_from_request,
)
from .tasks import send_otp_sms_eskiz
from .tokens import (
    create_client_tokens,
    rotate_tokens,
    create_partner_tokens,
    CustomRefreshToken,
)
from payment.services import PlumAPIError
from shared.permissions import IsClient, IsPartner, IsClientOrPartner
from users.authentication import (
    ClientJWTAuthentication,
    PartnerJWTAuthentication,
    ClientOrPartnerJWTAuthentication,
)
from .raw_repository import (
    create_client,
    create_partner,
    ensure_test_partner,
    exists_partner_username,
    exists_user_by_phone,
    get_active_user_by_phone,
    soft_deactivate_user,
    update_user_profile,
)

from payment.services import PlumAPIService


class ClientSendOTPLoginView(APIView):
    permission_classes = (AllowAny,)

    @swagger_auto_schema(
        tags=["Auth - Login"],
        request_body=UserPhoneNumberSerializer,
    )
    def post(self, request):
        serializer = UserPhoneNumberSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        phone_number = serializer.validated_data["phone_number"]

        client = get_active_user_by_phone(phone_number, role="client")
        if client is None:
            return Response(
                {"detail": _("Client not found. Please register first")},
                status=status.HTTP_404_NOT_FOUND,
            )

        if OTPRedisService.is_test_phone_for_purpose(phone_number, SmsPurpose.LOGIN):
            return Response(
                {
                    "detail": _("OTP sent successfully"),
                    "phone_number": phone_number,
                    "expires_in": f"{OTPRedisService.OTP_EXPIRE} seconds",
                }
            )

        otp_code = OTPRedisService.create_otp(phone_number, SmsPurpose.LOGIN)
        send_otp_sms_eskiz.delay(phone_number, SmsPurpose.LOGIN, otp_code)

        return Response(
            {
                "detail": _("OTP sent successfully"),
                "phone_number": phone_number,
                "expires_in": f"{OTPRedisService.OTP_EXPIRE} seconds",
            }
        )


class ClientVerifyOTPLoginView(APIView):
    permission_classes = (AllowAny,)

    @swagger_auto_schema(
        tags=["Auth - Login"],
        request_body=ClientOTPLoginVerifySerializer,
    )
    def post(self, request):
        serializer = ClientOTPLoginVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        client = serializer.validated_data["client"]
        fcm_token = serializer.validated_data.get("fcm_token")
        device_type = serializer.validated_data.get("device_type")

        if fcm_token:
            ClientDeviceService.register_device(
                client=client,
                fcm_token=fcm_token,
                device_type=device_type,
            )

        tokens = create_client_tokens(client, request)

        return Response(
            {
                "access": tokens["access"],
                "refresh": tokens["refresh"],
                "client": {
                    "guid": client.guid,
                    "phone_number": client.phone_number,
                    "first_name": client.first_name,
                    "last_name": client.last_name,
                },
            }
        )


class ClientSendOTPRegisterView(APIView):
    permission_classes = (AllowAny,)

    @swagger_auto_schema(
        tags=["Auth - Register"],
        request_body=ClientRegisterSerializer,
    )
    def post(self, request):
        serializer = ClientRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        phone_number = serializer.validated_data["phone_number"]
        registration_data = {
            "first_name": serializer.validated_data.get("first_name") or "",
            "last_name": serializer.validated_data.get("last_name") or "",
            "phone_number": phone_number,
        }

        otp_code = OTPRedisService.create_otp_with_data(
            phone_number, SmsPurpose.REGISTER, registration_data
        )

        send_otp_sms_eskiz.delay(phone_number, SmsPurpose.REGISTER, otp_code)

        # DEBUG: kod telefonga kelmasa terminal/logda ko‘ring yoki javobda (faqat dev)
        if settings.DEBUG:
            logger.warning(
                "[DEBUG] Client register OTP for %s: %s (Celery worker ishlashi kerak, SMS Eskiz orqali yuboriladi)",
                phone_number,
                otp_code,
            )

        return Response(
            {
                "detail": _("OTP sent successfully for registration"),
                "phone_number": phone_number,
                "expires_in": f"{OTPRedisService.OTP_EXPIRE} seconds",
            }
        )


class ClientRegisterVerifyView(APIView):
    permission_classes = (AllowAny,)

    @swagger_auto_schema(
        tags=["Auth - Register"],
        request_body=ClientOTPRegistrationVerifySerializer,
    )
    def post(self, request):
        serializer = ClientOTPRegistrationVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        registration_data = data["registration_data"]
        phone_number = data["phone_number"]
        fcm_token = data.get("fcm_token")
        device_type = data.get("device_type")

        client = create_client(
            phone_number=phone_number,
            first_name=registration_data.get("first_name") or "",
            last_name=registration_data.get("last_name") or "",
        )

        if fcm_token:
            ClientDeviceService.register_device(
                client=client,
                fcm_token=fcm_token,
                device_type=device_type,
            )

        tokens = create_client_tokens(client, request)

        return Response(
            {
                "access": tokens["access"],
                "refresh": tokens["refresh"],
                "client": {
                    "guid": client.guid,
                    "phone_number": client.phone_number,
                    "first_name": client.first_name,
                    "last_name": client.last_name,
                },
                "detail": _("Registration completed successfully"),
            },
            status=status.HTTP_201_CREATED,
        )


class ClientResendOTPLoginView(APIView):
    permission_classes = (AllowAny,)

    @swagger_auto_schema(
        tags=["Auth - Login"],
        request_body=ResendOTPSerializer,
    )
    def post(self, request):
        serializer = ResendOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        phone_number = serializer.validated_data["phone_number"]

        if not get_active_user_by_phone(phone_number, role="client"):
            return Response(
                {"detail": _("Client not found")},
                status=status.HTTP_404_NOT_FOUND,
            )

        if OTPRedisService.is_test_phone_for_purpose(phone_number, SmsPurpose.LOGIN):
            return Response(
                {
                    "detail": _("OTP resent successfully"),
                    "expires_in": f"{OTPRedisService.OTP_EXPIRE} seconds",
                }
            )

        if not OTPRedisService.can_resend(phone_number, SmsPurpose.LOGIN):
            return Response(
                {"detail": _("Please wait before resending OTP")},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        otp_code = OTPRedisService.create_otp(phone_number, SmsPurpose.LOGIN)
        OTPRedisService.mark_resend(phone_number, SmsPurpose.LOGIN)

        send_otp_sms_eskiz.delay(phone_number, SmsPurpose.LOGIN, otp_code)

        return Response(
            {
                "detail": _("OTP resent successfully"),
                "expires_in": f"{OTPRedisService.OTP_EXPIRE} seconds",
            }
        )


class ClientResendOTPRegisterView(APIView):
    permission_classes = (AllowAny,)

    @swagger_auto_schema(
        tags=["Auth - Register"],
        request_body=ResendOTPSerializer,
    )
    def post(self, request):
        serializer = ResendOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        phone_number = serializer.validated_data["phone_number"]

        registration_data = OTPRedisService.get_registration_data(
            phone_number, SmsPurpose.REGISTER
        )

        if not registration_data:
            return Response(
                {"detail": _("Registration not started")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not OTPRedisService.can_resend(phone_number, SmsPurpose.REGISTER):
            return Response(
                {"detail": _("Please wait before resending OTP")},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        otp_code = OTPRedisService.create_otp_with_data(
            phone_number, SmsPurpose.REGISTER, registration_data
        )
        OTPRedisService.mark_resend(phone_number, SmsPurpose.REGISTER)

        send_otp_sms_eskiz.delay(phone_number, SmsPurpose.REGISTER, otp_code)

        return Response(
            {
                "detail": _("OTP resent successfully"),
                "expires_in": f"{OTPRedisService.OTP_EXPIRE} seconds",
            }
        )


class UserTokenRefreshView(APIView):
    permission_classes = (AllowAny,)

    @swagger_auto_schema(
        tags=["Auth - Refresh"],
        request_body=TokenRefreshSerializer,
    )
    def post(self, request):
        serializer = TokenRefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        refresh_token = serializer.validated_data["refresh"]

        try:
            new_tokens = rotate_tokens(refresh_token)
            return Response(
                {"access": new_tokens["access"], "refresh": new_tokens["refresh"]}
            )
        except TokenError:
            return Response(
                {"detail": _("Invalid or expired refresh token")},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        except Exception as e:
            logger.warning("Token refresh failed in API: %s", e)
            return Response(
                {"detail": _("Token refresh failed")},
                status=status.HTTP_400_BAD_REQUEST,
            )


logout_request_body = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    properties={
        "refresh": openapi.Schema(
            type=openapi.TYPE_STRING, description="Refresh token to blacklist"
        )
    },
    required=["refresh"],
)

optional_refresh_request_body = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    properties={
        "refresh": openapi.Schema(
            type=openapi.TYPE_STRING, description="Refresh token to blacklist"
        )
    },
)


def deactivate_account(user, refresh_token=None):
    if refresh_token:
        try:
            token = CustomRefreshToken(refresh_token)
            token.blacklist()
        except TokenError:
            pass

    if not user:
        return
    role = getattr(user, "role", None)
    if role not in ("client", "partner"):
        return
    soft_deactivate_user(user)


class ClientLogoutView(APIView):
    permission_classes = (AllowAny,)

    @swagger_auto_schema(
        tags=["Auth - Logout"],
        request_body=logout_request_body,
    )
    def post(self, request):
        try:
            refresh_token = request.data.get("refresh")
            if refresh_token:
                token = CustomRefreshToken(refresh_token)
                token.blacklist()
            return Response({"detail": _("Successfully logged out")})
        except TokenError:
            return Response(
                {"detail": _("Invalid token")}, status=status.HTTP_400_BAD_REQUEST
            )


class ClientProfileView(APIView):
    authentication_classes = (ClientJWTAuthentication,)
    permission_classes = (IsClient,)

    @swagger_auto_schema(
        tags=["Auth - Profile"],
    )
    def get(self, request):
        client = request.user
        serializer = ClientProfileSerializer(client)
        return Response(serializer.data)


class ClientUpdateProfileView(APIView):
    authentication_classes = (ClientJWTAuthentication,)
    permission_classes = (IsClient,)

    @swagger_auto_schema(
        tags=["Auth - Profile"],
        request_body=ClientProfileSerializer,
    )
    def put(self, request, *args, **kwargs):
        serializer = ClientProfileSerializer(data=request.data, partial=False)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        updated = update_user_profile(
            request.user.id,
            role="client",
            first_name=data.get("first_name"),
            last_name=data.get("last_name"),
            avatar=data.get("avatar"),
        )
        if updated is None:
            return Response({"detail": _("Client not found")}, status=status.HTTP_404_NOT_FOUND)
        return Response(ClientProfileSerializer(updated).data)

    @swagger_auto_schema(
        tags=["Auth - Profile"],
        request_body=ClientProfileSerializer,
    )
    def patch(self, request, *args, **kwargs):
        serializer = ClientProfileSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        updated = update_user_profile(
            request.user.id,
            role="client",
            first_name=data.get("first_name"),
            last_name=data.get("last_name"),
            avatar=data.get("avatar"),
        )
        if updated is None:
            return Response({"detail": _("Client not found")}, status=status.HTTP_404_NOT_FOUND)
        return Response(ClientProfileSerializer(updated).data)


class PartnerOTPRegisterView(APIView):
    permission_classes = (AllowAny,)

    @swagger_auto_schema(
        tags=["Auth - Register"],
        request_body=PartnerOTPRegisterSerializer,
    )
    def post(self, request):
        serializer = PartnerOTPRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        phone_number = serializer.validated_data["phone_number"]

        registration_data = {
            "username": serializer.validated_data["username"],
            "first_name": serializer.validated_data["first_name"],
            "last_name": serializer.validated_data["last_name"],
            "phone_number": phone_number,
            # "password": serializer.validated_data["password"],
            "email": serializer.validated_data.get("email", ""),
        }

        otp_code = OTPRedisService.create_otp_with_data(
            phone_number, SmsPurpose.PARTNER_REGISTER, registration_data
        )

        send_otp_sms_eskiz.delay(phone_number, SmsPurpose.PARTNER_REGISTER, otp_code)

        return Response(
            {
                "detail": _("OTP sent successfully for registration"),
                "phone_number": phone_number,
                "expires_in": f"{OTPRedisService.OTP_EXPIRE} seconds",
            }
        )


class PartnerRegisterVerifyView(APIView):
    permission_classes = (AllowAny,)

    @swagger_auto_schema(
        tags=["Auth - Register"],
        request_body=PartnerOTPRegisterVerifySerializer,
    )
    def post(self, request):

        serializer = PartnerOTPRegisterVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        phone_number = serializer.validated_data["phone_number"]
        fcm_token = serializer.validated_data.get("fcm_token")
        device_type = serializer.validated_data.get("device_type")
        registration_data = serializer.validated_data["registration_data"]

        partner_data = {
            "phone_number": phone_number,
            "username": registration_data["username"],
            "first_name": registration_data["first_name"],
            "last_name": registration_data["last_name"],
            "email": registration_data.get("email"),
            "is_active": True,
        }

        # unhashed_password = registration_data["password"]

        # partner_data["password"] = PasswordService.hash_password(unhashed_password)

        if exists_user_by_phone(phone_number, role="partner"):
            return Response(
                {"detail": "User with this phone number already exists."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        username = partner_data.get("username")
        if username and exists_partner_username(username):
            return Response(
                {"detail": "User with this username already exists."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        partner = create_partner(
            phone_number=partner_data["phone_number"],
            username=partner_data["username"],
            first_name=partner_data["first_name"],
            last_name=partner_data["last_name"],
            email=partner_data.get("email"),
        )

        tg_user = get_telegram_user_from_request(request)
        if tg_user:
            TelegramBindingService.bind_partner(partner=partner, tg_user=tg_user)

        if fcm_token:
            PartnerDeviceService.register_device(
                partner=partner,
                fcm_token=fcm_token,
                device_type=device_type,
            )

        tokens = create_partner_tokens(partner, request)

        return Response(
            {
                "access": tokens["access"],
                "refresh": tokens["refresh"],
                "partner": {
                    "id": partner.id,
                    "guid": partner.guid,
                    "username": partner.username,
                    "phone_number": partner.phone_number,
                    "first_name": partner.first_name,
                    "last_name": partner.last_name,
                    "email": partner.email,
                },
                "detail": _("Registration completed successfully"),
            },
            status=status.HTTP_201_CREATED,
        )


class PartnerSendOTPLoginView(APIView):
    permission_classes = (AllowAny,)

    @swagger_auto_schema(
        tags=["Auth - Login"],
        request_body=UserPhoneNumberSerializer,
    )
    def post(self, request):
        serializer = UserPhoneNumberSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        phone_number = serializer.validated_data["phone_number"]

        partner = get_active_user_by_phone(phone_number, role="partner")
        if partner is None:
            if OTPRedisService.is_test_phone_for_purpose(
                phone_number, SmsPurpose.PARTNER_LOGIN
            ):
                partner = ensure_test_partner(phone_number)
            else:
                return Response(
                    {"detail": _("Partner not found. Please register first.")},
                    status=status.HTTP_404_NOT_FOUND,
                )

        if OTPRedisService.is_test_phone_for_purpose(
            phone_number, SmsPurpose.PARTNER_LOGIN
        ):
            return Response(
                {
                    "detail": _("OTP Send successfully"),
                    "phone_number": phone_number,
                    "expires_in": f"{OTPRedisService.OTP_EXPIRE} seconds",
                }
            )

        otp_code = OTPRedisService.create_otp(phone_number, SmsPurpose.PARTNER_LOGIN)

        send_otp_sms_eskiz.delay(phone_number, SmsPurpose.PARTNER_LOGIN, otp_code)

        return Response(
            {
                "detail": _("OTP Send successfully"),
                "phone_number": phone_number,
                "expires_in": f"{OTPRedisService.OTP_EXPIRE} seconds",
            }
        )


class PartnerLoginVerifyView(APIView):
    permission_classes = (AllowAny,)

    @swagger_auto_schema(
        tags=["Auth - Login"],
        request_body=PartnerOTPLoginSerializer,
    )
    def post(self, request):
        serializer = PartnerOTPLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        partner = serializer.validated_data["partner"]
        fcm_token = serializer.validated_data.get("fcm_token")
        device_type = serializer.validated_data.get("device_type")

        tg_user = get_telegram_user_from_request(request)
        if tg_user:
            TelegramBindingService.bind_partner(partner=partner, tg_user=tg_user)

        if fcm_token:
            PartnerDeviceService.register_device(
                partner=partner,
                fcm_token=fcm_token,
                device_type=device_type,
            )

        tokens = create_partner_tokens(partner, request)

        return Response(
            {
                "access": tokens["access"],
                "refresh": tokens["refresh"],
                "partner": {
                    "id": partner.id,
                    "guid": partner.guid,
                    "username": partner.username,
                    "phone_number": partner.phone_number,
                    "first_name": partner.first_name,
                    "last_name": partner.last_name,
                    "email": partner.email,
                },
                "detail": _("Login successful"),
            }
        )


class PartnerResendOTPLoginView(APIView):
    permission_classes = (AllowAny,)

    @swagger_auto_schema(
        tags=["Auth - Login"],
        request_body=ResendOTPSerializer,
    )
    def post(self, request):
        serializer = ResendOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        phone_number = serializer.validated_data["phone_number"]

        if not get_active_user_by_phone(phone_number, role="partner"):
            if OTPRedisService.is_test_phone_for_purpose(
                phone_number, SmsPurpose.PARTNER_LOGIN
            ):
                ensure_test_partner(phone_number)
            else:
                return Response(
                    {"detail": _("Partner not found")},
                    status=status.HTTP_404_NOT_FOUND,
                )

        if OTPRedisService.is_test_phone_for_purpose(
            phone_number, SmsPurpose.PARTNER_LOGIN
        ):
            return Response({"detail": _("OTP resent successfully")})

        if not OTPRedisService.can_resend(phone_number, SmsPurpose.PARTNER_LOGIN):
            return Response(
                {"detail": _("Please wait before resending OTP")},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        otp_code = OTPRedisService.create_otp(phone_number, SmsPurpose.PARTNER_LOGIN)
        OTPRedisService.mark_resend(phone_number, SmsPurpose.PARTNER_LOGIN)

        send_otp_sms_eskiz.delay(phone_number, SmsPurpose.PARTNER_LOGIN, otp_code)

        return Response({"detail": _("OTP resent successfully")})


class PartnerResendOTPRegisterView(APIView):
    permission_classes = (AllowAny,)

    @swagger_auto_schema(
        tags=["Auth - Register"],
        request_body=ResendOTPSerializer,
    )
    def post(self, request):
        serializer = ResendOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        phone_number = serializer.validated_data["phone_number"]

        registration_data = OTPRedisService.get_registration_data(
            phone_number, SmsPurpose.PARTNER_REGISTER
        )

        if not registration_data:
            return Response(
                {"detail": _("Registration not started")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not OTPRedisService.can_resend(phone_number, SmsPurpose.PARTNER_REGISTER):
            return Response(
                {"detail": _("Please wait before resending OTP")},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        otp_code = OTPRedisService.create_otp_with_data(
            phone_number, SmsPurpose.PARTNER_REGISTER, registration_data
        )
        OTPRedisService.mark_resend(phone_number, SmsPurpose.PARTNER_REGISTER)

        send_otp_sms_eskiz.delay(phone_number, SmsPurpose.PARTNER_REGISTER, otp_code)

        return Response({"detail": _("OTP resent successfully")})


# class PartnerPasswordLoginView(APIView):
#     permission_classes = (AllowAny,)
#
#     @swagger_auto_schema(
#         tags=["Auth - Login"],
#         request_body=PartnerPasswordLoginSerializer,
#     )
#     def post(self, request):
#         serializer = PartnerPasswordLoginSerializer(data=request.data)
#         serializer.is_valid(raise_exception=True)
#
#         partner = serializer.validated_data["partner"]
#
#         tokens = create_partner_tokens(partner, request)
#
#         return Response(
#             {
#                 "access": tokens["access"],
#                 "refresh": tokens["refresh"],
#                 "partner": {
#                     "id": partner.id,
#                     "guid": partner.guid,
#                     "username": partner.username,
#                     "phone_number": partner.phone_number,
#                     "first_name": partner.first_name,
#                     "last_name": partner.last_name,
#                     "email": partner.email,
#                 },
#                 "detail": _("Login successful"),
#             }
#         )


class PartnerLogoutView(APIView):
    permission_classes = (AllowAny,)

    @swagger_auto_schema(
        tags=["Auth - Logout"],
        request_body=logout_request_body,
    )
    def post(self, request):
        try:
            refresh_token = request.data.get("refresh")
            if refresh_token:
                token = CustomRefreshToken(refresh_token)
                token.blacklist()
            return Response(
                {"detail": _("Successfully logged out.")}, status=status.HTTP_200_OK
            )
        except Exception as e:
            return Response(
                {"detail": _("Logout failed.")}, status=status.HTTP_400_BAD_REQUEST
            )


class PartnerProfileView(APIView):
    authentication_classes = (PartnerJWTAuthentication,)
    permission_classes = (IsPartner,)

    @swagger_auto_schema(
        tags=["Auth - Profile"],
    )
    def get(self, request):
        partner = request.user
        serializer = PartnerProfileSerializer(partner)
        return Response(serializer.data)

    @swagger_auto_schema(
        tags=["Auth - Profile"],
        operation_summary="Permanently delete own partner profile",
        operation_description=(
            "Hard delete partner account and related records. "
            "This action is irreversible."
        ),
        request_body=optional_refresh_request_body,
        responses={
            200: openapi.Response(
                description="Partner account permanently deleted",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "detail": openapi.Schema(type=openapi.TYPE_STRING),
                    },
                ),
            ),
            401: "Unauthorized",
            403: "Forbidden",
        },
    )
    def delete(self, request):
        refresh_token = request.data.get("refresh")
        deactivate_account(request.user, refresh_token=refresh_token)
        return Response(
            {"detail": _("Account has been deleted.")},
            status=status.HTTP_200_OK,
        )


class OwnAccountView(APIView):
    """
    DELETE — faqat o'z akkauntini o'chiradi (Partner yoki Client).
    Tekshiruv: token orqali kirgan user o'zini o'chiradi.
    """
    authentication_classes = (ClientOrPartnerJWTAuthentication,)
    permission_classes = (IsClientOrPartner,)

    @swagger_auto_schema(
        tags=["Auth - Profile"],
        operation_summary="Permanently delete own account (Client or Partner)",
        operation_description=(
            "Hard delete the authenticated client or partner account and related records. "
            "This action is irreversible."
        ),
        request_body=optional_refresh_request_body,
        responses={
            200: openapi.Response(
                description="Account permanently deleted",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "detail": openapi.Schema(type=openapi.TYPE_STRING),
                    },
                ),
            ),
            401: "Unauthorized",
            403: "Forbidden",
        },
    )
    def delete(self, request):
        refresh_token = request.data.get("refresh")
        deactivate_account(request.user, refresh_token=refresh_token)
        return Response(
            {"detail": _("Account has been deleted.")},
            status=status.HTTP_200_OK,
        )


class PartnerUpdateView(APIView):
    authentication_classes = (PartnerJWTAuthentication,)
    permission_classes = (IsPartner,)

    @swagger_auto_schema(
        tags=["Auth - Profile"],
        request_body=PartnerProfileSerializer,
    )
    def put(self, request, *args, **kwargs):
        serializer = PartnerProfileSerializer(data=request.data, partial=False)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        updated = update_user_profile(
            request.user.id,
            role="partner",
            first_name=data.get("first_name"),
            last_name=data.get("last_name"),
            username=data.get("username"),
            avatar=data.get("avatar"),
        )
        if updated is None:
            return Response({"detail": _("Partner not found")}, status=status.HTTP_404_NOT_FOUND)
        return Response(PartnerProfileSerializer(updated).data)

    @swagger_auto_schema(
        tags=["Auth - Profile"],
        request_body=PartnerProfileSerializer,
    )
    def patch(self, request, *args, **kwargs):
        serializer = PartnerProfileSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        updated = update_user_profile(
            request.user.id,
            role="partner",
            first_name=data.get("first_name"),
            last_name=data.get("last_name"),
            username=data.get("username"),
            avatar=data.get("avatar"),
        )
        if updated is None:
            return Response({"detail": _("Partner not found")}, status=status.HTTP_404_NOT_FOUND)
        return Response(PartnerProfileSerializer(updated).data)


class PartnerPassportUploadView(APIView):
    authentication_classes = (PartnerJWTAuthentication,)
    permission_classes = (IsPartner,)

    @swagger_auto_schema(
        operation_summary="Upload partner passport",
        tags=["Partner Documents"],
        consumes=["multipart/form-data"],
        manual_parameters=[
            openapi.Parameter(
                "document",
                openapi.IN_FORM,
                type=openapi.TYPE_FILE,
                description="Passport file (pdf, jpg, png)",
                required=True,
            )
        ],
        responses={
            201: openapi.Response("Passport uploaded successfully"),
            400: "Validation error",
            401: "Unauthorized",
            403: "Forbidden",
        },
    )
    def post(self, request):
        serializer = PartnerPassportUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            {
                "detail": _(
                    "Passport upload is temporarily disabled in normalized raw-sql mode."
                )
            },
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )


class ClientCardViewSet(viewsets.ViewSet):
    authentication_classes = (ClientJWTAuthentication,)
    permission_classes = (IsClient,)

    def get_client(self):
        return self.request.user

    @swagger_auto_schema(
        operation_summary="List all cards for the authenticated client",
        tags=["Client Cards"],
        responses={200: openapi.Response("List of cards")},
    )
    def list(self, request):
        client = self.get_client()
        service = PlumAPIService()
        cards = service.get_client_cards(client)
        if cards is None:
            return Response(
                {"detail": "Failed to fetch cards"}, status=status.HTTP_400_BAD_REQUEST
            )
        card_list = cards.get("result", {}).get("cards", [])
        for card in card_list:
            number = card.get("number")
            card_brand = get_brand_for_card(number)
            card["brand"] = card_brand or "Unknown"
        return Response(cards)

    @swagger_auto_schema(
        operation_summary="Add a new card",
        tags=["Client Cards"],
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=["card_number", "expire_date"],
            properties={
                "card_number": openapi.Schema(type=openapi.TYPE_STRING),
                "expire_date": openapi.Schema(type=openapi.TYPE_STRING),
            },
        ),
    )
    def create(self, request):
        client = self.get_client()
        card_number = request.data.get("card_number")
        expire_date = request.data.get("expire_date")

        if not card_number or not expire_date:
            return Response(
                {"detail": "card_number and expire_date required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Always use the authenticated client's phone number for Plum (no client-side phone input).
        normalized = str(client.phone_number)
        service = PlumAPIService()
        try:
            result = service.add_client_card(
                client, card_number, expire_date, phone_number=normalized
            )
            return Response(result, status=status.HTTP_201_CREATED)
        except PlumAPIError as e:
            return Response(
                {"detail": e.message, "plum_error": e.payload},
                status=e.status_code,
            )

    @swagger_auto_schema(
        method="post",
        operation_summary="Verify newly added card (OTP check)",
        tags=["Client Cards"],
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=["session", "otp"],
            properties={
                "session": openapi.Schema(type=openapi.TYPE_STRING),
                "otp": openapi.Schema(type=openapi.TYPE_STRING),
            },
        ),
        responses={
            200: openapi.Response("Verification successful"),
            400: openapi.Response("Invalid session or OTP"),
        },
    )
    @action(detail=False, methods=["post"], url_path="verify")
    def verify(self, request, *args, **kwargs):
        session = request.data.get("session")
        otp = request.data.get("otp")

        if not session or not otp:
            return Response(
                {"detail": "session and otp required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        service = PlumAPIService()

        try:
            result = service.verify_client_card(session, otp)
            return Response(result, status=200)
        except PlumAPIError as e:
            return Response(
                {"detail": e.message, "plum_error": e.payload},
                status=e.status_code,
            )

    @swagger_auto_schema(
        method="post",
        operation_summary="Resend OTP for card verification",
        tags=["Client Cards"],
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=["session"],
            properties={
                "session": openapi.Schema(type=openapi.TYPE_STRING),
            },
        ),
        responses={
            200: openapi.Response("OTP resent successfully"),
            400: openapi.Response("Invalid session"),
        },
    )
    @action(detail=False, methods=["post"], url_path="resend")
    def resend_otp(self, request, *args, **kwargs):
        session = request.data.get("session")

        if not session:
            return Response(
                {"detail": "session is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        service = PlumAPIService()

        try:
            result = service.resend_otp_client(session)
            return Response(result, status=status.HTTP_200_OK)
        except PlumAPIError as e:
            return Response(
                {"detail": e.message, "plum_error": e.payload},
                status=e.status_code,
            )

    @swagger_auto_schema(
        operation_summary="Remove a card by its user_card_id",
        tags=["Client Cards"],
        responses={204: "Card removed successfully"},
    )
    def destroy(self, request, pk=None):
        client = self.get_client()
        user_card_id = pk

        service = PlumAPIService()

        cards = service.get_client_cards(client)
        if not cards or "result" not in cards or "cards" not in cards["result"]:
            return Response({"detail": "Failed to fetch cards"}, status=400)

        user_cards = cards["result"]["cards"]
        card_ids = [str(card["id"]) for card in user_cards]

        if user_card_id not in card_ids:
            return Response(
                {"detail": _("You cannot delete a card that does not belong to you")},
                status=403,
            )

        try:
            service.remove_client_card(user_card_id)
            return Response(status=204)
        except PlumAPIError as e:
            return Response(
                {"detail": e.message, "plum_error": e.payload},
                status=e.status_code,
            )
