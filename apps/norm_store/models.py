"""
Unmanaged ORM models for norm_* tables (analytics / mirror).
"""
import uuid

from django.db import models


class NormCustomer(models.Model):
    id = models.BigAutoField(primary_key=True)
    guid = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    legacy_client_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    legacy_client_guid = models.UUIDField(null=True, blank=True)
    first_name = models.CharField(max_length=255)
    last_name = models.CharField(max_length=255)
    phone_number = models.CharField(max_length=16, db_index=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        managed = False
        db_table = "norm_customers"


class NormClientSession(models.Model):
    id = models.BigAutoField(primary_key=True)
    guid = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    device_id = models.CharField(max_length=255, null=True, blank=True)
    user_agent = models.CharField(max_length=512, default="")
    last_ip = models.CharField(max_length=45, default="0.0.0.0")
    client = models.ForeignKey(NormCustomer, on_delete=models.CASCADE, db_column="client_id")

    class Meta:
        managed = False
        db_table = "norm_client_sessions"


class NormClientDevice(models.Model):
    id = models.BigAutoField(primary_key=True)
    guid = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    fcm_token = models.CharField(max_length=255)
    device_type = models.CharField(max_length=10)
    is_active = models.BooleanField(default=True)
    client = models.ForeignKey(NormCustomer, on_delete=models.CASCADE, db_column="client_id")

    class Meta:
        managed = False
        db_table = "norm_client_devices"


class NormPartner(models.Model):
    id = models.BigAutoField(primary_key=True)
    guid = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    legacy_partner_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    legacy_partner_guid = models.UUIDField(null=True, blank=True)
    first_name = models.CharField(max_length=255)
    last_name = models.CharField(max_length=255)
    username = models.CharField(max_length=255)
    phone_number = models.CharField(max_length=16)
    email = models.CharField(max_length=254, blank=True, default="")
    is_active = models.BooleanField(default=True)
    is_verified = models.BooleanField(null=True, blank=True)
    verified_by_admin_id = models.IntegerField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "norm_partners"


class NormPartnerSession(models.Model):
    id = models.BigAutoField(primary_key=True)
    guid = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    device_id = models.CharField(max_length=255, null=True, blank=True)
    user_agent = models.CharField(max_length=512, default="")
    last_ip = models.CharField(max_length=45, default="0.0.0.0")
    partner = models.ForeignKey(NormPartner, on_delete=models.CASCADE, db_column="partner_id")

    class Meta:
        managed = False
        db_table = "norm_partner_sessions"


class NormPartnerDevice(models.Model):
    id = models.BigAutoField(primary_key=True)
    guid = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    fcm_token = models.CharField(max_length=255)
    device_type = models.CharField(max_length=10)
    is_active = models.BooleanField(default=True)
    partner = models.ForeignKey(NormPartner, on_delete=models.CASCADE, db_column="partner_id")

    class Meta:
        managed = False
        db_table = "norm_partner_devices"


class NormProperty(models.Model):
    id = models.BigAutoField(primary_key=True)
    guid = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    legacy_property_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    legacy_property_guid = models.UUIDField(null=True, blank=True)
    title = models.CharField(max_length=75)
    currency = models.CharField(max_length=3)
    verification_status = models.CharField(max_length=10, blank=True, default="")
    is_verified = models.BooleanField(default=False)
    is_archived = models.BooleanField(default=False)
    region_id = models.BigIntegerField(null=True, blank=True)
    district_id = models.BigIntegerField(null=True, blank=True)
    city = models.CharField(max_length=100, blank=True, default="")
    country = models.CharField(max_length=100, blank=True, default="")
    latitude = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    longitude = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    partner = models.ForeignKey(NormPartner, on_delete=models.SET_NULL, null=True, blank=True, db_column="partner_id")

    class Meta:
        managed = False
        db_table = "norm_properties"


class NormPropertyPrice(models.Model):
    id = models.BigAutoField(primary_key=True)
    guid = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    legacy_property_price_id = models.BigIntegerField(null=True, blank=True)
    month_from = models.DateField()
    month_to = models.DateField()
    price_per_person = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    price_on_working_days = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    price_on_weekends = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    property = models.ForeignKey(NormProperty, on_delete=models.CASCADE, db_column="property_id")

    class Meta:
        managed = False
        db_table = "norm_property_prices"


class NormBooking(models.Model):
    id = models.BigAutoField(primary_key=True)
    guid = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    legacy_booking_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    legacy_booking_guid = models.UUIDField(null=True, blank=True, db_index=True)
    booking_number = models.CharField(max_length=7)
    check_in = models.DateField()
    check_out = models.DateField()
    adults = models.SmallIntegerField()
    children = models.SmallIntegerField()
    babies = models.SmallIntegerField()
    current_status = models.CharField(max_length=20)
    cancellation_reason = models.CharField(max_length=100, blank=True, default="")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    reminder_sent = models.BooleanField(default=False)
    payment_reminder_stage = models.CharField(max_length=10, blank=True, default="")
    customer = models.ForeignKey(NormCustomer, on_delete=models.CASCADE, db_column="customer_id")
    property = models.ForeignKey(NormProperty, on_delete=models.CASCADE, db_column="property_id")

    class Meta:
        managed = False
        db_table = "norm_bookings"


class NormBookingStatusHistory(models.Model):
    id = models.BigAutoField(primary_key=True)
    guid = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    from_status = models.CharField(max_length=20, blank=True, default="")
    to_status = models.CharField(max_length=20)
    reason = models.CharField(max_length=100, blank=True, default="")
    source = models.CharField(max_length=32, blank=True, default="api")
    changed_at = models.DateTimeField()
    booking = models.ForeignKey(NormBooking, on_delete=models.CASCADE, db_column="booking_id")

    class Meta:
        managed = False
        db_table = "norm_booking_status_history"


class NormBookingPaymentLink(models.Model):
    id = models.BigAutoField(primary_key=True)
    guid = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    legacy_booking_transaction_id = models.BigIntegerField(null=True, blank=True)
    booking = models.ForeignKey(NormBooking, on_delete=models.CASCADE, db_column="booking_id")
    payment_transaction_id = models.BigIntegerField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "norm_booking_payment_links"


class NormExchangeRate(models.Model):
    id = models.BigAutoField(primary_key=True)
    guid = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    currency = models.CharField(max_length=3)
    rate = models.DecimalField(max_digits=18, decimal_places=6)
    date = models.DateField()

    class Meta:
        managed = False
        db_table = "norm_exchange_rates"


class NormNotification(models.Model):
    id = models.BigAutoField(primary_key=True)
    guid = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    legacy_notification_id = models.BigIntegerField(null=True, blank=True)
    legacy_partner_notification_id = models.BigIntegerField(null=True, blank=True)
    title = models.CharField(max_length=255)
    body = models.TextField()
    notification_type = models.CharField(max_length=30)
    status = models.CharField(max_length=15, blank=True, default="")
    is_broadcast = models.BooleanField(default=False)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    payload = models.JSONField(null=True, blank=True)
    customer_id = models.BigIntegerField(null=True, blank=True)
    partner_id = models.BigIntegerField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "norm_notifications"


class NormPaymentTransaction(models.Model):
    id = models.BigAutoField(primary_key=True)
    guid = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    legacy_plum_transaction_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    legacy_plum_transaction_guid = models.UUIDField(null=True, blank=True)
    provider_transaction_id = models.CharField(max_length=255)
    provider_hold_id = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    type = models.CharField(max_length=4)
    status = models.CharField(max_length=20)
    card_id = models.CharField(max_length=255, null=True, blank=True)
    extra_id = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        managed = False
        db_table = "norm_payment_transactions"
