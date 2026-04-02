import json
import codecs
import logging

from pythonjsonlogger import json as jsonlogger, core
from pythonjsonlogger.core import LogData


# UTF-8 cyrillic bytes decoded as Latin-1 produce these characters
MOJIBAKE_MARKERS = "\xd0\xd1"


def decode_bytes_string(message):
    """Decode common UTF-8 encoding issues in log messages"""
    if not isinstance(message, str):
        return message

    # remove bytes wrapper: b'...' or b"..."
    if (
        len(message) > 3
        and message[0] == "b"
        and message[1] in ("'", '"')
        and message[-1] == message[1]
    ):
        message = message[2:-1]

    # decode \xNN escape sequences
    if "\\x" in message:
        try:
            message = codecs.decode(message, "unicode_escape")
        except Exception:
            pass

    # mojibake (UTF-8 decoded as Latin-1)
    if any(c in message for c in MOJIBAKE_MARKERS):
        try:
            message = message.encode("latin-1").decode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass

    # try to parse as JSON
    try:
        return json.loads(message)
    except (json.JSONDecodeError, TypeError):
        return message


class UnicodeJsonFormatter(jsonlogger.JsonFormatter):
    """JSON formatter with proper UTF-8 support"""

    def __init__(self, *args, **kwargs):
        kwargs["json_ensure_ascii"] = False
        super().__init__(*args, **kwargs)

    def process_log_record(self, log_data: LogData) -> LogData:
        for key, value in log_data.items():
            if isinstance(value, str):
                log_data[key] = decode_bytes_string(value)
        return super().process_log_record(log_data)

    def jsonify_log_record(self, log_data: core.LogData) -> str:
        return json.dumps(log_data, ensure_ascii=False, default=str)


class UnicodeConsoleFormatter(logging.Formatter):
    """Console formatter with proper UTF-8 support"""

    def format(self, record):
        if hasattr(record, "msg") and isinstance(record.msg, str):
            record.msg = decode_bytes_string(record.msg)
        return super().format(record)
