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


def test_origin_linked_c_keeps_pending_public_source_and_upgrades_same_ref(
    tmp_path, monkeypatch,
):
    from cifereca.core import search_orchestration
    clock = [200]
    monkeypatch.setattr(search_orchestration.time, "time", lambda: clock[0])
    db = Storage(tmp_path / "pending-upgrade.sqlite")
    try:
        _origins(db, tmp_path)
        absent = witnessed(public_row(
            source_provider="seller-a", record_id="edition-a",
            title="L'ouvrage commun", thumbnail_url=None,
        ))
        _, b = _source_rows()
        assert _mirror(db, absent) == 1
        assert _mirror(db, b) == 1
        ref = b._core_notice_canonical_cover_document_ref
        partial = read_consolidated_notice_cover(db, ref)
        assert partial is not None
        assert len(partial.sources) == 2
        assert {c.url for c in partial.candidates} == {B_URL}
        assert any(s.state == "pending" for s in partial.sources)
        assert b._core_notice_canonical_cover_document_ref == ref

        # Actual newly attested Search observation: one provider now has
        # its own cover. The second provider's C association survives.
        clock[0] = 201
        present, _ = _source_rows()
        assert _mirror(db, present) == 1
        assert present._core_notice_canonical_cover_document_ref == ref
        expanded = read_consolidated_notice_cover(db, ref)
        assert expanded.epoch > partial.epoch
        assert {c.url for c in expanded.candidates} == {A_URL, B_URL}
        assert cached_notice_cover_snapshot(
            NS(storage=db), ref,
        ).revision == expanded.epoch
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


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


def test_origin_reconciliation_survives_large_unrelated_source_inventory(tmp_path):
    import json
    from cifereca.core.notice_cover_repository import _PREFIX

    db = Storage(tmp_path / "large-origin-inventory.sqlite")
    try:
        _origins(db, tmp_path)
        a, b = _source_rows()
        assert _mirror(db, a) == 1
        # A large unrelated source inventory must not veto the actual
        # provider pair. These deliberately malformed records also check
        # that the SQL prefilter is safe on invalid persisted JSON.
        with db._lock, db._connect() as sql:
            own = sql.execute("SELECT value FROM metadata WHERE key=?",
                              (_opaque_key(a._core_notice_cover_document_ref),)).fetchone()[0]
            payload = json.loads(own)
            payload["key"]["provider_id"] = "unrelated-provider"
            payload["key"]["record_id"] = "search:unrelated"
            raw = json.dumps(payload)
            sql.executemany("INSERT INTO metadata(key,value) VALUES(?,?)",
                            [(_PREFIX + f"{n:064x}", raw) for n in range(4100)])
            sql.execute("INSERT INTO metadata(key,value) VALUES(?,?)",
                        (_PREFIX + "corrupt", "{"))
        assert _mirror(db, b) == 1
        linked = read_consolidated_notice_cover(db, b._core_notice_canonical_cover_document_ref)
        assert len(linked.sources) == 2
        assert {cover.url for cover in linked.candidates} == {A_URL, B_URL}
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_public_group_atomic_rollback_new_existing_and_retry(tmp_path):
    from cifereca.core.notice_c_cover_index import persist_attested_public_cover_group
    from tests.test_core_p0_6366_public_c_sqlite_source_search import persist

    path = tmp_path / "atomic-public-group.sqlite"
    db = Storage(path)
    target = "edition:atomic-public-group"
    proof = "strong:fixture:atomic-group"
    document = canonical_cover_document_ref(target)
    def store(provider, record, url, at):
        return persist(db, witnessed(public_row(
            source_provider=provider, record_id=record,
            title="L'ouvrage commun", thumbnail_url=url,
        )), at=at)
    def commit(*sources):
        return persist_attested_public_cover_group(
            db, canonical_edition_id=target, sources=sources,
            relation_proof_id=proof,
        )
    try:
        a = store("seller-a", "edition-a", A_URL, 100)
        b = store("seller-b", "edition-b", B_URL, 100)
        c = store("seller-c", "edition-c", None, 100)
        with db._lock, db._connect() as sql:
            sql.execute("DELETE FROM metadata WHERE key=?", (_opaque_key(c.document),))
        with pytest.raises(ValueError, match="stale_canonical_cover_source"):
            commit(a, b, c)
        assert read_consolidated_notice_cover(db, document) is None

        c = store("seller-c", "edition-c", None, 100)
        good = commit(a, b, c)
        assert len(good.sources) == 3
        assert {cover.url for cover in good.candidates} == {A_URL, B_URL}
        assert commit(a, b, c) == good

        changed = store("seller-a", "edition-a",
                        "https://covers.example.org/changed.jpg", 101)
        with db._lock, db._connect() as sql:
            sql.execute("DELETE FROM metadata WHERE key=?", (_opaque_key(c.document),))
        with pytest.raises(ValueError, match="stale_canonical_cover_source"):
            commit(changed, b, c)
        assert read_consolidated_notice_cover(db, document) == good
        c = store("seller-c", "edition-c", None, 100)
        updated = commit(changed, b, c)
        assert updated.epoch > good.epoch
        assert len(updated.sources) == 3
        assert commit(changed, b, c) == updated
        with pytest.raises(ValueError, match="group_conflict"):
            commit(changed, b)
        assert read_consolidated_notice_cover(db, document) == updated
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()

    restored = Storage(path)
    try:
        assert read_consolidated_notice_cover(restored, document) == updated
    finally:
        restored.close()


def test_three_completed_origins_expand_existing_c_without_competing_ref(tmp_path):
    from cifereca.core.notice_c_exact_source_lookup import exact_public_source_c_many
    path = tmp_path / "three-real-origins.sqlite"
    db = Storage(path)
    try:
        first, _ = _origins(db, tmp_path)
        a, b = _source_rows()
        assert _mirror(db, a) == 1 and _mirror(db, b) == 1
        ref = b._core_notice_canonical_cover_document_ref
        pair = read_consolidated_notice_cover(db, ref)
        assert len(pair.sources) == 2

        # Publish a real third completed provider origin to the SAME owner
        # and placement. No direct canonical-relation injection.
        c_key, c_snapshot = new_source(
            "A", provider="seller-c", credential="card-c",
            edition="edition-c", right="right-c",
        )
        register(db, "A", "seller-c", "card-c")
        third = publish(db, tmp_path / "EBooks", target(tmp_path, "A"),
                        c_key, c_snapshot)
        assert third.artifact_id == first.artifact_id
        assert third.placement_id == first.placement_id
        third_url = "https://covers.example.org/origin-c.jpg"
        def c_row(**changes):
            return witnessed(public_row(**{
                "source_provider": "seller-c", "record_id": "edition-c",
                "title": "L'ouvrage commun", "thumbnail_url": third_url,
                **changes,
            }))
        c = c_row()
        assert _mirror(db, c) == 1
        assert c._core_notice_canonical_cover_document_ref == ref
        expanded = read_consolidated_notice_cover(db, ref)
        assert expanded.epoch > pair.epoch
        assert len(expanded.sources) == 3
        assert {x.url for x in expanded.candidates} == {A_URL, B_URL, third_url}

        a2, b2 = _source_rows()
        assert _mirror(db, a2, b2, c_row()) == 3
        assert a2._core_notice_canonical_cover_document_ref == ref
        assert b2._core_notice_canonical_cover_document_ref == ref
        keys = [(row.source_provider, row.record_id, row.docbase,
                 row.medium, row.revision_id) for row in (a, b, c)]
        resolved = exact_public_source_c_many(db, keys)
        assert set(resolved) == set(keys)
        assert {snap.document for snap in resolved.values()} == {ref}
        assert read_consolidated_notice_cover(db, ref) == expanded
        with db._lock, db._connect() as sql:
            sql.execute("DELETE FROM metadata WHERE key LIKE 'notice.c.observation.v1:%'")
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()

    reopened = Storage(path)
    try:
        assert read_consolidated_notice_cover(reopened, ref) == expanded
        assert {snap.document for snap in
                exact_public_source_c_many(reopened, keys).values()} == {ref}
    finally:
        reopened.close()


@pytest.mark.parametrize("second_owner", ["A", "B"])
def test_distinct_records_same_provider_require_independent_completed_origins(
    tmp_path, second_owner,
):
    from contextlib import closing
    from cifereca.core.notice_c_origin_link_producer import reattest_exact_origin_group_txn

    path = tmp_path / "same-provider-records.sqlite"
    with closing(Storage(path)) as db:
        register(db, "A", "seller-a", "card-a")
        register(db, second_owner, "seller-a", "card-b")
        root = tmp_path / "EBooks"
        a_key, a_snapshot = new_source(
            "A", provider="seller-a", credential="card-a",
            edition="edition-a", right="right-a",
        )
        b_key, b_snapshot = new_source(
            second_owner, provider="seller-a", credential="card-b",
            edition="edition-b", right="right-b",
        )
        first = publish(db, root, target(tmp_path, "A"), a_key, a_snapshot)
        second = publish(db, root, target(tmp_path, second_owner), b_key, b_snapshot)
        assert first.artifact_id == second.artifact_id
        assert first.source_reference_id != second.source_reference_id
        a, _ = _source_rows()
        b = witnessed(public_row(
            source_provider="seller-a", record_id="edition-b",
            thumbnail_url=B_URL, title="L'ouvrage commun",
        ))
        assert _mirror(db, a) == 1
        own = a._core_notice_canonical_cover_document_ref
        assert _mirror(db, b) == 1
        shared = b._core_notice_canonical_cover_document_ref
        canonical = read_consolidated_notice_cover(db, shared)
        assert shared != own
        if second_owner != "A":
            assert len(canonical.sources) == 1
            return
        assert first.placement_id == second.placement_id
        assert len(canonical.sources) == 2
        assert {m.public_bibliography.record_id for m in canonical.sources} == {
            "edition-a", "edition-b",
        }
        assert {m.provider_id for m in canonical.sources} == {"seller-a"}
        assert {c.url for c in canonical.candidates} == {A_URL, B_URL}
        sources = tuple(read_notice_cover(db, row._core_notice_cover_document_ref)
                        for row in (a, b))
        with db._lock, db._connect() as sql:
            sql.execute("BEGIN")
            assert reattest_exact_origin_group_txn(sql, sources) == sources
        with db._lock, db._connect() as sql:
            sql.execute("DELETE FROM origin_links WHERE source_reference_id=?",
                        (second.source_reference_id,))
        with db._lock, db._connect() as sql:
            sql.execute("BEGIN")
            with pytest.raises(ValueError, match="relation_changed"):
                reattest_exact_origin_group_txn(sql, sources)
        assert db.entitlements() == [] and db.acquisitions() == []
    with closing(Storage(path)) as db:
        assert read_consolidated_notice_cover(db, shared) == canonical
