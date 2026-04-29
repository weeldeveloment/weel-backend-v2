from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework_simplejwt.exceptions import TokenError
from drf_yasg.utils import swagger_auto_schema
from .authentication import create_admin_tokens, AdminJWTAuthentication
from .permissions import IsAdminUser
from .serializers import AdminLoginSerializer, AdminUserSerializer, AdminCreateSerializer
from .raw_repository import is_super_admin
from users.tokens import decode_token, TokenMetadata
from users.raw_repository import get_active_user_by_subject


class AdminLoginView(APIView):
    """Admin login endpoint - only for staff/superuser"""
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = AdminLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.validated_data['user']
        tokens = create_admin_tokens(user)

        return Response({
            'access': tokens['access'],
            'refresh': tokens['refresh'],
            'user': AdminUserSerializer(user).data
        }, status=status.HTTP_200_OK)


class AdminMeView(APIView):
    """Get current admin user info"""
    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]

    def get(self, request):
        user = request.user

        if getattr(user, "role", None) != "admin":
            return Response(
                {'error': 'Access denied. Admin privileges required.'},
                status=status.HTTP_403_FORBIDDEN
            )

        serializer = AdminUserSerializer(user)
        return Response(serializer.data)


class AdminRefreshTokenView(APIView):
    """Refresh admin tokens"""
    permission_classes = [AllowAny]

    def post(self, request):
        try:
            refresh_token = request.data.get("refresh")
            if not refresh_token:
                raise AuthenticationFailed(_("Refresh token is required"))
            payload = decode_token(str(refresh_token))
            if payload.get(TokenMetadata.TOKEN_TYPE_CLAIM) != "refresh":
                raise AuthenticationFailed(_("Invalid token type"))
            if payload.get(TokenMetadata.TOKEN_USER_TYPE) != "admin":
                raise AuthenticationFailed(_("Invalid admin refresh token"))
            subject = payload.get(TokenMetadata.TOKEN_SUBJECT)
            user = get_active_user_by_subject(subject, role="admin")
            if not user:
                raise AuthenticationFailed(_("Admin user not found"))
            tokens = create_admin_tokens(user)
        except (TokenError, AuthenticationFailed, ValueError):
            return Response(
                {"detail": _("Invalid refresh token")}, status=status.HTTP_401_UNAUTHORIZED
            )

        return Response(tokens, status=status.HTTP_200_OK)


class AdminRegisterView(APIView):
    """Create a new admin user (superuser only)."""

    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        tags=["Admin Auth"],
        operation_summary="Create admin user (superuser only)",
        request_body=AdminCreateSerializer,
        responses={
            201: AdminUserSerializer,
            403: "Only superusers can create admin users",
        },
    )
    def post(self, request):
        if not is_super_admin(request.user.id):
            return Response(
                {'error': 'Only superusers can create admin users.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = AdminCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        return Response(AdminUserSerializer(user).data, status=status.HTTP_201_CREATED)
