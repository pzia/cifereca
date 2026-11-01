"""#4093: unusable Downloads must fail BEFORE GUI, not after a session.

All tests use private temporary directories, with no external provider/GTK.
"""
from __future__ import annotations

import errno
import os
import sys

import pytest

from cifereca import app
from cifereca.core import capture_session_export


def test_probe_verifies_actual_write_and_leaves_no_file(tmp_path):
    folder = tmp_path / "Téléchargements avec espaces"
    folder.mkdir()
    assert capture_session_export.preflight_capture_destination(folder) == folder
    assert list(folder.iterdir()) == []


def test_unwritable_destination_is_rejected_before_gui_or_trace(
    monkeypatch, tmp_path, capsys,
):
    original = capture_session_export.tempfile.mkstemp
    probe_calls = []

    def deny_probe(*args, **kwargs):
        probe_calls.append(kwargs.get("prefix"))
        if kwargs.get("prefix") == ".cifereca-preflight-":
            raise PermissionError(errno.EACCES, "PRIVATE_SYNTHETIC_ACL_ERROR")
        return original(*args, **kwargs)

    monkeypatch.setattr(capture_session_export.tempfile, "mkstemp", deny_probe)
    monkeypatch.setattr(sys, "argv", [
        "cifereca", "--capture", "--capture-dir", str(tmp_path),
    ])
    monkeypatch.setattr(
        app, "_run_application",
        lambda **kw: pytest.fail("GUI was opened despite a denied ZIP directory"),
    )

    assert app.main() == 2
    err = capsys.readouterr().err
    assert "capture_directory_unavailable" in err
    assert "PRIVATE_SYNTHETIC_ACL_ERROR" not in err
    assert "capture_zip:" not in err
    assert probe_calls == [".cifereca-preflight-"]
    assert not list(tmp_path.iterdir())


def test_failed_probe_write_does_not_leave_stale_tempfile(monkeypatch, tmp_path):
    original_write = os.write

    def refuse_data(fd, payload):
        if payload == b"0":
            return 0
        return original_write(fd, payload)

    monkeypatch.setattr(capture_session_export.os, "write", refuse_data)
    with pytest.raises(OSError, match="capture_preflight_write_failed"):
        capture_session_export.preflight_capture_destination(tmp_path)
    assert not list(tmp_path.iterdir())


def test_invalid_symlink_destination_rejected_before_write_probe(
    monkeypatch, tmp_path,
):
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    monkeypatch.setattr(
        capture_session_export.tempfile, "mkstemp",
        lambda **kw: pytest.fail("wrote through symlink destination"),
    )
    with pytest.raises(ValueError, match="capture_directory_unavailable"):
        capture_session_export.preflight_capture_destination(alias)
    assert not list(real.iterdir())
