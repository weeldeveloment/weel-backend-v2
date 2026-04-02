from django.conf import settings
from django.db.models.signals import post_save, pre_save


def _norm_on():
    return bool(getattr(settings, "USE_NORM_DATASTORE", False))


def connect_norm_signals():
    from booking.models import Booking
    from payment.models import PlumTransaction
    from property.models import Property, PropertyPrice

    from norm_store.sync import sync_booking_to_norm, sync_plum_to_norm, sync_property_to_norm

    def booking_pre_save(sender, instance, **kwargs):
        if not _norm_on():
            return
        if instance.pk:
            try:
                prev = Booking.objects.get(pk=instance.pk)
                instance._norm_prev_status = prev.status
            except Booking.DoesNotExist:
                instance._norm_prev_status = None
        else:
            instance._norm_prev_status = None

    def booking_post_save(sender, instance, **kwargs):
        if not _norm_on():
            return
        old = getattr(instance, "_norm_prev_status", None)
        sync_booking_to_norm(instance, old_status=old)

    def property_post_save(sender, instance, **kwargs):
        if not _norm_on():
            return
        prop = Property.objects.prefetch_related("property_price").get(pk=instance.pk)
        sync_property_to_norm(prop)

    def property_price_post_save(sender, instance, **kwargs):
        if not _norm_on():
            return
        sync_property_to_norm(instance.property)

    def plum_post_save(sender, instance, **kwargs):
        if not _norm_on():
            return
        sync_plum_to_norm(instance)

    pre_save.connect(booking_pre_save, sender=Booking, dispatch_uid="norm_booking_pre")
    post_save.connect(booking_post_save, sender=Booking, dispatch_uid="norm_booking_post")
    post_save.connect(property_post_save, sender=Property, dispatch_uid="norm_property_post")
    post_save.connect(
        property_price_post_save, sender=PropertyPrice, dispatch_uid="norm_property_price_post"
    )
    post_save.connect(plum_post_save, sender=PlumTransaction, dispatch_uid="norm_plum_post")
