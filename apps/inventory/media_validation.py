"""Prove an upload is what it claims to be.

Classification used to read the filename extension, the browser's Content-Type
header, and `mimetypes.guess_type(filename)` — which is itself derived from the
filename. All three are supplied by the caller, so all three agree with each
other about a file that is not an image at all. Renaming `payload.html` to
`stone.jpg` and posting it with `Content-Type: image/jpeg` passed every check.

What stops that is reading the bytes. Images are decoded by Pillow, which the
project already depends on. Videos are checked against their container
signature: Pillow cannot help there, and a full demuxer is a large dependency
and a large attack surface for a check that only has to establish "this is an
MP4/MOV/WebM container, not a script".

Neither is a virus scanner, and neither claims to be. The property being
defended is narrower and worth having on its own: whatever ends up in storage
and gets served back to a browser is a media file.
"""

from __future__ import annotations

import logging

from django.core.files.uploadedfile import UploadedFile

logger = logging.getLogger(__name__)

#: Formats Pillow may return. Anything it decodes but that is not on this list is
#: refused: a valid TIFF or PDF is still not something to serve as a product photo.
ALLOWED_IMAGE_FORMATS = frozenset({"JPEG", "PNG", "WEBP", "GIF"})

#: How much of the file the container probe needs. ISO-BMFF puts its box header
#: in the first 12 bytes; Matroska's EBML magic is the first 4.
_PROBE_BYTES = 32

#: ISO base media file format brands SANGA accepts, read from the `ftyp` box.
#: mp4 and mov are the same container with different brands, which is why one
#: check covers both.
_ISOBMFF_BRANDS = (
    b"isom", b"iso2", b"iso4", b"iso5", b"iso6", b"avc1", b"mp41", b"mp42",
    b"qt  ", b"M4V ", b"mmp4", b"dash",
)

#: EBML magic, shared by Matroska and WebM.
_EBML_MAGIC = b"\x1a\x45\xdf\xa3"


class MediaValidationError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def _head(upload: UploadedFile, size: int = _PROBE_BYTES) -> bytes:
    position = upload.tell() if hasattr(upload, "tell") else 0
    upload.seek(0)
    head = upload.read(size)
    upload.seek(position)
    return head or b""


def verify_image(upload: UploadedFile) -> str:
    """Decode the file and return its real format.

    Pillow's ``verify()`` consumes the file object, so the image is opened twice:
    once to check the container parses, once to force the pixel data to decode.
    A header that parses over truncated or corrupt data is exactly the case the
    second open catches.
    """
    from PIL import Image, UnidentifiedImageError

    upload.seek(0)
    try:
        with Image.open(upload) as probe:
            image_format = (probe.format or "").upper()
            probe.verify()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise MediaValidationError("این فایل یک تصویر معتبر نیست.") from exc

    if image_format not in ALLOWED_IMAGE_FORMATS:
        raise MediaValidationError("قالب تصویر پشتیبانی نمی‌شود. از jpg، png، webp یا gif استفاده کنید.")

    upload.seek(0)
    try:
        with Image.open(upload) as image:
            image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise MediaValidationError("این تصویر ناقص یا خراب است.") from exc
    finally:
        upload.seek(0)

    return image_format


def verify_video(upload: UploadedFile) -> str:
    """Check the container signature and return the family it belongs to."""
    head = _head(upload)
    if len(head) < 12:
        raise MediaValidationError("این فایل یک ویدیوی معتبر نیست.")

    if head[:4] == _EBML_MAGIC:
        return "webm"
    # ISO-BMFF: [4-byte box size][b"ftyp"][4-byte major brand].
    if head[4:8] == b"ftyp" and head[8:12] in _ISOBMFF_BRANDS:
        return "mp4"

    raise MediaValidationError("قالب ویدیو پشتیبانی نمی‌شود. از mp4، mov یا webm استفاده کنید.")
