"""#3132/#3133: file and console mirrors retain raw diagnostics, private permissions."""
from __future__ import annotations

import logging
import os
import stat

from cifereca import logging_utils


def make_record(msg, args=()):
    return logging.LogRecord("cifereca", logging.WARNING, __file__, 1, msg, args, None)


def test_raw_log_preserves_formatted_values_and_does_not_mutate_record(tmp_path, monkeypatch):
    monkeypatch.setattr(logging_utils, "_diagnostic_level", "normal")
    source = 'https://reader.invalid/LOAN/123?token=visible'
    body = f"login=reader@example.invalid password=visible cookie=other {source}"
    record = make_record("%s", (body,))
    before = (record.msg, record.args)
    path = tmp_path / "journal.log"
    handler = logging_utils.PrivateRotatingFileHandler(path, maxBytes=1000, backupCount=1)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    handler.addFilter(logging_utils.RedactionFilter())
    try:
        handler.handle(record)
        assert (record.msg, record.args) == before
        assert handler.filters[0].filter(record)
        assert record.getMessage() == body
    finally:
        handler.close()
    assert body in path.read_text(encoding="utf-8")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_existing_world_readable_log_is_tightened(tmp_path):
    path = tmp_path / "journal.log"
    path.write_text("old=raw\n", encoding="utf-8")
    os.chmod(path, 0o644)
    handler = logging_utils.PrivateRotatingFileHandler(path, maxBytes=1000, backupCount=1)
    handler.close()
    assert path.read_text(encoding="utf-8") == "old=raw\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_rotated_files_stay_private_and_keep_raw_values(tmp_path, monkeypatch):
    monkeypatch.setattr(logging_utils, "_diagnostic_level", "normal")
    path = tmp_path / "journal.log"
    handler = logging_utils.PrivateRotatingFileHandler(path, maxBytes=90, backupCount=2)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.addFilter(logging_utils.RedactionFilter())
    try:
        handler.handle(make_record("password=FIRST " + "A" * 40))
        handler.handle(make_record("token=SECOND " + "B" * 40))
        handler.handle(make_record("login=THIRD " + "C" * 40))
    finally:
        handler.close()
    files = sorted(tmp_path.glob("journal.log*"))
    assert len(files) == 3
    assert all(stat.S_IMODE(item.stat().st_mode) == 0o600 for item in files)
    merged = "\n".join(item.read_text(encoding="utf-8") for item in files)
    assert "password=FIRST" in merged
    assert "token=SECOND" in merged
    assert "login=THIRD" in merged


def test_errors_level_never_silences_a_genuine_generic_failure():
    from cifereca.diagnostics import Diagnostics

    diagnostic = Diagnostics(level="errors")
    diagnostic.add("SYNC", "connexion", "ok", level="normal")
    diagnostic.add("SYNC", "téléchargement", "failed", level="normal")
    diagnostic.add("LOAN", "ACSM", "error", level="trace")
    assert [(row["operation"], row["status"]) for row in diagnostic.snapshot()] == [
        ("téléchargement", "failed"), ("ACSM", "error"),
    ]


def test_legacy_rotations_are_tightened_without_rewriting_raw_contents(tmp_path):
    """Private raw retention covers backups from older Cifereca versions."""
    path = tmp_path / "cifereca.log"
    path.write_text("login=visible password=visible\n", encoding="utf-8")
    backups = []
    for index in range(1, 4):
        previous = tmp_path / f"cifereca.log.{index}"
        previous.write_text(f"URL https://example.invalid/{index}?token=raw\n",
                            encoding="utf-8")
        os.chmod(previous, 0o644)
        backups.append(previous)
    handler = logging_utils.PrivateRotatingFileHandler(
        path, maxBytes=1_000_000, backupCount=3, encoding="utf-8",
    )
    handler.close()

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert all(stat.S_IMODE(item.stat().st_mode) == 0o600 for item in backups)
    assert "password=visible" in path.read_text()
    assert all("token=raw" in item.read_text() for item in backups)


def test_legacy_rotation_symlink_does_not_change_external_target(tmp_path):
    """Historical backups must never chmod other users' symlink targets."""
    if not hasattr(os, "O_NOFOLLOW"):
        return
    outside = tmp_path / "external-existing.txt"
    outside.write_text("non-cifereca-content", encoding="utf-8")
    os.chmod(outside, 0o644)
    (tmp_path / "cifereca.log.1").symlink_to(outside)
    handler = logging_utils.PrivateRotatingFileHandler(
        tmp_path / "cifereca.log", maxBytes=1000, backupCount=1,
    )
    handler.close()
    assert outside.read_text() == "non-cifereca-content"
    assert stat.S_IMODE(outside.stat().st_mode) == 0o644


def test_active_log_symlink_is_rejected_instead_of_followed(tmp_path):
    """Never append password-bearing journal records to a symlink target."""
    import pytest

    if not hasattr(os, "O_NOFOLLOW"):
        return
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("untouched", encoding="utf-8")
    (tmp_path / "cifereca.log").symlink_to(outside)
    with pytest.raises(OSError):
        logging_utils.PrivateRotatingFileHandler(
            tmp_path / "cifereca.log", maxBytes=1000, backupCount=2,
        )
    assert outside.read_text() == "untouched"
