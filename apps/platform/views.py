from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken, AccessToken

from apps.platform.authentication import PmsJWTAuthentication
from apps.platform.raw_repository import (
    create_organization,
    create_organization_member,
    create_tenant_schema,
    delete_orphaned_pms_user,
    get_organization_by_id,
    get_organization_by_slug,
    get_user_organizations,
    list_organization_members,
    remove_organization_member,
    update_member_role,
    update_organization,
    user_has_org_membership,
)
from apps.platform.serializers import (
    AddMemberSerializer,
    OrganizationCreateSerializer,
    OrganizationMemberSerializer,
    OrganizationSerializer,
    OrganizationUpdateSerializer,
    PmsOtpRegisterSerializer,
    PmsOtpVerifySerializer,
    PmsOtpLoginSerializer,
    PmsOtpLoginVerifySerializer,
    PmsLoginResponseSerializer,
    PmsSwitchOrgSerializer,
    PlatformUserSerializer,
    PlatformUserUpdateSerializer,
    UpdateMemberRoleSerializer,
)
from users.models.logs import SmsPurpose
from users.services import OTPRedisService
from users.tasks import send_otp_sms_eskiz
from users.raw_repository import (
    create_pms_user,
    get_active_user_by_phone,
    get_user_by_id,
    update_user_profile,
)
from users.tokens import create_pms_tokens

logger = logging.getLogger(__name__)

PLATFORM_USER_TYPE = "pms"


def _user_claims(user: Any) -> dict[str, Any]:
    if isinstance(user, dict):
        return user

    return {
        "id": getattr(user, "id", None),
        "phone_number": getattr(user, "phone_number", ""),
        "first_name": getattr(user, "first_name", None),
        "last_name": getattr(user, "last_name", None),
    }


def _get_request_user_id(request) -> int | None:
    user = request.user
    if isinstance(user, dict):
        user_id = user.get("id")
    else:
        user_id = getattr(user, "id", None)

    if user_id is None:
        return None

    try:
        return int(user_id)
    except (TypeError, ValueError):
        return None


def _get_request_organization_id(request) -> int | None:
    user = request.user
    if isinstance(user, dict):
        org_id = user.get("organization_id")
    else:
        org_id = getattr(user, "organization_id", None)

    try:
        return int(org_id)
    except (TypeError, ValueError):
        return None


def _get_primary_organization(
    orgs: list[dict[str, Any]],
    organization_id: int | None,
) -> dict[str, Any] | None:
    if organization_id is None:
        return None

    for organization in orgs:
        try:
            if int(organization.get("id")) == int(organization_id):
                return organization
        except (TypeError, ValueError, AttributeError):
            continue
    return None


def _create_pms_tokens(user: dict[str, Any], organization_id: int) -> dict[str, str]:
    user_data = _user_claims(user)
    refresh = RefreshToken()
    access = AccessToken()

    claims = {
        "sub": str(user_data["id"]),
        "user_type": PLATFORM_USER_TYPE,
        "phone_number": user_data.get("phone_number", ""),
        "organization_id": organization_id,
        "iss": getattr(settings, "JWT_ISSUER", "weel"),
    }

    for key, value in claims.items():
        refresh[key] = value
        access[key] = value

    refresh["type"] = "refresh"
    access["type"] = "access"

    return {
        "refresh": str(refresh),
        "access": str(access),
    }


class PmsSendOTPRegisterView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp_login_send"

    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=["phone_number"],
            properties={
                "phone_number": openapi.Schema(type=openapi.TYPE_STRING, description="Phone number"),
                "org_name": openapi.Schema(type=openapi.TYPE_STRING, description="Organization name"),
                "first_name": openapi.Schema(type=openapi.TYPE_STRING, description="First name"),
                "last_name": openapi.Schema(type=openapi.TYPE_STRING, description="Last name"),
            },
        ),
        responses={200: openapi.Response("OTP sent successfully")},
    )
    def post(self, request):
        serializer = PmsOtpRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        phone_number = serializer.validated_data["phone_number"]
        org_name = serializer.validated_data.get("org_name", "")

        registration_data = {
            "phone_number": phone_number,
            "org_name": org_name,
            "first_name": serializer.validated_data.get("first_name", ""),
            "last_name": serializer.validated_data.get("last_name", ""),
        }

        otp_code = OTPRedisService.create_otp_with_data(
            phone_number, SmsPurpose.PMS_REGISTER, registration_data
        )

        try:
            send_otp_sms_eskiz.delay(phone_number, SmsPurpose.PMS_REGISTER, otp_code)
        except Exception:
            logger.warning("Failed to queue SMS task (Redis/Celery unavailable), OTP: %s", otp_code)
        logger.info("PMS Registration OTP for %s: %s", phone_number, otp_code)

        return Response({
            "detail": _("OTP sent successfully for registration"),
            "phone_number": phone_number,
            "expires_in": f"{OTPRedisService.OTP_EXPIRE} seconds",
        })


class PmsVerifyOTPRegisterView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp_register_verify"

    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=["phone_number", "otp_code"],
            properties={
                "phone_number": openapi.Schema(type=openapi.TYPE_STRING, description="Phone number"),
                "otp_code": openapi.Schema(type=openapi.TYPE_STRING, description="OTP code"),
            },
        ),
        responses={201: openapi.Response("Registration successful")},
    )
    def post(self, request):
        serializer = PmsOtpVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        phone_number = serializer.validated_data["phone_number"]
        registration_data = serializer.validated_data["registration_data"]

        existing_user = get_active_user_by_phone(phone_number, role="pms")
        if existing_user:
            if user_has_org_membership(existing_user.id):
                return Response(
                    {"detail": "User with this phone number already exists."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            else:
                delete_orphaned_pms_user(existing_user.id)
                logger.info("Deleted orphaned PMS user %s for phone %s", existing_user.id, phone_number)

        org_name = registration_data.get("org_name", "").strip()
        if not org_name:
            return Response(
                {"org_name": "Organization name is required for registration."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        slug_base = org_name.lower().replace(" ", "-").replace("_", "-")
        slug = slug_base
        counter = 1
        while get_organization_by_slug(slug):
            slug = f"{slug_base}-{counter}"
            counter += 1

        schema_name = f"tenant_{uuid4().hex[:12]}"

        with transaction.atomic():
            create_tenant_schema(schema_name)

            user = create_pms_user(
                phone_number=phone_number,
                first_name=registration_data.get("first_name", ""),
                last_name=registration_data.get("last_name", ""),
            )
            if not user:
                raise RuntimeError("Failed to create PMS user")

            org = create_organization(name=org_name, slug=slug, schema_name=schema_name)
            if not org:
                raise RuntimeError("Failed to create organization")

            create_organization_member(
                organization_id=org["id"],
                user_id=user.id,
                role="owner",
            )

        user_dict = {
            "id": user.id,
            "phone_number": user.phone_number,
            "first_name": user.first_name,
            "last_name": user.last_name,
        }

        tokens = _create_pms_tokens(user_dict, organization_id=org["id"])

        return Response(
            PmsLoginResponseSerializer({
                "access": tokens["access"],
                "refresh": tokens["refresh"],
                "user": PlatformUserSerializer(user_dict).data,
                "organization": OrganizationSerializer(org).data,
            }).data,
            status=status.HTTP_201_CREATED,
        )


class PmsSendOTPLoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp_login_send"

    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=["phone_number"],
            properties={
                "phone_number": openapi.Schema(type=openapi.TYPE_STRING, description="Phone number"),
            },
        ),
        responses={200: openapi.Response("OTP sent successfully")},
    )
    def post(self, request):
        serializer = PmsOtpLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        phone_number = serializer.validated_data["phone_number"]

        user = get_active_user_by_phone(phone_number, role="pms")
        if user is None:
            return Response(
                {"detail": _("User not found. Please register first.")},
                status=status.HTTP_404_NOT_FOUND,
            )

        if OTPRedisService.is_test_phone_for_purpose(phone_number, SmsPurpose.PMS_LOGIN):
            return Response({
                "detail": _("OTP sent successfully"),
                "phone_number": phone_number,
                "expires_in": f"{OTPRedisService.OTP_EXPIRE} seconds",
            })

        otp_code = OTPRedisService.create_otp(phone_number, SmsPurpose.PMS_LOGIN)
        logger.info("PMS Login OTP for %s: %s", phone_number, otp_code)
        try:
            send_otp_sms_eskiz.delay(phone_number, SmsPurpose.PMS_LOGIN, otp_code)
        except Exception:
            logger.warning("Failed to queue SMS task (Redis/Celery unavailable), OTP: %s", otp_code)

        return Response({
            "detail": _("OTP sent successfully"),
            "phone_number": phone_number,
            "expires_in": f"{OTPRedisService.OTP_EXPIRE} seconds",
        })


class PmsVerifyOTPLoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp_login_verify"

    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=["phone_number", "otp_code"],
            properties={
                "phone_number": openapi.Schema(type=openapi.TYPE_STRING, description="Phone number"),
                "otp_code": openapi.Schema(type=openapi.TYPE_STRING, description="OTP code"),
                "organization_id": openapi.Schema(type=openapi.TYPE_INTEGER, description="Optional: select which organization to log into"),
            },
        ),
        responses={200: openapi.Response("Login successful")},
    )
    def post(self, request):
        serializer = PmsOtpLoginVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.validated_data["user"]

        orgs = get_user_organizations(user["id"])
        if not orgs:
            delete_orphaned_pms_user(user["id"])
            logger.info("Deleted orphaned PMS user %s during login attempt", user["id"])
            return Response(
                {"detail": "Your account is incomplete. Please register again."},
                status=status.HTTP_410_GONE,
            )

        org_id = serializer.validated_data.get("organization_id")
        if org_id is not None:
            primary_org = _get_primary_organization(orgs, org_id)
            if not primary_org:
                return Response(
                    {"detail": "You are not a member of the specified organization."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            primary_org = orgs[0]

        tokens = _create_pms_tokens(user, organization_id=primary_org["id"])

        return Response(
            PmsLoginResponseSerializer({
                "access": tokens["access"],
                "refresh": tokens["refresh"],
                "user": PlatformUserSerializer(user).data,
                "organization": OrganizationSerializer(primary_org).data,
                "organizations": [OrganizationSerializer(o).data for o in orgs],
            }).data,
        )


class PmsSwitchOrganizationView(APIView):
    authentication_classes = [PmsJWTAuthentication]
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        request_body=PmsSwitchOrgSerializer,
        responses={200: openapi.Response("Switched organization")},
    )
    def post(self, request):
        serializer = PmsSwitchOrgSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        target_org_id = serializer.validated_data["organization_id"]
        user_id = _get_request_user_id(request)
        if not user_id:
            return Response({"detail": "Not authenticated."}, status=status.HTTP_401_UNAUTHORIZED)

        orgs = get_user_organizations(user_id)
        target_org = _get_primary_organization(orgs, target_org_id)
        if not target_org:
            return Response(
                {"detail": "You are not a member of this organization."},
                status=status.HTTP_403_FORBIDDEN,
            )

        user = get_user_by_id(user_id, role="pms", active_only=True)
        if not user:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)

        user_dict = {
            "id": user.id,
            "phone_number": user.phone_number,
            "first_name": user.first_name,
            "last_name": user.last_name,
        }

        tokens = _create_pms_tokens(user_dict, organization_id=target_org["id"])

        return Response({
            "access": tokens["access"],
            "refresh": tokens["refresh"],
            "organization": OrganizationSerializer(target_org).data,
        })


class PmsMeView(APIView):
    authentication_classes = [PmsJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user_id = _get_request_user_id(request)
        if not user_id:
            return Response({"detail": "Not authenticated."}, status=status.HTTP_401_UNAUTHORIZED)

        user_data = get_user_by_id(user_id, role="pms", active_only=True)
        if not user_data:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)

        orgs = get_user_organizations(user_id)
        primary_org = _get_primary_organization(orgs, _get_request_organization_id(request))

        return Response({
            "user": PlatformUserSerializer(user_data).data,
            "organization": OrganizationSerializer(primary_org).data if primary_org else None,
            "organizations": [OrganizationSerializer(o).data for o in orgs],
        })

    @swagger_auto_schema(
        request_body=PlatformUserUpdateSerializer,
        tags=["platform"],
    )
    def patch(self, request):
        user_id = _get_request_user_id(request)
        if not user_id:
            return Response({"detail": "Not authenticated."}, status=status.HTTP_401_UNAUTHORIZED)

        serializer = PlatformUserUpdateSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        updated_user = update_user_profile(
            int(user_id),
            role="pms",
            **serializer.validated_data,
        )
        if not updated_user:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)

        orgs = get_user_organizations(int(user_id))
        primary_org = _get_primary_organization(orgs, _get_request_organization_id(request))

        return Response({
            "user": PlatformUserSerializer(updated_user).data,
            "organization": OrganizationSerializer(primary_org).data if primary_org else None,
            "organizations": [OrganizationSerializer(o).data for o in orgs],
        })


class PmsOrganizationView(APIView):
    authentication_classes = [PmsJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        org_id = _get_request_organization_id(request)
        if not org_id:
            return Response({"detail": "No organization in token."}, status=status.HTTP_400_BAD_REQUEST)

        org = get_organization_by_id(int(org_id))
        if not org:
            return Response({"detail": "Organization not found."}, status=status.HTTP_404_NOT_FOUND)

        return Response(OrganizationSerializer(org).data)

    def patch(self, request):
        org_id = _get_request_organization_id(request)
        if not org_id:
            return Response({"detail": "No organization in token."}, status=status.HTTP_400_BAD_REQUEST)

        serializer = OrganizationUpdateSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        org = update_organization(int(org_id), **serializer.validated_data)
        if not org:
            return Response({"detail": "Organization not found."}, status=status.HTTP_404_NOT_FOUND)

        return Response(OrganizationSerializer(org).data)


class PmsMembersView(APIView):
    authentication_classes = [PmsJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        org_id = _get_request_organization_id(request)
        if not org_id:
            return Response({"detail": "No organization in token."}, status=status.HTTP_400_BAD_REQUEST)

        members = list_organization_members(int(org_id))
        return Response(OrganizationMemberSerializer(members, many=True).data)

    def post(self, request):
        org_id = _get_request_organization_id(request)
        if not org_id:
            return Response({"detail": "No organization in token."}, status=status.HTTP_400_BAD_REQUEST)

        serializer = AddMemberSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        phone_number = serializer.validated_data["phone_number"]
        existing = get_active_user_by_phone(phone_number, role="pms")

        if existing:
            user = existing
        else:
            return Response(
                {"detail": "User not found. They must register first via OTP."},
                status=status.HTTP_404_NOT_FOUND,
            )

        from apps.platform.raw_repository import get_organization_member
        existing_member = get_organization_member(int(org_id), user["id"])
        if existing_member:
            return Response(
                {"detail": "User is already a member of this organization."},
                status=status.HTTP_409_CONFLICT,
            )

        member = create_organization_member(
            organization_id=int(org_id),
            user_id=user["id"],
            role=serializer.validated_data.get("role", "manager"),
        )

        return Response(OrganizationMemberSerializer(member).data, status=status.HTTP_201_CREATED)


class PmsMemberDetailView(APIView):
    authentication_classes = [PmsJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def patch(self, request, member_id):
        org_id = _get_request_organization_id(request)
        if not org_id:
            return Response({"detail": "No organization in token."}, status=status.HTTP_400_BAD_REQUEST)

        serializer = UpdateMemberRoleSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        member = update_member_role(int(org_id), int(member_id), serializer.validated_data["role"])
        if not member:
            return Response({"detail": "Member not found."}, status=status.HTTP_404_NOT_FOUND)

        return Response(OrganizationMemberSerializer(member).data)

    def delete(self, request, member_id):
        org_id = _get_request_organization_id(request)
        if not org_id:
            return Response({"detail": "No organization in token."}, status=status.HTTP_400_BAD_REQUEST)

        if not remove_organization_member(int(org_id), int(member_id)):
            return Response({"detail": "Member not found."}, status=status.HTTP_404_NOT_FOUND)

        return Response(status=status.HTTP_204_NO_CONTENT)


class PmsTokenRefreshView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        refresh_token = request.data.get("refresh")
        if not refresh_token:
            return Response({"detail": "Refresh token is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            from rest_framework_simplejwt.tokens import RefreshToken as RT
            token = RT(refresh_token)
            user_type = token.get("user_type")
            if user_type != PLATFORM_USER_TYPE:
                return Response({"detail": "Invalid token type."}, status=status.HTTP_401_UNAUTHORIZED)

            user_id = token.get("sub")
            organization_id = token.get("organization_id")

            if not user_id:
                return Response({"detail": "Invalid token: missing user."}, status=status.HTTP_401_UNAUTHORIZED)

            user = get_user_by_id(int(user_id), role="pms", active_only=True)
            if not user:
                return Response({"detail": "User not found."}, status=status.HTTP_401_UNAUTHORIZED)

            if not organization_id:
                return Response(
                    {"detail": "No organization available for refresh token."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            new_tokens = _create_pms_tokens(user, organization_id=int(organization_id))

            try:
                token.blacklist()
            except Exception:
                pass

            return Response(new_tokens)
        except Exception:
            return Response({"detail": "Invalid or expired refresh token."}, status=status.HTTP_401_UNAUTHORIZED)
