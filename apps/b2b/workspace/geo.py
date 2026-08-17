"""Great-circle distance, for checking a check-in against the office point.

Only ever compares two points a few hundred metres apart at most, so the
haversine formula's flat-earth error (worse over long distances) is not a
concern here.
"""
from __future__ import annotations

from math import asin, cos, radians, sin, sqrt

EARTH_RADIUS_METERS = 6_371_000


def distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lon1, lat2, lon2 = map(radians, (lat1, lon1, lat2, lon2))
    d_lat = lat2 - lat1
    d_lon = lon2 - lon1
    a = sin(d_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(d_lon / 2) ** 2
    return 2 * EARTH_RADIUS_METERS * asin(sqrt(a))
