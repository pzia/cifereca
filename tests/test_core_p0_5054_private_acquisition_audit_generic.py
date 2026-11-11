"""#5054: private acquisition audit never publishes provider record identifiers."""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.models import SearchResult
from cifereca.services import LibraryService
from tests.test_core_p0_1862_numilog_store_mirror import _row


def _service(item, *, session=None):
    events = []
    def forbidden(*_a, **_kw):
        pytest.fail("private audit must not resolve untrusted connection owner")
    service = NS(
        _sessions={
            f"{item.source_provider}:{item.source_credential}": session
        } if session is not None else {},
        _session_key=lambda p, c: f"{p}:{c}",
        identity_for_connection=forbidden,
        current_identity=NS(identity_id="alice"),
        _record_action=lambda *a, **kw: events.append((a, kw)),
        _acquire_direct_item_impl=lambda _item, **_kw: (
            (_ for _ in ()).throw(ValueError("private acquisition denied"))
        ),
    )
    return service, events


@pytest.mark.parametrize("session,changes", [
    (None, {}),
    (NS(selected_purchase_branch="selected"), {"source_collection": "public_catalog"}),
    (NS(private_inventory_row_contract=()), {"source_collection": "public_catalog"}),
])
def test_private_record_and_selected_abi_never_leak_record_id(session, changes):
    row = replace(
        _row("credential-A", 1),
        source_provider="future-store", **changes,
    )
    service, events = _service(row, session=session)
    with pytest.raises(ValueError, match="private acquisition denied"):
        LibraryService.acquire_direct_item(service, row)
    assert len(events) == 1
    args, kw = events[0]
    assert args[:3] == (None, "acquisition", "failed")
    assert kw["provider_id"] == "future-store"
    assert kw["facts"] == {"private_item": True, "error": "ValueError"}
    assert row.record_id not in str(events)


def test_legacy_public_offer_keeps_existing_record_audit():
    row = replace(
        _row("credential-A", 2),
        source_provider="public-fixture",
        source_collection="public_catalog", owned=False,
        download_url="https://example.invalid/item.epub",
    )
    events = []
    service = NS(
        _sessions={},
        _session_key=lambda p, c: f"{p}:{c}",
        identity_for_connection=lambda *_: NS(identity_id="alice"),
        current_identity=NS(identity_id="alice"),
        _record_action=lambda *a, **kw: events.append((a, kw)),
        _acquire_direct_item_impl=lambda _item, **_kw: NS(metadata={}),
    )
    LibraryService.acquire_direct_item(service, row)
    assert events[0][0][:3] == ("alice", "acquisition", "success")
    assert events[0][1]["facts"] == {"record_id": row.record_id}
