"""P0 #2290: rollback follows actual embedded packages, not a stale release pin.

Tiny synthetic nested ZIPs only: no network, plugin activation or user XDG.
The separate CI full-release gate exercises the real 26-plugin installation.
"""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from cifereca import updater


PRIVATE = "https://private.invalid/token=DO_NOT_EXPORT_2290"


def _plugin(package_id: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "cifereca-plugin.json",
            json.dumps({"package": {"id": package_id, "version": "1.0.0"}}),
        )
    return buffer.getvalue()


def _snapshot(tmp_path, *, payloads=None, manifest=None) -> object:
    payloads = payloads if payloads is not None else {
        "org.cifereca.one": _plugin("org.cifereca.one"),
        "org.cifereca.two": _plugin("org.cifereca.two"),
        # This source package is NOT present in frozen plugin-release.json.
        "org.cifereca.new": _plugin("org.cifereca.new"),
    }
    manifest = manifest if manifest is not None else {
        "plugins": [{"path": f"packages/{name}.zip"} for name in payloads],
    }
    path = tmp_path / "snapshot.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("cifereca-release-plugins/cifereca-dev.json", json.dumps(manifest))
        for name, payload in payloads.items():
            archive.writestr(f"cifereca-release-plugins/packages/{name}.zip", payload)
    return path


def test_actual_snapshot_package_ids_include_new_source_packages(tmp_path):
    snapshot = _snapshot(tmp_path)
    actual = updater._snapshot_package_ids(snapshot)
    frozen_publication = {"org.cifereca.one", "org.cifereca.two"}
    assert set(actual) == frozen_publication | {"org.cifereca.new"}
    assert len(actual) == 3


@pytest.mark.parametrize("fault", [
    "duplicate_package_id", "missing_nested_manifest", "wrong_snapshot_path",
    "invalid_package_id", "html_instead_of_json",
])
def test_snapshot_inventory_fails_before_activation_without_echoing_paths(tmp_path, fault):
    ids = ["org.cifereca.one", "org.cifereca.two"]
    payloads = {name: _plugin(name) for name in ids}
    manifest = {"plugins": [{"path": f"packages/{name}.zip"} for name in ids]}
    if fault == "duplicate_package_id":
        payloads[ids[1]] = _plugin(ids[0])
    elif fault == "missing_nested_manifest":
        payloads[ids[1]] = b"not-an-inner-zip"
    elif fault == "wrong_snapshot_path":
        manifest["plugins"][1]["path"] = PRIVATE
    elif fault == "invalid_package_id":
        payloads[ids[1]] = _plugin(PRIVATE)
    else:
        payloads[ids[1]] = b"<html>PRIVATE</html>"
    snapshot = _snapshot(tmp_path, payloads=payloads, manifest=manifest)
    with pytest.raises(updater.UpdateError) as err:
        updater._snapshot_package_ids(snapshot)
    assert str(err.value) == "Inventaire des packages du snapshot de release invalide"
    assert "DO_NOT_EXPORT_2290" not in str(err.value)
    assert err.value.__suppress_context__


def test_snapshot_inventory_does_not_need_private_package_payload_metadata(tmp_path):
    first = _plugin("org.cifereca.one")
    snapshot = _snapshot(
        tmp_path,
        payloads={"one": first},
        manifest={"plugins": [{"path": "packages/one.zip", "private": PRIVATE}]},
    )
    assert updater._snapshot_package_ids(snapshot) == ["org.cifereca.one"]
