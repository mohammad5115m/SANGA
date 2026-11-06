"""Exercise real Redis across processes and under concurrent export requests."""

import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.core.cache import cache

from apps.invoicing.rendering import export_allowed

pytestmark = pytest.mark.skipif(
    not os.environ.get("SANGA_TEST_REDIS_URL"), reason="Requires an isolated Redis service (provided by CI).",
)


def test_export_budget_is_shared_between_processes_and_atomic(settings):
    prefix = f"sanga-test-{uuid.uuid4().hex}"
    settings.CACHES = {"default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": os.environ["SANGA_TEST_REDIS_URL"], "KEY_PREFIX": prefix,
    }}
    settings.INVOICE_EXPORTS_PER_MINUTE = 3
    user_id = uuid.uuid4().hex
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(
                lambda index: export_allowed(user_id=user_id, invoice_id=str(index), kind="pdf"), range(12),
            ))
        assert sum(outcomes) == 3
        code = """
import os
import django
django.setup()
from django.conf import settings
settings.CACHES = {'default': {
    'BACKEND': 'django.core.cache.backends.redis.RedisCache',
    'LOCATION': os.environ['SANGA_TEST_REDIS_URL'],
    'KEY_PREFIX': os.environ['SANGA_TEST_CACHE_PREFIX'],
}}
settings.INVOICE_EXPORTS_PER_MINUTE = 3
from apps.invoicing.rendering import export_allowed
print(export_allowed(user_id=os.environ['SANGA_TEST_EXPORT_USER'], invoice_id='another', kind='png'))
"""
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=30,
            env={**os.environ, "SANGA_TEST_CACHE_PREFIX": prefix, "SANGA_TEST_EXPORT_USER": user_id},
        )
        assert result.returncode == 0, "Export cache subprocess failed"
        assert result.stdout.strip() == "False"
    finally:
        cache.delete(f"invoice-export:{user_id}")
