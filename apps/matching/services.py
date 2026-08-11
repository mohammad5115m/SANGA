from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.db.models import QuerySet

from apps.core.persian import normalize_persian_text
from apps.inventory.models import InventoryLot
from apps.marketplace.selectors import marketplace_lots_for
from apps.purchase_requests.models import PurchaseRequest

from .models import MatchResult


@dataclass(frozen=True)
class ScoredMatch:
    lot: InventoryLot
    score: int
    reasons: list[str]


class MatchingService(ABC):
    @abstractmethod
    def find_matches(self, purchase_request: PurchaseRequest, *, limit: int = 20) -> list[ScoredMatch]:
        raise NotImplementedError


class RuleBasedMatchingService(MatchingService):
    """
    Deterministic supply-demand matcher.

    Scoring (rough weights):
    - stone type: 30
    - color: 20
    - quantity availability: 20
    - thickness proximity: 10
    - grade: 10
    - budget vs B2B price: 10
    - city/warehouse relevance: 5 (bonus)
    """

    def candidate_lots(self, purchase_request: PurchaseRequest) -> QuerySet[InventoryLot]:
        # Sellers see demand; matching uses lots visible in B2B network to the requester
        # plus the requester shouldn't match their own inventory as supply for themselves.
        return marketplace_lots_for(purchase_request.business).filter(
            status__in=[
                InventoryLot.Status.AVAILABLE,
                InventoryLot.Status.NEEDS_CONFIRMATION,
                InventoryLot.Status.PARTIALLY_SOLD,
            ]
        )

    def score_lot(self, purchase_request: PurchaseRequest, lot: InventoryLot) -> ScoredMatch | None:
        score = 0
        reasons: list[str] = []
        product = lot.product

        pr_stone = normalize_persian_text(purchase_request.stone_type or "")
        lot_stone = normalize_persian_text(product.stone_type or "")
        if pr_stone and lot_stone:
            if pr_stone == lot_stone:
                score += 30
                reasons.append("نوع سنگ یکسان")
            elif pr_stone in lot_stone or lot_stone in pr_stone:
                score += 18
                reasons.append("نوع سنگ مشابه")
            elif not purchase_request.similar_accepted:
                return None
        elif not pr_stone:
            score += 5

        pr_color = normalize_persian_text(purchase_request.color or "")
        lot_color = normalize_persian_text(product.primary_color or "")
        if pr_color and lot_color:
            if pr_color == lot_color:
                score += 20
                reasons.append("رنگ یکسان")
            elif pr_color in lot_color or lot_color in pr_color:
                score += 12
                reasons.append("رنگ نزدیک")
            elif not purchase_request.similar_accepted:
                return None

        if lot.available_sqm >= purchase_request.required_qty_sqm:
            score += 20
            reasons.append("متراژ کافی")
        elif lot.available_sqm >= purchase_request.required_qty_sqm * Decimal("0.7"):
            score += 10
            reasons.append("متراژ نسبتاً کافی")
        else:
            return None

        if purchase_request.thickness_mm and lot.thickness_mm:
            delta = abs(lot.thickness_mm - purchase_request.thickness_mm)
            if delta <= Decimal("1"):
                score += 10
                reasons.append("ضخامت منطبق")
            elif delta <= Decimal("3"):
                score += 5
                reasons.append("ضخامت نزدیک")

        pr_grade = normalize_persian_text(purchase_request.acceptable_grade or "")
        lot_grade = normalize_persian_text(lot.grade or "")
        if pr_grade and lot_grade and (pr_grade == lot_grade or pr_grade in lot_grade):
            score += 10
            reasons.append("سورت قابل قبول")

        if purchase_request.budget_amount:
            b2b = lot.prices.filter(tier__code="b2b").first()
            if b2b and b2b.amount is not None:
                if b2b.amount <= purchase_request.budget_amount:
                    score += 10
                    reasons.append("در بودجه")
                elif b2b.amount <= purchase_request.budget_amount * Decimal("1.15"):
                    score += 4
                    reasons.append("نزدیک بودجه")

        dest = normalize_persian_text(purchase_request.destination_city or "")
        wh_city = normalize_persian_text(getattr(lot.warehouse, "city", "") or "")
        biz_city = normalize_persian_text(lot.business.city or "")
        if dest and (dest == wh_city or dest == biz_city):
            score += 5
            reasons.append("نزدیکی جغرافیایی")

        if score < 25:
            return None
        return ScoredMatch(lot=lot, score=score, reasons=reasons)

    def find_matches(self, purchase_request: PurchaseRequest, *, limit: int = 20) -> list[ScoredMatch]:
        scored: list[ScoredMatch] = []
        for lot in self.candidate_lots(purchase_request)[:200]:
            result = self.score_lot(purchase_request, lot)
            if result is not None:
                scored.append(result)
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:limit]


def get_matching_service() -> MatchingService:
    return RuleBasedMatchingService()


@transaction.atomic
def persist_matches(purchase_request: PurchaseRequest, *, limit: int = 20) -> list[MatchResult]:
    service = get_matching_service()
    matches = service.find_matches(purchase_request, limit=limit)
    saved: list[MatchResult] = []
    for match in matches:
        obj, _created = MatchResult.objects.update_or_create(
            purchase_request=purchase_request,
            lot=match.lot,
            defaults={"score": match.score, "reasons": match.reasons},
        )
        saved.append(obj)
    if matches and purchase_request.status == PurchaseRequest.Status.OPEN:
        purchase_request.status = PurchaseRequest.Status.MATCHING
        purchase_request.save(update_fields=["status", "updated_at"])
    return saved
