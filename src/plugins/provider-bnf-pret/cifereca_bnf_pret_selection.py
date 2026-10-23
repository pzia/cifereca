"""Ephemeral selection of BnF *observed* catalog links (#2055).

The chooser never manufactures a resource route from a title, search term,
credential or other account. It stores only validated visible resource IDs
inside one immutable reader identity/account/epoch. Nothing is persisted.
"""
from __future__ import annotations

import re

from cifereca_bnf_pret_web import CatalogPage, CatalogRecord, ProfileScope, WebParseError


_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


class VisibleResourceSelection:
    def __init__(self, scope: ProfileScope, *, maximum=12):
        if not isinstance(scope, ProfileScope):
            raise WebParseError("invalid_scope")
        if type(maximum) is not int or not 1 <= maximum <= 12:
            raise WebParseError("invalid_selection_limit")
        self._scope = scope
        self._maximum = maximum
        self._resource_ids = ()
        self._revision = 0
        self._closed = False

    def __repr__(self):
        return "VisibleResourceSelection(state='closed')" if self._closed else "VisibleResourceSelection(state='active')"

    @property
    def revision(self):
        return self._revision

    def clear(self):
        self._revision += 1
        self._resource_ids = ()

    def close(self):
        self.clear()
        self._closed = True

    def observe(self, page: CatalogPage, *, expected: ProfileScope):
        """Replace previous choices atomically; return displayed item count."""
        self.clear()  # stale choices become invalid even if the new read fails
        if self._closed:
            raise WebParseError("session_closed")
        if not isinstance(expected, ProfileScope) or expected != self._scope:
            raise WebParseError("scope_mismatch")
        if (not isinstance(page, CatalogPage) or page.scope != "suggestions"
                or page.exhaustive is not False or page.status not in {"ok", "partial"}
                or type(page.records) not in (tuple, list)
                or len(page.records) > 100):
            raise WebParseError("invalid_observation")
        ids = []
        seen = set()
        for record in page.records[:self._maximum]:
            if not isinstance(record, CatalogRecord) or type(record.resource_id) is not str:
                raise WebParseError("invalid_resource")
            rid = record.resource_id
            if not _ID.fullmatch(rid):
                raise WebParseError("invalid_resource")
            if rid in seen:
                raise WebParseError("ambiguous_resource")
            seen.add(rid)
            ids.append(rid)
        self._resource_ids = tuple(ids)
        return len(ids)

    def choose(self, index: int, *, expected: ProfileScope, revision: int):
        """Only explicit selection of a currently displayed, observed ID."""
        if self._closed:
            raise WebParseError("session_closed")
        if not isinstance(expected, ProfileScope) or expected != self._scope:
            raise WebParseError("scope_mismatch")
        if type(revision) is not int or revision != self._revision:
            raise WebParseError("selection_expired")
        if type(index) is not int or index < 0 or index >= len(self._resource_ids):
            raise WebParseError("invalid_selection")
        return self._resource_ids[index]
