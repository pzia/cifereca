"""P0 #1905: explicit Numilog direct acquisition must not bypass Core guards."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.models import Account
from cifereca.providers import ProviderConfigurationError
from tests.test_core_p0_1862_numilog_store_mirror import _connect, _row
from tests.test_sync_state_contract import _service


def _rig(tmp_path, monkeypatch):
    service = _service(tmp_path)
    account, session, calls = _connect(service, number=0)
    item = _row(account.credential_id, 0)
    provider_calls = []
    records = []
    service._record_action = lambda *a, **k: records.append((a, k))

    def download(item, directory):
        provider_calls.append(item.record_id)
        path = Path(directory) / "fictional.epub"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake binary")
        return path, "https://private.invalid/URLLink.acsm?auth=SIGNED_SECRET"

    session.download_direct = download
    monkeypatch.setattr(
        service.acquisition, "run_post_download",
        lambda *_a: provider_calls.append("post_download"),
    )
    return service, account, session, item, provider_calls, records


def test_manual_numilog_acquire_locks_source_and_discards_signed_url(tmp_path, monkeypatch):
    service, account, session, item, calls, records = _rig(tmp_path, monkeypatch)
    try:
        item.thumbnail_url = "https://private.invalid/cover.png?token=SIGNED_SECRET"
        item.friendly_url = "https://private.invalid/detail?auth=SIGNED_SECRET"
        item.download_url = "https://private.invalid/direct?auth=SIGNED_SECRET"
        item.metadata = {"private_url": "https://private.invalid?token=SIGNED_SECRET"}
        acquired = service.acquire_direct_item(item)
        assert acquired.provider_id == "numilog"
        assert acquired.credential_id == account.credential_id
        assert acquired.item_id == item.record_id
        assert acquired.account_id == account.unique_id
        assert acquired.metadata["identity_id"] == account.identity_id
        assert acquired.metadata["cover_url"] is None
        assert acquired.source_url is None
        assert "SIGNED_SECRET" not in repr(acquired)
        assert acquired.local_path.is_file()
        assert calls == [item.record_id, "post_download"]
        assert all("SIGNED_SECRET" not in repr(record) for record in records)
        assert all(item.record_id not in repr(record) for record in records)
        assert records[-1][1]["facts"] == {"private_item": True}
    finally:
        service.close()


@pytest.mark.parametrize("mutation", [
    "public_catalog", "downloadable_false",
    "wrong_scenario", "wrong_credential", "bad_item_id",
    "mismatched_ids", "stale_search", "disabled_source", "disabled_connection",
    "detached_identity", "disconnected", "capability_off", "duplicate_account",
    "missing_session",
])
def test_manual_numilog_acquire_rejects_untrusted_source_before_delivery(
    tmp_path, monkeypatch, mutation,
):
    service, account, session, item, calls, records = _rig(tmp_path, monkeypatch)
    try:
        if mutation == "public_catalog":
            item.source_collection = "public_catalog"
        elif mutation == "owned_false":
            item.owned = False
        elif mutation == "owned_missing":
            item.owned = None
        elif mutation == "downloadable_false":
            item.downloadable = False
        elif mutation == "wrong_scenario":
            item.scenario = "NUMILOG_PUBLIC"
        elif mutation == "wrong_credential":
            item.source_credential = "another-credential"
        elif mutation == "bad_item_id":
            item.record_id = "numilog:invalid"
        elif mutation == "mismatched_ids":
            item.id = "other-book"
        elif mutation == "stale_search":
            item.metadata["search_provenance"] = "known_catalog_stale"
        elif mutation == "disabled_source":
            broker = SynchronizationBrokerImpl(service)
            service.storage.metadata_set(
                "sync.source.enabled." + broker._source_id(account), "0",
            )
        elif mutation == "disabled_connection":
            service.storage.provider_connection_put({
                "identity_id": account.identity_id, "provider_id": "numilog",
                "credential_id": account.credential_id, "enabled": False,
            })
        elif mutation == "detached_identity":
            service.storage.provider_connection_put({
                "identity_id": "alex", "provider_id": "numilog",
                "credential_id": account.credential_id, "enabled": True,
            })
        elif mutation == "disconnected":
            session.connected = False
        elif mutation == "capability_off":
            session.capabilities.direct_download = False
        elif mutation == "duplicate_account":
            service.accounts.append(replace(account, unique_id="ambiguous"))
        elif mutation == "missing_session":
            service._sessions.clear()
        with pytest.raises(ProviderConfigurationError) as exc:
            service.acquire_direct_item(item)
        assert "SIGNED_SECRET" not in str(exc.value)
        assert account.credential_id not in str(exc.value)
        assert item.record_id not in str(exc.value)
        assert calls == []
    finally:
        service.close()


@pytest.mark.parametrize("kind", ["ledger", "placement"])
def test_numilog_manual_acquire_blocks_existing_or_ambiguous_local_evidence(
    tmp_path, monkeypatch, kind,
):
    service, account, session, item, calls, records = _rig(tmp_path, monkeypatch)
    try:
        if kind == "ledger":
            service.storage.acquisition_put({
                "acquisition_id": "old-attempt",
                "provider_id": "numilog", "credential_id": account.credential_id,
                "account_id": account.unique_id, "identity_id": account.identity_id,
                "item_id": item.record_id, "status": "retryable_error",
            })
        else:
            # A provenance-linked placement is enough to require local
            # reconciliation, even if an acquisition row was lost.
            service.storage.placement_put({
                "placement_id": "orphan-local-file",
                "artifact_id": None, "path": str(tmp_path / "fake.epub"),
                "identity_id": account.identity_id, "provider_id": "numilog",
                "source_item_id": item.record_id, "state": "missing",
            })
        with pytest.raises(ProviderConfigurationError, match="rapprochement local requis"):
            service.acquire_direct_item(item)
        assert calls == []
    finally:
        service.close()


@pytest.mark.parametrize("change", ["rebound", "session_swap", "disable", "account_changed"])
def test_numilog_owner_change_during_download_prevents_registration(
    tmp_path, monkeypatch, change,
):
    service, account, session, item, calls, records = _rig(tmp_path, monkeypatch)
    try:
        original = session.download_direct
        def changed(item, directory):
            result = original(item, directory)
            if change == "rebound":
                service.storage.provider_connection_put({
                    "identity_id": "alex", "provider_id": "numilog",
                    "credential_id": account.credential_id, "enabled": True,
                })
            elif change == "session_swap":
                service._sessions[service._session_key("numilog", account.credential_id)] = (
                    type(session)(**vars(session))
                )
            elif change == "account_changed":
                account.unique_id = "reassigned-account"
            else:
                broker = SynchronizationBrokerImpl(service)
                service.storage.metadata_set(
                    "sync.source.enabled." + broker._source_id(account), "0",
                )
            return result
        session.download_direct = changed
        with pytest.raises(ProviderConfigurationError):
            service.acquire_direct_item(item)
        assert calls == [item.record_id]
        assert all("SIGNED_SECRET" not in repr(record) for record in records)
    finally:
        service.close()


def test_numilog_private_provider_error_does_not_reach_ui_or_diagnostics(
    tmp_path, monkeypatch,
):
    service, account, session, item, calls, records = _rig(tmp_path, monkeypatch)
    try:
        def fail(*_args):
            raise RuntimeError(
                "ACSM https://entrepot2r.numilog.com/URLLink.acsm?auth=SIGNED_SECRET"
            )
        session.download_direct = fail
        with pytest.raises(ProviderConfigurationError) as exc:
            service.acquire_direct_item(item)
        assert "SIGNED_SECRET" not in str(exc.value)
        assert all("SIGNED_SECRET" not in repr(record) for record in records)
        assert calls == []
    finally:
        service.close()


def test_numilog_busy_global_acquisition_lock_does_not_call_provider(
    tmp_path, monkeypatch,
):
    from cifereca.locking import LockBusyError
    service, account, session, item, calls, records = _rig(tmp_path, monkeypatch)
    try:
        class Busy:
            def __init__(self, *_a, **_k):
                pass
            def __enter__(self):
                raise LockBusyError("locked")
            def __exit__(self, *_a):
                pass
        monkeypatch.setattr("cifereca.services.InterProcessLock", Busy)
        with pytest.raises(ProviderConfigurationError, match="déjà en cours"):
            service.acquire_direct_item(item)
        assert calls == []
    finally:
        service.close()

def test_numilog_detached_source_action_is_never_attributed_to_selected_identity(
    tmp_path, monkeypatch,
):
    service, account, session, item, calls, records = _rig(tmp_path, monkeypatch)
    try:
        service.current_identity = service.identity_by_id("alex")
        service.storage.provider_connection_put({
            "identity_id": "unconfigured-credential-owner",
            "provider_id": "numilog",
            "credential_id": account.credential_id, "enabled": True,
        })
        with pytest.raises(ProviderConfigurationError):
            service.acquire_direct_item(item)
        assert calls == []
        assert records and records[-1][0][0] is None
        assert records[-1][1]["facts"]["private_item"] is True
        assert item.record_id not in repr(records)
    finally:
        service.close()


def test_numilog_cooperative_cancellation_propagates_without_registration(
    tmp_path, monkeypatch,
):
    from cifereca.plugin_api import Cancelled
    service, account, session, item, calls, records = _rig(tmp_path, monkeypatch)
    try:
        def cancel(*_args):
            raise Cancelled("cancelled")
        session.download_direct = cancel
        with pytest.raises(Cancelled):
            service.acquire_direct_item(item)
        assert calls == []
        assert records[-1][1]["facts"]["private_item"] is True
        assert records[-1][1]["facts"]["error"] == "Cancelled"
    finally:
        service.close()


@pytest.mark.parametrize("kind", ["orphan_file", "orphan_dangling_symlink"])
def test_numilog_manual_acquire_will_not_overwrite_untracked_hashed_file(
    tmp_path, monkeypatch, kind,
):
    import hashlib
    service, account, session, item, calls, records = _rig(tmp_path, monkeypatch)
    try:
        directory = service.library_identity_provider_dir(account.identity_id, "numilog")
        name = "numilog-" + hashlib.sha256(
            item.record_id.encode("utf-8"),
        ).hexdigest()[:32] + ".epub"
        candidate = directory / name
        if kind == "orphan_file":
            candidate.write_bytes(b"ORIGINAL-LOCAL-BOOK")
        else:
            candidate.symlink_to(directory / "nonexistent-untrusted-target.epub")
        with pytest.raises(ProviderConfigurationError, match="rapprochement requis"):
            service.acquire_direct_item(item)
        assert calls == []
        if kind == "orphan_file":
            assert candidate.read_bytes() == b"ORIGINAL-LOCAL-BOOK"
        else:
            assert candidate.is_symlink()
            assert not candidate.exists()
    finally:
        service.close()


@pytest.mark.parametrize("kind", [
    "outside_identity", "symlink_file", "symlink_parent",
    "unreadable_acsm", "nested_adept_book",
])
def test_numilog_provider_output_requires_owned_regular_book_path(
    tmp_path, monkeypatch, kind,
):
    service, account, session, item, calls, records = _rig(tmp_path, monkeypatch)
    try:
        def provider_output(book, target):
            calls.append("download_called")
            if kind == "outside_identity":
                destination = tmp_path / "foreign-user-book.epub"
            elif kind == "symlink_file":
                original = tmp_path / "outside.epub"
                original.write_bytes(b"NOT-FROM-IDENTITY")
                destination = target / "linked-book.epub"
                destination.symlink_to(original)
            elif kind == "symlink_parent":
                real = target / "actual"
                real.mkdir()
                (real / "book.epub").write_bytes(b"NOT-TRUSTED")
                alias = target / "linked"
                alias.symlink_to(real, target_is_directory=True)
                destination = alias / "book.epub"
            elif kind == "unreadable_acsm":
                destination = target / "raw-delivery.acsm"
            else:
                destination = target / "acsm-abcdef" / "book.epub"
                destination.parent.mkdir()
            if not destination.exists() and not destination.is_symlink():
                destination.write_bytes(b"NOT-REAL-BOOK")
            return destination, "https://private.invalid?auth=SIGNED_SECRET"
        session.download_direct = provider_output
        if kind == "nested_adept_book":
            result = service.acquire_direct_item(item)
            assert result.local_path.name == "book.epub"
            assert calls == ["download_called", "post_download"]
        else:
            with pytest.raises(
                ProviderConfigurationError, match="artefact local non attesté",
            ):
                service.acquire_direct_item(item)
            assert calls == ["download_called"]
        assert all("SIGNED_SECRET" not in repr(row) for row in records)
    finally:
        service.close()


@pytest.mark.parametrize("symlink_at", ["provider", "identity"])
def test_numilog_manual_acquire_refuses_symlinked_identity_root_before_network(
    tmp_path, monkeypatch, symlink_at,
):
    service, account, session, item, calls, records = _rig(tmp_path, monkeypatch)
    try:
        target = service.library_identity_provider_dir(account.identity_id, "numilog")
        outer = tmp_path / "foreign-root"
        outer.mkdir()
        if symlink_at == "provider":
            target.rmdir()
            target.symlink_to(outer, target_is_directory=True)
        else:
            target.rmdir()
            target.parent.rmdir()
            (outer / "numilog").mkdir()
            target.parent.symlink_to(outer, target_is_directory=True)
        with pytest.raises(
            ProviderConfigurationError, match="répertoire identité non attesté",
        ):
            service.acquire_direct_item(item)
        assert calls == []
    finally:
        service.close()


@pytest.mark.parametrize('mutation', ['deleted', 'foreign_symlink'])
def test_manual_numilog_revalidates_artifact_after_post_download_hook(tmp_path, monkeypatch, mutation):
    service, account, session, item, calls, records = _rig(tmp_path, monkeypatch)
    try:
        foreign = tmp_path/'foreign.epub'
        foreign.write_bytes(b'outside admitted identity')
        def change_file(_item, path, _url, _directory):
            path.unlink()
            if mutation == 'foreign_symlink':
                path.symlink_to(foreign)
        monkeypatch.setattr(service.acquisition, 'run_post_download', change_file)
        with pytest.raises(ProviderConfigurationError, match='artefact local non attesté'):
            service.acquire_direct_item(item)
        assert foreign.read_bytes()==b'outside admitted identity'
        assert records[-1][1]['facts']=={'private_item':True,'error':'ProviderConfigurationError'}
    finally:
        service.close()


@pytest.mark.parametrize("owned_metadata", [False, None])
def test_authenticated_offer_downloadability_supersedes_owned_label(
    tmp_path, monkeypatch, owned_metadata,
):
    """#3171: an optional right flag is not the acquisition authority."""
    service, account, session, item, calls, _records = _rig(tmp_path, monkeypatch)
    try:
        item.owned = owned_metadata
        item.downloadable = True
        acquired = service.acquire_direct_item(item)
        assert acquired.account_id == account.unique_id
        assert acquired.metadata["identity_id"] == account.identity_id
        assert acquired.path.is_file()
        assert calls == [item.record_id, "post_download"]
    finally:
        service.close()
