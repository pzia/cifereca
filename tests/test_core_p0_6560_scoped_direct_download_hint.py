"""Passive acquisition affordances are independent of the active client.

No provider I/O, acquisition or rights mutation is required to test these.
"""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core.search_orchestration import capture_search_read_scope
from cifereca.services import LibraryService
from tests.test_core_p0_6560_scoped_holdings_read import Core, row


def hint(core, scope, item):
    return LibraryService.can_direct_download_for_scope(core, scope, item)


def install(core, credential):
    session = NS(capabilities=NS(direct_download=True))
    core._sessions["synthetic", credential] = session
    return session


def test_two_headless_readers_get_only_their_exact_offer_hints():
    core = Core()
    a, b = (capture_search_read_scope(core, key) for key in ("alice", "bob"))
    install(core, "card-a")
    install(core, "card-b")
    book_a, book_b = row(), row("card-b", "account-b")
    assert hint(core, a, book_a) is True
    assert hint(core, b, book_b) is True
    assert hint(core, b, book_a) is False
    assert hint(core, a, book_b) is False
    assert hint(core, a, replace(book_b, source_collection="public", owned=False)) is False
    assert hint(core, a, replace(book_a, metadata={"catalogue_access": "shared_read_only"})) is False
    assert hint(core, a, replace(book_a, metadata={"search_provenance": "known_catalog_stale"})) is False
    assert hint(core, None, book_a) is False
    assert core.storage.cache == {}


def test_public_offer_obeys_admitted_readers_policy():
    core = Core()
    a, b = (capture_search_read_scope(core, key) for key in ("alice", "bob"))
    session = install(core, "__public__")
    session.exact_catalogue_download_offer_required = True
    item = replace(row("__public__"), source_collection="public", owned=False,
                   downloadable=True, download_url="https://synthetic.invalid/book.epub")
    core.disabled.add("bob")
    assert hint(core, a, item) is True
    assert hint(core, b, item) is False
    assert hint(core, a, replace(item, download_url=None)) is False
    session.legacy_direct_get_retired = True
    assert hint(core, a, item) is False


@pytest.mark.parametrize("revoke", ["epoch", "identity", "owner", "policy"])
def test_revocation_during_capability_read_never_returns_positive_hint(revoke):
    core = Core()
    scope = capture_search_read_scope(core, "alice")
    session = install(core, "card-a")

    class Capability:
        @property
        def direct_download(self):
            if revoke == "epoch":
                core._source_context_epoch += 1
            elif revoke == "identity":
                core.identities["alice"] = NS(identity_id="alice")
            elif revoke == "owner":
                core.storage.owners["card-a"] = "bob"
            else:
                core.disabled.add("alice")
            return True

    session.capabilities = Capability()
    assert hint(core, scope, row()) is False
    assert hint(core, scope, row()) is False
