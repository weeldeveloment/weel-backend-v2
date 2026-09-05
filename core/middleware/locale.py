from django.conf import settings
from django.utils import translation


class HeaderLocaleMiddleware:
    """
    Custom locale middleware that checks X-Language header first,
    then falls back to Accept-Language. Must be placed BEFORE
    django.middleware.locale.LocaleMiddleware in MIDDLEWARE.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        language = self.get_language_from_header(request)
        if language:
            translation.activate(language)
            request.LANGUAGE_CODE = language
            # Django's own LocaleMiddleware runs next and re-resolves the
            # language from the cookie and Accept-Language, falling back to
            # LANGUAGE_CODE — which silently undid an X-Language-only request.
            # Feeding it the same answer keeps both middlewares in agreement.
            request.META["HTTP_ACCEPT_LANGUAGE"] = language
        response = self.get_response(request)
        return response

    @staticmethod
    def get_language_from_header(request):
        # Mobile/frontend apps usually send X-Language header
        lang = request.headers.get("X-Language")
        if not lang:
            # Standard browser header fallback
            lang = request.headers.get("Accept-Language")

        if lang:
            # Take first language if Accept-Language contains multiple
            lang = lang.split(",")[0].strip().lower()
            if lang.startswith("en"):
                return "en"
            elif lang.startswith("ru"):
                return "ru"
            elif lang.startswith("uz"):
                return "uz"

        return None
