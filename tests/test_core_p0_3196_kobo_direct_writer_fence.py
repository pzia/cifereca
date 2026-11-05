"""#3196: a fully canonical Kobo session cannot enter the backup_items writer.

Real LibraryService/SQLite with connected synthetic Kobo accounts. No
supplier I/O, no acquisition, no filesystem publication.
"""
from __future__ import annotations

import logging

import pytest

from cifereca.core import kobo_backup
from cifereca.core.canonical_delivery_abi import has_complete_delivery_abi
from cifereca.locking import InterProcessLock
from cifereca.providers import ProviderConfigurationError
from tests.test_kobo_backup_guard import _service


_CONTRACTS = (
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


@pytest.mark.parametrize(
    "contract", _CONTRACTS,
    ids=["downloadable_v2", "downloadable_guarded_only",
         "downloadable_runtime_guarded_only", "rights_v1"],
)
@pytest.mark.parametrize("scoped", [True, False], ids=["scoped", "unscoped"])
def test_complete_core_abi_blocks_direct_legacy_writer_before_io(
    tmp_path, monkeypatch, contract, scoped,
):
    service = _service(tmp_path)
    session = service._sessions[service._session_key("kobo", "vault-1")]
    def forbidden(*_args, **_kwargs):
        pytest.fail("legacy acquisition must not scan or request the supplier")

    try:
        assert not has_complete_delivery_abi(session)
        for method in contract:
            monkeypatch.setattr(session, method, forbidden, raising=False)
        monkeypatch.setattr(service, "catalog_scan", forbidden)
        monkeypatch.setattr(service, "kobo_adept_state", forbidden)
        monkeypatch.setattr(session, "backup_manifest", forbidden)
        assert has_complete_delivery_abi(session)

        before_backups = service.storage.backup_list()
        before_placements = service.storage.placements()
        credential_id = "vault-1" if scoped else None
        with pytest.raises(ProviderConfigurationError,
                           match="kobo_canonical_source_delivery_active"):
            kobo_backup._sync_kobo_backup_locked(
                service, logger=logging.getLogger("synthetic"),
                credential_id=credential_id,
            )
        with pytest.raises(ProviderConfigurationError,
                           match="kobo_canonical_source_delivery_active"):
            kobo_backup.sync_kobo_backup(
                service, credential_id=credential_id,
                lock_factory=InterProcessLock,
                lock_path=lambda: tmp_path / "acquisition.lock",
            )
        assert service.storage.backup_list() == before_backups
        assert service.storage.placements() == before_placements
    finally:
        service.close()


@pytest.mark.parametrize("only_method", [
    "downloadable_selected_delivery_candidate_guarded",
    "downloadable_selected_delivery_candidate_runtime_guarded",
])
def test_only_candidate_method_is_not_complete_without_source_and_offer(
    tmp_path, monkeypatch, only_method,
):
    service = _service(tmp_path)
    session = service._sessions[service._session_key("kobo", "vault-1")]
    try:
        monkeypatch.setattr(session, only_method, lambda *_a, **_kw: None,
                            raising=False)
        assert not has_complete_delivery_abi(session)
        monkeypatch.setattr(session, "downloadable_resource_snapshot",
                            lambda *_a, **_kw: None, raising=False)
        assert not has_complete_delivery_abi(session)
        monkeypatch.setattr(session, "downloadable_selected_offer_snapshot",
                            lambda *_a, **_kw: None, raising=False)
        assert has_complete_delivery_abi(session)
    finally:
        service.close()


def test_partial_and_dynamically_probed_abi_cannot_impersonate_core(tmp_path, monkeypatch):
    service = _service(tmp_path)
    session = service._sessions[service._session_key("kobo", "vault-1")]
    try:
        assert not has_complete_delivery_abi(session)
        monkeypatch.setattr(
            session, "downloadable_resource_snapshot",
            lambda *_args, **_kwargs: pytest.fail("partial ABI must not be invoked"),
            raising=False,
        )
        assert not has_complete_delivery_abi(session)
        monkeypatch.setattr(
            session, "downloadable_selected_offer_snapshot",
            lambda *_args, **_kwargs: pytest.fail("partial ABI must not be invoked"),
            raising=False,
        )
        assert not has_complete_delivery_abi(session)
    finally:
        service.close()
