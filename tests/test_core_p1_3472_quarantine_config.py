"""P1 #3472: one configured age gate for pip, producer and offline consumer."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from cifereca import config as config_module
from cifereca.plugins.python_quarantine import (
    QuarantineRejected,
    check_pip_report,
    quarantine_diagnostic,
    quarantine_duration,
)
from cifereca.wheelhouse_quarantine import attest_wheelhouse, verify_wheelhouse


NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)


def _release(age: timedelta, *, name: str = "policy-wheel"):
    wheel = name.replace("-", "_") + "-1.2.3-py3-none-any.whl"
    url = "https://files.pythonhosted.org/packages/" + wheel
    sha = "a" * 64
    return (
        {"install": [{
            "metadata": {"name": name, "version": "1.2.3"},
            "download_info": {"url": url, "archive_info": {"hashes": {"sha256": sha}}},
        }]},
        {"info": {"name": name, "version": "1.2.3"}, "urls": [{
            "filename": wheel, "url": url, "packagetype": "bdist_wheel",
            "digests": {"sha256": sha}, "yanked": False,
            "upload_time_iso_8601": (NOW - age).isoformat(),
        }]},
    )


def _online(age: timedelta, *, hours: object):
    report, registry = _release(age)
    return check_pip_report(
        report, now=NOW, read_release=lambda *_: registry,
        quarantine_hours=hours,
    )


def test_config_default_and_persistent_overrides_drive_online_admission(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(config_module, "config_file", lambda: path)

    # No config file / historical JSON with no security section both receive 72 h.
    assert quarantine_duration() == (timedelta(hours=72), 72)
    path.write_text(json.dumps({"providers": {"accounts": []}}), encoding="utf-8")
    assert quarantine_duration() == (timedelta(hours=72), 72)

    config = config_module.Config(path)
    config.set("security.python_dependency_quarantine_hours", 168)
    config.save()
    assert config_module.Config(path).get("security.python_dependency_quarantine_hours") == 168

    report, registry = _release(timedelta(hours=72))
    with pytest.raises(QuarantineRejected, match="^dependency_too_recent$") as rejected:
        check_pip_report(report, now=NOW, read_release=lambda *_: registry)
    assert rejected.value.quarantine_hours == 168
    assert rejected.value.eligible_at == NOW + timedelta(hours=96)
    assert "eligible_at=" in quarantine_diagnostic(rejected.value)

    config = config_module.Config(path)
    config.set("security.python_dependency_quarantine_hours", 24)
    config.save()
    assert check_pip_report(report, now=NOW, read_release=lambda *_: registry)
    assert quarantine_duration() == (timedelta(hours=24), 24)


def test_exact_24h_and_72h_boundaries_and_stricter_168h_override():
    with pytest.raises(QuarantineRejected, match="dependency_too_recent"):
        _online(timedelta(hours=24, seconds=-1), hours=24)
    assert _online(timedelta(hours=24), hours=24)
    with pytest.raises(QuarantineRejected, match="dependency_too_recent"):
        _online(timedelta(hours=72, seconds=-1), hours=72)
    assert _online(timedelta(hours=72), hours=72)
    with pytest.raises(QuarantineRejected, match="dependency_too_recent"):
        _online(timedelta(hours=72), hours=168)


@pytest.mark.parametrize("invalid", [
    None, True, False, "72", "0", 0, -1, 1.2, [], {},
    10 ** 100,
])
def test_invalid_quarantine_policy_refuses_before_network(invalid):
    seen = []
    report, registry = _release(timedelta(days=9))
    with pytest.raises(QuarantineRejected, match="^dependency_quarantine_configuration_invalid$"):
        check_pip_report(report, now=NOW,
                         read_release=lambda *_: seen.append(registry),
                         quarantine_hours=invalid)
    assert seen == []


def test_invalid_persisted_security_section_refuses_closed(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(config_module, "config_file", lambda: path)
    path.write_text(json.dumps({"security": "bad"}), encoding="utf-8")
    with pytest.raises(QuarantineRejected, match="dependency_quarantine_configuration_invalid"):
        check_pip_report({"install": []}, now=NOW)
    path.write_text(json.dumps({"security": {"python_dependency_quarantine_hours": False}}), encoding="utf-8")
    with pytest.raises(QuarantineRejected, match="dependency_quarantine_configuration_invalid"):
        verify_wheelhouse(tmp_path, now=NOW)


def test_attested_under_24h_does_not_bypass_168h_offline_consumer(tmp_path):
    filename = "policy_wheel-1.2.3-py3-none-any.whl"
    wheel = tmp_path / filename
    wheel.write_bytes(b"independent synthetic wheel bytes")
    report, registry = _release(timedelta(hours=72))
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    registry["urls"][0]["digests"]["sha256"] = digest
    attest_wheelhouse(tmp_path, now=NOW, read_release=lambda *_: registry,
                      quarantine_hours=24)
    assert verify_wheelhouse(tmp_path, now=NOW, quarantine_hours=24) == (wheel,)
    assert verify_wheelhouse(tmp_path, now=NOW, quarantine_hours=72) == (wheel,)

    with pytest.raises(QuarantineRejected, match="^dependency_too_recent$") as rejected:
        verify_wheelhouse(tmp_path, now=NOW, quarantine_hours=168)
    assert rejected.value.eligible_at == NOW + timedelta(hours=96)
    assert rejected.value.quarantine_hours == 168
    assert "168" in quarantine_diagnostic(rejected.value)
    # An unrelated wheelhouse timestamp manipulation remains unable to alter SHA.
    wheel.write_bytes(b"changed bytes")
    with pytest.raises(QuarantineRejected, match="dependency_not_eligible"):
        verify_wheelhouse(tmp_path, now=NOW, quarantine_hours=24)


def test_override_invalid_on_producer_and_consumer_fails_before_any_file_io(tmp_path):
    with pytest.raises(QuarantineRejected, match="dependency_quarantine_configuration_invalid"):
        attest_wheelhouse(tmp_path / "does-not-exist", now=NOW,
                          quarantine_hours=0)
    with pytest.raises(QuarantineRejected, match="dependency_quarantine_configuration_invalid"):
        verify_wheelhouse(tmp_path / "does-not-exist", now=NOW,
                          quarantine_hours="168")
