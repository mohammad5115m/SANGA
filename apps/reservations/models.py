from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class Reservation(models.Model):
    """A quantity hold placed by a requester business against a seller's lot."""

    class Status(models.TextChoices):
        REQUESTED = "requested", "درخواست‌شده"
        APPROVED = "approved", "تأییدشده"
        REJECTED = "rejected", "ردشده"
        CANCELLED = "cancelled", "لغوشده"
        EXPIRED = "expired", "منقضی"
        CONVERTED = "converted", "تبدیل به فروش"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lot = models.ForeignKey(
        "inventory.InventoryLot",
        on_delete=models.CASCADE,
        related_name="reservations",
    )
    seller_business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="reservations",
    )
    requester_business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="reservation_requests",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reservations_requested",
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reservations_decided",
    )
    source_offer = models.ForeignKey(
        "purchase_requests.PurchaseOffer",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reservations",
    )
    quantity_sqm = models.DecimalField(
        "متراژ رزرو",
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REQUESTED)
    expires_at = models.DateTimeField(null=True, blank=True)
    extended_count = models.PositiveSmallIntegerField(default=0)
    released_at = models.DateTimeField(null=True, blank=True)
    reason = models.CharField("علت", max_length=255, blank=True)
    notes = models.TextField("توضیحات", blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "رزرو"
        verbose_name_plural = "رزروها"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity_sqm__gt=0),
                name="reservation_quantity_positive",
            ),
        ]
        indexes = [
            models.Index(fields=["seller_business", "status"]),
            models.Index(fields=["requester_business", "status"]),
            models.Index(fields=["status", "expires_at"]),
        ]

    def __str__(self) -> str:
        return f"Reservation {self.quantity_sqm} m² · {self.get_status_display()}"

    @property
    def is_active_hold(self) -> bool:
        return self.status == self.Status.APPROVED

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            self.Status.REJECTED,
            self.Status.CANCELLED,
            self.Status.EXPIRED,
            self.Status.CONVERTED,
        }
