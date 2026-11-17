"""#2385/#4552: durable Notice C covers retain attested truth on partial fields."""
from __future__ import annotations

from cifereca.core.notice_source_observation import (
    NoticeSourceKey, admit_source_notice,
)
from cifereca.core.notice_cover_repository import (
    persist_notice_cover, read_notice_cover,
)
from cifereca.storage import Storage


KEY = NoticeSourceKey(
    provider_id="bnf", visibility="public", identity_id=None,
    credential_id=None, account_id=None,
    branch_id="catalogue", record_id="ark:retention",
    edition_id="digital:1", manifestation_role="digital",
)
A = "https://covers.example.org/attested-a.jpg"
B = "https://covers.example.org/attested-b.jpg"


def write(db, cover, at, *, status="complete", complete=True,
          path=("media", "cover")):
    source = admit_source_notice(
        key=KEY, parser_id="bnf-parser", parser_version="1",
        observed_at=at, status=status,
        raw_fields={"media": {"cover": cover}},
    )
    return persist_notice_cover(
        db, source, field_path=path,
        cover_inventory_complete=complete,
    )


def test_incomplete_field_inventory_retains_complete_cover_and_epoch(tmp_path):
    db = Storage(tmp_path / "c.sqlite")
    try:
        first = write(db, A, 10)
        assert first.epoch == 1
        incomplete = write(db, B, 11, complete=False)
        assert incomplete == first
        assert read_notice_cover(db, first.document) == first

        # Source parsing can be complete while an individual image field is
        # malformed. Neither failure is an explicit tombstone.
        invalid = write(db, "http://not-an-admitted-image.example/cover", 12)
        assert invalid == first
        assert read_notice_cover(db, first.document).attribute.candidates[0].url == A

        updated = write(db, B, 13)
        assert updated.epoch == 2
        assert updated.attribute.candidates[0].url == B
        assert read_notice_cover(db, first.document) == updated
    finally:
        db.close()


def test_explicit_missing_is_retained_against_incomplete_observation(tmp_path):
    db = Storage(tmp_path / "c.sqlite")
    try:
        missing = write(db, None, 10)
        assert missing.attribute.state == "missing"
        incomplete = write(db, B, 11, complete=False)
        assert incomplete == missing
        assert read_notice_cover(db, missing.document).attribute.state == "missing"
        observed = write(db, B, 12)
        assert observed.epoch == missing.epoch + 1
        assert observed.attribute.candidates[0].url == B
    finally:
        db.close()


def test_new_field_contract_requires_complete_inventory_before_invalidation(tmp_path):
    db = Storage(tmp_path / "c.sqlite")
    try:
        first = write(db, A, 10)
        incomplete = write(
            db, B, 11, path=("unmapped", "cover"), complete=False,
        )
        # Changing the parser's field mapping does not attest absence. N2
        # keeps its old image/revision until a complete new source arrives.
        assert incomplete == first
        assert read_notice_cover(db, first.document) == first
        attested_missing = write(
            db, B, 12, path=("unmapped", "cover"), complete=True,
        )
        assert attested_missing.epoch == first.epoch + 1
        assert attested_missing.attribute.state == "missing"
        assert attested_missing.attribute.candidates == ()
        assert read_notice_cover(db, first.document) == attested_missing
    finally:
        db.close()


def test_first_incomplete_observation_is_pending_not_attested_missing(tmp_path):
    db = Storage(tmp_path / "c.sqlite")
    try:
        first = write(db, None, 1, complete=False)
        assert first.attribute.state == "pending"
        assert first.attribute.candidates == ()
        complete = write(db, None, 2)
        assert complete.epoch == first.epoch + 1
        assert complete.attribute.state == "missing"
    finally:
        db.close()
