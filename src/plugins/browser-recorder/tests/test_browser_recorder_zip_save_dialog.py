"""Save As is a real explicit pathname decision, never silent overwriting.

No real users/sites. Only fictional synthetic ZIP bytes in temp directories.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from browser_recorder import RecorderError
from browser_recorder_zip import ReviewedBundlePlan


def plan():
    return ReviewedBundlePlan(
        b"FAKE_ZIP_BYTES_NO_SOURCE_SECRETS",
        {"manifest.json": b'{"schema":"fictional"}'},
    )


def test_save_as_rejects_no_preview_or_no_consent(tmp_path):
    output = tmp_path / "fictional-reviewed.zip"
    p = plan()
    with pytest.raises(RecorderError, match="export_unconfirmed"):
        p.save_as(output, user_confirmed=True)
    p.preview()
    with pytest.raises(RecorderError, match="export_unconfirmed"):
        p.save_as(output)
    assert not output.exists()


def test_native_chosen_name_exact_file_with_no_overwrite(tmp_path):
    p = plan()
    p.preview()
    file = tmp_path / "Example-reviewed.zip"
    assert p.save_as(file, user_confirmed=True) == file.name
    assert file.read_bytes() == b"FAKE_ZIP_BYTES_NO_SOURCE_SECRETS"
    assert file.stat().st_mode & 0o777 == 0o600
    with pytest.raises(RecorderError, match="save_failed"):
        p.save_as(file, user_confirmed=True)
    assert file.read_bytes() == b"FAKE_ZIP_BYTES_NO_SOURCE_SECRETS"
    assert list(tmp_path.iterdir()) == [file]


@pytest.mark.parametrize("unsafe", [
    "../relative.zip",
    "unsafe.txt",
    ".hidden.zip",
    "bad name.zip",
    "a" * 103 + ".zip",
])
def test_unsafe_filename_or_non_absolute_destination_never_writes(tmp_path, unsafe):
    p = plan()
    p.preview()
    target = Path(unsafe) if unsafe == "../relative.zip" else tmp_path / unsafe
    with pytest.raises(RecorderError, match="bundle_invalid_filename"):
        p.save_as(target, user_confirmed=True)
    assert list(tmp_path.iterdir()) == []


def test_symlink_target_and_folder_rejected(tmp_path):
    p = plan()
    p.preview()
    original = tmp_path / "existing.zip"
    original.write_bytes(b"PRIVATE_ORIGINAL_NOT_OVERWRITTEN")
    link = tmp_path / "safe.zip"
    link.symlink_to(original)
    with pytest.raises(RecorderError, match="save_failed"):
        p.save_as(link, user_confirmed=True)
    assert original.read_bytes() == b"PRIVATE_ORIGINAL_NOT_OVERWRITTEN"
    folder = tmp_path / "folder"
    folder.mkdir(mode=0o700)
    alias = tmp_path / "linked-dir"
    alias.symlink_to(folder, target_is_directory=True)
    with pytest.raises(RecorderError, match="bundle_invalid_filename"):
        p.save_as(alias / "normal.zip", user_confirmed=True)
    assert list(folder.iterdir()) == []


def test_nonexistent_folder_refused_without_creating_it(tmp_path):
    p = plan()
    p.preview()
    dst = tmp_path / "not-created" / "review.zip"
    with pytest.raises(RecorderError, match="save_failed"):
        p.save_as(dst, user_confirmed=True)
    assert not dst.parent.exists()


def test_cancelled_chooser_does_not_consume_or_export(tmp_path, monkeypatch):
    from test_browser_recorder_review_ui import make_panel, _capture_and_decide

    panel, tasks, raw = make_panel(
        monkeypatch, export_directory=tmp_path / "old-fixed-path",
    )
    _capture_and_decide(panel, tasks)
    panel.prepare_zip()
    panel.export_confirm.set_active(True)
    calls = []
    panel._prompt_export_file = lambda callback: calls.append(callback)
    panel.export_zip()
    assert len(calls) == 1
    assert not list(tmp_path.iterdir())  # chooser is open, no ZIP created
    # The UI can invalidate the plan while native chooser remains visible.
    panel._invalidate_bundle()
    calls[0](tmp_path / "never-written.zip")
    assert not list(tmp_path.iterdir())
    panel.destroy()
    assert raw.exchanges == []
