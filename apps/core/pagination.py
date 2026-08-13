"""Pagination for the listing surfaces.

The discovery pages used to slice — `[:60]`, `[:80]`, `[:100]` — with no next
page. A display cap is not pagination: past the cap the products still matched
the search and were simply unreachable, and the only way to find one was to guess
a narrower filter. On a marketplace, "we have it but you cannot see it" is the
worst possible answer.

One helper rather than six, so every list gets the same out-of-range behaviour
and the same query-string handling.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.http import HttpRequest

#: Page sizes tuned per surface: cards are tall, table rows are not.
CARD_PAGE_SIZE = 24
ROW_PAGE_SIZE = 50

PAGE_PARAM = "page"


@dataclass(frozen=True)
class Page:
    """What a template needs to render a pager, and nothing else."""

    object_list: list
    number: int
    num_pages: int
    total: int
    has_previous: bool
    has_next: bool
    previous_page_number: int | None
    next_page_number: int | None
    querystring: str

    @property
    def is_paginated(self) -> bool:
        return self.num_pages > 1


def paginate(request: HttpRequest, queryset, *, per_page: int = CARD_PAGE_SIZE) -> Page:
    """Page ``queryset``, preserving every other query parameter.

    An out-of-range page shows the last one rather than 404ing. Changing a filter
    while on page 7 of the previous result set is the normal way to arrive there,
    and answering that with an error page punishes the user for narrowing their
    search.
    """
    paginator = Paginator(queryset, per_page)
    raw = request.GET.get(PAGE_PARAM)
    try:
        page = paginator.page(raw)
    except PageNotAnInteger:
        page = paginator.page(1)
    except EmptyPage:
        page = paginator.page(paginator.num_pages)

    return Page(
        object_list=list(page.object_list),
        number=page.number,
        num_pages=paginator.num_pages,
        total=paginator.count,
        has_previous=page.has_previous(),
        has_next=page.has_next(),
        previous_page_number=page.previous_page_number() if page.has_previous() else None,
        next_page_number=page.next_page_number() if page.has_next() else None,
        querystring=preserved_querystring(request),
    )


def preserved_querystring(request: HttpRequest) -> str:
    """Every current parameter except the page number, ready to append.

    Returns ``""`` or ``"stone_type=تراورتن&sort=price_asc&"``, so a template can
    write ``?{{ page.querystring }}page=2`` without knowing which filters exist.
    Losing the filters on page 2 is the classic version of this bug.
    """
    params = request.GET.copy()
    params.pop(PAGE_PARAM, None)
    encoded = params.urlencode()
    return f"{encoded}&" if encoded else ""
