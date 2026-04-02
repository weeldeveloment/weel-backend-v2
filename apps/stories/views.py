from __future__ import annotations

from django.core.cache import cache
from django.utils.translation import gettext_lazy as _

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema

from rest_framework import status, viewsets
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.generics import ListAPIView
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from shared.permissions import IsPartner
from users.authentication import ClientJWTAuthentication, PartnerJWTAuthentication

from .raw_repository import (
    delete_story_for_partner,
    delete_story_media,
    get_story_by_guid,
    get_story_media_by_guid,
    list_active_stories,
    parse_property_kind,
)
from .serializers import StoryCreateSerializer, StoryDetailSerializer, StorySerializer


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
        operation_description="Request without parameters returns 404. property_type must be sent.",
        manual_parameters=[
            openapi.Parameter(
                "property_type",
                openapi.IN_QUERY,
                description="Property type (apartment/cottage)",
                type=openapi.TYPE_STRING,
                required=True,
            ),
        ],
        responses={
            status.HTTP_200_OK: StorySerializer(many=True),
            status.HTTP_404_NOT_FOUND: "Parameters not provided",
        },
    )
    def get(self, request, *args, **kwargs):
        property_type_raw = request.query_params.get("property_type")
        if not property_type_raw:
            raise NotFound(_("Parametrlar kerak. property_type yuboring."))

        property_kind = parse_property_kind(property_type_raw)
        stories = list_active_stories(
            public_only=True,
            property_kind=property_kind,
        )
        serializer = self.get_serializer(stories, many=True, context={"request": request})
        return Response(serializer.data)
