"""#4520: staged bytes, durable intent, exclusive destination, no unlink."""
from dataclasses import replace
import os
from pathlib import Path

import pytest

from cifereca.core import derived_variant_journal as journal
from cifereca.core import derived_variant_link as link
from tests.test_core_p0_4520_derived_journal import _fixture


def _prepared(tmp_path):
    service, intent = _fixture(tmp_path)
    stage = Path(intent.staged_path)
    stage.parent.mkdir(parents=True)
    stage.write_bytes(b"new bytes")
    info = stage.stat()
    intent = replace(intent, output_stamp=(
        info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
    ))
    def stamp(path):
        value = path.stat()
        return (value.st_dev, value.st_ino)
    pins = {
        "source_directory_stamp": stamp(Path(intent.parent_path).parent),
        "staging_directory_stamp": stamp(stage.parent),
        "staging_root_stamp": stamp(stage.parent.parent),
    }
    return service.storage, intent, pins


def test_reservation_precedes_link_and_keeps_staged_bytes(tmp_path, monkeypatch):
    db, intent, pins = _prepared(tmp_path)
    events = []
    original_sync = os.fsync
    original_link = os.link

    def sync(fd):
        events.append("fsync")
        return original_sync(fd)

    def reserve(value):
        events.append("reserve")
        assert events.count("fsync") >= 4
        assert not Path(value.candidate_path).exists()
        return journal.reserve_derived_intent(db, value)

    def hardlink(*args, **kwargs):
        events.append("link")
        assert journal.summarize_derived_journal(db)["count"] == 1
        return original_link(*args, **kwargs)

    monkeypatch.setattr(os, "fsync", sync)
    monkeypatch.setattr(os, "link", hardlink)
    try:
        receipt = link.link_reserved_variant(
            intent, **pins, reserve=reserve, scope_current=lambda: True,
        )
        assert events.index("reserve") < events.index("link")
        assert "fsync" in events[events.index("link") + 1:]
        assert db.metadata_get(receipt) is not None
        assert Path(intent.staged_path).samefile(intent.candidate_path)
        assert Path(intent.parent_path).read_bytes() == b"book-data"
        assert len(db.placements()) == 1
    finally:
        db.close()


@pytest.mark.parametrize("failure", ["collision", "revocation", "substitute", "fsync"])
def test_interruption_retains_receipt_and_never_unlinks(tmp_path, monkeypatch, failure):
    db, intent, pins = _prepared(tmp_path)
    live = True
    stage = Path(intent.staged_path)
    candidate = Path(intent.candidate_path)
    original_sync = os.fsync
    reserved = []

    def reserve(value):
        nonlocal live
        key = journal.reserve_derived_intent(db, value)
        reserved.append(key)
        if failure == "collision":
            candidate.write_bytes(b"unrelated")
        elif failure == "revocation":
            live = False
        elif failure == "substitute":
            stage.rename(stage.with_name("prior.epub"))
            stage.write_bytes(b"new bytes")
        return key

    def sync(fd):
        if failure == "fsync" and reserved and candidate.exists():
            raise OSError("synthetic disk interruption")
        return original_sync(fd)

    monkeypatch.setattr(os, "fsync", sync)
    try:
        with pytest.raises(journal.DerivedJournalConflict):
            link.link_reserved_variant(intent, **pins, reserve=reserve,
                                       scope_current=lambda: live)
        assert len(reserved) == 1
        assert db.metadata_get(reserved[0]) is not None
        assert stage.read_bytes() == b"new bytes"
        if failure == "collision":
            assert candidate.read_bytes() == b"unrelated"
        elif failure == "fsync":
            assert candidate.samefile(stage)
        else:
            assert not candidate.exists()
        if failure == "substitute":
            assert stage.with_name("prior.epub").read_bytes() == b"new bytes"
        assert len(db.placements()) == 1
        assert not db.artifact_variants()
    finally:
        db.close()


def test_preexisting_entry_refuses_before_reservation(tmp_path):
    db, intent, pins = _prepared(tmp_path)
    target = Path(intent.candidate_path)
    target.symlink_to(tmp_path / "absent")
    try:
        with pytest.raises(journal.DerivedJournalConflict, match="filename_conflict"):
            link.link_reserved_variant(
                intent, **pins, reserve=lambda _: pytest.fail("must not reserve"),
                scope_current=lambda: True,
            )
        assert target.is_symlink()
        assert journal.summarize_derived_journal(db)["count"] == 0
    finally:
        db.close()
