from PIL import Image

from rest_framework import exceptions
from rest_framework.response import Response
from rest_framework.views import exception_handler
from rest_framework.settings import api_settings


class ErrorsFormatter:
    """
    The current formatter gets invalid serializer errors,
    uses DRF standard for status code and messaging
    and then parses it to the following format:
    {
        "errors": [
            {
                "detail": "Error message",
                "status_code": "Some code",
                "field": "field_name"
            },
            {
                "detail": "Error message",
                "status_code": "Some code",
                "field": "nested.field_name"
            },
            ...
        ]
    }
    """

    FIELD = "field"
    DETAIL = "detail"
    STATUS_CODE = "status_code"
    ERRORS = "errors"

    def __init__(self, exception, status_code):
        self.exception = exception
        self.status_code = status_code

    def __call__(self):
        if hasattr(self.exception, "get_full_details"):
            formatted_errors = self._get_response_json_from_drf_errors(
                serializer_errors=self.exception.get_full_details()
            )
        else:
            formatted_errors = self._get_response_json_from_error_message(
                message=str(self.exception)
            )

        return formatted_errors

    def _get_response_json_from_drf_errors(self, serializer_errors=None):
        if serializer_errors is None:
            serializer_errors = {}

        if isinstance(serializer_errors, list):
            errors = []
            for index, item in enumerate(serializer_errors):
                errors += self._get_list_of_errors(
                    field_path=str(index), errors_dict=item
                )
            return {self.ERRORS: errors}

        list_of_errors = self._get_list_of_errors(errors_dict=serializer_errors)

        response_data = {self.ERRORS: list_of_errors}

        return response_data

    def _get_response_json_from_error_message(self, *, message="", field=None):
        error = {
            self.DETAIL: message,
            self.STATUS_CODE: self.status_code,
        }

        if field:
            error[self.FIELD] = field
        return {self.ERRORS: [error]}

    @staticmethod
    def _unpack(obj):
        if type(obj) is list and len(obj) == 1:
            return obj[0]

        return obj

    def _get_list_of_errors(self, field_path="", errors_dict=None):
        """
        Error_dict is in the following format:
        {
            'field1': {
                'detail': 'some message...'
                'code' 'some code...'
            },
            'field2: ...'
        }
        """
        if errors_dict is None:
            return []

        if isinstance(errors_dict, (exceptions.ErrorDetail, str)):
            return [
                {
                    self.DETAIL: str(errors_dict),
                    self.STATUS_CODE: self.status_code,
                    **({self.FIELD: field_path} if field_path else {}),
                }
            ]

        message_value = errors_dict.get(self.DETAIL, None)

        if message_value is not None and isinstance(
            message_value, (str, exceptions.ErrorDetail)
        ):
            error = {
                self.DETAIL: str(message_value),
                self.STATUS_CODE: self.status_code,
            }

            if field_path:
                error[self.FIELD] = field_path
            return [error]

        errors_list = []
        for key, value in errors_dict.items():
            new_field_path = "{0}.{1}".format(field_path, key) if field_path else key
            key_is_non_field_errors = key == api_settings.NON_FIELD_ERRORS_KEY

            if isinstance(value, list):
                for error in value:
                    # if the type of field_error is list we need to unpack it
                    field_error = self._unpack(error)

                    if isinstance(field_error, dict):
                        message = field_error.get(self.DETAIL) or field_error.get(
                            "message"
                        )
                    else:
                        message = str(field_error)

                    formatted = {
                        self.DETAIL: message,
                        self.STATUS_CODE: self.status_code,
                    }

                    if not key_is_non_field_errors:
                        formatted[self.FIELD] = new_field_path

                    errors_list.append(formatted)
            else:
                path = field_path if key_is_non_field_errors else new_field_path
                errors_list += self._get_list_of_errors(path, value)

        return errors_list


def exception_errors_format_handler(exc, context):
    # Catch Pillow's DecompressionBombError
    if isinstance(exc, Image.DecompressionBombError):
        return Response(
            {"images": {"Image resolution is too high. Please upload a smaller image"}},
            status=400,
        )

    response = exception_handler(exc, context)

    # If an unexpected error occurs (server error, etc.)
    if response is None:
        return response
    try:
        code = exc.get_codes()
        if isinstance(code, dict):
            code = code.get("code")
        elif not isinstance(code, str):
            code = None
        if code != "token_not_valid":
            formatter = ErrorsFormatter(exc, response.status_code)
            response.data = formatter()
            # Plum API xatolari errorCode/errorMessage ko‘rinishida keladi — bitta detail ga qisqartiramiz
            response.data = _normalize_plum_like_errors(response.data)
        else:
            # Token xato — bitta formatda va aniq yo‘riqnoma bilan qaytaramiz
            response.data = _format_token_error_response(response)
    except Exception as e:
        formatter = ErrorsFormatter(exc, response.status_code)
        response.data = formatter()
        response.data = _normalize_plum_like_errors(response.data)
    return response


def _format_token_error_response(response):
    """Token not valid xatosi uchun bitta format va yo‘riqnoma."""
    detail = "Token is invalid or expired."
    if response.data and isinstance(response.data, dict):
        if "detail" in response.data:
            d = response.data["detail"]
            detail = d if isinstance(d, str) else (d[0] if isinstance(d, list) else detail)
    return {
        ErrorsFormatter.ERRORS: [
            {
                ErrorsFormatter.DETAIL: detail,
                ErrorsFormatter.STATUS_CODE: response.status_code,
                "hint": "Use the Access token (from login/verify). If expired, use POST /api/users/refresh/ with Refresh token to get new tokens.",
            }
        ]
    }


def _normalize_plum_like_errors(data):
    """Plum/API xatolarini bitta detail ga qisqartiramiz (errorCode/errorMessage yoki 0.message/0.code)."""
    if not data or not isinstance(data, dict):
        return data
    errors = data.get(ErrorsFormatter.ERRORS)
    if not isinstance(errors, list) or len(errors) <= 1:
        return data
    fields = [e.get("field") for e in errors if isinstance(e, dict) and e.get("field")]

    # errorMessage.message / errorCode.message (Plum format)
    if "errorMessage.message" in fields or "errorCode.message" in fields:
        detail = None
        for e in errors:
            if isinstance(e, dict) and e.get("field") == "errorMessage.message" and e.get("detail"):
                detail = e["detail"]
                break
        if detail is None:
            for e in errors:
                if isinstance(e, dict) and e.get("field") == "errorCode.message" and e.get("detail"):
                    detail = e["detail"]
                    break
        if detail is not None:
            return _single_error_response(errors, detail)

    # 0.message / 0.code (ValidationError list format — masalan "Invalid response from Plum API")
    idx_message = next(
        (
            i
            for i, e in enumerate(errors)
            if isinstance(e, dict)
            and e.get("field") == "0.message"
            and e.get("detail")
        ),
        None,
    )
    if idx_message is not None:
        return _single_error_response(errors, errors[idx_message]["detail"])

    # message / code (PermissionDenied va boshqa DRF exception format)
    if "message" in fields and "code" in fields:
        for e in errors:
            if isinstance(e, dict) and e.get("field") == "message" and e.get("detail"):
                return _single_error_response(errors, e["detail"])

    for e in errors:
        if isinstance(e, dict) and e.get("field") and ".message" in e.get("field", "") and e.get("detail"):
            return _single_error_response(errors, e["detail"])

    return data


def _single_error_response(errors, detail):
    status_code = errors[0].get("status_code", 400) if errors else 400
    return {
        ErrorsFormatter.ERRORS: [
            {
                ErrorsFormatter.DETAIL: detail,
                ErrorsFormatter.STATUS_CODE: status_code,
            }
        ]
    }
