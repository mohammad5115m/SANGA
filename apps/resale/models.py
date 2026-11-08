from __future__ import annotations

import uuid

from django.db import models


class WholesaleProgram(models.Model):
    """Opt-in; existing businesses keep their current price rules until enabled."""

    warehouse = models.OneToOneField(
        "businesses.Business", on_delete=models.CASCADE, related_name="wholesale_program",
    )

    def __str__(self):
        return f"انبار {self.warehouse_id}"


class ResaleGrant(models.Model):
    program = models.ForeignKey(WholesaleProgram, on_delete=models.CASCADE, related_name="grants")
    retailer = models.ForeignKey("businesses.Business", on_delete=models.CASCADE, related_name="resale_grants")
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("program", "retailer"), name="resale_one_grant_per_partner"),
        ]

    def __str__(self):
        return f"مجوز عرضه {self.pk}"


class ResaleRule(models.Model):
    class Margin(models.TextChoices):
        FIXED = "fixed", "مبلغ ثابت به ریال در هر متر مربع"
        PERCENT = "percent", "درصدی از قیمت خرید همکاری"

    lot = models.OneToOneField("inventory.InventoryLot", on_delete=models.CASCADE, related_name="resale_rule")
    margin_kind = models.CharField(max_length=10, choices=Margin.choices)
    margin_value = models.DecimalField(max_digits=16, decimal_places=2)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(margin_value__gt=0), name="resale_margin_positive"),
        ]

    def __str__(self):
        return f"حداقل سود {self.lot_id}"


class ResaleOffer(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    grant = models.ForeignKey(ResaleGrant, on_delete=models.CASCADE, related_name="offers")
    rule = models.ForeignKey(ResaleRule, on_delete=models.CASCADE, related_name="offers")
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    special_amount = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    special_until = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at", "id")
        constraints = [
            models.UniqueConstraint(fields=("grant", "rule"), name="resale_one_offer_per_lot"),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="resale_offer_positive"),
            models.CheckConstraint(
                condition=models.Q(special_amount__isnull=True, special_until__isnull=True) | (
                    models.Q(special_amount__gt=0, special_amount__lt=models.F("amount"))
                    & models.Q(special_until__isnull=False, special_amount__isnull=False)
                ),
                name="resale_offer_valid_special",
            ),
        ]

    def __str__(self):
        return f"عرضه {self.pk}"


class ResaleRequest(models.Model):
    """Customer contact belongs to the retailer, never to the warehouse queue."""

    inquiry = models.OneToOneField("inquiries.Inquiry", on_delete=models.CASCADE, related_name="resale_request")
    offer = models.ForeignKey(ResaleOffer, null=True, on_delete=models.SET_NULL, related_name="requests")
    warehouse = models.ForeignKey("businesses.Business", on_delete=models.PROTECT, related_name="resale_requests")
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    displayed_amount = models.DecimalField(max_digits=16, decimal_places=2, null=True)
    requested_at = models.DateTimeField(null=True, blank=True)
    quote_until = models.DateTimeField(null=True, blank=True)
    wholesale_amount = models.DecimalField(max_digits=16, decimal_places=2, null=True)
    retail_amount = models.DecimalField(max_digits=16, decimal_places=2, null=True)
    wholesale_invoice = models.OneToOneField(
        "invoicing.SalesInvoice", null=True, blank=True, on_delete=models.SET_NULL, related_name="resale_purchase",
    )
    retail_invoice = models.OneToOneField(
        "invoicing.SalesInvoice", null=True, blank=True, on_delete=models.SET_NULL, related_name="resale_sale",
    )

    class Meta:
        indexes = [models.Index(fields=("warehouse", "-requested_at"))]

    def __str__(self):
        return f"درخواست عرضه {self.pk}"

    @property
    def can_request_purchase(self):
        from apps.invoicing.models import SalesInvoice

        return not self.retail_invoice_id and (
            not self.wholesale_invoice_id or self.wholesale_invoice.status in {
                SalesInvoice.Status.CANCELLED, SalesInvoice.Status.CANCELLED_BY_SENDER,
            }
        )
