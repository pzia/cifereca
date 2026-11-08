"""P0 #4858 W2: an opaque executor exception is not a no-delivery receipt."""
from __future__ import annotations

from pathlib import Path

import pytest

from cifereca.kobo.vault import KoboVaultError
from tests.test_kobo_backup_guard import _service, _write_epub


@pytest.mark.parametrize("failure", [
    "bytes_then_exception", "exception_without_file", "provider_503",
])
def test_opaque_exception_quarantines_attempt_across_restart(
    tmp_path: Path, monkeypatch, failure: str,
):
    service = _service(tmp_path)
    artifact = tmp_path / "EBooks" / "alice" / "kobo" / "hidden-received.epub"
    attempts = []

    def opaque_executor(_item, _target):
        attempts.append(failure)
        if len(attempts) != 1:
            pytest.fail("opaque acquisition must not be dispatched twice")
        if failure == "bytes_then_exception":
            _write_epub(artifact, b"GET delivered before exception")
        if failure == "provider_503":
            raise KoboVaultError(
                "provider returned uncertain result",
                code="temporary", status_code=503,
            )
        raise OSError("ambiguous executor exception")

    monkeypatch.setattr(service, "_execute_kobo_acquisition", opaque_executor)
    try:
        first = service.sync_kobo_backup()
        row = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert first["attempted"] == first["errors"] == 1
        assert first["downloaded"] == 0
        assert first["reconcile_pending"] >= 1
        assert row["status"] == "acquiring"
        assert row["sha256"] is None and row["local_path"] is None
        with service.storage._lock, service.storage._connect() as db:
            assert db.execute("SELECT COUNT(*) FROM origin_links").fetchone()[0] == 0

        replay = service.sync_kobo_backup()
        assert replay["attempted"] == replay["downloaded"] == 0
        assert replay["reconcile_skipped"] >= 1
        assert attempts == [failure]
    finally:
        service.close()

    restarted = _service(tmp_path)
    monkeypatch.setattr(restarted, "_execute_kobo_acquisition", opaque_executor)
    try:
        after_restart = restarted.sync_kobo_backup()
        assert after_restart["attempted"] == after_restart["downloaded"] == 0
        assert after_restart["reconcile_skipped"] >= 1
        assert attempts == [failure]
        assert restarted.storage.backup_get(
            "kobo", "vault-1", "book-1",
        )["status"] in {"acquiring", "pending_acquisition"}
        if failure == "bytes_then_exception":
            assert artifact.is_file()
    finally:
        restarted.close()


def test_pre_dispatch_exception_can_keep_historical_retryable_error(
    tmp_path: Path, monkeypatch,
):
    service = _service(tmp_path)
    session = service._sessions[service._session_key("kobo", "vault-1")]
    dispatched = []

    def malformed_item(*_args, **_kwargs):
        raise ValueError("invalid manifest normalization")

    monkeypatch.setattr(session, "normalize_item", malformed_item)
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: dispatched.append("GET"),
    )
    try:
        first = service.sync_kobo_backup()
        assert first["attempted"] == first["errors"] == 1
        assert dispatched == []
        row = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert row["status"] == "error"
    finally:
        service.close()
