from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


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
    """One audience's price for one item.

    Freshness and special-sale pricing live on this row rather than on the item,
    and that placement is a security decision, not a modelling preference. A
    single ``special_price`` column on the item would be an unlabelled number
    that some public template eventually renders — leaking a B2B figure. Here,
    the tier gate that already protects ``amount`` protects the special price
    for free.
    """

    class Mode(models.TextChoices):
        FIXED = "fixed", "قیمت مشخص"
        INQUIRY = "inquiry", "استعلام قیمت"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # FK added when inventory.InventoryLot exists; use string reference.
    lot = models.ForeignKey(
        "inventory.InventoryLot",
        on_delete=models.CASCADE,
        related_name="prices",
    )
    tier = models.ForeignKey(PriceTier, on_delete=models.PROTECT, related_name="lot_prices")
    mode = models.CharField("نوع قیمت", max_length=20, choices=Mode.choices, default=Mode.FIXED)
    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    currency = models.CharField(max_length=3, default="IRR")

    # Price validity is independent of stock validity: a seller may trust their
    # stock for ten days and their price for two.
    price_confirmed_at = models.DateTimeField(null=True, blank=True)
    price_valid_for_days = models.PositiveSmallIntegerField("اعتبار قیمت (روز)", default=7)
    # Derived on write from the two fields above, so "which prices need
    # rechecking?" is an indexed query rather than a Python scan. Nothing
    # rewrites this on a timer.
    price_expires_at = models.DateTimeField(null=True, blank=True, db_index=True, editable=False)

    special_amount = models.DecimalField(
        "قیمت فروش ویژه",
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    special_until = models.DateTimeField("پایان فروش ویژه", null=True, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "قیمت محصول"
        verbose_name_plural = "قیمت محصولات"
        constraints = [
            models.UniqueConstraint(fields=["lot", "tier"], name="uniq_price_per_lot_tier"),
            models.CheckConstraint(
                condition=models.Q(mode="inquiry") | models.Q(amount__isnull=False),
                name="price_fixed_requires_amount",
            ),
            models.CheckConstraint(
                condition=models.Q(amount__isnull=True) | models.Q(amount__gt=0),
                name="price_amount_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(special_amount__isnull=True) | models.Q(special_amount__gt=0),
                name="price_special_positive",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(special_amount__isnull=True, special_until__isnull=True)
                    | models.Q(special_amount__isnull=False, special_until__isnull=False)
                ),
                name="price_special_pair",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(special_amount__isnull=True)
                    | (
                        models.Q(mode="fixed", amount__isnull=False)
                        & models.Q(special_amount__lt=models.F("amount"))
                    )
                ),
                name="price_special_below_amount",
            ),
            models.CheckConstraint(
                condition=models.Q(price_valid_for_days__gte=1, price_valid_for_days__lte=365),
                name="price_valid_days_range",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.lot_id} / {self.tier.code}: {self.amount} {self.currency}"

    def save(self, *args, **kwargs):
        expires_at = self.compute_price_expiry()
        if expires_at != self.price_expires_at:
            self.price_expires_at = expires_at
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                kwargs["update_fields"] = {*update_fields, "price_expires_at"}
        super().save(*args, **kwargs)

    def compute_price_expiry(self):
        if self.mode != self.Mode.FIXED or self.price_confirmed_at is None:
            return None
        return self.price_confirmed_at + timedelta(days=self.price_valid_for_days)

    @property
    def is_fresh(self) -> bool:
        expires_at = self.compute_price_expiry()
        return expires_at is not None and expires_at > timezone.now()

    @classmethod
    def needs_confirmation_q(cls):
        """Predicate for a fixed price that has stopped being current."""
        from django.db.models import Q

        return Q(mode=cls.Mode.FIXED) & (
            Q(price_expires_at__isnull=True) | Q(price_expires_at__lte=timezone.now())
        )

    @property
    def special_is_live(self) -> bool:
        if self.special_amount is None or self.special_until is None:
            return False
        return self.special_until > timezone.now()

    def effective_amount(self) -> Decimal | None:
        """The number to show, or ``None`` when it must read «استعلام قیمت».

        An expired fixed price returns ``None``: the stored figure is kept so the
        seller can see what they last set, but it stops being presented as
        current. A live special sale beats the standard amount.
        """
        if self.mode == self.Mode.INQUIRY:
            return None
        if self.special_is_live:
            return self.special_amount
        if not self.is_fresh:
            return None
        return self.amount


# ContactPrice (a per-colleague price override) was removed in V2. Two channels —
# B2B and B2C — are what the business actually needs, and a third, per-counterparty
# axis made every price question ("what does this cost?") depend on who is asking
# in a way sellers could not audit. See pricing.0003.
