PYTHON ?= venv/bin/python
TEST_APPS = users shared payment property stories booking notification bot sanatorium chat admin_auth
PYTHONWARNINGS ?= ignore::Warning:requests

run:
    # Run local server
	command $(PYTHON) manage.py runserver
migrate:
    # Migrate models in the database
	command $(PYTHON) manage.py makemigrations && $(PYTHON) manage.py migrate
superuser:
    # Create superuser in the database
	command $(PYTHON) manage.py createsuperuser

# --- Test DB (asosiy baza ichida test_schema, asosiy ma'lumotlarga ta'sir yo'q) ---
test-db-create:
	command $(PYTHON) manage.py create_test_db
test-db-migrate: test-db-create
	command $(PYTHON) manage.py migrate --settings=core.settings_test_db
test-db: test-db-migrate
# test_schema da testlarni ishga tushiradi
test: test-db-migrate
	command $(PYTHON) manage.py test --settings=core.settings_test_db --keepdb
# To'liq: schema + migrate + test
test-all: test

# CI/local pre-deploy automation tests (isolated, no external DB/Redis required)
test-ci:
	PYTHONWARNINGS="$(PYTHONWARNINGS)" command $(PYTHON) manage.py check --settings=core.test_settings
	PYTHONWARNINGS="$(PYTHONWARNINGS)" command $(PYTHON) manage.py test $(TEST_APPS) --settings=core.test_settings

# --- Partner property reminder SMS (manual test helpers) ---
# Example: make sms-reminder-one PARTNER_ID=123
sms-reminder-one:
	@if [ -z "$(PARTNER_ID)" ]; then echo "Usage: make sms-reminder-one PARTNER_ID=<id>"; exit 1; fi
	command $(PYTHON) manage.py shell -c "from users.models import Partner; from users.services import EskizService; from users.models.logs import SmsLog, SmsPurpose; from users.tasks import PARTNER_PROPERTY_CHECK_REMINDER_TEXT; p=Partner.objects.get(id=int('$(PARTNER_ID)')); result=EskizService().send_text_sms(phone_number=p.phone_number, message=PARTNER_PROPERTY_CHECK_REMINDER_TEXT); SmsLog.objects.create(phone_number=p.phone_number, purpose=SmsPurpose.PARTNER_PROPERTY_REMINDER, is_sent=True); print({'partner_id': p.id, 'phone': p.phone_number, 'result': result})"

# Run the periodic task now (3-day rule is applied inside task)
sms-reminder-run:
	command $(PYTHON) manage.py shell -c "from users.tasks import send_partner_property_check_reminders; print(send_partner_property_check_reminders())"

# Last 20 reminder SMS logs
sms-reminder-logs:
	command $(PYTHON) manage.py shell -c "from users.models.logs import SmsLog, SmsPurpose; rows=SmsLog.objects.filter(purpose=SmsPurpose.PARTNER_PROPERTY_REMINDER).order_by('-created_at').values('phone_number','is_sent','created_at')[:20]; print(list(rows))"

# Partners that currently have at least one active property
sms-reminder-partners:
	command $(PYTHON) manage.py shell -c "from users.models import Partner; qs=Partner.objects.filter(is_active=True, partner__is_archived=False).exclude(phone_number='').distinct().values('id','first_name','last_name','phone_number')[:50]; print(list(qs))"
