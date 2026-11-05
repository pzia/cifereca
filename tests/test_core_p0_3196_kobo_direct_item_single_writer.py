"""#3196: Kobo search/Library direct GET must not bypass Core A->B.

Real LibraryService/SQLite with a connected fake Kobo session, no network.
The configured Core delivery ABI is an executor dispatch boundary, never a
purchase proof or permission inferred from a provider's method names.
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from cifereca import services
from cifereca.providers import ProviderConfigurationError
from tests.test_kobo_backup_guard import _manifest_row, _service


CONTRACTS = (
    (
        "downloadable_resource_snapshot",
        "downloadable_selected_offer_snapshot",
        "downloadable_selected_delivery_candidate",
    ),
    (
        "downloadable_resource_snapshot",
        "downloadable_selected_offer_snapshot",
        "downloadable_selected_delivery_candidate_guarded",
    ),
    (
        "downloadable_resource_snapshot",
        "downloadable_selected_offer_snapshot",
        "downloadable_selected_delivery_candidate_runtime_guarded",
    ),
    (
        "rights_observation_snapshot",
        "rights_selected_delivery_offer_snapshot",
        "rights_selected_delivery_candidate",
    ),
)


def _abi(session, monkeypatch, contract):
    for name in contract:
        monkeypatch.setattr(
            session, name,
            lambda *_args, **_kwargs: pytest.fail(
                "legacy dispatch must not execute canonical provider methods"
            ), raising=False,
        )


@pytest.mark.parametrize(
    "contract", CONTRACTS,
    ids=("downloadable_v2", "downloadable_guarded_only",
         "downloadable_runtime_guarded_only", "rights_v1"),
)
def test_canonical_kobo_never_offers_or_executes_legacy_direct_item(
    tmp_path, monkeypatch, contract,
):
    service = _service(tmp_path)
    session = service._sessions[service._session_key("kobo", "vault-1")]
    item = session.normalize_item(_manifest_row())
    calls = []

    @contextmanager
    def lock(_path):
        calls.append("locked")
        yield

    try:
        _abi(session, monkeypatch, contract)
        monkeypatch.setattr(services, "InterProcessLock", lock)
        monkeypatch.setattr(services, "acquisition_lock_file",
                            lambda: tmp_path / "acquisition.lock")
        monkeypatch.setattr(service, "_execute_kobo_acquisition",
                            lambda *_a, **_kw: pytest.fail("duplicate Kobo GET"))
        # Existing contract: public catalogue rows must pass the passive
        # exact-right admission before any provider-session lookup. Give
        # this synthetic book a local right; the canonical ABI still hides
        # the legacy download action before an ADEPT runtime probe.
        monkeypatch.setattr(service, "kobo_personal_right_states",
                            lambda _identity: {("vault-1", "book-1"): "active"})
        monkeypatch.setattr(service, "kobo_adept_state",
                            lambda **_kw: pytest.fail("should not inspect ADEPT"))
        before = service.storage.placements(), service.storage.backup_list()
        assert service.can_direct_download(item) is False
        with pytest.raises(
            ProviderConfigurationError,
            match="kobo_canonical_source_delivery_active",
        ):
            service.acquire_direct_item(item)
        assert calls == ["locked"]
        assert (service.storage.placements(), service.storage.backup_list()) == before
        assert service.storage.acquisitions() == []
    finally:
        service.close()


@pytest.mark.parametrize("when", ("lock_entry", "admission"))
def test_kobo_canonical_abi_appearing_after_preview_refuses_old_selected_get(
    tmp_path, monkeypatch, when,
):
    service = _service(tmp_path)
    session = service._sessions[service._session_key("kobo", "vault-1")]
    item = session.normalize_item(_manifest_row())
    events = []

    @contextmanager
    def lock(_path):
        events.append("locked")
        if when == "lock_entry":
            _abi(session, monkeypatch, CONTRACTS[0])
        yield

    def admission():
        events.append("admission")
        if when == "admission":
            _abi(session, monkeypatch, CONTRACTS[1])
        return item

    try:
        monkeypatch.setattr(services, "InterProcessLock", lock)
        monkeypatch.setattr(services, "acquisition_lock_file",
                            lambda: tmp_path / "acquisition.lock")
        monkeypatch.setattr(service, "_execute_kobo_acquisition",
                            lambda *_a, **_kw: pytest.fail("duplicate Kobo GET"))
        before = service.storage.placements(), service.storage.backup_list()
        with pytest.raises(
            ProviderConfigurationError,
            match="kobo_canonical_source_delivery_active",
        ):
            service.acquire_direct_item(item, _admission=admission)
        assert events == (["locked"] if when == "lock_entry"
                          else ["locked", "admission"])
        assert (service.storage.placements(), service.storage.backup_list()) == before
        assert service.storage.acquisitions() == []
    finally:
        service.close()


def test_incomplete_canonical_abi_keeps_historical_direct_get_compatible(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    session = service._sessions[service._session_key("kobo", "vault-1")]
    item = session.normalize_item(_manifest_row())
    completed = []

    @contextmanager
    def lock(_path):
        yield

    try:
        monkeypatch.setattr(session, CONTRACTS[0][0],
                            lambda *_a, **_kw: pytest.fail("partial ABI not callable"),
                            raising=False)
        monkeypatch.setattr(services, "InterProcessLock", lock)
        monkeypatch.setattr(service, "_execute_kobo_acquisition",
                            lambda _item, _target: completed.append(_item.record_id)
                            or SimpleNamespace(path=tmp_path / "synthetic.epub", metadata={}))
        service.acquire_direct_item(item)
        assert completed == ["book-1"]
    finally:
        service.close()
