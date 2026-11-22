"""#6937: actual FS/SQLite v3 preflight; no deletion even after restart."""
from dataclasses import replace
import hashlib
from pathlib import Path
import os
import zipfile

import pytest

from cifereca.core import derived_variant_journal as journal
from cifereca.core import derived_variant_retirement_preparation as prep
from cifereca.core.derived_variant_compact_shadow import write_compact_shadow
from cifereca.core.derived_variant_private_admission import reserve_private_derived_publication
from cifereca.core.derived_variant_private_recovery import (
    link_private_reserved_variant, recover_private_linked_derived_publication,
)
from cifereca.storage import Storage
from tests.test_core_p0_6937_private_admission import rig, _recover_args


def _epub(path, *, missing_spine=False):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml",
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>')
        archive.writestr("OPS/book.opf",
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
            '<metadata/><manifest><item id="page" href="page.xhtml" '
            'media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="page"/></spine></package>')
        if not missing_spine:
            archive.writestr("OPS/page.xhtml",
                '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Fixture</title>'
                '</head><body><p>Readable witness</p></body></html>')


def _committed(rig, *, missing_spine=False, compact=True):
    db, intent, stage, args = rig
    _epub(stage, missing_spine=missing_spine)
    info = stage.stat()
    intent = replace(intent, output_size=info.st_size,
                     output_sha256=hashlib.sha256(stage.read_bytes()).hexdigest(),
                     output_stamp=(info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns))
    args = dict(args, intent=intent)
    receipt = reserve_private_derived_publication(db, **args)
    options = _recover_args(args, receipt)
    link_private_reserved_variant(db, **options)
    recover_private_linked_derived_publication(db, **options)
    if compact:
        shadow_options = {k: v for k, v in options.items()
                          if k not in ("reservation", "approved_binding_sha256")}
        write_compact_shadow(db, receipt_sha256=receipt.receipt_sha256, **shadow_options)
    options.pop("reservation")
    options["receipt_sha256"] = receipt.receipt_sha256
    return db, intent, receipt, options


def test_checkpoint_is_durable_idempotent_and_never_authority(rig):
    db, intent, receipt, options = _committed(rig)
    original_key = journal._COMMITTED + receipt.receipt_sha256
    key = prep._PREFIX + receipt.receipt_sha256
    before = journal.summarize_derived_journal(db)
    sync = db._connect().execute("PRAGMA synchronous").fetchone()
    result = prep.prepare_private_retirement_review(db, **options)
    assert result["status"] == "blocked_external_consumers"
    assert result["checkpoint_durable"] and result["epub_structure_observed"]
    assert not any(result[k] for k in ("consumer_exclusivity_verified", "retention_authorized",
                                      "quota_refund_authorized", "destructive_cleanup_allowed"))
    assert db._connect().execute("PRAGMA synchronous").fetchone() == sync
    record = db.metadata_get(key)
    assert str(options["library_root"]) not in record
    assert receipt.receipt_sha256 not in repr(result)
    other = Storage(db.path)
    try:
        assert other.metadata_get(key) == record
        assert prep.prepare_private_retirement_review(other, **options) == result
        assert other.metadata_get(key) == record
        assert journal.summarize_derived_journal(other) == before
        assert other.metadata_get(original_key) is not None
        assert Path(intent.staged_path).samefile(intent.candidate_path)
        assert len(other.placements()) == 2 and len(other.artifact_variants()) == 1
    finally:
        other.close()


@pytest.mark.parametrize("case", ["bad_spine", "missing_compact", "sql_consumer",
                                  "symlink", "extra_link", "bad_binding", "unconfirmed",
                                  "scope", "nested", "late_fault"])
def test_preparation_refusal_preserves_pair_and_receipts(rig, monkeypatch, case):
    db, intent, receipt, options = _committed(
        rig, missing_spine=case == "bad_spine", compact=case != "missing_compact")
    stage, final = Path(intent.staged_path), Path(intent.candidate_path)
    key = prep._PREFIX + receipt.receipt_sha256
    sql = db._connect()
    if case == "sql_consumer":
        db.metadata_set("synthetic.stage.consumer", str(stage))
    elif case == "symlink":
        (final.parent / "alias").symlink_to(stage)
    elif case == "extra_link":
        os.link(stage, stage.with_name("other.epub"))
    elif case == "bad_binding":
        options["approved_binding_sha256"] = "0" * 64
    elif case == "unconfirmed":
        options["explicitly_requested"] = False
    elif case == "scope":
        options["scope_current"] = lambda _db: False
    elif case == "nested":
        sql.execute("BEGIN IMMEDIATE")
        sql.execute("INSERT INTO metadata(key,value) VALUES('test.retirement.caller','pending')")
    elif case == "late_fault":
        with db._lock, db._connect() as tx:
            tx.execute("CREATE TRIGGER synthetic_retirement_abort BEFORE INSERT ON metadata "
                       "WHEN NEW.key LIKE 'derived.retention.prepared.v1:%' "
                       "BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    before = stage.read_bytes()
    try:
        with pytest.raises(journal.DerivedJournalConflict):
            prep.prepare_private_retirement_review(db, **options)
        if case == "nested":
            assert sql.in_transaction
            assert sql.execute("SELECT value FROM metadata WHERE key='test.retirement.caller'").fetchone() == ("pending",)
            sql.rollback()
        assert db.metadata_get(key) is None
        assert db.metadata_get(journal._COMMITTED + receipt.receipt_sha256) is not None
        assert stage.samefile(final) and final.read_bytes() == before
        assert len(db.placements()) == 2 and len(db.artifact_variants()) == 1
    finally:
        if sql.in_transaction:
            sql.rollback()


def test_changed_consumer_never_reuses_prepared_checkpoint(rig):
    db, intent, receipt, options = _committed(rig)
    prep.prepare_private_retirement_review(db, **options)
    key = prep._PREFIX + receipt.receipt_sha256
    before = db.metadata_get(key)
    db.metadata_set("later.consumer", intent.staged_path)
    with pytest.raises(journal.DerivedJournalConflict, match="dependency_unverified"):
        prep.prepare_private_retirement_review(db, **options)
    assert db.metadata_get(key) == before
    assert Path(intent.staged_path).samefile(intent.candidate_path)


@pytest.mark.parametrize("case", ["deadline", "cancelled", "crc_refused"])
def test_retirement_reader_budget_or_scope_refusal_never_persists_checkpoint(
        rig, monkeypatch, case):
    db, intent, receipt, options = _committed(rig)
    stage, final = Path(intent.staged_path), Path(intent.candidate_path)
    key = prep._PREFIX + receipt.receipt_sha256
    before = journal.summarize_derived_journal(db)
    data = final.read_bytes()
    calls = []

    def bounded(stream, media_format, **kwargs):
        calls.append((media_format, kwargs["max_bytes"], kwargs["seconds"]))
        assert media_format == "epub"
        assert 0 < kwargs["max_bytes"] < prep._MAX_BYTES
        assert 0 < kwargs["seconds"] <= prep._SECONDS
        if case == "deadline":
            monkeypatch.setattr(prep, "monotonic", lambda: float("inf"))
            assert kwargs["cancelled"]()
            return "structure_cancelled"
        if case == "cancelled":
            live[0] = False
            assert kwargs["cancelled"]()
            return "structure_cancelled"
        return "epub_structure_invalid"

    live = [True]
    options["scope_current"] = lambda _db: live[0]
    monkeypatch.setattr(prep, "inspect_stream_structure", bounded)
    with pytest.raises(journal.DerivedJournalConflict, match="reader_unverified"):
        prep.prepare_private_retirement_review(db, **options)
    assert len(calls) == 1 and db.metadata_get(key) is None
    assert journal.summarize_derived_journal(db) == before
    assert stage.samefile(final) and final.read_bytes() == data
    assert len(db.placements()) == 2 and len(db.artifact_variants()) == 1


@pytest.mark.parametrize("references", [2, 513])
def test_spine_references_cannot_multiply_reader_budget(tmp_path, monkeypatch, references):
    path = tmp_path / "reader.epub"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml",
                         '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                         '<rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>')
        archive.writestr("OPS/book.opf",
                         '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
                         '<item id="p" href="p.xhtml" media-type="application/xhtml+xml"/>'
                         '</manifest><spine>' + '<itemref idref="p"/>' * references +
                         '</spine></package>')
        archive.writestr("OPS/p.xhtml",
                         '<html xmlns="http://www.w3.org/1999/xhtml"><body>Fixture</body></html>')
    size = path.stat().st_size
    with zipfile.ZipFile(path) as archive:
        expected = prep._MAX_BYTES - size - sum(
            archive.getinfo(name).file_size for name in
            ("META-INF/container.xml", "OPS/book.opf", "OPS/p.xhtml"))
    seen = []
    def inspect(stream, kind, **options):
        seen.append(options["max_bytes"])
        assert kind == "epub" and options["max_bytes"] == expected
        return "epub_structure_crc_ok"
    monkeypatch.setattr(prep, "inspect_stream_structure", inspect)
    with path.open("rb") as stream:
        if references > prep._MAX_SPINE_REFERENCES:
            with pytest.raises(journal.DerivedJournalConflict, match="reader_unverified"):
                prep._inspect_epub_final(stream.fileno(), size)
            assert not seen
        else:
            prep._inspect_epub_final(stream.fileno(), size)
            assert seen == [expected]
