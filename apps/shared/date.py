from datetime import date
from dateutil.relativedelta import relativedelta

from django.utils.translation import gettext_lazy as _

from rest_framework import serializers


def month_start(d: date) -> date:
    return d.replace(day=1)


def month_end(d: date) -> date:
    return (d.replace(day=1) + relativedelta(months=1)) - relativedelta(days=1)


def parse_yyyy_mm_dd(value, field_path: str) -> date:
    try:
        return date.fromisoformat(value)
    except Exception as e:
        raise serializers.ValidationError(
            {f"{field_path}": _("Must be a date string YYYY-MM-DD")}
        )
