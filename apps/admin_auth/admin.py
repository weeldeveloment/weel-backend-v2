from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin
from unfold.decorators import display

User = get_user_model()

# Unregister the default User admin
admin.site.unregister(User)


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    """
    Custom admin interface for managing admin users (Django User model).
    Integrates with Django Unfold theme for enhanced UI.
    """

    # List display configuration
    list_display = (
        "email",
        "get_full_name_display",
        "is_staff",
        "is_superuser",
        "is_active",
        "date_joined",
    )
    list_filter = (
        "is_staff",
        "is_superuser",
        "is_active",
        "date_joined",
        "last_login",
    )
    search_fields = ("email", "first_name", "last_name", "username")
    ordering = ("-date_joined",)
    
    # Fieldsets for editing existing users
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        (
            _("Personal info"),
            {"fields": ("first_name", "last_name", "email")},
        ),
        (
            _("Permissions"),
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
            },
        ),
        (
            _("Important dates"),
            {"fields": ("last_login", "date_joined")},
        ),
    )
    
    # Fieldsets for adding new users
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "username",
                    "email",
                    "password1",
                    "password2",
                    "first_name",
                    "last_name",
                    "is_staff",
                    "is_superuser",
                    "is_active",
                ),
            },
        ),
    )
    
    readonly_fields = ("last_login", "date_joined")
    filter_horizontal = ("groups", "user_permissions")

    @display(description=_("Full name"), ordering="first_name")
    def get_full_name_display(self, obj):
        """Display full name or email if name is not set."""
        full_name = obj.get_full_name()
        return full_name if full_name.strip() else obj.email

    def get_queryset(self, request):
        """Optimize queryset with select_related for better performance."""
        qs = super().get_queryset(request)
        return qs.select_related()

    @admin.action(description=_("Mark selected users as staff"))
    def make_staff(self, request, queryset):
        """Bulk action to promote users to staff status."""
        updated = queryset.update(is_staff=True)
        self.message_user(
            request,
            _(f"{updated} user(s) were successfully marked as staff."),
        )

    @admin.action(description=_("Remove staff status from selected users"))
    def remove_staff(self, request, queryset):
        """Bulk action to remove staff status from users."""
        updated = queryset.update(is_staff=False, is_superuser=False)
        self.message_user(
            request,
            _(f"{updated} user(s) had their staff status removed."),
        )

    @admin.action(description=_("Activate selected users"))
    def activate_users(self, request, queryset):
        """Bulk action to activate users."""
        updated = queryset.update(is_active=True)
        self.message_user(
            request,
            _(f"{updated} user(s) were successfully activated."),
        )

    @admin.action(description=_("Deactivate selected users"))
    def deactivate_users(self, request, queryset):
        """Bulk action to deactivate users."""
        updated = queryset.update(is_active=False)
        self.message_user(
            request,
            _(f"{updated} user(s) were successfully deactivated."),
        )

    actions = [make_staff, remove_staff, activate_users, deactivate_users]
