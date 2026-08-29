from __future__ import annotations

import secrets
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import slugify

from .permissions import CAPABILITY_MIGRATION_MAP, defaults_for_role


def generate_storefront_token() -> str:
    """Opaque, URL-safe capability used to enter one seller's storefront."""
    return secrets.token_urlsafe(24)


class Business(models.Model):
    class VerificationStatus(models.TextChoices):
        UNVERIFIED = "unverified", "تأییدنشده"
        PENDING = "pending", "در انتظار بررسی"
        VERIFIED = "verified", "تأییدشده"
        REJECTED = "rejected", "ردشده"
        SUSPENDED = "suspended", "معلق"

    class Status(models.TextChoices):
        ACTIVE = "active", "فعال"
        SUSPENDED = "suspended", "معلق"

    class Plan(models.TextChoices):
        """What the Business has been given access to.

        Deliberately two values and no billing engine. The MVP needs to tell a
        browse-only account from a selling one; everything finer than that is a
        conversation with support, not a state machine.
        """

        BROWSE = "browse", "فقط مشاهده"
        SELLER = "seller", "فروشنده"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField("نام کسب‌وکار", max_length=200)
    slug = models.SlugField("نامک", max_length=220, unique=True, allow_unicode=True)
    storefront_token = models.CharField(
        "توکن ویترین",
        max_length=64,
        unique=True,
        default=generate_storefront_token,
        editable=False,
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    verification_status = models.CharField(
        max_length=20,
        choices=VerificationStatus.choices,
        default=VerificationStatus.UNVERIFIED,
    )
    phone = models.CharField(max_length=20, blank=True)
    city = models.CharField(max_length=100, blank=True)
    province = models.CharField(max_length=100, blank=True)
    address = models.TextField(blank=True)
    website = models.URLField(blank=True)
    logo = models.ImageField(upload_to="business_logos/", blank=True, null=True)

    plan = models.CharField("پلن", max_length=20, choices=Plan.choices, default=Plan.SELLER)
    seat_limit = models.PositiveSmallIntegerField("تعداد کاربر مجاز", default=1)
    # Null means "no expiry set" rather than "expired": a Business provisioned by
    # an admin who did not fill this in must not lock itself out overnight.
    active_until = models.DateField("اعتبار تا", null=True, blank=True)
    #: Highest invoice number allocated so far. Bumped under the same row lock
    #: that already serialized invoice creation, so allocation is one UPDATE
    #: rather than a scan of every invoice this Business has ever issued —
    #: which grew with history while holding that lock. The formatted document
    #: number is derived from it; see apps.invoicing.services.allocate_number.
    invoice_sequence = models.PositiveIntegerField(default=0, editable=False)

    onboarding_step = models.PositiveSmallIntegerField(default=1)
    onboarding_completed_at = models.DateTimeField(null=True, blank=True)
    settings = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "کسب‌وکار"
        verbose_name_plural = "کسب‌وکارها"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name, allow_unicode=True) or "business"
            candidate = base
            idx = 1
            while Business.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                idx += 1
                candidate = f"{base}-{idx}"
            self.slug = candidate
        super().save(*args, **kwargs)

    @property
    def is_onboarded(self) -> bool:
        return self.onboarding_completed_at is not None


class BusinessMembership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "مالک"
        MANAGER = "manager", "مدیر"
        STAFF = "staff", "کارمند"
        VIEWER = "viewer", "بازدیدکننده"

    class Status(models.TextChoices):
        INVITED = "invited", "دعوت‌شده"
        ACTIVE = "active", "فعال"
        SUSPENDED = "suspended", "معلق"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.STAFF)
    # ``None`` means "not decided yet" and is replaced by the role defaults the
    # first time the row is saved. It is a distinct state from ``[]``, which
    # means "this member has no capabilities" and must survive a save — see
    # :meth:`save`.
    permissions = models.JSONField(default=None, null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "عضویت"
        verbose_name_plural = "عضویت‌ها"
        unique_together = ("user", "business")
        indexes = [
            models.Index(fields=["business", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} @ {self.business} ({self.role})"

    def save(self, *args, **kwargs):
        # Role defaults are materialized once, when the row is created and the
        # caller said nothing about permissions.
        #
        # The old test was ``if not self.permissions``, which cannot tell "not
        # initialized" from "deliberately empty": an admin who stripped every
        # capability from a member and saved got the role defaults handed
        # straight back, silently re-granting the create, price and sale access
        # they had just removed. ``None`` is the sentinel for "decide for me";
        # ``[]`` means what it says.
        if self.permissions is None:
            self.permissions = defaults_for_role(self.role) if self._state.adding else []
        super().save(*args, **kwargs)

    def clean(self) -> None:
        if self.permissions is not None and not isinstance(self.permissions, list):
            raise ValidationError({"permissions": "مجوزها باید لیست باشند."})

    def has_capability(self, capability: str) -> bool:
        if self.status != self.Status.ACTIVE:
            return False
        if self.role == self.Role.OWNER:
            return True
        current_code = CAPABILITY_MIGRATION_MAP.get(capability, capability)
        return current_code is not None and current_code in (self.permissions or [])


class Warehouse(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="warehouses")
    name = models.CharField("نام انبار", max_length=150)
    city = models.CharField(max_length=100, blank=True)
    address = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "انبار"
        verbose_name_plural = "انبارها"
        ordering = ["-is_default", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "name"],
                name="uniq_warehouse_name_per_business",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.business})"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_default:
            Warehouse.objects.filter(business=self.business, is_default=True).exclude(pk=self.pk).update(
                is_default=False
            )
