from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase
from django.urls import resolve
from django.utils import timezone

from rest_framework.test import APIRequestFactory

from property.raw_repository import APARTMENT_TYPE_GUID, COTTAGE_TYPE_GUID, list_property_types, parse_property_kind
from property.raw_serializers import (
    RawPropertyDetailSerializer,
    RawPropertyListSerializer,
    _parse_int_maybe,
)


class PropertyRepositoryHelpersTests(SimpleTestCase):
    def test_parse_property_kind_supports_titles_and_guids(self):
        self.assertEqual(parse_property_kind("apartment"), "apartment")
        self.assertEqual(parse_property_kind("cottages"), "cottage")
        self.assertEqual(parse_property_kind(str(APARTMENT_TYPE_GUID)), "apartment")
        self.assertEqual(parse_property_kind(str(COTTAGE_TYPE_GUID)), "cottage")

    def test_parse_property_kind_returns_none_for_unknown(self):
        self.assertIsNone(parse_property_kind("unknown-kind"))

    def test_list_property_types_returns_two_types(self):
        rows = list_property_types()
        self.assertEqual(len(rows), 2)
        self.assertEqual(str(rows[0]["guid"]), str(APARTMENT_TYPE_GUID))
        self.assertEqual(str(rows[1]["guid"]), str(COTTAGE_TYPE_GUID))


class PropertySerializerTests(SimpleTestCase):
    @patch("property.raw_serializers.to_uzs", side_effect=lambda amount: amount * Decimal("12000"))
    @patch("property.raw_serializers.default_storage.url", return_value="/media/test.jpg")
    def test_property_list_serializer_converts_usd_price_and_marks_favorite(
        self,
        _mock_storage_url,
        _mock_to_uzs,
    ):
        guid = uuid4()
        row = {
            "guid": guid,
            "title": "Test property",
            "img": "test.jpg",
            "currency": "USD",
            "price": Decimal("10"),
            "latitude": "41.3",
            "longitude": "69.2",
            "country": "UZ",
            "city": "Tashkent",
            "region_id": 1,
            "district_id": 2,
            "average_rating": 4.7,
            "created_at": timezone.now(),
        }
        request = APIRequestFactory().get("/api/property/properties/")
        serializer = RawPropertyListSerializer(
            row,
            context={"request": request, "favorite_guids": [str(guid)]},
        )
        data = serializer.data

        self.assertEqual(data["title"], "Test property")
        self.assertTrue(data["is_favorite"])
        self.assertEqual(str(data["price"]), "120000.00")
        self.assertEqual(data["img"], "/media/test.jpg")

    @patch("property.raw_serializers.default_storage.url", return_value="/media/test.jpg")
    def test_property_detail_serializer_resolves_language_specific_description(self, _mock_url):
        row = {
            "guid": uuid4(),
            "title": "Detail property",
            "img": "test.jpg",
            "created_at": timezone.now(),
            "currency": "UZS",
            "price": Decimal("100000"),
            "minimum_weekend_day_stay": False,
            "review_count": 3,
            "average_rating": 4.5,
            "description_en": "English text",
            "description_ru": "Русский текст",
            "description_uz": "O'zbekcha matn",
            "latitude": "41.3",
            "longitude": "69.2",
            "country": "UZ",
            "city": "Tashkent",
            "apartment_number": None,
            "home_number": None,
            "entrance_number": None,
            "floor_number": None,
            "pass_code": None,
            "check_in": None,
            "check_out": None,
            "is_allowed_alcohol": False,
            "is_allowed_corporate": True,
            "is_allowed_pets": False,
            "is_quiet_hours": False,
        }
        request = type(
            "Req",
            (),
            {
                "query_params": {"lang": "ru"},
                "headers": {},
                "build_absolute_uri": staticmethod(lambda url: f"http://testserver{url}"),
            },
        )()
        serializer = RawPropertyDetailSerializer(row, context={"request": request})
        data = serializer.data

        self.assertEqual(data["description"], "Русский текст")
        self.assertEqual(data["comment_count"], 3)
        self.assertEqual(data["img"], "http://testserver/media/test.jpg")

    def test_parse_int_maybe_handles_invalid_values(self):
        self.assertEqual(_parse_int_maybe("42"), 42)
        self.assertIsNone(_parse_int_maybe("abc"))
        self.assertIsNone(_parse_int_maybe(None))


class PropertyUrlsTests(SimpleTestCase):
    def test_property_list_url_resolves(self):
        match = resolve("/api/property/properties/")
        self.assertEqual(match.func.view_class.__name__, "PropertyListCreateView")

    def test_property_types_url_resolves(self):
        match = resolve("/api/property/types/")
        self.assertEqual(match.func.view_class.__name__, "PropertyTypeListView")
