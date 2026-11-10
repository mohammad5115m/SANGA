from __future__ import annotations

from django.db.models import F, Prefetch

from apps.businesses.eligibility import business_can_sell, can_sell_q
from apps.inventory.policy import eligible_items
from apps.pricing.models import LotPrice

from .models import ResaleOffer, ResaleRule


def available_rules():
    return ResaleRule.objects.filter(lot_id__in=eligible_items(audience="public").values("pk"))


def retailer_rules(retailer):
    rules = available_rules()
    if not business_can_sell(retailer):
        return rules.none()
    return rules.filter(
        lot__business__wholesale_program__grants__retailer=retailer,
        lot__business__wholesale_program__grants__is_active=True,
    ).exclude(lot__business=retailer)


def resale_prices_prefetch(path):
    return Prefetch(
        path,
        queryset=LotPrice.objects.select_related("tier").filter(tier__code__in=("b2b", "b2c")),
        to_attr="resale_prices",
    )


def offer_queryset():
    return (
        ResaleOffer.objects.filter(
            can_sell_q("grant__retailer"),
            is_active=True,
            grant__is_active=True,
            rule_id__in=available_rules().values("pk"),
            rule__lot__business_id=F("grant__program__warehouse_id"),
        )
        .exclude(grant__retailer_id=F("grant__program__warehouse_id"))
        .select_related("rule__lot__product__stone", "grant__retailer", "grant__program__warehouse")
        .prefetch_related(resale_prices_prefetch("rule__lot__prices"))
    )
