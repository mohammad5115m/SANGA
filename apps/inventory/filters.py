"""One filter schema, shared by every surface that lists inventory.

«موجودی من», the colleague marketplace, public search and rule-based catalogs
all ask the same kinds of question. Before this module each of them had its own
slightly different filter function, so a fix in one never reached the others.

:class:`ItemFilterSpec` round-trips through plain dicts, which is what lets a
rule-based catalog be *literally* a stored search rather than a second filtering
language that has to be kept in step with the first.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from decimal import Decimal, InvalidOperation

from django.db.models import Q, QuerySet
from django.utils import timezone

from apps.core.persian import normalize_persian_text

from .models import InventoryLot
from .policy import Audience

#: Price tier a given audience's price filters apply to. A public visitor
#: filtering by price must be filtering B2C numbers, never B2B ones.
_FILTER_TIER: dict[str, str] = {
    "owner": "b2c",
    "colleague": "b2b",
    "public": "b2c",
}

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
    """A serializable description of "which items".

    Every field is optional and an empty spec means "no narrowing", so the same
    object works for an unfiltered listing and for a tightly-scoped catalog rule.
    """

    q: str = ""
    stone_type: str = ""
    color: str = ""
    quarry_region: str = ""
    processing_type: str = ""
    grade: str = ""
    applications: list[str] = field(default_factory=list)
    thickness_min: Decimal | None = None
    thickness_max: Decimal | None = None
    price_min: Decimal | None = None
    price_max: Decimal | None = None
    min_qty_sqm: Decimal | None = None
    stock_mode: str = ""
    only_special: bool = False
    sort: str = "recent"

    # --- serialization --------------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict | None) -> ItemFilterSpec:
        """Build a spec from untrusted input (query string or stored rule JSON).

        Unknown keys are dropped and unparseable numbers become ``None`` rather
        than raising: a catalog rule saved by an older version of the form must
        keep resolving, and a hand-edited query string must not 500.
        """
        data = data or {}
        known = {f.name for f in fields(cls)}
        clean: dict = {}

        for key in ("q", "stone_type", "color", "quarry_region", "processing_type", "grade"):
            if key in data:
                clean[key] = _text(data.get(key))
        for key in ("thickness_min", "thickness_max", "price_min", "price_max", "min_qty_sqm"):
            if key in data:
                clean[key] = _decimal(data.get(key))
        if "applications" in data:
            raw = data.get("applications") or []
            if isinstance(raw, str):
                raw = [raw]
            clean["applications"] = [str(item) for item in raw if item]
        if "stock_mode" in data:
            mode = str(data.get("stock_mode") or "")
            clean["stock_mode"] = mode if mode in InventoryLot.StockMode.values else ""
        if "only_special" in data:
            clean["only_special"] = bool(data.get("only_special"))
        if "sort" in data:
            sort = str(data.get("sort") or "recent")
            clean["sort"] = sort if sort in dict(SORT_CHOICES) else "recent"

        return cls(**{k: v for k, v in clean.items() if k in known})

    def to_dict(self) -> dict:
        """JSON-safe form, with empty values dropped so stored rules stay small."""
        out: dict = {}
        for key, value in asdict(self).items():
            if value in (None, "", [], False):
                continue
            if key == "sort" and value == "recent":
                continue
            out[key] = str(value) if isinstance(value, Decimal) else value
        return out

    @property
    def is_empty(self) -> bool:
        return not self.to_dict()

    # --- query ----------------------------------------------------------------

    def apply(self, qs: QuerySet[InventoryLot], *, audience: Audience = "public") -> QuerySet[InventoryLot]:
        """Narrow ``qs`` according to this spec.

        ``qs`` is expected to already be scoped by
        :func:`apps.inventory.policy.eligible_items` or
        :func:`~apps.inventory.policy.owned_items`. This method only narrows; it
        never widens, and it never makes an eligibility decision of its own.
        """
        if self.q:
            qs = qs.filter(
                Q(product__commercial_name__icontains=self.q)
                | Q(product__stone_type__icontains=self.q)
                | Q(product__primary_color__icontains=self.q)
                | Q(product__quarry_region__icontains=self.q)
                | Q(lot_code__icontains=self.q)
                | Q(processing_type__icontains=self.q)
                | Q(grade__icontains=self.q)
            )
        if self.stone_type:
            qs = qs.filter(product__stone_type__icontains=self.stone_type)
        if self.color:
            qs = qs.filter(product__primary_color__icontains=self.color)
        if self.quarry_region:
            qs = qs.filter(product__quarry_region__icontains=self.quarry_region)
        if self.processing_type:
            qs = qs.filter(processing_type__icontains=self.processing_type)
        if self.grade:
            qs = qs.filter(grade__icontains=self.grade)
        if self.applications:
            qs = qs.filter(product__applications__code__in=self.applications).distinct()
        if self.thickness_min is not None:
            qs = qs.filter(thickness_mm__gte=self.thickness_min)
        if self.thickness_max is not None:
            qs = qs.filter(thickness_mm__lte=self.thickness_max)

        qs = self._apply_stock(qs)
        qs = self._apply_price(qs, audience=audience)
        return self._apply_sort(qs, audience=audience)

    def _apply_stock(self, qs: QuerySet[InventoryLot]) -> QuerySet[InventoryLot]:
        if self.stock_mode:
            qs = qs.filter(stock_mode=self.stock_mode)
        if self.min_qty_sqm is not None:
            # Unlimited items satisfy any quantity; inquiry items have no number
            # to compare, so asking for a minimum excludes them.
            qs = qs.filter(
                Q(stock_mode=InventoryLot.StockMode.UNLIMITED)
                | Q(stock_mode=InventoryLot.StockMode.EXACT, available_sqm__gte=self.min_qty_sqm)
            )
        return qs

    def _apply_price(self, qs: QuerySet[InventoryLot], *, audience: Audience) -> QuerySet[InventoryLot]:
        tier = _FILTER_TIER.get(audience, "b2c")
        if self.price_min is None and self.price_max is None and not self.only_special:
            return qs

        price_q = Q(prices__tier__code=tier, prices__tier__is_active=True)
        if self.price_min is not None:
            price_q &= Q(prices__amount__gte=self.price_min)
        if self.price_max is not None:
            price_q &= Q(prices__amount__lte=self.price_max)
        if self.only_special:
            price_q &= Q(prices__special_amount__isnull=False) & (
                Q(prices__special_until__isnull=True) | Q(prices__special_until__gt=timezone.now())
            )
        return qs.filter(price_q).distinct()

    def _apply_sort(self, qs: QuerySet[InventoryLot], *, audience: Audience) -> QuerySet[InventoryLot]:
        if self.sort == "confirmed":
            return qs.order_by("-stock_confirmed_at", "-updated_at")
        if self.sort in ("price_asc", "price_desc"):
            tier = _FILTER_TIER.get(audience, "b2c")
            # Filtering the join to one tier keeps the other audience's numbers
            # out of the ORDER BY, which would otherwise silently order a public
            # page by B2B price.
            direction = "" if self.sort == "price_asc" else "-"
            return (
                qs.filter(prices__tier__code=tier, prices__tier__is_active=True)
                .order_by(f"{direction}prices__amount")
                .distinct()
            )
        return qs
