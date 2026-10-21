"""An upload that is small on disk can still be enormous once decoded.

The byte limit does not bound memory. Compression ratios of several thousand to
one are ordinary for synthetic images, so a 40 KB PNG declaring 30,000 × 30,000
pixels passes a 10 MB check and then asks for gigabytes during ``load()``. That
is the decompression bomb, and the only defence is a limit on the decoded size,
applied before the decode.

The images here are built by rewriting a real PNG's IHDR rather than by
generating something huge, because a test that allocates 900 million pixels to
prove we refuse 900 million pixels is a test nobody will keep.
"""

from __future__ import annotations

import io
import struct
import zlib

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.inventory.media_validation import (
    MAX_IMAGE_DIMENSION,
    MAX_IMAGE_PIXELS,
    MediaValidationError,
    _check_size,
    verify_image,
)
from apps.inventory.services import MAX_VIDEO_BYTES, InventoryError


def _png(width: int = 4, height: int = 4) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _png_claiming(width: int, height: int) -> bytes:
    """A real, tiny PNG whose header declares a different size.

    Exactly the shape of the attack: the file is small, and nothing about its
    size on disk hints at what decoding it would cost. Pillow reads the declared
    dimensions from IHDR at ``open()`` time, which is the moment the refusal has
    to happen.
    """
    data = bytearray(_png())
    struct.pack_into(">II", data, 16, width, height)
    # The IHDR CRC covers the chunk type and its data: bytes 12 through 29.
    struct.pack_into(">I", data, 29, zlib.crc32(bytes(data[12:29])))
    return bytes(data)


def _upload(content: bytes, name: str = "stone.png") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content, content_type="image/png")


# --- the bomb -----------------------------------------------------------------


def test_an_image_declaring_impossible_dimensions_is_refused():
    bomb = _upload(_png_claiming(30_000, 30_000))
    assert len(bomb.read()) < 1024, "the point is that it is tiny"
    bomb.seek(0)

    with pytest.raises(MediaValidationError):
        verify_image(bomb)


def test_an_image_within_the_edge_limit_but_over_the_pixel_limit_is_refused():
    """Both edges are legal on their own; their product is not. Checking only the
    longest edge would let this through."""
    side = 11_000
    assert side < MAX_IMAGE_DIMENSION
    assert side * side > MAX_IMAGE_PIXELS

    with pytest.raises(MediaValidationError):
        verify_image(_upload(_png_claiming(side, side)))


def test_a_single_impossible_edge_is_refused():
    with pytest.raises(MediaValidationError):
        verify_image(_upload(_png_claiming(MAX_IMAGE_DIMENSION + 1, 2)))


def test_a_zero_dimension_image_is_refused():
    with pytest.raises(MediaValidationError):
        verify_image(_upload(_png_claiming(0, 0)))


def test_the_bomb_is_refused_before_it_is_decoded():
    """If this ever regresses, the test suite hangs or the box swaps rather than
    reporting a failure — which is the same thing that would happen in
    production."""
    import time

    started = time.monotonic()
    with pytest.raises(MediaValidationError):
        verify_image(_upload(_png_claiming(40_000, 40_000)))

    assert time.monotonic() - started < 5, "the image was decoded before being refused"


# --- ordinary photographs still work ------------------------------------------


def test_a_normal_photograph_is_accepted():
    assert verify_image(_upload(_png(1600, 1200))) == "PNG"


@pytest.mark.parametrize(
    ("width", "height"),
    [
        (6000, 4000),  # a 24-megapixel camera is not an attack
        (MAX_IMAGE_DIMENSION, 1),  # the longest edge exactly on the limit
        (7_745, 7_745),  # just under the pixel limit
    ],
)
def test_the_limits_admit_every_photograph_a_seller_would_upload(width, height):
    """Checked against the size gate directly rather than by building a
    24-megapixel PNG: this is about where the boundary sits, and decoding a
    hundred megabytes to find out would make the suite unusable."""
    assert width * height <= MAX_IMAGE_PIXELS
    _check_size(width, height)  # must not raise


# --- the stream is left usable ------------------------------------------------


def test_the_stream_is_rewound_after_a_successful_check():
    """The caller stores from this same object, so a consumed stream would write
    a truncated or empty file."""
    upload = _upload(_png(64, 64))
    verify_image(upload)
    assert upload.tell() == 0
    assert len(upload.read()) > 0


def test_the_stream_is_rewound_after_a_refusal():
    upload = _upload(_png_claiming(30_000, 30_000))
    with pytest.raises(MediaValidationError):
        verify_image(upload)
    assert upload.tell() == 0


# --- the existing guarantees still hold ---------------------------------------


def test_a_truncated_image_is_still_refused():
    with pytest.raises(MediaValidationError):
        verify_image(_upload(_png(64, 64)[:40]))


def test_a_disguised_script_is_still_refused():
    with pytest.raises(MediaValidationError):
        verify_image(_upload(b"<html><script>alert(1)</script></html>"))


def test_an_unsupported_but_valid_image_format_is_still_refused():
    """A real TIFF is a real image and still not something to serve as a product
    photo."""
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format="TIFF")

    with pytest.raises(MediaValidationError):
        verify_image(_upload(buffer.getvalue(), name="stone.tiff"))


# --- video is size- and container-limited, and says so ------------------------


@pytest.mark.django_db
def test_an_oversized_video_is_refused(business_with_item):
    """Video validation stops at the container signature by design, so the size
    limit is doing more of the work here than it is for images."""
    from apps.inventory.services import add_lot_media

    lot, membership = business_with_item
    oversized = SimpleUploadedFile(
        "clip.mp4",
        b"\x00\x00\x00\x18ftypisom" + b"\x00" * (MAX_VIDEO_BYTES + 1),
        content_type="video/mp4",
    )

    with pytest.raises(InventoryError, match="حجم"):
        add_lot_media(lot=lot, membership=membership, upload=oversized)


@pytest.fixture
def business_with_item(db):
    from apps.core.testing import make_business, make_item, owner_membership

    business = make_business(name="سنگ رسانه", owner_phone="09241110001")
    return make_item(business, lot_code="MD-1"), owner_membership(business)
