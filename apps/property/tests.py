from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase
from django.urls import resolve
from django.utils import timezone

from rest_framework.test import APIRequestFactory

from property.apartment_repository import APARTMENT_TYPE_GUID, COTTAGE_TYPE_GUID, list_property_types, parse_property_kind
from property.apartment_serializers import ApartmentCreateSerializer, ApartmentListSerializer, ApartmentDetailSerializer, _parse_int_maybe
from property.cottage_serializers import CottageCreateSerializer, CottageListSerializer, CottageDetailSerializer


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


class ApartmentSerializerTests(SimpleTestCase):
    @patch("property.apartment_serializers.to_uzs", side_effect=lambda amount: amount * Decimal("12000"))
    @patch("property.apartment_serializers.default_storage.url", return_value="/media/test.jpg")
    def test_apartment_list_serializer_converts_usd_price_and_marks_favorite(
        self, _mock_storage_url, _mock_to_uzs,
    ):
        guid = uuid4()
        row = {
            "guid": guid,
            "title": "Test apartment",
            "img": "test.jpg",
            "currency": "USD",
            "price": Decimal("10"),
            "property_kind": "apartment",
            "latitude": "41.3",
            "longitude": "69.2",
            "country": "UZ",
            "city": "Tashkent",
            "region_id": 1,
            "district_id": 2,
            "average_rating": 4.7,
            "created_at": timezone.now(),
        }
        request = APIRequestFactory().get("/api/property/apartments/")
        serializer = ApartmentListSerializer(row, context={"request": request, "favorite_guids": [str(guid)]})
        data = serializer.data

        self.assertEqual(data["title"], "Test apartment")
        self.assertTrue(data["is_favorite"])
        self.assertEqual(str(data["price"]), "120000.00")
        self.assertNotIn("price_per_person", data)
        self.assertNotIn("price_on_working_days", data)
        self.assertNotIn("price_on_weekends", data)
        self.assertEqual(data["region_id"], 1)
        self.assertEqual(data["district_id"], 2)
        self.assertNotIn("region", data)
        self.assertNotIn("district", data)
        self.assertEqual(data["latitude"], "41.3")
        self.assertEqual(data["longitude"], "69.2")
        self.assertNotIn("property_location", data)
        self.assertEqual(data["img"], ["http://testserver/media/test.jpg"])


class CottageSerializerTests(SimpleTestCase):
    @patch("property.cottage_serializers.to_uzs", side_effect=lambda amount: amount * Decimal("12000"))
    @patch("property.cottage_serializers.default_storage.url", return_value="/media/test.jpg")
    def test_cottage_list_serializer_has_three_prices(self, _mock_storage_url, _mock_to_uzs):
        guid = uuid4()
        row = {
            "guid": guid,
            "title": "Test cottage",
            "img": "test.jpg",
            "currency": "USD",
            "price_per_person": Decimal("5"),
            "price_on_working_days": Decimal("10"),
            "price_on_weekends": Decimal("15"),
            "property_kind": "cottage",
            "latitude": "41.3",
            "longitude": "69.2",
            "country": "UZ",
            "city": "Tashkent",
            "region_id": None,
            "district_id": None,
            "average_rating": 5.0,
            "created_at": timezone.now(),
        }
        request = APIRequestFactory().get("/api/property/cottages/")
        serializer = CottageListSerializer(row, context={"request": request, "favorite_guids": []})
        data = serializer.data

        self.assertEqual(str(data["price_per_person"]), "60000.00")
        self.assertEqual(str(data["price_on_working_days"]), "120000.00")
        self.assertEqual(str(data["price_on_weekends"]), "180000.00")
        self.assertNotIn("price", data)


class DetailSerializerTests(SimpleTestCase):
    @patch("property.apartment_serializers.default_storage.url", return_value="/media/test.jpg")
    def test_apartment_detail_uses_db_field_names(self, _mock_url):
        row = {
            "guid": uuid4(),
            "title": "Detail apartment",
            "img": "test.jpg",
            "created_at": timezone.now(),
            "currency": "UZS",
            "price": Decimal("100000"),
            "property_kind": "apartment",
            "comment_count": 3,
            "review_count": 3,
            "average_rating": 4.5,
            "description_en": "English text",
            "description_ru": "Русский текст",
            "description_uz": "O'zbekcha matn",
            "latitude": "41.3",
            "longitude": "69.2",
            "country": "UZ",
            "city": "Tashkent",
            "apartment_number": "12",
            "home_number": "5",
            "entrance_number": "2",
            "floor_number": "3",
            "pass_code": "1234",
            "check_in": None,
            "check_out": None,
            "is_allowed_alcohol": False,
            "is_allowed_corporate": True,
            "is_allowed_pets": False,
            "is_quiet_hours": False,
        }
        request = type("Req", (), {"build_absolute_uri": staticmethod(lambda url: f"http://testserver{url}")})()
        data = ApartmentDetailSerializer(row, context={"request": request}).data
        self.assertEqual(data["description_en"], "English text")
        self.assertEqual(data["description_ru"], "Русский текст")
        self.assertEqual(data["description_uz"], "O'zbekcha matn")
        self.assertEqual(data["comment_count"], 3)
        self.assertEqual(data["img"], ["http://testserver/media/test.jpg"])
        self.assertNotIn("property_room", data)

    @patch("property.apartment_serializers.default_storage.url", return_value="/media/test.jpg")
    def test_apartment_detail_defaults_uzbek_description(self, _mock_url):
        row = {
            "guid": uuid4(),
            "title": "Detail apartment",
            "img": "test.jpg",
            "created_at": timezone.now(),
            "currency": "UZS",
            "price": Decimal("100000"),
            "property_kind": "apartment",
            "review_count": 0,
            "average_rating": 5.0,
            "description_en": "English fallback",
            "description_ru": None,
            "description_uz": "",
            "latitude": "41.3",
            "longitude": "69.2",
            "country": "UZ",
            "city": "Tashkent",
            "apartment_number": "12",
            "home_number": "5",
            "entrance_number": "2",
            "floor_number": "3",
            "pass_code": "1234",
            "check_in": None,
            "check_out": None,
            "is_allowed_alcohol": False,
            "is_allowed_corporate": True,
            "is_allowed_pets": False,
            "is_quiet_hours": False,
        }
        request = type("Req", (), {"build_absolute_uri": staticmethod(lambda url: f"http://testserver{url}")})()
        data = ApartmentDetailSerializer(row, context={"request": request}).data
        self.assertEqual(data["description_uz"], "English fallback")

    @patch("property.cottage_serializers.default_storage.url", return_value="/media/cottage.jpg")
    def test_cottage_detail_has_three_prices(self, _mock_url):
        row = {
            "guid": uuid4(),
            "title": "Detail cottage",
            "img": "cottage.jpg",
            "created_at": timezone.now(),
            "currency": "UZS",
            "price_per_person": Decimal("200000"),
            "price_on_working_days": Decimal("1500000"),
            "price_on_weekends": Decimal("1500000"),
            "property_kind": "cottage",
            "review_count": 1,
            "average_rating": 5.0,
            "description_en": "Cottage English",
            "description_ru": "",
            "description_uz": "",
            "latitude": "41.3",
            "longitude": "69.2",
            "country": "UZ",
            "city": "Tashkent",
            "check_in": None,
            "check_out": None,
            "is_allowed_alcohol": False,
            "is_allowed_corporate": False,
            "is_allowed_pets": True,
            "is_quiet_hours": False,
        }
        request = type("Req", (), {
            "query_params": {"lang": "en"},
            "headers": {},
            "build_absolute_uri": staticmethod(lambda url: f"http://testserver{url}"),
        })()
        data = CottageDetailSerializer(row, context={"request": request}).data
        self.assertEqual(str(data["price_per_person"]), "200000.00")
        self.assertEqual(str(data["price_on_working_days"]), "1500000.00")
        self.assertNotIn("price", data)


class UtilTests(SimpleTestCase):
    def test_parse_int_maybe_handles_invalid_values(self):
        self.assertEqual(_parse_int_maybe("42"), 42)
        self.assertIsNone(_parse_int_maybe("abc"))
        self.assertIsNone(_parse_int_maybe(None))

    def test_apartment_create_serializer_prepares_blank_coordinates(self):
        serializer = ApartmentCreateSerializer(
            data={
                "title": "Test apartment",
                "latitude": "",
                "longitude": "",
                "country": "Uzbekistan",
                "city": "Tashkent",
                "apartment_number": "12",
                "home_number": "10",
                "entrance_number": "2",
                "floor_number": "3",
                "pass_code": "0000",
                "description_ru": "Test",
                "description_uz": "Test",
                "check_in": "14:00:00",
                "check_out": "12:00:00",
                "is_allowed_alcohol": False,
                "is_allowed_corporate": False,
                "is_allowed_pets": False,
                "is_quiet_hours": True,
                "guests": 2,
                "rooms": 1,
                "beds": 1,
                "bathrooms": 1,
            },
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        values = serializer.validated_data["values"]
        self.assertIsNone(values.get("latitude"))
        self.assertIsNone(values.get("longitude"))

    def test_cottage_create_serializer_normalizes_blank_location_coordinates(self):
        serializer = CottageCreateSerializer(
            data={
                "title": "Test cottage",
                "latitude": "",
                "longitude": "",
                "country": "Uzbekistan",
                "city": "Tashkent",
            },
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        normalized = serializer.validated_data["normalized_values"]
        self.assertIsNone(normalized["latitude"])
        self.assertIsNone(normalized["longitude"])

    def test_apartment_create_serializer_accepts_services(self):
        serializer = ApartmentCreateSerializer(
            data={
                "title": "Test apartment",
                "services": ["guid1", "guid2"],
                "apartment_number": "12",
                "home_number": "10",
                "entrance_number": "2",
                "floor_number": "3",
                "pass_code": "0000",
                "description_ru": "Test",
                "description_uz": "Test",
                "check_in": "14:00:00",
                "check_out": "12:00:00",
                "is_allowed_alcohol": False,
                "is_allowed_corporate": False,
                "is_allowed_pets": False,
                "is_quiet_hours": True,
                "guests": 2,
                "rooms": 1,
                "beds": 1,
                "bathrooms": 1,
            },
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        values = serializer.validated_data["values"]
        self.assertEqual(values["services"], ["guid1", "guid2"])

    def test_apartment_create_serializer_accepts_detail_fields(self):
        serializer = ApartmentCreateSerializer(
            data={
                "title": "Test apartment",
                "description_ru": "Русское описание",
                "description_uz": "O'zbek tavsifi",
                "description_en": "English description",
                "check_in": "19:00:00",
                "check_out": "17:00:00",
                "is_allowed_alcohol": True,
                "is_allowed_corporate": False,
                "is_allowed_pets": True,
                "is_quiet_hours": False,
                "apartment_number": "12",
                "home_number": "10",
                "entrance_number": "2",
                "floor_number": "3",
                "pass_code": "0000",
                "guests": 2,
                "rooms": 1,
                "beds": 1,
                "bathrooms": 1,
            },
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        values = serializer.validated_data["values"]
        self.assertEqual(values.get("description_ru"), "Русское описание")
        self.assertEqual(values.get("is_allowed_alcohol"), True)
        self.assertEqual(values.get("is_allowed_pets"), True)

    def test_cottage_update_serializer_processes_services(self):
        serializer = CottageCreateSerializer(
            data={
                "title": "Test cottage",
                "services": ["service1", "service2"],
                "latitude": "41.3",
                "longitude": "69.2",
                "country": "Uzbekistan",
                "city": "Tashkent",
            },
            context={"is_update": True},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        normalized = serializer.validated_data["normalized_values"]
        self.assertEqual(normalized["services"], ["service1", "service2"])


class PropertyUrlsTests(SimpleTestCase):
    def test_apartment_list_url_resolves(self):
        match = resolve("/api/property/apartments/")
        self.assertEqual(match.func.view_class.__name__, "ApartmentPropertyListCreateView")

    def test_cottage_list_url_resolves(self):
        match = resolve("/api/property/cottages/")
        self.assertEqual(match.func.view_class.__name__, "CottagePropertyListCreateView")

    def test_property_types_url_resolves(self):
        match = resolve("/api/property/types/")
        self.assertEqual(match.func.view_class.__name__, "PropertyTypeListView")

    def test_partner_properties_url_resolves(self):
        match = resolve("/api/property/partner/properties/")
        self.assertEqual(match.func.view_class.__name__, "PartnerPropertyListView")

    def test_partner_all_properties_url_resolves(self):
        match = resolve("/api/property/partner/all/")
        self.assertEqual(match.func.view_class.__name__, "PartnerAllPropertyListView")

    def test_admin_all_properties_url_resolves(self):
        match = resolve("/api/property/admin/properties/all/")
        self.assertEqual(match.func.view_class.__name__, "AdminAllPropertiesListView")

    def test_partner_property_analytics_url_resolves(self):
        match = resolve("/api/property/partner/properties/00000000-0000-0000-0000-000000000001/analytics/")
        self.assertEqual(match.func.view_class.__name__, "PartnerPropertyAnalyticsView")

    def test_partner_apartments_url_resolves(self):
        match = resolve("/api/property/partner/apartments/")
        self.assertEqual(match.func.view_class.__name__, "ApartmentPartnerPropertyListView")

    def test_partner_cottages_url_resolves(self):
        match = resolve("/api/property/partner/cottages/")
        self.assertEqual(match.func.view_class.__name__, "CottagePartnerPropertyListView")

    def test_apartment_detail_url_resolves(self):
        match = resolve("/api/property/apartments/00000000-0000-0000-0000-000000000001/")
        self.assertEqual(match.func.view_class.__name__, "PropertyRetrieveUpdateDestroyView")

    def test_cottage_detail_url_resolves(self):
        match = resolve("/api/property/cottages/00000000-0000-0000-0000-000000000001/")
        self.assertEqual(match.func.view_class.__name__, "PropertyRetrieveUpdateDestroyView")
