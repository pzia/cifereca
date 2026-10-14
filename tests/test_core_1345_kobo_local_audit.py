"""P1 #1345: offline Kobo SHA audit is privacy-preserving and never repairs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca import cli
from cifereca.core.kobo_local_audit import (
    _read_hash_within, kobo_local_reconciliation_audit,
)
from tests.test_kobo_backup_fault_isolation import _service, _write_epub


def _backup(service, item, path=None, *, credential="vault-1", identity="default", sha=None):
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": credential,
        "identity_id": identity, "item_id": item, "status": "complete",
        "title": f"Private-{item}",
        "revision_id": "r1", "format": "EPUB",
        "local_path": str(path) if path else None,
        "sha256": sha, "size": path.stat().st_size if path and path.is_file() else None,
    })


def _catalog(service, item, path, *, identity="default"):
    service.storage.catalog_entry_put({
        "title": f"Private-{item}", "provider_id": "kobo",
        "identity_id": identity, "item_id": item,
        "metadata": {"path": str(path), "secret": "PRIVATE_CATALOGUE"},
        "local_state": "local",
    })


def _no_mutation(service, monkeypatch):
    def denied(*_args, **_kwargs):
        pytest.fail("Kobo audit must not mutate, call a provider or create files")
    for name in ("backup_put", "metadata_set", "catalog_entry_put", "entitlement_put"):
        monkeypatch.setattr(service.storage, name, denied)
    for name in ("connect_configured", "sync_kobo_backup", "_execute_kobo_acquisition"):
        monkeypatch.setattr(service, name, denied)


def test_exact_binary_duplicates_and_divergent_same_product_are_only_dry_run(
    tmp_path, monkeypatch,
):
    service, session = _service(tmp_path, [])
    target = service._kobo_backup_target_dir("default")
    first = target / "PRIVATE-title-one.epub"
    copy = target / "PRIVATE-title-two.epub"
    different = target / "PRIVATE-title-three.epub"
    _write_epub(first)
    copy.write_bytes(first.read_bytes())
    different.write_bytes(b"PK\x03\x04NOT_THE_SAME_DOCUMENT")
    _backup(service, "private-book-1", first)
    _backup(service, "private-book-2", copy)
    _catalog(service, "private-book-1", different)
    db_before = {
        item: service.storage.backup_get("kobo", "vault-1", item)
        for item in ("private-book-1", "private-book-2")
    }
    _no_mutation(service, monkeypatch)
    monkeypatch.setattr(session, "backup_manifest", lambda: pytest.fail("no network"))
    try:
        report = kobo_local_reconciliation_audit(service)
        assert report["offline"] and report["read_only"] and report["dry_run"]
        assert not report["mutations_authorized"] and not report["downloads_authorized"]
        assert report["counts"]["backup_rows"] == 2
        assert report["counts"]["linked_files_verified"] == 3
        assert report["counts"]["binary_match_groups"] == 1
        assert report["counts"]["same_product_different_bytes"] == 1
        assert {
            "same_product_different_bytes", "confirmed_binary_match",
        } <= {row["category"] for row in report["cases"]}
        # Backup rows without an attested stored SHA are not enough to
        # declare an active entitlement safely materialized.
        assert report["accounts"][0]["account"] == "account-1"
        exported = json.dumps(report, ensure_ascii=False)
        for private in ("vault-1", "default", "PRIVATE", "private-book",
                        str(tmp_path), first.name, different.name,
                        hashlib.sha256(first.read_bytes()).hexdigest()):
            assert private not in exported
        for item, expected in db_before.items():
            assert service.storage.backup_get("kobo", "vault-1", item) == expected
        assert first.is_file() and copy.is_file() and different.is_file()
    finally:
        service.close()


def test_scope_isolates_account_and_refuses_legacy_outside_paths(tmp_path):
    service, session = _service(tmp_path, [])
    extra = tmp_path / "EBooks" / "reader-other" / "kobo"
    extra.mkdir(parents=True, exist_ok=True)
    external = tmp_path / "SECRET_OUTSIDE.epub"
    _write_epub(external)
    symlink = service._kobo_backup_target_dir("default") / "PRIVATE_LINK.epub"
    symlink.symlink_to(external)
    _backup(service, "private-external", external)
    _backup(service, "private-symlink", symlink)
    _backup(service, "private-missing", tmp_path / "EBooks" / "default" / "missing.epub")
    _backup(service, "private-outside-identity", extra / "private.epub")
    try:
        report = kobo_local_reconciliation_audit(service, credential_id="vault-1")
        assert report["scope"] == "single_account"
        assert report["counts"]["linked_files_verified"] == 0
        assert report["counts"]["unverified_placements"] >= 3
        assert report["counts"]["backup_rows"] == 4
        assert "SECRET_OUTSIDE" not in json.dumps(report)
        assert _read_hash_within(str(symlink), (symlink.parent,)) is None
        assert _read_hash_within(str(external), (symlink.parent,)) is None
        with pytest.raises(ValueError, match="unknown_kobo_audit_scope"):
            kobo_local_reconciliation_audit(service, credential_id="missing")
    finally:
        service.close()


def test_two_identities_are_not_merged_even_when_bytes_equal(tmp_path):
    service, _session = _service(tmp_path, [])
    try:
        service.config.set("identities", [
            {"id": "default", "label": "Default", "directory_name": "default"},
            {"id": "reader-2", "label": "Private Reader", "directory_name": "reader-2"},
        ])
        service._sync_identity_configuration()
        service.storage.provider_connection_put({
            "provider_id": "kobo", "credential_id": "vault-2",
            "identity_id": "reader-2", "label": "Account 2",
        })
        original = service._kobo_backup_target_dir("default") / "a.epub"
        second = service._kobo_backup_target_dir("reader-2") / "b.epub"
        _write_epub(original)
        second.write_bytes(original.read_bytes())
        _backup(service, "same-id", original, identity="default")
        _backup(service, "same-id", second, credential="vault-2", identity="reader-2")
        report = kobo_local_reconciliation_audit(service)
        assert len(report["accounts"]) == 2
        assert report["counts"]["binary_match_groups"] == 0
        assert report["counts"]["same_product_different_bytes"] == 0
        assert report["counts"]["linked_files_verified"] == 2
        filtered = kobo_local_reconciliation_audit(service, credential_id="vault-2")
        assert filtered["counts"]["backup_rows"] == 1
        assert filtered["accounts"][0]["account"] == "account-1"
        for private in ("reader-2", "vault-2", "same-id", "Private Reader"):
            assert private not in json.dumps(report)
            assert private not in json.dumps(filtered)
    finally:
        service.close()



def test_entitlements_without_verified_local_artifacts_are_manual_only(tmp_path):
    service, session = _service(tmp_path, [])
    target = service._kobo_backup_target_dir("default") / "verified.epub"
    _write_epub(target)
    _backup(service, "has-local", target,
            sha=hashlib.sha256(target.read_bytes()).hexdigest())
    for item in ("has-local", "missing-right", "missing-right"):
        service.storage.entitlement_put({
            "provider_id": "kobo", "credential_id": "vault-1",
            "identity_id": "default", "item_id": item,
            "entitlement_type": "owned", "state": "active",
        })
    service.storage.catalog_entry_put({
        "title": "PRIVATE orphan without product id",
        "provider_id": "kobo", "identity_id": "default",
        "local_state": "local", "metadata": {"path": "/home/PRIVATE.epub"},
    })
    try:
        report = kobo_local_reconciliation_audit(service)
        assert report["counts"]["active_entitlements_unmaterialized"] == 1
        assert report["counts"]["linked_files_verified"] == 1
        assert report["catalogue_entries_without_product_identity"] == 1
        assert any(case["category"] == "active_entitlement_without_verified_artifact"
                   for case in report["cases"])
        assert report["downloads_authorized"] is False
        assert "PRIVATE" not in json.dumps(report)
    finally:
        service.close()



def test_offline_cli_does_not_connect_or_modify_local_backup(tmp_path, monkeypatch, capsys):
    service, session = _service(tmp_path, [])
    _backup(service, "PRIVATE_CLI_ITEM")
    called = []
    monkeypatch.setattr(
        cli.CiferecaCore, "open",
        lambda **_kw: NS(library=service, close=lambda: called.append("close")),
    )
    _no_mutation(service, monkeypatch)
    try:
        assert cli.main(["kobo-audit", "--credential", "vault-1"]) == 0
        exported = capsys.readouterr().out
        report = json.loads(exported)
        assert report["offline"] is True
        assert report["counts"]["backup_rows"] == 1
        assert "vault-1" not in exported
        assert "PRIVATE_CLI_ITEM" not in exported
        assert called == ["close"]
    finally:
        service.close()


def test_missing_path_referenced_twice_is_checked_only_once(tmp_path, monkeypatch):
    """#1345: a failed backup path repeated by catalogue costs one probe."""
    import cifereca.core.kobo_local_audit as audit_module

    service, _session = _service(tmp_path, [])
    missing = service._kobo_backup_target_dir("default") / "PRIVATE-missing.epub"
    _backup(service, "private-duplicate-missing", missing)
    _catalog(service, "private-duplicate-missing", missing)
    calls = []
    original = audit_module._read_hash_within

    def count_probes(path, roots):
        calls.append(path)
        return original(path, roots)

    monkeypatch.setattr(audit_module, "_read_hash_within", count_probes)
    try:
        report = kobo_local_reconciliation_audit(service)
        assert calls == [str(missing)]
        assert report["counts"]["linked_files_verified"] == 0
        assert report["counts"]["unverified_placements"] == 1
        assert report["counts"]["missing_or_unlinked"] == 0
    finally:
        service.close()
