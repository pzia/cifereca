"""#5851 / #5975: recent-acquisitions Core reads require explicit identity."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.services import LibraryService
from cifereca.core import library_projection


def _read_host():
    host = LibraryService.__new__(LibraryService)
    host.current_identity = NS(identity_id="private-selected-profile")
    calls = []

    def forbidden(*_args, **_kwargs):
        calls.append("storage")
        pytest.fail("unscoped Core projection must not touch storage")

    host.storage = NS(
        catalog_entries=forbidden, entitlements=forbidden,
        placements=forbidden, provider_origin_links=forbidden,
    )
    host.identities = [NS(identity_id="default")]
    return host, calls


@pytest.mark.parametrize("identity_id", [None, "", " ", "\t", False, 1, [], {}])
def test_recent_unscoped_read_never_infers_navigation_or_touches_storage(identity_id):
    host, calls = _read_host()
    assert host.recent_acquisitions(identity_id=identity_id) == []
    assert calls == []


def test_recent_requires_valid_explicit_identity_without_navigational_fallback():
    host, calls = _read_host()
    # The no-argument public ABI remains callable, but not privately scoped.
    assert host.recent_acquisitions() == []
    # Directly invoking the extracted worker likewise cannot infer a GTK
    # identity or use a default provider/account selector.
    assert library_projection.recent_acquisitions(host) == []
    assert host.current_identity.identity_id == "private-selected-profile"
    assert calls == []
