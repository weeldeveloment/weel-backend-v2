from __future__ import annotations

import uuid

from django.core.cache import cache
from django.core.files.storage import default_storage
from django.utils.translation import gettext_lazy as _

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema

from rest_framework import status, viewsets
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.generics import ListAPIView
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from admin_auth.authentication import AdminJWTAuthentication
from admin_auth.permissions import IsAdminUser
from shared.permissions import IsPartner
from users.authentication import ClientJWTAuthentication, PartnerJWTAuthentication

from .raw_repository import (
    count_banners,
    count_platform_news_for_admin,
    delete_banner_by_guid,
    delete_platform_news_by_guid,
    delete_story_for_partner,
    delete_story_media,
    get_banner_by_guid,
    get_platform_news_by_guid,
    get_story_by_guid,
    get_story_media_by_guid,
    list_active_stories,
    list_banners,
    list_platform_news_for_admin,
    parse_property_kind,
)
from .serializers import (
    AdminBannerCreateSerializer,
    AdminBannerSerializer,
    AdminBannerUpdateSerializer,
    AdminNewsCreateSerializer,
    AdminNewsSerializer,
    AdminNewsUpdateSerializer,
    PublicBannerSerializer,
    StoryCreateSerializer,
    StoryDetailSerializer,
    StorySerializer,
)


def _is_partner(user) -> bool:
    return getattr(user, "role", None) == "partner"


def _is_client(user) -> bool:
    return getattr(user, "role", None) == "client"


class StoryViewSet(viewsets.GenericViewSet):
    authentication_classes = [PartnerJWTAuthentication, ClientJWTAuthentication]
    parser_classes = [MultiPartParser, FormParser]
    lookup_field = "guid"
    lookup_url_kwarg = "story_id"

    def get_serializer_class(self):
        if self.action == "create":
            return StoryCreateSerializer
        if self.action == "retrieve":
            return StoryDetailSerializer
        return StorySerializer

    def get_permissions(self):
        if self.action in ["create", "destroy", "destroy_media"]:
            return [IsPartner()]
        return [AllowAny()]

    @swagger_auto_schema(
        tags=["Stories"],
        operation_summary="Retrieve all stories(non-expired)",
        operation_description="For clients property_type is required; request without it returns 404.",
        manual_parameters=[
            openapi.Parameter(
                "property_type",
                openapi.IN_QUERY,
                description="Property type (apartment/cottage). Required for client/public requests.",
                type=openapi.TYPE_STRING,
                required=False,
            ),
        ],
        responses={
            status.HTTP_200_OK: StorySerializer(many=True),
            status.HTTP_404_NOT_FOUND: "property_type is required for client",
        },
    )
    def list(self, request, *args, **kwargs):
        property_type_raw = request.query_params.get("property_type")
        property_kind = parse_property_kind(property_type_raw)

        if not _is_partner(request.user) and not property_type_raw:
            raise NotFound(_("Parametrlar kerak. property_type yuboring."))

        if _is_partner(request.user):
            stories = list_active_stories(
                partner_user_id=request.user.id,
                public_only=False,
                property_kind=property_kind,
            )
        else:
            stories = list_active_stories(
                public_only=True,
                property_kind=property_kind,
            )

        serializer = StorySerializer(stories, many=True, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    @swagger_auto_schema(
        tags=["Story Media"],
        operation_summary="Retrieve a story(non-expired) media",
        operation_description="Retrieve a specific media from a story and count view for authenticated client",
        manual_parameters=[
            openapi.Parameter(
                "story_id",
                openapi.IN_PATH,
                description="Unique story GUID",
                type=openapi.TYPE_STRING,
                format=openapi.FORMAT_UUID,
            ),
            openapi.Parameter(
                "media_id",
                openapi.IN_PATH,
                description="Unique media GUID",
                type=openapi.TYPE_STRING,
                format=openapi.FORMAT_UUID,
            ),
        ],
        responses={
            status.HTTP_200_OK: StoryDetailSerializer,
            status.HTTP_404_NOT_FOUND: "Story not found",
        },
    )
    def retrieve_media(self, request, story_id=None, media_id=None):
        story = get_story_by_guid(story_id, active_only=True)
        if not story:
            raise NotFound("Story not found")

        if _is_partner(request.user):
            if not story.get("is_verified") and int(story.get("partner_user_id") or 0) != request.user.id:
                raise NotFound("Story not found")
        else:
            if not story.get("is_verified"):
                raise NotFound("Story not found")

        media = get_story_media_by_guid(int(story["id"]), media_id)
        if not media:
            raise NotFound("Media not found")

        if _is_client(request.user):
            viewer_key = f"story:{story['guid']}:viewer:{request.user.id}"
            if cache.add(viewer_key, 1, timeout=48 * 60 * 60):
                views_key = f"story:{story['guid']}:views"
                if cache.get(views_key) is None:
                    cache.set(views_key, 0)
                cache.incr(views_key)

        serializer = StoryDetailSerializer(
            story,
            context={"request": request, "media_id": media_id},
        )
        return Response(status=status.HTTP_200_OK, data=serializer.data)

    @swagger_auto_schema(
        tags=["Stories"],
        operation_summary="Create a new story",
        operation_description="Create a new story, only partners can upload stories",
        request_body=StoryCreateSerializer,
        responses={
            status.HTTP_201_CREATED: StorySerializer,
            status.HTTP_400_BAD_REQUEST: "Bad request",
        },
    )
    def create(self, request, *args, **kwargs):
        serializer = StoryCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        story = serializer.save()
        return Response(
            StorySerializer(story, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    @swagger_auto_schema(
        tags=["Stories"],
        operation_summary="Delete all the stories entirely",
        operation_description="Delete all stories, only partners can delete their own stories",
        manual_parameters=[
            openapi.Parameter(
                "story_id",
                openapi.IN_PATH,
                description="Unique story GUID",
                type=openapi.TYPE_STRING,
                format=openapi.FORMAT_UUID,
            )
        ],
        responses={
            status.HTTP_204_NO_CONTENT: None,
            status.HTTP_404_NOT_FOUND: "Story not found",
        },
    )
    def destroy(self, request, *args, **kwargs):
        story_id = kwargs.get(self.lookup_url_kwarg)
        deleted = delete_story_for_partner(story_id, request.user.id)
        if not deleted:
            raise NotFound("Story not found")
        return Response(status=status.HTTP_204_NO_CONTENT)

    @swagger_auto_schema(
        tags=["Story Media"],
        operation_summary="Delete story media",
        operation_description="Delete a specific media from a story, only partners can delete their own stories",
        manual_parameters=[
            openapi.Parameter(
                "story_id",
                openapi.IN_PATH,
                description="Unique story GUID",
                type=openapi.TYPE_STRING,
                format=openapi.FORMAT_UUID,
                required=True,
            ),
            openapi.Parameter(
                "media_id",
                openapi.IN_PATH,
                description="Unique media GUID",
                type=openapi.TYPE_STRING,
                format=openapi.FORMAT_UUID,
                required=True,
            ),
        ],
    )
    def destroy_media(self, request, story_id=None, media_id=None):
        story = get_story_by_guid(story_id, active_only=True)
        if not story:
            raise NotFound("Story not found")

        owner_id = int(story.get("partner_user_id") or 0)
        if request.user.id != owner_id:
            if not story.get("is_verified"):
                raise NotFound("Story not found")
            raise PermissionDenied(_("You don't have permission to delete this story media"))

        deleted = delete_story_media(int(story["id"]), media_id)
        if not deleted:
            raise NotFound("Media not found")
        return Response(status=status.HTTP_204_NO_CONTENT)


class PartnerStoryListView(ListAPIView):
    serializer_class = StorySerializer
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    @swagger_auto_schema(
        tags=["Stories"],
        operation_summary="Partner's own stories",
        operation_description="Retrieve all stories created by the authenticated partner (including unverified)",
        responses={status.HTTP_200_OK: StorySerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        property_kind = parse_property_kind(request.query_params.get("property_type"))
        stories = list_active_stories(
            partner_user_id=request.user.id,
            public_only=False,
            property_kind=property_kind,
            exclude_archived=False,
        )
        serializer = self.get_serializer(stories, many=True, context={"request": request})
        return Response(serializer.data)


class PublicStoryListView(ListAPIView):
    serializer_class = StorySerializer
    permission_classes = [AllowAny]
    authentication_classes = [ClientJWTAuthentication]

    @swagger_auto_schema(
        tags=["Stories"],
        operation_summary="Public stories list",
        operation_description="List public stories. If property_type is provided, filters by type + includes platform news.",
        manual_parameters=[
            openapi.Parameter(
                "property_type",
                openapi.IN_QUERY,
                description="Property type (apartment/cottage). Optional.",
                type=openapi.TYPE_STRING,
                required=False,
            ),
        ],
        responses={
            status.HTTP_200_OK: StorySerializer(many=True),
        },
    )
    def get(self, request, *args, **kwargs):
        property_type_raw = request.query_params.get("property_type")

        if property_type_raw:
            property_kind = parse_property_kind(property_type_raw)
            stories = list_active_stories(
                public_only=True,
                property_kind=property_kind,
                include_news=True,
            )
        else:
            stories = list_active_stories(
                public_only=True,
                include_news=True,
            )

        serializer = self.get_serializer(stories, many=True, context={"request": request})
        return Response(serializer.data)


# ── Admin Platform News CRUD ─────────────────────────────────────────


class AdminNewsPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class AdminNewsListView(APIView):
    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]
    pagination_class = AdminNewsPagination

    @swagger_auto_schema(
        tags=["Admin - News"],
        operation_summary="List all platform news",
        operation_description="List all platform news with search and ordering. Admin only.",
        manual_parameters=[
            openapi.Parameter("search", openapi.IN_QUERY, description="Search by title, body, or GUID", type=openapi.TYPE_STRING, required=False),
            openapi.Parameter("ordering", openapi.IN_QUERY, description="Order by field (e.g. -created_at, views)", type=openapi.TYPE_STRING, required=False),
            openapi.Parameter("page", openapi.IN_QUERY, description="Page number", type=openapi.TYPE_INTEGER, required=False),
            openapi.Parameter("page_size", openapi.IN_QUERY, description="Items per page", type=openapi.TYPE_INTEGER, required=False),
        ],
        responses={status.HTTP_200_OK: AdminNewsSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        search = request.query_params.get("search")
        ordering = request.query_params.get("ordering")

        total = count_platform_news_for_admin(search=search)
        news_list = list_platform_news_for_admin(
            search=search,
            ordering=ordering,
            limit=max(total, 1),
            offset=0,
        )

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(news_list, request, view=self)
        serializer = AdminNewsSerializer(page, many=True, context={"request": request})
        return paginator.get_paginated_response(serializer.data)


class AdminNewsCreateView(APIView):
    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]
    parser_classes = [MultiPartParser, FormParser]

    @swagger_auto_schema(
        tags=["Admin - News"],
        operation_summary="Create platform news",
        operation_description="Create a new platform news article. Admin only.",
        request_body=AdminNewsCreateSerializer,
        responses={
            status.HTTP_201_CREATED: AdminNewsSerializer,
            status.HTTP_400_BAD_REQUEST: "Bad request",
        },
    )
    def post(self, request, *args, **kwargs):
        serializer = AdminNewsCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        news = serializer.save()
        return Response(
            AdminNewsSerializer(news, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class AdminNewsDetailView(APIView):
    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        tags=["Admin - News"],
        operation_summary="Get platform news by GUID",
        operation_description="Retrieve a single platform news article. Admin only.",
        responses={
            status.HTTP_200_OK: AdminNewsSerializer,
            status.HTTP_404_NOT_FOUND: "News not found",
        },
    )
    def get(self, request, news_guid, *args, **kwargs):
        try:
            news_guid = uuid.UUID(str(news_guid))
        except ValueError:
            return Response(
                {"detail": _("Invalid news GUID")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        news = get_platform_news_by_guid(news_guid)
        if not news:
            raise NotFound(_("News not found"))
        return Response(
            AdminNewsSerializer(news, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )


class AdminNewsUpdateView(APIView):
    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]
    parser_classes = [MultiPartParser, FormParser]

    @swagger_auto_schema(
        tags=["Admin - News"],
        operation_summary="Update platform news",
        operation_description="Update a platform news article. Admin only.",
        request_body=AdminNewsUpdateSerializer,
        responses={
            status.HTTP_200_OK: AdminNewsSerializer,
            status.HTTP_400_BAD_REQUEST: "Bad request",
            status.HTTP_404_NOT_FOUND: "News not found",
        },
    )
    def patch(self, request, news_guid, *args, **kwargs):
        try:
            news_guid = uuid.UUID(str(news_guid))
        except ValueError:
            return Response(
                {"detail": _("Invalid news GUID")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        news = get_platform_news_by_guid(news_guid)
        if not news:
            raise NotFound(_("News not found"))

        serializer = AdminNewsUpdateSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        updated = serializer.update(news, serializer.validated_data)
        return Response(
            AdminNewsSerializer(updated, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )


class AdminNewsDeleteView(APIView):
    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        tags=["Admin - News"],
        operation_summary="Delete platform news",
        operation_description="Delete a platform news article. Admin only.",
        responses={
            status.HTTP_204_NO_CONTENT: None,
            status.HTTP_404_NOT_FOUND: "News not found",
        },
    )
    def delete(self, request, news_guid, *args, **kwargs):
        try:
            news_guid = uuid.UUID(str(news_guid))
        except ValueError:
            return Response(
                {"detail": _("Invalid news GUID")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        deleted = delete_platform_news_by_guid(news_guid)
        if not deleted:
            raise NotFound(_("News not found"))
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── Admin Banner CRUD ────────────────────────────────────────────────


class AdminBannerPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class AdminBannerListView(APIView):
    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]
    pagination_class = AdminBannerPagination

    @swagger_auto_schema(
        tags=["Admin - Banners"],
        operation_summary="List all banners",
        manual_parameters=[
            openapi.Parameter("search", openapi.IN_QUERY, description="Search by html_source or GUID", type=openapi.TYPE_STRING, required=False),
            openapi.Parameter("ordering", openapi.IN_QUERY, description="Order by field (e.g. -created_at)", type=openapi.TYPE_STRING, required=False),
            openapi.Parameter("page", openapi.IN_QUERY, description="Page number", type=openapi.TYPE_INTEGER, required=False),
            openapi.Parameter("page_size", openapi.IN_QUERY, description="Items per page", type=openapi.TYPE_INTEGER, required=False),
        ],
        responses={status.HTTP_200_OK: AdminBannerSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        search = request.query_params.get("search")
        ordering = request.query_params.get("ordering")

        total = count_banners(search=search)
        banner_list = list_banners(
            search=search,
            ordering=ordering,
            limit=max(total, 1),
            offset=0,
        )

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(banner_list, request, view=self)
        serializer = AdminBannerSerializer(page, many=True, context={"request": request})
        return paginator.get_paginated_response(serializer.data)


class AdminBannerCreateView(APIView):
    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]
    parser_classes = [MultiPartParser, FormParser]

    @swagger_auto_schema(
        tags=["Admin - Banners"],
        operation_summary="Create a banner",
        request_body=AdminBannerCreateSerializer,
        responses={
            status.HTTP_201_CREATED: AdminBannerSerializer,
            status.HTTP_400_BAD_REQUEST: "Bad request",
        },
    )
    def post(self, request, *args, **kwargs):
        serializer = AdminBannerCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        banner = serializer.save()
        return Response(
            AdminBannerSerializer(banner, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class AdminBannerDetailView(APIView):
    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        tags=["Admin - Banners"],
        operation_summary="Get banner by GUID",
        responses={
            status.HTTP_200_OK: AdminBannerSerializer,
            status.HTTP_404_NOT_FOUND: "Banner not found",
        },
    )
    def get(self, request, banner_guid, *args, **kwargs):
        try:
            banner_guid = uuid.UUID(str(banner_guid))
        except ValueError:
            return Response(
                {"detail": _("Invalid banner GUID")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        banner = get_banner_by_guid(banner_guid)
        if not banner:
            raise NotFound(_("Banner not found"))
        return Response(
            AdminBannerSerializer(banner, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )


class AdminBannerUpdateView(APIView):
    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]
    parser_classes = [MultiPartParser, FormParser]

    @swagger_auto_schema(
        tags=["Admin - Banners"],
        operation_summary="Update a banner",
        request_body=AdminBannerUpdateSerializer,
        responses={
            status.HTTP_200_OK: AdminBannerSerializer,
            status.HTTP_400_BAD_REQUEST: "Bad request",
            status.HTTP_404_NOT_FOUND: "Banner not found",
        },
    )
    def patch(self, request, banner_guid, *args, **kwargs):
        try:
            banner_guid = uuid.UUID(str(banner_guid))
        except ValueError:
            return Response(
                {"detail": _("Invalid banner GUID")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        banner = get_banner_by_guid(banner_guid)
        if not banner:
            raise NotFound(_("Banner not found"))

        serializer = AdminBannerUpdateSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        updated = serializer.update(banner, serializer.validated_data)
        return Response(
            AdminBannerSerializer(updated, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )


class AdminBannerDeleteView(APIView):
    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        tags=["Admin - Banners"],
        operation_summary="Delete a banner",
        responses={
            status.HTTP_204_NO_CONTENT: None,
            status.HTTP_404_NOT_FOUND: "Banner not found",
        },
    )
    def delete(self, request, banner_guid, *args, **kwargs):
        try:
            banner_guid = uuid.UUID(str(banner_guid))
        except ValueError:
            return Response(
                {"detail": _("Invalid banner GUID")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        banner = get_banner_by_guid(banner_guid)
        if not banner:
            raise NotFound(_("Banner not found"))
        image_path = banner.get("image")
        if image_path:
            try:
                default_storage.delete(image_path)
            except Exception:
                pass
        delete_banner_by_guid(banner_guid)
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── Public Banner Endpoints ──────────────────────────────────────────


class PublicBannerListView(APIView):
    authentication_classes = [ClientJWTAuthentication]
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        tags=["Banners"],
        operation_summary="List public banners",
        responses={status.HTTP_200_OK: PublicBannerSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        banners = list_banners(limit=100, offset=0)
        serializer = PublicBannerSerializer(banners, many=True, context={"request": request})
        return Response(serializer.data)


class PublicBannerDetailView(APIView):
    authentication_classes = [ClientJWTAuthentication]
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        tags=["Banners"],
        operation_summary="Get banner by GUID",
        responses={
            status.HTTP_200_OK: PublicBannerSerializer,
            status.HTTP_404_NOT_FOUND: "Banner not found",
        },
    )
    def get(self, request, banner_guid, *args, **kwargs):
        try:
            banner_guid = uuid.UUID(str(banner_guid))
        except ValueError:
            return Response(
                {"detail": _("Invalid banner GUID")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        banner = get_banner_by_guid(banner_guid)
        if not banner:
            raise NotFound(_("Banner not found"))
        return Response(
            PublicBannerSerializer(banner, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )
