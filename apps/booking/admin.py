from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import (
    Booking,
    CalendarDate,
    BookingPrice,
    BookingTransaction,
)


# Register your models here.


@admin.register(Booking)
class BookingAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True

    list_filter_submit = False
    list_display = [
        "guid",
        "property",
        "client",
        "check_in",
        "check_out",
        "booking_number",
        "reminder_sent",
        "status",
        "created_at",
    ]
    list_filter = ["property", "status"]
    search_fields = ["guid", "client__phone_number", "property__title"]

    readonly_preprocess_fields = {
        "model_field_name": "html.unescape",
        "other_field_name": lambda content: content.strip(),
    }


@admin.register(BookingPrice)
class BookingPriceAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True

    list_filter_submit = False
    list_display = [
        "guid",
        "booking",
        "subtotal",
        "hold_amount",
        "charge_amount",
        "service_fee",
        "service_fee_percentage",
        "created_at",
    ]
    list_filter = [
        "guid",
        "booking__guid",
    ]

    readonly_preprocess_fields = {
        "model_field_name": "html.unescape",
        "other_field_name": lambda content: content.strip(),
    }


@admin.register(BookingTransaction)
class BookingTransactionAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True

    list_filter_submit = False
    list_display = [
        "guid",
        "booking",
        "plum_transaction",
        "created_at",
    ]

    readonly_preprocess_fields = {
        "model_field_name": "html.unescape",
        "other_field_name": lambda content: content.strip(),
    }


@admin.register(CalendarDate)
class CalendarDateAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True

    list_filter_submit = False
    list_display = [
        "guid",
        "property",
        "date",
        "status",
        "created_at",
    ]

    readonly_preprocess_fields = {
        "model_field_name": "html.unescape",
        "other_field_name": lambda content: content.strip(),
    }
