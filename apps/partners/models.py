from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class PartnerRelation(models.Model):
    class Status(models.TextChoices):
        REQUESTED = "requested", "درخواست‌شده"
        APPROVED = "approved", "تأییدشده"
        REJECTED = "rejected", "ردشده"
        BLOCKED = "blocked", "مسدود"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    supplier_business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="partner_relations_as_supplier",
    )
    partner_business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="partner_relations_as_partner",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REQUESTED)
    message = models.CharField(max_length=255, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "رابطه همکاری"
        verbose_name_plural = "روابط همکاری"
        constraints = [
            models.UniqueConstraint(
                fields=["supplier_business", "partner_business"],
                name="uniq_partner_relation",
            ),
            models.CheckConstraint(
                condition=~models.Q(supplier_business=models.F("partner_business")),
                name="partner_relation_not_self",
            ),
        ]
        indexes = [
            models.Index(fields=["supplier_business", "status"]),
            models.Index(fields=["partner_business", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.partner_business} → {self.supplier_business} ({self.status})"


class SupplierFollow(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    follower_business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="followed_suppliers",
    )
    supplier_business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="followers",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="supplier_follows",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "دنبال‌کردن تأمین‌کننده"
        verbose_name_plural = "دنبال‌کردن تأمین‌کنندگان"
        constraints = [
            models.UniqueConstraint(
                fields=["follower_business", "supplier_business"],
                name="uniq_supplier_follow",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.follower_business} follows {self.supplier_business}"


class SavedSearch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="saved_searches",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="saved_searches",
    )
    name = models.CharField(max_length=150)
    query = models.JSONField(default=dict, blank=True)
    notify_enabled = models.BooleanField(default=True)
    last_matched_at = models.DateTimeField(null=True, blank=True)
    last_notified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = "جستجوی ذخیره‌شده"
        verbose_name_plural = "جستجوهای ذخیره‌شده"

    def __str__(self) -> str:
        return self.name
