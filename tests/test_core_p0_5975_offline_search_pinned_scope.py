"""#5975 / #5851: offline Core search workers cannot infer GTK profile."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.core import search_orchestration
from cifereca.services import LibraryService


@pytest.mark.parametrize("scope", [None, "", " ", "\t", False, 0, [], {}])
@pytest.mark.parametrize("worker", [
    search_orchestration.search_local_library,
    search_orchestration.search_known_catalog,
])
def test_offline_search_worker_rejects_bad_identity_before_private_io(worker, scope):
    def forbidden(*_args, **_kwargs):
        pytest.fail("missing identity used SQLite/library/covers/logs")
    host = NS(
        current_identity=NS(identity_id="alice"),
        library_entries=forbidden, catalog_entries=forbidden,
    )
    logger = NS(info=forbidden)
    assert worker(host, "private title", identity_id=scope, logger=logger) == []
    assert host.current_identity.identity_id == "alice"


@pytest.mark.parametrize("worker", [
    search_orchestration.search_local_library,
    search_orchestration.search_known_catalog,
])
def test_offline_search_worker_requires_keyword_scope(worker):
    with pytest.raises(TypeError):
        worker(NS(), "private title", logger=NS())


@pytest.mark.parametrize("method,worker", [
    ("search_local_library", "search_local_library"),
    ("search_known_catalog", "search_known_catalog"),
])
def test_public_facade_pins_offline_search_identity_before_navigation_change(
    monkeypatch, method, worker,
):
    host = LibraryService.__new__(LibraryService)
    host.current_identity = NS(identity_id="alice")
    snapshots = []

    def captured(service, query, scope, **kwargs):
        assert service is host
        snapshots.append(kwargs["identity_id"])
        host.current_identity = NS(identity_id="bob")
        return [{"identity_id": kwargs["identity_id"]}]

    monkeypatch.setattr(search_orchestration, worker, captured)
    assert getattr(host, method)("private title") == [{"identity_id": "alice"}]
    assert snapshots == ["alice"]
    # A caller's explicit scope, including an invalid one, is never changed
    # to the currently selected GTK identity by the public facade.
    assert getattr(host, method)("private title", identity_id="alice") == [
        {"identity_id": "alice"}
    ]
    assert getattr(host, method)("private title", identity_id="") == [
        {"identity_id": ""}
    ]
    assert snapshots == ["alice", "alice", ""]
