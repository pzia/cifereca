"""#6168/#2385: persistent canonical Notice C cover candidates by provider."""
from __future__ import annotations

from threading import Event
from types import SimpleNamespace as NS

import pytest

from cifereca.core.book_cover_projection import resolve_book_cover
from cifereca.core.notice_c_cover_index import (
    canonical_cover_document_ref, is_canonical_cover_document,
    persist_consolidated_cover_source, read_consolidated_notice_cover,
)
from cifereca.core.notice_cover_repository import (
    is_notice_cover_document, persist_notice_cover,
)
from cifereca.core.notice_source_observation import (
    NoticeSourceKey, admit_source_notice,
)
from cifereca.gui.notice_cover_cached_reader import cached_notice_cover_snapshot
from cifereca.gui.document_cover_service import DocumentCoverService
from cifereca.gui.cover_loader import CoverLoader
from cifereca.storage import Storage


EDITION = "edition:shared-qualified-20261116"


def _source(provider: str, *, identity: str | None = None) -> NoticeSourceKey:
    return NoticeSourceKey(
        provider_id=provider, visibility="public" if identity is None else "private",
        identity_id=identity, credential_id=None if identity is None else "cred-a",
        account_id=None if identity is None else "account-a",
        branch_id="catalogue",
        record_id="record:" + provider, edition_id="edition:" + provider,
        manifestation_role="digital",
    )


def _store(db, provider: str, url: str | None, *, at: int = 10,
           identity: str | None = None):
    observation = admit_source_notice(
        key=_source(provider, identity=identity), parser_id="fixture",
        parser_version="1", observed_at=at, status="complete",
        raw_fields={"cover": url},
    )
    return persist_notice_cover(
        db, observation, field_path=("cover",), cover_inventory_complete=True,
    )


def _join(db, provider, url, *, proof=None, **kwargs):
    source = _store(db, provider, url, **kwargs)
    return persist_consolidated_cover_source(
        db, canonical_edition_id=EDITION, source=source,
        relation_proof_id=proof or "strong:fixture:" + provider,
    )


def test_three_sources_one_canonical_ref_survives_source_eviction_and_restart(tmp_path):
    path = tmp_path / "cover.sqlite"
    db = Storage(path)
    ref = canonical_cover_document_ref(EDITION)
    try:
        assert is_canonical_cover_document(ref)
        assert is_notice_cover_document(ref)
        for i, provider in enumerate(("paris", "kobo", "bnf"), 1):
            result = _join(
                db, provider, "https://covers.example.org/" + provider + ".jpg",
            )
            assert result.epoch == i
            assert len(result.sources) == i
            assert len(result.candidates) == i
        assert {x.source for x in result.candidates} == {"paris", "kobo", "bnf"}
        snapshot = cached_notice_cover_snapshot(NS(storage=db), ref)
        assert snapshot.scope == ref
        assert snapshot.revision == 3
        assert snapshot.attested_book_identity == ref.source_key
        decision = resolve_book_cover(
            ref, snapshot.candidates, revision=snapshot.revision,
            snapshot_complete=snapshot.snapshot_complete,
            attested_book_identity=snapshot.attested_book_identity,
        )
        assert decision.status == "selected"
        assert decision.source == "bnf"
        # Short-lived source notices can be purged without a cascade to C.
        with db._lock, db._connect() as sql:
            sql.execute(
                "DELETE FROM metadata WHERE key LIKE 'notice.c.observation.v1:%'"
            )
        assert len(read_consolidated_notice_cover(db, ref).candidates) == 3
        assert cached_notice_cover_snapshot(NS(storage=db), ref) == snapshot
    finally:
        db.close()
    reopened = Storage(path)
    try:
        assert read_consolidated_notice_cover(reopened, ref) == result
        assert cached_notice_cover_snapshot(NS(storage=reopened), ref) == snapshot
    finally:
        reopened.close()


def test_one_source_update_preserves_other_provider_and_cas(tmp_path):
    db = Storage(tmp_path / "cover.sqlite")
    ref = canonical_cover_document_ref(EDITION)
    try:
        _join(db, "paris", "https://covers.example.org/paris.jpg")
        current = _join(db, "bnf", "https://covers.example.org/bnf.jpg")
        bnf = _store(
            db, "bnf", "https://covers.example.org/bnf-new.jpg", at=11,
        )
        with pytest.raises(ValueError, match="epoch_changed"):
            persist_consolidated_cover_source(
                db, canonical_edition_id=EDITION, source=bnf,
                relation_proof_id="strong:fixture:bnf", expected_epoch=1,
            )
        updated = persist_consolidated_cover_source(
            db, canonical_edition_id=EDITION, source=bnf,
            relation_proof_id="strong:fixture:bnf", expected_epoch=2,
        )
        assert updated.epoch == 3
        urls = {x.url for x in updated.candidates}
        assert "https://covers.example.org/paris.jpg" in urls
        assert "https://covers.example.org/bnf-new.jpg" in urls
        assert "https://covers.example.org/bnf.jpg" not in urls
        again = persist_consolidated_cover_source(
            db, canonical_edition_id=EDITION, source=bnf,
            relation_proof_id="strong:fixture:bnf",
        )
        assert again.epoch == updated.epoch
        assert read_consolidated_notice_cover(db, ref) == again
    finally:
        db.close()


def test_private_source_keeps_only_opaque_provenance_no_image_session_url(tmp_path):
    db = Storage(tmp_path / "private.sqlite")
    try:
        private = _store(
            db, "paris", "https://private.example.org/cover.jpg",
            identity="john",
        )
        aggregate = persist_consolidated_cover_source(
            db, canonical_edition_id=EDITION, source=private,
            relation_proof_id="strong:private:provider",
        )
        assert len(aggregate.sources) == 1
        assert aggregate.sources[0].visibility == "private"
        assert aggregate.sources[0].urls == ()
        assert aggregate.candidates == ()
        assert not aggregate.snapshot_complete
        assert cached_notice_cover_snapshot(
            NS(storage=db), aggregate.document,
        ).snapshot_complete is False
        with db._lock, db._connect() as sql:
            raw = sql.execute(
                "SELECT value FROM metadata WHERE key LIKE "
                "'notice.c.cover.canonical.v1:%'"
            ).fetchone()[0]
        assert "private.example.org" not in raw
        assert "cred-a" not in raw
        assert "account-a" not in raw
    finally:
        db.close()


def test_unattested_source_cannot_be_created_from_image_or_title(tmp_path):
    source_db = Storage(tmp_path / "source.sqlite")
    target_db = Storage(tmp_path / "target.sqlite")
    try:
        source = _store(source_db, "kobo", "https://covers.example.org/x.jpg")
        with pytest.raises(ValueError, match="stale_canonical_cover_source"):
            persist_consolidated_cover_source(
                target_db, canonical_edition_id=EDITION, source=source,
                relation_proof_id="unverified:test",
            )
        with pytest.raises(ValueError, match="invalid_canonical_cover_evidence"):
            persist_consolidated_cover_source(
                source_db, canonical_edition_id=EDITION, source=source,
                relation_proof_id="",
            )
        with pytest.raises(ValueError, match="invalid_canonical_edition"):
            canonical_cover_document_ref("a title with whitespace")
        assert read_consolidated_notice_cover(
            source_db, canonical_cover_document_ref(EDITION),
        ) is None
    finally:
        source_db.close()
        target_db.close()


def test_canonical_cover_through_shared_n2_worker_without_provider_requests(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    db = Storage(tmp_path / "cover.sqlite")
    _join(db, "paris", "https://covers.example.org/paris.jpg")
    aggregate = _join(db, "kobo", "https://covers.example.org/kobo.jpg")
    urls = []
    loader = CoverLoader(max_workers=2)
    monkeypatch.setattr(
        loader, "load", lambda uri, callback, **kw: (
            urls.append(uri) or NS(cancel=lambda: None)
        ),
    )
    reads = []
    def source_reader(doc):
        reads.append(doc)
        return cached_notice_cover_snapshot(NS(storage=db), doc)

    service = DocumentCoverService(
        loader, cached_source_reader=source_reader,
        cached_source_scope=is_notice_cover_document,
    )
    ready = Event()
    try:
        first = service.get_cover(
            aggregate.document, "list", lambda _: None,
            on_source_changed=ready.set,
        )
        assert ready.wait(10)
        assert not first.still_current
        second = service.get_cover(aggregate.document, "list", lambda _: None)
        assert second.source_uri in {
            "https://covers.example.org/paris.jpg",
            "https://covers.example.org/kobo.jpg",
        }
        assert len(urls) == 1
        assert service._snapshots[aggregate.document].revision == aggregate.epoch
        # A list/detail N2 hit must not initiate another documentary read;
        # N3 may legitimately request a separate preview size.
        assert reads == [aggregate.document]
        service.get_cover(aggregate.document, "detail", lambda _: None)
        assert reads == [aggregate.document]
    finally:
        service.close()
        loader.close()
        db.close()
