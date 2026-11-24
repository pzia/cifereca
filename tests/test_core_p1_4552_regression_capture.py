import json
import zipfile

import pytest

from cifereca.core.notice_regression_capture import write_regression_capture


def test_capture_partial_idempotent_and_private(tmp_path, monkeypatch):
    monkeypatch.setattr("cifereca.core.notice_regression_capture.downloads_dir",
                        lambda: tmp_path)
    args = dict(diagnostic_id="a" * 64, generation=1,
                members={"diff.json": b'{"missing":["title"]}'})
    path, status = write_regression_capture(**args)
    assert path.parent == tmp_path and status == "partial"
    assert path.stat().st_mode & 0o077 == 0
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert "response.raw" in manifest["omitted"]
        assert archive.read("diff.json") == args["members"]["diff.json"]
    assert write_regression_capture(**args) == (path, status)
    assert list(tmp_path.iterdir()) == [path]
    with pytest.raises(ValueError, match="conflict"):
        write_regression_capture(**{**args, "members": {"diff.json": b"other"}})


def test_capture_refuses_traversal_and_oversized_input(tmp_path):
    for members in ({"../secret": b"x"}, {"response.raw": b"x" * (8*1024*1024+1)}):
        with pytest.raises(ValueError):
            write_regression_capture(diagnostic_id="b"*64, generation=1,
                                     members=members, directory=tmp_path)
    assert not list(tmp_path.iterdir())


def test_capture_refuses_symlinked_ancestor_on_write_and_recovery(tmp_path):
    from hashlib import sha256
    from cifereca.core.notice_regression_capture import recover_regression_capture

    actual = tmp_path / "actual"
    actual.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    directory = alias / "nested"
    args = dict(diagnostic_id="c"*64, generation=1, directory=directory)
    with pytest.raises(OSError):
        write_regression_capture(**args, members={"diff.json": b"diff"})
    assert not list(actual.iterdir())
    path, _ = write_regression_capture(
        **{**args, "directory": actual / "nested"}, members={"diff.json": b"diff"},
    )
    with pytest.raises(OSError):
        recover_regression_capture(
            **args, expected_members={"diff.json": b"diff"},
            input_digest=sha256(b"raw").hexdigest(),
        )
    assert path.is_file()


def test_capture_rejects_writable_by_other_users_directory(tmp_path):
    directory = tmp_path / "shared"
    directory.mkdir(mode=0o700)
    directory.chmod(0o770)
    try:
        with pytest.raises(ValueError, match="unsafe_directory"):
            write_regression_capture(diagnostic_id="d"*64, generation=1,
                                     members={}, directory=directory)
        assert not list(directory.iterdir())
    finally:
        directory.chmod(0o700)


def test_existing_identical_archive_requires_current_owner(tmp_path, monkeypatch):
    import os
    import stat
    from types import SimpleNamespace
    from hashlib import sha256
    from cifereca.core.notice_regression_capture import recover_regression_capture

    args = dict(diagnostic_id="e"*64, generation=1, directory=tmp_path)
    members = {"diff.json": b"diff"}
    path, _ = write_regression_capture(**args, members=members)
    original = os.fstat

    def foreign_file(fd):
        info = original(fd)
        if stat.S_ISREG(info.st_mode):
            return SimpleNamespace(st_mode=info.st_mode, st_size=info.st_size,
                                   st_uid=os.getuid()+1)
        return info

    with monkeypatch.context() as patch:
        patch.setattr(os, "fstat", foreign_file)
        with pytest.raises(ValueError, match="conflict"):
            write_regression_capture(**args, members=members)
        with pytest.raises(ValueError, match="conflict"):
            recover_regression_capture(
                **args, expected_members=members,
                input_digest=sha256(b"raw").hexdigest(),
            )
    assert list(tmp_path.iterdir()) == [path]
    assert write_regression_capture(**args, members=members) == (path, "partial")


def test_recovery_does_not_create_missing_directory(tmp_path):
    from hashlib import sha256
    from cifereca.core.notice_regression_capture import recover_regression_capture

    directory = tmp_path / "missing" / "nested"
    assert recover_regression_capture(
        diagnostic_id="f"*64, generation=1, directory=directory,
        expected_members={}, input_digest=sha256(b"raw").hexdigest(),
    ) is None
    assert not (tmp_path / "missing").exists()
