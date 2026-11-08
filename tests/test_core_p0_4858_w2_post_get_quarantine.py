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
        assert first["reconcile_pending"] >= 1
        row = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert row["status"] == "acquiring"
        assert row["sha256"] is None
        assert not service.storage.origin_links()
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


def test_failure_before_executor_return_keeps_existing_error_contract(
    tmp_path: Path, monkeypatch,
):
    service = _service(tmp_path)
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("transport failed")),
    )
    try:
        first = service.sync_kobo_backup()
        assert first["errors"] >= 1
        assert service.storage.backup_get("kobo", "vault-1", "book-1")["status"] == "error"
    finally:
        service.close()
