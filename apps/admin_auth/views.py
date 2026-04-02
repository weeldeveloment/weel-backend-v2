from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from drf_yasg.utils import swagger_auto_schema
from .authentication import create_admin_tokens, AdminJWTAuthentication
from .permissions import IsAdminUser
from .serializers import AdminLoginSerializer, AdminUserSerializer, AdminCreateSerializer
from .raw_repository import is_super_admin


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
        from rest_framework_simplejwt.serializers import TokenRefreshSerializer

        serializer = TokenRefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        return Response(serializer.validated_data, status=status.HTTP_200_OK)


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
