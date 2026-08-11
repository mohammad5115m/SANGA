from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class Notification(models.Model):
    class Kind(models.TextChoices):
        SAVED_SEARCH_MATCH = "saved_search_match", "تطابق جستجو"
        PARTNER_REQUEST = "partner_request", "درخواست همکاری"
        PARTNER_DECISION = "partner_decision", "نتیجه همکاری"
        RESERVATION_REQUEST = "reservation_request", "درخواست رزرو"
        RESERVATION_DECISION = "reservation_decision", "نتیجه رزرو"
        RESERVATION_EXPIRED = "reservation_expired", "انقضای رزرو"
        GENERAL = "general", "عمومی"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="notifications",
    )
    kind = models.CharField(max_length=40, choices=Kind.choices, default=Kind.GENERAL)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    link = models.CharField(max_length=255, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "is_read", "created_at"])]

    def __str__(self) -> str:
        return self.title
