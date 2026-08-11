from __future__ import annotations

import logging

from celery import shared_task

from .services import expire_due_reservations

logger = logging.getLogger(__name__)


@shared_task
def expire_reservations() -> dict[str, int]:
    """Release quantities held by reservations whose hold has elapsed."""
    result = expire_due_reservations()
    logger.info("expire_reservations result=%s", result)
    return result
