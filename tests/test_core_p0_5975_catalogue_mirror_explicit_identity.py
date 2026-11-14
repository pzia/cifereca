"""#5975 / #5851: a catalog mirror write never infers the GTK-selected identity."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.core import search_orchestration
from cifereca.services import LibraryService


class _NoIOStorage:
    def __getattr__(self, name):
        pytest.fail("unscoped catalog mirror reached storage: " + name)


@pytest.mark.parametrize("scope", [None, "", " ", "\t", False, 0, [], {}])
def test_catalog_mirror_worker_rejects_implicit_gtk_identity_without_io(scope):
    # A previously selected private profile is not the admission authority
    # for a delayed provider result, even when the result list is empty.
    host = NS(current_identity=NS(identity_id="alice"), storage=_NoIOStorage())
    assert search_orchestration._mirror_remote_results(
        host, [], identity_id=scope,
    ) == 0
    assert host.current_identity.identity_id == "alice"


def test_catalog_mirror_facade_requires_explicit_identity_for_sqlite_mutation():
    host = LibraryService.__new__(LibraryService)
    host.current_identity = NS(identity_id="alice")
    host.storage = _NoIOStorage()
    assert host._mirror_remote_results([]) == 0
    assert host._mirror_remote_results([], identity_id="") == 0
    assert host._mirror_remote_results([], identity_id=False) == 0
