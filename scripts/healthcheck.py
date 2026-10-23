"""Probe the application using a real allowed host; redirects are failures."""

import json
import os
import sys
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main() -> int:
    host = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost").split(",")[0].strip()
    port = os.environ.get("PORT", "8000")
    request = Request(f"http://127.0.0.1:{port}/health/", headers={"Host": host})
    try:
        with build_opener(NoRedirect).open(request, timeout=3) as response:
            payload = json.load(response)
            return 0 if response.status == 200 and payload.get("status") == "ok" else 1
    except Exception:
        return 1


if __name__ == "__main__":
    sys.exit(main())
