"""#4858 W2: once an opaque GET returns, failures cannot authorize another GET."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_kobo_backup_guard import _service, _write_epub


@pytest.mark.parametrize("failure", ["wrong_account", "missing_artifact", "invalid_artifact"])
def test_post_get_failure_keeps_acquiring_and_prevents_second_get(
    tmp_path: Path, monkeypatch, failure,
):
    service = _service(tmp_path)
    calls = []
    target = service._kobo_backup_target_dir("alice") / "received.epub"
    def execute(_item, _directory):
        calls.append("GET")
        if len(calls) > 1:
            pytest.fail("opaque GET must be at-most-once")
        if failure != "missing_artifact":
            _write_epub(target, b"post-get-bytes")
        if failure == "invalid_artifact":
            target.write_bytes(b"not-an-epub")
        return SimpleNamespace(
            path=target if failure != "missing_artifact" else None,
            account_id="another-borrower" if failure == "wrong_account" else "account",
            metadata={},
        )
    monkeypatch.setattr(service, "_execute_kobo_acquisition", execute)
    try:
        first = service.sync_kobo_backup()
        assert calls == ["GET"]
        assert first["downloaded"] == 0
        # Owner mismatch takes the pre-existing hard session-scope abort
        # rather than the generic post-GET reconcile counter.
        if failure != "wrong_account":
            assert first["reconcile_pending"] >= 1
        row = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert row["status"] == "acquiring"
        assert row["sha256"] is None
        with service.storage._lock, service.storage._connect() as db:
            assert db.execute("SELECT COUNT(*) FROM origin_links").fetchone()[0] == 0
        second = service.sync_kobo_backup()
        assert second["attempted"] == second["downloaded"] == 0
        assert second["reconcile_skipped"] >= 1
        assert calls == ["GET"]
        assert service.storage.backup_get("kobo", "vault-1", "book-1")["status"] in {
            "acquiring", "pending_acquisition",
        }
        if target.exists():
            assert target.read_bytes()
    finally:
        service.close()


def test_exception_without_executor_return_is_not_safe_to_retry(
    tmp_path: Path, monkeypatch,
):
    service = _service(tmp_path)
    calls = []
    def explode(*_args, **_kw):
        calls.append("executor_entered")
        raise RuntimeError("transport failed; delivery unknown")

    monkeypatch.setattr(service, "_execute_kobo_acquisition", explode)
    try:
        first = service.sync_kobo_backup()
        assert first["errors"] >= 1
        assert first["reconcile_pending"] >= 1
        assert service.storage.backup_get("kobo", "vault-1", "book-1")["status"] == "acquiring"
        replay = service.sync_kobo_backup()
        assert replay["attempted"] == replay["downloaded"] == 0
        assert calls == ["executor_entered"]
    finally:
        service.close()
