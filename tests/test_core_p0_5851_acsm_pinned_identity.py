"""#5851: a looked-up identity object is not a mutable operation owner."""
import hashlib
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core.manual_acsm_import import import_acsm
from cifereca.providers import ProviderConfigurationError


class _VolatileIdentity:
    def __init__(self):
        self.reads = 0

    @property
    def identity_id(self):
        self.reads += 1
        if self.reads > 1:
            raise AssertionError("Core re-read mutable identity during ACSM import")
        return "alice"


def _import(service, source, artifact):
    def sha(path):
        return "acsm-source-sha" if Path(path) == source else "artifact-sha"

    return import_acsm(
        service, source, identity_id="alice",
        Path=Path, ProviderConfigurationError=ProviderConfigurationError,
        sha256_file=sha, hashlib=hashlib, data_dir=None, shutil=None,
        detect_acsm_format=None, AdeptProfileResolutionError=Exception,
        inspect_artifact=lambda _path: NS(valid=True), os=None,
    )


def test_completed_acsm_reuse_never_rereads_the_identity_object(tmp_path):
    source = tmp_path / "source.acsm"
    source.write_bytes(b"synthetic")
    artifact = tmp_path / "book.epub"
    artifact.write_bytes(b"synthetic")
    identity = _VolatileIdentity()
    expected = "manual-acsm:" + hashlib.sha256(
        b"alice:acsm-source-sha"
    ).hexdigest()[:32]
    seen = []

    class Storage:
        def acquisition_get(self, key):
            seen.append(key)
            return {
                "status": "complete", "artifact_id": "sha256:artifact-sha",
                "metadata": {"path": str(artifact)},
            }

        def acquisition_put(self, *_a, **_kw):
            pytest.fail("completed receipt must not be rewritten")

    service = NS(identity_by_id=lambda _iid: identity, storage=Storage())
    result = _import(service, source, artifact)
    assert result == {
        "status": "unchanged", "path": str(artifact),
        "acquisition_id": expected,
    }
    assert seen == [expected]
    assert identity.reads == 1


def test_wrong_looked_up_owner_is_rejected_before_staging(tmp_path):
    source = tmp_path / "source.acsm"
    source.write_bytes(b"synthetic")
    service = NS(
        identity_by_id=lambda _iid: NS(identity_id="bob"),
        storage=NS(acquisition_get=lambda *_a: pytest.fail("unexpected SQL")),
    )
    with pytest.raises(ProviderConfigurationError, match="Aucune Identity active"):
        _import(service, source, tmp_path / "book.epub")
    assert source.read_bytes() == b"synthetic"
