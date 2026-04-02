from datetime import date
from decimal import Decimal, InvalidOperation

from dateutil.relativedelta import relativedelta
from django.utils.translation import gettext_lazy as _

from rest_framework import serializers

from .models import PropertyType
from shared.date import month_start, month_end, parse_yyyy_mm_dd


class LanguageFieldMixin:
    """
    A mixin to dynamically return translated model fields
    based on requests Accept-Language header
    """

    DEFAULT_LANGUAGE = "ru"
    SUPPORTED_LANGUAGE = {"en", "ru", "uz"}

    def get_lang(self):
        """Extract language code from the request"""
        request = self.context.get("request")
        lang = self.DEFAULT_LANGUAGE

        if request:
            accept_language = request.headers.get("Accept-Language")
            if accept_language:
                # Keep only first part lang ("en-Us" -> "en")"""
                lang = accept_language.split(",")[0].split("-")[0].lower()

        if lang not in self.SUPPORTED_LANGUAGE:
            lang = self.DEFAULT_LANGUAGE
        return lang

    def get_lang_field(self, obj, field: str):
        """Dynamically get translated field based on language"""
        lang = self.get_lang()
        lang_field = f"{field}_{lang}"
        # Fallback, if missing default language
        return getattr(
            obj, lang_field, getattr(obj, f"{field}_{self.DEFAULT_LANGUAGE}", None)
        )


class PropertyServicesValidateMixin:
    def _get_property_type(self):
        initial = getattr(self, "initial_data", {})

        property_type_id = initial.get("property_type_id")
        if property_type_id is not None:
            property_type_id = str(property_type_id).strip()
        if property_type_id:
            property_type = PropertyType.objects.filter(guid=property_type_id).first()
            if property_type is None:
                raise serializers.ValidationError(_("Invalid property type"))
            return property_type

        # Update serializer with existing instance
        if hasattr(self, "instance") and self.instance:
            return getattr(self.instance.property, "property_type", None)

        return None

    def validate_property_services(self, value):
        property_type = self._get_property_type()

        if property_type is None:
            raise serializers.ValidationError(_("Property type not found"))

        # Frontend barcha xizmatlarni yuborishi mumkin; faqat shu property type ga tegishlilarini saqlaymiz
        value = [s for s in value if s.property_type == property_type]

        if len(value) != len(set(value)):
            raise serializers.ValidationError(_("Duplication is prohibited"))
        return value


class PropertyPriceValidateMixin:
    def validate_single_price(self, price):
        try:
            decimal_value = Decimal(price)
        except (InvalidOperation, TypeError, ValueError):
            raise serializers.ValidationError("Price must be a valid number")

        total_digits = len(str(decimal_value).replace(".", "").replace("-", ""))
        if total_digits > 12:
            raise serializers.ValidationError(
                _("Price is too large, maximum 12 digits allowed")
            )
        return decimal_value

    def validate_property_price(self, value, property_type):
        if property_type is None:
            raise serializers.ValidationError(_("Invalid property type"))

        # price: ro'yxat yoki bitta raqam (raqam bo'lsa joriy oy uchun avtomatik ro'yxat)
        if not isinstance(value, list):
            try:
                single_price = self.validate_single_price(value)
            except serializers.ValidationError:
                raise serializers.ValidationError(
                    _(
                        "Price must be a list of objects with: "
                        "month_from, month_to, price_per_person, price_on_weekends, price_on_working_days. "
                        "Or use a single number for current month (e.g. 100)."
                    )
                )
            today = date.today()
            value = [
                {
                    "month_from": today.isoformat(),
                    "month_to": month_end(today).isoformat(),
                    "price_per_person": 0,
                    "price_on_working_days": single_price,
                    "price_on_weekends": single_price,
                }
            ]

        same_months = set()

        required_fields = [
            "price_per_person",
            "price_on_weekends",
            "price_on_working_days",
        ]
        for idx, price_dict in enumerate(value):
            if not price_dict.get("month_from") or not price_dict.get("month_to"):
                raise serializers.ValidationError(
                    _("month_from and month_to are required")
                )

            month_from = parse_yyyy_mm_dd(
                price_dict["month_from"],
                field_path="month_from",
            )
            month_to = parse_yyyy_mm_dd(
                price_dict["month_to"],
                field_path="month_to",
            )

            m_from = month_start(month_from)
            if m_from in same_months:
                raise serializers.ValidationError(
                    _("Duplication price for this month isn't allowed")
                )

            same_months.add(m_from)
            self.validate_property_price_month_range(
                month_from=month_from,
                month_to=month_to,
            )

            if not isinstance(price_dict, dict):
                raise serializers.ValidationError(
                    _("Each item in price list must be a dict")
                )

            missing_fields = [
                required_field
                for required_field in required_fields
                if required_field not in price_dict
            ]

            partial = getattr(self, "partial", False)
            if not partial:
                if missing_fields:
                    raise serializers.ValidationError(
                        _("Missing fields for price: ")
                        + ", ".join(missing_fields)
                    )

            # Validate each present required field is a valid decimal
            for required_field in set(required_fields) & price_dict.keys():
                self.validate_single_price(price_dict[required_field])

        return value

    def validate_property_price_month_range(self, month_from, month_to):
        today = date.today()
        current_month = month_start(today)
        # # relativedelta is similar to timedelta, but it understands calendar rules
        # # (months, years, leap years, end-of-month logic, etc)
        next_month = month_start(today + relativedelta(months=1))

        # normalize
        m_from = month_start(month_from)

        if month_start(month_to) != m_from:
            raise serializers.ValidationError(
                _("Price should only be determined for one month")
            )

        if m_from < current_month:
            raise serializers.ValidationError(
                _("You can't modify prices for past months")
            )

        if m_from not in (current_month, next_month):
            raise serializers.ValidationError(
                _("You can set only price for current or next month")
            )

        expected_to = month_end(m_from)
        # Normalize to full-month coverage; month_from is stored as month start.
        if month_to != expected_to:
            raise serializers.ValidationError(_("Price should cover whole month"))

        return m_from, expected_to
