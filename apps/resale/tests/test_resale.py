from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from apps.accounting.models import LedgerEntry
from apps.businesses.models import Business
from apps.core.testing import make_business, make_item, make_product, owner_membership
from apps.inquiries.models import Inquiry
from apps.invoicing.models import SalesInvoice
from apps.invoicing.partner_services import confirm_partner_invoice, finalize_customer_invoice, send_partner_invoice
from apps.invoicing.services import InvoiceError
from apps.invoicing.tests.test_invoice_first_lifecycle import install_signatures
from apps.pricing.services import confirm_lot_price, set_lot_price, set_lot_prices
from apps.resale.models import ResaleRequest, WholesaleProgram
from apps.resale.orders import make_retail_invoice, make_wholesale_invoice, request_purchase, submit_request
from apps.resale.pricing import ResaleError, current_terms, offer_price
from apps.resale.services import configure_lot, enable_program, set_grant, set_offer
from apps.trading.models import Trade

pytestmark = pytest.mark.django_db


@pytest.fixture
def pair():
    warehouse = make_business(name="انبار مبدأ", owner_phone="09120009571")
    retailer = make_business(name="فروشندهٔ منتخب", owner_phone="09120009572")
    other = make_business(name="فروشندهٔ دیگر", owner_phone="09120009573")
    lot = make_item(warehouse, product=make_product(warehouse, commercial_name="تراورتن نمونه آزمایش"),
                    b2b="800000", b2c="1000000")
    return warehouse, retailer, other, lot


def specs(wholesale="800000", retail="1000000", w_special=None, r_special=None, end=None):
    return {
        "b2b": {"amount": wholesale, "valid_for_days": 7, "special_amount": w_special,
                "special_until": end if w_special else None},
        "b2c": {"amount": retail, "valid_for_days": 7, "special_amount": r_special,
                "special_until": end if r_special else None},
    }


@pytest.fixture
def program(pair):
    warehouse, retailer, _, lot = pair
    member = owner_membership(warehouse)
    enable_program(warehouse=warehouse, membership=member)
    rule = configure_lot(lot=lot, membership=member, margin_kind="fixed",
                         margin_value="80000", specs=specs())
    set_grant(warehouse=warehouse, membership=member, retailer=retailer)
    return rule


@pytest.fixture
def offer(pair, program):
    retailer = pair[1]
    return set_offer(rule=program, retailer=retailer, membership=owner_membership(retailer), amount="1100000")


def test_opt_in_refuses_existing_retail_only_promotion_without_changes(pair):
    warehouse, _, _, lot = pair
    end = timezone.now() + timedelta(days=1)
    set_lot_price(lot=lot, tier_code="b2c", amount="1000000", special_amount="900000", special_until=end)
    with pytest.raises(ResaleError, match="فقط تخفیف مصرف‌کننده"):
        enable_program(warehouse=warehouse, membership=owner_membership(warehouse))
    assert not WholesaleProgram.objects.filter(warehouse=warehouse).exists()
    assert lot.prices.get(tier__code="b2c").special_amount == Decimal("900000")


@pytest.mark.concurrency
@pytest.mark.django_db(transaction=True)
def test_opt_in_and_retail_promotion_serialize(pair):
    warehouse, _, _, lot = pair
    member = owner_membership(warehouse)
    start = Barrier(2, timeout=10)

    def attempt(action):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '5s'")
            start.wait()
            action()
        except ResaleError:
            return False
        finally:
            connection.close()
        return True

    def activate():
        enable_program(warehouse=warehouse, membership=member)

    def discount():
        set_lot_price(lot=lot, tier_code="b2c", amount="1000000", special_amount="900000",
                      special_until=timezone.now() + timedelta(days=1))

    with ThreadPoolExecutor(max_workers=2) as pool:
        activated, discounted = pool.submit(attempt, activate), pool.submit(attempt, discount)
        assert activated.result(timeout=20) != discounted.result(timeout=20)
    assert WholesaleProgram.objects.filter(warehouse=warehouse).exists() != lot.prices.filter(
        tier__code="b2c", special_amount__isnull=False,
    ).exists()


@pytest.mark.parametrize(("mode", "value", "cap"), [("fixed", "80000", "920000"), ("percent", "10", "909090")])
def test_margin_boundary_is_enforced_and_invalid_batch_rolls_back(pair, program, mode, value, cap):
    warehouse, _, _, lot = pair
    member = owner_membership(warehouse)
    rule = configure_lot(lot=lot, membership=member, margin_kind=mode, margin_value=value, specs=specs(cap))
    with pytest.raises(ResaleError, match="حداکثر"):
        configure_lot(lot=lot, membership=member, margin_kind=mode, margin_value=value,
                      specs=specs(str(int(cap) + 1)))
    rule.refresh_from_db()
    assert rule.margin_value == Decimal(value)
    assert current_terms(rule).wholesale == Decimal(cap)
    confirm_lot_price(lot=lot, tier_code="b2b", membership=member)
    assert len({p.price_expires_at for p in lot.prices.all()}) == 1


def test_promotion_modes_validate_windows_and_margin(pair, program):
    lot = pair[3]
    end = timezone.now() + timedelta(days=1)
    with pytest.raises(ResaleError, match="فقط تخفیف مصرف‌کننده"):
        set_lot_prices(lot=lot, specs=specs(r_special="780000", end=end))
    assert lot.prices.filter(special_amount__isnull=False).count() == 0
    set_lot_prices(lot=lot, specs=specs(w_special="700000", end=end))
    assert current_terms(program).floor == Decimal("1000000")
    different_ends = specs(w_special="700000", r_special="780000", end=end)
    different_ends["b2c"]["special_until"] += timedelta(hours=1)
    with pytest.raises(ResaleError, match="هم‌زمان"):
        set_lot_prices(lot=lot, specs=different_ends)
    with pytest.raises(ResaleError, match="حداکثر"):
        set_lot_prices(lot=lot, specs=specs(w_special="710000", r_special="780000", end=end))
    set_lot_prices(lot=lot, specs=specs(w_special="700000", r_special="780000", end=end))
    assert current_terms(program).floor == Decimal("780000")
    assert current_terms(program).wholesale == Decimal("700000")


def test_warehouse_policy_applies_to_other_lots_too(pair, program):
    warehouse = pair[0]
    lot = make_item(warehouse, b2b="800000", b2c="1000000")
    with pytest.raises(ResaleError, match="فقط تخفیف مصرف‌کننده"):
        set_lot_price(lot=lot, tier_code="b2c", amount="1000000", special_amount="900000",
                      special_until=timezone.now() + timedelta(days=1))


def test_retailer_can_match_floor_without_sale_and_specials_expire(pair, program):
    _, retailer, _, lot = pair
    member = owner_membership(retailer)
    with pytest.raises(ResaleError, match="کمتر"):
        set_offer(rule=program, retailer=retailer, membership=member, amount="999999")
    match = set_offer(rule=program, retailer=retailer, membership=member, amount="1000000")
    assert offer_price(match, current_terms(program)).amount == Decimal("1000000")
    end = timezone.now() + timedelta(days=1)
    with pytest.raises(ResaleError, match="تخفیف همکاری"):
        set_offer(rule=program, retailer=retailer, membership=member, amount="1100000",
                  special_amount="1000000", special_until=end)
    set_lot_prices(lot=lot, specs=specs(w_special="700000", r_special="780000", end=end))
    special = set_offer(rule=program, retailer=retailer, membership=member, amount="1100000",
                        special_amount="780000", special_until=end)
    assert offer_price(special, current_terms(program)).special
    after = end + timedelta(seconds=1)
    assert offer_price(special, current_terms(program, at=after), at=after).amount == Decimal("1100000")
    low = set_offer(rule=program, retailer=retailer, membership=member, amount="780000")
    assert offer_price(low, current_terms(program, at=after), at=after).amount is None


def test_private_prices_are_only_in_authorized_workspace(client, pair, program, offer):
    warehouse, retailer, other, _ = pair
    endpoint = reverse("resale:public_store", args=[retailer.storefront_token])
    public = client.get(endpoint)
    assert public.status_code == 200
    assert "1,100,000" in public.content.decode()
    for secret in ("800,000", "1,000,000", warehouse.name):
        assert secret not in public.content.decode()
    assert set(public.context["cards"][0]) == {"id", "name", "stone", "amount", "is_special", "image_url", "stock"}

    client.force_login(owner_membership(other).user)
    assert "800,000" not in client.get(reverse("resale:index")).content.decode()
    assert client.get(reverse("resale:offer_edit", args=[program.pk])).status_code == 404
    assert client.get(reverse("resale:ask", args=[other.storefront_token, offer.pk])).status_code == 404

    client.force_login(owner_membership(retailer).user)
    private = client.get(reverse("resale:index")).content.decode()
    for visible in ("800,000", "1,000,000", "1,100,000"):
        assert visible in private
    Business.objects.filter(pk=retailer.pk).update(active_until=timezone.localdate() - timedelta(days=1))
    expired = client.get(reverse("resale:index"))
    assert expired.status_code == 200 and not expired.context["rows"]
    assert client.get(reverse("resale:offer_edit", args=[program.pk])).status_code == 404


@pytest.mark.parametrize("state", ["revoked", "withdrawn", "expired", "browse"])
def test_ineligible_offer_disappears_from_public_routes(client, pair, offer, state):
    warehouse, retailer, _, lot = pair
    if state == "revoked":
        set_grant(warehouse=warehouse, retailer=retailer, membership=owner_membership(warehouse), active=False)
    elif state == "withdrawn":
        lot.is_visible = False
        lot.save(update_fields=["is_visible"])
    elif state == "expired":
        Business.objects.filter(pk=warehouse.pk).update(active_until=timezone.localdate() - timedelta(days=1))
    else:
        Business.objects.filter(pk=warehouse.pk).update(plan=Business.Plan.BROWSE)
    assert not client.get(reverse("resale:public_store", args=[retailer.storefront_token])).context["cards"]
    assert client.get(reverse("resale:ask", args=[retailer.storefront_token, offer.pk])).status_code == 404


def test_http_request_saved_only_for_retailer_and_purchase_only_shared_on_request(client, pair, offer, settings):
    settings.CUSTOMER_OTP_ENABLED = False
    warehouse, retailer, _, _ = pair
    path = reverse("resale:ask", args=[retailer.storefront_token, offer.pk])
    data = {"name": "مشتری خصوصی نمونه", "phone": "09120009574", "quantity": "۲٫۵", "submission_id": str(uuid4())}
    assert client.get(path).status_code == 200
    assert client.post(path, data).status_code == 302
    assert client.post(path, data).status_code == 302
    order = ResaleRequest.objects.get()
    assert order.inquiry.business_id == retailer.pk
    assert order.quantity == Decimal("2.5")
    assert order.inquiry.items.get().item_id is None
    assert not Inquiry.objects.filter(business=warehouse).exists()
    assert order.requested_at is None
    client.force_login(owner_membership(retailer).user)
    review = reverse("resale:purchase", args=[order.pk])
    assert client.get(review).status_code == 200
    assert client.post(review, {"wholesale": "800000", "retail": "1100000"}).status_code == 302
    client.force_login(owner_membership(warehouse).user)
    queue = client.get(reverse("resale:warehouse_requests")).content.decode()
    assert "فروشندهٔ منتخب" in queue
    assert "مشتری خصوصی نمونه" not in queue and "09120009574" not in queue
    assert "1,100,000" not in queue


def test_public_request_with_otp_waits_for_verification(client, pair, offer, settings, monkeypatch):
    settings.CUSTOMER_OTP_ENABLED = True
    retailer = pair[1]
    verified_phones = []
    monkeypatch.setattr("apps.resale.public.request_customer_otp", lambda *a, **kw: SimpleNamespace(dev_code=None))
    monkeypatch.setattr("apps.resale.public.verify_customer_otp", lambda phone, code: verified_phones.append(phone))
    args = [retailer.storefront_token, offer.pk]
    ask, verify = (reverse(f"resale:{name}", args=args) for name in ("ask", "verify"))
    data = {"name": "مشتری نمونه", "phone": "09120009576", "quantity": "2", "submission_id": str(uuid4())}
    assert client.post(ask, data).url == verify
    assert not ResaleRequest.objects.exists()
    assert client.post(verify, {"code": "123456"}).status_code == 302
    assert verified_phones == ["09120009576"]
    assert ResaleRequest.objects.get().inquiry.business_id == retailer.pk


def test_separate_invoices_hold_rates_and_existing_accounting_rules(pair, offer, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    warehouse, retailer, _, lot = pair
    seller, buyer = owner_membership(warehouse), owner_membership(retailer)
    install_signatures(warehouse, seller)
    install_signatures(retailer, buyer)
    order = submit_request(offer_id=offer.pk, retailer=retailer, quantity="2.5", submission_id=uuid4(),
                           name="مشتری آزمایشی", phone="09120009575")
    with pytest.raises(ResaleError, match="قیمت تغییر"):
        request_purchase(order_id=order.pk, retailer=retailer, membership=buyer,
                         expected_wholesale=Decimal("700000"), expected_retail=Decimal("1100000"))
    order = request_purchase(order_id=order.pk, retailer=retailer, membership=buyer,
                             expected_wholesale=Decimal("800000"), expected_retail=Decimal("1100000"))
    assert not Trade.objects.exists() and not LedgerEntry.objects.exists()
    wholesale = make_wholesale_invoice(order_id=order.pk, warehouse=warehouse, membership=seller)
    assert wholesale.items.get().item_id == lot.pk and wholesale.total_amount == Decimal("2000000")
    with pytest.raises(ResaleError, match="ابتدا فاکتور خرید"):
        make_retail_invoice(order_id=order.pk, retailer=retailer, membership=buyer)
    wholesale = send_partner_invoice(invoice=wholesale, membership=seller)
    wholesale = confirm_partner_invoice(invoice=wholesale, membership=buyer, expected_revision=1)
    assert wholesale.status == SalesInvoice.Status.CONFIRMED
    assert LedgerEntry.objects.filter(related_invoice=wholesale).count() == 2
    retail = make_retail_invoice(order_id=order.pk, retailer=retailer, membership=buyer)
    assert retail.seller_business == retailer and retail.total_amount == Decimal("2750000")
    assert retail.items.get().item_id is None
    assert make_retail_invoice(order_id=order.pk, retailer=retailer, membership=buyer).pk == retail.pk
    retail.paid_amount = retail.total_amount
    retail.amount_due = 0
    retail.save(update_fields=["paid_amount", "amount_due"])
    retail.items.update(discount_amount=Decimal("1"))
    with pytest.raises(InvoiceError, match="تخفیف دوباره"):
        finalize_customer_invoice(invoice=retail, membership=buyer)
    assert Trade.objects.count() == 1
    retail.items.update(discount_amount=Decimal("0"))
    issued = finalize_customer_invoice(invoice=retail, membership=buyer)
    assert issued.status == SalesInvoice.Status.ISSUED
    assert finalize_customer_invoice(invoice=issued, membership=buyer).pk == issued.pk
    assert Trade.objects.count() == 2 and LedgerEntry.objects.count() == 2
    lot.refresh_from_db()
    assert lot.available_sqm == Decimal("100")
