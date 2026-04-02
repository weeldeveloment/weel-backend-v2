from django.contrib import admin
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

from unfold.admin import ModelAdmin, TabularInline

from .models import (
    MedicalSpecialization,
    Treatment,
    RoomType,
    PackageType,
    RoomAmenity,
    Sanatorium,
    SanatoriumImage,
    SanatoriumLocation,
    SanatoriumRoom,
    SanatoriumRoomImage,
    SanatoriumRoomPrice,
    RoomCalendarDate,
    SanatoriumReview,
    SanatoriumFavorite,
    SanatoriumBooking,
    SanatoriumBookingPrice,
    SanatoriumBookingTransaction,
)


def _localized_title(obj):
    lang = get_language()
    return getattr(obj, f"title_{lang}", obj.title_ru)


# ──────────────────────────────────────────────
# Lookup tables
# ──────────────────────────────────────────────


@admin.register(MedicalSpecialization)
class MedicalSpecializationAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_display = ["guid", "get_title", "created_at"]

    def get_title(self, obj):
        return _localized_title(obj)

    get_title.short_description = _("Title")


@admin.register(Treatment)
class TreatmentAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_display = ["guid", "get_title", "created_at"]

    def get_title(self, obj):
        return _localized_title(obj)

    get_title.short_description = _("Title")


@admin.register(RoomType)
class RoomTypeAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_display = ["guid", "get_title", "created_at"]

    def get_title(self, obj):
        return _localized_title(obj)

    get_title.short_description = _("Title")


@admin.register(PackageType)
class PackageTypeAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_display = ["guid", "get_title", "duration_days", "created_at"]

    def get_title(self, obj):
        return _localized_title(obj)

    get_title.short_description = _("Title")


@admin.register(RoomAmenity)
class RoomAmenityAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_display = ["guid", "get_title", "created_at"]

    def get_title(self, obj):
        return _localized_title(obj)

    get_title.short_description = _("Title")


# ──────────────────────────────────────────────
# Sanatorium
# ──────────────────────────────────────────────


class SanatoriumImageInline(TabularInline):
    model = SanatoriumImage
    extra = 1
    fields = ("image", "order", "is_pending")


@admin.register(SanatoriumLocation)
class SanatoriumLocationAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_display = ["guid", "city", "country", "latitude", "longitude"]


@admin.register(Sanatorium)
class SanatoriumAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_display = [
        "guid",
        "title",
        "verification_status",
        "is_verified",
        "partner",
        "comment_count",
        "created_at",
    ]
    list_filter = ["verification_status", "is_verified", "created_at"]
    search_fields = ["title"]
    inlines = [SanatoriumImageInline]
    filter_horizontal = ["specializations"]


# ──────────────────────────────────────────────
# Room
# ──────────────────────────────────────────────


class SanatoriumRoomImageInline(TabularInline):
    model = SanatoriumRoomImage
    extra = 1
    fields = ("image", "order")


class SanatoriumRoomPriceInline(TabularInline):
    model = SanatoriumRoomPrice
    extra = 1
    fields = ("package_type", "price", "currency")


@admin.register(SanatoriumRoom)
class SanatoriumRoomAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_display = [
        "guid",
        "title",
        "sanatorium",
        "room_type",
        "capacity",
        "area",
    ]
    list_filter = ["room_type", "sanatorium"]
    search_fields = ["title"]
    inlines = [SanatoriumRoomImageInline, SanatoriumRoomPriceInline]
    filter_horizontal = ["amenities"]


# ──────────────────────────────────────────────
# Calendar
# ──────────────────────────────────────────────


@admin.register(RoomCalendarDate)
class RoomCalendarDateAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_display = ["guid", "room", "date", "status"]
    list_filter = ["status", "date"]


# ──────────────────────────────────────────────
# Review
# ──────────────────────────────────────────────


@admin.register(SanatoriumReview)
class SanatoriumReviewAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_display = ["guid", "client", "sanatorium", "rating", "is_hidden", "created_at"]
    list_filter = ["is_hidden", "created_at"]


# ──────────────────────────────────────────────
# Booking
# ──────────────────────────────────────────────


class SanatoriumBookingPriceInline(TabularInline):
    model = SanatoriumBookingPrice
    extra = 0
    fields = (
        "subtotal",
        "hold_amount",
        "charge_amount",
        "service_fee",
        "service_fee_percentage",
    )
    readonly_fields = fields


@admin.register(SanatoriumBooking)
class SanatoriumBookingAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_display = [
        "booking_number",
        "sanatorium",
        "room",
        "client",
        "status",
        "check_in",
        "check_out",
        "created_at",
    ]
    list_filter = ["status", "created_at"]
    search_fields = ["booking_number"]
    inlines = [SanatoriumBookingPriceInline]


@admin.register(SanatoriumImage)
class SanatoriumImageAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_display = ["guid", "sanatorium", "order", "is_pending", "created_at"]
    list_filter = ["is_pending"]
