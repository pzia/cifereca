"""#2770: test the standard pip dry-run admission, offline and without pip IO."""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from cifereca.plugins.python_quarantine import QuarantineRejected, check_pip_report
from cifereca.plugins.python_dependencies import PythonDependencyError, PythonDependencyManager
from cifereca.plugins import python_dependencies as manager_module


NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
SHA = "a" * 64


def _item(name="sample-pkg", version="1.2.3", sha=SHA, host="files.pythonhosted.org"):
    wheel = name.replace("-", "_") + "-" + version + "-py3-none-any.whl"
    url = f"https://{host}/packages/a/b/{wheel}"
    item = {
        "metadata": {"name": name, "version": version},
        "download_info": {"url": url, "archive_info": {"hashes": {"sha256": sha}}},
    }
    return item, {
        "info": {"name": name, "version": version},
        "urls": [{
            "filename": wheel, "url": url, "packagetype": "bdist_wheel",
            "digests": {"sha256": sha}, "yanked": False,
            "upload_time_iso_8601": (NOW - timedelta(days=7)).isoformat().replace("+00:00", "Z"),
        }],
    }


def _check(item, registry, now=NOW):
    return check_pip_report(
        {"install": [item]}, now=now,
        read_release=lambda name, version: registry,
    )


def test_167h_59min_fails_and_exactly_168h_passes():
    item, release = _item()
    release["urls"][0]["upload_time_iso_8601"] = (
        NOW - timedelta(hours=167, minutes=59)
    ).isoformat().replace("+00:00", "Z")
    with pytest.raises(QuarantineRejected, match="^dependency_too_recent$"):
        _check(item, release)
    release["urls"][0]["upload_time_iso_8601"] = (
        NOW - timedelta(hours=168)
    ).isoformat().replace("+00:00", "Z")
    assert _check(item, release) == (
        item["download_info"]["url"] + "#sha256=" + SHA,
    )


def test_old_version_new_wheel_is_not_eligible():
    item, release = _item(version="0.0.1")
    release["urls"][0]["upload_time_iso_8601"] = (
        NOW - timedelta(minutes=1)
    ).isoformat().replace("+00:00", "Z")
    with pytest.raises(QuarantineRejected, match="dependency_too_recent"):
        _check(item, release)


@pytest.mark.parametrize("mutation,reason", [
    ("hash", "dependency_provenance_unavailable"),
    ("date", "dependency_provenance_unavailable"),
    ("naive", "dependency_provenance_unavailable"),
    ("wrong_url", "dependency_provenance_unavailable"),
    ("yanked", "dependency_not_eligible"),
    ("other_host", "dependency_provenance_unavailable"),
    ("no_hash", "dependency_provenance_unavailable"),
    ("source_archive", "dependency_provenance_unavailable"),
    ("missing_file", "dependency_provenance_unavailable"),
])
def test_missing_or_inconsistent_provenance_fails_closed(mutation, reason):
    item, release = _item()
    row = release["urls"][0]
    if mutation == "hash":
        row["digests"]["sha256"] = "b" * 64
    elif mutation == "date":
        row.pop("upload_time_iso_8601")
    elif mutation == "naive":
        row["upload_time_iso_8601"] = "2026-10-15T12:00:00"
    elif mutation == "wrong_url":
        row["url"] = "https://files.pythonhosted.org/packages/OTHER.whl"
    elif mutation == "yanked":
        row["yanked"] = True
    elif mutation == "other_host":
        item["download_info"]["url"] = "https://untrusted.example.org/file.whl"
    elif mutation == "no_hash":
        item["download_info"]["archive_info"]["hashes"] = {}
    elif mutation == "source_archive":
        row["packagetype"] = "sdist"
    elif mutation == "missing_file":
        release["urls"] = []
    with pytest.raises(QuarantineRejected, match="^" + reason + "$"):
        _check(item, release)


def test_expired_or_broken_clock_does_not_admit():
    item, release = _item()
    with pytest.raises(QuarantineRejected, match="dependency_provenance_unavailable"):
        _check(item, release, datetime(2026, 10, 22, 12, 0))
    with pytest.raises(QuarantineRejected, match="dependency_provenance_unavailable"):
        _check(item, release, NOW - timedelta(days=100))


def test_transitive_dependency_must_also_be_aged():
    direct, old = _item()
    transitive, fresh = _item("required-subpackage", version="2.3")
    fresh["urls"][0]["upload_time_iso_8601"] = (NOW - timedelta(hours=12)).isoformat()
    by_name = {"sample-pkg": old, "required-subpackage": fresh}
    with pytest.raises(QuarantineRejected, match="dependency_too_recent"):
        check_pip_report(
            {"install": [direct, transitive]}, now=NOW,
            read_release=lambda name, version: by_name[name],
        )
    fresh["urls"][0]["upload_time_iso_8601"] = (NOW - timedelta(hours=169)).isoformat()
    result = check_pip_report(
        {"install": [direct, transitive]}, now=NOW,
        read_release=lambda name, version: by_name[name],
    )
    assert len(result) == 2 and all("#sha256=" + SHA in link for link in result)


def test_pip_dry_run_checks_every_wheel_before_any_mutating_install(monkeypatch):
    item, registry = _item()
    called = []
    def fake_pip(command, **kwargs):
        called.append(command)
        if "--dry-run" in command:
            Path(command[command.index("--report") + 1]).write_text(
                json.dumps({"install": [item]}), encoding="utf-8",
            )
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess, "run", fake_pip)
    monkeypatch.setattr(manager_module, "check_pip_report", lambda report, now: (
        item["download_info"]["url"] + "#sha256=" + SHA,
    ))
    PythonDependencyManager(allow_index=True)._pip_install(("sample-pkg>=1",), ())
    assert len(called) == 2
    assert "--dry-run" in called[0] and "--report" in called[0]
    assert "--only-binary=:all:" in called[0]
    assert "--no-cache-dir" in called[0]
    assert "--no-index" in called[1] and "--no-deps" in called[1]
    assert "#sha256=" + SHA in called[1][-1]
    assert "sample-pkg>=1" not in called[1]


def test_ineligible_or_missing_pypi_proof_never_mutates_venv(monkeypatch):
    item, registry = _item()
    commands = []
    def fake_pip(command, **kwargs):
        commands.append(command)
        if "--dry-run" in command:
            Path(command[command.index("--report") + 1]).write_text(
                json.dumps({"install": [item]}), encoding="utf-8",
            )
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess, "run", fake_pip)
    for code in ("dependency_too_recent", "dependency_provenance_unavailable"):
        commands.clear()
        def reject(plan, now):
            raise QuarantineRejected(code)
        monkeypatch.setattr(manager_module, "check_pip_report", reject)
        with pytest.raises(PythonDependencyError, match="^" + code + "$"):
            PythonDependencyManager(allow_index=True)._pip_install(("sample-pkg",), ())
        assert len(commands) == 1 and "--dry-run" in commands[0]
    commands.clear()
    with pytest.raises(PythonDependencyError, match="dependency_provenance_unavailable"):
        PythonDependencyManager(allow_index=False)._pip_install(("sample-pkg",), ())
    assert commands == []


def test_pip_standard_constraints_choose_an_older_eligible_release(monkeypatch):
    fresh_item, _fresh = _item(version="2.0")
    old_item, _old = _item(version="1.0")
    calls = []
    def fake_pip(command, **kwargs):
        calls.append(command)
        if "--dry-run" in command:
            payload = old_item if "--constraint" in command else fresh_item
            if "--constraint" in command:
                constraints = Path(command[command.index("--constraint") + 1]).read_text()
                assert constraints == "sample-pkg<2.0\n"
            Path(command[command.index("--report") + 1]).write_text(
                json.dumps({"install": [payload]}), encoding="utf-8",
            )
        return SimpleNamespace(returncode=0)
    def checked(plan, now):
        version = plan["install"][0]["metadata"]["version"]
        if version == "2.0":
            raise QuarantineRejected(
                "dependency_too_recent", name="sample-pkg", version="2.0",
            )
        return (old_item["download_info"]["url"] + "#sha256=" + SHA,)
    monkeypatch.setattr(subprocess, "run", fake_pip)
    monkeypatch.setattr(manager_module, "check_pip_report", checked)
    PythonDependencyManager(allow_index=True)._pip_install(("sample-pkg>=1",), ())
    assert len(calls) == 3
    assert "--constraint" not in calls[0]
    assert "--constraint" in calls[1]
    assert "--no-deps" in calls[2]
    assert old_item["download_info"]["url"] in calls[2][-1]


def test_pip_solver_conflict_after_constrained_retry_never_installs(monkeypatch):
    item, _registry = _item(version="2.0")
    calls = []
    def fake_pip(command, **kwargs):
        calls.append(command)
        if "--constraint" in command:
            return SimpleNamespace(returncode=1, stderr="SECRET PRIVATE INDEX URL")
        Path(command[command.index("--report") + 1]).write_text(
            json.dumps({"install": [item]}), encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess, "run", fake_pip)
    def too_recent(plan, now):
        raise QuarantineRejected(
            "dependency_too_recent", name="sample-pkg", version="2.0",
        )
    monkeypatch.setattr(manager_module, "check_pip_report", too_recent)
    with pytest.raises(PythonDependencyError, match="^dependency_not_eligible$"):
        PythonDependencyManager(allow_index=True)._pip_install(("sample-pkg==2.0",), ())
    assert len(calls) == 2 and all("--dry-run" in command for command in calls)


def test_no_candidate_to_change_skips_mutating_pip(monkeypatch):
    calls = []
    def fake_pip(command, **kwargs):
        calls.append(command)
        Path(command[command.index("--report") + 1]).write_text(
            json.dumps({"install": []}), encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess, "run", fake_pip)
    PythonDependencyManager(allow_index=True)._pip_install(("already-installed",), ())
    assert len(calls) == 1 and "--dry-run" in calls[0]


def test_rollback_reinstalls_only_through_quarantine_gate(monkeypatch):
    """Even restoring a previously installed version is a new acquisition."""
    manager = PythonDependencyManager(allow_index=True)
    monkeypatch.setattr(manager_module, "_installed_versions", lambda: {
        "sample-pkg": ("sample-pkg", "2.0"),
    })
    requested = []
    monkeypatch.setattr(
        manager, "_pip_install",
        lambda requirements, wheels: requested.append((requirements, wheels)),
    )
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: pytest.fail("unattested rollback pip reached"),
    )
    manager._restore({"sample-pkg": ("sample-pkg", "1.0")}, ())
    assert requested == [(["sample-pkg==1.0"], ())]


def test_offline_rollback_does_not_use_unattested_wheelhouse(monkeypatch):
    manager = PythonDependencyManager(allow_index=False)
    monkeypatch.setattr(manager_module, "_installed_versions", lambda: {
        "sample-pkg": ("sample-pkg", "2.0"),
    })
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: pytest.fail("unsafe offline rollback reached pip"),
    )
    with pytest.raises(PythonDependencyError, match="dependency_provenance_unavailable"):
        manager._restore(
            {"sample-pkg": ("sample-pkg", "1.0")}, (Path("/unverified-wheelhouse"),),
        )


def test_rollback_uninstall_failure_does_not_echo_pip_stderr(monkeypatch):
    """Private index credentials must never appear in rollback diagnostics."""
    secret = "https://user:password@private.example.org/simple?token=SECRET"
    manager = PythonDependencyManager(allow_index=True)
    monkeypatch.setattr(manager_module, "_installed_versions", lambda: {
        "new-addon": ("new-addon", "3.4"),
    })
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1, stderr=secret, stdout=secret,
        ),
    )
    with pytest.raises(PythonDependencyError, match="^dependency_not_eligible$") as error:
        manager._restore({}, ())
    assert secret not in str(error.value)
    assert error.value.__cause__ is None


def test_rollback_quarantine_failure_preserves_added_installed_distributions(monkeypatch):
    """No purge before deciding whether changed versions can be recovered."""
    manager = PythonDependencyManager(allow_index=False)
    before = {"old-pkg": ("old-pkg", "1.0")}
    monkeypatch.setattr(manager_module, "_installed_versions", lambda: {
        "old-pkg": ("old-pkg", "2.0"),
        "new-addon": ("new-addon", "3.0"),
    })
    calls = []
    def reject(requirements, wheel_dirs):
        calls.append(("preflight", tuple(requirements)))
        raise PythonDependencyError("dependency_provenance_unavailable")
    monkeypatch.setattr(manager, "_pip_install", reject)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail(
        "rollback purged a distribution despite rejected wheel provenance",
    ))
    with pytest.raises(PythonDependencyError, match="^dependency_provenance_unavailable$"):
        manager._restore(before, ())
    assert calls == [("preflight", ("old-pkg==1.0",))]
