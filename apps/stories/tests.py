import logging
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.test import TestCase, override_settings
from django.urls import resolve
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from django.core.cache import cache

from rest_framework import status
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from property.models import (
    Property,
    PropertyType,
    PropertyDetail,
    PropertyRoom,
    PropertyLocation,
    VerificationStatus,
)
from stories.models import Story, StoryMedia, StoryView
from stories.serializers import (
    StorySerializer,
    StoryDetailSerializer,
    StoryCreateSerializer,
    StoryMediaSerializer,
)
from stories.views import (
    StoryViewSet,
    PartnerStoryListView,
    PublicStoryListView,
)
from users.models import Partner, Client

logging.getLogger("django.request").setLevel(logging.ERROR)


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────


def make_partner(**kwargs):
    defaults = {
        "first_name": "Test",
        "last_name": "Partner",
        "username": f"partner_{uuid.uuid4().hex[:8]}",
        "phone_number": f"+99890{uuid.uuid4().int % 10**7:07d}",
        "is_active": True,
    }
    defaults.update(kwargs)
    return Partner.objects.create(**defaults)


def make_client(**kwargs):
    defaults = {
        "first_name": "Test",
        "last_name": "Client",
        "phone_number": f"+99891{uuid.uuid4().int % 10**7:07d}",
        "is_active": True,
    }
    defaults.update(kwargs)
    return Client.objects.create(**defaults)


def make_property(partner=None, verified=True, **kwargs):
    partner = partner or make_partner()
    pt = PropertyType.objects.create(
        title_en="Apartment",
        title_ru="Квартира",
        title_uz="Kvartira",
    )
    loc = PropertyLocation.objects.create(
        latitude=Decimal("41.2995"),
        longitude=Decimal("69.2401"),
        city="Tashkent",
        country="Uzbekistan",
    )
    defaults = {
        "title": f"Property {uuid.uuid4().hex[:6]}",
        "price": "100.00",
        "currency": "USD",
        "property_type": pt,
        "property_location": loc,
        "partner": partner,
    }
    if verified:
        defaults["verification_status"] = VerificationStatus.ACCEPTED
    defaults.update(kwargs)
    prop = Property.objects.create(**defaults)
    PropertyDetail.objects.create(
        property=prop,
        description_en="Desc",
        description_ru="Описание",
        description_uz="Tavsif",
    )
    PropertyRoom.objects.create(
        property=prop,
        guests=2,
        rooms=1,
        beds=1,
        bathrooms=1,
    )
    return prop


# ──────────────────────────────────────────────
# URL tests
# ──────────────────────────────────────────────


class StoriesUrlTests(TestCase):
    def test_stories_list_resolves(self):
        match = resolve("/api/story/stories/")
        self.assertEqual(match.func.cls, StoryViewSet)
        self.assertEqual(match.url_name, "stories-list")

    def test_partner_stories_list_resolves(self):
        match = resolve("/api/story/partner/stories/")
        self.assertEqual(match.func.view_class, PartnerStoryListView)

    def test_public_stories_list_resolves(self):
        match = resolve("/api/story/public/stories/")
        self.assertEqual(match.func.view_class, PublicStoryListView)


# ──────────────────────────────────────────────
# Model tests
# ──────────────────────────────────────────────


class StoryModelTests(TestCase):
    def test_story_save_sets_expires_at_if_not_set(self):
        prop = make_property()
        story = Story.objects.create(property=prop)
        self.assertIsNotNone(story.expires_at)
        self.assertGreater(story.expires_at, timezone.now())
        self.assertLessEqual(
            (story.expires_at - timezone.now()).total_seconds(),
            timedelta(hours=49).total_seconds(),
        )

    def test_story_save_keeps_expires_at_if_set(self):
        prop = make_property()
        custom = timezone.now() + timedelta(hours=1)
        story = Story.objects.create(property=prop, expires_at=custom)
        story.refresh_from_db()
        self.assertAlmostEqual(
            story.expires_at.timestamp(),
            custom.timestamp(),
            delta=1,
        )

    def test_story_is_expired_false_when_future(self):
        prop = make_property()
        story = Story.objects.create(
            property=prop,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        self.assertFalse(story.is_expired())

    def test_story_is_expired_true_when_past(self):
        prop = make_property()
        story = Story.objects.create(
            property=prop,
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        self.assertTrue(story.is_expired())

    def test_story_str(self):
        prop = make_property()
        story = Story.objects.create(property=prop)
        self.assertIn(str(story.guid), str(story))
        self.assertIn(prop.title, str(story))


@override_settings(MEDIA_ROOT="/tmp/weel-test-media")
class StoryMediaModelTests(TestCase):
    def _video_file(self, name="test.mp4"):
        return SimpleUploadedFile(
            name,
            b"x" * 100,
            content_type="video/mp4",
        )

    @patch("stories.models.story_image_compress")
    @patch("stories.models.story_video_compress")
    def test_story_media_str(self, mock_video_compress, mock_image_compress):
        mock_video_compress.return_value = self._video_file("out.mp4")
        prop = make_property()
        story = Story.objects.create(property=prop)
        media = StoryMedia.objects.create(
            story=story,
            media=self._video_file(),
            media_type=StoryMedia.MediaType.VIDEO,
        )
        self.assertIn(str(media.guid), str(media))
        self.assertIn("Media", str(media))

    def test_story_media_type_choices(self):
        self.assertEqual(StoryMedia.MediaType.IMAGE.value, "image")
        self.assertEqual(StoryMedia.MediaType.VIDEO.value, "video")


class StoryViewModelTests(TestCase):
    def test_unique_story_client_constraint_one_view_per_client(self):
        prop = make_property()
        story = Story.objects.create(property=prop)
        client = make_client()
        obj1, created1 = StoryView.objects.get_or_create(story=story, client=client)
        obj2, created2 = StoryView.objects.get_or_create(story=story, client=client)
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(obj1.id, obj2.id)
        self.assertEqual(StoryView.objects.filter(story=story, client=client).count(), 1)


# ──────────────────────────────────────────────
# Serializer tests
# ──────────────────────────────────────────────


class StorySerializerTests(TestCase):
    def test_story_serializer_fields(self):
        prop = make_property()
        story = Story.objects.create(property=prop, is_verified=True)
        request = APIRequestFactory().get("/")
        serializer = StorySerializer(
            story,
            context={"request": request},
        )
        data = serializer.data
        self.assertEqual(data["guid"], str(story.guid))
        self.assertEqual(data["property_id"], str(prop.guid))
        self.assertEqual(data["property_title"], prop.title)
        self.assertEqual(data["property_type_guid"], str(prop.property_type.guid))
        self.assertIn("property_image_url", data)
        self.assertIn("media", data)
        self.assertEqual(data["media"], [])


class StoryCreateSerializerTests(TestCase):
    def test_validate_property_id_not_found(self):
        partner = make_partner()
        request = APIRequestFactory().post("/")
        request.user = partner
        serializer = StoryCreateSerializer(
            data={
                "property_id": uuid.uuid4(),
                "media_type": "video",
                "media_file": SimpleUploadedFile("x.mp4", b"x", content_type="video/mp4"),
            },
            context={"request": request},
        )
        serializer.is_valid()
        self.assertIn("property_id", serializer.errors)

    def test_validate_property_id_not_owner(self):
        other_partner = make_partner()
        prop = make_property(partner=other_partner)
        partner = make_partner()
        request = APIRequestFactory().post("/")
        request.user = partner
        serializer = StoryCreateSerializer(
            data={
                "property_id": prop.guid,
                "media_type": "video",
                "media_file": SimpleUploadedFile("x.mp4", b"x", content_type="video/mp4"),
            },
            context={"request": request},
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("property_id", serializer.errors)

    def test_validate_media_type_unsupported(self):
        partner = make_partner()
        prop = make_property(partner=partner)
        request = APIRequestFactory().post("/")
        request.user = partner
        serializer = StoryCreateSerializer(
            data={
                "property_id": prop.guid,
                "media_type": "audio",
                "media_file": SimpleUploadedFile("x.mp4", b"x", content_type="video/mp4"),
            },
            context={"request": request},
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("media_type", serializer.errors)

    @override_settings(
        ALLOWED_VIDEO_EXTENSION=["mp4", "mov", "avi", "mkv"],
        MAX_VIDEO_SIZE=100 * 1024 * 1024,
    )
    def test_validate_video_extension_invalid(self):
        partner = make_partner()
        prop = make_property(partner=partner)
        request = APIRequestFactory().post("/")
        request.user = partner
        serializer = StoryCreateSerializer(
            data={
                "property_id": prop.guid,
                "media_type": "video",
                "media_file": SimpleUploadedFile(
                    "x.xyz",
                    b"x",
                    content_type="video/xyz",
                ),
            },
            context={"request": request},
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("media_file", serializer.errors)


# ──────────────────────────────────────────────
# API tests
# ──────────────────────────────────────────────


class PublicStoriesAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_public_stories_list_without_params_returns_404(self):
        response = self.client.get("/api/story/public/stories/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_public_stories_list_with_property_type_returns_200(self):
        prop = make_property()
        Story.objects.create(
            property=prop,
            expires_at=timezone.now() + timedelta(hours=1),
            is_verified=True,
        )
        response = self.client.get(
            "/api/story/public/stories/",
            {"property_type": str(prop.property_type.guid)},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)

    def test_public_stories_list_returns_only_verified_non_expired(self):
        prop = make_property()
        expired = Story.objects.create(
            property=prop,
            expires_at=timezone.now() - timedelta(seconds=1),
            is_verified=True,
        )
        future = Story.objects.create(
            property=prop,
            expires_at=timezone.now() + timedelta(hours=1),
            is_verified=True,
        )
        unverified = Story.objects.create(
            property=prop,
            expires_at=timezone.now() + timedelta(hours=1),
            is_verified=False,
        )
        response = self.client.get(
            "/api/story/public/stories/",
            {"property_type": str(prop.property_type.guid)},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        guids = [item["guid"] for item in response.data]
        self.assertIn(str(future.guid), guids)
        self.assertNotIn(str(expired.guid), guids)
        self.assertNotIn(str(unverified.guid), guids)

    def test_public_stories_list_filter_by_property_type(self):
        """Client dacha tanlasa faqat dacha istoriyalari, ..sanatorium tanlasa faqat ..sanatorium."""
        pt_dacha = PropertyType.objects.create(
            title_en="Dacha",
            title_ru="Дача",
            title_uz="Dacha",
        )
        pt_sanatorium = PropertyType.objects.create(
            title_en="Sanatorium",
            title_ru="Саноторий",
            title_uz="Sanatorium",
        )
        prop_dacha = make_property(property_type=pt_dacha)
        prop_sanatorium = make_property(property_type=pt_sanatorium)
        story_dacha = Story.objects.create(
            property=prop_dacha,
            expires_at=timezone.now() + timedelta(hours=1),
            is_verified=True,
        )
        story_sanatorium = Story.objects.create(
            property=prop_sanatorium,
            expires_at=timezone.now() + timedelta(hours=1),
            is_verified=True,
        )
        # Faqat dacha
        response = self.client.get(
            "/api/story/public/stories/",
            {"property_type": str(pt_dacha.guid)},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        guids = [item["guid"] for item in response.data]
        self.assertIn(str(story_dacha.guid), guids)
        self.assertNotIn(str(story_sanatorium.guid), guids)
        # Faqat ..sanatorium
        response = self.client.get(
            "/api/story/public/stories/",
            {"property_type": str(pt_sanatorium.guid)},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        guids = [item["guid"] for item in response.data]
        self.assertNotIn(str(story_dacha.guid), guids)
        self.assertIn(str(story_sanatorium.guid), guids)

    def test_public_stories_list_property_type_invalid_uuid_ignored(self):
        prop = make_property()
        story = Story.objects.create(
            property=prop,
            expires_at=timezone.now() + timedelta(hours=1),
            is_verified=True,
        )
        response = self.client.get(
            "/api/story/public/stories/",
            {"property_type": "not-a-uuid"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        guids = [item["guid"] for item in response.data]
        self.assertIn(str(story.guid), guids)


class PartnerStoriesAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_partner_stories_list_unauthenticated_returns_401_or_403(self):
        response = self.client.get("/api/story/partner/stories/")
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_partner_stories_list_authenticated_returns_200(self):
        partner = make_partner()
        prop = make_property(partner=partner)
        Story.objects.create(
            property=prop,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        request = APIRequestFactory().get("/api/story/partner/stories/")
        force_authenticate(request, user=partner)
        response = PartnerStoryListView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)


class StoryViewSetAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_story_list_unauthenticated_without_params_returns_404(self):
        response = self.client.get("/api/story/stories/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_story_list_unauthenticated_with_property_type_returns_200(self):
        prop = make_property()
        Story.objects.create(
            property=prop,
            expires_at=timezone.now() + timedelta(hours=1),
            is_verified=True,
        )
        response = self.client.get(
            "/api/story/stories/",
            {"property_type": str(prop.property_type.guid)},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)

    def test_story_create_unauthenticated_returns_401_or_403(self):
        prop = make_property()
        response = self.client.post(
            "/api/story/stories/",
            data={
                "property_id": str(prop.guid),
                "media_type": "video",
                "media_file": SimpleUploadedFile("x.mp4", b"x", content_type="video/mp4"),
            },
            format="multipart",
        )
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_story_destroy_unauthenticated_returns_401_or_403(self):
        prop = make_property()
        story = Story.objects.create(
            property=prop,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        response = self.client.delete(f"/api/story/stories/{story.guid}/")
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_story_destroy_partner_owner_returns_204(self):
        partner = make_partner()
        prop = make_property(partner=partner)
        story = Story.objects.create(
            property=prop,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        request = APIRequestFactory().delete(f"/api/story/stories/{story.guid}/")
        force_authenticate(request, user=partner)
        response = StoryViewSet.as_view({"delete": "destroy"})(
            request,
            story_id=str(story.guid),
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Story.objects.filter(guid=story.guid).exists())

    def test_story_destroy_partner_not_owner_returns_403_or_404(self):
        owner = make_partner()
        other = make_partner()
        prop = make_property(partner=owner)
        story = Story.objects.create(
            property=prop,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        request = APIRequestFactory().delete(f"/api/story/stories/{story.guid}/")
        force_authenticate(request, user=other)
        response = StoryViewSet.as_view({"delete": "destroy"})(
            request,
            story_id=str(story.guid),
        )
        self.assertIn(
            response.status_code,
            (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND),
            "Non-owner must get 403 or 404",
        )
        self.assertTrue(Story.objects.filter(guid=story.guid).exists())


# ──────────────────────────────────────────────
# Task tests
# ──────────────────────────────────────────────


class PersistStoryViewsTaskTests(TestCase):
    def tearDown(self):
        cache.clear()

    @patch("stories.tasks.Story.objects")
    def test_persist_story_views_updates_views_and_deletes_key(self, mock_story_qs):
        from stories.tasks import persist_story_views

        mock_filter = MagicMock()
        mock_update = MagicMock(return_value=1)
        mock_filter.update = mock_update
        mock_story_qs.filter.return_value = mock_filter

        cache.set("story:abc-uuid-here:views", 5)

        with patch("stories.tasks.cache") as mock_cache:
            mock_cache.iter_keys.return_value = ["story:abc-uuid-here:views"]
            mock_cache.get.side_effect = lambda k: 5 if k == "story:abc-uuid-here:views" else None

            persist_story_views()

        mock_filter.update.assert_called_once()
        mock_cache.delete.assert_called_with("story:abc-uuid-here:views")
