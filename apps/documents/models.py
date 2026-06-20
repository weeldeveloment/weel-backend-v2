from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

from shared.models import HardDeleteBaseModel


class DocumentType:
    INVOICE = "invoice"
    AGREEMENT = "agreement"
    ADDITIONAL_AGREEMENT = "additional_agreement"
    CERTIFICATE = "certificate"
    RECONCILIATION = "reconciliation"
    VOUCHER = "voucher"
    POWER_OF_ATTORNEY = "power_of_attorney"
    OTHER = "other"

    CHOICES = [INVOICE, AGREEMENT, ADDITIONAL_AGREEMENT, CERTIFICATE, RECONCILIATION, VOUCHER, POWER_OF_ATTORNEY, OTHER]


class DocumentStatus:
    CREATED = "created"
    SENT = "sent"
    SIGNED = "signed"
    REJECTED = "rejected"

    CHOICES = [CREATED, SENT, SIGNED, REJECTED]


class RecipientType:
    CLIENT = "client"
    PARTNER = "partner"
    HOTEL = "hotel"
    B2B = "b2b"

    CHOICES = [CLIENT, PARTNER, HOTEL, B2B]


@dataclass(slots=True)
class Document(HardDeleteBaseModel):
    id: int | None = None
    doc_number: str | None = None
    doc_type: str = DocumentType.INVOICE
    organization_id: int | None = None
    company_id: int | None = None
    partner_id: int | None = None
    booking_id: int | None = None
    trip_id: int | None = None
    amount: Decimal | None = None
    status: str = DocumentStatus.CREATED
    pdf_url: str | None = None
    notes: str | None = None
    created_by: int | None = None
    _meta = SimpleNamespace(db_table="document")


@dataclass(slots=True)
class DocumentRecipient(HardDeleteBaseModel):
    id: int | None = None
    document_id: int | None = None
    recipient_type: str = RecipientType.CLIENT
    inn: str | None = None
    org_name: str | None = None
    bank_details: dict = field(default_factory=dict)
    _meta = SimpleNamespace(db_table="document_recipient")
