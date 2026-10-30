"""#3171: selected provider candidate -> Core verified final bytes and receipt.

Synthetic provider GET and ADEPT runtime, real v14 SQLite and Sync lock.
No live credentials, DRM service or supplier network is contacted.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from cifereca.catalog import LocalCatalog
from cifereca.core.sync_coordination import synchronize_account_source
from tests.test_core_p0_3171_canonical_sync_stage import rig, refs
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, SHA


@pytest.fixture(autouse=True)
def _isolate_delivery_lock(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "cifereca.core.canonical_selected_delivery.acquisition_lock_file",
        lambda: tmp_path / "state" / "acquisition.lock",
    )
    monkeypatch.setattr(
        "cifereca.core.canonical_selected_offers.acquisition_lock_file",
        lambda: tmp_path / "state" / "acquisition.lock",
    )


def _prepare(service):
    grants = {service.source}
    service._canonical_persisted_consent = lambda _: frozenset(grants)
    observations = []

    def selected(_account, **kwargs):
        observations.append(kwargs)
        return replace(
            service.snapshot,
            status="partial", coverage="partial",
            rights=(replace(
                service.snapshot.rights[0],
                delivery="available", delivery_offer_id="signed-right-offer",
            ),),
        )

    service.session.rights_selected_delivery_offer_snapshot = selected
    return grants, observations


def _native(_account, *, target_dir, **kwargs):
    assert kwargs == {
        "edition_id": "edition", "right_id": "purchase",
        "kind": "purchase", "branch": "purchases",
        "delivery_offer_id": "signed-right-offer",
        "explicit_user_action": True,
    }
    received = Path(target_dir) / "received.pdf"
    assert not received.exists()
    received.write_bytes(BYTES)
    return received, "https://private.invalid/?token=NO_REAL_SECRET"


def test_exact_selected_candidate_becomes_core_receipt_once_then_no_second_get(rig):
    service, db, root = rig
    grants, preflights = _prepare(service)
    calls = []

    def candidate(*args, **kwargs):
        calls.append(kwargs["delivery_offer_id"])
        return _native(*args, **kwargs)

    service.session.rights_selected_delivery_candidate = candidate
    first = synchronize_account_source(service, service.account)
    assert first["canonical_execution"]["downloaded"] == 1
    assert first["files_materialized"] == 1
    assert calls == ["signed-right-offer"] and len(preflights) == 1
    placement = db.placements()[0]
    assert placement["identity_id"] == "A"
    assert placement["artifact_id"] == "sha256:" + SHA
    path = Path(placement["path"])
    assert path.read_bytes() == BYTES
    assert path.is_relative_to(root / "A" / ".canonical-delivery")
    immediate = db.catalog_entries()
    assert len(immediate) == 1
    assert immediate[0]["placement_id"] == placement["placement_id"]
    assert immediate[0]["artifact_id"] == "sha256:" + SHA
    assert immediate[0]["identity_id"] == "A"
    with db._lock, db._connect() as sql:
        origin = sql.execute(
            "SELECT origin_kind,evidence_ref FROM origin_links"
        ).fetchall()
        assert len(origin) == 1 and origin[0][0] == "provider"
        assert origin[0][1].startswith("core-delivery:")
        assert origin[0][1] != service.snapshot.rights[0].evidence_ref
        journal = sql.execute(
            "SELECT status FROM acquisition_ledger "
            "WHERE acquisition_id LIKE 'canonical-intake:%'"
        ).fetchone()
        assert journal == ("complete",)
    second = synchronize_account_source(service, service.account)
    assert second["canonical_execution"]["present_attested"] == 1
    assert second["canonical_execution"]["downloaded"] == 0
    assert calls == ["signed-right-offer"] and len(preflights) == 1
    assert refs(db)[1] == 1


def test_no_persisted_consent_means_no_private_offer_or_candidate_get(rig):
    service, db, root = rig
    grants, reads = _prepare(service)
    grants.clear()
    service.session.rights_selected_delivery_candidate = lambda *_a, **_kw: pytest.fail(
        "candidate GET without explicit durable consent"
    )
    service.session.rights_selected_delivery_offer_snapshot = lambda *_a, **_kw: pytest.fail(
        "private preflight without consent"
    )
    outcome = synchronize_account_source(service, service.account)
    assert outcome["canonical_execution"]["pending"] == 1
    assert db.placements() == [] and refs(db)[1] == 0


@pytest.mark.parametrize("failure", ["callback", "foreign_path", "bad_bytes"])
def test_rejected_candidate_removes_private_staging_without_publishing(
    rig, failure,
):
    service, db, root = rig
    _prepare(service)
    original = root / "A" / "manual" / "original.pdf"
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(BYTES)

    def candidate(_account, *, target_dir, **_kwargs):
        staged = Path(target_dir) / "private.acsm"
        staged.write_text("synthetic-private-ACSM-token", encoding="utf-8")
        if failure == "callback":
            raise ValueError("synthetic-provider-rejection")
        if failure == "foreign_path":
            return original, ""
        return staged, ""

    service.session.rights_selected_delivery_candidate = candidate
    result = synchronize_account_source(service, service.account)
    assert result["canonical_execution"]["failed"] == 1
    assert refs(db)[1] == 0 and db.placements() == []
    assert original.read_bytes() == BYTES
    # Failed attempt contains no recoverable completed-delivery receipt;
    # provider tokens and rejected candidates must not remain on disk.
    base = root / "A" / ".canonical-delivery"
    assert base.is_dir() and not list(base.glob("run-*"))


def test_provider_candidate_outside_fresh_staging_cannot_publish(rig):
    service, db, root = rig
    _prepare(service)
    stray = root / "A" / "manual" / "same-title.pdf"
    stray.parent.mkdir(parents=True)
    stray.write_bytes(BYTES)
    service.session.rights_selected_delivery_candidate = (
        lambda *_a, **_kw: (stray, "https://private.invalid/file")
    )
    outcome = synchronize_account_source(service, service.account)
    assert outcome["canonical_execution"]["failed"] == 1
    assert stray.read_bytes() == BYTES
    assert refs(db)[1] == 0 and db.placements() == []


def test_consent_revoked_during_candidate_get_does_not_publish_origin(rig):
    service, db, root = rig
    grants, _ = _prepare(service)

    def candidate(*args, **kwargs):
        finished = _native(*args, **kwargs)
        grants.clear()
        return finished

    service.session.rights_selected_delivery_candidate = candidate
    outcome = synchronize_account_source(service, service.account)
    assert outcome["canonical_execution"]["blocked"] == 1
    assert refs(db)[1] == 0 and db.placements() == []


def test_upstream_borrower_switched_during_candidate_get_never_publishes(rig):
    service, db, root = rig
    _prepare(service)

    def candidate(*args, **kwargs):
        received = _native(*args, **kwargs)
        # Same connected Python session, same Core account and epoch.
        service.session.accounts = [
            replace(service.account, unique_id="different-borrower"),
        ]
        return received

    service.session.rights_selected_delivery_candidate = candidate
    outcome = synchronize_account_source(service, service.account)
    assert outcome["canonical_execution"]["failed"] == 1
    assert refs(db)[1] == 0 and db.placements() == []


def test_candidate_bridge_rechecks_session_borrower_before_direct_get(rig):
    from cifereca.core.canonical_selected_delivery import complete_selected_delivery
    from cifereca.locking import InterProcessLock

    service, db, root = rig
    service.session.accounts = [
        replace(service.account, unique_id="different-borrower"),
    ]
    service.session.rights_selected_delivery_candidate = (
        lambda *_a, **_kw: pytest.fail("foreign borrower cannot GET")
    )
    offered = replace(
        service.snapshot.rights[0],
        delivery="available", delivery_offer_id="synthetic-signed-offer",
    )
    with InterProcessLock(root.parent / "state" / "acquisition.lock") as lease:
        with pytest.raises(ValueError, match="canonical_delivery_source_rebound"):
            complete_selected_delivery(
                service, service.account, service.session,
                service.source, offered, library_root=root, shared_lock=lease,
            )
    assert refs(db)[1] == 0 and db.placements() == []


def test_changed_session_epoch_during_candidate_get_refuses_old_owner(rig):
    service, db, root = rig
    _prepare(service)

    def candidate(*args, **kwargs):
        completed = _native(*args, **kwargs)
        service._source_context_epoch += 1
        return completed

    service.session.rights_selected_delivery_candidate = candidate
    outcome = synchronize_account_source(service, service.account)
    assert outcome["canonical_execution"]["failed"] == 1
    assert refs(db)[1] == 0 and db.placements() == []


def test_acsm_candidate_requires_adept_final_pdf_and_independent_origin(rig):
    service, db, root = rig
    _prepare(service)
    runtime_calls = []

    def candidate(_account, *, target_dir, **_kwargs):
        path = Path(target_dir) / "token.acsm"
        path.write_text(
            '<fulfillmentToken><format>application/pdf</format></fulfillmentToken>',
            encoding="utf-8",
        )
        return path, ""

    service.session.rights_selected_delivery_candidate = candidate
    service.resolve_adept_profile_for_identity = lambda _: ("borrower-profile", "configured")
    service._originating_adept_profile_id = lambda *_: "profile-id"

    def execute(request):
        runtime_calls.append(request)
        assert request["source"]["type"] == "acsm"
        assert request["source"]["provider"] == "seller-a"
        assert request["profile"] == "borrower-profile"
        path = Path(request["destination"])
        path.write_bytes(BYTES)
        return {
            "ok": True, "profile": "borrower-profile",
            "artifact": {"path": str(path)},
        }

    service.execute_acquisition_request = execute
    result = synchronize_account_source(service, service.account)
    assert result["canonical_execution"]["downloaded"] == 1
    assert len(runtime_calls) == 1
    assert db.placements()[0]["artifact_id"] == "sha256:" + SHA
    with db._lock, db._connect() as sql:
        kinds = [row[0] for row in sql.execute(
            "SELECT origin_kind FROM origin_links ORDER BY origin_kind"
        ).fetchall()]
    assert kinds == ["adept", "provider"]


def test_acsm_without_confirmed_adept_result_never_becomes_artifact(rig):
    service, db, root = rig
    _prepare(service)
    def candidate(_account, *, target_dir, **_kwargs):
        path = Path(target_dir) / "token.acsm"
        path.write_text('<fulfillmentToken><format>application/pdf</format></fulfillmentToken>')
        return path, ""
    service.session.rights_selected_delivery_candidate = candidate
    service.resolve_adept_profile_for_identity = lambda _: ("borrower-profile", "configured")
    service._originating_adept_profile_id = lambda *_: "profile-id"
    service.execute_acquisition_request = lambda _: {"ok": False}
    outcome = synchronize_account_source(service, service.account)
    assert outcome["canonical_execution"]["failed"] == 1
    assert refs(db)[1] == 0 and db.placements() == []


def test_account_unregistered_after_selected_offer_blocks_candidate_get(rig):
    service, db, root = rig
    _prepare(service)
    original = service.session.rights_selected_delivery_offer_snapshot

    def remove_account(*args, **kwargs):
        observed = original(*args, **kwargs)
        with db._lock, db._connect() as sql:
            sql.execute(
                "DELETE FROM provider_accounts "
                "WHERE identity_id='A' AND provider_id='seller-a' "
                "AND credential_id='card-a' AND account_id='buyer-a'"
            )
        return observed

    service.session.rights_selected_delivery_offer_snapshot = remove_account
    service.session.rights_selected_delivery_candidate = (
        lambda *_a, **_kw: pytest.fail("no GET after SQL account unbinding")
    )
    result = synchronize_account_source(service, service.account)
    assert result["canonical_execution"]["downloaded"] == 0
    assert refs(db)[1] == 0 and db.placements() == []


def test_uncommitted_staging_is_invisible_until_atomic_provider_publication(rig):
    service, db, root = rig
    _prepare(service)
    catalog = LocalCatalog(db, root)
    staged = []

    def candidate(*args, **kwargs):
        delivery = _native(*args, **kwargs)
        staged.append(delivery[0])
        assert catalog.index_path(delivery[0])["reason"] == "canonical_staging_not_published"
        observed = catalog.scan()
        assert observed.cataloged == 0
        assert db.placements() == [] and db.catalog_entries() == []
        return delivery

    service.session.rights_selected_delivery_candidate = candidate
    synced = synchronize_account_source(service, service.account)
    assert synced["canonical_execution"]["downloaded"] == 1
    assert len(staged) == 1 and staged[0].is_file()
    # Only after Core commits both the exact OriginLink and Placement may
    # the physical library scanner reveal the EPUB/PDF as a local catalogue.
    scanned = catalog.scan()
    assert scanned.cataloged == 1 and scanned.invalid == 0
    entries = db.catalog_entries()
    assert len(entries) == 1
    assert entries[0]["placement_id"] == db.placements()[0]["placement_id"]


def test_candidate_after_commit_catalogue_failure_recovers_without_second_get(rig, monkeypatch):
    service, db, root = rig
    _, preflights = _prepare(service)
    gets = []

    def candidate(*args, **kwargs):
        gets.append(kwargs["delivery_offer_id"])
        return _native(*args, **kwargs)

    service.session.rights_selected_delivery_candidate = candidate
    genuine_index = LocalCatalog.index_path

    def interrupt(*_args, **_kwargs):
        raise RuntimeError("synthetic post-commit descriptive index failure")

    monkeypatch.setattr(LocalCatalog, "index_path", interrupt)
    first = synchronize_account_source(service, service.account)
    assert first["canonical_execution"]["downloaded"] == 1
    assert first["files_materialized"] == 1
    assert gets == ["signed-right-offer"] and len(preflights) == 1
    assert db.catalog_entries() == []
    assert len(db.placements()) == 1 and refs(db)[1] == 1

    monkeypatch.setattr(LocalCatalog, "index_path", genuine_index)
    second = synchronize_account_source(service, service.account)
    assert second["canonical_execution"]["present_attested"] == 1
    assert second["canonical_execution"]["downloaded"] == 0
    assert gets == ["signed-right-offer"] and len(preflights) == 1
    assert db.catalog_entries() == []

    # The next ordinary read-only inventory can rebuild only the
    # descriptive catalogue from the already committed provider origin.
    observed = LocalCatalog(db, root).scan()
    assert observed.cataloged == 1 and observed.invalid == 0
    entries = db.catalog_entries()
    assert len(entries) == 1
    assert entries[0]["placement_id"] == db.placements()[0]["placement_id"]
