"""P0 #5069 B2.1: no symlink/hardlink or TOCTOU journal reads."""
from __future__ import annotations

import json
import os

import pytest

from cifereca import updater
from cifereca import updater_retention_inventory as inv


NOW = 1_000_000.0
ID = "20261108T101214Z-12345678abcd"


def _journal(tmp_path):
    root = tmp_path / "runtime"
    folder = root / "transactions" / ID
    folder.mkdir(parents=True)
    updater._write_json_atomic(folder / "transaction.json", {
        "schema": updater.TRANSACTION_SCHEMA,
        "id": ID,
        "state": "restoration_incomplete",
        "core": {"before_current": None, "before_previous": None, "after": None},
        "packages": {},
    })
    return root, folder, folder / "transaction.json"


def test_anchored_reader_accepts_only_regular_single_link_journal(tmp_path):
    root, folder, journal = _journal(tmp_path)
    assert inv._read_transaction_for_inspection(folder)["id"] == ID
    assert inv.inspect_core_transaction_history(root, now=NOW)["plan"]["total"] == 1
    os.link(journal, tmp_path / "journal-hardlink")
    with pytest.raises(ValueError, match="unsafe_journal"):
        inv._read_transaction_for_inspection(folder)
    result = inv.inspect_core_transaction_history(root, now=NOW)
    assert "transaction_unreadable" in result["blockers"]
    assert result["safe_to_apply"] is False


def test_anchored_reader_rejects_folder_and_journal_symlinks(tmp_path):
    root, folder, journal = _journal(tmp_path)
    outsider = tmp_path / "private"
    outsider.mkdir()
    (outsider / "transaction.json").write_text("PRIVATE", encoding="utf-8")
    journal.unlink()
    journal.symlink_to(outsider / "transaction.json")
    with pytest.raises(OSError):
        inv._read_transaction_for_inspection(folder)
    assert "unsafe_transaction_journal" in inv.inspect_core_transaction_history(root, now=NOW)["blockers"]
    folder.rename(folder.with_name("original"))
    folder.symlink_to(outsider, target_is_directory=True)
    with pytest.raises(OSError):
        inv._read_transaction_for_inspection(folder)
    report = inv.inspect_core_transaction_history(root, now=NOW)
    assert "unsafe_transaction_entry" in report["blockers"]
    assert "PRIVATE" not in json.dumps(report)


def test_symlink_swap_at_open_does_not_read_external_journal(tmp_path, monkeypatch):
    root, folder, journal = _journal(tmp_path)
    outside = tmp_path / "SECRET-EXTERNAL"
    outside.write_text('{"secret":"PRIVATE"}', encoding="utf-8")
    raw_open = os.open
    swapped = False

    def swap_then_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == "transaction.json" and not swapped:
            swapped = True
            journal.unlink()
            journal.symlink_to(outside)
        return raw_open(path, flags, *args, **kwargs)

    with monkeypatch.context() as mp:
        mp.setattr(inv.os, "open", swap_then_open)
        result = inv.inspect_core_transaction_history(root, now=NOW)
    assert swapped
    assert "transaction_unreadable" in result["blockers"]
    assert "PRIVATE" not in json.dumps(result)
    assert outside.read_text(encoding="utf-8") == '{"secret":"PRIVATE"}'


def test_oversized_journal_refused_before_read(tmp_path):
    root, folder, journal = _journal(tmp_path)
    with journal.open("ab") as stream:
        stream.truncate(32 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="unsafe_journal"):
        inv._read_transaction_for_inspection(folder)
    assert "transaction_unreadable" in inv.inspect_core_transaction_history(root, now=NOW)["blockers"]
