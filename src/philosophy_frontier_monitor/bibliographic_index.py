"""Conservative candidate index for OpenAlex identity comparisons.

This only selects possible comparisons. The existing bibliographic comparator
still decides whether two works are equivalent or require review.
"""

from __future__ import annotations

from collections.abc import Iterator

from .identity import identity_lookup_keys
from .sources.openalex import OpenAlexWork


class OpenAlexIdentityIndex:
    """Index every route that can satisfy the current equivalence rule.

    Exact folded title and shared DOI can match without comparable authors. A
    non-exact title needs compatible first authors, which implies either identical
    name-token sets or equal parsed surnames under ``authors_equivalent``.
    """

    def __init__(self, works: tuple[OpenAlexWork, ...]) -> None:
        self.works = works
        self.by_doi: dict[str, set[int]] = {}
        self.by_title: dict[str, set[int]] = {}
        self.by_surname: dict[str, set[int]] = {}
        self.by_author_tokens: dict[frozenset[str], set[int]] = {}
        for index, work in enumerate(works):
            title, tokens, surname = identity_lookup_keys(
                work.title, work.authors[0] if work.authors else None
            )
            if work.doi:
                self.by_doi.setdefault(work.doi, set()).add(index)
            self.by_title.setdefault(title, set()).add(index)
            if tokens and surname:
                self.by_surname.setdefault(surname, set()).add(index)
                self.by_author_tokens.setdefault(tokens, set()).add(index)

    def possible(
        self, title: str, first_author: str | None, dois: tuple[str, ...] = ()
    ) -> Iterator[OpenAlexWork]:
        title_key, tokens, surname = identity_lookup_keys(title, first_author)
        possible = set(self.by_title.get(title_key, ()))
        for doi in dois:
            possible.update(self.by_doi.get(doi, ()))
        if tokens and surname:
            possible.update(self.by_surname.get(surname, ()))
            possible.update(self.by_author_tokens.get(tokens, ()))
        for index in sorted(possible):
            yield self.works[index]
