"""No services.

The demand board this app implemented — free-form purchase requests posted to
the network, answered with private seller offers — was removed in V2. Buyers now
request a **specific existing product** through :mod:`apps.trading`.

The models stay because their rows are history and because
``accounting.LedgerEntry.related_offer`` still points at ``PurchaseOffer``. They
are read-only: nothing creates, edits or decides one any more, and there are no
routes to them outside Django admin.
"""

from __future__ import annotations
