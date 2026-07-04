from __future__ import annotations

from rest_framework import serializers


class OrganizationSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(max_length=200)
    slug = serializers.SlugField(max_length=100)
    schema_name = serializers.CharField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)


class OrganizationCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200)
    slug = serializers.SlugField(max_length=100)


class AuthenticatedOrgCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200)


class OrganizationUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200, required=False)
    slug = serializers.SlugField(max_length=100, required=False)


class PlatformUserSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    phone_number = serializers.CharField(read_only=True)
    first_name = serializers.CharField(read_only=True, allow_null=True)
    last_name = serializers.CharField(read_only=True, allow_null=True)
    is_active = serializers.BooleanField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


class PlatformUserUpdateSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=100, required=False)
    last_name = serializers.CharField(max_length=100, required=False)
    phone = serializers.CharField(max_length=32, required=False)


class PmsOtpRegisterSerializer(serializers.Serializer):
    phone_number = serializers.CharField(max_length=32)
    org_name = serializers.CharField(max_length=200, required=False, allow_blank=True)
    first_name = serializers.CharField(max_length=100, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=100, required=False, allow_blank=True)


class PmsOtpVerifySerializer(serializers.Serializer):
    phone_number = serializers.CharField(max_length=32)
    otp_code = serializers.CharField(max_length=10)

    def validate(self, data):
        from users.services import OTPRedisService
        from users.models.logs import SmsPurpose

        phone_number = data["phone_number"]
        otp_code = data["otp_code"]

        registration_data = OTPRedisService.get_registration_data(
            phone_number, SmsPurpose.PMS_REGISTER
        )
        if not registration_data:
            raise serializers.ValidationError({"otp_code": "Invalid or expired OTP."})

        stored_otp = OTPRedisService.get_otp(phone_number, SmsPurpose.PMS_REGISTER)
        if stored_otp != otp_code:
            raise serializers.ValidationError({"otp_code": "Incorrect OTP."})

        OTPRedisService.consume_otp(phone_number, SmsPurpose.PMS_REGISTER)
        data["registration_data"] = registration_data
        return data


class PmsOtpLoginSerializer(serializers.Serializer):
    phone_number = serializers.CharField(max_length=32)


class PmsOtpLoginVerifySerializer(serializers.Serializer):
    phone_number = serializers.CharField(max_length=32)
    otp_code = serializers.CharField(max_length=10)
    organization_id = serializers.IntegerField(required=False)

    def validate(self, data):
        from users.services import OTPRedisService
        from users.models.logs import SmsPurpose
        from users.raw_repository import get_active_user_by_phone

        phone_number = data["phone_number"]
        otp_code = data["otp_code"]

        stored_otp = OTPRedisService.get_otp(phone_number, SmsPurpose.PMS_LOGIN)
        if stored_otp != otp_code:
            raise serializers.ValidationError({"otp_code": "Incorrect OTP."})

        OTPRedisService.consume_otp(phone_number, SmsPurpose.PMS_LOGIN)

        user = get_active_user_by_phone(phone_number, role="pms")
        if not user:
            raise serializers.ValidationError({"phone_number": "User not found."})

        data["user"] = {
            "id": user.id,
            "phone_number": user.phone_number,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "guid": str(user.guid),
        }

        org_id = data.get("organization_id")
        if org_id is not None:
            from apps.platform.raw_repository import get_organization_by_id, get_organization_member
            org = get_organization_by_id(org_id)
            if not org:
                raise serializers.ValidationError({"organization_id": "Organization not found."})
            member = get_organization_member(org_id, user.id)
            if not member:
                raise serializers.ValidationError({"organization_id": "You are not a member of this organization."})

        return data


class PmsLoginResponseSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    user = PlatformUserSerializer()
    organization = OrganizationSerializer()
    organizations = OrganizationSerializer(many=True, required=False)


class PmsSwitchOrgSerializer(serializers.Serializer):
    organization_id = serializers.IntegerField()

    def validate_organization_id(self, value):
        from apps.platform.raw_repository import get_organization_by_id
        org = get_organization_by_id(value)
        if not org:
            raise serializers.ValidationError("Organization not found.")
        return value


class OrganizationMemberSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    user_id = serializers.IntegerField(read_only=True)
    organization_id = serializers.IntegerField(read_only=True)
    role = serializers.CharField(read_only=True)
    phone_number = serializers.CharField(read_only=True, allow_null=True)
    first_name = serializers.CharField(read_only=True, allow_null=True)
    last_name = serializers.CharField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)


class AddMemberSerializer(serializers.Serializer):
    phone_number = serializers.CharField(max_length=32)
    role = serializers.ChoiceField(
        choices=["owner", "admin", "manager", "receptionist", "housekeeping"],
        default="manager",
    )


class UpdateMemberRoleSerializer(serializers.Serializer):
    role = serializers.ChoiceField(
        choices=["owner", "admin", "manager", "receptionist", "housekeeping"],
    )


class PmsOtpSendResponseSerializer(serializers.Serializer):
    detail = serializers.CharField()
    phone_number = serializers.CharField()
    expires_in = serializers.CharField()


class PmsMeResponseSerializer(serializers.Serializer):
    user = PlatformUserSerializer()
    organization = OrganizationSerializer(allow_null=True, required=False, default=None)
    organizations = OrganizationSerializer(many=True)


class PmsSwitchOrgResponseSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    organization = OrganizationSerializer()


class PmsTokenRefreshResponseSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
