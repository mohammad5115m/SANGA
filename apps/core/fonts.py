"""A bounded, bundled font for documents that cannot fetch external resources."""

from base64 import b64encode
from functools import lru_cache

from django.conf import settings


@lru_cache(maxsize=1)
def invoice_font_data() -> str:
    content = (settings.BASE_DIR / "static/fonts/Vazirmatn.woff2").read_bytes()
    return "data:font/woff2;base64," + b64encode(content).decode("ascii")
