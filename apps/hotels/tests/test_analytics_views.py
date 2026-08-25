"""Company-scoped hotel analytics: who can see them, and the fallbacks."""
from __future__ import annotations

from unittest.mock import patch

from django.conf import settings

if not settings.configured:  # pragma: no cover - defensive, mirrors the suite
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from rest_framework.test import APIRequestFactory, force_authenticate

from apps.hotels.views import (
    HotelMonthlySummaryView,
    HotelRecommendationsView,
    HotelTopByBookingsView,
)

factory = APIRequestFactory()

COMPANY_ID = 42


class _B2BUser:
    is_authenticated = True

    def __init__(self, company_id=COMPANY_ID, user_id=1):
        self.company_id = company_id
        self.id = user_id


class _ClientUser:
    is_authenticated = True
    company_id = None
    id = 7


class TestNonB2BCallersAreRefused:
    def test_monthly_summary_requires_a_company(self):
        request = factory.get("/monthly-summary/")
        force_authenticate(request, user=_ClientUser())
        response = HotelMonthlySummaryView.as_view()(request)
        assert response.status_code == 400

    def test_top_by_bookings_requires_a_company(self):
        request = factory.get("/top-by-bookings/")
        force_authenticate(request, user=_ClientUser())
        response = HotelTopByBookingsView.as_view()(request)
        assert response.status_code == 400

    def test_recommendations_requires_a_company(self):
        request = factory.get("/recommendations/")
        force_authenticate(request, user=_ClientUser())
        response = HotelRecommendationsView.as_view()(request)
        assert response.status_code == 400


class TestMonthlySummary:
    def test_a_company_with_history_gets_its_numbers(self):
        request = factory.get("/monthly-summary/", {"year": 2026, "month": 8})
        force_authenticate(request, user=_B2BUser())
        summary = {
            "year": 2026, "month": 8, "month_spend": "3179523.38",
            "top_hotels": [{
                "hotel_id": 1969, "names": {"en": "Test Hotel"}, "name_en": "Test Hotel",
                "photos": [], "star_id": 4, "bookings_count": 1, "spend": "3179523.38",
            }],
        }
        with patch(
            "apps.hotels.views.repo.fetch_monthly_summary", return_value=summary
        ) as fetch:
            response = HotelMonthlySummaryView.as_view()(request)
        assert response.status_code == 200
        assert response.data["month_spend"] == "3179523.38"
        assert len(response.data["top_hotels"]) == 1
        fetch.assert_called_once_with(b2b_company_id=COMPANY_ID, year=2026, month=8)

    def test_defaults_to_the_current_month_when_unspecified(self):
        request = factory.get("/monthly-summary/")
        force_authenticate(request, user=_B2BUser())
        with patch(
            "apps.hotels.views.repo.fetch_monthly_summary",
            return_value={"year": 0, "month": 0, "month_spend": 0, "top_hotels": []},
        ) as fetch:
            HotelMonthlySummaryView.as_view()(request)
        assert fetch.call_args.kwargs["b2b_company_id"] == COMPANY_ID


class TestTopByBookings:
    def test_a_company_with_no_bookings_gets_an_empty_list_not_an_error(self):
        request = factory.get("/top-by-bookings/")
        force_authenticate(request, user=_B2BUser())
        with patch("apps.hotels.views.repo.fetch_top_hotels_by_bookings", return_value=[]):
            response = HotelTopByBookingsView.as_view()(request)
        assert response.status_code == 200
        assert response.data["results"] == []


class TestRecommendations:
    def test_a_company_with_history_is_recommended_within_its_own_cities(self):
        request = factory.get("/recommendations/")
        force_authenticate(request, user=_B2BUser())
        with (
            patch(
                "apps.hotels.views.repo.fetch_company_booking_cities", return_value=[90]
            ),
            patch(
                "apps.hotels.views.repo.fetch_company_active_hotel_ids", return_value=[1969]
            ),
            patch(
                "apps.hotels.views.repo.fetch_recommended_hotels", return_value=[]
            ) as fetch_recs,
        ):
            HotelRecommendationsView.as_view()(request)
        fetch_recs.assert_called_once_with(
            city_ids=[90], exclude_hotel_ids=[1969], limit=10
        )

    def test_a_new_company_with_no_history_falls_back_to_the_overall_catalogue(self):
        request = factory.get("/recommendations/")
        force_authenticate(request, user=_B2BUser())
        with (
            patch("apps.hotels.views.repo.fetch_company_booking_cities", return_value=[]),
            patch("apps.hotels.views.repo.fetch_company_active_hotel_ids", return_value=[]),
            patch(
                "apps.hotels.views.repo.fetch_recommended_hotels",
                return_value=[{"id": 1, "city_id": 90, "star_id": 5, "names": {}, "photos": [], "address": {}}],
            ) as fetch_recs,
        ):
            response = HotelRecommendationsView.as_view()(request)
        # No booking history -> city_ids=None, so fetch_recommended_hotels falls back
        # to the overall top-rated catalogue instead of returning nothing.
        fetch_recs.assert_called_once_with(city_ids=None, exclude_hotel_ids=[], limit=10)
        assert response.status_code == 200
        assert len(response.data["results"]) == 1
