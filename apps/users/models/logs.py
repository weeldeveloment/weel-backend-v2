from dataclasses import dataclass

from shared.raw.choices import ChoiceEnum, build_choices


class SmsPurpose(ChoiceEnum):
    LOGIN = "CL_LGN"
    REGISTER = "CL_RGR"
    PARTNER_LOGIN = "PR_LGN"
    PARTNER_REGISTER = "PR_RGR"
    PARTNER_PROPERTY_REMINDER = "PR_RMD"
    PMS_LOGIN = "PM_LGN"
    PMS_REGISTER = "PM_RGR"
    B2B_LOGIN = "B2_LGN"
    ACCOUNT_DELETE = "AC_DEL"
    PMS_ACCOUNT_DELETE = "PM_DEL"


SmsPurpose.choices = build_choices(SmsPurpose)


@dataclass(slots=True)
class SmsLog:
    phone_number: str
    purpose: str
    is_sent: bool = True
