"""#5975 / #5851: Core Library worker cannot infer the GTK identity."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.core import library_projection
from cifereca.services import LibraryService


@pytest.mark.parametrize("identity_id", [None, "", " ", "\t", False, 1, [], {}])
def test_extracted_worker_rejects_invalid_scope_before_db_scan_or_diagnostics(identity_id):
    host = LibraryService.__new__(LibraryService)
    host.current_identity = NS(identity_id="private-selected")
    host.storage = NS(
        artifacts=lambda: pytest.fail("unscoped read touched artifacts"),
        placements=lambda **_kwargs: pytest.fail("unscoped read touched placements"),
        entitlements=lambda: pytest.fail("unscoped read touched entitlements"),
    )
    host.catalog_scan = lambda **_kwargs: pytest.fail("unscoped read started a scan")
    logger = NS(info=lambda *_args: pytest.fail("unscoped read reached logger"))
    assert library_projection.library_entries(
        host, identity_id=identity_id, scan=True, logger=logger,
    ) == []
    assert host.current_identity.identity_id == "private-selected"


def test_extracted_worker_requires_keyword_scope_before_any_side_effect():
    host = LibraryService.__new__(LibraryService)
    with pytest.raises(TypeError):
        library_projection.library_entries(host, logger=NS())


def test_public_facade_pins_legacy_navigation_at_entry_before_work(monkeypatch):
    host = LibraryService.__new__(LibraryService)
    host.current_identity = NS(identity_id="alice")
    calls = []

    def worker(service, *, identity_id, scan, logger):
        assert service is host
        calls.append((identity_id, scan))
        host.current_identity = NS(identity_id="bob")
        return ["alice-only"] if identity_id == "alice" else []

    monkeypatch.setattr(library_projection, "library_entries", worker)
    assert host.library_entries(scan=True) == ["alice-only"]
    assert calls == [("alice", True)]
    assert host.current_identity.identity_id == "bob"


@pytest.mark.parametrize("scope", ["", " ", False, 0, [], {}])
def test_public_facade_never_substitutes_gtk_identity_for_explicit_invalid_scope(
    monkeypatch, scope,
):
    host = LibraryService.__new__(LibraryService)
    host.current_identity = NS(identity_id="private-selected")
    seen = []
    def worker(_host, *, identity_id, scan, logger):
        seen.append(identity_id)
        return []
    monkeypatch.setattr(library_projection, "library_entries", worker)
    assert host.library_entries(identity_id=scope, scan=True) == []
    assert seen == [scope]
