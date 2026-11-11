"""#5054: private-source action events never expose unverified IDs or owners.

The rule is selected ABI / exact private record shape, never a provider name.
No session login, HTTP, historical right, file IO or SQLite is involved.
"""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.models import SearchResult
from cifereca.services import LibraryService


def _item(**changes):
    item = SearchResult(
        id="private-credential-secret", record_id="private-credential-secret",
        docbase="FUTURE", medium="digital", title="Private fixture",
        author=None, type="ebook", format=None, publisher=None,
        published_at=None, description=None, thumbnail_url=None,
        friendly_url=None, scenario="PRIVATE_LIBRARY",
        source_provider="synthetic-store", source_credential="credential-a",
        source_collection="library", download_url=None,
        owned=True, downloadable=True, content_type="ebook",
    )
    return replace(item, **changes)


def _service(session=None, *, result=None, error=True):
    events = []
    owner_calls = []

    def identity_for_connection(*args):
        owner_calls.append(args)
        return NS(identity_id="untrusted-owner")

    def acquire(_item, **_kwargs):
        if error:
            raise RuntimeError("private-endpoint-denied")
        return result

    service = NS(
        _sessions={"synthetic-store:credential-a": session}
                  if session is not None else {},
        _session_key=lambda provider, credential: f"{provider}:{credential}",
        _acquire_direct_item_impl=acquire,
        identity_for_connection=identity_for_connection,
        current_identity=NS(identity_id="current"),
        _record_action=lambda identity, action, state, **kw:
            events.append((identity, action, state, kw)),
    )
    return service, events, owner_calls


@pytest.mark.parametrize("session,item", [
    (None, _item()),
    (NS(selected_purchase_branch="purchases"), _item()),
    (NS(selected_purchase_branch=""), _item(source_collection="public_catalog")),
    (NS(private_inventory_row_contract=("library", "PRIVATE", "credential_scoped_record_v1")),
     _item(source_collection="public_catalog")),
    (NS(selected_purchase_branch=object()), _item(download_url="https://invalid.test/file")),
])
def test_private_failed_action_never_attributed_or_emits_item_id(session, item):
    service, events, owner_calls = _service(session)
    with pytest.raises(RuntimeError, match="private-endpoint-denied"):
        LibraryService.acquire_direct_item(service, item)
    assert owner_calls == []
    assert events == [(None, "acquisition", "failed", {
        "provider_id": "synthetic-store",
        "facts": {"private_item": True, "error": "RuntimeError"},
    })]
    assert "private-credential-secret" not in repr(events)


def test_private_success_can_only_use_identity_from_admitted_result():
    result = NS(metadata={"identity_id": "admitted-identity"})
    service, events, owner_calls = _service(
        NS(selected_purchase_branch="purchases"), result=result, error=False,
    )
    assert LibraryService.acquire_direct_item(service, _item()) is result
    assert owner_calls == []
    assert events == [("admitted-identity", "acquisition", "success", {
        "provider_id": "synthetic-store",
        "facts": {"private_item": True},
    })]


def test_private_result_without_verified_identity_stays_unattributed():
    result = NS(metadata={"identity_id": "ignored"})  # non-dict metadata denied below
    result.metadata = NS(identity_id="untrusted")
    service, events, owner_calls = _service(
        NS(selected_purchase_branch="purchases"), result=result, error=False,
    )
    assert LibraryService.acquire_direct_item(service, _item()) is result
    assert events[0][0] is None
    assert events[0][3]["facts"] == {"private_item": True}
    assert owner_calls == []


def test_public_existing_action_preserves_legacy_audit():
    public = _item(
        source_provider="synthetic-store", source_collection="public_catalog",
        owned=False, download_url="https://invalid.test/edition",
    )
    service, events, owner_calls = _service(None)
    with pytest.raises(RuntimeError):
        LibraryService.acquire_direct_item(service, public)
    assert owner_calls == [("synthetic-store", "credential-a")]
    assert events == [("untrusted-owner", "acquisition", "failed", {
        "provider_id": "synthetic-store",
        "facts": {"record_id": "private-credential-secret", "error": "RuntimeError"},
    })]
