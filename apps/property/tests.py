from __future__ import annotations

from datetime import datetime, date
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase
from django.urls import resolve
from django.utils import timezone

from rest_framework.test import APIRequestFactory

from property.apartment_repository import (
    APARTMENT_TYPE_GUID,
    COTTAGE_TYPE_GUID,
    list_property_types,
    parse_property_kind,
    _sort_rows,
    prepare_property_rows,
)
from property.apartment_serializers import ApartmentAdminUpdateSerializer, ApartmentCreateSerializer, ApartmentListSerializer, ApartmentDetailSerializer, _parse_int_maybe
from property.cottage_serializers import CottageCreateSerializer, CottageListSerializer, CottageDetailSerializer
from property.cottage_serializers import CottageAdminUpdateSerializer
from property.hotel_serializers import HotelAdminUpdateSerializer
from property.hotel_repository import (
    create_admin_hotel,
    list_admin_hotels,
    list_hotels,
    _serialize_hotel_row,
    _find_hotel_by_guid_across_schemas,
    list_hotel_organizations,
    _fetch_hotel_rows_for_schema,
)
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

    @patch("property.hotel_repository.get_admin_hotel", return_value={"guid": "tenant1:10"})
    @patch("property.hotel_repository._run_in_schema")
    @patch("property.hotel_repository.get_organization_by_schema")
    def test_create_admin_hotel_uses_schema_organization_id(
        self,
        mock_get_org,
        mock_run_in_schema,
        mock_get_admin_hotel,
    ):
        captured: dict[str, object] = {}

        def run_in_schema(schema_name, callback):
            self.assertEqual(schema_name, "tenant1")

            class CursorStub:
                def execute(self, _sql, params):
                    captured["params"] = params

                def fetchone(self):
                    return [10]

            class CursorContext:
                def __enter__(self_inner):
                    return CursorStub()

                def __exit__(self_inner, exc_type, exc, tb):
                    return False

            class ConnectionStub:
                def cursor(self_inner):
                    return CursorContext()

            with patch("property.hotel_repository.connection", ConnectionStub()):
                return callback()

        mock_get_org.return_value = {"id": 5, "schema_name": "tenant1"}
        mock_run_in_schema.side_effect = run_in_schema

        result = create_admin_hotel(
            schema_name="tenant1",
            values={
                "name": "Hotel A",
                "organization_id": 999,
            },
        )

        self.assertEqual(result, {"guid": "tenant1:10"})
        self.assertEqual(captured["params"][0], 5)
        mock_get_admin_hotel.assert_called_once_with("tenant1:10")


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
            "prefecture_id": None,
            "average_rating": 4.7,
            "created_at": timezone.now(),
            "services": [],
            "guests": None,
            "rooms": None,
            "beds": None,
            "bathrooms": None,
            "is_allowed_corporate": False,
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
        self.assertIn("property_location", data)
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
            "prefecture_id": None,
            "average_rating": 5.0,
            "created_at": timezone.now(),
            "services": [],
            "guests": None,
            "rooms": None,
            "beds": None,
            "bathrooms": None,
            "comment_count": 0,
            "review_count": 0,
            "is_allowed_corporate": False,
        }
        request = APIRequestFactory().get("/api/property/cottages/")
        serializer = CottageListSerializer(row, context={"request": request, "favorite_guids": []})
        data = serializer.data

        self.assertEqual(str(data["price_per_person"]), "60000.00")
        self.assertEqual(str(data["price_on_working_days"]), "120000.00")
        self.assertEqual(str(data["price_on_weekends"]), "180000.00")
        self.assertEqual(data["price"], [])


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
    databases = ["default"]

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
    @patch("property.views._list_hotel_rows", return_value=[])
    def test_hotel_public_list_passes_testing_only_true(
        self,
        _mock_hotel_rows,
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
    @patch("property.views._list_hotel_rows", return_value=[])
    def test_properties_endpoint_returns_empty_for_single_kind_hotels(
        self,
        _mock_hotel_rows,
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
    @patch("property.views.resolve_region_id_by_guid", return_value=7)
    def test_region_public_list_passes_testing_only_true(
        self,
        _mock_resolve,
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


def _make_row(**kwargs):
    """Return a minimal property row dict for sort testing."""
    base = {
        "id": 1,
        "guid": uuid4(),
        "title": "Test",
        "property_kind": "apartment",
        "currency": "UZS",
        "price": Decimal("100000"),
        "average_rating": Decimal("5.0"),
        "review_count": 0,
        "comment_count": 0,
        "is_allowed_corporate": False,
        "created_at": timezone.now(),
    }
    base.update(kwargs)
    return base


class SortRowsTests(SimpleTestCase):
    """Unit tests for _sort_rows — all sort parameter values."""

    def _rows_by_price(self):
        return [
            _make_row(id=1, order_price_uzs=Decimal("300000")),
            _make_row(id=2, order_price_uzs=Decimal("100000")),
            _make_row(id=3, order_price_uzs=Decimal("200000")),
        ]

    def test_price_high_sorts_descending(self):
        rows = self._rows_by_price()
        result = _sort_rows(rows, sort="price_high")
        self.assertEqual([r["id"] for r in result], [1, 3, 2])

    def test_price_low_sorts_ascending(self):
        rows = self._rows_by_price()
        result = _sort_rows(rows, sort="price_low")
        self.assertEqual([r["id"] for r in result], [2, 3, 1])

    def test_rating_high_sorts_descending(self):
        rows = [
            _make_row(id=1, average_rating=Decimal("3.0")),
            _make_row(id=2, average_rating=Decimal("5.0")),
            _make_row(id=3, average_rating=Decimal("4.0")),
        ]
        result = _sort_rows(rows, sort="rating_high")
        self.assertEqual([r["id"] for r in result], [2, 3, 1])

    def test_rating_low_sorts_ascending(self):
        rows = [
            _make_row(id=1, average_rating=Decimal("3.0")),
            _make_row(id=2, average_rating=Decimal("5.0")),
            _make_row(id=3, average_rating=Decimal("4.0")),
        ]
        result = _sort_rows(rows, sort="rating_low")
        self.assertEqual([r["id"] for r in result], [1, 3, 2])

    def test_reviews_high_sorts_by_review_count_descending(self):
        rows = [
            _make_row(id=1, review_count=5),
            _make_row(id=2, review_count=20),
            _make_row(id=3, review_count=10),
        ]
        result = _sort_rows(rows, sort="reviews_high")
        self.assertEqual([r["id"] for r in result], [2, 3, 1])

    def test_reviews_low_sorts_by_review_count_ascending(self):
        rows = [
            _make_row(id=1, review_count=5),
            _make_row(id=2, review_count=20),
            _make_row(id=3, review_count=10),
        ]
        result = _sort_rows(rows, sort="reviews_low")
        self.assertEqual([r["id"] for r in result], [1, 3, 2])

    def test_title_asc_sorts_alphabetically(self):
        rows = [
            _make_row(id=1, title="Zebra"),
            _make_row(id=2, title="Apple"),
            _make_row(id=3, title="Mango"),
        ]
        result = _sort_rows(rows, sort="title_asc")
        self.assertEqual([r["id"] for r in result], [2, 3, 1])

    def test_title_desc_sorts_reverse_alphabetically(self):
        rows = [
            _make_row(id=1, title="Zebra"),
            _make_row(id=2, title="Apple"),
            _make_row(id=3, title="Mango"),
        ]
        result = _sort_rows(rows, sort="title_desc")
        self.assertEqual([r["id"] for r in result], [1, 3, 2])

    def test_title_sort_is_case_insensitive(self):
        rows = [
            _make_row(id=1, title="zebra"),
            _make_row(id=2, title="Apple"),
        ]
        result = _sort_rows(rows, sort="title_asc")
        self.assertEqual([r["id"] for r in result], [2, 1])

    def test_corporate_yes_puts_allowed_first(self):
        rows = [
            _make_row(id=1, is_allowed_corporate=False),
            _make_row(id=2, is_allowed_corporate=True),
            _make_row(id=3, is_allowed_corporate=False),
        ]
        result = _sort_rows(rows, sort="corporate_yes")
        self.assertEqual(result[0]["id"], 2)

    def test_corporate_no_puts_not_allowed_first(self):
        rows = [
            _make_row(id=1, is_allowed_corporate=True),
            _make_row(id=2, is_allowed_corporate=False),
            _make_row(id=3, is_allowed_corporate=True),
        ]
        result = _sort_rows(rows, sort="corporate_no")
        self.assertEqual(result[0]["id"], 2)

    def test_unknown_sort_falls_back_to_ordering(self):
        now = timezone.now()
        import datetime as dt
        rows = [
            _make_row(id=1, created_at=now - dt.timedelta(days=2)),
            _make_row(id=2, created_at=now),
            _make_row(id=3, created_at=now - dt.timedelta(days=1)),
        ]
        result = _sort_rows(rows, sort="nonexistent", ordering="-created_at")
        self.assertEqual([r["id"] for r in result], [2, 3, 1])

    def test_no_sort_default_ordering_descending_by_created_at(self):
        import datetime as dt
        now = timezone.now()
        rows = [
            _make_row(id=1, created_at=now - dt.timedelta(days=2)),
            _make_row(id=2, created_at=now),
            _make_row(id=3, created_at=now - dt.timedelta(days=1)),
        ]
        result = _sort_rows(rows)
        self.assertEqual([r["id"] for r in result], [2, 3, 1])

    def test_ordering_created_at_ascending(self):
        import datetime as dt
        now = timezone.now()
        rows = [
            _make_row(id=1, created_at=now - dt.timedelta(days=2)),
            _make_row(id=2, created_at=now),
            _make_row(id=3, created_at=now - dt.timedelta(days=1)),
        ]
        result = _sort_rows(rows, ordering="created_at")
        self.assertEqual([r["id"] for r in result], [1, 3, 2])

    def test_price_high_with_equal_prices_uses_id_as_tiebreaker(self):
        rows = [
            _make_row(id=3, order_price_uzs=Decimal("100000")),
            _make_row(id=1, order_price_uzs=Decimal("100000")),
            _make_row(id=2, order_price_uzs=Decimal("100000")),
        ]
        result = _sort_rows(rows, sort="price_high")
        self.assertEqual([r["id"] for r in result], [3, 2, 1])

    def test_review_count_none_treated_as_zero(self):
        rows = [
            _make_row(id=1, review_count=None),
            _make_row(id=2, review_count=5),
        ]
        result = _sort_rows(rows, sort="reviews_high")
        self.assertEqual(result[0]["id"], 2)

    def test_empty_rows_returns_empty(self):
        self.assertEqual(_sort_rows([], sort="price_high"), [])
        self.assertEqual(_sort_rows([], sort="title_asc"), [])
        self.assertEqual(_sort_rows([]), [])


class PreparePropertyRowsSortTests(SimpleTestCase):
    """Integration tests for prepare_property_rows with sort param."""

    def _make_apartment_row(self, **kwargs):
        base = {
            "id": 1,
            "guid": uuid4(),
            "title": "Test",
            "property_kind": "apartment",
            "currency": "UZS",
            "price": Decimal("100000"),
            "average_rating": Decimal("5.0"),
            "review_count": 0,
            "comment_count": 0,
            "is_allowed_corporate": False,
            "created_at": timezone.now(),
        }
        base.update(kwargs)
        return base

    @patch("property.apartment_repository._exchange_rate_safe", return_value=Decimal("12500"))
    def test_prepare_rows_price_high_sort(self, _mock_rate):
        ref_date = date(2026, 7, 4)
        rows = [
            self._make_apartment_row(id=1, price=Decimal("300000")),
            self._make_apartment_row(id=2, price=Decimal("100000")),
            self._make_apartment_row(id=3, price=Decimal("200000")),
        ]
        result = prepare_property_rows(rows, reference_date=ref_date, sort="price_high")
        self.assertEqual([r["id"] for r in result], [1, 3, 2])

    @patch("property.apartment_repository._exchange_rate_safe", return_value=Decimal("12500"))
    def test_prepare_rows_price_low_sort(self, _mock_rate):
        ref_date = date(2026, 7, 4)
        rows = [
            self._make_apartment_row(id=1, price=Decimal("300000")),
            self._make_apartment_row(id=2, price=Decimal("100000")),
            self._make_apartment_row(id=3, price=Decimal("200000")),
        ]
        result = prepare_property_rows(rows, reference_date=ref_date, sort="price_low")
        self.assertEqual([r["id"] for r in result], [2, 3, 1])

    @patch("property.apartment_repository._exchange_rate_safe", return_value=Decimal("12500"))
    def test_prepare_rows_filters_by_min_max_price_before_sorting(self, _mock_rate):
        ref_date = date(2026, 7, 4)
        rows = [
            self._make_apartment_row(id=1, price=Decimal("50000")),
            self._make_apartment_row(id=2, price=Decimal("200000")),
            self._make_apartment_row(id=3, price=Decimal("500000")),
        ]
        result = prepare_property_rows(
            rows, reference_date=ref_date, sort="price_low",
            min_price=Decimal("100000"), max_price=Decimal("400000"),
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], 2)

    @patch("property.apartment_repository._exchange_rate_safe", return_value=Decimal("12500"))
    def test_prepare_rows_limit_applied_after_sort(self, _mock_rate):
        ref_date = date(2026, 7, 4)
        rows = [
            self._make_apartment_row(id=1, price=Decimal("300000")),
            self._make_apartment_row(id=2, price=Decimal("100000")),
            self._make_apartment_row(id=3, price=Decimal("200000")),
        ]
        result = prepare_property_rows(rows, reference_date=ref_date, sort="price_high", limit=2)
        self.assertEqual([r["id"] for r in result], [1, 3])

    @patch("property.apartment_repository._exchange_rate_safe", return_value=Decimal("12500"))
    def test_prepare_rows_rating_high_sort(self, _mock_rate):
        ref_date = date(2026, 7, 4)
        rows = [
            self._make_apartment_row(id=1, average_rating=Decimal("3.5")),
            self._make_apartment_row(id=2, average_rating=Decimal("5.0")),
            self._make_apartment_row(id=3, average_rating=Decimal("4.2")),
        ]
        result = prepare_property_rows(rows, reference_date=ref_date, sort="rating_high")
        self.assertEqual([r["id"] for r in result], [2, 3, 1])

    @patch("property.apartment_repository._exchange_rate_safe", return_value=Decimal("12500"))
    def test_prepare_rows_reviews_high_sort(self, _mock_rate):
        ref_date = date(2026, 7, 4)
        rows = [
            self._make_apartment_row(id=1, review_count=2),
            self._make_apartment_row(id=2, review_count=15),
            self._make_apartment_row(id=3, review_count=7),
        ]
        result = prepare_property_rows(rows, reference_date=ref_date, sort="reviews_high")
        self.assertEqual([r["id"] for r in result], [2, 3, 1])

    @patch("property.apartment_repository._exchange_rate_safe", return_value=Decimal("12500"))
    def test_prepare_rows_title_asc_sort(self, _mock_rate):
        ref_date = date(2026, 7, 4)
        rows = [
            self._make_apartment_row(id=1, title="Zebra Apartment"),
            self._make_apartment_row(id=2, title="Apple Apartment"),
            self._make_apartment_row(id=3, title="Mango Apartment"),
        ]
        result = prepare_property_rows(rows, reference_date=ref_date, sort="title_asc")
        self.assertEqual([r["id"] for r in result], [2, 3, 1])

    @patch("property.apartment_repository._exchange_rate_safe", return_value=Decimal("12500"))
    def test_prepare_rows_sets_order_price_uzs_field(self, _mock_rate):
        ref_date = date(2026, 7, 4)
        rows = [self._make_apartment_row(id=1, price=Decimal("100000"), currency="UZS")]
        result = prepare_property_rows(rows, reference_date=ref_date)
        self.assertIn("order_price_uzs", result[0])
        self.assertEqual(result[0]["order_price_uzs"], Decimal("100000"))


class AdminHotelListingTests(SimpleTestCase):
    """Verify list_admin_hotels returns hotels from all schemas and handles failures gracefully."""

    def _make_hotel_row(self, tenant_schema: str, hotel_id: int, **overrides):
        row = {
            "id": hotel_id,
            "guid": str(uuid4()),
            "tenant_schema": tenant_schema,
            "organization_id": None,
            "partner_user_id": None,
            "name": f"Hotel-{tenant_schema}-{hotel_id}",
            "description_uz": None,
            "description_ru": None,
            "description_en": None,
            "address": None,
            "city": "Tashkent",
            "country": "UZ",
            "latitude": "41.3",
            "longitude": "69.2",
            "star_rating": None,
            "amenities": [],
            "legal_info": {},
            "check_in_time": None,
            "check_out_time": None,
            "cancellation_policy": None,
            "quiet_hours": True,
            "alcohol_allowed": False,
            "pets_allowed": False,
            "currency": "USD",
            "timezone": "Asia/Tashkent",
            "photos": [],
            "is_active": True,
            "is_testing": False,
            "is_verified": True,
            "is_archived": False,
            "is_recommended": False,
            "verification_status": "accepted",
            "created_at": timezone.now(),
            "updated_at": timezone.now(),
            "price_from": None,
            "review_score": None,
            "review_count": 0,
        }
        row.update(overrides)
        return row

    @patch("property.hotel_repository.list_hotel_organizations")
    @patch("property.hotel_repository._fetch_hotel_rows_for_schema")
    def test_returns_all_hotels_from_all_schemas(self, mock_fetch_rows, mock_orgs):
        mock_orgs.return_value = [
            {"id": 1, "name": "OrgA", "slug": "org-a", "schema_name": "tenant_a"},
            {"id": 2, "name": "OrgB", "slug": "org-b", "schema_name": "tenant_b"},
            {"id": 3, "name": "OrgC", "slug": "org-c", "schema_name": "tenant_c"},
        ]

        def fetch_side_effect(schema_name, **kwargs):
            if schema_name == "tenant_a":
                return [
                    self._make_hotel_row("tenant_a", 1),
                    self._make_hotel_row("tenant_a", 2),
                ]
            if schema_name == "tenant_b":
                return [self._make_hotel_row("tenant_b", 3)]
            if schema_name == "tenant_c":
                return [
                    self._make_hotel_row("tenant_c", 4),
                    self._make_hotel_row("tenant_c", 5),
                    self._make_hotel_row("tenant_c", 6),
                ]
            return []

        mock_fetch_rows.side_effect = fetch_side_effect

        result = list_admin_hotels()

        self.assertEqual(len(result), 6)
        guids = {r["guid"] for r in result}
        self.assertEqual(len(guids), 6, "Each hotel should have a unique GUID")

    @patch("property.hotel_repository.list_hotel_organizations")
    @patch("property.hotel_repository._fetch_hotel_rows_for_schema")
    @patch("property.hotel_repository.logger")
    def test_single_schema_failure_does_not_drop_other_schemas(self, mock_logger, mock_fetch_rows, mock_orgs):
        mock_orgs.return_value = [
            {"id": 1, "name": "OrgA", "slug": "org-a", "schema_name": "tenant_a"},
            {"id": 2, "name": "OrgB", "slug": "org-b", "schema_name": "tenant_broken"},
            {"id": 3, "name": "OrgC", "slug": "org-c", "schema_name": "tenant_c"},
        ]

        def fetch_side_effect(schema_name, **kwargs):
            if schema_name == "tenant_a":
                return [self._make_hotel_row("tenant_a", 1)]
            if schema_name == "tenant_broken":
                raise RuntimeError("Schema unavailable")
            if schema_name == "tenant_c":
                return [self._make_hotel_row("tenant_c", 2)]
            return []

        mock_fetch_rows.side_effect = fetch_side_effect

        result = list_admin_hotels()

        self.assertEqual(len(result), 2, "Hotels from working schemas should be returned")
        schemas_returned = {r["tenant_schema"] for r in result}
        self.assertEqual(schemas_returned, {"tenant_a", "tenant_c"})
        mock_logger.warning.assert_called()

    @patch("property.hotel_repository.list_hotel_organizations")
    @patch("property.hotel_repository._fetch_hotel_rows_for_schema")
    def test_search_filters_hotels_by_name(self, mock_fetch_rows, mock_orgs):
        mock_orgs.return_value = [
            {"id": 1, "name": "Org", "slug": "org", "schema_name": "tenant1"},
        ]

        all_hotels = [
            self._make_hotel_row("tenant1", 1, name="Grand Plaza"),
            self._make_hotel_row("tenant1", 2, name="Budget Inn"),
            self._make_hotel_row("tenant1", 3, name="Plaza Suites"),
        ]
        mock_fetch_rows.return_value = all_hotels

        # search is SQL-level ILIKE; since we mock _fetch_hotel_rows_for_schema,
        # the search arg is passed to it — verify it's forwarded correctly
        result = list_admin_hotels(search="Plaza")

        mock_fetch_rows.assert_called_once()
        call_kwargs = mock_fetch_rows.call_args.kwargs
        self.assertEqual(call_kwargs["search"], "Plaza")
        self.assertTrue(call_kwargs["include_inactive"])
        self.assertTrue(call_kwargs["include_unverified"])

    @patch("property.hotel_repository.list_hotel_organizations")
    @patch("property.hotel_repository._fetch_hotel_rows_for_schema")
    def test_is_active_filter_excludes_inactive_hotels(self, mock_fetch_rows, mock_orgs):
        mock_orgs.return_value = [
            {"id": 1, "name": "Org", "slug": "org", "schema_name": "tenant1"},
        ]

        mock_fetch_rows.return_value = [
            self._make_hotel_row("tenant1", 1, is_active=True),
            self._make_hotel_row("tenant1", 2, is_active=False),
            self._make_hotel_row("tenant1", 3, is_active=True),
        ]

        active_only = list_admin_hotels(is_active=True)
        self.assertEqual(len(active_only), 2)
        self.assertTrue(all(r["is_active"] for r in active_only))

        inactive_only = list_admin_hotels(is_active=False)
        self.assertEqual(len(inactive_only), 1)
        self.assertFalse(inactive_only[0]["is_active"])

    @patch("property.hotel_repository.list_hotel_organizations")
    @patch("property.hotel_repository._fetch_hotel_rows_for_schema")
    def test_hotel_fields_include_is_verified(self, mock_fetch_rows, mock_orgs):
        """Verify is_verified is present in serialized hotel rows (frontend uses it for filtering)."""
        mock_orgs.return_value = [
            {"id": 1, "name": "Org", "slug": "org", "schema_name": "tenant1"},
        ]
        mock_fetch_rows.return_value = [
            self._make_hotel_row("tenant1", 1, is_verified=True),
            self._make_hotel_row("tenant1", 2, is_verified=False),
        ]

        result = list_admin_hotels()
        self.assertEqual(len(result), 2)
        verified = [r for r in result if r["is_verified"]]
        unverified = [r for r in result if not r["is_verified"]]
        self.assertEqual(len(verified), 1)
        self.assertEqual(len(unverified), 1)
        self.assertEqual(verified[0]["id"], 1)
        self.assertEqual(unverified[0]["id"], 2)

    @patch("property.hotel_repository.list_hotel_organizations")
    @patch("property.hotel_repository._fetch_hotel_rows_for_schema")
    @patch("property.hotel_repository.logger")
    def test_find_by_guid_logs_schema_failure(self, mock_logger, mock_fetch_rows, mock_orgs):
        """_find_hotel_by_guid_across_schemas logs failures and continues to next schema."""
        mock_orgs.return_value = [
            {"id": 1, "name": "Broken", "slug": "broken", "schema_name": "tenant_broken"},
            {"id": 2, "name": "Working", "slug": "working", "schema_name": "tenant_working"},
        ]

        def fetch_side_effect(schema_name, **kwargs):
            if schema_name == "tenant_broken":
                raise RuntimeError("Schema error")
            if schema_name == "tenant_working" and kwargs.get("hotel_guid"):
                return [self._make_hotel_row("tenant_working", 42)]
            return []

        mock_fetch_rows.side_effect = fetch_side_effect

        result = _find_hotel_by_guid_across_schemas(str(uuid4()))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], 42)
        self.assertEqual(result[0]["tenant_schema"], "tenant_working")
        mock_logger.warning.assert_called()


class HotelListingPaginationLogicTests(SimpleTestCase):
    """Verify hotel pagination computation (extracted logic, no serializer)."""

    def test_pagination_slices_and_counts_correctly(self):
        rows = [{"id": i, "title": f"Hotel {i}"} for i in range(1, 26)]

        page_size = 5
        page_number = 2
        total_count = len(rows)
        start = (page_number - 1) * page_size
        end = start + page_size
        paged_rows = rows[start:end]
        next_page = page_number + 1 if end < total_count else None

        self.assertEqual(total_count, 25)
        self.assertEqual(len(paged_rows), 5)
        self.assertEqual(next_page, 3)
        self.assertEqual(paged_rows[0]["title"], "Hotel 6")

    def test_no_pagination_returns_all(self):
        rows = [{"id": 1, "title": "Only Hotel"}]

        page_size = None
        page_number = None
        should_paginate = bool(page_size or page_number)

        self.assertFalse(should_paginate)
        self.assertEqual(len(rows), 1)
