"""P0 #5054: Core persists scoped attested observations without vendor rules.

Both producers are synthetic. No network, provider package, legacy manifest,
download offer, acquisition or filesystem placement is required.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.core.canonical_sync_stage import persist_attested_source_references
from cifereca.storage import Storage
from tests.test_core_p0_3171_atomic_publication_v14 import new_source, register


def _rows(storage):
    with storage._lock, storage._connect() as db:
        return db.execute(
            "SELECT identity_id,provider_id,credential_id,account_id,"
            "edition_id,right_id,evidence_kind FROM source_references "
            "ORDER BY provider_id"
        ).fetchall()


@pytest.fixture
def store(tmp_path):
    storage = Storage(tmp_path / "canonical.sqlite")
    try:
        register(storage, "profile-a", "synthetic-alpha", "cred-a", account="borrower-a")
        register(storage, "profile-b", "synthetic-beta", "cred-b", account="borrower-b")
        yield storage
    finally:
        storage.close()


def _snapshots():
    # Deliberately identical edition/right identifiers: only the full source
    # scope distinguishes the two positive observations.
    _, alpha = new_source(
        "profile-a", provider="synthetic-alpha", credential="cred-a",
        account="borrower-a", edition="same-edition", right="same-right",
    )
    _, beta = new_source(
        "profile-b", provider="synthetic-beta", credential="cred-b",
        account="borrower-b", edition="same-edition", right="same-right",
    )
    return alpha, beta


def test_two_synthetic_providers_identical_right_ids_remain_isolated(store):
    alpha, beta = _snapshots()
    assert persist_attested_source_references(store, alpha) == 1
    assert persist_attested_source_references(store, beta) == 1
    assert persist_attested_source_references(store, alpha) == 0
    assert persist_attested_source_references(store, beta) == 0

    assert _rows(store) == [
        ("profile-a", "synthetic-alpha", "cred-a", "borrower-a",
         "same-edition", "same-right", "provider_response"),
        ("profile-b", "synthetic-beta", "cred-b", "borrower-b",
         "same-edition", "same-right", "provider_response"),
    ]
    assert store.entitlements() == []
    assert store.acquisitions() == []
    assert store.placements() == []


@pytest.mark.parametrize("field,forged", [
    ("identity_id", "profile-b"),
    ("provider_id", "synthetic-beta"),
    ("credential_id", "cred-b"),
    ("account_id", "borrower-b"),
])
def test_cross_source_attestation_cannot_create_own_or_foreign_right(
    store, field, forged,
):
    alpha, _ = _snapshots()
    wrong_scope = replace(alpha.scope, **{field: forged})
    forged_snapshot = replace(alpha, scope=wrong_scope)
    with pytest.raises(ValueError, match="canonical_rights_core_binding_changed"):
        persist_attested_source_references(store, forged_snapshot)
    assert _rows(store) == []
    assert store.entitlements() == []
    assert store.placements() == []


def test_partial_empty_or_legacy_replay_cannot_revoke_or_forge_right(store):
    alpha, beta = _snapshots()
    assert persist_attested_source_references(store, alpha) == 1
    assert persist_attested_source_references(store, beta) == 1
    before = _rows(store)

    assert persist_attested_source_references(
        store, replace(alpha, rights=(), status="partial", coverage="partial"),
    ) == 0
    legacy_row = replace(
        beta.rights[0], evidence_kind="legacy_unverified",
        evidence_ref="", observed_at=None,
    )
    assert persist_attested_source_references(
        store, replace(beta, rights=(legacy_row,)),
    ) == 0
    assert _rows(store) == before
    assert store.entitlements() == []
    assert store.acquisitions() == []
    assert store.placements() == []
