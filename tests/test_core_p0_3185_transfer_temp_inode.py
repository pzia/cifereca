"""#3185: local send must write into its pinned mkstemp descriptor.

A substituted temporary symlink must not truncate another profile's file.
SQLite and bytes are disposable; no provider, DRM or live user's files.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

import pytest

from cifereca.providers import ProviderConfigurationError
from tests.test_core0925_open_destinations import _placement, _service
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES


@pytest.mark.parametrize("canonical", [False, True])
@pytest.mark.parametrize("replacement", ["symlink", "regular"])
def test_substituted_temporary_path_never_writes_or_unlinks_foreign_file(
    tmp_path, monkeypatch, canonical, replacement,
):
    service = _service(tmp_path)
    root = tmp_path / "EBooks"
    source = root / "alice" / "local" / "book.pdf"
    source.parent.mkdir(parents=True)
    contents = BYTES if canonical else b"legacy-file-content"
    source.write_bytes(contents)
    digest = hashlib.sha256(contents).hexdigest()
    artifact = "sha256:" + digest if canonical else "sha256:legacy"
    if canonical:
        service.storage.artifact_put({
            "artifact_id": artifact, "sha256": digest,
            "size": len(contents), "media_format": "pdf",
        })
    source_id = _placement(
        service, source, identity_id="alice", artifact_id=artifact,
    )

    foreign = tmp_path / "other-profile-unrelated.bin"
    original = b"keep these unrelated original bytes"
    replacement_bytes = b"another process owns this temporary pathname"
    foreign.write_bytes(original)
    mkstemp_original = tempfile.mkstemp
    replacements = []

    def substitute(*args, **kwargs):
        fd, name = mkstemp_original(*args, **kwargs)
        assert Path(kwargs["dir"]) == root / "bob" / "Acquisitions"
        os.unlink(name)
        if replacement == "symlink":
            Path(name).symlink_to(foreign)
        else:
            Path(name).write_bytes(replacement_bytes)
        replacements.append(name)
        return fd, name

    monkeypatch.setattr(tempfile, "mkstemp", substitute)
    try:
        with pytest.raises(
            ProviderConfigurationError, match="Fichier temporaire modifié",
        ):
            service.materialize_to_identity(source_id, "bob")
        assert len(replacements) == 1
        assert foreign.read_bytes() == original
        assert source.read_bytes() == contents
        # The substituted inode is NOT ours to delete either. For the
        # symlink case, unlinking the link would not truncate the target,
        # but it would still destroy another process's directory entry.
        assert os.path.lexists(replacements[0])
        if replacement == "symlink":
            assert Path(replacements[0]).is_symlink()
        else:
            assert Path(replacements[0]).read_bytes() == replacement_bytes
        Path(replacements[0]).unlink()
        assert not list((root / "bob" / "Acquisitions").iterdir())
        assert not any(
            p.get("identity_id") == "bob" for p in service.storage.placements()
        )
        assert service.storage.catalog_entries() == []
        assert service.storage.entitlements() == []
    finally:
        service.close()
