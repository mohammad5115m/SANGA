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

#: Longest edge SANGA will decode. Well past any phone camera; a product photo
#: does not need 20,000 pixels of anything.
MAX_IMAGE_DIMENSION = 12_000

#: Total pixels SANGA will decode, which is the number that actually bounds
#: memory. A decoded pixel costs about four bytes, so this is roughly 240 MB at
#: worst — survivable on one request, fatal if several arrive together.
#:
#: The byte limit does not cover this. Compression ratios of several thousand to
#: one are ordinary for synthetic images, so a 40 KB PNG that passes a 10 MB
#: check can still expand to gigabytes when decoded. That is the decompression
#: bomb, and the only defence is a limit on the decoded size.
MAX_IMAGE_PIXELS = 60_000_000


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


TOO_LARGE_MESSAGE = (
    f"ابعاد تصویر بیش از حد مجاز است (حداکثر {MAX_IMAGE_DIMENSION} پیکسل در هر ضلع)."
)


def _check_size(width: int, height: int) -> None:
    """Refuse before decoding, using the header's declared dimensions.

    Order matters: this runs against the size Pillow read from the header, so a
    bomb is refused without ever being expanded. Checking afterwards would mean
    the memory had already been spent.
    """
    if width <= 0 or height <= 0:
        raise MediaValidationError("این فایل یک تصویر معتبر نیست.")
    if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
        raise MediaValidationError(TOO_LARGE_MESSAGE)
    if width * height > MAX_IMAGE_PIXELS:
        raise MediaValidationError(TOO_LARGE_MESSAGE)


def verify_image(upload: UploadedFile) -> str:
    """Decode the file and return its real format.

    Pillow's ``verify()`` consumes the file object, so the image is opened twice:
    once to check the container parses, once to force the pixel data to decode.
    A header that parses over truncated or corrupt data is exactly the case the
    second open catches.

    Between the two, the declared dimensions are checked. The byte limit applied
    before this does not bound the decoded size at all — compression ratios in
    the thousands are ordinary for synthetic images, so a 40 KB PNG can expand to
    gigabytes of pixels — and the expansion happens during ``load()``. Refusing
    on the header is the only point at which the memory has not been spent yet.

    ``Image.MAX_IMAGE_PIXELS`` is set alongside rather than relied upon: Pillow
    raises for images beyond twice that value but only *warns* between one and
    two times it, and a warning does not stop a decode.
    """
    import warnings

    from PIL import Image, UnidentifiedImageError

    # Belt and braces with the explicit check below. Pillow's own limit catches
    # formats whose header size we did not read, and can be reached from inside
    # load() on a multi-frame image.
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

    # One finally around the whole thing: the caller stores from this same
    # stream, so it has to be back at the start however this exits — including
    # on the size refusal between the two decodes.
    try:
        upload.seek(0)
        try:
            with Image.open(upload) as probe:
                image_format = (probe.format or "").upper()
                width, height = probe.size
                probe.verify()
        except Image.DecompressionBombError as exc:
            raise MediaValidationError(TOO_LARGE_MESSAGE) from exc
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
            raise MediaValidationError("این فایل یک تصویر معتبر نیست.") from exc

        if image_format not in ALLOWED_IMAGE_FORMATS:
            raise MediaValidationError(
                "قالب تصویر پشتیبانی نمی‌شود. از jpg، png، webp یا gif استفاده کنید."
            )

        _check_size(width, height)

        upload.seek(0)
        try:
            with warnings.catch_warnings():
                # Pillow warns rather than raises in the band between one and two
                # times MAX_IMAGE_PIXELS. A warning is not a defence, so promote it.
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(upload) as image:
                    image.load()
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise MediaValidationError(TOO_LARGE_MESSAGE) from exc
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise MediaValidationError("این تصویر ناقص یا خراب است.") from exc
    finally:
        upload.seek(0)

    return image_format


def verify_video(upload: UploadedFile) -> str:
    """Check the container signature and return the family it belongs to.

    **This is a container check, not a validity check, and the difference is
    deliberate.** It establishes that the bytes begin as an MP4/MOV/WebM rather
    than as a script or an executable, which is the property that matters for
    something a browser will be handed. It does not establish that the stream
    decodes, that its codecs are ones any player supports, or how long it is.

    Full validation means ffprobe, which means ffmpeg in the image: a large
    dependency with a large CVE history, pulled in for an MVP that stores short
    product clips. The deferral is recorded here rather than implied, and what
    stands in for it is stated so the gap is a decision rather than an oversight:

    * a 60 MB size limit, applied before any of this (``services.MAX_VIDEO_BYTES``);
    * a closed list of containers;
    * ``Content-Type`` and ``X-Content-Type-Options: nosniff`` on the stored
      object, so a file that turns out not to be a video is still never executed
      in SANGA's origin.

    Revisit when video becomes a real part of the product rather than an
    occasional attachment.
    """
    head = _head(upload)
    if len(head) < 12:
        raise MediaValidationError("این فایل یک ویدیوی معتبر نیست.")

    if head[:4] == _EBML_MAGIC:
        return "webm"
    # ISO-BMFF: [4-byte box size][b"ftyp"][4-byte major brand].
    if head[4:8] == b"ftyp" and head[8:12] in _ISOBMFF_BRANDS:
        return "mp4"

    raise MediaValidationError("قالب ویدیو پشتیبانی نمی‌شود. از mp4، mov یا webm استفاده کنید.")
