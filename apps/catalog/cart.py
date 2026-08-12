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
MAX_ITEMS = 20


def _raw(request) -> dict:
    value = request.session.get(SESSION_KEY)
    return value if isinstance(value, dict) else {}


def _save(request, data: dict) -> None:
    request.session[SESSION_KEY] = data
    request.session.modified = True


def selected_ids(request) -> list[str]:
    return list(_raw(request).keys())


def count(request) -> int:
    return len(_raw(request))


def contains(request, item_id) -> bool:
    return str(item_id) in _raw(request)


def add(request, item: InventoryLot, quantity=None) -> None:
    data = _raw(request)
    if len(data) >= MAX_ITEMS and str(item.pk) not in data:
        return
    data[str(item.pk)] = str(quantity) if quantity not in (None, "") else ""
    _save(request, data)


def remove(request, item_id) -> None:
    data = _raw(request)
    data.pop(str(item_id), None)
    _save(request, data)


def toggle(request, item: InventoryLot) -> bool:
    """Add or remove. Returns whether the item is now selected."""
    if contains(request, item.pk):
        remove(request, item.pk)
        return False
    add(request, item)
    return contains(request, item.pk)


def set_quantity(request, item_id, quantity) -> None:
    data = _raw(request)
    key = str(item_id)
    if key in data:
        data[key] = str(quantity) if quantity not in (None, "") else ""
        _save(request, data)


def clear(request) -> None:
    request.session.pop(SESSION_KEY, None)
    request.session.modified = True


def _decimal(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def resolve(request) -> list[dict]:
    """The selection as live, still-eligible products.

    Re-resolved through the public eligibility gate on every read, so a product
    withdrawn while the visitor was browsing simply drops out of their selection
    rather than reaching the seller as a request for something unavailable.
    """
    data = _raw(request)
    if not data:
        return []

    items = {
        str(item.pk): item
        for item in eligible_items(audience="public").filter(pk__in=list(data.keys()))
    }
    # Prune anything that has become ineligible, so the badge count matches what
    # the review page shows.
    if len(items) != len(data):
        _save(request, {key: value for key, value in data.items() if key in items})

    return [
        {"item": items[key], "quantity": _decimal(value)}
        for key, value in _raw(request).items()
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
