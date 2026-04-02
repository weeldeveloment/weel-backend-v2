from django.contrib import admin
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from unfold.admin import ModelAdmin

from .models import Story, StoryView, StoryMedia


# Register your models here.


@admin.action(description=_("Verify selected stories"))
def make_stories_verified(modeladmin, request, queryset):
    updated = queryset.update(is_verified=True, verified_by=request.user, verified_at=timezone.now())
    modeladmin.message_user(request, _("%(count)d story(s) verified.") % {"count": updated})


class StoryMediaInline(admin.TabularInline):
    model = StoryMedia
    extra = 1

    fields = ("media", "media_type")
    show_change_link = True


@admin.register(Story)
class StoryAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True

    list_filter_submit = False
    list_display = ["guid", "property", "is_verified", "uploaded_at", "expires_at"]
    list_editable = ["is_verified"]
    actions = [make_stories_verified]

    inlines = [StoryMediaInline]

    readonly_preprocess_fields = {
        "model_field_name": "html.unescape",
        "other_field_name": lambda content: content.strip(),
    }


@admin.register(StoryMedia)
class StoryMediaAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True

    list_filter_submit = False
    list_display = ["guid", "media", "media_type"]


@admin.register(StoryView)
class StoryViewAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True

    list_filter_submit = False
    list_display = ["guid", "story", "client", "created_at"]

    readonly_preprocess_fields = {
        "model_field_name": "html.unescape",
        "other_field_name": lambda content: content.strip(),
    }
