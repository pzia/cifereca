"""P0 #3196 D3: typed Core source forbids a second legacy direct Kobo GET.

Real SQLite and LibraryService, synthetic connected Kobo session, no supplier IO.
A source may precede a complete provider ABI or arrive during admission.
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from cifereca import services
from cifereca.providers import ProviderConfigurationError
from tests.test_kobo_backup_guard import _manifest_row, _service


def _source(service, *, edition="book-1", account="account", identity="alice"):
    with service.storage._lock, service.storage._connect() as db:
        db.execute(
            "INSERT INTO source_references("
            "source_reference_id,identity_id,provider_id,credential_id,"
            "account_id,branch_id,edition_id,right_id,reference_kind,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("source:" + edition, identity, "kobo", "vault-1",
             account, "purchases", edition, "purchase", "purchase", 1, 1),
        )


@pytest.mark.parametrize("account,identity", [
    ("account", "alice"),
    ("other-borrower", "alice"),
    ("other-borrower", "bob"),
])
def test_typed_source_blocks_partial_abi_direct_action_and_get(
    tmp_path, monkeypatch, account, identity,
):
    service = _service(tmp_path)
    item = service._sessions[service._session_key("kobo", "vault-1")].normalize_item(
        _manifest_row()
    )
    lock_entries = []

    @contextmanager
    def lock(_path):
        lock_entries.append("locked")
        yield

    try:
        _source(service, account=account, identity=identity)
        monkeypatch.setattr(service, "kobo_personal_right_states",
                            lambda _identity: {("vault-1", "book-1"): "active"})
        monkeypatch.setattr(service, "kobo_adept_state",
                            lambda **_kw: pytest.fail("no runtime probe"))
        monkeypatch.setattr(services, "InterProcessLock", lock)
        monkeypatch.setattr(service, "_execute_kobo_acquisition",
                            lambda *_a, **_kw: pytest.fail("duplicate Kobo GET"))
        before = (service.storage.backup_list(), service.storage.placements(),
                  service.storage.acquisitions(provider_id="kobo"))
        assert service.can_direct_download(item) is False
        with pytest.raises(ProviderConfigurationError,
                           match="kobo_canonical_source_delivery_active"):
            service._acquire_direct_item_impl(item)
        assert lock_entries == ["locked"]
        assert (service.storage.backup_list(), service.storage.placements(),
                service.storage.acquisitions(provider_id="kobo")) == before
    finally:
        service.close()


@pytest.mark.parametrize("arrival", ["lock_entry", "admission"])
def test_typed_source_arriving_during_admission_blocks_legacy_get(
    tmp_path, monkeypatch, arrival,
):
    service = _service(tmp_path)
    item = service._sessions[service._session_key("kobo", "vault-1")].normalize_item(
        _manifest_row()
    )
    events = []

    @contextmanager
    def lock(_path):
        events.append("locked")
        if arrival == "lock_entry":
            _source(service)
        yield

    def admission():
        events.append("admission")
        if arrival == "admission":
            _source(service)
        return item

    try:
        monkeypatch.setattr(services, "InterProcessLock", lock)
        monkeypatch.setattr(service, "_execute_kobo_acquisition",
                            lambda *_a, **_kw: pytest.fail("duplicate Kobo GET"))
        with pytest.raises(ProviderConfigurationError,
                           match="kobo_canonical_source_delivery_active"):
            service._acquire_direct_item_impl(item, _admission=admission)
        assert events == ["locked", "admission"]
        assert service.storage.backup_list() == []
        assert service.storage.acquisitions(provider_id="kobo") == []
    finally:
        service.close()


def test_other_edition_source_preserves_legacy_get_with_partial_abi(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    item = service._sessions[service._session_key("kobo", "vault-1")].normalize_item(
        _manifest_row()
    )
    downloaded = []

    @contextmanager
    def lock(_path):
        yield

    try:
        _source(service, edition="other-edition")
        monkeypatch.setattr(services, "InterProcessLock", lock)
        monkeypatch.setattr(service, "_execute_kobo_acquisition",
                            lambda got, _target: downloaded.append(got.record_id)
                            or SimpleNamespace(path=tmp_path / "synthetic.epub", metadata={}))
        service._acquire_direct_item_impl(item)
        assert downloaded == ["book-1"]
    finally:
        service.close()
