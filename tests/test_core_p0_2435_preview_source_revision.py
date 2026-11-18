"""N3 physical preview cache: a replaced source must not reuse stale pixels."""
import os

import pytest

from cifereca.gui.cover_preview import create_preview, preview_path


def replace_preserving_mtime(source):
    info = source.stat()
    replacement = source.with_name("replacement")
    replacement.write_bytes(b"new!")
    os.utime(replacement, ns=(info.st_atime_ns, info.st_mtime_ns))
    replacement.replace(source)


def test_same_size_and_mtime_replacement_changes_preview_key(tmp_path):
    source = tmp_path / "original"
    source.write_bytes(b"old!")
    first = preview_path(source, tmp_path, 80, 120)
    replace_preserving_mtime(source)
    second = preview_path(source, tmp_path, 80, 120)
    assert first != second
    with pytest.raises(ValueError, match="preview_source_changed"):
        create_preview(source, first, 80, 120, backend=object())
    assert not first.exists()


@pytest.mark.parametrize("mutate", [False, True])
def test_preview_publication_rechecks_source_after_decode(tmp_path, mutate):
    source = tmp_path / "original"
    source.write_bytes(b"old!")
    dest = preview_path(source, tmp_path, 80, 120)

    class Backend:
        @staticmethod
        def get_file_info(path):
            return True, 100, 200

        @staticmethod
        def new_from_file_at_scale(path, width, height, preserve):
            assert (width, height, preserve) == (80, 120, True)
            return Backend()

        def savev(self, path, kind, keys, values):
            from pathlib import Path
            Path(path).write_bytes(b"preview")
            if mutate:
                replace_preserving_mtime(source)
            return True

    if mutate:
        with pytest.raises(ValueError, match="preview_source_changed"):
            create_preview(source, dest, 80, 120, backend=Backend)
        assert not dest.exists()
    else:
        assert create_preview(source, dest, 80, 120, backend=Backend) == dest
        assert dest.read_bytes() == b"preview"
    assert not list(tmp_path.glob("*.tmp"))


def test_symlink_is_not_a_physical_preview_source(tmp_path):
    original = tmp_path / "original"
    original.write_bytes(b"old!")
    alias = tmp_path / "alias"
    alias.symlink_to(original)
    with pytest.raises(ValueError, match="preview_source_not_regular"):
        preview_path(alias, tmp_path, 80, 120)
