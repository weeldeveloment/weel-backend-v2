from typing import Any

from django.contrib import admin
from django.forms import Form
from django.http import HttpRequest

from unfold.admin import ModelAdmin
from .models import Notification
from .service import NotificationService


# Register your models here.


@admin.register(Notification)
class NotificationAdmin(ModelAdmin):
    list_display = [
        "guid",
        "recipient",
        "title",
        "push_message",
        "notification_type",
        "status",
        "created_at",
    ]

    def save_model(
        self, request: HttpRequest, obj: Notification, form: Form, change: Any
    ) -> None:
        is_new = obj.pk is None
        super().save_model(request, obj, form, change)

        # Only send when:
        # -- newly created
        # -- system
        # -- pending
        if (
            is_new
            and obj.is_for_every_one
            and obj.notification_type == Notification.NotificationType.SYSTEM
            and obj.status == Notification.Status.PENDING
        ):
            NotificationService.send_broadcast(obj)
