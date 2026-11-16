"""#6366: actual completed Core OriginLinks -> two public providers -> one C.

No manually injected relation_proof_id. The origin proof is persisted by
publish_completed_acquisition (v14): provider-response SourceReferences,
completed provider OriginLinks and one present same-profile Artifact.

These tests use synthetic completed provider receipts, not real web endpoints.
No account entitlement is minted by the descriptive Search C producer.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core.notice_c_cover_index import (
    canonical_cover_document_ref, read_consolidated_notice_cover,
)
from cifereca.core.notice_c_known_projection import (
    matching_live_public_canonical_ref,
)
from cifereca.core.notice_c_origin_link_producer import (
    reconcile_origin_linked_public_c,
)
from cifereca.core.notice_cover_repository import read_notice_cover, _opaque_key
from cifereca.core.search_orchestration import _mirror_remote_results
from cifereca.gui.notice_cover_cached_reader import cached_notice_cover_snapshot
from cifereca.gui.search_document_cover_bridge import exact_remote_search_document
from cifereca.storage import Storage
from tests.test_core_p0_3171_atomic_publication_v14 import (
    new_source, publish, register, target,
)
from tests.test_core_p0_6366_same_call_public_cover_writer import witnessed
from tests.test_core_p0_6366_real_search_notice_c_ingestion import public_row
from tests.test_core_p1_6168_search_remote_document_gateway import host


A_URL = "https://covers.example.org/origin-bnf.jpg"
B_URL = "https://covers.example.org/origin-numilog.jpg"


def _source_rows():
    a = witnessed(public_row(
        source_provider="seller-a", record_id="edition-a",
        thumbnail_url=A_URL, title="L'ouvrage commun",
    ))
    b = witnessed(public_row(
        source_provider="seller-b", record_id="edition-b",
        thumbnail_url=B_URL, title="L'ouvrage commun",
    ))
    return a, b


def _mirror(db, *rows):
    return _mirror_remote_results(
        NS(storage=db), list(rows), identity_id="A",
        require_public_cover_witness=True,
    )


def _origins(db, tmp_path, *, split_owners=False):
    root = tmp_path / "EBooks"
    a, sa = new_source(
        "A", provider="seller-a", credential="card-a",
        edition="edition-a", right="right-a",
    )
    b_owner = "B" if split_owners else "A"
    b, sb = new_source(
        b_owner, provider="seller-b", credential="card-b",
        edition="edition-b", right="right-b",
    )
    register(db, "A", "seller-a", "card-a")
    register(db, b_owner, "seller-b", "card-b")
    pa = target(tmp_path, "A")
    pb = target(tmp_path, "B") if split_owners else pa
    first = publish(db, root, pa, a, sa)
    second = publish(db, root, pb, b, sb)
    assert first.artifact_id == second.artifact_id
    assert first.source_reference_id != second.source_reference_id
    return first, second


def test_two_completed_real_provider_origins_publish_shared_c_on_live_search(
    tmp_path,
):
    filename = tmp_path / "origin.sqlite"
    db = Storage(filename)
    try:
        first, second = _origins(db, tmp_path)
        assert first.origin_link_ids != second.origin_link_ids
        a, b = _source_rows()
        assert _mirror(db, a) == 1
        own = a._core_notice_canonical_cover_document_ref
        assert matching_live_public_canonical_ref(
            a, identity_id="A",
        ) == own
        # A alone cannot claim B's source or manufacture a shared C.
        assert not hasattr(a, "_core_notice_cross_provider_proof")

        assert _mirror(db, b) == 1
        shared = b._core_notice_canonical_cover_document_ref
        assert shared != own
        assert shared != canonical_cover_document_ref(
            read_notice_cover(
                db, b._core_notice_cover_document_ref,
            ).observation.key.edition_id,
        )
        assert matching_live_public_canonical_ref(
            b, identity_id="A",
        ) == shared
        # The *real* Search GUI list/detail gateway receives that exact
        # canonical ref, not the provider's individual thumbnail scope.
        window_b, _, key_b = host(b)
        visible_b = exact_remote_search_document(window_b, b, key_b)
        assert visible_b is not None and visible_b.document == shared
        assert {c.url for c in visible_b.candidates} == {B_URL}
        stored = read_consolidated_notice_cover(db, shared)
        assert stored is not None and len(stored.sources) == 2
        assert {proof.provider_id for proof in stored.sources} == {
            "seller-a", "seller-b",
        }
        assert {c.url for c in stored.candidates} == {A_URL, B_URL}
        assert all(src.visibility == "public" for src in stored.sources)

        # The other provider subsequently refreshes from a real same-call
        # response; both independently stamped live rows now share N2.
        a_fresh, _ = _source_rows()
        assert _mirror(db, a_fresh) == 1
        assert a_fresh._core_notice_canonical_cover_document_ref == shared
        assert matching_live_public_canonical_ref(
            a_fresh, identity_id="A",
        ) == shared
        window_a, _, key_a = host(a_fresh)
        visible_a = exact_remote_search_document(window_a, a_fresh, key_a)
        assert visible_a is not None and visible_a.document == shared
        assert {c.url for c in visible_a.candidates} == {A_URL}
        assert matching_live_public_canonical_ref(
            replace(a_fresh), identity_id="A",
        ) is None
        assert matching_live_public_canonical_ref(
            replace(b), identity_id="A",
        ) is None

        packet = cached_notice_cover_snapshot(NS(storage=db), shared)
        assert packet is not None and packet.scope == shared
        assert {c.url for c in packet.candidates} == {A_URL, B_URL}
        assert db.entitlements() == [] and db.acquisitions() == []
        # C must contain no private provider receipt, account, path or right.
        with db._lock, db._connect() as sql:
            raw = sql.execute(
                "SELECT value FROM metadata WHERE key LIKE "
                "'notice.c.cover.canonical.v1:%'"
            ).fetchall()
        joined = " ".join(x[0] for x in raw)
        assert "buyer-A" not in joined
        assert "synthetic-completed-delivery" not in joined
        assert str(tmp_path) not in joined

        # Short-lived supplier rows are disposable; the canonical N2 cover
        # remains independently persisted and survives process restart.
        with db._lock, db._connect() as sql:
            for row in (a, b):
                sql.execute(
                    "DELETE FROM metadata WHERE key=?",
                    (_opaque_key(row._core_notice_cover_document_ref),),
                )
        assert read_consolidated_notice_cover(db, shared).candidates == stored.candidates
    finally:
        db.close()

    restored = Storage(filename)
    try:
        assert cached_notice_cover_snapshot(NS(storage=restored), shared).scope == shared
        assert len(read_consolidated_notice_cover(restored, shared).sources) == 2
    finally:
        restored.close()


def test_same_artifact_sha_in_two_profiles_is_not_same_owner_proof(tmp_path):
    db = Storage(tmp_path / "two-profiles.sqlite")
    try:
        _origins(db, tmp_path, split_owners=True)
        a, b = _source_rows()
        assert _mirror(db, a) == 1 and _mirror(db, b) == 1
        assert b._core_notice_canonical_cover_document_ref != (
            a._core_notice_canonical_cover_document_ref
        )
        assert not hasattr(b, "_core_notice_cross_provider_proof")
        assert db.entitlements() == []
    finally:
        db.close()


@pytest.mark.parametrize("damage", [
    "no_provider_origin", "no_source_evidence", "record_mismatch",
])
def test_absent_or_untrusted_origin_refuses_cross_provider_merge(
    tmp_path, damage,
):
    db = Storage(tmp_path / "untrusted.sqlite")
    try:
        _origins(db, tmp_path)
        if damage == "no_provider_origin":
            with db._lock, db._connect() as sql:
                sql.execute(
                    "DELETE FROM origin_links WHERE source_reference_id IN ("
                    "SELECT source_reference_id FROM source_references "
                    "WHERE provider_id='seller-b')"
                )
        elif damage == "no_source_evidence":
            with db._lock, db._connect() as sql:
                sql.execute(
                    "UPDATE source_references SET evidence_kind='historical_receipt' "
                    "WHERE provider_id='seller-b'"
                )
        a, b = _source_rows()
        if damage == "record_mismatch":
            b = witnessed(public_row(
                source_provider="seller-b", record_id="unrelated-edition",
                thumbnail_url=B_URL, title="L'ouvrage commun",
            ))
        assert _mirror(db, a) == 1 and _mirror(db, b) == 1
        assert b._core_notice_canonical_cover_document_ref != (
            a._core_notice_canonical_cover_document_ref
        )
        assert not hasattr(b, "_core_notice_cross_provider_proof")
        assert db.entitlements() == []
    finally:
        db.close()


def test_two_confirmed_receipts_for_same_generic_bytes_cannot_merge_two_works(
    tmp_path,
):
    db = Storage(tmp_path / "different-works.sqlite")
    try:
        _origins(db, tmp_path)
        a, _ = _source_rows()
        unrelated = witnessed(public_row(
            source_provider="seller-b", record_id="edition-b",
            thumbnail_url=B_URL, title="Autre ouvrage",
            author="Une autre autrice",
        ))
        assert _mirror(db, a) == 1
        assert _mirror(db, unrelated) == 1
        assert unrelated._core_notice_canonical_cover_document_ref != (
            a._core_notice_canonical_cover_document_ref
        )
        assert not hasattr(unrelated, "_core_notice_cross_provider_proof")
        assert db.entitlements() == []
    finally:
        db.close()


def test_laundered_cross_ref_and_stale_original_never_bind_live_ui(tmp_path):
    db = Storage(tmp_path / "forged.sqlite")
    try:
        _origins(db, tmp_path)
        a, b = _source_rows()
        _mirror(db, a)
        _mirror(db, b)
        canonical = b._core_notice_canonical_cover_document_ref
        assert matching_live_public_canonical_ref(b, identity_id="A") == canonical

        proof = b._core_notice_cross_provider_proof
        b._core_notice_cross_provider_proof = (object(), *proof[1:])
        assert matching_live_public_canonical_ref(b, identity_id="A") is None
        b._core_notice_cross_provider_proof = proof
        b._core_notice_canonical_cover_document_epoch += 1
        assert matching_live_public_canonical_ref(b, identity_id="A") is None
        b._core_notice_canonical_cover_document_epoch = proof[3]
        b.thumbnail_url = "https://covers.example.org/forged.jpg"
        assert matching_live_public_canonical_ref(b, identity_id="A") is None
        assert db.entitlements() == []
    finally:
        db.close()
