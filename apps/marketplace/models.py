from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class SavedSearch(models.Model):
    """A stored marketplace filter. Moved here from the removed partners app."""

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
