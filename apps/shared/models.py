import uuid

from django.db import models
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _

User = get_user_model()


class BaseModel(models.Model):
    guid = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Created at"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("Updated at"))

    class Meta:
        abstract = True


class HardDeleteBaseModel(BaseModel):
    class Meta:
        abstract = True


class VerifiedByMixin(models.Model):
    is_verified = models.BooleanField(
        null=True,
        blank=True,
        default=False,
        db_default=False,
        verbose_name=_("Verified"),
    )
    verified_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        limit_choices_to={"is_staff": True},
        verbose_name=_("Verified by"),
    )
    verified_at = models.DateTimeField(blank=True, null=True, verbose_name=_("Verified at"))

    class Meta:
        abstract = True
