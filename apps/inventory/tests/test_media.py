"""Product media: many files per product, and upload validation.

The validation matters more than it looks. A browser-supplied ``Content-Type``
is attacker-controlled, so trusting it alone would let an arbitrary file through
under an image label.
"""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.core.testing import make_business, make_item, owner_membership
from apps.inventory.models import LotMedia
from apps.inventory.services import (
    MAX_IMAGE_BYTES,
    InventoryError,
    add_lot_media,
    delete_lot_media,
    reorder_lot_media,
    set_primary_media,
)
from apps.pricing.services import ensure_default_tiers

# A one-pixel PNG, so the bytes are a real image rather than a plausible name.
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00"
    b"\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture
def shop(db):
    ensure_default_tiers()
    business = make_business(name="سنگ رسانه", owner_phone="09271110001")
    return {
        "business": business,
        "membership": owner_membership(business),
        "item": make_item(business, lot_code="MED-1"),
    }


def _image(name="a.png") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, PNG, content_type="image/png")


def _video(name="a.mp4") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"\x00" * 1024, content_type="video/mp4")


def _add(shop, upload, **kwargs):
    return add_lot_media(lot=shop["item"], membership=shop["membership"], upload=upload, **kwargs)


# --- many files per product -------------------------------------------------------


@pytest.mark.django_db
def test_a_product_can_have_several_images(shop):
    for index in range(4):
        _add(shop, _image(f"{index}.png"))
    assert shop["item"].media.filter(kind=LotMedia.Kind.IMAGE).count() == 4


@pytest.mark.django_db
def test_a_product_can_have_several_videos(shop):
    for index in range(2):
        _add(shop, _video(f"{index}.mp4"))
    assert shop["item"].media.filter(kind=LotMedia.Kind.VIDEO).count() == 2


@pytest.mark.django_db
def test_images_and_videos_live_side_by_side(shop):
    _add(shop, _image())
    _add(shop, _video())
    kinds = set(shop["item"].media.values_list("kind", flat=True))
    assert kinds == {LotMedia.Kind.IMAGE, LotMedia.Kind.VIDEO}


# --- primary image ------------------------------------------------------------------


@pytest.mark.django_db
def test_the_first_upload_becomes_the_cover(shop):
    first = _add(shop, _image("first.png"))
    _add(shop, _image("second.png"))
    first.refresh_from_db()
    assert first.is_primary is True
    assert shop["item"].media.filter(is_primary=True).count() == 1


@pytest.mark.django_db
def test_choosing_a_new_cover_demotes_the_old_one(shop):
    first = _add(shop, _image("first.png"))
    second = _add(shop, _image("second.png"))

    set_primary_media(lot=shop["item"], membership=shop["membership"], media_id=second.id)
    first.refresh_from_db()
    second.refresh_from_db()

    assert second.is_primary is True
    assert first.is_primary is False


@pytest.mark.django_db
def test_a_video_cannot_be_the_cover(shop):
    """Cards render an image; a video cover would show an empty box."""
    _add(shop, _image())
    video = _add(shop, _video())
    with pytest.raises(InventoryError):
        set_primary_media(lot=shop["item"], membership=shop["membership"], media_id=video.id)


@pytest.mark.django_db
def test_deleting_the_cover_promotes_another_image(shop):
    """A gallery must never be left without a cover."""
    first = _add(shop, _image("first.png"))
    second = _add(shop, _image("second.png"))

    delete_lot_media(lot=shop["item"], membership=shop["membership"], media_id=first.id)
    second.refresh_from_db()
    assert second.is_primary is True


# --- ordering -------------------------------------------------------------------------


@pytest.mark.django_db
def test_media_can_be_reordered(shop):
    first = _add(shop, _image("1.png"))
    second = _add(shop, _image("2.png"))
    third = _add(shop, _image("3.png"))

    reorder_lot_media(
        lot=shop["item"],
        membership=shop["membership"],
        media_ids=[third.id, first.id, second.id],
    )
    assert [m.id for m in shop["item"].media.all()] == [third.id, first.id, second.id]


@pytest.mark.django_db
def test_reordering_ignores_ids_from_another_product(shop):
    """A stale tab should not error; a cosmetic action has nothing to refuse."""
    other = make_item(shop["business"], lot_code="MED-2")
    foreign = add_lot_media(lot=other, membership=shop["membership"], upload=_image())
    mine = _add(shop, _image())

    reorder_lot_media(
        lot=shop["item"],
        membership=shop["membership"],
        media_ids=[foreign.id, mine.id],
    )
    assert [m.id for m in shop["item"].media.all()] == [mine.id]


# --- validation -------------------------------------------------------------------------


@pytest.mark.django_db
def test_an_arbitrary_file_is_refused(shop):
    upload = SimpleUploadedFile("payload.exe", b"MZ\x00\x00", content_type="application/x-msdownload")
    with pytest.raises(InventoryError):
        _add(shop, upload)
    assert not shop["item"].media.exists()


@pytest.mark.django_db
def test_a_lying_content_type_does_not_get_a_file_through(shop):
    """The header is attacker-controlled; the extension has to agree."""
    upload = SimpleUploadedFile("payload.exe", b"MZ\x00\x00", content_type="image/png")
    with pytest.raises(InventoryError):
        _add(shop, upload)


@pytest.mark.django_db
def test_an_oversized_image_is_refused(shop):
    big = SimpleUploadedFile("big.png", b"\x00" * (MAX_IMAGE_BYTES + 1), content_type="image/png")
    with pytest.raises(InventoryError) as exc:
        _add(shop, big)
    assert "حجم" in exc.value.message
    assert not shop["item"].media.exists()


@pytest.mark.django_db
def test_another_business_cannot_touch_this_products_media(shop):
    intruder = make_business(name="سنگ غریبه", owner_phone="09271110009")
    intruder_m = owner_membership(intruder)
    media = _add(shop, _image())

    with pytest.raises(InventoryError):
        add_lot_media(lot=shop["item"], membership=intruder_m, upload=_image("x.png"))
    with pytest.raises(InventoryError):
        delete_lot_media(lot=shop["item"], membership=intruder_m, media_id=media.id)
    with pytest.raises(InventoryError):
        set_primary_media(lot=shop["item"], membership=intruder_m, media_id=media.id)


# --- visibility -----------------------------------------------------------------------


@pytest.mark.django_db
def test_media_of_a_hidden_product_is_not_listed_publicly(client, shop):
    _add(shop, _image())
    shop["item"].is_visible = False
    shop["item"].save()

    response = client.get(f"/p/{shop['item'].public_token}/")
    assert response.status_code == 404


# --- page ------------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_media_page_uploads_and_deletes(client, shop):
    client.force_login(shop["business"].memberships.get(role="owner").user)
    session = client.session
    session["current_business_id"] = str(shop["business"].id)
    session.save()

    url = reverse("inventory:lot_media", kwargs={"lot_id": shop["item"].id})
    client.post(url, {"action": "upload", "images": _image()}, follow=True)
    assert shop["item"].media.count() == 1

    media = shop["item"].media.get()
    client.post(url, {"action": "delete", "media_id": str(media.id)}, follow=True)
    assert shop["item"].media.count() == 0
