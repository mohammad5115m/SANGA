from __future__ import annotations

import secrets
import uuid
from datetime import timedelta
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.text import slugify


def generate_public_token() -> str:
    """Opaque, stable, URL-safe identifier for a shareable item page.

    Deliberately not the primary key: share links are pasted into WhatsApp and
    Telegram, and an opaque token keeps them from doubling as a handle on
    internal records.
    """
    return secrets.token_urlsafe(12)


class Application(models.Model):
    """Where a stone can be used — a controlled vocabulary, not free text.

    Application is one of the main things buyers filter by, and free text cannot
    be filtered reliably: «نمای بیرونی», «نما بیرونی» and «نمای خارجی» would be
    three different values for one idea. The list is platform-wide rather than
    per-business so that a colleague's search matches another seller's items.
    """

    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField("کاربرد", max_length=100)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "کاربرد"
        verbose_name_plural = "کاربردها"

    def __str__(self) -> str:
        return self.name


#: Seeded by the ``sync_applications`` data migration and kept in code so the
#: list is reviewable in a diff. Extending it means adding a row here plus a
#: migration that calls the same sync helper.
class VocabularyTerm(models.Model):
    """An admin-controlled stone vocabulary shared by every seller."""

    class Kind(models.TextChoices):
        STONE_TYPE = "stone_type", "نوع سنگ"

    kind = models.CharField(max_length=20, choices=Kind.choices)
    #: The spelling SANGA stores when it recognises a value.
    name = models.CharField("عنوان", max_length=100)
    #: Alternative spellings matched after orthographic normalization. Distinct
    #: commercial stone types such as «کریستال» and «چینی» remain separate terms.
    aliases = models.JSONField(default=list, blank=True)
    code_prefix = models.CharField(
        "پیشوند کد محصول",
        max_length=3,
        blank=True,
        help_text="حروف لاتین بزرگ؛ برای نمونه T یا CH",
    )
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "واژه‌نامه"
        verbose_name_plural = "واژه‌نامه‌ها"
        ordering = ["kind", "sort_order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["kind", "name"], name="uniq_vocabulary_term"),
            models.UniqueConstraint(
                fields=["code_prefix"],
                condition=models.Q(kind="stone_type") & ~models.Q(code_prefix=""),
                name="uniq_stone_code_prefix",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()}: {self.name}"


#: Seeded on migration. Aliases carry the spellings that normalization cannot
#: reach on its own — different words for one stone, not different letters.
DEFAULT_VOCABULARY: dict[str, tuple[tuple[str, tuple[str, ...], str], ...]] = {
    VocabularyTerm.Kind.STONE_TYPE: (
        ("تراورتن", ("تراورتون", "travertine"), "T"),
        ("مرمریت", ("marble tile",), "M"),
        ("گرانیت", ("granite",), "G"),
        ("کریستال", ("crystal",), "C"),
        ("مرمر", ("انیکس", "اونیکس", "onyx"), "O"),
        ("لایمستون", ("لایم استون", "لایم‌استون", "limestone"), "L"),
        ("ترامیت", ("tramite",), "TR"),
        ("چینی", ("سنگ چینی",), "CH"),
    ),
}


DEFAULT_APPLICATIONS: tuple[tuple[str, str], ...] = (
    ("exterior-facade", "نمای بیرونی"),
    ("interior-wall", "دیوار داخلی"),
    ("floor", "کف"),
    ("stairs", "راه پله"),
    ("parking", "پارکینگ"),
    ("landscape", "محوطه"),
    ("bathroom", "سرویس بهداشتی"),
    ("counter", "کانتر"),
    ("column", "ستون"),
    ("pool", "استخر"),
)


class Product(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="products",
    )
    commercial_name = models.CharField("نام تجاری", max_length=200, editable=False)
    name_suffix = models.CharField("نام تکمیلی", max_length=160, blank=True)
    slug = models.SlugField(max_length=220, allow_unicode=True)
    stone = models.ForeignKey(
        VocabularyTerm,
        on_delete=models.PROTECT,
        related_name="products",
        limit_choices_to={"kind": VocabularyTerm.Kind.STONE_TYPE},
        verbose_name="نوع سنگ",
    )
    pattern = models.CharField("طرح/بافت", max_length=150, blank=True)
    vein_notes = models.CharField(max_length=255, blank=True)
    applications = models.ManyToManyField(
        Application,
        related_name="products",
        blank=True,
        verbose_name="کاربردها",
    )
    interior_suitable = models.BooleanField(default=True)
    exterior_suitable = models.BooleanField(default=False)
    technical_notes = models.TextField(blank=True)
    description_public = models.TextField("توضیح مشتری", blank=True)
    description_professional = models.TextField("توضیح حرفه‌ای", blank=True)
    alt_names = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "محصول"
        verbose_name_plural = "محصولات"
        ordering = ["commercial_name"]
        constraints = [
            models.UniqueConstraint(fields=["business", "slug"], name="uniq_product_slug_per_business"),
        ]

    def __str__(self) -> str:
        return self.commercial_name

    def save(self, *args, **kwargs):
        stone_name = self.stone.name if self.stone_id else ""
        self.name_suffix = " ".join((self.name_suffix or "").split())
        self.commercial_name = " ".join(part for part in ("سنگ", stone_name, self.name_suffix) if part)
        if not self.slug:
            base = slugify(self.commercial_name, allow_unicode=True) or "product"
            candidate = base
            idx = 1
            while Product.objects.filter(business=self.business, slug=candidate).exclude(pk=self.pk).exists():
                idx += 1
                candidate = f"{base}-{idx}"
            self.slug = candidate
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"commercial_name", "name_suffix", "slug"}
        super().save(*args, **kwargs)


class InventoryLot(models.Model):
    """A sellable instance of a Product.

    Four lifecycle questions are kept on four separate fields, and are never
    merged into one status column:

    * ``is_visible``          — should the seller publish this at all?
    * ``availability_status`` — is it offered for sale right now?
    * stock freshness         — do we trust the quantity? (derived, see freshness.py)
    * ``deleted_at``          — should it still exist as an active business object?

    The distinction that matters most to users is «ناموجود» versus «استعلام
    موجودی». The first is ``availability_status`` and removes the item from
    every buyer-facing surface. The second is derived from stock expiry and
    leaves the item discoverable.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "پیش‌نویس"
        ACTIVE = "active", "فعال"

    class Availability(models.TextChoices):
        AVAILABLE = "available", "موجود"
        UNAVAILABLE = "unavailable", "ناموجود"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="lots",
    )
    product = models.OneToOneField(Product, on_delete=models.PROTECT, related_name="lot")
    lot_code = models.CharField("کد محصول", max_length=12, unique=True, editable=False)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.DRAFT)

    is_visible = models.BooleanField("منتشر شده", default=False)
    availability_status = models.CharField(
        "وضعیت موجود بودن",
        max_length=20,
        choices=Availability.choices,
        default=Availability.AVAILABLE,
    )
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    public_token = models.CharField(
        max_length=32,
        unique=True,
        default=generate_public_token,
        editable=False,
    )

    stock_confirmed_at = models.DateTimeField(null=True, blank=True)
    stock_valid_for_days = models.PositiveSmallIntegerField("اعتبار موجودی (روز)", default=7)
    # Derived from the two fields above and refreshed on save. It exists so that
    # "which items need a stock check?" is an indexed WHERE clause instead of a
    # Python loop over every row. Note this is derived *on write*, not swept by a
    # scheduled job — nothing mutates a row just because time passed.
    stock_expires_at = models.DateTimeField(null=True, blank=True, db_index=True, editable=False)

    available_sqm = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        default=None,
        validators=[MinValueValidator(Decimal("0"))],
    )
    length_cm = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    width_cm = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    thickness_mm = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    processing_type = models.CharField(max_length=100, default="ساب خورده")
    min_sale_qty = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    ready_for_loading_at = models.DateField(null=True, blank=True)
    photographed_at = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True)
    defect_notes = models.TextField(blank=True)
    is_featured = models.BooleanField(default=False)
    is_urgent_sale = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "محصول قابل فروش"
        verbose_name_plural = "محصولات قابل فروش"
        ordering = ["-updated_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(available_sqm__isnull=True) | models.Q(available_sqm__gte=0),
                name="lot_available_sqm_nonnegative",
            ),
        ]
        indexes = [
            models.Index(fields=["business", "status"]),
            models.Index(fields=["business", "stock_confirmed_at"], name="inventory_i_busines_stockc_idx"),
            # Covers the buyer-facing eligibility predicate in inventory.policy.
            models.Index(
                fields=["is_visible", "availability_status", "deleted_at"],
                name="inventory_i_elig_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.lot_code} — {self.product.commercial_name}"

    def save(self, *args, **kwargs):
        if self.pk and hasattr(self, "_original_lot_code") and self.lot_code != self._original_lot_code:
            from django.core.exceptions import ValidationError

            raise ValidationError({"lot_code": "کد محصول پس از ایجاد قابل تغییر نیست."})
        expires_at = self.compute_stock_expiry()
        if expires_at != self.stock_expires_at:
            self.stock_expires_at = expires_at
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                kwargs["update_fields"] = {*update_fields, "stock_expires_at"}
        super().save(*args, **kwargs)
        self._original_lot_code = self.lot_code

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._original_lot_code = instance.lot_code
        return instance

    # --- derived lifecycle state ---------------------------------------------

    def compute_stock_expiry(self):
        """When the confirmed quantity stops being trustworthy.

        ``None`` means the question does not apply: either the seller never
        confirmed, or the mode carries no number that could go stale.
        """
        if self.available_sqm is None:
            return None
        if self.stock_confirmed_at is None:
            return None
        return self.stock_confirmed_at + timedelta(days=self.stock_valid_for_days)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    @property
    def is_unavailable(self) -> bool:
        return self.availability_status == self.Availability.UNAVAILABLE

    @property
    def thickness_cm(self):
        return self.thickness_mm / Decimal("10") if self.thickness_mm is not None else None

    @property
    def is_stock_fresh(self) -> bool:
        expires_at = self.compute_stock_expiry()
        return expires_at is not None and expires_at > timezone.now()

    def mark_stock_confirmed(self) -> None:
        self.stock_confirmed_at = timezone.now()
        self.save(update_fields=["stock_confirmed_at", "updated_at"])

    @classmethod
    def needs_stock_confirmation_q(cls):
        """Predicate for "the seller is showing a number they no longer vouch for".

        Uses the derived ``stock_expires_at`` column so callers can count,
        paginate and index this instead of filtering in Python.
        """
        from django.db.models import Q

        return Q(available_sqm__isnull=False) & (
            Q(stock_expires_at__isnull=True) | Q(stock_expires_at__lte=timezone.now())
        )


class LotMedia(models.Model):
    class Kind(models.TextChoices):
        IMAGE = "image", "تصویر"
        VIDEO = "video", "ویدیو"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lot = models.ForeignKey(InventoryLot, on_delete=models.CASCADE, related_name="media")
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.IMAGE)
    file = models.FileField(upload_to="lot_media/")
    thumbnail = models.ImageField(upload_to="lot_media/thumbs/", blank=True, null=True)
    caption = models.CharField(max_length=255, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "created_at"]
        verbose_name = "رسانه محصول"
        verbose_name_plural = "رسانه‌های محصول"
        constraints = [
            # One cover per gallery, enforced where it cannot be raced. The
            # services demote the current primary before promoting the new one,
            # but two uploads arriving together both did that and both won,
            # leaving an item with two covers and a card that picked whichever
            # the ordering happened to return first.
            models.UniqueConstraint(
                fields=["lot"],
                condition=models.Q(is_primary=True),
                name="uniq_primary_media_per_lot",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.lot.lot_code} media"
