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


def test_preview_temp_is_exclusive_and_replaces_only_symlink_leaf(tmp_path):
    """A previous thumbnail symlink must not receive preview bytes."""
    import stat

    source = tmp_path / "original"
    source.write_bytes(b"attested-physical-image")
    dest = preview_path(source, tmp_path, 80, 120)
    foreign = tmp_path / "unrelated-account-image"
    foreign.write_bytes(b"DO_NOT_CHANGE")
    dest.symlink_to(foreign)
    observed = []

    class Backend:
        @staticmethod
        def get_file_info(_path):
            return True, 100, 200

        @staticmethod
        def new_from_file_at_scale(_path, _width, _height, _preserve):
            return Backend()

        def savev(self, path, kind, keys, values):
            from pathlib import Path
            temporary = Path(path)
            assert kind == "png" and keys == [] and values == []
            assert temporary.exists() and not temporary.is_symlink()
            assert temporary.parent == dest.parent and temporary != dest
            assert stat.S_ISREG(temporary.stat().st_mode)
            assert stat.S_IMODE(temporary.stat().st_mode) & 0o077 == 0
            observed.append(temporary)
            temporary.write_bytes(b"rendered-preview")
            return True

    assert create_preview(source, dest, 80, 120, backend=Backend) == dest
    assert observed and not observed[0].exists()
    assert not dest.is_symlink() and dest.read_bytes() == b"rendered-preview"
    assert foreign.read_bytes() == b"DO_NOT_CHANGE"
    assert not list(tmp_path.glob("*.tmp"))


def test_failed_preview_publication_does_not_leave_exclusive_temp(tmp_path):
    source = tmp_path / "original"
    source.write_bytes(b"attested-physical-image")
    dest = preview_path(source, tmp_path, 80, 120)

    class Backend:
        @staticmethod
        def get_file_info(_path):
            return True, 100, 200

        @staticmethod
        def new_from_file_at_scale(_path, _width, _height, _preserve):
            return Backend()

        def savev(self, path, *_args):
            from pathlib import Path
            Path(path).write_bytes(b"partial-preview")
            return False

    with pytest.raises(ValueError, match="preview_save_failed"):
        create_preview(source, dest, 80, 120, backend=Backend)
    assert not dest.exists()
    assert not list(tmp_path.glob("*.tmp"))
