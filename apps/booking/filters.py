from datetime import date

from dateutil.relativedelta import relativedelta
from django_filters import rest_framework as filters
from django.utils.translation import gettext_lazy as _

from rest_framework.exceptions import ValidationError

from .models import CalendarDate
from shared.date import month_start

class PropertyCalenderDateFilter(filters.FilterSet):
    from_date = filters.DateFilter(
        field_name="date",
        lookup_expr="gte",
        required=True,
    )
    to_date = filters.DateFilter(
        field_name="date",
        lookup_expr="lte",
        required=True,
    )
    status = filters.ChoiceFilter(
        field_name="status",
        choices=CalendarDate.CalendarStatus.choices,
    )

    class Meta:
        model = CalendarDate
        fields = ["from_date", "to_date", "status"]

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)

        from_date = self.form.cleaned_data.get("from_date")
        to_date = self.form.cleaned_data.get("to_date")

        if not from_date or not to_date:
            raise ValidationError(_("from_date and to_date are required"))

        if from_date and to_date:
            if from_date >= to_date:
                raise ValidationError(_("to_date must be greater than from_date"))

            today = date.today()
            current_month = month_start(today)
            next_month = month_start(today + relativedelta(months=1))

            from_month = month_start(from_date)
            to_month = month_start(to_date)

            if from_month not in {current_month, next_month}:
                raise ValidationError(
                    _("Calendar range can start only in current or next months")
                )

            if from_month == current_month and from_date < today:
                raise ValidationError(
                    _("For the current month, from_date must be today or later")
                )

            # Calculate month difference correctly
            month_diff = (to_month.year - from_month.year) * 12 + (
                to_month.month - from_month.month
            )

            if month_diff > 1:
                raise ValidationError(
                    _("Calendar range can cover at most current and next month")
                )

        return queryset
