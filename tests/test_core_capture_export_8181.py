from io import BytesIO
from zipfile import ZipFile

import pytest

from cifereca.plugin_api.capture_vault import (
    VaultError, build_capture_zip, write_capture_zip,
)


def test_private_zip_is_bounded_and_exclusive(tmp_path):
    payload = build_capture_zip({"trace.json": b'{"fixture":true}'}, max_bytes=4096)
    with ZipFile(BytesIO(payload)) as archive:
        assert archive.read("trace.json") == b'{"fixture":true}'
        assert archive.getinfo("trace.json").external_attr >> 16 == 0o600
    dest = tmp_path / "capture.zip"
    with pytest.raises(VaultError, match="private_zip_confirmation_required"):
        write_capture_zip(dest, payload, max_bytes=4096)
    assert not dest.exists()
    assert write_capture_zip(dest, payload, confirmed=True, max_bytes=4096) == len(payload)
    assert dest.stat().st_mode & 0o777 == 0o600
    with pytest.raises(VaultError, match="private_zip_exists"):
        write_capture_zip(dest, payload, confirmed=True, max_bytes=4096)
    assert dest.read_bytes() == payload
    alias = tmp_path / "alias.zip"
    alias.symlink_to(dest)
    with pytest.raises(VaultError, match="private_zip_exists"):
        write_capture_zip(alias, payload, confirmed=True, max_bytes=4096)
    assert dest.read_bytes() == payload


@pytest.mark.parametrize("name", ["../secret", "/absolute", "a/../b", "a//b", "a\\b"])
def test_zip_rejects_unsafe_members(name):
    with pytest.raises(VaultError, match="private_zip_invalid"):
        build_capture_zip({name: b"fictional"}, max_bytes=4096)


def test_zip_rejects_oversized_input_and_output(tmp_path):
    with pytest.raises(VaultError, match="private_zip_limit"):
        build_capture_zip({"trace.json": b"x" * 4097}, max_bytes=1024)
    with pytest.raises(VaultError, match="private_zip_limit"):
        build_capture_zip({"trace.json": b"x"}, max_bytes=16)
    dest = tmp_path / "capture.zip"
    with pytest.raises(VaultError, match="private_zip_limit"):
        write_capture_zip(dest, b"x" * 17, confirmed=True, max_bytes=16)
    assert not dest.exists()


def test_shared_writer_refuses_intermediate_symlink_and_strict_unsafe_directory(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    nested = actual / "nested"
    nested.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    with pytest.raises(VaultError, match="private_zip_write_failed"):
        write_capture_zip(alias / "nested" / "test.zip", b"fixture", confirmed=True, max_bytes=4096)
    assert not list(nested.iterdir())
    nested.chmod(0o777)
    try:
        with pytest.raises(VaultError, match="private_zip_destination"):
            write_capture_zip(nested / "test.zip", b"fixture", confirmed=True, max_bytes=4096,
                              require_safe_parent=True)
    finally:
        nested.chmod(0o700)
    assert not list(nested.iterdir())
