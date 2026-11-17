"""P0 #6366: exact known Search -> public C -> recommendation N2 detail.

Exercises the real persisted public C, the existing explicit holdings task and
both success/failure delivery paths without importing GTK, issuing HTTP or
inferring an edition from a recommendation title/ISBN/thumbnail.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.core.notice_c_cover_index import (
    persist_consolidated_cover_source, persist_exact_public_search_cover,
)
from cifereca.core.notice_c_recommendation_projection import (
    known_recommendation_public_c_document,
)
from cifereca.core.notice_cover_repository import _opaque_key
from cifereca.core.known_catalog_provenance import known_catalog_search_result
from cifereca.gui import recommendation_exact_detail as route
from cifereca.gui.detail_model import book_detail_presentation
from cifereca.models import ItemDetail
from cifereca.storage import Storage
from tests.test_core_p0_6366_public_c_sqlite_source_search import (
    persist, verified_public,
)


def known_row(source, identity="alice"):
    b = source.observation.fields_copy()["search_result"]["public_bibliography"]
    return {
        "key": "known:exact-public-source",
        "source": "known_catalog",
        "identity_id": identity,
        "provider_id": source.observation.key.provider_id,
        "item_id": b["record_id"],
        "title": b["title"],
        "author": b.get("author"),
        "local": False,
        "state": "remote_catalog",
        "media_format": b.get("format"),
        "thumbnail_url": "https://covers.example.org/legacy.jpg",
        "metadata": {
            "catalogue_scope_version": 2,
            "source_credential": "__public__",
            "source_collection": "public_catalog",
            "docbase": b["docbase"],
            "medium": b["medium"],
            "source_account_id": None,
            "source_revision": None,
        },
    }


def lookup(db, row, **overrides):
    meta = row["metadata"]
    coords = dict(
        identity_id=row["identity_id"],
        provider=row["provider_id"], credential="__public__",
        record_id=row["item_id"], docbase=meta["docbase"],
        source_collection="public_catalog",
    )
    coords.update(overrides)
    return known_recommendation_public_c_document(db, row, **coords)


def detail(row):
    return ItemDetail(
        record_id=row["item_id"], docbase=row["metadata"]["docbase"],
        title=row["title"], author=row["author"], type=None, year=None,
        thumbnail_url="https://covers.example.org/provider.jpg",
        availability_status="available", availability_label="Available",
        holdings=[], source_provider=row["provider_id"],
        source_credential="__public__",
    )


def test_real_public_c_reused_by_known_recommendation_after_source_purge(tmp_path):
    db = Storage(tmp_path / "recommendation-c.sqlite")
    try:
        source = persist(db, verified_public(
            source_provider="bnf", record_id="known-77", title="Même édition",
        ), at=100)
        canonical = persist_exact_public_search_cover(db, source)
        row = known_row(source)
        assert lookup(db, row) == canonical.document
        with db._lock, db._connect() as sql:
            sql.execute(
                "DELETE FROM metadata WHERE key=?",
                (_opaque_key(source.document),),
            )
        assert lookup(db, row) == canonical.document
        presentation = book_detail_presentation(
            title=row["title"], cover_document_ref=lookup(db, row),
        )
        assert presentation.cover_document_ref == canonical.document
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


@pytest.mark.parametrize("field,value", [
    ("source", "plugin"),
    ("identity_id", "bob"),
    ("provider_id", "other"),
    ("item_id", "other-record"),
    ("local", True),
    ("state", "remote_owned"),
    ("metadata.catalogue_scope_version", 3),
    ("metadata.source_credential", "private-credential"),
    ("metadata.source_collection", "library"),
    ("metadata.docbase", "other-docbase"),
    ("metadata.medium", "physical"),
    ("metadata.source_account_id", "borrower-1"),
    ("metadata.source_revision", "rev-2"),
    ("metadata.search_provenance", "known_catalog_stale"),
])
def test_changed_provenance_never_borrows_public_c(tmp_path, field, value):
    db = Storage(tmp_path / "rejected.sqlite")
    try:
        source = persist(db, verified_public(
            source_provider="bnf", record_id="known-77",
        ))
        persist_exact_public_search_cover(db, source)
        row = known_row(source)
        if field.startswith("metadata."):
            row["metadata"][field.split(".", 1)[1]] = value
        else:
            row[field] = value
        assert lookup(
            db, row, **({"identity_id": "alice"} if field == "identity_id" else {}),
        ) is None
        assert db.entitlements() == []
    finally:
        db.close()


def test_same_attested_public_c_can_be_read_by_two_independent_profiles(tmp_path):
    db = Storage(tmp_path / "public-cross-profile.sqlite")
    try:
        source = persist(db, verified_public(
            source_provider="bnf", record_id="known-77",
        ))
        canonical = persist_exact_public_search_cover(db, source).document
        assert lookup(db, known_row(source, identity="alice")) == canonical
        assert lookup(db, known_row(source, identity="bob")) == canonical
        assert db.entitlements() == []
    finally:
        db.close()


def test_verified_multisource_c_outranks_own_public_c_without_title_join(tmp_path):
    db = Storage(tmp_path / "multisource.sqlite")
    try:
        a = persist(db, verified_public(
            source_provider="bnf", record_id="known-77", title="Même titre",
        ))
        b = persist(db, verified_public(
            source_provider="numilog-catalog",
            record_id="different-record", title="Même titre",
        ))
        own_a = persist_exact_public_search_cover(db, a)
        persist_exact_public_search_cover(db, b)
        assert lookup(db, known_row(a)) == own_a.document
        target = a.observation.key.edition_id
        persist_consolidated_cover_source(
            db, canonical_edition_id=target,
            source=a, relation_proof_id="strong:fixture:bnf",
        )
        joined = persist_consolidated_cover_source(
            db, canonical_edition_id=target,
            source=b, relation_proof_id="strong:fixture:numilog",
        )
        assert lookup(db, known_row(a)) == joined.document
        assert lookup(db, known_row(b)) == joined.document
        assert len(joined.candidates) == 2
        assert db.entitlements() == []
    finally:
        db.close()


class ScopedStorage:
    """Keep real C SQLite read but use an independent fake public session."""
    def __init__(self, db):
        self._lock = db._lock
        self._connect = db._connect

    def identity_for_connection(self, provider, credential):
        return "alice" if credential == "__public__" else None

    def provider_connections(self, *, identity_id):
        return [{
            "identity_id": identity_id,
            "provider_id": "bnf",
            "credential_id": "__public__", "enabled": True,
        }]


def invoke_recommendation(db, row, *, fail=False, delayed=False, monkeypatch=None):
    item = known_catalog_search_result(row, identity_id="alice")
    assert item is not None
    calls, submitted, presentations, errors = [], [], [], []
    session = NS(connected=True)
    storage = ScopedStorage(db)
    def holdings(*_args, **_kw):
        calls.append("holdings")
        if fail:
            raise OSError("source unavailable")
        return detail(row)

    service = NS(
        storage=storage, current_identity=NS(identity_id="alice"),
        _source_context_epoch=1,
        _session_for_source=lambda provider, credential: session,
        holdings=holdings,
    )
    view = NS(
        widget=NS(get_mapped=lambda: True),
        show_existing_search_detail=lambda *a, **k: presentations.append(("ok", a, k)),
        show_existing_search_unavailable=lambda *a, **k: presentations.append(("offline", a, k)),
    )
    window = NS(
        service=service, _closed=False, _identity_context_generation=3,
        _search_generation=4, _shell_page_rebuild_token=object(),
        toast=lambda *args: errors.append(args),
    )
    view._window = window
    def submit(_label, work, done, failed, **_kwargs):
        submitted.append((work, done, failed))
        if not delayed:
            try:
                result = work()
            except BaseException as exc:
                failed(exc)
            else:
                done(result)
    window.runner = NS(submit=submit)
    monkeypatch.setattr(
        route, "_known_item", lambda _window, _coords: (item, row),
    )
    result = route.present_loaded_search_detail_in_place(
        window, view=view, context_sections=(), is_current=lambda: True,
        provider="bnf", credential="__public__", record_id=row["item_id"],
        docbase=row["metadata"]["docbase"],
        source_collection="public_catalog", identity_id="alice",
        known_source=True,
    )
    return result, window, submitted, presentations, calls, errors


@pytest.mark.parametrize("fail", [False, True])
def test_real_core_worker_propagates_c_to_known_recommendation_detail(
    tmp_path, monkeypatch, fail,
):
    db = Storage(tmp_path / "worker.sqlite")
    try:
        source = persist(db, verified_public(
            source_provider="bnf", record_id="known-77",
        ))
        expected = persist_exact_public_search_cover(db, source).document
        ok, _w, submitted, painted, calls, errors = invoke_recommendation(
            db, known_row(source), fail=fail, monkeypatch=monkeypatch,
        )
        assert ok and len(submitted) == 1
        assert calls == ["holdings"]
        assert len(painted) == 1
        assert painted[0][0] == ("offline" if fail else "ok")
        assert painted[0][2]["public_c_document"] == expected
        assert callable(painted[0][2]["c_selection_is_current"])
        assert painted[0][2]["c_selection_is_current"]() is True
        assert bool(errors) == fail
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_changed_account_epoch_refuses_late_documentary_detail(
    tmp_path, monkeypatch,
):
    db = Storage(tmp_path / "revoked.sqlite")
    try:
        source = persist(db, verified_public(
            source_provider="bnf", record_id="known-77",
        ))
        persist_exact_public_search_cover(db, source)
        ok, window, tasks, painted, _calls, _errors = invoke_recommendation(
            db, known_row(source), delayed=True, monkeypatch=monkeypatch,
        )
        assert ok and len(tasks) == 1
        work, deliver, _failed = tasks[0]
        produced = work()
        window.service._source_context_epoch += 1
        deliver(produced)
        assert painted == []
    finally:
        db.close()
