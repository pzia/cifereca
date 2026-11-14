"""#5851/#5975: library worker must never select a mutable GTK identity."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.services import LibraryService
from cifereca.core import library_projection


def _host():
    host = LibraryService.__new__(LibraryService)
    host.current_identity = NS(identity_id="alice")
    effects = []
    def forbidden(*_args, **_kwargs):
        effects.append("io")
        pytest.fail("unscoped Library touched SQLite or filesystem")
    host.catalog_scan = forbidden
    host.storage = NS(
        artifacts=forbidden, placements=forbidden,
        catalog_entries=forbidden, entitlements=forbidden,
    )
    return host, effects


@pytest.mark.parametrize("scope", [None, "", "  ", False, 7, [], {}])
def test_worker_requires_explicit_identity_before_any_storage_or_scan(scope):
    host, effects = _host()
    logs = []
    logger = NS(info=lambda *_args: logs.append("log"))
    assert library_projection.library_entries(
        host, identity_id=scope, scan=True, logger=logger
    ) == []
    assert effects == []
    assert logs == []
    assert host.current_identity.identity_id == "alice"


def test_worker_cannot_be_invoked_without_a_pinned_scope():
    host, effects = _host()
    with pytest.raises(TypeError):
        library_projection.library_entries(host, logger=NS(info=lambda *_: None))
    assert effects == []


def test_legacy_facade_snapshots_identity_before_delegating(monkeypatch):
    host, _effects = _host()
    forwarded = []
    def worker(service, *, identity_id, scan, logger):
        forwarded.append((identity_id, scan))
        service.current_identity = NS(identity_id="bob")
        return [{"identity_id": identity_id}]
    monkeypatch.setattr(library_projection, "library_entries", worker)
    assert host.library_entries(scan=False) == [{"identity_id": "alice"}]
    assert forwarded == [("alice", False)]
    assert host.current_identity.identity_id == "bob"
    assert host.library_entries(identity_id="alice") == [{"identity_id": "alice"}]
    assert forwarded[-1] == ("alice", False)
