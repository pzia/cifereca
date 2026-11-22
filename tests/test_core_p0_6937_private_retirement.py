"""Isolated FS/SQLite recipe, never production consumer authority."""
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import zipfile

import pytest

from cifereca.core import derived_variant_journal as journal
from cifereca.core import derived_variant_private_retirement as retire
from cifereca.core.derived_variant_private_admission import reserve_private_derived_publication
from cifereca.core.derived_variant_private_recovery import (
    link_private_reserved_variant, recover_private_linked_derived_publication,
)
from cifereca.storage import Storage
from tests.test_core_p0_6937_private_admission import rig, _recover_args


def _epub(stage, *, empty=False, kepub=False, bad_span=False):
    with zipfile.ZipFile(stage, "w") as archive:
        archive.writestr("mimetype", b"application/epub+zip")
        archive.writestr("META-INF/container.xml",
                         '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                         '<rootfiles><rootfile full-path="OEBPS/book.opf"/></rootfiles></container>')
        package = '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
        if not empty:
            package += ('<manifest><item id="body" href="body.xhtml" media-type="application/xhtml+xml"/>'
                        '</manifest><spine><itemref idref="body"/></spine>')
        archive.writestr("OEBPS/book.opf", package + '</package>')
        body = '<p>Fixture</p>'
        if kepub:
            span_id = "invalid" if bad_span else "kobo.1.1"
            body = f'<p><span class="koboSpan" id="{span_id}">Fixture</span></p>'
        archive.writestr("OEBPS/body.xhtml",
                         '<html xmlns="http://www.w3.org/1999/xhtml"><body>' + body + '</body></html>')


def _published(rig, *, empty=False, media_format="epub", kepub=False, bad_span=False):
    db, intent, stage, args = rig
    if media_format == "kepub":
        new_stage = stage.with_name("unique.kepub.epub")
        stage.rename(new_stage)
        stage = new_stage
        intent = replace(intent, staged_path=str(stage),
                         candidate_path=str(Path(intent.candidate_path).with_name("variant.kepub.epub")))
    _epub(stage, empty=empty, kepub=kepub, bad_span=bad_span)
    stamp = stage.stat()
    intent = replace(intent, output_sha256=hashlib.sha256(stage.read_bytes()).hexdigest(),
                     output_size=stamp.st_size, media_format=media_format,
                     output_stamp=(stamp.st_dev, stamp.st_ino, stamp.st_size, stamp.st_mtime_ns))
    args = dict(args, intent=intent)
    receipt = reserve_private_derived_publication(db, **args)
    options = _recover_args(args, receipt)
    assert link_private_reserved_variant(db, **options)["status"] == "linked_pending"
    assert recover_private_linked_derived_publication(db, **options)["status"] == "committed"
    options.pop("reservation")
    options["receipt_sha256"] = options.pop("approved_receipt_sha256")
    return db, intent, stage, options


@pytest.fixture
def isolated_fence(monkeypatch, tmp_path):
    # An intentionally synthetic proof boundary. The REAL gate always
    # refuses; no caller argument or public interface can override it.
    monkeypatch.setattr(retire, "_exclusive_consumer_fence_current",
                        lambda storage, root, identity, lock:
                        root == tmp_path and identity == "id1" and Path(storage.path).is_relative_to(tmp_path))


def _prepare(db, options):
    saved = retire.prepare_private_stage_retirement(db, **options)
    assert saved["status"] == "prepared" and not saved["quota_refund_authorized"]
    return dict(options, approved_intent_sha256=saved["intent_sha256"])


def _unchanged(db, intent, quota, committed, content):
    assert journal.summarize_derived_journal(db) == quota
    assert db.metadata_get(journal._COMMITTED + committed) is not None
    assert len(db.placements()) == 2 and len(db.artifact_variants()) == 1
    assert Path(intent.parent_path).read_bytes() == b"book-data"
    assert Path(intent.candidate_path).read_bytes() == content
    with db._lock, db._connect() as sql:
        assert sql.execute("SELECT count(*) FROM origin_links WHERE origin_kind='derived'").fetchone() == (1,)


def test_production_gate_never_arms(rig):
    db, intent, stage, options = _published(rig)
    key = retire._PREFIX + options["receipt_sha256"]
    with pytest.raises(journal.DerivedJournalConflict, match="consumer_fence_unavailable"):
        retire.prepare_private_stage_retirement(db, **options)
    with pytest.raises(journal.DerivedJournalConflict, match="consumer_fence_unavailable"):
        retire.retire_private_stage(db, **options, approved_intent_sha256="0" * 64)
    assert db.metadata_get(key) is None and stage.samefile(intent.candidate_path)


def test_prepare_restart_retire_keeps_all_canonical_evidence(rig, isolated_fence):
    db, intent, stage, options = _published(rig)
    quota = journal.summarize_derived_journal(db)
    digest = options["receipt_sha256"]
    key = retire._PREFIX + digest
    data = stage.read_bytes()
    approved = _prepare(db, options)
    original_trace = db.metadata_get(key)
    assert journal._receipt_digest(original_trace) == approved["approved_intent_sha256"]
    assert stage.samefile(intent.candidate_path)
    reopened = Storage(db.path)
    try:
        assert reopened.metadata_get(key) == original_trace
        result = retire.retire_private_stage(reopened, **approved)
        assert result == {"status": "retired", "outcome": "unlink_confirmed",
                          "quota_refund_authorized": False, "destructive_cleanup_allowed": False}
        assert not stage.exists()
        assert Path(intent.candidate_path).stat().st_nlink == 1
        trace = retire._decode_trace(key, reopened.metadata_get(key))
        assert trace["state"] == "retired" and trace["outcome"] == "unlink_confirmed"
        assert trace["after"][:4] == trace["before"][:4]
        _unchanged(reopened, intent, quota, digest, data)
        with pytest.raises(journal.DerivedJournalConflict):
            retire.retire_private_stage(reopened, **approved)
    finally:
        reopened.close()


@pytest.mark.parametrize("fault", ["after_unlink", "sql_commit"])
def test_crash_after_unlink_resumes_from_durable_prepared_trace(rig, isolated_fence, monkeypatch, fault):
    db, intent, stage, options = _published(rig)
    digest = options["receipt_sha256"]
    key = retire._PREFIX + digest
    data = stage.read_bytes()
    quota = journal.summarize_derived_journal(db)
    approved = _prepare(db, options)
    before = db.metadata_get(key)
    if fault == "after_unlink":
        unlink = os.unlink
        def fail_after(name, *args, **kwargs):
            unlink(name, *args, **kwargs)
            raise OSError("synthetic interruption after exact unlink")
        with monkeypatch.context() as patch:
            patch.setattr(os, "unlink", fail_after)
            with pytest.raises(journal.DerivedJournalConflict):
                retire.retire_private_stage(db, **approved)
    else:
        with db._lock, db._connect() as sql:
            sql.execute("CREATE TRIGGER synthetic_retire_abort BEFORE UPDATE OF value ON metadata "
                        "WHEN NEW.key LIKE 'derived.publication.retirement.v1:%' "
                        "BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        # The Core boundary translates the real SQLite trigger abort.
        with pytest.raises(journal.DerivedJournalConflict):
            retire.retire_private_stage(db, **approved)
        with db._lock, db._connect() as sql:
            sql.execute("DROP TRIGGER synthetic_retire_abort")
    assert not stage.exists() and db.metadata_get(key) == before
    _unchanged(db, intent, quota, digest, data)
    reopened = Storage(db.path)
    try:
        with monkeypatch.context() as patch:
            def never_unlink(*args, **kwargs):
                raise AssertionError("absence reconciliation must not unlink")
            patch.setattr(os, "unlink", never_unlink)
            result = retire.retire_private_stage(reopened, **approved)
        assert result["outcome"] == "absence_reconciled"
        assert retire._decode_trace(key, reopened.metadata_get(key))["state"] == "retired"
        _unchanged(reopened, intent, quota, digest, data)
    finally:
        reopened.close()


@pytest.mark.parametrize("case", ["wrong_approval", "replacement", "third_link", "sql_reference",
                                  "symlink", "scope", "binding", "final_missing", "fence_revoked"])
def test_late_changes_preserve_prepared_trace_and_surviving_names(rig, isolated_fence, monkeypatch, case):
    db, intent, stage, options = _published(rig)
    approved = _prepare(db, options)
    digest = options["receipt_sha256"]
    key = retire._PREFIX + digest
    before = db.metadata_get(key)
    target = Path(intent.candidate_path)
    if case == "wrong_approval":
        approved["approved_intent_sha256"] = "0" * 64
    elif case == "replacement":
        stage.unlink()
        stage.write_bytes(b"unrelated")
    elif case == "third_link":
        os.link(stage, stage.with_name("other.epub"))
    elif case == "sql_reference":
        db.metadata_set("synthetic.consumer", str(stage))
    elif case == "symlink":
        (target.parent / "consumer").symlink_to(stage)
    elif case == "scope":
        approved["scope_current"] = lambda sql: False
    elif case == "binding":
        approved["approved_binding_sha256"] = "0" * 64
    elif case == "final_missing":
        target.unlink()
    else:
        monkeypatch.setattr(retire, "_exclusive_consumer_fence_current", lambda *args: False)
    stage_data = stage.read_bytes()
    target_data = target.read_bytes() if target.exists() else None
    with pytest.raises(journal.DerivedJournalConflict):
        retire.retire_private_stage(db, **approved)
    assert db.metadata_get(key) == before
    assert stage.read_bytes() == stage_data
    assert (target.read_bytes() if target.exists() else None) == target_data


@pytest.mark.parametrize("case", ["empty_package", "pdf", "nested", "budget"])
def test_preparation_refuses_unverified_content_or_transaction(rig, isolated_fence, monkeypatch, case):
    db, intent, stage, options = _published(rig, empty=case == "empty_package",
                                          media_format="pdf" if case == "pdf" else "epub")
    sql = db._connect()
    if case == "nested":
        sql.execute("BEGIN IMMEDIATE")
        sql.execute("INSERT INTO metadata(key,value) VALUES('test.retire.caller','pending')")
    elif case == "budget":
        monkeypatch.setattr(retire, "_MAX_RECORDS", 0)
    try:
        with pytest.raises(journal.DerivedJournalConflict):
            retire.prepare_private_stage_retirement(db, **options)
        if case == "nested":
            assert sql.in_transaction
            assert sql.execute("SELECT value FROM metadata WHERE key='test.retire.caller'").fetchone() == ("pending",)
            sql.rollback()
        assert db.metadata_get(retire._PREFIX + options["receipt_sha256"]) is None
        assert stage.samefile(intent.candidate_path)
    finally:
        if sql.in_transaction:
            sql.rollback()


@pytest.mark.parametrize("case", ["valid", "suffix_only", "bad_span", "no_fence"])
def test_isolated_kepub_retirement_requires_text_spine_and_keeps_authority(
        rig, isolated_fence, monkeypatch, case):
    db, intent, stage, options = _published(
        rig, media_format="kepub", kepub=case != "suffix_only",
        bad_span=case == "bad_span")
    quota = journal.summarize_derived_journal(db)
    data = stage.read_bytes()
    digest = options["receipt_sha256"]
    key = retire._PREFIX + digest
    if case == "no_fence":
        monkeypatch.setattr(retire, "_exclusive_consumer_fence_current", lambda *_args: False)
    if case != "valid":
        with pytest.raises(journal.DerivedJournalConflict, match=(
                "consumer_fence_unavailable" if case == "no_fence" else "reader_unverified")):
            retire.prepare_private_stage_retirement(db, **options)
        assert db.metadata_get(key) is None and stage.samefile(intent.candidate_path)
        _unchanged(db, intent, quota, digest, data)
        return
    approved = _prepare(db, options)
    other = Storage(db.path)
    try:
        result = retire.retire_private_stage(other, **approved)
        assert result["status"] == "retired" and result["quota_refund_authorized"] is False
        assert not stage.exists()
        _unchanged(other, intent, quota, digest, data)
    finally:
        other.close()


@pytest.mark.parametrize("case", ["revoked", "deadline", "invalid_crc"])
def test_commit_reader_proof_is_revocable_and_never_unlinks_on_refusal(
        rig, isolated_fence, monkeypatch, case):
    db, intent, stage, options = _published(rig)
    approved = _prepare(db, options)
    digest = options["receipt_sha256"]
    trace = db.metadata_get(retire._PREFIX + digest)
    quota, data = journal.summarize_derived_journal(db), stage.read_bytes()
    from cifereca.core import derived_variant_retirement_preparation as preparation
    def inspect(stream, kind, **kwargs):
        if case == "revoked":
            monkeypatch.setattr(retire, "_exclusive_consumer_fence_current", lambda *args: False)
            assert kwargs["cancelled"]()
        if case == "deadline":
            monkeypatch.setattr(preparation, "monotonic", lambda: float("inf"))
            assert kwargs["cancelled"]()
        return "structure_invalid" if case == "invalid_crc" else "epub_structure_crc_ok"
    monkeypatch.setattr(preparation, "inspect_stream_structure", inspect)
    with pytest.raises(journal.DerivedJournalConflict):
        retire.retire_private_stage(db, **approved)
    assert db.metadata_get(retire._PREFIX + digest) == trace
    assert stage.samefile(intent.candidate_path)
    _unchanged(db, intent, quota, digest, data)
