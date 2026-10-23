"""Ephemeral, non-authorizing selection of a *visible loan's book page*.

A loan link is never opened here: only the already observed *public resource*
identifier can be returned on an explicit UI choice. No download, return, rights
ledger, account proof, network or persistence. IDs remain private in memory.
"""
from __future__ import annotations

import re

from cifereca_bnf_pret_web import LoanSnapshot, LoanRecord, ProfileScope, WebParseError, MAX_ITEMS

_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


class VisibleLoanBookSelection:
    def __init__(self, scope: ProfileScope, *, maximum: int = 12):
        if not isinstance(scope, ProfileScope):
            raise WebParseError("invalid_scope")
        if type(maximum) is not int or not 1 <= maximum <= 12:
            raise WebParseError("invalid_selection_limit")
        self._scope = scope
        self._maximum = maximum
        self._resources = ()
        self._revision = 0
        self._closed = False

    def __repr__(self):
        return "VisibleLoanBookSelection(closed)" if self._closed else "VisibleLoanBookSelection(active)"

    @property
    def revision(self):
        return self._revision

    @property
    def count(self):
        return len(self._resources) if not self._closed else 0

    def clear(self):
        self._revision += 1
        self._resources = ()

    def close(self):
        self.clear()
        self._closed = True

    def observe(self, snapshot: LoanSnapshot, *, expected: ProfileScope) -> int:
        """Replace the last page. An invalid/empty page revokes old choices."""
        self.clear()
        if self._closed:
            raise WebParseError("session_closed")
        if not isinstance(expected, ProfileScope) or expected != self._scope:
            raise WebParseError("scope_mismatch")
        if (not isinstance(snapshot, LoanSnapshot) or snapshot.scope != self._scope
                or snapshot.exhaustive is not False
                or snapshot.status not in {"observed", "partial"}
                or type(snapshot.loans) not in (tuple, list)
                or len(snapshot.loans) > MAX_ITEMS):
            raise WebParseError("invalid_observation")
        found = []
        seen_loans = set()
        for loan in snapshot.loans:
            if (not isinstance(loan, LoanRecord)
                    or type(loan.loan_id) is not str or type(loan.resource_id) is not str
                    or not _ID.fullmatch(loan.loan_id)
                    or not _ID.fullmatch(loan.resource_id)):
                raise WebParseError("invalid_observation")
            if loan.loan_id in seen_loans:
                raise WebParseError("ambiguous_loans")
            seen_loans.add(loan.loan_id)
            if len(found) < self._maximum:
                found.append(loan.resource_id)
        self._resources = tuple(found)
        return len(found)

    def visible_book_lines(self) -> tuple[str, ...]:
        """Local UI labels in the same order as choose; never show loan IDs."""
        if self._closed:
            return ()
        return tuple(f"{index}. Livre BnF [{rid}]"
                     for index, rid in enumerate(self._resources, 1))

    def choose(self, index: int, *, expected: ProfileScope, revision: int) -> str:
        """Only expose a public book route id from the last validated page."""
        if self._closed:
            raise WebParseError("session_closed")
        if not isinstance(expected, ProfileScope) or expected != self._scope:
            raise WebParseError("scope_mismatch")
        if type(revision) is not int or revision != self._revision:
            raise WebParseError("selection_expired")
        if type(index) is not int or not 0 <= index < len(self._resources):
            raise WebParseError("invalid_selection")
        return self._resources[index]
