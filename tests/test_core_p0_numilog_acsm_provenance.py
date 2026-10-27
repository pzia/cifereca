"""#2112: Numilog's ADEPT ACSM hash is not the owned edition provenance.

Real Storage, LocalCatalog, receipt and SHA checks; synthetic EPUBs only.
The original manual-acsm ledger must independently attest the same bytes.
"""
from __future__ import annotations

from pathlib import Path
import hashlib
import sqlite3

import pytest

from cifereca.providers import ProviderConfigurationError
from tests.test_kobo_backup_fault_isolation import _write_epub
from tests.test_numilog_owned_library_download import case


@pytest.fixture
def acsm_case(case):
    source_sha = "a" * 64
    service = case.service
    account = case.account
    manual_id = "manual-acsm:" + hashlib.sha256(
        (account.identity_id + ":" + source_sha).encode(),
    ).hexdigest()[:32]
    calls = []
    def deliver(item, root):
        calls.append(item.record_id)
        destination = Path(root) / ("acsm-" + source_sha) / "owned.epub"
        destination.parent.mkdir(exist_ok=True, parents=True)
        _write_epub(destination)
        indexed = service.catalog_index_path(
            destination, title="Synthetic owned book",
            item_id=source_sha, provider_id="numilog",
            identity_id=account.identity_id,
        )
        assert indexed["status"] == "indexed"
        service.storage.acquisition_put({
            "acquisition_id": manual_id,
            "provider_id": "numilog",
            "credential_id": "manual",
            "identity_id": account.identity_id,
            "item_id": source_sha,
            "status": "complete",
            "artifact_id": indexed["artifact_id"],
            "metadata": {
                "source_acsm_sha256": source_sha, "path": str(destination),
            },
        })
        return destination, "https://private.invalid?auth=SIGNED_PRIVATE"
    case.session.download_direct = deliver
    case.source_sha = source_sha
    case.manual_id = manual_id
    case.deliveries = calls
    return case


def _delivered(case):
    acquired = case.service.acquire_direct_item(case.item)
    receipt_id = acquired.metadata["numilog_checkpoint_id"]
    path = acquired.path
    assert path.is_file()
    placed = case.service.storage.placement_for_path(path)
    assert placed["source_item_id"] == case.source_sha
    return acquired, receipt_id, path


def test_exact_numilog_acsm_delivery_reconciles_existing_file_and_two_scans(
    acsm_case,
):
    case = acsm_case
    acquired, receipt_id, path = _delivered(case)
    service = case.service
    before = path.read_bytes()
    initial = service.storage.placement_for_path(path)
    assert initial["source_item_id"] == case.source_sha
    assert initial["artifact_id"].startswith("sha256:")
    assert initial["provider_id"] == "numilog"

    result = service.resume_numilog_materialization(receipt_id)
    assert result.indexed["status"] == "indexed"
    assert result.path == path
    placed = service.storage.placement_for_path(path)
    assert placed["source_item_id"] == case.item.record_id
    assert placed["placement_id"] == initial["placement_id"]
    entries = [
        x for x in service.storage.catalog_entries()
        if x["placement_id"] == placed["placement_id"]
    ]
    assert len(entries) == 1 and entries[0]["item_id"] == case.item.record_id
    assert service.storage.acquisition_get(receipt_id)["status"] == "complete"
    assert len(case.deliveries) == 1
    for _ in range(2):
        service.catalog_scan()
        reread = service.storage.placement_for_path(path)
        assert reread["source_item_id"] == case.item.record_id
        assert reread["artifact_id"] == initial["artifact_id"]
        rows = [x for x in service.storage.catalog_entries()
                if x["placement_id"] == placed["placement_id"]]
        assert len(rows) == 1 and rows[0]["item_id"] == case.item.record_id
        assert path.read_bytes() == before
    for verify_scan in (False, True):
        library = service.library_entries(
            identity_id=case.account.identity_id, scan=verify_scan,
        )
        # Reconciliation gives ONE content-hash row with both the local
        # artifact and the exact private Numilog possession; no duplicate
        # remote_owned card survives a cache refresh / recursive disk scan.
        matching = [
            row for row in library
            if row.get("artifact_id") == initial["artifact_id"]
        ]
        assert len(matching) == 1
        book = matching[0]
        assert book["local"] is True and book["state"] == "local"
        assert book["identity_id"] == case.account.identity_id
        assert any(
            ref.get("provider_id") == "numilog"
            and ref.get("item_id") == case.item.record_id
            for ref in book["source_refs"]
        )
        assert any(
            p.get("entitlement_id") == case.right["entitlement_id"]
            and p.get("item_id") == case.item.record_id
            for p in book["possessions"]
        )
        assert not any(
            row is not book and row.get("item_id") == case.item.record_id
            and row.get("state") == "remote_owned"
            for row in library
        )
    assert len(case.deliveries) == 1


@pytest.mark.parametrize("mutation", [
    "missing_manual_ledger", "manual_foreign_owner",
    "manual_wrong_artifact", "manual_wrong_acsm",
    "placement_foreign_owner", "catalog_wrong_purchase",
    "second_catalog_row", "same_owner_artifact_placement",
])
def test_ambiguous_acsm_provenance_stays_pending_without_network(
    acsm_case, mutation,
):
    case = acsm_case
    acquired, receipt_id, path = _delivered(case)
    service = case.service
    storage = service.storage
    placed = storage.placement_for_path(path)
    if mutation == "missing_manual_ledger":
        with storage._connect() as db:
            db.execute("DELETE FROM acquisition_ledger WHERE acquisition_id=?",
                       (case.manual_id,))
    elif mutation == "manual_foreign_owner":
        with storage._connect() as db:
            db.execute("UPDATE acquisition_ledger SET identity_id='foreign' "
                       "WHERE acquisition_id=?", (case.manual_id,))
    elif mutation == "manual_wrong_artifact":
        with storage._connect() as db:
            db.execute("UPDATE acquisition_ledger SET artifact_id='sha256:wrong' "
                       "WHERE acquisition_id=?", (case.manual_id,))
    elif mutation == "manual_wrong_acsm":
        manual = storage.acquisition_get(case.manual_id)
        storage.acquisition_update_state(
            case.manual_id, "complete",
            metadata={"source_acsm_sha256": "f" * 64},
        )
    elif mutation == "placement_foreign_owner":
        with storage._connect() as db:
            db.execute("UPDATE placements SET identity_id='foreign' "
                       "WHERE placement_id=?", (placed["placement_id"],))
    elif mutation == "catalog_wrong_purchase":
        with storage._connect() as db:
            db.execute("UPDATE catalog_entries SET item_id='foreign-purchase' "
                       "WHERE placement_id=?", (placed["placement_id"],))
    elif mutation == "second_catalog_row":
        storage.catalog_entry_put({
            "catalog_entry_id": "foreign-second-entry",
            "artifact_id": placed["artifact_id"],
            "placement_id": placed["placement_id"],
            "identity_id": case.account.identity_id,
            "provider_id": "numilog",
            "item_id": "other-purchase", "local_state": "local",
            "title": "Unrelated title",
        })
    else:
        foreign = path.parent / "other.epub"
        _write_epub(foreign)
        storage.placement_put({
            "placement_id": "foreign-second-placement",
            "artifact_id": placed["artifact_id"], "path": str(foreign),
            "identity_id": case.account.identity_id, "provider_id": "numilog",
            "source_item_id": "different-purchase", "state": "present",
        })
    before_bytes = path.read_bytes()
    with pytest.raises(ProviderConfigurationError):
        service.resume_numilog_materialization(receipt_id)
    assert storage.acquisition_get(receipt_id)["status"] == "attention_required"
    assert storage.placement_for_path(path)["source_item_id"] == case.source_sha
    assert path.read_bytes() == before_bytes
    assert len(case.deliveries) == 1


def test_rejected_sql_catalog_update_rolls_back_placement_then_offline_resume(
    acsm_case,
):
    case = acsm_case
    acquired, receipt_id, path = _delivered(case)
    service = case.service
    storage = service.storage
    with storage._connect() as db:
        db.execute(
            "CREATE TRIGGER block_owned_acsm_catalog BEFORE UPDATE OF item_id "
            "ON catalog_entries WHEN NEW.item_id<>OLD.item_id "
            "BEGIN SELECT RAISE(ABORT,'injected_catalog_failure'); END"
        )
    before_bytes = path.read_bytes()
    with pytest.raises(ProviderConfigurationError):
        service.resume_numilog_materialization(receipt_id)
    assert storage.placement_for_path(path)["source_item_id"] == case.source_sha
    assert storage.acquisition_get(receipt_id)["status"] == "attention_required"
    assert len(case.deliveries) == 1
    with storage._connect() as db:
        db.execute("DROP TRIGGER block_owned_acsm_catalog")
    result = service.resume_numilog_materialization(receipt_id)
    assert result.indexed["status"] == "indexed"
    assert storage.placement_for_path(path)["source_item_id"] == case.item.record_id
    assert path.read_bytes() == before_bytes
    assert len(case.deliveries) == 1


@pytest.mark.parametrize("mutation", ["tamper_bytes", "symlink_external"])
def test_changed_acsm_output_bytes_or_symlink_never_adopts_purchase(
    acsm_case, tmp_path, mutation,
):
    case = acsm_case
    acquired, receipt_id, path = _delivered(case)
    storage = case.service.storage
    if mutation == "tamper_bytes":
        path.write_bytes(b"modified")
    else:
        target = tmp_path / "untrusted.epub"
        _write_epub(target)
        path.unlink()
        path.symlink_to(target)
    with pytest.raises(ProviderConfigurationError):
        case.service.resume_numilog_materialization(receipt_id)
    # After a symlink swap, a path lookup resolves outside the stored key.
    placed = storage.placements()
    assert any(p["source_item_id"] == case.source_sha
               and p["provider_id"] == "numilog" for p in placed)
    assert storage.acquisition_get(receipt_id)["status"] == "attention_required"
    assert len(case.deliveries) == 1


@pytest.mark.parametrize(("media_format", "preexisting"), [
    ("epub", False), ("pdf", False), ("epub", True), ("pdf", True),
])
def test_real_core_acsm_import_complete_or_unchanged_reconciles_private_purchase(
    case, monkeypatch, tmp_path, media_format, preexisting,
):
    """Actual LibraryService.import_acsm, not a forged manual ledger.

    The DRM runtime and provider HTTP alone are replaced with deterministic
    local fixtures. Core performs its real staging, receipt, catalogue and
    two distinct acquisition-lock operations.
    """
    import cifereca.services as facade

    service = case.service
    source = tmp_path / "fictional-acsm.acsm"
    provider_format = (
        "application/pdf" if media_format == "pdf"
        else "application/epub+zip"
    )
    source.write_bytes(
        ("<fulfillment><id>fake-order</id><format>" + provider_format
         + "</format></fulfillment>").encode()
    )
    monkeypatch.setattr(
        facade, "data_dir", lambda: tmp_path / "staging-runtime",
    )
    monkeypatch.setattr(
        service, "resolve_adept_profile_for_identity",
        lambda identity_id: ("synthetic-adept", "identity"),
    )
    monkeypatch.setattr(
        service, "_originating_adept_profile_id", lambda *_a: None,
    )
    drm_calls = []
    def synthetic_drm(request):
        drm_calls.append(request)
        destination = Path(request["destination"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        if media_format == "epub":
            _write_epub(destination)
        else:
            destination.write_bytes(
                b"%PDF-1.4\n% synthetic output\n"
                b"1 0 obj << /Type /Catalog >> endobj\n%%EOF\n"
            )
        return {"ok": True, "artifact": {"path": str(destination)}}
    monkeypatch.setattr(
        service, "execute_acquisition_request", synthetic_drm,
    )

    def core_import():
        return service.import_acsm(
            source, provider_id="numilog",
            identity_id=case.account.identity_id,
            title="Fictional purchase",
        )

    first = core_import() if preexisting else None
    if first is not None:
        assert first["status"] == "complete"
        assert len(drm_calls) == 1

    download_calls = []
    def download(item, directory):
        download_calls.append(item.record_id)
        returned = core_import()
        assert returned["status"] == (
            "unchanged" if preexisting else "complete"
        )
        path = Path(returned["path"])
        assert path.parent.parent == Path(directory)
        return path, "https://private.invalid?auth=SIGNED_NEVER_STORE"
    case.session.download_direct = download

    acquired = service.acquire_direct_item(case.item)
    checkpoint = acquired.metadata["numilog_checkpoint_id"]
    initial = service.storage.placement_for_path(acquired.path)
    assert initial is not None
    assert initial["source_item_id"] == hashlib.sha256(
        source.read_bytes(),
    ).hexdigest()
    assert acquired.path.parent.name.startswith("acsm-")
    assert acquired.path.suffix == "." + media_format
    assert len(drm_calls) == 1 and len(download_calls) == 1

    indexed = service.resume_numilog_materialization(checkpoint)
    assert indexed.indexed["status"] == "indexed"
    assert service.storage.placement_for_path(acquired.path)[
        "source_item_id"
    ] == case.item.record_id
    assert service.storage.acquisition_get(checkpoint)["status"] == "complete"

    for scan in (False, True):
        library = service.library_entries(
            identity_id=case.account.identity_id, scan=scan,
        )
        local = [
            row for row in library
            if row.get("artifact_id") == initial["artifact_id"]
            and row.get("local") is True
        ]
        assert len(local) == 1
        assert any(
            p.get("entitlement_id") == case.right["entitlement_id"]
            for p in local[0]["possessions"]
        )
    assert len(drm_calls) == len(download_calls) == 1


def test_crash_after_atomic_acsm_adoption_resumes_index_only_without_http(
    acsm_case, monkeypatch,
):
    case = acsm_case
    acquired, receipt_id, path = _delivered(case)
    service = case.service
    storage = service.storage
    original_index = service.catalog_index_path

    def interrupted_after_sql(*_args, **_kwargs):
        raise RuntimeError("synthetic interruption after atomic adoption")

    monkeypatch.setattr(service, "catalog_index_path", interrupted_after_sql)
    with pytest.raises(ProviderConfigurationError):
        service.resume_numilog_materialization(receipt_id)
    placement = storage.placement_for_path(path)
    assert placement["source_item_id"] == case.item.record_id
    linked = [row for row in storage.catalog_entries()
              if row["placement_id"] == placement["placement_id"]]
    assert len(linked) == 1 and linked[0]["item_id"] == case.item.record_id
    assert storage.acquisition_get(receipt_id)["status"] == "attention_required"
    assert len(case.deliveries) == 1

    monkeypatch.setattr(service, "catalog_index_path", original_index)
    completed = service.resume_numilog_materialization(receipt_id)
    assert completed.indexed["status"] == "indexed"
    assert storage.acquisition_get(receipt_id)["status"] == "complete"
    assert storage.placement_for_path(path)["source_item_id"] == case.item.record_id
    assert len(case.deliveries) == 1


@pytest.mark.parametrize("mutation", [
    "missing_broker_receipt", "incomplete_broker_receipt",
    "foreign_broker_owner", "wrong_broker_artifact", "wrong_acsm_sha",
])
def test_publication_guard_does_not_trust_unattested_existing_acsm_file(
    acsm_case, mutation,
):
    case = acsm_case
    service = case.service
    storage = service.storage
    root = service.library_identity_provider_dir(case.account.identity_id, "numilog")
    path, _link = case.session.download_direct(case.item, root)
    assert path.is_file() and len(case.deliveries) == 1
    if mutation == "missing_broker_receipt":
        with storage._connect() as db:
            db.execute(
                "DELETE FROM acquisition_ledger WHERE acquisition_id=?",
                (case.manual_id,),
            )
    elif mutation == "incomplete_broker_receipt":
        storage.acquisition_update_state(case.manual_id, "retryable_error")
    elif mutation == "foreign_broker_owner":
        with storage._connect() as db:
            db.execute(
                "UPDATE acquisition_ledger SET identity_id='another' "
                "WHERE acquisition_id=?", (case.manual_id,),
            )
    elif mutation == "wrong_broker_artifact":
        with storage._connect() as db:
            db.execute(
                "UPDATE acquisition_ledger SET artifact_id='sha256:wrong' "
                "WHERE acquisition_id=?", (case.manual_id,),
            )
    else:
        storage.acquisition_update_state(
            case.manual_id, "complete",
            metadata={"source_acsm_sha256": "f" * 64},
        )
    # A publication merely named acsm-<sha> must not pass the Core guard.
    # No second provider I/O, no file rewrite, and no Numilog owned receipt.
    original = path.read_bytes()
    case.session.download_direct = lambda *_args: pytest.fail(
        "unproven pre-existing ACSM cannot trigger provider I/O"
    )
    with pytest.raises(ProviderConfigurationError, match="fichiers locaux non attestés"):
        service.acquire_direct_item(case.item)
    assert path.read_bytes() == original
    assert len(case.deliveries) == 1
    assert not any(
        r["item_id"] == case.item.record_id
        for r in storage.acquisitions(provider_id="numilog")
    )


@pytest.mark.parametrize("providers", [("numilog", "numilog"), ("kobo", "paris")])
def test_independent_copies_in_three_profiles_do_not_block_exact_local_adoption(
    acsm_case, tmp_path, monkeypatch, providers,
):
    case = acsm_case
    acquired, receipt_id, path = _delivered(case)
    service, storage = case.service, case.service.storage
    original = path.read_bytes()
    copies = []
    for index, provider in enumerate(providers):
        owner = f"independent-owner-{index}"
        storage.identity_put({"identity_id": owner, "label": owner})
        copy = Path(service.config.get("library.directory")) / owner / provider / "copy.epub"
        copy.parent.mkdir(parents=True)
        copy.write_bytes(original)
        result = service.catalog_index_path(
            copy, item_id=f"independent-item-{index}", provider_id=provider, identity_id=owner,
        )
        assert result["status"] == "indexed"
        placement = storage.placement_for_path(copy)
        entry = next(row for row in storage.catalog_entries()
                     if row["placement_id"] == placement["placement_id"])
        ledger_id = f"independent-receipt-{index}"
        storage.acquisition_put({
            "acquisition_id": ledger_id, "provider_id": provider, "identity_id": owner,
            "credential_id": f"independent-credential-{index}",
            "account_id": f"independent-account-{index}", "item_id": placement["source_item_id"],
            "artifact_id": placement["artifact_id"], "status": "complete",
            "metadata": {"path": str(copy), "origin": "synthetic-independent-delivery"},
        })
        copies.append((copy, placement, entry, storage.acquisition_get(ledger_id)))
    before_rights = storage.entitlements()
    for _ in range(2):
        result = service.resume_numilog_materialization(receipt_id)
        assert result.path == path and result.indexed["status"] == "indexed"
        assert storage.acquisition_get(receipt_id)["status"] == "complete"
        assert storage.placement_for_path(path)["source_item_id"] == case.item.record_id
        assert path.read_bytes() == original
        assert storage.entitlements() == before_rights
        for copy, placement, entry, ledger in copies:
            assert copy.read_bytes() == original
            assert storage.placement_get(placement["placement_id"]) == placement
            assert storage.catalog_entry_get(entry["catalog_entry_id"]) == entry
            assert storage.acquisition_get(ledger["acquisition_id"]) == ledger
    assert len(case.deliveries) == 1
    assert len([p for p in storage.placements()
                if p["artifact_id"] == result.artifact_id]) == 3

    # Automatic inventory after the explicit repair recognizes presence,
    # rather than replacing the EPUB because other profiles share its SHA.
    from time import time
    from cifereca.brokers import SynchronizationBrokerImpl
    from cifereca.core import numilog_materialization_startup as startup
    from tests.test_core_p0_numilog_auto_materialization import _authorize
    broker = SynchronizationBrokerImpl(service)
    _authorize(broker, broker._source_id(case.account))
    case.session.library = lambda: [case.item]
    case.session.download_direct = lambda *_a, **_k: pytest.fail("existing file downloaded again")
    for offset in (0, 7200):
        monkeypatch.setattr(startup, "time", lambda: time() + offset)
        outcome = startup.execute(broker, case.account.identity_id, "startup")
        assert outcome.ok and outcome.facts["status"] == "complete"
        assert outcome.facts["skipped"] == 1
        assert outcome.facts["attempted"] == outcome.facts["blocked"] == 0
        assert path.read_bytes() == original
        for copy, placement, entry, ledger in copies:
            assert copy.read_bytes() == original
            assert storage.placement_get(placement["placement_id"]) == placement
            assert storage.catalog_entry_get(entry["catalog_entry_id"]) == entry
            assert storage.acquisition_get(ledger["acquisition_id"]) == ledger
    assert len(case.deliveries) == 1


@pytest.mark.parametrize("owner", [None, "", "deleted-profile", "default"])
def test_shared_copy_with_unknown_or_same_owner_still_blocks_adoption(acsm_case, owner):
    case = acsm_case
    acquired, receipt_id, path = _delivered(case)
    storage = case.service.storage
    copy = path.parent / "unresolved-copy.epub"
    copy.write_bytes(path.read_bytes())
    original = storage.placement_for_path(path)
    storage.placement_put({
        "placement_id": "unresolved-copy", "artifact_id": original["artifact_id"],
        "path": str(copy), "identity_id": owner, "provider_id": "numilog",
        "source_item_id": "unresolved-edition", "state": "present",
    })
    with pytest.raises(ProviderConfigurationError):
        case.service.resume_numilog_materialization(receipt_id)
    assert storage.placement_for_path(path) == original
    assert storage.acquisition_get(receipt_id)["status"] == "attention_required"
    assert path.read_bytes() == copy.read_bytes()
    assert len(case.deliveries) == 1
