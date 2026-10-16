"""P0 #1862 — a Numilog store library is owned inventory, not a loan/backup.

The real Core/SQLite flow is exercised with an authentic Numilog-shaped
SearchResult. Fake network endpoints fail loudly if any download is requested.
"""
from __future__ import annotations

import logging
import hashlib
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.models import Account, SearchResult
from cifereca.plugin_api import SyncRequest
from tests.test_sync_state_contract import _service


def _row(credential: str, index: int) -> SearchResult:
    scope = hashlib.sha256(credential.encode("utf-8")).hexdigest()[:20]
    variant = hashlib.sha256(f"variant-{index}".encode("utf-8")).hexdigest()[:16]
    record_id = f"numilog:{scope}:lid{index}:{variant}"
    return SearchResult(
        id=record_id, record_id=record_id, docbase="NUMILOG",
        medium="digital", title=f"Achat #{index}", author=None,
        type="Livre numérique", format=None, publisher=None,
        published_at=None, description=None, thumbnail_url=None,
        friendly_url=None, scenario="NUMILOG_LIBRARY",
        source_provider="numilog", source_credential=credential,
        source_collection="library", content_type="ebook",
        owned=True, downloadable=True,
    )


def _connect(service, *, identity="default", credential="numilog-private",
             number=2, failed=False):
    calls = []
    account = Account(
        display_name="Boutique Numilog", unique_id="account",
        provider_id="numilog", credential_id=credential, identity_id=identity,
    )
    service.accounts.append(account)
    service.storage.provider_connection_put({
        "provider_id": "numilog", "credential_id": credential,
        "identity_id": identity, "enabled": True,
    })
    rows = [_row(credential, i) for i in range(number)]

    def library():
        calls.append("library")
        if failed:
            raise RuntimeError(
                "ACSM https://entrepot2r.numilog.com/private?token=SIGNATURE-SECRET "
                "Cookie=SESSION-SECRET"
            )
        # Generator: mirroring must not exhaust this before indexing.
        yield from rows

    def forbidden(*_args, **_kwargs):
        calls.append("FORBIDDEN DOWNLOAD")
        raise AssertionError("No download may be launched from inventory")

    session = NS(
        provider_id="numilog", credential_id=credential, connected=True,
        capabilities=NS(loans=False, personal_library=True, direct_download=True),
        library=library, download_direct=forbidden,
        # Deliberately no download_library_item method: the real Numilog
        # subclass inherits an unsupported one from ProviderSession.
    )
    service._sessions[service._session_key("numilog", credential)] = session
    return account, session, calls


@pytest.mark.parametrize("number", [0, 1, 3])
def test_opening_inventory_handles_zero_one_or_many_purchases_without_acquisition(
    tmp_path, monkeypatch, number,
):
    service = _service(tmp_path)
    try:
        _account, _session, calls = _connect(service, number=number)
        monkeypatch.setattr(
            service, "_acquire_personal_library_item",
            lambda *_args: pytest.fail("legacy backup path must never run"),
        )
        # Current UI selection is unrelated to the store account provenance.
        service.current_identity = service.identity_by_id("alex")
        first = service.mirror_personal_libraries()
        second = service.mirror_personal_libraries()
        assert first == {"numilog:numilog-private": number}
        assert second == first
        assert first.failed_sources == first.blocked_sources == 0
        assert calls == ["library", "library"]
        actual = [
            r for r in service.storage.catalog_entries()
            if r.get("provider_id") == "numilog"
        ]
        assert len(actual) == number
        assert all(r["identity_id"] == "default" and
                   r["local_state"] == "remote_owned" for r in actual)
        assert list((tmp_path / "EBooks").rglob("*.epub")) == []
        assert list((tmp_path / "EBooks").rglob("*.acsm")) == []
    finally:
        service.close()


def test_manual_source_inventory_sync_does_not_use_backup_or_direct_download(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        _account, _session, calls = _connect(service)
        broker = SynchronizationBrokerImpl(service)
        source = next(
            row for row in broker.sources("default")
            if row.provider_id == "numilog"
        )
        assert "remote_inventory" in source.sync_capabilities
        assert "local_materialization" not in source.sync_capabilities
        monkeypatch.setattr(
            service, "_acquire_personal_library_item",
            lambda *_args: pytest.fail("legacy backup"),
        )
        result = broker.synchronize_source(source.source_id)
        assert result.ok and result.changed == 4  # two notices and two attested ownership rights
        assert result.facts["library"] == 2
        assert "acquired" not in result.facts
        batch = broker.synchronize(SyncRequest(identity_id="default"))
        assert batch.ok and batch.changed == 4  # two notices and two attested ownership rights
        assert calls == ["library", "library"]
    finally:
        service.close()


def test_disabled_source_skips_remote_inventory_and_every_download(tmp_path):
    service = _service(tmp_path)
    try:
        _account, _session, calls = _connect(service)
        broker = SynchronizationBrokerImpl(service)
        source = next(
            row for row in broker.sources("default")
            if row.provider_id == "numilog"
        )
        broker.set_enabled(source.source_id, False)
        assert service.mirror_personal_libraries() == {}
        result = broker.synchronize_source(source.source_id)
        assert result.ok and result.changed == 0
        assert result.facts["skipped"] is True
        assert calls == []
    finally:
        service.close()


@pytest.mark.parametrize("rebind", ["other_identity", "missing_account", "ambiguous"])
def test_rebound_or_ambiguous_store_cannot_mirror_into_selected_identity(
    tmp_path, rebind,
):
    service = _service(tmp_path)
    try:
        account, _session, calls = _connect(service)
        if rebind == "other_identity":
            service.storage.provider_connection_put({
                "provider_id": "numilog", "credential_id": account.credential_id,
                "identity_id": "alex", "enabled": True,
            })
        elif rebind == "missing_account":
            service.accounts.clear()
        else:
            service.accounts.append(Account(
                display_name="Second", unique_id="second",
                provider_id="numilog", credential_id=account.credential_id,
                identity_id="alex",
            ))
        service.current_identity = service.identity_by_id("alex")
        result = service.mirror_personal_libraries()
        assert result == {}
        assert result.blocked_sources == 1
        assert result.failed_sources == 0
        assert calls == []
        assert not service.storage.catalog_entries()
    finally:
        service.close()


def test_deprecated_backup_helper_fails_closed_with_no_provider_call(tmp_path):
    service = _service(tmp_path)
    try:
        _account, session, calls = _connect(service, number=1)
        blocked = service._acquire_personal_library_item(
            session, _row("numilog-private", 0), "default",
        )
        assert blocked == {
            "status": "blocked", "reason": "explicit_acquisition_required",
        }
        assert calls == []
    finally:
        service.close()


def _opening_dependencies(service, monkeypatch):
    monkeypatch.setattr(service, "_kobo_adapter", lambda: NS(available=lambda: False))
    monkeypatch.setattr(service, "_loans_for_account", lambda *_args, **_kw: [])
    monkeypatch.setattr(service, "_reconcile_identity_adept_inventory",
                        lambda _identity: {"status": "skipped"})
    monkeypatch.setattr(
        service, "_acquire_personal_library_item",
        lambda *_args: pytest.fail("automatic backup never authorized"),
    )


def test_real_opening_reports_inventory_only_and_explicit_zero_acquisitions(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        _account, _session, calls = _connect(service, number=2)
        _opening_dependencies(service, monkeypatch)
        events = []
        result = service.opening_sync(progress=events.append)
        assert calls == ["library"]
        mirror = result["catalog_mirror"]
        assert mirror == {
            "status": "inventory_only", "notices_mirrored": 2,
            "sources_reported": 1, "sources_failed": 0,
            "sources_blocked": 0,
            "materialization": {
                "status": "not_requested", "complete": 0, "unchanged": 0,
                "skipped": 0, "blocked": 0, "failed": 0,
                "attention_required": 0,
            },
        }
        assert [e["result"] for e in events if e.get("event") == "catalog_mirror"] == [mirror]
        assert all(row["new_acquisitions"] == 0 for row in result["identities"])
        assert not any(e.get("event") == "downloaded" for e in events)
    finally:
        service.close()


def test_real_opening_reports_failed_inventory_without_raw_secrets(
    tmp_path, monkeypatch, caplog,
):
    service = _service(tmp_path)
    try:
        _account, _session, calls = _connect(service, failed=True)
        _opening_dependencies(service, monkeypatch)
        with caplog.at_level(logging.WARNING):
            result = service.opening_sync()
        assert calls == ["library"]
        mirror = result["catalog_mirror"]
        assert mirror["status"] == "failed"
        assert mirror["sources_failed"] == 1
        assert mirror["notices_mirrored"] == 0
        assert mirror["materialization"]["status"] == "not_requested"
        rendered = str(mirror) + caplog.text
        assert "SIGNATURE-SECRET" not in rendered
        assert "SESSION-SECRET" not in rendered
        assert "entrepot2r" not in rendered
        assert "numilog-private" not in rendered
        assert not service.storage.catalog_entries()
    finally:
        service.close()


def test_one_store_error_does_not_hide_other_store_success(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        _a, _s, first = _connect(service, credential="numilog-a", number=1)
        _b, _s2, second = _connect(
            service, identity="alex", credential="numilog-b", failed=True,
        )
        _opening_dependencies(service, monkeypatch)
        result = service.opening_sync()
        summary = result["catalog_mirror"]
        assert summary["status"] == "partial"
        assert summary["notices_mirrored"] == 1
        assert summary["sources_reported"] == 1
        assert summary["sources_failed"] == 1
        assert first == ["library"] and second == ["library"]
        assert len(service.storage.catalog_entries()) == 1
    finally:
        service.close()
