from __future__ import annotations

import logging
from typing import Any

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_yasg.utils import swagger_auto_schema

from apps.documents.repository import (
    add_document_recipient,
    create_document,
    get_document,
    get_document_recipients,
    list_documents,
    update_document_status,
)
from apps.documents.serializers import (
    DocumentRecipientSerializer,
    DocumentSerializer,
    DocumentStatusSerializer,
    DocumentWithRecipientsSerializer,
)

logger = logging.getLogger(__name__)


def _get_user_id(request) -> int | None:
    user = getattr(request, "user", None)
    if isinstance(user, dict):
        return user.get("id")
    return getattr(user, "id", None)


def _get_org_or_company(request) -> dict[str, Any]:
    user = getattr(request, "user", None)
    result: dict[str, Any] = {}
    if isinstance(user, dict):
        if user.get("organization_id"):
            result["organization_id"] = user["organization_id"]
        if user.get("company_id"):
            result["company_id"] = user["company_id"]
    else:
        org = getattr(request, "organization", None)
        if org:
            result["organization_id"] = org if isinstance(org, int) else org.get("id")
    return result


class DocumentListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(responses={200: DocumentSerializer(many=True)})
    def get(self, request):
        context = _get_org_or_company(request)
        doc_type = request.query_params.get("doc_type")
        status_filter = request.query_params.get("status")
        trip_id = request.query_params.get("trip_id")
        search = request.query_params.get("search")

        docs = list_documents(
            **context,
            doc_type=doc_type,
            status=status_filter,
            trip_id=int(trip_id) if trip_id else None,
            search=search,
        )
        return Response(DocumentSerializer(docs, many=True).data)

    @swagger_auto_schema(request_body=DocumentSerializer, responses={201: DocumentSerializer()})
    def post(self, request):
        serializer = DocumentSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated = serializer.validated_data
        context = _get_org_or_company(request)

        doc = create_document(
            doc_type=validated["doc_type"],
            created_by=_get_user_id(request),
            **context,
            **{k: v for k, v in validated.items() if k not in ("doc_type", "organization_id", "company_id")},
        )
        if not doc:
            return Response({"detail": "Failed to create document."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(DocumentSerializer(doc).data, status=status.HTTP_201_CREATED)


class DocumentRetrieveView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(responses={200: DocumentWithRecipientsSerializer()})
    def get(self, request, doc_id):
        doc = get_document(doc_id)
        if not doc:
            return Response({"detail": "Document not found."}, status=status.HTTP_404_NOT_FOUND)
        recipients = get_document_recipients(doc_id)
        doc["recipients"] = recipients
        return Response(DocumentWithRecipientsSerializer(doc).data)


class DocumentStatusView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(request_body=DocumentStatusSerializer, responses={200: DocumentSerializer()})
    def patch(self, request, doc_id):
        serializer = DocumentStatusSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        doc = update_document_status(doc_id, serializer.validated_data["status"])
        if not doc:
            return Response({"detail": "Document not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(DocumentSerializer(doc).data)


class DocumentRecipientCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(request_body=DocumentRecipientSerializer, responses={201: DocumentRecipientSerializer()})
    def post(self, request, doc_id):
        doc = get_document(doc_id)
        if not doc:
            return Response({"detail": "Document not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = DocumentRecipientSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        recipient = add_document_recipient(
            document_id=doc_id,
            **serializer.validated_data,
        )
        if not recipient:
            return Response({"detail": "Failed to add recipient."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(DocumentRecipientSerializer(recipient).data, status=status.HTTP_201_CREATED)
