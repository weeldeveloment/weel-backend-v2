from apps.property.views import PropertyListCreateView
from django.test import RequestFactory
import traceback
request = RequestFactory().get('/api/property/properties/')
try:
    response = PropertyListCreateView.as_view()(request)
    print("STATUS_CODE:", response.status_code)
except Exception:
    traceback.print_exc()
