import os
import shutil
import subprocess
from pathlib import Path

import pytest
from django.conf import settings


def test_failed_requests_retries_history_and_duplicate_initialization():
    node = os.environ.get("SANGA_NODE_BINARY") or shutil.which("node")
    if node is None:
        pytest.skip("Node is required for JavaScript behavior checks; CI installs Node 22.")
    result = subprocess.run(
        [node, str(Path(__file__).with_name("ui_runtime.cjs")),
         str(settings.BASE_DIR / "static/js/app.js"), str(settings.BASE_DIR / "static/js/sw.js")],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
