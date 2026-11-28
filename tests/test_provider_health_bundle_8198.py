from io import BytesIO
from zipfile import ZipFile
from hashlib import sha256
import json
import pytest

from cifereca.core import provider_health_bundle as bundle
from cifereca.core.capture_vault import VaultError


def test_bundle_manifest_and_exact_bytes(monkeypatch):
    monkeypatch.setattr(bundle, "core_contract_kit", lambda: {"plugin-dev-kit/test.txt": b"contract"})
    payload = bundle.build_health_bundle((("search", b"first"), ("detail", b"second")),
                                         include_binary=False)
    with ZipFile(BytesIO(payload)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["provider_source"] == "not_included"
        assert manifest["business_comparison"] == "not_attested"
        assert manifest["include_binary"] is False
        for row, expected in zip(manifest["captures"], (b"first", b"second")):
            assert archive.read(row["member"]) == expected
            assert row["sha256"] == sha256(expected).hexdigest()
        assert archive.read("plugin-dev-kit/test.txt") == b"contract"
        assert archive.testzip() is None
    with pytest.raises(VaultError):
        bundle.build_health_bundle((("download", b"x"),), include_binary=False)
    with pytest.raises(VaultError):
        bundle.build_health_bundle((), include_binary=False)
