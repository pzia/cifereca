"""#3171: schema-v14 post-acquisition publication is one atomic transaction.

All bytes, rights and receipts are synthetic. No network/ADEPT/provider I/O.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import sqlite3

import pytest

from cifereca.core.canonical_publication import publish_completed_acquisition
from cifereca.core.source_convergence import SourceKey
from cifereca.plugin_api.provider_rights_observation import (
    ProviderRightsScope, ProviderRightObservation, ProviderRightsSnapshot,
)
from cifereca.storage import Storage


STAMP = "2026-10-28T08:00:00+00:00"
BYTES = b"%PDF-1.7\nSynthetic completed publication\n"
SHA = hashlib.sha256(BYTES).hexdigest()


def new_source(profile="A", *, provider="seller", credential="card-A",
               account="buyer-A", branch="purchases", edition="edition",
               right="purchase", kind="purchase"):
    scope = ProviderRightsScope(profile, provider, credential, account)
    key = SourceKey(scope, edition, right, kind, branch)
    observation = ProviderRightObservation(
        edition, right, kind, "active", "provider_response",
        "positive-right-observation", STAMP, "unknown",
    )
    snapshot = ProviderRightsSnapshot(
        scope, (observation,), status="partial", coverage="partial",
        branch=branch,
    )
    return key, snapshot


@pytest.fixture
def store(tmp_path):
    storage = Storage(tmp_path / "state.sqlite")
    try:
        assert storage.schema_version() == 14
        yield storage
    finally:
        storage.close()


def register(store, profile, provider, credential, account="buyer-A"):
    store.identity_put({
        "identity_id": profile, "directory_name": profile, "label": profile,
    })
    connection_id = store.provider_connection_put({
        "identity_id": profile, "provider_id": provider,
        "credential_id": credential, "label": "Synthetic account",
    })
    store.provider_account_put({
        "identity_id": profile, "provider_id": provider,
        "credential_id": credential, "connection_id": connection_id,
        "account_id": account, "label": "Synthetic borrower",
    })


def target(tmp_path, profile, filename="book.pdf"):
    path = tmp_path / "EBooks" / profile / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(BYTES)
    return path


def publish(store, root, path, source, snapshot, **kwargs):
    return publish_completed_acquisition(
        store, library_root=root, path=path, source=source,
        snapshot=snapshot, final_sha256=SHA,
        delivery_receipt_ref="synthetic-completed-delivery", **kwargs,
    )


def rows(store):
    with store._lock, store._connect() as db:
        return tuple(
            db.execute("SELECT count(*) FROM " + name).fetchone()[0]
            for name in ("artifacts", "placements", "source_references", "origin_links")
        )


def test_one_commit_idempotent_second_publication_no_false_rights(store, tmp_path):
    key, snap = new_source()
    register(store, "A", "seller", "card-A")
    path = target(tmp_path, "A")
    root = tmp_path / "EBooks"
    result = publish(store, root, path, key, snap, adept_receipt_ref="adept-final-proof")
    assert result.changed
    assert result.artifact_id == "sha256:" + SHA
    assert len(result.origin_link_ids) == 2
    assert rows(store) == (1, 1, 1, 2)
    assert len(store.entitlements()) == 0
    assert len(store.acquisitions()) == 0
    assert store.backup_list() == []
    assert store.placement_for_path(path)["identity_id"] == "A"
    second = publish(store, root, path, key, snap, adept_receipt_ref="adept-final-proof")
    assert second == replace(result, changed=False)
    assert rows(store) == (1, 1, 1, 2)
    assert path.read_bytes() == BYTES


def test_two_providers_identical_bytes_distinct_profile_and_right(store, tmp_path):
    a, sa = new_source("A", provider="seller-A", credential="cred-A", account="buyer-A")
    b, sb = new_source("B", provider="seller-B", credential="cred-B", account="buyer-B")
    register(store, "A", "seller-A", "cred-A")
    register(store, "B", "seller-B", "cred-B", account="buyer-B")
    root = tmp_path / "EBooks"
    pa, pb = target(tmp_path, "A"), target(tmp_path, "B")
    ra = publish(store, root, pa, a, sa)
    rb = publish(store, root, pb, b, sb)
    assert ra.artifact_id == rb.artifact_id
    assert ra.placement_id != rb.placement_id
    assert ra.source_reference_id != rb.source_reference_id
    assert ra.origin_link_ids != rb.origin_link_ids
    assert rows(store) == (1, 2, 2, 2)
    assert store.placement_for_path(pa)["identity_id"] == "A"
    assert store.placement_for_path(pb)["identity_id"] == "B"
    assert store.entitlements() == []


@pytest.mark.parametrize("tamper", ["wrong_sha", "foreign_profile", "foreign_scope",
                                    "inactive", "historical", "no_receipt", "missing"])
def test_bad_evidence_or_physical_target_cannot_write(store, tmp_path, tamper):
    key, snap = new_source()
    register(store, "A", "seller", "card-A")
    path = target(tmp_path, "A")
    kwargs = {}
    expected_sha = SHA
    if tamper == "wrong_sha":
        expected_sha = "b" * 64
    elif tamper == "foreign_profile":
        path = target(tmp_path, "B")
    elif tamper == "foreign_scope":
        key, _ = new_source("B")
    elif tamper == "inactive":
        snap = replace(snap, rights=(replace(snap.rights[0], state="inactive"),))
    elif tamper == "historical":
        snap = replace(snap, rights=(replace(snap.rights[0], evidence_kind="historical_receipt"),))
    elif tamper == "no_receipt":
        kwargs["delivery_receipt_ref"] = ""
    elif tamper == "missing":
        path.unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        publish_completed_acquisition(
            store, library_root=tmp_path / "EBooks", path=path,
            source=key, snapshot=snap, final_sha256=expected_sha,
            delivery_receipt_ref=kwargs.get("delivery_receipt_ref", "synthetic-completed-delivery"),
        )
    assert rows(store) == (0, 0, 0, 0)


def test_same_account_id_two_credentials_cannot_cross_published_profiles(store, tmp_path):
    key_a, snap_a = new_source(credential="first", account="upstream")
    key_b, snap_b = new_source(credential="second", account="upstream")
    register(store, "A", "seller", "first", account="upstream")
    register(store, "B", "seller", "second", account="upstream")
    path = target(tmp_path, "A")
    first = publish(store, tmp_path / "EBooks", path, key_a, snap_a)
    assert first.changed
    # Second credential is registered to B, even with identical upstream ID.
    with pytest.raises(ValueError, match="connection_or_profile_rebound"):
        publish(store, tmp_path / "EBooks", path, key_b, snap_b)
    assert rows(store) == (1, 1, 1, 1)


def test_stale_connection_or_recorded_account_fails_closed(store, tmp_path):
    key, snap = new_source()
    register(store, "A", "seller", "card-A", account="another-buyer")
    path = target(tmp_path, "A")
    with pytest.raises(ValueError, match="canonical_account_not_registered"):
        publish(store, tmp_path / "EBooks", path, key, snap)
    assert rows(store) == (0, 0, 0, 0)


def test_atomic_failure_does_not_leave_artifact_placement_or_reference(store, tmp_path):
    key, snap = new_source()
    register(store, "A", "seller", "card-A")
    path = target(tmp_path, "A")
    with store._lock, store._connect() as db:
        db.execute(
            "CREATE TRIGGER forced_failure BEFORE INSERT ON origin_links "
            "BEGIN SELECT RAISE(ABORT, 'synthetic_publication_failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic_publication_failure"):
        publish(store, tmp_path / "EBooks", path, key, snap)
    assert rows(store) == (0, 0, 0, 0)
    assert path.read_bytes() == BYTES
    with store._lock, store._connect() as db:
        db.execute("DROP TRIGGER forced_failure")
    assert publish(store, tmp_path / "EBooks", path, key, snap).changed
    assert rows(store) == (1, 1, 1, 1)


def test_existing_path_cannot_be_adopted_as_foreign_or_replaced_without_review(store, tmp_path):
    key, snap = new_source()
    register(store, "A", "seller", "card-A")
    path = target(tmp_path, "A")
    store.placement_put({
        "path": str(path), "identity_id": "B", "state": "present",
    })
    with pytest.raises(ValueError, match="canonical_placement_scope_or_bytes_conflict"):
        publish(store, tmp_path / "EBooks", path, key, snap)
    assert rows(store) == (0, 1, 0, 0)


def test_same_profile_two_independent_purchases_share_one_placement(store, tmp_path):
    register(store, "A", "seller", "card-A")
    root = tmp_path / "EBooks"
    path = target(tmp_path, "A")
    first, snap_first = new_source(right="purchase-first", edition="same-edition")
    second, snap_second = new_source(right="purchase-second", edition="same-edition")
    r1 = publish(store, root, path, first, snap_first)
    r2 = publish(store, root, path, second, snap_second)
    assert r1.artifact_id == r2.artifact_id
    assert r1.placement_id == r2.placement_id
    assert r1.source_reference_id != r2.source_reference_id
    assert rows(store) == (1, 1, 2, 2)
    assert store.entitlements() == []


def test_same_right_id_on_two_branches_is_independent(store, tmp_path):
    register(store, "A", "seller", "card-A")
    root = tmp_path / "EBooks"
    path = target(tmp_path, "A")
    purchased, snap_one = new_source(branch="purchases")
    renewed, snap_two = new_source(branch="loans")
    a = publish(store, root, path, purchased, snap_one)
    b = publish(store, root, path, renewed, snap_two)
    assert a.source_reference_id != b.source_reference_id
    assert a.origin_link_ids != b.origin_link_ids
    assert rows(store) == (1, 1, 2, 2)


def test_empty_primary_upstream_account_is_exact_not_null(store, tmp_path):
    key, snapshot = new_source(provider="paris", account="", credential="card-primary",
                               branch="dilicom", kind="loan", right="loan-1")
    register(store, "A", "paris", "card-primary", account="")
    path = target(tmp_path, "A")
    assert publish(store, tmp_path / "EBooks", path, key, snapshot).changed
    with store._lock, store._connect() as db:
        result = db.execute(
            "SELECT account_id,reference_kind FROM source_references"
        ).fetchall()
    assert result == [("", "loan")]


def test_same_path_with_new_unverified_bytes_does_not_mutate_existing_right(store, tmp_path):
    key, snap = new_source()
    register(store, "A", "seller", "card-A")
    path = target(tmp_path, "A")
    first = publish(store, tmp_path / "EBooks", path, key, snap)
    old_counts = rows(store)
    path.write_bytes(b"%PDF-1.7\\nAltered physical contents\\n")
    with pytest.raises(ValueError, match="canonical_final_sha_mismatch"):
        publish(store, tmp_path / "EBooks", path, key, snap)
    assert rows(store) == old_counts
    assert store.placement_for_path(path)["artifact_id"] == first.artifact_id


def test_epub_and_kepub_same_final_sha_do_not_split_artifact(store, tmp_path):
    import zipfile

    first, one = new_source(profile="A", provider="seller-A", credential="card-A")
    second, two = new_source(profile="B", provider="seller-B", credential="card-B")
    register(store, "A", "seller-A", "card-A")
    register(store, "B", "seller-B", "card-B")
    root = tmp_path / "EBooks"
    a = root / "A" / "book.epub"
    b = root / "B" / "book.kepub.epub"
    a.parent.mkdir(parents=True)
    b.parent.mkdir(parents=True)
    with zipfile.ZipFile(a, "w") as archive:
        archive.writestr("META-INF/container.xml", "<container/>")
    b.write_bytes(a.read_bytes())
    same_sha = hashlib.sha256(a.read_bytes()).hexdigest()
    def save(path, source, snapshot):
        return publish_completed_acquisition(
            store, library_root=root, path=path, source=source,
            snapshot=snapshot, final_sha256=same_sha,
            delivery_receipt_ref="synthetic-completed-delivery",
        )
    x, y = save(a, first, one), save(b, second, two)
    assert x.artifact_id == y.artifact_id
    assert x.placement_id != y.placement_id
    assert rows(store) == (1, 2, 2, 2)
