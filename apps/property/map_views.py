"""Map search, filter metadata and search-page destination endpoints."""

from __future__ import annotations

import logging
import math
from decimal import Decimal
from typing import Any

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from shared.raw.db import fetch_all
from users.authentication import OptionalClientOrPartnerJWTAuthentication

from .apartment_repository import (
    PROPERTY_KIND_APARTMENT,
    PROPERTY_KIND_COTTAGE,
    PROPERTY_KIND_HOTEL,
    get_apartment_for_public,
    list_property_services,
    parse_property_kind,
    prepare_property_rows,
)
from .cottage_repository import get_cottage_for_public
from .hotel_repository import get_hotel_for_public
from .map_serializers import (
    DestinationSerializer,
    MapResponseSerializer,
    PriceHistogramSerializer,
    PropertyCardSerializer,
    build_map_pin,
    build_property_card,
    row_coordinates,
    row_price,
)
from .views import (
    PROPERTY_LIST_QUERY_PARAMS,
    TESTING_MODE_HEADER_PARAM,
    ApartmentPagination,
    _favorite_guids_from_request,
    _is_testing_mode_request,
    _list_apartment_rows,
    _list_cottage_rows,
    _list_hotel_rows,
    _parse_float,
    _parse_int,
    _preferred_language,
    _source_get,
    _source_get_list,
)


logger = logging.getLogger(__name__)

ALL_KINDS = (PROPERTY_KIND_APARTMENT, PROPERTY_KIND_COTTAGE, PROPERTY_KIND_HOTEL)

MAX_MAP_PINS = 600
DEFAULT_CLUSTER_MAX_ZOOM = 14
DEFAULT_HISTOGRAM_BUCKETS = 30

# A cluster cell should cover roughly 60 screen pixels at the requested zoom.
CLUSTER_CELL_PIXELS = 60
TILE_PIXELS = 256


MAP_QUERY_PARAMS = PROPERTY_LIST_QUERY_PARAMS + [
    openapi.Parameter(
        "sw_lat", openapi.IN_QUERY, type=openapi.TYPE_NUMBER, format="float",
        description="South-west corner latitude of the visible map viewport.",
    ),
    openapi.Parameter(
        "sw_lon", openapi.IN_QUERY, type=openapi.TYPE_NUMBER, format="float",
        description="South-west corner longitude of the visible map viewport.",
    ),
    openapi.Parameter(
        "ne_lat", openapi.IN_QUERY, type=openapi.TYPE_NUMBER, format="float",
        description="North-east corner latitude of the visible map viewport.",
    ),
    openapi.Parameter(
        "ne_lon", openapi.IN_QUERY, type=openapi.TYPE_NUMBER, format="float",
        description="North-east corner longitude of the visible map viewport.",
    ),
    openapi.Parameter(
        "bbox", openapi.IN_QUERY, type=openapi.TYPE_STRING,
        description="Viewport as `sw_lat,sw_lon,ne_lat,ne_lon`. Alternative to the four corner params.",
    ),
    openapi.Parameter(
        "zoom", openapi.IN_QUERY, type=openapi.TYPE_INTEGER,
        description=(
            "Current map zoom level (0–20). Results are clustered below "
            f"`cluster_max_zoom` (default {DEFAULT_CLUSTER_MAX_ZOOM}) and returned as individual pins above it."
        ),
    ),
    openapi.Parameter(
        "cluster_max_zoom", openapi.IN_QUERY, type=openapi.TYPE_INTEGER,
        description=f"Zoom level from which clustering is disabled. Default {DEFAULT_CLUSTER_MAX_ZOOM}.",
    ),
    openapi.Parameter(
        "property_types", openapi.IN_QUERY, type=openapi.TYPE_STRING,
        description="Comma-separated property kinds: `apartment,cottage,hotel`. Repeatable.",
    ),
]

FILTER_QUERY_PARAMS = [
    openapi.Parameter(
        "services", openapi.IN_QUERY, type=openapi.TYPE_STRING,
        description="Comma-separated amenity GUIDs (Удобства). Repeatable.",
    ),
    openapi.Parameter(
        "services_match", openapi.IN_QUERY, type=openapi.TYPE_STRING, enum=["all", "any"],
        description="`all` (default) requires every selected amenity, `any` requires at least one.",
    ),
    openapi.Parameter("bedrooms", openapi.IN_QUERY, type=openapi.TYPE_INTEGER, description="Minimum bedrooms (Спальни)."),
    openapi.Parameter("beds", openapi.IN_QUERY, type=openapi.TYPE_INTEGER, description="Minimum beds (Кровати)."),
    openapi.Parameter("bathrooms", openapi.IN_QUERY, type=openapi.TYPE_INTEGER, description="Minimum bathrooms (Ванные комнаты)."),
    openapi.Parameter("guests", openapi.IN_QUERY, type=openapi.TYPE_INTEGER, description="Minimum guest capacity (Кто)."),
    openapi.Parameter("allowed_pets", openapi.IN_QUERY, type=openapi.TYPE_BOOLEAN),
    openapi.Parameter("allowed_alcohol", openapi.IN_QUERY, type=openapi.TYPE_BOOLEAN),
    openapi.Parameter("min_stars", openapi.IN_QUERY, type=openapi.TYPE_INTEGER, description="Minimum hotel star rating."),
]


def _parse_kinds(source) -> list[str]:
    """Resolve the multi-select property type filter (Дом / Квартира / Отель)."""
    raw_values = _source_get_list(source, "property_types")
    if not raw_values:
        single = _source_get(source, "property_type") or _source_get(source, "kind")
        raw_values = [single] if single else []
    kinds: list[str] = []
    for raw in raw_values:
        kind = parse_property_kind(raw)
        if kind in ALL_KINDS and kind not in kinds:
            kinds.append(kind)
    return kinds or list(ALL_KINDS)


def _collect_rows(source, *, kinds: list[str], testing_only: bool | None) -> list[dict[str, Any]]:
    """Run the shared filter pipeline across every requested property kind."""
    rows: list[dict[str, Any]] = []
    loaders = {
        PROPERTY_KIND_APARTMENT: lambda: _list_apartment_rows(
            source, public_only=True, default_limit=None, testing_only=testing_only
        ),
        PROPERTY_KIND_COTTAGE: lambda: _list_cottage_rows(
            source, public_only=True, default_limit=None, testing_only=testing_only
        ),
        PROPERTY_KIND_HOTEL: lambda: _list_hotel_rows(
            source, default_limit=None, testing_only=testing_only
        ),
    }
    for kind in kinds:
        loader = loaders.get(kind)
        if loader is None:
            continue
        try:
            rows.extend(loader())
        except Exception:
            logger.warning("map row collection failed for kind=%s", kind, exc_info=True)
    return rows


def _cluster_cell_size(zoom: int) -> float:
    """Cluster cell width in degrees for the given zoom level."""
    zoom = max(0, min(int(zoom), 20))
    return (360.0 / (2 ** zoom)) * (CLUSTER_CELL_PIXELS / TILE_PIXELS)


def _cluster_rows(rows: list[dict[str, Any]], *, zoom: int, favorites: set[str]) -> tuple[list, list]:
    """Group nearby properties into grid cells; single-property cells stay pins."""
    cell_size = _cluster_cell_size(zoom)
    if cell_size <= 0:
        return [pin for pin in (build_map_pin(r, favorites=favorites) for r in rows) if pin], []

    cells: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for row in rows:
        coordinates = row_coordinates(row)
        if coordinates is None:
            continue
        latitude, longitude = coordinates
        key = (int(math.floor(latitude / cell_size)), int(math.floor(longitude / cell_size)))
        cells.setdefault(key, []).append(row)

    pins: list[dict[str, Any]] = []
    clusters: list[dict[str, Any]] = []
    for members in cells.values():
        if len(members) == 1:
            pin = build_map_pin(members[0], favorites=favorites)
            if pin:
                pins.append(pin)
            continue
        latitudes: list[float] = []
        longitudes: list[float] = []
        prices: list[Decimal] = []
        for member in members:
            coordinates = row_coordinates(member)
            if coordinates is None:
                continue
            latitudes.append(coordinates[0])
            longitudes.append(coordinates[1])
            price = row_price(member)
            if price is not None:
                prices.append(price)
        if not latitudes:
            continue
        clusters.append(
            {
                "latitude": sum(latitudes) / len(latitudes),
                "longitude": sum(longitudes) / len(longitudes),
                "count": len(members),
                "min_price": min(prices) if prices else None,
                "currency": "UZS",
            }
        )
    return pins, clusters


class PropertyMapView(APIView):
    """Pins and clusters for the map screen."""

    permission_classes = [AllowAny]
    authentication_classes = [OptionalClientOrPartnerJWTAuthentication]

    @swagger_auto_schema(
        operation_id="listPropertyMapPins",
        operation_summary="Map pins and clusters",
        operation_description=(
            "Returns lightweight map markers for the current viewport. Below `cluster_max_zoom` "
            "nearby properties are merged into clusters; above it every property is returned as a "
            "pin carrying its nightly price. Tap handling should fetch the card via "
            "`/property/map/cards/`. Accepts every filter supported by `/property/properties/`."
        ),
        tags=["Property / Public"],
        manual_parameters=MAP_QUERY_PARAMS + FILTER_QUERY_PARAMS + [TESTING_MODE_HEADER_PARAM],
        responses={200: MapResponseSerializer},
    )
    def get(self, request, *args, **kwargs):
        source = request.query_params
        kinds = _parse_kinds(source)
        rows = _collect_rows(
            source, kinds=kinds, testing_only=_is_testing_mode_request(request)
        )
        rows = [row for row in rows if row_coordinates(row) is not None]

        favorites = _favorite_guids_from_request(request)
        zoom = _parse_int(_source_get(source, "zoom"))
        cluster_max_zoom = _parse_int(_source_get(source, "cluster_max_zoom"))
        if cluster_max_zoom is None:
            cluster_max_zoom = DEFAULT_CLUSTER_MAX_ZOOM

        if zoom is not None and zoom < cluster_max_zoom:
            pins, clusters = _cluster_rows(rows, zoom=zoom, favorites=favorites)
        else:
            pins = [
                pin
                for pin in (build_map_pin(row, favorites=favorites) for row in rows)
                if pin
            ]
            clusters = []

        truncated = len(pins) > MAX_MAP_PINS
        return Response(
            {
                "total": len(rows),
                "truncated": truncated,
                "pins": pins[:MAX_MAP_PINS],
                "clusters": clusters,
            },
            status=status.HTTP_200_OK,
        )


class PropertyMapCardsView(APIView):
    """Card payloads for pins the user tapped on the map."""

    permission_classes = [AllowAny]
    authentication_classes = [OptionalClientOrPartnerJWTAuthentication]

    @swagger_auto_schema(
        operation_id="listPropertyMapCards",
        operation_summary="Property cards by GUID",
        operation_description=(
            "Returns the card payload (image, title, rating, nightly price, district line and "
            "review count) for up to 20 properties. Used when a map price pin is tapped. "
            "Unknown GUIDs are skipped silently."
        ),
        tags=["Property / Public"],
        manual_parameters=[
            openapi.Parameter(
                "guids", openapi.IN_QUERY, type=openapi.TYPE_STRING, required=True,
                description="Comma-separated property GUIDs (max 20). Repeatable.",
            ),
            openapi.Parameter(
                "from_date", openapi.IN_QUERY, type=openapi.TYPE_STRING, format="date",
                description="Reference date used to pick the seasonal price. Defaults to today.",
            ),
        ],
        responses={200: PropertyCardSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        guids = _source_get_list(request.query_params, "guids")[:20]
        if not guids:
            return Response([], status=status.HTTP_200_OK)

        favorites = _favorite_guids_from_request(request)
        rows: list[dict[str, Any]] = []
        for guid in guids:
            row = self._load_row(guid)
            if row is not None:
                rows.append(row)

        prepared = prepare_property_rows(
            rows,
            reference_date=_resolve_card_reference_date(request),
        )
        cards = [
            build_property_card(row, request=request, favorites=favorites)
            for row in prepared
        ]
        # Preserve the order the client asked for.
        by_guid = {card["guid"]: card for card in cards}
        ordered = [by_guid[guid] for guid in guids if guid in by_guid]
        return Response(ordered, status=status.HTTP_200_OK)

    @staticmethod
    def _load_row(guid: str) -> dict[str, Any] | None:
        for loader in (get_apartment_for_public, get_cottage_for_public, get_hotel_for_public):
            try:
                row = loader(guid)
            except Exception:
                continue
            if row:
                return row
        return None


def _resolve_card_reference_date(request):
    from .views import _resolve_reference_date

    return _resolve_reference_date(request.query_params.get("from_date"))


class PropertySearchView(APIView):
    """Paginated search results in the compact card shape."""

    permission_classes = [AllowAny]
    authentication_classes = [OptionalClientOrPartnerJWTAuthentication]
    pagination_class = ApartmentPagination

    @swagger_auto_schema(
        operation_id="searchProperties",
        operation_summary="Search properties (card list)",
        operation_description=(
            "Mixed apartment / cottage / hotel search returning the compact card payload used on "
            "the search results screen: image, title, rating, `от X / 1 чел · ночь`, district line "
            "and review count. Accepts the full filter set plus `property_types` multi-select."
        ),
        tags=["Property / Public"],
        manual_parameters=MAP_QUERY_PARAMS + FILTER_QUERY_PARAMS + [TESTING_MODE_HEADER_PARAM],
        responses={200: PropertyCardSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        source = request.query_params.copy()
        # Pagination owns the slicing; the repository limit would truncate first.
        source.pop("limit", None)

        rows = _collect_rows(
            source,
            kinds=_parse_kinds(source),
            testing_only=_is_testing_mode_request(request),
        )
        favorites = _favorite_guids_from_request(request)
        cards = [
            build_property_card(row, request=request, favorites=favorites) for row in rows
        ]

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(cards, request)
        if page is not None:
            return paginator.get_paginated_response(page)
        return Response(cards, status=status.HTTP_200_OK)


class PropertyFilterMetaView(APIView):
    """Everything the filter sheet needs to render itself."""

    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_id="getPropertyFilterMeta",
        operation_summary="Filter sheet metadata",
        operation_description=(
            "Returns the amenity list grouped by category, the selectable property types and the "
            "min/max bounds for the budget slider and the room steppers. Pass any active filters "
            "to scope the price bounds to the current result set."
        ),
        tags=["Property / Public"],
        manual_parameters=PROPERTY_LIST_QUERY_PARAMS + FILTER_QUERY_PARAMS,
        responses={200: openapi.Response("Filter metadata")},
    )
    def get(self, request, *args, **kwargs):
        language = _preferred_language(request)
        services = list_property_services(language=language)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for service in services:
            category = str(service.get("category_key") or "other")
            grouped.setdefault(category, []).append(
                {
                    "guid": str(service.get("guid")),
                    "title": service.get("title"),
                    "icon_url": service.get("icon_url"),
                }
            )

        source = request.query_params.copy()
        # The budget bounds describe the full range, not the current selection.
        source.pop("min_price", None)
        source.pop("max_price", None)
        rows = _collect_rows(
            source,
            kinds=_parse_kinds(source),
            testing_only=_is_testing_mode_request(request),
        )
        prices = [price for price in (row_price(row) for row in rows) if price is not None]

        return Response(
            {
                "property_types": [
                    {"kind": PROPERTY_KIND_COTTAGE, "title_ru": "Дом", "title_uz": "Dom / Dacha"},
                    {"kind": PROPERTY_KIND_APARTMENT, "title_ru": "Квартира", "title_uz": "Kvartira"},
                    {"kind": PROPERTY_KIND_HOTEL, "title_ru": "Гостиница", "title_uz": "Mehmonxona"},
                ],
                "amenities": [
                    {"category_key": category, "items": items}
                    for category, items in grouped.items()
                ],
                "budget": {
                    "currency": "UZS",
                    "min_price": min(prices) if prices else None,
                    "max_price": max(prices) if prices else None,
                },
                "rooms": {
                    "bedrooms": {"min": 0, "max": 10},
                    "beds": {"min": 0, "max": 20},
                    "bathrooms": {"min": 0, "max": 10},
                },
                "total": len(rows),
            },
            status=status.HTTP_200_OK,
        )


class PropertyPriceHistogramView(APIView):
    """Bucketed price distribution behind the budget slider."""

    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_id="getPropertyPriceHistogram",
        operation_summary="Budget slider histogram",
        operation_description=(
            "Returns the nightly-price distribution for the current filter selection as equal-width "
            "buckets, so the filter sheet can draw the bar chart above the budget slider. "
            "`min_price`/`max_price` are ignored when building the buckets so the chart keeps its "
            "full shape while the handles move."
        ),
        tags=["Property / Public"],
        manual_parameters=PROPERTY_LIST_QUERY_PARAMS + FILTER_QUERY_PARAMS + [
            openapi.Parameter(
                "buckets", openapi.IN_QUERY, type=openapi.TYPE_INTEGER,
                description=f"Number of histogram bars. Default {DEFAULT_HISTOGRAM_BUCKETS}, max 60.",
            ),
        ],
        responses={200: PriceHistogramSerializer},
    )
    def get(self, request, *args, **kwargs):
        source = request.query_params.copy()
        # The slider handles must not reshape the chart underneath them.
        source.pop("min_price", None)
        source.pop("max_price", None)

        rows = _collect_rows(
            source,
            kinds=_parse_kinds(source),
            testing_only=_is_testing_mode_request(request),
        )
        prices = sorted(
            float(price)
            for price in (row_price(row) for row in rows)
            if price is not None and float(price) > 0
        )
        bucket_count = _parse_int(_source_get(source, "buckets")) or DEFAULT_HISTOGRAM_BUCKETS
        bucket_count = max(1, min(bucket_count, 60))

        if not prices:
            return Response(
                {
                    "currency": "UZS",
                    "total": 0,
                    "min_price": None,
                    "max_price": None,
                    "buckets": [],
                },
                status=status.HTTP_200_OK,
            )

        low, high = prices[0], prices[-1]
        width = (high - low) / bucket_count if high > low else 0.0
        counts = [0] * bucket_count
        for price in prices:
            index = 0 if width == 0 else min(int((price - low) / width), bucket_count - 1)
            counts[index] += 1

        buckets = [
            {
                "min_price": round(low + width * index, 2),
                "max_price": round(low + width * (index + 1), 2) if width else round(high, 2),
                "count": count,
            }
            for index, count in enumerate(counts)
        ]
        return Response(
            {
                "currency": "UZS",
                "total": len(prices),
                "min_price": round(low, 2),
                "max_price": round(high, 2),
                "buckets": buckets,
            },
            status=status.HTTP_200_OK,
        )


class SearchDestinationsView(APIView):
    """The "Где?" destination picker on the search screen."""

    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_id="listSearchDestinations",
        operation_summary="Search destinations",
        operation_description=(
            "Powers the `Где?` sheet. Returns `nearby` places ordered by distance when `lat`/`lon` "
            "are supplied, and `recommended` destinations (regions and districts with the most "
            "listings) otherwise. `search` filters both lists by name."
        ),
        tags=["Property / Public"],
        manual_parameters=[
            openapi.Parameter("search", openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter("lat", openapi.IN_QUERY, type=openapi.TYPE_NUMBER, format="float"),
            openapi.Parameter("lon", openapi.IN_QUERY, type=openapi.TYPE_NUMBER, format="float"),
            openapi.Parameter("limit", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
        ],
        responses={
            200: openapi.Response(
                "Destinations",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "nearby": openapi.Schema(type=openapi.TYPE_ARRAY, items=openapi.Items(type=openapi.TYPE_OBJECT)),
                        "recommended": openapi.Schema(type=openapi.TYPE_ARRAY, items=openapi.Items(type=openapi.TYPE_OBJECT)),
                    },
                ),
            )
        },
    )
    def get(self, request, *args, **kwargs):
        search = str(request.query_params.get("search") or "").strip()
        latitude = _parse_float(request.query_params.get("lat"))
        longitude = _parse_float(request.query_params.get("lon"))
        limit = max(1, min(_parse_int(request.query_params.get("limit")) or 20, 50))
        language = _preferred_language(request)

        destinations = _load_destinations(language)
        if search:
            needle = search.lower()
            destinations = [
                item
                for item in destinations
                if needle in str(item.get("title") or "").lower()
                or needle in str(item.get("subtitle") or "").lower()
            ]

        nearby: list[dict[str, Any]] = []
        if latitude is not None and longitude is not None:
            for item in destinations:
                if item.get("latitude") is None or item.get("longitude") is None:
                    continue
                item = dict(item)
                item["distance_km"] = round(
                    _haversine_km(latitude, longitude, item["latitude"], item["longitude"]), 1
                )
                nearby.append(item)
            nearby.sort(key=lambda item: item["distance_km"])
            nearby = nearby[:limit]

        recommended = sorted(
            destinations, key=lambda item: item.get("property_count") or 0, reverse=True
        )[:limit]

        return Response(
            {
                "nearby": DestinationSerializer(nearby, many=True).data,
                "recommended": DestinationSerializer(recommended, many=True).data,
            },
            status=status.HTTP_200_OK,
        )


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return 6371.0 * 2.0 * math.asin(math.sqrt(a))


def _load_destinations(language: str) -> list[dict[str, Any]]:
    """Regions and districts with a listing count and an averaged centre point."""
    from .apartment_repository import (
        APARTMENT_TABLE,
        DISTRICT_TABLE,
        REGION_TABLE,
    )

    title_column = {
        "ru": "title_ru",
        "en": "title_en",
    }.get(language, "title_uz")

    try:
        region_rows = fetch_all(
            f"""
            SELECT
                r.id,
                r.guid,
                COALESCE(NULLIF(r.{title_column}, ''), NULLIF(r.title_uz, ''), NULLIF(r.title_ru, ''), NULLIF(r.title_en, '')) AS title,
                COUNT(a.id) AS property_count,
                AVG(a.latitude::float) AS latitude,
                AVG(a.longitude::float) AS longitude
            FROM {REGION_TABLE} r
            LEFT JOIN {APARTMENT_TABLE} a
                ON a.region_id = r.id
               AND COALESCE(a.is_verified, FALSE) = TRUE
               AND COALESCE(a.is_archived, FALSE) = FALSE
            GROUP BY r.id, r.guid, title
            ORDER BY property_count DESC, title
            """
        )
    except Exception:
        logger.warning("region destination lookup failed", exc_info=True)
        region_rows = []

    try:
        district_rows = fetch_all(
            f"""
            SELECT
                d.id,
                d.guid,
                COALESCE(NULLIF(d.{title_column}, ''), NULLIF(d.title_uz, ''), NULLIF(d.title_ru, ''), NULLIF(d.title_en, '')) AS title,
                COALESCE(NULLIF(r.{title_column}, ''), NULLIF(r.title_uz, ''), NULLIF(r.title_ru, ''), NULLIF(r.title_en, '')) AS region_title,
                COUNT(a.id) AS property_count,
                AVG(a.latitude::float) AS latitude,
                AVG(a.longitude::float) AS longitude
            FROM {DISTRICT_TABLE} d
            LEFT JOIN {REGION_TABLE} r ON r.id = d.region_id
            LEFT JOIN {APARTMENT_TABLE} a
                ON a.district_id = d.id
               AND COALESCE(a.is_verified, FALSE) = TRUE
               AND COALESCE(a.is_archived, FALSE) = FALSE
            GROUP BY d.id, d.guid, title, region_title
            ORDER BY property_count DESC, title
            """
        )
    except Exception:
        logger.warning("district destination lookup failed", exc_info=True)
        district_rows = []

    destinations: list[dict[str, Any]] = []
    for row in region_rows:
        destinations.append(
            {
                "id": str(row.get("guid") or row.get("id")),
                "type": "region",
                "title": row.get("title") or "",
                "subtitle": "",
                "latitude": _as_float(row.get("latitude")),
                "longitude": _as_float(row.get("longitude")),
                "property_count": int(row.get("property_count") or 0),
                "distance_km": None,
            }
        )
    for row in district_rows:
        destinations.append(
            {
                "id": str(row.get("guid") or row.get("id")),
                "type": "district",
                "title": row.get("title") or "",
                "subtitle": row.get("region_title") or "",
                "latitude": _as_float(row.get("latitude")),
                "longitude": _as_float(row.get("longitude")),
                "property_count": int(row.get("property_count") or 0),
                "distance_km": None,
            }
        )
    return destinations


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
