from __future__ import annotations

from rest_framework import serializers

from .raw_serializers import (
    RawAdminBookingClientSerializer,
    RawAdminBookingListSerializer,
    RawAdminBookingPriceSerializer,
    RawAdminBookingPropertySerializer,
    RawBookingPriceSerializer,
    RawCalendarDateSerializer,
    RawClientBookingCreateSerializer,
    RawClientBookingDetailSerializer,
    RawClientBookingHistoryDetailSerializer,
    RawClientBookingHistoryListSerializer,
    RawClientBookingListSerializer,
    RawClientBookingSerializer,
    RawPartnerBookingListSerializer,
    RawPartnerBookingSerializer,
    RawPropertyBookingHistoryDetailSerializer,
    RawPropertyBookingHistorySerializer,
    RawPropertyBookingSerializer,
    RawPropertyCalendarDateRangeSerializer,
    RawPropertyLocationBookingSerializer,
)


class CalendarDateSerializer(RawCalendarDateSerializer):
    pass


class PropertyCalendarDateSerializer(serializers.Serializer):
    guid = serializers.UUIDField(required=False, allow_null=True)
    calendar = CalendarDateSerializer(many=True)


class PropertyCalendarDateRangeSerializer(RawPropertyCalendarDateRangeSerializer):
    pass


class ClientBookingCreateSerializer(RawClientBookingCreateSerializer):
    pass


class PropertyLocationBookingSerializer(RawPropertyLocationBookingSerializer):
    pass


class PropertyBookingSerializer(RawPropertyBookingSerializer):
    pass


class ClientBookingSerializer(RawClientBookingSerializer):
    pass


class PartnerBookingSerializer(RawPartnerBookingSerializer):
    pass


class BookingPriceSerializer(RawBookingPriceSerializer):
    pass


class PartnerBookingListSerializer(RawPartnerBookingListSerializer):
    pass


class ClientBookingListSerializer(RawClientBookingListSerializer):
    pass


class ClientBookingDetailSerializer(RawClientBookingDetailSerializer):
    pass


class PropertyBookingHistorySerializer(RawPropertyBookingHistorySerializer):
    pass


class ClientBookingHistoryListSerializer(RawClientBookingHistoryListSerializer):
    pass


class PropertyBookingHistoryDetailSerializer(RawPropertyBookingHistoryDetailSerializer):
    pass


class ClientBookingHistoryDetailSerializer(RawClientBookingHistoryDetailSerializer):
    pass


class AdminBookingClientSerializer(RawAdminBookingClientSerializer):
    pass


class AdminBookingPropertySerializer(RawAdminBookingPropertySerializer):
    pass


class AdminBookingPriceSerializer(RawAdminBookingPriceSerializer):
    pass


class AdminBookingListSerializer(RawAdminBookingListSerializer):
    pass
