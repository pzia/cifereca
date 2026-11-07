"""#4858 D1: a connected complete Kobo ABI has NO offline 0.7 authority.

Real v14 SQLite, real canonical B and no-follow file SHA check; all supplier
and legacy backup reads are deliberately forbidden after fixture setup.
"""
from __future__ import annotations

from dataclasses import replace
import json

import pytest

from cifereca.core import acquisition_audit
from cifereca.providers import ProviderConfigurationError
from cifereca.core.canonical_publication import publish_completed_acquisition
from tests.test_kobo_backup_guard import _service
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, SHA, new_source,
)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    service = _service(tmp_path)
    session = service._sessions[service._session_key("kobo", "vault-1")]
    account = session.accounts[0]
    service.accounts = [account]
    service.current_account = account
    for method in (
        "downloadable_resource_snapshot",
        "downloadable_selected_offer_snapshot",
        "downloadable_selected_delivery_candidate_runtime_guarded",
    ):
        monkeypatch.setattr(
            session, method, lambda *_a, **_kw:
            pytest.fail("offline preview cannot call supplier"), raising=False,
        )
    monkeypatch.setattr(
        session, "backup_manifest", lambda *_a, **_kw:
        pytest.fail("offline modern preview cannot call 0.7 manifest"),
    )
    monkeypatch.setattr(
        session, "library", lambda *_a, **_kw:
        pytest.fail("offline modern preview cannot read remote inventory"),
        raising=False,
    )
    try:
        yield service, session, account
    finally:
        service.close()


def _disable_backup_reads(service, monkeypatch):
    for name in ("backup_get", "backup_list"):
        monkeypatch.setattr(
            service.storage, name, lambda *_a, **_kw:
            pytest.fail("modern preview must not read backup_items"),
        )
    monkeypatch.setattr(
        service, "catalog_scan", lambda *_a, **_kw:
        pytest.fail("offline preview cannot scan or mutate library"),
    )


def test_modern_offline_preview_does_not_read_backup_or_supplier(rig, monkeypatch):
    service, session, account = rig
    _disable_backup_reads(service, monkeypatch)
    result = service.kobo_backup_preview("vault-1")
    assert result["canonical_source"] is True
    assert result["historical_canonical_only"] is True
    assert result["manifest_refreshed"] is False
    assert result["manifest_items"] == 0
    assert result["acquisition_authorized"] is False
    assert result["requires_manifest_validation"] is True
    assert result["manifest_valid"] is False
    assert result["counts"]["already_local_verified"] == 0
    assert result["counts"]["potentially_missing"] == 0
    assert result["historical_canonical"]["recorded_rights"] == 0
    assert "vault-1" not in json.dumps(result)
    # 'scope=account' is a public enum, not the private borrower ID.
    assert '"account_id": "account"' not in json.dumps(result)


def test_canonical_published_B_is_historical_not_current_A(
    rig, tmp_path, monkeypatch,
):
    service, session, account = rig
    key, snapshot = new_source(
        "alice", provider="kobo", credential="vault-1",
        account="account", branch="purchases",
        edition="book-1", right="book-1",
    )
    path = tmp_path / "EBooks" / "alice" / "book.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(BYTES)
    published = publish_completed_acquisition(
        service.storage, library_root=tmp_path / "EBooks",
        path=path, source=key, snapshot=snapshot,
        final_sha256=SHA, delivery_receipt_ref="synthetic-completed-delivery",
    )
    assert published.changed
    _disable_backup_reads(service, monkeypatch)
    verified = service.kobo_backup_preview("vault-1", verify_local_files=True)
    assert verified["historical_canonical"]["recorded_rights"] == 1
    assert (verified["historical_canonical"]
            ["previously_published_files_physically_verified"]) == 1
    assert verified["historical_canonical"]["file_verification_requested"] is True
    assert verified["counts"]["already_local_verified"] == 0
    assert verified["counts"]["potentially_missing"] == 0
    assert verified["manifest_valid"] is False
    assert verified["acquisition_authorized"] is False

    # A read without expensive SHA verification may report the known
    # historical record but cannot count verified physical presence.
    simple = service.kobo_backup_preview("vault-1")
    assert simple["historical_canonical"]["recorded_rights"] == 1
    assert (simple["historical_canonical"]
            ["previously_published_files_physically_verified"]) == 0
    path.unlink()
    gone = service.kobo_backup_preview("vault-1", verify_local_files=True)
    assert (gone["historical_canonical"]
            ["previously_published_files_physically_verified"]) == 0
    assert gone["counts"]["potentially_missing"] == 0
    assert gone["counts"]["already_local_verified"] == 0


def test_borrower_rebound_during_offline_audit_discards_projection(
    rig, monkeypatch,
):
    service, session, account = rig
    original = acquisition_audit.inspect_exact_source
    def rebound(*args, **kwargs):
        result = original(*args, **kwargs)
        session._accounts = [replace(account, unique_id="borrower-elsewhere")]
        return result
    monkeypatch.setattr(acquisition_audit, "inspect_exact_source", rebound)
    _disable_backup_reads(service, monkeypatch)
    with pytest.raises(ProviderConfigurationError,
                       match="canonical_kobo_preview_scope_changed"):
        service.kobo_backup_preview("vault-1")


def test_provider_default_identity_retains_exact_sql_owner(rig, monkeypatch):
    service, session, account = rig
    # The real Vault adapter constructs Account without a Core identity_id;
    # its default label is not a second profile or a bearer of rights.
    session._accounts = [replace(account, identity_id="default")]
    _disable_backup_reads(service, monkeypatch)
    result = service.kobo_backup_preview("vault-1")
    assert result["historical_canonical_only"] is True
    assert result["acquisition_authorized"] is False


def test_explicit_foreign_provider_identity_refuses_offline_audit(
    rig, monkeypatch,
):
    service, session, account = rig
    session._accounts = [replace(account, identity_id="bob")]
    _disable_backup_reads(service, monkeypatch)
    with pytest.raises(ProviderConfigurationError,
                       match="canonical_kobo_preview_scope_changed"):
        service.kobo_backup_preview("vault-1")


def test_ambiguous_core_credential_denies_offline_audit(rig, monkeypatch):
    service, session, account = rig
    service.accounts.append(replace(account))
    _disable_backup_reads(service, monkeypatch)
    with pytest.raises(ProviderConfigurationError,
                       match="canonical_kobo_preview_scope_unavailable"):
        service.kobo_backup_preview("vault-1")


def test_disabled_connection_denies_offline_audit(rig, monkeypatch):
    service, session, account = rig
    original = service.storage.provider_scope_account_bound
    monkeypatch.setattr(
        service.storage, "provider_scope_account_bound",
        lambda **_kw: False,
    )
    _disable_backup_reads(service, monkeypatch)
    with pytest.raises(ProviderConfigurationError,
                       match="canonical_kobo_preview_scope_changed"):
        service.kobo_backup_preview("vault-1")
