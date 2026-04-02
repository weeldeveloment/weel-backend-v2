from datetime import date

from dateutil.relativedelta import relativedelta
from rest_framework.serializers import ValidationError

from django.utils.translation import gettext_lazy as _

from shared.date import month_start


class DateRangeValidationMixin:
    """Reusable validation logic for date ranges"""

    start_field = "from_date"
    end_field = "to_date"
    is_single_day = True

    def validate_date_range(self, attrs):
        start_date = attrs.get(self.start_field)
        end_date = attrs.get(self.end_field) or start_date

        if not self.is_single_day and start_date >= end_date:
            raise ValidationError(
                {
                    self.end_field: _(
                        "{end_date} must be greater than {start_date}"
                    ).format(end_date=self.end_field, start_date=self.start_field)
                }
            )

        if self.is_single_day and start_date > end_date:
            raise ValidationError(
                {
                    self.end_field: _(
                        "{end_date} must be greater than {start_date}"
                    ).format(end_date=self.end_field, start_date=self.start_field)
                }
            )

        today = date.today()
        current_month = month_start(today)
        next_month = month_start(today + relativedelta(months=1))

        start_month = month_start(start_date)
        end_month = month_start(end_date)

        if start_date < today:
            raise ValidationError({self.start_field: _("Past dates aren't allowed")})

        allowed_months = {current_month, next_month}
        if start_month not in allowed_months or end_month not in allowed_months:
            raise ValidationError(
                _("Date range is allowed only for current and next months")
            )

        if start_month == current_month and start_date < today:
            raise ValidationError(_("Past dates aren't allowed"))

        month_diff = (end_month.year - start_month.year) * 12 + (
            end_month.month - start_month.month
        )
        if month_diff > 1:
            raise ValidationError(
                _("Date range is allowed only for current and next months")
            )

        attrs[self.end_field] = end_date
        attrs["is_single_day"] = start_date == end_date
        return attrs
