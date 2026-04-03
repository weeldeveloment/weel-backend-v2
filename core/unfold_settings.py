from django.templatetags.static import static
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

UNFOLD = {
    "SITE_TITLE": _("WEEL"),
    "SITE_HEADER": _("Booking Service"),
    "SITE_SUBHEADER": _("Booking Service"),
    "SITE_DROPDOWN": [
        {
            "icon": "diamond",
            "title": _("My site"),
            "link": "https://example.com",
        },
        # ...
    ],
    "SITE_URL": "/",
    # "SITE_ICON": lambda request: static("icon.svg"),  # both modes, optimise for 32px height
    "SITE_ICON": {
        "light": lambda request: static("logo/weel.svg"),  # light mode
        "dark": lambda request: static("logo/weel.svg"),  # dark mode
    },
    # "SITE_LOGO": lambda request: static("logo.svg"),  # both modes, optimise for 32px height
    "SITE_LOGO": {
        "light": lambda request: static("logo/weel.svg"),  # light mode
        "dark": lambda request: static("logo/weel.svg"),  # dark mode
    },
    "SITE_SYMBOL": "speed",  # symbol from icon set
    "SITE_FAVICONS": [
        {
            "rel": "icon",
            "sizes": "32x32",
            "type": "image/svg+xml",
            "href": lambda request: static("logo/weel.svg"),
        },
    ],
    "SHOW_HISTORY": True,  # show/hide "History" button, default: True
    "SHOW_VIEW_ON_SITE": True,  # show/hide "View on site" button, default: True
    "SHOW_BACK_BUTTON": False,  # show/hide "Back" button on changeform in header, default: False
    "LOGIN": {
        "image": lambda request: static("images/login-bg.jpg"),
        "redirect_after": lambda request: reverse_lazy("admin:APP_MODEL_changelist"),
    },
    "BORDER_RADIUS": "6px",
    "COLORS": {
        "base": {
            "50": "oklch(98.5% .002 247.839)",
            "100": "oklch(96.7% .003 264.542)",
            "200": "oklch(92.8% .006 264.531)",
            "300": "oklch(87.2% .01 258.338)",
            "400": "oklch(70.7% .022 261.325)",
            "500": "oklch(55.1% .027 264.364)",
            "600": "oklch(44.6% .03 256.802)",
            "700": "oklch(37.3% .034 259.733)",
            "800": "oklch(27.8% .033 256.848)",
            "900": "oklch(21% .034 264.665)",
            "950": "oklch(13% .028 261.692)",
        },
        "primary": {
            "50": "oklch(97.7% .014 308.299)",
            "100": "oklch(94.6% .033 307.174)",
            "200": "oklch(90.2% .063 306.703)",
            "300": "oklch(82.7% .119 306.383)",
            "400": "oklch(71.4% .203 305.504)",
            "500": "oklch(62.7% .265 303.9)",
            "600": "oklch(55.8% .288 302.321)",
            "700": "oklch(49.6% .265 301.924)",
            "800": "oklch(43.8% .218 303.724)",
            "900": "oklch(38.1% .176 304.987)",
            "950": "oklch(29.1% .149 302.717)",
        },
        "font": {
            "subtle-light": "var(--color-base-500)",  # text-base-500
            "subtle-dark": "var(--color-base-400)",  # text-base-400
            "default-light": "var(--color-base-600)",  # text-base-600
            "default-dark": "var(--color-base-300)",  # text-base-300
            "important-light": "var(--color-base-900)",  # text-base-900
            "important-dark": "var(--color-base-100)",  # text-base-100
        },
    },
    "LANGUAGES": {
        "navigation": [
            {
                "bidi": False,
                "code": "en",
                "name": "English",
                "name_local": "English",
                "name_translated": _("English"),
            },
            {
                "bidi": False,
                "code": "ru",
                "name": "Russian",
                "name_local": "Русский",
                "name_translated": _("Russian"),
            },
            {
                "bidi": False,
                "code": "uz",
                "name": "Uzbek",
                "name_local": "Oʻzbek",
                "name_translated": _("Uzbek"),
            },
        ],
    },
    "SHOW_LANGUAGES": True,
    "SIDEBAR": {
        "show_search": True,  # Search in applications and models names
        "command_search": False,  # Replace the sidebar search with the command search
        "show_all_applications": True,  # Dropdown with all applications and models
        "navigation": [
            {
                "title": _("Navigation"),
                "separator": True,  # Top border
                "collapsible": True,  # Collapsible group of links
                "items": [
                    # {
                    #     "title": _("Dashboard"),
                    #     "icon": "dashboard",  # Supported icon set: https://fonts.google.com/icons
                    #     # "link": reverse_lazy("admin:index"),
                    #     "badge": None,
                    #     "permission": lambda request: request.user.is_superuser,
                    # },
                    {
                        "title": _("Users"),
                        "icon": "user_attributes",
                        "link": reverse_lazy("admin:app_list", args=["users"]),
                        "badge": None,
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": _("Properties"),
                        "icon": "apartment",
                        "link": reverse_lazy("admin:app_list", args=["property"]),
                        "badge": None,
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": _("Stories"),
                        "icon": "web_stories",
                        "link": reverse_lazy("admin:app_list", args=["stories"]),
                        "badge": None,
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": _("Payments"),
                        "icon": "payments",
                        "link": reverse_lazy("admin:app_list", args=["payment"]),
                        "badge": None,
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": _("Booking"),
                        "icon": "event",
                        "link": reverse_lazy("admin:app_list", args=["booking"]),
                        "badge": None,
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": _("Sanatorium"),
                        "icon": "local_hospital",
                        "link": reverse_lazy("admin:app_list", args=["..sanatorium"]),
                        "badge": None,
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": _("Notification"),
                        "icon": "notifications",
                        "link": reverse_lazy("admin:app_list", args=["notification"]),
                        "badge": None,
                        "permission": lambda request: request.user.is_superuser,
                    },
                ],
            },
        ],
    },
    # "TABS": [
    #     {
    #         "models": [
    #             "app_label.model_name_in_lowercase",
    #         ],
    #         "items": [
    #             {
    #                 "title": _("Your custom title"),
    #                 "link": reverse_lazy("admin:app_label_model_name_changelist"),
    #                 "permission": "sample_app.permission_callback",
    #             },
    #         ],
    #     },
    # ],
}
