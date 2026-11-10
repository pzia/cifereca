"""P0 #5056: completed ACSM reuse depends on verified bytes, not provider id."""
import hashlib
import os
import shutil
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core.manual_acsm_import import import_acsm
from cifereca.providers import ProviderConfigurationError


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("placement", [None, "bnf-pret"])
@pytest.mark.parametrize("damage", [
    "none", "changed", "invalid", "symlink", "dangling_symlink", "missing", "directory",
])
def test_completed_acsm_reuse_requires_exact_artifact_for_every_provider(
    tmp_path, placement, damage,
):
    source = tmp_path / "source.acsm"
    source.write_bytes(b"synthetic acsm")
    artifact = tmp_path / "book.epub"
    artifact.write_bytes(b"synthetic epub")
    original_sha = _sha(artifact)
    if damage == "changed":
        artifact.write_bytes(b"modified epub")
    if damage in {"symlink", "dangling_symlink"}:
        target = tmp_path / "target.epub"
        if damage == "symlink":
            target.write_bytes(b"synthetic epub")
        artifact.unlink()
        artifact.symlink_to(target)
    if damage == "missing":
        artifact.unlink()
    if damage == "directory":
        artifact.unlink()
        artifact.mkdir()

    class Storage:
        def acquisition_get(self, acquisition_id):
            return {
                "status": "complete",
                "artifact_id": "sha256:" + original_sha,
                "metadata": {"path": str(artifact)},
            }

        def acquisition_put(self, *_args, **_kwargs):
            pytest.fail("completed import must never be rewritten")

    service = NS(
        current_identity=NS(identity_id="alice"),
        storage=Storage(),
    )
    kwargs = dict(
        provider_id="manual",
        placement_provider_id=placement,
        Path=Path,
        ProviderConfigurationError=ProviderConfigurationError,
        sha256_file=_sha,
        hashlib=hashlib,
        data_dir=lambda: tmp_path,
        shutil=shutil,
        detect_acsm_format=lambda *_: "epub",
        AdeptProfileResolutionError=Exception,
        inspect_artifact=lambda _: NS(valid=damage != "invalid"),
        os=os,
    )
    if damage == "none":
        result = import_acsm(service, source, **kwargs)
        assert result["status"] == "unchanged"
        assert result["path"] == str(artifact)
    else:
        with pytest.raises(ProviderConfigurationError, match="réconciliation nécessaire"):
            import_acsm(service, source, **kwargs)
