"""No models.

``SavedSearch`` lived here and was dropped in V2 along with the half-hourly
Celery job that re-ran every stored filter and notified on matches. It was the
only remaining consumer of the periodic-task machinery, and the notifications it
produced were guesses about intent rather than something a seller had asked for.

Search itself is unaffected: the shared ``apps.inventory.filters.ItemFilterSpec``
serializes to a dict, so re-introducing saved searches later would mean storing
that spec, not rebuilding a filter language.
"""

from __future__ import annotations
