"""Image-only upload hardening for invoice branding assets."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_ASSET_BYTES = 5 * 1024 * 1024
MAX_ASSET_PIXELS = 16_000_000
ALLOWED_FORMATS = {"PNG", "WEBP", "JPEG"}


def sanitize_invoice_image(upload, *, stem: str = "asset") -> ContentFile:
    if upload is None:
        return upload
    size = getattr(upload, "size", 0) or 0
    if size <= 0 or size > MAX_ASSET_BYTES:
        raise ValidationError("حجم تصویر باید کمتر از ۵ مگابایت باشد.")
    try:
        upload.seek(0)
        with Image.open(upload) as source:
            if source.format not in ALLOWED_FORMATS:
                raise ValidationError("فقط تصویر PNG، WebP یا JPEG مجاز است.")
            if source.width * source.height > MAX_ASSET_PIXELS:
                raise ValidationError("ابعاد تصویر بیش از حد مجاز است.")
            source.verify()
        upload.seek(0)
        with Image.open(upload) as source:
            image = ImageOps.exif_transpose(source)
            image.thumbnail((3000, 3000), Image.Resampling.LANCZOS)
            image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            output = BytesIO()
            image.save(output, format="PNG", optimize=True)
    except ValidationError:
        raise
    except (Image.DecompressionBombError, OSError, UnidentifiedImageError, ValueError) as exc:
        raise ValidationError("محتوای فایل یک تصویر سالم و مجاز نیست.") from exc
    content = output.getvalue()
    if len(content) > MAX_ASSET_BYTES:
        raise ValidationError("حجم تصویر پاک‌سازی‌شده باید کمتر از ۵ مگابایت باشد.")
    safe_stem = "".join(ch for ch in Path(stem).stem.lower() if ch.isalnum() or ch in "-_") or "asset"
    return ContentFile(content, name=f"{safe_stem}.png")
