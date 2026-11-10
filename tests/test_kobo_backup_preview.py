"""Kobo P0: preflight shows potential acquisitions without mutating rights.

The default preview works strictly offline. Explicit refresh requests only
the provider's rights manifest, never acquisition descriptors or content.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cifereca.providers import ProviderConfigurationError
from tests.test_kobo_backup_fault_isolation import _service, _write_epub


def _backup(service, item_id, *, status="pending", local_path=None, **extra):
    service.storage.backup_put({
        "provider_id": "kobo",
        "credential_id": "vault-1",
        "identity_id": "default",
        "item_id": item_id,
        "revision_id": "r1",
        "title": item_id,
        "format": "EPUB",
        "status": status,
        "local_path": str(local_path) if local_path is not None else None,
        **extra,
    })


def _forbid_mutation_and_acquisition(service, session, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("preview must not mutate Core, download or invoke Adept")

    monkeypatch.setattr(service.storage, "backup_put", forbidden)
    monkeypatch.setattr(service.storage, "metadata_set", forbidden)
    monkeypatch.setattr(service, "_execute_kobo_acquisition", forbidden)
    monkeypatch.setattr(service, "kobo_adept_state", forbidden)
    monkeypatch.setattr(service, "catalog_scan", forbidden)
    monkeypatch.setattr(session, "resolve_acquisition", forbidden)


def _read_only_legacy_preview(service, *, refresh=False, verify=False):
    """No Kobo 0.7 row may be promoted by partial-ABI preview."""
    try:
        report = service.kobo_backup_preview(
            "vault-1", refresh_manifest=refresh,
            verify_local_files=verify,
        )
    except ProviderConfigurationError as exc:
        assert str(exc) in {
            "canonical_kobo_preview_scope_unavailable",
            "canonical_kobo_preview_scope_changed",
            "canonical_kobo_preview_abi_unavailable",
        }
        return
    assert refresh is False, "partial ABI must not read provider A"
    assert report["canonical_source"] is True
    assert report["historical_canonical_only"] is True
    assert report["acquisition_authorized"] is False
    assert report["manifest_refreshed"] is False
    assert report["counts"]["already_local_verified"] == 0
    assert report["counts"]["already_local_linked"] == 0
    assert report["counts"]["potentially_missing"] == 0
    for private in ("vault-1", "historical.epub", '"item_id"', '"credential_id"'):
        assert private not in json.dumps(report)


@pytest.mark.parametrize("verify", [False, True])
@pytest.mark.parametrize("status", ["ok", "complete", "acquiring", "pending"])
@pytest.mark.parametrize("local", ["absent", "intact", "modified", "symlink"])
def test_legacy_checkpoint_never_proves_canonical_B_or_allows_provider_GET(
    tmp_path: Path, monkeypatch, status, verify, local,
):
    """Only a canonical OriginLink/Placement can attest B for a Kobo source."""
    service, session = _service(tmp_path, [
        {"product_id": "historical", "revision_id": "r1", "title": "Historical"}
    ])
    try:
        target = service._kobo_backup_target_dir("default") / "historical.epub"
        if local != "absent":
            _write_epub(target)
        original = target.read_bytes() if target.is_file() else b""
        sha = hashlib.sha256(original).hexdigest() if original else None
        if local == "modified":
            target.write_bytes(b"changed bytes")
        elif local == "symlink":
            target.unlink()
            other = tmp_path / "outside.epub"
            _write_epub(other)
            target.symlink_to(other)
        _backup(
            service, "historical", status=status,
            local_path=target if local != "absent" else None,
            sha256=sha, size=len(original) if original else None,
            account_id="account",
        )
        before = service.storage.backup_get(
            "kobo", "vault-1", "historical"
        )
        with service.storage._lock, service.storage._connect() as db:
            before_b = tuple(db.execute(
                "SELECT * FROM origin_links ORDER BY 1"
            ))
        monkeypatch.setattr(
            session, "backup_manifest",
            lambda: pytest.fail("D1 may not read 0.7 manifest"),
        )
        _forbid_mutation_and_acquisition(service, session, monkeypatch)
        _read_only_legacy_preview(service, verify=verify)
        with pytest.raises(ProviderConfigurationError,
                           match="canonical_kobo_preview_abi_unavailable"):
            service.kobo_backup_preview("vault-1", refresh_manifest=True)
        with pytest.raises(ProviderConfigurationError,
                           match="canonical_kobo_preview_abi_unavailable"):
            service.kobo_backup_preview(
                "vault-1", refresh_manifest=True, _item_reasons={},
            )
        assert service.storage.backup_get(
            "kobo", "vault-1", "historical"
        ) == before
        with service.storage._lock, service.storage._connect() as db:
            assert tuple(db.execute(
                "SELECT * FROM origin_links ORDER BY 1"
            )) == before_b
    finally:
        service.close()


@pytest.mark.parametrize("old_token", ["", "a" * 64, "synthetic"])
def test_private_legacy_reason_sink_never_mints_confirmation(
    tmp_path: Path, monkeypatch, old_token,
):
    from cifereca.core.kobo_backup import kobo_backup_preview
    service, session = _service(tmp_path, [])
    try:
        _forbid_mutation_and_acquisition(service, session, monkeypatch)
        with pytest.raises(
            ProviderConfigurationError,
            match="canonical_kobo_preview_abi_unavailable",
        ):
            kobo_backup_preview(
                service, "vault-1", _item_reasons={},
                _manifest_evidence=([], 0),
            )
        from cifereca.brokers import SynchronizationBrokerImpl
        broker = SynchronizationBrokerImpl(service)
        for account in service.accounts:
            if account.provider_id == "kobo":
                result = broker.materialize_source(
                    broker._source_id(account), old_token,
                )
                assert not result.ok
                assert "confirmation_token" not in result.facts
        assert session.resolve_calls == []
    finally:
        service.close()


def test_unknown_kobo_credential_denies_preview(tmp_path: Path):
    service, _ = _service(tmp_path, [])
    try:
        with pytest.raises(ProviderConfigurationError, match="non configuré"):
            service.kobo_backup_preview("not-bound")
    finally:
        service.close()
