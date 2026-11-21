"""Real BnF parser -> existing federated Search -> exact private C -> click."""
from dataclasses import asdict, replace
from copy import deepcopy

import pytest

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.services import LibraryService
from cifereca.storage import Storage
from cifereca.models import SearchResult
from cifereca.core.notice_c_private_search_web import (
    capture_authenticated_search_call, stamp_authenticated_search_rows,
    persist_authenticated_search_rows, read_authenticated_web_for_search,
)
from cifereca.core.notice_c_authenticated_web_choices import resolve_authenticated_web_choice
from cifereca.core.notice_c_cover_index import read_consolidated_notice_cover
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.search_orchestration import capture_search_read_scope
from test_p0_6397_authenticated_web_proof import active, card, results, ORIGIN


@pytest.fixture
def harness(tmp_path, monkeypatch):
    config = Config(tmp_path / "config.json")
    config.set("library.directory", str(tmp_path / "library"))
    config.set("identities", [{"id": "alice", "label": "Alice"},
                              {"id": "bob", "label": "Bob"}])
    config.set("providers.accounts", [])
    service = LibraryService(config, Storage(tmp_path / "private.sqlite"), Diagnostics())
    session, account, client = active(results(card()))
    service.accounts = [account]
    service._sessions = {service._session_key(account.provider_id, account.credential_id): session}
    monkeypatch.setattr(service, "_provider_enabled_for_identity", lambda p, i=None: True)
    db = service.storage
    connection = db.provider_connection_put({
        "identity_id": "alice", "provider_id": account.provider_id,
        "credential_id": account.credential_id, "enabled": True,
    })
    db.provider_account_put({
        "connection_id": connection, "identity_id": "alice", "provider_id": account.provider_id,
        "credential_id": account.credential_id, "account_id": account.unique_id, "is_primary": True,
    })
    try:
        yield service, session, account, client
    finally:
        service.close()


@pytest.mark.parametrize("mode", ["sync", "progressive", "headless"])
def test_actual_federated_owner_search_admits_only_its_private_reference(harness, mode):
    service, session, account, client = harness
    assert service.current_identity.identity_id == "alice"
    scope = capture_search_read_scope(service, "alice")
    if mode == "sync":
        row, = service.search("fiction", provider_id=session.provider_id, force=True)
    elif mode == "progressive":
        batches = []
        def receive(batch):
            actual, = batch.results
            assert read_authenticated_web_for_search(service, actual, reader_scope=scope).choices
            batches.append(actual)
        summary = service.search_progressive("fiction", provider_id=session.provider_id,
                                             on_batch=receive, force=True)
        assert not summary.cancelled and summary.result_count == 1
        row, = batches
    else:
        summary, rows = service.search_progressive_result_for_scope(
            scope, "fiction", provider_id=session.provider_id, force=True,
        )
        assert not summary.cancelled and summary.result_count == 1
        row, = rows
    assert row.source_account_id == account.unique_id
    menu = read_authenticated_web_for_search(service, row, reader_scope=scope)
    choice, = menu.choices
    url = ORIGIN + "/resources/" + row.record_id
    assert resolve_authenticated_web_choice(service, menu, choice_id=choice.choice_id) == url
    c = read_consolidated_notice_cover(service.storage, menu.document)
    assert len(c.sources) == 1 and c.sources[0].visibility == "private"
    assert c.catalogue_web_references == () and c.candidates == ()
    assert search_public_notice_c(service.storage, "fiction").hits == ()
    # A detached view/cache row cannot republish a fresh observation; it
    # can only read the independently persisted exact source after scope checks.
    cached = SearchResult(**asdict(row))
    assert persist_authenticated_search_rows(service, [cached], identity_id="alice") == 0
    cached.friendly_url = ORIGIN + "/resources/wrong"
    saved = read_authenticated_web_for_search(service, cached, reader_scope=scope)
    assert resolve_authenticated_web_choice(service, saved, choice_id=saved.choices[0].choice_id) == url
    assert read_authenticated_web_for_search(
        service, row, reader_scope=capture_search_read_scope(service, "bob"),
    ) is None
    assert len(client.calls) == 1
    assert service.storage.entitlements() == service.storage.acquisitions() == service.storage.placements() == []


@pytest.mark.parametrize("changed", [
    "duplicate", "copy", "deepcopy", "proof", "row", "scope", "session", "connection",
])
def test_no_private_write_from_forged_or_stale_occurrence(harness, changed):
    service, session, account, client = harness
    call = capture_authenticated_search_call(service, session, "alice")
    assert call is not None
    row, = session.search("fiction")
    rows = [row, replace(row)] if changed == "duplicate" else [row]
    stamp_authenticated_search_rows(service, call, rows)
    if changed == "copy":
        rows = [replace(row)]
    elif changed == "deepcopy":
        rows = [deepcopy(row)]
    elif changed == "proof":
        row.metadata["authenticated_web_evidence"]["source_field"] = "other"
    elif changed == "row":
        row.friendly_url = ORIGIN + "/resources/other"
    elif changed == "scope":
        service._source_context_epoch += 2
    elif changed == "session":
        session.connected = False
    elif changed == "connection":
        service.storage.provider_connection_put({
            "identity_id": "alice", "provider_id": account.provider_id,
            "credential_id": account.credential_id, "enabled": False,
        })
    assert persist_authenticated_search_rows(service, rows, identity_id="alice") == 0
    with service.storage._lock, service.storage._connect() as db:
        assert db.execute("SELECT count(*) FROM metadata WHERE key LIKE 'notice.c.%'").fetchone()[0] == 0
    assert len(client.calls) == 1


def test_private_call_cannot_be_captured_as_foreign_reader(harness):
    service, session, _, client = harness
    assert capture_authenticated_search_call(service, session, "bob") is None
    assert not client.calls
