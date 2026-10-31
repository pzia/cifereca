"""#3882: source sync report is atomic, scoped, bounded and never a raw trace."""
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from cifereca.core import sync_report
from cifereca.storage import Storage


ACCOUNT = SimpleNamespace(
    identity_id="private-profile-A", provider_id="numilog",
    credential_id="private-credential", unique_id="private-account",
)
SECOND = SimpleNamespace(
    identity_id="private-profile-B", provider_id="numilog",
    credential_id="private-credential", unique_id="private-account",
)


def _insert_source(storage, account, ref):
    with storage._lock, storage._connect() as db:
        db.execute(
            "INSERT INTO source_references("
            "source_reference_id,identity_id,provider_id,credential_id,account_id,"
            "branch_id,edition_id,right_id,reference_kind,synchronizable,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (ref, account.identity_id, account.provider_id, account.credential_id,
             account.unique_id, "purchases", ref, "right", "purchase", 1, 1, 1),
        )


def test_exact_source_count_never_borrows_other_profile(tmp_path):
    storage = Storage(tmp_path / "library.sqlite3")
    try:
        _insert_source(storage, ACCOUNT, "private-edition-A")
        _insert_source(storage, SECOND, "private-edition-B")
        counts = sync_report.audit_source_counts(storage, ACCOUNT)
        assert counts["rights_by_kind"] == {"purchase": 1}
        assert counts["rights_by_state"] == {"unknown": 1}
        assert counts["origin_link_count"] == 0
        assert counts["placement_reference_count"] == 0
        assert counts["physical_verification"] == "not_performed"
    finally:
        storage.close()


def test_new_report_has_only_closed_pseudonymized_evidence(tmp_path, monkeypatch):
    from cifereca import startup_revision
    monkeypatch.setattr(startup_revision, "build_identity", lambda: {
        "schema_version": 1, "status": "known",
        "commit_sha": "a" * 40, "build_version": "synthetic",
    })
    path = tmp_path / "report.zip"
    before = {"rights_by_kind": {"purchase": 1}}
    after = {"rights_by_kind": {"purchase": 2}}
    sync_report.write_sync_report(
        path, account=ACCOUNT, run_ref="b" * 32,
        before=before, after=after,
        result={"status": "partial", "downloaded": 1,
                "signed_url": "https://private.invalid/?token=secret",
                "secret": "PASSWORD_PRIVATE"},
        started_at="2026-10-31T00:00:00+00:00", exit_code=2,
    )
    assert os.stat(path).st_mode & 0o777 == 0o600
    with zipfile.ZipFile(path) as z:
        assert set(z.namelist()) == {
            "manifest.json", "sync-result.json",
            "acquisition-audit-before.json", "acquisition-audit-after.json",
            "events.jsonl", "summary.txt",
        }
        manifest = json.loads(z.read("manifest.json"))
        assert manifest["status"] == "partial"
        assert manifest["exit_code"] == 2
        assert manifest["build_identity"]["commit_sha"] == "a" * 40
        assert manifest["evidence_coverage"] == "canonical_sqlite_graph_without_filesystem"
        assert json.loads(z.read("sync-result.json")) == {
            "status": "partial", "downloaded": 1,
        }
        whole = b"".join(z.read(name) for name in z.namelist())
        for private in (b"private-profile-A", b"private-credential",
                        b"private-account", b"PASSWORD_PRIVATE", b"token=secret"):
            assert private not in whole
    with pytest.raises(FileExistsError):
        sync_report.write_sync_report(
            path, account=ACCOUNT, run_ref="b" * 32, before={}, after={},
            result={"status": "failed"}, started_at="start", exit_code=2,
        )
    assert path.is_file()


def test_symlink_destination_is_rejected_without_mutation(tmp_path):
    target = tmp_path / "private"
    target.write_bytes(b"PRIVATE")
    link = tmp_path / "report.zip"
    link.symlink_to(target)
    with pytest.raises(FileExistsError):
        sync_report.write_sync_report(
            link, account=ACCOUNT, run_ref="c" * 32, before={}, after={},
            result={"status": "success"}, started_at="start", exit_code=0,
        )
    assert target.read_bytes() == b"PRIVATE"
    assert link.is_symlink()


def test_unwritable_report_does_not_publish_partial_zip(tmp_path, monkeypatch):
    original = sync_report.os.link
    def refuse(*args, **kwargs):
        raise OSError("synthetic interruption")
    monkeypatch.setattr(sync_report.os, "link", refuse)
    path = tmp_path / "report.zip"
    with pytest.raises(OSError):
        sync_report.write_sync_report(
            path, account=ACCOUNT, run_ref="d" * 32, before={}, after={},
            result={"status": "failed"}, started_at="start", exit_code=2,
        )
    assert not path.exists()
    assert not list(tmp_path.glob(".cifereca-report-*"))
    monkeypatch.setattr(sync_report.os, "link", original)



def test_parent_directory_symlink_cannot_redirect_archive(tmp_path):
    actual = tmp_path / "real"
    actual.mkdir()
    symlink = tmp_path / "alias"
    symlink.symlink_to(actual, target_is_directory=True)
    with pytest.raises(FileExistsError):
        sync_report.write_sync_report(
            symlink / "report.zip", account=ACCOUNT, run_ref="e" * 32,
            before={}, after={}, result={"status": "failed"},
            started_at="start", exit_code=2,
        )
    assert not (actual / "report.zip").exists()


def test_report_pseudonym_uses_secret_ephemeral_key(tmp_path):
    source_refs = []
    for index in (1, 2):
        path = tmp_path / f"report-{index}.zip"
        sync_report.write_sync_report(
            path, account=ACCOUNT, run_ref="f" * 32,
            before={}, after={}, result={"status": "failed"},
            started_at="start", exit_code=2,
        )
        with zipfile.ZipFile(path) as z:
            source_refs.append(json.loads(z.read("manifest.json"))["source_ref"])
    assert len(source_refs[0]) == len(source_refs[1]) == 64
    assert source_refs[0] != source_refs[1]
