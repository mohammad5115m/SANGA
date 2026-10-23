from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from redis.exceptions import ConnectionError as RedisConnectionError

from apps.invoicing.rendering import export_allowed


@override_settings(INVOICE_EXPORTS_PER_MINUTE=2)
def test_changing_invoice_or_format_does_not_bypass_user_budget():
    cache.clear()
    assert export_allowed(user_id="one", invoice_id="a", kind="pdf")
    assert export_allowed(user_id="one", invoice_id="b", kind="png")
    assert not export_allowed(user_id="one", invoice_id="c", kind="pdf")
    assert export_allowed(user_id="two", invoice_id="a", kind="pdf")


def test_cache_outage_refuses_expensive_rendering_without_logging_credentials(caplog):
    with patch("apps.invoicing.rendering.cache.add", side_effect=RedisConnectionError("SECRET")):
        assert not export_allowed(user_id="one", invoice_id="a", kind="pdf")
    assert "SECRET" not in caplog.text
