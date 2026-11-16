"""#6366/#4979: Notice C public covers must preserve exact supplier edition revisions.

A shared image, record ID or docbase is NOT proof of the same edition. The
legacy Search N2 scope already distinguishes revision_id; the persisted
Notice C source must not silently collapse its exact revisions.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

from cifereca.core import search_orchestration
from cifereca.core.notice_cover_projection import notice_cover_document_ref
from cifereca.core.notice_cover_repository import read_notice_cover
from cifereca.core.notice_search_cover_producer import admit_public_search_cover
from cifereca.gui.notice_cover_cached_reader import cached_notice_cover_snapshot
from cifereca.storage import Storage
from tests.test_core_p0_6366_real_search_notice_c_ingestion import (
    URL, OTHER_URL, public_row,
)


def ref(row):
    observation = admit_public_search_cover(
        row, identity_id="alice", observed_at=10,
        public_cover_url=row.thumbnail_url,
    )
    assert observation is not None
    return observation, notice_cover_document_ref(observation.key)


def test_revision_marker_is_part_of_exact_notice_c_edition_not_source_record():
    source = public_row()
    baseline, a = ref(source)
    latest, b = ref(replace(source, revision_id="rev-v2"))
    another, c = ref(replace(source, revision_id="rev-v3"))
    assert len({a, b, c}) == 3
    assert baseline.key.record_id == latest.key.record_id == another.key.record_id
    assert len({
        baseline.key.edition_id, latest.key.edition_id,
        another.key.edition_id,
    }) == 3
    assert a.identity_id is b.identity_id is c.identity_id is None
    assert baseline.key.visibility == latest.key.visibility == "public"
    # Repeated viewers do not make new editions.
    same = admit_public_search_cover(
        replace(source, revision_id="rev-v2"),
        identity_id="bob", observed_at=11, public_cover_url=URL,
    )
    assert same is not None and notice_cover_document_ref(same.key) == b


def test_two_versions_with_shared_record_and_image_never_revoke_one_another(
    tmp_path, monkeypatch,
):
    clock = [200]
    monkeypatch.setattr(search_orchestration.time, "time", lambda: clock[0])
    db = Storage(tmp_path / "notices.sqlite")
    v1 = public_row(revision_id="edition-v1")
    v2 = replace(v1, revision_id="edition-v2")
    _, a = ref(v1)
    _, b = ref(v2)
    try:
        assert search_orchestration._mirror_remote_results(
            NS(storage=db), [v1, v2], identity_id="alice",
        ) == 2
        assert v1._core_notice_cover_document_ref == a
        assert v2._core_notice_cover_document_ref == b
        assert read_notice_cover(db, a).attribute.candidates[0].url == URL
        assert read_notice_cover(db, b).attribute.candidates[0].url == URL
        assert read_notice_cover(db, a).epoch == 1
        assert read_notice_cover(db, b).epoch == 1
        assert cached_notice_cover_snapshot(NS(storage=db), a).revision == 1
        assert cached_notice_cover_snapshot(NS(storage=db), b).revision == 1
        # A later, same-record cover change on edition v2 cannot replace v1.
        clock[0] = 201
        changed_v2 = replace(v2, thumbnail_url=OTHER_URL)
        assert search_orchestration._mirror_remote_results(
            NS(storage=db), [changed_v2], identity_id="bob",
        ) == 1
        assert changed_v2._core_notice_cover_document_ref == b
        assert read_notice_cover(db, a).attribute.candidates[0].url == URL
        assert read_notice_cover(db, a).epoch == 1
        assert read_notice_cover(db, b).attribute.candidates[0].url == OTHER_URL
        assert read_notice_cover(db, b).epoch == 2
        with db._lock, db._connect() as conn:
            assert conn.execute(
                "SELECT count(*) FROM metadata "
                "WHERE key LIKE 'notice.c.observation.v1:%'"
            ).fetchone()[0] == 2
    finally:
        db.close()


def test_supplier_revision_marker_cannot_be_harvested_from_unqualified_row():
    source = public_row()
    for mutation in (
        {"source_credential": "personal-token"},
        {"source_collection": "library"},
        {"source_account_id": "owned-account"},
        {"metadata": {"search_provenance": "known_catalog_stale"}},
        {"thumbnail_url": URL + "?token=secret"},
    ):
        row = replace(source, revision_id="rev-v2", **mutation)
        assert admit_public_search_cover(
            row, identity_id="alice", observed_at=10,
            public_cover_url=URL,
        ) is None
