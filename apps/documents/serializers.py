from __future__ import annotations

from rest_framework import serializers


DOC_TYPE_CHOICES = [
    "invoice", "agreement", "additional_agreement",
    "certificate", "reconciliation", "voucher",
    "power_of_attorney", "other",
]

DOC_STATUS_CHOICES = ["created", "sent", "signed", "rejected"]


class DocumentRecipientSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    document_id = serializers.IntegerField(read_only=True)
    recipient_type = serializers.ChoiceField(choices=["client", "partner", "hotel", "b2b"])
    inn = serializers.CharField(max_length=20, required=False, allow_blank=True, allow_null=True)
    org_name = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    bank_details = serializers.DictField(required=False, default=dict)
    created_at = serializers.DateTimeField(read_only=True)


class DocumentSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    doc_number = serializers.CharField(read_only=True)
    doc_type = serializers.ChoiceField(choices=DOC_TYPE_CHOICES)
    organization_id = serializers.IntegerField(required=False, allow_null=True)
    company_id = serializers.IntegerField(required=False, allow_null=True)
    partner_id = serializers.IntegerField(required=False, allow_null=True)
    booking_id = serializers.IntegerField(required=False, allow_null=True)
    trip_id = serializers.IntegerField(required=False, allow_null=True)
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, required=False, allow_null=True)
    status = serializers.ChoiceField(choices=DOC_STATUS_CHOICES, read_only=True)
    pdf_url = serializers.CharField(read_only=True, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    created_by = serializers.IntegerField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)


class DocumentStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=DOC_STATUS_CHOICES)


class DocumentWithRecipientsSerializer(DocumentSerializer):
    recipients = DocumentRecipientSerializer(many=True, read_only=True)
