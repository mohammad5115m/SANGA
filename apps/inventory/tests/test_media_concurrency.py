"""Two people making a photo the cover at the same moment.

Runs only on the PostgreSQL lane. Both services demote the current primary and
then promote their own; run together, both did that and both won, leaving an
item with two covers and a card that showed whichever the ordering happened to
return first. SQLite cannot demonstrate it — it serializes writers behind one
database lock, so the second attempt always arrives after the first has finished.
"""

from __future__ import annotations

import threading

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection

from apps.core.testing import make_business, make_item, owner_membership
from apps.inventory.models import LotMedia
from apps.inventory.services import add_lot_media, set_primary_media
from apps.pricing.services import ensure_default_tiers

from .test_media import PNG

pytestmark = [pytest.mark.concurrency, pytest.mark.django_db(transaction=True)]


def _race(targets: list) -> list:
    barrier = threading.Barrier(len(targets), timeout=15)
    errors: list = []
    lock = threading.Lock()

    def runner(target) -> None:
        try:
            barrier.wait()
            target()
        except Exception as exc:  # noqa: BLE001 - the test inspects what escaped
            with lock:
                errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=runner, args=(target,)) for target in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return errors


def _world():
    ensure_default_tiers()
    business = make_business(name="سنگ رسانه همزمان", owner_phone="09421110001")
    return {
        "business": business,
        "membership": owner_membership(business),
        "item": make_item(business, lot_code="MC-1"),
    }


def _upload(world, name: str) -> LotMedia:
    return add_lot_media(
        lot=world["item"],
        membership=world["membership"],
        upload=SimpleUploadedFile(name, PNG, content_type="image/png"),
    )


def test_two_threads_setting_the_cover_leave_exactly_one():
    world = _world()
    first = _upload(world, "a.png")
    second = _upload(world, "b.png")
    third = _upload(world, "c.png")
    assert first.is_primary is True

    errors = _race(
        [
            lambda: set_primary_media(lot=world["item"], membership=world["membership"], media_id=second.id),
            lambda: set_primary_media(lot=world["item"], membership=world["membership"], media_id=third.id),
        ]
    )

    assert errors == [], errors
    assert LotMedia.objects.filter(lot=world["item"], is_primary=True).count() == 1


def test_two_simultaneous_first_uploads_do_not_both_become_the_cover():
    """The other half of the race: nothing is primary yet, so both uploads
    conclude they are the first."""
    world = _world()

    errors = _race(
        [
            lambda: _upload(world, "one.png"),
            lambda: _upload(world, "two.png"),
        ]
    )

    assert errors == [], errors
    assert LotMedia.objects.filter(lot=world["item"]).count() == 2
    assert LotMedia.objects.filter(lot=world["item"], is_primary=True).count() == 1
