"""Core P0 #1346: source inventory sync must never materialize local Numilog EPUBs.

Exercise both real Core orchestration and the public generic broker; no GTK,
provider network, actual download, ADEPT or on-disk book file is required.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core import sync_coordination
from cifereca.models import Account
from cifereca.plugin_api import SyncRequest
from tests.test_sync_state_contract import _service


def _account(identity, credential):
    return Account(
        display_name="Numilog personnel", unique_id="numilog-account",
        provider_id="numilog", credential_id=credential, identity_id=identity,
    )


def _session(credential, record, calls):
    def library():
        calls.append(("manifest", credential))
        # Deliberately one-shot to catch double fetching during sync.
        yield record

    return NS(
        provider_id="numilog", credential_id=credential, connected=True,
        capabilities=NS(loans=False, personal_library=True, direct_download=True),
        library=library,
    )


def _install(service, monkeypatch, tmp_path, identities=("default",)):
    monkeypatch.setattr(
        sync_coordination, "acquisition_lock_file",
        lambda: tmp_path / "source-metadata-only.lock",
    )
    calls = []
    book = NS(record_id="PRIVATE_NUMILOG_BOOK", title="Titre privé")
    accounts = [_account(identity, f"private-{identity}") for identity in identities]
    service.accounts = accounts
    for account in accounts:
        service.storage.provider_connection_put({
            "provider_id": "numilog", "credential_id": account.credential_id,
            "identity_id": account.identity_id, "enabled": True,
        })
        service._sessions[service._session_key(account.provider_id, account.credential_id)] = (
            _session(account.credential_id, book, calls)
        )
    def mirror(rows, *, identity_id):
        calls.append(("mirror", identity_id, len(rows)))
        assert rows == [book]
        return len(rows)

    def forbidden_acquire(*args, **kwargs):
        pytest.fail("a generic source inventory sync may not acquire an EPUB")

    monkeypatch.setattr(service, "_mirror_remote_results", mirror)
    monkeypatch.setattr(service, "_acquire_personal_library_item", forbidden_acquire)
    return accounts, calls


@pytest.mark.parametrize("deep", [False, True])
def test_direct_numilog_account_sync_only_mirrors_remote_inventory(
    tmp_path, monkeypatch, deep,
):
    service = _service(tmp_path)
    try:
        accounts, calls = _install(service, monkeypatch, tmp_path)
        if deep:
            monkeypatch.setattr(
                service, "catalog_scan",
                lambda: {"filesystem_scan": "requested"},
            )
        result = service.synchronize_account_source(accounts[0], deep=deep)
        assert result["provider"] == "numilog"
        assert result["library"] == 1
        assert "acquired" not in result
        assert calls == [
            ("manifest", "private-default"),
            ("mirror", "default", 1),
        ]
        if deep:
            assert result["catalog_scan"] == {"filesystem_scan": "requested"}
        else:
            assert "catalog_scan" not in result
    finally:
        service.close()


def test_public_broker_sync_and_repeat_do_not_download_numilog(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        _accounts, calls = _install(service, monkeypatch, tmp_path)
        broker = SynchronizationBrokerImpl(service)
        target = broker.sources("default")[0]
        assert target.sync_capable is True
        assert target.connected is True
        assert "remote_inventory" in target.sync_capabilities
        assert "local_materialization" not in target.sync_capabilities
        for _ in range(2):
            result = broker.synchronize_source(target.source_id)
            assert result.ok, repr(result)
            assert result.changed == 1
            assert result.facts["library"] == 1
            assert "acquired" not in result.facts
        assert calls == [
            ("manifest", "private-default"), ("mirror", "default", 1),
            ("manifest", "private-default"), ("mirror", "default", 1),
        ]
        assert "PRIVATE_NUMILOG_BOOK" not in repr(result)
        assert "private-default" not in repr(result)
    finally:
        service.close()


def test_generic_batch_is_scoped_to_identity_and_mirrors_without_acquisition(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        _accounts, calls = _install(
            service, monkeypatch, tmp_path, identities=("default", "alex"),
        )
        broker = SynchronizationBrokerImpl(service)
        result = broker.synchronize(SyncRequest(identity_id="alex"))
        assert result.ok, repr(result)
        assert result.changed == 1
        assert calls == [
            ("manifest", "private-alex"), ("mirror", "alex", 1),
        ]
        assert "private-alex" not in repr(result)
        assert "private-default" not in repr(result)
    finally:
        service.close()


def test_disabled_numilog_source_never_fetches_or_acquires(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        _accounts, calls = _install(service, monkeypatch, tmp_path)
        broker = SynchronizationBrokerImpl(service)
        target = broker.sources("default")[0]
        broker.set_enabled(target.source_id, False)
        result = broker.synchronize_source(target.source_id)
        assert result.ok and result.facts["skipped"] is True
        assert result.changed == 0
        assert calls == []
    finally:
        service.close()


def test_source_sync_and_opening_mirror_never_call_legacy_numilog_backup():
    """P0 #1862: neither source sync nor opening inventory is bulk backup."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    source = (root / "src/cifereca/core/sync_coordination.py").read_text(encoding="utf-8")
    first = source.split("def _synchronize_account_source_impl(", 1)[1]
    account_impl = first.split("\ndef synchronize_account_source(", 1)[0]
    assert "_acquire_personal_library_item" not in account_impl
    code = (root / "src/cifereca/services.py").read_text(encoding="utf-8")
    mirror = code.split("def mirror_personal_libraries(", 1)[1]
    mirror = mirror.split("\n    def _acquire_personal_library_item(", 1)[0]
    assert "_acquire_personal_library_item" not in mirror
    assert "session.download_library_item(" not in mirror
