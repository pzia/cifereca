"""P1 #1345: offline Kobo SHA audit is privacy-preserving and never repairs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca import cli
from cifereca.core.kobo_local_audit import (
    _read_hash_within, _unindexed_identity_files, kobo_local_reconciliation_audit,
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




def test_same_identity_two_kobo_accounts_do_not_share_catalogue_proposals(
    tmp_path, monkeypatch,
):
    """A catalogue product ID has no credential: it cannot prove either account."""
    service, session = _service(tmp_path, [])
    try:
        # Normal writes rightly forbid a second Kobo account for an identity.
        # This fixture represents legacy/corrupt SQLite rows that a read-only
        # audit must not silently use to cross-assign catalogue evidence.
        with service.storage._lock, service.storage._connect() as db:
            db.execute(
                "INSERT INTO provider_connections("
                "connection_id,identity_id,provider_id,credential_id,label,"
                "enabled,metadata_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                ("legacy-duplicate", "default", "kobo", "vault-2",
                 "Private second account", 1, "{}", 0, 0),
            )
        first = service._kobo_backup_target_dir("default") / "PRIVATE-shared.epub"
        _write_epub(first)
        sha = hashlib.sha256(first.read_bytes()).hexdigest()
        # Only the first account recorded a specific local artifact and SHA.
        _backup(service, "identical-product", first, credential="vault-1",
                sha=sha)
        _backup(service, "identical-product", credential="vault-2")
        # The catalogue knows only identity + product: not the account.
        _catalog(service, "identical-product", first)
        _no_mutation(service, monkeypatch)
        monkeypatch.setattr(
            session, "backup_manifest",
            lambda: pytest.fail("No network access in the audit"),
        )

        second_only = kobo_local_reconciliation_audit(
            service, credential_id="vault-2",
        )
        assert second_only["counts"]["backup_rows"] == 1
        assert second_only["counts"]["linked_files_verified"] == 0
        assert second_only["counts"]["unproven_catalogue_links"] == 0
        assert second_only["counts"]["missing_or_unlinked"] == 1
        assert second_only["downloads_authorized"] is False

        first_only = kobo_local_reconciliation_audit(
            service, credential_id="vault-1",
        )
        assert first_only["counts"]["linked_files_verified"] == 1
        assert first_only["counts"]["unproven_catalogue_links"] == 0
        assert first_only["counts"]["active_entitlements_unmaterialized"] == 0

        both = kobo_local_reconciliation_audit(service)
        assert len(both["accounts"]) == 2
        assert both["counts"]["linked_files_verified"] == 1
        assert both["counts"]["unproven_catalogue_links"] == 0
        assert both["mutations_authorized"] is False
        assert both["downloads_authorized"] is False
        assert first.read_bytes()
        for report in (second_only, first_only, both):
            serialized = json.dumps(report)
            assert "vault-1" not in serialized
            assert "vault-2" not in serialized
            assert "identical-product" not in serialized
            assert str(first) not in serialized
            assert sha not in serialized
    finally:
        service.close()


def test_single_kobo_credential_still_uses_catalogue_suggestions(tmp_path):
    """The conservative shared-identity rule must not suppress normal audits."""
    service, _session = _service(tmp_path, [])
    try:
        file = service._kobo_backup_target_dir("default") / "PRIVATE-catalogue.epub"
        _write_epub(file)
        _backup(service, "catalogue-only")
        _catalog(service, "catalogue-only", file)
        report = kobo_local_reconciliation_audit(
            service, credential_id="vault-1",
        )
        assert report["counts"]["linked_files_verified"] == 1
        assert report["counts"]["unproven_catalogue_links"] == 1
        assert report["downloads_authorized"] is False
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
        assert report["evidence_status"] == "no_verified_local_evidence"
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

    def count_probes(path, roots, *, budget=None):
        calls.append(path)
        return original(path, roots, budget=budget)

    monkeypatch.setattr(audit_module, "_read_hash_within", count_probes)
    try:
        report = kobo_local_reconciliation_audit(service)
        assert calls == [str(missing)]
        assert report["counts"]["linked_files_verified"] == 0
        assert report["counts"]["unverified_placements"] == 1
        assert report["counts"]["missing_or_unlinked"] == 0
    finally:
        service.close()


def test_audit_rejects_symlinked_root_ancestor_before_reading_other_identity(tmp_path):
    """O_NOFOLLOW on the final directory alone does not protect parent parts."""
    directory = tmp_path / "library"
    bob = directory / "bob"
    target = bob / "kobo"
    target.mkdir(parents=True)
    file = target / "private-book.epub"
    _write_epub(file)
    alice = directory / "alice"
    alice.symlink_to(bob, target_is_directory=True)
    # The linked source resolves to Bob's file, but the declared path claims
    # Alice. Never use the symlink to observe Bob's SHA.
    wrong_path = alice / "kobo" / "private-book.epub"
    assert _read_hash_within(str(wrong_path), (alice / "kobo", alice)) is None
    # Sanity: the same file is valid only with Bob's actual non-symlink root.
    assert _read_hash_within(str(file), (target,)) is not None
    # A symlink below a genuine root must be refused as well.
    trusted = directory / "trusted"
    trusted.mkdir()
    (trusted / "shortcut").symlink_to(target, target_is_directory=True)
    assert _read_hash_within(
        str(trusted / "shortcut" / "private-book.epub"), (trusted,),
    ) is None


def test_more_than_eight_catalogue_placements_marks_report_truncated(tmp_path):
    service, _session = _service(tmp_path, [])
    try:
        _backup(service, "many-placements")
        for index in range(10):
            service.storage.catalog_entry_put({
                "catalog_entry_id": f"local-placement-{index}",
                "title": "PRIVATE TITLE", "provider_id": "kobo",
                "identity_id": "default", "item_id": "many-placements",
                "local_state": "local",
                "metadata": {"path": str(tmp_path / f"private-{index}.epub")},
            })
        report = kobo_local_reconciliation_audit(service)
        assert report["truncated"] is True
        assert report["counts"]["linked_files_verified"] == 0
        assert "PRIVATE TITLE" not in json.dumps(report)
    finally:
        service.close()


def test_unindexed_candidates_are_only_local_file_names_and_never_orphan_proof(
    tmp_path, monkeypatch,
):
    service, session = _service(tmp_path, [])
    root = service._kobo_backup_target_dir("default")
    root.mkdir(parents=True, exist_ok=True)
    known = root / "PRIVATE-known.epub"
    unindexed = root / "PRIVATE-unindexed.epub"
    shared = root / "PRIVATE-another-provider.epub"
    flat = root.parent / "PRIVATE-flat.pdf"
    external = tmp_path / "PRIVATE-outside.pdf"
    for path in (known, unindexed, shared, flat, external):
        _write_epub(path)
    (root / "PRIVATE-link.epub").symlink_to(external)
    _backup(service, "PRIVATE-known", known)
    service.storage.catalog_entry_put({
        "title": "PRIVATE-other-provider", "provider_id": "gutenberg",
        "identity_id": "default", "item_id": "PRIVATE-other",
        "metadata": {"path": str(shared)}, "local_state": "local",
    })
    _no_mutation(service, monkeypatch)
    monkeypatch.setattr(session, "backup_manifest", lambda: pytest.fail("no network"))
    try:
        report = kobo_local_reconciliation_audit(service)
        assert report["counts"]["unindexed_regular_file_candidates"] == 2
        assert report["counts"]["identity_flat_file_candidates"] == 1
        assert report["counts"]["linked_files_verified"] == 1
        assert report["mutations_authorized"] is False
        assert report["downloads_authorized"] is False
        assert any(c["category"] == "unindexed_identity_file_candidate"
                   and c["decision"] == "manual_review_only"
                   for c in report["cases"])
        serialized = json.dumps(report)
        for private in ("PRIVATE", str(tmp_path), "vault-1", "default"):
            assert private not in serialized
        assert all(path.exists() for path in (known, unindexed, shared, flat, external))
    finally:
        service.close()


def test_unindexed_directory_scan_refuses_symlinked_ancestors_and_never_reads_bytes(
    tmp_path, monkeypatch,
):
    import builtins
    original_open = builtins.open
    original_os_open = __import__("os").open

    real_root = tmp_path / "SECRET_REAL" / "kobo"
    real_root.mkdir(parents=True)
    _write_epub(real_root / "PRIVATE.epub")
    linked_root = tmp_path / "SECRET_LINK"
    linked_root.symlink_to(real_root.parent, target_is_directory=True)
    # Do not scan another identity via a symlink ancestor.
    assert _unindexed_identity_files(
        linked_root / "kobo", set(), 256
    ) == (0, 0, True)
    # The no-content scan must never use the usual file-open API.
    def only_directory_opens(path, flags, *args, **kwargs):
        assert flags & getattr(__import__("os"), "O_DIRECTORY")
        return original_os_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", lambda *_a, **_kw:
                        pytest.fail("candidate scan must never read file bytes"))
    monkeypatch.setattr(__import__("os"), "open", only_directory_opens)
    try:
        count, inspected, incomplete = _unindexed_identity_files(real_root, set(), 256)
        assert (count, inspected, incomplete) == (1, 1, False)
    finally:
        monkeypatch.setattr(builtins, "open", original_open)


def test_unindexed_candidates_scope_and_global_budget(tmp_path):
    service, _session = _service(tmp_path, [])
    try:
        service.config.set("identities", [
            {"id": "default", "label": "Default", "directory_name": "default"},
            {"id": "reader-2", "label": "Reader 2", "directory_name": "reader-2"},
        ])
        service._sync_identity_configuration()
        service.storage.provider_connection_put({
            "identity_id": "reader-2", "provider_id": "kobo",
            "credential_id": "vault-2", "label": "Second Kobo",
        })
        first = service._kobo_backup_target_dir("default") / "PRIVATE-A.epub"
        second = service._kobo_backup_target_dir("reader-2") / "PRIVATE-B.epub"
        _write_epub(first)
        _write_epub(second)
        single = kobo_local_reconciliation_audit(service, credential_id="vault-1")
        assert single["counts"]["unindexed_regular_file_candidates"] == 1
        both = kobo_local_reconciliation_audit(service)
        assert both["counts"]["unindexed_regular_file_candidates"] == 2
        assert [a["counts"]["unindexed_regular_file_candidates"]
                for a in both["accounts"]] == [1, 1]
        assert "reader-2" not in json.dumps(both)
        # Directly exercise the bound without creating 257 files.
        assert _unindexed_identity_files(first.parent, set(), 0) == (0, 0, True)
        assert _unindexed_identity_files(first.parent, set(), 1) == (1, 1, False)
    finally:
        service.close()
