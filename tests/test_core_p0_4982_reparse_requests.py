"""#4982: exact raw input -> durable request; no parser or worker activation."""
import pytest

from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_reparse_requests import (
    request_public_reparse, read_public_reparse_request,
    check_public_reparse_inputs_txn,
)
from cifereca.core.notice_c_raw_input_receipt import _NAMESPACE
from cifereca.core.notice_cover_repository import read_notice_cover
from cifereca.storage import Storage
from tests.test_core_p0_4982_raw_input_receipt import BODY, ALT, digest, public, record

TOKEN = "a" * 32
NEXT_TOKEN = "b" * 32
PARSER = digest("supplier-parser-v2")
NEXT_PARSER = digest("supplier-parser-v3")


def request(db, source, receipt, *, body=BODY, now=100, generation=0, implementation=PARSER):
    return request_public_reparse(
        db, source, receipt, body, now=now, expected_generation=generation,
        implementation_digest=implementation,
    )


def check(db, spec, prepared, *, body=BODY, now=100, **changes):
    receipt = prepared.receipt
    with db._lock, db._connect() as sql:
        sql.execute("BEGIN IMMEDIATE")
        return check_public_reparse_inputs_txn(
            sql, spec, prepared, body, now=now,
            **{"producer_token": receipt.producer_token,
               "transport_digest": receipt.transport_digest,
               "parser_family_digest": receipt.parser_family_digest,
               "implementation_digest": prepared.implementation_digest,
               **changes},
        )


def test_request_deduplicates_and_survives_restart_without_serializing_locator(tmp_path):
    path = tmp_path / "work.sqlite"
    db = Storage(path)
    try:
        source = public(db)
        receipt = record(db, source, producer_token=TOKEN)
        ref = request(db, source, receipt)
        assert request(db, source, receipt) == ref
        job = journal.read_job(db, ref)
        assert job.state == "pending" and job.attempts == 0
        assert job.spec.kind == "reparse_cached_input"
        assert job.spec.target == source.document.source_key
        assert job.spec.implementation_digest == PARSER
        with db._lock, db._connect() as sql:
            value, = sql.execute("SELECT value FROM metadata WHERE key=?",
                                 (journal._PREFIX + ref.key,)).fetchone()
            assert TOKEN not in value and BODY.decode() not in value
        assert read_notice_cover(db, source.document) == source
    finally:
        db.close()
    db = Storage(path)
    try:
        spec = journal.read_job(db, ref).spec
        prepared = read_public_reparse_request(db, spec, now=101)
        assert prepared.source == source and prepared.receipt == receipt
        assert check(db, spec, prepared, now=101) == source
        for changes in (
            {"producer_token": NEXT_TOKEN},
            {"transport_digest": digest("different transport")},
            {"parser_family_digest": digest("different family")},
            {"implementation_digest": NEXT_PARSER},
        ):
            with pytest.raises(ValueError, match="implementation_or_transport_changed"):
                check(db, spec, prepared, **changes)
        with pytest.raises(ValueError, match="bytes_changed"):
            check(db, spec, prepared, body=ALT)
        # A surviving receipt is not itself proof of byte availability.
        with pytest.raises(ValueError, match="input_unavailable"):
            read_public_reparse_request(db, spec, now=160)
        assert journal.read_job(db, ref).attempts == 0
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_new_token_and_parser_version_fence_old_lease(tmp_path):
    db = Storage(tmp_path / "generations.sqlite")
    try:
        source = public(db)
        receipt = record(db, source, producer_token=TOKEN)
        ref = request(db, source, receipt)
        old_spec = journal.read_job(db, ref).spec
        prepared = read_public_reparse_request(db, old_spec, now=100)
        lease = journal.claim_job(db, ref, now=100, lease_seconds=30)
        assert lease is not None

        other = record(db, source, producer_token=NEXT_TOKEN)
        assert other != receipt and other.input_digest == receipt.input_digest
        with pytest.raises(ValueError, match="inputs_changed"):
            check(db, old_spec, prepared)
        with pytest.raises(ValueError, match="receipt_changed"):
            request(db, source, receipt, generation=ref.generation)
        with pytest.raises(ValueError, match="generation_changed"):
            request(db, source, other)
        new = request(db, source, other, generation=ref.generation)
        assert new.key == ref.key and new.generation == ref.generation + 1
        newer = request(db, source, other, generation=new.generation,
                        implementation=NEXT_PARSER)
        assert newer.generation == new.generation + 1
        with db._lock, db._connect() as sql:
            sql.execute("BEGIN IMMEDIATE")
            with pytest.raises(ValueError, match="generation_changed"):
                journal._complete_job_txn(sql, lease, now=101)
        assert journal.read_job(db, newer).attempts == 0
        assert read_notice_cover(db, source.document) == source
    finally:
        db.close()


def test_missing_token_expiry_source_change_and_atomic_request_rollback(tmp_path, monkeypatch):
    db = Storage(tmp_path / "refusals.sqlite")
    try:
        source = public(db)
        no_locator = record(db, source)
        with pytest.raises(ValueError, match="input_not_locatable"):
            request(db, source, no_locator)
        receipt = record(db, source, producer_token=TOKEN)
        captured = []
        original = journal._request_job_txn

        def crash(*args, **kwargs):
            captured.append(original(*args, **kwargs))
            raise RuntimeError("crash")

        with monkeypatch.context() as patch:
            patch.setattr(journal, "_request_job_txn", crash)
            with pytest.raises(RuntimeError, match="crash"):
                request(db, source, receipt)
        assert len(captured) == 1
        with pytest.raises(ValueError, match="generation_changed"):
            journal.read_job(db, captured[0])
        ref = request(db, source, receipt)
        spec = journal.read_job(db, ref).spec
        with pytest.raises(ValueError, match="input_unavailable"):
            read_public_reparse_request(db, spec, now=160)
        public(db, at=11)
        with pytest.raises(ValueError, match="source_changed"):
            read_public_reparse_request(db, spec, now=101)
        db.cache_delete_namespace(_NAMESPACE)
        with pytest.raises(ValueError, match="input_unavailable"):
            read_public_reparse_request(db, spec, now=101)
    finally:
        db.close()
