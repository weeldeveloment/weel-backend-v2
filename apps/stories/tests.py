from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase
from django.utils import timezone

from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from stories.raw_repository import parse_property_kind
from stories.serializers import StorySerializer
from stories.views import StoryViewSet


class StoryRepositoryHelpersTests(SimpleTestCase):
    def test_parse_property_kind_supports_aliases(self):
        self.assertEqual(parse_property_kind("apartment"), "apartment")
        self.assertEqual(parse_property_kind("apartments"), "apartment")
        self.assertEqual(parse_property_kind("dacha"), "cottage")
        self.assertEqual(parse_property_kind("cottages"), "cottage")

    def test_parse_property_kind_returns_none_for_unknown(self):
        self.assertIsNone(parse_property_kind("villa"))


class StorySerializerTests(SimpleTestCase):
    @patch("stories.serializers.default_storage.url", return_value="/media/property.jpg")
    def test_story_serializer_keeps_property_type_guid_field_for_compat(self, _mock_url):
        row = {
            "guid": uuid4(),
            "property_guid": uuid4(),
            "property_title": "Seaside cottage",
            "property_type_label": "Cottages",
            "property_img": "property.jpg",
            "media": [],
        }
        request = APIRequestFactory().get("/api/story/stories/")
        data = StorySerializer(row, context={"request": request}).data

        self.assertEqual(data["property_type_guid"], "Cottages")
        self.assertEqual(data["property_title"], "Seaside cottage")


class StoryViewSetTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    @patch("stories.views.parse_property_kind", return_value=None)
    def test_list_returns_404_when_property_type_missing_for_public(
        self,
        _mock_parse_kind,
    ):
        request = self.factory.get("/api/story/stories/")
        response = StoryViewSet.as_view({"get": "list"})(request)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @patch("stories.views.parse_property_kind", return_value="apartment")
    @patch("stories.views.list_active_stories")
    def test_partner_list_calls_repository_with_partner_scope(
        self,
        mock_list_active_stories,
        _mock_parse_kind,
    ):
        partner = SimpleNamespace(id=55, role="partner", is_active=True)
        mock_list_active_stories.return_value = []

        request = self.factory.get("/api/story/stories/?property_type=apartment")
        force_authenticate(request, user=partner, token="token")
        response = StoryViewSet.as_view({"get": "list"})(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_list_active_stories.assert_called_once_with(
            partner_user_id=55,
            public_only=False,
            property_kind="apartment",
        )

    @patch("stories.views.get_story_by_guid", return_value=None)
    def test_retrieve_media_returns_404_when_story_missing(self, _mock_get_story):
        request = self.factory.get("/api/story/stories/x/y/")
        response = StoryViewSet.as_view({"get": "retrieve_media"})(
            request,
            story_id=str(uuid4()),
            media_id=str(uuid4()),
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @patch("stories.views.delete_story_for_partner", return_value=1)
    def test_destroy_returns_204_on_successful_delete(self, mock_delete_story):
        partner = SimpleNamespace(id=7, role="partner", is_active=True)
        story_guid = str(uuid4())
        request = self.factory.delete(f"/api/story/stories/{story_guid}/")
        force_authenticate(request, user=partner, token="token")

        response = StoryViewSet.as_view({"delete": "destroy"})(
            request,
            story_id=story_guid,
        )

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        mock_delete_story.assert_called_once_with(story_guid, 7)

