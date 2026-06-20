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
from property.apartment_serializers import ApartmentAdminUpdateSerializer, ApartmentCreateSerializer, ApartmentListSerializer, ApartmentDetailSerializer, _parse_int_maybe
from property.cottage_serializers import CottageCreateSerializer, CottageListSerializer, CottageDetailSerializer
from property.cottage_serializers import CottageAdminUpdateSerializer
from property.hotel_serializers import HotelAdminUpdateSerializer
from property.views import (
    ApartmentPropertyListCreateView,
    CottagePropertyListCreateView,
    HotelPropertyListView,
    PropertyListCreateView,
    RegionPropertyListView,
    UnifiedRecommendationsListView,
    _is_testing_mode_request,
    _public_cache_key,
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
        self.assertEqual(len(rows), 3)
        self.assertIn(str(APARTMENT_TYPE_GUID), {str(row["guid"]) for row in rows})
        self.assertIn(str(COTTAGE_TYPE_GUID), {str(row["guid"]) for row in rows})

    @patch("property.hotel_repository.list_hotel_organizations")
    @patch("property.hotel_repository._fetch_hotel_rows_for_schema")
    def test_list_hotels_filters_testing_rows(self, mock_fetch_rows, mock_orgs):
        from property.hotel_repository import list_hotels

        mock_orgs.return_value = [{"id": 1, "name": "Org", "slug": "org", "schema_name": "tenant1"}]
        mock_fetch_rows.return_value = [
            {"id": 1, "tenant_schema": "tenant1", "is_testing": True, "name": "Test Hotel"},
            {"id": 2, "tenant_schema": "tenant1", "is_testing": False, "name": "Live Hotel"},
        ]

        testing_rows = list_hotels(testing_only=True)
        live_rows = list_hotels(testing_only=False)

        self.assertEqual(len(testing_rows), 1)
        self.assertTrue(testing_rows[0]["is_testing"])
        self.assertEqual(len(live_rows), 1)
        self.assertFalse(live_rows[0]["is_testing"])


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
        self.assertIn("property_room", data)

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
        self.assertIsInstance(data.get("price"), list)
        self.assertEqual(len(data["price"]), 0)


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
                "services": [
                    "11111111-1111-1111-1111-111111111111",
                    "22222222-2222-2222-2222-222222222222",
                ],
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
        self.assertEqual(
            values["services"],
            [
                "11111111-1111-1111-1111-111111111111",
                "22222222-2222-2222-2222-222222222222",
            ],
        )

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
                "services": [
                    "11111111-1111-1111-1111-111111111111",
                    "22222222-2222-2222-2222-222222222222",
                ],
                "latitude": "41.3",
                "longitude": "69.2",
                "country": "Uzbekistan",
                "city": "Tashkent",
            },
            context={"is_update": True},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        normalized = serializer.validated_data["normalized_values"]
        self.assertEqual(
            [str(value) for value in normalized["services"]],
            [
                "11111111-1111-1111-1111-111111111111",
                "22222222-2222-2222-2222-222222222222",
            ],
        )

    def test_testing_mode_header_parser(self):
        request = APIRequestFactory().get("/api/property/apartments/", HTTP_X_TESTING_MODE="true")
        self.assertTrue(_is_testing_mode_request(request))
        request = APIRequestFactory().get("/api/property/apartments/", HTTP_X_TESTING_MODE="false")
        self.assertFalse(_is_testing_mode_request(request))

    def test_public_cache_key_varies_by_testing_mode(self):
        normal_request = APIRequestFactory().get("/api/property/properties/?search=test")
        testing_request = APIRequestFactory().get(
            "/api/property/properties/?search=test",
            HTTP_X_TESTING_MODE="true",
        )
        self.assertNotEqual(
            _public_cache_key(normal_request, "property:list"),
            _public_cache_key(testing_request, "property:list"),
        )


class AdminSerializerTestingFlagTests(SimpleTestCase):
    def test_apartment_admin_update_accepts_is_testing(self):
        serializer = ApartmentAdminUpdateSerializer(
            data={"title": "Admin apartment", "is_testing": True},
            partial=True,
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertTrue(serializer.validated_data["values"]["is_testing"])

    def test_cottage_admin_update_accepts_is_testing(self):
        serializer = CottageAdminUpdateSerializer(
            data={
                "title": "Admin cottage",
                "latitude": "41.3",
                "longitude": "69.2",
                "is_testing": True,
            },
            partial=True,
            context={"is_admin": True, "is_update": True},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertTrue(serializer.validated_data["normalized_values"]["is_testing"])

    def test_hotel_admin_update_accepts_is_testing(self):
        serializer = HotelAdminUpdateSerializer(
            data={"title": "Admin hotel", "is_testing": True},
            partial=True,
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertTrue(serializer.validated_data["values"]["is_testing"])

    def test_hotel_admin_update_maps_public_flag_names_and_ignores_tenant_schema_column(self):
        serializer = HotelAdminUpdateSerializer(
            data={
                "title": "Admin hotel",
                "tenant_schema": "tenant_c40d93034f48",
                "is_allowed_alcohol": False,
                "is_allowed_pets": True,
                "is_quiet_hours": True,
            },
            partial=True,
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        values = serializer.validated_data["values"]
        self.assertEqual(values["tenant_schema"], "tenant_c40d93034f48")
        self.assertEqual(values["alcohol_allowed"], False)
        self.assertEqual(values["pets_allowed"], True)
        self.assertEqual(values["quiet_hours"], True)


class PublicTestingModeViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    @patch("property.views._favorite_guids_from_request", return_value=set())
    @patch("property.views._track_client_search")
    @patch("property.views._list_apartment_rows", return_value=[])
    def test_apartment_public_list_passes_testing_only_true(
        self,
        mock_list_rows,
        _mock_track,
        _mock_favorites,
    ):
        request = self.factory.get("/api/property/apartments/", HTTP_X_TESTING_MODE="true")
        response = ApartmentPropertyListCreateView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(mock_list_rows.call_args.kwargs["testing_only"])

    @patch("property.views._favorite_guids_from_request", return_value=set())
    @patch("property.views._track_client_search")
    @patch("property.views._list_cottage_rows", return_value=[])
    def test_cottage_public_list_defaults_testing_only_false(
        self,
        mock_list_rows,
        _mock_track,
        _mock_favorites,
    ):
        request = self.factory.get("/api/property/cottages/")
        response = CottagePropertyListCreateView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(mock_list_rows.call_args.kwargs["testing_only"])

    @patch("property.views._track_client_search")
    def test_hotel_public_list_passes_testing_only_true(
        self,
        _mock_track,
    ):
        request = self.factory.get("/api/property/hotels/?page=2&limit=10", HTTP_X_TESTING_MODE="true")
        response = HotelPropertyListView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 0)
        self.assertEqual(response.data["results"], [])

    @patch("property.views._favorite_guids_from_request", return_value=set())
    @patch("property.views._list_cottage_rows", return_value=[])
    @patch("property.views._list_apartment_rows", return_value=[])
    def test_mixed_public_list_skips_hotels(
        self,
        mock_apartments,
        mock_cottages,
        _mock_favorites,
    ):
        request = self.factory.get("/api/property/properties/", HTTP_X_TESTING_MODE="true")
        response = PropertyListCreateView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(mock_apartments.call_args.kwargs["testing_only"])
        self.assertTrue(mock_cottages.call_args.kwargs["testing_only"])
        self.assertEqual(response.data, [])

    @patch("property.views._favorite_guids_from_request", return_value=set())
    @patch("property.views._list_apartment_rows")
    def test_properties_endpoint_paginates_single_kind_apartments(
        self,
        mock_list_rows,
        _mock_favorites,
    ):
        now = timezone.now()
        mock_list_rows.return_value = [
            {
                "guid": uuid4(),
                "title": f"Apartment {idx}",
                "img": [],
                "currency": "UZS",
                "price": Decimal("100000"),
                "property_kind": "apartment",
                "latitude": "41.3",
                "longitude": "69.2",
                "country": "UZ",
                "city": "Tashkent",
                "region_id": 1,
                "district_id": 2,
                "average_rating": 4.7,
                "created_at": now,
                "comment_count": 0,
                "services": [],
                "prefecture_id": None,
                "is_allowed_alcohol": False,
                "is_allowed_corporate": False,
                "is_allowed_pets": False,
                "is_quiet_hours": False,
                "guests": None,
                "rooms": None,
                "beds": None,
                "bathrooms": None,
            }
            for idx in range(3)
        ]

        request = self.factory.get(
            "/api/property/properties/?property_type=apartment&page=2&limit=1"
        )
        response = PropertyListCreateView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 3)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["title"], "Apartment 1")
        self.assertIsNone(mock_list_rows.call_args.kwargs["default_limit"])

    @patch("property.views._favorite_guids_from_request", return_value=set())
    def test_properties_endpoint_returns_empty_for_single_kind_hotels(
        self,
        _mock_favorites,
    ):
        request = self.factory.get(
            "/api/property/properties/?property_type=hotel&page=2&limit=2"
        )
        response = PropertyListCreateView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 0)
        self.assertEqual(response.data["results"], [])

    @patch("property.views._favorite_guids_from_request", return_value=set())
    @patch("property.views._list_apartment_rows", return_value=[])
    def test_properties_endpoint_returns_empty_page_for_out_of_range_apartments(
        self,
        _mock_list_rows,
        _mock_favorites,
    ):
        request = self.factory.get(
            "/api/property/properties/?property_type=apartment&page=2&limit=10"
        )
        response = PropertyListCreateView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 0)
        self.assertEqual(response.data["results"], [])

    @patch("property.views._favorite_guids_from_request", return_value=set())
    @patch("property.views._list_cottage_rows", return_value=[])
    def test_properties_endpoint_returns_empty_page_for_out_of_range_cottages(
        self,
        _mock_list_rows,
        _mock_favorites,
    ):
        request = self.factory.get(
            "/api/property/properties/?property_type=cottage&page=2&limit=10"
        )
        response = PropertyListCreateView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 0)
        self.assertEqual(response.data["results"], [])

    @patch("property.views._favorite_guids_from_request", return_value=set())
    @patch("property.views._list_cottage_rows", return_value=[])
    @patch("property.views._list_apartment_rows", return_value=[])
    def test_region_public_list_passes_testing_only_true(
        self,
        mock_apartments,
        mock_cottages,
        _mock_favorites,
    ):
        region_guid = "00000000-0000-0000-0000-000000000007"
        request = self.factory.get(
            f"/api/property/regions/{region_guid}/properties/",
            HTTP_X_TESTING_MODE="true",
        )
        response = RegionPropertyListView.as_view()(request, region_id=region_guid)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(mock_apartments.call_args.kwargs["testing_only"])
        self.assertTrue(mock_cottages.call_args.kwargs["testing_only"])

    @patch("property.views._get_or_set_cached_payload", side_effect=lambda request, cache_key, timeout, loader: loader())
    @patch("property.views.CottageListSerializer")
    @patch("property.views.ApartmentListSerializer")
    @patch("property.views._favorite_guids_from_request", return_value=set())
    @patch("property.views.fetch_all")
    @patch("property.views._list_cottage_rows", return_value=[])
    @patch("property.views._list_apartment_rows", return_value=[])
    def test_recommendations_skip_guaranteed_rows_in_testing_mode(
        self,
        mock_apartments,
        mock_cottages,
        mock_fetch_all,
        _mock_favorites,
        mock_apartment_serializer,
        mock_cottage_serializer,
        _mock_cache,
    ):
        mock_apartment_serializer.return_value.data = []
        mock_cottage_serializer.return_value.data = []
        request = self.factory.get("/api/property/recommendations/", HTTP_X_TESTING_MODE="true")
        response = UnifiedRecommendationsListView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        mock_fetch_all.assert_not_called()
        self.assertTrue(mock_apartments.call_args.kwargs["testing_only"])
        self.assertTrue(mock_cottages.call_args.kwargs["testing_only"])


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
        match = resolve("/api/property/admin/all/")
        self.assertEqual(match.func.view_class.__name__, "AdminAllPropertiesListView")

    def test_admin_cottage_detail_url_resolves(self):
        match = resolve("/api/property/admin/cottages/00000000-0000-0000-0000-000000000001/")
        self.assertEqual(match.func.view_class.__name__, "AdminCottagePatchView")

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
        self.assertEqual(match.func.view_class.__name__, "CottagePropertyRetrieveUpdateDestroyView")


class AdminDeleteCottageTests(SimpleTestCase):
    @patch("property.cottage_repository.execute")
    @patch("property.cottage_repository.fetch_all")
    @patch("property.cottage_repository.fetch_one")
    def test_admin_delete_cottage_deletes_dependents_then_cottage(
        self,
        mock_fetch_one,
        mock_fetch_all,
        mock_execute,
    ):
        from property.cottage_repository import admin_delete_cottage

        mock_fetch_one.return_value = {"id": 87}
        mock_fetch_all.return_value = [{"id": 101}, {"id": 102}]
        mock_execute.side_effect = [0, 0, 2, 3, 1]  # booking deps, bookings, calendar, cottage

        deleted = admin_delete_cottage(cottage_guid="ec649542-6e50-4252-8b4f-ab0f09de0d39")
        self.assertEqual(deleted, 1)

        sqls = [call.args[0].lower() for call in mock_execute.call_args_list]
        self.assertTrue(any("delete from" in sql and "booking" in sql for sql in sqls))
        self.assertTrue(any("delete from" in sql and "calendar" in sql for sql in sqls))
        self.assertTrue(any("delete from" in sql and "cottage" in sql for sql in sqls))
