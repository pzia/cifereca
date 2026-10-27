"""P0 #2770: deterministic offline attestation/consumer checks, no PyPI traffic."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from cifereca.wheelhouse_quarantine import (
    MANIFEST, attest_wheelhouse, verify_wheelhouse,
)
from cifereca.plugins.python_quarantine import QuarantineRejected

NOW = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)


def _wheel(directory, name="example_pkg-1.0-py3-none-any.whl", data=b"wheel bytes"):
    directory.mkdir(exist_ok=True)
    file = directory / name
    file.write_bytes(data)
    return file


def _pypi(name: str, version: str, file, age: timedelta):
    stamp = (NOW - age).isoformat()
    return {
        "info": {"name": name, "version": version},
        "urls": [{
            "filename": file.name,
            "packagetype": "bdist_wheel",
            "url": "https://files.pythonhosted.org/packages/" + file.name,
            "digests": {"sha256": hashlib.sha256(file.read_bytes()).hexdigest()},
            "yanked": False, "upload_time_iso_8601": stamp,
        }],
    }


def test_eligible_offline_wheelhouse_accepts_exact_72h_without_network(tmp_path):
    wheel = _wheel(tmp_path)
    record = _pypi("example-pkg", "1.0", wheel, timedelta(hours=72))
    attest_wheelhouse(tmp_path, now=NOW, read_release=lambda *_: record)
    assert verify_wheelhouse(tmp_path, now=NOW) == (wheel,)


def test_71h59_refused_at_producer(tmp_path):
    wheel = _wheel(tmp_path)
    record = _pypi("example-pkg", "1.0", wheel, timedelta(hours=71, minutes=59, seconds=59))
    with pytest.raises(QuarantineRejected, match="dependency_too_recent"):
        attest_wheelhouse(tmp_path, now=NOW, read_release=lambda *_: record)
    assert not (tmp_path / MANIFEST).exists()


@pytest.mark.parametrize("alteration", ["content", "sha", "timestamp", "extra", "missing"])
def test_offline_wheelhouse_is_closed_to_modified_or_unattested_files(tmp_path, alteration):
    wheel = _wheel(tmp_path)
    record = _pypi("example-pkg", "1.0", wheel, timedelta(days=9))
    attest_wheelhouse(tmp_path, now=NOW, read_release=lambda *_: record)
    manifest = tmp_path / MANIFEST
    if alteration == "content":
        wheel.write_bytes(b"tampered")
    elif alteration == "extra":
        _wheel(tmp_path, "another-1.0-py3-none-any.whl")
    elif alteration == "missing":
        manifest.unlink()
    else:
        data = json.loads(manifest.read_text())
        if alteration == "sha":
            data["wheels"][0]["sha256"] = "0" * 64
        else:
            data["wheels"][0]["uploaded_at"] = (NOW - timedelta(hours=1)).isoformat()
        manifest.write_text(json.dumps(data))
    with pytest.raises(QuarantineRejected):
        verify_wheelhouse(tmp_path, now=NOW)


def test_old_version_newly_built_wheel_is_not_eligible(tmp_path):
    wheel = _wheel(tmp_path, "example_pkg-0.1-py3-none-any.whl")
    record = _pypi("example-pkg", "0.1", wheel, timedelta(hours=20))
    with pytest.raises(QuarantineRejected, match="dependency_too_recent"):
        attest_wheelhouse(tmp_path, now=NOW, read_release=lambda *_: record)


def test_yanked_or_unknown_wheel_refused(tmp_path):
    wheel = _wheel(tmp_path)
    record = _pypi("example-pkg", "1.0", wheel, timedelta(days=9))
    record["urls"][0]["yanked"] = True
    with pytest.raises(QuarantineRejected):
        attest_wheelhouse(tmp_path, now=NOW, read_release=lambda *_: record)
    record["urls"][0]["yanked"] = False
    record["urls"][0]["digests"]["sha256"] = "0" * 64
    with pytest.raises(QuarantineRejected):
        attest_wheelhouse(tmp_path, now=NOW, read_release=lambda *_: record)


def test_local_core_wheel_exception_is_exact_and_does_not_cover_third_party(tmp_path):
    wheel = _wheel(tmp_path, "cifereca-9.9.9-py3-none-any.whl")
    attest_wheelhouse(tmp_path, core_version="9.9.9", now=NOW)
    assert verify_wheelhouse(tmp_path, core_version="9.9.9", now=NOW) == (wheel,)
    with pytest.raises(QuarantineRejected):
        verify_wheelhouse(tmp_path, core_version="9.9.8", now=NOW)
    with pytest.raises(QuarantineRejected):
        verify_wheelhouse(tmp_path, now=NOW)


def test_missing_unattested_directory_blocked(tmp_path):
    _wheel(tmp_path)
    with pytest.raises(QuarantineRejected, match="dependency_provenance_unavailable"):
        verify_wheelhouse(tmp_path, now=NOW)


def test_unverified_url_cannot_claim_pypi_origin(tmp_path):
    wheel = _wheel(tmp_path)
    record = _pypi("example-pkg", "1.0", wheel, timedelta(days=9))
    attest_wheelhouse(tmp_path, now=NOW, read_release=lambda *_: record)
    data = json.loads((tmp_path / MANIFEST).read_text())
    data["wheels"][0]["url"] = "https://mirror.example/" + wheel.name
    (tmp_path / MANIFEST).write_text(json.dumps(data))
    with pytest.raises(QuarantineRejected):
        verify_wheelhouse(tmp_path, now=NOW)
