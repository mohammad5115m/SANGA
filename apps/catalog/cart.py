"""The public customer's product selection, held in the session.

Not a shopping cart: nothing is bought here. It is the list of products a visitor
has picked out while browsing, so they can ask about all of them in one request
instead of submitting the same form three times.

Session-backed on purpose. Persisting it would mean identifying the visitor, and
the whole point of the public surface is that browsing costs nothing — identity
is asked for at submission, not at the door.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from apps.inventory.models import InventoryLot
from apps.inventory.policy import eligible_items

SESSION_KEY = "public_selection"
#: Which surface the visitor entered the inquiry flow from, so the seller can
#: still tell a product-page question from a search-results one now that both go
#: through the same pipeline.
SOURCE_KEY = "public_selection_source"
#: A message the entry point pre-filled — «استعلام موجودی» seeds this, so the
#: customer does not have to type the question they just clicked.
MESSAGE_SEED_KEY = "public_selection_message"
MAX_ITEMS = 20


def _raw(request, business) -> dict:
    value = request.session.get(SESSION_KEY)
    if not isinstance(value, dict) or value.get("business_id") != str(business.pk):
        return {}
    items = value.get("items")
    return items if isinstance(items, dict) else {}


def _save(request, business, data: dict) -> None:
    current = request.session.get(SESSION_KEY)
    if not isinstance(current, dict) or current.get("business_id") != str(business.pk):
        request.session.pop(SOURCE_KEY, None)
        request.session.pop(MESSAGE_SEED_KEY, None)
    request.session[SESSION_KEY] = {"business_id": str(business.pk), "items": data}
    request.session.modified = True


def selected_ids(request, business) -> list[str]:
    return list(_raw(request, business).keys())


def count(request, business) -> int:
    return len(_raw(request, business))


def contains(request, business, item_id) -> bool:
    return str(item_id) in _raw(request, business)


def add(request, business, item: InventoryLot, quantity=None) -> None:
    if item.business_id != business.pk:
        return
    data = _raw(request, business)
    if len(data) >= MAX_ITEMS and str(item.pk) not in data:
        return
    data[str(item.pk)] = str(quantity) if quantity not in (None, "") else ""
    _save(request, business, data)


def remove(request, business, item_id) -> None:
    data = _raw(request, business)
    data.pop(str(item_id), None)
    _save(request, business, data)


def toggle(request, business, item: InventoryLot) -> bool:
    """Add or remove. Returns whether the item is now selected."""
    if contains(request, business, item.pk):
        remove(request, business, item.pk)
        return False
    add(request, business, item)
    return contains(request, business, item.pk)


def set_quantity(request, business, item_id, quantity) -> None:
    data = _raw(request, business)
    key = str(item_id)
    if key in data:
        data[key] = str(quantity) if quantity not in (None, "") else ""
        _save(request, business, data)


def set_source(request, source: str) -> None:
    request.session[SOURCE_KEY] = source
    request.session.modified = True


def source(request) -> str:
    from apps.inquiries.models import Inquiry

    value = request.session.get(SOURCE_KEY)
    return value if value in Inquiry.Source.values else Inquiry.Source.STOREFRONT


def set_message_seed(request, message: str) -> None:
    request.session[MESSAGE_SEED_KEY] = message
    request.session.modified = True


def message_seed(request) -> str:
    return request.session.get(MESSAGE_SEED_KEY) or ""


def clear(request) -> None:
    for key in (SESSION_KEY, SOURCE_KEY, MESSAGE_SEED_KEY):
        request.session.pop(key, None)
    request.session.modified = True


def _decimal(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def resolve(request, business) -> list[dict]:
    """The selection as live, still-eligible products.

    Re-resolved through the public eligibility gate on every read, so a product
    withdrawn while the visitor was browsing simply drops out of their selection
    rather than reaching the seller as a request for something unavailable.
    """
    data = _raw(request, business)
    if not data:
        return []

    items = {
        str(item.pk): item
        for item in eligible_items(audience="public", seller_business=business).filter(
            pk__in=list(data.keys())
        )
    }
    # Prune anything that has become ineligible, so the badge count matches what
    # the review page shows.
    if len(items) != len(data):
        _save(request, business, {key: value for key, value in data.items() if key in items})

    return [
        {"item": items[key], "quantity": _decimal(value)}
        for key, value in _raw(request, business).items()
        if key in items
    ]


def group_by_seller(rows: list[dict]) -> list[dict]:
    """Split a selection by seller.

    A customer browsing across the whole platform can pick products from several
    businesses. Each seller gets their own inquiry containing only their own
    products — one seller must never see what a customer asked another.
    """
    grouped: dict = {}
    for row in rows:
        business = row["item"].business
        bucket = grouped.setdefault(business.id, {"business": business, "rows": []})
        bucket["rows"].append(row)
    return list(grouped.values())
