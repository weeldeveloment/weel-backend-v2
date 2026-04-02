"""
Test asosiy baza ichida, alohida schema da (test_schema).
Asosiy ma'lumotlar (public) ta'sirlanmaydi. Jadvallar faqat test_schema da yaratiladi.

Ishlatish:
  python manage.py create_test_db   # test_schema yaratadi (asosiy bazada)
  python manage.py migrate --settings=core.settings_test_db
  python manage.py test --settings=core.settings_test_db --keepdb
"""
import os

from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(), override=True)

from .settings import *  # noqa: F401, F403

# Asosiy baza bilan bir xil — faqat search_path = test_schema, shuning uchun jadvallar public ga emas, test_schema ga yoziladi
_TEST_SCHEMA = os.environ.get("TEST_DB_SCHEMA", "test_schema")

_default_db = {**DATABASES["default"]}
_default_db["OPTIONS"] = {
    **DATABASES["default"].get("OPTIONS", {}),
    "options": f"-c search_path={_TEST_SCHEMA},public",
}
# Test runner yangi baza yaratmasin — xuddi shu baza + test_schema ishlatilsin
_default_db["TEST"] = {"NAME": DATABASES["default"].get("NAME")}
DATABASES = {
    "default": _default_db,
}
