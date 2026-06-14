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


def _build_media_url(request, media_path: str | list[str] | None) -> str | None:
    if not media_path:
        return None
    value = media_path
    if isinstance(media_path, list):
        value = next((item for item in media_path if item), None)
        if not value:
            return None
    url = default_storage.url(value)
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
    img = serializers.SerializerMethodField("get_img")
    media = serializers.SerializerMethodField("get_media")
    is_platform_news = serializers.SerializerMethodField("get_is_platform_news")
    title = serializers.SerializerMethodField("get_news_title")
    body = serializers.SerializerMethodField("get_news_body")

    def get_property_id(self, obj):
        return str(obj.get("property_guid")) if obj.get("property_guid") else None

    def get_property_title(self, obj):
        return obj.get("property_title")

    def get_property_type_guid(self, obj):
        return obj.get("property_type_label")

    def get_img(self, obj):
        request = self.context.get("request")
        return _build_media_url(request, obj.get("property_img"))

    def get_media(self, obj):
        media_items = obj.get("media") or []
        return StoryMediaSerializer(media_items, many=True, context=self.context).data

    def get_is_platform_news(self, obj):
        return bool(obj.get("is_platform_news", False))

    def get_news_title(self, obj):
        return obj.get("news_title") if obj.get("is_platform_news") else None

    def get_news_body(self, obj):
        return obj.get("news_body") if obj.get("is_platform_news") else None


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


class AdminStoryMediaSerializer(serializers.Serializer):
    guid = serializers.UUIDField()
    media_type = serializers.CharField()
    media_url = serializers.SerializerMethodField("get_media_url")

    def get_media_url(self, obj):
        request = self.context.get("request")
        media_path = obj.get("media") if isinstance(obj, dict) else getattr(obj, "media", None)
        return _build_media_url(request, media_path)


class AdminStorySerializer(serializers.Serializer):
    guid = serializers.UUIDField()
    property_id = serializers.SerializerMethodField("get_property_id")
    property_title = serializers.SerializerMethodField("get_property_title")
    property_kind = serializers.SerializerMethodField("get_property_kind")
    property_img = serializers.SerializerMethodField("get_property_img")
    partner_user_id = serializers.IntegerField(allow_null=True)
    is_verified = serializers.BooleanField()
    verified_by_user_id = serializers.IntegerField(allow_null=True)
    verified_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    expires_at = serializers.DateTimeField(allow_null=True)
    uploaded_at = serializers.DateTimeField(allow_null=True)
    views = serializers.IntegerField()
    media = serializers.SerializerMethodField("get_media")
    is_platform_news = serializers.SerializerMethodField("get_is_platform_news")
    title = serializers.SerializerMethodField("get_news_title")
    body = serializers.SerializerMethodField("get_news_body")

    def get_property_id(self, obj):
        return str(obj.get("property_guid")) if obj.get("property_guid") else None

    def get_property_title(self, obj):
        return obj.get("property_title")

    def get_property_kind(self, obj):
        return obj.get("property_kind")

    def get_property_img(self, obj):
        request = self.context.get("request")
        return _build_media_url(request, obj.get("property_img"))

    def get_media(self, obj):
        media_items = obj.get("media") or []
        return AdminStoryMediaSerializer(media_items, many=True, context=self.context).data

    def get_is_platform_news(self, obj):
        return bool(obj.get("is_platform_news", False))

    def get_news_title(self, obj):
        return obj.get("news_title") if obj.get("is_platform_news") else None

    def get_news_body(self, obj):
        return obj.get("news_body") if obj.get("is_platform_news") else None


class AdminStoryModerateSerializer(serializers.Serializer):
    is_verified = serializers.BooleanField(required=True)


class AdminNewsSerializer(serializers.Serializer):
    guid = serializers.UUIDField()
    title = serializers.SerializerMethodField("get_title")
    body = serializers.SerializerMethodField("get_body")
    is_verified = serializers.BooleanField()
    verified_by_user_id = serializers.IntegerField(allow_null=True)
    verified_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    uploaded_at = serializers.DateTimeField(allow_null=True)
    views = serializers.IntegerField()
    media = serializers.SerializerMethodField("get_media")

    def get_title(self, obj):
        return obj.get("news_title") or obj.get("title")

    def get_body(self, obj):
        return obj.get("news_body") or obj.get("body")

    def get_media(self, obj):
        media_items = obj.get("media") or []
        return AdminStoryMediaSerializer(media_items, many=True, context=self.context).data


class AdminNewsCreateSerializer(serializers.Serializer):
    title = serializers.CharField(required=True, max_length=500)
    body = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    media_type = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    media_file = serializers.FileField(required=False, allow_null=True)

    def validate(self, attrs):
        media_type = attrs.get("media_type")
        media_file = attrs.get("media_file")

        if media_file and not media_type:
            raise serializers.ValidationError(
                {"media_type": _("media_type is required when media_file is provided")}
            )

        if not media_file:
            return attrs

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
        from .raw_repository import add_news_media, create_platform_news, get_platform_news_by_guid

        news = create_platform_news(
            title=validated_data["title"],
            body=validated_data.get("body") or "",
            admin_user_id=self.context["request"].user.id,
        )

        media_file = validated_data.get("media_file")
        if media_file:
            media_type = validated_data["media_type"]
            extension = media_file.name.split(".")[-1].lower()
            filename = f"stories/{uuid.uuid4().hex}.{extension}"
            media_path = default_storage.save(filename, media_file)

            add_news_media(
                story_id=int(news["id"]),
                media_path=media_path,
                media_type=media_type,
            )

        result = get_platform_news_by_guid(news["guid"])
        if not result:
            raise serializers.ValidationError({"detail": _("News not found after creation")})
        return result

    def to_representation(self, instance):
        return AdminNewsSerializer(instance, context=self.context).data


class AdminNewsUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(required=False, max_length=500)
    body = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    media_type = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    media_file = serializers.FileField(required=False, allow_null=True)

    def validate(self, attrs):
        media_type = attrs.get("media_type")
        media_file = attrs.get("media_file")

        if media_file and not media_type:
            raise serializers.ValidationError(
                {"media_type": _("media_type is required when media_file is provided")}
            )

        if media_file:
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

    def update(self, instance, validated_data):
        from .raw_repository import add_news_media, update_platform_news

        news_guid = instance["guid"]

        news = update_platform_news(
            news_guid,
            title=validated_data.get("title"),
            body=validated_data.get("body"),
        )
        if not news:
            raise serializers.ValidationError({"detail": _("News not found")})

        media_file = validated_data.get("media_file")
        if media_file:
            media_type = validated_data["media_type"]
            extension = media_file.name.split(".")[-1].lower()
            filename = f"stories/{uuid.uuid4().hex}.{extension}"
            media_path = default_storage.save(filename, media_file)

            add_news_media(
                story_id=int(news["id"]),
                media_path=media_path,
                media_type=media_type,
            )

        from .raw_repository import get_platform_news_by_guid

        result = get_platform_news_by_guid(news_guid)
        if not result:
            raise serializers.ValidationError({"detail": _("News not found after update")})
        return result

    def to_representation(self, instance):
        return AdminNewsSerializer(instance, context=self.context).data


class AdminBannerSerializer(serializers.Serializer):
    guid = serializers.UUIDField()
    html_source = serializers.CharField()
    image = serializers.SerializerMethodField("get_image")
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()

    def get_image(self, obj):
        request = self.context.get("request")
        media_path = obj.get("image") if isinstance(obj, dict) else getattr(obj, "image", None)
        return _build_media_url(request, media_path)


class AdminBannerCreateSerializer(serializers.Serializer):
    html_source = serializers.CharField(required=True)
    image = serializers.FileField(required=True)

    def validate_image(self, value):
        extension = value.name.split(".")[-1].lower()
        if extension not in settings.ALLOWED_PHOTO_EXTENSION:
            raise serializers.ValidationError(
                _("Invalid image format, allowed are: jpg, jpeg, png, heif, heic")
            )
        if value.size > settings.MAX_IMAGE_SIZE:
            raise serializers.ValidationError(
                _("Image file too large, maximum size is 20MB")
            )
        return value

    def create(self, validated_data):
        from .raw_repository import create_banner, get_banner_by_guid

        image_file = validated_data["image"]
        extension = image_file.name.split(".")[-1].lower()
        filename = f"banners/{uuid.uuid4().hex}.{extension}"
        media_path = default_storage.save(filename, image_file)

        banner = create_banner(
            html_source=validated_data["html_source"],
            image=media_path,
        )

        result = get_banner_by_guid(banner["guid"])
        if not result:
            raise serializers.ValidationError({"detail": _("Banner not found after creation")})
        return result

    def to_representation(self, instance):
        return AdminBannerSerializer(instance, context=self.context).data


class AdminBannerUpdateSerializer(serializers.Serializer):
    html_source = serializers.CharField(required=False)
    image = serializers.FileField(required=False)

    def validate_image(self, value):
        extension = value.name.split(".")[-1].lower()
        if extension not in settings.ALLOWED_PHOTO_EXTENSION:
            raise serializers.ValidationError(
                _("Invalid image format, allowed are: jpg, jpeg, png, heif, heic")
            )
        if value.size > settings.MAX_IMAGE_SIZE:
            raise serializers.ValidationError(
                _("Image file too large, maximum size is 20MB")
            )
        return value

    def update(self, instance, validated_data):
        from .raw_repository import get_banner_by_guid, update_banner

        banner_guid = instance["guid"]
        html_source = validated_data.get("html_source")
        image = validated_data.get("image")

        image_path = instance.get("image")
        if image:
            if image_path:
                try:
                    default_storage.delete(image_path)
                except Exception:
                    pass
            extension = image.name.split(".")[-1].lower()
            filename = f"banners/{uuid.uuid4().hex}.{extension}"
            image_path = default_storage.save(filename, image)

        banner = update_banner(
            banner_guid,
            html_source=html_source,
            image=image_path,
        )
        if not banner:
            raise serializers.ValidationError({"detail": _("Banner not found")})

        result = get_banner_by_guid(banner_guid)
        if not result:
            raise serializers.ValidationError({"detail": _("Banner not found after update")})
        return result

    def to_representation(self, instance):
        return AdminBannerSerializer(instance, context=self.context).data


class PublicBannerSerializer(serializers.Serializer):
    guid = serializers.UUIDField()
    html_source = serializers.CharField()
    image = serializers.SerializerMethodField("get_image")
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()

    def get_image(self, obj):
        request = self.context.get("request")
        media_path = obj.get("image") if isinstance(obj, dict) else getattr(obj, "image", None)
        return _build_media_url(request, media_path)
