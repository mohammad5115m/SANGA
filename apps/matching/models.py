from __future__ import annotations

import uuid

from django.db import models


class MatchResult(models.Model):
    """Persisted rule-based match between a purchase request and a lot."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    purchase_request = models.ForeignKey(
        "purchase_requests.PurchaseRequest",
        on_delete=models.CASCADE,
        related_name="match_results",
    )
    lot = models.ForeignKey(
        "inventory.InventoryLot",
        on_delete=models.CASCADE,
        related_name="match_results",
    )
    score = models.PositiveSmallIntegerField(default=0)
    reasons = models.JSONField(default=list, blank=True)
    notified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-score", "-created_at"]
        verbose_name = "نتیجه تطبیق"
        verbose_name_plural = "نتایج تطبیق"
        constraints = [
            models.UniqueConstraint(
                fields=["purchase_request", "lot"],
                name="uniq_match_pr_lot",
            ),
        ]

    def __str__(self) -> str:
        return f"Match {self.score} :: {self.purchase_request_id} / {self.lot_id}"
