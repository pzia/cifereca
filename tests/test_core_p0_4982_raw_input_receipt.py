"""Raw input receipts are not parsed metadata, a body cache, or authority."""
from hashlib import sha256

import pytest

from cifereca.core.notice_c_raw_input_receipt import (
    _NAMESPACE, record_public_raw_input_receipt, read_public_raw_input_receipt,
    check_public_raw_input_txn,
)
from cifereca.core.notice_c_cover_index import (
    persist_exact_public_search_cover, read_consolidated_notice_cover,
)
from cifereca.core.notice_cover_repository import evict_notice_cover, persist_notice_cover
from cifereca.storage import Storage
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist, verified_public
from tests.test_core_p1_2385_notice_cover_persistence import PRIVATE, notice

BODY = b"<html><article>synthetic original supplier body</article></html>"
ALT = b"<html><article>another original supplier body</article></html>"


def digest(value):
    return sha256(value.encode("ascii")).hexdigest()


def record(db, source, body=BODY, *, now=100, ttl=60, **changes):
    return record_public_raw_input_receipt(
        db, source, body, now=now, ttl_seconds=ttl,
        **{"transport_digest": digest("exact-authorized-transport"),
           "parser_family_digest": digest("supplier-parser-family"),
           "parser_implementation_digest": digest("supplier-parser-v1"),
           **changes},
    )


def check(db, source, receipt, body=BODY, *, now=100):
    with db._lock, db._connect() as sql:
        sql.execute("BEGIN IMMEDIATE")
        return check_public_raw_input_txn(sql, source.document, receipt, body, now=now)


def public(db, title="Un livre", at=10):
    return persist(db, verified_public(record_id="same-edition", title=title), at=at)


def test_receipt_survives_restart_but_never_copies_body_or_confers_input_availability(tmp_path):
    path = tmp_path / "input.sqlite"
    db = Storage(path)
    try:
        source = public(db)
        canonical = persist_exact_public_search_cover(db, source)
        receipt = record(db, source)
        assert receipt.input_digest == sha256(BODY).hexdigest()
        assert record(db, source, now=101, ttl=100) == receipt
        assert check(db, source, receipt, now=102) == source
        with pytest.raises(ValueError, match="bytes_changed"):
            check(db, source, receipt, ALT)
        with db._lock, db._connect() as sql:
            key, raw, stored, expires = sql.execute(
                "SELECT cache_key,payload,stored_at,expires_at FROM cache WHERE namespace=?",
                (_NAMESPACE,),
            ).fetchone()
        assert BODY.decode() not in raw and source.observation.raw_json not in raw
        assert "same-edition" not in raw and "https://" not in raw
        assert (stored, expires) == (100, 160)
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()
    db = Storage(path)
    try:
        assert read_public_raw_input_receipt(db, source.document, now=159) == receipt
        assert check(db, source, receipt, now=159) == source
        assert read_public_raw_input_receipt(db, source.document, now=160) is None
        with pytest.raises(ValueError, match="receipt_changed"):
            check(db, source, receipt, now=160)
        assert read_consolidated_notice_cover(db, canonical.document) == canonical
    finally:
        db.close()


def test_receipt_aba_and_purge_fence_old_work_even_for_equal_bytes(tmp_path):
    db = Storage(tmp_path / "aba.sqlite")
    try:
        source = public(db)
        a = record(db, source)
        b = record(db, source, ALT)
        again = record(db, source)
        assert a.input_digest == again.input_digest != b.input_digest
        assert len({a.generation_token,b.generation_token,again.generation_token}) == 3
        with pytest.raises(ValueError, match="receipt_changed"):
            check(db, source, a)
        assert check(db, source, again) == source
        db.cache_delete_namespace(_NAMESPACE)
        new = record(db, source)
        assert new != again
        with pytest.raises(ValueError, match="receipt_changed"):
            check(db, source, again)
        assert record(db, source, now=101, ttl=1) == new
        assert read_public_raw_input_receipt(db, source.document, now=102) is None
    finally:
        db.close()


def test_source_revision_watermark_and_eviction_invalidate_receipt(tmp_path):
    db = Storage(tmp_path / "source.sqlite")
    try:
        source = public(db)
        canonical = persist_exact_public_search_cover(db, source)
        old = record(db, source)
        refreshed = public(db, at=11)
        assert refreshed == source
        # Same documentary epoch; a different actual source observation
        # watermark is not authority to reuse the earlier raw receipt.
        assert read_public_raw_input_receipt(db, source.document, now=102) is None
        with pytest.raises(ValueError, match="source_changed"):
            check(db, source, old, now=102)
        new = record(db, refreshed, now=102)
        changed = public(db, title="Autre titre", at=12)
        with pytest.raises(ValueError, match="source_changed"):
            check(db, source, new, now=103)
        assert record(db, changed, now=103) != new
        assert evict_notice_cover(db, changed.document, expected_epoch=changed.epoch)
        assert read_public_raw_input_receipt(db, changed.document, now=104) is None
        assert read_consolidated_notice_cover(db, canonical.document) == canonical
    finally:
        db.close()


def test_refuse_projection_private_invalid_bytes_and_receipt_corruption(tmp_path):
    db = Storage(tmp_path / "invalid.sqlite")
    try:
        source = public(db)
        with pytest.raises(ValueError, match="projected_only"):
            record(db, source, source.observation.raw_json.encode())
        for body in (b"", bytearray(BODY), b"x" * (2 * 1024 * 1024 + 1)):
            with pytest.raises(ValueError, match="invalid_bytes"):
                record(db, source, body)
        private = persist_notice_cover(
            db, notice(key=PRIVATE), field_path=("media", "cover"),
            cover_inventory_complete=True,
        )
        with pytest.raises(ValueError, match="public_scope_required"):
            record(db, private)
        with pytest.raises(ValueError, match="invalid_receipt"):
            record(db, source, transport_digest="https://private.invalid/token")
        receipt = record(db, source)
        with db._lock, db._connect() as sql:
            with pytest.raises(ValueError, match="transaction_required"):
                check_public_raw_input_txn(sql, source.document, receipt, BODY, now=100)
            sql.execute("UPDATE cache SET payload='{}' WHERE namespace=?", (_NAMESPACE,))
        with pytest.raises(ValueError, match="corrupt_receipt"):
            read_public_raw_input_receipt(db, source.document, now=100)
    finally:
        db.close()
