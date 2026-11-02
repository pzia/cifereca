"""P0 #3252/#3171: official Numilog MesLivres coverage is complete by default.

Only the authenticated, bounded and fully traversed private page chain may
attest inventory coverage. This never proves a downloaded EPUB, negative
revocation, delivery offer, or source consent.
"""
from __future__ import annotations

import pytest

import cifereca_numilog as provider
from tests.test_p1_3252_rights_observation_snapshot import (
    bound_account, owned_row, session,
)


def _pages(monkeypatch, session_obj, pages):
    urls = [session_obj.library_url] + [
        session_obj.library_url + f"?page={i}" for i in range(2, len(pages) + 1)
    ]
    calls = []

    def page(url, **_kwargs):
        calls.append(url)
        assert url in urls
        return url, str(urls.index(url))

    def parse(html, url, **kwargs):
        index = int(html)
        assert url == urls[index]
        diagnostics = kwargs.get("inventory_diagnostics")
        if diagnostics is not None:
            diagnostics["complete"] = True  # Synthetic parser saw all cards.
        return list(pages[index]), urls[index + 1] if index + 1 < len(urls) else None

    session_obj._page = page
    monkeypatch.setattr(provider, "parse_library_page", parse)
    return calls, urls


@pytest.mark.parametrize("total", [0, 1, 6])
def test_official_complete_meslivres_page_chain_attests_its_scope(monkeypatch, total):
    s, _ = session()
    try:
        rows = [{**owned_row(), "lid": str(100 + i)} for i in range(total)]
        pages = [rows[:3], rows[3:]] if total >= 6 else [rows]
        calls, urls = _pages(monkeypatch, s, pages)
        snapshot = s.rights_observation_snapshot(bound_account(s))
        assert calls == urls
        assert len(snapshot.rights) == total
        assert snapshot.status == "success"
        assert snapshot.coverage == "complete"
        assert snapshot.pagination_complete is True
        assert snapshot.claims_complete_branch is True
        assert snapshot.authorizes_negative_reconciliation is False
        assert snapshot.authorizes_mutation is False
        legacy = s.library()  # Legacy projection remains a list, not a page tuple.
        assert type(legacy) is list and len(legacy) == total
        assert len(calls) == 2 * len(urls)
    finally:
        s.close()


def test_duplicate_meslivres_card_keeps_positive_siblings_but_partial_coverage(monkeypatch):
    s, _ = session()
    try:
        first = {**owned_row(), "lid": "101"}
        sibling = {**owned_row(), "lid": "102"}
        pages = [[first, sibling], [first, {**owned_row(), "lid": "103"}]]
        calls, urls = _pages(monkeypatch, s, pages)
        snapshot = s.rights_observation_snapshot(bound_account(s))
        assert calls == urls
        assert {r.edition_id for r in snapshot.rights} == {
            s._id(sibling), s._id(pages[1][1]),
        }
        assert snapshot.status == snapshot.coverage == "partial"
        assert snapshot.pagination_complete is False
        assert not snapshot.claims_complete_branch
        assert not snapshot.authorizes_negative_reconciliation
    finally:
        s.close()


def test_malformed_meslivres_row_does_not_attest_exhaustiveness(monkeypatch):
    s, _ = session()
    try:
        malformed = {**owned_row(), "lid": "invalid", "variant": None}
        calls, urls = _pages(monkeypatch, s, [[owned_row(), malformed]])
        snapshot = s.rights_observation_snapshot(bound_account(s))
        assert calls == urls
        assert len(snapshot.rights) == 1
        assert snapshot.status == "partial"
        assert snapshot.coverage == "partial"
        assert snapshot.pagination_complete is False
        assert not snapshot.authorizes_negative_reconciliation
    finally:
        s.close()
