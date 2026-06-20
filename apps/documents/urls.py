from django.urls import path
from apps.documents.views import (
    DocumentListCreateView,
    DocumentRetrieveView,
    DocumentStatusView,
    DocumentRecipientCreateView,
)

urlpatterns = [
    path("", DocumentListCreateView.as_view(), name="documents-list"),
    path("<int:doc_id>/", DocumentRetrieveView.as_view(), name="document-detail"),
    path("<int:doc_id>/status/", DocumentStatusView.as_view(), name="document-status"),
    path("<int:doc_id>/recipients/", DocumentRecipientCreateView.as_view(), name="document-recipients"),
]
