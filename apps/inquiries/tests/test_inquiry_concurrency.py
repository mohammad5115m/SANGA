"""Two identical public submissions arriving at once.

Runs only on the PostgreSQL lane. A double-tapped submit button on a slow mobile
connection is the realistic version of this, and SQLite cannot demonstrate it:
it serializes writers behind one database lock, so the second attempt always
arrives after the first has finished and the partial unique index is never
tested under contention.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest
from django.db import connection

from apps.core.testing import make_business, make_item, make_product
from apps.inquiries.models import Inquiry, InquiryItem
from apps.inquiries.services import submit_public_inquiry
from apps.pricing.services import ensure_default_tiers

pytestmark = [pytest.mark.concurrency, pytest.mark.django_db(transaction=True)]

TOKEN = "9a1c7d20-0000-4000-8000-00000000dead"


def test_a_double_submitted_form_records_one_inquiry_per_seller():
    ensure_default_tiers()
    seller = make_business(name="سنگ فروشنده همزمان", owner_phone="09381110001")
    other = make_business(name="سنگ دیگر همزمان", owner_phone="09381110002")
    first = make_item(
        seller,
        product=make_product(seller, commercial_name="تراورتن همزمان"),
        lot_code="IC-1",
        b2c="100",
    )
    second = make_item(
        other,
        product=make_product(other, commercial_name="گرانیت همزمان"),
        lot_code="IC-2",
        b2c="200",
    )
    groups = [
        {"business": seller, "rows": [{"item": first, "quantity": Decimal("10")}]},
        {"business": other, "rows": [{"item": second, "quantity": Decimal("20")}]},
    ]

    barrier = threading.Barrier(2, timeout=15)
    errors: list = []
    lock = threading.Lock()

    def submit() -> None:
        try:
            barrier.wait()
            submit_public_inquiry(
                submission_id=TOKEN,
                groups=groups,
                name="مشتری",
                phone="09121119900",
                verified=True,
            )
        except Exception as exc:  # noqa: BLE001 - the test inspects what escaped
            with lock:
                errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=submit) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == [], errors
    assert Inquiry.objects.count() == 2, "one inquiry per seller, not per submit"
    assert Inquiry.objects.filter(business=seller).count() == 1
    assert Inquiry.objects.filter(business=other).count() == 1
    # And the losing thread must not have doubled the lines onto the winner.
    assert InquiryItem.objects.count() == 2
