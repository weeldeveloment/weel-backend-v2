"""TZ §9 — who may start a business trip (a hotel booking, on the workspace
side). `CanCreateTrip` is the only thing standing between `HotelBookingListCreateView.post`
and anybody signed in; these pin who it lets through.
"""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings

if not settings.configured:  # pragma: no cover - defensive, mirrors the suite
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.workspace.access import Module, Permission
from apps.b2b.workspace.authentication import WorkspaceUser
from apps.hotels.models import HotelBookingStatus
from apps.hotels.views import HotelBookingListCreateView

factory = APIRequestFactory()

COMPANY_ID = 42
CHECK_IN = date.today() + timedelta(days=10)
CHECK_OUT = CHECK_IN + timedelta(days=2)
GUID = str(uuid4())


class _ClientUser:
    """A consumer booking their own stay — never a `WorkspaceUser`."""

    is_authenticated = True
    company_id = None
    id = 7


def _employee(role: str) -> WorkspaceUser:
    return WorkspaceUser({
        "id": 1, "company_id": COMPANY_ID, "role": role, "full_name": "Test Person",
    })


def _access(modules, permissions):
    return patch(
        "apps.b2b.workspace.access_repository.access_for_employee",
        return_value=(modules, permissions),
    )


def _payload() -> dict:
    return {
        "quote_id": "931264bd-6d0c-4abb-9a4f-a6cfe5e8eb3e",
        "hotel_id": 1969,
        "check_in": CHECK_IN.isoformat(),
        "check_out": CHECK_OUT.isoformat(),
        "booking_rooms": [{
            "option_ref_id": "130|1020|7585|x",
            "price": "100500.00",
            "currency": "uzs",
            "guests": [{
                "person_title": "MR", "first_name": "Bruce", "last_name": "Wayne",
                "nationality": "uz",
            }],
        }],
    }


def _booking() -> dict:
    return {
        "id": 1, "guid": GUID, "external_id": "WEEL-ABC", "provider_booking_id": "24888",
        "hotel_id": 1969, "status": HotelBookingStatus.DRAFT,
        "client_user_id": None, "b2b_company_id": COMPANY_ID,
    }


def _post(user):
    request = factory.post("/bookings/", _payload(), format="json")
    force_authenticate(request, user=user)
    with (
        patch("apps.hotels.views.service.create_booking", return_value=_booking()),
        patch("apps.hotels.views.repo.fetch_booking_rooms", return_value=[]),
    ):
        return HotelBookingListCreateView.as_view()(request)


class TestTripCreatePermission:
    def test_an_employee_without_trip_create_is_refused(self):
        # The TZ default: employee opens Trips (read-only) but does not
        # start one unless the workspace grants it.
        with _access([Module.TRIPS], [Permission.TRIP_VIEW]):
            response = _post(_employee("employee"))
        assert response.status_code == 403

    def test_a_manager_may_start_a_trip(self):
        with _access([Module.TRIPS], [Permission.TRIP_VIEW, Permission.TRIP_CREATE]):
            response = _post(_employee("manager"))
        assert response.status_code == 201

    def test_an_owner_may_start_a_trip(self):
        with _access(Module.CHOICES, list(Permission.all())):
            response = _post(_employee("owner"))
        assert response.status_code == 201

    def test_a_guest_with_no_trips_module_is_refused(self):
        with _access([], []):
            response = _post(_employee("guest"))
        assert response.status_code == 403

    def test_an_employee_granted_trip_create_may_use_it(self):
        # A workspace can widen the default — the gate reads what was
        # actually granted, not the role's stock permissions.
        with _access([Module.TRIPS], [Permission.TRIP_VIEW, Permission.TRIP_CREATE]):
            response = _post(_employee("employee"))
        assert response.status_code == 201

    def test_a_consumer_booking_their_own_stay_is_unaffected(self):
        assert _post(_ClientUser()).status_code == 201
