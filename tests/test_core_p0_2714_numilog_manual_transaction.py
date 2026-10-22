"""#2714-C3b: offline explicit Numilog placement, real SQLite rollback tests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
from dataclasses import replace

import pytest

from cifereca.models import Account
from cifereca.core.library_inventory import scan_identity_roots
from cifereca.core.numilog_manual_link_transaction import (
    ManualAttachmentRejected, reconcile_numilog_manual,
)
from cifereca.core.library_fresh_file_check import FreshFileCheck
from tests.test_sync_state_contract import _service
from tests.test_library_reconciliation import epub


@pytest.fixture
def rig(tmp_path, monkeypatch):
    service = _service(tmp_path)
    root = tmp_path / "EBooks" / "hugues"
    root.mkdir(parents=True, exist_ok=True)
    book = root / "PRIVATE_edition.epub"
    epub(book, title="PRIVATE_TITLE")
    observation, = scan_identity_roots({"default": (root,)}).files
    assert observation.sha256 and observation.structure_validation == "epub_structure_crc_ok"
    account = Account(
        display_name="PRIVATE Numilog", unique_id="PRIVATE-account",
        provider_id="numilog", credential_id="private-credential",
        provider_label="Numilog", identity_id="default",
    )
    service.accounts = [account]
    service.storage.provider_connection_put({
        "identity_id": "default", "provider_id": "numilog",
        "credential_id": account.credential_id, "label": "Numilog",
    })
    service.storage.entitlement_put({
        "entitlement_id": "private-right", "provider_id": "numilog",
        "credential_id": account.credential_id, "account_id": account.unique_id,
        "identity_id": "default", "item_id": "private-item",
        "entitlement_type": "owned", "state": "active",
        "metadata": {"source": "numilog.authenticated_library",
                     "title": "PRIVATE_TITLE"},
    })
    service.storage.artifact_put({
        "artifact_id": "sha256:" + observation.sha256,
        "sha256": observation.sha256, "size": observation.size,
        "media_format": "epub", "metadata": {},
    })
    service.storage.acquisition_put({
        "acquisition_id": "private-acquisition",
        "entitlement_id": "private-right", "provider_id": "numilog",
        "credential_id": account.credential_id, "account_id": account.unique_id,
        "identity_id": "default", "item_id": "private-item",
        "title": "PRIVATE_TITLE", "status": "complete",
        "artifact_id": "sha256:" + observation.sha256,
    })
    from cifereca.core import numilog_manual_link_transaction as engine
    monkeypatch.setattr(engine, "acquisition_lock_file",
                        lambda: tmp_path / "private-acquisition.lock")
    params = {
        "observation": observation, "identity_id": "default",
        "credential_id": account.credential_id,
        "account_id": account.unique_id, "item_id": "private-item",
    }
    try:
        yield service, book, params
    finally:
        service.close()


def _rows(service, table):
    with service.storage._lock, service.storage._connect() as db:
        return list(db.execute(f"SELECT * FROM {table} ORDER BY 1"))


def _review(rig, **kwargs):
    service, _book, options = rig
    return reconcile_numilog_manual(service, **{**options, **kwargs})


def _stage(rig):
    proposed = _review(rig)
    assert proposed["status"] == "preview"
    assert proposed["requires_explicit_confirmation"]
    assert proposed["plan_token"] and len(proposed["plan_token"]) == 64
    return proposed


def test_preview_cannot_write_then_confirm_creates_one_placement_only(rig):
    service, book, options = rig
    original = book.read_bytes()
    before = {name: _rows(service, name)
              for name in ("entitlements", "acquisition_ledger",
                           "artifacts", "placements", "catalog_entries")}
    plan = _stage(rig)
    assert {name: _rows(service, name) for name in before} == before
    assert all(token not in json.dumps(plan)
               for token in ("PRIVATE", options["observation"].sha256, str(book)))
    changed = _review(rig, confirmation=plan["plan_token"])
    assert changed["status"] == "applied" and changed["changed"] == 1
    assert changed["rights_mutations"] == changed["file_mutations"] == 0
    assert book.read_bytes() == original
    rows = _rows(service, "placements")
    assert len(rows) == 1
    assert service.storage.placement_for_path(book)["provider_id"] == "numilog"
    assert service.storage.placement_for_path(book)["source_item_id"] == "private-item"
    for name in before:
        if name != "placements":
            assert _rows(service, name) == before[name]
    assert _review(rig)["status"] == "already_linked"
    assert _review(rig, confirmation=plan["plan_token"])["status"] == "already_linked"
    assert len(_rows(service, "placements")) == 1


@pytest.mark.parametrize("mutation,expected", [
    ("right_revoked", "owned_right_not_attested"),
    ("foreign_entitlement", "ambiguous_or_missing_right"),
    ("wrong_purchase", "purchase_provenance_unattested"),
    ("foreign_receipt", "acquisition_unproven"),
    ("no_receipt", "acquisition_ambiguous_or_missing"),
    ("other_artifact", "artifact_file_mismatch"),
    ("foreign_connection", "source_not_attested"),
    ("foreign_placement", "placement_conflict"),
    ("ambiguous_edition", "placement_ambiguous"),
])
def test_incomplete_or_conflicting_evidence_rejected_before_write(
    rig, mutation, expected,
):
    service, book, options = rig
    with service.storage._lock, service.storage._connect() as db:
        if mutation == "right_revoked":
            db.execute("UPDATE entitlements SET state='expired'")
        elif mutation == "foreign_entitlement":
            db.execute("INSERT INTO entitlements "
                       "(entitlement_id,provider_id,credential_id,account_id,item_id,"
                       "entitlement_type,state,identity_id,metadata_json,discovered_at,updated_at)"
                       "VALUES ('second','numilog',?,'another','private-item','owned',"
                       "'active','other','{}',1,1)", (options["credential_id"],))
        elif mutation == "wrong_purchase":
            db.execute("UPDATE entitlements SET metadata_json='{}'")
        elif mutation == "foreign_receipt":
            db.execute("UPDATE acquisition_ledger SET identity_id='alex'")
        elif mutation == "no_receipt":
            db.execute("DELETE FROM acquisition_ledger")
        elif mutation == "other_artifact":
            db.execute("UPDATE artifacts SET size=size+1")
        elif mutation == "foreign_connection":
            db.execute("UPDATE provider_connections SET identity_id='alex'")
        elif mutation == "foreign_placement":
            db.execute("INSERT INTO placements "
                       "(placement_id,artifact_id,path,identity_id,provider_id,"
                       "source_item_id,state,created_at,updated_at)"
                       "VALUES ('foreign',?,?,'alex','kobo','wrong','present',1,1)",
                       ("sha256:" + options["observation"].sha256, str(book)))
        elif mutation == "ambiguous_edition":
            db.execute("INSERT INTO placements "
                       "(placement_id,artifact_id,path,identity_id,provider_id,"
                       "source_item_id,state,created_at,updated_at)"
                       "VALUES ('one',?,?,'default','numilog','private-item',"
                       "'present',1,1)",
                       ("sha256:" + options["observation"].sha256,
                        str(book.with_name("one.epub"))))
            db.execute("INSERT INTO placements "
                       "(placement_id,artifact_id,path,identity_id,provider_id,"
                       "source_item_id,state,created_at,updated_at)"
                       "VALUES ('two',?,?,'default','numilog','private-item',"
                       "'present',1,1)",
                       ("sha256:" + options["observation"].sha256,
                        str(book.with_name("two.epub"))))
    before = _rows(service, "placements")
    with pytest.raises(ManualAttachmentRejected, match="^" + expected + "$"):
        _review(rig)
    assert _rows(service, "placements") == before


def test_stale_preview_refuses_changed_receipt_without_writes(rig):
    service, _book, _ = rig
    plan = _stage(rig)
    with service.storage._lock, service.storage._connect() as db:
        db.execute("UPDATE acquisition_ledger SET updated_at=updated_at+1")
    before = _rows(service, "placements")
    with pytest.raises(ManualAttachmentRejected, match="^preview_changed$"):
        _review(rig, confirmation=plan["plan_token"])
    assert _rows(service, "placements") == before


def test_cross_identity_revocation_and_config_change_rejected(rig):
    service, _book, _ = rig
    plan = _stage(rig)
    service.current_identity = service.identity_by_id("alex")
    with pytest.raises(ManualAttachmentRejected, match="^identity_not_active$"):
        _review(rig, confirmation=plan["plan_token"])
    service.current_identity = service.identity_by_id("default")
    service.config.set("library.directory", "/tmp/PRIVATE-other-library")
    with pytest.raises(ManualAttachmentRejected):
        _review(rig, confirmation=plan["plan_token"])
    assert not _rows(service, "placements")


def test_replaced_file_after_preview_fails_without_mutation(rig):
    service, book, _ = rig
    plan = _stage(rig)
    book.write_bytes(book.read_bytes() + b"mutated")
    with pytest.raises(ManualAttachmentRejected, match="^file_revalidation_failed$"):
        _review(rig, confirmation=plan["plan_token"])
    assert not _rows(service, "placements")


def test_failure_after_insert_uses_sql_rollback(rig, monkeypatch):
    from cifereca.core import numilog_manual_link_transaction as engine

    plan = _stage(rig)
    original = engine.revalidate_observed_file
    calls = [0]

    def fail_second(*a, **kwargs):
        calls[0] += 1
        return (original(*a, **kwargs) if calls[0] == 1
                else FreshFileCheck(False, "file_changed"))

    monkeypatch.setattr(engine, "revalidate_observed_file", fail_second)
    with pytest.raises(ManualAttachmentRejected, match="^file_changed_before_commit$"):
        _review(rig, confirmation=plan["plan_token"])
    assert calls[0] == 2
    assert not _rows(rig[0], "placements")


def test_attack_fields_never_echo_private_data(rig):
    service, book, options = rig
    try:
        _review(rig, confirmation="PRIVATE /home/account/9780306406157")
    except ManualAttachmentRejected as exc:
        assert str(exc) == "invalid_confirmation"
        assert "PRIVATE" not in str(exc)
    else:
        pytest.fail("hostile confirmation unexpectedly accepted")
    with pytest.raises(ManualAttachmentRejected, match="^source_scope_changed$"):
        _review(rig, observation=replace(options["observation"], identity_id="alex"))
    assert not _rows(service, "placements")


def _insert_catalogue_refs(service, placement_id, count, *, foreign_last=False):
    """Synthetic references to the same placement; no private names enter errors."""
    with service.storage._lock, service.storage._connect() as db:
        for index in range(count):
            foreign = foreign_last and index == count - 1
            db.execute(
                "INSERT INTO catalog_entries "
                "(catalog_entry_id,placement_id,title,identity_id,provider_id,item_id,"
                "local_state,metadata_json,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,'present','{}',1,1)",
                (f"catalog-reference-{index}", placement_id, "Synthetic edition",
                 "other" if foreign else "default",
                 "kobo" if foreign else "numilog",
                 "another-edition" if foreign else "private-item"),
            )


def test_existing_link_with_two_matching_catalogue_references_remains_idempotent(rig):
    service, book, _params = rig
    plan = _stage(rig)
    assert _review(rig, confirmation=plan["plan_token"])["status"] == "applied"
    placement_id = service.storage.placement_for_path(book)["placement_id"]
    _insert_catalogue_refs(service, placement_id, 2)
    assert _review(rig)["status"] == "already_linked"
    assert len(_rows(service, "placements")) == 1


def test_catalogue_reference_overflow_cannot_hide_foreign_edition_on_existing_link(rig):
    """The fourth row is outside the LIMIT 3 projection, never silently ignored."""
    service, book, _params = rig
    plan = _stage(rig)
    assert _review(rig, confirmation=plan["plan_token"])["status"] == "applied"
    placement_id = service.storage.placement_for_path(book)["placement_id"]
    _insert_catalogue_refs(service, placement_id, 4, foreign_last=True)
    before = _rows(service, "placements")
    with pytest.raises(ManualAttachmentRejected, match="^catalogue_evidence_ambiguous$"):
        _review(rig)
    with pytest.raises(ManualAttachmentRejected, match="^catalogue_evidence_ambiguous$"):
        _review(rig, confirmation=plan["plan_token"])
    assert _rows(service, "placements") == before


def test_commit_stamp_callback_runs_once_only_after_actual_durable_placement(rig):
    calls = []
    # Read-only previews and already-linked no-ops cannot refresh a private
    # cached stamp; only a real successful SQL placement may acknowledge.
    preview = _review(rig, on_committed=lambda: calls.append("commit"))
    assert preview["status"] == "preview"
    assert calls == []
    applied = _review(
        rig, confirmation=preview["plan_token"],
        on_committed=lambda: calls.append("commit"),
    )
    assert applied["status"] == "applied"
    assert calls == ["commit"]
    again = _review(rig, on_committed=lambda: calls.append("replay"))
    assert again["status"] == "already_linked"
    assert calls == ["commit"]


def test_failed_after_commit_observer_does_not_lie_about_durable_write(rig):
    def observer():
        raise RuntimeError("PRIVATE observer failure")

    plan = _stage(rig)
    result = _review(rig, confirmation=plan["plan_token"],
                     on_committed=observer)
    assert result["status"] == "applied"
    assert len(_rows(rig[0], "placements")) == 1
    assert "PRIVATE" not in json.dumps(result)
