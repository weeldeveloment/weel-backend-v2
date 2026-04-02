from django.contrib import admin

from unfold.admin import ModelAdmin

from .models import ExchangeRate, PlumTransaction

# Register your models here.


@admin.register(PlumTransaction)
class PlumTransactionAdmin(ModelAdmin):
    list_display = ["guid", "transaction_id", "hold_id", "amount", "type", "status", "created_at"]
    list_filter = ["type", "status"]
    search_fields = ["transaction_id", "hold_id", "card_id"]
    readonly_fields = ["guid", "transaction_id", "hold_id", "amount", "type", "status", "card_id", "extra_id", "created_at"]
    date_hierarchy = "created_at"
    ordering = ["-created_at"]


@admin.register(ExchangeRate)
class ExchangeRateAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True

    list_filter_submit = False
    list_display = ["guid", "currency", "rate", "date", "created_at"]

    readonly_preprocess_fields = {
        "model_field_name": "html.unescape",
        "other_field_name": lambda content: content.strip(),
    }
