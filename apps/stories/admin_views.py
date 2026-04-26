from __future__ import annotations

import uuid

from django.utils.translation import gettext_lazy as _

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema

from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from admin_auth.authentication import AdminJWTAuthentication
from admin_auth.permissions import IsAdminUser

from .raw_repository import (
    count_stories_for_admin,
    delete_story_by_guid,
    list_stories_for_admin,
    update_story_verification,
)
from .serializers import AdminStoryModerateSerializer, AdminStorySerializer


class AdminStoryPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class AdminStoryListView(APIView):
    """List all stories for admin moderation - admin only"""

    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]
    pagination_class = AdminStoryPagination

    @swagger_auto_schema(
        tags=["Admin - Stories"],
        operation_summary="List all stories",
        operation_description="List all stories with optional filtering by verification status and search. Admin only.",
        manual_parameters=[
            openapi.Parameter(
                "is_verified",
                openapi.IN_QUERY,
                description="Filter by verification status (true/false)",
                type=openapi.TYPE_BOOLEAN,
                required=False,
            ),
            openapi.Parameter(
                "search",
                openapi.IN_QUERY,
                description="Search by property title, partner ID, or story GUID",
                type=openapi.TYPE_STRING,
                required=False,
            ),
            openapi.Parameter(
                "ordering",
                openapi.IN_QUERY,
                description="Order by field (e.g. -created_at, uploaded_at, expires_at, views)",
                type=openapi.TYPE_STRING,
                required=False,
            ),
            openapi.Parameter(
                "page",
                openapi.IN_QUERY,
                description="Page number",
                type=openapi.TYPE_INTEGER,
                required=False,
            ),
            openapi.Parameter(
                "page_size",
                openapi.IN_QUERY,
                description="Items per page",
                type=openapi.TYPE_INTEGER,
                required=False,
            ),
        ],
        responses={
            status.HTTP_200_OK: AdminStorySerializer(many=True),
            status.HTTP_403_FORBIDDEN: "Admin access required",
        },
    )
    def get(self, request, *args, **kwargs):
        is_verified_raw = request.query_params.get("is_verified")
        is_verified = None
        if is_verified_raw is not None:
            is_verified = is_verified_raw.lower() in ("true", "1", "yes")

        search = request.query_params.get("search")
        ordering = request.query_params.get("ordering")

        total = count_stories_for_admin(
            is_verified=is_verified,
            search=search,
        )

        stories = list_stories_for_admin(
            is_verified=is_verified,
            search=search,
            ordering=ordering,
            limit=max(total, 1),
            offset=0,
        )

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(stories, request, view=self)
        serializer = AdminStorySerializer(page, many=True, context={"request": request})
        return paginator.get_paginated_response(serializer.data)


class AdminStoryModerateView(APIView):
    """Approve or reject a story - admin only"""

    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        tags=["Admin - Stories"],
        operation_summary="Moderate a story",
        operation_description="Approve or reject a story by setting is_verified. Admin only.",
        request_body=AdminStoryModerateSerializer,
        responses={
            status.HTTP_200_OK: AdminStorySerializer,
            status.HTTP_404_NOT_FOUND: "Story not found",
            status.HTTP_403_FORBIDDEN: "Admin access required",
        },
    )
    def patch(self, request, story_guid, *args, **kwargs):
        serializer = AdminStoryModerateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            story_guid = uuid.UUID(str(story_guid))
        except ValueError:
            return Response(
                {"detail": _("Invalid story GUID")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        story = update_story_verification(
            story_guid,
            is_verified=serializer.validated_data["is_verified"],
            verified_by_user_id=request.user.id,
        )
        if not story:
            return Response(
                {"detail": _("Story not found")},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            AdminStorySerializer(story, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )


class AdminStoryDeleteView(APIView):
    """Delete a story - admin only"""

    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        tags=["Admin - Stories"],
        operation_summary="Delete a story",
        operation_description="Delete a story by GUID. Admin only.",
        responses={
            status.HTTP_204_NO_CONTENT: None,
            status.HTTP_404_NOT_FOUND: "Story not found",
            status.HTTP_403_FORBIDDEN: "Admin access required",
        },
    )
    def delete(self, request, story_guid, *args, **kwargs):
        try:
            story_guid = uuid.UUID(str(story_guid))
        except ValueError:
            return Response(
                {"detail": _("Invalid story GUID")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        deleted = delete_story_by_guid(story_guid)
        if not deleted:
            return Response(
                {"detail": _("Story not found")},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(status=status.HTTP_204_NO_CONTENT)
