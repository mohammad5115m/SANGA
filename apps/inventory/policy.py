"""The single definition of who may see which inventory item.

Before this module existed the same question was answered independently by the
marketplace selector, the storefront selector, and an inline loop in the shared
catalog view. They drifted, and the drift was a live data leak: the catalog path
checked ``status`` but forgot ``visibility``, so a private item attached to a
share link rendered publicly.

Every buyer-facing surface must go through :func:`eligible_items`. Adding a new
lifecycle rule here fixes the marketplace, seller storefronts, share links and catalogs at
once, which is the entire point.
"""

from __future__ import annotations

from typing import Literal

from django.db.models import Prefetch, QuerySet

from apps.businesses.eligibility import (
    business_can_sell,
    business_is_network_eligible,
    can_sell_q,
)
from apps.businesses.models import Business
from apps.pricing.models import LotPrice

from .models import InventoryLot

# Who is asking. Kept separate from the pricing audience codes, which answer the
# narrower question of which price tier may be rendered.
Audience = Literal["owner", "colleague", "public"]

#: Price tier each audience is allowed to have loaded at all. Restricting the
#: prefetch means a B2B row is never even fetched on a public page, so a
#: template bug cannot leak what was never in memory.
_AUDIENCE_TIER: dict[Audience, tuple[str, ...]] = {
    "owner": ("b2b", "b2c"),
    "colleague": ("b2b",),
    "public": ("b2c",),
}


def _price_prefetch(audience: Audience) -> Prefetch:
    tiers = _AUDIENCE_TIER[audience]
    return Prefetch(
        "prices",
        queryset=LotPrice.objects.select_related("tier").filter(tier__code__in=tiers, tier__is_active=True),
    )


def owned_items(business: Business) -> QuerySet[InventoryLot]:
    """Everything the owning business may manage.

    Deliberately wider than :func:`eligible_items`: a seller must still be able
    to find, edit, re-publish and delete an item that is hidden, unavailable or
    stale. Only deleted items disappear.
    """
    return (
        InventoryLot.objects.filter(business=business, deleted_at__isnull=True)
        .select_related("product", "product__stone", "business")
        .prefetch_related(_price_prefetch("owner"), "media")
        .order_by("-updated_at")
    )


def eligible_items(
    *,
    audience: Audience,
    viewer_business: Business | None = None,
    seller_business: Business | None = None,
) -> QuerySet[InventoryLot]:
    """Items a buyer of ``audience`` is allowed to discover.

    An item is eligible when it is not deleted, is currently offered for sale,
    has been published by its seller, and its seller may currently sell — which
    means active, subscription current, not refused by platform verification,
    and on a plan that includes selling.

    That last clause used to be missing, and read eligibility and write
    entitlement disagreed as a result: a seller could downgrade to browse-only or
    let their subscription lapse and their published products stayed
    discoverable, right up until ``create_purchase_request`` re-checked the plan
    and refused. The buyer's journey ended in an error page for a product the
    platform had gone on advertising.

    Note what is *not* here: stock and price freshness. An item whose quantity
    has gone stale stays discoverable and simply shows «استعلام موجودی» — that
    is the difference between «ناموجود» and an expired confirmation, and
    collapsing the two would hide inventory sellers still want to sell.
    """
    if audience == "colleague":
        # A viewer who cannot participate in the network sees nothing, and a
        # seller who cannot sell is shown to nobody. Both directions matter: the
        # second keeps such a business's B2B prices off other people's screens.
        if not business_is_network_eligible(viewer_business):
            return InventoryLot.objects.none()

    if seller_business is not None and not business_can_sell(seller_business):
        return InventoryLot.objects.none()

    qs = InventoryLot.objects.filter(
        can_sell_q("business"),
        product__is_active=True,
        deleted_at__isnull=True,
        is_visible=True,
        availability_status=InventoryLot.Availability.AVAILABLE,
        status=InventoryLot.Status.ACTIVE,
    )

    if seller_business is not None:
        qs = qs.filter(business=seller_business)

    if audience == "colleague":
        # Own stock belongs in «موجودی من», not in the colleague marketplace.
        qs = qs.exclude(business=viewer_business)

    return (
        qs.select_related("product", "product__stone", "business")
        .prefetch_related(_price_prefetch(audience), "media", "product__applications")
        .order_by("-is_urgent_sale", "-stock_confirmed_at", "-updated_at")
    )


def get_eligible_item(
    *,
    audience: Audience,
    viewer_business: Business | None = None,
    seller_business: Business | None = None,
    item_id=None,
    public_token: str | None = None,
) -> InventoryLot | None:
    """Fetch one item through the same gate as the listing pages.

    Detail views and share links historically had their own lookups, which is
    how they ended up more permissive than the lists they were reached from.
    """
    qs = eligible_items(
        audience=audience,
        viewer_business=viewer_business,
        seller_business=seller_business,
    )
    if public_token:
        return qs.filter(public_token=public_token).first()
    if item_id is None:
        return None
    return qs.filter(pk=item_id).first()
