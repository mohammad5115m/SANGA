from __future__ import annotations

import uuid
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models


class PriceTier(models.Model):
    """Extensible price tier registry. v1 exposes b2b and b2c only."""

    code = models.SlugField(max_length=32, unique=True)
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "code"]
        verbose_name = "سطح قیمت"
        verbose_name_plural = "سطوح قیمت"

    def __str__(self) -> str:
        return self.name


class LotPrice(models.Model):
    class Unit(models.TextChoices):
        PER_SQM = "per_sqm", "به ازای متر مربع"
        PER_SLAB = "per_slab", "به ازای اسلب"
        INQUIRY_ONLY = "inquiry_only", "فقط استعلام"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # FK added when inventory.InventoryLot exists; use string reference.
    lot = models.ForeignKey(
        "inventory.InventoryLot",
        on_delete=models.CASCADE,
        related_name="prices",
    )
    tier = models.ForeignKey(PriceTier, on_delete=models.PROTECT, related_name="lot_prices")
    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    currency = models.CharField(max_length=3, default="IRR")
    unit = models.CharField(max_length=20, choices=Unit.choices, default=Unit.PER_SQM)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "قیمت محموله"
        verbose_name_plural = "قیمت محموله‌ها"
        constraints = [
            models.UniqueConstraint(fields=["lot", "tier"], name="uniq_price_per_lot_tier"),
        ]

    def __str__(self) -> str:
        return f"{self.lot_id} / {self.tier.code}: {self.amount} {self.currency}"
