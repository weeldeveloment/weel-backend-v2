from __future__ import annotations

import uuid

from django.core.cache import cache
from django.core.files.storage import default_storage
from django.utils.translation import gettext_lazy as _

from rest_framework import serializers

from core import settings

from .raw_repository import (
    create_story_for_property,
    create_story_media,
    get_active_story_for_property,
    get_owned_property_by_guid,
    get_story_by_guid,
)


def _build_media_url(request, media_path: str | None) -> str | None:
    if not media_path:
        return None
    url = default_storage.url(media_path)
    if not request:
        return url
    return request.build_absolute_uri(url)


class StoryMediaSerializer(serializers.Serializer):
    guid = serializers.UUIDField()
    media_type = serializers.CharField()
    media_url = serializers.SerializerMethodField("get_media_url")

    def get_media_url(self, obj):
        request = self.context.get("request")
        media_path = obj.get("media") if isinstance(obj, dict) else getattr(obj, "media", None)
        return _build_media_url(request, media_path)


class StorySerializer(serializers.Serializer):
    guid = serializers.UUIDField()
    property_id = serializers.SerializerMethodField("get_property_id")
    property_title = serializers.SerializerMethodField("get_property_title")
    property_type_guid = serializers.SerializerMethodField("get_property_type_guid")
    property_image_url = serializers.SerializerMethodField("get_property_image_url")
    media = serializers.SerializerMethodField("get_media")

    def get_property_id(self, obj):
        return str(obj.get("property_guid")) if obj.get("property_guid") else None

    def get_property_title(self, obj):
        return obj.get("property_title")

    def get_property_type_guid(self, obj):
        # Normalized schema has no dedicated property_type GUID table.
        # Keep response field for backward compatibility with existing clients.
        return obj.get("property_type_label")

    def get_property_image_url(self, obj):
        request = self.context.get("request")
        return _build_media_url(request, obj.get("property_img"))

    def get_media(self, obj):
        media_items = obj.get("media") or []
        return StoryMediaSerializer(media_items, many=True, context=self.context).data


class StoryPropertySerializer(serializers.Serializer):
    guid = serializers.UUIDField(read_only=True)
    title = serializers.CharField(read_only=True)


class StoryDetailSerializer(serializers.Serializer):
    guid = serializers.UUIDField()
    property = serializers.SerializerMethodField("get_property")
    media = serializers.SerializerMethodField("get_media")
    views = serializers.SerializerMethodField("get_views")

    def get_property(self, obj):
        return {
            "guid": obj.get("property_guid"),
            "title": obj.get("property_title"),
        }

    def get_media(self, obj):
        media_id = str(self.context.get("media_id") or "").strip()
        media_items = obj.get("media") or []
        media = next((item for item in media_items if str(item.get("guid")) == media_id), None)
        if not media:
            raise serializers.ValidationError("Media not found")
        return StoryMediaSerializer(media, context=self.context).data

    def get_views(self, obj):
        cache_key = f"story:{obj.get('guid')}:views"
        count = int(cache.get(cache_key) or 0)
        base = int(obj.get("views") or 0)
        return base + count


class StoryCreateSerializer(serializers.Serializer):
    property_id = serializers.UUIDField(required=True)
    media_type = serializers.CharField(required=True)
    media_file = serializers.FileField(required=True)

    def validate_property_id(self, value):
        request = self.context["request"]
        partner = getattr(request, "user")

        property_row = get_owned_property_by_guid(
            partner_user_id=partner.id,
            property_guid=value,
        )
        if not property_row:
            raise serializers.ValidationError(_("Property not found"))

        self.context["resolved_property"] = property_row
        return value

    def validate(self, attrs):
        media_type = attrs["media_type"]
        media_file = attrs["media_file"]

        extension = media_file.name.split(".")[-1].lower()
        if media_type == "image":
            if extension not in settings.ALLOWED_PHOTO_EXTENSION:
                raise serializers.ValidationError(
                    {
                        "media_file": _(
                            "Invalid image format, allowed are: jpg, jpeg, png, heif, heic"
                        )
                    }
                )

            if media_file.size > settings.MAX_IMAGE_SIZE:
                raise serializers.ValidationError(
                    {"media_file": _("Image file too large, maximum size is 20MB")}
                )

        elif media_type == "video":
            if extension not in settings.ALLOWED_VIDEO_EXTENSION:
                raise serializers.ValidationError(
                    {
                        "media_file": _(
                            "Invalid video format, allowed are: mp4, mov, avi, mkv"
                        )
                    }
                )

            if media_file.size > settings.MAX_VIDEO_SIZE:
                raise serializers.ValidationError(
                    {"media_file": _("Video file too large, maximum size is 100MB")}
                )

        else:
            raise serializers.ValidationError({"media_type": _("Unsupported media type")})

        return attrs

    def create(self, validated_data):
        property_row = self.context.get("resolved_property")
        if not property_row:
            request = self.context["request"]
            partner = getattr(request, "user")
            property_row = get_owned_property_by_guid(
                partner_user_id=partner.id,
                property_guid=validated_data["property_id"],
            )
            if not property_row:
                raise serializers.ValidationError({"property_id": _("Property not found")})

        property_kind = property_row["property_kind"]
        property_pk = int(property_row["id"])
        story = get_active_story_for_property(property_kind, property_pk)
        if not story:
            story = create_story_for_property(property_kind, property_pk)

        media_type = validated_data["media_type"]
        media_file = validated_data["media_file"]
        extension = media_file.name.split(".")[-1].lower()
        filename = f"stories/{uuid.uuid4().hex}.{extension}"
        media_path = default_storage.save(filename, media_file)

        create_story_media(
            story_id=int(story["id"]),
            media_path=media_path,
            media_type=media_type,
        )

        story_with_details = get_story_by_guid(story["guid"], active_only=False)
        if not story_with_details:
            raise serializers.ValidationError({"property_id": _("Story not found")})
        return story_with_details

    def to_representation(self, instance):
        return StorySerializer(instance, context=self.context).data
