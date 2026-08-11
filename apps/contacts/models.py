from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class Contact(models.Model):
    """A private business contact (customer / supplier / trader).

    A contact belongs to exactly one owning ``business`` and is never visible to
    any other business. ``linked_business`` is an optional, privacy-safe pointer
    to a real platform business (only an approved partner) — it never exposes the
    linked business's own private contacts or financial records.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="contacts",
    )
    display_name = models.CharField("نام / نام کسب‌وکار", max_length=200)
    phone = models.CharField("موبایل/تلفن", max_length=32, blank=True)
    address = models.TextField("آدرس", blank=True)
    notes = models.TextField("یادداشت", blank=True)

    is_customer = models.BooleanField("مشتری", default=False)
    is_supplier = models.BooleanField("تأمین‌کننده", default=False)
    is_trader = models.BooleanField("واسطه", default=False)

    linked_business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="linked_contacts",
        help_text="اتصال اختیاری به یک همکار تأییدشده",
    )

    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_contacts",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "مخاطب"
        verbose_name_plural = "مخاطبین"
        ordering = ["display_name"]
        constraints = [
            # One partner, one ledger: without this, two contacts of the same
            # business could both point at the same platform partner and that
            # partner's balance would silently split across two statements.
            models.UniqueConstraint(
                fields=["business", "linked_business"],
                condition=models.Q(linked_business__isnull=False),
                name="uniq_linked_business_per_business",
            ),
        ]
        indexes = [
            models.Index(fields=["business", "is_active"]),
            models.Index(fields=["business", "display_name"]),
        ]

    def __str__(self) -> str:
        return self.display_name

    @property
    def relationship_labels(self) -> list[str]:
        labels: list[str] = []
        if self.is_customer:
            labels.append("مشتری")
        if self.is_supplier:
            labels.append("تأمین‌کننده")
        if self.is_trader:
            labels.append("واسطه")
        return labels
