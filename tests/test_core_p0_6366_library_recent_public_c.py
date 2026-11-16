"""P0 #6366: exact completed OriginLink → public C → Library/Recent N2.

All Core source records and OriginLinks use real SQLite writers. There is no
catalogue-to-book join by title, ISBN, URL or identical downloaded bytes alone.
The C is descriptive; the published provider right is NOT projected by it.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.core.notice_c_cover_index import (
    persist_consolidated_cover_source, persist_exact_public_search_cover,
    read_consolidated_notice_cover,
)
from cifereca.core.notice_c_local_origin_projection import (
    VerifiedLocalPublicCLink, project_local_origin_public_c,
    valid_local_public_c_link,
)
from cifereca.core.notice_cover_repository import _opaque_key
from cifereca.gui.library_rows import project_library_rows
from cifereca.gui.recent_rows import project_recent_rows
from cifereca.gui.recent_detail_controller import recent_selected
from cifereca.gui.library_detail_controller import library_selected
from cifereca.gui.detail_model import book_detail_presentation
from cifereca.gui.notice_cover_cached_reader import cached_notice_cover_snapshot
from cifereca.storage import Storage
from tests.test_core_p0_3171_atomic_publication_v14 import (
    new_source, publish, register, target,
)
from tests.test_core_p0_6366_public_c_sqlite_source_search import (
    persist, verified_public,
)


A_URL = "https://covers.example.org/origin-a-c.jpg"
B_URL = "https://covers.example.org/origin-b-c.jpg"


class Row:
    def __init__(self, *args):
        self.args = args
        self.key = args[0]


def library_view(source):
    rows, _ = project_library_rows(
        [source], row_factory=Row,
        provider_display_name=lambda provider: provider,
        availability_label=lambda _: "Local",
        backup_status_label=lambda _: "",
        format_date=str,
    )
    return rows[0]


def recent_view(source):
    rows, _ = project_recent_rows([source], row_factory=Row)
    return rows[0]


def _real_origins(db, tmp_path, *, separate_owners=False):
    a, sa = new_source(
        "A", provider="seller-a", credential="card-a",
        edition="edition-a", right="right-a",
    )
    b_owner = "B" if separate_owners else "A"
    b, sb = new_source(
        b_owner, provider="seller-b", credential="card-b",
        edition="edition-b", right="right-b",
    )
    register(db, "A", "seller-a", "card-a")
    register(db, b_owner, "seller-b", "card-b")
    pa = target(tmp_path, "A")
    pb = target(tmp_path, "B") if separate_owners else pa
    root = tmp_path / "EBooks"
    result_a = publish(db, root, pa, a, sa)
    result_b = publish(db, root, pb, b, sb)
    assert result_a.artifact_id == result_b.artifact_id
    return result_a, result_b, pa


def _source(db, provider, record, url):
    public = verified_public(
        source_provider=provider, record_id=record,
        title="Un ouvrage commun", author="Autrice",
        thumbnail_url=url,
    )
    return persist(db, public, at=100)


def _views(db, artifact, placement, path):
    library = {
        "key": "artifact:" + artifact,
        "identity_id": "A", "artifact_id": artifact,
        "placement_id": placement, "local": True,
        "provider_id": "local", "providers": ["local"],
        "state": "local", "source": "catalog",
        "title": "Un ouvrage commun", "author": "Autrice",
        "thumbnail_url": "https://covers.example.org/legacy.jpg",
        "metadata": {}, "path": str(path),
    }
    recent = {
        **library, "key": placement,
        "acquired_at": 100, "updated_at": 100,
    }
    return library, recent


def _render_recent_detail(row, link):
    events = []
    detail = NS(
        show_presentation=lambda presentation: events.append(presentation),
        show_summary=lambda **kwargs: events.append(kwargs),
        empty=lambda *args: events.append(("empty", args)),
    )
    host = NS(
        _closed=False, _recent_objects={row["placement_id"]: row},
        recent_page=NS(detail=detail),
        service=NS(current_identity=NS(identity_id="A"),
                   provider_display_name=lambda _: "Local"),
        search_controls=object(),
        _add_book_notice=lambda *_: None,
        _add_local_book_actions=lambda *_: None,
        _add_facets=lambda *_: None,
    )
    recent_selected(
        host, NS(key=row["placement_id"]), Path=Path,
        format_recent_timestamp=lambda *_args, **_kwargs: "Aujourd'hui",
    )
    assert len(events) == 1
    assert events[0].cover_document_ref == link.document
    assert events[0].thumbnail_url is None


def _render_library_detail(row, link):
    events = []
    detail = NS(
        show_presentation=lambda presentation, **_: events.append(presentation),
        show_local_presentation=lambda *args, **kwargs: events.append(("local",)),
        empty=lambda *_: None,
    )
    host = NS(
        _closed=False,
        _cancel_cached_book_details=lambda: None,
        _library_objects={row["key"]: row},
        library_page=NS(detail=detail),
        service=NS(
            current_identity=NS(identity_id="A"),
            provider_display_name=lambda value: value,
            identity_by_id=lambda _: None,
        ),
        _book_description=lambda _: "",
        _library_availability_label=lambda _: "Local",
        _kobo_backup_status_label=lambda *_: "",
        _add_local_book_actions=lambda *_: None,
        _add_book_recommendation_action=lambda *_: None,
        _add_facets=lambda *_: None,
        _add_plugin_book_actions=lambda *_: None,
        _request_cached_book_details=lambda *_: None,
        library_controls=object(),
    )
    library_selected(
        host, NS(key=row["key"]),
        Gtk=NS(), unique_library_loan=lambda *_: None,
        book_detail_presentation=book_detail_presentation,
        detail_language=lambda value: value, document_links=lambda _: (),
    )
    assert len(events) == 1
    assert events[0].cover_document_ref == link.document
    assert events[0].thumbnail_url is None


def test_real_origin_to_same_public_c_in_both_views_list_detail(tmp_path):
    db = Storage(tmp_path / "real-origin.sqlite")
    try:
        a, b, path = _real_origins(db, tmp_path)
        source_a = _source(db, "seller-a", "edition-a", A_URL)
        source_b = _source(db, "seller-b", "edition-b", B_URL)
        persist_exact_public_search_cover(db, source_a)
        persist_exact_public_search_cover(db, source_b)
        # The relation is already stored in canonical Core C, not invented
        # by the Library/Recent viewer. The live producer is tested in #6440.
        persist_consolidated_cover_source(
            db, canonical_edition_id="edition:verified-two-origins",
            source=source_a, relation_proof_id="strong:fixture:origin-a",
        )
        canonical = persist_consolidated_cover_source(
            db, canonical_edition_id="edition:verified-two-origins",
            source=source_b, relation_proof_id="strong:fixture:origin-b",
        )
        lib, recent = _views(db, a.artifact_id, a.placement_id, path)
        project_local_origin_public_c(db, [lib, recent], identity_id="A")
        lnk = lib["_public_canonical_cover_link"]
        assert type(lnk) is VerifiedLocalPublicCLink
        assert lnk == recent["_public_canonical_cover_link"]
        assert lnk.document == canonical.document
        assert len(lnk.source_refs) == 2
        assert lnk.epoch == canonical.epoch
        assert valid_local_public_c_link(
            lnk, identity_id="A",
            artifact_id=a.artifact_id, placement_id=a.placement_id,
        )

        ls, rs = library_view(lib), recent_view(recent)
        assert ls._cover_document_ref == rs._cover_document_ref == canonical.document
        assert ls.args[5] is rs.args[5] is None
        _render_library_detail(lib, lnk)
        _render_recent_detail(recent, lnk)

        n2 = cached_notice_cover_snapshot(NS(storage=db), canonical.document)
        assert {cover.url for cover in n2.candidates} == {A_URL, B_URL}
        assert n2.scope == ls._cover_document_ref
        assert db.entitlements() == [] and db.acquisitions() == []
        with db._lock, db._connect() as sql:
            for src in (source_a, source_b):
                sql.execute(
                    "DELETE FROM metadata WHERE key=?",
                    (_opaque_key(src.document),),
                )
        retained = read_consolidated_notice_cover(db, canonical.document)
        assert len(retained.sources) == 2
        assert retained.candidates == canonical.candidates
        # Even offline after the provider source TTL purge, current exact
        # OriginLinks can still attach this persisted public C to local rows.
        again, fresh = _views(db, a.artifact_id, a.placement_id, path)
        project_local_origin_public_c(db, [again, fresh], identity_id="A")
        assert again["_public_canonical_cover_link"].document == canonical.document
    finally:
        db.close()


def test_same_sha_in_other_profile_does_not_adopt_foreign_public_c(tmp_path):
    db = Storage(tmp_path / "foreign.sqlite")
    try:
        a, b, path = _real_origins(db, tmp_path, separate_owners=True)
        first = _source(db, "seller-a", "edition-a", A_URL)
        second = _source(db, "seller-b", "edition-b", B_URL)
        common = persist_consolidated_cover_source(
            db, canonical_edition_id="edition:cross-account",
            source=first, relation_proof_id="strong:fixture:first",
        )
        persist_consolidated_cover_source(
            db, canonical_edition_id="edition:cross-account",
            source=second, relation_proof_id="strong:fixture:second",
        )
        lib, recent = _views(db, a.artifact_id, a.placement_id, path)
        project_local_origin_public_c(db, [lib, recent], identity_id="A")
        assert "_public_canonical_cover_link" not in lib
        assert "_public_canonical_cover_link" not in recent
        assert common.document != getattr(library_view(lib), "_cover_document_ref", None)
        assert db.entitlements() == []
    finally:
        db.close()


def test_no_origin_only_title_is_no_link(tmp_path):
    db = Storage(tmp_path / "not-linked.sqlite")
    try:
        source = _source(db, "seller-a", "edition-a", A_URL)
        persist_exact_public_search_cover(db, source)
        lib = {
            "identity_id": "A",
            "artifact_id": "sha256:" + "a"*64,
            "placement_id": "placement:unrelated",
            "title": "Un ouvrage commun",
            "thumbnail_url": A_URL,
        }
        project_local_origin_public_c(db, [lib], identity_id="A")
        assert "_public_canonical_cover_link" not in lib
        assert db.entitlements() == []
    finally:
        db.close()


def test_public_c_link_preserves_exact_epub_n1_and_rejects_forged_rows(tmp_path):
    db = Storage(tmp_path / "local-priority.sqlite")
    try:
        a, _, path = _real_origins(db, tmp_path)
        first = _source(db, "seller-a", "edition-a", A_URL)
        c = persist_exact_public_search_cover(db, first)
        row, recent = _views(db, a.artifact_id, a.placement_id, path)
        project_local_origin_public_c(db, [row, recent], identity_id="A")
        proof = row["_public_canonical_cover_link"]
        assert proof.document == c.document
        # Any explicit EPUB packet owns the row and never borrows N3 by URL.
        with_epub = {**row, "_exact_local_cover_evidence": object()}
        with_recent_epub = {**recent, "_exact_local_cover_evidence": object()}
        assert not hasattr(library_view(with_epub), "_cover_public_c_link")
        assert not hasattr(recent_view(with_recent_epub), "_cover_public_c_link")
        assert not valid_local_public_c_link(
            proof, identity_id="B",
            artifact_id=a.artifact_id, placement_id=a.placement_id,
        )
        for change in (
            {"identity_id": "B"},
            {"artifact_id": "sha256:" + "f"*64},
            {"placement_id": "other"},
            {"_library_view_owner": "B"},
        ):
            wrong = {**row, **change}
            assert not hasattr(library_view(wrong), "_cover_public_c_link")
    finally:
        db.close()
