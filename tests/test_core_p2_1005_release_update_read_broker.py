"""#1005: official announcement is not verified installation authorization."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
import json

import pytest

from cifereca.brokers import build_public_brokers
from cifereca.core.release_update_read import ReleaseUpdateReadBrokerImpl
from cifereca.plugin_api import (
    ReleaseInstallationSnapshot, ReleaseUpdatePreview, ReleaseUpdateReadBroker,
)


def _ann(**overrides):
    result = {
        "repository": "pzia/cifereca",
        "tag_name": "v0.9.45",
        "draft": False,
        "prerelease": False,
        "published_at": "2026-10-19T10:00:00Z",
        "assets": [{"name": "cifereca.zip", "size": 123,
                    "browser_download_url": "https://private.invalid/?token=NEVER"}],
        "sha256": "a" * 64, "sigstore": {"verified": True},
        "provenance_verified": True, "bytes_sha256_verified": True,
        "can_apply": True, "approved": True,
    }
    result.update(overrides)
    return result


def test_public_broker_registered_as_optional_read_only_contract():
    brokers = build_public_brokers(object())
    assert "release.update.read" in brokers
    exposed = brokers["release.update.read"]
    assert isinstance(exposed, ReleaseUpdateReadBroker)
    assert not hasattr(exposed, "apply")
    assert not hasattr(exposed, "install")
    assert not hasattr(exposed, "download")
    assert not hasattr(exposed, "restart")
    assert callable(exposed.installation)
    assert callable(exposed.preview_release)
    assert "release.update" not in brokers


def test_untrusted_github_announcement_and_self_signed_sidecars_never_authorize_apply():
    result = ReleaseUpdateReadBrokerImpl().preview_release(_ann())
    assert isinstance(result, ReleaseUpdatePreview)
    assert result.state == "blocked"
    assert result.reason_code == "provenance_not_verified"
    assert result.version == "v0.9.45"
    assert result.schema_version == 1
    assert result.dry_run
    assert result.provenance_verified is False
    assert result.bytes_sha256_verified is False
    assert result.can_apply is False
    assert "NEVER" not in repr(result)
    with pytest.raises(FrozenInstanceError):
        result.can_apply = True


@pytest.mark.parametrize("input,reason", [
    (None, "invalid_announcement"),
    ("https://private.invalid?token=SECRET", "invalid_announcement"),
    ([], "invalid_announcement"),
    ({}, "invalid_announcement"),
    ({"repository": "pzia/cifereca"}, "not_published"),
    (_ann(repository="pzia/other"), "not_official_repository"),
    (_ann(repository="PZIA/cifereca"), "not_official_repository"),
    (_ann(repository="https://private.invalid/?token=SECRET"),
     "not_official_repository"),
    (_ann(draft=True), "not_published"),
    (_ann(published_at=None), "not_published"),
    (_ann(prerelease=True), "channel_not_approved"),
    (_ann(tag_name="../v0.9.45"), "invalid_announcement"),
    (_ann(tag_name="v0.9.45\nBearer SECRET"), "invalid_announcement"),
    (_ann(tag_name="version-A"), "invalid_announcement"),
    (_ann(tag_name="v" + "1" * 120), "invalid_announcement"),
])
def test_closed_reason_codes_and_no_untrusted_values(input, reason):
    obj = ReleaseUpdateReadBrokerImpl().preview_release(input)
    assert obj.state == "blocked"
    assert obj.reason_code == reason
    assert not obj.can_apply
    assert not obj.provenance_verified
    assert not obj.bytes_sha256_verified
    assert "private.invalid" not in repr(obj)
    assert "SECRET" not in repr(obj)


def test_valid_but_untrusted_checksums_do_not_change_unverified_state():
    broker = ReleaseUpdateReadBrokerImpl()
    old = broker.preview_release(_ann(sha256="b" * 64))
    missing = broker.preview_release(_ann(assets=[], sha256=None))
    contradictory = broker.preview_release(_ann(
        sha256="0" * 64, sigstore={"verified": True},
        provenance={"subject": {"sha256": "f" * 64}},
    ))
    assert old == missing == contradictory
    assert old.reason_code == "provenance_not_verified"


def test_no_installed_release_reports_explicit_not_installed_without_creating_files(tmp_path):
    root = tmp_path / "install"
    broker = ReleaseUpdateReadBrokerImpl(root)
    assert broker.installation() == ReleaseInstallationSnapshot(state="not_installed")
    assert not root.exists()


def test_installation_reads_only_managed_current_release_and_bounded_version(tmp_path):
    root = tmp_path / "runtime"
    current_release = root / "releases" / "0.9.44"
    current_release.mkdir(parents=True)
    manifest = current_release / ".cifereca-install.json"
    manifest.write_text(json.dumps({
        "version": "0.9.44", "provenance": {"token": "DO_NOT_SHOW"},
        "install_root": "https://private.invalid/?token=DO_NOT_SHOW",
    }), encoding="utf-8")
    (root / "current").symlink_to(current_release, target_is_directory=True)
    broker = ReleaseUpdateReadBrokerImpl(root)
    before = manifest.read_bytes()
    installed = broker.installation()
    assert installed.state == "installed"
    assert installed.version == "0.9.44"
    assert installed.schema_version == 1
    assert installed.can_apply is False
    assert not any(secret in repr(installed)
                   for secret in ("DO_NOT_SHOW", "private.invalid", str(root)))
    assert manifest.read_bytes() == before
    with pytest.raises(FrozenInstanceError):
        broker.root_override = tmp_path


@pytest.mark.parametrize("payload", [
    {"version": "https://private.invalid/?token=DO_NOT_SHOW"},
    {"version": "0.9.44\npassword=PRIVATE"},
    {"version": {"version": "0.9.44"}},
    {"version": ""},
    ["0.9.44"],
])
def test_installation_manifest_must_have_sanitized_version(tmp_path, payload):
    root = tmp_path / "install"
    release = root / "releases" / "app"
    release.mkdir(parents=True)
    (release / ".cifereca-install.json").write_text(
        json.dumps(payload), encoding="utf-8",
    )
    (root / "current").symlink_to(release, target_is_directory=True)
    result = ReleaseUpdateReadBrokerImpl(root).installation()
    assert result.state == "unknown"
    assert result.version == ""


def test_installed_pointer_cannot_disclose_file_outside_managed_releases(tmp_path):
    root = tmp_path / "install"
    outside = tmp_path / "secrets"
    outside.mkdir()
    (outside / ".cifereca-install.json").write_text(
        json.dumps({"version": "0.9.44", "token": "DO_NOT_SHOW"}),
        encoding="utf-8",
    )
    root.mkdir()
    (root / "current").symlink_to(outside, target_is_directory=True)
    result = ReleaseUpdateReadBrokerImpl(root).installation()
    assert result.state == "unknown"
    assert result.version == ""


def test_oversized_or_symlinked_install_manifest_is_never_trusted(tmp_path):
    root = tmp_path / "install"
    release = root / "releases" / "app"
    release.mkdir(parents=True)
    (root / "current").symlink_to(release, target_is_directory=True)
    manifest = release / ".cifereca-install.json"
    manifest.write_bytes(b"x" * 4097)
    assert ReleaseUpdateReadBrokerImpl(root).installation().state == "unknown"
    manifest.unlink()
    external = tmp_path / "secret.json"
    external.write_text('{"version":"0.9.44"}')
    manifest.symlink_to(external)
    assert ReleaseUpdateReadBrokerImpl(root).installation().state == "unknown"


def test_updater_write_functions_unreachable_even_if_monkeypatched(tmp_path, monkeypatch):
    from cifereca import updater
    def blocked(*args, **kwargs):
        raise AssertionError("update/install function called")
    monkeypatch.setattr(updater, "install_source", blocked)
    monkeypatch.setattr(updater, "apply_release", blocked, raising=False)
    broker = ReleaseUpdateReadBrokerImpl(tmp_path / "missing")
    assert broker.installation().state == "not_installed"
    assert broker.preview_release(_ann()).state == "blocked"
    assert not hasattr(broker, "apply_release")
