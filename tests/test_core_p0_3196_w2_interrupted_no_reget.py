"""#3196/#4858: retired W2 never replays an interrupted 0.7 GET.

Historical checkpoint rows remain readable and unchanged for supervised
reconciliation. The positive canonical crash/post-GET replay contract is
exercised in test_core_p0_3171_crash_recovery_v14.py: an old 0.7 checkpoint
is NOT a canonical delivery receipt or permission to retry via W2.
"""
from __future__ import annotations

import pytest

from cifereca.providers import ProviderConfigurationError
from tests.test_kobo_backup_reconcile_prune import _manifest_row, _service


def _checkpoint(*, status, error=None, attention_code=None, attempts=1):
    return {
        "provider_id": "kobo", "credential_id": "vault-1",
        "identity_id": "default", "account_id": "account",
        "item_id": "book-1", "revision_id": "rev-before-crash",
        "title": "Book One", "author": "Author", "format": "EPUB",
        "entitlement_type": "owned", "status": status,
        "local_path": None, "size": None, "sha256": None,
        "attempt_count": attempts, "job_id": "interrupted-job",
        "last_attempt_at": 123, "attention_code": attention_code,
        "error": error,
    }


@pytest.mark.parametrize("checkpoint", [
    _checkpoint(status="acquiring"),
    _checkpoint(status="pending_acquisition", attempts=2,
                error="Acquisition locale interrompue avant finalisation"),
    _checkpoint(status="pending_acquisition", attempts=2,
                attention_code="interrupted_acquisition_review"),
    _checkpoint(status="pending_acquisition", attempts=0,
                error="Kobo-Adept indisponible"),
    _checkpoint(status="error", error="Kobo-Adept indisponible"),
    _checkpoint(status="complete"),
])
@pytest.mark.parametrize("archived", [True, False])
def test_retired_w2_never_retries_or_rewrites_historical_checkpoint(
    tmp_path, monkeypatch, checkpoint, archived,
):
    """Interrupted, pending, complete and archived rows never revive W2."""
    raw = {**_manifest_row(revision_id="rev-after-crash"),
           "archived": archived}
    service = _service(tmp_path, manifest=[raw])
    try:
        service.storage.backup_put(checkpoint)
        before = service.storage.backup_get("kobo", "vault-1", "book-1")
        with service.storage._lock, service.storage._connect() as db:
            links_before = db.execute(
                "SELECT * FROM origin_links ORDER BY 1"
            ).fetchall()
            deliveries_before = db.execute(
                "SELECT * FROM completed_deliveries ORDER BY 1"
            ).fetchall()

        def forbidden(*_args, **_kwargs):
            pytest.fail("retired W2 cannot read manifest, GET or mutate 0.7")

        session = service._sessions[service._session_key("kobo", "vault-1")]
        monkeypatch.setattr(session, "backup_manifest", forbidden)
        monkeypatch.setattr(service, "_execute_kobo_acquisition", forbidden)
        monkeypatch.setattr(service.storage, "backup_put", forbidden)
        monkeypatch.setattr(service.storage, "backup_prune_missing", forbidden)
        for _ in range(2):  # repeated startup/replay must remain no-op
            with pytest.raises(
                ProviderConfigurationError,
                match="kobo_legacy_w2_retired_use_canonical_provider",
            ):
                service.sync_kobo_backup()
            assert service.storage.backup_get(
                "kobo", "vault-1", "book-1",
            ) == before
        with service.storage._lock, service.storage._connect() as db:
            assert db.execute(
                "SELECT * FROM origin_links ORDER BY 1"
            ).fetchall() == links_before
            assert db.execute(
                "SELECT * FROM completed_deliveries ORDER BY 1"
            ).fetchall() == deliveries_before
        assert service.storage.placements() == []
    finally:
        service.close()


@pytest.mark.parametrize("sha,size", [
    (None, None), (None, 43), ("a" * 64, 43),
])
def test_interrupted_locator_does_not_mint_sha_or_canonical_origin(
    tmp_path, monkeypatch, sha, size,
):
    """Local bytes near an old interrupted row are not delivery evidence."""
    from tests.test_kobo_backup_reconcile_prune import _write_epub

    service = _service(tmp_path, manifest=[_manifest_row()])
    try:
        path = service._kobo_backup_target_dir("default") / "Book One.epub"
        _write_epub(path)
        original = path.read_bytes()
        service.storage.backup_put({
            **_checkpoint(status="acquiring"),
            "sha256": sha, "size": size, "local_path": str(path),
        })
        before = service.storage.backup_get("kobo", "vault-1", "book-1")
        monkeypatch.setattr(service.storage, "backup_put",
                            lambda *_a, **_k: pytest.fail("legacy writer"))
        monkeypatch.setattr(service, "_execute_kobo_acquisition",
                            lambda *_a, **_k: pytest.fail("second GET"))
        with pytest.raises(
            ProviderConfigurationError,
            match="kobo_legacy_w2_retired_use_canonical_provider",
        ):
            service.sync_kobo_backup()
        assert service.storage.backup_get("kobo", "vault-1", "book-1") == before
        assert path.read_bytes() == original
        assert service.storage.placements() == []
    finally:
        service.close()
