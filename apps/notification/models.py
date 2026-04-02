from django.db import models
from django.utils.translation import gettext_lazy as _

from shared.models import HardDeleteBaseModel
from users.models import Client
from users.models.partners import Partner


class Notification(HardDeleteBaseModel):
    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        SENT = "sent", _("Sent")

    class NotificationType(models.TextChoices):
        BOOKING_CONFIRMED = "booking_confirmed", _("Booking confirmed")
        BOOKING_CANCELLED = "booking_cancelled", _("Booking cancelled")
        REMINDER = "reminder", _("Reminder")
        SYSTEM = "system", _("System")

    recipient = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        related_name="notifications",
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=255, null=True, verbose_name=_("Title"))
    push_message = models.TextField(null=True, verbose_name=_("Push message"))
    notification_type = models.CharField(
        choices=NotificationType,
        max_length=20,
    )
    status = models.CharField(
        max_length=15,
        choices=Status,
        default=Status.PENDING,
        verbose_name=_("Status"),
    )
    is_for_every_one = models.BooleanField(
        default=False, verbose_name=_("Is for everyone")
    )

    class Meta:
        db_table = "notifications"
        app_label = "notification"
        verbose_name = _("Notification")
        verbose_name_plural = _("Notifications")
        constraints = [
            models.CheckConstraint(
                check=models.Q(is_for_every_one=True, recipient__isnull=True)
                | models.Q(is_for_every_one=False, recipient__isnull=False),
                name="notification_recipient_consistency",
            )
        ]

    def __str__(self):
        return f"Recipient: {self.recipient_id} | Title: {self.title} | Push message: {self.push_message}"

    def __repr__(self):
        return f"<Recipient={self.id} title={self.title}> push_message={self.push_message} notification_type={self.notification_type}"


class PartnerNotification(HardDeleteBaseModel):
    """Notification history for partners"""
    
    class NotificationType(models.TextChoices):
        BOOKING_NEW = "booking_new", _("New Booking")
        BOOKING_CONFIRMED = "booking_confirmed", _("Booking Confirmed")
        BOOKING_CANCELLED = "booking_cancelled", _("Booking Cancelled")
        BOOKING_COMPLETED = "booking_completed", _("Booking Completed")
        BOOKING_NO_SHOW = "booking_no_show", _("Booking No Show")
        SYSTEM = "system", _("System")
        PROMO = "promo", _("Promotion")

    partner = models.ForeignKey(
        Partner,
        on_delete=models.CASCADE,
        related_name="notifications",
        verbose_name=_("Partner"),
    )
    title = models.CharField(max_length=255, verbose_name=_("Title"))
    body = models.TextField(verbose_name=_("Body"))
    notification_type = models.CharField(
        max_length=30,
        choices=NotificationType,
        default=NotificationType.SYSTEM,
        verbose_name=_("Notification Type"),
    )
    data = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_("Data"),
        help_text=_("Additional data like booking_id, etc."),
    )
    is_read = models.BooleanField(
        default=False,
        verbose_name=_("Is Read"),
    )
    read_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Read At"),
    )

    class Meta:
        db_table = "partner_notifications"
        app_label = "notification"
        verbose_name = _("Partner Notification")
        verbose_name_plural = _("Partner Notifications")
        ordering = ["-created_at"]

    def __str__(self):
        return f"Partner: {self.partner_id} | {self.title}"

    def __repr__(self):
        return f"<PartnerNotification id={self.id} partner={self.partner_id} title={self.title}>"

    def mark_as_read(self):
        """Mark notification as read"""
        if not self.is_read:
            from django.utils import timezone
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=["is_read", "read_at"])
