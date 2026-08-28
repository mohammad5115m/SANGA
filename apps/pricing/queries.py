"""The current price, expressed as SQL.

``LotPrice.effective_amount()`` decides what a card shows: an expired fixed price
reads «استعلام قیمت» rather than a figure nobody has stood behind for a fortnight,
and a live special sale beats the standard amount. The filters did not know that.
They compared the stored ``amount``, so a price range could return items whose own
card refused to quote a price, and price sorting ordered by numbers the viewer was
never shown.

These expressions are the query half of that definition. The Python half lives on
:class:`~apps.pricing.models.LotPrice`, and the two must keep matching.
"""

from __future__ import annotations

from django.db.models import Case, DecimalField, F, OuterRef, Q, Subquery, When
from django.utils import timezone

from .models import LotPrice

#: Wide enough for any stored amount; the annotation is only ever compared and
#: ordered, never summed.
AMOUNT_FIELD = DecimalField(max_digits=16, decimal_places=2)


def special_is_live_q(prefix: str = "") -> Q:
    field = f"{prefix}__" if prefix else ""
    return Q(**{f"{field}special_amount__isnull": False}) & Q(
        **{f"{field}special_until__gt": timezone.now()}
    )


def price_is_fresh_q(prefix: str = "") -> Q:
    field = f"{prefix}__" if prefix else ""
    return Q(**{f"{field}mode": LotPrice.Mode.FIXED}) & Q(
        **{f"{field}price_expires_at__isnull": False}
    ) & Q(**{f"{field}price_expires_at__gt": timezone.now()})


def _effective_amount_case():
    """``special_amount`` if the sale is live, else ``amount`` if still fresh."""
    return Case(
        When(
            Q(mode=LotPrice.Mode.INQUIRY),
            then=None,
        ),
        When(special_is_live_q(), then=F("special_amount")),
        When(price_is_fresh_q(), then=F("amount")),
        default=None,
        output_field=AMOUNT_FIELD,
    )


def effective_amount_subquery(tier_code: str) -> Subquery:
    """The item's current price for one tier, or NULL when it must be asked for.

    A subquery rather than a join, so a price filter cannot multiply rows and a
    ``.distinct()`` is not needed to undo the damage. Scoped to a single tier
    because the audience decides which number may be filtered on at all — joining
    every tier would let a public page order itself by B2B prices.
    """
    return Subquery(
        LotPrice.objects.filter(
            lot=OuterRef("pk"),
            tier__code=tier_code,
            tier__is_active=True,
        )
        .annotate(effective=_effective_amount_case())
        .values("effective")[:1],
        output_field=AMOUNT_FIELD,
    )


def live_special_subquery(tier_code: str) -> Subquery:
    """Whether this item has a live special sale on one tier."""
    return Subquery(
        LotPrice.objects.filter(
            Q(lot=OuterRef("pk"), tier__code=tier_code, tier__is_active=True) & special_is_live_q()
        )
        .exclude(mode=LotPrice.Mode.INQUIRY)
        .values("pk")[:1]
    )
