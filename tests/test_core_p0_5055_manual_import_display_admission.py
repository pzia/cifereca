"""P0 #5055: completed local import bytes are display evidence, not a loan."""
from __future__ import annotations

from hashlib import sha256
from types import SimpleNamespace as NS

import pytest

from cifereca.core import manual_import_display_admission as admission
from cifereca.core.bnf_import_provenance import remember_import


@pytest.fixture()
def receipt(tmp_path, monkeypatch):
    path = tmp_path / "private-book.epub"
    path.write_bytes(b"original imported bytes")
    artifact = "sha256:" + sha256(path.read_bytes()).hexdigest()
    row = {"artifact_id": artifact}
    calls = []
    service = NS(storage=NS(
        acquisition_get=lambda aid: row if aid == "purchase-1" else None,
        acquisition_bind_bnf_display_loan=lambda *args, **kwargs:
            calls.append((args, kwargs)),
    ))
    result = {
        "status": "complete", "acquisition_id": "purchase-1",
        "path": str(path),
    }
    monkeypatch.setattr(admission, "inspect_artifact",
                        lambda _: NS(valid=True, media_format="epub"))
    return service, result, row, path, calls


def _scope():
    return dict(identity_id="alice", credential_id="card",
                account_id="borrower", loan_id="loan-1", item_id="edition-1")


def test_unchanged_import_is_only_an_eligible_display_candidate(receipt):
    service, result, row, path, calls = receipt
    candidate = admission.verified_manual_import_candidate(service, result)
    assert candidate == ("purchase-1", path, row["artifact_id"])
    assert calls == []  # generic admission performs no writes
    remember_import(service, result, _scope())
    assert len(calls) == 1
    assert calls[0][1] == {
        "path": str(path), "artifact_id": row["artifact_id"],
        "scope": _scope(),
    }
    assert calls[0][0] == ("purchase-1",)


@pytest.mark.parametrize("change,value", [
    ("status", "pending"), ("acquisition_id", ""),
    ("acquisition_id", "foreign"), ("path", None),
    ("path", {"secret": "unhashable"}),
])
def test_invalid_or_foreign_import_cannot_bind(receipt, change, value):
    service, result, row, path, calls = receipt
    result[change] = value
    assert admission.verified_manual_import_candidate(service, result) is None
    remember_import(service, result, _scope())
    assert calls == []


def test_wrong_hash_and_changed_file_cannot_bind(receipt):
    service, result, row, path, calls = receipt
    row["artifact_id"] = "sha256:" + "a" * 64
    assert admission.verified_manual_import_candidate(service, result) is None
    remember_import(service, result, _scope())
    assert not calls
    row["artifact_id"] = "sha256:" + sha256(path.read_bytes()).hexdigest()
    path.write_bytes(b"changed")
    assert admission.verified_manual_import_candidate(service, result) is None


def test_symlink_never_confers_display_or_triggers_write(receipt):
    service, result, row, path, calls = receipt
    symlink = path.with_name("linked.epub")
    symlink.symlink_to(path)
    result["path"] = str(symlink)
    assert admission.verified_manual_import_candidate(service, result) is None
    remember_import(service, result, _scope())
    assert symlink.is_symlink() and path.is_file()
    assert not calls


def test_invalid_artifact_and_private_filesystem_error_fail_closed(receipt, monkeypatch):
    service, result, row, path, calls = receipt
    monkeypatch.setattr(admission, "inspect_artifact",
                        lambda _: NS(valid=False, media_format="epub"))
    assert admission.verified_manual_import_candidate(service, result) is None
    monkeypatch.setattr(admission, "inspect_artifact",
                        lambda _: (_ for _ in ()).throw(OSError("private/book/path")))
    assert admission.verified_manual_import_candidate(service, result) is None
    remember_import(service, result, _scope())
    assert not calls


def test_bnf_exact_scope_still_required_before_storage_write(receipt):
    service, result, row, path, calls = receipt
    bad = _scope()
    bad["account_id"] = ""
    remember_import(service, result, bad)
    assert calls == []
