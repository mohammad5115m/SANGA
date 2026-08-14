"""Public customer inquiries.

Three rules under test: a customer can ask about several products at once, the
request is saved on the server before any share button appears, and none of it
ever creates a platform User.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.accounts.models import OTPChallenge
from apps.core.testing import make_business, make_item, make_product
from apps.inquiries.models import CustomerLead, Inquiry, InquiryItem
from apps.inquiries.services import InquiryError, create_inquiry, get_or_create_lead
from apps.pricing.services import ensure_default_tiers

User = get_user_model()


@pytest.fixture
def market(db):
    ensure_default_tiers()
    seller = make_business(name="سنگ فروشنده", owner_phone="09211110001")
    other = make_business(name="سنگ دیگر", owner_phone="09211110002")
    first = make_item(
        seller,
        product=make_product(seller, commercial_name="تراورتن عباس‌آباد"),
        lot_code="A-1",
        b2c="2000000",
    )
    second = make_item(
        seller,
        product=make_product(seller, commercial_name="مرمریت لاشتر", stone_type="مرمریت"),
        lot_code="A-2",
        b2c="2500000",
    )
    third = make_item(
        other,
        product=make_product(other, commercial_name="گرانیت نطنز", stone_type="گرانیت"),
        lot_code="B-1",
        b2c="3000000",
    )
    return {"seller": seller, "other": other, "first": first, "second": second, "third": third}


def _select(client, item):
    return client.post(reverse("catalog:selection_toggle", kwargs={"item_id": item.id}), follow=True)


def _dev_code(phone: str) -> str:
    """Read the code back the way the console provider makes it available."""
    from apps.accounts.services import _hash_code

    challenge = OTPChallenge.objects.filter(phone=phone, purpose="customer").order_by("-created_at").first()
    for candidate in range(0, 1000000):
        code = f"{candidate:06d}"
        if _hash_code(code) == challenge.code_hash:
            return code
    raise AssertionError("code not found")


# --- multi-product selection ---------------------------------------------------


@pytest.mark.django_db
def test_a_visitor_can_browse_without_logging_in(client, market):
    assert client.get(reverse("catalog:public_search")).status_code == 200
    assert client.get(f"/p/{market['first'].public_token}/").status_code == 200


@pytest.mark.django_db
def test_selecting_products_never_asks_who_you_are(client, market):
    """Nothing interrupts browsing to capture a lead."""
    _select(client, market["first"])
    _select(client, market["second"])

    body = client.get(reverse("catalog:public_search")).content.decode()
    assert "2 محصول انتخاب شده" in body
    assert not CustomerLead.objects.exists()
    assert not Inquiry.objects.exists()


@pytest.mark.django_db
def test_selecting_the_same_product_twice_removes_it(client, market):
    _select(client, market["first"])
    _select(client, market["first"])
    body = client.get(reverse("catalog:inquiry_review")).content.decode()
    assert "هنوز محصولی انتخاب نکرده‌اید" in body


@pytest.mark.django_db
def test_a_withdrawn_product_drops_out_of_the_selection(client, market):
    """Re-resolved on every read, so the seller never receives a request for
    something they have taken down."""
    _select(client, market["first"])
    _select(client, market["second"])

    market["first"].is_visible = False
    market["first"].save()

    body = client.get(reverse("catalog:inquiry_review")).content.decode()
    assert "تراورتن عباس‌آباد" not in body
    assert "مرمریت لاشتر" in body


# --- submission ----------------------------------------------------------------


@pytest.mark.django_db
def test_the_full_public_flow_saves_one_inquiry_with_several_products(client, market, settings):
    settings.DEBUG = True
    settings.SMS_PROVIDER = "console"

    _select(client, market["first"])
    _select(client, market["second"])

    client.post(
        reverse("catalog:inquiry_review"),
        {f"qty-{market['first'].id}": "120", f"qty-{market['second'].id}": "80"},
        follow=True,
    )
    client.post(
        reverse("catalog:inquiry_identify"),
        {"name": "آقای رضایی", "phone": "09121112233", "message": "نمای پروژه"},
        follow=True,
    )
    code = _dev_code("09121112233")
    response = client.post(reverse("catalog:inquiry_verify"), {"code": code}, follow=True)

    assert response.status_code == 200
    inquiry = Inquiry.objects.get()
    assert inquiry.business_id == market["seller"].id
    assert inquiry.name == "آقای رضایی"
    assert inquiry.message == "نمای پروژه"

    lines = {line.product_name: line.requested_qty_sqm for line in inquiry.items.all()}
    assert lines == {
        "سنگ تراورتن عباس‌آباد": Decimal("120.000"),
        "سنگ مرمریت لاشتر": Decimal("80.000"),
    }


@pytest.mark.django_db
def test_the_inquiry_is_saved_before_any_share_button_appears(client, market, settings):
    """Order matters: a seller must not depend on a message never sent."""
    settings.DEBUG = True
    settings.SMS_PROVIDER = "console"

    _select(client, market["first"])
    client.post(reverse("catalog:inquiry_review"), {f"qty-{market['first'].id}": "50"}, follow=True)
    client.post(
        reverse("catalog:inquiry_identify"),
        {"name": "مشتری", "phone": "09121112244"},
        follow=True,
    )
    client.post(reverse("catalog:inquiry_verify"), {"code": _dev_code("09121112244")}, follow=True)

    assert Inquiry.objects.count() == 1

    body = client.get(reverse("catalog:inquiry_done")).content.decode()
    assert "درخواست شما ثبت شد" in body
    assert "واتس‌اپ" in body


@pytest.mark.django_db
def test_a_selection_spanning_two_sellers_becomes_two_inquiries(client, market, settings):
    """One seller must never see what the customer asked another."""
    settings.DEBUG = True
    settings.SMS_PROVIDER = "console"

    _select(client, market["first"])
    _select(client, market["third"])
    client.post(reverse("catalog:inquiry_review"), {}, follow=True)
    client.post(
        reverse("catalog:inquiry_identify"),
        {"name": "مشتری", "phone": "09121112255"},
        follow=True,
    )
    client.post(reverse("catalog:inquiry_verify"), {"code": _dev_code("09121112255")}, follow=True)

    assert Inquiry.objects.count() == 2
    seller_inquiry = Inquiry.objects.get(business=market["seller"])
    other_inquiry = Inquiry.objects.get(business=market["other"])

    assert [line.product_name for line in seller_inquiry.items.all()] == ["سنگ تراورتن عباس‌آباد"]
    assert [line.product_name for line in other_inquiry.items.all()] == ["سنگ گرانیت نطنز"]


# --- one submission, one set of inquiries ---------------------------------------


def _run_flow(client, phone: str) -> None:
    client.post(reverse("catalog:inquiry_review"), {}, follow=True)
    client.post(
        reverse("catalog:inquiry_identify"),
        {"name": "مشتری", "phone": phone},
        follow=True,
    )
    client.post(reverse("catalog:inquiry_verify"), {"code": _dev_code(phone)}, follow=True)


@pytest.mark.django_db
def test_every_inquiry_from_one_submission_shares_a_token(client, market, settings):
    settings.DEBUG = True
    settings.SMS_PROVIDER = "console"

    _select(client, market["first"])
    _select(client, market["third"])
    _run_flow(client, "09121113300")

    tokens = set(Inquiry.objects.values_list("submission_id", flat=True))
    assert len(tokens) == 1
    assert None not in tokens


@pytest.mark.django_db
def test_resubmitting_the_same_token_creates_nothing_new(market):
    """AUD-010. A browser retry, a double-click or a refresh after a partial
    failure used to duplicate every seller inquiry that had already committed."""
    from apps.inquiries.services import submit_public_inquiry

    token = "3f1a5b0c-0000-4000-8000-000000000001"
    groups = [
        {"business": market["seller"], "rows": [{"item": market["first"], "quantity": Decimal("10")}]},
        {"business": market["other"], "rows": [{"item": market["third"], "quantity": Decimal("20")}]},
    ]

    first = submit_public_inquiry(
        submission_id=token, groups=groups, name="مشتری", phone="09121113311", verified=True
    )
    second = submit_public_inquiry(
        submission_id=token, groups=groups, name="مشتری", phone="09121113311", verified=True
    )

    assert Inquiry.objects.count() == 2
    assert {row.pk for row in first} == {row.pk for row in second}


@pytest.mark.django_db
def test_a_failure_on_the_second_seller_leaves_nothing_behind(market):
    """All or nothing. The loop used to run outside any transaction, so the first
    seller's inquiry stayed committed while the page reported failure."""
    from apps.inquiries.services import InquiryError, submit_public_inquiry

    token = "3f1a5b0c-0000-4000-8000-000000000002"
    groups = [
        {"business": market["seller"], "rows": [{"item": market["first"], "quantity": Decimal("10")}]},
        # A product belonging to a different seller than the group claims: the
        # service refuses it rather than silently dropping it.
        {"business": market["other"], "rows": [{"item": market["second"], "quantity": Decimal("20")}]},
    ]

    with pytest.raises(InquiryError):
        submit_public_inquiry(
            submission_id=token, groups=groups, name="مشتری", phone="09121113322", verified=True
        )

    assert not Inquiry.objects.exists()


@pytest.mark.django_db
def test_the_retry_after_a_failure_records_the_whole_submission_once(market):
    from apps.inquiries.services import InquiryError, submit_public_inquiry

    token = "3f1a5b0c-0000-4000-8000-000000000003"
    broken = [
        {"business": market["seller"], "rows": [{"item": market["first"], "quantity": Decimal("10")}]},
        {"business": market["other"], "rows": [{"item": market["second"], "quantity": Decimal("20")}]},
    ]
    fixed = [
        {"business": market["seller"], "rows": [{"item": market["first"], "quantity": Decimal("10")}]},
        {"business": market["other"], "rows": [{"item": market["third"], "quantity": Decimal("20")}]},
    ]

    with pytest.raises(InquiryError):
        submit_public_inquiry(
            submission_id=token, groups=broken, name="مشتری", phone="09121113333", verified=True
        )
    submit_public_inquiry(
        submission_id=token, groups=fixed, name="مشتری", phone="09121113333", verified=True
    )

    assert Inquiry.objects.count() == 2


@pytest.mark.django_db
def test_two_separate_submissions_are_two_sets_of_inquiries(client, market, settings):
    """Idempotency must not turn a genuine second question into a no-op.

    Two phones rather than one, because the OTP cooldown — correctly — refuses a
    second code for the same number within the minute, and that is a different
    rule from the one under test here.
    """
    settings.DEBUG = True
    settings.SMS_PROVIDER = "console"

    _select(client, market["first"])
    _run_flow(client, "09121113344")
    _select(client, market["first"])
    _run_flow(client, "09121113355")

    assert Inquiry.objects.count() == 2
    assert len(set(Inquiry.objects.values_list("submission_id", flat=True))) == 2


# --- customers are not platform users ------------------------------------------


@pytest.mark.django_db
def test_the_whole_flow_creates_no_platform_user(client, market, settings):
    settings.DEBUG = True
    settings.SMS_PROVIDER = "console"
    before = User.objects.count()

    _select(client, market["first"])
    client.post(reverse("catalog:inquiry_review"), {}, follow=True)
    client.post(
        reverse("catalog:inquiry_identify"),
        {"name": "مشتری", "phone": "09121112266"},
        follow=True,
    )
    client.post(reverse("catalog:inquiry_verify"), {"code": _dev_code("09121112266")}, follow=True)

    assert Inquiry.objects.count() == 1
    assert User.objects.count() == before
    assert not User.objects.filter(phone="09121112266").exists()


@pytest.mark.django_db
def test_verifying_a_customer_never_starts_a_session(client, market, settings):
    settings.DEBUG = True
    settings.SMS_PROVIDER = "console"

    _select(client, market["first"])
    client.post(reverse("catalog:inquiry_review"), {}, follow=True)
    client.post(
        reverse("catalog:inquiry_identify"),
        {"name": "مشتری", "phone": "09121112277"},
        follow=True,
    )
    response = client.post(
        reverse("catalog:inquiry_verify"),
        {"code": _dev_code("09121112277")},
        follow=True,
    )
    assert not response.wsgi_request.user.is_authenticated


@pytest.mark.django_db
def test_a_customer_code_cannot_be_used_to_log_in(rf, market, settings):
    """Purposes are separate so a code from one flow cannot be replayed on the other."""
    from apps.accounts.services import OTPValidationError, request_customer_otp, verify_login_otp

    settings.DEBUG = True
    settings.SMS_PROVIDER = "console"
    User.objects.create_user(phone="09121112288")

    request_customer_otp("09121112288")
    code = _dev_code("09121112288")

    with pytest.raises(OTPValidationError):
        verify_login_otp("09121112288", code, request=rf.post("/auth/verify/"))


# --- leads ----------------------------------------------------------------------


@pytest.mark.django_db
def test_the_same_phone_is_one_lead_per_business(market):
    first = get_or_create_lead(business=market["seller"], name="رضایی", phone="09121110000")
    second = get_or_create_lead(business=market["seller"], name="رضایی", phone="09121110000")
    assert first.pk == second.pk
    assert CustomerLead.objects.count() == 1


@pytest.mark.django_db
def test_two_sellers_keep_separate_customer_lists(market):
    get_or_create_lead(business=market["seller"], name="رضایی", phone="09121110000")
    get_or_create_lead(business=market["other"], name="رضایی", phone="09121110000")
    assert CustomerLead.objects.count() == 2


@pytest.mark.django_db
def test_a_newer_name_wins(market):
    get_or_create_lead(business=market["seller"], name="رضا", phone="09121110000")
    lead = get_or_create_lead(business=market["seller"], name="آقای رضایی", phone="09121110000")
    assert lead.name == "آقای رضایی"


@pytest.mark.django_db
def test_an_invalid_phone_is_refused(market):
    with pytest.raises(InquiryError):
        get_or_create_lead(business=market["seller"], name="کسی", phone="12345")


@pytest.mark.django_db
def test_an_inquiry_cannot_reference_another_businesss_product(market):
    with pytest.raises(InquiryError):
        create_inquiry(
            business=market["seller"],
            name="مشتری",
            phone="09121110000",
            items=[{"item": market["third"], "quantity": None}],
        )
    assert not Inquiry.objects.exists()


@pytest.mark.django_db
def test_the_line_keeps_a_product_name_snapshot(market):
    inquiry = create_inquiry(
        business=market["seller"],
        name="مشتری",
        phone="09121110000",
        items=[{"item": market["first"], "quantity": Decimal("30")}],
    )
    product = market["first"].product
    product.name_suffix = "نام جدید"
    product.save(update_fields=["name_suffix"])

    line = InquiryItem.objects.get(inquiry=inquiry)
    assert line.product_name == "سنگ تراورتن عباس‌آباد"


# --- seller inbox ----------------------------------------------------------------


def _login(client, business):
    client.force_login(business.memberships.get(role="owner").user)
    session = client.session
    session["current_business_id"] = str(business.id)
    session.save()


@pytest.mark.django_db
def test_the_seller_sees_the_inquiry_in_their_inbox(client, market):
    create_inquiry(
        business=market["seller"],
        name="آقای رضایی",
        phone="09121110000",
        items=[{"item": market["first"], "quantity": Decimal("30")}],
    )
    _login(client, market["seller"])
    body = client.get(reverse("inquiries:inbox")).content.decode()
    assert "آقای رضایی" in body
    assert "09121110000" in body


@pytest.mark.django_db
def test_another_seller_sees_nothing(client, market):
    create_inquiry(
        business=market["seller"],
        name="آقای رضایی",
        phone="09121110000",
        items=[{"item": market["first"], "quantity": Decimal("30")}],
    )
    _login(client, market["other"])
    assert "آقای رضایی" not in client.get(reverse("inquiries:inbox")).content.decode()


@pytest.mark.django_db
def test_the_customer_page_lists_their_previous_inquiries(client, market):
    for _ in range(2):
        create_inquiry(
            business=market["seller"],
            name="آقای رضایی",
            phone="09121110000",
            items=[{"item": market["first"], "quantity": Decimal("30")}],
        )
    lead = CustomerLead.objects.get()
    _login(client, market["seller"])

    body = client.get(reverse("inquiries:lead_detail", kwargs={"lead_id": lead.id})).content.decode()
    assert body.count("تراورتن عباس‌آباد") >= 2

    listing = client.get(reverse("inquiries:leads") + "?q=09121110000").content.decode()
    assert "آقای رضایی" in listing
    assert "2 استعلام" in listing


@pytest.mark.django_db
def test_a_seller_can_move_an_inquiry_through_its_states(client, market):
    inquiry = create_inquiry(
        business=market["seller"],
        name="مشتری",
        phone="09121110000",
        items=[{"item": market["first"], "quantity": None}],
    )
    _login(client, market["seller"])
    client.post(
        reverse("inquiries:set_status", kwargs={"inquiry_id": inquiry.id}),
        {"status": Inquiry.Status.CONTACTED},
    )
    inquiry.refresh_from_db()
    assert inquiry.status == Inquiry.Status.CONTACTED
    assert inquiry.contacted_at is not None
