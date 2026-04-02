import uuid
from datetime import timedelta

from django.db import models
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

# Create your models here.

from core import settings
from shared.models import HardDeleteBaseModel, VerifiedByMixin
from property.models import Property
from users.models.clients import Client

from shared.compress_video import compress_video as story_video_compress
from shared.compress_image import property_image_compress as story_image_compress


class Story(HardDeleteBaseModel, VerifiedByMixin):
    property = models.ForeignKey(
        Property,
        on_delete=models.CASCADE,
        related_name="stories",
        verbose_name=_("Property"),
    )
    expires_at = models.DateTimeField(
        blank=True, null=True, verbose_name=_("Expires at")
    )
    views = models.PositiveIntegerField(
        default=0, db_default=0, verbose_name=_("Views")
    )
    uploaded_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Uploaded at"))

    class Meta:
        verbose_name = _("Story")
        verbose_name_plural = _("Stories")

    def save(self, *args, **kwargs):
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(hours=48)
        super().save(*args, **kwargs)

    def is_expired(self):
        return timezone.now() >= self.expires_at

    def __str__(self):
        return f"Story {self.guid} for {self.property}"

    def __repr__(self):
        return f"Story={self.guid} property={self.property}"


class StoryMedia(HardDeleteBaseModel):
    class MediaType(models.TextChoices):
        IMAGE = ("image", _("Image"))
        VIDEO = ("video", _("Video"))

    def _upload_directory_path(self, filename: str) -> str:
        extension = filename.split(".")[-1]
        unique_name = uuid.uuid4().hex
        return f"stories/{unique_name}.{extension}"

    story = models.ForeignKey(
        Story,
        on_delete=models.CASCADE,
        related_name="media",
        verbose_name=_("Story"),
    )
    media = models.FileField(upload_to=_upload_directory_path, verbose_name=_("Media"))
    media_type = models.CharField(
        max_length=10,
        choices=MediaType,
        default=MediaType.VIDEO,
        db_default=MediaType.VIDEO,
        verbose_name=_("Media type"),
    )

    class Meta:
        verbose_name = _("Story media")
        verbose_name_plural = _("Story media")

    def clean(self):
        if not self.media:
            return

        extension = self.media.name.split(".")[-1].lower()
        if self.media_type == self.MediaType.IMAGE:
            if extension not in settings.ALLOWED_PHOTO_EXTENSION:
                raise ValidationError(
                    {
                        "media": _(
                            "Invalid image format, allowed are: jpg, jpeg, png, heif, heic"
                        )
                    }
                )

            if self.media.size > settings.MAX_IMAGE_SIZE:
                raise ValidationError(
                    {"media": _("Image file too large, maximum size is 20MB")}
                )

        if self.media_type == self.MediaType.VIDEO:
            if extension not in settings.ALLOWED_VIDEO_EXTENSION:
                raise ValidationError(
                    {
                        "media": _(
                            "Invalid video format, allowed are: mp4, mov, avi, mkv"
                        )
                    }
                )

            if self.media.size > settings.MAX_VIDEO_SIZE:
                raise ValidationError(
                    {"media": _("Video file too large, maximum size is 100MB")}
                )

    def save(self, *args, **kwargs):
        # Only compress on first save (when pk is None)
        if not self.pk:
            self.full_clean()

            if self.media_type == "image":
                if self.media and self.media.size > settings.PHOTO_SIZE_TO_COMPRESS:
                    self.media = story_image_compress(self.media)
            elif self.media_type == "video":
                if self.media and self.media.size > settings.VIDEO_SIZE_TO_COMPRESS:
                    self.media = story_video_compress(
                        self.media, target_resolution="720"
                    )
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Story: {self.guid} | Media: {self.media}"

    def __repr__(self):
        return f"<StoryMedia guid={self.guid} Story guid={self.guid}>"


class StoryView(HardDeleteBaseModel):
    story = models.ForeignKey(
        Story,
        on_delete=models.CASCADE,
        related_name="story_views",
        verbose_name=_("Story"),
    )
    client = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        related_name="story_view_client",
        verbose_name=_("Client"),
    )
    viewed_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Viewed at"))

    class Meta:
        verbose_name = _("Story view")
        verbose_name_plural = _("Story views")
        constraints = [
            models.UniqueConstraint(
                fields=["story", "client"],
                name="unique_story_client_story_view",
            )
        ]
