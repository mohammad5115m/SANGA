from __future__ import annotations

import logging

from django.db import transaction

from .models import Notification

logger = logging.getLogger(__name__)


def members_who_can(business, capability: str) -> list:
    """Active members of ``business`` holding ``capability``.

    Notifications used to go to whoever held the OWNER or MANAGER *role*. SANGA
    authorizes by capability, not by role, and the default ``staff`` role holds
    ``trade.confirm``, ``trade.propose`` and ``leads.manage`` — so the
    salesperson whose job it is to answer a customer inquiry was the one person
    guaranteed never to hear about one. A permission set edited to grant a
    capability granted the work without the notification, which is the same bug
    from the other direction.

    Filtered in Python rather than in SQL on purpose. ``has_capability`` combines
    an owner bypass with membership of a JSON list, and JSON containment lookups
    do not work on SQLite, so expressing it as a query would mean two
    implementations of one rule that drift. Memberships are bounded by the plan's
    seat limit, so this is a handful of rows behind one indexed query.
    """
    from apps.businesses.models import BusinessMembership

    memberships = BusinessMembership.objects.filter(
        business=business,
        status=BusinessMembership.Status.ACTIVE,
    ).select_related("user")
    return [membership for membership in memberships if membership.has_capability(capability)]


def notify_business(business, *, capability: str, title: str, body: str = "", link: str = "") -> int:
    """Tell the people who can actually act on this. Returns how many were told.

    Naming the capability at each call site is the point: it forces whoever adds
    a notification to answer "who is supposed to do something about this?" rather
    than defaulting to everybody, which is how notification lists stop being read.
    """
    recipients = members_who_can(business, capability)
    for membership in recipients:
        notify_user(
            user=membership.user,
            business=business,
            kind=Notification.Kind.GENERAL,
            title=title,
            body=body,
            link=link,
        )
    if not recipients:
        # Not an error — a one-person business that has had a capability removed
        # is a legitimate state — but it means work is arriving that nobody is
        # being told about, which is worth being able to find later.
        logger.info(
            "No member of business %s holds %s; notification not delivered", business.id, capability
        )
    return len(recipients)


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
