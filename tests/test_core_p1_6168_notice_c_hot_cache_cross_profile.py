"""#6168/#2385: one public Notice C source, exact N2 and shared warm N3."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import Event

from cifereca.core.notice_source_observation import (
    NoticeSourceKey, admit_source_notice,
)
from cifereca.core.notice_cover_repository import (
    is_notice_cover_document, persist_notice_cover,
)
from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.document_cover_service import DocumentCoverService
from cifereca.gui.notice_cover_cached_reader import cached_notice_cover_snapshot
from cifereca.storage import Storage


URL = "https://covers.example.org/same-media-for-two-editions.png"
PUBLIC = NoticeSourceKey(
    provider_id="bnf", visibility="public",
    identity_id=None, credential_id=None, account_id=None,
    branch_id="catalogue", record_id="ark:identical-image",
    edition_id="edition:one", manifestation_role="digital",
)
# Valid recognized N3 PNG prefix, enough for a physical source cache HIT.
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 40


def saved(db, key, at=1):
    proof = admit_source_notice(
        key=key, parser_id="bnf-parser", parser_version="1",
        observed_at=at, raw_fields={"media": {"cover": URL}},
    )
    return persist_notice_cover(
        db, proof, field_path=("media", "cover"),
        cover_inventory_complete=True,
    )


def facade(loader, db):
    return DocumentCoverService(
        loader,
        cached_source_reader=lambda document: cached_notice_cover_snapshot(
            type("Core", (), {"storage": db})(), document,
        ),
        cached_source_scope=is_notice_cover_document,
    )


def wait_for_document(service, document):
    changed = Event()
    pending = service.get_cover(
        document, "detail", lambda _: None,
        on_source_changed=changed.set,
    )
    assert pending.source_uri is None
    assert changed.wait(10)
    assert not pending.still_current


def hit(service, document, variant="detail"):
    done = Event()
    received = []
    request = service.get_cover(
        document, variant,
        lambda value: (received.append(value), done.set()),
    )
    assert done.wait(10)
    assert received and isinstance(received[-1], Path)
    assert received[-1].is_file()
    assert request.source_uri == URL
    return received[-1]


def test_public_cross_profile_same_n2_and_n3_without_get_or_reparse(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    db = Storage(tmp_path / "library.sqlite")
    loader = CoverLoader(max_workers=2)
    # Any transport attempt is a regression: the seeded N3 file is valid.
    network = []
    monkeypatch.setattr(
        loader.client, "stream",
        lambda *args, **kwargs: network.append(args) or
        (_ for _ in ()).throw(AssertionError("unexpected image GET")),
    )
    cache = loader._path(URL)
    cache.write_bytes(PNG)
    first = saved(db, PUBLIC)
    different = saved(db, replace(PUBLIC, edition_id="edition:two"))
    assert first.document != different.document
    assert first.document.identity_id is None
    assert first.attribute.candidates[0].url == different.attribute.candidates[0].url

    initial = facade(loader, db)
    try:
        wait_for_document(initial, first.document)
        # A's view has attested the exact document; same service/view cache is
        # shared with B without involving the active viewer identity.
        a = hit(initial, first.document)
        source_reads = initial.stats()["n2_miss"]
        b = hit(initial, first.document)
        assert a == b == cache
        assert initial.stats()["n2_miss"] == source_reads
        assert initial.stats()["n2_hit"] >= 2
        assert not network
    finally:
        initial.close()

    # Process-level N2 was reset, but C's persisted notice and N3 survive.
    reopened = facade(loader, db)
    try:
        wait_for_document(reopened, first.document)
        again = hit(reopened, first.document)
        assert again == cache

        # The same physical PNG is not proof of two equivalent editions.
        wait_for_document(reopened, different.document)
        second_edition = hit(reopened, different.document)
        assert second_edition == cache
        assert reopened._snapshots[first.document].scope != (
            reopened._snapshots[different.document].scope
        )
        assert not network
        assert loader.stats()["cache_hit_valid"] >= 4
        # No new N3 identity/profile directory and no network on warm media.
        assert len(list(loader.root.glob("*.img"))) == 1
    finally:
        reopened.close()
        loader.close()
        db.close()
