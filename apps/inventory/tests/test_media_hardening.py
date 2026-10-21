"""What reaches storage, and what leaves it.

Three properties, none of which the old code had:

- an upload is what it claims to be, established by reading the bytes rather
  than by believing three caller-supplied strings that all derive from the
  filename;
- deleting a photo deletes the photo, not just the row pointing at it;
- an item has one cover image, enforced where two simultaneous uploads cannot
  both win.
"""

from __future__ import annotations

from unittest import mock

import pytest
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction

from apps.core.testing import make_business, make_item, owner_membership
from apps.inventory.models import LotMedia
from apps.inventory.services import (
    InventoryError,
    add_lot_media,
    delete_lot_media,
    set_primary_media,
)
from apps.pricing.services import ensure_default_tiers

from .test_media import MP4, PNG


@pytest.fixture
def shop(db):
    ensure_default_tiers()
    business = make_business(name="سنگ سخت‌شده", owner_phone="09411110001")
    return {
        "business": business,
        "membership": owner_membership(business),
        "item": make_item(business, lot_code="HD-1"),
    }


def _add(shop, upload, **kwargs):
    return add_lot_media(lot=shop["item"], membership=shop["membership"], upload=upload, **kwargs)


# --- the bytes decide -----------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("name", "content", "content_type"),
    [
        ("stone.jpg", b"<html><script>alert(1)</script></html>", "image/jpeg"),
        ("stone.png", b"#!/bin/sh\nrm -rf /\n", "image/png"),
        ("stone.webp", b"MZ\x90\x00\x03\x00\x00\x00", "image/webp"),
    ],
)
def test_a_renamed_file_is_refused_however_it_labels_itself(shop, name, content, content_type):
    """Extension, Content-Type and the guessed MIME all agreed about these."""
    with pytest.raises(InventoryError):
        _add(shop, SimpleUploadedFile(name, content, content_type=content_type))
    assert not LotMedia.objects.exists()


@pytest.mark.django_db
def test_a_truncated_image_is_refused(shop):
    """A header that parses over missing pixel data is exactly why the file is
    opened twice."""
    with pytest.raises(InventoryError):
        _add(shop, SimpleUploadedFile("half.png", PNG[:20], content_type="image/png"))


@pytest.mark.django_db
def test_a_file_pretending_to_be_a_video_is_refused(shop):
    with pytest.raises(InventoryError):
        _add(shop, SimpleUploadedFile("clip.mp4", b"\x00" * 512, content_type="video/mp4"))


@pytest.mark.django_db
def test_a_real_webm_container_is_accepted(shop):
    webm = b"\x1a\x45\xdf\xa3" + b"\x00" * 64
    media = _add(shop, SimpleUploadedFile("clip.webm", webm, content_type="video/webm"))
    assert media.kind == LotMedia.Kind.VIDEO


@pytest.mark.django_db
def test_a_real_image_is_still_accepted(shop):
    media = _add(shop, SimpleUploadedFile("real.png", PNG, content_type="image/png"))
    assert media.kind == LotMedia.Kind.IMAGE


@pytest.mark.django_db
def test_a_lying_content_type_does_not_help_a_real_image(shop):
    """The header is ignored in both directions: real bytes are accepted whatever
    the browser said."""
    media = _add(shop, SimpleUploadedFile("real.png", PNG, content_type="application/octet-stream"))
    assert media.kind == LotMedia.Kind.IMAGE


# --- storage follows the database ------------------------------------------------


@pytest.mark.django_db
def test_deleting_media_deletes_the_stored_object(shop, django_capture_on_commit_callbacks):
    media = _add(shop, SimpleUploadedFile("gone.png", PNG, content_type="image/png"))
    name = media.file.name

    with mock.patch.object(FileSystemStorage, "delete") as deleted:
        with django_capture_on_commit_callbacks(execute=True):
            delete_lot_media(lot=shop["item"], membership=shop["membership"], media_id=media.id)

    assert name in {call.args[0] for call in deleted.call_args_list}


@pytest.mark.django_db
def test_a_rolled_back_delete_leaves_the_object_alone(shop, django_capture_on_commit_callbacks):
    """The opposite failure is worse: a deleted file with a row still pointing at
    it. Cleanup runs on commit, so a rollback runs nothing."""
    media = _add(shop, SimpleUploadedFile("kept.png", PNG, content_type="image/png"))

    with mock.patch.object(FileSystemStorage, "delete") as deleted:
        with django_capture_on_commit_callbacks(execute=True):
            with pytest.raises(RuntimeError), transaction.atomic():
                delete_lot_media(lot=shop["item"], membership=shop["membership"], media_id=media.id)
                raise RuntimeError("something later in the request failed")

    assert deleted.call_args_list == []
    assert LotMedia.objects.filter(pk=media.pk).exists()


@pytest.mark.django_db
def test_purging_an_item_cleans_up_all_its_media(shop, django_capture_on_commit_callbacks):
    from apps.inventory.services import delete_item

    first = _add(shop, SimpleUploadedFile("one.png", PNG, content_type="image/png"))
    second = _add(shop, SimpleUploadedFile("two.mp4", MP4, content_type="video/mp4"))
    names = {first.file.name, second.file.name}

    with mock.patch.object(FileSystemStorage, "delete") as deleted:
        with django_capture_on_commit_callbacks(execute=True):
            outcome = delete_item(lot=shop["item"], membership=shop["membership"])

    assert outcome == "purged"
    assert names <= {call.args[0] for call in deleted.call_args_list}


# --- one cover -------------------------------------------------------------------


@pytest.mark.django_db
def test_the_database_refuses_a_second_primary_image(shop):
    """AUD-020. The services demote before promoting, but two doing that together
    both won. This is what holds when they do."""
    first = _add(shop, SimpleUploadedFile("a.png", PNG, content_type="image/png"))
    second = _add(shop, SimpleUploadedFile("b.png", PNG, content_type="image/png"))

    assert first.is_primary is True
    assert second.is_primary is False

    with pytest.raises(IntegrityError), transaction.atomic():
        LotMedia.objects.filter(pk=second.pk).update(is_primary=True)


@pytest.mark.django_db
def test_two_items_may_each_have_their_own_cover(shop):
    """The constraint is per item, not global."""
    other = make_item(shop["business"], lot_code="HD-2")
    _add(shop, SimpleUploadedFile("a.png", PNG, content_type="image/png"))
    add_lot_media(
        lot=other,
        membership=shop["membership"],
        upload=SimpleUploadedFile("b.png", PNG, content_type="image/png"),
    )
    assert LotMedia.objects.filter(is_primary=True).count() == 2


@pytest.mark.django_db
def test_switching_the_cover_leaves_exactly_one(shop):
    _add(shop, SimpleUploadedFile("a.png", PNG, content_type="image/png"))
    second = _add(shop, SimpleUploadedFile("b.png", PNG, content_type="image/png"))

    set_primary_media(lot=shop["item"], membership=shop["membership"], media_id=second.id)

    assert LotMedia.objects.filter(lot=shop["item"], is_primary=True).count() == 1
    second.refresh_from_db()
    assert second.is_primary is True
