"""One compact, audience-aware filter schema shared by every inventory surface."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from decimal import Decimal, InvalidOperation

from django.db.models import F, Max, Min, Q, QuerySet
from django.utils import timezone

from apps.core.persian import normalize_persian_text
from apps.pricing.queries import effective_amount_subquery

from .models import InventoryLot
from .policy import Audience

_FILTER_TIER: dict[str, str] = {"owner": "b2c", "colleague": "b2b", "public": "b2c"}

SORT_CHOICES: tuple[tuple[str, str], ...] = (
    ("recent", "جدیدترین"),
    ("price_asc", "ارزان‌ترین"),
    ("price_desc", "گران‌ترین"),
    ("confirmed", "تازه‌ترین موجودی"),
)


def _decimal(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _text(value) -> str:
    return normalize_persian_text(str(value)).strip() if value else ""


@dataclass(frozen=True)
class ItemFilterSpec:
    q: str = ""
    stone: str = ""
    processing_type: str = ""
    applications: list[str] = field(default_factory=list)
    availability: str = ""
    price_min: Decimal | None = None
    price_max: Decimal | None = None
    min_qty_sqm: Decimal | None = None
    sort: str = "recent"

    @classmethod
    def from_dict(cls, data: dict | None) -> ItemFilterSpec:
        data = data or {}
        known = {item.name for item in fields(cls)}
        clean: dict = {}
        for key in ("q", "stone", "processing_type"):
            if key in data:
                clean[key] = _text(data.get(key))
        # Compatibility for links generated before the controlled stone FK.
        if not clean.get("stone") and data.get("stone_type"):
            clean["stone"] = _text(data.get("stone_type"))
        for key in ("price_min", "price_max", "min_qty_sqm"):
            if key in data:
                clean[key] = _decimal(data.get(key))
        if "applications" in data:
            raw = data.get("applications") or []
            if isinstance(raw, str):
                raw = [raw]
            clean["applications"] = [str(item) for item in raw if item]
        if "availability" in data:
            value = str(data.get("availability") or "")
            clean["availability"] = value if value in InventoryLot.Availability.values else ""
        if "sort" in data:
            value = str(data.get("sort") or "recent")
            clean["sort"] = value if value in dict(SORT_CHOICES) else "recent"
        return cls(**{key: value for key, value in clean.items() if key in known})

    def to_dict(self) -> dict:
        result: dict = {}
        for key, value in asdict(self).items():
            if value in (None, "", [], False) or (key == "sort" and value == "recent"):
                continue
            result[key] = str(value) if isinstance(value, Decimal) else value
        return result

    @property
    def is_empty(self) -> bool:
        return not self.to_dict()

    def apply_non_price(self, qs: QuerySet[InventoryLot]) -> QuerySet[InventoryLot]:
        if self.q:
            qs = qs.filter(
                Q(product__commercial_name__icontains=self.q)
                | Q(product__name_suffix__icontains=self.q)
                | Q(product__stone__name__icontains=self.q)
                | Q(product__pattern__icontains=self.q)
                | Q(lot_code__icontains=self.q)
                | Q(processing_type__icontains=self.q)
            )
        if self.stone:
            try:
                stone_id = int(self.stone)
            except (TypeError, ValueError):
                qs = qs.filter(product__stone__name__icontains=self.stone)
            else:
                qs = qs.filter(product__stone_id=stone_id)
        if self.processing_type:
            qs = qs.filter(processing_type__icontains=self.processing_type)
        if self.applications:
            qs = qs.filter(product__applications__code__in=self.applications).distinct()
        if self.availability:
            qs = qs.filter(availability_status=self.availability)
        if self.min_qty_sqm is not None:
            qs = qs.filter(
                available_sqm__gte=self.min_qty_sqm,
                stock_expires_at__gt=timezone.now(),
            )
        return qs

    def apply(self, qs: QuerySet[InventoryLot], *, audience: Audience = "public") -> QuerySet[InventoryLot]:
        qs = self.apply_non_price(qs)
        qs = self._apply_price(qs, audience=audience)
        return self._apply_sort(qs, audience=audience)

    def _apply_price(self, qs: QuerySet[InventoryLot], *, audience: Audience) -> QuerySet[InventoryLot]:
        if self.price_min is None and self.price_max is None:
            return qs
        tier = _FILTER_TIER.get(audience, "b2c")
        qs = qs.annotate(_effective_price=effective_amount_subquery(tier))
        if self.price_min is not None:
            qs = qs.filter(_effective_price__gte=self.price_min)
        if self.price_max is not None:
            qs = qs.filter(_effective_price__lte=self.price_max)
        return qs

    def _apply_sort(self, qs: QuerySet[InventoryLot], *, audience: Audience) -> QuerySet[InventoryLot]:
        if self.sort == "confirmed":
            return qs.order_by("-stock_confirmed_at", "-updated_at")
        if self.sort in ("price_asc", "price_desc"):
            tier = _FILTER_TIER.get(audience, "b2c")
            qs = qs.annotate(_effective_price=effective_amount_subquery(tier))
            if self.sort == "price_asc":
                return qs.order_by(F("_effective_price").asc(nulls_last=True), "-updated_at")
            return qs.order_by(F("_effective_price").desc(nulls_last=True), "-updated_at")
        return qs.order_by("-updated_at")


def effective_price_bounds(
    qs: QuerySet[InventoryLot], *, spec: ItemFilterSpec, audience: Audience
) -> tuple[Decimal | None, Decimal | None]:
    """Bounds after non-price filters, using exactly the price this audience sees."""

    tier = _FILTER_TIER.get(audience, "b2c")
    values = spec.apply_non_price(qs).annotate(_effective_price=effective_amount_subquery(tier)).aggregate(
        minimum=Min("_effective_price"), maximum=Max("_effective_price")
    )
    return values["minimum"], values["maximum"]
