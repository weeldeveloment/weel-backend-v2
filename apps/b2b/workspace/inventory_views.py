"""``/api/b2b/workspace/inventory/`` — the stock room the sales screen opens.

Everything here belongs to the sales module, and inside it the TZ's rights
apply on the server, never only in the UI:

    sales.stock_view        catalogue and sellable balance — every role
    sales.stock_manage      receipts, transfers, counts, the catalogue itself
    sales.stock_write_off   writing stock off
    sales.stock_reprice     repricing
    sales.stock_free_price  selling at a price other than the card's
    sales.stock_view_cost   seeing purchase prices, cost and profit
    sales.stock_import      XLSX in and out

An owner or an administrator passes every check whatever the role editor
says — they are the ones who edit it.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from django.http import HttpResponse
from django.utils.dateparse import parse_datetime, parse_date
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import serializers, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.b2b.workspace import inventory_documents as documents
from apps.b2b.workspace import inventory_io
from apps.b2b.workspace import inventory_repository as inventory
from apps.b2b.workspace.access import Permission, Role
from apps.b2b.workspace.access_repository import record_audit
from apps.b2b.workspace.inventory_documents import DocumentKind, DocumentStatus
from apps.b2b.workspace.inventory_repository import (
    InventoryError,
    MovementKind,
    ProductKind,
    WriteOffReason,
)
from apps.b2b.workspace.permissions import IsWorkspaceUser
from apps.b2b.workspace.secondment import Module
from apps.b2b.workspace.views import WORKSPACE_TAG, WorkspaceAPIView, store_upload


# ─── Rights ───────────────────────────────────────────────────────────────────

def may(user, permission: str) -> bool:
    """One named stock permission, or the standing that outranks the editor."""
    if Role.clean(getattr(user, "role", None)) in Role.ADMINISTRATIVE:
        return True
    try:
        return bool(user.may(permission))
    except Exception:  # noqa: BLE001 - a user object without the map
        return False


def _require(request, permission: str) -> Response | None:
    if may(request.user, permission):
        return None
    return Response(
        {"detail": _("Your role does not allow this stock operation."), "permission": permission},
        status=status.HTTP_403_FORBIDDEN,
    )


COST_FIELDS = ("purchase_price", "stock_value", "markup_percent", "cost_price")


def _hide_costs(rows: Any, request) -> Any:
    """Purchase prices and everything derived from them, blanked for a
    reader without ``stock_view_cost``. Sale prices stay: those are what
    the person sells at."""
    if may(request.user, Permission.STOCK_VIEW_COST):
        return rows

    def strip(row: dict[str, Any]) -> dict[str, Any]:
        out = {k: (None if k in COST_FIELDS else v) for k, v in row.items()}
        if isinstance(out.get("components"), list):
            out["components"] = [strip(c) for c in out["components"]]
        return out

    if isinstance(rows, list):
        return [strip(r) for r in rows]
    if isinstance(rows, dict):
        return strip(rows)
    return rows


def _refusal(exc: InventoryError) -> Response:
    code = (
        status.HTTP_404_NOT_FOUND if exc.code.endswith("not_found")
        else status.HTTP_409_CONFLICT if exc.code in (
            "insufficient_stock", "warehouse_not_empty", "sku_taken", "barcode_taken",
            "already_done", "not_draft", "not_sent",
        )
        else status.HTTP_400_BAD_REQUEST
    )
    body: dict[str, Any] = {"detail": str(exc), "code": exc.code}
    if exc.details is not None:
        body["shortages" if exc.code == "insufficient_stock" else "details"] = exc.details
    return Response(body, status=code)


def _int_param(request, name: str) -> int | None:
    raw = request.query_params.get(name)
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _flag(request, name: str) -> bool:
    return request.query_params.get(name) in ("1", "true", "True")


def _datetime_param(request, name: str, *, end: bool = False):
    """``?from=2026-09-01`` or a full ISO datetime. A bare date on ``to`` is
    read as the end of that day, which is what a person picking a range
    means by it."""
    raw = (request.query_params.get(name) or "").strip()
    if not raw:
        return None
    # A bare date is asked about first: `parse_datetime` would happily read
    # "2026-09-05" as midnight, and midnight is the start of the day the
    # `to` side is meant to include, not the end of it.
    day = parse_date(raw) if len(raw) == 10 else None
    if day is not None:
        value = datetime.combine(day, datetime.min.time())
        if end:
            value = value + timedelta(days=1)
    else:
        value = parse_datetime(raw)
        if value is None:
            return None
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_current_timezone())
    return value


class _InventoryView(WorkspaceAPIView):
    required_module = Module.SALES
    permission_classes = [IsAuthenticated, IsWorkspaceUser]


# ─── Serializers ──────────────────────────────────────────────────────────────

class SettingsSerializer(serializers.Serializer):
    allow_backorder = serializers.BooleanField()
    base_currency = serializers.CharField()
    sku_prefix = serializers.CharField()
    write_off_alert = serializers.DecimalField(max_digits=14, decimal_places=2)


class SettingsWriteSerializer(serializers.Serializer):
    allow_backorder = serializers.BooleanField(required=False)
    base_currency = serializers.CharField(max_length=3, required=False)
    sku_prefix = serializers.CharField(max_length=10, required=False, allow_blank=True)
    write_off_alert = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0, required=False)


class WarehouseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    address = serializers.CharField(allow_null=True, required=False)
    is_default = serializers.BooleanField()
    is_active = serializers.BooleanField()
    product_count = serializers.IntegerField(required=False)
    quantity_total = serializers.DecimalField(max_digits=14, decimal_places=3, required=False)
    stock_value = serializers.DecimalField(max_digits=16, decimal_places=2, required=False, allow_null=True)
    created_at = serializers.DateTimeField()


class WarehouseWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200)
    address = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    is_default = serializers.BooleanField(required=False)


class WarehousePatchSerializer(WarehouseWriteSerializer):
    name = serializers.CharField(max_length=200, required=False)
    is_active = serializers.BooleanField(required=False)


class CategorySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    parent_id = serializers.IntegerField(allow_null=True)
    position = serializers.IntegerField()
    product_count = serializers.IntegerField(required=False)


class CategoryWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200)
    parent_id = serializers.IntegerField(required=False, allow_null=True)


class CategoryPatchSerializer(CategoryWriteSerializer):
    name = serializers.CharField(max_length=200, required=False)
    position = serializers.IntegerField(required=False)


class SupplierSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    kind = serializers.CharField()
    phone = serializers.CharField(allow_null=True)
    email = serializers.CharField(allow_null=True)
    requisites = serializers.CharField(allow_null=True)
    note = serializers.CharField(allow_null=True)
    is_active = serializers.BooleanField()
    receipt_count = serializers.IntegerField(required=False)
    purchases = serializers.DecimalField(max_digits=16, decimal_places=2, required=False, allow_null=True)
    product_count = serializers.IntegerField(required=False)
    last_receipt_at = serializers.DateTimeField(allow_null=True, required=False)
    created_at = serializers.DateTimeField()


class SupplierWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=300)
    kind = serializers.ChoiceField(choices=["company", "person"], required=False)
    phone = serializers.CharField(max_length=40, required=False, allow_blank=True, allow_null=True)
    email = serializers.CharField(max_length=254, required=False, allow_blank=True, allow_null=True)
    requisites = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    note = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class SupplierPatchSerializer(SupplierWriteSerializer):
    name = serializers.CharField(max_length=300, required=False)
    is_active = serializers.BooleanField(required=False)


class StockLineSerializer(serializers.Serializer):
    warehouse_id = serializers.IntegerField()
    warehouse_name = serializers.CharField()
    quantity = serializers.DecimalField(max_digits=14, decimal_places=3)


class ComponentSerializer(serializers.Serializer):
    component_id = serializers.IntegerField()
    quantity = serializers.DecimalField(max_digits=12, decimal_places=3)
    name = serializers.CharField(required=False)
    sku = serializers.CharField(allow_null=True, required=False)
    unit = serializers.CharField(required=False)


class ComponentWriteSerializer(serializers.Serializer):
    component_id = serializers.IntegerField()
    quantity = serializers.DecimalField(max_digits=12, decimal_places=3, min_value=0)


class ProductSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    kind = serializers.ChoiceField(choices=ProductKind.CHOICES)
    name = serializers.CharField()
    category_id = serializers.IntegerField(allow_null=True)
    category_name = serializers.CharField(allow_null=True, required=False)
    supplier_id = serializers.IntegerField(allow_null=True, required=False)
    supplier_name = serializers.CharField(allow_null=True, required=False)
    brand = serializers.CharField(allow_null=True, required=False)
    sku = serializers.CharField(allow_null=True)
    barcode = serializers.CharField(allow_null=True)
    unit = serializers.CharField()
    purchase_price = serializers.DecimalField(max_digits=14, decimal_places=2, allow_null=True)
    sale_price = serializers.DecimalField(max_digits=14, decimal_places=2)
    wholesale_price = serializers.DecimalField(max_digits=14, decimal_places=2, required=False)
    markup_percent = serializers.DecimalField(max_digits=7, decimal_places=2, allow_null=True, required=False)
    allow_free_price = serializers.BooleanField(required=False)
    min_stock = serializers.DecimalField(max_digits=12, decimal_places=3)
    description = serializers.CharField(allow_null=True)
    attributes = serializers.DictField(required=False)
    parent_id = serializers.IntegerField(allow_null=True, required=False)
    parent_name = serializers.CharField(allow_null=True, required=False)
    variant_label = serializers.CharField(allow_null=True, required=False)
    variant_count = serializers.IntegerField(required=False)
    photo_url = serializers.CharField(allow_null=True, required=False)
    currency = serializers.CharField(required=False)
    is_active = serializers.BooleanField()
    stock_total = serializers.DecimalField(max_digits=14, decimal_places=3)
    reserved = serializers.DecimalField(max_digits=14, decimal_places=3)
    available = serializers.DecimalField(max_digits=14, decimal_places=3)
    stock_value = serializers.DecimalField(max_digits=16, decimal_places=2, allow_null=True)
    is_low = serializers.BooleanField()
    is_out = serializers.BooleanField()
    stock_status = serializers.CharField()
    stocks = StockLineSerializer(many=True)
    components = ComponentSerializer(many=True, required=False)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class ProductListSerializer(serializers.Serializer):
    results = ProductSerializer(many=True)
    brands = serializers.ListField(child=serializers.CharField(), required=False)


class ProductWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=300)
    kind = serializers.ChoiceField(choices=ProductKind.CHOICES, required=False)
    category_id = serializers.IntegerField(required=False, allow_null=True)
    supplier_id = serializers.IntegerField(required=False, allow_null=True)
    brand = serializers.CharField(max_length=200, required=False, allow_blank=True, allow_null=True)
    sku = serializers.CharField(max_length=100, required=False, allow_blank=True, allow_null=True)
    barcode = serializers.CharField(max_length=100, required=False, allow_blank=True, allow_null=True)
    generate_sku = serializers.BooleanField(required=False)
    generate_barcode = serializers.BooleanField(required=False)
    unit = serializers.CharField(max_length=30, required=False, allow_blank=True)
    purchase_price = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0, required=False)
    sale_price = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0, required=False, allow_null=True)
    markup_percent = serializers.DecimalField(max_digits=7, decimal_places=2, required=False, allow_null=True)
    wholesale_price = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0, required=False)
    allow_free_price = serializers.BooleanField(required=False)
    min_stock = serializers.DecimalField(max_digits=12, decimal_places=3, min_value=0, required=False)
    description = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    attributes = serializers.DictField(required=False, allow_null=True)
    parent_id = serializers.IntegerField(required=False, allow_null=True)
    variant_label = serializers.CharField(max_length=200, required=False, allow_blank=True, allow_null=True)
    currency = serializers.CharField(max_length=3, required=False, allow_blank=True)
    components = ComponentWriteSerializer(many=True, required=False)
    #: "Hozir skladda nechta bor" — booked as an opening receipt.
    initial_quantity = serializers.DecimalField(
        max_digits=12, decimal_places=3, min_value=0, required=False, allow_null=True
    )
    initial_warehouse_id = serializers.IntegerField(required=False, allow_null=True)


class ProductPatchSerializer(ProductWriteSerializer):
    name = serializers.CharField(max_length=300, required=False)
    is_active = serializers.BooleanField(required=False)


class PriceHistorySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    field = serializers.CharField()
    old_price = serializers.DecimalField(max_digits=14, decimal_places=2, allow_null=True)
    new_price = serializers.DecimalField(max_digits=14, decimal_places=2, allow_null=True)
    author_id = serializers.IntegerField(allow_null=True)
    author_name = serializers.CharField(allow_null=True, required=False)
    document_id = serializers.IntegerField(allow_null=True)
    document_number = serializers.CharField(allow_null=True, required=False)
    created_at = serializers.DateTimeField()


class MovementSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    kind = serializers.ChoiceField(choices=MovementKind.CHOICES)
    product_id = serializers.IntegerField()
    product_name = serializers.CharField(required=False)
    sku = serializers.CharField(allow_null=True, required=False)
    unit = serializers.CharField(required=False)
    warehouse_id = serializers.IntegerField()
    warehouse_name = serializers.CharField(required=False)
    to_warehouse_id = serializers.IntegerField(allow_null=True)
    to_warehouse_name = serializers.CharField(allow_null=True, required=False)
    quantity = serializers.DecimalField(max_digits=14, decimal_places=3)
    unit_cost = serializers.DecimalField(max_digits=14, decimal_places=2)
    cost_price = serializers.DecimalField(max_digits=14, decimal_places=2, allow_null=True)
    currency = serializers.CharField(required=False)
    note = serializers.CharField(allow_null=True)
    lead_id = serializers.IntegerField(allow_null=True)
    document_id = serializers.IntegerField(allow_null=True, required=False)
    document_number = serializers.CharField(allow_null=True, required=False)
    document_kind = serializers.CharField(allow_null=True, required=False)
    document_reason = serializers.CharField(allow_null=True, required=False)
    reversal_of = serializers.IntegerField(allow_null=True, required=False)
    supplier_name = serializers.CharField(allow_null=True, required=False)
    customer_name = serializers.CharField(allow_null=True, required=False)
    author_id = serializers.IntegerField(allow_null=True)
    author_name = serializers.CharField(allow_null=True, required=False)
    created_at = serializers.DateTimeField()


class MovementListSerializer(serializers.Serializer):
    results = MovementSerializer(many=True)


class MovementWriteSerializer(serializers.Serializer):
    """A one-line operation booked straight from the sheet. Becomes a
    document of the matching kind and is confirmed at once."""

    kind = serializers.ChoiceField(choices=MovementKind.CHOICES)
    product_id = serializers.IntegerField()
    warehouse_id = serializers.IntegerField(required=False, allow_null=True)
    to_warehouse_id = serializers.IntegerField(required=False, allow_null=True)
    #: The amount moved — or, for an adjustment, the amount counted.
    quantity = serializers.DecimalField(max_digits=12, decimal_places=3, min_value=0)
    unit_cost = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=0, required=False, allow_null=True
    )
    reason = serializers.ChoiceField(choices=WriteOffReason.CHOICES, required=False, allow_null=True)
    supplier_id = serializers.IntegerField(required=False, allow_null=True)
    note = serializers.CharField(max_length=1000, required=False, allow_blank=True, allow_null=True)
    idempotency_key = serializers.CharField(max_length=80, required=False, allow_blank=True, allow_null=True)


class DocumentItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    product_id = serializers.IntegerField()
    product_name = serializers.CharField(required=False)
    product_kind = serializers.CharField(required=False)
    sku = serializers.CharField(allow_null=True, required=False)
    unit = serializers.CharField(required=False)
    quantity = serializers.DecimalField(max_digits=14, decimal_places=3)
    system_quantity = serializers.DecimalField(max_digits=14, decimal_places=3, allow_null=True)
    counted_quantity = serializers.DecimalField(max_digits=14, decimal_places=3, allow_null=True)
    difference = serializers.DecimalField(max_digits=14, decimal_places=3, required=False)
    unit_cost = serializers.DecimalField(max_digits=14, decimal_places=2, allow_null=True)
    old_price = serializers.DecimalField(max_digits=14, decimal_places=2, allow_null=True)
    new_price = serializers.DecimalField(max_digits=14, decimal_places=2, allow_null=True)
    old_wholesale = serializers.DecimalField(max_digits=14, decimal_places=2, allow_null=True)
    new_wholesale = serializers.DecimalField(max_digits=14, decimal_places=2, allow_null=True)
    lead_item_id = serializers.IntegerField(allow_null=True)


class DocumentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    kind = serializers.ChoiceField(choices=DocumentKind.CHOICES)
    kind_label = serializers.CharField(required=False)
    number = serializers.CharField(allow_null=True)
    status = serializers.ChoiceField(choices=DocumentStatus.CHOICES)
    warehouse_id = serializers.IntegerField(allow_null=True)
    warehouse_name = serializers.CharField(allow_null=True, required=False)
    to_warehouse_id = serializers.IntegerField(allow_null=True)
    to_warehouse_name = serializers.CharField(allow_null=True, required=False)
    supplier_id = serializers.IntegerField(allow_null=True)
    supplier_name = serializers.CharField(allow_null=True, required=False)
    customer_id = serializers.IntegerField(allow_null=True)
    customer_name = serializers.CharField(allow_null=True, required=False)
    lead_id = serializers.IntegerField(allow_null=True)
    currency = serializers.CharField()
    extra_costs = serializers.DecimalField(max_digits=14, decimal_places=2)
    reason = serializers.CharField(allow_null=True)
    note = serializers.CharField(allow_null=True)
    doc_date = serializers.DateField(allow_null=True)
    external_number = serializers.CharField(allow_null=True)
    file_id = serializers.IntegerField(allow_null=True)
    reversal_of = serializers.IntegerField(allow_null=True)
    reversal_of_number = serializers.CharField(allow_null=True, required=False)
    cancel_reason = serializers.CharField(allow_null=True)
    author_id = serializers.IntegerField(allow_null=True)
    author_name = serializers.CharField(allow_null=True, required=False)
    confirmed_by_name = serializers.CharField(allow_null=True, required=False)
    confirmed_at = serializers.DateTimeField(allow_null=True)
    sent_at = serializers.DateTimeField(allow_null=True)
    received_at = serializers.DateTimeField(allow_null=True)
    cancelled_at = serializers.DateTimeField(allow_null=True)
    cancelled_by_name = serializers.CharField(allow_null=True, required=False)
    line_count = serializers.IntegerField()
    total = serializers.DecimalField(max_digits=16, decimal_places=2, allow_null=True)
    quantity_total = serializers.DecimalField(max_digits=16, decimal_places=3)
    items = DocumentItemSerializer(many=True, required=False)
    created_at = serializers.DateTimeField()


class DocumentListSerializer(serializers.Serializer):
    results = DocumentSerializer(many=True)


class DocumentItemWriteSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    quantity = serializers.DecimalField(max_digits=12, decimal_places=3, min_value=0, required=False, allow_null=True)
    counted_quantity = serializers.DecimalField(max_digits=12, decimal_places=3, min_value=0, required=False, allow_null=True)
    unit_cost = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0, required=False, allow_null=True)
    new_price = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0, required=False, allow_null=True)
    new_wholesale = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0, required=False, allow_null=True)


class DocumentWriteSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=DocumentKind.CHOICES)
    warehouse_id = serializers.IntegerField(required=False, allow_null=True)
    to_warehouse_id = serializers.IntegerField(required=False, allow_null=True)
    supplier_id = serializers.IntegerField(required=False, allow_null=True)
    customer_id = serializers.IntegerField(required=False, allow_null=True)
    lead_id = serializers.IntegerField(required=False, allow_null=True)
    currency = serializers.CharField(max_length=3, required=False, allow_blank=True)
    extra_costs = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0, required=False)
    reason = serializers.CharField(max_length=30, required=False, allow_blank=True, allow_null=True)
    note = serializers.CharField(max_length=2000, required=False, allow_blank=True, allow_null=True)
    doc_date = serializers.DateField(required=False, allow_null=True)
    external_number = serializers.CharField(max_length=100, required=False, allow_blank=True, allow_null=True)
    file_id = serializers.IntegerField(required=False, allow_null=True)
    idempotency_key = serializers.CharField(max_length=80, required=False, allow_blank=True, allow_null=True)
    items = DocumentItemWriteSerializer(many=True)
    #: Confirm in the same request — the common case for a receipt typed
    #: and posted in one go.
    confirm = serializers.BooleanField(required=False, default=False)


class DocumentPatchSerializer(DocumentWriteSerializer):
    kind = serializers.ChoiceField(choices=DocumentKind.CHOICES, required=False)
    items = DocumentItemWriteSerializer(many=True, required=False)


class CancelSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=1000)


class StockChangeSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    name = serializers.CharField()
    unit = serializers.CharField(allow_null=True)
    warehouse_id = serializers.IntegerField()
    warehouse_name = serializers.CharField(allow_null=True)
    before = serializers.DecimalField(max_digits=14, decimal_places=3)
    after = serializers.DecimalField(max_digits=14, decimal_places=3)
    change = serializers.DecimalField(max_digits=14, decimal_places=3)


class DailyPointSerializer(serializers.Serializer):
    day = serializers.DateField()
    revenue = serializers.DecimalField(max_digits=16, decimal_places=2)
    purchases = serializers.DecimalField(max_digits=16, decimal_places=2)


class TopProductSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    name = serializers.CharField()
    unit = serializers.CharField()
    sold_qty = serializers.DecimalField(max_digits=14, decimal_places=3)
    revenue = serializers.DecimalField(max_digits=16, decimal_places=2)
    profit = serializers.DecimalField(max_digits=16, decimal_places=2, allow_null=True)


class InventorySummarySerializer(serializers.Serializer):
    date_from = serializers.DateTimeField()
    date_to = serializers.DateTimeField()
    product_count = serializers.IntegerField()
    quantity_total = serializers.DecimalField(max_digits=16, decimal_places=3)
    stock_value = serializers.DecimalField(max_digits=16, decimal_places=2, allow_null=True)
    retail_value = serializers.DecimalField(max_digits=16, decimal_places=2)
    low_stock_count = serializers.IntegerField()
    out_of_stock_count = serializers.IntegerField()
    sale_count = serializers.IntegerField()
    sold_qty = serializers.DecimalField(max_digits=16, decimal_places=3)
    revenue = serializers.DecimalField(max_digits=16, decimal_places=2)
    cogs = serializers.DecimalField(max_digits=16, decimal_places=2, allow_null=True)
    profit = serializers.DecimalField(max_digits=16, decimal_places=2, allow_null=True)
    received_qty = serializers.DecimalField(max_digits=16, decimal_places=3)
    purchases = serializers.DecimalField(max_digits=16, decimal_places=2, allow_null=True)
    written_off_qty = serializers.DecimalField(max_digits=16, decimal_places=3)
    write_offs = serializers.DecimalField(max_digits=16, decimal_places=2, allow_null=True)
    returned_qty = serializers.DecimalField(max_digits=16, decimal_places=3)
    adjusted_qty = serializers.DecimalField(max_digits=16, decimal_places=3)
    adjustment_count = serializers.IntegerField()
    turnover_ratio = serializers.DecimalField(max_digits=10, decimal_places=2, allow_null=True)
    daily = DailyPointSerializer(many=True)
    top_products = TopProductSerializer(many=True)


class ImportRowSerializer(serializers.Serializer):
    line = serializers.IntegerField(required=False, allow_null=True)
    action = serializers.CharField()
    name = serializers.CharField(allow_blank=True)
    kind = serializers.CharField(required=False, allow_null=True)
    sku = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    barcode = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    category = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    brand = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    supplier = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    unit = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    description = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    warehouse = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    purchase_price = serializers.DecimalField(max_digits=14, decimal_places=2, required=False, allow_null=True)
    sale_price = serializers.DecimalField(max_digits=14, decimal_places=2, required=False, allow_null=True)
    wholesale_price = serializers.DecimalField(max_digits=14, decimal_places=2, required=False, allow_null=True)
    min_stock = serializers.DecimalField(max_digits=12, decimal_places=3, required=False, allow_null=True)
    initial_quantity = serializers.DecimalField(max_digits=12, decimal_places=3, required=False, allow_null=True)


class ImportCommitSerializer(serializers.Serializer):
    rows = ImportRowSerializer(many=True)
    update_existing = serializers.BooleanField(required=False, default=True)
    update_fields = serializers.ListField(child=serializers.CharField(), required=False)


# ─── Settings ─────────────────────────────────────────────────────────────────

class WorkspaceInventorySettingsView(_InventoryView):
    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Stock-room settings", responses={200: SettingsSerializer()})
    def get(self, request):
        return Response(inventory.get_settings(request.user.company_id))

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Change stock-room settings (manage)",
                         request_body=SettingsWriteSerializer, responses={200: SettingsSerializer()})
    def patch(self, request):
        if refusal := _require(request, Permission.STOCK_MANAGE):
            return refusal
        serializer = SettingsWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        before = inventory.get_settings(request.user.company_id)
        row = inventory.update_settings(request.user.company_id, **serializer.validated_data)
        record_audit(
            request.user.company_id, actor_employee_id=request.user.id, action="inventory.settings",
            target_type="inventory_settings", target_id=request.user.company_id,
            payload={"before": {k: str(v) for k, v in before.items() if k != "company_id"},
                     "after": {k: str(v) for k, v in row.items() if k != "company_id"}},
        )
        return Response(row)


# ─── Warehouses ───────────────────────────────────────────────────────────────

class WorkspaceWarehouseListCreateView(_InventoryView):
    @swagger_auto_schema(
        tags=WORKSPACE_TAG, operation_summary="List warehouses",
        manual_parameters=[openapi.Parameter("all", openapi.IN_QUERY, type=openapi.TYPE_BOOLEAN)],
        responses={200: WarehouseSerializer(many=True)},
    )
    def get(self, request):
        rows = inventory.list_warehouses(request.user.company_id, include_inactive=_flag(request, "all"))
        return Response(_hide_costs(rows, request))

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Create a warehouse (manage)",
                         request_body=WarehouseWriteSerializer, responses={201: WarehouseSerializer()})
    def post(self, request):
        if refusal := _require(request, Permission.STOCK_MANAGE):
            return refusal
        serializer = WarehouseWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        row = inventory.create_warehouse(
            request.user.company_id, name=data["name"], address=data.get("address"),
            is_default=bool(data.get("is_default")),
        )
        return Response(row, status=status.HTTP_201_CREATED)


class WorkspaceWarehouseDetailView(_InventoryView):
    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Edit a warehouse",
                         request_body=WarehousePatchSerializer, responses={200: WarehouseSerializer()})
    def patch(self, request, warehouse_id: int):
        if refusal := _require(request, Permission.STOCK_MANAGE):
            return refusal
        if not inventory.get_warehouse(warehouse_id, request.user.company_id):
            return Response({"detail": _("Warehouse not found.")}, status=status.HTTP_404_NOT_FOUND)
        serializer = WarehousePatchSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        return Response(inventory.update_warehouse(
            warehouse_id, request.user.company_id, **serializer.validated_data
        ))

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Close a warehouse")
    def delete(self, request, warehouse_id: int):
        if refusal := _require(request, Permission.STOCK_MANAGE):
            return refusal
        try:
            deleted = inventory.delete_warehouse(warehouse_id, request.user.company_id)
        except InventoryError as exc:
            return _refusal(exc)
        if not deleted:
            return Response({"detail": _("Warehouse not found.")}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─── Categories ───────────────────────────────────────────────────────────────

class WorkspaceCategoryListCreateView(_InventoryView):
    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="List product categories",
                         responses={200: CategorySerializer(many=True)})
    def get(self, request):
        return Response(inventory.list_categories(request.user.company_id))

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Create a product category (manage)",
                         request_body=CategoryWriteSerializer, responses={201: CategorySerializer()})
    def post(self, request):
        if refusal := _require(request, Permission.STOCK_MANAGE):
            return refusal
        serializer = CategoryWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        parent_id = data.get("parent_id")
        if parent_id and not inventory.get_category(parent_id, request.user.company_id):
            return Response({"detail": _("Category not found.")}, status=status.HTTP_404_NOT_FOUND)
        row = inventory.create_category(request.user.company_id, name=data["name"], parent_id=parent_id)
        return Response(row, status=status.HTTP_201_CREATED)


class WorkspaceCategoryDetailView(_InventoryView):
    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Edit a product category",
                         request_body=CategoryPatchSerializer, responses={200: CategorySerializer()})
    def patch(self, request, category_id: int):
        if refusal := _require(request, Permission.STOCK_MANAGE):
            return refusal
        if not inventory.get_category(category_id, request.user.company_id):
            return Response({"detail": _("Category not found.")}, status=status.HTTP_404_NOT_FOUND)
        serializer = CategoryPatchSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            row = inventory.update_category(category_id, request.user.company_id, **serializer.validated_data)
        except InventoryError as exc:
            return _refusal(exc)
        return Response(row)

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Delete a product category")
    def delete(self, request, category_id: int):
        if refusal := _require(request, Permission.STOCK_MANAGE):
            return refusal
        if not inventory.delete_category(category_id, request.user.company_id):
            return Response({"detail": _("Category not found.")}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─── Suppliers ────────────────────────────────────────────────────────────────

class WorkspaceSupplierListCreateView(_InventoryView):
    @swagger_auto_schema(
        tags=WORKSPACE_TAG, operation_summary="List suppliers",
        manual_parameters=[
            openapi.Parameter("q", openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter("all", openapi.IN_QUERY, type=openapi.TYPE_BOOLEAN),
        ],
        responses={200: SupplierSerializer(many=True)},
    )
    def get(self, request):
        rows = inventory.list_suppliers(
            request.user.company_id, q=request.query_params.get("q") or None,
            include_inactive=_flag(request, "all"),
        )
        if not may(request.user, Permission.STOCK_VIEW_COST):
            rows = [{**r, "purchases": None} for r in rows]
        return Response(rows)

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Create a supplier (manage)",
                         request_body=SupplierWriteSerializer, responses={201: SupplierSerializer()})
    def post(self, request):
        if refusal := _require(request, Permission.STOCK_MANAGE):
            return refusal
        serializer = SupplierWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        row = inventory.create_supplier(request.user.company_id, **serializer.validated_data)
        return Response(row, status=status.HTTP_201_CREATED)


class WorkspaceSupplierDetailView(_InventoryView):
    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Supplier with purchase history",
                         responses={200: SupplierSerializer()})
    def get(self, request, supplier_id: int):
        row = next(
            (s for s in inventory.list_suppliers(request.user.company_id, include_inactive=True)
             if s["id"] == supplier_id), None,
        )
        if not row:
            return Response({"detail": _("Supplier not found.")}, status=status.HTTP_404_NOT_FOUND)
        purchases = inventory.supplier_purchases(supplier_id, request.user.company_id)
        if not may(request.user, Permission.STOCK_VIEW_COST):
            row["purchases"] = None
            purchases = [{**p, "total": None} for p in purchases]
        return Response({**row, "history": purchases})

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Edit a supplier (manage)",
                         request_body=SupplierPatchSerializer, responses={200: SupplierSerializer()})
    def patch(self, request, supplier_id: int):
        if refusal := _require(request, Permission.STOCK_MANAGE):
            return refusal
        if not inventory.get_supplier(supplier_id, request.user.company_id):
            return Response({"detail": _("Supplier not found.")}, status=status.HTTP_404_NOT_FOUND)
        serializer = SupplierPatchSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        return Response(inventory.update_supplier(supplier_id, request.user.company_id, **serializer.validated_data))

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Archive a supplier (manage)")
    def delete(self, request, supplier_id: int):
        if refusal := _require(request, Permission.STOCK_MANAGE):
            return refusal
        if not inventory.update_supplier(supplier_id, request.user.company_id, is_active=False):
            return Response({"detail": _("Supplier not found.")}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─── Products ─────────────────────────────────────────────────────────────────

class WorkspaceProductListCreateView(_InventoryView):
    """GET  /inventory/products/ — the catalogue with stock per warehouse.
    POST /inventory/products/ — add to it (manage)."""

    @swagger_auto_schema(
        tags=WORKSPACE_TAG, operation_summary="List products with stock",
        manual_parameters=[
            openapi.Parameter("q", openapi.IN_QUERY, type=openapi.TYPE_STRING, description="Name, article, barcode or brand"),
            openapi.Parameter("category_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter("supplier_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter("warehouse_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER, description="Only products with stock in this warehouse"),
            openapi.Parameter("brand", openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter("kind", openapi.IN_QUERY, type=openapi.TYPE_STRING, enum=ProductKind.CHOICES),
            openapi.Parameter("status", openapi.IN_QUERY, type=openapi.TYPE_STRING, enum=["active", "inactive", "low", "zero", "archived"]),
            openapi.Parameter("price_min", openapi.IN_QUERY, type=openapi.TYPE_NUMBER),
            openapi.Parameter("price_max", openapi.IN_QUERY, type=openapi.TYPE_NUMBER),
            openapi.Parameter("low_stock", openapi.IN_QUERY, type=openapi.TYPE_BOOLEAN),
            openapi.Parameter("all", openapi.IN_QUERY, type=openapi.TYPE_BOOLEAN, description="Archived products only"),
        ],
        responses={200: ProductListSerializer()},
    )
    def get(self, request):
        params = request.query_params

        def money(name):
            raw = params.get(name)
            try:
                return Decimal(raw) if raw not in (None, "") else None
            except Exception:  # noqa: BLE001
                return None

        rows = inventory.list_products(
            request.user.company_id,
            q=params.get("q") or None,
            category_id=_int_param(request, "category_id"),
            supplier_id=_int_param(request, "supplier_id"),
            warehouse_id=_int_param(request, "warehouse_id"),
            brand=params.get("brand") or None,
            kind=params.get("kind") or None,
            status=params.get("status") or None,
            price_min=money("price_min"),
            price_max=money("price_max"),
            low_stock=_flag(request, "low_stock"),
            include_inactive=_flag(request, "all"),
        )
        return Response({"results": _hide_costs(rows, request), "brands": inventory.list_brands(request.user.company_id)})

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Create a product (manage)",
                         request_body=ProductWriteSerializer, responses={201: ProductSerializer()})
    def post(self, request):
        if refusal := _require(request, Permission.STOCK_MANAGE):
            return refusal
        serializer = ProductWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        category_id = data.get("category_id")
        if category_id and not inventory.get_category(category_id, request.user.company_id):
            return Response({"detail": _("Category not found.")}, status=status.HTTP_404_NOT_FOUND)
        supplier_id = data.get("supplier_id")
        if supplier_id and not inventory.get_supplier(supplier_id, request.user.company_id):
            return Response({"detail": _("Supplier not found.")}, status=status.HTTP_404_NOT_FOUND)
        try:
            row = inventory.create_product(
                request.user.company_id,
                author_id=request.user.id,
                name=data["name"],
                kind=data.get("kind") or ProductKind.PRODUCT,
                category_id=category_id,
                supplier_id=supplier_id,
                brand=data.get("brand"),
                sku=data.get("sku"),
                barcode=data.get("barcode"),
                generate_sku=bool(data.get("generate_sku")),
                generate_barcode=bool(data.get("generate_barcode")),
                unit=data.get("unit") or "dona",
                purchase_price=data.get("purchase_price") or 0,
                sale_price=data.get("sale_price"),
                markup_percent=data.get("markup_percent"),
                wholesale_price=data.get("wholesale_price") or 0,
                allow_free_price=bool(data.get("allow_free_price")),
                min_stock=data.get("min_stock") or 0,
                description=data.get("description"),
                attributes=data.get("attributes"),
                parent_id=data.get("parent_id"),
                variant_label=data.get("variant_label"),
                currency=data.get("currency") or None,
                components=data.get("components") or (),
                initial_quantity=data.get("initial_quantity"),
                initial_warehouse_id=data.get("initial_warehouse_id"),
            )
        except InventoryError as exc:
            return _refusal(exc)
        return Response(row, status=status.HTTP_201_CREATED)


class WorkspaceProductDetailView(_InventoryView):
    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Product with stock, components and variants",
                         responses={200: ProductSerializer()})
    def get(self, request, product_id: int):
        row = inventory.get_product(product_id, request.user.company_id)
        if not row:
            return Response({"detail": _("Product not found.")}, status=status.HTTP_404_NOT_FOUND)
        row["variants"] = inventory.list_variants(product_id, request.user.company_id)
        return Response(_hide_costs({**row, "variants": _hide_costs(row["variants"], request)}, request))

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Edit a product (manage; prices need reprice)",
                         request_body=ProductPatchSerializer, responses={200: ProductSerializer()})
    def patch(self, request, product_id: int):
        if refusal := _require(request, Permission.STOCK_MANAGE):
            return refusal
        current = inventory.get_product_raw(product_id, request.user.company_id)
        if not current:
            return Response({"detail": _("Product not found.")}, status=status.HTTP_404_NOT_FOUND)
        serializer = ProductPatchSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        for key in ("initial_quantity", "initial_warehouse_id", "generate_sku", "generate_barcode"):
            data.pop(key, None)
        # Changing a sale or wholesale price on the card is a repricing in
        # all but name, and the TZ hands that to a narrower right.
        price_changed = any(
            key in data and data[key] is not None
            and Decimal(str(data[key])) != Decimal(str(current.get(key) or 0))
            for key in ("sale_price", "wholesale_price", "markup_percent")
        )
        if price_changed and (refusal := _require(request, Permission.STOCK_REPRICE)):
            return refusal
        category_id = data.get("category_id")
        if category_id and not inventory.get_category(category_id, request.user.company_id):
            return Response({"detail": _("Category not found.")}, status=status.HTTP_404_NOT_FOUND)
        try:
            row = inventory.update_product(product_id, request.user.company_id, author_id=request.user.id, **data)
        except InventoryError as exc:
            return _refusal(exc)
        if price_changed:
            record_audit(
                request.user.company_id, actor_employee_id=request.user.id, action="inventory.reprice",
                target_type="product", target_id=product_id,
                payload={"before": {k: str(current.get(k)) for k in ("sale_price", "wholesale_price")},
                         "after": {k: str((row or {}).get(k)) for k in ("sale_price", "wholesale_price")}},
            )
        return Response(_hide_costs(row, request))

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Archive a product (manage)")
    def delete(self, request, product_id: int):
        if refusal := _require(request, Permission.STOCK_MANAGE):
            return refusal
        if not inventory.delete_product(product_id, request.user.company_id):
            return Response({"detail": _("Product not found.")}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkspaceProductPhotoView(_InventoryView):
    parser_classes = [MultiPartParser, FormParser]

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Set a product's photo (manage)",
                         responses={200: ProductSerializer()})
    def post(self, request, product_id: int):
        if refusal := _require(request, Permission.STOCK_MANAGE):
            return refusal
        if not inventory.get_product_raw(product_id, request.user.company_id):
            return Response({"detail": _("Product not found.")}, status=status.HTTP_404_NOT_FOUND)
        upload = request.FILES.get("photo")
        if upload is None:
            return Response({"photo": [_("Attach a picture.")]}, status=status.HTTP_400_BAD_REQUEST)
        if not (getattr(upload, "content_type", "") or "").lower().startswith("image/"):
            return Response({"photo": [_("That is not a picture.")]}, status=status.HTTP_400_BAD_REQUEST)
        file, refusal = store_upload(request=request, upload=upload, kind="product")
        if refusal:
            return refusal
        row = inventory.update_product(product_id, request.user.company_id, author_id=request.user.id, photo=file["path"])
        return Response(_hide_costs(row, request))

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Remove a product's photo (manage)")
    def delete(self, request, product_id: int):
        if refusal := _require(request, Permission.STOCK_MANAGE):
            return refusal
        row = inventory.update_product(product_id, request.user.company_id, author_id=request.user.id, photo=None)
        if not row:
            return Response({"detail": _("Product not found.")}, status=status.HTTP_404_NOT_FOUND)
        return Response(_hide_costs(row, request))


class WorkspaceProductMovementsView(_InventoryView):
    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="One product's stock history",
                         responses={200: MovementListSerializer()})
    def get(self, request, product_id: int):
        if not inventory.get_product_raw(product_id, request.user.company_id):
            return Response({"detail": _("Product not found.")}, status=status.HTTP_404_NOT_FOUND)
        rows = inventory.list_movements(request.user.company_id, product_id=product_id, limit=300)
        return Response({"results": _hide_costs(rows, request)})


class WorkspaceProductPriceHistoryView(_InventoryView):
    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="One product's price history",
                         responses={200: PriceHistorySerializer(many=True)})
    def get(self, request, product_id: int):
        if not inventory.get_product_raw(product_id, request.user.company_id):
            return Response({"detail": _("Product not found.")}, status=status.HTTP_404_NOT_FOUND)
        rows = inventory.list_price_history(product_id, request.user.company_id)
        if not may(request.user, Permission.STOCK_VIEW_COST):
            rows = [r for r in rows if r.get("field") != "purchase_price"]
        return Response(rows)


class WorkspaceGenerateCodeView(_InventoryView):
    """GET /inventory/generate/?what=sku|barcode — a fresh article or barcode."""

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Generate an article or barcode",
                         manual_parameters=[openapi.Parameter("what", openapi.IN_QUERY, type=openapi.TYPE_STRING, enum=["sku", "barcode"])])
    def get(self, request):
        if refusal := _require(request, Permission.STOCK_MANAGE):
            return refusal
        what = request.query_params.get("what") or "sku"
        try:
            value = (
                inventory.next_barcode(request.user.company_id) if what == "barcode"
                else inventory.next_sku(request.user.company_id)
            )
        except InventoryError as exc:
            return _refusal(exc)
        return Response({"what": what, "value": value})


# ─── Documents ────────────────────────────────────────────────────────────────

_KIND_PERMISSION = {
    DocumentKind.RECEIPT: Permission.STOCK_MANAGE,
    DocumentKind.TRANSFER: Permission.STOCK_MANAGE,
    DocumentKind.INVENTORY: Permission.STOCK_MANAGE,
    DocumentKind.WRITE_OFF: Permission.STOCK_WRITE_OFF,
    DocumentKind.REVALUATION: Permission.STOCK_REPRICE,
    DocumentKind.SALE: Permission.DEAL_CREATE,
    DocumentKind.RETURN: Permission.STOCK_MANAGE,
}


def _document_refusal(request, kind: str) -> Response | None:
    return _require(request, _KIND_PERMISSION.get(kind, Permission.STOCK_MANAGE))


class WorkspaceDocumentListCreateView(_InventoryView):
    @swagger_auto_schema(
        tags=WORKSPACE_TAG, operation_summary="List stock documents",
        manual_parameters=[
            openapi.Parameter("kind", openapi.IN_QUERY, type=openapi.TYPE_STRING, enum=DocumentKind.CHOICES),
            openapi.Parameter("status", openapi.IN_QUERY, type=openapi.TYPE_STRING, enum=DocumentStatus.CHOICES),
            openapi.Parameter("warehouse_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter("supplier_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter("customer_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter("lead_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter("q", openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter("from", openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter("to", openapi.IN_QUERY, type=openapi.TYPE_STRING),
        ],
        responses={200: DocumentListSerializer()},
    )
    def get(self, request):
        kind = request.query_params.get("kind") or None
        doc_status = request.query_params.get("status") or None
        rows = documents.list_documents(
            request.user.company_id,
            kind=kind if kind in DocumentKind.CHOICES else None,
            status=doc_status if doc_status in DocumentStatus.CHOICES else None,
            warehouse_id=_int_param(request, "warehouse_id"),
            supplier_id=_int_param(request, "supplier_id"),
            customer_id=_int_param(request, "customer_id"),
            lead_id=_int_param(request, "lead_id"),
            q=request.query_params.get("q") or None,
            date_from=_datetime_param(request, "from"),
            date_to=_datetime_param(request, "to", end=True),
            limit=min(max(_int_param(request, "limit") or 200, 1), 1000),
        )
        if not may(request.user, Permission.STOCK_VIEW_COST):
            rows = [
                {**r, "total": None} if r["kind"] in (DocumentKind.RECEIPT, DocumentKind.WRITE_OFF) else r
                for r in rows
            ]
        return Response({"results": rows})

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="File a stock document (and confirm it)",
                         request_body=DocumentWriteSerializer, responses={201: DocumentSerializer()})
    def post(self, request):
        serializer = DocumentWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        kind = data["kind"]
        if refusal := _document_refusal(request, kind):
            return refusal
        confirm = bool(data.pop("confirm", False))
        try:
            doc = documents.create_document(request.user.company_id, author_id=request.user.id, **data)
            if confirm and doc and doc.get("status") == DocumentStatus.DRAFT:
                doc = documents.confirm_document(doc["id"], request.user.company_id, actor_id=request.user.id)
        except InventoryError as exc:
            return _refusal(exc)
        record_audit(
            request.user.company_id, actor_employee_id=request.user.id, action=f"inventory.{kind}",
            target_type="stock_document", target_id=doc.get("id"),
            payload={"number": doc.get("number"), "status": doc.get("status"), "lines": doc.get("line_count")},
        )
        return Response(doc, status=status.HTTP_201_CREATED)


class WorkspaceDocumentDetailView(_InventoryView):
    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="A stock document with its lines",
                         responses={200: DocumentSerializer()})
    def get(self, request, document_id: int):
        doc = documents.get_document(document_id, request.user.company_id)
        if not doc:
            return Response({"detail": _("Document not found.")}, status=status.HTTP_404_NOT_FOUND)
        if not may(request.user, Permission.STOCK_VIEW_COST) and doc["kind"] in (DocumentKind.RECEIPT, DocumentKind.WRITE_OFF):
            doc["total"] = None
            doc["items"] = [{**i, "unit_cost": None, "current_purchase_price": None} for i in doc["items"]]
        return Response(doc)

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Edit a draft document",
                         request_body=DocumentPatchSerializer, responses={200: DocumentSerializer()})
    def patch(self, request, document_id: int):
        current = documents.get_document_raw(document_id, request.user.company_id)
        if not current:
            return Response({"detail": _("Document not found.")}, status=status.HTTP_404_NOT_FOUND)
        if refusal := _document_refusal(request, current["kind"]):
            return refusal
        serializer = DocumentPatchSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        for key in ("kind", "confirm", "idempotency_key"):
            data.pop(key, None)
        try:
            doc = documents.update_document(document_id, request.user.company_id, **data)
        except InventoryError as exc:
            return _refusal(exc)
        return Response(doc)

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Delete a draft document")
    def delete(self, request, document_id: int):
        current = documents.get_document_raw(document_id, request.user.company_id)
        if not current:
            return Response({"detail": _("Document not found.")}, status=status.HTTP_404_NOT_FOUND)
        if refusal := _document_refusal(request, current["kind"]):
            return refusal
        if not documents.delete_draft(document_id, request.user.company_id):
            return Response(
                {"detail": _("Only a draft can be deleted; cancel a confirmed document instead.")},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkspaceDocumentPreviewView(_InventoryView):
    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="What confirming would do to each balance",
                         responses={200: StockChangeSerializer(many=True)})
    def get(self, request, document_id: int):
        if not documents.get_document_raw(document_id, request.user.company_id):
            return Response({"detail": _("Document not found.")}, status=status.HTTP_404_NOT_FOUND)
        try:
            return Response(documents.preview_confirm(document_id, request.user.company_id))
        except InventoryError as exc:
            return _refusal(exc)


class _DocumentActionView(_InventoryView):
    action = ""

    def post(self, request, document_id: int):
        current = documents.get_document_raw(document_id, request.user.company_id)
        if not current:
            return Response({"detail": _("Document not found.")}, status=status.HTTP_404_NOT_FOUND)
        if refusal := _document_refusal(request, current["kind"]):
            return refusal
        try:
            if self.action == "confirm":
                doc = documents.confirm_document(document_id, request.user.company_id, actor_id=request.user.id)
            elif self.action == "send":
                doc = documents.send_transfer(document_id, request.user.company_id, actor_id=request.user.id)
            elif self.action == "receive":
                doc = documents.receive_transfer(document_id, request.user.company_id, actor_id=request.user.id)
            else:
                serializer = CancelSerializer(data=request.data)
                serializer.is_valid(raise_exception=True)
                doc = documents.cancel_document(
                    document_id, request.user.company_id, actor_id=request.user.id,
                    reason=serializer.validated_data["reason"],
                )
        except InventoryError as exc:
            return _refusal(exc)
        payload = {"number": doc.get("number"), "status": doc.get("status")}
        if self.action == "cancel":
            payload["reason"] = request.data.get("reason")
        record_audit(
            request.user.company_id, actor_employee_id=request.user.id,
            action=f"inventory.{current['kind']}.{self.action}", target_type="stock_document",
            target_id=document_id, payload=payload,
        )
        return Response(doc)


class WorkspaceDocumentConfirmView(_DocumentActionView):
    action = "confirm"

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Confirm a document — apply it to the ledger",
                         responses={200: DocumentSerializer(), 409: openapi.Response(description="Insufficient stock, with `shortages`")})
    def post(self, request, document_id: int):
        return super().post(request, document_id)


class WorkspaceDocumentSendView(_DocumentActionView):
    action = "send"

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Send a transfer (stock leaves the source)",
                         responses={200: DocumentSerializer()})
    def post(self, request, document_id: int):
        return super().post(request, document_id)


class WorkspaceDocumentReceiveView(_DocumentActionView):
    action = "receive"

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Receive a sent transfer",
                         responses={200: DocumentSerializer()})
    def post(self, request, document_id: int):
        return super().post(request, document_id)


class WorkspaceDocumentCancelView(_DocumentActionView):
    action = "cancel"

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Cancel (storno) a document, with a reason",
                         request_body=CancelSerializer, responses={200: DocumentSerializer()})
    def post(self, request, document_id: int):
        return super().post(request, document_id)


class WorkspacePendingSalesView(_InventoryView):
    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Sales waiting for stock (backorders)",
                         responses={200: DocumentListSerializer()})
    def get(self, request):
        return Response({"results": documents.list_pending_sales(request.user.company_id)})


# ─── Movements ────────────────────────────────────────────────────────────────

class WorkspaceMovementListCreateView(_InventoryView):
    """GET  /inventory/movements/ — the ledger, newest first.
    POST /inventory/movements/ — one line booked straight away, as a
    document of the matching kind that is confirmed in the same breath."""

    @swagger_auto_schema(
        tags=WORKSPACE_TAG, operation_summary="List stock movements",
        manual_parameters=[
            openapi.Parameter("product_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter("warehouse_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter("kind", openapi.IN_QUERY, type=openapi.TYPE_STRING, enum=MovementKind.CHOICES),
            openapi.Parameter("category_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter("supplier_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter("customer_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter("author_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter("document_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter("q", openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter("from", openapi.IN_QUERY, type=openapi.TYPE_STRING, description="ISO date or datetime, inclusive"),
            openapi.Parameter("to", openapi.IN_QUERY, type=openapi.TYPE_STRING, description="ISO date (whole day) or datetime, exclusive"),
            openapi.Parameter("limit", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
        ],
        responses={200: MovementListSerializer()},
    )
    def get(self, request):
        kind = request.query_params.get("kind") or None
        if kind and kind not in MovementKind.CHOICES:
            kind = None
        rows = inventory.list_movements(
            request.user.company_id,
            product_id=_int_param(request, "product_id"),
            warehouse_id=_int_param(request, "warehouse_id"),
            kind=kind,
            category_id=_int_param(request, "category_id"),
            supplier_id=_int_param(request, "supplier_id"),
            customer_id=_int_param(request, "customer_id"),
            author_id=_int_param(request, "author_id"),
            document_id=_int_param(request, "document_id"),
            q=request.query_params.get("q") or None,
            date_from=_datetime_param(request, "from"),
            date_to=_datetime_param(request, "to", end=True),
            limit=min(max(_int_param(request, "limit") or 200, 1), 1000),
        )
        return Response({"results": _hide_costs(rows, request)})

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Book a one-line stock operation",
                         request_body=MovementWriteSerializer, responses={201: DocumentSerializer()})
    def post(self, request):
        serializer = MovementWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        kind = {
            MovementKind.RECEIPT: DocumentKind.RECEIPT,
            MovementKind.SALE: DocumentKind.SALE,
            MovementKind.WRITE_OFF: DocumentKind.WRITE_OFF,
            MovementKind.TRANSFER: DocumentKind.TRANSFER,
            MovementKind.ADJUSTMENT: DocumentKind.INVENTORY,
            MovementKind.RETURN: DocumentKind.RETURN,
        }[data["kind"]]
        if refusal := _document_refusal(request, kind):
            return refusal
        item: dict[str, Any] = {"product_id": data["product_id"], "unit_cost": data.get("unit_cost")}
        if kind == DocumentKind.INVENTORY:
            item["counted_quantity"] = data["quantity"]
        else:
            item["quantity"] = data["quantity"]
        try:
            doc = documents.create_document(
                request.user.company_id, kind=kind, author_id=request.user.id,
                warehouse_id=data.get("warehouse_id"), to_warehouse_id=data.get("to_warehouse_id"),
                supplier_id=data.get("supplier_id"), reason=data.get("reason"), note=data.get("note"),
                idempotency_key=data.get("idempotency_key") or None, items=[item],
            )
            if doc.get("status") == DocumentStatus.DRAFT:
                doc = documents.confirm_document(doc["id"], request.user.company_id, actor_id=request.user.id)
        except InventoryError as exc:
            return _refusal(exc)
        return Response(doc, status=status.HTTP_201_CREATED)


# ─── Turnover ─────────────────────────────────────────────────────────────────

class WorkspaceInventorySummaryView(_InventoryView):
    @swagger_auto_schema(
        tags=WORKSPACE_TAG, operation_summary="Stock value and turnover for a period",
        manual_parameters=[
            openapi.Parameter("from", openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter("to", openapi.IN_QUERY, type=openapi.TYPE_STRING),
        ],
        responses={200: InventorySummarySerializer()},
    )
    def get(self, request):
        data = inventory.summary(
            request.user.company_id,
            date_from=_datetime_param(request, "from"),
            date_to=_datetime_param(request, "to", end=True),
        )
        if not may(request.user, Permission.STOCK_VIEW_COST):
            for key in ("stock_value", "cogs", "profit", "purchases", "write_offs", "adjustments", "turnover_ratio"):
                data[key] = None
            data["top_products"] = [{**p, "profit": None} for p in data["top_products"]]
            data["by_supplier"] = [{**s, "purchases": None} for s in data["by_supplier"]]
            data["write_off_reasons"] = [{**r, "value": None} for r in data["write_off_reasons"]]
        return Response(data)


# ─── XLSX ─────────────────────────────────────────────────────────────────────

class WorkspaceInventoryExportView(_InventoryView):
    @swagger_auto_schema(
        tags=WORKSPACE_TAG, operation_summary="Export the catalogue, balances or ledger as XLSX",
        manual_parameters=[
            openapi.Parameter("what", openapi.IN_QUERY, type=openapi.TYPE_STRING, enum=["catalog", "stock", "movements"]),
            openapi.Parameter("from", openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter("to", openapi.IN_QUERY, type=openapi.TYPE_STRING),
        ],
    )
    def get(self, request):
        if refusal := _require(request, Permission.STOCK_IMPORT):
            return refusal
        what = request.query_params.get("what") or "catalog"
        with_costs = may(request.user, Permission.STOCK_VIEW_COST)
        if what == "stock":
            payload = inventory_io.export_stock(request.user.company_id, with_costs=with_costs)
        elif what == "movements":
            payload = inventory_io.export_movements(
                request.user.company_id, with_costs=with_costs,
                date_from=_datetime_param(request, "from"), date_to=_datetime_param(request, "to", end=True),
                warehouse_id=_int_param(request, "warehouse_id"), kind=request.query_params.get("kind") or None,
            )
        else:
            what = "catalog"
            payload = inventory_io.export_catalog(request.user.company_id, with_costs=with_costs)
        response = HttpResponse(
            payload, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response["Content-Disposition"] = f'attachment; filename="weel-{what}-{timezone.now():%Y%m%d}.xlsx"'
        return response


class WorkspaceInventoryImportPreviewView(_InventoryView):
    parser_classes = [MultiPartParser, FormParser]

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Read an XLSX and say what importing it would do")
    def post(self, request):
        if refusal := _require(request, Permission.STOCK_IMPORT):
            return refusal
        upload = request.FILES.get("file")
        if upload is None:
            return Response({"file": [_("Attach an XLSX file.")]}, status=status.HTTP_400_BAD_REQUEST)
        try:
            return Response(inventory_io.parse_import(request.user.company_id, upload.read()))
        except InventoryError as exc:
            return _refusal(exc)


class WorkspaceInventoryImportCommitView(_InventoryView):
    parser_classes = [JSONParser]

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Write the rows a preview produced",
                         request_body=ImportCommitSerializer)
    def post(self, request):
        if refusal := _require(request, Permission.STOCK_IMPORT):
            return refusal
        serializer = ImportCommitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = inventory_io.commit_import(
            request.user.company_id, data["rows"], author_id=request.user.id,
            update_existing=bool(data.get("update_existing", True)),
            update_fields=data.get("update_fields") or None,
        )
        record_audit(
            request.user.company_id, actor_employee_id=request.user.id, action="inventory.import",
            target_type="catalog", target_id=None,
            payload={k: v for k, v in result.items() if k != "errors"},
        )
        return Response(result)
