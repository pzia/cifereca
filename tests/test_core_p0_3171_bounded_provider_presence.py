"""#3171: exact provider presence reads bounded SourceReference/Placement evidence.

Synthetic PDFs and SQLite only. Over-budget proof aborts before download
planning; unrelated local book copies do not consume an exact-source budget.
"""
from __future__ import annotations

import hashlib

import pytest

from cifereca.core import convergence_verified_presence as presence
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, SHA, new_source, publish, register, store, target,
)


def _setup(store, tmp_path):
    source, snapshot = new_source()
    register(store, "A", "seller", "card-A")
    original = target(tmp_path, "A")
    publication = publish(
        store, tmp_path / "EBooks", original, source, snapshot,
    )
    return source, snapshot, publication, original


def _plan(store, snap, tmp_path):
    return presence.plan_existing_verified_presence(
        store, snap, library_root=tmp_path / "EBooks",
    )


def test_unrelated_same_profile_placements_do_not_consume_source_budget(
    store, tmp_path, monkeypatch,
):
    _source, snap, publication, original = _setup(store, tmp_path)
    monkeypatch.setattr(presence, "_MAX_EVIDENCE_ROWS", 2)
    for idx in range(5):
        data = f"%PDF-1.7\\nUnrelated local content {idx}\\n".encode()
        digest = hashlib.sha256(data).hexdigest()
        path = tmp_path / "EBooks" / "A" / f"unrelated-{idx}.pdf"
        path.write_bytes(data)
        store.artifact_put({
            "artifact_id": "sha256:" + digest, "sha256": digest,
            "size": len(data), "media_format": "pdf",
        })
        store.placement_put({
            "artifact_id": "sha256:" + digest, "path": str(path),
            "identity_id": "A", "state": "present",
        })
    before = store.placements()
    plan = _plan(store, snap, tmp_path)
    row, = plan.decisions
    assert row.state == "present_attested"
    assert row.placement_ids == (publication.placement_id,)
    assert row.provider_satisfied
    assert len(before) == 6
    assert store.placements() == before
    assert original.read_bytes() == BYTES


def test_multiple_origins_for_one_artifact_never_multiply_physical_rows(
    store, tmp_path, monkeypatch,
):
    _source, snap, pub, original = _setup(store, tmp_path)
    monkeypatch.setattr(presence, "_MAX_EVIDENCE_ROWS", 3)
    with store._lock, store._connect() as db:
        owner, artifact, ref, created, updated = db.execute(
            "SELECT identity_id,artifact_id,source_reference_id,created_at,"
            "updated_at FROM origin_links LIMIT 1"
        ).fetchone()
        for i in range(2):
            db.execute(
                "INSERT INTO origin_links(origin_link_id,origin_kind,"
                "identity_id,artifact_id,source_reference_id,evidence_ref,"
                "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (f"independent-adept-proof-{i}", "adept", owner, artifact, ref,
                 f"independent-evidence-{i}", created, updated),
            )
    # Only provider origin rows can satisfy source evidence, and multiple
    # local copies are not multiplied by unrelated ADEPT provenance.
    row, = _plan(store, snap, tmp_path).decisions
    assert row.state == "present_attested"
    assert row.placement_ids == (pub.placement_id,)
    assert original.read_bytes() == BYTES


def test_exact_provider_origins_over_budget_abort_instead_of_claiming_absence(
    store, tmp_path, monkeypatch,
):
    _source, snap, _publication, original = _setup(store, tmp_path)
    monkeypatch.setattr(presence, "_MAX_EVIDENCE_ROWS", 2)
    with store._lock, store._connect() as db:
        owner, artifact, ref, created, updated = db.execute(
            "SELECT identity_id,artifact_id,source_reference_id,created_at,"
            "updated_at FROM origin_links LIMIT 1"
        ).fetchone()
        for i in range(2):
            db.execute(
                "INSERT INTO origin_links(origin_link_id,origin_kind,"
                "identity_id,artifact_id,source_reference_id,evidence_ref,"
                "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (f"provider-unique-{i}", "provider", owner, artifact, ref,
                 f"provider-claim-{i}", created, updated),
            )
    before = store.placements()
    with pytest.raises(ValueError, match="canonical_presence_evidence_over_budget"):
        _plan(store, snap, tmp_path)
    assert store.placements() == before
    assert original.read_bytes() == BYTES


def test_exact_origin_with_too_many_physical_copies_aborts_unverified_plan(
    store, tmp_path, monkeypatch,
):
    _source, snap, pub, original = _setup(store, tmp_path)
    monkeypatch.setattr(presence, "_MAX_EVIDENCE_ROWS", 2)
    for idx in range(3):
        path = target(tmp_path, "A", f"copy-{idx}.pdf")
        store.placement_put({
            "path": str(path), "identity_id": "A",
            "artifact_id": pub.artifact_id, "state": "present",
        })
    before = store.placements()
    with pytest.raises(ValueError, match="canonical_presence_evidence_over_budget"):
        _plan(store, snap, tmp_path)
    assert store.placements() == before
    assert original.read_bytes() == BYTES


def test_persisted_source_without_positive_proof_cannot_satisfy_b(
    store, tmp_path,
):
    """The provider OriginLink is insufficient if source A lost its evidence.

    Imported or damaged SQLite can retain stable SourceReference/OriginLink
    IDs and a SHA-perfect physical Placement while losing the provider's
    actual observation proof. The read-only plan must not suppress delivery.
    """
    _source, snapshot, publication, physical = _setup(store, tmp_path)
    before = store.placements()
    with store._lock, store._connect() as db:
        original, = db.execute(
            "SELECT evidence_ref FROM source_references "
            "WHERE source_reference_id=?",
            (publication.source_reference_id,),
        ).fetchone()
        assert original
        db.execute(
            "UPDATE source_references SET evidence_ref='' "
            "WHERE source_reference_id=?",
            (publication.source_reference_id,),
        )

    decision, = _plan(store, snapshot, tmp_path).decisions
    assert not decision.provider_satisfied
    assert decision.placement_ids == ()
    assert store.placements() == before
    assert physical.read_bytes() == BYTES

    with store._lock, store._connect() as db:
        db.execute(
            "UPDATE source_references SET evidence_ref=? "
            "WHERE source_reference_id=?",
            (original, publication.source_reference_id),
        )
    restored, = _plan(store, snapshot, tmp_path).decisions
    assert restored.provider_satisfied
    assert restored.placement_ids == (publication.placement_id,)
    assert store.placements() == before
    assert physical.read_bytes() == BYTES
