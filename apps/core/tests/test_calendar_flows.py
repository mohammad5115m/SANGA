import builtins
from datetime import date

import pytest
from django.urls import reverse

from apps.accounting.models import LedgerEntry
from apps.accounting.services import post_manual_entry
from apps.core.testing import make_business, owner_membership
from apps.invoicing.forms import InvoiceLineFormSet
from apps.invoicing.rendering import DocumentRenderError, render_pdf


def test_invalid_formset_preserves_order_and_keeps_empty_rows_last():
    formset = InvoiceLineFormSet({
        "lines-TOTAL_FORMS": "3", "lines-INITIAL_FORMS": "0",
        "lines-0-product_name": "first", "lines-0-ORDER": "1",
        "lines-1-product_name": "second", "lines-1-ORDER": "0",
        "lines-2-ORDER": "",
    }, prefix="lines")
    assert not formset.is_valid()  # Missing quantities/prices must retain the user's work.
    assert [form.prefix for form in formset] == ["lines-1", "lines-0", "lines-2"]
    assert all(form["ORDER"].is_hidden for form in formset)


@pytest.fixture
def ledger_client(client, db):
    business = make_business(name="فروشنده تاریخ", owner_phone="09995550111")
    colleague = make_business(name="همکار تاریخ", owner_phone="09995550112")
    membership = owner_membership(business)
    client.force_login(membership.user)
    session = client.session
    session["current_business_id"] = str(business.pk)
    session.save()
    for day in (19, 20, 21):
        post_manual_entry(
            business=business, counterparty=colleague, membership=membership,
            entry_type=LedgerEntry.Type.PAYMENT_RECEIVED, amount=100,
            occurred_on=date(2024, 3, day), description=f"day-{day}",
        )
    return client, colleague


def test_statement_screen_and_print_share_inclusive_jalali_range(ledger_client):
    client, colleague = ledger_client
    query = {"from_jalali": "۱۴۰۳/۰۱/۰۱", "to_jalali": "۱۴۰۳/۰۱/۰۱"}
    for route in ("accounting:statement", "accounting:print"):
        response = client.get(reverse(route, args=[colleague.pk]), query)
        assert response.status_code == 200
        assert [entry.occurred_on for entry in response.context["entries"]] == [date(2024, 3, 20)]
        assert "۱۴۰۳/۰۱/۰۱" in response.content.decode()


def test_invalid_print_range_returns_persian_field_feedback(ledger_client):
    client, colleague = ledger_client
    response = client.get(reverse("accounting:print", args=[colleague.pk]), {
        "from_jalali": "۱۴۰۴/۱۲/۳۰",
    }, HTTP_ACCEPT_LANGUAGE="en-US,en;q=0.9")
    assert response.status_code == 200
    assert not response.context["entries"]
    assert "یک تاریخ معتبر وارد کنید" in response.content.decode()


def test_missing_native_pdf_library_is_a_controlled_export_error(monkeypatch):
    original = builtins.__import__

    def unavailable(name, *args, **kwargs):
        if name == "weasyprint":
            raise OSError("native library unavailable")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", unavailable)
    with pytest.raises(DocumentRenderError):
        render_pdf(None)
