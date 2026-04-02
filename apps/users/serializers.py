from __future__ import annotations

from rest_framework import serializers
from django.utils.translation import gettext_lazy as _

from .models.logs import SmsPurpose
from .services import OTPRedisService
from .raw_repository import (
    ensure_test_partner,
    exists_partner_email,
    exists_partner_username,
    exists_user_by_phone,
    get_active_user_by_phone,
)


CLIENT_DEVICE_TYPE_CHOICES = (("ios", "iOS"), ("android", "Android"))
PARTNER_DEVICE_TYPE_CHOICES = (("ios", "iOS"), ("android", "Android"))


class OTPCodeFieldAliasMixin:
    """Accept both otp_code and otp-code in request body."""

    def to_internal_value(self, data):
        if isinstance(data, dict):
            data = dict(data)
            if "otp-code" in data and "otp_code" not in data:
                data["otp_code"] = data.pop("otp-code")
        return super().to_internal_value(data)


class UserPhoneNumberSerializer(serializers.Serializer):
    phone_number = serializers.CharField()

    def validate_phone_number(self, value: str):
        if not value.startswith("998"):
            raise serializers.ValidationError(_("Phone number must start with 998"))
        return value


class ClientOTPLoginVerifySerializer(OTPCodeFieldAliasMixin, serializers.Serializer):
    phone_number = serializers.CharField(required=True)
    fcm_token = serializers.CharField(required=False, allow_null=True)
    device_type = serializers.ChoiceField(
        required=False,
        choices=CLIENT_DEVICE_TYPE_CHOICES,
    )
    otp_code = serializers.CharField(
        min_length=OTPRedisService.OTP_LENGTH,
        max_length=OTPRedisService.OTP_LENGTH,
        required=True,
    )

    def validate(self, attrs):
        phone_number = attrs["phone_number"]
        otp_code = attrs["otp_code"]

        is_valid, message = OTPRedisService.verify_otp(
            phone_number, otp_code, SmsPurpose.LOGIN
        )

        if not is_valid:
            raise serializers.ValidationError(message)

        client = get_active_user_by_phone(phone_number, role="client")
        if client is None:
            raise serializers.ValidationError(_("Client not found"))

        attrs["client"] = client
        return attrs


class ClientRegisterSerializer(serializers.Serializer):
    phone_number = serializers.CharField(required=True)
    first_name = serializers.CharField(required=False, min_length=2, max_length=64)
    last_name = serializers.CharField(required=False, min_length=2, max_length=64)

    def validate_phone_number(self, value):
        if exists_user_by_phone(value, role="client"):
            raise serializers.ValidationError(
                _("Client with this phone number already exists")
            )
        return value


class ClientOTPRegistrationVerifySerializer(OTPCodeFieldAliasMixin, serializers.Serializer):
    phone_number = serializers.CharField(required=True)
    fcm_token = serializers.CharField(required=False, allow_null=True)
    device_type = serializers.ChoiceField(
        required=False,
        choices=CLIENT_DEVICE_TYPE_CHOICES,
    )
    otp_code = serializers.CharField(
        min_length=OTPRedisService.OTP_LENGTH,
        max_length=OTPRedisService.OTP_LENGTH,
        required=True,
    )

    def validate(self, attrs):
        phone_number = attrs["phone_number"]
        otp_code = attrs["otp_code"]

        registration_data = OTPRedisService.get_registration_data(
            phone_number, SmsPurpose.REGISTER
        )
        if not registration_data:
            raise serializers.ValidationError(
                _("Registration data not found. Please start registration again")
            )

        is_valid, message = OTPRedisService.verify_otp(
            phone_number, otp_code, SmsPurpose.REGISTER
        )
        if not is_valid:
            raise serializers.ValidationError(message)

        if exists_user_by_phone(phone_number, role="client"):
            raise serializers.ValidationError(_("Client already registered"))

        attrs["registration_data"] = registration_data
        attrs["phone_number"] = phone_number
        return attrs


class TokenRefreshSerializer(serializers.Serializer):
    refresh = serializers.CharField(required=True)


class ClientProfileSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    guid = serializers.SerializerMethodField(read_only=True)
    phone_number = serializers.CharField(read_only=True)
    first_name = serializers.CharField(required=False, allow_blank=True, max_length=255)
    last_name = serializers.CharField(required=False, allow_blank=True, max_length=255)
    avatar = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    def get_guid(self, obj):
        return str(obj.guid) if getattr(obj, "guid", None) else None


class PartnerOTPLoginSerializer(OTPCodeFieldAliasMixin, serializers.Serializer):
    phone_number = serializers.CharField(required=True)
    fcm_token = serializers.CharField(required=False, allow_null=True)
    device_type = serializers.ChoiceField(
        required=False,
        choices=PARTNER_DEVICE_TYPE_CHOICES,
    )
    otp_code = serializers.CharField(
        min_length=OTPRedisService.OTP_LENGTH,
        max_length=OTPRedisService.OTP_LENGTH,
        required=True,
    )

    def validate(self, attrs):
        phone_number = attrs["phone_number"]
        otp_code = attrs["otp_code"]

        is_valid, message = OTPRedisService.verify_otp(
            phone_number, otp_code, SmsPurpose.PARTNER_LOGIN
        )
        if not is_valid:
            raise serializers.ValidationError(message)

        partner = get_active_user_by_phone(phone_number, role="partner")
        if partner is None:
            if OTPRedisService.is_test_phone_for_purpose(
                phone_number, SmsPurpose.PARTNER_LOGIN
            ):
                partner = ensure_test_partner(phone_number)
            else:
                raise serializers.ValidationError(_("Partner not found"))

        attrs["partner"] = partner
        return attrs


class PartnerOTPRegisterSerializer(serializers.Serializer):
    phone_number = serializers.CharField(required=True)
    username = serializers.CharField(required=True, min_length=2)
    first_name = serializers.CharField(required=True, min_length=2, max_length=64)
    last_name = serializers.CharField(required=True, min_length=2, max_length=64)
    email = serializers.EmailField(required=False, write_only=True)

    def validate_phone_number(self, value):
        if exists_user_by_phone(value, role="partner"):
            raise serializers.ValidationError(
                _("Partner with this phone number already exists")
            )
        return value

    def validate_username(self, value):
        if exists_partner_username(value):
            raise serializers.ValidationError(_("Username already exists"))
        return value

    def validate_email(self, value):
        if value and exists_partner_email(value):
            raise serializers.ValidationError(
                _("Partner with this email already exists")
            )
        return value


class PartnerOTPRegisterVerifySerializer(OTPCodeFieldAliasMixin, serializers.Serializer):
    phone_number = serializers.CharField(required=True)
    fcm_token = serializers.CharField(required=False, allow_null=True)
    device_type = serializers.ChoiceField(
        required=False,
        choices=PARTNER_DEVICE_TYPE_CHOICES,
    )
    otp_code = serializers.CharField(
        min_length=OTPRedisService.OTP_LENGTH,
        max_length=OTPRedisService.OTP_LENGTH,
        required=True,
    )

    def validate(self, attrs):
        phone_number = attrs["phone_number"]
        registration_data = OTPRedisService.get_registration_data(
            phone_number, SmsPurpose.PARTNER_REGISTER
        )
        if not registration_data:
            raise serializers.ValidationError(
                _("Registration data not found. Please start registration again")
            )

        otp_code = attrs["otp_code"]
        is_valid, message = OTPRedisService.verify_otp(
            phone_number, otp_code, SmsPurpose.PARTNER_REGISTER
        )
        if not is_valid:
            raise serializers.ValidationError(message)

        if exists_user_by_phone(phone_number, role="partner"):
            raise serializers.ValidationError(_("Partner already registered"))

        attrs["registration_data"] = registration_data
        attrs["phone_number"] = phone_number
        return attrs


class ResendOTPSerializer(serializers.Serializer):
    phone_number = serializers.CharField(required=True)

    def validate_phone_number(self, value: str):
        if not value.startswith("998"):
            raise serializers.ValidationError(_("Phone number must start with 998"))
        return value


class PartnerProfileSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    guid = serializers.SerializerMethodField(read_only=True)
    username = serializers.CharField(required=False, allow_blank=True, max_length=255)
    first_name = serializers.CharField(required=False, allow_blank=True, max_length=255)
    last_name = serializers.CharField(required=False, allow_blank=True, max_length=255)
    phone_number = serializers.CharField(read_only=True)
    avatar = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)

    def get_guid(self, obj):
        return str(obj.guid) if getattr(obj, "guid", None) else None


class PartnerPassportUploadSerializer(serializers.Serializer):
    document = serializers.FileField(required=True)

    def validate_document(self, file):
        max_size = 5 * 1024 * 1024  # 5MB
        if file.size > max_size:
            raise serializers.ValidationError("File size must be ≤ 5MB")
        return file

