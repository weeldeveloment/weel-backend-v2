from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(
        USE_TZ=True,
        TIME_ZONE="UTC",
        REST_FRAMEWORK={},
    )

from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.views import B2BHotelBookingListCreateView


def _booking_payload():
    return {
        "trip_id": 7,
        "hotel_guid": "hotel-guid",
        "check_in": "2026-07-20",
        "check_out": "2026-07-22",
        "rooms": [{"room_id": 11, "employee_ids": [21]}],
    }


def _booking_response():
    return {
        "id": 101,
        "company_id": 55,
        "trip_id": 7,
        "tenant_schema": "tenant_hotel",
        "hotel_property_id": 3,
        "hotel_name": "Hotel",
        "check_in": date(2026, 7, 20),
        "check_out": date(2026, 7, 22),
        "status": "pending",
        "room_count": 1,
        "employee_count": 1,
        "requested_by": 9,
        "created_at": datetime(2026, 7, 18, tzinfo=timezone.utc),
        "rooms": [
            {
                "id": 501,
                "room_id": 11,
                "room_name": "Standard",
                "price_per_night": Decimal("100.00"),
                "total_price": Decimal("200.00"),
                "pms_booking_id": 701,
                "employees": [
                    {
                        "employee_id": 21,
                        "full_name": "Ali Valiyev",
                        "position": "Engineer",
                    }
                ],
            }
        ],
    }


def _post_booking(role):
    request = APIRequestFactory().post(
        "/api/b2b/hotels/bookings/",
        _booking_payload(),
        format="json",
    )
    user = SimpleNamespace(
        id=9,
        company_id=55,
        role=role,
        is_authenticated=True,
    )
    force_authenticate(request, user=user)
    return B2BHotelBookingListCreateView.as_view()(request)


@pytest.mark.parametrize("role", ["owner", "performer"])
def test_hotel_booking_post_allows_b2b_owner_and_performer(role):
    with patch(
        "apps.b2b.views.create_booking_request",
        return_value=_booking_response(),
    ) as create_booking_request:
        response = _post_booking(role)

    assert response.status_code == 201
    create_booking_request.assert_called_once()
    assert create_booking_request.call_args.kwargs["company_id"] == 55
    assert create_booking_request.call_args.kwargs["requested_by"] == 9
    assert create_booking_request.call_args.kwargs["data"]["trip_id"] == 7


@pytest.mark.parametrize("role", ["employee", None])
def test_hotel_booking_post_rejects_non_b2b_booking_roles(role):
    with patch("apps.b2b.views.create_booking_request") as create_booking_request:
        response = _post_booking(role)

    assert response.status_code == 403
    create_booking_request.assert_not_called()
