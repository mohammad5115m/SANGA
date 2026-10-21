"""Small, safe CSV exports for immutable financial records in admin."""

from __future__ import annotations

import csv
from urllib.parse import quote

from django.http import HttpResponse


def _cell(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        return value
    # Spreadsheets treat these prefixes as formulas. Names and descriptions
    # entered by users must remain plain text when an admin opens the export.
    if value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def csv_response(filename: str, headers: tuple[str, ...], rows):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(filename)}"
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(_cell(value) for value in row)
    return response
