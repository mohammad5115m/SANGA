"""Bounded document exports with no external resource fetching."""

from __future__ import annotations

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from django.conf import settings
from django.core.cache import cache
from django.template.loader import render_to_string

from .documents import build_invoice_document


class DocumentRenderError(RuntimeError):
    pass


def export_allowed(*, user_id, invoice_id, kind: str) -> bool:
    """A small per-user burst limit; cache backends make this shared in production."""
    key = f"invoice-export:{user_id}:{invoice_id}:{kind}"
    limit = getattr(settings, "INVOICE_EXPORTS_PER_MINUTE", 6)
    if limit < 1:
        return False
    if cache.add(key, 1, 60):
        return True
    try:
        return cache.incr(key) <= limit
    except ValueError:
        return cache.add(key, 1, 60)


def document_html(invoice, *, mode: str = "export") -> str:
    return render_to_string(
        "invoicing/document.html",
        {"document": build_invoice_document(invoice), "document_mode": mode},
    )


def _locked_fetcher(url: str, *args, **kwargs):
    if not url.startswith("data:"):
        raise DocumentRenderError("بارگیری منبع خارجی در سند مجاز نیست.")
    from weasyprint import default_url_fetcher

    return default_url_fetcher(url, *args, **kwargs)


def render_pdf(invoice) -> bytes:
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as exc:
        raise DocumentRenderError("موتور PDF روی سرور نصب نشده است.") from exc
    try:
        result = HTML(string=document_html(invoice), url_fetcher=_locked_fetcher).write_pdf()
    except Exception as exc:
        raise DocumentRenderError("تولید PDF انجام نشد.") from exc
    maximum = getattr(settings, "INVOICE_EXPORT_MAX_BYTES", 25 * 1024 * 1024)
    if not result or len(result) > maximum:
        raise DocumentRenderError("اندازه خروجی PDF بیش از حد مجاز است.")
    return result


def render_png(invoice) -> tuple[bytes, str, str]:
    try:
        import pymupdf
    except ImportError as exc:
        raise DocumentRenderError("موتور خروجی تصویر روی سرور نصب نشده است.") from exc
    pdf = render_pdf(invoice)
    document = pymupdf.open(stream=pdf, filetype="pdf")
    try:
        max_pages = int(getattr(settings, "INVOICE_EXPORT_MAX_PAGES", 20))
        dpi = int(getattr(settings, "INVOICE_IMAGE_DPI", 200))
    except (TypeError, ValueError) as exc:
        document.close()
        raise DocumentRenderError("تنظیمات خروجی تصویر معتبر نیست.") from exc
    if not 1 <= max_pages <= 50 or not 72 <= dpi <= 300:
        document.close()
        raise DocumentRenderError("تنظیمات خروجی تصویر خارج از محدوده امن است.")
    if document.page_count < 1 or document.page_count > max_pages:
        document.close()
        raise DocumentRenderError("تعداد صفحات خروجی بیش از حد مجاز است.")
    maximum = getattr(settings, "INVOICE_EXPORT_MAX_BYTES", 25 * 1024 * 1024)
    try:
        if document.page_count == 1:
            result = document[0].get_pixmap(dpi=dpi, alpha=False).tobytes("png")
            content_type, extension = "image/png", "png"
        else:
            archive = BytesIO()
            with ZipFile(archive, "w", compression=ZIP_DEFLATED) as bundle:
                for index, page in enumerate(document, start=1):
                    content = page.get_pixmap(dpi=dpi, alpha=False).tobytes("png")
                    if len(content) > maximum:
                        raise DocumentRenderError(
                            "اندازه یکی از صفحات تصویر بیش از حد مجاز است."
                        )
                    bundle.writestr(f"page-{index:02d}.png", content)
                    if archive.tell() > maximum:
                        raise DocumentRenderError("اندازه خروجی تصویر بیش از حد مجاز است.")
            result, content_type, extension = archive.getvalue(), "application/zip", "zip"
    finally:
        document.close()
    if not result or len(result) > maximum:
        raise DocumentRenderError("اندازه خروجی تصویر بیش از حد مجاز است.")
    return result, content_type, extension
