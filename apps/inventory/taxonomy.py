"""Turning what a seller typed into something a buyer's search can match.

Two separate problems, and only one of them was being solved.

**Orthography.** «مرمريت» typed with an Arabic ي and «مرمریت» typed with a
Persian ی are the same word and different strings. ``normalize_persian_text``
handles that — but it was only ever applied to the incoming *query*. Stored
values were saved with whatever the seller's keyboard produced, so normalizing
one side of the comparison protected nothing: a product entered on an Arabic
keyboard was invisible to a search typed on a Persian one, and vice versa.

**Vocabulary.** «کریستال» and «چینی» are the same stone and no amount of
letter-level normalization will ever join them. That needs a list, which is what
:class:`~apps.inventory.models.VocabularyTerm` is.

Both are applied on write, so the stored value is the one that will be searched.
Applying them only on read would mean every query paying for a scan, and would
still leave two spellings of one stone counted as two things in every report.
"""

from __future__ import annotations

from apps.core.persian import normalize_persian_text

#: Cached per kind: the vocabulary is platform-wide, changes when an
#: administrator edits it, and is read on every product save.
_TERMS: dict[str, dict[str, str]] = {}


def _lookup(kind: str) -> dict[str, str]:
    """Normalized spelling → canonical name, for one dimension."""
    if kind not in _TERMS:
        from .models import VocabularyTerm

        table: dict[str, str] = {}
        for term in VocabularyTerm.objects.filter(kind=kind, is_active=True):
            table[normalize_persian_text(term.name).casefold()] = term.name
            for alias in term.aliases or []:
                table[normalize_persian_text(str(alias)).casefold()] = term.name
        _TERMS[kind] = table
    return _TERMS[kind]


def clear_cache() -> None:
    """Forget the cached vocabulary. Called after it is edited, and from tests."""
    _TERMS.clear()


def canonical(kind: str, value: str) -> str:
    """The spelling to store for ``value`` in the ``kind`` dimension.

    Normalizes first, then maps onto a known term if there is one. A value that
    matches nothing is kept — normalized — rather than refused: Iranian stone
    naming has a long tail, and a seller who cannot record the stone they
    actually have will stop recording stone.
    """
    text = normalize_persian_text(value or "")
    if not text:
        return ""
    return _lookup(kind).get(text.casefold(), text)


def normalize_searchable(value: str) -> str:
    """For the free-text dimensions with no controlled list.

    Quarry and region stay open on purpose — there are hundreds of Iranian
    quarries and a closed list would be wrong within a month — and grade stays
    open because the industry genuinely does not share one vocabulary. They still
    have to be spelled consistently to be searchable.
    """
    return normalize_persian_text(value or "")


def terms_for(kind: str) -> list[str]:
    """The canonical names for one dimension, for a form's suggestion list."""
    from .models import VocabularyTerm

    return list(
        VocabularyTerm.objects.filter(kind=kind, is_active=True).values_list("name", flat=True)
    )


def vocabulary_context() -> dict[str, list[str]]:
    """Every controlled dimension, for the ``_vocabulary_lists.html`` datalists.

    One query rather than three: the table is small and read together.
    """
    from .models import VocabularyTerm

    grouped: dict[str, list[str]] = {kind.value: [] for kind in VocabularyTerm.Kind}
    for kind, name in (
        VocabularyTerm.objects.filter(is_active=True)
        .order_by("kind", "sort_order", "name")
        .values_list("kind", "name")
    ):
        grouped.setdefault(kind, []).append(name)
    return grouped
