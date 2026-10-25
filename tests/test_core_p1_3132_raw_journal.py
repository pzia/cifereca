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
