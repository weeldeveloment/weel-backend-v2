from django.contrib import admin
from django.contrib.admin import SimpleListFilter
from django.db.models import Exists, Max, OuterRef
from django.utils.translation import gettext_lazy as _
from django.utils.formats import date_format
from unfold.admin import ModelAdmin, StackedInline, TabularInline
from unfold.decorators import display, action

from .models.partners import (
    Partner,
    PartnerDevice,
    PartnerSession,
    PartnerDocument,
    PartnerTelegramUser,
)
from .models.clients import Client, ClientSession, ClientDevice


class HasPropertyFilter(SimpleListFilter):
    """Partner admin: filter by whether partner has created at least one property."""

    title = _("Has property")
    parameter_name = "has_property"

    def lookups(self, request, model_admin):
        return (
            ("yes", _("Yes (has property)")),
            ("no", _("No (no property)")),
        )

    def queryset(self, request, queryset):
        from property.models import Property

        if self.value() == "yes":
            return queryset.filter(
                Exists(Property.objects.filter(partner=OuterRef("pk")))
            )
        if self.value() == "no":
            return queryset.exclude(
                Exists(Property.objects.filter(partner=OuterRef("pk")))
            )
        return queryset


class PartnerDocumentInline(admin.TabularInline):
    model = PartnerDocument
    extra = 0
    fields = ("type", "document", "is_verified", "verified_by", "verified_at")
    readonly_fields = ("verified_at",)
    autocomplete_fields = ("verified_by",)


@admin.register(Partner)
class PartnerAdmin(ModelAdmin):
    list_display = (
        "id",
        "username",
        "first_name",
        "last_name",
        "phone_number",
        "email",
        "is_email_verified",
        "is_active",
        "created_at",
        "last_online_display",
    )
    list_filter = ("is_active", "is_email_verified", "created_at", HasPropertyFilter)
    search_fields = ("username", "first_name", "last_name", "phone_number", "email")
    ordering = ("-created_at",)
    readonly_fields = ("created_at", "updated_at")
    inlines = [PartnerDocumentInline]

    # Unfold-specific options (these are optional customizations)
    list_fullwidth = True
    warn_unsaved_form = True
    list_filter_submit = True

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(last_online=Max("partnersession__created_at"))

    @display(description=_("Last online"), ordering="last_online")
    def last_online_display(self, obj):
        last = getattr(obj, "last_online", None)
        if last is None:
            return "—"
        return date_format(last, "DATETIME_FORMAT")

    # Example: custom display using @display
    @display(description=_("Full name / email"))
    def name_and_email(self, obj):
        return obj.first_name + " " + obj.last_name, obj.email


@admin.register(PartnerSession)
class PartnerSessionAdmin(ModelAdmin):
    list_display = ("id", "partner", "device_id", "last_ip", "user_agent")
    search_fields = ("device_id", "last_ip", "user_agent", "partner__username")
    list_filter = ("last_ip",)
    autocomplete_fields = ("partner",)

    warn_unsaved_form = False


@admin.register(PartnerDevice)
class PartnerDeviceAdmin(ModelAdmin):
    list_display = ["guid", "partner", "device_type", "is_active", "created_at"]
    list_filter = ("device_type", "is_active")
    search_fields = ("partner__username", "partner__phone_number", "fcm_token")


@admin.register(PartnerDocument)
class PartnerDocumentAdmin(ModelAdmin):
    list_display = (
        "id",
        "partner",
        "type",
        "document",
        "is_verified",
        "verified_by",
        "verified_at",
    )
    list_filter = ("type", "is_verified")
    search_fields = ("partner__username", "partner__first_name", "partner__last_name")
    autocomplete_fields = ("partner", "verified_by")
    readonly_fields = ("verified_at",)

    list_filter_submit = True
    list_fullwidth = False


class ClientSessionInline(admin.TabularInline):
    model = ClientSession
    extra = 0
    fields = ("device_id", "user_agent", "last_ip", "created_at")
    readonly_fields = ("created_at",)
    show_change_link = True


@admin.register(Client)
class ClientAdmin(ModelAdmin):
    list_display = (
        "id",
        "first_name",
        "last_name",
        "phone_number",
        "is_active",
        "created_at",
    )
    list_filter = ("is_active", "created_at")
    search_fields = ("first_name", "last_name", "phone_number")
    inlines = [ClientSessionInline]
    ordering = ("-created_at",)

    list_fullwidth = True


@admin.register(ClientSession)
class ClientSessionAdmin(ModelAdmin):
    list_display = ("id", "client", "device_id", "last_ip", "created_at")
    list_filter = ("created_at",)
    search_fields = ("device_id", "last_ip", "user_agent")
    autocomplete_fields = ("client",)
    ordering = ("-created_at",)


@admin.register(ClientDevice)
class ClientDeviceAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True

    list_filter_submit = False
    list_display = ["guid", "client", "device_type", "is_active", "created_at"]

    readonly_preprocess_fields = {
        "model_field_name": "html.unescape",
        "other_field_name": lambda content: content.strip(),
    }


# 1. Define the Inline for the Partner page
class PartnerTelegramUserInline(TabularInline):
    model = PartnerTelegramUser
    extra = 0
    # Use fields appropriate for a quick overview
    fields = ("telegram_user_id", "username", "is_active")
    # This ensures it looks like the Unfold style
    tab = True


# 2. Standalone Admin for Telegram Users
@admin.register(PartnerTelegramUser)
class PartnerTelegramUserAdmin(ModelAdmin):
    list_display = (
        "telegram_user_id",
        "username",
        "partner_link",
        "is_active",
        "created_at",
    )
    search_fields = (
        "telegram_user_id",
        "username",
        "partner__username",
        "partner__phone_number",
    )
    list_filter = ("is_active",)
    autocomplete_fields = ("partner",)

    @display(description=_("Partner"))
    def partner_link(self, obj):
        return f"{obj.partner.first_name} {obj.partner.last_name}"


# # 3. Update your existing PartnerAdmin
# @admin.register(Partner)
# class PartnerAdmin(ModelAdmin):
#     list_display = (
#         "id",
#         "username",
#         "phone_number",
#         "is_active",
#         "telegram_status",  # Optional: see at a glance if TG is linked
#     )
#     # Add the Telegram Inline to the existing list
#     inlines = [PartnerDocumentInline, PartnerTelegramUserInline]
#
#     # ... keep your existing search_fields and other configs ...
#     search_fields = ("username", "first_name", "last_name", "phone_number", "email")
#
#     @display(description="Telegram", boolean=True)
#     def telegram_status(self, obj):
#         return hasattr(obj, "telegram")
