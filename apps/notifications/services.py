from __future__ import annotations

from django.db import transaction

from .models import Notification


@transaction.atomic
def notify_user(
    *,
    user,
    title: str,
    body: str = "",
    link: str = "",
    kind: str = Notification.Kind.GENERAL,
    business=None,
) -> Notification:
    return Notification.objects.create(
        user=user,
        business=business,
        kind=kind,
        title=title,
        body=body,
        link=link,
    )
