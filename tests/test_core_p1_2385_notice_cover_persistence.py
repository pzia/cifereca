"""#2385/#6168: durable exact Notice C data are the only source of N2 images."""
from __future__ import annotations

from dataclasses import replace
from threading import Event
from types import SimpleNamespace as NS

import pytest

from cifereca.core.notice_source_observation import (
    NoticeSourceKey, admit_source_notice,
)
from cifereca.core.notice_cover_projection import notice_cover_document_ref
from cifereca.core.notice_cover_repository import (
    PersistedNoticeCover, persist_notice_cover, read_notice_cover,
    read_current_notice_cover,
    is_notice_cover_document,
)
from cifereca.gui.notice_cover_cached_reader import cached_notice_cover_snapshot
from cifereca.gui.document_cover_service import DocumentCoverService
from cifereca.gui.cover_loader import CoverLoader
from cifereca.storage import Storage


URL = "https://covers.example.org/exact.jpg"
OTHER = "https://covers.example.org/revised.jpg"
PUBLIC = NoticeSourceKey(
    provider_id="bnf", visibility="public", identity_id=None,
    credential_id=None, account_id=None, branch_id="catalogue",
    record_id="ark:exact", edition_id="digital:1",
    manifestation_role="digital",
)
PRIVATE = replace(
    PUBLIC, visibility="private", identity_id="alice",
    credential_id="connection-a", account_id="account-a",
)


def notice(url=URL, *, key=PUBLIC, status="complete", observed_at=1,
           parser_version="1"):
    return admit_source_notice(
        key=key, parser_id="bnf-parser", parser_version=parser_version,
        observed_at=observed_at, status=status,
        raw_fields={"media": {"cover": url}, "title": "Le livre"},
    )


def store(storage, observed, **kw):
    return persist_notice_cover(
        storage, observed, field_path=kw.get("field_path", ("media", "cover")),
        cover_inventory_complete=kw.get("complete", True),
        expected_epoch=kw.get("epoch"),
    )


def test_public_cover_source_durable_and_reused_across_viewer_profiles(tmp_path):
    path = tmp_path / "reader.sqlite"
    first = Storage(path)
    ref = notice_cover_document_ref(PUBLIC)
    try:
        a = store(first, notice())
        assert isinstance(a, PersistedNoticeCover)
        assert a.epoch == 1
        assert a.attribute.candidates[0].url == URL
        assert first.schema_version() >= 17
        # A and B are viewers on one installation. The public source has
        # no viewer profile key; the exact same N2 ref is reused.
        for _viewer in ("alice", "bob", "alice"):
            same = read_notice_cover(first, ref)
            assert same == a
            assert same.document.identity_id is None
            assert store(first, notice(observed_at=3)).epoch == 1
    finally:
        first.close()
    second = Storage(path)
    try:
        persisted = read_notice_cover(second, ref)
        assert persisted is not None
        assert persisted.epoch == 1
        assert persisted.attribute.source_revision == a.attribute.source_revision
        assert persisted.attribute.candidates[0].url == URL
        assert cached_notice_cover_snapshot(NS(storage=second), ref).revision == 1
    finally:
        second.close()


def test_two_same_image_url_editions_and_private_accounts_never_join(tmp_path):
    db = Storage(tmp_path / "c.sqlite")
    try:
        a, b = notice(), notice(key=replace(PUBLIC, edition_id="digital:2"))
        pa = store(db, a)
        pb = store(db, b)
        assert pa.document != pb.document
        assert pa.attribute.candidates[0].url == pb.attribute.candidates[0].url
        private_a = store(db, notice(key=PRIVATE))
        private_b = store(db, notice(key=replace(
            PRIVATE, identity_id="bob", credential_id="connection-b",
            account_id="account-b",
        )))
        assert private_a.document != private_b.document
        assert read_notice_cover(db, private_a.document) == private_a
        assert read_notice_cover(db, private_b.document) == private_b
        assert read_notice_cover(db, notice_cover_document_ref(
            replace(PUBLIC, record_id="ark:unknown"),
        )) is None
        assert cached_notice_cover_snapshot(
            NS(storage=db), notice_cover_document_ref(
                replace(PUBLIC, record_id="ark:unknown"),
            ),
        ) is None
    finally:
        db.close()


def test_monotone_revision_and_cas_for_changed_parser_and_cover_contract(tmp_path):
    db = Storage(tmp_path / "c.sqlite")
    ref = notice_cover_document_ref(PUBLIC)
    try:
        one = store(db, notice(), epoch=0)
        assert one.epoch == 1
        with pytest.raises(ValueError, match="epoch_changed"):
            store(db, notice(OTHER, observed_at=2), epoch=0)
        two = store(db, notice(OTHER, observed_at=2), epoch=1)
        assert two.epoch == 2
        with pytest.raises(ValueError, match="stale_source"):
            store(db, notice(observed_at=1))
        three = store(
            db, notice(OTHER, observed_at=3, parser_version="2"), epoch=2,
        )
        assert three.epoch == 3
        incomplete = store(
            db, notice(OTHER, observed_at=4, parser_version="2"),
            field_path=("new", "path"), complete=False, epoch=3,
        )
        # A changed parser field cannot erase the previous attested image
        # until the replacement field's inventory is actually complete.
        assert incomplete == three
        assert read_notice_cover(db, ref).epoch == 3
        assert incomplete.attribute.candidates[0].url == OTHER
        four = store(
            db, notice(OTHER, observed_at=5, parser_version="2"),
            field_path=("new", "path"), complete=True, epoch=3,
        )
        assert four.epoch == 4
        assert four.attribute.state == "missing"
        assert read_notice_cover(db, ref).epoch == 4
        assert four.attribute.candidates == ()
    finally:
        db.close()


def test_newer_partial_observation_cannot_tombstone_complete_source(tmp_path):
    db = Storage(tmp_path / "c.sqlite")
    try:
        first = store(db, notice())
        partial = store(db, notice(None, status="partial", observed_at=9))
        assert partial == first
        assert read_notice_cover(db, first.document).attribute.candidates[0].url == URL
        replaced = store(db, notice(OTHER, observed_at=10))
        assert replaced.epoch == 2
        assert replaced.attribute.candidates[0].url == OTHER
    finally:
        db.close()


def test_first_partial_is_pending_and_complete_empty_is_only_attested_missing(tmp_path):
    db = Storage(tmp_path / "c.sqlite")
    try:
        part = store(db, notice(None, status="partial"))
        assert part.attribute.state == "pending"
        assert not part.attribute.snapshot_complete
        full = store(db, notice(None, observed_at=2))
        assert full.epoch == 2
        assert full.attribute.state == "missing"
        assert full.attribute.snapshot_complete
        same = store(db, notice(None, observed_at=3))
        assert same.epoch == 2
    finally:
        db.close()


def test_corrupt_persisted_source_is_not_treated_as_empty_or_silently_overwritten(tmp_path):
    db = Storage(tmp_path / "c.sqlite")
    try:
        initial = store(db, notice())
        with db._lock, db._connect() as con:
            con.execute(
                "UPDATE metadata SET value=? WHERE key LIKE ?",
                ('{"schema":1,"schema":1}', "notice.c.observation.v1:%"),
            )
        with pytest.raises(ValueError, match="notice_c_persisted_corrupt"):
            read_notice_cover(db, initial.document)
        with pytest.raises(ValueError, match="notice_c_persisted_corrupt"):
            store(db, notice(OTHER, observed_at=3))
    finally:
        db.close()


def test_invalid_parsed_observation_fails_before_storage_write(tmp_path):
    db = Storage(tmp_path / "c.sqlite")
    valid = notice()
    corrupted = replace(valid, source_revision="0" * 64)
    try:
        with pytest.raises(ValueError):
            store(db, corrupted)
        with db._lock, db._connect() as con:
            assert con.execute(
                "SELECT count(*) FROM metadata WHERE key LIKE 'notice.c.observation.v1:%'"
            ).fetchone()[0] == 0
        with pytest.raises(ValueError):
            persist_notice_cover(
                db, valid, field_path=("bad\x00",),
                cover_inventory_complete=True,
            )
    finally:
        db.close()


def test_core_reader_source_miss_asynchronous_and_single_n2_ref(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    storage = Storage(tmp_path / "c.sqlite")
    observed = store(storage, notice())
    loader = CoverLoader(max_workers=2)
    requests = []
    monkeypatch.setattr(
        loader, "load", lambda uri, cb, **kwargs: (
            requests.append(uri) or NS(cancel=lambda: None)
        ),
    )
    facade = NS(storage=storage)
    service = DocumentCoverService(
        loader, cached_source_reader=lambda ref: cached_notice_cover_snapshot(
            facade, ref,
        ),
    )
    changed = Event()
    notified = []
    try:
        initial = service.get_cover(
            observed.document, "detail", lambda _: None,
            on_source_changed=lambda: (notified.append("rebind"), changed.set()),
        )
        assert changed.wait(10)
        assert notified == ["rebind"]
        assert not initial.still_current
        request = service.get_cover(observed.document, "detail", lambda _: None)
        assert request.source_uri == URL
        assert requests == [URL]
        assert service._snapshots[observed.document].revision == observed.epoch
    finally:
        service.close()
        loader.close()
        storage.close()



def test_same_second_revision_change_requires_exact_cas_not_fake_time_resolution(
    tmp_path,
):
    db = Storage(tmp_path / "same-second.sqlite")
    try:
        one = store(db, notice(URL, observed_at=1))
        with pytest.raises(ValueError, match="stale_source"):
            store(db, notice(OTHER, observed_at=1))
        updated = store(db, notice(OTHER, observed_at=1), epoch=one.epoch)
        assert updated.epoch == one.epoch + 1
        assert updated.attribute.candidates[0].url == OTHER
        with pytest.raises(ValueError, match="epoch_changed"):
            store(db, notice(URL, observed_at=1), epoch=one.epoch)
    finally:
        db.close()



def test_readthrough_never_schedules_legacy_n2_ref_or_unknown_plugin_source(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    loader = CoverLoader(max_workers=2)
    attempts = []
    reader = lambda ref: attempts.append(ref)
    n2 = DocumentCoverService(
        loader, cached_source_reader=reader,
        cached_source_scope=is_notice_cover_document,
    )
    try:
        doc = notice_cover_document_ref(PUBLIC)
        foreign = replace(doc, source_key="search:unrelated-legacy")
        assert is_notice_cover_document(doc)
        assert not is_notice_cover_document(foreign)
        callbacks = []
        for _ in range(40):
            n2.get_cover(foreign, "list", callbacks.append)
        assert callbacks == [None] * 40
        assert not attempts
        assert not n2._read_active
        assert not n2._read_waiters
        assert n2.decisions.peek(foreign, revision=1) is None
    finally:
        n2.close()
        loader.close()


def test_source_scope_predicate_requires_reader_and_fails_closed(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    loader = CoverLoader(max_workers=2)
    try:
        with pytest.raises(TypeError, match="requires a callable reader"):
            DocumentCoverService(
                loader, cached_source_scope=is_notice_cover_document,
            )
        source_reads = []
        service = DocumentCoverService(
            loader,
            cached_source_reader=lambda ref: source_reads.append(ref),
            cached_source_scope=lambda ref: (_ for _ in ()).throw(
                RuntimeError("corrupt eligibility"),
            ),
        )
        try:
            assert service.get_cover(
                notice_cover_document_ref(PUBLIC), "detail", lambda _: None,
            ).source_uri is None
            assert source_reads == []
            assert not service._read_active
        finally:
            service.close()
    finally:
        loader.close()


@pytest.mark.parametrize("damage", ["epoch", "watermark", "proof", "evicted", "malformed", "legacy"])
@pytest.mark.parametrize("private", [False, True])
def test_exact_n2_checks_durable_source_fence_after_restart(tmp_path, damage, private):
    from contextlib import closing
    import json
    from cifereca.core.notice_cover_repository import _fence_key, _opaque_key
    path = tmp_path / "n2-fence.sqlite"
    with closing(Storage(path)) as db:
        source = store(db, notice(key=PRIVATE if private else PUBLIC))
        independent = store(db, notice(key=replace(PUBLIC, edition_id="digital:other")))
        key = _fence_key(_opaque_key(source.document))
        with db._lock, db._connect() as sql:
            raw, = sql.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
            fence = json.loads(raw)
            if damage == "legacy":
                sql.execute("DELETE FROM metadata WHERE key=?", (key,))
            else:
                if damage == "epoch":
                    fence["epoch"] += 1
                elif damage == "watermark":
                    fence["last_seen_at"] += 1
                elif damage == "proof":
                    fence["proof"] = "0" * 64
                elif damage == "evicted":
                    fence["epoch"] += 1
                    fence["evicted"] = True
                else:
                    fence = {}
                sql.execute("UPDATE metadata SET value=? WHERE key=?",
                            (json.dumps(fence), key))
    with closing(Storage(path)) as db:
        # Raw diagnostic body is retained; it is not N2 authority.
        assert read_notice_cover(db, source.document) == source
        # Core must still check the durable fence for both source scopes.
        # A private source is never admitted by the shared GTK loader (#7454),
        # even when the Core record is valid or a historical fence is absent.
        if damage == "legacy":
            assert read_current_notice_cover(db, source.document) == source
        else:
            with pytest.raises(ValueError, match="notice_c_source_fence_corrupt"):
                read_current_notice_cover(db, source.document)
        if private:
            assert cached_notice_cover_snapshot(NS(storage=db), source.document) is None
        elif damage == "legacy":
            assert cached_notice_cover_snapshot(NS(storage=db), source.document).revision == source.epoch
        else:
            with pytest.raises(ValueError, match="notice_c_source_fence_corrupt"):
                cached_notice_cover_snapshot(NS(storage=db), source.document)
        assert read_current_notice_cover(db, independent.document) == independent
        assert cached_notice_cover_snapshot(NS(storage=db), independent.document).revision == independent.epoch
        assert db.entitlements() == [] and db.acquisitions() == []
